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
SERVE_PROOF_FILE = "serve.proof"
OWNER_SESSION_COOKIE = "alicia_owner_session"
SERVE_PROOF_HEADER = "x-alicia-serve-proof"

# Short-lived one-time tickets for open-operator browser pairing.
_PAIR_TICKETS: dict[str, float] = {}
_PAIR_TTL_SECONDS = 120


def owner_token_path() -> Path:
    return state_path(OWNER_TOKEN_FILE)


def _mint_secret_file(path: Path) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(path.parent, 0o700)
    except OSError:
        pass
    token = secrets.token_urlsafe(48)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(token + "\n")
    return token


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
        token = _mint_secret_file(path)
    if not token:
        raise RuntimeError(f"owner token is empty: {path}")
    return token


def serve_proof_path() -> Path:
    return state_path(SERVE_PROOF_FILE)


def configured_serve_proof() -> str:
    """Shared secret proving a request crossed the Tailscale Serve proxy.

    Client-supplied ``tailscale-user-login`` headers are forgeable on loopback.
    Serve terminates TLS and proxies as 127.0.0.1; only the local Serve proxy
    (see ``scripts/alicia-serve-proxy.py``) injects this proof header.
    """

    token = os.environ.get("ALICIA_SERVE_PROOF", "").strip()
    if token:
        return token
    path = serve_proof_path()
    try:
        token = path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        token = _mint_secret_file(path)
    if not token:
        raise RuntimeError(f"serve proof is empty: {path}")
    return token


def verify_serve_proof(presented: str | None) -> bool:
    expected = configured_serve_proof()
    if not presented or not expected:
        return False
    return hmac.compare_digest(presented.strip(), expected)


def configured_adapter_token() -> str:
    """Return a separate token limited to bounded workflow-event ingestion."""

    token = os.environ.get("ALICIA_ADAPTER_TOKEN", "").strip()
    if token:
        return token
    path = state_path(ADAPTER_TOKEN_FILE)
    try:
        token = path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        token = _mint_secret_file(path)
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


def owner_session_csrf(request: Request) -> str | None:
    return _session_csrf(request.cookies.get(OWNER_SESSION_COOKIE, ""))


def mint_pair_ticket() -> str:
    """Single-use ticket for browser pairing (open-operator)."""
    now = time.time()
    expired = [k for k, exp in _PAIR_TICKETS.items() if exp < now]
    for key in expired:
        _PAIR_TICKETS.pop(key, None)
    ticket = secrets.token_urlsafe(24)
    _PAIR_TICKETS[ticket] = now + _PAIR_TTL_SECONDS
    return ticket


def consume_pair_ticket(ticket: str) -> bool:
    exp = _PAIR_TICKETS.pop((ticket or "").strip(), None)
    return exp is not None and exp >= time.time()


def _presented_owner_token(
    authorization: str | None,
    x_alicia_owner_token: str | None,
) -> str:
    presented = (x_alicia_owner_token or "").strip()
    if not presented and authorization and authorization.startswith("Bearer "):
        presented = authorization[7:].strip()
    return presented


def owner_auth_via(
    request: Request,
    *,
    authorization: str | None = None,
    x_alicia_owner_token: str | None = None,
    x_alicia_csrf: str | None = None,
) -> str | None:
    """Return how the request is authorized, or None."""

    presented = _presented_owner_token(authorization, x_alicia_owner_token)
    if authenticate_owner_token(presented):
        return "token"
    session_csrf = owner_session_csrf(request)
    if session_csrf is not None and x_alicia_csrf and hmac.compare_digest(x_alicia_csrf, session_csrf):
        return "csrf"
    if getattr(request.state, "studio_owner", False):
        return "studio_owner"
    return None


def require_owner_token(
    request: Request,
    authorization: str | None = Header(default=None),
    x_alicia_owner_token: str | None = Header(default=None),
    x_alicia_csrf: str | None = Header(default=None),
) -> None:
    """FastAPI dependency for consequential local owner actions (token or CSRF).

    Does **not** accept Tailscale ``studio_owner`` alone — Canon / workflow keep
    the stricter token-or-cookie bar.
    """

    presented = _presented_owner_token(authorization, x_alicia_owner_token)
    if authenticate_owner_token(presented):
        return
    session_csrf = owner_session_csrf(request)
    if session_csrf is None:
        raise HTTPException(status_code=401, detail="owner authentication required")
    if not x_alicia_csrf or not hmac.compare_digest(x_alicia_csrf, session_csrf):
        raise HTTPException(status_code=403, detail="owner CSRF token required")


def require_owner_action(
    request: Request,
    authorization: str | None = Header(default=None),
    x_alicia_owner_token: str | None = Header(default=None),
    x_alicia_csrf: str | None = Header(default=None),
) -> str:
    """Owner proof for conversation / spend / approve surfaces.

    Accepts (1) owner token, (2) cookie + CSRF, or (3) Serve-proven
    ``studio_owner``. Raw loopback without proof is rejected.
    """

    from .auth_audit import record as audit_record

    via = owner_auth_via(
        request,
        authorization=authorization,
        x_alicia_owner_token=x_alicia_owner_token,
        x_alicia_csrf=x_alicia_csrf,
    )
    if via:
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            audit_record(
                "owner_action",
                ok=True,
                via=via,
                path=str(request.url.path),
            )
        return via
    session_csrf = owner_session_csrf(request)
    if session_csrf is not None and not x_alicia_csrf:
        audit_record(
            "owner_action",
            ok=False,
            detail="csrf_required",
            path=str(request.url.path),
        )
        raise HTTPException(status_code=403, detail="owner CSRF token required")
    audit_record(
        "owner_action",
        ok=False,
        detail="auth_required",
        path=str(request.url.path),
    )
    raise HTTPException(status_code=401, detail="owner authentication required")


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
