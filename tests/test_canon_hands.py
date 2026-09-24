"""REV-514: canonical persistence around the Hands dispatch seam."""

from __future__ import annotations

import inspect
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
    Run,
    RunStatus,
    WorkItem,
    WorkItemState,
    WorkItemType,
    transition,
)

# alicia_stack is vendored as a portable package rather than included in the
# main wheel; the Canon CLI imports the retained Hands/Prove package at runtime.
_STACK_ROOT = Path(__file__).resolve().parents[1] / "alicia_stack"
if str(_STACK_ROOT) not in sys.path:
    sys.path.insert(0, str(_STACK_ROOT))

from alicia_stack.hands import CanonHandsDispatcher, transition_run_to_review
from alicia_stack.types import HandsResult

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
        self.packets: list[dict] = []

    def dispatch(self, packet: dict) -> HandsResult:
        self.packets.append(packet)
        return self.result


def _work_item_in_validation(store: CanonStore) -> WorkItem:
    work_item = WorkItem(title="Wire Atlas dispatch", type=WorkItemType.TASK)
    decision = Decision(
        question="How should dispatch be wired?",
        chosen_option="Use the canonical wrapper",
        rationale="It preserves the existing Hands protocol.",
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


def _dispatch(store: CanonStore, work_item: WorkItem) -> tuple[HandsResult, Run]:
    result = HandsResult(
        job_id="atlas-job-514",
        summary="Merged to main and all tests passed.",
        claims=["merged"],
        evidence={
            "sha": TEST_SHA,
            "test_command": "pytest -q",
            "test_exit_code": 0,
            "pull_request": "https://github.com/justinfowler925/alicia/pull/514",
            "test_output": "pytest -q: 4 passed",
        },
        raw={"wired": True, "atlas_job_id": "atlas-job-514"},
    )
    fake = FakeHands(result)
    hands = CanonHandsDispatcher(
        fake,
        store,
        actor="conversation:unknown",
        work_item_id=work_item.id,
        target="github:justinfowler925/alicia",
        scope="wire Atlas worker dispatch to canon",
    )

    returned = hands.dispatch(
        {
            "template_id": "ship",
            "fields": {"target": "alicia"},
            "utterance": "wire Atlas dispatch to canon",
            # This is the best currently available caller identity. REV-519
            # will replace it with an authenticated identity.
            "canon": {"actor": WORKER},
        }
    )
    run = store.get(Run, returned.raw["canon_run_id"])
    assert run is not None
    assert fake.packets
    return returned, run


def _save_verified_evidence(store: CanonStore, evidence: Evidence) -> None:
    """Stay compatible while REV-519 adds authenticated verifier writes."""
    if "authenticated_principal" not in inspect.signature(store.save).parameters:
        store.save(evidence)
        return

    from alicia.canon.identity import DEFAULT_IDENTITY_REGISTRY

    store.save(
        evidence,
        authenticated_principal=DEFAULT_IDENTITY_REGISTRY.owner_principal(),
    )


def test_dispatch_creates_run_and_persists_worker_artifacts() -> None:
    store = CanonStore()
    work_item = _work_item_in_validation(store)

    returned, run = _dispatch(store, work_item)

    assert returned.job_id == "atlas-job-514"
    assert run.actor == WORKER
    assert run.work_item_id == work_item.id
    assert run.target == "github:justinfowler925/alicia"
    assert run.scope == "wire Atlas worker dispatch to canon"
    assert run.started_at <= run.ended_at
    assert run.status == RunStatus.READY_FOR_REVIEW
    assert run.prove_verdict.value == "PASS"

    artifacts = [store.get(Evidence, evidence_id) for evidence_id in run.evidence_refs]
    assert all(artifact is not None for artifact in artifacts)
    assert {artifact.type for artifact in artifacts} >= {
        EvidenceType.LOG,  # worker claim
        EvidenceType.DIFF,  # pull request
        EvidenceType.RUN_OUTPUT,  # test output and raw handoff
    }
    assert all(artifact.linked_object_id == run.id for artifact in artifacts)
    assert all(artifact.captured_by == WORKER for artifact in artifacts)
    assert all(not artifact.verified for artifact in artifacts)

    persisted_work_item = store.get(WorkItem, work_item.id)
    assert persisted_work_item is not None
    assert set(run.evidence_refs).issubset(persisted_work_item.evidence_refs)


def test_unverified_run_evidence_cannot_leave_validation() -> None:
    store = CanonStore()
    work_item = _work_item_in_validation(store)
    _, run = _dispatch(store, work_item)

    with pytest.raises(CanonError, match="cannot exit 'validation'"):
        transition_run_to_review(
            store,
            work_item=work_item,
            run=run,
            actor=OWNER,
            owners=[OWNER],
        )

    assert work_item.state == WorkItemState.VALIDATION


def test_verified_required_run_evidence_can_proceed_to_review() -> None:
    store = CanonStore()
    work_item = _work_item_in_validation(store)
    _, run = _dispatch(store, work_item)

    for evidence_id in run.evidence_refs:
        evidence = store.get(Evidence, evidence_id)
        assert evidence is not None
        if evidence.type == EvidenceType.DIFF or evidence.content_ref == "pytest -q: 4 passed":
            evidence.verified = True
            evidence.verified_by = OWNER
            _save_verified_evidence(store, evidence)

    reviewed = transition_run_to_review(
        store,
        work_item=work_item,
        run=run,
        actor=OWNER,
        owners=[OWNER],
    )

    assert reviewed.state == WorkItemState.REVIEW
    assert store.get(WorkItem, work_item.id).state == WorkItemState.REVIEW
    assert set(run.evidence_refs).issubset(reviewed.evidence_refs)


def test_stale_work_item_save_preserves_dispatcher_evidence_references() -> None:
    store = CanonStore()
    work_item = _work_item_in_validation(store)
    stale_work_item = work_item.model_copy(deep=True)

    _, run = _dispatch(store, work_item)
    # A caller which loaded before dispatch must not be able to erase the
    # dispatch wrapper's append-only evidence index with a later save.
    store.save(stale_work_item)

    persisted = store.get(WorkItem, work_item.id)
    assert persisted is not None
    assert set(run.evidence_refs).issubset(persisted.evidence_refs)


def test_review_history_references_verified_completion_diff_not_raw_handoff() -> None:
    store = CanonStore()
    work_item = _work_item_in_validation(store)
    _, run = _dispatch(store, work_item)

    artifacts = [store.get(Evidence, evidence_id) for evidence_id in run.evidence_refs]
    assert all(artifact is not None for artifact in artifacts)
    evidence = [artifact for artifact in artifacts if artifact is not None]
    completion_diff = next(artifact for artifact in evidence if artifact.type == EvidenceType.DIFF)
    checks = next(artifact for artifact in evidence if artifact.content_ref == "pytest -q: 4 passed")
    for artifact in (completion_diff, checks):
        artifact.verified = True
        artifact.verified_by = OWNER
        _save_verified_evidence(store, artifact)
    raw_handoff = evidence[-1]

    reviewed = transition_run_to_review(
        store,
        work_item=work_item,
        run=run,
        actor=OWNER,
        owners=[OWNER],
    )

    assert reviewed.state_history[-1].evidence_ref == completion_diff.id
    assert reviewed.state_history[-1].evidence_ref != raw_handoff.id
