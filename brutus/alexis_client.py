"""Brutus adapter for the shared brain; tool execution stays local."""
from __future__ import annotations

import json
import sqlite3

import httpx

from .brain import BRAIN_FREE_WRITES, BRAIN_READS, _run_tool
from .paths import state_path


def execute_once(call_id, invoke):
    """A retried central turn must never repeat a local write after a lost reply."""
    conn = sqlite3.connect(state_path("alexis-tool-calls.sqlite"), timeout=10)
    try:
        with conn:
            conn.execute("CREATE TABLE IF NOT EXISTS calls (id TEXT PRIMARY KEY, result TEXT)")
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT result FROM calls WHERE id=?", (call_id,)).fetchone()
            if row:
                return json.loads(row[0]) if row[0] else {"ok": False, "error": "A prior tool execution has no confirmed receipt. Inspect it; do not repeat automatically."}
            conn.execute("INSERT INTO calls VALUES (?,NULL)", (call_id,))
        result = invoke()
        with conn:
            conn.execute("UPDATE calls SET result=? WHERE id=?", (json.dumps(result, default=str), call_id))
        return result
    finally:
        conn.close()


def shared_reply(cfg, registry, *, session_id, turn_id, message, channel,
                 standing_notes, on_propose, on_tool_result, recall):
    schemas = [s for s in registry.list_schemas() if s["name"] in (*BRAIN_READS, *BRAIN_FREE_WRITES)]
    schemas += [
        {"name": "recall", "description": "Search earlier Brutus history", "parameters": {"type": "object", "properties": {"q": {"type": "string"}}}},
        {"name": "propose_action", "description": "Prepare a gated action for owner review, never execute it", "parameters": {"type": "object", "properties": {"tool": {"type": "string"}, "args": {"type": "object"}}}},
    ]
    meta = {"brain": True, "backend": "alexis_shared", "tools": []}
    try:
        with httpx.Client(base_url=cfg.alexis_brain_url.rstrip("/"), timeout=150,
                          headers={"Authorization": f"Bearer {cfg.alexis_brain_token}"}) as client:
            response = client.post("/v1/turns", json={
                "session_id": session_id, "request_id": f"brutus:{session_id}:{turn_id}",
                "message": message, "channel": channel, "context": standing_notes[:12000], "tools": schemas,
                "supersede": True,
            })
            for _ in range(9):
                response.raise_for_status()
                result = response.json()
                meta["central_turn_id"] = result["turn_id"]
                meta["central_session_id"] = result["session_id"]
                if result["status"] == "done":
                    meta["brain_version"] = result["brain_version"]
                    meta["ms"] = result.get("model_ms")
                    return result["reply"], meta
                if result["status"] != "tool":
                    raise ValueError("Shared turn is not ready")
                call = result["tool"]
                meta["tools"].append(call["name"])
                payload = execute_once(call["call_id"], lambda call=call: _run_tool(
                    registry, call["name"], call["args"], channel=channel,
                    on_propose=on_propose, on_tool_result=on_tool_result, recall=recall))
                response = client.post(f"/v1/turns/{result['turn_id']}/tool-result",
                                       json={"call_id": call["call_id"], "result": payload})
        raise ValueError("Tool budget exhausted")
    except (httpx.HTTPError, ValueError, KeyError):
        return "I couldn't reach a complete answer from my shared brain. Please try again.", {**meta, "error": "alexis_unavailable"}
