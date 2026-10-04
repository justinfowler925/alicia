"""Alicia manager routing to Forge / Atlas / Scout / Hollywood.

Dry-run by default. Live launch is gated through Alicia's tool gate and only
enqueues work — it does not claim completion or acceptance.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

SPECIALISTS = ("atlas", "forge", "scout", "hollywood")

_ATLAS = re.compile(
    r"\b(?:salesforce|revops|apex|lwc|flow|metadata|soql|sfdc|partial sandbox|"
    r"permission set|validation rule|rev-\d+)\b",
    re.I,
)
_SCOUT = re.compile(
    r"\b(?:scrape|scraping|ingest|ingestion|crawl|crawler|zoom notes?|meeting notes?|"
    r"dedup|dedupe|catalog|index(?:ing)?|import data|data steward|rss|collector)\b",
    re.I,
)
_HOLLYWOOD = re.compile(
    r"\b(?:hollywood|video|audio|transcribe|transcription|render|voiceover|"
    r"soundscape|caption|ffmpeg|media studio|generate music|clone voice)\b",
    re.I,
)

_STUDIO_AGENT = Path.home() / ".local/bin/studio-agent"
_RECEIPTS = Path.home() / ".local/share/alicia-specialist-router"


def choose_specialist(task: str, specialist: str | None = None) -> tuple[str, str]:
    """Return (specialist, reason). Explicit choice wins when valid."""
    chosen = (specialist or "").strip().lower()
    if chosen:
        if chosen not in SPECIALISTS:
            raise ValueError(
                "Specialist must be atlas, forge, scout, or hollywood"
            )
        return chosen, "explicit"
    text = task or ""
    if _ATLAS.search(text):
        return "atlas", "salesforce_or_revops_keywords"
    if _SCOUT.search(text):
        return "scout", "scrape_or_data_keywords"
    if _HOLLYWOOD.search(text):
        return "hollywood", "media_keywords"
    return "forge", "default_general_worker"


def org_chart() -> dict[str, Any]:
    return {
        "manager": "alicia",
        "formerly": "brutus",
        "control_plane": ["alicia", "canon"],
        "specialists": {
            "atlas": "Salesforce / RevOps development only",
            "forge": "General-purpose local Gemma worker (Alicia #forge)",
            "scout": "Data scraping and organization",
            "hollywood": "Media studio",
        },
        "rule": "Alicia manages; specialists execute; one studio-agent at a time",
    }


def preview(task: str, specialist: str | None = None, **kwargs: Any) -> dict[str, Any]:
    chosen, reason = choose_specialist(task, specialist)
    return {
        "ok": True,
        "dry_run": True,
        "specialist": chosen,
        "reason": reason,
        "task": task,
        "cwd": kwargs.get("cwd") or "",
        "work_item": kwargs.get("work_item") or "",
        "read_only": bool(kwargs.get("read_only")),
        "would_launch": True,
        "org_chart": org_chart(),
        "note": "Preview only — set dry_run=false after Alicia gate approval to enqueue",
    }


def route_specialist(
    task: str,
    *,
    specialist: str | None = None,
    dry_run: bool = True,
    cwd: str = "",
    work_item: str = "",
    read_only: bool = False,
) -> dict[str, Any]:
    task = (task or "").strip()
    if not task or len(task) > 100_000:
        raise ValueError("Task must be 1–100000 characters")
    if dry_run:
        return preview(task, specialist, cwd=cwd, work_item=work_item, read_only=read_only)

    chosen, reason = choose_specialist(task, specialist)
    if chosen == "forge":
        result = _launch_forge(task, cwd=cwd, work_item=work_item)
    else:
        result = _launch_studio_agent(
            chosen, task, cwd=cwd, work_item=work_item, read_only=read_only
        )
    receipt = {
        "ok": True,
        "dry_run": False,
        "specialist": chosen,
        "reason": reason,
        "task": task[:4000],
        "cwd": cwd or "",
        "work_item": work_item or "",
        "read_only": bool(read_only),
        "launched_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "result": result,
        "accepted": False,
        "note": "Enqueued only — process success is not delivery acceptance",
    }
    _write_receipt(receipt)
    return receipt


def _launch_forge(task: str, *, cwd: str, work_item: str) -> dict[str, Any]:
    from . import forge_local

    root = Path(cwd).expanduser() if cwd else Path.home() / ".local/share/studio-agents/workspaces/forge"
    root.mkdir(parents=True, exist_ok=True)
    run = forge_local.enqueue(
        "forge",
        task,
        cwd=str(root.resolve()),
        work_item=work_item or f"alicia:{int(time.time())}",
    )
    forge_local.kick()
    return {
        "launcher": "forge_local",
        "run_id": run.get("id"),
        "status": run.get("status"),
        "surface": "http://127.0.0.1:8768/#forge",
    }


def _launch_studio_agent(
    agent: str,
    task: str,
    *,
    cwd: str,
    work_item: str,
    read_only: bool,
) -> dict[str, Any]:
    if not _STUDIO_AGENT.is_file():
        raise RuntimeError("studio-agent binary missing on Studio")
    cmd = [str(_STUDIO_AGENT), "launch", agent, "--prompt", task]
    if cwd:
        cmd.extend(["--cwd", str(Path(cwd).expanduser())])
    if work_item:
        cmd.extend(["--work-item", work_item])
    if read_only:
        cmd.append("--read-only")
    # Atlas writable builds need explicit requirements; force read-only plan unless provided.
    if agent == "atlas" and not read_only and "--requirement" not in cmd:
        cmd.append("--read-only")
        read_only = True
    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"studio-agent launch failed ({proc.returncode}): "
            f"{(proc.stderr or proc.stdout or '').strip()[:800]}"
        )
    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError("studio-agent returned non-JSON output") from exc
    return {
        "launcher": "studio-agent",
        "run_id": payload.get("id"),
        "status": payload.get("status"),
        "agent": payload.get("agent", agent),
        "read_only": read_only,
        "raw": {
            k: payload.get(k)
            for k in ("id", "agent", "status", "cwd", "work_item", "created")
            if k in payload
        },
    }


def _write_receipt(receipt: dict[str, Any]) -> Path:
    _RECEIPTS.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = _RECEIPTS / f"{int(time.time())}-{receipt['specialist']}.json"
    path.write_text(json.dumps(receipt, indent=2) + "\n")
    path.chmod(0o600)
    return path


def latest_receipts(limit: int = 10) -> list[dict[str, Any]]:
    if not _RECEIPTS.is_dir():
        return []
    files = sorted(_RECEIPTS.glob("*.json"), reverse=True)[: max(1, min(limit, 50))]
    out: list[dict[str, Any]] = []
    for path in files:
        try:
            out.append(json.loads(path.read_text()))
        except (OSError, json.JSONDecodeError):
            continue
    return out


if __name__ == "__main__":
    # CLI: python -m alicia.specialist_router --dry-run "fix a Flow"
    import argparse

    parser = argparse.ArgumentParser(description="Alicia specialist router")
    parser.add_argument("task")
    parser.add_argument("--specialist", choices=SPECIALISTS)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--cwd", default="")
    parser.add_argument("--work-item", default="")
    parser.add_argument("--read-only", action="store_true")
    args = parser.parse_args()
    print(
        json.dumps(
            route_specialist(
                args.task,
                specialist=args.specialist,
                dry_run=not args.live,
                cwd=args.cwd,
                work_item=args.work_item,
                read_only=args.read_only,
            ),
            indent=2,
        )
    )
    raise SystemExit(0)
