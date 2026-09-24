"""Local typed-chat transport to the installed Forge runtime on Studio."""
from __future__ import annotations

import asyncio
import json
import shlex
import struct
import subprocess
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field


def require_local_chat(request: Request):
    """Same local typed-input trust boundary as Alicia, with explicit CSRF checks.

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
    if request.headers.get("x-alicia-chat") != "forge":
        raise HTTPException(403, "Chat request header required")


router = APIRouter(prefix="/api/forge", dependencies=[Depends(require_local_chat)])


class ChatRequest(BaseModel):
    action: Literal["list", "create", "get", "send", "stop"]
    thread_id: UUID | None = None
    message_id: UUID | None = None
    message: str = Field(default="", max_length=16000)
    attachments: list[UUID] = Field(default_factory=list, max_length=10)


def local_runtime_source():
    return Path(__file__).with_name("forge_local.py").read_text()


def local_tools_source():
    return Path(__file__).with_name("forge_tools.py").read_text()


# Content-addressed worker installation on Studio; concurrent requests never
# overwrite code used by an active run. Only our deployed source is installed.
INSTALL_LOCAL = """
import hashlib,pathlib,uuid
code=p['local_source']
sources={'forge_local.py':code,'forge_tools.py':p['tools_source']}
root=pathlib.Path.home()/'.local/share/alicia-forge-chat/runtime'/hashlib.sha256(json.dumps(sources,sort_keys=True).encode()).hexdigest()
root.mkdir(parents=True,exist_ok=True)
for name,source in sources.items():
    dest=root/name
    if not dest.exists():
        temporary=root/('worker-'+uuid.uuid4().hex)
        temporary.write_text(source)
        temporary.replace(dest)
sys.path.insert(0,str(root))
"""



def remote(request: dict):
    # Ship this deployed bridge source for this invocation, so Studio cannot
    # silently run an older bridge. Only the action JSON contains user input.
    source = Path(__file__).with_name("forge_bridge.py").read_text()
    bootstrap = "import json,sys,io; p=json.load(sys.stdin); " + INSTALL_LOCAL + "sys.stdin=io.StringIO(json.dumps(p['request'])); exec(compile(p['source'],'forge_bridge.py','exec'),{'__name__':'__main__'})"
    import shlex
    try:
        result = subprocess.run(
            ["/usr/bin/ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5",
             "jfstudio@100.102.92.119", "/opt/homebrew/bin/python3 -c " + shlex.quote(bootstrap)],
            input=json.dumps({"source": source, "local_source": local_runtime_source(),
                              "tools_source": local_tools_source(), "request": request}),
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


@router.post("/upload/{thread_id}/{attachment_id}")
async def upload(thread_id: UUID, attachment_id: UUID, request: Request, name: str):
    """Stream framed chunks directly to Studio with SSH backpressure."""
    if not name or len(name) > 255:
        raise HTTPException(422, "Filename must contain 1–255 characters")
    source = Path(__file__).with_name("forge_bridge.py").read_text()
    bootstrap = "import json,sys; p=json.loads(sys.stdin.buffer.readline()); " + INSTALL_LOCAL + "ns={'__name__':'bridge'}; exec(compile(p['source'],'forge_bridge.py','exec'),ns); ns['stream_main'](p['request'],sys.stdin.buffer)"
    proc = await asyncio.create_subprocess_exec(
        "/usr/bin/ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5",
        "-o", "ServerAliveInterval=30", "-o", "ServerAliveCountMax=3",
        "jfstudio@100.102.92.119", "/opt/homebrew/bin/python3 -c " + shlex.quote(bootstrap),
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        metadata = {"source": source, "local_source": local_runtime_source(), "tools_source": local_tools_source(), "request": {
            "action": "upload", "thread_id": str(thread_id), "attachment_id": str(attachment_id), "name": name,
        }}
        proc.stdin.write((json.dumps(metadata) + "\n").encode())
        await proc.stdin.drain()
        async for chunk in request.stream():
            for offset in range(0, len(chunk), 1024 * 1024):
                block = chunk[offset:offset + 1024 * 1024]
                proc.stdin.write(struct.pack("!I", len(block)))
                proc.stdin.write(block)
                await proc.stdin.drain()
        # Only a complete HTTP body gets the end marker; disconnects cannot commit partial files.
        proc.stdin.write(struct.pack("!I", 0))
        await proc.stdin.drain()
        proc.stdin.close()
        output = await proc.stdout.read()
        await proc.wait()
        if proc.returncode:
            raise HTTPException(503, "Upload connection interrupted. Retry this file.")
        body = json.loads(output)
        if not body.get("ok"):
            raise HTTPException(409, body.get("error", "Upload failed. Retry this file."))
        return body["data"]
    except (OSError, json.JSONDecodeError) as exc:
        raise HTTPException(503, "Upload connection interrupted. Retry this file.") from exc
    finally:
        if proc.returncode is None:
            proc.kill()
            await proc.wait()
