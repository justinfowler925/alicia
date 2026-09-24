"""Answer steering recovers when Atlas5 lost the inbox work order."""

from __future__ import annotations

import httpx

from alicia.client import AtlasClient
from alicia.config import AliciaCfg


def _resp(status: int, payload: dict | str) -> httpx.Response:
    req = httpx.Request("POST", "http://test")
    if isinstance(payload, dict):
        return httpx.Response(status, json=payload, request=req)
    return httpx.Response(status, text=payload, request=req)


def test_answer_steering_redrops_when_work_order_missing(monkeypatch):
    steering = {
        "ok": True,
        "resumed": False,
        "dispatch_error": (
            "no inbox work order retained for this ticket/action — "
            "re-drop the work order before resuming"
        ),
        "note": {"id": 1, "ticket_id": "REV-256"},
    }
    queue = {
        "items": [
            {
                "ticket_id": "REV-256",
                "action": "investigate",
                "ledger": {"action": "investigate", "attempts": 5, "run_state": "paused"},
            }
        ]
    }
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        calls.append(f"{request.method} {url}")
        if url.rstrip("/").endswith("/steering") and request.method == "POST":
            return _resp(200, steering)
        if url.rstrip("/").endswith("/operator/linear-queue") and request.method == "GET":
            return _resp(200, queue)
        if "/reset-attempts" in url:
            return _resp(200, {"ok": True, "rows_reset": 1})
        if url.rstrip("/").endswith("/operator/inbox") and request.method == "POST":
            return _resp(200, {"ok": True, "path": "/tmp/rev-256__investigate.md"})
        if url.rstrip("/").endswith("/resume") and request.method == "POST":
            return _resp(200, {"ok": True, "run_state": "queued"})
        return _resp(404, {"detail": url})

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
    out = client.answer_steering("REV-256", "channels are on both objects")

    assert out["resumed"] is True
    assert out.get("dispatch_error") is None
    assert out.get("recovered") is True
    assert out.get("redropped") is True
    assert any("/operator/inbox" in c for c in calls)
    assert any("/resume" in c for c in calls)
    assert any("/reset-attempts" in c for c in calls)


def test_answer_steering_success_passthrough(monkeypatch):
    payload = {"ok": True, "resumed": True, "dispatch_error": None, "note": {}}

    def handler(request: httpx.Request) -> httpx.Response:
        return _resp(200, payload)

    transport = httpx.MockTransport(handler)
    real_client = httpx.Client

    def factory(*a, **kw):
        kw = dict(kw)
        kw["transport"] = transport
        return real_client(*a, **kw)

    monkeypatch.setattr("alicia.client.httpx.Client", factory)
    client = AtlasClient(AliciaCfg(atlas_enabled=True, atlas5_url="http://atlas5.test"))
    out = client.answer_steering("REV-1", "ok")
    assert out["resumed"] is True
    assert "recovered" not in out
