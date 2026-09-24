"""REV-522 coverage for Canon portfolio rollups, aging, and failed Runs."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from alicia import __main__ as alicia_main
from alicia.canon import (
    CanonStore,
    Project,
    Run,
    RunStatus,
    StateHistoryEntry,
    WorkItem,
    WorkItemState,
)
from alicia.canon.report import (
    age_work_item,
    build_portfolio_report,
    failed_runs_within,
    project_state_rollups,
)

NOW = datetime(2026, 8, 23, 16, 0, tzinfo=UTC)


def _item(
    item_id: str,
    title: str,
    state: WorkItemState,
    *,
    project_id: str | None = None,
    entered_at: datetime | None = None,
) -> WorkItem:
    history = (
        [StateHistoryEntry(state=state, actor="owner", time=entered_at)]
        if entered_at is not None
        else []
    )
    return WorkItem(
        id=item_id,
        title=title,
        project_id=project_id,
        state=state,
        state_history=history,
    )


def test_project_state_rollups_count_membership_and_surface_blocked_items() -> None:
    alpha = Project(
        id="project-alpha",
        name="Alpha",
        objective="Ship Alpha",
        owner="owner",
        work_item_ids=["review-1", "blocked-1"],
    )
    beta = Project(id="project-beta", name="Beta", objective="Ship Beta", owner="owner")
    review = _item("review-1", "Review Alpha", WorkItemState.REVIEW)
    blocked = _item("blocked-1", "Wait for vendor", WorkItemState.BLOCKED)
    validation = _item("validation-1", "Validate Beta", WorkItemState.VALIDATION, project_id=beta.id)

    rollups = project_state_rollups([alpha, beta], [review, blocked, validation])

    assert rollups[0].state_counts == {
        WorkItemState.REVIEW: 1,
        WorkItemState.BLOCKED: 1,
    }
    assert rollups[0].blocked_items == (blocked,)
    assert rollups[1].state_counts == {WorkItemState.VALIDATION: 1}
    assert rollups[1].blocked_items == ()


def test_aging_uses_current_state_history_and_flags_stuck_at_threshold() -> None:
    review = _item(
        "review-1",
        "Awaiting approval",
        WorkItemState.REVIEW,
        entered_at=NOW - timedelta(hours=48),
    )
    validation = _item(
        "validation-1",
        "Fresh validation",
        WorkItemState.VALIDATION,
        entered_at=NOW - timedelta(hours=47, minutes=59),
    )

    review_aging = age_work_item(review, now=NOW, stuck_after=timedelta(hours=48))
    validation_aging = age_work_item(validation, now=NOW, stuck_after=timedelta(hours=48))

    assert review_aging.age == timedelta(hours=48)
    assert review_aging.is_stuck is True
    assert validation_aging.age == timedelta(hours=47, minutes=59)
    assert validation_aging.is_stuck is False


def test_initial_state_aging_uses_persisted_state_entered_at() -> None:
    triage = WorkItem(
        id="triage-1",
        title="New work",
        state_entered_at=NOW - timedelta(hours=3),
    )

    aging = age_work_item(triage, now=NOW)

    assert aging.age == timedelta(hours=3)
    assert aging.is_stuck is False


def test_failed_runs_within_uses_end_time_and_excludes_outside_lookback() -> None:
    recent = Run(
        id="recent",
        actor="worker",
        work_item_id="review-1",
        status=RunStatus.FAILED,
        started_at=NOW - timedelta(days=4),
        ended_at=NOW - timedelta(days=2),
    )
    old = Run(
        id="old",
        actor="worker",
        work_item_id="review-1",
        status=RunStatus.FAILED,
        started_at=NOW - timedelta(days=10),
        ended_at=NOW - timedelta(days=8),
    )
    not_failed = Run(
        id="ready",
        actor="worker",
        work_item_id="review-1",
        status=RunStatus.READY_FOR_REVIEW,
        started_at=NOW - timedelta(days=1),
    )

    failed = failed_runs_within([old, not_failed, recent], now=NOW, lookback=timedelta(days=7))

    assert failed == [recent]
    report = build_portfolio_report([], [], [old, not_failed, recent], now=NOW)
    assert report.failed_runs == (recent,)


def test_portfolio_command_renders_rollups_stuck_items_and_failed_runs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    db_path = tmp_path / "canon.db"
    project = Project(
        id="project-alpha",
        name="Alpha",
        objective="Ship Alpha",
        owner="owner",
        work_item_ids=["review-1"],
    )
    review = _item(
        "review-1",
        "Awaiting approval",
        WorkItemState.REVIEW,
        entered_at=datetime.now(UTC) - timedelta(hours=49),
    )
    failed_run = Run(
        id="failed-run",
        actor="worker",
        work_item_id=review.id,
        status=RunStatus.FAILED,
        ended_at=datetime.now(UTC) - timedelta(hours=1),
    )
    store = CanonStore(db_path)
    for obj in (project, review, failed_run):
        store.save(obj)
    store.close()

    monkeypatch.setattr(
        alicia_main.sys,
        "argv",
        ["alicia", "canon", "--db", str(db_path), "report", "portfolio"],
    )
    alicia_main.main()

    output = capsys.readouterr().out
    assert "Portfolio report" in output
    assert "Alpha (project-alpha)" in output
    assert "review: 1" in output
    assert "Awaiting approval" in output
    assert "failed-run" in output
