"""HTTP API for the Canon work surface."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .identity import CanonError
from ..security import require_owner_token
from .surface import (
    capture_inbox,
    open_canon_store,
    owner_review,
    promote,
    snapshot,
)

router = APIRouter(prefix="/api/canon", tags=["canon"])


class CaptureBody(BaseModel):
    raw_capture: str = Field(min_length=1)
    source: str = Field(min_length=1)


class PromoteBody(BaseModel):
    title: str = Field(min_length=1)
    description: str = ""


class ReviewBody(BaseModel):
    action: str = Field(min_length=1)
    reason: str = ""


def _store():
    return open_canon_store()


@router.get("")
@router.get("/")
def canon_snapshot() -> dict[str, Any]:
    store = _store()
    try:
        return snapshot(store)
    finally:
        store.close()


@router.post("/inbox", dependencies=[Depends(require_owner_token)])
def canon_capture(body: CaptureBody) -> dict[str, Any]:
    store = _store()
    try:
        item = capture_inbox(store, raw_capture=body.raw_capture, source=body.source)
        return {"ok": True, "item": item.model_dump(mode="json")}
    except CanonError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        store.close()


@router.post("/inbox/{inbox_item_id}/promote", dependencies=[Depends(require_owner_token)])
def canon_promote(inbox_item_id: str, body: PromoteBody) -> dict[str, Any]:
    store = _store()
    try:
        work = promote(store, inbox_item_id, title=body.title, description=body.description)
        return {"ok": True, "work_item": work.model_dump(mode="json")}
    except CanonError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        store.close()


@router.post("/work/{work_item_id}/review", dependencies=[Depends(require_owner_token)])
def canon_review(work_item_id: str, body: ReviewBody) -> dict[str, Any]:
    store = _store()
    try:
        work = owner_review(store, work_item_id, body.action, reason=body.reason)
        return {"ok": True, "work_item": work.model_dump(mode="json")}
    except CanonError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        store.close()
