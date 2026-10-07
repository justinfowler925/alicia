"""Spend caps, auth audit log, and Tailscale node allowlist."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from alicia import auth_audit, spend_limits
from alicia.config import AliciaCfg, CursorCloudCfg, OpenAICfg
from alicia import cursor_cloud, openai_lane
from alicia.studio_access import install, peer_allowed

pytestmark = pytest.mark.no_auto_owner


def test_spend_cap_blocks_fourth_hourly(tmp_path, monkeypatch):
    ledger = tmp_path / "spend.json"
    monkeypatch.setenv("ALICIA_SPEND_LEDGER", str(ledger))
    base = 1_700_000_000.0
    for i in range(3):
        out = spend_limits.check_and_record(
            "cursor_cloud", max_per_hour=3, max_per_day=10, now=base + i
        )
        assert out["ok"] is True
    blocked = spend_limits.check_and_record(
        "cursor_cloud", max_per_hour=3, max_per_day=10, now=base + 10
    )
    assert blocked["ok"] is False
    assert "hour" in blocked["error"]


def test_cursor_cloud_respects_spend_cap(tmp_path, monkeypatch):
    monkeypatch.setenv("ALICIA_SPEND_LEDGER", str(tmp_path / "spend.json"))
    cursor_cloud.reset_cloud_http_flag()
    cfg = AliciaCfg(cursor_cloud=CursorCloudCfg(enabled=True, max_per_hour=1, max_per_day=1))

    def fake_prompt(message, **kwargs):
        return {
            "ok": True,
            "agent_id": "bc-cap",
            "reply": "ok",
            "status": "completed",
            "model": "composer-2.5",
            "agents_spawned": 1,
        }

    first = cursor_cloud.run_cursor_cloud(
        cfg,
        "one",
        repo_url="https://github.com/justinfowler925/alicia",
        prompt_fn=fake_prompt,
    )
    assert first["ok"] is True
    second = cursor_cloud.run_cursor_cloud(
        cfg,
        "two",
        repo_url="https://github.com/justinfowler925/alicia",
        prompt_fn=fake_prompt,
    )
    assert second["ok"] is False
    assert "spend cap" in second["error"]
    assert second["cloud_http_attempted"] is False


def test_openai_respects_spend_cap(tmp_path, monkeypatch):
    monkeypatch.setenv("ALICIA_SPEND_LEDGER", str(tmp_path / "spend.json"))
    cfg = AliciaCfg(openai=OpenAICfg(enabled=True, max_per_hour=1, max_per_day=1))

    def fake(message, oai):
        return {"ok": True, "executor": "openai", "attribution": "OpenAI", "reply": "hi"}

    assert openai_lane.run_openai(cfg, "a", prompt_fn=fake)["ok"] is True
    denied = openai_lane.run_openai(cfg, "b", prompt_fn=fake)
    assert denied["ok"] is False
    assert "spend cap" in denied["error"]


def test_auth_audit_records_and_endpoint_reads(tmp_path, monkeypatch):
    monkeypatch.setenv("ALICIA_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("ALICIA_AUTH_AUDIT", str(tmp_path / "state" / "auth-audit.jsonl"))
    monkeypatch.setenv("ALICIA_OWNER_TOKEN", "audit-owner")
    monkeypatch.setenv("ALICIA_SERVE_PROOF", "proof")
    auth_audit.record("owner_action", ok=False, detail="auth_required", path="/api/todos")
    auth_audit.record("owner_action", ok=True, via="token", path="/api/todos")
    rows = auth_audit.read_recent(limit=10)
    assert len(rows) == 2
    assert rows[0]["ok"] is False
    assert rows[1]["via"] == "token"

    from unittest.mock import MagicMock
    from alicia.server import create_app

    with patch("alicia.server.AtlasClient") as atlas:
        atlas.return_value = MagicMock()
        client = TestClient(create_app(AliciaCfg(watchdog_enabled=False), start_watchdog=False))
    denied = client.get("/api/auth/audit")
    assert denied.status_code == 401
    ok = client.get("/api/auth/audit", headers={"X-Alicia-Owner-Token": "audit-owner"})
    assert ok.status_code == 200
    body = ok.json()
    assert body["ok"] is True
    assert body["count"] >= 2


def test_node_allowlist_rejects_unknown_peer(monkeypatch):
    monkeypatch.setenv("ALICIA_TAILSCALE_ALLOWED_NODES", "JFMacM5,hyper")
    monkeypatch.delenv("ALICIA_TAILSCALE_ALLOWED_TAGS", raising=False)

    def fake_whois(ip):
        return {"Node": {"HostName": "stranger", "Addresses": [ip]}}

    with patch("alicia.studio_access._whois", side_effect=fake_whois):
        allowed, reason, label = peer_allowed({"x-forwarded-for": "100.90.100.85"})
    assert allowed is False
    assert reason == "node_not_allowlisted"
    assert label == "stranger"


def test_node_allowlist_accepts_tagged_peer(monkeypatch):
    monkeypatch.delenv("ALICIA_TAILSCALE_ALLOWED_NODES", raising=False)
    monkeypatch.setenv("ALICIA_TAILSCALE_ALLOWED_TAGS", "tag:owner")

    def fake_whois(ip):
        return {"Node": {"HostName": "JFMacM5", "Tags": ["tag:owner"], "Addresses": [ip]}}

    with patch("alicia.studio_access._whois", side_effect=fake_whois):
        allowed, reason, label = peer_allowed({"x-forwarded-for": "100.90.100.85"})
    assert allowed is True
    assert reason == "peer_ok"
    assert label == "JFMacM5"


def test_middleware_enforces_node_allowlist(monkeypatch):
    monkeypatch.setenv("ALICIA_PUBLIC_ORIGIN", "https://studio.example:8768")
    monkeypatch.setenv("ALICIA_TAILSCALE_OWNER", "owner@example.com")
    monkeypatch.setenv("ALICIA_SERVE_PROOF", "real-proof")
    monkeypatch.setenv("ALICIA_TAILSCALE_ALLOWED_NODES", "JFMacM5")
    app = FastAPI()
    install(app)

    @app.get("/who")
    def who(request: Request):
        return {"studio_owner": bool(getattr(request.state, "studio_owner", False))}

    client = TestClient(app, client=("127.0.0.1", 4567), base_url="https://studio.example:8768")
    headers = {
        "tailscale-user-login": "owner@example.com",
        "origin": "https://studio.example:8768",
        "X-Alicia-Serve-Proof": "real-proof",
        "X-Forwarded-For": "100.1.1.1",
    }
    with patch(
        "alicia.studio_access._whois",
        return_value={"Node": {"HostName": "bad-device", "Addresses": ["100.1.1.1"]}},
    ):
        assert client.get("/who", headers=headers).status_code == 403
    with patch(
        "alicia.studio_access._whois",
        return_value={"Node": {"HostName": "JFMacM5", "Addresses": ["100.1.1.1"]}},
    ):
        assert client.get("/who", headers=headers).json()["studio_owner"] is True
