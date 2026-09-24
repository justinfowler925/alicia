"""The brain: full history in, native tools, gated hands, honest failures."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from alicia.brain import (
    BRAIN_FREE_WRITES,
    BRAIN_READS,
    PROPOSABLE,
    BrainError,
    anthropic_tools,
    brain_reply,
    complete,
    pack_messages,
)
from alicia.config import AliciaCfg, ClaudeCfg, CursorRunnerCfg
from alicia.gate import GATED
from alicia.tools import Tool, ToolRegistry, build_default_registry


def _cfg() -> AliciaCfg:
    return AliciaCfg(
        claude=ClaudeCfg(enabled=True, model="claude-sonnet-5", api_key="k", transport="api", api_enabled=True),
        cursor_runner=CursorRunnerCfg(enabled=True),
    )


@pytest.fixture(autouse=True)
def isolated_brain_stores(tmp_path, monkeypatch):
    from alicia.memory import MemoryStore
    from alicia.todos import TodoStore

    memory = MemoryStore(tmp_path / "memory.sqlite")
    todos = TodoStore(tmp_path / "todos.sqlite")
    monkeypatch.setattr("alicia.tools.MemoryStore", lambda: memory)
    monkeypatch.setattr("alicia.tools.TodoStore", lambda: todos)
    monkeypatch.setattr("alicia.resilience.KILL_API", tmp_path / "brain.api.off")


def _registry():
    return build_default_registry(MagicMock(), _cfg(), read_only=False)


def _text_resp(text: str, **usage):
    return SimpleNamespace(
        content=[SimpleNamespace(type="text", text=text)],
        stop_reason="end_turn",
        usage=SimpleNamespace(
            input_tokens=usage.get("input", 100),
            output_tokens=usage.get("output", 20),
            cache_read_input_tokens=usage.get("cache_read", 0),
            cache_creation_input_tokens=usage.get("cache_write", 0),
        ),
    )


def _tool_resp(name: str, args: dict, use_id: str = "tu_1"):
    return SimpleNamespace(
        content=[SimpleNamespace(type="tool_use", name=name, input=args, id=use_id)],
        stop_reason="tool_use",
        usage=SimpleNamespace(
            input_tokens=100,
            output_tokens=20,
            cache_read_input_tokens=0,
            cache_creation_input_tokens=0,
        ),
    )


def _history(*msgs: tuple[str, str]) -> list[dict]:
    return [{"role": r, "content": c} for r, c in msgs]


# --- the tool surface is the gate, restated ---------------------------------


def test_gated_tools_are_never_offered_to_the_model():
    names = {t["name"] for t in anthropic_tools(_registry())}
    for gated in GATED:
        assert gated not in names, f"{gated} must only be reachable via propose_action"
    assert "propose_action" in names
    assert "recall" in names


def test_reads_and_free_writes_are_offered():
    names = {t["name"] for t in anthropic_tools(_registry())}
    for name in (*BRAIN_READS, *BRAIN_FREE_WRITES):
        assert name in names


def test_every_offered_tool_has_a_valid_object_schema():
    for tool in anthropic_tools(_registry()):
        schema = tool["input_schema"]
        assert schema.get("type") == "object", f"{tool['name']} schema is not an object"


def test_proposable_matches_the_gate():
    assert set(PROPOSABLE) == {
        "organize_agent_thread",
        "organize_project",
        "delete_note",
        "ask_cursor",
        "ask_frontier",
        "create_linear_ticket",
    }
    assert set(PROPOSABLE) <= set(GATED)


# --- the loop ----------------------------------------------------------------


def test_a_plain_answer_lands_in_one_round():
    with patch("alicia.brain._create", return_value=_text_resp("Yeah — it shipped.")):
        reply, meta = brain_reply(_cfg(), _registry(), history=_history(("user", "did the fix ship?")))
    assert reply == "Yeah — it shipped."
    assert meta["rounds"] == 1
    assert meta["input_tokens"] == 100


def test_the_brain_gets_the_whole_history():
    seen: dict = {}

    def create(cfg, **kwargs):
        seen.update(kwargs)
        return _text_resp("ok")

    history = _history(
        ("user", "let's talk about the renewal tracker"),
        ("assistant", "What about it?"),
        ("user", "is it done"),
    )
    with patch("alicia.brain._create", side_effect=create):
        brain_reply(_cfg(), _registry(), history=history)
    sent = seen["messages"]
    assert len(sent) == 3
    assert sent[0]["content"] == "let's talk about the renewal tracker"
    assert sent[-1] == {"role": "user", "content": "is it done"}


def test_accepting_the_brains_offer_executes_it_without_repeating_or_reasking():
    looked_up: list[str] = []
    registry = ToolRegistry()
    registry.register(
        Tool(
            name="list_notes",
            description="List local notes.",
            parameters={
                "type": "object",
                "properties": {},
            },
            fn=lambda: looked_up.append("notes") or {"notes": [{"text": "renewal tracker"}]},
        )
    )
    responses = iter(
        [
            _tool_resp("list_notes", {}),
            _text_resp("The renewal tracker is in your local notes."),
        ]
    )
    calls: list[dict] = []

    def create(cfg, **kwargs):
        calls.append(kwargs)
        return next(responses)

    history = _history(
        ("user", "I wanna know what the fuck I need to be doing right now"),
        (
            "assistant",
            "The renewal tracker may be in your notes. Want me to pull up the note?",
        ),
        ("user", "Go ahead. I'm listening"),
    )
    with patch("alicia.brain._create", side_effect=create):
        reply, meta = brain_reply(_cfg(), registry, history=history, channel="voice")

    assert looked_up == ["notes"]
    assert meta["tools"] == ["list_notes"]
    assert "stripped_reoffer" not in meta
    assert "renewal tracker" in reply.lower()
    assert any("JUSTIN ACCEPTED YOUR IMMEDIATELY PRIOR OFFER" in b["text"] for b in calls[0]["system"])
    assert "want me" not in reply.lower()


def test_accepted_offer_drops_an_amputated_follow_up_question():
    registry = ToolRegistry()
    registry.register(
        Tool(
            name="list_notes",
            description="Look up one thread.",
            parameters={"type": "object", "properties": {"external_id": {"type": "string"}}},
            fn=lambda external_id="": {"ticket": external_id, "status": "review"},
        )
    )
    responses = iter(
        [
            _tool_resp("list_notes", {"external_id": "REV-507"}),
            _text_resp("REV-507 is in review. Which way you want to"),
        ]
    )
    history = _history(
        ("assistant", "Want me to pull up REV-507's details?"),
        ("user", "Go ahead. I'm listening"),
    )
    with patch("alicia.brain._create", side_effect=lambda cfg, **kw: next(responses)):
        reply, meta = brain_reply(_cfg(), registry, history=history, channel="voice")

    assert reply == "REV-507 is in review."
    assert meta["dropped_incomplete_tail"] is True


def test_native_claude_call_caches_stable_system_and_uses_low_effort():
    client = MagicMock()
    client.messages.create.return_value = _text_resp("ok")
    with patch("anthropic.Anthropic", return_value=client):
        from alicia.brain import _create

        _create(
            _cfg(),
            system=[{"type": "text", "text": "stable"}, {"type": "text", "text": "volatile"}],
            tools=[{"name": "list_notes", "input_schema": {"type": "object"}}],
            messages=[{"role": "user", "content": "hi"}],
        )
    sent = client.messages.create.call_args.kwargs
    assert sent["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert "cache_control" not in sent["system"][1]
    assert sent["output_config"] == {"effort": "low"}
    assert sent["tools"][0]["name"] == "list_notes"


def test_voice_turn_gets_the_current_short_spoken_contract():
    seen: dict = {}

    def create(cfg, **kwargs):
        seen.update(kwargs)
        return _text_resp("Direct answer.")

    with patch("alicia.brain._create", side_effect=create):
        brain_reply(_cfg(), _registry(), history=_history(("user", "status")), channel="voice")
    system = seen["system"]
    assert "LIVE VOICE TURN" in system[1]["text"]
    assert "45 spoken words" in system[1]["text"]


def test_a_tool_round_executes_and_answers_in_one_user_message():
    responses = iter(
        [
            _tool_resp("list_notes", {}),
            _text_resp("Nothing on the pad."),
        ]
    )
    sent_messages: list = []

    def create(cfg, **kwargs):
        sent_messages.append(kwargs["messages"])
        return next(responses)

    with patch("alicia.brain._create", side_effect=create):
        reply, meta = brain_reply(_cfg(), _registry(), history=_history(("user", "anything on the pad?")))
    assert reply == "Nothing on the pad."
    assert meta["tools"] == ["list_notes"]
    assert meta["rounds"] == 2
    # Second call carries assistant tool_use + ONE user message of tool_results.
    followup = sent_messages[1]
    assert followup[-1]["role"] == "user"
    results = followup[-1]["content"]
    assert isinstance(results, list) and results[0]["type"] == "tool_result"


def test_propose_action_drafts_and_never_executes():
    drafted: list = []

    def on_propose(tool: str, args: dict) -> dict:
        drafted.append((tool, args))
        return {"artifact_id": "abc123", "summary": "Approve REV-412"}

    client = MagicMock()
    registry = build_default_registry(client, _cfg(), read_only=False)
    responses = iter(
        [
            _tool_resp("propose_action", {"tool": "approve_gate", "args": {"ticket": "REV-412"}}),
            _text_resp("Queued: approve REV-412. Say yes to do it."),
        ]
    )
    with patch("alicia.brain._create", side_effect=lambda cfg, **kw: next(responses)):
        reply, _meta = brain_reply(
            _cfg(),
            registry,
            history=_history(("user", "approve REV-412")),
            on_propose=on_propose,
        )
    assert drafted == [("approve_gate", {"ticket": "REV-412"})]
    client.approve.assert_not_called()
    assert "Say yes" in reply


def test_prose_cannot_claim_a_proposal_without_a_stored_artifact():
    with patch(
        "alicia.brain._create",
        side_effect=[
            _text_resp("Queued create_linear_ticket. Say yes to do it."),
            _text_resp("The proposal is queued. Say yes to do it."),
        ],
    ):
        reply, meta = brain_reply(
            _cfg(),
            _registry(),
            history=_history(("user", "draft the ticket")),
            on_propose=lambda _tool, _args: {"artifact_id": "never"},
        )
    assert reply == "I didn't create a proposal. Nothing was queued or changed."
    assert meta["blocked_action_claim"] is True
    assert "propose_action" not in meta["tools"]


def test_unbacked_proposal_claim_gets_one_chance_to_call_the_real_tool():
    drafted = []

    def on_propose(tool, args):
        drafted.append((tool, args))
        return {"artifact_id": "real", "summary": "Create ticket", "spoken": "Create it?"}

    with patch(
        "alicia.brain._create",
        side_effect=[
            _text_resp("Queued the ticket. Say yes to do it."),
            _tool_resp(
                "propose_action",
                {
                    "tool": "create_linear_ticket",
                    "args": {
                        "title": "T",
                        "outcome": "O",
                        "target": "X",
                        "premise": "P",
                        "scope": "S",
                        "preservation": "Keep",
                        "acceptance": ["A"],
                        "delivery": "D",
                    },
                },
            ),
            _text_resp("The real proposal is ready. Say yes to do it."),
        ],
    ):
        reply, meta = brain_reply(
            _cfg(), _registry(), history=_history(("user", "draft it")), on_propose=on_propose
        )
    assert "Say yes" in reply
    assert meta["tools"] == ["propose_action"]
    assert drafted[0][0] == "create_linear_ticket"


def test_voice_does_not_spend_a_second_model_call_correcting_an_action_claim():
    with patch("alicia.brain._create", return_value=_text_resp("Queued it. Say yes to do it.")) as create:
        reply, meta = brain_reply(
            _cfg(),
            _registry(),
            history=_history(("user", "draft it")),
            channel="voice",
            on_propose=lambda _tool, _args: {"artifact_id": "never"},
        )
    assert reply == "I didn't create a proposal. Nothing was queued or changed."
    assert meta["blocked_action_claim"] is True
    create.assert_called_once()


def test_cursor_tool_protocol_accepts_one_markdown_fence():
    from alicia.brain import _parse_cursor_tool_call

    assert _parse_cursor_tool_call('```json\nTOOL: list_notes\nARGS: {"q": "x"}\n```') == (
        "list_notes",
        {"q": "x"},
    )
    assert _parse_cursor_tool_call('I checked.\nTOOL: list_notes\nARGS: {"q": "x"}') is None


def test_propose_action_refuses_non_gated_tools():
    responses = iter(
        [
            _tool_resp("propose_action", {"tool": "capture_note", "args": {"text": "x"}}),
            _text_resp("ok"),
        ]
    )
    captured: list = []

    def create(cfg, **kwargs):
        captured.append(kwargs["messages"])
        return next(responses)

    with patch("alicia.brain._create", side_effect=create):
        brain_reply(
            _cfg(),
            _registry(),
            history=_history(("user", "note x")),
            on_propose=lambda t, a: {"artifact_id": "z"},
        )
    result_blocks = captured[1][-1]["content"]
    assert result_blocks[0]["is_error"] is True
    assert "not a gated tool" in result_blocks[0]["content"]


def test_voice_cannot_propose_keyboard_only_tools():
    responses = iter(
        [
            _tool_resp("propose_action", {"tool": "ask_cursor", "args": {"message": "x"}}),
            _text_resp("Can't do that from voice."),
        ]
    )
    captured: list = []

    def create(cfg, **kwargs):
        captured.append(kwargs["messages"])
        return next(responses)

    with patch("alicia.brain._create", side_effect=create):
        brain_reply(
            _cfg(),
            _registry(),
            history=_history(("user", "have cursor fix it")),
            channel="voice",
            on_propose=lambda t, a: {"artifact_id": "z"},
        )
    result_blocks = captured[1][-1]["content"]
    assert result_blocks[0]["is_error"] is True
    assert "keyboard-only" in result_blocks[0]["content"]


# --- invented tickets: challenge once, then annotate --------------------------


def test_an_invented_ticket_is_challenged_then_annotated():
    responses = iter(
        [
            _text_resp("REV-999 is ready to go."),
            _text_resp("REV-999 is ready to go."),  # doubles down
        ]
    )
    sent: list = []

    def create(cfg, **kwargs):
        sent.append(kwargs["messages"])
        return next(responses)

    with patch("alicia.brain._create", side_effect=create):
        reply, meta = brain_reply(_cfg(), _registry(), history=_history(("user", "carry on")))
    # Round two carried the challenge.
    challenge = sent[1][-1]["content"]
    assert "REV-999" in challenge and "verify" in challenge
    assert meta["invented_tickets"] == "REV-999"
    assert "(unverified)" in reply


def test_a_ticket_from_history_is_not_invented():
    history = _history(
        ("user", "what about REV-401?"),
        ("assistant", "REV-401 is with the bots."),
        ("user", "and now?"),
    )
    with patch("alicia.brain._create", return_value=_text_resp("REV-401 landed an hour ago.")):
        reply, meta = brain_reply(_cfg(), _registry(), history=history)
    assert "unverified" not in reply
    assert "invented_tickets" not in meta


def test_a_ticket_from_a_tool_result_is_not_invented():
    client = MagicMock()
    client.list_threads.return_value = {"threads": []}
    registry = build_default_registry(client, _cfg(), read_only=False)
    registry.register(Tool(name="list_notes", description="Notes", parameters={"type": "object"},
                           fn=lambda: {"notes": [{"text": "REV-777"}]}))
    responses = iter(
        [
            _tool_resp("list_notes", {}),
            _text_resp("REV-777 is the only open one."),
        ]
    )
    with patch("alicia.brain._create", side_effect=lambda cfg, **kw: next(responses)):
        reply, meta = brain_reply(_cfg(), registry, history=_history(("user", "what's open?")))
    assert "unverified" not in reply
    assert "invented_tickets" not in meta


# --- failure is honest ---------------------------------------------------------


def test_claude_failure_never_falls_back_to_cursor():
    with (
        patch("alicia.brain._create", side_effect=RuntimeError("boom")),
        patch("alicia.cursor_runner.run_cursor_chat") as cursor,
    ):
        reply, meta = brain_reply(_cfg(), _registry(), history=_history(("user", "you there?")))
    assert "couldn't finish" in reply
    assert meta["api_error"] == "brain_service_unavailable"
    cursor.assert_not_called()


def test_voice_social_fallback_never_calls_cursor():
    with (
        patch("alicia.brain._create", side_effect=RuntimeError("api down")),
        patch("alicia.cursor_runner.run_cursor_chat") as cursor,
    ):
        reply, meta = brain_reply(
            _cfg(), _registry(), history=_history(("user", "you there?")), channel="voice"
        )
    cursor.assert_not_called()
    assert reply == "Hello — I'm here and ready to work."
    assert meta["fallback"] == "deterministic_social"
    assert meta["api_error"] == "brain_service_unavailable"


def test_voice_brain_failure_keeps_work_status_grounded_without_external_fallback():
    registry = MagicMock()
    registry.call.return_value = {
        "ok": True,
        "result": {
            "needs_you": [{"ticket": "REV-507", "title": "Tune voice"}],
            "next_decision": "REV-507 needs your decision: tune voice.",
        },
    }
    with (
        patch("alicia.brain._create", side_effect=RuntimeError("api down")),
        patch("alicia.cursor_runner.run_cursor_chat") as cursor,
    ):
        reply, meta = brain_reply(
            _cfg(),
            registry,
            history=_history(("user", "What needs my attention today?")),
            channel="voice",
        )
    assert reply == "REV-507: Needs a decision. Approve or reject?"
    assert meta["fallback"] == "deterministic_work_surface"
    registry.call.assert_called_once_with("get_work_surface", {})
    cursor.assert_not_called()


def test_voice_brain_failure_answers_greeting_without_external_fallback():
    with (
        patch("alicia.brain._create", side_effect=RuntimeError("api down")),
        patch("alicia.cursor_runner.run_cursor_chat") as cursor,
    ):
        reply, meta = brain_reply(
            _cfg(),
            _registry(),
            history=_history(("user", "Stop. Say hello in one sentence.")),
            channel="voice",
        )
    assert reply == "Hello — I'm here and ready to work."
    assert meta["fallback"] == "deterministic_social"
    cursor.assert_not_called()


def test_voice_auth_failure_never_exposes_credential_implementation():
    with patch(
        "alicia.brain._create",
        side_effect=RuntimeError("ANTHROPIC_API_KEY missing from 1Password vault"),
    ):
        reply, meta = brain_reply(
            _cfg(), _registry(), history=_history(("user", "you there?")), channel="voice"
        )
    assert meta["api_error"] == "brain_auth_unavailable"
    folded = reply.casefold()
    for forbidden in ("api_key", "api key", "1password", "vault", "credential", "token"):
        assert forbidden not in folded


def test_total_failure_says_so_instead_of_inventing():
    with (
        patch("alicia.brain._create", side_effect=RuntimeError("api down")),
        patch(
            "alicia.claude.ask_claude",
            return_value={"ok": False, "error": "claude cli down"},
        ),
    ):
        reply, meta = brain_reply(_cfg(), _registry(), history=_history(("user", "you there?")))
    assert "Your request is safe" in reply
    assert meta["api_error"] == "brain_service_unavailable"


# --- the one-shot completion path (chat_resolve, refine, summaries) -----------


def test_pack_messages_single_user_is_bare_body():
    system, body = pack_messages(
        [
            {"role": "system", "content": "be brief"},
            {"role": "user", "content": "what needs me"},
        ]
    )
    assert system == "be brief"
    assert body == "what needs me"


def test_complete_uses_cursor_even_when_claude_is_configured():
    with (
        patch("alicia.claude.ask_claude", return_value={"ok": True, "reply": "from sonnet"}) as claude,
        patch(
            "alicia.cursor_runner.run_cursor_chat",
            return_value={"ok": True, "reply": "from cursor"},
        ) as cursor,
    ):
        assert complete(_cfg(), [{"role": "user", "content": "hi"}]) == "from cursor"
    claude.assert_not_called()
    cursor.assert_called_once()


def test_complete_does_not_consult_claude_before_cursor():
    with (
        patch("alicia.claude.ask_claude", return_value={"ok": False, "error": "down"}) as claude,
        patch(
            "alicia.cursor_runner.run_cursor_chat",
            return_value={"ok": True, "reply": "from cursor"},
        ) as cursor,
    ):
        assert complete(_cfg(), [{"role": "user", "content": "hi"}]) == "from cursor"
    cursor.assert_called_once()
    claude.assert_not_called()
    assert cursor.call_args.kwargs.get("mutate") is False


def test_complete_prefer_cursor_flips_order():
    with (
        patch(
            "alicia.cursor_runner.run_cursor_chat",
            return_value={"ok": True, "reply": "from cursor"},
        ) as cursor,
        patch("alicia.claude.ask_claude") as claude,
    ):
        assert complete(_cfg(), [{"role": "user", "content": "hi"}], prefer="cursor") == "from cursor"
    cursor.assert_called_once()
    claude.assert_not_called()


def test_complete_raises_when_cursor_is_disabled():
    cfg = AliciaCfg(claude=ClaudeCfg(enabled=False), cursor_runner=CursorRunnerCfg(enabled=False))
    with pytest.raises(BrainError) as exc:
        complete(cfg, [{"role": "user", "content": "hi"}])
    assert "cursor" in str(exc.value)
    assert exc.value.tried == ["cursor"]


def test_complete_does_not_call_local_llm():
    with (
        patch("alicia.claude.ask_claude", return_value={"ok": True, "reply": "ok"}),
        patch("alicia.cursor_runner.run_cursor_chat", return_value={"ok": True, "reply": "ok"}),
        patch("alicia.local_llm.chat_completion") as local,
    ):
        complete(_cfg(), [{"role": "user", "content": "hi"}])
    local.assert_not_called()


def test_chat_only_prompt_forbids_edits():
    from alicia.cursor_runner import build_chat_prompt

    p = build_chat_prompt("what is the gate design", mutate=False)
    assert "do not create, edit, delete, commit" in p.lower()
    assert "what is the gate design" in p


def test_the_round_cap_fails_honest():
    with patch(
        "alicia.brain._create",
        side_effect=lambda cfg, **kw: _tool_resp("list_notes", {}),
    ):
        reply, meta = brain_reply(_cfg(), _registry(), history=_history(("user", "loop forever")))
    assert meta["error"] == "tool round cap"
    assert "say it again" in reply


# --- "I can't find it" has to mean every surface, not the first two ----------


def test_not_found_is_challenged_until_agent_threads_have_been_searched():
    """From the transcript of 2026-09-09, session 7ac0ae3e2421, turn 1816.

    Asked "can you tell me the status of the UI/UX audit redesign?", Alicia
    answered "Sorry, I can't find that shit — nothing in Linear or notes
    matching a UI/UX audit redesign. ... Want me to check agent threads for
    it?" — and 55 agent threads matched those words, two of them running.
    Offering the surface that holds the answer is knowing where it is and
    declining to look.
    """
    searched: list[dict] = []
    registry = _registry()
    registry.register(
        Tool(
            name="list_agent_threads",
            description="Codex, Cursor and Claude threads on this laptop.",
            parameters={"type": "object", "properties": {"q": {"type": "string"}}},
            fn=lambda **kw: searched.append(kw)
            or {"threads": [{"title": "Company page UI/UX audit and redesign", "state": "running"}]},
        )
    )
    responses = iter(
        [
            _text_resp("Sorry, I can't find that shit — nothing in Linear or notes matching that."),
            _tool_resp("list_agent_threads", {"q": "UI/UX audit redesign"}),
            _text_resp("Two threads are on it — the company page audit is running."),
        ]
    )

    with patch("alicia.brain._create", side_effect=lambda cfg, **kw: next(responses)):
        reply, meta = brain_reply(
            _cfg(),
            registry,
            history=_history(("user", "can you tell me the status of the UI/UX audit redesign?")),
        )

    assert searched, "it declared not-found without ever searching agent threads"
    assert meta["challenged_not_found"] is True
    assert "can't find that" not in reply.casefold()
    assert "audit is running" in reply


def test_not_found_stands_once_the_threads_really_were_searched():
    """The guard is one challenge, not a loop. An honest empty answer survives."""
    registry = _registry()
    registry.register(
        Tool(
            name="list_agent_threads",
            description="Codex, Cursor and Claude threads on this laptop.",
            parameters={"type": "object", "properties": {"q": {"type": "string"}}},
            fn=lambda **kw: {"threads": []},
        )
    )
    responses = iter(
        [
            _tool_resp("list_agent_threads", {"q": "renewal tracker rewrite"}),
            _text_resp("Sorry, I can't find that shit."),
        ]
    )

    with patch("alicia.brain._create", side_effect=lambda cfg, **kw: next(responses)):
        reply, meta = brain_reply(
            _cfg(), registry, history=_history(("user", "where is the renewal tracker rewrite?"))
        )

    assert reply == "Sorry, I can't find that shit."
    assert "challenged_not_found" not in meta


def test_the_not_found_guard_matches_the_sentence_as_actually_spoken():
    """The prompt asks for the bare line; the transcript had it with a clause
    appended, and a guard that only caught the exact string missed it."""
    from alicia.brain import _looks_not_found

    assert _looks_not_found("Sorry, I can't find that shit.")
    assert _looks_not_found(
        "Sorry, I can't find that shit — nothing in Linear or notes matching a UI/UX audit redesign."
    )
    assert _looks_not_found("I cannot find that anywhere.")
    assert not _looks_not_found("Two threads are on it — the company page audit is running.")


@pytest.mark.parametrize("transport", ["api", "cli"])
@pytest.mark.parametrize("failure", [False, True])
def test_selected_conversation_transport_never_changes_provider(transport, failure):
    cfg = _cfg()
    cfg.claude.transport = transport
    error = RuntimeError("selected provider unavailable") if failure else None
    with (
        patch("alicia.brain._create", return_value=_text_resp("Selected answer."), side_effect=error) as api,
        patch("alicia.claude.ask_claude", return_value={
            "ok": not failure, "reply": "Selected answer." if not failure else "", "error": "unavailable",
        }) as cli,
        patch("alicia.brain.complete") as alternate,
    ):
        reply, meta = brain_reply(cfg, ToolRegistry(), history=_history(("user", "explain the design")))
    assert api.call_count == int(transport == "api")
    assert cli.call_count == int(transport == "cli")
    alternate.assert_not_called()
    assert meta["backend"] == f"claude_{transport}"
    assert ("couldn't finish" in reply) if failure else reply == "Selected answer."


@pytest.mark.parametrize("gate", ["disabled", "kill_file", "missing_key", "unknown_transport"])
def test_selected_api_refuses_missing_opt_in_without_running_another_transport(gate, tmp_path):
    from alicia import resilience

    cfg = _cfg()
    if gate == "disabled":
        cfg.claude.api_enabled = False
    elif gate == "kill_file":
        resilience.KILL_API.touch()
    elif gate == "missing_key":
        cfg.claude.api_key = ""
    else:
        cfg.claude.transport = "unknown"
    with (
        patch("alicia.brain._create") as api,
        patch("alicia.claude.ask_claude") as cli,
        patch("alicia.brain.complete") as alternate,
    ):
        reply, meta = brain_reply(cfg, ToolRegistry(), history=_history(("user", "explain the design")))
    assert "couldn't finish" in reply and meta["error"]
    api.assert_not_called()
    cli.assert_not_called()
    alternate.assert_not_called()


@pytest.mark.parametrize("transport", ["cli", "api"])
@pytest.mark.parametrize("case", ["unbacked", "failed_proposal", "invented", "search", "round_cap"])
def test_each_transport_reaches_truthfulness_gates(transport, case):
    import json

    cfg = _cfg()
    cfg.claude.transport = transport
    registry = ToolRegistry()
    observed = []
    registry.register(Tool(name="list_agent_threads", description="Search", parameters={"type": "object"},
                           fn=lambda: observed.append("searched") or {"threads": []}))
    if case == "unbacked":
        replies = [_text_resp("Queued it. Say yes to do it.")]
    elif case == "failed_proposal":
        replies = [_tool_resp("propose_action", {"tool": "capture_note", "args": {"text": "x"}}),
                   _text_resp("Queued it. Say yes to do it.")]
    elif case == "invented":
        replies = [_text_resp("REV-99999 shipped."), _text_resp("REV-99999 shipped.")]
    elif case == "search":
        replies = [_text_resp("I can't find that work."), _tool_resp("list_agent_threads", {}),
                   _text_resp("I can't find that work.")]
    else:
        replies = [_tool_resp("list_agent_threads", {})] * 8
    api_replies = list(replies)
    cli_replies = []
    for reply in replies:
        block = reply.content[0]
        text = block.text if block.type == "text" else f"TOOL: {block.name}\nARGS: {json.dumps(block.input)}"
        cli_replies.append({"ok": True, "reply": text})
    with (
        patch("alicia.brain._create", side_effect=api_replies) as api,
        patch("alicia.claude.ask_claude", side_effect=cli_replies) as cli,
        patch("alicia.brain.complete") as alternate,
    ):
        reply, meta = brain_reply(cfg, registry, history=_history(("user", "find the work")), channel="voice")
    alternate.assert_not_called()
    selected = api if transport == "api" else cli
    assert selected.call_count == len(replies)
    if case in {"unbacked", "failed_proposal"}:
        assert reply == "I didn't create a proposal. Nothing was queued or changed."
        assert meta.get("blocked_action_claim") is True
    elif case == "invented":
        assert meta["challenged_tickets"] == "REV-99999"
        assert "REV-99999 (unverified)" in reply
    elif case == "search":
        assert observed == ["searched"]
        assert meta["challenged_not_found"] is True
    else:
        assert len(observed) == 8
        assert meta["error"] == "tool round cap"
    if transport == "cli" and len(replies) > 1:
        assert "assistant:" in cli.call_args.args[1]
        if case in {"failed_proposal", "search", "round_cap"}:
            assert "TOOL_RESULT:" in cli.call_args.args[1]
