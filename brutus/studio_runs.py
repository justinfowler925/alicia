"""Brutus reader for the durable Studio observer; never starts a feed job."""

from __future__ import annotations

import copy
import datetime as dt
import json
import os
import re
import shlex
import subprocess
import threading
import time

from fastapi import APIRouter, HTTPException
from fastapi.responses import PlainTextResponse

from . import process_control
from .paths import state_path
from .studio_collector import UTC, calendar_times, parse_time, stamp

router = APIRouter(prefix="/api/studio-runs", tags=["studio-runs"])
_lock = threading.Lock()
_cached = None
_checked = 0.0
_error = ""


def remote(*args):
    host = os.environ.get("BRUTUS_STUDIO_SSH", "100.102.92.119")
    if not re.fullmatch(r"[a-zA-Z0-9_.@-]+", host) or host.startswith("-"):
        raise ValueError("Invalid Studio SSH target")
    result = subprocess.run(
        [
            "/usr/bin/ssh",
            "-o",
            "BatchMode=yes",
            "-o",
            "ConnectTimeout=5",
            host,
            " ".join(shlex.quote(a) for a in args),
        ],
        capture_output=True,
        text=True,
        timeout=12,
        check=False,
    )
    if result.returncode:
        raise RuntimeError("Studio evidence unavailable over SSH")
    return result.stdout


def assess(snapshot, now=None, error=""):
    now = now or dt.datetime.now(UTC)
    result = copy.deepcopy(snapshot)
    collected = parse_time(result.get("collected_at"))
    disconnected = not collected or (now - collected).total_seconds() > 180 or bool(error)
    result.update(connection="stale" if disconnected else "live", error=error, checked_at=stamp(now))
    summary = {key: 0 for key in ("healthy", "failed", "stale", "running", "unknown", "never_ran")}
    for job in result.get("jobs", []):
        job["next_run_at"] = None
        job["next_run_note"] = "Not available"
        status = job.get("status", "unknown")
        last = parse_time(job.get("last_run_at"))
        schedule = job.get("schedule", {})
        due = None
        try:
            if schedule.get("kind") == "calendar":
                prev, nxt = calendar_times(schedule["calendar"], schedule["timezone"], now)
                job["next_run_at"] = nxt if job.get("loaded") is not False else None
                job["next_run_note"] = (
                    "Scheduled wall time" if job["next_run_at"] else "Scheduler is not loaded"
                )
                due = parse_time(prev)
            elif schedule.get("kind") == "interval":
                job["next_run_note"] = "Interval phase not exposed by launchd"
            elif schedule.get("kind") == "service":
                job["next_run_note"] = "Continuous / event driven"
        except (ValueError, KeyError, TypeError):
            job["notes"].append("Schedule could not be evaluated")
        age = (now - last).total_seconds() if last else None
        threshold = job.get("stale_after_seconds", max(3600, schedule.get("seconds", 43200) * 2))
        overdue = bool(
            last
            and ((due and last < due and (now - due).total_seconds() > 3600) or (not due and age > threshold))
        )
        stuck = (
            status == "running"
            and age is not None
            and age > job.get("max_runtime_seconds", 7200)
            and schedule.get("kind") != "service"
        )
        if disconnected:
            health = "stale"
        elif status == "failure":
            health = "failed"
        elif stuck or overdue and status != "running":
            health = "stale"
        elif job.get("loaded") is False:
            health = "unknown"
        elif status == "running":
            health = "running"
        elif status == "success" and last:
            health = "healthy"
        elif not last and any("Never ran" in n for n in job.get("notes", [])):
            health = "never_ran"
        else:
            health = "unknown"
        job["health"] = health
        job["health_reason"] = (
            "Studio observation is unavailable or older than 3 minutes"
            if disconnected
            else "Run exceeded its expected duration"
            if stuck
            else "Latest receipt is older than the expected cadence"
            if overdue
            else "Scheduler is not loaded"
            if job.get("loaded") is False
            else ""
        )
        summary[health] += 1
    result["summary"] = summary
    return result


@router.get("")
def snapshot():
    global _cached, _checked, _error
    with _lock:
        if time.monotonic() - _checked > 20:
            try:
                data = json.loads(
                    remote("cat", "/Users/jfstudio/.local/share/brutus-studio-runs/snapshot.json")
                )
                if data.get("schema_version") != 1 or not isinstance(data.get("jobs"), list):
                    raise ValueError("Invalid Studio snapshot")
                _cached, _error = data, ""
                # The cache is an availability fallback, never the authority.
                path = state_path("studio-runs-cache.json")
                temp = path.with_suffix(".tmp")
                temp.write_text(json.dumps(data))
                temp.chmod(0o600)
                temp.replace(path)
            except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired):
                _error = "Studio could not be reached. Showing the last collected evidence."
                if _cached is None:
                    try:
                        _cached = json.loads(state_path("studio-runs-cache.json").read_text())
                    except (OSError, ValueError):
                        _cached = {"jobs": [], "collected_at": None, "coverage": {}}
            _checked = time.monotonic()
        return assess(_cached or {"jobs": []}, error=_error)


@router.post("/{job_id}/{action}")
def control(job_id: str, action: str):
    """Turn a scheduled job off, back on, or run it now.

    The board listed twenty of these with a status and a next-run time and no
    way to touch any of them, which is a status page dressed as a console.
    """
    jobs = (snapshot() or {}).get("jobs") or []
    try:
        return process_control.studio_job_action(
            job_id, action, jobs, os.environ.get("BRUTUS_STUDIO_SSH", "100.102.92.119")
        )
    except process_control.ControlError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except subprocess.TimeoutExpired as exc:
        raise HTTPException(status_code=504, detail="The Studio did not answer in time") from exc


@router.get("/{job_id}/log", response_class=PlainTextResponse)
def log(job_id: str, kind: str = "stdout"):
    if kind not in {"stdout", "stderr", "receipt"}:
        raise HTTPException(400, "Unknown log kind")
    data = snapshot()
    job = next((j for j in data["jobs"] if j["id"] == job_id), None)
    if not job or kind not in job.get("logs", {}):
        raise HTTPException(404, "No registered log for this job")
    try:
        content = remote(
            "/usr/bin/python3",
            "/Users/jfstudio/.local/share/brutus-studio-runs/collector.py",
            "--log-job",
            job_id,
            "--kind",
            kind,
        )
        return PlainTextResponse(
            content, headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"}
        )
    except (OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
        raise HTTPException(503, "Studio log unavailable; retry when Studio is reachable") from exc
