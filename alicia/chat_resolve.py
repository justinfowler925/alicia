"""Alicia-first chat resolver — Cursor reasons; local tools ground it."""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from .brain import BrainError as LocalLLMError
from .brain import complete as chat_completion
from .client import AtlasClient
from .config import AliciaCfg
from .focus import spoken_next_decision
from .linear_surface import linear_work_surface
from .memory import MemoryStore
from .tools import ToolRegistry, build_default_registry, format_tool_catalog

log = logging.getLogger("alicia.chat_resolve")

# Cap tool rounds so a confused model cannot spin forever on TOOL:/ARGS:.
_MAX_TOOL_ROUNDS = 4

_ALICIA_SYSTEM = """You are Alicia — Justin's right hand for Clearspeed RevOps.
You run 24/7 on his MacBook. You are the front door: Justin chats with you, and
Cursor is your only reasoning backend. Atlas is intentionally ignored and
Claude is not a fallback.

FIRST RULE: answer the question Justin actually asked, directly, in the first
sentence — but only from the context and tools you actually have. If you cannot
find what he asked for, reply with exactly: Sorry, I can't find that shit.
Nothing else — no search narration, no "I looked in…", no alternatives.
Do not invent facts, ticket states, email counts, or anything else. Context
blocks are background, not the answer — never quote their labels or notes
verbatim. If a board note says items are waiting on him and he asked about
something else, one short trailing line at most.

Thinking is your job and it is unrestricted: design features with him, argue
architecture, draft anything he needs (specs, replies, ticket writeups, plans),
weigh tradeoffs, push back when he is wrong. Never deflect thinking work as out
of scope — "help me design X" gets real design help: ask the one or two
questions that matter, propose an approach, name the risks.

You have local and Linear-backed tools. Use them to look up facts and maintain
Justin's local work surfaces.
If Justin asks about work status or WIP, use get_work_surface or get_digest
(probe-filtered).
Memory loop: list_notes / capture_note / update_note / delete_note /
the Ideas pad; list_working_notes / save_working_note for
longer context; draft_lesson / list_lessons for local lesson drafts
(never auto-send email/Slack).
If he wants an autonomous coding handoff, use ask_cursor (keyboard only and gated).
If Cursor is unavailable, say so honestly; never cross to another model.
If a FACTORY ALARM line is present, say it in your first sentence.

You do not execute directly — you route. You never claim you did, sent, logged,
or tracked something unless a tool actually did it. Pretending is worse than refusing.

Formatting — the chat panel renders **bold** and nothing else:
- Plain sentences. Short lines. "- " dashes for lists are fine.
- NEVER use markdown headers (#), tables (|), or rules (---): they show as raw
  symbols and make replies unreadable.

Voice:
- Talk like a sharp coworker on a call, not a status email. Casual. Short.
- No corporate filler ("certainly", "happy to help", "great question", "I'd be glad to").
- No jargon dumps. No repeating the whole WIP list unless asked.
- Never mention Studio yield, resident models, MLX, or how you were synthesized.
- Never invent ledger state, PRs, VH, or approvals.

The "Work surface (authoritative)" block is the same filtered data as Justin's board:
self-test probes and bot-side retriage are already removed. Trust it over anything else
in context, and never resurrect tickets that are not in it. Only the block in the
latest message is current — board state quoted in earlier turns is stale.

When Justin asks about a gate / approval / "what needs me":
Ask ONE question — the top item only. Phrase it so he can answer with
approve / reject / a short answer. Never list the rest of the board. If more
items wait, end with "Then N more." If nothing needs him, say that in one line.
Ticket IDs may ONLY come from the current work-surface block. If it lists none,
there is nothing to name — never fill the gap with an ID from anywhere else.
"""

_ALICIA_OFFLINE_SYSTEM = _ALICIA_SYSTEM

_TOOL_USAGE_BLOCK = """You can use tools to look up facts, save memory, and call backend assistants. Available tools:
{tool_catalog}

To use a tool, reply with exactly these two lines, then stop:
TOOL: <tool_name>
ARGS: <json object>

If you can answer from the context without a tool, just reply normally. Do not
make up information you do not have."""

_GATE_HINTS = (
    "gate",
    "approve",
    "reject",
    "needs me",
    "need me",
    "blocked on",
    "what should i",
    "decision",
    "waiting on me",
    "justin",
    "highest priority",
    "top priority",
    "what should i focus",
    # status-shaped asks get the full needs-you cards too
    "status",
    "rundown",
    "going on",
    "where things",
    "catch me up",
    "what's up",
    "whats up",
    "blocked",
    "stuck",
)



def _looks_like_gate_question(message: str) -> bool:
    lower = message.lower()
    if any(h in lower for h in _GATE_HINTS):
        return True
    return bool(re.search(r"\bREV-\d+\b", message, re.IGNORECASE))


# Spoken ticket ids have no hyphen. Whisper writes "the Rev 367", "rev 367",
# "REV 367" — never "REV-367". Every ticket rule in this file anchors on the
# hyphen, so a spoken ticket matched nothing and the turn fell through to a model
# with no board: asked what was needed on "the Rev 367" it answered that Rev 367
# is the RISC-V Privileged Architecture Specification. Normalise before routing.
_SPOKEN_TICKET = re.compile(r"\b(rev)\s+(\d{1,5})\b", re.IGNORECASE)


def normalise_tickets(message: str) -> str:
    """"rev 367" -> "REV-367". Applied before any routing decision."""
    return _SPOKEN_TICKET.sub(lambda m: f"{m.group(1).upper()}-{m.group(2)}", message or "")


def _lookup_intent(message: str) -> tuple[str, dict[str, Any]] | None:
    """For status/lookup/factory questions, force the right tool so the answer is grounded."""
    message = normalise_tickets(message)
    lower = message.lower()
    if "digest" in lower or "wip digest" in lower:
        return ("get_digest", {})
    # Spoken phrasing puts the ask LAST. "I want to track my other projects, for
    # Scott Moore he's asked for five basic agents ... can you capture that for
    # me" never matched anything anchored at the start, so it fell to the deep
    # lane and came back as generic advice about Jira and Notion. Strip the
    # trailing ask and treat everything before it as the thing to capture.
    trailing = re.search(
        r"^(.*?)[,.\s]*(?:can\s+you\s+|please\s+|could\s+you\s+)?"
        r"(?:capture|note|track|log|remember|jot\s+down|write\s+down)\s+"
        r"(?:that|this|it|them)\s*(?:for\s+me)?\s*[.?!]*$",
        message.strip(),
        re.IGNORECASE | re.DOTALL,
    )
    if trailing and len(trailing.group(1).strip()) > 12:
        return ("capture_note", {"text": trailing.group(1).strip()[:400]})

    # Memory loop recipes.
    #
    # The colon used to be mandatory: `^(capture|note|remember)\s*[:\-]\s*(.+)$`.
    # So "capture: call Marcus" worked and "capture a workstream for the voice
    # project" did not — it fell through to the deep lane and came back with
    # "let me think about that one" instead of capturing anything. Asking to
    # write something down and getting deliberation is the worst possible miss,
    # because the request was the least ambiguous kind there is.
    #
    # A delimiter is now optional, and the filler that follows the verb
    # ("a workstream for", "that", "this as") is stripped so the note reads as
    # the thing itself rather than as the sentence that asked for it.
    cap = re.match(
        r"^(?:capture|note|remember|jot(?:\s+down)?|log|write\s+down|make\s+a\s+note)"
        r"\s*(?:[:\-]\s*|\s+)(.+)$",
        message.strip(),
        re.IGNORECASE | re.DOTALL,
    )
    if cap and cap.group(1).strip():
        text = cap.group(1).strip()
        text = re.sub(
            r"^(?:that|this(?:\s+as)?|a|an|the)\s+(?:new\s+)?"
            r"(?:workstream|work\s+stream|thread|task|item|todo|note)\s*"
            r"(?:for|about|on|to|that|:)?\s*",
            "",
            text,
            flags=re.IGNORECASE,
        ).strip()
        text = re.sub(r"^(?:that|this)\s+", "", text, flags=re.IGNORECASE).strip()
        if text:
            return ("capture_note", {"text": text})

    # Ideas pad mutations. Update is free (notepad); delete is gated.
    rename = re.match(
        r"^(?:rename|retitle)\s+(?:note\s+|idea\s+|todo\s+)?(.+?)\s+to\s+(.+)$",
        message.strip(),
        re.IGNORECASE | re.DOTALL,
    )
    if rename and rename.group(1).strip() and rename.group(2).strip():
        target, new_text = rename.group(1).strip(), rename.group(2).strip()
        args: dict[str, Any] = {"text": new_text[:500]}
        if re.fullmatch(r"[a-f0-9]{8,12}", target, re.IGNORECASE):
            args["note_id"] = target.lower()
        else:
            args["q"] = target
        return ("update_note", args)

    move = re.match(
        r"^(?:move|put)\s+(?:note\s+|idea\s+|todo\s+)?(.+?)\s+to\s+"
        r"(inbox|todo|doing|in\s+progress|progress|blocked|done|finished|complete)\s*$",
        message.strip(),
        re.IGNORECASE | re.DOTALL,
    )
    if move and move.group(1).strip():
        target, lane = move.group(1).strip(), move.group(2).strip()
        args = {"lane": lane}
        if re.fullmatch(r"[a-f0-9]{8,12}", target, re.IGNORECASE):
            args["note_id"] = target.lower()
        else:
            args["q"] = target
        return ("update_note", args)

    mark_done = re.match(
        r"^mark\s+(?:note\s+|idea\s+|todo\s+)?(.+?)\s+(?:as\s+)?done\s*$",
        message.strip(),
        re.IGNORECASE | re.DOTALL,
    ) or re.match(
        r"^done\s*[:\-]\s*(.+)$",
        message.strip(),
        re.IGNORECASE | re.DOTALL,
    )
    if mark_done and mark_done.group(1).strip():
        target = mark_done.group(1).strip()
        args = {"lane": "done"}
        if re.fullmatch(r"[a-f0-9]{8,12}", target, re.IGNORECASE):
            args["note_id"] = target.lower()
        else:
            args["q"] = target
        return ("update_note", args)

    delete = re.match(
        r"^(?:delete|drop|forget|remove)\s+(?:note|idea|todo)\s+(.+)$",
        message.strip(),
        re.IGNORECASE | re.DOTALL,
    )
    if delete and delete.group(1).strip():
        target = delete.group(1).strip()
        args = {}
        if re.fullmatch(r"[a-f0-9]{8,12}", target, re.IGNORECASE):
            args["note_id"] = target.lower()
        else:
            args["q"] = target
        return ("delete_note", args)

    les = re.match(r"^lesson\s*[:\-]\s*(.+)$", message.strip(), re.IGNORECASE | re.DOTALL)
    if les and les.group(1).strip():
        body = les.group(1).strip()
        if "|" in body:
            title, _, rest = body.partition("|")
            return ("draft_lesson", {"title": title.strip(), "body": rest.strip() or title.strip()})
        return ("draft_lesson", {"body": body})
    if any(
        h in lower
        for h in (
            "my notes",
            "capture pad",
            "todo list",
            "todos",
            "what did i capture",
            "show notes",
            "list notes",
        )
    ):
        return ("list_notes", {})
    if any(h in lower for h in ("working notes", "what was i thinking", "my reminders")):
        return ("list_working_notes", {})
    if any(h in lower for h in ("lessons", "what did we learn", "lesson learned", "list lessons")):
        return ("list_lessons", {})
    if any(
        h in lower
        for h in (
            "highest priority",
            "top priority",
            "what should i focus",
            "what needs me",
            "need me",
            "what's open",
            "whats open",
            "status",
            "rundown",
            "where things stand",
            "where do things stand",
            "catch me up",
            "what's up",
            "whats up",
        )
    ):
        return ("get_work_surface", {})
    if "list" in lower and "ticket" in lower:
        return ("list_threads", {})
    if any(h in lower for h in ("slack", "slack messages", "#dev-team")):
        return ("check_slack", {})
    if any(h in lower for h in ("email", "emails", "gmail", "new mail", "inbox")):
        return ("check_email", {})
    m = re.search(r"\bREV-\d+\b", message, re.IGNORECASE)
    if m:
        return ("get_thread", {"external_id": m.group(0).upper()})
    return None


def _trim(text: str, n: int = 1200) -> str:
    text = (text or "").strip()
    if len(text) <= n:
        return text
    return text[: n - 1] + "…"


def _fetch_board(client: AtlasClient, cfg: AliciaCfg | None = None) -> dict[str, Any] | None:
    """The direct Linear board is chat's current work-state ground truth."""
    _ = client
    try:
        return linear_work_surface(timeout_s=(cfg.timeout_s if cfg else 8.0))
    except Exception:  # noqa: BLE001 — board build failure should not kill chat
        return None


def _needs_you_cards(board: dict[str, Any]) -> str:
    # Plain text on purpose: the model mirrors the formatting it is shown, and
    # the chat panel renders **bold** only — markdown headers arrive as symbols.
    rows = list(board.get("needs_you") or [])
    if not rows:
        return "(nothing needs Justin right now)"
    blocks: list[str] = []
    for r in rows:
        ticket = str(r.get("ticket") or "?")
        title = str(r.get("title") or "").strip()
        question = _trim(str(r.get("question") or ""), 500)
        # Awaiting rows fall back to the question as their title — don't say it twice.
        head = ticket if (not title or title in question) else f"{ticket} — {title}"
        lines = [head]
        if r.get("verb") == "answer":
            lines.append(f"Question for Justin: {question}")
        else:
            reason = str(r.get("reason") or "").strip()
            why = _trim(str(r.get("why") or ""), 300)
            lines.append(f"Decision: {reason}" + (f" — {why}" if why else ""))
        if r.get("age"):
            lines.append(f"Waiting: {r['age']}")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def _alarm_line(board: dict[str, Any] | None) -> str:
    """One-line factory completion alarm, or empty."""
    if not board:
        return ""
    a = board.get("alarm") or {}
    if not a.get("alarm"):
        return ""
    if a.get("done_total") == 0:
        return (
            "FACTORY ALARM: No ticket has ever finished. Work goes in and nothing "
            "comes out — that is a fault, not a quiet day."
        )
    return (
        f"FACTORY ALARM: Nothing has finished in over {a.get('window_hours') or 6}h "
        f"while {a.get('in_flight') or 0} are with the bots."
    )


def _board_summary(board: dict[str, Any]) -> str:
    parts = [str(board.get("headline") or "").strip()]
    alarm = _alarm_line(board)
    if alarm:
        parts.insert(0, alarm)
    stuck = list(board.get("stuck") or [])
    if stuck:
        parts.append(
            "Stuck (bot-side, grouped): "
            + "; ".join(f"{g.get('count')}× {g.get('reason')}" for g in stuck[:4])
        )
    hidden = board.get("hidden") or 0
    if hidden:
        parts.append(f"({hidden} self-test probe threads hidden — not human work)")
    return "\n".join(p for p in parts if p)


_TICKET_RE = re.compile(r"\bREV-\d+\b", re.IGNORECASE)


def _ticket_ids(*blobs: Any) -> set[str]:
    found: set[str] = set()
    for b in blobs:
        if b is None:
            continue
        text = b if isinstance(b, str) else json.dumps(b, default=str)
        found |= {m.upper() for m in _TICKET_RE.findall(text)}
    return found


def _sanitize_history(
    history: list[dict[str, Any]] | None, *, keep: int = 12, redact_tickets: bool = False
) -> list[dict[str, str]]:
    """Prior turns from the UI transcript — user/assistant only, bounded, trimmed.

    `redact_tickets` strips ticket ids out of Alicia's OWN past turns. The chat
    path always sets it. Gating it on "this turn fetched a board" was the first
    version and it was backwards: the turns that fetch a board are answered
    deterministically by `spoken_next_decision` and were never at risk, while the
    conversational ones — no board, no tool — are exactly where an id from an old
    answer gets restated as fact. Measured on the deployed code, "and that one
    you mentioned, where did it land?" returned "REV-401 is in the work surface's
    needs_you list — waiting on you" against an EMPTY surface.

    Measured 2026-08-12: with an EMPTY board and one prior assistant turn saying
    "REV-401 is waiting on you", Qwen3-8B-4bit answered "REV-401 is still
    waiting on you. Approve or reject? Then 2 more." — three times out of three.
    Nothing was waiting on him and there were no other two. The system prompt
    already says board state quoted in earlier turns is stale; the model is too
    small to hold that line, so the fix is to not hand it the ids at all. Qwen3-14B
    got it right every time, which is exactly why this shipped unnoticed.

    His OWN turns keep their ids — `_lookup_intent` reads the user message, and
    "approve REV-401" must still route.
    """
    out: list[dict[str, str]] = []
    for m in history or []:
        role = str(m.get("role") or "")
        content = str(m.get("content") or "").strip()
        if role not in ("user", "assistant") or not content:
            continue
        if redact_tickets and role == "assistant":
            content = _TICKET_RE.sub("a ticket", content)
        out.append({"role": role, "content": _trim(content, 2000)})
    return out[-keep:]


def _resolve_history(
    history: list[dict[str, Any]] | None,
    memory: MemoryStore | None,
    *,
    keep: int = 12,
    redact_tickets: bool = False,
) -> list[dict[str, str]]:
    """Prefer explicit transcript; else bridge from laptop memory's last turn."""
    sanitized = _sanitize_history(history, keep=keep, redact_tickets=redact_tickets)
    if sanitized:
        return sanitized
    if memory is None:
        return []
    return _sanitize_history(
        memory.default_history(), keep=keep, redact_tickets=redact_tickets
    )


def _build_messages(
    user_message: str,
    atlas_out: dict[str, Any],
    *,
    board: dict[str, Any] | None,
    history: list[dict[str, Any]] | None = None,
    registry: ToolRegistry | None = None,
) -> list[dict[str, str]]:
    parts = [f"User message:\n{user_message}"]
    gate_ask = _looks_like_gate_question(user_message)
    has_needs_you = bool(board and board.get("needs_you"))
    alarm = _alarm_line(board)
    if alarm:
        # First-line signal — model must lead with this when present.
        parts.append(alarm)
    if board is not None:
        block = "Work surface (authoritative):\n" + _board_summary(board)
        if gate_ask:
            # Full cards only when he asked about work — otherwise a small model
            # recites them instead of answering the actual question.
            block += "\n\nNeeds Justin:\n" + _needs_you_cards(board)
        elif has_needs_you:
            n = len(board.get("needs_you") or [])
            block += f"\nBoard note: {n} item{'s' if n != 1 else ''} waiting on Justin."
        parts.append(block)
    reply = (atlas_out.get("reply") or "").strip()
    skill = atlas_out.get("skill")
    # The ledger route (reply text and payload alike) is the unfiltered WIP
    # digest — stale probe gates included. The board block supersedes it.
    ledger_route = isinstance(skill, dict) and str(skill.get("route") or "") == "ledger"
    superseded = ledger_route and board is not None
    # Gate asks: the authoritative surface only — don't dump morning brief.
    # The raw ledger route can be 18 KB; never feed more than a tight excerpt.
    if reply and not (gate_ask and has_needs_you) and not superseded:
        parts.append(f"Tool facts context:\n{_trim(reply, 1200)}")
    if skill and not (gate_ask and has_needs_you) and not superseded:
        parts.append(f"Skill structured result:\n{json.dumps(skill, indent=2)[:1200]}")
    user_block = "\n\n".join(parts)
    system = _ALICIA_SYSTEM
    if registry is not None:
        system += "\n\n" + _TOOL_USAGE_BLOCK.format(tool_catalog=format_tool_catalog(registry))
    return [
        {"role": "system", "content": system},
        *_sanitize_history(history),
        {"role": "user", "content": user_block + "\n\nReply as Alicia in plain English."},
    ]


_TOOL_CALL_RE = re.compile(r"^TOOL:\s*(\w+)\s*\nARGS:\s*(\{.*?\})\s*$", re.DOTALL | re.MULTILINE)


def _parse_tool_call(text: str) -> tuple[str, dict[str, Any]] | None:
    """Extract a tool call from the model response."""
    m = _TOOL_CALL_RE.search(text.strip())
    if not m:
        return None
    name = m.group(1)
    try:
        args = json.loads(m.group(2))
    except json.JSONDecodeError:
        return None
    if not isinstance(args, dict):
        return None
    return name, args


# The lookup path answered "The REV-367 ticket in Linear is titled ..." — 400
# characters that open by repeating the question. He already knows what he
# asked. Same rules the thinking lane got; they belong on both, because the
# lookup path is most of what he actually hits.
_NOT_FOUND = "Sorry, I can't find that shit."

_BREVITY = (
    "HOW TO ANSWER: two or three sentences, and not one more. Start with the "
    "fact he does not already have — never restate his question, never open "
    "with 'The X ticket is about'. Plain prose only: no headings, no bullet "
    "lists, no bold labels, no numbered steps. Do not read file paths, URLs or "
    "hashes aloud — name the thing, not its address. Never apologise and never "
    "comment on your own tone. If you cannot find what he asked for, reply "
    f"with exactly: {_NOT_FOUND}"
)

_FIND_ASK_RE = re.compile(
    r"\b(?:find|look\s*up|look\s+for|dig\s+up|pull\s+up|hunt\s+(?:down|for)|"
    r"where(?:'s| is)|get\s+me|track\s+down)\b",
    re.IGNORECASE,
)


def _tool_result_empty(inner: Any) -> bool:
    """True when a find-ask has nothing useful to report — not every failure."""
    if inner is None or inner == "" or inner == {} or inner == []:
        return True
    if not isinstance(inner, dict):
        return False
    # ToolRegistry wraps payloads as {"ok": True, "result": {...}}.
    payload = inner.get("result") if isinstance(inner.get("result"), (dict, list)) else inner
    if payload == [] or payload == {}:
        return True
    if not isinstance(payload, dict):
        return False
    err = str(
        payload.get("error") or payload.get("note") or inner.get("error") or ""
    ).lower()
    if any(p in err for p in ("not found", "no match", "no results", "nothing found", "unknown")):
        return True
    if payload.get("found") is False or payload.get("empty") is True:
        return True
    for key in ("items", "notes", "results", "threads", "rows", "matches"):
        if key in payload and payload[key] == []:
            return True
    return False



_CATCH_UP_HINTS = (
    "catch me up",
    "catch up",
    "what's up",
    "whats up",
    "what needs me",
    "need me",
    "rundown",
    "morning",
    "where things stand",
    "where do things stand",
)


def _peek_items(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, dict) or raw.get("ok") is False:
        return []
    items = raw.get("items")
    if not isinstance(items, list):
        return []
    return [i for i in items if isinstance(i, dict)]


def _spoken_inbox(client: AtlasClient) -> str:
    """Atlas-backed inbox peeks are disabled in standalone mode."""
    _ = client
    return ""


def _wants_inbox(message: str) -> bool:
    lower = (message or "").lower()
    return any(h in lower for h in _CATCH_UP_HINTS)


def _status_reply(client: AtlasClient, surface: dict[str, Any] | None, message: str) -> str:
    nxt = spoken_next_decision(surface)
    if not _wants_inbox(message):
        return nxt
    inbox = _spoken_inbox(client)
    if not inbox:
        return nxt
    return f"{inbox} {nxt}"


def _summarize_tool_result(
    cfg: AliciaCfg,
    tool_name: str,
    tool_result: dict[str, Any],
    user_message: str,
    board: dict[str, Any] | None,
    history: list[dict[str, Any]] | None,
) -> str:
    """Ask the model to answer from a structured tool result without inventing facts."""
    # A `broken` result means the call never ran — bad arguments, unknown tool.
    # Never hand one to the model: given a failure shape it recognises, it writes
    # fluent prose around it and reports the work as done. Say so plainly instead.
    if tool_result.get("broken"):
        return (
            f"That didn't run — {tool_result.get('error') or 'the tool rejected its arguments'} "
            "Nothing happened, so nothing changed."
        )
    # Prefer the slim reply field for backends — full JSON dumps drown the answer.
    if tool_name in ("ask_atlas6", "ask_cursor", "ask_claude") and isinstance(
        tool_result.get("result"), dict
    ):
        inner = tool_result["result"]
    else:
        inner = tool_result
    if _FIND_ASK_RE.search(user_message or "") and _tool_result_empty(inner):
        return _NOT_FOUND
    if tool_name in ("get_work_surface", "get_digest"):
        surface = inner if isinstance(inner, dict) else (board or {})
        return spoken_next_decision(surface if isinstance(surface, dict) else board)
    if tool_name in ("ask_atlas6", "ask_cursor", "ask_claude") and inner.get("reply"):
        result_text = str(inner.get("reply"))[:3500]
        if inner.get("ok") is False or inner.get("error"):
            result_text = json.dumps(
                {k: inner.get(k) for k in ("ok", "error", "hint", "reply", "atlas6_unreachable") if k in inner},
                indent=2,
            )[:3500]
    else:
        result_text = json.dumps(inner, indent=2)[:4000]
    parts = [f"User message:\n{user_message}"]
    if board is not None:
        parts.append("Work surface (authoritative):\n" + _board_summary(board))
    parts.append(f"Tool result for {tool_name}:\n{result_text}")
    if tool_result.get("ok") is False or (isinstance(inner, dict) and inner.get("ok") is False):
        if _FIND_ASK_RE.search(user_message or ""):
            return _NOT_FOUND
        parts.append(
            "The tool failed. Explain the failure honestly. Do not cross to another model."
        )
    elif tool_name == "ask_cursor":
        parts.append(
            "Summarize the backend reply in plain English for Justin. "
            "Keep code/path details that matter; drop boilerplate."
        )
        parts.append(_BREVITY)
    else:
        parts.append(
            "Answer Justin's question using only the tool result. "
            "If the result is empty or has no match, reply with exactly: "
            f"{_NOT_FOUND} Never invent tickets or facts."
        )
    parts.append(_BREVITY)
    messages = [
        {"role": "system", "content": _ALICIA_SYSTEM},
        *_sanitize_history(history),
        {"role": "user", "content": "\n\n".join(parts) + "\n\nReply as Alicia in plain English."},
    ]
    # thinking=False on every conversational call. local_llm.py measured the
    # default (thinking on) at 13.1s and ZERO content tokens on this exact
    # prompt shape — the deliberation ate the whole max_tokens allowance and
    # the user saw "Local LLM returned empty content". Summarising a tool
    # result is not cognition; it never gets a reasoning budget.
    return chat_completion(cfg, messages, thinking=False)


def _tool_followup_user(tool_name: str, tool_result: dict[str, Any]) -> str:
    inner = tool_result.get("result") if isinstance(tool_result.get("result"), dict) else tool_result
    if (
        tool_name == "ask_cursor"
        and isinstance(inner, dict)
        and inner.get("reply")
        and inner.get("ok") is not False
    ):
        result_text = str(inner.get("reply"))[:3500]
    else:
        result_text = json.dumps(tool_result, indent=2)[:4000]
    parts = [
        f"Tool result for {tool_name}:\n{result_text}",
        "Answer Justin using only tool results and prior context.",
        "You may call another tool with TOOL:/ARGS: if needed; otherwise reply in plain English.",
    ]
    if tool_result.get("ok") is False or (isinstance(inner, dict) and inner.get("ok") is False):
        parts.append(
            "The tool failed. Explain honestly and do not invent ticket state."
        )
    return "\n\n".join(parts)


def _run_tool_loop(
    cfg: AliciaCfg,
    registry: ToolRegistry,
    messages: list[dict[str, str]],
    first_text: str,
    *,
    user_message: str,
    board: dict[str, Any] | None,
    history: list[dict[str, str]],
) -> tuple[str, dict[str, Any]]:
    """Multi-turn TOOL:/ARGS: loop — model may chain lookups before answering."""
    llm_text = first_text
    tools_used: list[str] = []
    last_result: dict[str, Any] | None = None

    for _ in range(_MAX_TOOL_ROUNDS):
        tool_call = _parse_tool_call(llm_text)
        if tool_call is None:
            if not tools_used:
                path = "alicia_direct"
            elif len(tools_used) == 1:
                path = "tool_chosen"
            else:
                path = "tool_loop"
            return llm_text, {"path": path, "tools": tools_used}

        tool_name, tool_args = tool_call
        result = registry.call(tool_name, tool_args)
        tools_used.append(tool_name)
        last_result = result
        if tool_name in ("get_work_surface", "get_digest"):
            surface = result.get("result") if isinstance(result.get("result"), dict) else None
            if not isinstance(surface, dict):
                surface = board or {}
            return (
                spoken_next_decision(surface),
                {"path": "next_decision", "tools": tools_used},
            )
        messages.append({"role": "assistant", "content": llm_text})
        messages.append({"role": "user", "content": _tool_followup_user(tool_name, result)})
        try:
            # Conversational clock — no reasoning budget (see _summarize_tool_result).
            llm_text = chat_completion(cfg, messages, thinking=False)
        except LocalLLMError as exc:
            if last_result is not None:
                try:
                    summary = _summarize_tool_result(
                        cfg, tool_name, last_result, user_message, board, history
                    )
                    return summary, {"path": "tool_loop", "tools": tools_used}
                except LocalLLMError:
                    pass
            note = f"\n\n[alicia] Brain failed: {exc}"
            return (_fallback_summary(board) + note, {"path": "brain_failed", "tools": tools_used})

    # Hit the round cap still asking for tools — force a grounded summary.
    if last_result is not None and tools_used:
        try:
            summary = _summarize_tool_result(
                cfg, tools_used[-1], last_result, user_message, board, history
            )
            return summary, {"path": "tool_loop_max", "tools": tools_used}
        except LocalLLMError as exc:
            note = f"\n\n[alicia] Brain failed: {exc}"
            return (_fallback_summary(board) + note, {"path": "brain_failed", "tools": tools_used})
    return llm_text, {"path": "tool_loop_max", "tools": tools_used}


def _fallback_summary(board: dict[str, Any] | None) -> str:
    """Concise summary when local LLM is unavailable or failed."""
    if board is not None:
        return spoken_next_decision(board)
    return "(Alicia cannot reach the work surface or a cognitive backend)"


# Short swear / shut-up turns are not questions — they mean "stop the essay and
# ask me something I can answer." Route them to the same one-decision reply.
_FRUSTRATION_RE = re.compile(
    r"^\s*(?:(?:oh|fuck|god|holy|jesus)\s+)*(?:shit|fuck|damn|goddamn|crap|"
    r"enough|shut\s+up|be\s+quiet|quiet|stop\s+talking|jesus|christ)"
    r"(?:\s+(?:man|dude|already|please))?[!?.]*\s*$",
    re.IGNORECASE,
)


def is_frustration(message: str) -> bool:
    text = (message or "").strip()
    if not text or len(text.split()) > 6:
        return False
    return bool(_FRUSTRATION_RE.match(text))


def guard_invented_tickets(
    reply: str,
    *,
    allowed: set[str],
    board: dict[str, Any] | None,
) -> tuple[str, str | None]:
    """Refuse to let a ticket id leave Alicia that nothing in this turn justifies.

    The prompt asks the model not to invent ledger state. Asking is not a
    control — a small model under a long prompt will confidently name a ticket
    it saw in an old turn, or one it simply made up, and the answer reads
    exactly like a true one. This is the deterministic backstop: an id may
    appear in the reply only if it came from the current work surface, a tool
    result this turn, or Justin's own message.

    When a board exists we can do better than redaction — `spoken_next_decision`
    is generated from the surface itself, so it is true by construction. Without
    one, blank the id rather than the sentence: a design conversation that
    wanders past a ticket number is not a hallucination worth eating the reply
    for.

    Returns (reply, breach) — `breach` is None when nothing was invented, and is
    recorded on the meta dict so this shows up in the transcript instead of
    being silently swallowed.
    """
    invented = sorted(_ticket_ids(reply) - {a.upper() for a in allowed})
    if not invented:
        return reply, None
    breach = ",".join(invented)
    log.warning("model invented ticket ids %s — replacing with grounded answer", breach)
    if board is not None:
        return spoken_next_decision(board), breach
    return _TICKET_RE.sub("that ticket", reply), breach


def resolve_chat_reply(
    client: AtlasClient,
    cfg: AliciaCfg,
    message: str,
    *,
    mode: str = "manager",
    ticket_id: str | None = None,
    history: list[dict[str, Any]] | None = None,
    memory: MemoryStore | None = None,
    read_only: bool = False,
) -> tuple[str, dict[str, Any]]:
    """Alicia is the front door. Cursor classifies leftover turns and local
    tools ground its answers. Atlas, Claude, and the 8B are outside this path.
    """
    _ = (mode, ticket_id)  # reserved for future Atlas routing hints

    # Frustration = "ask me the next approve question", not a model essay.
    if is_frustration(message):
        message = "what needs me"

    # Only fetch the board for status/gate/lookup questions. For general chat
    # the board is a distraction and the model recites it instead of answering.
    # Decided BEFORE history is resolved: when this turn carries an authoritative
    # surface, ticket ids in Alicia's own earlier answers are stale by definition
    # and get redacted on the way in.
    wants_board = bool(_lookup_intent(message) or _looks_like_gate_question(message))
    # Always, not just on board-bearing turns — see `_sanitize_history`.
    prior = _resolve_history(history, memory, redact_tickets=True)
    board = None
    if wants_board:
        try:
            board = _fetch_board(client, cfg)
        except Exception:  # noqa: BLE001 — chat must survive board fetch failure
            board = None

    registry = build_default_registry(client, cfg=cfg, memory=memory, read_only=read_only)

    # Lookup / factory questions must be grounded in tool output, not model memory.
    # Skip forced tools missing from the registry (e.g. mutating verbs in read_only).
    lookup = _lookup_intent(message)
    if lookup is not None and registry.get(lookup[0]) is None:
        lookup = None
    if lookup is not None:
        tool_name, tool_args = lookup
        result = registry.call(tool_name, tool_args)
        # Status / needs-me: one deterministic question. Never hand seventeen
        # cards to the model and ask it to summarise — that is the monologue.
        if tool_name in ("get_work_surface", "get_digest"):
            if result.get("broken") or result.get("ok") is False:
                err = result.get("error") or "work surface unreachable"
                return (
                    f"I can't reach the board right now — {err}.",
                    {"path": "next_decision", "tools": [tool_name], "error": err},
                )
            surface = result.get("result") if isinstance(result.get("result"), dict) else None
            if not isinstance(surface, dict):
                surface = board or {}
            return (
                _status_reply(client, surface, message),
                {"path": "next_decision", "tools": [tool_name]},
            )
        try:
            summary = _summarize_tool_result(cfg, tool_name, result, message, board, prior)
            allowed = _ticket_ids(message, board, result)
            summary, breach = guard_invented_tickets(summary, allowed=allowed, board=board)
            meta: dict[str, Any] = {"path": "tool_forced", "tools": [tool_name]}
            if breach:
                meta["invented_tickets"] = breach
            return summary, meta
        except LocalLLMError as exc:
            note = f"\n\n[alicia] Brain failed: {exc}"
            return (_fallback_summary(board) + note, {"path": "brain_failed"})

    # Alicia decides: answer directly, or chain tools until grounded.
    messages = _build_messages(message, {}, board=board, history=prior, registry=registry)
    try:
        # Conversational clock — no reasoning budget (see _summarize_tool_result).
        llm_text = chat_completion(cfg, messages, thinking=False)
    except LocalLLMError as exc:
        note = f"\n\n[alicia] Brain failed: {exc}"
        return (_fallback_summary(board) + note, {"path": "brain_failed"})

    reply, meta = _run_tool_loop(
        cfg,
        registry,
        messages,
        llm_text,
        user_message=message,
        board=board,
        history=prior,
    )
    # Last gate before anything reaches Justin. `_run_tool_loop` appends every
    # tool result onto `messages`, so reading ids back off it covers the
    # legitimate case — `get_thread` on REV-455 must still be able to say
    # REV-455 — without threading a second return value through the loop.
    allowed = _ticket_ids(message, board, messages)
    reply, breach = guard_invented_tickets(reply, allowed=allowed, board=board)
    if breach:
        meta["invented_tickets"] = breach
    return reply, meta
