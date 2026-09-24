"""REV-516 tests for Slack polling -> Canon InboxItem capture."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from alicia import __main__ as alicia_main
from alicia.canon import CanonStore, InboxItem, InboxStatus, WorkItem
from alicia.canon.slack import capture_slack_items


def _invoke(monkeypatch: pytest.MonkeyPatch, args: list[str]) -> None:
    monkeypatch.setattr(alicia_main.sys, "argv", ["alicia", *args])
    alicia_main.main()


def _incoming_message() -> dict[str, object]:
    return {
        "type": "message",
        "channel": "C_CAPTURE",
        "channel_name": "capture",
        "user": "U_SENDER",
        "ts": "1724414400.250000",
        "text": "  Please investigate the renewal discrepancy.\nKeep this exact.  ",
    }


def test_slack_work_signal_creates_verbatim_unreviewed_inbox_item() -> None:
    store = CanonStore()
    result = capture_slack_items(store, {"ok": True, "items": [_incoming_message()]})

    assert len(result.captured_ids) == 1
    assert result.duplicate_ids == ()
    item = store.get(InboxItem, result.captured_ids[0])
    assert item is not None
    assert item.raw_capture == "  Please investigate the renewal discrepancy.\nKeep this exact.  "
    assert item.status == InboxStatus.UNCATEGORIZED
    assert item.received_at == datetime.fromtimestamp(1724414400.25, tz=UTC)
    assert json.loads(item.source) == {
        "channel": "C_CAPTURE",
        "channel_name": "capture",
        "sender": "U_SENDER",
        "timestamp": "1724414400.250000",
        "type": "slack",
    }


def test_slack_capture_never_promotes_automatically(monkeypatch: pytest.MonkeyPatch) -> None:
    store = CanonStore()

    def _unexpected_promotion(*args: object, **kwargs: object) -> WorkItem:
        pytest.fail("Slack capture must not promote InboxItems")

    monkeypatch.setattr(store, "promote_inbox_item", _unexpected_promotion)
    result = capture_slack_items(store, {"items": [_incoming_message()]})

    assert len(result.captured_ids) == 1
    item = store.get(InboxItem, result.captured_ids[0])
    assert item is not None and item.status == InboxStatus.UNCATEGORIZED
    assert store.list(WorkItem) == []


def test_repeated_slack_poll_is_idempotent() -> None:
    store = CanonStore()

    first = capture_slack_items(store, {"items": [_incoming_message()]})
    second = capture_slack_items(store, {"items": [_incoming_message()]})

    assert len(first.captured_ids) == 1
    assert second.captured_ids == ()
    assert second.duplicate_ids == first.captured_ids
    assert len(store.list(InboxItem)) == 1


def test_cli_lists_and_shows_captured_inbox_item(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    db_path = tmp_path / "canon.db"
    store = CanonStore(db_path)
    result = capture_slack_items(store, {"items": [_incoming_message()]})
    item_id = result.captured_ids[0]
    store.close()

    _invoke(monkeypatch, ["canon", "--db", str(db_path), "inbox", "list"])
    list_output = capsys.readouterr().out
    assert item_id in list_output
    assert "uncategorized" in list_output
    assert "Please investigate the renewal discrepancy." in list_output

    _invoke(monkeypatch, ["canon", "--db", str(db_path), "inbox", "show", item_id])
    show_output = capsys.readouterr().out
    assert "Inbox Item" in show_output
    assert "Keep this exact." in show_output
    assert "C_CAPTURE" in show_output


def test_cli_capture_slack_reuses_atlas_peek(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    db_path = tmp_path / "canon.db"

    class FakeAtlasClient:
        def peek_slack(self, *, limit: int) -> dict[str, object]:
            assert limit == 7
            return {"items": [_incoming_message()]}

    monkeypatch.setattr("alicia.client.AtlasClient", FakeAtlasClient)
    _invoke(monkeypatch, ["canon", "--db", str(db_path), "inbox", "capture-slack", "--limit", "7"])

    assert "1 captured" in capsys.readouterr().out
    store = CanonStore(db_path)
    captured = store.list(InboxItem)
    store.close()
    assert len(captured) == 1
    assert captured[0].status == InboxStatus.UNCATEGORIZED


def test_cli_owner_review_promotes_captured_item(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = tmp_path / "canon.db"
    store = CanonStore(db_path)
    result = capture_slack_items(store, {"items": [_incoming_message()]})
    item_id = result.captured_ids[0]
    store.close()

    _invoke(
        monkeypatch,
        [
            "canon",
            "--db",
            str(db_path),
            "inbox",
            "promote",
            item_id,
            "--title",
            "Investigate renewal discrepancy",
        ],
    )

    store = CanonStore(db_path)
    item = store.get(InboxItem, item_id)
    work_items = store.list(WorkItem)
    store.close()
    assert item is not None and item.status == InboxStatus.PROMOTED
    assert len(work_items) == 1
    assert work_items[0].origin == item_id
