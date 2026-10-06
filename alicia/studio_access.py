"""Private Studio access: Tailscale Serve identity plus same-origin writes.

Serve terminates TLS and proxies as 127.0.0.1, so ``tailscale-user-login`` alone
is forgeable by any local process. Trusted remote identity requires the Serve
proxy proof header (``X-Alicia-Serve-Proof``) that only
``scripts/alicia-serve-proxy.py`` injects.
"""

from __future__ import annotations

import os
import subprocess
from urllib.parse import urlsplit

from starlette.responses import JSONResponse

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
        if not (loopback and local_host) and not trusted:
            return JSONResponse(
                {"detail": "Your private Tailscale identity is required"},
                status_code=403,
            )
        supplied = request.headers.get("origin")
        expected_origin = origin if trusted else f"{request.url.scheme}://{host}"
        if supplied and supplied.rstrip("/") != expected_origin.rstrip("/"):
            return JSONResponse({"detail": "Same-origin request required"}, status_code=403)
        if request.method not in ("GET", "HEAD", "OPTIONS") and trusted and not supplied:
            return JSONResponse({"detail": "Origin header required"}, status_code=403)
        request.state.studio_owner = trusted
        return await call_next(request)
