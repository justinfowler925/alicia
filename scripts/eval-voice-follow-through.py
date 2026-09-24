#!/usr/bin/env python3
"""Real-model regression for accepting Alicia's offered next step."""

from __future__ import annotations

import argparse
import json
import re
import statistics
import tempfile
import time
from pathlib import Path

from alicia.client import AtlasClient
from alicia.config import load_config
from alicia.conversation import ConversationManager
from alicia.memory import MemoryStore
from alicia.session import SessionStore
from alicia.todos import TodoStore
from alicia.tools import Tool, ToolRegistry


class FollowThroughManager(ConversationManager):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.lookups: list[str] = []

    def _registry(self) -> ToolRegistry:
        registry = ToolRegistry()
        registry.register(
            Tool(
                name="get_thread",
                description="Look up one open thread by REV ticket id.",
                parameters={
                    "type": "object",
                    "properties": {
                        "external_id": {"type": "string"},
                        "thread_id": {"type": "string"},
                    },
                },
                fn=self._get_thread,
            )
        )
        return registry

    def _get_thread(self, external_id: str = "", thread_id: str = "") -> dict:
        ticket = external_id or thread_id
        self.lookups.append(ticket)
        return {
            "ticket": ticket,
            "status": "in review",
            "decision": "approve or reject the rollout",
            "detail": "The implementation and automated checks are complete.",
        }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs", type=int, default=5)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.runs < 1:
        parser.error("--runs must be at least 1")

    cfg = load_config()
    rows: list[dict] = []
    for number in range(1, args.runs + 1):
        with tempfile.TemporaryDirectory(prefix="alicia-follow-through-") as tmp:
            root = Path(tmp)
            sessions = SessionStore(root / "sessions.sqlite")
            manager = FollowThroughManager(
                AtlasClient(cfg),
                cfg,
                sessions,
                memory=MemoryStore(root / "memory.sqlite"),
                todos=TodoStore(root / "todos.sqlite"),
            )
            sid = sessions.open_session(kind="eval")
            sessions.append_turn(
                sid,
                "user",
                "I wanna know what the fuck I need to be doing right now",
                channel="voice",
            )
            sessions.append_turn(
                sid,
                "alicia",
                "Top of the pile is REV-507. Want me to pull up REV-507's details so you can decide?",
                channel="voice",
            )
            started = time.monotonic()
            result = manager.handle(
                sid, "Go ahead. I'm listening", channel="voice", read_only=True, wait=True
            )
            elapsed = time.monotonic() - started
            folded = f"{result.reply}\n{result.spoken}".casefold()
            failures: list[str] = []
            if manager.lookups != ["REV-507"]:
                failures.append(f"expected_one_REV-507_lookup:got:{manager.lookups}")
            if result.tool != "get_thread":
                failures.append(f"expected_tool:get_thread:got:{result.tool or 'none'}")
            if not any(word in folded for word in ("review", "decision", "rollout")):
                failures.append("missing_lookup_detail")
            for phrase in ("top of the pile", "want me to", "pull up rev-507"):
                if phrase in folded:
                    failures.append(f"repeated_or_reasked:{phrase}")
            if re.search(
                r"(?:\b(?:to|the|a|an|or|and|which)|[—-])\s*$",
                result.reply,
                re.IGNORECASE,
            ):
                failures.append("incomplete_sentence")
            rows.append(
                {
                    "run": number,
                    "pass": not failures,
                    "failures": failures,
                    "tool": result.tool,
                    "lookups": manager.lookups,
                    "reply": result.reply,
                    "spoken": result.spoken,
                    "meta": sessions.transcript(sid)[-1].meta,
                    "elapsed_s": round(elapsed, 3),
                }
            )

    passed = sum(row["pass"] for row in rows)
    report = {
        "eval": "voice_follow_through_exact_transcript",
        "runs": args.runs,
        "passed": passed,
        "failed": args.runs - passed,
        "mean_latency_s": round(statistics.mean(row["elapsed_s"] for row in rows), 3),
        "rows": rows,
    }
    rendered = json.dumps(report, indent=2, sort_keys=True)
    if args.output:
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    raise SystemExit(0 if passed == args.runs else 1)


if __name__ == "__main__":
    main()
