"""Private Studio access: Tailscale Serve identity plus same-origin writes.

Serve terminates TLS and proxies as 127.0.0.1, so ``tailscale-user-login`` alone
is forgeable by any local process. Trusted remote identity requires the Serve
proxy proof header (``X-Alicia-Serve-Proof``) that only
``scripts/alicia-serve-proxy.py`` injects.

Optional per-device allowlists (env):
- ``ALICIA_TAILSCALE_ALLOWED_NODES`` — comma-separated hostnames and/or Tailscale IPs
- ``ALICIA_TAILSCALE_ALLOWED_TAGS`` — comma-separated tags (e.g. ``tag:owner``)

When either list is non-empty, Serve-proven requests must also match a peer from
``tailscale whois`` on ``X-Forwarded-For`` / ``X-Real-IP``.
"""

from __future__ import annotations

import json
import os
import subprocess
from urllib.parse import urlsplit

from starlette.responses import JSONResponse

from .auth_audit import record as audit_record
from .security import SERVE_PROOF_HEADER, verify_serve_proof


def funnel_exposes_alicia_port(port: int = 8768) -> bool | None:
    """True when ``tailscale serve status`` shows Alicia's port under Funnel.

    Returns None when the CLI is unavailable or unparsable — healthz then omits
    a hard claim rather than inventing a green light.
    """

    try:
        result = subprocess.run(
            ["tailscale", "serve", "status"],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    mode: str | None = None
    needle = f":{port}"
    upstream = f"127.0.0.1:{port}"
    for line in (result.stdout or "").splitlines():
        if "(Funnel on)" in line:
            mode = "funnel"
            continue
        if "(tailnet only)" in line:
            mode = "tailnet"
            continue
        if mode == "funnel" and (needle in line or upstream in line):
            return True
    return False


def _csv_env(name: str) -> list[str]:
    raw = os.environ.get(name, "") or ""
    return [part.strip() for part in raw.split(",") if part.strip()]


def _client_ip(headers) -> str:
    for key in ("x-forwarded-for", "x-real-ip"):
        raw = (headers.get(key) or "").strip()
        if not raw:
            continue
        # First hop is the Tailscale client when Serve proxies.
        return raw.split(",")[0].strip()
    return ""


def _whois(ip: str) -> dict:
    if not ip:
        return {}
    try:
        result = subprocess.run(
            ["tailscale", "whois", "--json", ip],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
        return {}
    if result.returncode != 0 or not (result.stdout or "").strip():
        return {}
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _normalize_peer_token(value: object) -> str:
    return str(value or "").strip().rstrip(".").lower()


def _peer_identity(node: dict, ip: str) -> tuple[set[str], str]:
    """Build match candidates from real ``tailscale whois --json`` shape.

    Live whois often omits ``HostName`` / ``DNSName`` and puts the MagicDNS
    name in ``Name`` (trailing dot) plus ``ComputedName`` / ``Hostinfo.Hostname``.
    """

    candidates: set[str] = set()
    hostinfo = node.get("Hostinfo") if isinstance(node.get("Hostinfo"), dict) else {}
    for raw in (
        node.get("HostName"),
        node.get("Name"),
        node.get("DNSName"),
        node.get("ComputedName"),
        node.get("ComputedNameWithHost"),
        hostinfo.get("Hostname"),
        ip,
    ):
        token = _normalize_peer_token(raw)
        if not token:
            continue
        candidates.add(token)
        if "." in token:
            candidates.add(token.split(".", 1)[0])
    for addr in node.get("Addresses") or node.get("IPs") or []:
        token = _normalize_peer_token(str(addr).split("/", 1)[0])
        if token:
            candidates.add(token)
    candidates.discard("")
    label = (
        _normalize_peer_token(node.get("ComputedName"))
        or _normalize_peer_token(hostinfo.get("Hostname"))
        or _normalize_peer_token(node.get("HostName"))
        or _normalize_peer_token(node.get("Name"))
        or ip
        or "unknown"
    )
    return candidates, label


def peer_allowed(headers) -> tuple[bool, str, str]:
    """Return (allowed, reason, peer_label) for optional node/tag allowlists."""

    nodes = {_normalize_peer_token(n) for n in _csv_env("ALICIA_TAILSCALE_ALLOWED_NODES")}
    nodes.discard("")
    tags = {t if t.startswith("tag:") else f"tag:{t}" for t in _csv_env("ALICIA_TAILSCALE_ALLOWED_TAGS")}
    tags = {_normalize_peer_token(t) for t in tags}
    tags.discard("")
    if not nodes and not tags:
        return True, "allowlist_unset", ""

    ip = _client_ip(headers)
    info = _whois(ip)
    node = info.get("Node") if isinstance(info.get("Node"), dict) else {}
    candidates, label = _peer_identity(node, ip)
    peer_tags = {
        _normalize_peer_token(t)
        for t in (node.get("Tags") or info.get("Tags") or [])
        if t
    }
    peer_tags.discard("")

    if nodes and candidates.isdisjoint(nodes):
        return False, "node_not_allowlisted", label
    if tags and peer_tags.isdisjoint(tags):
        return False, "tag_not_allowlisted", label
    return True, "peer_ok", label


def install(app):
    origin = os.environ.get("ALICIA_PUBLIC_ORIGIN", "").rstrip("/")
    owner = os.environ.get("ALICIA_TAILSCALE_OWNER", "")
    if not origin:
        return

    public_netloc = urlsplit(origin).netloc

    @app.middleware("http")
    async def private_access(request, call_next):
        host = request.headers.get("host", "")
        loopback = request.client and request.client.host in ("127.0.0.1", "::1")
        local_host = request.url.hostname in ("127.0.0.1", "localhost", "::1")
        proof_ok = verify_serve_proof(request.headers.get(SERVE_PROOF_HEADER))
        login = request.headers.get("tailscale-user-login") or ""
        trusted = bool(
            loopback
            and host == public_netloc
            and owner
            and login == owner
            and proof_ok
        )
        peer_label = ""
        if trusted:
            allowed, reason, peer_label = peer_allowed(request.headers)
            if not allowed:
                audit_record(
                    "studio_access",
                    ok=False,
                    detail=reason,
                    path=str(request.url.path),
                    login=login,
                    peer=peer_label,
                )
                return JSONResponse(
                    {"detail": f"Tailscale device not allowlisted ({reason})"},
                    status_code=403,
                )
        if not (loopback and local_host) and not trusted:
            audit_record(
                "studio_access",
                ok=False,
                detail="identity_required",
                path=str(request.url.path),
                login=login,
            )
            return JSONResponse(
                {"detail": "Your private Tailscale identity is required"},
                status_code=403,
            )
        supplied = request.headers.get("origin")
        expected_origin = origin if trusted else f"{request.url.scheme}://{host}"
        if supplied and supplied.rstrip("/") != expected_origin.rstrip("/"):
            audit_record(
                "studio_access",
                ok=False,
                detail="origin_mismatch",
                path=str(request.url.path),
                login=login,
            )
            return JSONResponse({"detail": "Same-origin request required"}, status_code=403)
        if request.method not in ("GET", "HEAD", "OPTIONS") and trusted and not supplied:
            audit_record(
                "studio_access",
                ok=False,
                detail="origin_required",
                path=str(request.url.path),
                login=login,
            )
            return JSONResponse({"detail": "Origin header required"}, status_code=403)
        request.state.studio_owner = trusted
        if trusted and request.method not in ("GET", "HEAD", "OPTIONS"):
            audit_record(
                "studio_access",
                ok=True,
                detail="trusted",
                via="studio_owner",
                path=str(request.url.path),
                login=login,
                peer=peer_label,
            )
        return await call_next(request)
