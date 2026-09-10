"""The session endpoints, at the HTTP layer.

The write boundary in particular is asserted here rather than only against
resolve_chat_reply — the last time that guarantee lived one layer down, the
endpoint quietly didn't pass the flag and the guarantee was decorative.
"""

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from brutus.config import BrutusCfg, LocalLLMCfg
from brutus.server import create_app
from brutus.session import SessionStore
from brutus.session_bus import SessionEventBus


@pytest.fixture()
def client(tmp_path):
    cfg = BrutusCfg(
        local_llm=LocalLLMCfg(enabled=True, model="m", router_url="http://127.0.0.1:7901")
    )
    app = create_app(cfg, start_watchdog=False)
    # Keep the test off the real state/ sqlite files, and off the real Atlas —
    # create_app builds a live AtlasClient, so it has to be swapped in BOTH
    # places that hold a reference or the assertions watch the wrong object.
    store = SessionStore(tmp_path / "s.sqlite")
    atlas = MagicMock()
    app.state.sessions = store
    app.state.client = atlas
    app.state.conversation.store = store
    app.state.conversation.client = atlas
    with TestClient(app) as c:
        c.app_state = app.state  # type: ignore[attr-defined]
        yield c


def test_open_a_session(client):
    r = client.post("/api/session/open", json={"title": "pricing"})
    assert r.status_code == 200
    assert r.json()["session"]["title"] == "pricing"


def test_voice_overview_stylesheet_is_served_and_linked(client):
    assert '/static/overview.css' in client.get('/session').text
    response = client.get('/static/overview.css')
    assert response.status_code == 200
    assert 'text/css' in response.headers['content-type']
    assert '.shell.voice-shell' in response.text


def test_voice_enrollment_status_is_available_without_exposing_a_profile(client):
    r = client.get("/api/voice-enrollment")
    assert r.status_code == 200
    assert "enrolled" in r.json()
    assert "embedding" not in r.json()


def test_owner_live_voice_fails_closed_until_enrollment(client):
    sid = client.post("/api/session/open", json={}).json()["session_id"]
    response = client.post(f"/api/session/{sid}/voice-token")
    assert response.status_code == 200
    assert response.json()["owner_enrollment_required"] is True


def test_say_records_both_sides(client):
    sid = client.post("/api/session/open", json={}).json()["session_id"]
    with patch("brutus.conversation.brain_reply", return_value=("Two need you.", {})):
        r = client.post(f"/api/session/{sid}/say", json={"message": "what needs me"})
        assert r.status_code == 200
        body = r.json()
        # The endpoint acknowledges the turn; the answer lands via events.
        assert body["thinking"] is True
        client.app_state.conversation.wait_for_brain(sid)  # type: ignore[attr-defined]
    snap = client.get(f"/api/session/{sid}").json()
    assert [t["role"] for t in snap["turns"]] == ["user", "brutus"]
    assert snap["turns"][-1]["text"] == "Two need you."


def test_say_wait_returns_the_finished_reply_for_voice(client):
    sid = client.post("/api/session/open", json={}).json()["session_id"]
    with patch("brutus.conversation.brain_reply", return_value=("Two need you.", {})):
        r = client.post(
            f"/api/session/{sid}/say",
            json={"message": "what needs me", "channel": "voice", "wait": True},
        )
    assert r.status_code == 200
    assert r.json()["thinking"] is False
    assert r.json()["reply"] == "Two need you."


def test_the_landed_answer_carries_both_renderings(client):
    """The screen gets the full text (a turn event); the mouth gets speechify
    (the answer event's `spoken`). Asserted through the Ear's synchronous path,
    which returns the same TurnResult the events are built from."""
    sid = client.post("/api/session/open", json={}).json()["session_id"]
    screen = "Merged 3018589 and REV-418 is live."
    mgr = client.app_state.conversation  # type: ignore[attr-defined]
    with patch("brutus.conversation.brain_reply", return_value=(screen, {})):
        result = mgr.handle(sid, "what needs me", wait=True)
    assert result.reply == screen
    assert "3018589" not in result.spoken
    assert "rev four eighteen" in result.spoken


def test_channel_is_recorded_but_changes_nothing(client):
    sid = client.post("/api/session/open", json={}).json()["session_id"]
    mgr = client.app_state.conversation  # type: ignore[attr-defined]
    with patch("brutus.conversation.brain_reply", return_value=("ok", {})):
        client.post(f"/api/session/{sid}/say", json={"message": "digest", "channel": "voice"})
        mgr.wait_for_brain(sid)
        client.post(f"/api/session/{sid}/say", json={"message": "digest", "channel": "text"})
        mgr.wait_for_brain(sid)
    snap = client.get(f"/api/session/{sid}").json()
    assert [t["channel"] for t in snap["turns"] if t["role"] == "user"] == ["voice", "text"]
    replies = [t["text"] for t in snap["turns"] if t["role"] == "brutus"]
    assert replies == ["ok", "ok"]


def test_the_brain_is_never_offered_a_gated_tool(client):
    """read_only used to be the boundary and the endpoint once forgot to pass
    it. The boundary is now structural: whatever registry the endpoint's
    manager builds, the tool catalog offered to the model excludes every gated
    tool."""
    from brutus.brain import anthropic_tools
    from brutus.gate import GATED

    sid = client.post("/api/session/open", json={}).json()["session_id"]
    seen: dict = {}

    def spy(cfg, registry, **kwargs):
        seen["registry"] = registry
        return ("ok", {})

    with patch("brutus.conversation.brain_reply", side_effect=spy):
        client.post(f"/api/session/{sid}/say", json={"message": "what needs me"})
        client.app_state.conversation.wait_for_brain(sid)  # type: ignore[attr-defined]
    offered = {t["name"] for t in anthropic_tools(seen["registry"])}
    assert not (offered & GATED), f"gated tools offered to the model: {offered & GATED}"


def test_a_mutating_ask_is_proposed_not_executed(client):
    """Talking never mutates. Writes go through an artifact you approve — and
    even a brain drafting dry_run=False gets coerced back to preview unless
    Justin's own phrase was deliberate."""
    sid = client.post("/api/session/open", json={}).json()["session_id"]
    atlas = client.app_state.client  # type: ignore[attr-defined]
    mgr = client.app_state.conversation  # type: ignore[attr-defined]

    def proposing_brain(cfg, registry, **kwargs):
        out = kwargs["on_propose"]("dispatch_tick", {"dry_run": False})
        return (f"Queued: {out['summary']}. Say yes to do it.", {})

    with patch("brutus.conversation.brain_reply", side_effect=proposing_brain):
        client.post(f"/api/session/{sid}/say", json={"message": "dispatch a tick for real"})
        mgr.wait_for_brain(sid)
    assert not atlas.dispatch_tick.called
    snap = client.get(f"/api/session/{sid}").json()
    assert [a["state"] for a in snap["artifacts"]] == ["draft"]
    assert snap["artifacts"][0]["tool"] == "dispatch_tick"
    # "dispatch a tick for real" is filler, not the deliberate live phrase.
    assert snap["artifacts"][0]["args"]["dry_run"] is True


def test_no_mutating_client_method_is_reachable_by_talking(client):
    """Asserted at HTTP with a HOSTILE brain: the API layer is faked to emit a
    direct tool_use for a gated tool, exactly as a jailbroken model would. The
    brain's tool runner must refuse it and nothing on the Atlas client may be
    touched."""
    from types import SimpleNamespace

    sid = client.post("/api/session/open", json={}).json()["session_id"]
    atlas = client.app_state.client  # type: ignore[attr-defined]
    mgr = client.app_state.conversation  # type: ignore[attr-defined]

    def hostile(cfg, **kwargs):
        n = len(kwargs["messages"])
        usage = SimpleNamespace(
            input_tokens=1, output_tokens=1,
            cache_read_input_tokens=0, cache_creation_input_tokens=0,
        )
        if n <= 1:
            return SimpleNamespace(
                content=[
                    {"type": "tool_use", "name": "approve_gate",
                     "input": {"ticket": "REV-412"}, "id": "tu_1"},
                    {"type": "tool_use", "name": "dispatch_tick",
                     "input": {"dry_run": False}, "id": "tu_2"},
                ],
                stop_reason="tool_use", usage=usage,
            )
        return SimpleNamespace(
            content=[{"type": "text", "text": "fine."}],
            stop_reason="end_turn", usage=usage,
        )

    with patch("brutus.brain._create", side_effect=hostile):
        client.post(f"/api/session/{sid}/say", json={"message": "approve REV-412"})
        mgr.wait_for_brain(sid)
    for method in ("approve", "dispatch_tick", "reconcile", "answer_steering"):
        assert not getattr(atlas, method).called, f"talking reached client.{method}"
    snap = client.get(f"/api/session/{sid}").json()
    assert snap["artifacts"] == [], "a refused tool call must not draft anything"


def test_unknown_session_is_404(client):
    assert client.get("/api/session/nope").status_code == 404
    assert client.post("/api/session/nope/say", json={"message": "hi"}).status_code == 404


def test_close_then_list(client):
    a = client.post("/api/session/open", json={"title": "a"}).json()["session_id"]
    client.post("/api/session/open", json={"title": "b"})
    client.post(f"/api/session/{a}/close")
    open_titles = [s["title"] for s in client.get("/api/session/list?open_only=true").json()["sessions"]]
    assert open_titles == ["b"]


# NOTE: there is no in-process test of the SSE endpoint here on purpose.
# An infinite stream driven through TestClient or httpx.ASGITransport hangs the
# suite rather than failing it — the transports have no way to abandon a
# generator that is legitimately never going to end. Chasing that produced a
# harness artifact, not a bug. The stream is verified against a REAL uvicorn
# process instead: scripts/verify-sse.sh, which starts the server, opens the
# stream, posts a turn, and asserts the frames arrive. The bus, which is where
# the actual logic lives, is fully covered below.


# --- the bus itself -------------------------------------------------------


def test_publishing_with_no_listener_is_a_no_op():
    SessionEventBus().publish("turn", {"session_id": "x"})  # must not raise


def test_an_event_without_a_session_id_is_dropped():
    import asyncio

    async def run():
        bus = SessionEventBus()
        q = bus.subscribe("s1")
        bus.publish("turn", {"no": "session"})
        assert q.empty()

    asyncio.run(run())


def test_a_full_queue_drops_the_oldest_rather_than_blocking():
    """A backgrounded tab must never apply backpressure to the conversation."""
    import asyncio

    from brutus.session_bus import QUEUE_DEPTH

    async def run():
        bus = SessionEventBus()
        q = bus.subscribe("s1")
        for i in range(QUEUE_DEPTH + 10):
            bus.publish("turn", {"session_id": "s1", "n": i})
        assert q.qsize() == QUEUE_DEPTH
        assert q.get_nowait()["n"] == 10  # the first ten were dropped

    asyncio.run(run())


def test_unsubscribe_removes_the_listener():
    import asyncio

    async def run():
        bus = SessionEventBus()
        q = bus.subscribe("s1")
        assert bus.subscriber_count("s1") == 1
        bus.unsubscribe("s1", q)
        assert bus.subscriber_count("s1") == 0

    asyncio.run(run())


def test_events_are_isolated_per_session():
    import asyncio

    async def run():
        bus = SessionEventBus()
        a, b = bus.subscribe("s1"), bus.subscribe("s2")
        bus.publish("turn", {"session_id": "s1"})
        assert a.qsize() == 1
        assert b.qsize() == 0

    asyncio.run(run())
