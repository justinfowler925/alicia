"""The session store — a conversation that survives the tab closing."""

import pytest

from alicia.session import SessionStore


@pytest.fixture()
def store(tmp_path):
    return SessionStore(tmp_path / "sessions.sqlite")


# --- turns: the thing memory.conversations cannot do ----------------------


def test_a_conversation_keeps_every_turn_not_just_the_last_pair(store):
    sid = store.open_session(title="pricing")
    for i in range(6):
        store.append_turn(sid, "user", f"user {i}")
        store.append_turn(sid, "alicia", f"alicia {i}")
    turns = store.transcript(sid)
    assert len(turns) == 12
    assert turns[0].text == "user 0"
    assert turns[-1].text == "alicia 5"


def test_transcript_survives_a_new_store_instance(tmp_path):
    path = tmp_path / "s.sqlite"
    sid = SessionStore(path).open_session()
    SessionStore(path).append_turn(sid, "user", "still here?")
    assert SessionStore(path).transcript(sid)[0].text == "still here?"


def test_limit_trims_from_the_end_not_the_start(store):
    sid = store.open_session()
    for i in range(10):
        store.append_turn(sid, "user", f"turn {i}")
    kept = store.transcript(sid, limit=3)
    assert [t.text for t in kept] == ["turn 7", "turn 8", "turn 9"]


def test_channel_is_recorded_per_turn(store):
    """Voice and text are transports over one conversation, so both land here."""
    sid = store.open_session()
    store.append_turn(sid, "user", "spoken", channel="voice")
    store.append_turn(sid, "user", "typed", channel="text")
    assert [t.channel for t in store.transcript(sid)] == ["voice", "text"]


def test_switching_channel_midconversation_loses_nothing(store):
    sid = store.open_session()
    store.append_turn(sid, "user", "start by voice", channel="voice")
    store.append_turn(sid, "alicia", "got it")
    store.append_turn(sid, "user", "finish by typing", channel="text")
    assert len(store.transcript(sid)) == 3
    assert store.history_for_model(sid)[0]["content"] == "start by voice"


def test_history_for_model_uses_role_names_the_model_expects(store):
    sid = store.open_session()
    store.append_turn(sid, "user", "hi")
    store.append_turn(sid, "alicia", "hello")
    assert store.history_for_model(sid) == [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "hello"},
    ]


def test_history_for_model_excludes_deep_lane_ack_strings(store):
    """Feeding 'Hang on.' / 'Let me dig.' as assistant history taught stall filler."""
    sid = store.open_session()
    store.append_turn(sid, "user", "can you tell me")
    store.append_turn(sid, "alicia", "Hang on.", meta={"lane": "deep", "thinking": True})
    store.append_turn(sid, "user", "for what")
    store.append_turn(sid, "alicia", "Let me dig.", meta={"lane": "deep"})
    store.append_turn(sid, "user", "about the renewals")
    store.append_turn(sid, "alicia", "Waiting on Marcus for the SOW.")
    hist = store.history_for_model(sid, keep=8)
    contents = [m["content"] for m in hist]
    assert "Hang on." not in contents
    assert "Let me dig." not in contents
    assert "Yeah, let me check." not in contents
    assert {"role": "user", "content": "about the renewals"} in hist
    assert {"role": "assistant", "content": "Waiting on Marcus for the SOW."} in hist


def test_sessions_are_isolated(store):
    a, b = store.open_session(), store.open_session()
    store.append_turn(a, "user", "in a")
    assert store.transcript(b) == []


# --- captured fields: one row per field -----------------------------------


def test_each_field_is_its_own_row(store):
    """A multi-answer string silently kept 1 of 3 on a live call. Never again."""
    sid = store.open_session()
    store.capture_field(sid, "q1", "no")
    store.capture_field(sid, "q2", "yes")
    store.capture_field(sid, "q3", "no")
    assert store.field_map(sid) == {"q1": "no", "q2": "yes", "q3": "no"}


def test_recapturing_a_field_updates_it(store):
    sid = store.open_session()
    store.capture_field(sid, "owner", "Marcus")
    store.capture_field(sid, "owner", "Sergii")
    assert store.field_map(sid)["owner"] == "Sergii"
    assert len(store.fields(sid)) == 1


def test_a_field_remembers_which_turn_said_it(store):
    sid = store.open_session()
    turn = store.append_turn(sid, "user", "the owner is Marcus")
    store.capture_field(sid, "owner", "Marcus", source_turn=turn.id)
    assert store.fields(sid)[0].source_turn == turn.id


def test_missing_fields_drives_the_next_question(store):
    """The slot tracker is code, not a prompt. Prose routing scored 0 of 8."""
    sid = store.open_session()
    required = ["what", "why", "who", "when"]
    assert store.missing_fields(sid, required) == required
    store.capture_field(sid, "what", "rewrite the prompt")
    store.capture_field(sid, "who", "me")
    assert store.missing_fields(sid, required) == ["why", "when"]


def test_missing_fields_preserves_the_callers_order(store):
    sid = store.open_session()
    store.capture_field(sid, "b", "1")
    assert store.missing_fields(sid, ["c", "a", "b"]) == ["c", "a"]


# --- artifacts: the gate is an object, not a sentence ---------------------


def test_an_artifact_holds_the_exact_call_it_will_make(store):
    sid = store.open_session()
    art = store.draft_artifact(
        sid, kind="ticket", tool="register_thread", args={"title": "x", "goal": "y"}
    )
    assert art["state"] == "draft"
    assert art["args"] == {"title": "x", "goal": "y"}


def test_approving_returns_the_same_args_that_were_previewed(store):
    """Preview and execution must be the same object, or it isn't a gate."""
    sid = store.open_session()
    args = {"ticket": "REV-412", "decision": "approve"}
    art = store.draft_artifact(sid, kind="gate", tool="approve_gate", args=args)
    previewed = store.get_artifact(art["id"])["args"]
    settled = store.settle_artifact(art["id"], state="executed", result={"ok": True})
    assert settled["args"] == previewed == args


def test_an_artifact_can_only_be_settled_once(store):
    """A repeated click or a re-heard 'yes' must not execute twice."""
    sid = store.open_session()
    art = store.draft_artifact(sid, kind="gate", tool="approve_gate", args={"ticket": "REV-1"})
    assert store.settle_artifact(art["id"], state="executed") is not None
    assert store.settle_artifact(art["id"], state="executed") is None


def test_an_artifact_can_only_be_claimed_once(store):
    sid = store.open_session()
    art = store.draft_artifact(
        sid, kind="gate", tool="create_linear_ticket", args={"title": "One"}
    )
    claimed = store.claim_artifact(art["id"])
    assert claimed is not None
    assert claimed["state"] == "executing"
    assert store.claim_artifact(art["id"]) is None
    finished = store.finish_artifact(art["id"], state="executed", result={"ok": True})
    assert finished is not None
    assert finished["state"] == "executed"


def test_rejecting_leaves_it_unexecutable(store):
    sid = store.open_session()
    art = store.draft_artifact(sid, kind="gate", tool="approve_gate", args={"ticket": "REV-1"})
    store.settle_artifact(art["id"], state="rejected")
    assert store.settle_artifact(art["id"], state="executed") is None
    assert store.get_artifact(art["id"])["state"] == "rejected"


# --- sessions ------------------------------------------------------------


def test_closing_a_session_keeps_its_contents(store):
    sid = store.open_session(title="pricing")
    store.append_turn(sid, "user", "hello")
    store.capture_field(sid, "what", "x")
    store.close_session(sid)
    assert store.get_session(sid)["state"] == "closed"
    assert len(store.transcript(sid)) == 1
    assert store.field_map(sid) == {"what": "x"}


def test_open_only_filters_closed_sessions(store):
    a = store.open_session(title="a")
    store.open_session(title="b")
    store.close_session(a)
    assert [s["title"] for s in store.list_sessions(open_only=True)] == ["b"]


def test_snapshot_returns_everything_the_screen_needs(store):
    sid = store.open_session(title="pricing")
    store.append_turn(sid, "user", "hello", channel="voice")
    store.capture_field(sid, "what", "rewrite")
    store.draft_artifact(sid, kind="note", tool="capture_note", args={"text": "x"})
    snap = store.snapshot(sid)
    assert snap["session"]["title"] == "pricing"
    assert len(snap["turns"]) == 1
    assert len(snap["fields"]) == 1
    assert len(snap["artifacts"]) == 1


def test_snapshot_of_an_unknown_session_is_empty(store):
    assert store.snapshot("nope") == {}
