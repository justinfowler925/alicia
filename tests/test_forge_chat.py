"""Durability, retry, conversation context, cancellation and local boundary."""
import asyncio
import json
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from brutus import forge_bridge, forge_chat


def test_slow_brutus_canary_does_not_block_forge(monkeypatch):
    from brutus import resilience
    from brutus.config import BrutusCfg
    from brutus.server import create_app

    started, release = threading.Event(), threading.Event()
    def slow_probe(**kwargs):
        started.set()
        release.wait(5)
        return {"overall_ok": True}
    monkeypatch.setattr(resilience, "run_canaries", slow_probe)
    monkeypatch.setattr(resilience, "ensure_state_dirs", lambda: None)
    monkeypatch.setattr(resilience, "ensure_api_killed_by_default", lambda: None)
    monkeypatch.setattr(resilience, "pending_outbox", lambda limit: [])
    monkeypatch.setattr(forge_chat, "remote", lambda request: {"threads": []})
    app = create_app(BrutusCfg(watchdog_enabled=False), start_watchdog=False)

    async def exercise():
        transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 50000))
        async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as client:
            before = time.monotonic()
            probe = asyncio.create_task(client.get("/api/resilience"))
            try:
                assert await asyncio.to_thread(started.wait, 2)
                response = await client.post("/api/forge/request", json={"action": "list"}, headers={"X-Brutus-Chat": "forge"})
                assert response.status_code == 200
                assert time.monotonic() - before < 2
                assert not probe.done()
            finally:
                release.set()
                await probe
    asyncio.run(exercise())


def test_real_brutus_serves_forge_assets_and_transport(monkeypatch):
    from brutus.config import BrutusCfg
    from brutus.server import create_app

    app = create_app(BrutusCfg(watchdog_enabled=False), start_watchdog=False)
    monkeypatch.setattr(forge_chat, "remote", lambda request: {"threads": [], "model": "configured-model"})
    with TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 50000)) as client:
        page = client.get("/")
        assert page.status_code == 200
        for name, media_type in [("forge.js", "application/javascript"), ("forge.css", "text/css")]:
            assert "/static/" + name in page.text
            asset = client.get("/static/" + name)
            assert asset.status_code == 200
            assert asset.headers["content-type"].startswith(media_type)
            assert "no-store" in asset.headers["cache-control"]
        assert client.post("/api/forge/request", json={"action": "list"}, headers={"X-Brutus-Chat": "forge"}).json()["model"] == "configured-model"


class Agent:
    TERMINAL = frozenset({"succeeded", "failed", "cancelled", "blocked"})

    def __init__(self, root):
        self.STATE = root
        root.mkdir()
        self.runs = {}
        self.calls = []
        self.kicks = 0
        with self.db() as c:
            c.execute("CREATE TABLE runs(id TEXT, agent TEXT, work_item TEXT)")

    @contextmanager
    def db(self):
        with sqlite3.connect(self.STATE / "runs.db") as c:
            c.row_factory = sqlite3.Row
            yield c

    def config(self):
        return {"forge": {"model": "configured-model"}}

    def enqueue(self, role, prompt, **kwargs):
        self.calls.append((role, prompt, kwargs))
        run = dict(id=uuid.uuid4().hex, agent=role, status="queued", model="configured-model", **kwargs)
        self.runs[run["id"]] = run
        with self.db() as c:
            c.execute("INSERT INTO runs VALUES(?,?,?)", (run["id"], role, kwargs["work_item"]))
        return run

    def get(self, run_id):
        return self.runs[run_id]

    def update(self, run_id, **kwargs):
        self.runs[run_id].update(kwargs)

    def kick(self):
        self.kicks += 1


@pytest.fixture
def bridge(tmp_path):
    agent = Agent(tmp_path / "runtime")
    state = tmp_path / "chat"
    thread_id = str(uuid.uuid4())
    def call(action, **kwargs):
        return forge_bridge.handle(dict(action=action, thread_id=thread_id, **kwargs), agent, state)
    call("create")
    return call, agent, state, thread_id


def test_retry_launches_once_and_keeps_history(bridge):
    call, agent, state, thread_id = bridge
    msg = {"message_id": str(uuid.uuid4()), "message": "Remember amber."}
    first = call("send", **msg)
    second = call("send", **msg)
    assert first == second
    assert len(agent.calls) == 1
    assert call("list")["threads"][0]["title"] == "Remember amber."
    with pytest.raises(ValueError, match="still responding"):
        call("send", message_id=str(uuid.uuid4()), message="too soon")
    run = first["turns"][0]["run_id"]
    agent.update(run, status="succeeded")
    directory = agent.STATE / "runs" / run
    directory.mkdir(parents=True)
    (directory / "answer.md").write_text(json.dumps({"summary": "I remember amber.", "checks": []}))
    assert call("get")["turns"][0]["answer"] == "I remember amber."
    call("send", message_id=str(uuid.uuid4()), message="What color?")
    assert 'I remember amber.' in agent.calls[-1][1]
    assert agent.calls[-1][2]["cwd"].endswith(thread_id)
    assert len(call("get")["turns"]) == 2
    assert state.stat().st_mode & 0o777 == 0o700


def test_lost_commit_recovers_existing_run(bridge):
    call, agent, state, _thread_id = bridge
    msg = {"message_id": str(uuid.uuid4()), "message": "Hello"}
    first = call("send", **msg)
    with sqlite3.connect(state / "chat.sqlite3") as c:
        c.execute("DELETE FROM turns")
    recovered = call("send", **msg)
    assert recovered["turns"][0]["run_id"] == first["turns"][0]["run_id"]
    assert len(agent.calls) == 1


def test_stop_cancels_only_this_chat_and_preserves_turns(bridge):
    call, agent, _state, _thread_id = bridge
    call("send", message_id=str(uuid.uuid4()), message="Hello")
    other = agent.enqueue("forge", "unrelated", work_item="unrelated")
    result = call("stop")
    assert result["turns"][0]["status"] == "cancelled"
    assert agent.get(other["id"])["status"] == "queued"
    assert len(call("get")["turns"]) == 1


def test_message_id_cannot_be_reused_for_different_text(bridge):
    call, agent, *_ = bridge
    mid = str(uuid.uuid4())
    call("send", message_id=mid, message="Original")
    with pytest.raises(ValueError, match="already belongs"):
        call("send", message_id=mid, message="Changed")
    assert len(agent.calls) == 1


@pytest.mark.parametrize("base_url,client_host,headers,expected", [
    ("http://127.0.0.1:8768", "127.0.0.1", {"X-Brutus-Chat": "forge"}, 200),
    ("http://127.0.0.1:8768", "127.0.0.1", {}, 403),
    ("http://127.0.0.1:8768", "127.0.0.1", {"X-Brutus-Chat": "forge", "Origin": "https://attacker.example"}, 403),
    ("http://attacker.example", "127.0.0.1", {"X-Brutus-Chat": "forge"}, 403),
    ("http://127.0.0.1:8768", "100.1.2.3", {"X-Brutus-Chat": "forge"}, 403),
])
def test_local_boundary(monkeypatch, base_url, client_host, headers, expected):
    app = FastAPI()
    app.include_router(forge_chat.router)
    calls = []
    monkeypatch.setattr(forge_chat, "remote", lambda request: calls.append(request) or {"threads": []})
    with TestClient(app, base_url=base_url, client=(client_host, 50000)) as client:
        response = client.post("/api/forge/request", json={"action": "list"}, headers=headers)
    assert response.status_code == expected
    assert bool(calls) == (expected == 200)
