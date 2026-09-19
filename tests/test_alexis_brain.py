
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest
from fastapi.testclient import TestClient

from brutus.alexis_brain import create_app


@pytest.fixture
def setup(tmp_path):
    calls = []
    def model(system, messages):
        calls.append((system, messages))
        return "I remember the prototype is called Orchard."
    app = create_app(tmp_path / "brain.sqlite", tokens={
        "brutus-secret": {"principal": "justin", "surface": "brutus"},
        "other-secret": {"principal": "justin", "surface": "other"},
        "other-user": {"principal": "someone-else", "surface": "other"},
    }, model=model)
    return TestClient(app), app.state.brain, calls


def headers(token="brutus-secret"):
    return {"Authorization": f"Bearer {token}"}


def post(client, message="Remember Orchard", session="one", request="request1", token="brutus-secret", **extra):
    return client.post("/v1/turns", headers=headers(token), json={
        "session_id": session, "request_id": request, "message": message, **extra})


def test_same_conversation_across_surfaces_and_protocols(setup):
    client, _brain, calls = setup
    first = post(client).json()
    result = client.post("/v1/chat/completions", headers={**headers("other-secret"), "X-Alexis-Session-Id": "one"},
                         json={"messages": [{"role": "user", "content": "What did I call it?"}]})
    assert result.status_code == 200
    assert len(calls[-1][1]) == 3
    assert calls[-1][1][0]["content"] == "Remember Orchard"
    assert client.get(f"/v1/turns/{first['turn_id']}", headers=headers("other-user")).status_code == 404


def test_cross_session_memory_is_principal_scoped(setup):
    client, _brain, calls = setup
    client.post("/v1/memory", headers=headers(), json={"key": "preferred_style", "value": "Lead with decisions"})
    post(client)
    post(client, session="two", request="request2", token="other-secret")
    assert "Lead with decisions" in calls[-1][0]
    assert "Orchard" in calls[-1][0]
    post(client, session="three", request="request3", token="other-user", message="Hello")
    assert "Lead with decisions" not in calls[-1][0]
    assert "Orchard" not in calls[-1][0]


def test_idempotency_and_conflicts(setup):
    client, _brain, calls = setup
    first = post(client).json()
    assert post(client).json() == first
    assert len(calls) == 1
    assert post(client, message="Changed message").status_code == 409
    assert post(client, token="other-secret").status_code == 409


def test_surface_owns_tool_completion_and_replay_is_refused(setup):
    client, brain, _calls = setup
    brain.model = lambda system, messages: 'TOOL: inspect\nARGS: {"id":"1"}'
    result = post(client, tools=[{"name": "inspect"}]).json()
    assert result["status"] == "tool"
    body = {"call_id": result["tool"]["call_id"], "result": {"found": True}}
    url = f"/v1/turns/{result['turn_id']}/tool-result"
    assert client.post(url, headers=headers("other-secret"), json=body).status_code == 403
    assert post(client, request="second").status_code == 409
    brain.model = lambda system, messages: "Found the real record."
    assert client.post(url, headers=headers(), json=body).json()["status"] == "done"
    assert client.post(url, headers=headers(), json=body).status_code == 409


def test_unsupported_tool_fails_without_execution(setup):
    client, brain, _calls = setup
    brain.model = lambda *_: 'TOOL: delete_everything\nARGS: {}'
    assert post(client).status_code == 503


def test_failure_does_not_spawn_an_alternate_brain(setup):
    client, brain, _calls = setup
    def broken(*_):
        raise RuntimeError("provider failure with sensitive detail")
    brain.model = broken
    response = post(client)
    assert response.status_code == 503
    assert "sensitive" not in response.text
    assert "No alternate brain" in response.text


def test_feedback_requires_completed_owned_turn_and_is_idempotent(setup):
    client, brain, _calls = setup
    turn = post(client).json()
    body = {"turn_id": turn["turn_id"], "dimension": "organization", "rating": "needs_work", "correction": "Lead with the decision"}
    assert client.post("/v1/feedback", headers=headers("other-user"), json=body).status_code == 404
    assert client.post("/v1/feedback", headers=headers(), json={**body, "correction": ""}).status_code == 422
    for _ in range(2):
        assert client.post("/v1/feedback", headers=headers(), json=body).status_code == 200
    with brain.db() as db:
        assert db.execute("SELECT count(*) FROM feedback").fetchone()[0] == 1


def test_persistence_after_service_restart(setup):
    client, brain, calls = setup
    post(client)
    fresh = create_app(brain.path, tokens={"brutus-secret": {"principal": "justin", "surface": "brutus"}},
                       model=brain.model)
    post(TestClient(fresh), request="next", message="Continue")
    assert len(calls[-1][1]) == 3


def test_no_auth_and_no_session_are_rejected(setup):
    client, _, _ = setup
    assert client.post("/v1/turns", json={}).status_code == 401
    assert client.post("/v1/chat/completions", headers=headers(), json={"messages": []}).status_code == 422


def test_core_memory_tool_persists_across_sessions(setup):
    client, brain, _calls = setup
    responses = iter(['TOOL: remember_preference\nARGS: {"key":"answer_order","value":"Decision first"}', 'I will lead with the decision.'])
    brain.model = lambda *_: next(responses)
    assert post(client, message="Remember that I prefer decision first").json()["status"] == "done"
    with brain.db() as db:
        assert db.execute("SELECT value FROM memories WHERE principal='justin'").fetchone()[0] == "Decision first"


def test_older_recall_never_crosses_principals(setup):
    client, brain, _calls = setup
    post(client, message="The codename is Orchard")
    assert len(brain.core_tool("justin", "recall_shared", {"query": "Orchard"})["results"]) == 1
    assert brain.core_tool("someone-else", "recall_shared", {"query": "Orchard"})["results"] == []


def test_tool_round_limit_prevents_an_infinite_memory_loop(setup):
    client, brain, _calls = setup
    brain.model = lambda *_: 'TOOL: recall_shared\nARGS: {"query":"Orchard"}'
    assert post(client).status_code == 503


def test_local_tool_retries_use_persisted_receipt(tmp_path, monkeypatch):
    from brutus.alexis_client import execute_once
    monkeypatch.setattr("brutus.alexis_client.state_path", lambda _: tmp_path / "calls.sqlite")
    calls = []
    def action():
        calls.append(1)
        return {"ok": True, "artifact_id": "one"}
    assert execute_once("call-one", action) == execute_once("call-one", action)
    assert len(calls) == 1


def test_interrupted_tool_is_not_repeated(tmp_path, monkeypatch):
    from brutus.alexis_client import execute_once
    monkeypatch.setattr("brutus.alexis_client.state_path", lambda _: tmp_path / "calls.sqlite")
    calls = []
    def action():
        calls.append(1)
        raise RuntimeError("interrupted")
    with pytest.raises(RuntimeError):
        execute_once("call-one", action)
    assert execute_once("call-one", action)["ok"] is False
    assert len(calls) == 1


def test_new_direction_supersedes_slow_reply_without_late_overwrite(setup):
    client, brain, _calls = setup
    entered, release = Event(), Event()
    def model(_system, messages):
        if messages[-1]["content"] == "Slow question":
            entered.set()
            assert release.wait(5)
            return "Old answer"
        return "New direction"
    brain.model = model
    with ThreadPoolExecutor() as pool:
        old = pool.submit(post, client, message="Slow question")
        assert entered.wait(5)
        try:
            assert post(client, request="new", message="Instead, this", token="other-secret", supersede=True).status_code == 409
            new = post(client, request="new", message="Instead, this", supersede=True)
            assert new.json()["reply"] == "New direction"
        finally:
            release.set()
        assert old.result().json()["status"] == "cancelled"


def test_explicit_stop_is_surface_scoped_and_prevents_late_reply(setup):
    client, brain, _calls = setup
    entered, release = Event(), Event()
    def slow(*_):
        entered.set()
        assert release.wait(5)
        return "This answer must not land"
    brain.model = slow
    with ThreadPoolExecutor() as pool:
        pending = pool.submit(post, client)
        assert entered.wait(5)
        try:
            assert client.post("/v1/sessions/one/cancel", headers=headers("other-secret")).json()["cancelled"] == 0
            assert client.post("/v1/sessions/one/cancel", headers=headers("other-user")).json()["cancelled"] == 0
            assert client.post("/v1/sessions/one/cancel", headers=headers()).json()["cancelled"] == 1
        finally:
            release.set()
        assert pending.result().json()["status"] == "cancelled"
