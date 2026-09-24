"""Owner Canon surface: inbox → today → review → sealed card → closure."""

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from alicia.canon.models import ExecutionCard
from alicia.canon.surface import (
    capture_inbox,
    dogfood_pipeline,
    open_canon_store,
    promote,
    snapshot,
)
from alicia.config import AliciaCfg
from alicia.server import create_app


def test_dogfood_pipeline_reaches_closure(tmp_path):
    store = open_canon_store(tmp_path / "canon.sqlite")
    try:
        result = dogfood_pipeline(store, marker="test-proof")
        assert result["state"] == "closure"
        assert result["execution_card_status"] == "sealed"
        assert result["owner"] == "justin.fowler@clearspeed.com"
        card = store.get(ExecutionCard, result["execution_card_id"])
        assert card is not None
        card.scope = "mutated"
        with pytest.raises(ValueError, match="immutable"):
            store.save(card)
    finally:
        store.close()


def test_promoted_inbox_lands_on_today(tmp_path):
    store = open_canon_store(tmp_path / "canon.sqlite")
    try:
        item = capture_inbox(
            store, raw_capture="visible on Inbox then Today", source="test"
        )
        before = snapshot(store)
        assert any(row["id"] == item.id for row in before["inbox"])
        work = promote(
            store, item.id, title="Inbox to Today", description=item.raw_capture
        )
        after = snapshot(store)
        assert all(row["id"] != item.id for row in after["inbox"])
        assert any(row["id"] == work.id and row["state"] == "triage" for row in after["today"])
    finally:
        store.close()


def test_canon_http_capture_and_snapshot(tmp_path, monkeypatch):
    monkeypatch.setenv("ALICIA_CANON_DB_PATH", str(tmp_path / "canon.sqlite"))
    monkeypatch.setenv("ALICIA_OWNER_TOKEN", "test-owner-token")
    cfg = AliciaCfg(watchdog_enabled=False)
    with patch("alicia.server.AtlasClient") as cls:
        cls.return_value = MagicMock()
        client = TestClient(create_app(cfg, start_watchdog=False))
        captured = client.post(
            "/api/canon/inbox",
            headers={"X-Alicia-Owner-Token": "test-owner-token"},
            json={"raw_capture": "prove the inbox from HTTP", "source": "test"},
        )
        assert captured.status_code == 200
        body = captured.json()
        assert body["ok"] is True
        assert body["item"]["raw_capture"] == "prove the inbox from HTTP"
        snap = client.get("/api/canon")
        assert snap.status_code == 200
        data = snap.json()
        assert any(item["id"] == body["item"]["id"] for item in data["inbox"])
        assert "today" in data and "review" in data
        assert data["db_path"].endswith("canon.sqlite")
        health = client.get("/api/healthz")
        assert health.status_code == 200
        hz = health.json()
        assert "db_path" in hz["canon"]
