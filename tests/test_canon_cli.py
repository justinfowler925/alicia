"""REV-515 tests for the owner-facing Canon CLI."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from alicia import __main__ as alicia_main
from alicia.canon import (
    DEFAULT_IDENTITY_REGISTRY,
    Approval,
    CanonStore,
    Decision,
    Evidence,
    EvidenceType,
    Run,
    RunStatus,
    StateHistoryEntry,
    WorkItem,
    WorkItemState,
)
from alicia.canon import cli as canon_cli

OWNER = "justin.fowler@clearspeed.com"
WORKER = "atlas6-worker"


def _review_item(title: str = "Review this") -> WorkItem:
    return WorkItem(
        title=title,
        state=WorkItemState.REVIEW,
        state_history=[
            StateHistoryEntry(
                state=WorkItemState.REVIEW,
                actor=OWNER,
                time=datetime.now(UTC) - timedelta(hours=2),
            )
        ],
    )


def _invoke(monkeypatch: pytest.MonkeyPatch, args: list[str]) -> None:
    monkeypatch.setattr(alicia_main.sys, "argv", ["alicia", *args])
    alicia_main.main()


def test_list_filters_by_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    db_path = tmp_path / "canon.db"
    store = CanonStore(db_path)
    review = _review_item("Needs owner review")
    execution = WorkItem(title="Still executing", state=WorkItemState.EXECUTION)
    store.save(review)
    store.save(execution)
    store.close()

    _invoke(monkeypatch, ["canon", "--db", str(db_path), "list", "--state", "review"])

    output = capsys.readouterr().out
    assert review.id in output
    assert "Needs owner review" in output
    assert "review" in output
    assert "2h" in output
    assert execution.id not in output


def test_show_renders_linked_review_objects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    db_path = tmp_path / "canon.db"
    store = CanonStore(db_path)
    work_item = _review_item("Inspect implementation")
    store.save(work_item)
    evidence = Evidence(
        type=EvidenceType.DIFF,
        captured_by=WORKER,
        captured_by_kind="worker",
        linked_object_id=work_item.id,
        content_ref="https://github.com/justinfowler925/alicia/pull/515",
    )
    decision = Decision(
        question="Which interface?",
        chosen_option="CLI",
        rationale="Fastest usable review surface",
        decided_by=OWNER,
        linked_work_item_ids=[work_item.id],
    )
    approval = Approval(work_item_id=work_item.id, requested_by=WORKER, scope="release review")
    run = Run(
        actor=WORKER,
        work_item_id=work_item.id,
        status=RunStatus.READY_FOR_REVIEW,
        target="alicia",
    )
    for obj in (evidence, decision, approval, run):
        store.save(obj)
    store.close()

    _invoke(monkeypatch, ["canon", "--db", str(db_path), "show", work_item.id])

    output = capsys.readouterr().out
    assert "Work Item" in output
    assert "Evidence (1)" in output
    assert "Decisions (1)" in output
    assert "Approvals (1)" in output
    assert "Runs (1)" in output
    assert evidence.content_ref in output
    assert decision.question in output
    assert approval.scope in output
    assert run.target in output


def test_accept_uses_configured_owner_principal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    db_path = tmp_path / "canon.db"
    store = CanonStore(db_path)
    work_item = _review_item()
    store.save(work_item)
    store.close()

    _invoke(monkeypatch, ["canon", "--db", str(db_path), "accept", work_item.id])

    assert "accept:" in capsys.readouterr().out
    reopened = CanonStore(db_path)
    accepted = reopened.get(WorkItem, work_item.id)
    reopened.close()
    assert accepted is not None
    assert accepted.state == WorkItemState.ACCEPTANCE
    assert accepted.state_history[-1].actor == DEFAULT_IDENTITY_REGISTRY.owner_identity


def test_accept_rejects_a_worker_principal_even_if_cli_actor_is_spoofed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    db_path = tmp_path / "canon.db"
    store = CanonStore(db_path)
    work_item = _review_item()
    store.save(work_item)
    store.close()
    worker_principal = DEFAULT_IDENTITY_REGISTRY.worker_principal(WORKER)
    monkeypatch.setattr(
        canon_cli.DEFAULT_IDENTITY_REGISTRY,
        "owner_principal",
        lambda: worker_principal,
    )

    with pytest.raises(SystemExit, match="1"):
        _invoke(monkeypatch, ["canon", "--db", str(db_path), "accept", work_item.id])

    assert "not the authenticated owner" in capsys.readouterr().err
    reopened = CanonStore(db_path)
    persisted = reopened.get(WorkItem, work_item.id)
    reopened.close()
    assert persisted is not None and persisted.state == WorkItemState.REVIEW


@pytest.mark.parametrize(
    ("command", "expected_state"),
    [
        ("reject", WorkItemState.CANCELED),
        ("request-changes", WorkItemState.EXECUTION),
    ],
)
def test_review_outcomes_record_owner_reason(
    command: str,
    expected_state: WorkItemState,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = tmp_path / f"{command}.db"
    store = CanonStore(db_path)
    work_item = _review_item()
    store.save(work_item)
    store.close()

    _invoke(
        monkeypatch,
        ["canon", "--db", str(db_path), command, work_item.id, "--reason", "Needs a clearer test"],
    )

    reopened = CanonStore(db_path)
    changed = reopened.get(WorkItem, work_item.id)
    reopened.close()
    assert changed is not None
    assert changed.state == expected_state
    assert changed.state_history[-1].reason == "Needs a clearer test"
    assert changed.state_history[-1].actor == OWNER
