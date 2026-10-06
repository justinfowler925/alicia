"""Security hardening: owner auth on spend paths, Serve proof, redaction gate."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from alicia.config import AliciaCfg
from alicia.redact import RedactionGateError, contains_secret, gate_text, redact_secrets
from alicia.server import create_app
from alicia.studio_access import install

pytestmark = pytest.mark.no_auto_owner


def _client(tmp_path: Path, monkeypatch) -> TestClient:
    monkeypatch.setenv("ALICIA_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("ALICIA_CANON_DB_PATH", str(tmp_path / "canon.sqlite"))
    monkeypatch.setenv("ALICIA_OWNER_TOKEN", "test-owner-token")
    monkeypatch.setenv("ALICIA_SERVE_PROOF", "test-serve-proof")
    with patch("alicia.server.AtlasClient") as atlas:
        atlas.return_value = MagicMock()
        return TestClient(create_app(AliciaCfg(watchdog_enabled=False), start_watchdog=False))


def test_unauthenticated_say_and_approve_are_denied(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    opened = client.post(
        "/api/session/open",
        headers={"X-Alicia-Owner-Token": "test-owner-token"},
        json={"title": "sec"},
    )
    assert opened.status_code == 200
    sid = opened.json()["session_id"]

    assert client.post(f"/api/session/{sid}/say", json={"message": "hi"}).status_code == 401
    assert client.post(f"/api/session/{sid}/artifact/x/approve").status_code == 401
    assert client.post("/api/todos", json={"text": "nope"}).status_code == 401
    assert client.post(
        "/api/specialists/route",
        json={"task": "launch forge please", "dry_run": False},
    ).status_code == 401


def test_owner_token_allows_say_and_csrf_session(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    with patch("alicia.server.ConversationManager") as mgr:
        mgr.return_value = MagicMock()
        # create_app already built conversation; patch handle on app state instead
        pass
    opened = client.post(
        "/api/session/open",
        headers={"X-Alicia-Owner-Token": "test-owner-token"},
        json={"title": "sec"},
    )
    sid = opened.json()["session_id"]
    client.app.state.conversation.handle = MagicMock(
        return_value=MagicMock(as_dict=lambda: {"ok": True, "reply": "ack"})
    )
    ok = client.post(
        f"/api/session/{sid}/say",
        headers={"X-Alicia-Owner-Token": "test-owner-token"},
        json={"message": "hello", "wait": False},
    )
    assert ok.status_code == 200

    paired = client.post("/api/auth/session", json={"token": "test-owner-token"})
    assert paired.status_code == 200
    csrf = paired.json()["csrf"]
    todo = client.post(
        "/api/todos",
        headers={"X-Alicia-CSRF": csrf},
        json={"text": "csrf note"},
    )
    assert todo.status_code == 200


def test_forged_tailscale_headers_without_proof_are_not_studio_owner(monkeypatch):
    monkeypatch.setenv("ALICIA_PUBLIC_ORIGIN", "https://studio.example:8768")
    monkeypatch.setenv("ALICIA_TAILSCALE_OWNER", "owner@example.com")
    monkeypatch.setenv("ALICIA_SERVE_PROOF", "real-proof")
    app = FastAPI()
    install(app)

    @app.get("/who")
    def who(request: Request):
        return {"studio_owner": bool(getattr(request.state, "studio_owner", False))}

    client = TestClient(app, client=("127.0.0.1", 4567), base_url="https://studio.example:8768")
    forged = {
        "tailscale-user-login": "owner@example.com",
        "origin": "https://studio.example:8768",
    }
    # Public Host + forged login without Serve proof is rejected at the door.
    assert client.get("/who", headers=forged).status_code == 403
    proven = {**forged, "X-Alicia-Serve-Proof": "real-proof"}
    assert client.get("/who", headers=proven).json()["studio_owner"] is True


def test_private_studio_requires_proof_for_writes(monkeypatch):
    monkeypatch.setenv("ALICIA_PUBLIC_ORIGIN", "https://studio.example:8768")
    monkeypatch.setenv("ALICIA_TAILSCALE_OWNER", "owner@example.com")
    monkeypatch.setenv("ALICIA_SERVE_PROOF", "real-proof")
    app = FastAPI()
    install(app)

    @app.post("/write")
    def write():
        return {"ok": True}

    client = TestClient(app, client=("127.0.0.1", 4567), base_url="https://studio.example:8768")
    headers = {
        "tailscale-user-login": "owner@example.com",
        "origin": "https://studio.example:8768",
    }
    assert client.post("/write", headers=headers).status_code == 403
    assert client.post("/write", headers={**headers, "X-Alicia-Serve-Proof": "real-proof"}).status_code == 200


def test_live_specialist_route_requires_owner_and_names_requester(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    denied = client.post(
        "/api/specialists/route",
        json={"task": "do a thing", "dry_run": False},
    )
    assert denied.status_code == 401
    dry = client.post(
        "/api/specialists/route",
        json={"task": "do a thing", "dry_run": True},
    )
    assert dry.status_code == 200
    with patch("alicia.specialist_router._launch_forge", return_value={"run_id": "x", "status": "queued"}):
        live = client.post(
            "/api/specialists/route",
            headers={"X-Alicia-Owner-Token": "test-owner-token"},
            json={"task": "do a thing", "dry_run": False, "specialist": "forge"},
        )
    assert live.status_code == 200
    assert live.json()["requester"] == "token"


def test_state_dir_permissions_are_private(tmp_path, monkeypatch):
    from alicia.paths import harden_state_permissions

    state = tmp_path / "state"
    state.mkdir()
    db = state / "canon.sqlite"
    db.write_text("")
    db.chmod(0o644)
    state.chmod(0o755)
    harden_state_permissions(state)
    assert oct(state.stat().st_mode & 0o777) == "0o700"
    assert oct(db.stat().st_mode & 0o777) == "0o600"


def test_redaction_gate_strips_secrets_and_strict_mode_bites():
    planted = "Authorization: Bearer sk-abcdefghijklmnopqrstuvwxyz012345"
    cleaned = redact_secrets(planted)
    assert "sk-abcdefghijklmnopqrstuvwxyz012345" not in cleaned
    assert contains_secret(planted)
    assert not contains_secret(cleaned)
    with pytest.raises(RedactionGateError):
        gate_text(planted, strict=True)
    assert "[redacted" in gate_text(planted, strict=False)


def test_outbox_write_redacts_secrets(tmp_path, monkeypatch):
    monkeypatch.setenv("ALICIA_STATE_DIR", str(tmp_path))
    from alicia import resilience

    item = resilience.enqueue_say("s1", "here is sk-abcdefghijklmnopqrstuvwxyz012345", "text")
    raw = item.path().read_text()
    assert "sk-abcdefghijklmnopqrstuvwxyz012345" not in raw
    assert "[redacted" in raw
