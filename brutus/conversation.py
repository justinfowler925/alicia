"""One conversation, one brain, gated hands.

Every human turn goes to the brain (brain.py — Claude Sonnet 5 with the full
session history and a native tool catalog). Deterministic code keeps exactly
the jobs it is good at:

  * the write gate — drafts execute verbatim after Justin's yes, no model
    between preview and run (gate.py, unchanged);
  * the `capture:` machine protocol — agents file carry-forwards straight to
    the Ideas pad, no model, no cost;
  * the "hey rewind" codeword, which belongs to Cursor, not to a model.

The old architecture routed by regex before any model read the message, split
turns across a fast/deep lane, capped every reply at 320 characters, and sent
the deep lane to Claude with zero history. docs/CONVERSATION_REBUILD_PLAN.md
holds the autopsy; this module is the rebuild.

Nothing in this module speaks. It returns a reply plus a `spoken` rendering of
that same reply; whether audio happens is the caller's business.
"""

from __future__ import annotations

import re
import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

from .brain import brain_reply, drop_incomplete_tail
from .client import AtlasClient
from .config import BrutusCfg
from .gate import dispatch_is_live, propose, read_confirmation
from .intent_contract import compile_proposal
from .memory import MemoryStore
from .session import SessionStore
from .speechify import speechify
from .ticket_intake import compile_ticket_intake, intake_question
from .todos import TodoStore
from .tools import build_default_registry

Lane = Literal["fast", "deep"]

# "hey rewind" is a Cursor codeword (brutus-review skill). Said here it used to
# fall to a model that invented a meaning for it. Fixed reply, no model.
_REWIND_RE = re.compile(
    r"^\s*(?:hey[, ]+)?rewind(?:\s+me)?(?:\s+and\s+review)?\s*[.!?]*\s*$",
    re.IGNORECASE,
)
_REWIND_REPLY = (
    "That's for Cursor — say hey rewind there. It reads this transcript and "
    "fixes what broke. I don't review myself."
)

# The machine intake protocol. Agents POST "capture: <self-contained item>" as
# carry-forward; it must stay deterministic and free — 337 of the first 471
# user turns were this traffic, and none of it needs a model.
_MACHINE_CAPTURE_RE = re.compile(r"^capture\s*[:\-]\s*(.+)$", re.IGNORECASE | re.DOTALL)
_SILENCE_RE = re.compile(
    r"^\s*(?:(?:give|gimme)\s+me\s+a\s+(?:second|minute|moment)\s+to\s+think|"
    r"let\s+me\s+think|hold\s+on|wait|pause)(?:\s*,?\s*please)?\s*[.!?]*\s*$",
    re.IGNORECASE,
)

# How much conversation the brain sees. Sessions here are short (median under
# ten turns); forty covers every real one observed while bounding a runaway.
_HISTORY_KEEP = 40


def _flatten(text: str) -> str:
    """Strip document furniture for the panel. No length caps — capping every
    reply at 320 chars was the old architecture banning the cognition the
    brain exists for. Spoken length is speechify's job, not this one's."""
    if not text:
        return ""
    t = re.sub(r"^\s*#{1,6}\s*", "", text, flags=re.MULTILINE)      # headings
    t = re.sub(r"\*\*([^*]+)\*\*", r"\1", t)                # bold labels
    t = re.sub(r"`([^`]*)`", r"\1", t)                       # code ticks
    t = re.sub(r"[ \t]{2,}", " ", t)
    t = re.sub(r"\n{3,}", "\n\n", t).strip()
    # The pre-rebuild prompt trained a "Go?" ritual; strip any stragglers so
    # voice never hears it, whatever model text passes through here.
    return re.sub(r"(?:\s*Go\??)+$", "", t, flags=re.IGNORECASE).strip()


@dataclass
class TurnResult:
    """One turn, rendered twice: fully for the screen, briefly for the mouth."""

    session_id: str
    lane: Lane
    reply: str
    spoken: str
    turn_id: int
    tool: str | None = None
    thinking: bool = False
    error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "lane": self.lane,
            "reply": self.reply,
            "spoken": self.spoken,
            "turn_id": self.turn_id,
            "tool": self.tool,
            "thinking": self.thinking,
            "error": self.error,
        }


class ConversationManager:
    """Owns one conversation's turn pipeline. Not tied to any transport."""

    def __init__(
        self,
        client: AtlasClient,
        cfg: BrutusCfg,
        store: SessionStore,
        *,
        on_event: Callable[[str, dict[str, Any]], None] | None = None,
        memory: MemoryStore | None = None,
        todos: TodoStore | None = None,
    ) -> None:
        self.client = client
        self.cfg = cfg
        self.store = store
        self.memory = memory or MemoryStore()
        self.todos = todos or TodoStore()
        # Every state change goes through here so the screen can render it as
        # it happens rather than after a poll. Failures are swallowed — a
        # broken listener must never take down a turn.
        self._on_event = on_event or (lambda _kind, _payload: None)
        self._brain_threads: dict[str, threading.Thread] = {}
        self._brain_generation: dict[str, int] = {}
        self._outbox_by_turn: dict[int, Any] = {}

    def emit(self, kind: str, payload: dict[str, Any]) -> None:
        try:
            self._on_event(kind, payload)
        except Exception:  # noqa: BLE001, S110 — a listener must never break a turn
            pass

    # --- the turn pipeline ------------------------------------------------

    def handle(
        self,
        session_id: str,
        message: str,
        *,
        channel: str = "text",
        read_only: bool = True,
        wait: bool = False,
        owner_verified: bool | None = None,
        display_message: str | None = None,
    ) -> TurnResult:
        """Take one user turn and answer it. Voice and text land here identically.

        `read_only` is accepted for endpoint compatibility and deliberately
        unused: the brain's tool surface is fixed (reads + notepad), and every
        gated write goes through an artifact regardless of the flag.

        `owner_verified` is the speaker check, and it guards exactly one thing:
        settling a pending artifact. That is the only place a sentence spends
        anything, because the artifact holds the {tool, args} that will run and
        a spoken "yes" is what executes it. None means the transport cannot
        identify a speaker at all — typing, where the owner token is already
        the credential — and False means the voice transport listened and could
        not place the speaker. Both still get answered; only False is barred
        from approving, and it is told why rather than met with silence.

        `wait=True` runs the brain inline and returns the finished reply — the
        push-to-talk Ear needs the spoken text in hand. The web UI leaves it
        False and renders from events.
        """
        _ = read_only
        message = (message or "").strip()
        if not message:
            return TurnResult(session_id, "fast", "", "", 0, error="empty message")

        from . import resilience

        if channel == "voice" and resilience.voice_killed():
            return TurnResult(
                session_id,
                "fast",
                "Voice is paused (`~/.brutus/state/voice.off`). Type instead, or remove that file.",
                "",
                0,
                error="voice_killed",
            )

        # Durable outbox before the model runs — restart-safe user words.
        outbox = resilience.enqueue_say(session_id, message, channel)

        turn = self.store.append_turn(session_id, "user", display_message if display_message is not None else message, channel=channel)
        self.emit("turn", {"session_id": session_id, "turn": turn.as_dict()})

        # A pending proposal owns the next turn. Answering it is not a new
        # request, and must never be routed as one.
        pending = self._pending_artifact(session_id)
        if pending:
            if owner_verified is False and read_confirmation(message) == "yes":
                # A yes is the execution. Leave the artifact in draft so the
                # approval can still happen on screen, and say so out loud —
                # this used to be the point where the turn disappeared.
                result = self._land(
                    session_id,
                    "fast",
                    "I couldn't place your voice on that, so I'm not going to run it "
                    "on a spoken yes. It's still waiting on screen — approve it there.",
                    turn.id,
                    tool=pending["tool"],
                )
                resilience.mark_outbox(outbox, status="done", reply=result.reply)
                return result
            settled = self._answer_proposal(session_id, pending, message, turn.id)
            if settled is not None:
                resilience.mark_outbox(outbox, status="done", reply=settled.reply)
                return settled

        # Cursor codeword — never invent a meaning for it here.
        if _REWIND_RE.match(message):
            result = self._land(session_id, "fast", _REWIND_REPLY, turn.id)
            resilience.mark_outbox(outbox, status="done", reply=result.reply)
            return result

        # Machine intake: deterministic, free, and exactly as self-contained as
        # the agent wrote it.
        cap = _MACHINE_CAPTURE_RE.match(message)
        if cap and cap.group(1).strip():
            result = self._machine_capture(session_id, cap.group(1).strip(), turn.id)
            resilience.mark_outbox(outbox, status="done", reply=result.reply)
            return result

        # Silence is an action, not a line of dialogue. The user turn still
        # lands and supersedes stale in-flight work, but Brutus says nothing.
        if _SILENCE_RE.match(message):
            resilience.mark_outbox(outbox, status="done", reply="")
            return TurnResult(session_id, "fast", "", "", turn.id)

        # Explicit, labelled ticket contracts do not need a conversational
        # model to rediscover their shape.  Compiling them here fixes the
        # fragile Cursor text-tool boundary while preserving the exact same
        # Unfog decision and approval artifact used by every other write.
        ticket = self._ticket_intake(session_id, channel, turn.id)
        if ticket is not None:
            resilience.mark_outbox(outbox, status="done", reply=ticket.reply)
            return ticket

        resilience.mark_outbox(outbox, status="running")
        if wait:
            result = self._brain_now(session_id, message, turn.id, channel=channel)
            resilience.mark_outbox(
                outbox,
                status="failed" if result.error else "done",
                reply=result.reply,
                error=result.error or "",
            )
            return result
        # Async brain — outbox stays running until _land_brain; stash id on meta via thread.
        self._outbox_by_turn[turn.id] = outbox
        return self._start_brain(session_id, message, turn.id, channel=channel)

    # --- machine intake -----------------------------------------------------

    def _machine_capture(self, session_id: str, text: str, turn_id: int) -> TurnResult:
        result = self._registry().call("capture_note", {"text": text[:2000]})
        inner = result.get("result")
        inner_failed = isinstance(inner, dict) and inner.get("ok") is False
        if result.get("broken") or not result.get("ok") or inner_failed:
            error = result.get("error") or (
                inner.get("error") if isinstance(inner, dict) else None
            )
            return self._land(
                session_id, "fast", f"Couldn't save that — {error or 'unknown error'}.",
                turn_id, tool="capture_note",
            )
        self._emit_idea(session_id, inner if isinstance(inner, dict) else None)
        note = inner.get("note") if isinstance(inner, dict) else None
        subject = str((note or {}).get("text") or text).strip()
        reply = f"On Ideas — {subject}"
        if len(reply) > 120:
            reply = f"{reply[:117]}…"
        return self._land(session_id, "fast", reply, turn_id, tool="capture_note")

    def _ticket_intake(self, session_id: str, channel: str, turn_id: int) -> TurnResult | None:
        intake = compile_ticket_intake(
            self.store.history_for_model(session_id, keep=_HISTORY_KEEP)
        )
        if not intake.requested:
            return None
        if not intake.ready:
            return self._land(session_id, "fast", intake_question(intake), turn_id)

        args = intake.args()
        # ``title`` belongs to the eventual Linear mutation; the pure compiler
        # receives ``draft_title`` instead, which it uses for its live lookup.
        compiler_args = {key: value for key, value in args.items() if key != "title"}
        compiled = self._registry().call("compile_unfog_work", compiler_args)
        result = compiled.get("result") if compiled.get("ok") else None
        if not isinstance(result, dict) or not result.get("ok"):
            detail = (result or compiled).get("error") if isinstance(result or compiled, dict) else "unknown error"
            return self._land(
                session_id, "fast", f"I couldn't verify duplicate work for that ticket: {detail}.", turn_id,
                tool="compile_unfog_work", error="ticket_preflight_failed",
            )
        decision = result.get("decision") if isinstance(result.get("decision"), dict) else {}
        action = str(decision.get("action") or "")
        if action == "continue":
            return self._land(session_id, "fast", str(decision.get("reason") or "Matching work is already in flight."), turn_id, tool="compile_unfog_work")
        if action == "update_existing":
            return self._land(session_id, "fast", str(decision.get("reason") or "An exact open ticket already exists."), turn_id, tool="compile_unfog_work")
        if action != "draft_new_ticket":
            return self._land(session_id, "fast", str(decision.get("reason") or "That ticket needs one material decision first."), turn_id, tool="compile_unfog_work")

        proposal_args = {key: value for key, value in args.items() if key not in {"draft_title"}}
        try:
            draft = self._on_propose(session_id, channel)("create_linear_ticket", proposal_args)
        except Exception as exc:  # noqa: BLE001 -- nothing has crossed the approval boundary
            return self._land(session_id, "fast", f"I couldn't prepare that ticket proposal: {exc}", turn_id, error="ticket_proposal_failed")
        return self._land(
            session_id, "fast", f"{draft['summary']}. Say yes to do it.", turn_id,
            tool="create_linear_ticket",
        )

    def _emit_idea(self, session_id: str, result: dict[str, Any] | None) -> None:
        """Push Ideas-pad changes to the screen. Reserved bus id mirrors board."""
        if not isinstance(result, dict) or not result.get("ok"):
            return
        action = str(result.get("action") or "upsert")
        note = result.get("note") if isinstance(result.get("note"), dict) else None
        note_id = str(result.get("note_id") or (note or {}).get("id") or "")
        if action == "delete" and not note_id:
            return
        if action != "delete" and not note:
            return
        payload = {
            "session_id": "ideas",
            "action": action,
            "note": note,
            "note_id": note_id,
            "source_session": session_id,
        }
        self.emit("idea", payload)
        # Also onto the conversation so a single EventSource can react.
        self.emit("idea", {**payload, "session_id": session_id})

    # --- the write gate ---------------------------------------------------

    def _registry(self):
        # read_only=False: the registry holds everything so approved artifacts
        # can execute. The BRAIN never sees the gated tools — brain.py exposes
        # reads + free writes only, with propose_action in between.
        return build_default_registry(
            self.client, self.cfg, memory=self.memory, todos=self.todos, read_only=False
        )

    def _pending_artifact(self, session_id: str) -> dict[str, Any] | None:
        for artifact in reversed(self.store.artifacts(session_id)):
            if artifact.get("state") == "draft":
                return artifact
        return None

    def _answer_proposal(
        self, session_id: str, artifact: dict[str, Any], message: str, turn_id: int
    ) -> TurnResult | None:
        """Execute or cancel a pending artifact. None = not an answer to it."""
        answer = read_confirmation(message)
        if answer is None:
            # Not a yes and not a no. Cancel rather than guess — a wrong yes
            # costs the ledger, a wrong no costs you saying it again.
            self.store.settle_artifact(artifact["id"], state="cancelled")
            self.emit(
                "proposal_settled",
                {"session_id": session_id, "artifact": self.store.get_artifact(artifact["id"])},
            )
            return None  # and then answer whatever they actually said

        if answer == "no":
            self.store.settle_artifact(artifact["id"], state="rejected")
            self.emit(
                "proposal_settled",
                {"session_id": session_id, "artifact": self.store.get_artifact(artifact["id"])},
            )
            return self._land(session_id, "fast", "Cancelled.", turn_id, tool=artifact["tool"])

        return self.execute_artifact(session_id, artifact["id"], turn_id=turn_id)

    def execute_artifact(
        self, session_id: str, artifact_id: str, *, turn_id: int = 0
    ) -> TurnResult:
        """Run a previously-approved artifact. No model between preview and run."""
        artifact = self.store.get_artifact(artifact_id)
        if not artifact:
            return self._land(
                session_id, "fast", "That one already expired — say it again.", turn_id
            )
        artifact = self.store.claim_artifact(artifact_id)
        if not artifact:
            current = self.store.get_artifact(artifact_id) or {}
            reply = (
                "That action is already running."
                if current.get("state") == "executing"
                else "That one already settled — say it again if you need another run."
            )
            return self._land(session_id, "fast", reply, turn_id)
        # THE contract of this whole module: the object that runs is the object
        # that was shown. registry.call receives artifact["args"] verbatim.
        registry = self._registry()
        result = registry.call(artifact["tool"], artifact["args"])

        # TWO different `ok`s, and conflating them is how "Done" gets reported
        # for work that failed. registry.call's ok means THE CALL RAN. The
        # tool's own result.ok means THE WORK SUCCEEDED. A tool that catches its
        # own exception returns {"ok": True, "result": {"ok": False, ...}} — the
        # call was fine, the approve was not.
        inner = result.get("result")
        inner_failed = isinstance(inner, dict) and inner.get("ok") is False
        error = result.get("error") or (inner.get("error") if isinstance(inner, dict) else None)
        succeeded = bool(result.get("ok")) and not inner_failed

        state = "executed" if succeeded else "failed"
        settled = self.store.finish_artifact(artifact_id, state=state, result=result)
        self.emit("proposal_settled", {"session_id": session_id, "artifact": settled})
        if succeeded and artifact.get("tool") in (
            "delete_note",
            "promote_note",
            "capture_note",
            "update_note",
        ):
            self._emit_idea(
                session_id, inner if isinstance(inner, dict) else None
            )

        if result.get("broken"):
            reply = f"That didn't run — {error}"
        elif succeeded:
            reply = f"Done — {artifact['summary'].lower()}."
        else:
            reply = f"That failed — {error or 'no reason given'}."
        return self._land(session_id, "fast", reply, turn_id, tool=artifact["tool"])

    # --- the brain ----------------------------------------------------------

    def _standing_notes(self) -> str:
        """Laptop memory the brain should have in reach without asking."""
        lines: list[str] = []
        try:
            for n in self.memory.list_working_notes(limit=6):
                topic = str(getattr(n, "topic", "") or "").strip()
                body = str(getattr(n, "body", "") or "").strip()
                if len(f"{topic} {body}".strip()) < 6:
                    continue  # junk rows; see the 2026-08-19 purge note
                lines.append(f"- {topic}: {body[:200]}" if topic else f"- {body[:200]}")
        except Exception:  # noqa: BLE001 — memory must never break a turn
            return ""
        if not lines:
            return ""
        return "Standing notes (laptop memory, may be stale):\n" + "\n".join(lines)

    def _recall(self, q: str) -> dict[str, Any]:
        q = (q or "").strip()
        if not q:
            return {"ok": False, "error": "q is required"}
        try:
            notes = [n.to_dict() for n in self.memory.search_working_notes(q, limit=8)]
            lessons = [les.to_dict() for les in self.memory.search_lessons(q, limit=5)]
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc)}
        return {"ok": True, "notes": notes, "lessons": lessons}

    def _on_propose(
        self, session_id: str, channel: str, message: str = ""
    ) -> Callable[[str, dict[str, Any]], dict[str, Any]]:
        def draft(tool: str, args: dict[str, Any]) -> dict[str, Any]:
            args = dict(args or {})
            if tool == "dispatch_tick":
                # dry_run comes from a deliberate phrase Justin actually said —
                # never from filler words, and never from the model's own
                # enthusiasm. "dispatch now for real quick" once fanned out 78
                # tickets; the phrase test is the deterministic backstop.
                args["dry_run"] = not dispatch_is_live(message)
            contract = compile_proposal(tool, args)
            proposal = propose(tool, args)
            artifact = self.store.draft_artifact(
                session_id,
                kind=proposal.kind,
                tool=proposal.tool,
                args=proposal.args,
                summary=proposal.summary,
            )
            self.emit(
                "proposal",
                {
                    "session_id": session_id,
                    "artifact": artifact,
                    "intent_contract": contract.to_dict(),
                },
            )
            return {
                "artifact_id": artifact.get("id"),
                "summary": proposal.summary,
                "spoken": proposal.spoken,
                "intent_contract": contract.to_dict(),
            }

        _ = channel
        return draft

    def _run_brain(
        self, session_id: str, message: str, turn_id: int, channel: str
    ) -> tuple[str, dict[str, Any]]:
        registry = self._registry()

        def mirror(name: str, result: dict[str, Any]) -> None:
            if name in ("capture_note", "update_note"):
                inner = result.get("result")
                self._emit_idea(session_id, inner if isinstance(inner, dict) else None)

        if self.cfg.alicia_brain_url:
            from .alicia_client import shared_reply
            reply, meta = shared_reply(
                self.cfg, registry, session_id=session_id, turn_id=turn_id,
                message=message, channel=channel, standing_notes=self._standing_notes(),
                on_propose=self._on_propose(session_id, channel, message),
                on_tool_result=mirror, recall=self._recall,
            )
            return _flatten(reply), meta

        reply, meta = brain_reply(
            self.cfg,
            registry,
            history=self.store.history_for_model(session_id, keep=_HISTORY_KEEP),
            channel=channel,
            standing_notes=self._standing_notes(),
            on_propose=self._on_propose(session_id, channel, message),
            on_tool_result=mirror,
            recall=self._recall,
        )
        return _flatten(reply), meta

    def cancel_reply(self, session_id: str) -> None:
        """Suppress delivery of an interrupted in-flight answer."""
        self._brain_generation[session_id] = self._brain_generation.get(session_id, 0) + 1

    def _brain_now(
        self, session_id: str, message: str, turn_id: int, *, channel: str
    ) -> TurnResult:
        """Inline brain turn — the Ear speaks the return value, so it waits."""
        self.cancel_reply(session_id)
        generation = self._brain_generation[session_id]
        self.emit("thinking", {"session_id": session_id, "question": message, "turn_id": turn_id})
        reply, meta = self._run_brain(session_id, message, turn_id, channel)
        if generation != self._brain_generation.get(session_id):
            return TurnResult(session_id, "fast", "", "", turn_id)
        return self._land_brain(session_id, reply, meta, turn_id)

    def _start_brain(
        self, session_id: str, message: str, turn_id: int, *, channel: str
    ) -> TurnResult:
        """Answer behind the conversation; the screen shows a thinking card.

        No filler ack turn — "One sec, looking" with nothing behind it is the
        old architecture lying about progress. The thinking card carries the
        state; the reply lands as a real turn when it exists.
        """
        self.emit("thinking", {"session_id": session_id, "question": message, "turn_id": turn_id})

        # A new question SUPERSEDES the one still in flight — only the newest
        # question is still being asked, so only its answer lands.
        self._brain_generation[session_id] = self._brain_generation.get(session_id, 0) + 1
        generation = self._brain_generation[session_id]

        def run() -> None:
            reply, meta = self._run_brain(session_id, message, turn_id, channel)
            if generation != self._brain_generation.get(session_id):
                return
            self._land_brain(session_id, reply, meta, turn_id)

        thread = threading.Thread(
            target=run, name=f"brain-{session_id[:6]}", daemon=True
        )
        self._brain_threads[session_id] = thread
        thread.start()
        return TurnResult(
            session_id=session_id,
            lane="deep",
            reply="",
            spoken="",
            turn_id=turn_id,
            thinking=True,
        )

    def _land_brain(
        self, session_id: str, reply: str, meta: dict[str, Any], turn_id: int
    ) -> TurnResult:
        # Final user-facing boundary. Model/API truncation has produced endings
        # like "Which way you want to" despite a complete answer before it.
        reply = drop_incomplete_tail(_flatten(reply))
        turn_meta: dict[str, Any] = {"lane": "deep", "answers_turn": turn_id, **meta}
        landed = self.store.append_turn(session_id, "brutus", reply, meta=turn_meta)
        spoken = speechify(reply)
        outbox = self._outbox_by_turn.pop(turn_id, None)
        if outbox is not None:
            from . import resilience

            resilience.mark_outbox(
                outbox,
                status="failed" if meta.get("error") else "done",
                reply=reply,
                error=str(meta.get("error") or ""),
            )
        self.emit("turn", {"session_id": session_id, "turn": landed.as_dict()})
        self.emit(
            "answer",
            {
                "session_id": session_id,
                "turn": landed.as_dict(),
                "answers_turn": turn_id,
                "spoken": spoken,
            },
        )
        return TurnResult(
            session_id=session_id,
            lane="deep",
            reply=reply,
            spoken=spoken,
            turn_id=landed.id,
            tool=(meta.get("tools") or [None])[-1] if meta.get("tools") else None,
            error=meta.get("error"),
        )

    def wait_for_brain(self, session_id: str, timeout: float = 60.0) -> bool:
        """Test/CLI helper — block until the brain lands. Never used by the UI."""
        thread = self._brain_threads.get(session_id)
        if not thread:
            return True
        thread.join(timeout)
        return not thread.is_alive()

    # Old name, kept so nothing that joined the deep lane breaks.
    wait_for_deep = wait_for_brain

    # --- shared tail ------------------------------------------------------

    def _land(
        self,
        session_id: str,
        lane: Lane,
        reply: str,
        turn_id: int,
        *,
        tool: str | None = None,
        thinking: bool = False,
        error: str | None = None,
    ) -> TurnResult:
        reply = _flatten(reply)
        meta: dict[str, Any] = {"lane": lane, "tool": tool}
        if thinking:
            meta["thinking"] = True
        landed = self.store.append_turn(
            session_id, "brutus", reply, meta=meta
        )
        # ONE reply, rendered twice. The mouth gets a summary of the screen,
        # never a separately generated answer — that is what keeps voice and
        # text from drifting into two personalities.
        result = TurnResult(
            session_id=session_id,
            lane=lane,
            reply=reply,
            spoken=speechify(reply),
            turn_id=landed.id,
            tool=tool,
            thinking=thinking,
            error=error,
        )
        self.emit("turn", {"session_id": session_id, "turn": landed.as_dict()})
        self.emit("reply", result.as_dict())
        return result
