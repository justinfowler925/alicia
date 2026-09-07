"""Brutus MCP server — Cursor talks to Studio Atlas6 via Brutus (laptop client).

Run: brutus-mcp
Wire into ~/.cursor/mcp.json (see mcp.example.json).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .chat_resolve import resolve_chat_reply
from .client import AtlasClient
from .config import _expand, load_config
from .memory import MemoryStore
from .voice import HAS_PYAUDIO, HAS_WHISPER, listen as _voice_listen, speak as _voice_speak
from .paths import state_path


def _client() -> AtlasClient:
    return AtlasClient(load_config())


def _chat(message: str, *, mode: str = "manager", read_only: bool = False) -> str:
    """Shared chat path: inject memory history, persist the turn."""
    cfg = load_config()
    memory = MemoryStore()
    reply, _out = resolve_chat_reply(
        _client(),
        cfg,
        message,
        mode=mode,
        memory=memory,
        read_only=read_only,
    )
    memory.save_conversation(
        message,
        str(reply),
        title=(message or "Brutus chat")[:80],
    )
    return str(reply)


def build_mcp():
    """Create FastMCP app; imported lazily so CLI works without mcp installed."""
    from mcp.server.fastmcp import FastMCP

    mcp = FastMCP(
        "brutus",
        instructions=(
            "Brutus is Justin's MacBook client for Studio Atlas. "
            "Ledger and dispatcher live on Atlas6 (Studio), not here. "
            "Use these tools to register work, read WIP digests, dispatch ticks, "
            "reconcile handbacks, and approve Justin gates."
        ),
    )

    @mcp.tool()
    def brutus_health() -> str:
        """Ping Studio Atlas6 health."""
        return json.dumps(_client().health(), indent=2)

    @mcp.tool()
    def brutus_digest() -> str:
        """Return portfolio WIP digest markdown from Studio ledger."""
        body = _client().digest()
        return str(body.get("markdown") or json.dumps(body, indent=2))

    @mcp.tool()
    def brutus_threads() -> str:
        """List open portfolio threads on Studio."""
        return json.dumps(_client().list_threads(), indent=2)

    @mcp.tool()
    def brutus_register(
        title: str,
        external_id: str = "",
        source: str = "manual",
        goal: str = "",
    ) -> str:
        """Register a work thread on Studio. Use external_id=REV-61 for Linear tickets."""
        ext = external_id.strip() or None
        src = source
        if ext and ext.upper().startswith("REV-"):
            src = "linear"
            ext = ext.upper()
        return json.dumps(
            _client().register(title, external_id=ext, source=src, goal=goal),
            indent=2,
        )

    @mcp.tool()
    def brutus_query(message: str, mode: str = "manager") -> str:
        """Read-only chat with the Atlas6 work surface.

        Use this for status questions, gate explanations, and ticket lookup.
        This tool CANNOT approve, reject, dispatch, register, or modify threads.
        For actions that mutate state, use brutus_approve, brutus_dispatch,
        brutus_register, or brutus_chat (with explicit intent).
        """
        return _chat(message, mode=mode, read_only=True)

    @mcp.tool()
    def brutus_explain(target: str) -> str:
        """Read-only explanation of a single work thread or Justin gate.

        target is a REV-XX ticket id or a thread UUID.
        Returns title, status, blocker, next action, and what the gate needs from Justin.
        This tool never mutates state.
        """
        client = _client()
        target = (target or "").strip()
        if not target:
            return json.dumps({"ok": False, "error": "target is required"}, indent=2)

        threads = client.list_threads().get("threads") or []
        thread: dict[str, Any] | None = None
        if target.upper().startswith("REV-"):
            target_upper = target.upper()
            thread = next(
                (t for t in threads if (t.get("external_id") or "").upper() == target_upper),
                None,
            )
        else:
            thread = next(
                (t for t in threads if str(t.get("id") or "") == target),
                None,
            )

        if not thread:
            return json.dumps({"ok": False, "error": f"no open thread for {target}"}, indent=2)

        status = client.status()
        awaiting = client.list_awaiting_input()
        awaiting_match = next(
            (a for a in awaiting if (a.get("ticket_id") or "").upper() == (thread.get("external_id") or "").upper()),
            None,
        )

        question = ""
        if awaiting_match:
            question = awaiting_match.get("question") or ""

        blocker = thread.get("blocker") or ""
        if not blocker and thread.get("status") in ("blocked_justin", "blocked_frontier"):
            blocker = thread.get("status")

        explanation = {
            "ok": True,
            "thread_id": thread.get("id"),
            "external_id": thread.get("external_id"),
            "title": thread.get("title"),
            "status": thread.get("status"),
            "next_action": thread.get("next_action"),
            "blocker": blocker,
            "environment": thread.get("environment"),
            "executor": thread.get("executor"),
            "question_for_justin": question,
            "what_it_needs": question or blocker or "No explicit input needed.",
            "goal_excerpt": (thread.get("goal") or "")[:500],
            "evidence": thread.get("evidence"),
            "updated_at": thread.get("updated_at"),
        }
        return json.dumps(explanation, indent=2)

    @mcp.tool()
    def brutus_listen(
        duration: float = 5.0,
        read_only: bool = True,
    ) -> str:
        """Record microphone audio, transcribe it with local Whisper, and send it to Brutus.

        read_only=True (recommended) routes the transcription through brutus_query so it
        cannot approve, reject, or dispatch anything. Set read_only=False only when you
        intend to voice-command an action.

        Requires voice dependencies: pip install -e .[voice]
        Also requires portaudio: brew install portaudio
        """
        cfg = load_config()
        if not cfg.voice or not cfg.voice.enabled:
            return json.dumps(
                {
                    "ok": False,
                    "error": "voice is not enabled in config.yaml",
                },
                indent=2,
            )
        if not HAS_PYAUDIO:
            return json.dumps(
                {
                    "ok": False,
                    "error": "pyaudio is not installed. Run: brew install portaudio && pip install pyaudio",
                },
                indent=2,
            )
        if not HAS_WHISPER:
            return json.dumps(
                {
                    "ok": False,
                    "error": "faster-whisper is not installed. Run: pip install faster-whisper",
                },
                indent=2,
            )

        client = _client()
        try:
            result = _voice_listen(
                client,
                cfg,
                duration=duration,
                read_only=read_only,
            )
            return json.dumps(result, indent=2)
        except Exception as exc:  # noqa: BLE001 — tool failures must return gracefully to the model
            return json.dumps({"ok": False, "error": str(exc)}, indent=2)

    @mcp.tool()
    def brutus_speak(text: str) -> str:
        """Speak a Brutus response using ElevenLabs TTS. Returns the path to the generated audio file.

        Requires an ElevenLabs API key in config.yaml voice.elevenlabs_api_key.
        """
        cfg = load_config()
        if not cfg.voice or not cfg.voice.enabled:
            return json.dumps({"ok": False, "error": "voice is not enabled in config.yaml"}, indent=2)
        api_key = (cfg.voice.elevenlabs_api_key or "").strip()
        if not api_key:
            return json.dumps(
                {
                    "ok": False,
                    "error": "elevenlabs_api_key is not set in config.yaml voice section",
                },
                indent=2,
            )

        try:
            audio = _voice_speak(
                text,
                api_key,  # gitleaks:allow -- variable name, not an embedded value
                voice_id=cfg.voice.elevenlabs_voice_id or None,
            )
            out_path = state_path("brutus_speak.mp3")
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_bytes(audio)
            return json.dumps({"ok": True, "path": str(out_path)}, indent=2)
        except Exception as exc:  # noqa: BLE001 — tool failures must return gracefully to the model
            return json.dumps({"ok": False, "error": str(exc)}, indent=2)

    @mcp.tool()
    def brutus_chat(message: str, mode: str = "manager") -> str:
        """Send a command or question to the Atlas6 manager.

        This tool CAN mutate portfolio state (approve, reject, dispatch, register).
        Use only when you intend to take action or explicitly delegate a command.
        For read-only status, use brutus_status, brutus_digest, brutus_threads,
        brutus_query, or brutus_explain.
        """
        return _chat(message, mode=mode, read_only=False)

    @mcp.tool()
    def brutus_dispatch(dry_run: bool = True, ingest_linear: bool = False) -> str:
        """Ask Studio to run one portfolio dispatcher tick (default dry_run=true)."""
        return json.dumps(
            _client().dispatch_tick(dry_run=dry_run, ingest_linear=ingest_linear),
            indent=2,
        )

    @mcp.tool()
    def brutus_reconcile() -> str:
        """Reconcile in_flight threads against Atlas5 verified-handback receipts."""
        return json.dumps(_client().reconcile(), indent=2)

    @mcp.tool()
    def brutus_ingest_linear() -> str:
        """Pull open Linear RevOps issues into the Studio ledger."""
        return json.dumps(_client().ingest_linear(), indent=2)

    @mcp.tool()
    def brutus_peek_email(limit: int = 10) -> str:
        """Read-only Gmail peek (capped work signals). Does not register threads."""
        return json.dumps(_client().peek_gmail(limit=limit), indent=2)

    @mcp.tool()
    def brutus_peek_slack(limit: int = 10) -> str:
        """Read-only Slack peek (capped work signals). Does not register threads."""
        return json.dumps(_client().peek_slack(limit=limit), indent=2)

    @mcp.tool()
    def brutus_ingest_gmail() -> str:
        """Register work-like Gmail into Studio ledger (on-demand; keep tick ingest off)."""
        return json.dumps(_client().ingest_gmail(), indent=2)

    @mcp.tool()
    def brutus_ingest_slack() -> str:
        """Register work-like Slack into Studio ledger (on-demand; keep tick ingest off)."""
        return json.dumps(_client().ingest_slack(), indent=2)

    @mcp.tool()
    def brutus_approve(target: str, reject: bool = False) -> str:
        """Approve or reject a Justin gate. target = thread UUID or REV-XX."""
        client = _client()
        tid = target
        if target.upper().startswith("REV-"):
            threads = client.list_threads().get("threads") or []
            match = next(
                (t for t in threads if (t.get("external_id") or "").upper() == target.upper()),
                None,
            )
            if not match:
                return json.dumps({"ok": False, "error": f"no open thread for {target}"})
            tid = match["id"]
        decision = "reject" if reject else "approve"
        return json.dumps(client.approve(tid, decision=decision), indent=2)

    @mcp.tool()
    def brutus_morning_brief() -> str:
        """Ask Atlas6 for morning brief (gates + ready + in_flight)."""
        out = _client().brief()
        return str(out.get("data", {}).get("markdown") or out.get("summary") or json.dumps(out))

    @mcp.tool()
    def brutus_frontier() -> str:
        """List pending frontier consult jobs on Studio for Claude/Cursor to drain."""
        return json.dumps(_client().frontier(), indent=2)

    @mcp.tool()
    def brutus_frontier_apply(
        next_action: str,
        thread_id: str = "",
        path: str = "",
        notes: str = "",
    ) -> str:
        """Apply frontier result: set thread ready with next_action (build|investigate|gate_justin)."""
        return json.dumps(
            _client().frontier_apply(
                path=path or None,
                thread_id=thread_id or None,
                next_action=next_action,
                notes=notes,
            ),
            indent=2,
        )

    @mcp.tool()
    def brutus_status() -> str:
        """Full Studio status JSON (counts + queues)."""
        return json.dumps(_client().status(), indent=2)

    @mcp.tool()
    def brutus_cursor() -> str:
        """List pending Cursor agent jobs on Studio."""
        return json.dumps(_client().cursor(), indent=2)

    @mcp.tool()
    def brutus_cursor_apply(
        next_action: str = "dispatch_atlas5",
        thread_id: str = "",
        path: str = "",
        notes: str = "",
        evidence: str = "",
        mark_done: bool = False,
    ) -> str:
        """Apply Cursor job result; optionally mark_done with evidence (PR/SHA)."""
        return json.dumps(
            _client().cursor_apply(
                path=path or None,
                thread_id=thread_id or None,
                next_action=next_action,
                notes=notes,
                evidence=evidence,
                mark_done=mark_done,
            ),
            indent=2,
        )

    @mcp.tool()
    def brutus_work_route(
        request: str,
        repo_hint: str = "",
        cwd: str = "",
        create: bool = False,
    ) -> str:
        """Deduplicate and route one intent through Canon and saved Git projects.

        create=False is a read-only preview. Set create=True only when the user
        intends to open the returned Canon work item.
        """
        from .canon.surface import open_canon_store
        from .workflow_control import live_route

        store = open_canon_store()
        try:
            return json.dumps(
                live_route(
                    request,
                    store=store,
                    repo_hint=repo_hint,
                    cwd=cwd,
                    create=create,
                ).to_dict(),
                indent=2,
            )
        finally:
            store.close()

    @mcp.tool()
    def brutus_work_status(work_item_id: str) -> str:
        """Read one Canon item, its bounded events, and delivery-proof gaps."""
        from .canon.surface import open_canon_store
        from .workflow_control import work_status

        store = open_canon_store()
        try:
            return json.dumps(work_status(store, work_item_id), indent=2)
        finally:
            store.close()

    @mcp.tool()
    def brutus_work_event(
        work_item_id: str,
        event_id: str,
        event_type: str,
        surface: str,
        source_locator: str,
        result: str = "",
        artifact_digest: str = "",
    ) -> str:
        """Post an idempotent event receipt; cannot accept or close Canon work."""
        from .canon.surface import open_canon_store
        from .workflow_control import post_work_event

        store = open_canon_store()
        try:
            receipt, created = post_work_event(
                store,
                work_item_id=work_item_id,
                event_id=event_id,
                event_type=event_type,
                surface=surface,
                source_locator=source_locator,
                result=result,
                artifact_digest=artifact_digest,
            )
            return json.dumps(
                {"created": created, "evidence": receipt.model_dump(mode="json")},
                indent=2,
            )
        finally:
            store.close()

    @mcp.tool()
    def brutus_feedback_batch(reports_json: str) -> str:
        """Batch feedback by surface and shared acceptance/release/rollback path."""
        from .workflow_control import batch_feedback

        raw = json.loads(reports_json)
        reports = raw.get("reports") if isinstance(raw, dict) else raw
        batches = batch_feedback(reports or [])
        return json.dumps(
            {
                "reports": len(reports or []),
                "batches": [item.to_dict() for item in batches],
                "dispositioned": sum(len(item.dispositions) for item in batches),
            },
            indent=2,
        )

    @mcp.tool()
    def brutus_workflow_scorecard(days: int = 7) -> str:
        """Generate a denominator-bearing local workflow-efficiency scorecard."""
        from .canon.surface import open_canon_store
        from .workflow_control import live_efficiency_scorecard

        store = open_canon_store()
        try:
            return json.dumps(live_efficiency_scorecard(store, days=days), indent=2)
        finally:
            store.close()

    return mcp


def main() -> None:
    mcp = build_mcp()
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
