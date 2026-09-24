"""Command-line review surface for canonical Work Items (REV-515)."""

from __future__ import annotations

import json
import sys
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from .identity import DEFAULT_IDENTITY_REGISTRY, CanonError, require_owner
from .models import (
    Approval,
    Decision,
    Evidence,
    EvidenceType,
    InboxItem,
    InboxStatus,
    Project,
    Run,
    Watch,
    WorkItem,
    WorkItemState,
    WorkItemType,
)
from .report import PortfolioReport, build_portfolio_report
from .slack import SlackInboxCaptureResult, capture_slack_items
from .state_machine import transition
from .store import CanonStore
from .watches import evaluate_watch


def open_store(db_path: str) -> CanonStore:
    """Open the local Canon database, creating its parent directory if needed."""
    if db_path == ":memory:":
        return CanonStore(db_path)
    path = Path(db_path).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    return CanonStore(path)


def _age_in_state(work_item: WorkItem, *, now: datetime | None = None) -> str:
    """Return a compact age for the Work Item's current state."""
    entries = [entry for entry in work_item.state_history if entry.state == work_item.state]
    state_time = entries[-1].time if entries else work_item.state_entered_at
    if state_time is None:
        return "unknown"

    current_time = now or datetime.now(UTC)
    if state_time.tzinfo is None:
        state_time = state_time.replace(tzinfo=UTC)
    seconds = max(0, int((current_time - state_time).total_seconds()))
    days, seconds = divmod(seconds, 86_400)
    hours, seconds = divmod(seconds, 3_600)
    minutes = seconds // 60
    if days:
        return f"{days}d {hours}h"
    if hours:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


def render_work_item_list(work_items: Iterable[WorkItem]) -> str:
    """Render the concise queue view used by ``alicia canon list``."""
    rows = [
        ("ID", "TITLE", "TYPE", "PRIORITY", "AGE IN STATE"),
        *[
            (
                item.id,
                item.title,
                item.type.value,
                str(item.priority),
                _age_in_state(item),
            )
            for item in work_items
        ],
    ]
    widths = [max(len(row[column]) for row in rows) for column in range(len(rows[0]))]
    return "\n".join(
        "  ".join(value.ljust(widths[column]) for column, value in enumerate(row))
        for row in rows
    )


def render_inbox_list(inbox_items: Iterable[InboxItem]) -> str:
    """Render the owner queue of captured, not-yet-promoted InboxItems."""

    rows = [
        ("ID", "STATUS", "RECEIVED AT", "RAW CAPTURE"),
        *[
            (
                item.id,
                item.status.value,
                item.received_at.isoformat(),
                item.raw_capture.replace("\n", " ")[:100],
            )
            for item in inbox_items
        ],
    ]
    widths = [max(len(row[column]) for row in rows) for column in range(len(rows[0]))]
    return "\n".join(
        "  ".join(value.ljust(widths[column]) for column, value in enumerate(row))
        for row in rows
    )


def render_inbox_detail(inbox_item: InboxItem) -> str:
    """Render an InboxItem with its immutable raw capture and provenance."""

    return "\n".join(("Inbox Item", _render_object(inbox_item)))


def render_watch_list(watches: Iterable[Watch]) -> str:
    """Render the configured Watch routing and trigger conditions."""

    rows = [
        ("ID", "TARGET", "CONDITION", "CHANNEL", "ACTIVE"),
        *[
            (
                watch.id,
                watch.target,
                watch.trigger_condition,
                watch.notify_channel,
                str(watch.active).lower(),
            )
            for watch in watches
        ],
    ]
    widths = [max(len(row[column]) for row in rows) for column in range(len(rows[0]))]
    return "\n".join(
        "  ".join(value.ljust(widths[column]) for column, value in enumerate(row))
        for row in rows
    )


def _objects_for_ids(store: CanonStore, model_cls: type, object_ids: Iterable[str]) -> list:
    """Load referenced objects in their recorded order, skipping stale refs."""
    objects = []
    seen: set[str] = set()
    for object_id in object_ids:
        if object_id in seen:
            continue
        seen.add(object_id)
        obj = store.get(model_cls, object_id)
        if obj is not None:
            objects.append(obj)
    return objects


def _append_unique(objects: list, additions: Iterable) -> list:
    seen = {object.id for object in objects}
    objects.extend(obj for obj in additions if obj.id not in seen and not seen.add(obj.id))
    return objects


def linked_objects(store: CanonStore, work_item: WorkItem) -> dict[str, list]:
    """Gather direct and reverse Canon links for an owner review."""
    runs = [run for run in store.list(Run) if run.work_item_id == work_item.id]
    run_ids = {run.id for run in runs}

    evidence = _objects_for_ids(
        store,
        Evidence,
        [*work_item.evidence_refs, *(ref for run in runs for ref in run.evidence_refs)],
    )
    _append_unique(
        evidence,
        (
            item
            for item in store.list(Evidence)
            if item.linked_object_id == work_item.id or item.linked_object_id in run_ids
        ),
    )

    decisions = _objects_for_ids(store, Decision, work_item.decision_refs)
    _append_unique(
        decisions,
        (item for item in store.list(Decision) if work_item.id in item.linked_work_item_ids),
    )

    approvals = _objects_for_ids(store, Approval, work_item.approval_refs)
    _append_unique(
        approvals,
        (item for item in store.list(Approval) if item.work_item_id == work_item.id),
    )
    return {
        "Evidence": evidence,
        "Decisions": decisions,
        "Approvals": approvals,
        "Runs": runs,
    }


def _render_object(obj: object) -> str:
    return json.dumps(obj.model_dump(mode="json"), indent=2, sort_keys=True)


def render_work_item_detail(store: CanonStore, work_item: WorkItem) -> str:
    """Render a Work Item and every linked review object."""
    sections = ["Work Item", _render_object(work_item)]
    for heading, objects in linked_objects(store, work_item).items():
        sections.extend(["", f"{heading} ({len(objects)})"])
        if objects:
            sections.extend(_render_object(obj) for obj in objects)
        else:
            sections.append("(none)")
    return "\n".join(sections)


def _format_duration(value: timedelta | None) -> str:
    """Format a report duration without hiding unknown legacy state ages."""
    if value is None:
        return "unknown"
    seconds = max(0, int(value.total_seconds()))
    days, seconds = divmod(seconds, 86_400)
    hours, seconds = divmod(seconds, 3_600)
    minutes = seconds // 60
    if days:
        return f"{days}d {hours}h"
    if hours:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


def render_portfolio_report(report: PortfolioReport) -> str:
    """Render the owner-facing project rollup, state-aging, and run summary."""
    lines = ["Portfolio report", f"Generated at: {report.generated_at.isoformat()}", "", "Project rollups"]
    if report.project_rollups:
        for rollup in report.project_rollups:
            counts = ", ".join(
                f"{state.value}: {count}"
                for state, count in sorted(rollup.state_counts.items(), key=lambda item: item[0].value)
            ) or "no Work Items"
            blocked = ", ".join(item.id for item in rollup.blocked_items) or "none"
            lines.extend(
                [
                    f"- {rollup.project.name} ({rollup.project.id})",
                    f"  States: {counts}",
                    f"  Blocked: {blocked}",
                ]
            )
    else:
        lines.append("(none)")

    stuck_items = [aging for aging in report.work_item_aging if aging.is_stuck]
    lines.extend(["", f"Stuck items (review/validation >= {_format_duration(report.stuck_after)})"])
    if stuck_items:
        for aging in stuck_items:
            item = aging.work_item
            lines.append(f"- {item.id}  {item.title}  {item.state.value}  {_format_duration(aging.age)}")
    else:
        lines.append("(none)")

    lines.extend(["", f"Failed runs (last {_format_duration(report.failed_run_lookback)})"])
    if report.failed_runs:
        for run in report.failed_runs:
            occurred_at = run.ended_at or run.started_at
            lines.append(f"- {run.id}  work item {run.work_item_id}  {occurred_at.isoformat()}")
    else:
        lines.append("(none)")
    return "\n".join(lines)


def perform_owner_action(
    store: CanonStore,
    work_item_id: str,
    action: str,
    *,
    reason: str = "",
) -> WorkItem:
    """Apply an owner review outcome using the REV-519 principal boundary."""
    work_item = store.get(WorkItem, work_item_id)
    if work_item is None:
        raise CanonError(f"work item '{work_item_id}' was not found")
    if work_item.state != WorkItemState.REVIEW:
        raise CanonError(
            f"work item '{work_item_id}' is in {work_item.state.value}, not review"
        )
    if action not in {"accept", "reject", "request-changes"}:
        raise ValueError(f"unknown owner action '{action}'")
    if action != "accept" and not reason.strip():
        raise CanonError(f"{action} requires a recorded reason")

    # There is intentionally no --actor flag or caller-supplied principal. The
    # local CLI obtains the configured owner's registry-issued principal and
    # passes it through to the state machine. A worker principal cannot satisfy
    # this check even if it claims the owner's email address.
    principal = DEFAULT_IDENTITY_REGISTRY.owner_principal()
    actor = principal.identity
    require_owner(actor, principal, registry=DEFAULT_IDENTITY_REGISTRY)

    target_state = {
        "accept": WorkItemState.ACCEPTANCE,
        "reject": WorkItemState.CANCELED,
        "request-changes": WorkItemState.EXECUTION,
    }[action]
    transition(
        work_item,
        target_state,
        actor,
        reason=reason.strip(),
        authenticated_principal=principal,
    )
    store.save(work_item)
    return work_item


def promote_inbox_item(
    store: CanonStore,
    inbox_item_id: str,
    *,
    title: str,
    description: str = "",
    work_item_type: WorkItemType = WorkItemType.TASK,
    priority: int = 0,
) -> WorkItem:
    """Explicitly promote one captured item after the configured owner reviews it."""

    inbox_item = store.get(InboxItem, inbox_item_id)
    if inbox_item is None:
        raise CanonError(f"inbox item '{inbox_item_id}' was not found")
    if inbox_item.status == InboxStatus.PROMOTED:
        raise CanonError(f"inbox item '{inbox_item_id}' is already promoted")
    if inbox_item.status == InboxStatus.DISCARDED:
        raise CanonError(f"inbox item '{inbox_item_id}' is discarded")

    principal = DEFAULT_IDENTITY_REGISTRY.owner_principal()
    require_owner(principal.identity, principal, registry=DEFAULT_IDENTITY_REGISTRY)
    work_item = WorkItem(
        title=title,
        description=description or inbox_item.raw_capture,
        type=work_item_type,
        priority=priority,
    )
    # This is the sole production caller of CanonStore.promote_inbox_item:
    # capture only creates unreviewed InboxItems and never reaches this branch.
    return store.promote_inbox_item(
        inbox_item,
        reviewed_by=principal.identity,
        work_item=work_item,
    )


def capture_slack_from_atlas(store: CanonStore, *, limit: int) -> SlackInboxCaptureResult:
    """Capture configured-channel work signals through the existing Atlas6 poller."""

    from ..client import AtlasClient

    return capture_slack_items(store, AtlasClient().peek_slack(limit=limit))


def capture_manual_inbox_item(store: CanonStore, *, raw_capture: str, source: str) -> InboxItem:
    """Persist an immutable, unreviewed InboxItem from supplied provenance."""

    if not raw_capture.strip():
        raise CanonError("inbox capture requires non-empty --raw-capture")
    if not source.strip():
        raise CanonError("inbox capture requires non-empty --source provenance")
    inbox_item = InboxItem(raw_capture=raw_capture, source=source)
    store.save(inbox_item)
    return inbox_item


def _owner_principal() -> Any:
    """Issue the local configured owner's principal for owner-only operations."""

    principal = DEFAULT_IDENTITY_REGISTRY.owner_principal()
    require_owner(principal.identity, principal, registry=DEFAULT_IDENTITY_REGISTRY)
    return principal


def _verifier_principal(identity: str) -> Any:
    """Issue an allowlisted verifier principal, including the configured owner."""

    if identity == DEFAULT_IDENTITY_REGISTRY.owner_identity:
        return _owner_principal()
    return DEFAULT_IDENTITY_REGISTRY.verifier_principal(identity)


def _load_required(store: CanonStore, model_cls: type, object_id: str, label: str) -> Any:
    obj = store.get(model_cls, object_id)
    if obj is None:
        raise CanonError(f"{label} '{object_id}' was not found")
    return obj


def _optional_linked_object(store: CanonStore, model_cls: type, object_id: str) -> Any | None:
    if not object_id:
        return None
    return _load_required(store, model_cls, object_id, model_cls.__name__.lower())


def _evidence_for_ids(store: CanonStore, evidence_ids: Iterable[str]) -> list[Evidence]:
    return [_load_required(store, Evidence, evidence_id, "evidence") for evidence_id in evidence_ids]


def _add_ref(obj: Any, field_name: str, value: str) -> None:
    refs = getattr(obj, field_name)
    if value not in refs:
        refs.append(value)


def create_decision(
    store: CanonStore,
    *,
    question: str,
    chosen_option: str,
    rationale: str,
    decided_by: str,
    options_considered: Iterable[str] = (),
) -> Decision:
    """Create a resolved Decision object without silently linking it."""

    if not question.strip() or not chosen_option.strip() or not rationale.strip() or not decided_by.strip():
        raise CanonError("decision create requires --question, --chosen-option, --rationale, and --decided-by")
    decision = Decision(
        question=question,
        chosen_option=chosen_option,
        rationale=rationale,
        decided_by=decided_by,
        options_considered=list(options_considered),
    )
    store.save(decision)
    return decision


def link_decision(store: CanonStore, *, decision_id: str, work_item_id: str) -> Decision:
    """Record the bidirectional append-only link between Decision and WorkItem."""

    decision = _load_required(store, Decision, decision_id, "decision")
    work_item = _load_required(store, WorkItem, work_item_id, "work item")
    _add_ref(decision, "linked_work_item_ids", work_item.id)
    _add_ref(work_item, "decision_refs", decision.id)
    store.save(decision)
    store.save(work_item)
    return decision


def transition_work_item(store: CanonStore, args: object) -> WorkItem:
    """Thin CLI adapter around the canonical transition function."""

    work_item = _load_required(store, WorkItem, args.work_item_id, "work item")
    target = WorkItemState(args.to)
    if target == WorkItemState.ACCEPTANCE:
        raise CanonError("use 'alicia canon accept <work-item-id>' for authenticated acceptance")
    if target == WorkItemState.CLOSURE:
        raise CanonError("use 'alicia canon close <work-item-id>' for authenticated closure")

    decision = _optional_linked_object(store, Decision, args.decision_id)
    approval = _optional_linked_object(store, Approval, args.approval_id)
    evidence = _evidence_for_ids(store, args.evidence_id)
    transition(
        work_item,
        target,
        args.actor,
        reason=args.reason.strip(),
        evidence=evidence,
        approval=approval,
        decision=decision,
        superseded_by=args.superseded_by.strip() or None,
        has_owner_review_comment=args.owner_review_comment,
        decision_not_required=args.decision_not_required,
        lightweight_scope=args.lightweight_scope,
        low_risk=args.low_risk,
    )
    store.save(work_item)
    return work_item


def start_run(store: CanonStore, *, work_item_id: str, actor: str, target: str, scope: str) -> Run:
    """Create the persisted start record for a worker attempt."""

    _load_required(store, WorkItem, work_item_id, "work item")
    if not actor.strip():
        raise CanonError("run start requires --actor")
    run = Run(actor=actor, work_item_id=work_item_id, target=target, scope=scope)
    store.save(run)
    return run


def _hands_classes() -> tuple[Any, Any, Any]:
    """Load the existing Hands dispatcher without duplicating its lifecycle rules."""

    stack_root = Path(__file__).resolve().parents[2] / "alicia_stack"
    if stack_root.is_dir() and str(stack_root) not in sys.path:
        sys.path.insert(0, str(stack_root))
    from alicia_stack.hands import CanonHandsDispatcher
    from alicia_stack.types import HandsResult

    class CliHands:
        def __init__(self, result: Any) -> None:
            self.result = result

        def dispatch(self, packet: dict[str, Any]) -> Any:
            return self.result

    return CanonHandsDispatcher, CliHands, HandsResult


def _parse_evidence_pairs(pairs: Iterable[str]) -> dict[str, Any]:
    """Parse repeatable key=value worker receipts for the local Hands adapter."""

    evidence: dict[str, Any] = {}
    for pair in pairs:
        key, separator, value = pair.partition("=")
        if not separator or not key.strip():
            raise CanonError("--evidence must use key=value (for example sha=<commit>)")
        if value.lower() in {"true", "false"}:
            evidence[key] = value.lower() == "true"
        elif value.isdigit():
            evidence[key] = int(value)
        else:
            evidence[key] = value
    return evidence


def dispatch_run(store: CanonStore, args: object) -> Run:
    """Dispatch a local Hands packet through CanonHandsDispatcher and persist Prove."""

    _load_required(store, WorkItem, args.work_item_id, "work item")
    if not args.actor.strip():
        raise CanonError("run dispatch requires --actor")
    CanonHandsDispatcher, CliHands, HandsResult = _hands_classes()
    result = HandsResult(
        summary=args.summary,
        claims=list(args.claim),
        evidence=_parse_evidence_pairs(args.evidence),
        raw={"cli": True},
    )
    dispatched = CanonHandsDispatcher(
        CliHands(result),
        store,
        actor=args.actor,
        work_item_id=args.work_item_id,
        target=args.target,
        scope=args.scope,
    ).dispatch({"template_id": "canon-cli", "utterance": args.scope})
    return _load_required(store, Run, dispatched.raw["canon_run_id"], "run")


def attach_evidence(store: CanonStore, args: object) -> Evidence:
    """Capture one Evidence item and append it to its Run and WorkItem indexes."""

    run = _load_required(store, Run, args.run_id, "run")
    if not args.content_ref.strip():
        raise CanonError("evidence attach requires non-empty --content-ref")
    evidence = Evidence(
        type=EvidenceType(args.type),
        captured_by=args.captured_by,
        captured_by_kind=args.captured_by_kind,
        linked_object_id=run.id,
        content_ref=args.content_ref,
    )
    store.save(evidence)
    _add_ref(run, "evidence_refs", evidence.id)
    store.save(run)
    work_item = _load_required(store, WorkItem, run.work_item_id, "work item")
    _add_ref(work_item, "evidence_refs", evidence.id)
    store.save(work_item)
    return evidence


def verify_evidence(store: CanonStore, *, evidence_id: str, verifier: str) -> Evidence:
    """Mark Evidence verified using an authenticated configured verifier."""

    evidence = _load_required(store, Evidence, evidence_id, "evidence")
    principal = _verifier_principal(verifier)
    evidence.verified = True
    evidence.verified_by = principal.identity
    store.save(evidence, authenticated_principal=principal)
    return evidence


def transition_run_review(store: CanonStore, args: object) -> WorkItem:
    """Use the existing Hands validation-to-review helper for a persisted Run."""

    stack_root = Path(__file__).resolve().parents[2] / "alicia_stack"
    if stack_root.is_dir() and str(stack_root) not in sys.path:
        sys.path.insert(0, str(stack_root))
    from alicia_stack.hands import transition_run_to_review

    run = _load_required(store, Run, args.run_id, "run")
    work_item = _load_required(store, WorkItem, run.work_item_id, "work item")
    decision = _optional_linked_object(store, Decision, args.decision_id)
    approval = _optional_linked_object(store, Approval, args.approval_id)
    return transition_run_to_review(
        store,
        work_item=work_item,
        run=run,
        actor=args.actor,
        owners=[],
        decision=decision,
        approval=approval,
        has_owner_review_comment=args.owner_review_comment,
    )


def close_work_item(store: CanonStore, *, work_item_id: str, reason: str) -> WorkItem:
    """Close only after acceptance/monitoring with the configured owner principal."""

    work_item = _load_required(store, WorkItem, work_item_id, "work item")
    principal = _owner_principal()
    closure_reason = reason.strip() or "closed by configured owner"
    transition(
        work_item,
        WorkItemState.CLOSURE,
        principal.identity,
        reason=closure_reason,
        authenticated_principal=principal,
    )
    store.save(work_item)
    return work_item


def _run_inbox_command(store: CanonStore, args: object) -> None:
    command = args.inbox_command
    if command == "list":
        inbox_items = store.list(InboxItem)
        if args.status:
            status = InboxStatus(args.status)
            inbox_items = [item for item in inbox_items if item.status == status]
        print(render_inbox_list(inbox_items))
    elif command == "show":
        inbox_item = store.get(InboxItem, args.inbox_item_id)
        if inbox_item is None:
            raise CanonError(f"inbox item '{args.inbox_item_id}' was not found")
        print(render_inbox_detail(inbox_item))
    elif command == "capture-slack":
        result = capture_slack_from_atlas(store, limit=args.limit)
        print(
            "capture-slack: "
            f"{len(result.captured_ids)} captured, "
            f"{len(result.duplicate_ids)} duplicate, "
            f"{result.ignored} ignored"
        )
    elif command == "capture":
        inbox_item = capture_manual_inbox_item(
            store,
            raw_capture=args.raw_capture,
            source=args.source,
        )
        print(f"capture: {inbox_item.id}")
    elif command == "promote":
        work_item = promote_inbox_item(
            store,
            args.inbox_item_id,
            title=args.title,
            description=args.description,
            work_item_type=WorkItemType(args.type),
            priority=args.priority,
        )
        print(f"promote: {args.inbox_item_id} -> {work_item.id}")
    else:
        raise CanonError("an inbox command is required")


def _run_decision_command(store: CanonStore, args: object) -> None:
    if args.decision_command == "create":
        decision = create_decision(
            store,
            question=args.question,
            chosen_option=args.chosen_option,
            rationale=args.rationale,
            decided_by=args.decided_by,
            options_considered=args.option,
        )
        print(f"decision create: {decision.id}")
    elif args.decision_command == "link":
        decision = link_decision(store, decision_id=args.decision_id, work_item_id=args.work_item_id)
        print(f"decision link: {decision.id} -> {args.work_item_id}")
    else:
        raise CanonError("a decision command is required")


def _run_work_command(store: CanonStore, args: object) -> None:
    if args.work_command != "transition":
        raise CanonError("a work command is required")
    work_item = transition_work_item(store, args)
    print(f"transition: {work_item.id} -> {work_item.state.value}")


def _run_run_command(store: CanonStore, args: object) -> None:
    if args.run_command == "start":
        run = start_run(
            store,
            work_item_id=args.work_item_id,
            actor=args.actor,
            target=args.target,
            scope=args.scope,
        )
        print(f"run start: {run.id} -> {run.status.value}")
    elif args.run_command == "dispatch":
        run = dispatch_run(store, args)
        verdict = run.prove_verdict.value if run.prove_verdict is not None else "missing"
        print(f"run dispatch: {run.id} -> {run.status.value} (Prove {verdict})")
    elif args.run_command == "review":
        work_item = transition_run_review(store, args)
        print(f"run review: {args.run_id} -> {work_item.id} review")
    else:
        raise CanonError("a run command is required")


def _run_evidence_command(store: CanonStore, args: object) -> None:
    if args.evidence_command == "attach":
        evidence = attach_evidence(store, args)
        print(f"evidence attach: {evidence.id} -> run {args.run_id}")
    elif args.evidence_command == "verify":
        evidence = verify_evidence(store, evidence_id=args.evidence_id, verifier=args.verifier)
        print(f"evidence verify: {evidence.id} by {evidence.verified_by}")
    else:
        raise CanonError("an evidence command is required")


def _run_watch_command(store: CanonStore, args: object) -> None:
    """Dispatch the small owner debugging surface for persisted Watches."""

    command = args.watch_command
    if command == "list":
        print(render_watch_list(store.list(Watch)))
        return

    watch = store.get(Watch, args.watch_id)
    if watch is None:
        raise CanonError(f"watch '{args.watch_id}' was not found")
    if command == "show":
        print(_render_object(watch))
        return
    if command == "test":
        work_item = store.get(WorkItem, watch.target)
        if work_item is None:
            raise CanonError(f"watch target Work Item '{watch.target}' was not found")
        result = evaluate_watch(store, watch, work_item, force=True)
        if result.delivered:
            print(f"test: {watch.id} -> delivered")
        else:
            print(f"test: {watch.id} -> not delivered ({result.reason})")
        return
    raise CanonError("a watch command is required")


def _run_report_command(store: CanonStore, args: object) -> None:
    """Dispatch portfolio reporting without mixing it into the review queue."""
    if args.report_command != "portfolio":
        raise CanonError("a report command is required")
    report = build_portfolio_report(
        store.list(Project),
        store.list(WorkItem),
        store.list(Run),
        stuck_after=timedelta(hours=args.stuck_after_hours),
        failed_run_lookback=timedelta(days=args.failed_lookback_days),
    )
    print(render_portfolio_report(report))


def run(args: object) -> None:
    """Dispatch the parsed ``alicia canon`` command."""
    store = open_store(args.canon_db)
    try:
        if args.canon_command == "inbox":
            _run_inbox_command(store, args)
        elif args.canon_command == "work":
            _run_work_command(store, args)
        elif args.canon_command == "decision":
            _run_decision_command(store, args)
        elif args.canon_command == "run":
            _run_run_command(store, args)
        elif args.canon_command == "evidence":
            _run_evidence_command(store, args)
        elif args.canon_command == "watch":
            _run_watch_command(store, args)
        elif args.canon_command == "report":
            _run_report_command(store, args)
        elif args.canon_command == "list":
            work_items = store.list(WorkItem)
            if args.state:
                state = WorkItemState(args.state)
                work_items = [item for item in work_items if item.state == state]
            print(render_work_item_list(work_items))
        elif args.canon_command == "show":
            work_item = store.get(WorkItem, args.work_item_id)
            if work_item is None:
                raise CanonError(f"work item '{args.work_item_id}' was not found")
            print(render_work_item_detail(store, work_item))
        elif args.canon_command == "close":
            work_item = close_work_item(store, work_item_id=args.work_item_id, reason=args.reason)
            print(f"close: {work_item.id} -> {work_item.state.value} (terminal)")
        elif args.canon_command == "dogfood":
            from .surface import dogfood_pipeline

            print(json.dumps(dogfood_pipeline(store, marker=args.marker), indent=2, default=str))
        else:
            work_item = perform_owner_action(
                store,
                args.work_item_id,
                args.canon_command,
                reason=getattr(args, "reason", ""),
            )
            print(f"{args.canon_command}: {work_item.id} -> {work_item.state.value}")
    except CanonError as exc:
        raise CanonError(f"{exc}. Guidance: run 'alicia canon --help' for command usage.") from exc
    finally:
        store.close()
