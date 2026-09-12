import datetime as dt
import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).parents[1] / "scripts" / "weekly-workflow-efficiency.py"
SPEC = importlib.util.spec_from_file_location("weekly_workflow_efficiency", SCRIPT)
weekly = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(weekly)


def test_studio_report_combines_agent_email_calendar_and_explicit_slack_gap(tmp_path):
    snapshots = tmp_path / "snapshots"
    snapshots.mkdir()
    (snapshots / "2026-09-11-context.json").write_text(
        """{
          "sources": {"gmail": {"status": "ok"}, "calendar": {"status": "ok"}},
          "productivity": {
            "meetings": 10,
            "meeting_hours_internal": 5.2,
            "meeting_hours_external": 0,
            "emails_sent": 10,
            "emails_received": 100,
            "conflicts_next_72h": 0
          }
        }""",
        encoding="utf-8",
    )

    def loader(url):
        if "8767" in url:
            raise OSError("service down")
        return {
            "source_coverage": {"codex": True, "claude": True, "cursor": True, "canon": True},
            "metrics": {
                "broad_root_starts": {"numerator": 6, "denominator": 24, "rate": 0.25},
                "completion_push_sessions": {"numerator": 26, "denominator": 57, "rate": 0.4561},
            },
        }

    report = weekly.collect_report(
        now=dt.datetime(2026, 9, 11, tzinfo=dt.UTC),
        scorecard_url="http://laptop/scorecard",
        slack_health_url="http://studio:8767/health",
        cro_snapshots=snapshots,
        output_dir=tmp_path / "reports",
        loader=loader,
    )

    assert report["source_coverage"] == {
        "codex": True,
        "claude": True,
        "cursor": True,
        "canon": True,
        "slack": False,
        "email": True,
        "calendar": True,
    }
    assert report["gaps"] == ["slack"]
    assert report["communication"]["emails_received"] == 100
    assert report["recommended_action"]["id"] == "enforce-canonical-repository-routing"


def test_studio_report_survives_offline_laptop_without_turning_gaps_into_zero(tmp_path):
    report = weekly.collect_report(
        now=dt.datetime(2026, 9, 11, tzinfo=dt.UTC),
        scorecard_url="http://offline/scorecard",
        slack_health_url="http://offline/slack",
        cro_snapshots=tmp_path / "missing",
        output_dir=tmp_path / "reports",
        loader=lambda _url: (_ for _ in ()).throw(OSError("offline")),
    )

    assert set(report["gaps"]) == {"calendar", "canon", "claude", "codex", "cursor", "email", "slack"}
    assert report["scorecard"] == {}
    assert report["recommended_action"]["id"] == "no-change"
