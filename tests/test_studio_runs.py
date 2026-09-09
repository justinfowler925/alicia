import datetime as dt
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from brutus.studio_collector import UTC, apply_receipts, base_job, calendar_times, normalized_receipt, redact
from brutus.studio_runs import assess, router

NOW = dt.datetime(2026, 9, 9, 14, tzinfo=UTC)


def job(**extra):
    j = base_job("feed", "Feed", "Source → publication", "America/Chicago")
    j.update(extra)
    return j


def report(j, **kwargs):
    return assess({"collected_at": NOW.isoformat(), "jobs": [j]}, now=NOW, **kwargs)


def test_calendar_timezone_dst_weekday_and_daily():
    previous, future = calendar_times({"Hour": 6, "Minute": 0}, "America/New_York", NOW)
    assert previous == "2026-09-09T10:00:00+00:00"
    assert future == "2026-09-10T10:00:00+00:00"
    spring = dt.datetime(2026, 3, 8, 6, tzinfo=UTC)
    _, future = calendar_times({"Hour": 2, "Minute": 30}, "America/New_York", spring)
    assert future == "2026-03-09T06:30:00+00:00"  # nonexistent local time skipped
    fall = dt.datetime(2026, 11, 1, 5, 45, tzinfo=UTC)
    _, future = calendar_times({"Hour": 1, "Minute": 30}, "America/New_York", fall)
    assert future == "2026-11-01T06:30:00+00:00"
    _, future = calendar_times({"Weekday": 1, "Hour": 8, "Minute": 0}, "America/Chicago", NOW)
    assert future == "2026-09-14T13:00:00+00:00"


def test_receipt_failure_does_not_erase_last_success_and_duration():
    j = job()
    receipts = [
        normalized_receipt({"status": "ok", "ts": "2026-09-08T10:01:00Z", "duration_ms": 60000}, "receipt"),
        normalized_receipt({"status": "failed", "started_at": "2026-09-09T10:00:00Z"}, "receipt"),
    ]
    apply_receipts(j, receipts)
    assert j["status"] == "failure"
    assert j["last_success_at"] == "2026-09-08T10:01:00Z"
    assert receipts[0]["started_at"] == "2026-09-08T10:00:00+00:00"
    assert receipts[0]["duration_seconds"] == 60
    assert report(j)["summary"]["failed"] == 1


def test_outage_invalidates_healthy_and_running_counts():
    for status in ("success", "running"):
        data = report(job(status=status, last_run_at=NOW.isoformat()), error="SSH unavailable")
        assert data["summary"]["stale"] == 1
        assert data["summary"]["healthy"] == data["summary"]["running"] == 0
        assert data["jobs"][0]["status"] == status  # preserve last observed result


def test_old_observation_cannot_be_green():
    data = assess(
        {
            "collected_at": "2026-09-09T13:00:00Z",
            "jobs": [job(status="success", last_run_at=NOW.isoformat())],
        },
        now=NOW,
    )
    assert data["jobs"][0]["health"] == "stale"


def test_no_receipt_unknown_and_never_ran_distinct():
    assert report(job(exit_code=0))["jobs"][0]["health"] == "unknown"
    assert report(job(notes=["Never ran: no receipt"]))["jobs"][0]["health"] == "never_ran"
    assert (
        report(job(status="success", loaded=False, last_run_at=NOW.isoformat()))["jobs"][0]["health"]
        == "unknown"
    )


def test_stale_receipt_and_long_running_job():
    j = job(
        status="success",
        last_run_at="2026-09-07T10:00:00Z",
        schedule={"kind": "calendar", "calendar": {"Hour": 6, "Minute": 0}, "timezone": "America/New_York"},
    )
    result = report(j)["jobs"][0]
    assert result["health"] == "stale"
    assert result["next_run_at"] == "2026-09-10T10:00:00+00:00"
    assert report(job(status="running", last_run_at="2026-09-09T08:00:00Z"))["jobs"][0]["health"] == "stale"
    assert report(job(status="running", schedule={"kind": "service"}))["jobs"][0]["health"] == "running"


def test_interval_does_not_invent_next_fire_phase():
    j = job(status="success", last_run_at=NOW.isoformat(), schedule={"kind": "interval", "seconds": 3600})
    assert report(j)["jobs"][0]["next_run_at"] is None
    assert report(j)["summary"]["healthy"] == 1


def test_log_path_is_selected_from_inventory_not_request():
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)
    data = {"jobs": [job(logs={"stdout": "/trusted/log"})]}
    with (
        patch("brutus.studio_runs.snapshot", return_value=data),
        patch("brutus.studio_runs.remote", return_value="safe log") as remote,
    ):
        assert client.get("/api/studio-runs/nope/log").status_code == 404
        assert client.get("/api/studio-runs/feed/log?kind=../../secret").status_code == 400
        assert client.get("/api/studio-runs/feed/log").text == "safe log"
        assert remote.call_args.args[-3:] == ("feed", "--kind", "stdout")


def test_log_secret_redaction():
    value = redact("Authorization: Bearer abcxyz token=hidden sk-testsecret")
    assert all(secret not in value for secret in ["abcxyz", "hidden", "sk-testsecret"])


def test_ui_javascript_parses(tmp_path):
    import re
    import subprocess

    from brutus.ui import BRUTUS_HTML

    script = tmp_path / "page.js"
    script.write_text("\n".join(re.findall(r"<script[^>]*>(.*?)</script>", BRUTUS_HTML, re.DOTALL)))
    subprocess.run(["node", "--check", str(script)], check=True)
    assert "nav-studio" in BRUTUS_HTML and "mob-nav-studio" in BRUTUS_HTML


def test_collect_discovers_new_jobs_preserves_missing_and_reads_nevada(tmp_path, monkeypatch):
    import plistlib
    from pathlib import Path

    from brutus import studio_collector as collector

    agents = tmp_path / "Library/LaunchAgents"
    agents.mkdir(parents=True)
    (agents / "com.jfstudio.new-feed.plist").write_bytes(
        plistlib.dumps(
            {
                "Label": "com.jfstudio.new-feed",
                "StartInterval": 3600,
                "ProgramArguments": ["/bin/bash", "/tmp/feed.sh"],
                "EnvironmentVariables": {"SECRET": "must-not-export"},
            }
        )
    )
    (agents / "com.jfstudio.broken.plist").write_text("not xml")
    out = tmp_path / "Projects/reports/nv-sled-brief"
    out.mkdir(parents=True)
    (tmp_path / "Projects/nv-sled-intel").mkdir()
    collector.atomic(
        out / "studio-state.json",
        {
            "status": "failed",
            "started_at": "2026-09-09T10:00:00Z",
            "completed_at": "2026-09-08T10:30:00Z",
            "failure_stage": "publication",
            "retry_after": "2026-09-09T10:45:00Z",
        },
    )
    state = tmp_path / "observer"
    collector.atomic(state / "snapshot.json", {"jobs": [job(id="removed")]})
    original = Path.glob
    monkeypatch.setattr(
        Path,
        "glob",
        lambda p, pattern: iter([]) if str(p) == "/Library/LaunchDaemons" else original(p, pattern),
    )
    monkeypatch.setattr(
        collector, "read_launch_state", lambda *a: {"loaded": True, "runs": 0, "pid": None, "exit_code": 0}
    )
    result = collector.collect(home=tmp_path, state=state)
    jobs = {j["id"]: j for j in result["jobs"]}
    assert jobs["com.jfstudio.new-feed"]["status"] == "unknown"
    assert jobs["removed"]["loaded"] is False
    assert len(result["coverage"]["errors"]) == 1
    nv = jobs["com.clearspeed.nv-sled-intel"]
    assert nv["status"] == "failure" and nv["loaded"] is False
    assert nv["last_success_at"] == "2026-09-08T10:30:00Z"
    assert not any("Never ran in this scheduler" in note for note in nv["notes"])
    assert nv["duration_seconds"] is None  # yesterday's completion isn't this run's end
    assert "must-not-export" not in str(result)


def test_receipt_duration_can_be_derived_from_recorded_boundaries():
    r = normalized_receipt(
        {"status": "success", "started_at": "2026-09-09T10:00:00Z", "finished_at": "2026-09-09T10:02:30Z"},
        "receipt",
    )
    assert r["duration_seconds"] == 150


def test_project_scan_does_not_block_studio_request_loop(monkeypatch):
    import asyncio
    import threading
    from unittest.mock import MagicMock

    from brutus.config import BrutusCfg
    from brutus.server import create_app

    released = threading.Event()
    monkeypatch.setattr("brutus.server.scan_projects", lambda: [{"loop_was_free": released.wait(1)}])
    with patch("brutus.server.AtlasClient", return_value=MagicMock()):
        app = create_app(BrutusCfg(watchdog_enabled=False), start_watchdog=False)
    endpoint = next(r.endpoint for r in app.routes if getattr(r, "path", "") == "/api/projects")

    async def probe():
        task = asyncio.create_task(endpoint())
        await asyncio.sleep(0.02)
        released.set()
        return await task

    assert asyncio.run(probe())["projects"][0]["loop_was_free"]
