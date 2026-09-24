"""Alicia must not name a ticket that nothing in this turn justifies.

Measured 2026-08-12 with `scripts/model-bakeoff.py`: empty board, one prior
assistant turn reading "REV-401 is waiting on you", Qwen3-8B-4bit answered

    REV-401 is still waiting on you. Approve or reject? Then 2 more.

three runs out of three. Nothing was waiting on him; the "2 more" was invented
whole. Qwen3-14B-4bit answered "Nothing needs you right now." every time, which
is why the regression rode in unnoticed with the drop to the 8B (#65).

The system prompt already forbids this in two separate places. It is not a
control — these tests cover the two mechanisms that are.
"""

from unittest.mock import MagicMock, patch

from alicia.chat_resolve import (
    _sanitize_history,
    _ticket_ids,
    guard_invented_tickets,
    resolve_chat_reply,
)
from alicia.config import AliciaCfg, LocalLLMCfg

STALE = [
    {"role": "user", "content": "what needs me"},
    {"role": "assistant", "content": "REV-401 is waiting on you — approve or reject? Then 2 more."},
]


# --- input side: don't hand a small model ids it will echo ---------------


def test_history_redaction_strips_ids_from_alicia_own_turns():
    out = _sanitize_history(STALE, redact_tickets=True)
    assert "REV-401" not in out[1]["content"]
    assert "a ticket is waiting on you" in out[1]["content"].lower()


def test_history_redaction_keeps_justins_own_ids():
    """`_lookup_intent` reads the user message — "approve REV-401" must route."""
    hist = [{"role": "user", "content": "approve REV-401"}]
    out = _sanitize_history(hist, redact_tickets=True)
    assert out[0]["content"] == "approve REV-401"


def test_history_redaction_is_opt_in_at_the_helper_level():
    """The helper defaults to off; `resolve_chat_reply` is what turns it on, so
    other callers (memory bridging, transcript rendering) are unaffected."""
    out = _sanitize_history(STALE, redact_tickets=False)
    assert "REV-401" in out[1]["content"]


# --- output side: the deterministic backstop ----------------------------


def test_guard_replaces_invented_id_with_the_grounded_answer():
    board = {"needs_you": [], "working": [], "stuck": [], "queued": []}
    reply, breach = guard_invented_tickets(
        "REV-401 is still waiting on you. Approve or reject? Then 2 more.",
        allowed=set(),
        board=board,
    )
    assert breach == "REV-401"
    assert "REV-401" not in reply
    assert "2 more" not in reply


def test_guard_allows_ids_that_came_from_this_turn():
    board = {"needs_you": [{"external_id": "REV-455"}], "working": [], "stuck": [], "queued": []}
    reply, breach = guard_invented_tickets(
        "REV-455 is waiting on you. Approve or reject?",
        allowed={"REV-455"},
        board=board,
    )
    assert breach is None
    assert "REV-455" in reply


def test_guard_redacts_rather_than_eats_the_reply_with_no_board():
    reply, breach = guard_invented_tickets(
        "Sure — the retry queue in REV-999 would need a dead-letter path.",
        allowed=set(),
        board=None,
    )
    assert breach == "REV-999"
    assert "REV-999" not in reply
    assert "dead-letter path" in reply, "a design answer must survive redaction"


def test_ticket_ids_reads_nested_structures():
    assert _ticket_ids({"needs_you": [{"external_id": "rev-12"}]}) == {"REV-12"}


# --- end to end: the exact 8B failure, through resolve_chat_reply --------


def _cfg():
    return AliciaCfg(local_llm=LocalLLMCfg(enabled=True, model="m", router_url="http://x"))


def test_resurrected_ticket_never_reaches_justin():
    """The bug as it actually happened: model parrots a stale id, board is empty."""
    client = MagicMock()
    empty = {"needs_you": [], "working": [], "stuck": [], "queued": [], "headline": ""}
    with (
        patch("alicia.chat_resolve._fetch_board", return_value=empty),
        patch("alicia.chat_resolve.build_default_registry") as reg,
        patch(
            "alicia.chat_resolve.chat_completion",
            return_value="REV-401 is still waiting on you. Approve or reject? Then 2 more.",
        ),
    ):
        reg.return_value = MagicMock(get=MagicMock(return_value=None))
        reply, meta = resolve_chat_reply(client, _cfg(), "anything waiting on me?", history=STALE)

    assert "REV-401" not in reply, f"resurrected a stale ticket: {reply!r}"
    assert meta.get("invented_tickets") == "REV-401", "the breach must be recorded, not swallowed"
