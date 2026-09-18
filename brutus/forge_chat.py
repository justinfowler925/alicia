"""Local typed-chat transport to the installed Forge runtime on Studio."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field


def require_local_chat(request: Request):
    """Same local typed-input trust boundary as Brutus, with explicit CSRF checks.

    Never accept remote clients, foreign Host/Origin, or a simple cross-site
    form. No CORS grants are made. This does not change owner-token routes.
    """
    if not request.client or request.client.host not in {"127.0.0.1", "::1"}:
        raise HTTPException(403, "Forge chat is available only on this Mac")
    if request.url.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise HTTPException(403, "Local host required")
    origin = request.headers.get("origin")
    if origin and urlsplit(origin).netloc != request.headers.get("host"):
        raise HTTPException(403, "Same-origin chat required")
    if request.headers.get("x-brutus-chat") != "forge":
        raise HTTPException(403, "Chat request header required")


router = APIRouter(prefix="/api/forge", dependencies=[Depends(require_local_chat)])


class ChatRequest(BaseModel):
    action: Literal["list", "create", "get", "send", "stop"]
    thread_id: UUID | None = None
    message_id: UUID | None = None
    message: str = Field(default="", max_length=16000)


def remote(request: dict):
    # Ship this deployed bridge source for this invocation, so Studio cannot
    # silently run an older bridge. Only the action JSON contains user input.
    source = Path(__file__).with_name("forge_bridge.py").read_text()
    bootstrap = "import json,sys,io; p=json.load(sys.stdin); sys.stdin=io.StringIO(json.dumps(p['request'])); exec(compile(p['source'],'forge_bridge.py','exec'),{'__name__':'__main__'})"
    import shlex
    try:
        result = subprocess.run(
            ["/usr/bin/ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5",
             "jfstudio@100.102.92.119", "/opt/homebrew/bin/python3 -c " + shlex.quote(bootstrap)],
            input=json.dumps({"source": source, "request": request}),
            text=True, capture_output=True, timeout=25, check=False,
        )
        if result.returncode:
            raise HTTPException(503, "Cannot reach Forge on Studio. Your saved chat is preserved; reconnect to check the request.")
        body = json.loads(result.stdout)
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError) as exc:
        raise HTTPException(503, "Studio did not respond. Reconnect to check the request before sending again.") from exc
    if not body.get("ok"):
        raise HTTPException(409, body.get("error", "Forge could not accept this request"))
    return body["data"]


@router.post("/request")
def chat(body: ChatRequest):
    if body.action != "list" and body.thread_id is None:
        raise HTTPException(422, "Conversation ID required")
    if body.action == "send" and body.message_id is None:
        raise HTTPException(422, "Message ID required")
    return remote(body.model_dump(mode="json"))
