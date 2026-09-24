"""Zoom Server-to-Server OAuth client for account meeting summaries.

This is what makes the AI Companion lane autonomous. The credentials already
exist (`ZOOM_ACCOUNT_ID` / `ZOOM_CLIENT_ID` / `ZOOM_CLIENT_SECRET`, used by
`~/Projects/sfdc/salesforce/scripts/zoom/backfill_summaries.py`) and the app's
scopes are exactly the ones needed:

    meeting:read:summary:admin            one meeting's summary
    meeting:read:list_summaries:admin     summaries across the account
    meeting:read:list_past_participants:admin

My Notes deliberately uses the separate user-managed OAuth client in
``zoom_user_oauth``. Zoom's My Notes endpoints reject Server-to-Server tokens,
including tokens carrying the Marketplace's apparent ``:admin`` equivalents.

The summary endpoint returns `next_steps` as a flat list of `"Owner: text"`
strings, which is the same information the connector renders as markdown under
"## Next steps" — `zoom_ingest.extract_items` reads either shape.

Two things to know before trusting a result:

*   **A summary can exist with no content.** Justin's own two meetings in the
    last month both return metadata and `next_steps: None`; ITC sync returns ten.
    That is the AI declining to summarise a thin transcript, not an auth problem
    — proven by fetching all three with one token.

*   **The UUID here is not the connector's UUID.** This API calls ITC sync
    `8ZHzz9PRRP6R2ToX5D3YKg==` where the connector calls it
    `F191F3CF-D3D1-44FE-91D9-3A17E43DD82A`. The ingest ledger keys on whatever it
    is given, so running both lanes over the same meeting would double-capture
    it. This lane is the automatic one; the connector lane is for ad-hoc pulls.

Credentials are read from the environment injected by the declared production
profile. Missing vars raise ZoomAPIError — fail closed, no fallback store.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

log = logging.getLogger("alicia.zoom_api")

TOKEN_URL = "https://zoom.us/oauth/token"
API_BASE = "https://api.zoom.us/v2"

CRED_VARS = ("ZOOM_ACCOUNT_ID", "ZOOM_CLIENT_ID", "ZOOM_CLIENT_SECRET")

#: Zoom rejects a range wider than a month on the summaries list.
MAX_RANGE_DAYS = 30


class ZoomAPIError(RuntimeError):
    pass


@dataclass(frozen=True)
class ZoomCredentials:
    account_id: str
    client_id: str
    client_secret: str

    @classmethod
    def load(cls) -> ZoomCredentials:
        """Environment only; production launchers inject the declared profile."""
        values: dict[str, str] = {}
        for var in CRED_VARS:
            values[var] = (os.environ.get(var) or "").strip()
        missing = [v for v in CRED_VARS if not values[v]]
        if missing:
            raise ZoomAPIError(f"missing Zoom credentials: {', '.join(missing)}")
        return cls(values["ZOOM_ACCOUNT_ID"], values["ZOOM_CLIENT_ID"], values["ZOOM_CLIENT_SECRET"])


def double_encode(uuid: str) -> str:
    """Zoom needs the UUID double-encoded — theirs contain `/` and `+`."""
    return urllib.parse.quote(urllib.parse.quote(uuid, safe=""), safe="")


class ZoomClient:
    """Minimal read-only client. One token, reused until it nearly expires."""

    def __init__(self, creds: ZoomCredentials | None = None, *, timeout: float = 40.0) -> None:
        self._creds = creds
        self.timeout = timeout
        self._token = ""
        self._token_expires_at = 0.0

    @property
    def creds(self) -> ZoomCredentials:
        if self._creds is None:
            self._creds = ZoomCredentials.load()
        return self._creds

    def _fetch_token(self) -> tuple[str, float]:
        c = self.creds
        url = f"{TOKEN_URL}?grant_type=account_credentials&account_id={urllib.parse.quote(c.account_id)}"
        req = urllib.request.Request(url, data=b"", method="POST")
        basic = base64.b64encode(f"{c.client_id}:{c.client_secret}".encode()).decode()
        req.add_header("Authorization", f"Basic {basic}")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                data = json.loads(r.read())
        except urllib.error.HTTPError as exc:
            # Never echo the secret; the status is the diagnostic.
            raise ZoomAPIError(f"Zoom token request failed: HTTP {exc.code}") from exc
        except Exception as exc:
            raise ZoomAPIError(f"Zoom token request failed: {exc}") from exc
        token = str(data.get("access_token") or "")
        if not token:
            raise ZoomAPIError("Zoom token response had no access_token")
        return token, time.time() + float(data.get("expires_in") or 3600)

    def token(self) -> str:
        if not self._token or time.time() >= self._token_expires_at - 120:
            self._token, self._token_expires_at = self._fetch_token()
        return self._token

    def get(self, path: str) -> dict[str, Any]:
        req = urllib.request.Request(f"{API_BASE}{path}")
        req.add_header("Authorization", f"Bearer {self.token()}")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return {}
            raise ZoomAPIError(f"GET {path} failed: HTTP {exc.code}") from exc
        except Exception as exc:
            raise ZoomAPIError(f"GET {path} failed: {exc}") from exc

    # --- meeting-summary calls -------------------------------------------

    def list_summaries(self, frm: str, to: str, *, page_size: int = 100) -> list[dict[str, Any]]:
        """Every meeting summary in a date range (`yyyy-mm-dd`), across the account."""
        out: list[dict[str, Any]] = []
        token = ""
        while True:
            q = f"?from={urllib.parse.quote(frm)}&to={urllib.parse.quote(to)}&page_size={page_size}"
            if token:
                q += f"&next_page_token={urllib.parse.quote(token)}"
            data = self.get(f"/meetings/meeting_summaries{q}")
            out.extend(data.get("summaries") or [])
            token = str(data.get("next_page_token") or "")
            if not token:
                return out

    def get_summary(self, meeting_uuid: str) -> dict[str, Any]:
        return self.get(f"/meetings/{double_encode(meeting_uuid)}/meeting_summary")

    def participant_emails(self, meeting_uuid: str) -> set[str]:
        data = self.get(f"/past_meetings/{double_encode(meeting_uuid)}/participants?page_size=300")
        emails: set[str] = set()
        for p in data.get("participants") or []:
            for field in ("user_email", "email", "name"):
                val = str(p.get(field) or "").strip().lower()
                if val:
                    emails.add(val)
        return emails

def assets_from_summary(summary: dict[str, Any]) -> dict[str, Any]:
    """Reshape an API summary into what `zoom_ingest.ingest_assets` expects."""
    return {
        "meeting_uuid": summary.get("meeting_uuid") or "",
        "topic": summary.get("meeting_topic") or "",
        "start_time": summary.get("meeting_start_time") or summary.get("summary_start_time") or "",
        "host_email": summary.get("meeting_host_email") or "",
        # The flat `"Owner: text"` list; extract_items reads it directly.
        "next_steps": summary.get("next_steps") or [],
    }


def default_window(days: int = 7) -> tuple[str, str]:
    """A `(from, to)` pair, clamped to the month Zoom allows."""
    days = max(1, min(days, MAX_RANGE_DAYS))
    today = datetime.now(UTC).date()
    return (today - timedelta(days=days)).isoformat(), today.isoformat()
