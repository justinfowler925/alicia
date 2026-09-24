"""Operator uncap surface for attempts>=5."""

from __future__ import annotations

from unittest.mock import MagicMock

import httpx
from fastapi.testclient import TestClient

from alicia.client import AtlasClient
from alicia.config import AliciaCfg
from alicia.server import create_app


def _resp(status: int, payload: dict | str) -> httpx.Response:
    req = httpx.Request("GET", "http://test")
    if isinstance(payload, dict):
        return httpx.Response(status, json=payload, request=req)
    return httpx.Response(status, text=payload, request=req)


def test_list_capped_attempts_filters(monkeypatch):
    payload = {
        "items": [
            {
                "ticket_id": "REV-1",
                "title": "Under",
                "action": "investigate",
                "ledger": {"attempts": 4, "run_state": "failed", "notes": "x"},
            },
            {
                "ticket_id": "REV-2",
                "title": "Capped",
                "action": "build",
                "ledger": {"attempts": 5, "run_state": "failed", "notes": "quality"},
            },
        ]
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return _resp(200, payload)

    transport = httpx.MockTransport(handler)
    real_client = httpx.Client

    def factory(*a, **kw):
        kw = dict(kw)
        kw["transport"] = transport
        return real_client(*a, **kw)

    monkeypatch.setattr("alicia.client.httpx.Client", factory)
    client = AtlasClient(
        AliciaCfg(atlas_enabled=True, atlas6_url="http://127.0.0.1:8767", atlas5_url="http://atlas5.test")
    )
    rows = client.list_capped_attempts(min_attempts=5)
    assert len(rows) == 1
    assert rows[0]["ticket"] == "REV-2"
    assert rows[0]["attempts"] == 5


def test_reset_requires_confirm():
    cfg = AliciaCfg()
    app = create_app(cfg, start_watchdog=False)
    app.state.client = MagicMock()
    with TestClient(app) as tc:
        r = tc.post(
            "/api/capped_attempts/reset",
            json={"ticket_id": "REV-2", "confirm": False},
        )
        assert r.status_code == 400
        assert "confirm" in r.json()["detail"]


def test_reset_with_confirm_calls_client():
    cfg = AliciaCfg()
    app = create_app(cfg, start_watchdog=False)
    mock = MagicMock()
    mock.reset_attempts.return_value = {
        "ok": True,
        "ticket_id": "REV-2",
        "resumed": True,
    }
    app.state.client = mock
    with TestClient(app) as tc:
        r = tc.post(
            "/api/capped_attempts/reset",
            json={
                "ticket_id": "REV-2",
                "action": "investigate",
                "confirm": True,
                "resume": True,
            },
        )
        assert r.status_code == 200
        assert r.json()["ok"] is True
        mock.reset_attempts.assert_called_once()
