"""Read-only Studio discovery. Standalone Python 3.9+; no Alicia/dependency imports.

Run on Studio every minute. Only this observer's snapshot is written. Existing
jobs, credentials, scheduler definitions and receipt files are never modified.
"""

from __future__ import annotations

import argparse
import datetime as dt
import fcntl
import hashlib
import json
import os
import plistlib
import re
import subprocess
from pathlib import Path
from zoneinfo import ZoneInfo

UTC = dt.timezone.utc  # noqa: UP017 -- deployed with macOS Python 3.9
PREFIXES = ("com.clearspeed.", "com.jfstudio.", "com.fowlerbrain.", "com.justinfowler.")
OBSERVER = "com.jfstudio.brutus-studio-runs"
STATE = Path.home() / ".local/share/brutus-studio-runs"
NAMES = {
    "com.clearspeed.sled-intel-sweep": (
        "SLED intelligence sweep",
        "News RSS → SLED signals, Salesforce and Intel publication",
    ),
    "com.clearspeed.nv-sled-intel": (
        "Nevada SLED daily brief",
        "Nevada sources → research, Salesforce, Nucleus and delivery receipt",
    ),
    "com.jfstudio.cro-revenue-intelligence": (
        "Revenue intelligence / RevOps",
        "Salesforce and revenue sources → revenue intelligence publication",
    ),
    "com.jfstudio.cro-morning-brief": ("CRO morning brief", "Revenue and government sources → daily brief"),
    "com.jfstudio.cro-gov-affairs-watch": (
        "Government affairs watch",
        "Government sources → weekly watch publication",
    ),
}


# Classification is separate from health: a failed feed must stay visible.
# Legacy journals record the pre-June Atlas scheduler, not current obligations.
FEED_IDS = {
    "com.jfstudio.atlas-trust-center-policy-sync",
    "com.jfstudio.nucleus-grant-knowledge",
}
CATEGORIES = {"feed", "sandbox", "service", "maintenance", "history", "inactive", "unclassified"}


def classify(job, descriptor=None):
    descriptor = descriptor or {}
    jid = job["id"]
    explicit = descriptor.get("category")
    if explicit in CATEGORIES:
        category, reason = explicit, descriptor.get("category_reason", "Registered job category")
    elif (
        jid.startswith("atlas-cron:")
        and jid.split(":", 1)[1]
        in {
            "auto-pickup",
            "daily-memory-summary",
            "deepseek-probe",
            "delivery-watchdog",
            "deploy-verify",
            "heartbeat-health-check-retry",
            "heartbeat-health-check",
            "heartbeat-tasks",
            "list-reconcile",
            "liveness-tick",
            "postmortem-distill",
            "sensor-poll",
            "sf-steering",
            "trophy-snapshot",
            "watchdog-tick",
            "weekly-cleanup",
        }
        and (not job.get("last_run_at") or job["last_run_at"] < "2026-06-05")
    ):
        category, reason = "history", "Legacy Atlas scheduler journal; active replacements use launchd"
    elif job.get("loaded") is False:
        category, reason = "inactive", "Scheduler is not loaded; retained for inspection"
    elif jid.endswith("-partial"):
        category, reason = "sandbox", "Sandbox validation job; separate from production feeds"
    elif jid.startswith(("com.clearspeed.", "com.jfstudio.cro-")) or jid in FEED_IDS:
        category, reason = "feed", "Scheduled data ingestion, synchronization or publication"
    elif job.get("schedule", {}).get("kind") == "service":
        category, reason = "service", "Supporting continuous service"
    elif jid.startswith(
        ("com.fowlerbrain.probe.", "com.jfstudio.atlas", "com.justinfowler.modelarchive.")
    ) or jid in {
        "com.fowlerbrain.sync",
        "com.jfstudio.fowler-brain-ci",
        "com.jfstudio.secrets-liveness",
        "com.jfstudio.skill-release-verification",
    }:
        category, reason = "maintenance", "Host maintenance, validation or health check"
    else:
        category, reason = "unclassified", "New job: register category in jobs.json"
    job.update(category=category, category_reason=reason)


def stamp(value=None):
    return (value or dt.datetime.now(UTC)).isoformat()


def parse_time(value):
    try:
        result = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))  # noqa: FURB162 -- Python 3.9
        return result.astimezone(UTC) if result.tzinfo else None
    except (ValueError, TypeError):
        return None


def read_json(path, default=None):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return default


def atomic(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(value, indent=2) + "\n")
    temp.chmod(0o600)
    temp.replace(path)


def tail(path, size=32000):
    with Path(path).open("rb") as f:
        f.seek(0, 2)
        f.seek(max(0, f.tell() - size))
        return f.read(size).decode("utf-8", "replace")


def redact(text):
    text = re.sub(r"(?i)(bearer\s+)[\w.+/=-]+", r"\1[redacted]", text)
    text = re.sub(
        r"(?i)((?:token|secret|password|api[_-]?key|authorization)[\w_-]*[\"']?\s*[:=]\s*[\"']?)[^\s\"',}]+",
        r"\1[redacted]",
        text,
    )
    return re.sub(r"\b(?:sk-|ghp_|gho_|xox[baprs]-)[A-Za-z0-9_-]+", "[redacted]", text)


def calendar_times(calendar, timezone, now):
    """Previous/next scheduled wall time, preserving DST gaps and both folds."""
    zone = ZoneInfo(timezone)
    entries = calendar if isinstance(calendar, list) else [calendar]
    local = now.astimezone(zone)
    previous = future = None
    for direction in (-1, 1):
        found = None
        for offset in range(370):
            day = local.date() + dt.timedelta(days=offset * direction)
            candidates = []
            for rule in entries:
                if any(k not in {"Month", "Day", "Weekday", "Hour", "Minute"} for k in rule):
                    raise ValueError("unsupported calendar field")
                if rule.get("Month", day.month) != day.month or rule.get("Day", day.day) != day.day:
                    continue
                if "Weekday" in rule and rule["Weekday"] % 7 != (day.weekday() + 1) % 7:
                    continue
                for hour in [rule["Hour"]] if "Hour" in rule else range(24):
                    for minute in [rule["Minute"]] if "Minute" in rule else range(60):
                        for fold in (0, 1):
                            wall = dt.datetime.combine(day, dt.time(hour, minute)).replace(
                                tzinfo=zone, fold=fold
                            )
                            utc = wall.astimezone(UTC)
                            if utc.astimezone(zone).replace(tzinfo=None) != wall.replace(tzinfo=None):
                                continue
                            if (direction < 0 and utc <= now) or (direction > 0 and utc > now):
                                candidates.append(utc)
            if candidates:
                found = max(candidates) if direction < 0 else min(candidates)
                break
        if direction < 0:
            previous = found
        else:
            future = found
    return stamp(previous) if previous else None, stamp(future) if future else None


def schedule(plist, timezone):
    cal = plist.get("StartCalendarInterval")
    interval = plist.get("StartInterval")
    if cal is not None:
        entries = cal if isinstance(cal, list) else [cal]
        days = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
        labels = []
        for item in entries:
            h = str(item["Hour"]).zfill(2) if "Hour" in item else "every hour"
            m = str(item.get("Minute", "*")).zfill(2)
            day = days[item["Weekday"]] if "Weekday" in item else "Daily"
            if "Day" in item or "Month" in item:
                day = f"Month {item.get('Month', '*')} day {item.get('Day', '*')}"
            labels.append(f"{day} {h}:{m}")
        return {"kind": "calendar", "calendar": cal, "timezone": timezone, "label": "; ".join(labels)}
    if interval:
        return {
            "kind": "interval",
            "seconds": interval,
            "timezone": timezone,
            "label": f"Every {interval} seconds",
            "note": "Next fire time is not exposed by launchd",
        }
    return {"kind": "service", "timezone": timezone, "label": "Continuous / on demand"}


def base_job(job_id, name, source, timezone):
    return {
        "id": job_id,
        "name": name,
        "source": source,
        "host": "Studio",
        "timezone": timezone,
        "status": "unknown",
        "last_run_at": None,
        "last_success_at": None,
        "duration_seconds": None,
        "last_run": None,
        "schedule": {"kind": "unknown", "timezone": timezone, "label": "Unknown"},
        "loaded": None,
        "notes": [],
        "logs": {},
        "evidence": [],
    }


def read_launch_state(domain, label):
    result = subprocess.run(
        ["/bin/launchctl", "print", domain + "/" + label],
        capture_output=True,
        text=True,
        timeout=4,
        check=False,
    )
    if result.returncode:
        return {"loaded": False}
    text = result.stdout

    def number(key):
        match = re.search(r"^\s*" + re.escape(key) + r" = (-?\d+)(?:\s*:[^\n]*)?\s*$", text, re.MULTILINE)
        return int(match[1]) if match else None

    return {
        "loaded": True,
        "pid": number("pid"),
        "runs": number("runs"),
        "exit_code": number("last exit code"),
    }


def apply_receipts(job, receipts):
    valid = [r for r in receipts if parse_time(r.get("started_at") or r.get("finished_at"))]
    valid.sort(key=lambda r: parse_time(r.get("started_at") or r.get("finished_at")))
    if not valid:
        return
    last = valid[-1]
    job.update(
        last_run=last,
        status=last["status"],
        last_run_at=last.get("started_at") or last.get("finished_at"),
        duration_seconds=last.get("duration_seconds"),
    )
    successes = [r for r in valid if r["status"] == "success"]
    if successes:
        good = successes[-1]
        job["last_success_at"] = good.get("finished_at") or good.get("started_at")
    job["evidence"] = valid[-20:]


def normalized_receipt(raw, ref):
    status = {
        "ok": "success",
        "complete": "success",
        "completed": "success",
        "failed": "failure",
        "error": "failure",
    }.get(raw.get("status"), raw.get("status"))
    if status not in {"success", "failure", "running", "unknown"}:
        status = "unknown"
    ended = raw.get("finished_at") or raw.get("ts")
    duration = raw.get("duration_seconds")
    if duration is None and isinstance(raw.get("duration_ms"), (int, float)):
        duration = raw["duration_ms"] / 1000
    started = raw.get("started_at")
    if duration is None and parse_time(started) and parse_time(ended):
        duration = max(0, (parse_time(ended) - parse_time(started)).total_seconds())
    if not started and parse_time(ended) and isinstance(duration, (int, float)):
        started = stamp(parse_time(ended) - dt.timedelta(seconds=max(0, duration)))
    return {
        "status": status,
        "started_at": started,
        "finished_at": ended,
        "duration_seconds": duration,
        "ref": ref,
        "error": redact(str(raw.get("error") or ""))[:1000],
        "basis": redact(str(raw.get("verification_scope") or "durable receipt"))[:300],
    }


def attach_verification(job, raw, ref):
    """A diagnostic check must never replace the scheduled publication result."""
    receipt = normalized_receipt(raw, ref)
    job["verification"] = receipt
    when = receipt.get("finished_at") or receipt.get("started_at") or "time unknown"
    job["notes"].append(
        "Verification: " + receipt["status"] + " at " + when + "; " + receipt["basis"]
        + ". Scheduled run status is retained."
    )


def collect(home=None, state=STATE):
    home = home or Path.home()
    now = dt.datetime.now(UTC)
    timezone = str(Path("/etc/localtime").resolve()).split("zoneinfo/")[-1]
    ZoneInfo(timezone)
    old = read_json(state / "snapshot.json", {})
    prior = {j["id"]: j for j in old.get("jobs", [])}
    config = read_json(state / "jobs.json", {})
    jobs, errors = {}, []
    checked = 0
    for root, domain in [
        (home / "Library/LaunchAgents", f"gui/{os.getuid()}"),
        (Path("/Library/LaunchDaemons"), "system"),
    ]:
        for path in sorted(root.glob("*.plist")):
            if not path.stem.startswith(PREFIXES) or path.stem == OBSERVER:
                continue
            checked += 1
            job_id = path.stem
            job = base_job(
                job_id, job_id.split(".")[-1].replace("-", " "), "Studio launchd service", timezone
            )
            jobs[job_id] = job
            try:
                plist = plistlib.loads(path.read_bytes())
                job_id = plist["Label"]
                job["schedule"] = schedule(plist, timezone)
                job["definition"] = str(path)
                job["source"] = (
                    " → ".join(
                        Path(a).name
                        for a in plist.get("ProgramArguments", [])
                        if a.endswith((".sh", ".py", ".mjs"))
                    )
                    or "Studio service"
                )
                job["logs"] = {
                    k: plist[key]
                    for k, key in [("stdout", "StandardOutPath"), ("stderr", "StandardErrorPath")]
                    if plist.get(key)
                }
                observed = read_launch_state(domain, job_id)
                job.update(observed)
                job["scheduler"] = "launchd " + domain
                previous = prior.get(job_id, {})
                for key in ("last_run", "last_run_at", "last_success_at", "duration_seconds", "evidence"):
                    if key in previous:
                        job[key] = previous[key]
                # Observation timestamps are explicitly not precise run timestamps.
                if observed.get("pid"):
                    job["status"] = "running"
                    job["notes"].append("Process currently present; this alone does not prove feed delivery")
                elif observed.get("runs") == 0:
                    job["status"] = "failure" if previous.get("status") == "failure" else "unknown"
                    job["notes"].append(
                        "Scheduler reloaded; retained last failure until a new run"
                        if job["status"] == "failure"
                        else "Never ran in this scheduler session"
                        if not job["last_run_at"]
                        else "Scheduler restarted; retained prior run evidence"
                    )
                elif observed.get("exit_code") not in (None, 0):
                    job["status"] = "failure"
                    job["notes"].append(
                        f"launchd last exit {observed['exit_code']}; run timestamp unavailable"
                    )
                else:
                    job["status"] = (job.get("last_run") or {}).get("status", "unknown")
                    if not job["last_run_at"]:
                        job["notes"].append(
                            "No timestamped run receipt; exit zero does not verify publication"
                        )
                if not observed["loaded"]:
                    job["notes"].append("Installed but not loaded")
            except Exception as exc:  # noqa: BLE001 -- one broken definition must not hide the population
                job["notes"].append("Cannot inspect definition: " + str(exc))
                errors.append(path.name + ": " + str(exc))
            if job_id in NAMES:
                job["name"], job["source"] = NAMES[job_id]
    sled = jobs.get("com.clearspeed.sled-intel-sweep")
    if sled and sled.get("logs", {}).get("stdout"):
        try:
            text = tail(sled["logs"]["stdout"], 128000)
            blocks = re.split(r"=== SLED intel sweep (\d{4}-\d{2}-\d{2}T\d{6}Z) org=[^\n]+ ===", text)
            receipts = []
            for index in range(1, len(blocks) - 1, 2):
                started = dt.datetime.strptime(blocks[index], "%Y-%m-%dT%H%M%SZ").replace(tzinfo=UTC)
                body = blocks[index + 1]
                receipts.append(
                    {
                        "started_at": stamp(started),
                        "status": "success" if "posted snapshot: {'ok': True" in body else "unknown",
                        "basis": "SLED timestamped run log; publication acknowledgement required for success",
                        "ref": sled["logs"]["stdout"],
                    }
                )
            if receipts:
                observed_status = sled["status"]
                apply_receipts(sled, receipts)
                if observed_status in {"running", "failure"}:
                    sled["status"] = observed_status
                sled["notes"] = [n for n in sled["notes"] if "No timestamped" not in n]
        except (OSError, ValueError):
            pass
    # CRO history records completed pipeline and delivery exit codes.
    for jid, filename in {
        "com.jfstudio.cro-morning-brief": "history.md",
        "com.jfstudio.cro-revenue-intelligence": "revenue-history.md",
        "com.jfstudio.cro-gov-affairs-watch": "gov-history.md",
    }.items():
        job = jobs.get(jid)
        path = home / "Projects/cro-suite/scheduled-task" / filename
        if not job or not path.exists():
            continue
        records = []
        for line in tail(path).splitlines():
            match = re.search(
                r"\|\s*(\d{4}-[^| ]+)\s*\|\s*studio-launchd\s*\|.*?rc=(\d+)\s+slack=(\d+)", line
            )
            if match:
                records.append(
                    normalized_receipt(
                        {
                            "finished_at": match[1],
                            "status": "success" if match[2] == match[3] == "0" else "failure",
                            "error": "" if match[2] == match[3] == "0" else "Runner or delivery failed",
                        },
                        str(path),
                    )
                )
            elif filename == "history.md":
                dated = re.search(r"\|\s*(\d{4}-[^| ]+)\s*\|\s*studio-launchd\s*\|", line)
                if dated:
                    records.append(
                        normalized_receipt(
                            {
                                "finished_at": dated[1],
                                "status": "unknown",
                                "error": "Legacy history records the run time but not its exit status",
                            },
                            str(path),
                        )
                    )
        observed_status = job["status"]
        apply_receipts(job, records)
        if observed_status in {"failure", "running"}:
            job["status"] = observed_status
        job["logs"]["receipt"] = str(path)
    # Atlas's own cron journal is independent of laptop automations. Include
    # every journal, including dormant ones; unknown cadence stays explicit.
    for path in sorted((home / "atlas-direct/state/cron").glob("*.jsonl")):
        jid = "atlas-cron:" + path.stem
        job = base_job(
            jid, "Atlas · " + path.stem.replace("-", " "), "Atlas internal scheduled workflow", timezone
        )
        job.update(
            scheduler="Atlas cron receipt journal", logs={"receipt": str(path)}, stale_after_seconds=86400
        )
        job["notes"].append("Cadence not exposed by the receipt journal; freshness limit 24 hours")
        records = []
        try:
            for line in tail(path, 256000).splitlines():
                try:
                    raw = json.loads(line)
                    records.append(normalized_receipt(raw, str(path)))
                except (ValueError, TypeError, AttributeError):
                    continue
            job["last_success_at"] = prior.get(jid, {}).get("last_success_at")
            apply_receipts(job, records)
        except OSError as exc:
            job["notes"].append(str(exc))
        jobs[jid] = job
    # Nevada's minute tick only checks whether the daily report is due. Its
    # durable state, not the tick exit code, is the report's execution status.
    nv_id = "com.clearspeed.nv-sled-intel"
    nv_out = home / "Projects/reports/nv-sled-brief"
    if (home / "Projects/nv-sled-intel").exists() or nv_id in jobs:
        if nv_id not in jobs:
            jobs[nv_id] = base_job(nv_id, *NAMES[nv_id], "America/New_York")
            jobs[nv_id]["loaded"] = False
        job = jobs[nv_id]
        job.update(
            name=NAMES[nv_id][0],
            source=NAMES[nv_id][1],
            timezone="America/New_York",
            schedule=schedule({"StartCalendarInterval": {"Hour": 6, "Minute": 0}}, "America/New_York"),
            max_runtime_seconds=5400,
        )
        raw = read_json(nv_out / "studio-state.json", {})
        job["logs"]["receipt"] = str(nv_out / "studio-state.json")
        if raw:
            # The report receipt supersedes tick-level process/exit observations.
            job["notes"] = [
                n
                for n in job["notes"]
                if not n.startswith(
                    (
                        "launchd last exit",
                        "No timestamped run receipt",
                        "Never ran in this scheduler",
                        "Process currently present",
                    )
                )
            ]
            ended = raw.get("completed_at") if raw.get("status") == "complete" else None
            started = raw.get("started_at")
            duration = (
                (parse_time(ended) - parse_time(started)).total_seconds()
                if parse_time(ended) and parse_time(started)
                else None
            )
            apply_receipts(
                job,
                [
                    normalized_receipt(
                        dict(raw, finished_at=ended, duration_seconds=duration),
                        str(nv_out / "studio-state.json"),
                    )
                ],
            )
            job["last_success_at"] = raw.get("completed_at")
            job["retry_at"] = raw.get("retry_after")
            job["notes"].append(
                "Daily 06:00 Eastern; scheduler tick and retries are separate from report completion"
            )
            if raw.get("failure_stage") and raw.get("status") == "failed":
                job["notes"].append("Failed stage: " + str(raw["failure_stage"]))
            if parse_time(started):
                day = parse_time(started).astimezone(ZoneInfo("America/New_York")).date()
                job["logs"]["stdout"] = str(nv_out / f"studio-{day}.log")
        else:
            job["status"] = "unknown"
            job["notes"].append("Never ran: no Studio report receipt yet")
    # Explicit descriptors enrich discovered services or register non-launchd feeds.
    for jid, desc in config.items():
        if jid not in jobs and jid in prior:
            jobs[jid] = dict(
                prior[jid],
                loaded=False,
                status="unknown",
                notes=["Previously discovered scheduler is no longer installed"],
            )
        job = jobs.setdefault(jid, base_job(jid, jid, "Registered Studio feed", timezone))
        if not job.get("evidence") and jid in prior:
            for key in ("evidence", "last_success_at"):
                job[key] = prior[jid].get(key)
        for key in ("name", "source", "schedule", "timezone", "stale_after_seconds", "max_runtime_seconds"):
            if key in desc:
                job[key] = desc[key]
        if desc.get("receipt_path"):
            path = Path(desc["receipt_path"]).expanduser()
            raw = read_json(path)
            job["logs"]["receipt"] = str(path)
            if isinstance(raw, dict):
                apply_receipts(job, [*(job.get("evidence") or []), normalized_receipt(raw, str(path))])
                if raw.get("verification_scope"):
                    job["notes"].append(redact(str(raw["verification_scope"]))[:300])
        if desc.get("verification_receipt_path"):
            path = Path(desc["verification_receipt_path"]).expanduser()
            raw = read_json(path)
            if isinstance(raw, dict):
                attach_verification(job, raw, str(path))
    # Disappearing definitions do not silently disappear from the dashboard.
    for jid, previous in prior.items():
        if jid not in jobs:
            job = dict(previous, loaded=False, status="unknown")
            job["notes"] = ["Previously discovered job is missing from the current inventory"]
            jobs[jid] = job
    for jid, job in jobs.items():
        classify(job, config.get(jid))
    return {
        "schema_version": 1,
        "collected_at": stamp(now),
        "host": "Studio",
        "timezone": timezone,
        "collector_sha": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "coverage": {
            "launchd_definitions_checked": checked,
            "jobs": len(jobs),
            "errors": errors,
            "sources": [
                "Studio user launchd",
                "Studio system launchd",
                "Atlas cron journals",
                "Nevada report state",
                "registered receipts",
            ],
        },
        "jobs": list(jobs.values()),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--state-dir", type=Path, default=STATE)
    parser.add_argument("--log-job")
    parser.add_argument("--kind", choices=["stdout", "stderr", "receipt"], default="stdout")
    args = parser.parse_args()
    if args.log_job:
        data = read_json(args.state_dir / "snapshot.json", {})
        job = next((j for j in data.get("jobs", []) if j["id"] == args.log_job), None)
        if not job or args.kind not in job.get("logs", {}):
            raise SystemExit("No registered log for this job")
        path = Path(job["logs"][args.kind])
        if not path.is_absolute() or path.is_symlink() or not path.is_file():
            raise SystemExit("Log unavailable")
        print(redact(tail(path)))
        return
    args.state_dir.mkdir(parents=True, exist_ok=True)
    with (args.state_dir / "collector.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        atomic(args.state_dir / "snapshot.json", collect(state=args.state_dir))


if __name__ == "__main__":
    main()
