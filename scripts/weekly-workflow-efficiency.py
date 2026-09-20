#!/usr/bin/env python3
"""Studio-owned weekly workflow-efficiency collector."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import socket
import tempfile
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any

UTC = dt.UTC
DEFAULT_SCORECARD_URL = "http://jfowler3:8769/api/workflow/scorecard"
DEFAULT_SLACK_HEALTH_URL = "http://127.0.0.1:8767/health"
DEFAULT_CRO_SNAPSHOTS = Path.home() / "Projects/cro-suite/state/snapshots"
DEFAULT_OUTPUT_DIR = Path.home() / ".local/share/workflow-efficiency/reports"


def fetch_json(url: str, timeout: float = 8) -> dict[str, Any]:
    with urllib.request.urlopen(url, timeout=timeout) as response:
        value = json.load(response)
    if not isinstance(value, dict):
        raise TypeError(f"{url} did not return a JSON object")
    return value


def source_call(url: str, loader: Callable[[str], dict[str, Any]]) -> dict[str, Any]:
    try:
        return {"status": "ok", "url": url, "data": loader(url)}
    except (OSError, TypeError, ValueError, json.JSONDecodeError, urllib.error.URLError) as exc:
        return {
            "status": "unavailable",
            "url": url,
            "reason": f"{type(exc).__name__}: {str(exc)[:240]}",
        }


def latest_cro_snapshot(directory: Path) -> dict[str, Any]:
    paths = sorted(directory.glob("????-??-??-context.json"), reverse=True)
    if not paths:
        return {"status": "unavailable", "reason": f"no context snapshots in {directory}"}
    path = paths[0]
    try:
        body = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {"status": "unavailable", "reason": f"{type(exc).__name__}: {exc}"}
    return {"status": "ok", "path": str(path), "data": body}


def _prior_report(output_dir: Path) -> dict[str, Any] | None:
    for path in sorted(output_dir.glob("*.json"), reverse=True):
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(value, dict):
            return value
    return None


def _one_action(scorecard: dict[str, Any]) -> dict[str, Any]:
    metrics = scorecard.get("metrics") if isinstance(scorecard, dict) else {}
    broad = metrics.get("broad_root_starts", {}) if isinstance(metrics, dict) else {}
    pushes = metrics.get("completion_push_sessions", {}) if isinstance(metrics, dict) else {}
    if (broad.get("rate") or 0) >= 0.05:
        return {
            "id": "enforce-canonical-repository-routing",
            "owner": "workflow-control",
            "target": "broad-root repository starts below 5%",
            "guardrail": "read-only cross-repository work remains allowed",
            "stop_condition": "remove or revise after seven days if valid work is blocked",
        }
    if (pushes.get("rate") or 0) >= 0.15:
        return {
            "id": "reduce-completion-pushes",
            "owner": "workflow-control",
            "target": "completion/correction pushes below 15%",
            "guardrail": "do not claim completion without delivery evidence",
            "stop_condition": "revise after seven days if verified delivery falls",
        }
    return {
        "id": "no-change",
        "owner": "workflow-control",
        "target": "preserve current measured performance",
        "guardrail": "do not create a new ritual or component",
        "stop_condition": "reassess at the next weekly run",
    }


def collect_report(
    *,
    now: dt.datetime,
    scorecard_url: str,
    slack_health_url: str,
    cro_snapshots: Path,
    output_dir: Path,
    loader: Callable[[str], dict[str, Any]] = fetch_json,
) -> dict[str, Any]:
    agent_source = source_call(scorecard_url, loader)
    slack_source = source_call(slack_health_url, loader)
    cro_source = latest_cro_snapshot(cro_snapshots)
    scorecard = agent_source.get("data", {}) if agent_source["status"] == "ok" else {}
    cro_data = cro_source.get("data", {}) if cro_source["status"] == "ok" else {}
    productivity = cro_data.get("productivity", {}) if isinstance(cro_data, dict) else {}
    cro_sources = cro_data.get("sources", {}) if isinstance(cro_data, dict) else {}
    previous = _prior_report(output_dir)
    scorecard_coverage = scorecard.get("source_coverage", {})
    coverage = {
        "codex": bool(scorecard_coverage.get("codex")),
        "claude": bool(scorecard_coverage.get("claude")),
        "cursor": bool(scorecard_coverage.get("cursor")),
        "canon": bool(scorecard_coverage.get("canon")),
        "slack": slack_source["status"] == "ok",
        "email": cro_sources.get("gmail", {}).get("status") == "ok",
        "calendar": cro_sources.get("calendar", {}).get("status") == "ok",
    }
    return {
        "schema_version": 1,
        "generated_at": now.astimezone(UTC).isoformat(),
        "host": socket.gethostname(),
        "window": scorecard.get(
            "window",
            {
                "start": (now.astimezone(UTC) - dt.timedelta(days=7)).isoformat(),
                "end": now.astimezone(UTC).isoformat(),
            },
        ),
        "source_coverage": coverage,
        "gaps": sorted(name for name, available in coverage.items() if not available),
        "sources": {"agents_and_canon": agent_source, "slack": slack_source, "cro": cro_source},
        "scorecard": scorecard,
        "communication": {
            "meetings": productivity.get("meetings"),
            "meeting_hours_internal": productivity.get("meeting_hours_internal"),
            "meeting_hours_external": productivity.get("meeting_hours_external"),
            "emails_sent": productivity.get("emails_sent"),
            "emails_received": productivity.get("emails_received"),
            "conflicts_next_72h": productivity.get("conflicts_next_72h"),
        },
        "previous_generated_at": previous.get("generated_at") if previous else None,
        "previous_metrics": previous.get("scorecard", {}).get("metrics") if previous else None,
        "recommended_action": _one_action(scorecard),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--scorecard-url", default=os.environ.get("WORKFLOW_SCORECARD_URL", DEFAULT_SCORECARD_URL)
    )
    parser.add_argument(
        "--slack-health-url",
        default=os.environ.get("WORKFLOW_SLACK_HEALTH_URL", DEFAULT_SLACK_HEALTH_URL),
    )
    parser.add_argument("--cro-snapshots", type=Path, default=DEFAULT_CRO_SNAPSHOTS)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    args = parser.parse_args()
    now = dt.datetime.now(UTC)
    report = collect_report(
        now=now,
        scorecard_url=args.scorecard_url,
        slack_health_url=args.slack_health_url,
        cro_snapshots=args.cro_snapshots,
        output_dir=args.output_dir,
    )
    args.output_dir.mkdir(parents=True, exist_ok=True)
    target = args.output_dir / f"{now.astimezone().date().isoformat()}.json"
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=args.output_dir, delete=False
    ) as handle:
        json.dump(report, handle, indent=2, sort_keys=True)
        handle.write("\n")
        temporary = Path(handle.name)
    temporary.replace(target)
    # Keep the existing efficiency receipt and add the requested work inventory.
    from weekly_work_recap import collect, write_report
    recap = collect(now, report)
    recap_status = write_report(recap, args.output_dir / "work")
    print(
        json.dumps(
            {
                "report": str(target),
                "work_recap": recap_status,
                "gaps": report["gaps"],
                "recommended_action": report["recommended_action"],
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
