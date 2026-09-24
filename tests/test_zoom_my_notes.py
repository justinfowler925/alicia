from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from alicia.config import AliciaCfg
from alicia.server import create_app
from alicia.todos import TodoStore
from alicia.zoom_ingest import ZoomIngestStore
from alicia.zoom_my_notes import (
    note_markdown,
    recap_excerpt,
    render_transcript,
    sync_my_note,
)

META = {
    "note_id": "note-1",
    "note_name": "Allison / Justin 1:1",
    "note_link": "https://zoom.us/my-notes/note-1",
    "created_time": "2026-08-27T15:02:00Z",
    "modified_time": "2026-08-27T15:32:00Z",
}

TRANSCRIPT = {
    "note_id": "note-1",
    "note_name": "Allison / Justin 1:1",
    "note_url": "https://zoom.us/my-notes/note-1",
    "manual_note_content": "",
    "transcript": {
        "speakers": [
            {"speaker_id": "a", "display_name": "Allison"},
            {"speaker_id": "j", "display_name": "Justin"},
        ],
        "items": [
            {"speaker_id": "a", "text": "Please send the account list."},
            {"speaker_id": "j", "text": "I will send it today."},
        ],
    },
}

GENERATED = """## Summary
They reviewed the account list.

## Key Points
- The list is ready.

## Decisions
- Use the current segmentation.

## Action Items
- **Justin**: Send the account list today.
- **Allison**: Confirm the segmentation.
"""


def _stores(tmp_path: Path) -> tuple[TodoStore, ZoomIngestStore]:
    todos = TodoStore(tmp_path / "todos.sqlite")
    return todos, ZoomIngestStore(todos.path)


def test_render_transcript_uses_speaker_names() -> None:
    assert render_transcript(TRANSCRIPT).splitlines() == [
        "Allison: Please send the account list.",
        "Justin: I will send it today.",
    ]


def test_zoom_generated_content_wins_without_calling_brain() -> None:
    content = {**TRANSCRIPT, "generated_note_content": GENERATED}
    with patch("alicia.zoom_my_notes.complete") as complete:
        markdown, source = note_markdown(AliciaCfg(), META, content)
    assert markdown == GENERATED.strip()
    assert source == "zoom"
    complete.assert_not_called()


def test_blank_zoom_page_falls_back_to_transcript() -> None:
    with patch("alicia.zoom_my_notes.complete", return_value=GENERATED) as complete:
        markdown, source = note_markdown(AliciaCfg(), META, TRANSCRIPT)
    assert markdown == GENERATED.strip()
    assert source == "alicia"
    prompt = complete.call_args.args[1][1]["content"]
    assert "Allison: Please send the account list" in prompt


def test_empty_unfinished_note_remains_pending(tmp_path: Path) -> None:
    todos, store = _stores(tmp_path)
    result = sync_my_note(AliciaCfg(), META, {"note_id": "note-1"}, todos, store)
    assert result["state"] == "pending"
    assert todos.list(include_done=True) == []
    assert store.my_note("note-1") is None


def test_manual_note_is_kept_when_no_transcript_exists() -> None:
    content = {"note_id": "note-1", "manual_note_content": "Justin's written note"}
    markdown, source = note_markdown(AliciaCfg(), META, content)
    assert markdown == "Justin's written note"
    assert source == "manual"


def test_sync_creates_one_recap_and_only_justins_task(tmp_path: Path) -> None:
    todos, store = _stores(tmp_path)
    content = {**TRANSCRIPT, "generated_note_content": GENERATED}
    result = sync_my_note(
        AliciaCfg(), META, content, todos, store, owners=["justin"]
    )
    rows = todos.list(include_done=True)
    assert result["created"] == 2
    assert result["tasks_created"] == 1
    assert len(rows) == 2
    assert any(r.text == "Allison / Justin 1:1 — 2026-08-27" for r in rows)
    assert any("Send the account list" in r.text for r in rows)
    assert not any("Confirm the segmentation" in r.text for r in rows)
    recap = next(r for r in rows if " — 2026-08-27" in r.text)
    assert "## Key Points" in recap.raw
    assert recap.summary == "They reviewed the account list."
    assert store.my_note("note-1")["recap_todo_id"] == recap.id


def test_sync_is_idempotent_and_updates_one_recap(tmp_path: Path) -> None:
    todos, store = _stores(tmp_path)
    content = {**TRANSCRIPT, "generated_note_content": GENERATED}
    first = sync_my_note(AliciaCfg(), META, content, todos, store, owners=["justin"])
    changed_meta = {**META, "modified_time": "2026-08-27T15:40:00Z"}
    second = sync_my_note(AliciaCfg(), changed_meta, content, todos, store, owners=["justin"])
    assert first["created"] == 2
    assert second["state"] == "unchanged"
    assert second["created"] == 0
    assert len(todos.list(include_done=True)) == 2
    assert store.my_note("note-1")["modified_time"] == "2026-08-27T15:40:00Z"


def test_transcript_fallback_does_not_resummarize_an_unchanged_source(tmp_path: Path) -> None:
    todos, store = _stores(tmp_path)
    with patch("alicia.zoom_my_notes.complete", return_value=GENERATED) as complete:
        first = sync_my_note(AliciaCfg(), META, TRANSCRIPT, todos, store, owners=["justin"])
        second = sync_my_note(
            AliciaCfg(),
            {**META, "modified_time": "2026-08-27T15:40:00Z"},
            TRANSCRIPT,
            todos,
            store,
            owners=["justin"],
        )
    assert first["created"] == 2
    assert second["state"] == "unchanged"
    complete.assert_called_once()


def test_recap_excerpt_rejects_heading_noise() -> None:
    assert recap_excerpt(GENERATED) == "They reviewed the account list."


class _FakeMyNotesZoom:
    def __init__(self, *, owner: str = "justin.fowler@clearspeed.com") -> None:
        self.owner = owner
        self.content_calls: list[str] = []

    def current_user(self):
        return {"email": self.owner}

    def search_my_notes(self, _created_from, _created_to):
        return [dict(META)]

    def get_my_note(self, note_id, **_kwargs):
        self.content_calls.append(note_id)
        return {**TRANSCRIPT, "generated_note_content": GENERATED}


@pytest.fixture
def my_notes_api(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("ALICIA_STATE_DIR", str(tmp_path))
    fake = _FakeMyNotesZoom()
    monkeypatch.setattr("alicia.server.ZoomMyNotesClient", lambda *a, **k: fake)
    cfg = AliciaCfg(atlas6_url="http://127.0.0.1:8767", watchdog_enabled=False)
    with patch("alicia.server.AtlasClient") as cls:
        cls.return_value = MagicMock()
        with TestClient(create_app(cfg, start_watchdog=False)) as client:
            yield client, fake


def test_my_notes_endpoint_processes_one_and_then_skips_unchanged(my_notes_api) -> None:
    client, fake = my_notes_api
    first = client.post("/api/zoom/my-notes/poll", json={"owners": ["justin"]}).json()
    second = client.post("/api/zoom/my-notes/poll", json={"owners": ["justin"]}).json()
    assert first["ok"] is True
    assert first["notes_recent"] == 1
    assert first["created"] == 2
    assert second["skipped_unchanged"] == 1
    assert fake.content_calls == ["note-1"], "unchanged notes must not refetch the transcript"


def test_my_notes_endpoint_refuses_the_wrong_principal(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ALICIA_STATE_DIR", str(tmp_path))
    fake = _FakeMyNotesZoom(owner="somebody.else@clearspeed.com")
    monkeypatch.setattr("alicia.server.ZoomMyNotesClient", lambda *a, **k: fake)
    cfg = AliciaCfg(atlas6_url="http://127.0.0.1:8767", watchdog_enabled=False)
    with patch("alicia.server.AtlasClient") as cls:
        cls.return_value = MagicMock()
        with TestClient(create_app(cfg, start_watchdog=False)) as client:
            response = client.post("/api/zoom/my-notes/poll", json={})
    assert response.status_code == 503
    assert "expected justin.fowler@clearspeed.com" in response.json()["detail"]
