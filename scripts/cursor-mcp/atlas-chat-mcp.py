#!/usr/bin/env python3
"""Cursor MCP server — chat with Studio Atlas.

Prefers Atlas6 portfolio conductor at http://127.0.0.1:8767 (Alicia tunnel).
Falls back to Atlas5 worker chat at :8766, then one-shot SSH into atlas-direct.
Prefer the dedicated `alicia` MCP for ledger/dispatch/gates; this remains the
chat-shaped entry used by existing Cursor sessions.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from urllib import request as urlrequest

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("atlas-chat")

ATLAS_SSH = os.environ.get("ATLAS_SSH_HOST", "jfstudio@100.93.125.5")
REMOTE_CHAT = os.environ.get(
    "ATLAS_REMOTE_CHAT",
    "~/atlas-direct/scripts/atlas_cursor_chat.py",
)
REMOTE_PYTHON = os.environ.get("ATLAS_REMOTE_PYTHON", "~/atlas-direct/.venv/bin/python")
ATLAS6_BASE = os.environ.get("ATLAS6_LOCAL_BASE", "http://127.0.0.1:8767")
LOCAL_BASE = os.environ.get("ATLAS_LOCAL_BASE", "http://127.0.0.1:8766")
LOCAL_CHAT = Path(__file__).with_name("atlas_cursor_chat.py")
LOCAL_PYTHON = Path(__file__).with_name("atlas-chat-mcp-venv") / "bin" / "python"


def _health(base: str) -> bool:
    try:
        with urlrequest.urlopen(f"{base.rstrip('/')}/api/healthz", timeout=2) as resp:
            return resp.status == 200
    except Exception:
        return False


def _tunnel_up() -> bool:
    return _health(LOCAL_BASE)


def _atlas6_chat(message: str, mode: str = "manager", reset_session: bool = False) -> dict:
    payload = json.dumps(
        {"message": message, "mode": mode, "reset_session": reset_session}
    ).encode()
    req = urlrequest.Request(
        f"{ATLAS6_BASE.rstrip('/')}/api/chat",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlrequest.urlopen(req, timeout=320) as resp:
            data = json.loads(resp.read().decode())
        reply = data.get("reply") or json.dumps(data)
        if data.get("atlas5_busy"):
            reply += "\n\n[Atlas5 busy — Atlas6 yielded resident model]"
        return {
            "ok": True,
            "content": reply,
            "session_id": "atlas6",
            "tool_calls": data.get("skills_run") or data.get("tool_calls"),
        }
    except Exception as exc:
        return {"ok": False, "error": f"atlas6 chat failed: {exc}"}


def _local_chat(payload: dict) -> dict:
    if not LOCAL_CHAT.is_file():
        return {"ok": False, "error": f"missing local chat bridge: {LOCAL_CHAT}"}
    py = str(LOCAL_PYTHON if LOCAL_PYTHON.is_file() else sys.executable)
    cmd = [py, str(LOCAL_CHAT), "--json-stdin", "--base", LOCAL_BASE]
    proc = subprocess.run(
        cmd,
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        timeout=int(payload.get("timeout_s") or 320),
    )
    if proc.returncode != 0 and not proc.stdout.strip():
        err = (proc.stderr or proc.stdout or "local chat failed").strip()
        return {"ok": False, "error": err[:2000]}
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        return {
            "ok": False,
            "error": f"bad JSON from local Atlas bridge: {proc.stdout[:500]} {proc.stderr[:500]}",
        }


def _ssh_chat(payload: dict) -> dict:
    cmd = [
        "ssh",
        "-o",
        "ConnectTimeout=15",
        "-o",
        "BatchMode=yes",
        ATLAS_SSH,
        f"cd {os.environ.get('ATLAS_REMOTE_REPO', '~/atlas-direct')} && "
        f"{REMOTE_PYTHON} {REMOTE_CHAT} --json-stdin",
    ]
    proc = subprocess.run(
        cmd,
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        timeout=int(payload.get("timeout_s") or 320),
    )
    if proc.returncode != 0 and not proc.stdout.strip():
        err = (proc.stderr or proc.stdout or "ssh failed").strip()
        return {"ok": False, "error": err[:2000]}
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        return {
            "ok": False,
            "error": f"bad JSON from Atlas bridge: {proc.stdout[:500]} {proc.stderr[:500]}",
        }


def _chat(payload: dict) -> dict:
    # Portfolio conductor first (Atlas6), then Atlas5 worker chat, then SSH.
    if _health(ATLAS6_BASE) and not payload.get("reset_only"):
        a6 = _atlas6_chat(
            str(payload.get("message") or ""),
            mode=str(payload.get("mode") or "manager"),
            reset_session=bool(payload.get("reset_session")),
        )
        if a6.get("ok"):
            return a6
    if _tunnel_up():
        result = _local_chat(payload)
        if result.get("ok") or not str(result.get("error", "")).startswith("missing"):
            return result
    return _ssh_chat(payload)


@mcp.tool()
def atlas_chat(
    message: str,
    mode: str = "manager",
    model: str = "vllm:qwen3.6-35b",
    reset_session: bool = False,
) -> str:
    """Send a message to Studio Atlas and return the reply.

    Prefers Atlas6 (:8767 / Alicia tunnel) — portfolio ledger + skills.
    Falls back to Atlas5 worker chat (:8766) then SSH into atlas-direct.
    mode=manager — skill/agent path (default). mode=direct — raw model chat.
    """
    result = _chat(
        {
            "message": message,
            "mode": mode,
            "model": model,
            "reset_session": reset_session,
        }
    )
    if not result.get("ok"):
        return f"Atlas error: {result.get('error', 'unknown')}"
    parts = [result.get("content") or ""]
    if result.get("tool_calls"):
        parts.append(f"\n[tool_calls={result.get('tool_calls')}]")
    parts.append(f"\n[session_id={result.get('session_id')}]")
    return "".join(parts)


@mcp.tool()
def atlas_reset_session() -> str:
    """Start a fresh Atlas chat session on Studio."""
    result = _chat({"reset_only": True, "mode": "manager"})
    if result.get("session_id"):
        return f"New Atlas session: {result['session_id']}"
    return f"Reset failed: {result.get('error', 'unknown')}"


if __name__ == "__main__":
    mcp.run()
