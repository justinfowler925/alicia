"""Tests for the REV-513 canonical Work Object model (alicia/canon/)."""

from datetime import datetime, timezone

import pytest

from alicia.canon import (
    DEFAULT_IDENTITY_REGISTRY,
    Approval,
    ApprovalStatus,
    CanonError,
    CanonStore,
    Decision,
    Evidence,
    EvidenceType,
    IdentityRegistry,
    InboxItem,
    InboxStatus,
    Run,
    RunStatus,
    WorkItem,
    WorkItemState,
    WorkItemType,
    transition,
)

OWNER = "justin.fowler@clearspeed.com"
WORKER = "atlas6-worker"
OWNER_PRINCIPAL = DEFAULT_IDENTITY_REGISTRY.owner_principal()
WORKER_PRINCIPAL = DEFAULT_IDENTITY_REGISTRY.worker_principal(WORKER)


def _advance_to_execution(wi: WorkItem, actor: str = OWNER) -> WorkItem:
    transition(wi, WorkItemState.CLARIFICATION, actor, owners=[OWNER])
    transition(wi, WorkItemState.PLANNING, actor, owners=[OWNER])
    decision = Decision(
        question="q", chosen_option="a", rationale="r", decided_by=OWNER, decided_at=datetime.now(timezone.utc)
    )
    transition(wi, WorkItemState.DECISION, actor, owners=[OWNER])
    transition(wi, WorkItemState.EXECUTION, actor, owners=[OWNER], decision=decision)
    return wi


def test_worker_cannot_set_acceptance():
    wi = WorkItem(title="t", type=WorkItemType.TASK)
    _advance_to_execution(wi)
    with pytest.raises(CanonError):
        transition(wi, WorkItemState.ACCEPTANCE, WORKER, owners=[OWNER])


def test_worker_cannot_set_closure():
    wi = WorkItem(title="t", type=WorkItemType.TASK)
    _advance_to_execution(wi)
    with pytest.raises(CanonError):
        transition(wi, WorkItemState.CLOSURE, WORKER, owners=[OWNER])


def test_worker_cannot_self_certify_by_claiming_the_owner_name():
    wi = WorkItem(title="t", type=WorkItemType.TASK)
    _advance_to_execution(wi)
    transition(wi, WorkItemState.VALIDATION, WORKER, owners=[OWNER])
    # `actor` is caller-controlled text; naming the owner with no principal
    # must not authenticate this worker.
    with pytest.raises(CanonError, match="authenticated principal"):
        transition(wi, WorkItemState.ACCEPTANCE, OWNER, owners=[OWNER])


def test_worker_can_report_ready_for_review_without_owner_identity():
    store = CanonStore()
    wi = WorkItem(title="t")
    store.save(wi)
    run = Run(actor=WORKER, work_item_id=wi.id, status=RunStatus.READY_FOR_REVIEW)
    store.save(run)
    assert store.get(Run, run.id).status == RunStatus.READY_FOR_REVIEW


def test_run_status_enum_excludes_owner_only_values():
    # Structural enforcement: the enum itself has no accepted/validated/
    # deployed/closed members, so a worker literally cannot construct one.
    values = {s.value for s in RunStatus}
    assert values == {"implementation_attempted", "blocked", "failed", "ready_for_review"}


def test_owner_can_accept_and_close_with_evidence():
    wi = WorkItem(title="t", type=WorkItemType.TASK)
    _advance_to_execution(wi)
    ev = [
        Evidence(type=EvidenceType.DIFF, captured_by=WORKER, captured_by_kind="worker", linked_object_id=wi.id, content_ref="https://github.com/org/repo/pull/1", verified=True, verified_by=OWNER),
        Evidence(type=EvidenceType.RUN_OUTPUT, captured_by=WORKER, captured_by_kind="worker", linked_object_id=wi.id, content_ref="pytest -q: 10 passed", verified=True, verified_by=OWNER),
    ]
    transition(wi, WorkItemState.VALIDATION, OWNER, owners=[OWNER])
    transition(wi, WorkItemState.REVIEW, OWNER, owners=[OWNER], evidence=ev)
    transition(
        wi,
        WorkItemState.ACCEPTANCE,
        OWNER,
        owners=[OWNER],
        authenticated_principal=OWNER_PRINCIPAL,
    )
    transition(
        wi,
        WorkItemState.CLOSURE,
        OWNER,
        owners=[OWNER],
        authenticated_principal=OWNER_PRINCIPAL,
    )
    assert wi.state == WorkItemState.CLOSURE
    assert [h.state for h in wi.state_history][-4:] == [
        WorkItemState.VALIDATION,
        WorkItemState.REVIEW,
        WorkItemState.ACCEPTANCE,
        WorkItemState.CLOSURE,
    ]


def test_validation_blocked_without_verified_evidence():
    wi = WorkItem(title="t", type=WorkItemType.TASK)
    _advance_to_execution(wi)
    transition(wi, WorkItemState.VALIDATION, OWNER, owners=[OWNER])
    with pytest.raises(CanonError):
        transition(wi, WorkItemState.REVIEW, OWNER, owners=[OWNER], evidence=[])


def test_granted_approval_does_not_replace_authenticated_owner_for_acceptance():
    wi = WorkItem(title="t", type=WorkItemType.COMMUNICATION)
    _advance_to_execution(wi)
    # Approval must precede the evidence documenting the action it covers.
    approval = Approval(work_item_id=wi.id, requested_by=WORKER, approved_by=OWNER, scope="send the announcement", status=ApprovalStatus.GRANTED, granted_at=datetime.now(timezone.utc))
    ev = [Evidence(type=EvidenceType.EXTERNAL_URL, captured_by=WORKER, captured_by_kind="worker", linked_object_id=wi.id, content_ref="https://example.com/sent", verified=True, verified_by=OWNER)]
    transition(wi, WorkItemState.VALIDATION, OWNER, owners=[OWNER])
    transition(wi, WorkItemState.REVIEW, OWNER, owners=[OWNER], evidence=ev, approval=approval)
    # A granted Approval records consent for its scoped action, but it does
    # not authenticate a delegate as the owner for Canon acceptance.
    with pytest.raises(CanonError):
        transition(
            wi,
            WorkItemState.ACCEPTANCE,
            "delegate-bot",
            owners=[OWNER],
            approval=approval,
            authenticated_principal=WORKER_PRINCIPAL,
        )
    transition(
        wi,
        WorkItemState.ACCEPTANCE,
        OWNER,
        owners=[OWNER],
        approval=approval,
        authenticated_principal=OWNER_PRINCIPAL,
    )
    assert wi.state == WorkItemState.ACCEPTANCE


def test_pending_approval_does_not_permit_acceptance():
    wi = WorkItem(title="t", type=WorkItemType.COMMUNICATION)
    _advance_to_execution(wi)
    ev = [Evidence(type=EvidenceType.EXTERNAL_URL, captured_by=WORKER, captured_by_kind="worker", linked_object_id=wi.id, content_ref="https://example.com/sent", verified=True, verified_by=OWNER)]
    approval = Approval(work_item_id=wi.id, requested_by=WORKER, scope="send the announcement", status=ApprovalStatus.PENDING)
    transition(wi, WorkItemState.VALIDATION, OWNER, owners=[OWNER])
    # Pending approval isn't enough to even clear validation for a
    # communication-type item, let alone acceptance.
    with pytest.raises(CanonError):
        transition(wi, WorkItemState.REVIEW, OWNER, owners=[OWNER], evidence=ev, approval=approval)


def test_task_evidence_must_specifically_be_verified_not_just_any_item():
    # A verified but irrelevant screenshot must not paper over an
    # unverified diff/test-output pair.
    wi = WorkItem(title="t", type=WorkItemType.TASK)
    _advance_to_execution(wi)
    ev = [
        Evidence(type=EvidenceType.SCREENSHOT, captured_by=WORKER, linked_object_id=wi.id, content_ref="unrelated.png", verified=True, verified_by=OWNER),
        Evidence(type=EvidenceType.DIFF, captured_by=WORKER, linked_object_id=wi.id, content_ref="https://github.com/org/repo/pull/2", verified=False),
        Evidence(type=EvidenceType.RUN_OUTPUT, captured_by=WORKER, linked_object_id=wi.id, content_ref="pytest -q: 5 passed", verified=False),
    ]
    transition(wi, WorkItemState.VALIDATION, OWNER, owners=[OWNER])
    with pytest.raises(CanonError):
        transition(wi, WorkItemState.REVIEW, OWNER, owners=[OWNER], evidence=ev)


def test_investigation_requires_an_actual_decision_object():
    wi = WorkItem(title="t", type=WorkItemType.INVESTIGATION)
    _advance_to_execution(wi)
    ev = [Evidence(type=EvidenceType.LOG, captured_by=WORKER, linked_object_id=wi.id, content_ref="log excerpt", verified=True, verified_by=OWNER)]
    transition(wi, WorkItemState.VALIDATION, OWNER, owners=[OWNER])
    # No Decision object at all -- generic verified evidence is not enough.
    with pytest.raises(CanonError):
        transition(wi, WorkItemState.REVIEW, OWNER, owners=[OWNER], evidence=ev)
    root_cause = Decision(question="why did it break", chosen_option="bad config", rationale="config drift on deploy", decided_by=OWNER, decided_at=datetime.now(timezone.utc), evidence_refs=[ev[0].id])
    transition(wi, WorkItemState.REVIEW, OWNER, owners=[OWNER], evidence=ev, decision=root_cause)
    assert wi.state == WorkItemState.REVIEW


def test_decision_state_requires_resolved_decision_to_exit():
    wi = WorkItem(title="t", type=WorkItemType.TASK)
    transition(wi, WorkItemState.CLARIFICATION, OWNER, owners=[OWNER])
    transition(wi, WorkItemState.PLANNING, OWNER, owners=[OWNER])
    transition(wi, WorkItemState.DECISION, OWNER, owners=[OWNER])
    with pytest.raises(CanonError):
        transition(wi, WorkItemState.EXECUTION, OWNER, owners=[OWNER])  # no decision passed


def test_standard_task_path_still_reaches_execution_with_a_resolved_decision():
    wi = WorkItem(title="Substantive implementation", type=WorkItemType.TASK)

    _advance_to_execution(wi)

    assert wi.state == WorkItemState.EXECUTION
    assert [entry.state for entry in wi.state_history] == [
        WorkItemState.CLARIFICATION,
        WorkItemState.PLANNING,
        WorkItemState.DECISION,
        WorkItemState.EXECUTION,
    ]


def test_lightweight_task_path_is_audited_and_allows_execution_from_triage():
    wi = WorkItem(title="Correct a one-line typo", type=WorkItemType.TASK)

    transition(
        wi,
        WorkItemState.EXECUTION,
        OWNER,
        owners=[OWNER],
        decision_not_required="The requested wording is unambiguous.",
        lightweight_scope="docs/README.md: one spelling correction",
        low_risk=True,
    )

    assert wi.state == WorkItemState.EXECUTION
    assert wi.state_history[-1].reason == (
        "decision_not_required=The requested wording is unambiguous.; "
        "lightweight_scope=docs/README.md: one spelling correction; low_risk=true"
    )


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        (
            {"lightweight_scope": "docs/README.md: one spelling correction", "low_risk": True},
            "decision_not_required reason",
        ),
        (
            {"decision_not_required": "The requested wording is unambiguous.", "low_risk": True},
            "bounded scope and explicit low_risk guard",
        ),
        (
            {
                "decision_not_required": "The requested wording is unambiguous.",
                "lightweight_scope": "docs/README.md: one spelling correction",
            },
            "bounded scope and explicit low_risk guard",
        ),
    ],
)
def test_lightweight_task_path_rejects_missing_reason_or_guard(kwargs: dict[str, object], message: str):
    wi = WorkItem(title="Correct a one-line typo", type=WorkItemType.TASK)

    with pytest.raises(CanonError, match=message):
        transition(wi, WorkItemState.EXECUTION, OWNER, owners=[OWNER], **kwargs)


@pytest.mark.parametrize("content_ref", ["", "   \t"])
def test_task_completion_rejects_empty_untracked_file_diff_receipt(content_ref: str):
    wi = WorkItem(title="Add a new tracked file", type=WorkItemType.TASK)
    _advance_to_execution(wi)
    evidence = [
        # `git diff path/to/untracked-file` produces no output. A verified
        # empty receipt must not satisfy Canon's completion gate.
        Evidence(
            type=EvidenceType.DIFF,
            captured_by=WORKER,
            linked_object_id=wi.id,
            content_ref=content_ref,
            verified=True,
            verified_by=OWNER,
        ),
        Evidence(
            type=EvidenceType.RUN_OUTPUT,
            captured_by=WORKER,
            linked_object_id=wi.id,
            content_ref="pytest -q: 1 passed",
            verified=True,
            verified_by=OWNER,
        ),
    ]
    transition(wi, WorkItemState.VALIDATION, OWNER, owners=[OWNER])

    with pytest.raises(CanonError, match="verified diff/PR/commit reference"):
        transition(wi, WorkItemState.REVIEW, OWNER, owners=[OWNER], evidence=evidence)


def test_task_completion_accepts_nonempty_no_index_diff_receipt():
    wi = WorkItem(title="Add a new tracked file", type=WorkItemType.TASK)
    _advance_to_execution(wi)
    diff = Evidence(
        type=EvidenceType.DIFF,
        captured_by=WORKER,
        linked_object_id=wi.id,
        content_ref=(
            "diff --git a/new-file.txt b/new-file.txt\n"
            "new file mode 100644\n"
            "--- /dev/null\n"
            "+++ b/new-file.txt"
        ),
        verified=True,
        verified_by=OWNER,
    )
    checks = Evidence(
        type=EvidenceType.RUN_OUTPUT,
        captured_by=WORKER,
        linked_object_id=wi.id,
        content_ref="pytest -q: 1 passed",
        verified=True,
        verified_by=OWNER,
    )
    transition(wi, WorkItemState.VALIDATION, OWNER, owners=[OWNER])

    transition(wi, WorkItemState.REVIEW, OWNER, owners=[OWNER], evidence=[diff, checks])

    assert wi.state == WorkItemState.REVIEW
    assert wi.state_history[-1].evidence_ref == diff.id


def test_cannot_skip_states():
    wi = WorkItem(title="t", type=WorkItemType.TASK)
    with pytest.raises(CanonError):
        transition(wi, WorkItemState.EXECUTION, OWNER, owners=[OWNER])


def test_blocked_requires_reason():
    wi = WorkItem(title="t", type=WorkItemType.TASK)
    with pytest.raises(CanonError):
        transition(wi, WorkItemState.BLOCKED, WORKER, owners=[OWNER], reason="")


def test_blocked_reachable_from_any_nonterminal_state_with_reason():
    wi = WorkItem(title="t", type=WorkItemType.TASK)
    transition(wi, WorkItemState.BLOCKED, WORKER, owners=[OWNER], reason="waiting on API key")
    assert wi.state == WorkItemState.BLOCKED


def test_superseded_requires_link():
    wi = WorkItem(title="t", type=WorkItemType.TASK)
    with pytest.raises(CanonError):
        transition(wi, WorkItemState.SUPERSEDED, OWNER, owners=[OWNER])
    transition(wi, WorkItemState.SUPERSEDED, OWNER, owners=[OWNER], superseded_by="wi-999")
    assert wi.state == WorkItemState.SUPERSEDED


def test_terminal_state_has_no_further_transitions():
    wi = WorkItem(title="t", type=WorkItemType.TASK)
    transition(wi, WorkItemState.CANCELED, OWNER, owners=[OWNER], reason="dropped")
    with pytest.raises(CanonError):
        transition(wi, WorkItemState.TRIAGE, OWNER, owners=[OWNER])


def test_inbox_cannot_auto_promote_without_reviewer():
    store = CanonStore()
    item = InboxItem(raw_capture="fix the flow", source="voice:2026-08-23")
    wi = WorkItem(title="fix the flow", origin=item.id)
    with pytest.raises(ValueError):
        store.promote_inbox_item(item, reviewed_by="", work_item=wi)


def test_inbox_promotion_with_reviewer_recorded():
    store = CanonStore()
    item = InboxItem(raw_capture="fix the flow", source="voice:2026-08-23")
    wi = WorkItem(title="fix the flow")
    store.promote_inbox_item(item, reviewed_by=OWNER, work_item=wi)
    assert item.status == InboxStatus.PROMOTED
    fetched = store.get(InboxItem, item.id)
    assert fetched is not None and fetched.status == InboxStatus.PROMOTED


def test_store_roundtrip_all_object_types():
    store = CanonStore()
    wi = WorkItem(title="round trip")
    store.save(wi)
    fetched = store.get(WorkItem, wi.id)
    assert fetched is not None
    assert fetched.title == "round trip"
    assert fetched.id == wi.id

    run = Run(actor=WORKER, work_item_id=wi.id, status=RunStatus.READY_FOR_REVIEW)
    store.save(run)
    assert store.get(Run, run.id).status == RunStatus.READY_FOR_REVIEW

    approval = Approval(work_item_id=wi.id, requested_by=WORKER, scope="x")
    store.save(approval)
    assert store.get(Approval, approval.id).status == ApprovalStatus.PENDING

    assert len(store.list(WorkItem)) == 1
    store.delete(WorkItem, wi.id)
    assert store.get(WorkItem, wi.id) is None


def test_store_rejects_spoofed_approval_owner_identity():
    store = CanonStore()
    approval = Approval(
        requested_by=WORKER,
        approved_by=OWNER,
        scope="send the announcement",
        status=ApprovalStatus.GRANTED,
    )
    with pytest.raises(CanonError, match="authenticated principal"):
        store.save(approval)
    with pytest.raises(CanonError, match="does not match"):
        store.save(approval, authenticated_principal=WORKER_PRINCIPAL)

    store.save(approval, authenticated_principal=OWNER_PRINCIPAL)
    assert store.get(Approval, approval.id).approved_by == OWNER


def test_store_rejects_spoofed_evidence_verifier_identity():
    store = CanonStore()
    evidence = Evidence(
        type=EvidenceType.RUN_OUTPUT,
        captured_by=WORKER,
        captured_by_kind="worker",
        linked_object_id="wi-1",
        content_ref="pytest -q: 10 passed",
        verified=True,
        verified_by=OWNER,
    )
    with pytest.raises(CanonError, match="authenticated principal"):
        store.save(evidence)
    with pytest.raises(CanonError, match="does not match"):
        store.save(evidence, authenticated_principal=WORKER_PRINCIPAL)

    store.save(evidence, authenticated_principal=OWNER_PRINCIPAL)
    assert store.get(Evidence, evidence.id).verified_by == OWNER


def test_store_accepts_allowlisted_automated_evidence_verifier():
    registry = IdentityRegistry(
        owner_identity=OWNER,
        automated_verifier_identities=frozenset({"ci-evidence-verifier"}),
    )
    store = CanonStore(identity_registry=registry)
    evidence = Evidence(
        type=EvidenceType.RUN_OUTPUT,
        captured_by=WORKER,
        captured_by_kind="worker",
        linked_object_id="wi-1",
        content_ref="pytest -q: 10 passed",
        verified=True,
        verified_by="ci-evidence-verifier",
    )
    store.save(
        evidence,
        authenticated_principal=registry.verifier_principal("ci-evidence-verifier"),
    )
    assert store.get(Evidence, evidence.id).verified_by == "ci-evidence-verifier"


def test_raw_capture_never_edited_field_exists_and_is_preserved():
    item = InboxItem(raw_capture="the exact verbatim text", source="slack")
    store = CanonStore()
    store.save(item)
    item.status = InboxStatus.REVIEWED
    store.save(item)
    fetched = store.get(InboxItem, item.id)
    assert fetched.raw_capture == "the exact verbatim text"
