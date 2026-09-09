#!/usr/bin/env python3
"""Durable receipt wrapper for a Studio launchd data job; preserves its exit code."""

import argparse
import datetime as dt
import fcntl
import json
import os
import re
import signal
import subprocess
import time
from pathlib import Path

UTC = dt.timezone.utc  # noqa: UP017 -- Studio macOS Python 3.9


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--job", required=True)
    parser.add_argument(
        "--state-dir", type=Path, default=Path.home() / ".local/share/brutus-studio-runs/receipts"
    )
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", args.job):
        parser.error("invalid job ID")
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command:
        parser.error("command required")
    root = args.state_dir
    root.mkdir(parents=True, exist_ok=True)
    with (root / (args.job + ".lock")).open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            # Preserve the in-flight run's receipt.
            return 0
        started = dt.datetime.now(UTC).isoformat()
        tick = time.monotonic()
        record = {"status": "running", "started_at": started, "host": "Studio"}
        path = root / (args.job + ".json")

        def save():
            temporary = path.with_suffix(".tmp")
            temporary.write_text(json.dumps(record) + "\n")
            temporary.chmod(0o600)
            temporary.replace(path)

        save()
        child = None

        def terminate(signum, _frame):
            if child:
                os.killpg(child.pid, signum)

        signal.signal(signal.SIGTERM, terminate)
        signal.signal(signal.SIGINT, terminate)
        try:
            child = subprocess.Popen(command, start_new_session=True)
            rc = child.wait()
            record.update(status="success" if rc == 0 else "failure", exit_code=rc)
            if rc:
                record["error"] = "Runner exited with code " + str(rc) + "; inspect error log"
        except OSError as exc:
            rc = 127
            record.update(status="failure", error=str(exc), exit_code=rc)
        record.update(
            finished_at=dt.datetime.now(UTC).isoformat(),
            duration_seconds=round(time.monotonic() - tick, 3),
        )
        save()
        return rc if rc >= 0 else 128 - rc


if __name__ == "__main__":
    raise SystemExit(main())
