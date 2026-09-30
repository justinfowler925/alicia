"""Private Studio access: Tailscale Serve identity plus same-origin writes."""
import os
from urllib.parse import urlsplit
from starlette.responses import JSONResponse


def install(app):
    origin = os.environ.get('ALICIA_PUBLIC_ORIGIN', '').rstrip('/')
    owner = os.environ.get('ALICIA_TAILSCALE_OWNER', '')
    if not origin:
        return
    @app.middleware('http')
    async def private_access(request, call_next):
        host = request.headers.get('host', '')
        loopback = request.client and request.client.host in ('127.0.0.1', '::1')
        local_host = request.url.hostname in ('127.0.0.1', 'localhost', '::1')
        trusted = bool(loopback and host == urlsplit(origin).netloc and owner
            and request.headers.get('tailscale-user-login') == owner)
        if not (loopback and local_host) and not trusted:
            return JSONResponse({'detail': 'Your private Tailscale identity is required'}, status_code=403)
        supplied = request.headers.get('origin')
        if supplied and supplied.rstrip('/') != (origin if trusted else f'{request.url.scheme}://{host}'):
            return JSONResponse({'detail': 'Same-origin request required'}, status_code=403)
        if request.method not in ('GET', 'HEAD', 'OPTIONS') and trusted and not supplied:
            return JSONResponse({'detail': 'Origin header required'}, status_code=403)
        request.state.studio_owner = trusted
        return await call_next(request)
