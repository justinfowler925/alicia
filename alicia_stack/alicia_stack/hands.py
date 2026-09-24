"""Hands — dispatch interface and canon-aware dispatch wrapper.

``CanonHandsDispatcher`` is deliberately a decorator for the existing
``HandsDispatcher`` protocol.  Backends still only need to implement
``dispatch(packet)``; the wrapper records the dispatch and its untrusted worker
handoff in the canonical work-object store.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any, Protocol

from alicia.canon import (
    CanonError,
    CanonStore,
    Evidence,
    EvidenceType,
    ProveVerdict,
    Run,
    RunStatus,
    WorkItem,
    WorkItemState,
    completion_proof_ok,
    transition,
)

from .prove import prove
from .types import HandsResult, ProveReport, Verdict


class HandsDispatcher(Protocol):
    def dispatch(self, packet: dict[str, Any]) -> HandsResult:
        """Run work offstage. Return structured handoff for Prove."""
        ...


class CanonHandsDispatcher:
    """Persist a canonical Run and worker artifacts around a Hands dispatch.

    ``actor`` is supplied by the dispatch caller rather than derived here.  The
    authenticated-identity enforcement in REV-519 will eventually provide this
    value; until then this wrapper faithfully records the identity string
    available at the dispatch seam.

    A packet may include a ``canon`` mapping to override the bound context for
    a single dispatch.  This makes it possible for one configured dispatcher to
    serve multiple Work Items without changing the Hands protocol:

    ``{"canon": {"actor": "...", "work_item_id": "...", "target": "...",
    "scope": "..."}}``.
    """

    def __init__(
        self,
        dispatcher: HandsDispatcher,
        store: CanonStore,
        *,
        actor: str,
        work_item_id: str,
        target: str = "",
        scope: str = "",
    ) -> None:
        if not actor:
            raise ValueError("canon dispatch requires an actor identity")
        if not work_item_id:
            raise ValueError("canon dispatch requires a work_item_id")
        self.dispatcher = dispatcher
        self.store = store
        self.actor = actor
        self.work_item_id = work_item_id
        self.target = target
        self.scope = scope

    def dispatch(self, packet: dict[str, Any]) -> HandsResult:
        """Dispatch work, recording its Run and all worker-produced artifacts."""
        context = self._context_for(packet)
        run = Run(**context)
        self.store.save(run)

        try:
            result = self.dispatcher.dispatch(packet)
        except Exception:
            run.status = RunStatus.FAILED
            run.ended_at = _now()
            self.store.save(run)
            raise

        artifacts = _evidence_from_hands_result(result, run)
        for evidence in artifacts:
            self.store.save(evidence)

        run.evidence_refs.extend(evidence.id for evidence in artifacts)
        # Run Prove exactly once at the persistence boundary.  The resulting
        # verdict is the completion signal recorded with this Run; raw worker
        # status keys are intentionally not trusted as a parallel mechanism.
        prove_report = prove(result)
        run.prove_verdict = ProveVerdict(prove_report.verdict.value)
        run.status = _run_status(prove_report)
        run.ended_at = _now()
        self.store.save(run)

        # The WorkItem keeps a convenient full evidence index while each
        # Evidence object is linked to the more precise Run that produced it.
        work_item = self.store.get(WorkItem, run.work_item_id)
        if work_item is not None:
            work_item.evidence_refs.extend(
                evidence.id for evidence in artifacts if evidence.id not in work_item.evidence_refs
            )
            self.store.save(work_item)

        # HandsResult is the existing API boundary.  Preserve that API while
        # making the persisted Run addressable by completion handling code.
        result.raw.setdefault("canon_run_id", run.id)
        # Router completion receives the exact report that was used to set the
        # persisted Run status, rather than producing a second verdict.
        result.raw.setdefault("canon_prove_report", prove_report)
        return result

    def _context_for(self, packet: dict[str, Any]) -> dict[str, str]:
        packet_context = packet.get("canon", {})
        if not isinstance(packet_context, dict):
            raise TypeError("packet canon context must be a mapping")
        fields = packet.get("fields", {})
        if not isinstance(fields, dict):
            fields = {}

        context = {
            "actor": str(packet_context.get("actor") or self.actor),
            "work_item_id": str(packet_context.get("work_item_id") or self.work_item_id),
            "target": str(packet_context.get("target") or self.target or packet.get("target") or fields.get("target") or ""),
            "scope": str(packet_context.get("scope") or self.scope or packet.get("scope") or packet.get("utterance") or ""),
        }
        if not context["actor"] or not context["work_item_id"]:
            raise ValueError("canon dispatch requires actor and work_item_id")
        return context


def transition_run_to_review(
    store: CanonStore,
    *,
    work_item: WorkItem,
    run: Run,
    actor: str,
    owners: list[str],
    approval: Any = None,
    decision: Any = None,
    has_owner_review_comment: bool = False,
) -> WorkItem:
    """Move validation -> review only when this Run's persisted proof passes.

    This is the dispatch-path enforcement hook: it requires the persisted
    Prove PASS verdict, then loads the Evidence objects attached to the
    supplied Run and calls ``completion_proof_ok`` *before* delegating to the
    canonical state-machine transition. It saves the resulting WorkItem. The
    state machine repeats the evidence proof check as a defense-in-depth guard
    for callers that bypass this helper.
    """
    if run.work_item_id != work_item.id:
        raise CanonError(f"run {run.id} does not belong to work item {work_item.id}")
    latest_work_item = store.get(WorkItem, work_item.id)
    if latest_work_item is None:
        raise CanonError(f"work item {work_item.id} was not found")
    # The dispatch wrapper may have appended evidence after the caller loaded
    # this Work Item. Transition the latest persisted object, while preserving
    # any append-only refs a caller added to its stale instance.
    for field_name in ("evidence_refs", "approval_refs", "decision_refs"):
        latest_refs = getattr(latest_work_item, field_name)
        supplied_refs = getattr(work_item, field_name)
        setattr(latest_work_item, field_name, list(dict.fromkeys([*latest_refs, *supplied_refs])))
    work_item = latest_work_item
    if work_item.state != WorkItemState.VALIDATION:
        raise CanonError("only a work item in 'validation' can move to 'review'")
    if run.status != RunStatus.READY_FOR_REVIEW:
        raise CanonError(f"run {run.id} is '{run.status.value}', not ready_for_review")
    if run.prove_verdict != ProveVerdict.PASS:
        verdict = run.prove_verdict.value if run.prove_verdict is not None else "missing"
        raise CanonError(f"run {run.id} Prove verdict is '{verdict}', not PASS")

    evidence = _evidence_for_run(store, run)
    ok, why = completion_proof_ok(
        work_item.type,
        evidence,
        has_owner_review_comment=has_owner_review_comment,
        decision=decision,
        approval=approval,
    )
    if not ok:
        raise CanonError(f"cannot exit 'validation': {why}")

    transition(
        work_item,
        WorkItemState.REVIEW,
        actor,
        owners=owners,
        evidence=evidence,
        approval=approval,
        decision=decision,
        has_owner_review_comment=has_owner_review_comment,
    )
    store.save(work_item)
    return work_item


def _evidence_for_run(store: CanonStore, run: Run) -> list[Evidence]:
    return [
        evidence
        for evidence_id in run.evidence_refs
        if (evidence := store.get(Evidence, evidence_id)) is not None
    ]


def _evidence_from_hands_result(result: HandsResult, run: Run) -> list[Evidence]:
    evidence: list[Evidence] = []
    for claim in result.claims:
        evidence.append(_artifact(run, EvidenceType.LOG, f"claim: {claim}"))
    for key, value in result.evidence.items():
        evidence.append(_artifact(run, _evidence_type_for(key, value), _content_ref(key, value)))
    # Preserve the backend's raw handoff separately.  It is useful audit
    # context but never verified merely because the worker reported it.
    evidence.append(_artifact(run, EvidenceType.RUN_OUTPUT, _content_ref("raw", result.raw)))
    return evidence


def _artifact(run: Run, evidence_type: EvidenceType, content_ref: str) -> Evidence:
    return Evidence(
        type=evidence_type,
        captured_by=run.actor,
        captured_by_kind="worker",
        linked_object_id=run.id,
        content_ref=content_ref,
    )


def _evidence_type_for(key: str, value: Any) -> EvidenceType:
    name = key.lower()
    if any(marker in name for marker in ("screenshot", "image", "png", "jpg")):
        return EvidenceType.SCREENSHOT
    if any(marker in name for marker in ("diff", "pull", "commit", "sha")):
        return EvidenceType.DIFF
    if any(marker in name for marker in ("doc", "document")):
        return EvidenceType.DOC_LINK
    if "url" in name or (isinstance(value, str) and value.startswith(("http://", "https://"))):
        return EvidenceType.EXTERNAL_URL
    if any(marker in name for marker in ("test", "check", "ci", "output", "log")):
        return EvidenceType.RUN_OUTPUT
    return EvidenceType.LOG


def _content_ref(key: str, value: Any) -> str:
    if isinstance(value, str):
        return value
    return f"{key}: {json.dumps(value, sort_keys=True, default=str)}"


def _run_status(report: ProveReport) -> RunStatus:
    """Map the persisted Prove conclusion to the only permitted Run status."""
    if report.verdict == Verdict.FAIL:
        return RunStatus.FAILED
    if report.verdict == Verdict.PASS:
        return RunStatus.READY_FOR_REVIEW
    return RunStatus.IMPLEMENTATION_ATTEMPTED


def _now() -> datetime:
    return datetime.now(UTC)
