"""HTTP surface for Alicia specialist routing."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .specialist_router import latest_receipts, org_chart, preview, route_specialist

router = APIRouter(prefix="/api/specialists", tags=["specialists"])


class PreviewBody(BaseModel):
    task: str = Field(min_length=1, max_length=100_000)
    specialist: str | None = None


class RouteBody(PreviewBody):
    dry_run: bool = True
    cwd: str = ""
    work_item: str = ""
    read_only: bool = False


@router.get("/org-chart")
def get_org_chart() -> dict[str, Any]:
    return {"ok": True, **org_chart()}


@router.post("/preview")
def post_preview(body: PreviewBody) -> dict[str, Any]:
    try:
        return preview(body.task, body.specialist)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/route")
def post_route(body: RouteBody) -> dict[str, Any]:
    try:
        return route_specialist(
            body.task,
            specialist=body.specialist,
            dry_run=body.dry_run,
            cwd=body.cwd,
            work_item=body.work_item,
            read_only=body.read_only,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/receipts")
def get_receipts(limit: int = 10) -> dict[str, Any]:
    return {"ok": True, "receipts": latest_receipts(limit)}
