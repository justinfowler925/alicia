"""CLI coverage for the REV-525/526/527/529/534 Canon lifecycle surface."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from alicia import __main__ as alicia_main
from alicia.canon import (
    CanonStore,
    Decision,
    Evidence,
    EvidenceType,
    InboxItem,
    InboxStatus,
    Run,
    RunStatus,
    WorkItem,
    WorkItemState,
)

OWNER = "justin.fowler@clearspeed.com"


def _invoke(monkeypatch: pytest.MonkeyPatch, args: list[str]) -> None:
    monkeypatch.setattr(alicia_main.sys, "argv", ["alicia", *args])
    alicia_main.main()


def _command(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], args: list[str]) -> str:
    _invoke(monkeypatch, args)
    return capsys.readouterr().out


def test_manual_capture_records_verbatim_provenance_without_promoting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    db_path = tmp_path / "canon.db"

    output = _command(
        monkeypatch,
        capsys,
        [
            "canon", "--db", str(db_path), "inbox", "capture",
            "--raw-capture", "  Exact customer signal\nwith whitespace.  ",
            "--source", "manual:customer-call:2026-08-23",
        ],
    )

    assert output.startswith("capture: ")
    store = CanonStore(db_path)
    inbox_items = store.list(InboxItem)
    work_items = store.list(WorkItem)
    store.close()
    assert len(inbox_items) == 1
    assert inbox_items[0].raw_capture == "  Exact customer signal\nwith whitespace.  "
    assert inbox_items[0].source == "manual:customer-call:2026-08-23"
    assert inbox_items[0].status == InboxStatus.UNCATEGORIZED
    assert work_items == []


def test_triage_age_uses_state_entered_at_when_history_has_no_triage_entry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    db_path = tmp_path / "canon.db"
    store = CanonStore(db_path)
    item = WorkItem(title="Freshly promoted", state_entered_at=datetime.now(UTC) - timedelta(minutes=4))
    store.save(item)
    store.close()

    output = _command(monkeypatch, capsys, ["canon", "--db", str(db_path), "list"])

    assert item.id in output
    assert "unknown" not in output
    assert "4m" in output


def test_transition_validation_error_has_actionable_guidance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    db_path = tmp_path / "canon.db"
    store = CanonStore(db_path)
    item = WorkItem(title="Needs triage")
    store.save(item)
    store.close()

    with pytest.raises(SystemExit, match="1"):
        _invoke(
            monkeypatch,
            ["canon", "--db", str(db_path), "work", "transition", item.id, "--to", "execution"],
        )

    error = capsys.readouterr().err
    assert "lightweight execution requires a decision_not_required reason" in error
    assert "Guidance: run 'alicia canon --help'" in error


def test_run_start_persists_a_worker_attempt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    db_path = tmp_path / "canon.db"
    store = CanonStore(db_path)
    item = WorkItem(title="Start a run")
    store.save(item)
    store.close()

    output = _command(
        monkeypatch, capsys,
        ["canon", "--db", str(db_path), "run", "start", item.id, "--actor", "atlas6-worker", "--scope", "test"],
    )

    assert "run start:" in output
    store = CanonStore(db_path)
    runs = store.list(Run)
    store.close()
    assert len(runs) == 1
    assert runs[0].status == RunStatus.IMPLEMENTATION_ATTEMPTED


def test_cli_only_lifecycle_from_capture_through_authenticated_closure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Exercise the dogfood path without any direct lifecycle mutation in Python."""
    db_path = tmp_path / "canon.db"
    base = ["canon", "--db", str(db_path)]

    _command(
        monkeypatch, capsys,
        [*base, "inbox", "capture", "--raw-capture", "Ship small CLI fix", "--source", "manual:dogfood"],
    )
    store = CanonStore(db_path)
    inbox = store.list(InboxItem)[0]
    store.close()
    _command(monkeypatch, capsys, [*base, "inbox", "promote", inbox.id, "--title", "Ship small CLI fix"])
    store = CanonStore(db_path)
    work_item = store.list(WorkItem)[0]
    store.close()

    _command(monkeypatch, capsys, [*base, "work", "transition", work_item.id, "--to", "clarification"])
    _command(monkeypatch, capsys, [*base, "work", "transition", work_item.id, "--to", "planning"])
    _command(
        monkeypatch, capsys,
        [
            *base, "decision", "create", "--question", "Which workflow?", "--option", "manual",
            "--option", "cli", "--chosen-option", "cli", "--rationale", "Dogfood needs a supported CLI path",
            "--decided-by", OWNER,
        ],
    )
    store = CanonStore(db_path)
    decision = store.list(Decision)[0]
    store.close()
    _command(monkeypatch, capsys, [*base, "decision", "link", decision.id, work_item.id])
    _command(monkeypatch, capsys, [*base, "work", "transition", work_item.id, "--to", "decision"])
    _command(
        monkeypatch, capsys,
        [*base, "work", "transition", work_item.id, "--to", "execution", "--decision-id", decision.id],
    )
    _command(monkeypatch, capsys, [*base, "work", "transition", work_item.id, "--to", "validation"])

    dispatch_output = _command(
        monkeypatch, capsys,
        [*base, "run", "dispatch", work_item.id, "--actor", "atlas6-worker", "--scope", "CLI smoke run"],
    )
    assert "Prove PASS" in dispatch_output
    store = CanonStore(db_path)
    run = store.list(Run)[0]
    assert run.status == RunStatus.READY_FOR_REVIEW
    store.close()
    _command(
        monkeypatch, capsys,
        [*base, "evidence", "attach", run.id, "--type", "diff", "--content-ref", "commit cafebabe"],
    )
    _command(
        monkeypatch, capsys,
        [*base, "evidence", "attach", run.id, "--type", "run_output", "--content-ref", "pytest -q: 1 passed"],
    )
    store = CanonStore(db_path)
    evidence = store.list(Evidence)
    store.close()
    for item in evidence:
        if item.type in {EvidenceType.DIFF, EvidenceType.RUN_OUTPUT}:
            _command(monkeypatch, capsys, [*base, "evidence", "verify", item.id])

    _command(monkeypatch, capsys, [*base, "run", "review", run.id])
    _command(monkeypatch, capsys, [*base, "accept", work_item.id])
    closure_output = _command(monkeypatch, capsys, [*base, "close", work_item.id, "--reason", "Owner accepted CLI proof"])
    assert "terminal" in closure_output

    store = CanonStore(db_path)
    closed = store.get(WorkItem, work_item.id)
    linked_decision = store.get(Decision, decision.id)
    store.close()
    assert closed is not None and closed.state == WorkItemState.CLOSURE
    assert closed.state_history[-1].reason == "Owner accepted CLI proof"
    assert linked_decision is not None and work_item.id in linked_decision.linked_work_item_ids


def test_close_requires_acceptance_or_monitoring(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    db_path = tmp_path / "canon.db"
    store = CanonStore(db_path)
    item = WorkItem(title="Do not close triage")
    store.save(item)
    store.close()

    with pytest.raises(SystemExit, match="1"):
        _invoke(monkeypatch, ["canon", "--db", str(db_path), "close", item.id])

    assert "closure requires the work item to have passed through acceptance" in capsys.readouterr().err
