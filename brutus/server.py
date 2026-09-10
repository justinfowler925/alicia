"""Brutus laptop HTTP face — ops board + chat + watchdog."""

from __future__ import annotations

import asyncio
import importlib.util
import json
import logging
import os
import re
import secrets
import shutil
import tempfile
from contextlib import asynccontextmanager
from datetime import timedelta
from pathlib import Path
from typing import Any

import httpx
import uvicorn
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response, StreamingResponse
from pydantic import BaseModel, Field

from . import avatars as avatar_ctl
from .agent_sessions import (
    active_counts,
    filter_cockpit,
    merge_overlays,
    scan_agent_sessions,
    summarize_transcript,
)
from .board_watch import BoardWatcher
from .canon import CanonStore
from .canon.http import router as canon_router
from .canon.surface import slack_capture_tick
from .chat_resolve import resolve_chat_reply
from .client import AtlasClient, AtlasDisabled
from .config import BrutusCfg, load_config
from .conversation import ConversationManager
from .cursor_runner import run_cursor_tick
from .focus import clip as clip_text
from .github_evidence import GitHubEvidenceReceiver
from .linear_surface import linear_work_surface
from .local_llm import list_models
from .memory import MemoryStore
from .model_gateway import judge_with_profile
from . import process_control
from .nucleus import (
    apply_project_overlay,
    build_nucleus_snapshot,
    invalidate_nucleus_cache,
    nucleus_project_detail,
    slim_nucleus_snapshot,
    warm_nucleus_snapshot,
)
from .paths import canon_db_path
from .projects import scan_projects
from .refine import refine_todo
from .security import (
    OWNER_SESSION_COOKIE,
    authenticate_owner_token,
    issue_owner_session,
    verify_github_signature,
)
from .session import SessionStore
from .session_bus import SessionEventBus, sse
from .sites import check_sites
from .supervisor_runtime import SupervisorRuntime
from .todos import STAGES, TodoStore
from .voice import HAS_WHISPER, save_wav
from .voice import speak as voice_speak
from .voice import transcribe as voice_transcribe
from .voice_identity import EnrollmentError, VoiceIdentity
from .watchdog import Watchdog
from .workflow_http import router as workflow_router
from .studio_runs import router as studio_runs_router
from .zoom_api import ZoomAPIError, ZoomClient, assets_from_summary, default_window
from .zoom_ingest import DEFAULT_SOURCE_MODE, ZoomIngestStore, ingest_assets
from .zoom_my_notes import sync_my_note
from .zoom_user_oauth import ZoomMyNotesClient

log = logging.getLogger("brutus.server")


def _deployment_manifest() -> dict[str, Any]:
    configured = os.environ.get("BRUTUS_DEPLOY_MANIFEST", "").strip()
    path = (
        Path(configured).expanduser()
        if configured
        else Path(__file__).resolve().parent.parent / ".brutus-deploy.json"
    )
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError, OSError):
        return {"sha": None, "deployed_at": None, "config_sha256": None}
    return {
        "sha": data.get("sha"),
        "deployed_at": data.get("deployed_at"),
        "config_sha256": data.get("config_sha256"),
    }


class ChatRequest(BaseModel):
    message: str
    mode: str = "manager"
    ticket_id: str | None = None
    conversation_id: str | None = None
    # Prior turns from the UI transcript ({role, content}); server trims/sanitizes.
    history: list[dict[str, str]] = []
    # When true, the registry is built without any mutating tool. This endpoint
    # never passed it, so it silently defaulted to False and the browser's
    # voice path ran the full mutating registry. Voice callers send True.
    read_only: bool = False


class SessionOpenRequest(BaseModel):
    title: str = ""
    kind: str = "work"


class SessionSayRequest(BaseModel):
    message: str
    # Which transport this turn arrived on. It changes nothing about how the
    # turn is handled — it is recorded so the screen can show how you said it.
    channel: str = "text"
    # Conversation turns never mutate. Writes go through an approved artifact.
    read_only: bool = True
    # Voice needs the completed reply so it can synthesize the answer. The web
    # client leaves this false and receives the answer from the event stream.
    wait: bool = False
    # Speaker-verification result for a voice turn. None means the transport
    # cannot verify a speaker at all — the typed textarea, where the owner
    # token is the credential. False means the voice transport listened and
    # could not place the speaker: answer them, but do not let them settle a
    # pending write by saying "yes".
    owner_verified: bool | None = None


class ApproveRequest(BaseModel):
    reject: bool = False


class DispatchRequest(BaseModel):
    dry_run: bool = True
    ingest_linear: bool = False


class AvatarApply(BaseModel):
    face: str | None = None
    tier: str | None = None
    transport: str | None = None
    replace_id: str | None = None
    redeploy: bool = True


class AvatarStage(BaseModel):
    draft: str
    persona: str
    look: str = "professional"
    # When set, stage then enroll+redeploy in one shot (delete-to-swap).
    enroll: bool = False
    replace_id: str | None = None
    tier: str | None = None
    transport: str | None = None
    redeploy: bool = True


class AvatarCursorPass(BaseModel):
    prompt: str
    name: str = "face"
    prior: list[dict] | None = None
    force_mflux: bool = False


class AvatarConfig(BaseModel):
    name: str
    face: str = ""
    tier: str = ""
    transport: str = ""


class RequeueRequest(BaseModel):
    thread_ids: list[str] = Field(default_factory=list)
    next_action: str = "triage"
    note: str = "requeued_from_brutus"


class OwnerSessionRequest(BaseModel):
    token: str = Field(min_length=1)


class FrontierApplyBody(BaseModel):
    path: str | None = None
    thread_id: str | None = None
    next_action: str = "investigate"
    notes: str = ""
    paths: list[str] = Field(default_factory=list)
    thread_ids: list[str] = Field(default_factory=list)


class CursorApplyBody(BaseModel):
    path: str | None = None
    thread_id: str | None = None
    next_action: str = "dispatch_atlas5"
    notes: str = ""
    evidence: str = ""
    mark_done: bool = False


class AnswerInputBody(BaseModel):
    ticket_id: str
    body: str
    scope: str = "next_turn"
    replace_pending: bool = True


class WorkingNoteBody(BaseModel):
    topic: str
    body: str
    ticket_ids: list[str] = []


class LessonBody(BaseModel):
    title: str = ""
    body: str = ""
    tags: str = ""


class SteerRetriageBody(BaseModel):
    ticket_ids: list[str] = Field(default_factory=list)


class ResetAttemptsBody(BaseModel):
    ticket_id: str
    action: str = "investigate"
    reason: str = "brutus operator uncap"
    resume: bool = True
    # Explicit confirm — UI must set true after a confirm dialog. Prevents
    # accidental uncapping from a stray click or chat tool.
    confirm: bool = False


class SpeakBody(BaseModel):
    text: str


# The nudge that unsticks a scoping-failure park. Proven live 2026-07-28: all
# four tickets parked on pre-keychain-fix "no grounding" questions resumed.
# How often the board is re-read. Only the DIFF reaches anyone, so this is a
# poll that never reads like one — a quiet board emits literally nothing.
BOARD_POLL_SECONDS = 20.0
SLACK_CAPTURE_SECONDS = 300.0

# Whose meetings the Zoom lane is about. The summaries API is account-wide, so
# without this the pad would fill with the whole company's action items.
ZOOM_DEFAULT_EMAIL = "justin.fowler@clearspeed.com"

# Gap between refinements while a backlog exists, and while there is nothing to
# do. The short one exists only to keep the router responsive to a real question
# arriving mid-sweep; the long one keeps an idle laptop idle.
REFINE_BUSY_SECONDS = 1.0
REFINE_IDLE_SECONDS = 30.0


def _board_payload(client: AtlasClient, cfg: BrutusCfg) -> dict[str, Any]:
    """The same board the /api/board endpoint returns, without the HTTP layer.

    Shared so the poller and the endpoint can never disagree about what the
    board IS — a second implementation here would drift and the screen would
    show one thing while the doorbell reacted to another.
    """
    _ = client
    board = linear_work_surface(timeout_s=min(cfg.timeout_s, 15.0))
    board["atlas_ignored"] = True
    board["linear_ok"] = bool(board.get("ok", True))
    return board


RETRIAGE_STEERING = (
    "This scoping question predates the schema-grounding fix and is obsolete. "
    "Salesforce describe/grounding now works on the box — re-run the investigation and "
    "look up objects, fields, and flows yourself via the schema tools. Do not park again "
    "asking Justin for schema facts; only pause for a genuine business decision."
)


def _supervisor_signature(payload: dict[str, Any]) -> str:
    """Publish changed visual progress without treating a clock tick as work."""
    return json.dumps(
        {key: payload.get(key) for key in ("sessions", "counts", "assessment")},
        sort_keys=True, default=str,
    )


def create_app(cfg: BrutusCfg | None = None, *, start_watchdog: bool = True) -> FastAPI:
    cfg = cfg or load_config()
    client = AtlasClient(cfg)
    watchdog = Watchdog(cfg, client)
    voice_identity = VoiceIdentity()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if start_watchdog and cfg.watchdog_enabled:
            app.state.watchdog.start()
        # Warm the project graph before anyone asks for it. A cold build walks
        # every checkout, scans agent sessions and pages Linear — 24 seconds —
        # and a restart used to hand that bill straight to the first page load.
        if start_watchdog:
            warm_nucleus_snapshot(app.state.client, app.state.memory)
        # Poll the board and diff it into transitions. Atlas has no event feed,
        # so this is a poller — but only the DIFF ever reaches anyone, which is
        # what keeps a poller from reading like a poller.
        poller = asyncio.create_task(_poll_board(app)) if start_watchdog else None
        # One sweeper drafts titles for every capture path there is — chat,
        # the API, the Zoom feeder, agent promotion. Hooking each entry point
        # instead would mean a new capture route silently arriving unrefined.
        refiner = asyncio.create_task(_refine_backlog_loop(app)) if start_watchdog else None
        supervisor_task = (
            asyncio.create_task(_supervisor_loop(app)) if start_watchdog else None
        )
        slack_capturer = (
            asyncio.create_task(_slack_capture_loop())
            if start_watchdog and cfg.atlas_enabled
            else None
        )
        ear: Any | None = None
        if (
            start_watchdog
            and cfg.voice
            and cfg.voice.enabled
            and cfg.voice.ear_enabled
        ):
            # pynput inspects macOS global input permissions while importing.
            # Import it only for the explicitly enabled system-wide hotkey;
            # browser and LiveKit voice must never touch Accessibility/TCC.
            from .ear import Ear

            ear_sid = app.state.sessions.open_session(
                title="Ear", kind="voice", session_id="ear"
            )
            app.state.ear_session_id = ear_sid

            def _on_ear_pcm(pcm: bytes) -> None:
                with tempfile.TemporaryDirectory() as tmp:
                    wav_path = Path(tmp) / "ear.wav"
                    save_wav(pcm, wav_path)
                    text = voice_transcribe(
                        wav_path,
                        cfg.voice.whisper_model,
                        cfg.voice.whisper_device,
                        cfg.voice.whisper_compute_type,
                    )
                if not (text or "").strip():
                    return
                # wait=True: the Ear speaks the return value, so the brain
                # runs inline on this (already background) thread.
                result = app.state.conversation.handle(
                    ear_sid, text.strip(), channel="voice", read_only=False, wait=True
                )
                spoken = (result.spoken or result.reply or "").strip()
                api_key = (cfg.voice.elevenlabs_api_key or "").strip()
                live = getattr(app.state, "ear", None)
                if not spoken or not api_key or live is None:
                    return
                audio = voice_speak(
                    spoken, api_key, cfg.voice.elevenlabs_voice_id or None
                )
                live.play_mpeg(audio)

            ear = Ear(on_utterance=_on_ear_pcm, hotkey=cfg.voice.ear_hotkey)
            app.state.ear = ear
            ear.start()
        try:
            yield
        finally:
            if ear is not None:
                ear.stop()
            for task in (poller, refiner, supervisor_task, slack_capturer):
                if task:
                    task.cancel()
            app.state.watchdog.stop()

    async def _slack_capture_loop() -> None:
        while True:
            try:
                await asyncio.to_thread(slack_capture_tick)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 — Atlas being down must not kill the daemon
                log.info("canon slack capture skipped: %s", exc)
            await asyncio.sleep(SLACK_CAPTURE_SECONDS)

    async def _poll_board(app: FastAPI) -> None:
        app.state.bus.bind_loop(asyncio.get_running_loop())
        while True:
            try:
                board = await asyncio.to_thread(_board_payload, app.state.client, app.state.cfg)
                app.state.board_watch.observe(board)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 — a poll failure is not fatal
                log.debug("board poll failed: %s", exc)
            await asyncio.sleep(BOARD_POLL_SECONDS)

    async def _refine_backlog_loop(app: FastAPI) -> None:
        """Draft a title and summary for anything still sitting raw.

        Runs off the request path entirely. The first call after the router goes
        idle costs ~55s to load the model (measured), so refining inside a POST
        would mean a capture that appears to hang — the one thing the pad may
        never do. One item at a time, because the router is a single process on
        this laptop and a batch fired at it turns into a batch of timeouts.
        """
        app.state.bus.bind_loop(asyncio.get_running_loop())
        while True:
            try:
                pending = await asyncio.to_thread(app.state.todos.needing_refinement, 1)
                if pending:
                    todo = pending[0]
                    refined = await asyncio.to_thread(
                        refine_todo, app.state.cfg, app.state.todos, todo.id
                    )
                    if refined:
                        _publish_idea_on(app, action="upsert", note=refined.to_dict())
                    # Straight to the next one while there is a backlog; the
                    # model is warm and waiting a full interval wastes that.
                    await asyncio.sleep(REFINE_BUSY_SECONDS)
                    continue
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 — a refiner may never take the app down
                log.debug("refine sweep failed: %s", exc)
            await asyncio.sleep(REFINE_IDLE_SECONDS)

    async def _supervisor_loop(app: FastAPI) -> None:
        """Publish changed session progress; the assessment still gates speech."""
        app.state.bus.bind_loop(asyncio.get_running_loop())
        previous = ""
        while True:
            try:
                payload = await asyncio.to_thread(app.state.supervisor.observe)
                signature = _supervisor_signature(payload)
                if signature != previous:
                    previous = signature
                    app.state.bus.publish(
                        "supervisor",
                        {"session_id": "supervisor", **payload},
                    )
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 — monitoring cannot kill Brutus
                log.debug("supervisor observation failed: %s", exc)
            await asyncio.sleep(max(15.0, float(cfg.watchdog_interval_s)))

    app = FastAPI(title="Brutus", version="0.3.0", lifespan=lifespan)
    app.state.voice_identity = voice_identity
    app.state.cfg = cfg
    app.state.client = client
    app.state.watchdog = watchdog
    # Construct the receiver in the request-serving context.  This also keeps
    # the Canon SQLite connection on the ASGI worker that handles webhooks.
    app.state.github_evidence = None
    memory = MemoryStore()
    app.state.memory = memory
    supervisor_judge = None
    if cfg.claude.enabled:
        supervisor_judge = lambda prompt: judge_with_profile(
            cfg, "supervisor", prompt, cwd=Path(__file__).resolve().parents[1]
        )
    app.state.supervisor = SupervisorRuntime(
        judge=supervisor_judge,
        stale_after_seconds=max(60.0, float(cfg.stale_inflight_minutes) * 60)
    )

    # --- conversation sessions -------------------------------------------
    # One store, one manager. The event bus is what lets the screen render
    # changes as they happen instead of polling for them.
    sessions = SessionStore()
    bus = SessionEventBus()
    conversation = ConversationManager(client, cfg, sessions, on_event=bus.publish)
    app.state.sessions = sessions
    app.state.bus = bus
    app.state.conversation = conversation
    # The board watcher publishes under the reserved id "board", so the screen
    # subscribes to it exactly like a conversation. One stream mechanism, not two.
    app.state.board_watch = BoardWatcher(on_event=bus.publish)
    app.include_router(canon_router)
    app.include_router(workflow_router)
    app.include_router(studio_runs_router)

    @app.exception_handler(AtlasDisabled)
    async def atlas_disabled_handler(_request: Request, exc: AtlasDisabled) -> Response:
        return Response(
            content=json.dumps({"ok": False, "atlas_ignored": True, "error": str(exc)}),
            status_code=409,
            media_type="application/json",
        )

    _STATIC = Path(__file__).resolve().parent / "static"

    # No-store on every surface: a cached page served Justin a stale UI twice —
    # once a button offering a sandbox alias that does not exist, once a page
    # with no Probes toggle, so the ticket he was told to test was unreachable.
    # The surface must never be a version behind the server.
    _NO_STORE = {
        "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
        "Pragma": "no-cache",
        "Expires": "0",
    }

    @app.get("/", response_class=HTMLResponse)
    async def home() -> HTMLResponse:
        """The one Brutus surface.

        `/` used to serve a second document with its own nav, its own numbers
        and its own chat dock, while the conversation lived at /session and a
        third voice UI lived at /mobile. Three surfaces answering "what needs
        me now" get neither adoption nor trust — people quote whichever one
        they opened last, and on one screen the three disagreed by four
        different project counts.
        """
        return HTMLResponse((_STATIC / "session.html").read_text(), headers=_NO_STORE)

    @app.get("/console", response_class=HTMLResponse)
    async def console() -> RedirectResponse:
        """The console is gone. Everything it answered, `/` answers.

        It was 161 KB and 2,432 lines producing ten pages: Nucleus, Work,
        Inbox, Agents, Studio, Projects, Notes, Sites, Avatar and Demos. Eight
        are panels on the one surface now, and Sites was six links given a nav
        entry and 192 lines of markup.

        Avatar and Demos are the exception and it is worth naming: their UI is
        deleted, their endpoints are not. /api/avatar, /api/avatar/{apply,
        stage,configs,cursor-pass} and /api/frontier/apply all still work, and
        avatars.py is untouched. The pipeline behind them is dormant — the
        Studio peer it talks to has been asleep long enough that the endpoint
        was the slowest thing on the laptop, and anam-avatar-chatbot,
        avatar-lab and voicemaker-studio have all been untouched for weeks.
        When that work restarts it gets a panel like everything else rather
        than a second document.
        """
        return RedirectResponse("/", status_code=308)

    @app.get("/api/healthz")
    async def healthz(request: Request) -> dict[str, Any]:
        wd: Watchdog = request.app.state.watchdog
        llm_cfg = cfg.local_llm
        if llm_cfg is None or not llm_cfg.enabled:
            llm = {
                "ok": None,
                "enabled": False,
                "retired": True,
                "generation": {"ok": None},
            }
        else:
            llm = list_models(cfg)
            # Reachability alone reported green for 14 hours through a dead router
            # (2026-08-11). Carry the watchdog's last generation probe so this
            # endpoint answers "can it reply", not "is the port open". Cached on
            # purpose: the browser polls healthz every few seconds and a real
            # completion per poll would keep the GPU busy for nothing.
            llm["generation"] = wd.snapshot().get("local_llm") or {"ok": None}
            llm["ok"] = bool(llm.get("ok")) and llm["generation"].get("ok") is not False
        cursor_cfg = cfg.cursor_runner
        voice_cfg = cfg.voice
        cursor_credential_loaded = bool(
            (os.environ.get("CURSOR_API_KEY") or os.environ.get("CURSOR_APIKEY") or "").strip()
        )
        cursor_sdk_importable = importlib.util.find_spec("cursor_sdk") is not None
        return {
            "service": "brutus",
            "mode": "standalone",
            "atlas": {"enabled": False, "ignored": True},
            "local_llm": llm,
            "brain": {
                "primary": "cursor",
                "profiles": {
                    "conversation": "cursor",
                    "supervisor": "claude",
                    "frontier": "codex",
                },
                "cursor_enabled": bool(cursor_cfg and cursor_cfg.enabled),
                # This endpoint runs inside the launchd actor, so it proves the
                # credential reached the process that will actually call Cursor.
                # Reporting only the configured flag let a caller shell fail a
                # healthy deployment—or bless a process that never loaded its key.
                "cursor_credential_loaded": cursor_credential_loaded,
                "cursor_sdk_importable": cursor_sdk_importable,
                "claude_enabled": bool(cfg.claude and cfg.claude.enabled),
                "claude_executable": bool(shutil.which("claude")),
                "codex_executable": bool(shutil.which("codex")),
            },
            "watchdog": wd.snapshot(),
            "voice": {
                "enabled": bool(voice_cfg and voice_cfg.enabled),
                "whisper": HAS_WHISPER,
                "tts": bool(voice_cfg and (voice_cfg.elevenlabs_api_key or "").strip()),
            },
            "canon": {
                "db_path": str(canon_db_path()),
            },
        }

    @app.post("/api/auth/session")
    async def owner_session(body: OwnerSessionRequest, response: Response) -> dict[str, Any]:
        if not authenticate_owner_token(body.token):
            raise HTTPException(status_code=401, detail="invalid owner token")
        cookie, csrf = issue_owner_session()
        response.set_cookie(
            OWNER_SESSION_COOKIE,
            cookie,
            httponly=True,
            samesite="strict",
            secure=False,  # loopback HTTP; never expose Brutus on a non-loopback bind
            max_age=8 * 3600,
            path="/",
        )
        return {"ok": True, "csrf": csrf, "expires_in": 8 * 3600}

    @app.get("/version")
    async def version() -> dict[str, Any]:
        return _deployment_manifest()

    @app.get("/api/status")
    async def status(request: Request) -> dict[str, Any]:
        try:
            body = await asyncio.to_thread(
                linear_work_surface, timeout_s=min(request.app.state.cfg.timeout_s, 15.0)
            )
            body.update({"mode": "standalone", "atlas_ignored": True})
            return body
        except Exception as exc:
            return {
                "mode": "standalone",
                "atlas_ignored": True,
                "source": "linear_direct",
                "error": str(exc),
                "counts": {"needs_you": 0, "working": 0, "queued": 0},
                "needs_you": [], "working": [], "queued": [], "stuck": [],
            }

    @app.get("/api/watchdog")
    async def watchdog_status(request: Request) -> dict[str, Any]:
        return request.app.state.watchdog.snapshot()

    @app.post("/api/watchdog/tick")
    async def watchdog_tick(request: Request) -> dict[str, Any]:
        return request.app.state.watchdog.tick_once()

    @app.get("/api/focus")
    async def focus(request: Request, include_probes: bool = False) -> dict[str, Any]:
        _ = include_probes
        try:
            body = await asyncio.to_thread(
                linear_work_surface, timeout_s=min(request.app.state.cfg.timeout_s, 15.0)
            )
        except Exception as exc:
            return {
                "atlas_ignored": True,
                "error": str(exc),
                "actions": [],
                "charts": {},
                "summary": {},
                "alarm": {"alarm": None, "message": f"Linear unavailable: {exc}"},
                "justin_touchable_count": 0,
            }
        return {
            **body,
            "atlas_ignored": True,
            "watchdog": request.app.state.watchdog.snapshot(),
            "max_working_set": request.app.state.cfg.max_working_set,
        }

    @app.get("/api/board")
    async def board(request: Request, include_probes: bool = False) -> dict[str, Any]:
        """Direct Linear board; Atlas is deliberately outside this path."""
        _ = include_probes
        try:
            return await asyncio.to_thread(
                _board_payload, request.app.state.client, request.app.state.cfg
            )
        except Exception as exc:
            return {
                "headline": "Linear work is unavailable.",
                "atlas_ignored": True,
                "error": str(exc),
                "needs_you": [], "working": [], "queued": [], "stuck": [],
                "stuck_total": 0, "hidden": 0,
                "alarm": {"alarm": None, "message": f"Linear unavailable: {exc}"},
                "counts": {"needs_you": 0, "working": 0, "queued": 0, "stuck": 0},
            }

    @app.get("/api/brief")
    async def brief(request: Request) -> dict[str, Any]:
        """Morning brief from the direct Linear work surface."""
        try:
            body = await asyncio.to_thread(
                linear_work_surface, timeout_s=min(request.app.state.cfg.timeout_s, 15.0)
            )
        except Exception as exc:
            return {"ok": False, "error": str(exc), "markdown": "", "text": ""}
        text = str(body.get("headline") or "")
        return {"ok": True, "markdown": text, "text": text, "raw": body}

    @app.post("/webhooks/github", status_code=202)
    async def github_webhook(request: Request) -> dict[str, Any]:
        """Receive GitHub PR/CI events and record matched Canon evidence."""

        body = await request.body()
        if not verify_github_signature(
            body, request.headers.get("X-Hub-Signature-256")
        ):
            raise HTTPException(status_code=401, detail="invalid GitHub webhook signature")
        delivery_id = request.headers.get("X-GitHub-Delivery", "").strip()
        if not delivery_id:
            raise HTTPException(status_code=400, detail="missing GitHub delivery id")
        try:
            payload = json.loads(body)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="GitHub webhook body must be JSON") from exc
        receiver = request.app.state.github_evidence
        if receiver is None:
            # GitHub webhook evidence must survive process restarts, unlike the
            # in-memory CanonStore used by focused unit tests.
            receiver = GitHubEvidenceReceiver(CanonStore(canon_db_path()))
            request.app.state.github_evidence = receiver
        result = receiver.handle(
            request.headers.get("X-GitHub-Event"),
            payload,
            delivery_id=delivery_id,
        )
        return {"status": result.status, "evidence_ids": list(result.evidence_ids)}

    todos = TodoStore()
    app.state.todos = todos
    # Same sqlite file, its own two tables — never a column on `todos`.
    zoom_store = ZoomIngestStore(todos.path)

    def _publish_idea_on(
        app_: FastAPI,
        *,
        action: str,
        note: dict[str, Any] | None = None,
        note_id: str = "",
    ) -> None:
        """The queue is global — same reserved bus id pattern as the board."""
        bus_: SessionEventBus = app_.state.bus
        bus_.bind_loop(asyncio.get_running_loop())
        bus_.publish(
            "idea",
            {
                "session_id": "ideas",
                "action": action,
                "note": note,
                "note_id": note_id or (note or {}).get("id") or "",
            },
        )

    def _publish_idea(
        request: Request,
        *,
        action: str,
        note: dict[str, Any] | None = None,
        note_id: str = "",
    ) -> None:
        _publish_idea_on(request.app, action=action, note=note, note_id=note_id)

    @app.get("/api/todos")
    async def todos_list(include_done: bool = False) -> dict[str, Any]:
        """The whole queue plus its shape.

        `stages` and `counts` ship alongside the flat list so the board can draw
        every column — including the empty ones — without inferring the pipeline
        from whichever stages happen to have rows today.
        """
        rows = todos.list(include_done=include_done)
        grouped = todos.by_stage(include_done=include_done)
        return {
            "todos": [t.to_dict() for t in rows],
            "stages": list(STAGES),
            "by_stage": {k: [t.to_dict() for t in v] for k, v in grouped.items()},
            "counts": todos.counts(),
            "unrefined": len(todos.needing_refinement(limit=500)),
        }

    @app.post("/api/todos")
    async def todos_add(body: dict, request: Request) -> dict[str, Any]:
        text = str(body.get("text") or "").strip()
        if not text:
            raise HTTPException(status_code=400, detail="text required")
        tags = str(body.get("tags") or "").strip()
        lane = str(body.get("lane") or "").strip()
        stage = str(body.get("stage") or "").strip()
        # Stored first, drafted later by the sweeper. Capture returns immediately
        # even when the model is cold.
        note = todos.add(
            text, tags=tags, lane=lane, stage=stage, source=str(body.get("source") or "typed")
        ).to_dict()
        _publish_idea(request, action="upsert", note=note)
        return note

    @app.patch("/api/todos/{todo_id}")
    async def todos_update(todo_id: str, body: dict, request: Request) -> dict[str, Any]:
        try:
            t = todos.update(
                todo_id,
                text=body.get("text"),
                status=body.get("status"),
                lane=body.get("lane"),
                stage=body.get("stage"),
                tags=body.get("tags"),
                summary=body.get("summary"),
                missing=body.get("missing"),
                blocked=body.get("blocked"),
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if not t:
            raise HTTPException(status_code=404, detail="no such todo")
        note = t.to_dict()
        _publish_idea(request, action="upsert", note=note)
        return note

    @app.post("/api/todos/{todo_id}/refine")
    async def todos_refine(todo_id: str, request: Request) -> dict[str, Any]:
        """Re-draft one item now, ahead of the sweeper."""
        if not todos.get(todo_id):
            raise HTTPException(status_code=404, detail="no such todo")
        t = await asyncio.to_thread(refine_todo, cfg, todos, todo_id)
        if not t:
            raise HTTPException(status_code=404, detail="no such todo")
        note = t.to_dict()
        _publish_idea(request, action="upsert", note=note)
        return note

    @app.delete("/api/todos/{todo_id}")
    async def todos_delete(todo_id: str, request: Request) -> dict[str, Any]:
        existing = todos.get(todo_id)
        deleted = todos.delete(todo_id)
        if deleted:
            _publish_idea(
                request,
                action="delete",
                note=existing.to_dict() if existing else None,
                note_id=todo_id,
            )
        return {"deleted": deleted}

    @app.post("/api/todos/{todo_id}/promote")
    async def todos_promote(todo_id: str, request: Request) -> dict[str, Any]:
        """Graduate a note into the real work ledger as a manual thread."""
        current = {t.id: t for t in todos.list(include_done=True)}.get(todo_id)
        if not current:
            raise HTTPException(status_code=404, detail="no such todo")
        c: AtlasClient = request.app.state.client
        # The ledger gets the verbatim capture as the goal, not just the title.
        # A drafted title is a handle for Justin; the thing a bot has to act on
        # is what he actually said.
        goal = current.raw or current.text
        if current.summary and current.summary not in goal:
            goal = f"{goal}\n\n{current.summary}"
        try:
            reg = c.register(current.text, source="justin", goal=goal)
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"could not register: {exc}") from exc
        ticket = str((reg.get("thread") or {}).get("external_id")
                     or (reg.get("thread") or {}).get("id") or "registered")
        # Promoting is starting. Anything the ledger is now tracking is Working.
        updated = todos.update(todo_id, promoted_ticket=ticket, stage="Working")
        note = (updated or current).to_dict()
        note["promoted_ticket"] = ticket
        _publish_idea(request, action="upsert", note=note)
        return {"ok": True, "ticket": ticket, "note": note}

    @app.get("/api/zoom/ingest")
    async def zoom_ingest_status(limit: int = 50) -> dict[str, Any]:
        """Which Zoom meetings have been ingested, newest first."""
        return {"meetings": zoom_store.meetings(limit=limit)}

    @app.post("/api/zoom/ingest")
    async def zoom_ingest(body: dict, request: Request) -> dict[str, Any]:
        """Ingest Zoom AI Companion notes as captures.

        Body is one `get_meeting_assets` payload, or `{"meetings": [...]}` for a
        batch. Extraction and dedupe live in `zoom_ingest`; the caller only has
        to fetch. That split is deliberate — the Zoom credential currently lives
        in a Claude connector, not in this daemon, and the logic should not have
        to move when that changes.
        """
        raw = body.get("meetings")
        payloads = raw if isinstance(raw, list) else [body]
        owners = body.get("owners") if isinstance(body.get("owners"), list) else None
        dry_run = bool(body.get("dry_run"))
        stage = str(body.get("stage") or "Captured").strip() or "Captured"
        # "notes" (default) trusts My Notes' Action Items and falls back to the
        # summary; "both" reads both and accepts the overlap. See zoom_ingest.
        mode = str(body.get("mode") or DEFAULT_SOURCE_MODE).strip() or DEFAULT_SOURCE_MODE
        skip_ingested = bool(body.get("skip_ingested"))

        results: list[dict[str, Any]] = []
        errors: list[dict[str, Any]] = []
        for payload in payloads:
            if not isinstance(payload, dict):
                errors.append({"error": "not an object"})
                continue
            uuid = str(payload.get("meeting_uuid") or "")
            if skip_ingested and uuid and zoom_store.is_ingested(uuid):
                results.append(
                    {"meeting_uuid": uuid, "extracted": 0, "created": 0, "skipped_meeting": True}
                )
                continue
            try:
                result = await asyncio.to_thread(
                    ingest_assets,
                    payload,
                    todos,
                    zoom_store,
                    owners=owners,
                    stage=stage,
                    mode=mode,
                    dry_run=dry_run,
                )
            except ValueError as exc:
                errors.append({"meeting_uuid": uuid, "error": str(exc)})
                continue
            except Exception as exc:  # one bad meeting must not sink the batch
                log.exception("zoom ingest failed for %s", uuid or "<no uuid>")
                errors.append({"meeting_uuid": uuid, "error": str(exc)})
                continue
            results.append(result)
            # Same live push as a typed capture, or the pad stays stale until reload.
            if not dry_run:
                for item in result.get("items") or []:
                    note = todos.get(str(item.get("todo_id") or ""))
                    if note:
                        _publish_idea(request, action="upsert", note=note.to_dict())

        return {
            "ok": not errors,
            "meetings_processed": len(results),
            "created": sum(r.get("created", 0) for r in results),
            "skipped_duplicate": sum(r.get("skipped_duplicate", 0) for r in results),
            "dry_run": dry_run,
            "results": results,
            "errors": errors,
        }

    @app.post("/api/zoom/poll")
    async def zoom_poll(body: dict, request: Request) -> dict[str, Any]:
        """Fetch new Zoom meeting summaries and ingest them. No Claude involved.

        The summaries list is account-wide (the app holds admin scopes), so
        meetings are filtered to the ones Justin was actually in: hosted by him,
        or with him in the participant list. That verdict costs one extra call per
        meeting and is only ever paid once, because a resolved meeting is skipped
        on every later run.

        The first run over a fresh ledger therefore walks every meeting in the
        window — ~600 in a week, of which ~30 are his — and takes minutes rather
        than seconds. His own meetings are ingested first, so the pad fills early
        and the tail is just bookkeeping; the refine sweeper then drafting titles
        for those items through the local 14B model is what makes the tail crawl.
        Nothing waits on it: this is a launchd job, and the next run is seconds.
        """
        days = int(body.get("days") or 7)
        emails = body.get("emails") if isinstance(body.get("emails"), list) else None
        emails = [str(e).lower() for e in (emails or [ZOOM_DEFAULT_EMAIL])]
        owners = body.get("owners") if isinstance(body.get("owners"), list) else None
        dry_run = bool(body.get("dry_run"))
        mode = str(body.get("mode") or DEFAULT_SOURCE_MODE)
        include_attended = body.get("include_attended", True)

        def _work() -> dict[str, Any]:
            # Per-call ceiling: a poll walks dozens of meetings, so one slow
            # request must not hold the whole job open.
            client = ZoomClient(timeout=15.0)
            frm, to = default_window(days)
            listed = client.list_summaries(frm, to)
            # One read, then set lookups: asking per meeting opened two sqlite
            # connections each against a file the refine sweeper is writing.
            resolved = zoom_store.resolved_uuids()
            considered = 0
            skipped_resolved = 0
            not_mine: list[tuple[str, str]] = []
            results: list[dict[str, Any]] = []
            errors: list[dict[str, Any]] = []
            for entry in listed:
                uuid = str(entry.get("meeting_uuid") or "")
                if not uuid or uuid in resolved:
                    skipped_resolved += 1
                    continue
                host = str(entry.get("meeting_host_email") or "").lower()
                topic = str(entry.get("meeting_topic") or "")
                mine = any(e in host for e in emails)
                if not mine and include_attended:
                    try:
                        attendees = client.participant_emails(uuid)
                        mine = any(any(e in a for e in emails) for a in attendees)
                    except ZoomAPIError as exc:
                        # Not fatal: a meeting we cannot resolve is left for the
                        # next run rather than guessed at.
                        errors.append({"meeting_uuid": uuid, "error": str(exc)})
                        continue
                if not mine:
                    # Remembered, so the participants call is paid once ever.
                    # Written as one batch below, not once per meeting.
                    not_mine.append((uuid, topic))
                    continue
                considered += 1
                try:
                    full = client.get_summary(uuid) or entry
                    assets = assets_from_summary({**entry, **full})
                    results.append(
                        ingest_assets(
                            assets, todos, zoom_store, owners=owners, mode=mode, dry_run=dry_run
                        )
                    )
                except (ZoomAPIError, ValueError) as exc:
                    errors.append({"meeting_uuid": uuid, "error": str(exc)})
            if not dry_run:
                zoom_store.mark_many_not_mine(not_mine)
            return {
                "window": {"from": frm, "to": to},
                "summaries_listed": len(listed),
                "marked_not_mine": len(not_mine),
                "already_resolved": skipped_resolved,
                "mine": considered,
                "results": results,
                "errors": errors,
            }

        try:
            out = await asyncio.to_thread(_work)
        except ZoomAPIError as exc:
            # Credentials or Zoom being unavailable is a 503, not a crash: the
            # launchd wrapper treats it as "try again next hour".
            raise HTTPException(status_code=503, detail=str(exc)) from exc

        created = sum(r.get("created", 0) for r in out["results"])
        if not dry_run:
            for res in out["results"]:
                for item in res.get("items") or []:
                    note = todos.get(str(item.get("todo_id") or ""))
                    if note:
                        _publish_idea(request, action="upsert", note=note.to_dict())
        return {
            "ok": not out["errors"],
            "created": created,
            "skipped_duplicate": sum(r.get("skipped_duplicate", 0) for r in out["results"]),
            "dry_run": dry_run,
            **out,
        }

    @app.get("/api/zoom/oauth/start")
    async def zoom_oauth_start() -> RedirectResponse:
        """Start the local PKCE grant for Justin's private My Notes."""
        return RedirectResponse(ZoomMyNotesClient(timeout=20.0).authorization_url())

    @app.get("/api/zoom/oauth/callback")
    async def zoom_oauth_callback(code: str = "", state: str = "", error: str = "") -> HTMLResponse:
        if error:
            raise HTTPException(status_code=400, detail=f"Zoom authorization refused: {error}")
        if not code or not state:
            raise HTTPException(status_code=400, detail="Zoom authorization callback is incomplete")
        try:
            identity = await asyncio.to_thread(
                ZoomMyNotesClient(timeout=20.0).accept_callback,
                code,
                state,
                expected_email=ZOOM_DEFAULT_EMAIL,
            )
        except ZoomAPIError as exc:
            log.warning("Zoom My Notes OAuth callback failed: %s", exc)
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        email = str(identity.get("email") or ZOOM_DEFAULT_EMAIL)
        return HTMLResponse(
            "<!doctype html><title>Brutus My Notes connected</title>"
            "<main style='font:16px system-ui;max-width:42rem;margin:5rem auto'>"
            "<h1>Brutus My Notes is connected</h1>"
            f"<p>Authorized as {email}. You can close this tab.</p></main>"
        )

    @app.post("/api/zoom/my-notes/poll")
    async def zoom_my_notes_poll(body: dict, request: Request) -> dict[str, Any]:
        """Ingest Justin's personal My Notes, including transcript-only notes.

        A note with neither generated content nor transcript is still being
        finalised (or captured no audio). It remains pending so the next poll
        retries it instead of recording a false success.
        """
        days = max(1, min(int(body.get("days") or 7), 30))
        owners = body.get("owners") if isinstance(body.get("owners"), list) else None
        dry_run = bool(body.get("dry_run"))

        def _work() -> dict[str, Any]:
            client = ZoomMyNotesClient(timeout=20.0)
            identity = client.current_user()
            email = str(identity.get("email") or "").strip().lower()
            if email != ZOOM_DEFAULT_EMAIL:
                raise ZoomAPIError(
                    f"My Notes token owner is {email or 'unknown'}, expected {ZOOM_DEFAULT_EMAIL}"
                )
            frm, to = default_window(days)
            listed = client.search_my_notes(frm, to)
            recent = listed
            results: list[dict[str, Any]] = []
            errors: list[dict[str, str]] = []
            skipped_unchanged = 0
            for meta in recent:
                note_id = str(meta.get("note_id") or "").strip()
                if not note_id:
                    errors.append({"note_id": "", "error": "listed note had no note_id"})
                    continue
                prior = zoom_store.my_note(note_id)
                modified = str(meta.get("modified_time") or "")
                if prior and modified and str(prior.get("modified_time") or "") == modified:
                    skipped_unchanged += 1
                    continue
                try:
                    content = client.get_my_note(note_id, include_transcript=True)
                    results.append(
                        sync_my_note(
                            cfg,
                            meta,
                            content,
                            todos,
                            zoom_store,
                            owners=owners,
                            dry_run=dry_run,
                        )
                    )
                except (ZoomAPIError, ValueError) as exc:
                    errors.append({"note_id": note_id, "error": str(exc)})
            return {
                "window": {"from": frm, "to": to},
                "owner": email,
                "notes_listed": len(listed),
                "notes_recent": len(recent),
                "skipped_unchanged": skipped_unchanged,
                "results": results,
                "errors": errors,
            }

        try:
            out = await asyncio.to_thread(_work)
        except ZoomAPIError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

        if not dry_run:
            for result in out["results"]:
                for todo_id in result.get("todo_ids") or []:
                    note = todos.get(todo_id)
                    if note:
                        _publish_idea(request, action="upsert", note=note.to_dict())
        return {
            "ok": not out["errors"],
            "created": sum(int(r.get("created") or 0) for r in out["results"]),
            "pending": sum(r.get("state") == "pending" for r in out["results"]),
            "dry_run": dry_run,
            **out,
        }

    @app.get("/api/projects")
    async def projects() -> dict[str, Any]:
        return {"projects": await asyncio.to_thread(scan_projects)}

    @app.get("/api/nucleus")
    async def nucleus(
        request: Request, force: bool = False, detail: str = "summary"
    ) -> dict[str, Any]:
        """Canonical project operating graph used by both screen and conversation brain.

        Two defaults are deliberate, and both were measured on the page that
        would not load: the screen is served stale-while-revalidating rather
        than waiting out a 38-second cold build, and it is served the summary
        projection rather than 1.04 MB of thread bodies it does not render.
        Pass `detail=full` for the whole graph, `force=true` to wait for fresh.
        """
        snapshot = await asyncio.to_thread(
            build_nucleus_snapshot,
            request.app.state.client,
            request.app.state.memory,
            force=force,
            allow_stale=not force,
        )
        return snapshot if detail == "full" else slim_nucleus_snapshot(snapshot)

    @app.get("/api/nucleus/projects/{project_id:path}")
    async def nucleus_project(project_id: str, request: Request) -> dict[str, Any]:
        """One project's threads and tickets — the detail the table omits."""
        snapshot = await asyncio.to_thread(
            build_nucleus_snapshot,
            request.app.state.client,
            request.app.state.memory,
            allow_stale=True,
        )
        project = nucleus_project_detail(snapshot, project_id)
        if project is None:
            raise HTTPException(status_code=404, detail="unknown project")
        return project

    @app.patch("/api/nucleus/projects/{project_id:path}")
    async def nucleus_project_update(project_id: str, body: dict, request: Request) -> dict[str, Any]:
        """Local organization only; GitHub and Linear remain source-owned."""
        allowed = {"pinned", "archived", "objective", "notes"}
        changes = {key: value for key, value in body.items() if key in allowed}
        if not changes:
            raise HTTPException(status_code=400, detail="at least one organization field required")
        try:
            overlay = request.app.state.memory.upsert_project_overlay(project_id, **changes)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        invalidate_nucleus_cache()
        # And fold it into what the screen reads next, so the row it just
        # changed actually looks changed before the rebuild lands.
        apply_project_overlay(project_id, changes)
        return {
            "ok": True,
            "project_id": project_id,
            "overlay": overlay,
            "source_records_changed": False,
        }

    @app.get("/api/services")
    async def services_list() -> dict[str, Any]:
        """Brutus's own launchd services: loaded, running, and restartable."""
        return {"services": await asyncio.to_thread(process_control.list_services)}

    @app.post("/api/services/{label}/{action}")
    async def services_control(label: str, action: str) -> dict[str, Any]:
        """start | stop | restart one Brutus service."""
        try:
            return await asyncio.to_thread(process_control.service_action, label, action)
        except process_control.ControlError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/agents/{agent_id:path}/cancel")
    async def agents_cancel(agent_id: str) -> dict[str, Any]:
        """Signal one running agent thread.

        The live scan is re-read here and handed to the canceller, so the pid
        being signalled is one the board still claims — a stale row plus a
        recycled pid is how you kill something else entirely.
        """
        rows = await asyncio.to_thread(_agents_merged, force=True)
        try:
            return await asyncio.to_thread(process_control.cancel_agent, agent_id, rows)
        except process_control.ControlError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail="Not permitted to signal that process") from exc

    def _agents_merged(*, force: bool = False) -> list[dict[str, Any]]:
        rows = scan_agent_sessions(force=force)
        return merge_overlays(rows, memory.list_agent_overlays())

    @app.get("/api/agents")
    async def agents_list(
        surface: str = "",
        q: str = "",
        include_hidden: bool = False,
    ) -> dict[str, Any]:
        """Codex + Cursor + Claude threads on this laptop (read-only scan + local overlay)."""
        merged = await asyncio.to_thread(_agents_merged)
        visible = filter_cockpit(
            merged,
            include_hidden=include_hidden,
            surface=surface,
            q=q,
        )
        return {
            "agents": visible,
            "counts": active_counts(merged),
        }

    @app.get("/api/supervisor")
    async def supervisor_status(request: Request, force: bool = False) -> dict[str, Any]:
        """One evidence-backed intervention across Claude, Cursor, and Codex."""
        return await asyncio.to_thread(request.app.state.supervisor.snapshot, force=force)

    @app.patch("/api/agents/{agent_id:path}")
    async def agents_update(agent_id: str, body: dict) -> dict[str, Any]:
        """Keep / park / hide overlay — does not touch transcript files."""
        try:
            overlay = memory.upsert_agent_overlay(
                agent_id,
                pinned=body.get("kept") if "kept" in body else body.get("pinned"),
                snooze_until=body.get("snooze_until"),
                archived=body.get("hidden") if "hidden" in body else body.get("archived"),
                labels=body.get("labels"),
                linked_rev=body.get("linked_rev"),
                notes=body.get("notes"),
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        invalidate_nucleus_cache()
        return overlay

    @app.post("/api/agents/{agent_id:path}/promote")
    async def agents_promote(agent_id: str, body: dict, request: Request) -> dict[str, Any]:
        """Graduate an agent thread into local Notes."""
        dest = str(body.get("to") or "notes").strip().lower()
        merged = {r["id"]: r for r in await asyncio.to_thread(_agents_merged)}
        row = merged.get(agent_id)
        if not row:
            raise HTTPException(status_code=404, detail="no such agent thread")
        title = str(body.get("title") or row.get("title") or agent_id).strip()
        if dest == "atlas":
            raise HTTPException(status_code=409, detail="Atlas is intentionally ignored")
        # Default: Notes capture pad
        note = todos.add(f"[agent] {title}"[:500], tags=str(row.get("surface") or "agent"))
        memory.upsert_agent_overlay(agent_id, notes=f"promoted_todo:{note.id}")
        invalidate_nucleus_cache()
        return {"ok": True, "to": "notes", "todo_id": note.id}

    @app.get("/api/agents/{agent_id:path}/summary")
    async def agents_summary(agent_id: str) -> dict[str, Any]:
        merged = {r["id"]: r for r in await asyncio.to_thread(_agents_merged)}
        row = merged.get(agent_id)
        if not row:
            raise HTTPException(status_code=404, detail="no such agent thread")
        return await asyncio.to_thread(summarize_transcript, row.get("path") or "", cfg=cfg)

    @app.get("/api/sites")
    async def sites() -> dict[str, Any]:
        return {"sites": await check_sites()}

    @app.get("/api/atlas5")
    async def atlas5_tab(request: Request) -> dict[str, Any]:
        """Compatibility endpoint; standalone Brutus never probes Atlas5."""
        if not cfg.atlas_enabled:
            return {"ok": False, "ignored": True, "jobs": [], "cost": {}}
        base = cfg.atlas5_url.rstrip("/")
        out: dict[str, Any] = {"ok": False, "url": "http://127.0.0.1:8766/",
                               "jobs": [], "cost": {}, "error": ""}
        try:
            async with httpx.AsyncClient(timeout=15.0) as c:
                jr = await c.get(f"{base}/api/jobs/active")
                jr.raise_for_status()
                jobs = (jr.json() or {}).get("jobs") or []
                try:
                    cr = await c.get(f"{base}/api/cost/today")
                    out["cost"] = cr.json() if cr.status_code == 200 else {}
                except Exception:  # noqa: BLE001 — cost is decoration, jobs are the point
                    out["cost"] = {}
            def _clean(j: dict) -> dict[str, Any]:
                note = str(j.get("notes") or "")
                if note.lower().startswith("awaiting_input:"):
                    note = note.split(":", 1)[1].strip()
                return {
                    "ticket": j.get("ticket_id"),
                    "doing": j.get("action"),
                    "state": j.get("run_state"),
                    "waiting_on_you": str(j.get("approval_state") or "") == "awaiting_input",
                    "note": clip_text(note, 180),
                }
            out["jobs"] = [_clean(j) for j in jobs]
            out["ok"] = True
        except Exception as exc:
            out["error"] = str(exc)
        return out

    @app.post("/api/answer_input")
    async def answer_input(req: AnswerInputBody, request: Request) -> dict[str, Any]:
        if not req.ticket_id or not req.body.strip():
            raise HTTPException(status_code=400, detail="ticket_id and body required")
        try:
            return request.app.state.client.answer_steering(
                req.ticket_id,
                req.body.strip(),
                scope=req.scope,
                replace_pending=req.replace_pending,
            )
        except httpx.HTTPError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    @app.post("/api/steer_retriage")
    async def steer_retriage(req: SteerRetriageBody, request: Request) -> dict[str, Any]:
        """Restart Atlas5 rows parked on obsolete scoping questions (steering, not requeue)."""
        tickets = [t.strip() for t in req.ticket_ids if (t or "").strip()]
        if not tickets:
            raise HTTPException(status_code=400, detail="ticket_ids required")
        steered: list[str] = []
        failed: list[dict[str, str]] = []
        for tid in tickets:
            try:
                request.app.state.client.answer_steering(tid, RETRIAGE_STEERING)
                steered.append(tid)
            except Exception as exc:
                failed.append({"ticket_id": tid, "error": str(exc)})
        return {"ok": not failed, "steered": steered, "failed": failed, "count": len(steered)}

    @app.get("/api/capped_attempts")
    async def capped_attempts(request: Request, min_attempts: int = 5) -> dict[str, Any]:
        """List tickets at/over the Atlas5 retry cap — operator uncap surface."""
        try:
            rows = request.app.state.client.list_capped_attempts(min_attempts=min_attempts)
        except httpx.HTTPError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        return {"ok": True, "min_attempts": min_attempts, "count": len(rows), "rows": rows}

    @app.post("/api/capped_attempts/reset")
    async def capped_attempts_reset(
        req: ResetAttemptsBody, request: Request
    ) -> dict[str, Any]:
        """Reset attempts for one capped ticket. Requires confirm=true."""
        if not req.confirm:
            raise HTTPException(
                status_code=400,
                detail="confirm=true required — uncapping is intentional",
            )
        tid = (req.ticket_id or "").strip().upper()
        if not tid:
            raise HTTPException(status_code=400, detail="ticket_id required")
        try:
            return request.app.state.client.reset_attempts(
                tid,
                action=req.action,
                reason=req.reason,
                resume=req.resume,
            )
        except httpx.HTTPError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    @app.post("/api/requeue_stale")
    async def requeue_stale(req: RequeueRequest, request: Request) -> dict[str, Any]:
        if not req.thread_ids:
            raise HTTPException(status_code=400, detail="thread_ids required")
        try:
            return request.app.state.client.requeue_threads(
                req.thread_ids, next_action=req.next_action, note=req.note
            )
        except httpx.HTTPError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    @app.post("/api/frontier/apply")
    async def frontier_apply(req: FrontierApplyBody, request: Request) -> dict[str, Any]:
        c: AtlasClient = request.app.state.client
        results = []
        paths = list(req.paths or [])
        if req.path:
            paths = [req.path] + paths
        thread_ids = list(req.thread_ids or [])
        if req.thread_id:
            thread_ids = [req.thread_id] + thread_ids
        try:
            if paths:
                for p in paths:
                    results.append(
                        c.frontier_apply(
                            path=p,
                            next_action=req.next_action,
                            notes=req.notes or "batch from Brutus focus",
                        )
                    )
            elif thread_ids:
                for tid in thread_ids:
                    results.append(
                        c.frontier_apply(
                            thread_id=tid,
                            next_action=req.next_action,
                            notes=req.notes or "batch from Brutus focus",
                        )
                    )
            else:
                raise HTTPException(status_code=400, detail="path or thread_id required")
        except httpx.HTTPError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        return {"ok": True, "applied": len(results), "results": results[:20]}

    @app.post("/api/cursor/apply")
    async def cursor_apply(req: CursorApplyBody, request: Request) -> dict[str, Any]:
        try:
            return request.app.state.client.cursor_apply(
                path=req.path,
                thread_id=req.thread_id,
                next_action=req.next_action,
                notes=req.notes,
                evidence=req.evidence,
                mark_done=req.mark_done,
            )
        except httpx.HTTPError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    @app.post("/api/cursor/run")
    async def cursor_run(request: Request) -> dict[str, Any]:
        """Drain the cursor queue once, on demand.

        The watchdog used to call this every 60s. It no longer schedules
        anything (see brutus/watchdog.py), and the cursor runner was NOT moved
        to the Studio in that change — its allowlist names laptop checkouts and
        a Studio home for it needs a dedicated worktree, not a shared one.
        Keeping one explicit entry point is the difference between "parked" and
        "silently deleted".
        """
        if not request.app.state.cfg.atlas_enabled:
            return {"ok": True, "skipped": True, "reason": "Atlas queue is ignored"}
        return await asyncio.to_thread(
            run_cursor_tick, request.app.state.cfg, request.app.state.client
        )

    # --- the conversation screen -----------------------------------------

    # /session and /mobile were the same screen twice over: /mobile's endpoint
    # set was a strict subset of /session's, and neither could reach the
    # console. /session stays as an alias because it is bookmarked; /mobile is
    # gone, and its 2,109 lines of forked JS and CSS with it. The one surface
    # is responsive, which is what /mobile was for.
    @app.get("/session", response_class=HTMLResponse)
    async def session_page() -> HTMLResponse:
        return HTMLResponse((_STATIC / "session.html").read_text(), headers=_NO_STORE)

    @app.get("/mobile", response_class=HTMLResponse)
    async def mobile_page() -> RedirectResponse:
        return RedirectResponse("/", status_code=308)

    @app.get("/static/{name}")
    async def static_file(name: str) -> Response:
        # Explicit allowlist rather than StaticFiles — small assets, and a name
        # that never reaches the filesystem unchecked.
        types = {
            "session.css": "text/css",
            "overview.css": "text/css",
            "session.js": "application/javascript",
            "operations.js": "application/javascript",
            "shine-tokens.css": "text/css",
        }
        if name not in types:
            raise HTTPException(status_code=404, detail="not found")
        return Response(
            (_STATIC / name).read_text(),
            media_type=types[name],
            headers={"Cache-Control": "no-store"},
        )

    # --- conversation sessions -------------------------------------------

    @app.post("/api/session/open")
    async def session_open(req: SessionOpenRequest, request: Request) -> dict[str, Any]:
        store: SessionStore = request.app.state.sessions
        sid = store.open_session(title=req.title, kind=req.kind)
        return {"ok": True, "session_id": sid, "session": store.get_session(sid)}

    @app.get("/api/session/list")
    async def session_list(request: Request, open_only: bool = False) -> dict[str, Any]:
        store: SessionStore = request.app.state.sessions
        return {"sessions": store.list_sessions(open_only=open_only)}

    @app.get("/api/session/{session_id}")
    async def session_snapshot(session_id: str, request: Request) -> dict[str, Any]:
        snap = request.app.state.sessions.snapshot(session_id)
        if not snap:
            raise HTTPException(status_code=404, detail="unknown session")
        return snap

    @app.post("/api/session/{session_id}/say")
    async def session_say(
        session_id: str, req: SessionSayRequest, request: Request
    ) -> dict[str, Any]:
        """One user turn. Voice and typing arrive here identically."""
        store: SessionStore = request.app.state.sessions
        if not store.get_session(session_id):
            raise HTTPException(status_code=404, detail="unknown session")
        mgr: ConversationManager = request.app.state.conversation
        # Keep MLX inference off the event loop so the board and the event
        # stream stay responsive while a turn is being answered.
        result = await asyncio.to_thread(
            mgr.handle,
            session_id,
            req.message,
            channel=req.channel,
            read_only=req.read_only,
            wait=req.wait,
            owner_verified=req.owner_verified,
        )
        return result.as_dict()

    @app.post("/api/session/{session_id}/voice-token")
    async def session_voice_token(session_id: str, request: Request) -> dict[str, Any]:
        """Mint a short-lived, room-scoped token for the local LiveKit voice transport."""
        store: SessionStore = request.app.state.sessions
        if not store.get_session(session_id):
            raise HTTPException(status_code=404, detail="unknown session")
        identity: VoiceIdentity = request.app.state.voice_identity
        if not identity.status()["enrolled"]:
            return {
                "enabled": False,
                "owner_enrollment_required": True,
                "reason": "Enroll your voice before starting owner-only live voice.",
            }
        voice_cfg = cfg.voice
        if not (
            voice_cfg
            and voice_cfg.enabled
            and voice_cfg.livekit_url
            and voice_cfg.livekit_api_key
            and voice_cfg.livekit_api_secret
        ):
            return {"enabled": False}
        try:
            from livekit import api as livekit_api
        except ImportError as exc:
            raise HTTPException(status_code=503, detail="LiveKit voice dependencies are not installed") from exc

        nonce = secrets.token_hex(4)
        room = f"brutus-{session_id}-{nonce}"
        identity = f"justin-{nonce}"
        token = (
            livekit_api.AccessToken(voice_cfg.livekit_api_key, voice_cfg.livekit_api_secret)
            .with_identity(identity)
            .with_name("Justin")
            .with_ttl(timedelta(minutes=10))
            .with_grants(livekit_api.VideoGrants(room_join=True, room=room))
            .to_jwt()
        )
        return {"enabled": True, "url": voice_cfg.livekit_url, "token": token, "room": room}

    @app.get("/api/voice-enrollment")
    async def voice_enrollment_status(request: Request) -> dict[str, Any]:
        """Return only enrollment metadata; the voice embedding never leaves this laptop."""
        identity: VoiceIdentity = request.app.state.voice_identity
        return identity.status()

    @app.post("/api/voice-enrollment")
    async def voice_enroll(
        request: Request,
        samples: list[UploadFile] = File(...),
        consent: bool = Form(False),
    ) -> dict[str, Any]:
        """Create the local owner speaker profile from explicit-consent WAV samples."""
        if not consent:
            raise HTTPException(status_code=400, detail="Explicit consent is required for voice enrollment.")
        if len(samples) != 3:
            raise HTTPException(status_code=400, detail="Record exactly three samples for enrollment.")
        wav_samples: list[bytes] = []
        for sample in samples:
            data = await sample.read()
            if not data or len(data) > 8 * 1024 * 1024:
                raise HTTPException(status_code=400, detail="Each recording must be a non-empty WAV under 8 MB.")
            wav_samples.append(data)
        identity: VoiceIdentity = request.app.state.voice_identity
        try:
            return await asyncio.to_thread(identity.enroll, wav_samples)
        except EnrollmentError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:  # noqa: BLE001 - model/download failures need a human answer
            log.exception("voice enrollment failed")
            raise HTTPException(status_code=503, detail="Voice enrollment could not start. Try again shortly.") from exc

    @app.post("/api/session/{session_id}/artifact/{artifact_id}/{decision}")
    async def session_artifact(
        session_id: str, artifact_id: str, decision: str, request: Request
    ) -> dict[str, Any]:
        """Approve or reject a proposed write from the screen.

        Approving runs the STORED args — the same object the preview showed.
        There is no request body on purpose: nothing the client sends can change
        what executes, so a stale page cannot approve something it wasn't shown.
        """
        if decision not in ("approve", "reject"):
            raise HTTPException(status_code=400, detail="decision must be approve or reject")
        store: SessionStore = request.app.state.sessions
        artifact = store.get_artifact(artifact_id)
        if not artifact or artifact.get("session_id") != session_id:
            raise HTTPException(status_code=404, detail="unknown artifact")
        mgr: ConversationManager = request.app.state.conversation
        if decision == "reject":
            settled = store.settle_artifact(artifact_id, state="rejected")
            if settled is None:
                raise HTTPException(status_code=409, detail="already settled")
            request.app.state.bus.publish(
                "proposal_settled", {"session_id": session_id, "artifact": settled}
            )
            return {"ok": True, "artifact": settled}
        result = await asyncio.to_thread(mgr.execute_artifact, session_id, artifact_id)
        return result.as_dict()

    @app.post("/api/session/{session_id}/close")
    async def session_close(session_id: str, request: Request) -> dict[str, Any]:
        request.app.state.sessions.close_session(session_id)
        return {"ok": True}

    @app.get("/api/session/{session_id}/events")
    async def session_events(session_id: str, request: Request):
        """Server-sent events, so the screen renders changes as they happen."""
        bus: SessionEventBus = request.app.state.bus
        bus.bind_loop(asyncio.get_running_loop())
        queue = bus.subscribe(session_id)

        async def stream():
            # NOTE: no `request.is_disconnected()` poll. It never returns on
            # some ASGI transports, which hangs the generator on the very first
            # loop and makes the endpoint untestable. A disconnect closes the
            # generator anyway — the `finally` below is the real cleanup, and
            # it runs on GeneratorExit and CancelledError alike.
            try:
                # Open the stream immediately so the client's onopen fires even
                # when nothing has happened yet.
                yield sse({"kind": "open", "session_id": session_id})
                while True:
                    try:
                        event = await asyncio.wait_for(queue.get(), timeout=15.0)
                    except TimeoutError:
                        # A comment frame, not an event — keeps proxies from
                        # reaping an idle connection without waking the client.
                        yield ": keepalive\n\n"
                        continue
                    yield sse(event)
            finally:
                bus.unsubscribe(session_id, queue)

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
        )

    @app.post("/api/chat")
    async def chat(req: ChatRequest, request: Request) -> dict[str, Any]:
        try:
            # Keep slow MLX/Studio inference off the async event loop so the
            # server stays responsive to board polling and other requests.
            reply, raw = await asyncio.to_thread(
                resolve_chat_reply,
                request.app.state.client,
                cfg,
                req.message,
                mode=req.mode,
                ticket_id=req.ticket_id,
                history=req.history,
                memory=memory,
                read_only=req.read_only,
            )
            # Persist the conversation so Brutus can resume across sessions.
            linked = re.findall(r"\bREV-\d+\b", req.message + " " + reply, flags=re.IGNORECASE)
            conv = memory.save_conversation(
                req.message,
                reply,
                conversation_id=req.conversation_id or "",
                title=(req.message or "Brutus chat")[:80],
                summary="",
                linked_tickets=[c.upper() for c in linked],
            )
            return {
                "reply": reply,
                "atlas5_busy": bool(raw.get("atlas5_busy")),
                "atlas6_unreachable": bool(raw.get("atlas6_unreachable")),
                "path": raw.get("path"),
                "skill": raw.get("skill"),
                "source": "brutus",
                "conversation_id": conv.id,
            }
        except Exception as exc:
            log.exception("brutus chat failed")
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @app.get("/api/voice")
    async def voice_status(request: Request) -> dict[str, Any]:
        voice_cfg = cfg.voice
        ear = getattr(request.app.state, "ear", None)
        return {
            "enabled": bool(voice_cfg and voice_cfg.enabled),
            "whisper": HAS_WHISPER,
            "tts": bool(voice_cfg and (voice_cfg.elevenlabs_api_key or "").strip()),
            "ear": ear.status() if ear is not None else {"enabled": False, "listening": False},
        }

    @app.post("/api/transcribe")
    async def transcribe(file: UploadFile = File(...)) -> dict[str, Any]:
        """Transcribe uploaded browser audio (webm/wav/mp3) with local Whisper."""
        if not cfg.voice or not cfg.voice.enabled:
            raise HTTPException(status_code=503, detail="voice is not enabled")
        if not HAS_WHISPER:
            raise HTTPException(
                status_code=503,
                detail="faster-whisper is not installed. Run: pip install -e '.[voice]'",
            )
        raw = await file.read()
        if not raw:
            raise HTTPException(status_code=400, detail="empty audio")
        suffix = Path(file.filename or "clip.webm").suffix or ".webm"
        tmp_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
                tmp.write(raw)
                tmp_path = Path(tmp.name)
            text = await asyncio.to_thread(
                voice_transcribe,
                tmp_path,
                cfg.voice.whisper_model,
                cfg.voice.whisper_device,
                cfg.voice.whisper_compute_type,
            )
        except HTTPException:
            raise
        except Exception as exc:
            log.exception("brutus transcribe failed")
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        finally:
            if tmp_path is not None:
                tmp_path.unlink(missing_ok=True)
        return {"ok": True, "text": text or ""}

    @app.post("/api/speak")
    async def speak(req: SpeakBody) -> Response:
        """Return ElevenLabs TTS audio/mpeg for the given text."""
        if not cfg.voice or not cfg.voice.enabled:
            raise HTTPException(status_code=503, detail="voice is not enabled")
        api_key = (cfg.voice.elevenlabs_api_key or "").strip()
        if not api_key:
            raise HTTPException(
                status_code=503,
                detail="elevenlabs_api_key is not set (ELEVENLABS_API_KEY env or config)",
            )
        text = (req.text or "").strip()
        if not text:
            raise HTTPException(status_code=400, detail="text is required")
        # Unsolicited board doorbells used to read UUIDs as "fee needs you"
        # even with the page closed (background EventSource). Never TTS that.
        if re.search(
            r"(?i)(?:[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
            r"|[0-9a-f]{12,}).{0,80}needs you",
            text,
        ):
            raise HTTPException(status_code=400, detail="doorbell is visual only")
        # Cap length so a huge board dump doesn't blow the TTS bill.
        if len(text) > 2500:
            text = text[:2500] + "…"
        try:
            audio = await asyncio.to_thread(
                voice_speak,
                text,
                api_key,
                cfg.voice.elevenlabs_voice_id or None,
            )
        except Exception as exc:
            log.exception("brutus speak failed")
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        return Response(content=audio, media_type="audio/mpeg")

    @app.get("/api/conversations")
    async def conversations_list() -> dict[str, Any]:
        return {"conversations": [c.to_dict() for c in memory.list_conversations()]}

    @app.get("/api/conversations/{conversation_id}")
    async def conversation_get(conversation_id: str) -> dict[str, Any]:
        c = memory.get_conversation(conversation_id)
        if not c:
            raise HTTPException(status_code=404, detail="no such conversation")
        return c.to_dict()

    @app.post("/api/working_notes")
    async def working_note_add(req: WorkingNoteBody) -> dict[str, Any]:
        if not req.topic.strip():
            raise HTTPException(status_code=400, detail="topic required")
        n = memory.add_working_note(req.topic, req.body, ticket_ids=req.ticket_ids)
        return n.to_dict()

    @app.get("/api/working_notes")
    async def working_notes_list(q: str = "") -> dict[str, Any]:
        notes = (
            memory.search_working_notes(q)
            if q.strip()
            else memory.list_working_notes()
        )
        return {"notes": [n.to_dict() for n in notes]}

    @app.get("/api/lessons")
    async def lessons_list(q: str = "") -> dict[str, Any]:
        lessons = memory.search_lessons(q) if q.strip() else memory.list_lessons()
        return {"lessons": [les.to_dict() for les in lessons]}

    @app.post("/api/lessons")
    async def lessons_add(req: LessonBody) -> dict[str, Any]:
        try:
            return memory.add_lesson(req.title, req.body, tags=req.tags).to_dict()
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/approve/{thread_id}")
    async def approve(thread_id: str, req: ApproveRequest, request: Request) -> dict[str, Any]:
        try:
            decision = "reject" if req.reject else "approve"
            return request.app.state.client.approve(thread_id, decision=decision)
        except httpx.HTTPError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    @app.post("/api/dispatch")
    async def dispatch(req: DispatchRequest, request: Request) -> dict[str, Any]:
        try:
            return request.app.state.client.dispatch_tick(
                dry_run=req.dry_run, ingest_linear=req.ingest_linear
            )
        except httpx.HTTPError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    @app.post("/api/reconcile")
    async def reconcile(request: Request) -> dict[str, Any]:
        try:
            return request.app.state.client.reconcile()
        except httpx.HTTPError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    # ---- Avatar control -------------------------------------------------
    @app.get("/api/avatar")
    async def avatar_state() -> dict[str, Any]:
        """Everything the avatar page needs: masters, drafts, live avatars, env.

        The four lookups reach four different machines and used to run one after
        another, so the handler cost the sum of every timeout — 36 seconds when
        the Studio peer was asleep. They are independent, so they run together
        and the page costs the slowest one.
        """
        state: dict[str, Any] = {"tiers": avatar_ctl.TIERS, "transports": avatar_ctl.TRANSPORTS,
                                 "configs": avatar_ctl.load_configs(),
                                 "studio": avatar_ctl.studio_reachability()}

        async def _live() -> list[dict[str, Any]]:
            return (await avatar_ctl.studio_avatars()).get("avatars", [])

        results = await asyncio.gather(
            avatar_ctl.studio_faces(),
            # Sync SSH — keep it off the event loop the same way session turns do.
            asyncio.to_thread(avatar_ctl.list_mflux_drafts),
            _live(),
            avatar_ctl.vercel_env(),
            return_exceptions=True,
        )
        for key, error_key, empty, result in zip(
            ("faces", "drafts", "enrolled", "env"),
            ("faces_error", "drafts_error", "studio_error", "env_error"),
            ([], [], [], {}),
            results,
            strict=True,
        ):
            if isinstance(result, BaseException):
                # Never an empty reason. httpx connect errors stringify to ""
                # and a source that failed for "" is indistinguishable on
                # screen from a source with nothing in it — which is the exact
                # failure this endpoint was already making.
                state[key] = empty
                state[error_key] = str(result).strip() or type(result).__name__
            else:
                state[key] = result
        return state

    @app.post("/api/avatar/apply")
    async def avatar_apply(body: AvatarApply) -> dict[str, Any]:
        return await avatar_ctl.apply_config(body.face, body.tier, body.transport,
                                             body.replace_id, body.redeploy)

    @app.post("/api/avatar/stage")
    async def avatar_stage(req: AvatarStage) -> dict[str, Any]:
        """Copy an mflux draft into faces/looks so Apply can enroll it."""
        staged = await asyncio.to_thread(
            avatar_ctl.stage_mflux_draft, req.draft, req.persona, req.look
        )
        if not staged.get("ok") or not req.enroll:
            return staged
        applied = await avatar_ctl.apply_config(
            staged["face"], req.tier, req.transport, req.replace_id, req.redeploy
        )
        return {"ok": applied.get("ok"), "staged": staged, "applied": applied,
                "face": staged.get("face"), "avatar_id": applied.get("avatar_id"),
                "steps": [{"step": "stage", "ok": True, "detail": staged.get("face")}]
                          + list(applied.get("steps") or [])}

    @app.post("/api/avatar/cursor-pass")
    async def avatar_cursor_pass(req: AvatarCursorPass, request: Request) -> dict[str, Any]:
        """Pass 3 of a face job: Cursor tries a different image, kept only if better.

        Atlas/make-face.sh call this after two local mflux renders of the same brief.
        """
        result = await asyncio.to_thread(
            avatar_ctl.run_cursor_image_pass,
            cfg,
            prompt=req.prompt,
            name=req.name,
            prior=req.prior,
        )
        return result

    @app.post("/api/avatar/configs")
    async def avatar_save(body: AvatarConfig) -> dict[str, Any]:
        return {"configs": avatar_ctl.save_config(body.name, body.face, body.tier, body.transport)}

    @app.delete("/api/avatar/configs/{name}")
    async def avatar_delete(name: str) -> dict[str, Any]:
        return {"configs": avatar_ctl.delete_config(name)}
    return app


def serve(cfg: BrutusCfg | None = None) -> None:
    cfg = cfg or load_config()
    app = create_app(cfg, start_watchdog=True)
    log.info("Brutus listening on http://%s:%s", cfg.serve_host, cfg.serve_port)
    uvicorn.run(app, host=cfg.serve_host, port=cfg.serve_port, log_level="info")
