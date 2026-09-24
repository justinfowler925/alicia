"""CLI handlers for workflow-control operations."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .canon.models import Evidence, WorkItem
from .canon.surface import open_canon_store
from .workflow_control import (
    attach_delivery_receipt,
    batch_feedback,
    bind_delivery_policy,
    dumps,
    evaluate_delivery_receipts,
    live_efficiency_scorecard,
    live_route,
    load_delivery_policy,
    persist_feedback_batches,
    post_work_event,
    work_status,
)


def run(args: Any) -> None:
    store = open_canon_store(args.canon_db)
    try:
        command = args.workflow_command
        if command == "route":
            result = live_route(
                args.request,
                store=store,
                repo_hint=args.repo_hint,
                cwd=args.cwd,
                create=args.create,
            )
            print(dumps(result.to_dict()))
            return
        if command == "status":
            print(dumps(work_status(store, args.work_item_id)))
            return
        if command == "policy":
            loaded = load_delivery_policy(args.repository, profile=getattr(args, "profile", None))
            payload: dict[str, Any] = {
                "path": loaded.path,
                "digest": loaded.digest,
                "profile": loaded.profile,
                "policy": loaded.policy.model_dump(mode="json"),
            }
            if args.bind:
                work = store.get(WorkItem, args.bind)
                if work is None:
                    raise ValueError(f"work item '{args.bind}' was not found")
                work.repository_path = str(Path(args.repository).expanduser().resolve())
                bind_delivery_policy(work, loaded)
                store.save(work)
                payload["bound_work_item_id"] = work.id
            print(dumps(payload))
            return
        if command == "delivery":
            work = store.get(WorkItem, args.work_item_id)
            if work is None:
                raise ValueError(f"work item '{args.work_item_id}' was not found")
            evidence = [item for item in store.list(Evidence) if item.id in work.evidence_refs]
            proof = evaluate_delivery_receipts(work, evidence)
            print(dumps(proof.to_dict()))
            if not proof.ok:
                raise SystemExit(2)
            return
        if command == "receipt":
            receipt = attach_delivery_receipt(
                store,
                work_item_id=args.work_item_id,
                requirement_id=args.requirement_id,
                evidence_type=args.type,
                content_ref=args.content_ref,
                result=args.result,
                target=args.target,
                artifact_digest=args.artifact_digest,
                captured_by=getattr(args, "captured_by", "owner-local-verifier"),
                captured_by_kind=getattr(args, "captured_by_kind", "human"),
            )
            print(dumps(receipt.model_dump(mode="json")))
            return
        if command == "event":
            receipt, created = post_work_event(
                store,
                work_item_id=args.work_item_id,
                event_id=args.event_id,
                event_type=args.event_type,
                surface=args.surface,
                source_locator=args.source_locator,
                result=args.result,
                artifact_digest=args.artifact_digest,
            )
            print(dumps({"created": created, "evidence": receipt.model_dump(mode="json")}))
            return
        if command == "feedback":
            raw = json.loads(Path(args.input).expanduser().read_text(encoding="utf-8"))
            reports = raw.get("reports") if isinstance(raw, dict) else raw
            batches = batch_feedback(reports or [])
            work_items = persist_feedback_batches(store, reports or []) if args.create else []
            print(
                dumps(
                    {
                        "reports": len(reports or []),
                        "batches": [item.to_dict() for item in batches],
                        "dispositioned": sum(len(item.dispositions) for item in batches),
                        "work_item_ids": [item.id for item in work_items],
                    }
                )
            )
            return
        if command == "scorecard":
            print(dumps(live_efficiency_scorecard(store, days=args.days)))
            return
        raise ValueError("workflow command is required")
    finally:
        store.close()
