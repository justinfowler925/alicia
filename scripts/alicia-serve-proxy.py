#!/usr/bin/env python3
"""Loopback reverse proxy that injects Alicia's Tailscale Serve proof header.

Tailscale Serve terminates TLS and forwards as 127.0.0.1, so identity headers
alone are forgeable on the app port. This proxy is what Serve targets; it adds
``X-Alicia-Serve-Proof`` before forwarding to the real Alicia listener. Direct
clients on :8768 cannot mint ``studio_owner`` with forged login headers.
"""

from __future__ import annotations

import argparse
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


def _proof() -> str:
    env = os.environ.get("ALICIA_SERVE_PROOF", "").strip()
    if env:
        return env
    # Prefer the deployed package when running under ~/.alicia/app.
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root))
    from alicia.security import configured_serve_proof  # noqa: WPS433

    return configured_serve_proof()


class _Handler(BaseHTTPRequestHandler):
    upstream: str = "http://127.0.0.1:8768"
    proof: str = ""

    def log_message(self, fmt: str, *args) -> None:  # noqa: A003 — stdlib API
        sys.stderr.write("serve-proxy: " + (fmt % args) + "\n")

    def _forward(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else None
        headers = {k: v for k, v in self.headers.items() if k.lower() != "host"}
        headers["X-Alicia-Serve-Proof"] = self.proof
        headers["Host"] = self.headers.get("Host") or "127.0.0.1"
        url = self.upstream.rstrip("/") + self.path
        req = Request(url, data=body, headers=headers, method=self.command)
        try:
            with urlopen(req, timeout=600) as resp:  # noqa: S310 — loopback only
                payload = resp.read()
                self.send_response(resp.status)
                for key, value in resp.headers.items():
                    if key.lower() in {"transfer-encoding", "connection", "content-encoding"}:
                        continue
                    self.send_header(key, value)
                self.end_headers()
                self.wfile.write(payload)
        except HTTPError as exc:
            payload = exc.read()
            self.send_response(exc.code)
            for key, value in exc.headers.items():
                if key.lower() in {"transfer-encoding", "connection", "content-encoding"}:
                    continue
                self.send_header(key, value)
            self.end_headers()
            self.wfile.write(payload)
        except URLError as exc:
            self.send_response(502)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(f"upstream unavailable: {exc}".encode())

    def do_GET(self) -> None:  # noqa: N802
        self._forward()

    def do_POST(self) -> None:  # noqa: N802
        self._forward()

    def do_PUT(self) -> None:  # noqa: N802
        self._forward()

    def do_PATCH(self) -> None:  # noqa: N802
        self._forward()

    def do_DELETE(self) -> None:  # noqa: N802
        self._forward()

    def do_OPTIONS(self) -> None:  # noqa: N802
        self._forward()

    def do_HEAD(self) -> None:  # noqa: N802
        self._forward()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--listen", default=os.environ.get("ALICIA_SERVE_PROXY_PORT", "8767"))
    parser.add_argument("--upstream", default=os.environ.get("ALICIA_SERVE_UPSTREAM", "http://127.0.0.1:8768"))
    args = parser.parse_args()
    proof = _proof()
    _Handler.upstream = args.upstream
    _Handler.proof = proof
    server = ThreadingHTTPServer(("127.0.0.1", int(args.listen)), _Handler)
    print(f"alicia-serve-proxy listening on 127.0.0.1:{args.listen} → {args.upstream}", flush=True)
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
