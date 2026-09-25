"""Alicia imports what Scout ingested, through the same ingestion code, exactly once."""

from __future__ import annotations

import hashlib
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

from alicia import scout_import
from alicia.config import AliciaCfg
from alicia.todos import TodoStore
from alicia.zoom_ingest import ZoomIngestStore

GENERATED = """## Summary
They reviewed the account list.

## Action Items
- **Justin**: Send the account list today.
"""

RECORDS = {
    "zoom-summary": [{"seq": 1, "key": "uuid-1", "version": "v", "fetched_at": "", "payload": {
        "meeting_uuid": "uuid-1", "topic": "Pipeline review", "start_time": "2026-09-24T15:00:00Z",
        "meeting_summary": {"summary_content": "## Next steps\n- Justin to update the forecast"}}}],
    "zoom-my-note": [{"seq": 2, "key": "note-1", "version": "v", "fetched_at": "", "payload": {
        "meta": {"note_id": "note-1", "note_name": "Allison / Justin 1:1", "created_time": "2026-09-24T15:00:00Z",
                 "modified_time": "2026-09-24T15:30:00Z"},
        "content": {"note_id": "note-1", "generated_note_content": GENERATED}}}],
    "sf-meeting-note": [{"seq": 3, "key": "a0X1", "version": "v", "fetched_at": "", "payload": {
        "Id": "a0X1", "Name": "Hartford sync", "Host_Email__c": "justin.fowler@clearspeed.com",
        "Action_Items_Raw__c": '[{"action": "Send the MSA redlines", "owner_email": "justin.fowler@clearspeed.com"}]'}}],
    "github-canon": [],
}


class FakeScout(BaseHTTPRequestHandler):
    backups: list[tuple[bytes, str]] = []
    fail_kind: str = ""

    def log_message(self, *args):  # noqa: D401 — silence test output
        pass

    def _send(self, body: dict, status: int = 200) -> None:
        raw = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):  # noqa: N802
        assert self.headers["Authorization"] == "Bearer test-token"
        q = parse_qs(urlparse(self.path).query)
        kind, after = q["kind"][0], int(q["after"][0])
        if kind == self.fail_kind:
            return self._send({"error": "down"}, 503)
        rows = [r for r in RECORDS[kind] if r["seq"] > after]
        self._send({"kind": kind, "records": rows, "next": rows[-1]["seq"] if rows else after, "more": False})

    def do_PUT(self):  # noqa: N802
        data = self.rfile.read(int(self.headers["Content-Length"]))
        FakeScout.backups.append((data, self.headers["X-SHA256"]))
        self._send({"ok": True, "bytes": len(data)})


@pytest.fixture
def scout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    server = HTTPServer(("127.0.0.1", 0), FakeScout)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    token = tmp_path / "token"
    token.write_text("test-token")
    monkeypatch.setenv("ALICIA_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("ALICIA_HOME", str(tmp_path))
    monkeypatch.setenv("ALICIA_SCOUT_TOKEN_FILE", str(token))
    monkeypatch.setattr(scout_import, "SCOUT_URL", f"http://127.0.0.1:{server.server_port}")
    FakeScout.backups, FakeScout.fail_kind = [], ""
    yield
    server.shutdown()


def _stores(tmp_path: Path) -> tuple[TodoStore, ZoomIngestStore]:
    todos = TodoStore(tmp_path / "todos.sqlite")
    return todos, ZoomIngestStore(todos.path)


def test_every_kind_lands_once_and_canon_backup_is_handed_over(scout, tmp_path: Path) -> None:
    todos, store = _stores(tmp_path)
    first = scout_import.import_once(cfg=AliciaCfg(), todos=todos, zoom_store=store)
    assert first["errors"] == {}
    assert first["imported"] == {"zoom-summary": 1, "zoom-my-note": 1, "sf-meeting-note": 1, "github-canon": 0}
    texts = [t.text for t in todos.list()]
    # Default "notes" mode takes cards from My Notes; a summary alone records the meeting.
    assert "uuid-1" in store.resolved_uuids()
    assert any("account list" in t for t in texts)
    assert any("MSA redlines" in t for t in texts)
    count = len(texts)

    data, digest = FakeScout.backups[0]
    assert data.startswith(b"SQLite format 3\x00") and hashlib.sha256(data).hexdigest() == digest

    again = scout_import.import_once(cfg=AliciaCfg(), todos=todos, zoom_store=store)
    assert sum(again["imported"].values()) == 0
    assert len(todos.list()) == count
    assert len(FakeScout.backups) == 1, "backup is daily, not per import"


def test_a_failing_kind_is_retried_not_skipped(scout, tmp_path: Path) -> None:
    todos, store = _stores(tmp_path)
    FakeScout.fail_kind = "sf-meeting-note"
    first = scout_import.import_once(cfg=AliciaCfg(), todos=todos, zoom_store=store)
    assert "sf-meeting-note" in first["errors"]
    assert not any("MSA redlines" in t.text for t in todos.list())
    FakeScout.fail_kind = ""
    second = scout_import.import_once(cfg=AliciaCfg(), todos=todos, zoom_store=store)
    assert second["imported"]["sf-meeting-note"] == 1
    assert any("MSA redlines" in t.text for t in todos.list())
