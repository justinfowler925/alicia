"""HTTP surface for the bounded workflow-control join layer."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .canon.identity import CanonError
from .canon.surface import open_canon_store
from .security import require_adapter_token, require_owner_token
from .workflow_control import (
    FeedbackReport,
    batch_feedback,
    find_work_by_binding,
    live_efficiency_scorecard,
    live_route,
    persist_feedback_batches,
    post_work_event,
    work_status,
)

router = APIRouter(prefix="/api/workflow", tags=["workflow"])


class RouteBody(BaseModel):
    request: str = Field(min_length=1)
    repo_hint: str = ""
    cwd: str = ""


class EventBody(BaseModel):
    work_item_id: str = Field(min_length=1)
    event_id: str = Field(min_length=1)
    event_type: str = Field(min_length=1)
    surface: str = Field(min_length=1)
    source_locator: str = Field(min_length=1)
    result: str = ""
    artifact_digest: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class FeedbackBody(BaseModel):
    reports: list[FeedbackReport] = Field(min_length=1)


def _store():
    return open_canon_store()


@router.post("/route")
def route_preview(body: RouteBody) -> dict[str, Any]:
    store = _store()
    try:
        return live_route(
            body.request,
            store=store,
            repo_hint=body.repo_hint,
            cwd=body.cwd,
        ).to_dict()
    finally:
        store.close()


@router.post("/route/create", dependencies=[Depends(require_owner_token)])
def route_create(body: RouteBody) -> dict[str, Any]:
    store = _store()
    try:
        return live_route(
            body.request,
            store=store,
            repo_hint=body.repo_hint,
            cwd=body.cwd,
            create=True,
        ).to_dict()
    finally:
        store.close()


@router.get("/status/{work_item_id}")
def status(work_item_id: str) -> dict[str, Any]:
    store = _store()
    try:
        return work_status(store, work_item_id)
    except CanonError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    finally:
        store.close()


@router.get("/bindings/{surface}/{external_id}", dependencies=[Depends(require_adapter_token)])
def binding(surface: str, external_id: str) -> dict[str, Any]:
    store = _store()
    try:
        work = find_work_by_binding(store, surface, external_id)
        return {"work_item_id": work.id, "state": work.state.value}
    except CanonError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    finally:
        store.close()


@router.post("/events", dependencies=[Depends(require_adapter_token)])
def event(body: EventBody) -> dict[str, Any]:
    store = _store()
    try:
        receipt, created = post_work_event(store, **body.model_dump())
        return {"ok": True, "created": created, "evidence": receipt.model_dump(mode="json")}
    except (CanonError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        store.close()


@router.post("/feedback/batch")
def feedback(body: FeedbackBody) -> dict[str, Any]:
    batches = batch_feedback(body.reports)
    return {
        "reports": len(body.reports),
        "batches": [item.to_dict() for item in batches],
        "dispositioned": sum(len(item.dispositions) for item in batches),
    }


@router.post("/feedback/batch/create", dependencies=[Depends(require_owner_token)])
def feedback_create(body: FeedbackBody) -> dict[str, Any]:
    store = _store()
    try:
        batches = batch_feedback(body.reports)
        work_items = persist_feedback_batches(store, body.reports)
        return {
            "reports": len(body.reports),
            "batches": [item.to_dict() for item in batches],
            "dispositioned": sum(len(item.dispositions) for item in batches),
            "work_item_ids": [item.id for item in work_items],
        }
    except (CanonError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        store.close()


@router.get("/scorecard")
def scorecard(days: int = 7) -> dict[str, Any]:
    store = _store()
    try:
        return live_efficiency_scorecard(store, days=days)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        store.close()
