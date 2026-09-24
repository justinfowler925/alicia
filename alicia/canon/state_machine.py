"""Canonical Work Item state machine + completion-proof enforcement.

Implements the REV-510 spec's state graph and its enforcement rule:

    No state_history entry with state = acceptance or state = closure is
    valid unless its actor is the authenticated accountable human owner.
    A granted Approval records consent for its scoped external action; it
    does not authenticate a worker/agent to finalize Canon state.

This is the load-bearing module for REV-513's acceptance criterion
"No model narration can change canonical completion state without
required evidence and accountable-owner acceptance."
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Iterable, Optional

from .identity import (
    AuthenticatedPrincipal,
    CanonError,
    DEFAULT_IDENTITY_REGISTRY,
    require_owner,
)
from .models import (
    Approval,
    ApprovalStatus,
    Decision,
    Evidence,
    HAPPY_PATH,
    StateHistoryEntry,
    TERMINAL_STATES,
    WorkItem,
    WorkItemState,
    WorkItemType,
)

SIDE_STATES = {WorkItemState.BLOCKED, WorkItemState.CANCELED, WorkItemState.SUPERSEDED}

# States that count as "the accountable owner has finalized this" per the
# spec's enforcement rule. Worker/agent actors may never author a
# state_history entry landing here directly.
OWNER_GATED_STATES = {WorkItemState.ACCEPTANCE, WorkItemState.CLOSURE}


def _has_usable_content_ref(evidence: Evidence) -> bool:
    """Return whether an Evidence reference contains inspectable content."""
    return bool(evidence.content_ref and evidence.content_ref.strip())


def _is_diff_reference(evidence: Evidence) -> bool:
    """Return whether evidence identifies a diff, PR, or commit receipt."""
    content_ref = evidence.content_ref.lower()
    return evidence.type.value == "diff" or "pull" in content_ref or "commit" in content_ref


def _task_completion_evidence(evidence: list[Evidence]) -> tuple[list[Evidence], list[Evidence]]:
    """Return the valid task diff receipts and passing-check receipts."""
    diff_evidence = [
        item
        for item in evidence
        if _is_diff_reference(item) and _has_usable_content_ref(item)
    ]
    check_evidence = [item for item in evidence if item.type.value == "run_output"]
    return diff_evidence, check_evidence


def _happy_path_index(state: WorkItemState) -> Optional[int]:
    try:
        return HAPPY_PATH.index(state)
    except ValueError:
        return None


def completion_proof_ok(
    work_item_type: WorkItemType,
    evidence: list[Evidence],
    *,
    has_owner_review_comment: bool = False,
    decision: Optional[Decision] = None,
    approval: Optional[Approval] = None,
) -> tuple[bool, str]:
    """Check the completion-proof table for the given work type.

    Returns (ok, reason). `reason` explains what's missing when not ok.

    Every branch checks that the SPECIFIC required evidence item is
    verified -- not merely that some unrelated verified evidence exists
    somewhere in the list. An unverified diff sitting next to a verified
    screenshot must not pass a task's proof requirement.
    """
    if work_item_type == WorkItemType.TASK:
        diff_evidence, check_evidence = _task_completion_evidence(evidence)
        if not (any(e.verified for e in diff_evidence) and any(e.verified for e in check_evidence)):
            return False, "task requires a verified diff/PR/commit reference plus verified passing-check evidence"
        return True, ""

    if work_item_type == WorkItemType.INVESTIGATION:
        if decision is None or not decision.rationale or not decision.decided_by:
            return False, "investigation requires a Decision object with rationale and decided_by set"
        if not decision.evidence_refs and not any(e.verified for e in evidence):
            return False, "investigation's Decision must have linked verified evidence"
        return True, ""

    if work_item_type == WorkItemType.POLICY:
        doc_evidence = [e for e in evidence if e.type.value == "doc_link"]
        if not any(e.verified for e in doc_evidence):
            return False, "policy/spec requires a verified committed-document reference"
        if not has_owner_review_comment:
            return False, "policy/spec requires an owner review comment referencing the document"
        return True, ""

    if work_item_type == WorkItemType.COMMUNICATION:
        if approval is None or approval.status != ApprovalStatus.GRANTED:
            return False, "communication/external write requires an Approval object granted before the Run"
        if approval.granted_at is not None:
            post_action = [e for e in evidence if e.verified and e.captured_at >= approval.granted_at]
        else:
            post_action = [e for e in evidence if e.verified]
        if not post_action:
            return False, "communication/external write requires verified post-action evidence (e.g. sent message, published record)"
        return True, ""

    # Default: any work type not in the table.
    verified = [e for e in evidence if e.verified]
    if not verified:
        return False, "default work type requires at least one verified Evidence item"
    return True, ""


def transition(
    work_item: WorkItem,
    new_state: WorkItemState,
    actor: str,
    *,
    reason: str = "",
    owners: Iterable[str] = (),
    authenticated_principal: Optional[AuthenticatedPrincipal] = None,
    evidence: Optional[list[Evidence]] = None,
    approval: Optional[Approval] = None,
    decision: Optional[Decision] = None,
    superseded_by: Optional[str] = None,
    has_owner_review_comment: bool = False,
    decision_not_required: str = "",
    lightweight_scope: str = "",
    low_risk: bool = False,
) -> WorkItem:
    """Attempt a state transition, raising CanonError if it violates the model.

    ``owners`` remains accepted for call compatibility, but it is no longer an
    authorization input: a caller can freely choose that list. Entering
    acceptance or closure instead requires ``authenticated_principal`` to be
    a registry-issued principal for the config-defined owner. Other transitions
    intentionally remain available to workers without an owner principal.

    A TASK may use the lightweight triage/planning -> execution path only when
    the caller records why a decision is not required, the bounded scope, and
    an explicit low-risk assertion. Those values are written into the immutable
    state history instead of silently bypassing the standard path.

    On success, appends a StateHistoryEntry and returns the same WorkItem
    (mutated in place) for convenience.
    """
    evidence = evidence or []
    current = work_item.state
    completion_evidence_ref: Optional[str] = None

    if current in TERMINAL_STATES:
        raise CanonError(f"work item {work_item.id} is in terminal state {current.value}; no further transitions allowed")

    # --- Side states: reachable from any non-terminal state ---
    if new_state == WorkItemState.BLOCKED:
        if not reason:
            raise CanonError("blocked requires a recorded blocking reason")
    elif new_state == WorkItemState.CANCELED:
        if not reason:
            raise CanonError("canceled requires a recorded reason")
    elif new_state == WorkItemState.SUPERSEDED:
        if not superseded_by:
            raise CanonError("superseded requires a link to the superseding work item")
        reason = reason or f"superseded_by:{superseded_by}"

    # --- Owner-gated states: acceptance / closure ---
    elif new_state in OWNER_GATED_STATES:
        # A string match against `owners` is not authentication. The owner name
        # must be backed by a principal issued from configured identity data.
        require_owner(actor, authenticated_principal, registry=DEFAULT_IDENTITY_REGISTRY)
        # closure additionally requires prior acceptance on the happy path
        if new_state == WorkItemState.CLOSURE and current not in (WorkItemState.ACCEPTANCE, WorkItemState.MONITORING):
            raise CanonError("closure requires the work item to have passed through acceptance (or monitoring) first")

    # --- Happy-path forward moves ---
    else:
        cur_idx = _happy_path_index(current)
        new_idx = _happy_path_index(new_state)
        if cur_idx is None or new_idx is None:
            raise CanonError(f"unknown transition {current.value} -> {new_state.value}")
        lightweight_execution = (
            new_state == WorkItemState.EXECUTION
            and current in (WorkItemState.TRIAGE, WorkItemState.PLANNING)
        )
        if lightweight_execution:
            if work_item.type != WorkItemType.TASK:
                raise CanonError("lightweight execution is only available for task work items")
            if not decision_not_required.strip():
                raise CanonError("lightweight execution requires a decision_not_required reason")
            if not lightweight_scope.strip() or not low_risk:
                raise CanonError("lightweight execution requires a bounded scope and explicit low_risk guard")
            audit_reason = (
                f"decision_not_required={decision_not_required.strip()}; "
                f"lightweight_scope={lightweight_scope.strip()}; low_risk=true"
            )
            reason = f"{reason}; {audit_reason}" if reason else audit_reason
        # An owner request for changes is a reasoned rework loop from review
        # back to execution. The CLI supplies the authenticated owner and
        # recorded reason; transition remains the canonical audit writer.
        if current == WorkItemState.REVIEW and new_state == WorkItemState.EXECUTION and not reason:
            raise CanonError("review -> execution requires a recorded request-changes reason")
        # clarification is explicitly re-enterable from triage or planning
        if new_state == WorkItemState.CLARIFICATION:
            if current not in (WorkItemState.TRIAGE, WorkItemState.PLANNING):
                raise CanonError("clarification is only re-enterable from triage or planning")
        elif (
            current == WorkItemState.REVIEW and new_state == WorkItemState.EXECUTION
        ) or lightweight_execution:
            pass
        elif new_idx != cur_idx + 1:
            raise CanonError(f"'{current.value}' -> '{new_state.value}' skips or reverses the canonical order")

        if new_state == WorkItemState.DECISION:
            pass  # entering decision is always fine; exiting is gated below
        if current == WorkItemState.DECISION and new_state == WorkItemState.EXECUTION:
            if decision is None or decision.decided_by is None or decision.decided_at is None:
                raise CanonError("cannot exit 'decision' state without a resolved Decision object (decided_by/decided_at set)")

        if current == WorkItemState.VALIDATION and new_state == WorkItemState.REVIEW:
            ok, why = completion_proof_ok(
                work_item.type,
                evidence,
                has_owner_review_comment=has_owner_review_comment,
                decision=decision,
                approval=approval,
            )
            if not ok:
                raise CanonError(f"cannot exit 'validation': {why}")
            if work_item.delivery_requirements:
                from ..workflow_control import evaluate_delivery_receipts

                delivery = evaluate_delivery_receipts(work_item, evidence)
                if not delivery.ok:
                    details = []
                    for label in ("missing", "stale", "failed", "mismatched"):
                        values = getattr(delivery, label)
                        if values:
                            details.append(f"{label}={','.join(values)}")
                    raise CanonError(
                        "cannot exit 'validation': repository delivery policy failed: "
                        + "; ".join(details)
                    )
            if work_item.type == WorkItemType.TASK:
                diff_evidence, _ = _task_completion_evidence(evidence)
                completion_evidence_ref = next(item.id for item in diff_evidence if item.verified)

    work_item.state = new_state
    entered_at = datetime.now(UTC)
    work_item.state_entered_at = entered_at
    work_item.state_history.append(
        StateHistoryEntry(
            state=new_state,
            actor=actor,
            time=entered_at,
            reason=reason,
            evidence_ref=completion_evidence_ref or (evidence[-1].id if evidence else None),
        )
    )
    return work_item
