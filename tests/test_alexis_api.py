from unittest.mock import AsyncMock, patch

import httpx
import pytest
from fastapi.testclient import TestClient

from brutus.config import BrutusCfg, VoiceCfg
from brutus.server import create_app
from brutus.session import SessionStore


@pytest.fixture
def client(tmp_path):
    cfg = BrutusCfg(voice=VoiceCfg(enabled=True, anam_api_key="private-provider-key", anam_avatar_id="alexis-avatar"),
                    alexis_brain_url="https://brain.example", alexis_brain_token="private-surface-key")
    app = create_app(cfg, start_watchdog=False)
    app.state.sessions = SessionStore(tmp_path / "sessions.sqlite")
    with TestClient(app) as client:
        yield client


def test_homepage_is_preserved_and_pages_have_separate_navigation(client):
    home = client.get("/brutus").text
    assert 'id="alexis-frame"' not in home
    assert '/static/alexis.js' not in home
    assert 'id="supervisor-agents"' in home
    assert 'href="/"' in home and 'href="/atlas"' in home
    assert 'id="alexis-frame"' in client.get("/").text
    assert 'id="alexis-frame"' in client.get("/alexis").text
    assert 'title="Atlas workspace"' in client.get("/atlas").text
    assert client.get("/static/alexis.jpg").headers["content-type"] == "image/jpeg"


def test_avatar_token_is_audio_only_and_does_not_expose_credentials(client):
    sid = client.post("/api/session/open", json={}).json()["session_id"]
    response = httpx.Response(200, json={"sessionToken": "ephemeral-token"}, request=httpx.Request("POST", "https://api.anam.ai"))
    with patch("brutus.alexis.httpx.AsyncClient.post", new=AsyncMock(return_value=response)) as post:
        result = client.post(f"/api/session/{sid}/alexis-token")
        assert result.json() == {"sessionToken": "ephemeral-token"}
        assert result.headers["cache-control"] == "no-store"
        assert post.call_args.kwargs["json"]["personaConfig"]["enableAudioPassthrough"] is True
        assert "private-provider-key" not in result.text


def test_cross_origin_cannot_start_avatar_or_save_feedback(client):
    assert client.post("/api/session/no/alexis-token", headers={"Origin": "https://attacker.example"}).status_code == 403
    assert client.post("/api/session/no/feedback", headers={"Origin": "https://attacker.example"},
                       json={"turn_id": 1, "dimension": "conversation", "rating": "helpful"}).status_code == 403


def test_feedback_rejects_fabricated_and_foreign_turns(client):
    store = client.app.state.sessions
    sid, other = store.open_session(), store.open_session()
    turn = store.append_turn(other, "brutus", "Other session answer", meta={"central_turn_id": "central"})
    result = client.post(f"/api/session/{sid}/feedback", json={"turn_id": turn.id, "dimension": "conversation", "rating": "helpful"})
    assert result.status_code == 404


def test_feedback_central_write_precedes_local_ack(client):
    store = client.app.state.sessions
    sid = store.open_session()
    turn = store.append_turn(sid, "brutus", "Answer", meta={"central_turn_id": "central"})
    body = {"turn_id": turn.id, "dimension": "presentation", "rating": "helpful"}
    with patch("brutus.alexis.httpx.post", side_effect=httpx.ConnectError("offline")):
        assert client.post(f"/api/session/{sid}/feedback", json=body).status_code == 503
    assert client.get(f"/api/session/{sid}/feedback").json()["count"] == 0
    response = httpx.Response(200, json={"ok": True}, request=httpx.Request("POST", "https://brain.example"))
    with patch("brutus.alexis.httpx.post", return_value=response) as post:
        assert client.post(f"/api/session/{sid}/feedback", json=body).status_code == 200
        assert post.call_args.kwargs["json"]["turn_id"] == "central"
    assert client.get(f"/api/session/{sid}/feedback").json()["count"] == 1


@pytest.mark.parametrize("status, message", [(402, "billing"), (429, "limit reached"), (401, "credential")])
def test_avatar_provisioning_failure_is_actionable_without_leaking_provider_body(client, status, message):
    sid = client.post("/api/session/open", json={}).json()["session_id"]
    response = httpx.Response(status, json={"secret": "provider-private-detail"},
                              request=httpx.Request("POST", "https://api.anam.ai"))
    with patch("brutus.alexis.httpx.AsyncClient.post", new=AsyncMock(return_value=response)):
        result = client.post(f"/api/session/{sid}/alexis-token")
    assert result.status_code == 503
    assert message in result.json()["detail"]
    assert "provider-private-detail" not in result.text
