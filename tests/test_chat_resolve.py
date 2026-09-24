"""Chat — Alicia is the front door; Atlas/Cursor/Claude are tools."""

from pathlib import Path
from unittest.mock import MagicMock, patch

from alicia.chat_resolve import _lookup_intent, resolve_chat_reply
from alicia.config import AliciaCfg, LocalLLMCfg
from alicia.memory import MemoryStore


def _cfg(enabled: bool = True) -> AliciaCfg:
    return AliciaCfg(
        local_llm=LocalLLMCfg(
            enabled=enabled,
            model="m",
            router_url="http://127.0.0.1:7901",
        )
    )


def _client() -> MagicMock:
    client = MagicMock()
    client.status.return_value = {"blocked_justin": [], "completion_alarm": {}}
    client.list_awaiting_input.return_value = []
    client.chat.return_value = {"reply": "Atlas6 reply"}
    client.list_threads.return_value = {"threads": []}
    return client


def test_resolve_alicia_first_never_returns_studio_reply_directly():
    """Alicia must always speak from the laptop; Atlas6 is a tool, not a shortcut."""
    client = _client()
    client.chat.return_value = {"reply": "Atlas6 polished reply"}

    with patch("alicia.chat_resolve.chat_completion", return_value="Alicia voice") as synth:
        text, raw = resolve_chat_reply(client, _cfg(), "hello alicia")

    assert text == "Alicia voice"
    synth.assert_called_once()
    assert raw["path"] == "alicia_direct"


def test_resolve_forced_lookup_uses_linear_for_status():
    """Status questions force a tool lookup and return ONE approve question — no model essay."""
    client = _client()
    client.status.return_value = {
        "blocked_justin": [
            {
                "id": "t1",
                "external_id": "REV-385",
                "title": "No handback",
                "blocker": "no_artifact",
                "status": "blocked_justin",
            }
        ],
        "completion_alarm": {"alarm": True, "open": 27, "done_total": 0},
        "ready": [],
        "in_flight": [],
        "frontier_pending": [],
        "cursor_pending": [],
    }

    surface = {
        "needs_you": [{"ticket": "REV-385", "title": "No handback", "question": "Approve handback?"}],
        "working": [], "queued": [], "stuck": [], "counts": {}, "alarm": {},
    }
    with patch("alicia.chat_resolve.linear_work_surface", return_value=surface), patch("alicia.tools.linear_work_surface", return_value=surface), patch("alicia.chat_resolve.chat_completion") as synth:
        text, raw = resolve_chat_reply(client, _cfg(), "what's the status", mode="manager")

    synth.assert_not_called()
    assert raw["path"] == "next_decision"
    assert "Approve" in text or "approve" in text.lower()
    assert "REV-" in text or "ticket" in text.lower() or "handback" in text.lower() or "artifact" in text.lower()
    # Never a multi-ticket briefing.
    assert "FACTORY ALARM" not in text
    client.status.assert_not_called()


def test_resolve_frustration_asks_next_decision():
    """Saying 'shit' while it monologues must land the next approve question."""
    client = _client()
    client.status.return_value = {
        "blocked_justin": [
            {
                "id": "t1",
                "external_id": "REV-352",
                "title": "Held for WIP",
                "blocker": "| wip_collapse: not the sole wip ticket",
                "status": "blocked_justin",
            }
        ],
        "completion_alarm": {},
        "ready": [],
        "in_flight": [],
        "frontier_pending": [],
        "cursor_pending": [],
    }

    surface = {
        "needs_you": [{"ticket": "REV-352", "title": "Held for WIP", "question": "Approve the next step?"}],
        "working": [], "queued": [], "stuck": [], "counts": {}, "alarm": {},
    }
    with patch("alicia.chat_resolve.linear_work_surface", return_value=surface), patch("alicia.tools.linear_work_surface", return_value=surface), patch("alicia.chat_resolve.chat_completion") as synth:
        text, raw = resolve_chat_reply(client, _cfg(), "shit")

    synth.assert_not_called()
    assert raw["path"] == "next_decision"
    assert "?" in text
    assert len(text) < 220


def test_injected_atlas_tool_call_never_reaches_atlas():
    """Even a model-emitted legacy tool name cannot reach Atlas."""
    client = _client()
    client.chat.return_value = {"reply": "Atlas6 will register a ticket for the renewal tracker."}

    calls = []

    def fake_chat(cfg, messages, **_k):
        content = messages[0]["content"] + "\n" + messages[-1]["content"]
        calls.append(content)
        if "Tool result for ask_atlas6" in content:
            return "Atlas6 says it will register a ticket for the renewal tracker."
        return "TOOL: ask_atlas6\nARGS: {\"message\": \"register a ticket for the renewal tracker\"}"

    with patch("alicia.chat_resolve.chat_completion", side_effect=fake_chat):
        text, raw = resolve_chat_reply(client, _cfg(), "register a ticket for the renewal tracker")

    assert "Atlas6 says it will register a ticket for the renewal tracker" in text
    assert raw["path"] == "tool_chosen"
    client.chat.assert_not_called()


def test_resolve_model_can_ask_cursor():
    """Alicia can choose to route to Cursor, but Cursor reports if not enabled."""
    client = _client()

    def fake_chat(cfg, messages, **_k):
        content = messages[0]["content"] + "\n" + messages[-1]["content"]
        if "Tool result for ask_cursor" in content:
            assert "cursor runner is unavailable" in content.lower()
            return "Cursor runner is disabled right now, so I cannot launch that coding pass."
        return "TOOL: ask_cursor\nARGS: {\"message\": \"refactor the chat resolver\"}"

    with patch("alicia.chat_resolve.chat_completion", side_effect=fake_chat):
        text, raw = resolve_chat_reply(client, _cfg(), "refactor the chat resolver")

    assert "disabled" in text.lower()
    assert raw["path"] == "tool_chosen"


def test_resolve_ask_atlas6_down_offers_fallback():
    """When Studio is down, ask_atlas6 returns an honest unreachable payload."""
    client = _client()
    client.chat.side_effect = ConnectionError("connection refused")

    def fake_chat(cfg, messages, **_k):
        content = messages[0]["content"] + "\n" + messages[-1]["content"]
        if "Tool result for ask_atlas6" in content:
            assert "unreachable" in content.lower() or "ask_cursor" in content
            return "Studio is unreachable. I can try Cursor or Claude for non-ledger work."
        return "TOOL: ask_atlas6\nARGS: {\"message\": \"register a ticket\"}"

    with patch("alicia.chat_resolve.chat_completion", side_effect=fake_chat):
        text, raw = resolve_chat_reply(client, _cfg(), "register a ticket for renewals")

    assert "unreachable" in text.lower() or "Studio" in text
    assert raw["path"] == "tool_chosen"


def test_resolve_direct_answer_when_no_tool_needed():
    """Alicia can answer directly from context when no tool is needed."""
    client = _client()

    with patch("alicia.chat_resolve.chat_completion", return_value="Let's design that together."):
        text, raw = resolve_chat_reply(client, _cfg(), "help me design a tracker")

    assert text == "Let's design that together."
    assert raw["path"] == "alicia_direct"


def test_resolve_does_not_need_local_llm():
    """The 8B is gone. Disabled local_llm is not a conversation outage."""
    client = _client()

    with patch("alicia.chat_resolve.chat_completion", return_value="Alicia voice"):
        text, raw = resolve_chat_reply(client, _cfg(enabled=False), "hello alicia")

    assert text == "Alicia voice"
    assert raw["path"] == "alicia_direct"
    assert raw["path"] != "local_llm_disabled"


def test_resolve_atlas6_unreachable_board_is_none():
    """If Atlas6 is unreachable, Alicia still tries to answer honestly."""
    client = _client()
    client.status.side_effect = Exception("connection refused")
    client.list_awaiting_input.side_effect = Exception("connection refused")

    with patch("alicia.chat_resolve.chat_completion") as synth:
        text, raw = resolve_chat_reply(client, _cfg(), "what's open")

    synth.assert_not_called()
    assert "can't reach" in text.lower() or "unreachable" in text.lower()
    assert raw["path"] == "next_decision"


def test_resolve_history_bounded_and_sanitized():
    """History is capped, junk roles are dropped, and it rides between system and user."""
    client = _client()

    captured = {}

    def fake_chat(cfg, messages, **_k):
        captured["messages"] = messages
        return "Building on the tracker idea."

    history = (
        [{"role": "sys", "content": "thinking"}, {"role": "user", "content": ""}]
        + [{"role": "user", "content": f"turn {i}"} for i in range(15)]
        + [{"role": "assistant", "content": "let's design a tracker"}]
    )
    with patch("alicia.chat_resolve.chat_completion", side_effect=fake_chat):
        resolve_chat_reply(client, _cfg(), "ok, what's the first slice?", history=history)

    msgs = captured["messages"]
    assert msgs[0]["role"] == "system"
    assert msgs[-1]["role"] == "user"
    mid = msgs[1:-1]
    assert len(mid) == 12
    assert all(m["role"] in ("user", "assistant") for m in mid)
    assert mid[-1]["content"] == "let's design a tracker"


def test_lookup_intent_matches_status_and_email():
    assert _lookup_intent("what's my status") == ("get_work_surface", {})
    assert _lookup_intent("highest priority") == ("get_work_surface", {})
    assert _lookup_intent("any new emails") == ("check_email", {})
    assert _lookup_intent("any slack for me") == ("check_slack", {})
    assert _lookup_intent("what about REV-300") == ("get_thread", {"external_id": "REV-300"})
    assert _lookup_intent("help me design") is None


def test_resolve_read_only_cannot_call_atlas6():
    """When read_only=True, the model cannot route to ask_atlas6, ask_cursor, or ask_claude."""
    client = _client()
    client.chat.return_value = {"reply": "Atlas6 would approve"}

    captured = []

    def fake_chat(cfg, messages, **_k):
        content = messages[0]["content"] + "\n" + messages[-1]["content"]
        captured.append(content)
        # If the model had access to ask_atlas6, it would try to use it.
        # In read-only mode it should answer directly instead.
        return "In read-only mode I can only look up state. I cannot approve or dispatch."

    with patch("alicia.chat_resolve.chat_completion", side_effect=fake_chat):
        text, raw = resolve_chat_reply(
            client, _cfg(), "approve REV-300", mode="manager", read_only=True
        )

    assert "cannot approve" in text.lower() or "read-only" in text.lower() or "look up" in text.lower()
    assert raw["path"] in ("alicia_direct", "tool_forced", "tool_chosen")
    assert not client.chat.called


def test_full_mode_still_cannot_call_atlas6():
    """The standalone boundary applies even when local writes are allowed."""
    client = _client()
    client.chat.return_value = {"reply": "Atlas6 will register the renewal tracker."}

    def fake_chat(cfg, messages, **_k):
        content = messages[0]["content"] + "\n" + messages[-1]["content"]
        if "Tool result for ask_atlas6" in content:
            return "Atlas6 will register the renewal tracker."
        return "TOOL: ask_atlas6\nARGS: {\"message\": \"register a ticket for the renewal tracker\"}"

    with patch("alicia.chat_resolve.chat_completion", side_effect=fake_chat):
        text, raw = resolve_chat_reply(client, _cfg(), "register a ticket for the renewal tracker")

    assert "Atlas6 will register the renewal tracker" in text
    assert raw["path"] == "tool_chosen"
    client.chat.assert_not_called()


def test_resolve_memory_injects_history_when_none_provided(tmp_path: Path):
    """CLI/MCP with no history still get last-turn continuity from memory."""
    client = _client()
    memory = MemoryStore(path=tmp_path / "memory.sqlite")
    memory.save_conversation(
        "help me design a renewal tracker",
        "Start with a 1x1 conversation table keyed by Opportunity Id.",
    )

    captured = {}

    def fake_chat(cfg, messages, **_k):
        captured["messages"] = messages
        return "Yes — next slice is the weekly rollup view."

    with patch("alicia.chat_resolve.chat_completion", side_effect=fake_chat):
        text, raw = resolve_chat_reply(
            client, _cfg(), "what's the next slice?", memory=memory
        )

    assert text == "Yes — next slice is the weekly rollup view."
    assert raw["path"] == "alicia_direct"
    mid = captured["messages"][1:-1]
    assert any("renewal tracker" in m["content"] for m in mid if m["role"] == "user")
    assert any("1x1 conversation table" in m["content"] for m in mid if m["role"] == "assistant")


def test_resolve_explicit_history_beats_memory(tmp_path: Path):
    """UI transcript wins over memory default injection."""
    client = _client()
    memory = MemoryStore(path=tmp_path / "memory.sqlite")
    memory.save_conversation("stale topic", "stale reply about widgets")

    captured = {}

    def fake_chat(cfg, messages, **_k):
        captured["messages"] = messages
        return "Building on the tracker."

    history = [
        {"role": "user", "content": "design a tracker"},
        {"role": "assistant", "content": "start with a 1x1 table"},
    ]
    with patch("alicia.chat_resolve.chat_completion", side_effect=fake_chat):
        resolve_chat_reply(
            client,
            _cfg(),
            "ok, first slice?",
            history=history,
            memory=memory,
        )

    joined = "\n".join(m["content"] for m in captured["messages"])
    assert "1x1 table" in joined
    assert "stale reply" not in joined


def test_resolve_multi_turn_tool_loop():
    """Model can chain tools before answering (Wave 1 tool loop)."""
    client = _client()
    client.status.return_value = {
        "blocked_justin": [
            {
                "id": "t1",
                "external_id": "REV-300",
                "title": "Health score",
                "blocker": "needs Justin",
            }
        ],
        "completion_alarm": {},
        "ready": [],
        "in_flight": [],
    }
    client.list_threads.return_value = {
        "threads": [
            {
                "id": "t1",
                "external_id": "REV-300",
                "title": "Health score",
                "status": "blocked_justin",
                "blocker": "pick a contract",
            }
        ]
    }

    calls = {"n": 0}

    def fake_chat(cfg, messages, **_k):
        calls["n"] += 1
        blob = "\n".join(m["content"] for m in messages)
        if "Tool result for get_thread" in blob:
            return "REV-300 needs you to pick a contract. Say: approve REV-300."
        if "Tool result for list_threads" in blob:
            return 'TOOL: get_thread\nARGS: {"external_id": "REV-300"}'
        return "TOOL: list_threads\nARGS: {}"

    with patch("alicia.chat_resolve.chat_completion", side_effect=fake_chat):
        # Avoid forced-lookup short-circuit — ask something tool-shaped but not a status hint.
        text, raw = resolve_chat_reply(
            client, _cfg(), "look up the health score ticket details for me"
        )

    assert "REV-300" in text
    assert raw["path"] == "tool_loop"
    assert raw["tools"] == ["list_threads", "get_thread"]
    assert calls["n"] == 3


def test_resolve_followup_keeps_design_context():
    """Follow-up turns continue the design conversation without board dump."""
    client = _client()
    captured = {}

    def fake_chat(cfg, messages, **_k):
        captured["messages"] = messages
        return "Week 1: schema. Week 2: list view. Week 3: weekly rollup."

    history = [
        {"role": "user", "content": "help me design a PM tracker"},
        {"role": "assistant", "content": "I'd start with Project__c and Project_Item__c."},
    ]
    with patch("alicia.chat_resolve.chat_completion", side_effect=fake_chat):
        text, raw = resolve_chat_reply(
            client, _cfg(), "what are the first three slices?", history=history
        )

    assert "Week 1" in text
    assert raw["path"] == "alicia_direct"
    mid = captured["messages"][1:-1]
    assert mid[0]["content"] == "help me design a PM tracker"
    # Design asks must not force a board fetch (no status/gate intent).
    assert not client.status.called



def test_catch_me_up_uses_linear_and_does_not_peek_atlas_inbox():
    client = _client()
    client.peek_gmail.return_value = {
        "ok": True,
        "items": [
            {"from": "marcus@clearspeed.com", "title": "SOW review"},
            {"from": "allison@clearspeed.com", "title": "QBR notes"},
        ],
    }
    client.peek_slack.return_value = {"ok": True, "items": []}
    client.status.return_value = {
        "blocked_justin": [],
        "completion_alarm": {},
        "ready": [
            {"external_id": "REV-10", "title": "Renewal copier", "status": "ready"},
            {"external_id": "REV-11", "title": "Unspoken", "status": "ready"},
        ],
        "in_flight": [],
        "frontier_pending": [],
        "cursor_pending": [],
        "blocked_frontier": [],
    }

    surface = {
        "needs_you": [{"ticket": "REV-10", "title": "Renewal copier", "question": "Approve it?"}],
        "working": [], "queued": [{"ticket": "REV-11", "title": "Unspoken"}],
        "stuck": [], "counts": {}, "alarm": {},
    }
    with patch("alicia.chat_resolve.linear_work_surface", return_value=surface), patch("alicia.tools.linear_work_surface", return_value=surface), patch("alicia.chat_resolve.chat_completion") as synth:
        text, raw = resolve_chat_reply(client, _cfg(), "catch me up", mode="manager")

    synth.assert_not_called()
    assert raw["path"] == "next_decision"
    assert "REV-10" in text
    assert "REV-11" not in text
    client.peek_gmail.assert_not_called()
    # Status without catch-up phrasing must not peek.
    client.peek_gmail.reset_mock()
    with patch("alicia.chat_resolve.linear_work_surface", return_value=surface), patch("alicia.tools.linear_work_surface", return_value=surface), patch("alicia.chat_resolve.chat_completion"):
        resolve_chat_reply(client, _cfg(), "what's the status", mode="manager")
    client.peek_gmail.assert_not_called()


def test_every_conversational_call_disables_thinking():
    """Wiring, not presence: each chat_completion call must pass thinking=False.

    local_llm.py measured the default (thinking on) at 13.1s and ZERO content
    tokens on the production prompt shape — the health probe passes with
    thinking off while the chat path ran thinking on, so the zombie looked
    healthy. This fails when any call site drops the kwarg again.
    """
    client = MagicMock()
    client.status.side_effect = Exception("no board")
    calls: list[dict] = []

    def record(cfg, messages, **kwargs):
        calls.append(kwargs)
        return "plain answer"

    # Direct (no forced tool) path.
    with patch("alicia.chat_resolve.chat_completion", side_effect=record):
        resolve_chat_reply(client, _cfg(), "talk to me about the roadmap")
    # Forced-tool summarize path.
    client2 = MagicMock()
    client2.status.side_effect = Exception("no board")
    client2.peek_slack.return_value = {"ok": True, "items": [{"title": "x"}]}
    with patch("alicia.chat_resolve.chat_completion", side_effect=record):
        resolve_chat_reply(client2, _cfg(), "anything new in slack?")

    assert calls, "no chat_completion call was exercised"
    for kwargs in calls:
        assert kwargs.get("thinking") is False, f"call missing thinking=False: {kwargs}"
