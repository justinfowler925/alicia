from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from brutus.canon.models import InboxItem, WorkItem
from brutus.canon.store import CanonStore
from brutus.config import BrutusCfg
from brutus.server import create_app


def test_adapter_event_endpoint_is_authenticated_idempotent_and_non_authoritative(tmp_path, monkeypatch):
    db = tmp_path / "canon.sqlite"
    monkeypatch.setenv("BRUTUS_CANON_DB_PATH", str(db))
    monkeypatch.setenv("BRUTUS_ADAPTER_TOKEN", "test-adapter-token")
    store = CanonStore(db)
    work = WorkItem(title="Atlas handoff")
    store.save(work)
    store.close()
    with patch("brutus.server.AtlasClient") as cls:
        cls.return_value = MagicMock()
        client = TestClient(create_app(BrutusCfg(watchdog_enabled=False), start_watchdog=False))
        body = {
            "work_item_id": work.id,
            "event_id": "atlas:job:1:completed",
            "event_type": "completed",
            "surface": "atlas",
            "source_locator": "atlas://jobs/1",
        }
        assert client.post("/api/workflow/events", json=body).status_code == 401
        first = client.post(
            "/api/workflow/events",
            headers={"X-Brutus-Adapter-Token": "test-adapter-token"},
            json=body,
        )
        second = client.post(
            "/api/workflow/events",
            headers={"X-Brutus-Adapter-Token": "test-adapter-token"},
            json=body,
        )
    assert first.status_code == 200 and first.json()["created"] is True
    assert second.status_code == 200 and second.json()["created"] is False
    check = CanonStore(db)
    assert check.get(WorkItem, work.id).state.value == "triage"
    check.close()


def test_feedback_endpoint_dispositions_every_report():
    with patch("brutus.server.AtlasClient") as cls:
        cls.return_value = MagicMock()
        client = TestClient(create_app(BrutusCfg(watchdog_enabled=False), start_watchdog=False))
        response = client.post(
            "/api/workflow/feedback/batch",
            json={
                "reports": [
                    {"id": "one", "raw_capture": "broken", "source": "voice", "surface": "home"},
                    {"id": "two", "raw_capture": "wrong", "source": "image", "surface": "home"},
                ]
            },
        )
    assert response.status_code == 200
    assert response.json()["reports"] == response.json()["dispositioned"] == 2
    assert len(response.json()["batches"]) == 2


def test_feedback_create_requires_owner_and_persists_one_item_per_batch(tmp_path, monkeypatch):
    db = tmp_path / "canon.sqlite"
    monkeypatch.setenv("BRUTUS_CANON_DB_PATH", str(db))
    monkeypatch.setenv("BRUTUS_OWNER_TOKEN", "test-owner-token")
    body = {
        "reports": [
            {
                "id": "voice-one",
                "raw_capture": "broken button",
                "source": "voice",
                "surface": "checkout",
                "acceptance_contract": "button submits",
                "release_path": "web",
                "rollback_path": "revert",
                "verified": True,
            },
            {
                "id": "image-one",
                "raw_capture": "screenshot://one",
                "source": "screenshot",
                "surface": "checkout",
                "acceptance_contract": "button submits",
                "release_path": "web",
                "rollback_path": "revert",
                "verified": True,
            },
        ]
    }
    with patch("brutus.server.AtlasClient") as cls:
        cls.return_value = MagicMock()
        client = TestClient(create_app(BrutusCfg(watchdog_enabled=False), start_watchdog=False))
        assert client.post("/api/workflow/feedback/batch/create", json=body).status_code == 401
        response = client.post(
            "/api/workflow/feedback/batch/create",
            headers={"X-Brutus-Owner-Token": "test-owner-token"},
            json=body,
        )
    assert response.status_code == 200
    assert len(response.json()["work_item_ids"]) == 1
    store = CanonStore(db)
    assert len(store.list(InboxItem)) == 2
    assert len(store.list(WorkItem)) == 1
    store.close()
