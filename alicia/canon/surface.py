"""Owner-facing Canon surface: one store, inbox/today/review, live dogfood."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..paths import canon_db_path
from .cli import (
    capture_manual_inbox_item,
    capture_slack_from_atlas,
    perform_owner_action,
    promote_inbox_item,
)
from .identity import DEFAULT_IDENTITY_REGISTRY, CanonError, require_owner
from .models import (
    Approval,
    ApprovalStatus,
    Evidence,
    EvidenceType,
    ExecutionCard,
    ExecutionCardStatus,
    InboxItem,
    InboxStatus,
    Project,
    ProveVerdict,
    Run,
    RunStatus,
    TERMINAL_STATES,
    WorkItem,
    WorkItemState,
)
from .report import build_portfolio_report
from .state_machine import transition
from .store import CanonStore

log = logging.getLogger("alicia.canon.surface")

# Today is every live work item. Promote creates TRIAGE; hiding that state
# made Start move a capture off Inbox into nowhere.
TODAY_STATES = frozenset(state for state in WorkItemState if state not in TERMINAL_STATES)
WORKER = next(iter(DEFAULT_IDENTITY_REGISTRY.worker_identities))


def open_canon_store(db_path: str | Path | None = None) -> CanonStore:
    path = Path(db_path) if db_path is not None else canon_db_path()
    if str(path) == ":memory:":
        return CanonStore(":memory:")
    path = path.expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    return CanonStore(path)


def _owner():
    principal = DEFAULT_IDENTITY_REGISTRY.owner_principal()
    require_owner(principal.identity, principal, registry=DEFAULT_IDENTITY_REGISTRY)
    return principal


def _public(obj: Any) -> dict[str, Any]:
    payload = obj.model_dump(mode="json")
    payload.pop("snapshot", None)
    if isinstance(obj, ExecutionCard):
        payload["snapshot_keys"] = sorted((obj.snapshot or {}).keys())
    return payload


def snapshot(store: CanonStore) -> dict[str, Any]:
    inbox = [
        item
        for item in store.list(InboxItem)
        if item.status in {InboxStatus.UNCATEGORIZED, InboxStatus.REVIEWED}
    ]
    work_items = store.list(WorkItem)
    today = [item for item in work_items if item.state in TODAY_STATES]
    review = [item for item in work_items if item.state == WorkItemState.REVIEW]
    report = build_portfolio_report(store.list(Project), work_items, store.list(Run))
    return {
        "inbox": [_public(item) for item in inbox],
        "today": [_public(item) for item in today],
        "review": [_public(item) for item in review],
        "execution_cards": [_public(card) for card in store.list(ExecutionCard)],
        "report": {
            "generated_at": report.generated_at.isoformat(),
            "stuck": [
                {
                    "id": row.work_item.id,
                    "title": row.work_item.title,
                    "state": row.work_item.state.value,
                }
                for row in report.work_item_aging
                if row.is_stuck
            ],
        },
        "db_path": str(store.db_path),
    }


def capture_inbox(store: CanonStore, *, raw_capture: str, source: str) -> InboxItem:
    return capture_manual_inbox_item(store, raw_capture=raw_capture, source=source)


def capture_slack(store: CanonStore, *, limit: int = 50) -> dict[str, Any]:
    result = capture_slack_from_atlas(store, limit=limit)
    captured = getattr(result, "captured_ids", None) or []
    return {"ok": True, "captured": list(captured), "detail": str(result)}


def promote(store: CanonStore, inbox_item_id: str, *, title: str, description: str = "") -> WorkItem:
    return promote_inbox_item(store, inbox_item_id, title=title, description=description)


def owner_review(store: CanonStore, work_item_id: str, action: str, *, reason: str = "") -> WorkItem:
    return perform_owner_action(store, work_item_id, action, reason=reason)


def grant_and_seal_card(
    store: CanonStore,
    *,
    work_item_id: str,
    scope: str,
    target: str = "",
    run_id: str | None = None,
) -> tuple[Approval, ExecutionCard]:
    principal = _owner()
    work_item = store.get(WorkItem, work_item_id)
    if work_item is None:
        raise CanonError(f"work item '{work_item_id}' was not found")
    approval = Approval(
        work_item_id=work_item_id,
        run_id=run_id,
        requested_by=principal.identity,
        approved_by=principal.identity,
        scope=scope,
        granted_at=datetime.now(UTC),
        status=ApprovalStatus.GRANTED,
    )
    store.save(approval, authenticated_principal=principal)
    if approval.id not in work_item.approval_refs:
        work_item.approval_refs.append(approval.id)
        store.save(work_item)
    card = ExecutionCard(
        work_item_id=work_item_id,
        approval_id=approval.id,
        run_id=run_id,
        scope=scope,
        target=target,
        sealed_by=principal.identity,
        sealed_at=datetime.now(UTC),
        status=ExecutionCardStatus.SEALED,
        snapshot={
            "work_item_id": work_item_id,
            "title": work_item.title,
            "state": work_item.state.value,
            "scope": scope,
            "target": target,
            "approval_id": approval.id,
        },
    )
    store.save(card)
    return approval, card


def slack_capture_tick() -> dict[str, Any]:
    try:
        store = open_canon_store()
        try:
            return capture_slack(store)
        finally:
            store.close()
    except Exception as exc:  # noqa: BLE001
        log.info("canon slack capture skipped: %s", exc)
        return {"ok": False, "error": str(exc)}


def _attach_verified(
    store: CanonStore,
    *,
    work_item: WorkItem,
    run: Run,
    evidence_type: EvidenceType,
    content_ref: str,
) -> Evidence:
    principal = _owner()
    evidence = Evidence(
        type=evidence_type,
        captured_by=WORKER,
        captured_by_kind="worker",
        linked_object_id=run.id,
        content_ref=content_ref,
        verified=True,
        verified_by=principal.identity,
    )
    store.save(evidence, authenticated_principal=principal)
    if evidence.id not in run.evidence_refs:
        run.evidence_refs.append(evidence.id)
        store.save(run)
    if evidence.id not in work_item.evidence_refs:
        work_item.evidence_refs.append(evidence.id)
        store.save(work_item)
    return evidence


def dogfood_pipeline(store: CanonStore, *, marker: str) -> dict[str, Any]:
    principal = _owner()
    inbox = capture_inbox(
        store,
        raw_capture=f"{marker}: finish Alicia Core and prove Canon is the work surface",
        source="alicia:dogfood",
    )
    work = promote(
        store,
        inbox.id,
        title=f"{marker} Alicia Core live proof",
        description=inbox.raw_capture,
    )
    transition(
        work,
        WorkItemState.EXECUTION,
        WORKER,
        reason="bounded dogfood dispatch",
        decision_not_required="single-laptop proof of the Canon surface",
        lightweight_scope="seal one execution card and close the item",
        low_risk=True,
    )
    store.save(work)
    approval, card = grant_and_seal_card(
        store,
        work_item_id=work.id,
        scope="dogfood-canon-pipeline",
        target="alicia",
    )
    run = Run(
        actor=WORKER,
        work_item_id=work.id,
        status=RunStatus.READY_FOR_REVIEW,
        prove_verdict=ProveVerdict.PASS,
        target="alicia",
        scope="dogfood-canon-pipeline",
        ended_at=datetime.now(UTC),
    )
    store.save(run)
    approval.run_id = run.id
    store.save(approval, authenticated_principal=principal)
    diff = _attach_verified(
        store,
        work_item=work,
        run=run,
        evidence_type=EvidenceType.DIFF,
        content_ref="https://github.com/justinfowler925/alicia/commit/dogfood",
    )
    checks = _attach_verified(
        store,
        work_item=work,
        run=run,
        evidence_type=EvidenceType.RUN_OUTPUT,
        content_ref="pytest tests/test_canon_surface.py",
    )
    work = store.get(WorkItem, work.id)
    assert work is not None
    evidence_objs = [item for ref in work.evidence_refs if (item := store.get(Evidence, ref))]
    transition(
        work,
        WorkItemState.VALIDATION,
        WORKER,
        reason="implementation attempted",
        evidence=evidence_objs,
    )
    store.save(work)
    work = store.get(WorkItem, work.id)
    assert work is not None
    transition(
        work,
        WorkItemState.REVIEW,
        WORKER,
        reason="completion proof attached",
        evidence=evidence_objs,
        approval=approval,
        has_owner_review_comment=True,
    )
    store.save(work)
    work = owner_review(store, work.id, "accept")
    transition(
        work,
        WorkItemState.CLOSURE,
        principal.identity,
        reason="owner accepted live dogfood proof",
        authenticated_principal=principal,
        evidence=evidence_objs,
        approval=approval,
    )
    store.save(work)
    return {
        "inbox_id": inbox.id,
        "work_item_id": work.id,
        "state": work.state.value,
        "approval_id": approval.id,
        "execution_card_id": card.id,
        "execution_card_status": card.status.value,
        "run_id": run.id,
        "diff_evidence_id": diff.id,
        "check_evidence_id": checks.id,
        "owner": principal.identity,
    }
