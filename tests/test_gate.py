"""The write gate: you approve an object, never a sentence a model wrote."""

from unittest.mock import MagicMock, patch

import pytest

from alicia.config import AliciaCfg, LocalLLMCfg
from alicia.conversation import ConversationManager
from alicia.gate import (
    FREE_WRITES,
    GATED,
    VOICE_FORBIDDEN,
    classify_write,
    describe,
    dispatch_is_live,
    read_confirmation,
)
from alicia.session import SessionStore


def _cfg():
    return AliciaCfg(
        local_llm=LocalLLMCfg(enabled=True, model="m", router_url="http://127.0.0.1:7901")
    )


@pytest.fixture()
def mgr(tmp_path):
    store = SessionStore(tmp_path / "s.sqlite")
    events = []
    m = ConversationManager(MagicMock(), _cfg(), store, on_event=lambda k, p: events.append((k, p)))
    m.events = events  # type: ignore[attr-defined]
    return m


@pytest.fixture()
def sid(mgr):
    return mgr.store.open_session()


# --- dry_run no longer comes from filler words ---------------------------


@pytest.mark.parametrize(
    "phrase",
    [
        "dispatch now for real quick",          # the one that fanned out 78 tickets
        "run a dispatch tick",
        "dispatch the ready work",
        "dispatch, and be real about it",
    ],
)
def test_ordinary_speech_never_means_live_dispatch(phrase):
    assert dispatch_is_live(phrase) is False


@pytest.mark.parametrize(
    "phrase",
    ["run a live dispatch", "dispatch for real", "do a real dispatch", "dispatch, not a dry run"],
)
def test_a_deliberate_phrase_does(phrase):
    assert dispatch_is_live(phrase) is True


def test_a_dispatch_defaults_to_preview_in_the_proposal(mgr, sid):
    """Even a brain that drafts dry_run=False gets coerced unless Justin's own
    phrase was deliberate — filler words fanned out 78 tickets once."""
    draft = mgr._on_propose(sid, "text", "dispatch now for real quick")
    draft("dispatch_tick", {"dry_run": False})
    art = mgr.store.artifacts(sid)[-1]
    assert art["tool"] == "dispatch_tick"
    assert art["args"]["dry_run"] is True, "filler words must not arm a live fan-out"

    live = mgr._on_propose(sid, "text", "run a live dispatch")
    live("dispatch_tick", {})
    assert mgr.store.artifacts(sid)[-1]["args"]["dry_run"] is False


# --- a mutating phrase describes; it does not perform --------------------


def _propose_approve(mgr, sid):
    """The brain's proposal hook, exactly as brain.py calls it."""
    return mgr._on_propose(sid, "text", "approve REV-412")(
        "approve_gate", {"ticket": "REV-412", "decision": "approve"}
    )


def test_approve_proposes_instead_of_approving(mgr, sid):
    out = _propose_approve(mgr, sid)
    assert not mgr.client.approve.called, "nothing may run before you say yes"
    art = mgr.store.artifacts(sid)[-1]
    assert art["state"] == "draft"
    assert art["args"] == {"ticket": "REV-412", "decision": "approve"}
    assert "REV-412" in out["summary"]


def test_the_proposal_is_announced_for_the_screen(mgr, sid):
    out = _propose_approve(mgr, sid)
    kinds = [k for k, _ in mgr.events]  # type: ignore[attr-defined]
    assert "proposal" in kinds
    assert out["intent_contract"]["target"] == "Atlas gate REV-412"
    proposal_event = next(payload for kind, payload in mgr.events if kind == "proposal")
    assert proposal_event["intent_contract"]["scope"] == "one named gate"


def test_an_incomplete_proposal_never_drafts_an_artifact(mgr, sid):
    draft = mgr._on_propose(sid, "text", "approve it")

    with pytest.raises(ValueError, match="ticket"):
        draft("approve_gate", {})

    assert mgr.store.artifacts(sid) == []


def test_reads_are_not_gated(mgr, sid):
    with patch("alicia.conversation.brain_reply", return_value=("ok", {})) as r:
        mgr.handle(sid, "what needs me")
        mgr.wait_for_brain(sid)
    assert r.called
    assert mgr.store.artifacts(sid) == []


def test_free_writes_are_never_gated(mgr, sid):
    """Writing to your own notepad is remembering, not acting.

    It also no longer goes through the model at all — see
    test_capturing_a_note_confirms_the_note_not_the_board below. So the
    assertion is that nothing was PROPOSED, not that the resolver ran.
    """
    out = mgr.handle(sid, "capture: call Marcus about the pilot")
    assert mgr.store.artifacts(sid) == [], "a note must never be gated"
    assert out.reply.startswith("On Ideas")
    assert "call Marcus about the pilot" in out.reply


def test_the_free_and_gated_sets_do_not_overlap():
    assert not (FREE_WRITES & GATED)


# --- approving runs the object that was shown ----------------------------


def test_legacy_atlas_preview_is_failed_without_execution(mgr, sid):
    _propose_approve(mgr, sid)
    previewed = mgr.store.artifacts(sid)[-1]["args"]

    captured = {}
    mgr.client.approve.side_effect = lambda ticket, decision: captured.update(
        ticket=ticket, decision=decision
    ) or {"status": "approved"}
    mgr.handle(sid, "yes")

    assert captured == {}
    assert previewed["ticket"] == "REV-412"
    assert mgr.store.artifacts(sid)[-1]["state"] == "failed"


def test_no_model_stands_between_the_preview_and_the_run(mgr, sid):
    _propose_approve(mgr, sid)
    with (
        patch("alicia.conversation.brain_reply") as brain,
        patch("alicia.brain._create") as api,
    ):
        mgr.client.approve.return_value = {"status": "approved"}
        mgr.handle(sid, "yes")
    assert not brain.called
    assert not api.called


def test_saying_no_cancels_and_runs_nothing(mgr, sid):
    _propose_approve(mgr, sid)
    out = mgr.handle(sid, "no")
    assert not mgr.client.approve.called
    assert mgr.store.artifacts(sid)[-1]["state"] == "rejected"
    assert out.reply == "Cancelled."


def test_anything_that_is_not_clearly_a_yes_is_not_a_yes(mgr, sid):
    """A wrong yes costs the ledger; a wrong no costs you repeating yourself."""
    _propose_approve(mgr, sid)
    with patch("alicia.conversation.brain_reply", return_value=("ok", {})):
        mgr.handle(sid, "hang on, what's it about")
        mgr.wait_for_brain(sid)
    assert not mgr.client.approve.called
    assert mgr.store.artifacts(sid)[-1]["state"] == "cancelled"


def test_a_re_heard_yes_never_executes_disabled_atlas_action(mgr, sid):
    _propose_approve(mgr, sid)
    mgr.client.approve.return_value = {"status": "approved"}
    mgr.handle(sid, "yes")
    with patch("alicia.conversation.brain_reply", return_value=("ok", {})):
        mgr.handle(sid, "yes")
        mgr.wait_for_brain(sid)
    assert mgr.client.approve.call_count == 0


def test_a_disabled_execution_says_it_did_not_run(mgr, sid):
    _propose_approve(mgr, sid)
    mgr.client.approve.side_effect = RuntimeError("atlas6 unreachable")
    out = mgr.handle(sid, "yes")
    assert "didn't run" in out.reply.lower()
    assert mgr.store.artifacts(sid)[-1]["state"] == "failed"


# --- ask_cursor is not reachable by talking ------------------------------


def test_cursor_is_refused_from_voice(mgr, sid):
    """Its allowlist includes ~/Projects/alicia — the gate's own source."""
    assert "ask_cursor" in VOICE_FORBIDDEN
    assert classify_write("ask_cursor") == "gated"


def test_a_voice_turn_cannot_propose_a_forbidden_tool(mgr, sid):
    """Enforced in brain._run_tool — the propose_action handler refuses before
    the hook that drafts artifacts is ever reached."""
    from alicia.brain import _run_tool

    out = _run_tool(
        MagicMock(),
        "propose_action",
        {"tool": "ask_cursor", "args": {"message": "x"}},
        channel="voice",
        on_propose=mgr._on_propose(sid, "voice", "have cursor look at the tunnel"),
        on_tool_result=None,
        recall=None,
    )
    assert out["ok"] is False and "keyboard-only" in out["error"]
    assert mgr.store.artifacts(sid) == [], "nothing may even be proposed"


# --- what you're shown is deterministic ----------------------------------


def test_a_live_dispatch_says_so_out_loud():
    screen, spoken = describe("dispatch_tick", {"dry_run": False})
    assert "LIVE" in screen
    assert "real work" in spoken


def test_steering_reads_the_body_back_not_just_the_act():
    """Confirming 'you're answering REV-418' confirms nothing about the answer."""
    body = "Tell them the schema grounding works now, re-run it"
    screen, spoken = describe("answer_steering", {"ticket_id": "REV-418", "body": body})
    assert body[:30] in screen
    assert body[:30] in spoken


def test_describe_uses_no_model():
    import inspect

    import alicia.gate as mod

    src = inspect.getsource(mod)
    for forbidden in ("chat_completion", "httpx", "openai"):
        assert forbidden not in src, f"the gate must stay deterministic; found {forbidden}"


@pytest.mark.parametrize("word", ["yes", "yeah", "yep", "do it", "go ahead", "send it", "ok"])
def test_yes_words(word):
    assert read_confirmation(word) == "yes"


@pytest.mark.parametrize("word", ["no", "nope", "cancel", "stop", "never mind", "don't"])
def test_no_words(word):
    assert read_confirmation(word) == "no"


@pytest.mark.parametrize("word", ["what's it about", "REV-419 too", "hmm", ""])
def test_neither(word):
    assert read_confirmation(word) is None


# --- a note gets a note's confirmation, not the board -------------------------


def test_machine_capture_confirms_the_note_not_the_board(mgr, sid):
    """The `capture:` protocol stays deterministic — the note is saved and the
    reply names the note, never a board summary. (Spoken no-colon captures now
    go to the brain, which calls capture_note as a tool.)"""
    with patch("alicia.conversation.brain_reply") as brain:
        out = mgr.handle(sid, "capture: a workstream for the voice project")
    assert not brain.called, "the machine protocol must not go through a model"
    assert out.reply.startswith("On Ideas")
    assert "voice project" in out.reply
    assert "ALARM" not in out.reply
    assert mgr.store.artifacts(sid) == [], "a note is never gated"


def test_updating_a_note_is_free_and_deleting_is_gated(mgr, sid):
    """The split is enforced by the brain's tool surface: update_note is
    callable directly, delete_note exists only behind propose_action."""
    from alicia.brain import BRAIN_FREE_WRITES, PROPOSABLE, anthropic_tools

    assert "update_note" in FREE_WRITES and "update_note" in BRAIN_FREE_WRITES
    assert "delete_note" in GATED and "delete_note" in PROPOSABLE
    offered = {t["name"] for t in anthropic_tools(mgr._registry())}
    assert "update_note" in offered
    assert "delete_note" not in offered

    # A drafted delete erases nothing until the yes.
    mgr.handle(sid, "capture: rename me later")
    draft = mgr._on_propose(sid, "text", "delete that idea")
    draft("delete_note", {"q": "rename me later"})
    art = mgr.store.artifacts(sid)[-1]
    assert art["tool"] == "delete_note" and art["state"] == "draft"
    assert mgr.todos.find("rename me later"), "gated delete must not erase before confirm"


def test_a_failed_capture_says_so(mgr, sid):
    from alicia import conversation as conv

    class Boom:
        def call(self, *_a, **_k):
            return {"ok": False, "error": "todos db locked"}

    with patch.object(conv.ConversationManager, "_registry", return_value=Boom()):
        out = mgr.handle(sid, "capture: something")
    assert "Couldn't save that" in out.reply
