"""Transport authentication for owner actions and GitHub deliveries."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from pathlib import Path

from fastapi import Header, HTTPException, Request

from .paths import state_path

OWNER_TOKEN_FILE = "owner.token"
ADAPTER_TOKEN_FILE = "adapter.token"
OWNER_SESSION_COOKIE = "alicia_owner_session"


def owner_token_path() -> Path:
    return state_path(OWNER_TOKEN_FILE)


def configured_owner_token() -> str:
    """Return the explicit owner token, creating a mode-0600 local token once.

    Environment injection is preferred for managed deployments. The file is a
    single-user laptop fallback: possession authorizes an owner action; merely
    reaching the loopback HTTP service does not.
    """

    token = os.environ.get("ALICIA_OWNER_TOKEN", "").strip()
    if token:
        return token
    path = owner_token_path()
    try:
        token = path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        path.parent.mkdir(parents=True, exist_ok=True)
        token = secrets.token_urlsafe(48)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(token + "\n")
    if not token:
        raise RuntimeError(f"owner token is empty: {path}")
    return token


def configured_adapter_token() -> str:
    """Return a separate token limited to bounded workflow-event ingestion."""

    token = os.environ.get("ALICIA_ADAPTER_TOKEN", "").strip()
    if token:
        return token
    path = state_path(ADAPTER_TOKEN_FILE)
    try:
        token = path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        path.parent.mkdir(parents=True, exist_ok=True)
        token = secrets.token_urlsafe(48)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(token + "\n")
    if not token:
        raise RuntimeError(f"adapter token is empty: {path}")
    return token


def authenticate_owner_token(presented: str) -> bool:
    return bool(presented) and hmac.compare_digest(presented, configured_owner_token())


def require_adapter_token(
    x_alicia_adapter_token: str | None = Header(default=None),
    x_brutus_adapter_token: str | None = Header(default=None),
) -> None:
    """Authenticate an adapter without granting any owner capability.

    ``X-Brutus-Adapter-Token`` is accepted for one release after the rename so
    external adapters can switch headers on their own schedule.
    """

    presented = (x_alicia_adapter_token or x_brutus_adapter_token or "").strip()
    if not presented or not hmac.compare_digest(presented, configured_adapter_token()):
        raise HTTPException(status_code=401, detail="adapter authentication required")


def issue_owner_session(*, lifetime_seconds: int = 8 * 3600) -> tuple[str, str]:
    csrf = secrets.token_urlsafe(24)
    payload = json.dumps(
        {"exp": int(time.time()) + lifetime_seconds, "csrf": csrf},
        separators=(",", ":"),
    ).encode()
    encoded = base64.urlsafe_b64encode(payload).decode().rstrip("=")
    signature = hmac.new(configured_owner_token().encode(), encoded.encode(), hashlib.sha256).hexdigest()
    return f"{encoded}.{signature}", csrf


def _session_csrf(cookie: str) -> str | None:
    try:
        encoded, supplied = cookie.rsplit(".", 1)
        expected = hmac.new(configured_owner_token().encode(), encoded.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(supplied, expected):
            return None
        padded = encoded + "=" * (-len(encoded) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded))
        if int(payload["exp"]) < int(time.time()):
            return None
        return str(payload["csrf"])
    except (ValueError, KeyError, TypeError, json.JSONDecodeError):
        return None


def require_owner_token(
    request: Request,
    authorization: str | None = Header(default=None),
    x_alicia_owner_token: str | None = Header(default=None),
    x_alicia_csrf: str | None = Header(default=None),
) -> None:
    """FastAPI dependency for consequential local owner actions."""

    presented = (x_alicia_owner_token or "").strip()
    if not presented and authorization and authorization.startswith("Bearer "):
        presented = authorization[7:].strip()
    if authenticate_owner_token(presented):
        return
    session_csrf = _session_csrf(request.cookies.get(OWNER_SESSION_COOKIE, ""))
    if session_csrf is None:
        raise HTTPException(status_code=401, detail="owner authentication required")
    if not x_alicia_csrf or not hmac.compare_digest(x_alicia_csrf, session_csrf):
        raise HTTPException(status_code=403, detail="owner CSRF token required")


def verify_github_signature(body: bytes, signature: str | None) -> bool:
    secret = os.environ.get("ALICIA_GITHUB_WEBHOOK_SECRET", "").strip()
    if not secret or not signature or not signature.startswith("sha256="):
        return False
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(signature[7:], expected)


def allowed_github_repositories() -> frozenset[str]:
    raw = os.environ.get(
        "ALICIA_GITHUB_REPOSITORIES",
        # The pre-rename name stays allowed until old webhook deliveries drain.
        "justinfowler925/alicia,justinfowler925/brutus",
    )
    return frozenset(item.strip().lower() for item in raw.split(",") if item.strip())
