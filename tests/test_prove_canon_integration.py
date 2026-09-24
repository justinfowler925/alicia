"""REV-518: one Prove verdict gates canonical Run review readiness."""

from __future__ import annotations

import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

from alicia.canon import (
    CanonError,
    CanonStore,
    Decision,
    Evidence,
    EvidenceType,
    ProveVerdict,
    Run,
    RunStatus,
    WorkItem,
    WorkItemState,
    WorkItemType,
    transition,
)
from alicia.canon.identity import DEFAULT_IDENTITY_REGISTRY

_STACK_ROOT = Path(__file__).resolve().parents[1] / "alicia_stack"
if str(_STACK_ROOT) not in sys.path:
    sys.path.insert(0, str(_STACK_ROOT))

from alicia_stack.hands import CanonHandsDispatcher, transition_run_to_review
from alicia_stack.types import HandsResult, Verdict

OWNER = "justin.fowler@clearspeed.com"
WORKER = "atlas6-worker"
TEST_SHA = "a" * 40


@pytest.fixture(autouse=True)
def _stub_sha_on_main(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep receipt tests independent of the checkout's remote refs."""
    monkeypatch.setattr(
        "alicia_stack.prove._sha_on_main",
        lambda sha, root: (True, "controlled test ancestry"),
    )


class FakeHands:
    def __init__(self, result: HandsResult) -> None:
        self.result = result

    def dispatch(self, packet: dict) -> HandsResult:
        return self.result


def _work_item_in_validation(store: CanonStore) -> WorkItem:
    work_item = WorkItem(title="Prove/canon integration", type=WorkItemType.TASK)
    decision = Decision(
        question="How should evidence be gated?",
        chosen_option="Persist the Prove verdict on the Run",
        rationale="One report must govern dispatch review readiness.",
        decided_by=OWNER,
        decided_at=datetime.now(UTC),
    )
    transition(work_item, WorkItemState.CLARIFICATION, OWNER, owners=[OWNER])
    transition(work_item, WorkItemState.PLANNING, OWNER, owners=[OWNER])
    transition(work_item, WorkItemState.DECISION, OWNER, owners=[OWNER])
    transition(work_item, WorkItemState.EXECUTION, OWNER, owners=[OWNER], decision=decision)
    transition(work_item, WorkItemState.VALIDATION, OWNER, owners=[OWNER])
    store.save(work_item)
    return work_item


def _dispatch(store: CanonStore, work_item: WorkItem, result: HandsResult) -> tuple[HandsResult, Run]:
    returned = CanonHandsDispatcher(
        FakeHands(result),
        store,
        actor=WORKER,
        work_item_id=work_item.id,
    ).dispatch({"template_id": "ship", "utterance": "complete the work"})
    run = store.get(Run, returned.raw["canon_run_id"])
    assert run is not None
    return returned, run


def _verify_task_evidence(store: CanonStore, run: Run) -> None:
    for evidence_id in run.evidence_refs:
        evidence = store.get(Evidence, evidence_id)
        assert evidence is not None
        if evidence.type in {EvidenceType.DIFF, EvidenceType.RUN_OUTPUT}:
            evidence.verified = True
            evidence.verified_by = OWNER
            store.save(
                evidence,
                authenticated_principal=DEFAULT_IDENTITY_REGISTRY.owner_principal(),
            )


def test_merged_claim_without_sha_persists_fail_and_is_not_review_ready() -> None:
    store = CanonStore()
    work_item = _work_item_in_validation(store)

    _, run = _dispatch(
        store,
        work_item,
        HandsResult(summary="Merged to main.", evidence={"test_command": "pytest -q"}),
    )

    assert run.evidence_refs
    assert run.prove_verdict == ProveVerdict.FAIL
    assert run.status == RunStatus.FAILED
    assert store.get(Evidence, run.evidence_refs[0]) is not None


def test_sha_and_test_receipts_persist_pass_ready_run_and_run_id() -> None:
    store = CanonStore()
    work_item = _work_item_in_validation(store)

    returned, run = _dispatch(
        store,
        work_item,
        HandsResult(
            summary="Merged to main and tests passed.",
            evidence={
                "sha": TEST_SHA,
                "test_command": "pytest -q",
                "test_exit_code": 0,
            },
        ),
    )

    assert run.prove_verdict == ProveVerdict.PASS
    assert run.status == RunStatus.READY_FOR_REVIEW
    assert run.prove_verdict == ProveVerdict.PASS
    assert returned.raw["canon_run_id"] == run.id


def test_transition_refuses_prove_fail_despite_verified_task_evidence() -> None:
    store = CanonStore()
    work_item = _work_item_in_validation(store)
    run = Run(
        actor=WORKER,
        work_item_id=work_item.id,
        status=RunStatus.READY_FOR_REVIEW,
        prove_verdict=ProveVerdict.FAIL,
    )
    diff = Evidence(
        type=EvidenceType.DIFF,
        captured_by=WORKER,
        captured_by_kind="worker",
        linked_object_id=run.id,
        content_ref="commit abc123",
        verified=True,
        verified_by=OWNER,
    )
    checks = Evidence(
        type=EvidenceType.RUN_OUTPUT,
        captured_by=WORKER,
        captured_by_kind="worker",
        linked_object_id=run.id,
        content_ref="pytest -q: 1 passed",
        verified=True,
        verified_by=OWNER,
    )
    for evidence in (diff, checks):
        store.save(
            evidence,
            authenticated_principal=DEFAULT_IDENTITY_REGISTRY.owner_principal(),
        )
    run.evidence_refs = [diff.id, checks.id]
    store.save(run)

    with pytest.raises(CanonError, match="Prove verdict is 'FAIL', not PASS"):
        transition_run_to_review(
            store,
            work_item=work_item,
            run=run,
            actor=OWNER,
            owners=[OWNER],
        )


def test_unsure_prove_verdict_does_not_advance_to_review() -> None:
    store = CanonStore()
    work_item = _work_item_in_validation(store)
    _, run = _dispatch(
        store,
        work_item,
        HandsResult(
            summary="Implementation completed.",
            evidence={
                "sha": TEST_SHA,
                "test_command": "pytest -q",
                "test_exit_code": 0,
            },
        ),
    )
    _verify_task_evidence(store, run)

    assert run.prove_verdict == ProveVerdict.UNSURE
    assert run.status == RunStatus.IMPLEMENTATION_ATTEMPTED
    with pytest.raises(CanonError, match="not ready_for_review"):
        transition_run_to_review(
            store,
            work_item=work_item,
            run=run,
            actor=OWNER,
            owners=[OWNER],
        )
    assert work_item.state == WorkItemState.VALIDATION
