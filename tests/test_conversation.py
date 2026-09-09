"""The conversation layer: one brain, one transcript, one reply rendered twice."""

import inspect
import threading
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import ANY, MagicMock, patch

import pytest

from brutus.config import BrutusCfg, ClaudeCfg, LocalLLMCfg
from brutus.conversation import ConversationManager, _flatten
from brutus.session import SessionStore


def _cfg() -> BrutusCfg:
    return BrutusCfg(
        claude=ClaudeCfg(enabled=True, model="claude-sonnet-5", api_key="k"),
        local_llm=LocalLLMCfg(enabled=True, model="m"),
    )


@pytest.fixture()
def mgr(tmp_path):
    store = SessionStore(tmp_path / "s.sqlite")
    events: list[tuple[str, dict]] = []
    m = ConversationManager(
        MagicMock(), _cfg(), store, on_event=lambda k, p: events.append((k, p))
    )
    m.events = events  # type: ignore[attr-defined]
    return m


def _brain(reply: str = "Here you go.", meta: dict | None = None):
    return patch(
        "brutus.conversation.brain_reply", return_value=(reply, meta or {"rounds": 1})
    )


# --- deterministic paths stay deterministic --------------------------------


def test_machine_capture_never_touches_the_brain(mgr):
    sid = mgr.store.open_session()
    with _brain() as brain:
        result = mgr.handle(sid, "capture: rotate the 1Password service token")
    brain.assert_not_called()
    assert result.reply.startswith("On Ideas — rotate the 1Password")
    notes = mgr.todos.list()
    assert any("1Password" in n.text for n in notes)


def test_machine_capture_emits_the_idea_for_the_screen(mgr):
    sid = mgr.store.open_session()
    with _brain():
        mgr.handle(sid, "capture: check the runner registration")
    kinds = [k for k, _ in mgr.events]
    assert "idea" in kinds


def test_hey_rewind_is_a_cursor_codeword_not_a_question(mgr):
    sid = mgr.store.open_session()
    with _brain() as brain:
        result = mgr.handle(sid, "hey rewind")
    brain.assert_not_called()
    assert "Cursor" in result.reply


def test_an_empty_message_does_nothing(mgr):
    sid = mgr.store.open_session()
    with _brain() as brain:
        result = mgr.handle(sid, "   ")
    brain.assert_not_called()
    assert result.error == "empty message"
    assert mgr.store.transcript(sid) == []


@pytest.mark.parametrize(
    "message",
    ["Give me a second to think.", "let me think", "hold on please", "pause"],
)
def test_explicit_thinking_pause_is_silent_and_never_calls_brain(mgr, message):
    sid = mgr.store.open_session()
    with _brain() as brain:
        result = mgr.handle(sid, message, channel="voice", wait=True)
    brain.assert_not_called()
    assert result.reply == ""
    assert result.spoken == ""
    assert [t.role for t in mgr.store.transcript(sid)] == ["user"]


def test_wait_with_a_question_is_not_mistaken_for_silence(mgr):
    sid = mgr.store.open_session()
    with _brain("The runner is healthy.") as brain:
        result = mgr.handle(sid, "Wait, is the runner healthy?", channel="voice", wait=True)
    brain.assert_called_once()
    assert result.reply == "The runner is healthy."


def test_user_facing_boundary_drops_an_amputated_model_tail(mgr):
    sid = mgr.store.open_session()
    with _brain("REV-507 is ready. Which way you want to"):
        result = mgr.handle(sid, "go ahead", channel="voice", wait=True)
    assert result.reply == "REV-507 is ready."
    assert result.spoken == "rev five oh seven is ready."


def test_the_regex_router_is_gone():
    """Structural: no _lookup_intent, no lane classifier, no canned greetings."""
    import brutus.conversation as mod

    src = inspect.getsource(mod)
    assert "_lookup_intent" not in src
    assert "_GREETING" not in src
    assert "_INCOMPLETE_SCRAP" not in src
    assert "def classify" not in src


# --- every human turn reaches the brain -------------------------------------


def test_a_human_turn_lands_via_the_brain(mgr):
    sid = mgr.store.open_session()
    with _brain("Two things shipped overnight."):
        result = mgr.handle(sid, "catch me up")
        assert result.thinking is True and result.reply == ""
        assert mgr.wait_for_brain(sid)
    turns = mgr.store.transcript(sid)
    assert [t.role for t in turns] == ["user", "brutus"]
    assert turns[-1].text == "Two things shipped overnight."
    kinds = [k for k, _ in mgr.events]
    assert "thinking" in kinds and "answer" in kinds


def test_no_filler_ack_turn_is_ever_landed(mgr):
    sid = mgr.store.open_session()
    with _brain("Answer."):
        mgr.handle(sid, "what do you think about the tracker design?")
        mgr.wait_for_brain(sid)
    texts = [t.text for t in mgr.store.transcript(sid) if t.role == "brutus"]
    assert texts == ["Answer."]  # exactly the answer — no "One sec — looking."


def test_greetings_and_swears_reach_the_brain_with_context(mgr):
    sid = mgr.store.open_session()
    with _brain("What's up?") as brain:
        mgr.handle(sid, "hey brutus")
        mgr.wait_for_brain(sid)
    brain.assert_called_once()
    history = brain.call_args.kwargs["history"]
    assert history[-1]["content"] == "hey brutus"


def test_the_brain_gets_the_whole_session_not_four_messages(mgr):
    sid = mgr.store.open_session()
    with _brain() as brain:
        for i in range(6):
            mgr.handle(sid, f"turn {i}")
            mgr.wait_for_brain(sid)
    history = brain.call_args.kwargs["history"]
    user_turns = [m for m in history if m["role"] == "user"]
    assert len(user_turns) == 6  # the old keep=4 amnesia is gone
    assert history[-1]["content"] == "turn 5"


def test_voice_and_text_share_one_transcript(mgr):
    sid = mgr.store.open_session()
    with _brain():
        mgr.handle(sid, "what needs me", channel="voice")
        mgr.wait_for_brain(sid)
        mgr.handle(sid, "what needs me", channel="text")
        mgr.wait_for_brain(sid)
    channels = [t.channel for t in mgr.store.transcript(sid) if t.role == "user"]
    assert channels == ["voice", "text"]


def test_wait_true_returns_the_finished_reply_for_the_ear(mgr):
    sid = mgr.store.open_session()
    with _brain("Spoken answer."):
        result = mgr.handle(sid, "is it done", channel="voice", wait=True)
    assert result.reply == "Spoken answer."
    assert result.spoken  # the Ear speaks this


def test_a_new_question_supersedes_the_one_in_flight(mgr):
    import threading

    sid = mgr.store.open_session()
    release = threading.Event()

    def slow_brain(cfg, registry, **kwargs):
        history = kwargs["history"]
        if history[-1]["content"] == "first question":
            release.wait(timeout=5)
            return ("stale answer", {})
        return ("fresh answer", {})

    with patch("brutus.conversation.brain_reply", side_effect=slow_brain):
        mgr.handle(sid, "first question")
        mgr.handle(sid, "second question")
        release.set()
        mgr.wait_for_brain(sid)
        # Join the superseded thread too.
        import time

        time.sleep(0.2)
    answers = [t.text for t in mgr.store.transcript(sid) if t.role == "brutus"]
    assert "stale answer" not in answers
    assert "fresh answer" in answers


def test_a_brain_error_is_landed_without_credential_noise(mgr):
    sid = mgr.store.open_session()
    with _brain("I couldn't finish that turn. Your request is safe.", {"error": "brain_service_unavailable"}):
        result = mgr.handle(sid, "you there?", wait=True)
    assert "Your request is safe" in result.reply
    assert result.error == "brain_service_unavailable"


# --- the write gate is unchanged ---------------------------------------------


def _draft(mgr, sid, tool="approve_gate", args=None, summary="Approve REV-412"):
    return mgr.store.draft_artifact(
        sid, kind=tool, tool=tool, args=args or {"ticket": "REV-412"}, summary=summary
    )


def test_legacy_atlas_artifact_cannot_execute(mgr):
    sid = mgr.store.open_session()
    mgr.client.approve.return_value = {"status": "approved"}
    _draft(mgr, sid)
    with _brain() as brain:
        result = mgr.handle(sid, "yes")
    brain.assert_not_called()
    mgr.client.approve.assert_not_called()
    assert "unknown tool approve_gate" in result.reply
    assert mgr.store.artifacts(sid)[-1]["state"] == "failed"


def test_no_cancels_the_draft(mgr):
    sid = mgr.store.open_session()
    art = _draft(mgr, sid)
    with _brain() as brain:
        result = mgr.handle(sid, "no")
    brain.assert_not_called()
    assert result.reply == "Cancelled."
    assert mgr.store.get_artifact(art["id"])["state"] == "rejected"


def test_a_non_answer_cancels_and_routes_to_the_brain(mgr):
    sid = mgr.store.open_session()
    art = _draft(mgr, sid)
    with _brain("New topic answer.") as brain:
        mgr.handle(sid, "actually what's the weather on the board")
        mgr.wait_for_brain(sid)
    brain.assert_called_once()
    assert mgr.store.get_artifact(art["id"])["state"] == "cancelled"


def test_the_brains_proposal_hook_drafts_a_real_artifact(mgr):
    sid = mgr.store.open_session()
    draft = mgr._on_propose(sid, "text")
    out = draft("approve_gate", {"ticket": "REV-9"})
    art = mgr.store.get_artifact(out["artifact_id"])
    assert art["state"] == "draft"
    assert art["args"] == {"ticket": "REV-9"}
    assert any(k == "proposal" for k, _ in mgr.events)


def test_complete_labelled_ticket_intake_compiles_then_drafts_without_cursor(mgr):
    sid = mgr.store.open_session()
    registry = MagicMock()
    registry.call.return_value = {
        "ok": True,
        "result": {"ok": True, "decision": {"action": "draft_new_ticket"}},
    }
    message = (
        "new ticket:\n"
        "title: Voice intake\n"
        "outcome: Draft a ticket from an explicit voice contract\n"
        "target: Brutus voice surface\n"
        "premise: Cursor text tool calls can fail\n"
        "scope: Explicit labelled ticket contracts\n"
        "preservation: Existing approval gate\n"
        "acceptance: Draft exists; no mutation before yes\n"
        "delivery: Test and deploy"
    )
    with patch.object(mgr, "_registry", return_value=registry), _brain() as brain:
        result = mgr.handle(sid, message, wait=True)
    brain.assert_not_called()
    registry.call.assert_called_once_with("compile_unfog_work", ANY)
    assert "title" not in registry.call.call_args.args[1]
    assert registry.call.call_args.args[1]["draft_title"] == "Voice intake"
    artifact = mgr.store.artifacts(sid)[-1]
    assert artifact["tool"] == "create_linear_ticket"
    assert artifact["state"] == "draft"
    assert "Say yes to do it" in result.reply


def test_incomplete_explicit_ticket_contract_gets_one_focused_question_without_cursor(mgr):
    sid = mgr.store.open_session()
    with _brain() as brain:
        result = mgr.handle(sid, "draft a ticket: outcome: Make voice intake reliable", wait=True)
    brain.assert_not_called()
    assert result.reply == "For that ticket draft, what is the target?"
    assert mgr.store.artifacts(sid) == []


def test_a_settled_artifact_cannot_execute_twice(mgr):
    sid = mgr.store.open_session()
    mgr.client.approve.return_value = {"status": "approved"}
    art = _draft(mgr, sid)
    mgr.handle(sid, "yes")
    mgr.client.approve.reset_mock()
    result = mgr.execute_artifact(sid, art["id"])
    mgr.client.approve.assert_not_called()
    assert "already settled" in result.reply


def test_concurrent_approvals_execute_external_mutation_once(mgr):
    sid = mgr.store.open_session()
    art = _draft(
        mgr,
        sid,
        tool="create_linear_ticket",
        args={"title": "Voice supervisor", "description": "Reviewed contract"},
    )
    entered = threading.Event()
    release = threading.Event()
    registry = MagicMock()

    def external_write(_tool, _args):
        entered.set()
        assert release.wait(timeout=5)
        return {"ok": True, "result": {"ok": True, "ticket": "REV-900"}}

    registry.call.side_effect = external_write
    with patch.object(mgr, "_registry", return_value=registry):
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(mgr.execute_artifact, sid, art["id"])
            assert entered.wait(timeout=5)
            second = pool.submit(mgr.execute_artifact, sid, art["id"])
            loser = second.result(timeout=5)
            release.set()
            winner = first.result(timeout=5)

    assert registry.call.call_count == 1
    assert winner.reply.startswith("Done")
    assert loser.reply == "That action is already running."
    assert mgr.store.get_artifact(art["id"])["state"] == "executed"


# --- rendering ----------------------------------------------------------------


def test_flatten_strips_furniture_but_never_caps_length():
    long_reply = "## Plan\n**Bold** `code`\n" + ("A real sentence. " * 60)
    out = _flatten(long_reply)
    assert "##" not in out and "**" not in out and "`" not in out
    assert len(out) > 600  # the 320-char cap is gone


def test_flatten_strips_the_go_ritual():
    assert _flatten("Approve it. Go? Go?") == "Approve it."


def test_the_reply_is_rendered_once_for_screen_and_once_for_mouth(mgr):
    sid = mgr.store.open_session()
    with _brain("The runner is back online."):
        result = mgr.handle(sid, "how's the runner", wait=True)
    assert result.reply == "The runner is back online."
    assert result.spoken  # speechify rendering of the SAME reply


# --- the spoken yes is the only thing a voice can spend --------------------


def _pending(mgr, sid):
    """Leave one artifact in draft, the way a gated write does."""
    from brutus.gate import propose

    proposal = propose("approve_gate", {"ticket": "REV-551", "decision": "approve"})
    return mgr.store.draft_artifact(
        sid,
        kind=proposal.kind,
        tool=proposal.tool,
        args=proposal.args,
        summary=proposal.summary,
    )


def test_an_unplaced_voice_cannot_execute_a_pending_write_by_saying_yes(mgr):
    """The artifact holds the {tool, args} that runs. A yes is the execution."""
    sid = mgr.store.open_session()
    _pending(mgr, sid)

    result = mgr.handle(sid, "yes", channel="voice", owner_verified=False)

    assert "approve it there" in result.reply.lower()
    assert mgr._pending_artifact(sid) is not None, "the draft must survive to be approved on screen"


def test_a_verified_voice_still_settles_a_pending_write(mgr):
    sid = mgr.store.open_session()
    _pending(mgr, sid)

    with patch.object(mgr, "execute_artifact", return_value="executed") as run:
        assert mgr.handle(sid, "yes", channel="voice", owner_verified=True) == "executed"
    assert run.called


def test_typing_is_never_asked_to_prove_a_voice(mgr):
    """None means the transport has no speaker to check; the token is the credential."""
    sid = mgr.store.open_session()
    _pending(mgr, sid)

    with patch.object(mgr, "execute_artifact", return_value="executed") as run:
        assert mgr.handle(sid, "yes") == "executed"
    assert run.called


def test_an_unplaced_voice_is_still_answered_when_nothing_is_pending(mgr):
    """Silence was the bug. An unrecognized voice gets a real reply."""
    sid = mgr.store.open_session()
    verified = mgr.handle(sid, "how is the Holloway rollout reading?", channel="voice", owner_verified=True)
    unplaced = mgr.handle(sid, "how is the Holloway rollout reading?", channel="voice", owner_verified=False)

    # The verdict decides what a yes may execute, never whether Brutus answers.
    assert (unplaced.lane, unplaced.thinking, unplaced.error) == (
        verified.lane,
        verified.thinking,
        verified.error,
    )
    assert unplaced.error is None
