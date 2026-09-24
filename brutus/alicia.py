"""Alicia presentation transport and turn-linked improvement evidence.

Anam receives only product-generated audio. Brutus retains its brain, tools,
session history and write gates. Feedback is evidence, never a prompt patch.
"""
from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from typing import Literal
from urllib.parse import urlsplit

import httpx
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

router = APIRouter()


def same_origin(request: Request) -> None:
    origin = request.headers.get("origin")
    if origin and urlsplit(origin).netloc != request.headers.get("host"):
        raise HTTPException(403, "Untrusted request origin")


class Feedback(BaseModel):
    turn_id: int = Field(gt=0)
    dimension: Literal["conversation", "organization", "presentation"]
    rating: Literal["helpful", "needs_work"]
    correction: str = Field(default="", max_length=2000)


def feedback_db(request: Request) -> sqlite3.Connection:
    conn = sqlite3.connect(str(request.app.state.sessions.path), timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("""CREATE TABLE IF NOT EXISTS alicia_feedback (
        session_id TEXT NOT NULL, turn_id INTEGER NOT NULL,
        dimension TEXT NOT NULL, rating TEXT NOT NULL, correction TEXT NOT NULL,
        captured_at TEXT NOT NULL, build_sha TEXT,
        PRIMARY KEY (session_id, turn_id, dimension))""")
    return conn


@router.post("/api/session/{session_id}/alicia-token")
async def avatar_token(session_id: str, request: Request):
    same_origin(request)
    if not request.app.state.sessions.get_session(session_id):
        raise HTTPException(404, "Unknown session")
    from . import resilience
    cfg = request.app.state.alicia_voice
    if not cfg or not cfg.enabled or resilience.voice_killed():
        raise HTTPException(503, "Voice is paused or disabled")
    if not cfg.anam_api_key or not cfg.anam_avatar_id:
        raise HTTPException(503, "Alicia video is not configured. Voice and text remain available.")
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.post(
                "https://api.anam.ai/v1/auth/session-token",
                headers={"Authorization": f"Bearer {cfg.anam_api_key}"},
                json={"personaConfig": {
                    "avatarId": cfg.anam_avatar_id, "avatarModel": "cara-4",
                    "enableAudioPassthrough": True,
                }},
            )
            response.raise_for_status()
            token = response.json().get("sessionToken")
            if not isinstance(token, str) or not token:
                raise ValueError("Missing session token")
    except httpx.HTTPStatusError as exc:
        messages = {
            401: "Anam rejected the configured API credential.",
            402: "Anam rejected the session for billing or available-credit reasons.",
            403: "Anam denied this account access to the requested avatar session.",
            429: "Anam session or rate limit reached. End another avatar call and retry.",
        }
        raise HTTPException(503, messages.get(
            exc.response.status_code, "Anam could not provision the video session. Retry shortly."
        )) from exc
    except (httpx.HTTPError, ValueError) as exc:
        raise HTTPException(502, "Alicia video could not connect. Retry or use voice only.") from exc
    from fastapi.responses import JSONResponse
    return JSONResponse({"sessionToken": token}, headers={"Cache-Control": "no-store"})


@router.post("/api/session/{session_id}/feedback")
def save_feedback(session_id: str, body: Feedback, request: Request):
    same_origin(request)
    store = request.app.state.sessions
    # Use the authoritative turn; callers cannot submit invented transcripts.
    turn = next((t for t in store.transcript(session_id) if t.id == body.turn_id), None)
    if not turn or turn.role != "brutus" or turn.meta.get("thinking"):
        raise HTTPException(404, "Choose an answered Alicia turn")
    if body.rating == "needs_work" and not body.correction.strip():
        raise HTTPException(422, "Describe what Alicia should do differently")
    cfg = request.app.state.alicia_config
    central_id = turn.meta.get("central_turn_id")
    if not cfg.alicia_brain_url or not central_id:
        raise HTTPException(409, "Choose an answered turn from the shared brain")
    try:
        response = httpx.post(
            cfg.alicia_brain_url.rstrip("/") + "/v1/feedback",
            headers={"Authorization": f"Bearer {cfg.alicia_brain_token}"},
            json={**body.model_dump(), "turn_id": central_id}, timeout=15,
        )
        response.raise_for_status()
    except httpx.HTTPError as exc:
        raise HTTPException(503, "Shared feedback was not saved. Retry when the brain service is available.") from exc
    from .server import _deployment_manifest
    conn = feedback_db(request)
    try:
        with conn:
            conn.execute("""INSERT INTO alicia_feedback VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(session_id, turn_id, dimension) DO UPDATE SET
                rating=excluded.rating, correction=excluded.correction,
                captured_at=excluded.captured_at, build_sha=excluded.build_sha""",
                (session_id, body.turn_id, body.dimension, body.rating,
                 body.correction.strip(), datetime.now(UTC).isoformat(),
                 _deployment_manifest().get("sha")))
    finally:
        conn.close()
    return {"ok": True, "turn_id": body.turn_id, "status": "captured"}


@router.get("/api/session/{session_id}/feedback")
def read_feedback(session_id: str, request: Request):
    if not request.app.state.sessions.get_session(session_id):
        raise HTTPException(404, "Unknown session")
    conn = feedback_db(request)
    try:
        rows = [dict(r) for r in conn.execute(
            "SELECT * FROM alicia_feedback WHERE session_id=? ORDER BY captured_at DESC", (session_id,))]
    finally:
        conn.close()
    return {"feedback": rows, "count": len(rows),
            "promotion": "awaiting_agreed_baseline_and_tests"}
