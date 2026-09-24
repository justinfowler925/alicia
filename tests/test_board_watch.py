"""The doorbell: report change, never state, and never become noise."""

import pytest

from alicia.board_watch import (
    AGGREGATE_ABOVE,
    BoardSnapshot,
    BoardWatcher,
    Transition,
    diff,
    spoken_line,
)


def board(needs=(), queued=(), working=(), stuck=(), alarm=False):
    return {
        "needs_you": [{"ticket": t, "title": f"title {t}"} for t in needs],
        "queued": [{"ticket": t, "title": f"title {t}"} for t in queued],
        "working": [{"ticket": t, "title": f"title {t}"} for t in working],
        "stuck": [{"reason": "Stopped", "rows": [{"ticket": t, "title": f"title {t}"} for t in stuck]}],
        "alarm": {"alarm": alarm},
        "counts": {},
        "headline": "",
    }


snap = lambda **kw: BoardSnapshot.from_board(board(**kw))  # noqa: E731


# --- diff reports movement, not state ------------------------------------


def test_no_change_is_silence():
    before = snap(needs=["REV-1"], queued=["REV-2"])
    assert diff(before, snap(needs=["REV-1"], queued=["REV-2"])) == []


def test_the_first_tick_after_a_restart_is_not_news():
    """Everything would look new, and you'd get the whole board read at you."""
    assert diff(None, snap(needs=["REV-1"], queued=["REV-2"])) == []


def test_arriving_at_your_desk_is_an_event():
    out = diff(snap(queued=["REV-1"]), snap(needs=["REV-1"]))
    assert [t.kind for t in out] == ["needs_you"]
    assert out[0].ticket == "REV-1"


def test_leaving_the_board_is_finishing():
    out = diff(snap(queued=["REV-1", "REV-2"]), snap(queued=["REV-1"]))
    assert [(t.kind, t.ticket) for t in out] == [("done", "REV-2")]


def test_moving_between_lanes_is_not_an_event():
    """queued -> working is still open. Only leaving entirely counts."""
    assert diff(snap(queued=["REV-1"]), snap(working=["REV-1"])) == []


def test_stuck_rows_are_tracked_too():
    assert diff(snap(stuck=["REV-9"]), snap()) == [Transition("done", "REV-9", "title REV-9")]


def test_a_row_with_no_ticket_id_is_still_tracked():
    """Otherwise every unnamed stuck row looks brand new on every single tick."""
    a = BoardSnapshot.from_board(
        {"stuck": [{"rows": [{"ticket": "", "title": "Capture Tool - create for David"}]}]}
    )
    b = BoardSnapshot.from_board({"stuck": [{"rows": [{"ticket": "", "title": "Capture Tool - create for David"}]}]})
    assert diff(a, b) == []


def test_the_alarm_flipping_both_ways_is_an_event():
    assert [t.kind for t in diff(snap(), snap(alarm=True))] == ["alarm_on"]
    assert [t.kind for t in diff(snap(alarm=True), snap())] == ["alarm_off"]


# --- the spoken line ------------------------------------------------------


def test_a_few_transitions_are_named():
    line = spoken_line([Transition("needs_you", "REV-1"), Transition("done", "REV-2")])
    assert "REV-1 needs you." in line
    assert "REV-2 finished." in line


def test_many_transitions_are_counted_not_listed():
    """At scale, per-item narration is unusable — rates and exceptions only."""
    many = [Transition("done", f"REV-{i}") for i in range(12)] + [
        Transition("needs_you", "REV-99")
    ]
    line = spoken_line(many)
    assert "12 finished" in line
    assert "1 now need you" in line
    assert "REV-0" not in line  # no individual ticket survives aggregation


def test_the_alarm_survives_aggregation():
    """It's the only transition that is bad news by definition."""
    many = [Transition("done", f"REV-{i}") for i in range(AGGREGATE_ABOVE + 2)]
    many.append(Transition("alarm_on"))
    line = spoken_line(many)
    assert "alarm" in line.lower()


def test_nothing_to_say_says_nothing():
    assert spoken_line([]) == ""


def test_no_model_is_involved_in_the_spoken_path():
    """A narrator is the purest unscored surface in the design. Guard it."""
    import inspect

    import alicia.board_watch as mod

    src = inspect.getsource(mod)
    for forbidden in ("chat_completion", "httpx", "openai"):
        assert forbidden not in src, f"the doorbell must stay deterministic; found {forbidden}"


# --- the watcher: throttling the ear, never the screen -------------------


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


@pytest.fixture()
def watcher():
    events = []
    clock = Clock()
    w = BoardWatcher(on_event=lambda k, p: events.append((k, p)), clock=clock)
    w.events, w.clock = events, clock  # type: ignore[attr-defined]
    return w


def test_the_doorbell_never_speaks(watcher):
    watcher.observe(board(queued=["REV-1"]))
    out = watcher.observe(board(needs=["REV-1"]))
    assert out["transitions"]
    assert out["spoken"] == ""


def test_the_screen_sees_every_transition(watcher):
    watcher.observe(board(queued=["REV-1"]))
    watcher.observe(board(needs=["REV-1"]))
    out2 = watcher.observe(board(needs=["REV-1", "REV-2"]))
    assert out2["transitions"], "the screen must still be told"
    assert out2["spoken"] == ""


def test_a_uuid_is_not_named_in_the_phrase():
    t = Transition(
        "needs_you",
        "6d0b8f2a-7e2b-4e4f-b19c-6dc5f6fd80fe",
        "Approve GitHub Actions Salesforce CI run 31731276061 for SFDC Prod",
    )
    line = t.phrase()
    assert "6d0b8f2a" not in line.lower()
    assert "needs you" in line
    assert "Approve" in line


def test_the_alarm_is_still_a_transition_but_silent(watcher):
    watcher.observe(board(queued=["REV-1"]))
    watcher.observe(board(needs=["REV-1"]))
    out = watcher.observe(board(needs=["REV-1"], alarm=True))
    assert any(t["kind"] == "alarm_on" for t in out["transitions"])
    assert out["spoken"] == ""


def test_a_quiet_board_emits_nothing_at_all(watcher):
    watcher.observe(board(queued=["REV-1"]))
    watcher.observe(board(queued=["REV-1"]))
    assert watcher.events == []  # type: ignore[attr-defined]


def test_no_still_working_chatter(watcher):
    """Ten identical ticks in a row must produce exactly zero events."""
    for _ in range(10):
        watcher.observe(board(needs=["REV-1"], queued=["REV-2"], working=["REV-3"]))
    assert len(watcher.events) == 0  # type: ignore[attr-defined]


def test_a_broken_listener_cannot_break_the_watcher():
    def explode(_k, _p):
        raise RuntimeError("browser went away")

    w = BoardWatcher(on_event=explode)
    w.observe(board(queued=["REV-1"]))
    with pytest.raises(RuntimeError):
        # The watcher itself does not swallow — the caller (the bus) does, and
        # the bus is already proven not to throw. Documented so nobody adds a
        # second silent catch here and hides a real publish failure.
        w.observe(board(needs=["REV-1"]))


def test_the_session_page_does_not_speak_board_events():
    """The doorbell is a flash on the card, never a sentence.

    A leftover EventSource kept talking after the tab closed because
    applyBoardEvent called speak() on every board delta.
    """
    from pathlib import Path

    src = (Path(__file__).resolve().parents[1] / "alicia/static/session.js").read_text()
    start = src.index("function applyBoardEvent")
    end = src.index("\nfunction ", start + 1)
    body = src[start:end]
    assert "speak(" not in body
    assert "setQueueNote" in body
    assert "recentlyMoved" in body
