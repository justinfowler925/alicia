"""Writes go through an object you approved, never a sentence a model wrote.

The obvious design — "read-only until you say yes" — cannot be made safe, and it
is worth being precise about why. In read-only mode the mutating tool is *absent
from the registry*, so there is nothing real to preview. What you'd be confirming
is prose the model authored, and then a different code path would execute. The
preview and the execution are two different computations, which is not a gate at
all; it is a user interface for a hallucination.

The proof was sitting in this codebase: given "approve REV-412" the model
answered "Approved REV-412." three times out of three while no approve tool
existed anywhere.

So the gate is an object:

  1. A mutating phrase never executes. It builds an Artifact — {tool, args} —
     and the args are exactly what will later be passed to the registry.
  2. You see it in full on screen and hear a summary of the same thing.
  3. Approving calls registry.call(artifact.tool, artifact.args) with NO model
     between the preview and the execution. The object you saw is the object
     that runs, byte for byte.
  4. Settling is single-use, so a double-click or a re-heard "yes" cannot
     execute twice.

Two things stay deliberately outside the gate, and one stays outside the voice
path entirely — see FREE_WRITES and VOICE_FORBIDDEN below.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

# Your own notepad. Writing something down is remembering, not acting, and
# gating it is what turns a confirmation habit into an annoyance you route
# around. Nothing here touches the ledger, spends money, or reaches a bot.
FREE_WRITES = frozenset(
    {"capture_note", "update_note", "save_working_note", "draft_lesson"}
)

# Everything that touches the ledger, dispatches a bot, or costs money.
# delete_note is gated because it is destructive — the pad is free to write,
# not free to erase without a confirm.
GATED = frozenset(
    {
        "approve_gate",
        "dispatch_tick",
        "answer_steering",
        "register_thread",
        "promote_note",
        "delete_note",
        "ask_atlas6",
        "ask_claude",
        "ask_cursor",
        "ask_frontier",
        "create_linear_ticket",
        "organize_agent_thread",
        "organize_project",
    }
)

# Not reachable by talking at all, gate or no gate. ask_cursor launches an
# autonomous agent with a shell, and its allowlist includes ~/Projects/alicia —
# so a spoken instruction could put an agent inside the gate's own source. Its
# "do not commit or push" instruction is prose to something that can run git,
# and "prescribe, don't prohibit" says that is how you get the banned action.
VOICE_FORBIDDEN = frozenset({"ask_cursor"})


@dataclass(frozen=True)
class Proposal:
    """A write that has been described but not performed."""

    tool: str
    args: dict[str, Any]
    kind: str
    summary: str
    spoken: str


def classify_write(tool: str) -> str:
    """free | gated | unknown — what the gate should do with this tool."""
    if tool in FREE_WRITES:
        return "free"
    if tool in GATED:
        return "gated"
    return "unknown"


def is_voice_forbidden(tool: str) -> bool:
    return tool in VOICE_FORBIDDEN


# --- reading intent, without letting filler words decide anything ---------

_LIVE_DISPATCH = re.compile(
    r"\b(?:live\s+dispatch|dispatch\s+for\s+real|real\s+dispatch|not\s+a\s+dry\s+run)\b", re.I
)


def dispatch_is_live(message: str) -> bool:
    """Whether a dispatch request means a LIVE fan-out rather than a preview.

    This used to be `any(w in lower for w in ("for real", "live tick", ...))`,
    which is a filler-word test, not an intent test. "Dispatch now for real
    quick" set dry_run=False and fanned out every ready ticket. No mishearing
    required — ordinary speech did it.

    Now it takes a deliberate phrase, and even then the result is only a
    *proposal*: you still see the live/preview distinction read back before
    anything runs.
    """
    return bool(_LIVE_DISPATCH.search(message or ""))


# --- turning a tool call into something you can read back ----------------


def _short(value: Any, limit: int = 160) -> str:
    text = str(value or "").strip().replace("\n", " ")
    return text if len(text) <= limit else f"{text[: limit - 1]}…"


def describe(tool: str, args: dict[str, Any]) -> tuple[str, str]:
    """(screen summary, spoken summary) for a proposed write.

    Deterministic. The whole point of the gate is that no model stands between
    what you are shown and what runs, and that includes the description.
    """
    a = args or {}
    if tool == "approve_gate":
        decision = str(a.get("decision") or "approve")
        ticket = a.get("ticket") or "?"
        return (f"{decision.title()} {ticket}", f"{decision} {ticket}?")
    if tool == "dispatch_tick":
        live = not a.get("dry_run", True)
        what = "a LIVE dispatch tick" if live else "a dispatch tick in preview only"
        return (
            f"Run {what}" + (" (this sends real work to the bots)" if live else ""),
            f"Run {'a live dispatch — this sends real work to the bots' if live else 'a preview dispatch'}?",
        )
    if tool == "answer_steering":
        ticket = a.get("ticket_id") or "?"
        body = _short(a.get("body"))
        # Read the BODY back, not just the act. Confirming "you're answering
        # REV-418" confirms nothing about what the answer says, and the body is
        # what actually gets posted to a live agent.
        return (
            f"Answer {ticket} with: “{body}”",
            f"Answer {ticket} with: {body}. Send it?",
        )
    if tool == "register_thread":
        return (f"Track a new thread: {_short(a.get('title'))}", "Start tracking that?")
    if tool == "promote_note":
        return (f"Promote note {a.get('note_id') or a.get('id') or a.get('q') or '?'} into the ledger", "Promote that note?")
    if tool == "delete_note":
        target = _short(a.get("q") or a.get("note_id") or "?")
        return (f"Delete idea: “{target}”", f"Delete that idea — {target}?")
    if tool in ("ask_atlas6", "ask_claude", "ask_cursor", "ask_frontier"):
        return (
            f"Send to {tool.removeprefix('ask_')}: {_short(a.get('message') or a.get('question'))}",
            f"Send that to {tool.removeprefix('ask_')}?",
        )
    if tool == "create_linear_ticket":
        title = _short(a.get("title"))
        return (f"Create one Linear ticket: {title}", f"Create the Linear ticket {title}?")
    if tool == "organize_agent_thread":
        target = _short(a.get("agent_id"))
        changes = ", ".join(f"{key}={_short(value, 60)}" for key, value in a.items() if key != "agent_id")
        return (f"Organize agent thread {target}: {changes}", f"Apply those Alicia labels to {target}?")
    if tool == "organize_project":
        target = _short(a.get("project_id"))
        changes = ", ".join(f"{key}={_short(value, 60)}" for key, value in a.items() if key != "project_id")
        return (f"Organize project {target}: {changes}", f"Apply that Alicia project organization to {target}?")
    pairs = ", ".join(f"{k}={_short(v, 40)}" for k, v in a.items())
    return (f"{tool}({pairs})", f"Run {tool.replace('_', ' ')}?")


def propose(tool: str, args: dict[str, Any]) -> Proposal:
    summary, spoken = describe(tool, args)
    return Proposal(tool=tool, args=dict(args or {}), kind=tool, summary=summary, spoken=spoken)


# --- the spoken yes ------------------------------------------------------

_YES = re.compile(r"^\s*(?:yes|yeah|yep|yup|do it|go ahead|confirm|send it|approved?|ok(?:ay)?)\b", re.I)
_NO = re.compile(r"^\s*(?:no|nope|cancel|stop|never mind|nevermind|don'?t|forget it)\b", re.I)


def read_confirmation(message: str) -> str | None:
    """yes | no | None. Anything that is not clearly a yes is NOT a yes.

    None means "this wasn't an answer to the question" — the caller cancels
    rather than guessing, because the cost of a wrong yes is unbounded and the
    cost of a wrong no is saying it again.
    """
    text = (message or "").strip()
    if _YES.match(text):
        return "yes"
    if _NO.match(text):
        return "no"
    return None
