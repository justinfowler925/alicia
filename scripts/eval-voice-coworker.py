#!/usr/bin/env python3
"""Run the real ConversationManager against scratch state; print JSON receipts."""

from __future__ import annotations

import argparse
import json
import tempfile
import statistics
from pathlib import Path

from alicia.client import AtlasClient
from alicia.config import load_config
from alicia.conversation import ConversationManager
from alicia.memory import MemoryStore
from alicia.session import SessionStore
from alicia.todos import TodoStore
from alicia.tools import Tool, ToolRegistry
from alicia.voice_eval import run_scenarios


def synthetic_registry() -> ToolRegistry:
    """Fixed non-sensitive facts: exercise tool judgment without exporting real work."""
    registry = ToolRegistry()
    registry.register(Tool(
        "get_work_surface",
        "Return the current work surface and its one next decision.",
        {"type": "object", "properties": {}},
        lambda: {
            "headline": "one decision",
            "next_decision": "REV-490 needs ten autonomous voice acceptance sessions.",
            "needs_you": [{"ticket": "REV-490", "title": "Voice acceptance", "reason": "evaluation"}],
            "working": [],
        },
    ))
    registry.register(Tool(
        "get_thread",
        "Look up one work thread by ticket identifier.",
        {
            "type": "object",
            "properties": {"ticket": {"type": "string"}, "thread_id": {"type": "string"}},
        },
        lambda ticket="", thread_id="": {
            "ticket": ticket or thread_id or "REV-490",
            "title": "Alicia conversational voice acceptance",
            "status": "in progress",
            "remaining": "autonomous voice evaluation and turn-taking replacement",
        },
    ))
    return registry


class SyntheticEvalManager(ConversationManager):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._eval_registry = synthetic_registry()

    def _registry(self):
        return self._eval_registry


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    parser.add_argument("--runs", type=int, default=3)
    args = parser.parse_args()
    if args.runs < 1:
        parser.error("--runs must be at least 1")
    cfg = load_config()
    reports = []
    for run_number in range(1, args.runs + 1):
        with tempfile.TemporaryDirectory(prefix="alicia-voice-eval-") as tmp:
            root = Path(tmp)
            sessions = SessionStore(root / "sessions.sqlite")
            manager = SyntheticEvalManager(
                AtlasClient(cfg),
                cfg,
                sessions,
                memory=MemoryStore(root / "memory.sqlite"),
                todos=TodoStore(root / "todos.sqlite"),
            )
            report = run_scenarios(
                lambda: sessions.open_session(kind="eval"),
                lambda sid, text: manager.handle(sid, text, channel="voice", read_only=True, wait=True),
            )
            report["run"] = run_number
            reports.append(report)
    passed = sum(int(report["passed"]) for report in reports)
    denominator = sum(int(report["scenario_count"]) for report in reports)
    report = {
        "mode": "canonical_manager",
        "run_count": args.runs,
        "scenario_evaluations": denominator,
        "passed": passed,
        "failed": denominator - passed,
        "pass_rate": round(passed / denominator, 3) if denominator else 0.0,
        "mean_turn_latency_s": round(statistics.mean(float(r["mean_turn_latency_s"]) for r in reports), 3),
        "runs": reports,
    }
    rendered = json.dumps(report, indent=2, sort_keys=True)
    if args.output:
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    raise SystemExit(0 if report["failed"] == 0 and denominator > 0 else 1)


if __name__ == "__main__":
    main()
