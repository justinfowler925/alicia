"""One Claude brain with native tools and gated local hands.

Two entry points, one policy:

  `brain_reply` — the conversation. Claude with the full session history,
  a native tool catalog, prompt caching, and the write gate exactly as it was. This
  replaces the regex router + two-lane split that docs/CONVERSATION_REBUILD_PLAN.md
  diagnosed: decisions belong to a model that read the message; deterministic
  code keeps the two jobs it is actually good at — executing tools and gating
  writes.

  `complete` — one-shot Cursor completion for everything that is not a
  conversation turn (chat_resolve's legacy /api/chat path, refine, transcript
  summaries). The 8B was never a conversation partner — it invented tickets,
  burned its token budget thinking, and leaked a persona that claimed it had
  no tools. Local MLX is not a fallback anywhere in this module.

Hard lines, inherited from the gate design (gate.py):

  * The brain can READ anything and write to Justin's own notepad. Nothing else.
  * Every ledger write, dispatch, delete, or backend hand-off goes through
    `propose_action`, which drafts an Artifact. Approval executes the artifact's
    args verbatim, with no model between the preview and the run.
  * Ticket ids that appear from nowhere are challenged once, then annotated —
    never silently trusted (the 8B invented REV-402/403 whole; a bigger model
    earns a challenge instead of a full-reply replacement, but not blind trust).

The Anthropic call itself is isolated in `_create` so tests patch one seam.
"""

from __future__ import annotations

import json
import logging
import re
import time
from collections.abc import Callable
from types import SimpleNamespace
from typing import Any

from .config import BrutusCfg
from .focus import spoken_next_decision
from .gate import classify_write, is_voice_forbidden
from .tools import ToolRegistry

log = logging.getLogger("brutus.brain")

# Tool rounds per turn. Each round is one API call; a chat turn normally takes
# one or two. Eight means something is wrong, and the loop says so honestly.
_MAX_ROUNDS = 8

_TICKET_RE = re.compile(r"\bREV-\d+\b", re.IGNORECASE)

_OFFER_ACCEPTANCE_RE = re.compile(
    r"^\s*(?:yes|yeah|yep|sure|okay|ok|go ahead|do it|please do|carry on|"
    r"i(?:'|’)m listening)(?:[\s,.!—-].*)?$",
    re.IGNORECASE,
)
_OFFER_RE = re.compile(
    r"(?:want me to|should i|i can|let me|shall i|would you like me to)",
    re.IGNORECASE,
)
_REOFFER_TAIL_RE = re.compile(
    r"(?:\s+|\n+)(?:want me to\b[^?]*\?|"
    r"i can\b[^.?!]*(?:if you want[^.?!]*)?[.?!]?)\s*$",
    re.IGNORECASE,
)
_INCOMPLETE_TAIL_RE = re.compile(
    r"(?:\b(?:to|the|a|an|or|and|which)|[—-])\s*$",
    re.IGNORECASE,
)

_NOT_FOUND = "Sorry, I can't find that shit."


def _looks_not_found(reply: str) -> bool:
    """Whether this reply is telling him the thing does not exist.

    Matched loosely on purpose: the exact string is what the prompt asks for,
    but the transcript that exposed this said "Sorry, I can't find that shit —
    nothing in Linear or notes matching a UI/UX audit redesign", and a guard
    that only catches the bare sentence would have let that through.
    """
    text = (reply or "").casefold()
    return "can't find that" in text or "cannot find that" in text
_UNBACKED_ACTION_CLAIM = re.compile(
    r"\b(?:queued|drafted|proposal (?:is )?(?:ready|queued)|say yes to do it)\b",
    re.IGNORECASE,
)


class BrainError(Exception):
    """Both cognitive backends failed, or the only requested one did."""

    def __init__(self, message: str, *, tried: list[str] | None = None) -> None:
        super().__init__(message)
        self.tried = tried or []


BRAIN_SYSTEM = """You are Brutus — Justin's right hand for Clearspeed RevOps, \
running 24/7 on his laptop. You are the front door: he talks to you by voice \
and text. Use the configured profile tools explicitly; never hide a provider \
fallback. Atlas is intentionally ignored.

VOICE. Sharp coworker on a live call. Casual, direct, brief by default — a \
spoken beat or two unless he asked for depth; real questions get real answers. \
No corporate filler ("certainly", "happy to help", "great question"). Never \
repeat his question verbatim. For a new work instruction or correction, begin \
with one short sentence stating the outcome and scope you understood, in your \
own words; distinguish what you will do from what needs approval. That sentence \
is the on-screen intent readback. For questions, lead with the answer instead. \
Never apologise for your tone. Plain prose; \
"- " dashes are fine; NEVER markdown headers, tables, or rules — the panel \
renders **bold** and nothing else. Don't read file paths, URLs, or hashes \
aloud — name the thing, not its address.

TRUTH. Facts about work state come from tools called THIS turn — board state \
quoted in earlier turns is stale, and you never invent tickets, PRs, \
approvals, or email/Slack contents. When he asks you to find something and \
the tool comes back empty, reply exactly: Sorry, I can't find that shit. \
When you don't know, look it up or say you don't know — a confident guess is \
the one unforgivable answer.

DOING vs PROPOSING. Freely: read local and Linear-backed work surfaces, notes, \
and agent threads, and write to his own notepad \
(capture_note, update_note, save_working_note, draft_lesson). Everything \
that touches the ledger, dispatches a bot, deletes, or hands work to a \
backend goes through propose_action — it drafts the action for his approval. \
After proposing, tell him in one line what is queued and end with: Say yes \
to do it. Never claim a gated action happened — his yes runs it, not you. \
Never claim you did, sent, or logged anything unless a tool result this turn \
proves it.

WHAT NEEDS HIM. For agent work, call get_supervised_work and lead with its one \
evidence-backed intervention; ordinary progress is silent. assess_agent_thread \
returns judgment, not a transcript summary. For portfolio work, get_work_surface \
returns next_decision. Never recite the whole board.

NEW WORK. Before proposing a ticket, call compile_unfog_work with outcome, \
target, premise, scope, preservation, acceptance, and delivery plus evidence. \
If matching work is inflight, continue it. If an exact ticket exists, update it. \
Only draft create_linear_ticket with that complete contract when the compiler says draft_new_ticket. If it \
says frontier, propose ask_frontier with the complete contract and material \
justification. Never use frontier merely to rewrite or summarize.

CONVERSATION. You have the whole session's history — use it. "That one", \
"is it done", "carry on" refer to things already discussed: resolve the \
referent from history, then verify current state with a tool before \
asserting it. ACCEPTANCE IS AN INSTRUCTION: when your immediately prior turn \
offered a specific next step and Justin accepts it, perform that exact step \
now. Do not repeat the prior summary, restate the offer, or ask permission a \
second time. Gated writes still go through propose_action."""

# The deterministic compiler behind propose_action rejects only details that
# change what would execute. This tells the conversational layer how to recover
# without turning every underspecified request into an interview.
BRAIN_SYSTEM += """\

INTENT CONTRACT. Before propose_action, resolve the concrete outcome and target \
from this session's history and current tool evidence. Preserve everything \
Justin did not put in scope. Do not ask for details that would not change the \
result. If propose_action reports a missing material intent detail, ask one \
focused question and do not draft a placeholder action."""


def _accepted_prior_offer(messages: list[dict[str, Any]]) -> str:
    """Return the accepted offer, or empty when the last turn is not an acceptance."""
    if len(messages) < 2:
        return ""
    previous, current = messages[-2], messages[-1]
    if previous.get("role") != "assistant" or current.get("role") != "user":
        return ""
    offer = str(previous.get("content") or "").strip()
    acceptance = str(current.get("content") or "").strip()
    if not _OFFER_ACCEPTANCE_RE.match(acceptance) or not _OFFER_RE.search(offer):
        return ""
    return offer


def drop_incomplete_tail(text: str) -> str:
    """Drop a visibly amputated final sentence while preserving a complete answer."""
    if not _INCOMPLETE_TAIL_RE.search(text):
        return text
    boundaries = list(re.finditer(r"[.!?](?:\s+|$)", text))
    if boundaries:
        complete = text[: boundaries[-1].end()].strip()
        if complete:
            return complete
    return text.rstrip(" —-").strip() + "."


# ---------------------------------------------------------------------------
# One-shot Cursor completion (chat_resolve, refine, transcript summaries).
# ---------------------------------------------------------------------------


def pack_messages(messages: list[dict[str, str]]) -> tuple[str, str]:
    """Fold a chat-completions list into (system, user body) for one-shot APIs."""
    system = ""
    parts: list[str] = []
    for raw in messages or []:
        role = str(raw.get("role") or "").strip().lower()
        content = str(raw.get("content") or "").strip()
        if not content:
            continue
        if role == "system" and not system:
            system = content
            continue
        if role == "system":
            parts.append(content)
            continue
        label = "Justin" if role == "user" else "Brutus"
        parts.append(f"{label}: {content}")
    body = "\n\n".join(parts).strip()
    if len(parts) == 1 and parts[0].startswith("Justin: "):
        body = parts[0][len("Justin: ") :]
    return system, body


def complete(
    cfg: BrutusCfg,
    messages: list[dict[str, str]],
    *,
    prefer: str | None = None,
    allow_alternate: bool = True,
    **_ignored: Any,
) -> str:
    """Return assistant text from Cursor only.

    Compatibility arguments are accepted but cannot select another backend.
    """
    system, body = pack_messages(messages)
    if not body:
        raise BrainError("empty prompt")
    _ = (prefer, allow_alternate)
    result = _call(cfg, "cursor", system, body)
    reply = str(result.get("reply") or "").strip()
    if result.get("ok") and reply:
        return reply
    raise BrainError(f"cursor: {result.get('error') or 'empty reply'}", tried=["cursor"])


def _call(cfg: BrutusCfg, backend: str, system: str, body: str) -> dict[str, Any]:
    if backend != "cursor":
        return {"ok": False, "error": f"backend {backend!r} is disabled"}
    from .cursor_runner import run_cursor_chat

    prompt = f"{system}\n\n{body}".strip() if system else body
    root = cfg.cursor_runner.reasoning_root if cfg.cursor_runner else "~/.brutus/app"
    return run_cursor_chat(cfg, prompt, repo_hint=root, mutate=False)


# ---------------------------------------------------------------------------
# Native Claude call seam (the conversation path)
# ---------------------------------------------------------------------------


def _create(cfg: BrutusCfg, **kwargs: Any) -> Any:
    """Create one native Anthropic Messages turn with a cacheable stable prefix."""
    from anthropic import Anthropic

    claude = cfg.claude
    if claude is None or not claude.enabled:
        raise BrainError("Claude is disabled", tried=["claude"])

    system = [dict(block) for block in (kwargs.get("system") or [])]
    if system:
        # BRAIN_SYSTEM is stable across turns. Voice instructions and standing
        # notes deliberately remain after the cache breakpoint.
        system[0]["cache_control"] = {"type": "ephemeral"}
    client_kwargs: dict[str, Any] = {"timeout": claude.timeout_s}
    if claude.api_key.strip():
        client_kwargs["api_key"] = claude.api_key.strip()
    client = Anthropic(**client_kwargs)
    return client.messages.create(
        model=claude.model,
        max_tokens=claude.max_tokens,
        output_config={"effort": claude.effort},
        system=system,
        tools=kwargs.get("tools") or [],
        messages=kwargs.get("messages") or [],
    )


def _parse_cursor_tool_call(text: str) -> tuple[str, dict[str, Any]] | None:
    """Accept the exact protocol with optional Markdown fence, never surrounding prose."""
    value = (text or "").strip()
    if value.startswith("```") and value.endswith("```"):
        value = re.sub(r"^```(?:json|text)?\s*", "", value, flags=re.IGNORECASE)
        value = re.sub(r"\s*```$", "", value).strip()
    match = re.fullmatch(r"TOOL:\s*([a-zA-Z0-9_]+)\s*\nARGS:\s*(\{.*\})\s*", value, re.DOTALL)
    if not match:
        return None
    try:
        args = json.loads(match.group(2))
    except ValueError:
        return None
    return (match.group(1), args) if isinstance(args, dict) else None


# ---------------------------------------------------------------------------
# Tool surface
# ---------------------------------------------------------------------------

# Everything the brain may call directly. Reads plus the notepad free writes —
# the same split gate.py enforces. Gated tools are NOT in this list on purpose:
# they exist only behind propose_action.
BRAIN_READS = (
    "get_nucleus",
    "get_work_surface",
    "get_digest",
    "list_notes",
    "list_working_notes",
    "list_lessons",
    "list_conversations",
    "list_agent_threads",
    "get_supervised_work",
    "assess_agent_thread",
    "compile_unfog_work",
)
BRAIN_FREE_WRITES = ("capture_note", "update_note", "save_working_note", "draft_lesson")

# Gated tools the brain may PROPOSE (drafts an artifact; Justin approves).
PROPOSABLE = (
    "organize_agent_thread",
    "organize_project",
    "delete_note",
    "ask_cursor",
    "ask_frontier",
    "create_linear_ticket",
)

_PROPOSE_TOOL = {
    "name": "propose_action",
    "description": (
        "Draft a gated action for Justin's approval. Nothing runs until he says "
        "yes — the drafted args execute verbatim after approval, with no model "
        "in between. Use for anything that touches the ledger, dispatches a "
        "bot, deletes, or hands work to a backend: "
        + ", ".join(PROPOSABLE)
        + ". After calling this, tell Justin what is queued and end with: Say yes to do it."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "tool": {"type": "string", "enum": list(PROPOSABLE)},
            "args": {
                "type": "object",
                "description": "arguments for the tool, exactly as they should execute",
            },
        },
        "required": ["tool", "args"],
    },
}

_RECALL_TOOL = {
    "name": "recall",
    "description": (
        "Search Brutus's laptop memory — working notes and lesson drafts — for "
        "context from earlier sessions. Use when Justin refers to something you "
        "don't see in this conversation."
    ),
    "input_schema": {
        "type": "object",
        "properties": {"q": {"type": "string", "description": "search phrase"}},
        "required": ["q"],
    },
}


def anthropic_tools(registry: ToolRegistry) -> list[dict[str, Any]]:
    """The brain's tool catalog in Messages API shape. Deterministic order —
    the tool list renders ahead of the system prompt, so a stable order is what
    makes the cached prefix actually cache."""
    out: list[dict[str, Any]] = []
    for name in (*BRAIN_READS, *BRAIN_FREE_WRITES):
        tool = registry.get(name)
        if tool is None:
            continue
        schema = tool.parameters or {}
        if not schema.get("type"):
            schema = {"type": "object", "properties": schema.get("properties", {}) or {}}
        out.append({"name": tool.name, "description": tool.description, "input_schema": schema})
    out.append(_RECALL_TOOL)
    out.append(_PROPOSE_TOOL)
    return out


# ---------------------------------------------------------------------------
# The loop
# ---------------------------------------------------------------------------


def _attr(block: Any, key: str, default: Any = None) -> Any:
    """Read a field off an SDK block object or a plain dict alike."""
    if isinstance(block, dict):
        return block.get(key, default)
    return getattr(block, key, default)


def _blocks_text(content: Any) -> str:
    texts: list[str] = []
    for block in content or []:
        if _attr(block, "type") == "text":
            text = _attr(block, "text") or ""
            if text:
                texts.append(str(text))
    return "\n".join(texts).strip()


def _tool_uses(content: Any) -> list[Any]:
    return [b for b in (content or []) if _attr(b, "type") == "tool_use"]


def _tick_usage(meta: dict[str, Any], usage: Any) -> None:
    if usage is None:
        return
    for src, dst in (
        ("input_tokens", "input_tokens"),
        ("output_tokens", "output_tokens"),
        ("cache_read_input_tokens", "cache_read"),
        ("cache_creation_input_tokens", "cache_write"),
    ):
        val = getattr(usage, src, None)
        if isinstance(val, int):
            meta[dst] = meta.get(dst, 0) + val


def _ticket_ids(*blobs: Any) -> set[str]:
    found: set[str] = set()
    for b in blobs:
        if b is None:
            continue
        text = b if isinstance(b, str) else json.dumps(b, default=str)
        found |= {m.upper() for m in _TICKET_RE.findall(text)}
    return found


def brain_reply(
    cfg: BrutusCfg,
    registry: ToolRegistry,
    *,
    history: list[dict[str, Any]],
    channel: str = "text",
    standing_notes: str = "",
    on_propose: Callable[[str, dict[str, Any]], dict[str, Any]] | None = None,
    on_tool_result: Callable[[str, dict[str, Any]], None] | None = None,
    recall: Callable[[str], dict[str, Any]] | None = None,
) -> tuple[str, dict[str, Any]]:
    """One turn on the selected Claude transport, never an alternate model.

    API requires explicit opt-in and an absent kill file. Both transports use
    the same tool and truthfulness gates. Voice may recover deterministically.
    """
    from . import resilience

    started = time.monotonic()
    meta: dict[str, Any] = {"brain": True, "backend": "claude_cli", "rounds": 0, "tools": []}
    claude = cfg.claude
    if claude is None or not claude.enabled:
        return (
            "The conversational brain is offline. Check ~/.brutus/config.yaml claude.enabled.",
            {**meta, "error": "brain_disabled", "backend": "none"},
        )

    system_text = BRAIN_SYSTEM
    if channel == "voice":
        system_text += (
            "\n\nTHIS IS A LIVE VOICE TURN. You have time — Justin sees a thinking "
            "face while you work. Prefer a correct, conversational spoken answer "
            "over a fast empty one. Usually one to three short sentences, under "
            "about 60 spoken words unless he asked for depth. Lead with the "
            "answer. Ask at most one question. Do not narrate tools or invent "
            "progress. Natural fragments are fine; never fill silence with filler."
        )
    if standing_notes.strip():
        system_text += "\n\n" + standing_notes.strip()

    messages: list[dict[str, Any]] = [
        {"role": m["role"], "content": m["content"]}
        for m in history
        if m.get("role") in ("user", "assistant") and str(m.get("content") or "").strip()
    ]
    if not messages or messages[-1]["role"] != "user":
        return "", {**meta, "error": "no user message"}

    accepted_offer = _accepted_prior_offer(messages)
    if accepted_offer:
        system_text += (
            "\n\nJUSTIN ACCEPTED YOUR IMMEDIATELY PRIOR OFFER. Carry out that exact "
            "offered next step now, using the relevant tool if it is a lookup. "
            "Do not repeat what you already told him, offer the step again, or ask "
            "for permission again. The accepted offer was: " + accepted_offer[:1200]
        )

    tool_names = ", ".join(
        [*(BRAIN_READS), *(BRAIN_FREE_WRITES), "recall", "propose_action"]
    )
    protocol = (
        "\n\nTOOL PROTOCOL (when you need a Brutus tool):\n"
        "Reply with exactly two lines and nothing else:\n"
        "TOOL: <tool_name>\n"
        "ARGS: <json object>\n"
        f"Available tools: {tool_names}\n"
        "When you are done with tools, reply with the spoken answer only — "
        "no TOOL: line.\n"
    )
    cli_system = system_text + protocol
    transport = str(claude.transport or "cli").strip().lower()
    meta["backend"] = f"claude_{transport}"
    if transport == "api" and not (
        claude.api_enabled and not resilience.api_killed() and claude.api_key.strip()
    ):
        meta["error"] = "selected API transport is disabled or unconfigured"
        meta["api_error"] = "brain_auth_unavailable"
        reply = ""
    elif transport not in {"cli", "api"}:
        meta["error"] = "unknown Claude transport"
        reply = ""
    else:
        reply, meta = _guarded_tool_loop(
            cfg, registry, messages=messages, system_text=system_text,
            standing_notes=standing_notes, channel=channel, meta=meta,
            on_propose=on_propose, on_tool_result=on_tool_result,
            recall=recall, accepted_offer=accepted_offer,
            create=_create if transport == "api" else _create_cli,
            cli_system=cli_system,
        )
    meta["ms"] = int((time.monotonic() - started) * 1000)
    if reply:
        return reply, meta
    # A grounded voice answer is not a provider fallback. Text failures remain
    # explicit; the durable conversation outbox owns retry, not another model.
    if channel == "voice":
        det = _deterministic_reply(registry, messages, meta)
        if det is not None:
            return det
    return (
        "I couldn't finish that turn. Your request is safe; please try again.",
        meta,
    )


def _create_cli(cfg: BrutusCfg, **kwargs: Any) -> Any:
    """Adapt completion-only CLI output to the shared guarded tool loop."""
    from .claude import ask_claude
    from .resilience import timeouts

    turns = []
    for message in kwargs["messages"]:
        content = message["content"]
        if not isinstance(content, str):
            parts = []
            for block in content:
                kind = _attr(block, "type")
                if kind == "tool_use":
                    parts.append(f"TOOL: {_attr(block, 'name')}\nARGS: {json.dumps(_attr(block, 'input'))}")
                elif kind == "tool_result":
                    parts.append(f"TOOL_RESULT: {_attr(block, 'content')}")
                elif kind == "text":
                    parts.append(str(_attr(block, "text")))
            content = "\n".join(parts)
        turns.append(f"{message['role']}: {content}")
    result = ask_claude(
        cfg, "\n\n".join(turns), system=kwargs["cli_system"],
        timeout_s=timeouts()["brain_cli_s"],
    )
    body = str(result.get("reply") or "").strip()
    if not result.get("ok") or not body:
        raise BrainError(str(result.get("error") or "Claude CLI returned no reply"))
    directive = _parse_cursor_tool_call(body) or _loose_tool_directive(body)
    if directive:
        name, args = directive
        content = [SimpleNamespace(type="tool_use", name=name, input=args, id="cli_tool")]
    else:
        content = [SimpleNamespace(type="text", text=body)]
    return SimpleNamespace(content=content, usage=None)


def _loose_tool_directive(text: str) -> tuple[str, dict[str, Any]] | None:
    """Accept TOOL:/ARGS: even when the model adds a trailing sentence."""
    match = re.search(
        r"TOOL:\s*([a-zA-Z0-9_]+)\s*\nARGS:\s*(\{.*\})",
        text or "",
        re.DOTALL,
    )
    if not match:
        return None
    try:
        args = json.loads(match.group(2))
    except ValueError:
        return None
    return (match.group(1), args) if isinstance(args, dict) else None


def _deterministic_reply(
    registry: ToolRegistry,
    messages: list[dict[str, Any]],
    meta: dict[str, Any],
) -> tuple[str, dict[str, Any]] | None:
    latest = next(
        (
            str(m.get("content") or "").strip()
            for m in reversed(messages)
            if m.get("role") == "user" and str(m.get("content") or "").strip()
        ),
        "",
    )
    folded = latest.casefold()
    if any(
        phrase in folded
        for phrase in (
            "what needs me",
            "needs my attention",
            "need my attention",
            "what's waiting",
            "whats waiting",
            "what is waiting",
        )
    ):
        try:
            called = registry.call("get_work_surface", {})
            surface = called.get("result") if called.get("ok") else None
            if isinstance(surface, dict):
                return spoken_next_decision(surface), {
                    **meta,
                    "backend": "deterministic",
                    "fallback": "deterministic_work_surface",
                }
        except Exception:
            log.debug("Deterministic recovery unavailable", exc_info=True)
    if "focus our conversation on agent session" in folded or (
        "agent session" in folded and ("needs me" in folded or "intervene" in folded)
    ):
        try:
            called = registry.call("get_supervised_work", {})
            payload = called.get("result") if called.get("ok") else None
            if isinstance(payload, dict):
                sessions = payload.get("sessions") or payload.get("agents") or []
                intervene = [
                    s
                    for s in sessions
                    if isinstance(s, dict)
                    and (s.get("assessment") or {}).get("should_intervene")
                ]
                if intervene:
                    top = intervene[0]
                    a = top.get("assessment") or {}
                    title = top.get("title") or "Untitled session"
                    why = (
                        a.get("blocker_or_decision")
                        or a.get("recommended_next_action")
                        or top.get("state")
                        or "needs you"
                    )
                    return (
                        (f"{title}. {why}. {len(intervene)} session"
                         f"{'s' if len(intervene) != 1 else ''} need you."),
                        {
                            **meta,
                            "backend": "deterministic",
                            "fallback": "deterministic_supervised_work",
                        },
                    )
        except Exception:
            log.debug("Deterministic recovery unavailable", exc_info=True)
    if "hello" in folded or folded in {"hi", "hey", "you there?"}:
        return (
            "Hello — I'm here and ready to work.",
            {**meta, "backend": "deterministic", "fallback": "deterministic_social"},
        )
    return None


def _guarded_tool_loop(
    cfg: BrutusCfg,
    registry: ToolRegistry,
    *,
    messages: list[dict[str, Any]],
    system_text: str,
    standing_notes: str,
    channel: str,
    meta: dict[str, Any],
    on_propose: Callable[[str, dict[str, Any]], dict[str, Any]] | None,
    on_tool_result: Callable[[str, dict[str, Any]], None] | None,
    recall: Callable[[str], dict[str, Any]] | None,
    accepted_offer: str,
    create: Callable[..., Any],
    cli_system: str,
) -> tuple[str, dict[str, Any]]:
    """Shared truthfulness and tool gates for the selected transport."""
    system: list[dict[str, Any]] = [{"type": "text", "text": BRAIN_SYSTEM}]
    volatile = system_text.removeprefix(BRAIN_SYSTEM).strip()
    if volatile:
        system.append({"type": "text", "text": volatile})
    tools = anthropic_tools(registry)
    working = [dict(m) for m in messages]
    allowed = _ticket_ids(*[m.get("content") for m in working], standing_notes)
    challenged = False
    challenged_action_claim = False
    challenged_not_found = False

    for _ in range(_MAX_ROUNDS):
        meta["rounds"] += 1
        try:
            call_args = {"system": system, "tools": tools, "messages": working}
            if create is _create_cli:
                call_args["cli_system"] = cli_system
            resp = create(cfg, **call_args)
        except Exception as exc:  # noqa: BLE001
            log.warning("brain %s call failed: %s", meta["backend"], exc)
            meta["error"] = str(exc)[:300]
            error_key = "api_error" if meta["backend"] == "claude_api" else "cli_error"
            meta[error_key] = "brain_service_unavailable"
            folded = str(exc).casefold()
            if any(t in folded for t in ("credit balance", "too low", "billing")):
                meta[error_key] = "brain_credits_exhausted"
            elif any(t in folded for t in ("api_key", "api key", "auth", "credential", "1password")):
                meta[error_key] = "brain_auth_unavailable"
            return "", meta

        _tick_usage(meta, getattr(resp, "usage", None))
        uses = _tool_uses(getattr(resp, "content", None))
        if not uses:
            reply = _blocks_text(getattr(resp, "content", None))
            if accepted_offer and meta["tools"]:
                cleaned = _REOFFER_TAIL_RE.sub("", reply).strip()
                if cleaned != reply:
                    meta["stripped_reoffer"] = True
                    reply = cleaned
            completed = drop_incomplete_tail(reply)
            if completed != reply:
                meta["dropped_incomplete_tail"] = True
                reply = completed
            unbacked_action = not meta.get("proposed_artifacts") and bool(
                _UNBACKED_ACTION_CLAIM.search(reply)
            )
            if unbacked_action and channel == "voice":
                meta["blocked_action_claim"] = True
                return (
                    "I didn't create a proposal. Nothing was queued or changed.",
                    meta,
                )
            if unbacked_action and not challenged_action_claim:
                challenged_action_claim = True
                meta["challenged_action_claim"] = True
                working.append({"role": "assistant", "content": resp.content})
                working.append(
                    {
                        "role": "user",
                        "content": (
                            "You claimed a proposal was queued, but no propose_action tool call "
                            "exists. Call propose_action now or say plainly that no proposal "
                            "was created."
                        ),
                    }
                )
                continue
            if unbacked_action:
                meta["blocked_action_claim"] = True
                return (
                    "I didn't create a proposal. Nothing was queued or changed.",
                    meta,
                )
            invented = sorted(_ticket_ids(reply) - allowed)
            if invented and not challenged:
                challenged = True
                meta["challenged_tickets"] = ",".join(invented)
                working.append({"role": "assistant", "content": resp.content})
                working.append(
                    {
                        "role": "user",
                        "content": (
                            f"You named {', '.join(invented)}, which appears nowhere in this "
                            "conversation or any tool result. Correct the answer using only "
                            "state you can verify — call get_work_surface or get_nucleus if "
                            "you need to."
                        ),
                    }
                )
                continue
            if invented:
                meta["invented_tickets"] = ",".join(invented)
                for tid in invented:
                    reply = re.sub(
                        rf"\b{re.escape(tid)}\b",
                        f"{tid} (unverified)",
                        reply,
                        flags=re.IGNORECASE,
                    )
            if (
                not challenged_not_found
                and _looks_not_found(reply)
                and "list_agent_threads" not in meta["tools"]
            ):
                challenged_not_found = True
                meta["challenged_not_found"] = True
                working.append({"role": "assistant", "content": resp.content})
                working.append(
                    {
                        "role": "user",
                        "content": (
                            "You have not searched agent threads this turn. Call "
                            "list_agent_threads with the words he used before you tell him "
                            "it does not exist."
                        ),
                    }
                )
                continue
            return reply or _NOT_FOUND, meta

        working.append({"role": "assistant", "content": resp.content})
        results: list[dict[str, Any]] = []
        for use in uses:
            name = _attr(use, "name")
            args = _attr(use, "input") or {}
            use_id = _attr(use, "id")
            if not isinstance(args, dict):
                args = {}
            meta["tools"].append(name)
            payload = _run_tool(
                registry,
                str(name),
                args,
                channel=channel,
                on_propose=on_propose,
                on_tool_result=on_tool_result,
                recall=recall,
            )
            if name == "propose_action" and payload.get("ok") and payload.get("artifact_id"):
                meta.setdefault("proposed_artifacts", []).append(payload["artifact_id"])
            allowed |= _ticket_ids(payload)
            results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": use_id,
                    "content": json.dumps(payload, default=str)[:8000],
                    "is_error": bool(isinstance(payload, dict) and payload.get("ok") is False),
                }
            )
        working.append({"role": "user", "content": results})

    meta["error"] = "tool round cap"
    return (
        "I got stuck in my own tools on that one — say it again and I'll take a straighter path.",
        meta,
    )


def _run_tool(
    registry: ToolRegistry,
    name: str,
    args: dict[str, Any],
    *,
    channel: str,
    on_propose: Callable[[str, dict[str, Any]], dict[str, Any]] | None,
    on_tool_result: Callable[[str, dict[str, Any]], None] | None,
    recall: Callable[[str], dict[str, Any]] | None,
) -> dict[str, Any]:
    if name == "recall":
        if recall is None:
            return {"ok": False, "error": "recall is not wired"}
        try:
            return recall(str(args.get("q") or ""))
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc)}

    if name == "propose_action":
        tool = str(args.get("tool") or "")
        t_args = args.get("args") if isinstance(args.get("args"), dict) else {}
        if classify_write(tool) != "gated":
            return {
                "ok": False,
                "error": f"{tool} is not a gated tool — call it directly or not at all",
            }
        if channel == "voice" and is_voice_forbidden(tool):
            return {
                "ok": False,
                "error": f"{tool} is keyboard-only — it cannot be proposed from voice",
            }
        if on_propose is None:
            return {"ok": False, "error": "proposals are not wired"}
        try:
            drafted = on_propose(tool, dict(t_args))
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc)}
        return {
            "ok": True,
            "proposed": tool,
            **drafted,
            "next": "Waiting on Justin — nothing runs until he says yes.",
        }

    if name not in (*BRAIN_READS, *BRAIN_FREE_WRITES):
        return {"ok": False, "error": f"{name} is not available to the brain"}
    result = registry.call(name, args)
    if on_tool_result is not None:
        try:
            on_tool_result(name, result)
        except Exception:  # noqa: BLE001, S110 — a mirror must never break the turn
            pass
    return result

