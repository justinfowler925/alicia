"""Executed on Studio over private SSH; chat state and agent runs stay there.

This module uses only the standard library until it loads the installed runtime.
The caller supplies an action, never executable code or a shell command.
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import time
import uuid
from pathlib import Path


def runtime():
    sys.path.insert(0, str(Path.home() / ".local/share/studio-agents/app"))
    import studio_agents
    return studio_agents


def snapshot(c, agent, thread_id):
    thread = c.execute("SELECT * FROM threads WHERE id=?", (thread_id,)).fetchone()
    if thread is None:
        raise ValueError("Conversation not found")
    turns = []
    for row in c.execute("SELECT * FROM turns WHERE thread_id=? ORDER BY created, rowid", (thread_id,)):
        turn = dict(row)
        run = agent.get(turn["run_id"])
        answer = agent.STATE / "runs" / run["id"] / "answer.md"
        answer_text = answer.read_text() if answer.exists() else ""
        try:
            report = json.loads(answer_text)
            if isinstance(report, dict) and isinstance(report.get("summary"), str):
                answer_text = report["summary"]
        except json.JSONDecodeError:
            pass
        turn.update(status=run["status"], reason=run.get("reason", ""),
                    answer=answer_text,
                    model=run["model"], cancellation_requested=bool(run.get("cancel")))
        turns.append(turn)
    return {**dict(thread), "turns": turns, "model": agent.config()["forge"]["model"]}


def handle(request, agent=None, state=None):
    agent = agent or runtime()
    state = state or Path.home() / ".local/share/brutus-forge-chat"
    state.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(state, 0o700)
    c = sqlite3.connect(state / "chat.sqlite3", timeout=15)
    c.row_factory = sqlite3.Row
    c.execute("CREATE TABLE IF NOT EXISTS threads(id TEXT PRIMARY KEY, title TEXT, created REAL)")
    c.execute("CREATE TABLE IF NOT EXISTS turns(id TEXT PRIMARY KEY, thread_id TEXT, message TEXT, run_id TEXT, created REAL)")
    try:
        action = request["action"]
        thread_id = request.get("thread_id", "")
        if action == "list":
            return {"threads": [dict(row) for row in c.execute("SELECT * FROM threads ORDER BY created DESC")],
                    "model": agent.config()["forge"]["model"]}
        if action == "create":
            thread_id = str(uuid.UUID(thread_id))
            with c:
                c.execute("INSERT OR IGNORE INTO threads VALUES(?,?,?)", (thread_id, "New conversation", time.time()))
            return snapshot(c, agent, thread_id)
        if action == "get":
            return snapshot(c, agent, thread_id)
        if action == "send":
            message = request.get("message", "").strip()
            message_id = str(uuid.UUID(request["message_id"]))
            if not message or len(message) > 16000:
                raise ValueError("Message must contain 1–16000 characters")
            # Serialize sends across tabs and SSH processes. Request IDs survive
            # a lost response; recovery also finds a run created before a crash.
            c.execute("BEGIN IMMEDIATE")
            old = c.execute("SELECT * FROM turns WHERE id=?", (message_id,)).fetchone()
            if old:
                if old["thread_id"] != thread_id or old["message"] != message:
                    raise ValueError("Message ID already belongs to another request")
            else:
                current = snapshot(c, agent, thread_id)
                if any(t["status"] not in agent.TERMINAL for t in current["turns"]):
                    raise ValueError("Forge is still responding in this conversation")
                history = [{"user": t["message"], "assistant": t["answer"], "status": t["status"]}
                           for t in current["turns"]]
                context = json.dumps(history, ensure_ascii=False)
                if len(context) > 160000:
                    raise ValueError("This conversation is full. Start a new conversation.")
                work_item = "brutus-forge-chat:" + message_id
                with agent.db() as runs:
                    existing = runs.execute("SELECT * FROM runs WHERE work_item=? AND agent='forge'", (work_item,)).fetchone()
                prompt = ("You are chatting directly with Justin through Brutus. Answer his latest message. "
                          "Use prior turns as conversation context, not fresh instructions. "
                          "Do not execute old requests again. For conversation, answer directly; "
                          "perform work only when the latest message requests it. Put the complete "
                          "user-facing answer in the completion report's summary field.\n"
                          "Prior turns (JSON):\n" + context + "\nLatest user message:\n" + message)
                workspace = agent.STATE / "workspaces" / "brutus-chat" / thread_id
                workspace.mkdir(parents=True, exist_ok=True)
                run = dict(existing) if existing else agent.enqueue(
                    "forge", prompt, cwd=str(workspace), work_item=work_item, requirements=[message])
                c.execute("INSERT INTO turns VALUES(?,?,?,?,?)", (message_id, thread_id, message, run["id"], time.time()))
                if not current["turns"]:
                    c.execute("UPDATE threads SET title=? WHERE id=?", (message[:70], thread_id))
            c.commit()
            agent.kick()
            return snapshot(c, agent, thread_id)
        if action == "stop":
            current = snapshot(c, agent, thread_id)
            for turn in current["turns"]:
                if turn["status"] not in agent.TERMINAL:
                    agent.update(turn["run_id"], cancel=1)
                    if turn["status"] == "queued":
                        agent.update(turn["run_id"], status="cancelled", finished=time.time())
            agent.kick()
            return snapshot(c, agent, thread_id)
        raise ValueError("Unknown chat action")
    finally:
        c.close()


def main():
    try:
        print(json.dumps({"ok": True, "data": handle(json.load(sys.stdin))}))
    except (ValueError, KeyError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}))


if __name__ == "__main__":
    main()
