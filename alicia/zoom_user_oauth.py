"""Zoom user OAuth for Justin's private My Notes.

Zoom's My Notes endpoints reject Server-to-Server tokens even when Zoom's app
builder grants the corresponding ``:admin`` scopes.  The supported path is a
user-managed General app.  Alicia uses that app's public PKCE client so there is
no client secret to distribute, and keeps Zoom's rotating refresh token in its
owner-only state directory.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import secrets
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .paths import alicia_home
from .zoom_api import API_BASE, TOKEN_URL, ZoomAPIError

AUTHORIZE_URL = "https://zoom.us/oauth/authorize"
PUBLIC_CLIENT_ID = "0agZSViXRMSOPgPlJaNAsw"
REDIRECT_URI = "http://127.0.0.1:8768/api/zoom/oauth/callback"
TOKEN_FILENAME = "zoom-my-notes-refresh-token"
AUTH_TTL_SECONDS = 600


@dataclass(frozen=True)
class _PendingAuthorization:
    verifier: str
    created_at: float


_pending: dict[str, _PendingAuthorization] = {}
_pending_lock = threading.Lock()


class ZoomRefreshTokenStore:
    """Persist Zoom's rotating token in Alicia's private state directory."""

    def __init__(
        self,
        *,
        path: str | Path | None = None,
    ) -> None:
        state_dir = Path(os.environ.get("ALICIA_STATE_DIR") or alicia_home() / "state")
        self.path = Path(path) if path else state_dir / TOKEN_FILENAME

    def read(self) -> str:
        try:
            token = self.path.read_text(encoding="utf-8").strip()
        except FileNotFoundError:
            token = ""
        except OSError as exc:
            raise ZoomAPIError(f"Zoom refresh-token read failed: {exc}") from exc
        if not token:
            raise ZoomAPIError("Zoom My Notes authorization is missing; open /api/zoom/oauth/start")
        return token

    def write(self, token: str) -> None:
        if not token:
            raise ZoomAPIError("Zoom token response had no refresh_token")
        temporary = ""
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            fd, temporary = tempfile.mkstemp(prefix=f".{self.path.name}.", dir=self.path.parent)
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(token)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
        except OSError as exc:
            if temporary:
                try:
                    Path(temporary).unlink(missing_ok=True)
                except OSError:
                    pass
            raise ZoomAPIError(f"Zoom refresh-token write failed: {exc}") from exc


class ZoomMyNotesClient:
    """Read-only Zoom client authenticated as one explicitly authorized user."""

    def __init__(
        self,
        *,
        client_id: str | None = None,
        redirect_uri: str | None = None,
        store: ZoomRefreshTokenStore | None = None,
        timeout: float = 40.0,
    ) -> None:
        self.client_id = (client_id or os.environ.get("ZOOM_MY_NOTES_CLIENT_ID") or PUBLIC_CLIENT_ID).strip()
        self.redirect_uri = (redirect_uri or REDIRECT_URI).strip()
        self.store = store or ZoomRefreshTokenStore()
        self.timeout = timeout
        self._token = ""
        self._token_expires_at = 0.0

    def authorization_url(self) -> str:
        verifier = secrets.token_urlsafe(64)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
        state = secrets.token_urlsafe(32)
        with _pending_lock:
            now = time.time()
            for key, pending in list(_pending.items()):
                if now - pending.created_at > AUTH_TTL_SECONDS:
                    _pending.pop(key, None)
            _pending[state] = _PendingAuthorization(verifier=verifier, created_at=now)
        query = urllib.parse.urlencode(
            {
                "response_type": "code",
                "client_id": self.client_id,
                "redirect_uri": self.redirect_uri,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
                "state": state,
            }
        )
        return f"{AUTHORIZE_URL}?{query}"

    def accept_callback(self, code: str, state: str, *, expected_email: str) -> dict[str, Any]:
        with _pending_lock:
            pending = _pending.pop(state, None)
        if pending is None or time.time() - pending.created_at > AUTH_TTL_SECONDS:
            raise ZoomAPIError("Zoom OAuth state is missing or expired; restart authorization")
        payload = self._token_request(
            {
                "grant_type": "authorization_code",
                "code": code,
                "client_id": self.client_id,
                "redirect_uri": self.redirect_uri,
                "code_verifier": pending.verifier,
            }
        )
        access_token = str(payload.get("access_token") or "")
        if not access_token:
            raise ZoomAPIError("Zoom token response had no access_token")
        identity = self._get_with_token("/users/me", access_token)
        email = str(identity.get("email") or "").strip().lower()
        if email != expected_email.strip().lower():
            raise ZoomAPIError(f"My Notes token owner is {email or 'unknown'}, expected {expected_email}")
        self.store.write(str(payload.get("refresh_token") or ""))
        self._token = access_token
        self._token_expires_at = time.time() + float(payload.get("expires_in") or 3600)
        return identity

    def _token_request(self, fields: dict[str, str]) -> dict[str, Any]:
        body = urllib.parse.urlencode(fields).encode()
        req = urllib.request.Request(TOKEN_URL, data=body, method="POST")
        req.add_header("Content-Type", "application/x-www-form-urlencoded")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as response:
                return json.loads(response.read())
        except urllib.error.HTTPError as exc:
            try:
                detail = json.loads(exc.read()).get("reason") or ""
            except (ValueError, AttributeError):
                detail = ""
            suffix = f" ({detail})" if detail else ""
            raise ZoomAPIError(f"Zoom user token request failed: HTTP {exc.code}{suffix}") from exc
        except Exception as exc:
            raise ZoomAPIError(f"Zoom user token request failed: {exc}") from exc

    def _refresh(self) -> tuple[str, float]:
        payload = self._token_request(
            {
                "grant_type": "refresh_token",
                "refresh_token": self.store.read(),
                "client_id": self.client_id,
            }
        )
        token = str(payload.get("access_token") or "")
        if not token:
            raise ZoomAPIError("Zoom refresh response had no access_token")
        self.store.write(str(payload.get("refresh_token") or ""))
        return token, time.time() + float(payload.get("expires_in") or 3600)

    def token(self) -> str:
        if not self._token or time.time() >= self._token_expires_at - 120:
            self._token, self._token_expires_at = self._refresh()
        return self._token

    def _get_with_token(self, path: str, token: str) -> dict[str, Any]:
        return self._request_with_token("GET", path, token)

    def _request_with_token(
        self,
        method: str,
        path: str,
        token: str,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        data = json.dumps(payload).encode() if payload is not None else None
        req = urllib.request.Request(f"{API_BASE}{path}", data=data, method=method)
        req.add_header("Authorization", f"Bearer {token}")
        if data is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as response:
                return json.loads(response.read())
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return {}
            try:
                body = json.loads(exc.read())
                detail = str(body.get("message") or body.get("reason") or "").strip()
            except (ValueError, AttributeError):
                detail = ""
            suffix = f" ({detail[:300]})" if detail else ""
            raise ZoomAPIError(f"{method} {path} failed: HTTP {exc.code}{suffix}") from exc
        except Exception as exc:
            raise ZoomAPIError(f"{method} {path} failed: {exc}") from exc

    def get(self, path: str) -> dict[str, Any]:
        return self._get_with_token(path, self.token())

    def post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request_with_token("POST", path, self.token(), payload)

    def list_my_notes(self) -> list[dict[str, Any]]:
        return list(self.get("/my_notes/notes").get("notes") or [])

    @staticmethod
    def _search_note(file: dict[str, Any], *, fallback_date: str) -> dict[str, Any]:
        name = str(file.get("file_name") or "Zoom meeting").strip()
        match = re.search(
            r"(\d{4}-\d{2}-\d{2})\s+(\d{2}):(\d{2})\(GMT([+-])(\d{1,2}):(\d{2})\)",
            name,
        )
        if match:
            sign = match.group(4)
            offset_hours = int(match.group(5))
            created = (
                f"{match.group(1)}T{match.group(2)}:{match.group(3)}:00"
                f"{sign}{offset_hours:02d}:{match.group(6)}"
            )
        else:
            created = f"{fallback_date}T00:00:00Z"
        return {
            "note_id": str(file.get("file_id") or "").strip(),
            "note_name": name,
            "note_link": str(file.get("file_link") or "").strip(),
            "created_time": created,
        }

    def search_my_notes(self, created_from: str, created_to: str) -> list[dict[str, Any]]:
        """Discover My Notes through Zoom Canvas Search.

        ``GET /my_notes/notes`` only lists notes for one required meeting id.
        Canvas Search is Zoom's cross-meeting index and also covers notes made
        during Teams, Meet, and in-person conversations.
        """
        base = {
            "file_types": ["note"],
            "page_size": 50,
            "created_time_from": f"{created_from}T00:00:00Z",
            "created_time_to": f"{created_to}T23:59:59Z",
        }
        notes: list[dict[str, Any]] = []
        page_token = ""
        seen_tokens: set[str] = set()
        while True:
            body = {**base}
            if page_token:
                body["next_page_token"] = page_token
            response = self.post("/docs/file_search", body)
            for file in response.get("files") or []:
                if str(file.get("file_type") or "").lower() != "note":
                    continue
                note = self._search_note(file, fallback_date=created_from)
                if note["note_id"]:
                    notes.append(note)
            next_token = str(response.get("next_page_token") or "").strip()
            if not next_token:
                break
            if next_token in seen_tokens:
                raise ZoomAPIError("Zoom file search repeated a pagination token")
            seen_tokens.add(next_token)
            page_token = next_token
        return notes

    def get_my_note(self, note_id: str, *, include_transcript: bool = True) -> dict[str, Any]:
        encoded = urllib.parse.quote(str(note_id or "").strip(), safe="")
        if not encoded:
            raise ZoomAPIError("My Notes content request needs a note id")
        suffix = "?include=transcript" if include_transcript else ""
        return self.get(f"/my_notes/notes/{encoded}/content{suffix}")

    def current_user(self) -> dict[str, Any]:
        return self.get("/users/me")
