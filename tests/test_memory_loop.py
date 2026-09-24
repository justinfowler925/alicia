"""Wave 5 — Notes retrieval, todo→register, local lessons."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from alicia.chat_resolve import _lookup_intent, resolve_chat_reply
from alicia.config import AliciaCfg, LocalLLMCfg
from alicia.memory import MemoryStore
from alicia.todos import TodoStore
from alicia.tools import (
    _capture_note,
    _delete_note,
    _draft_lesson,
    _list_lessons,
    _list_notes,
    _list_working_notes,
    _promote_note,
    _update_note,
    build_default_registry,
)


def _cfg() -> AliciaCfg:
    return AliciaCfg(local_llm=LocalLLMCfg(enabled=True, model="m"))


def test_working_notes_search(tmp_path: Path):
    m = MemoryStore(tmp_path / "m.sqlite")
    m.add_working_note("HubSpot sync", "flaky catalog pull")
    m.add_working_note("Renewals", "Total_of_Cases clone bug")
    assert len(m.search_working_notes("hubspot")) == 1
    assert _list_working_notes(m, q="cases")["count"] == 1


def test_lessons_roundtrip(tmp_path: Path):
    m = MemoryStore(tmp_path / "m.sqlite")
    les = m.add_lesson("Never default cwd", "Empty repo_hint must skip, not invent atlas6.")
    assert les.id
    assert m.list_lessons()[0].title == "Never default cwd"
    assert m.search_lessons("repo_hint")[0].id == les.id
    out = _draft_lesson(m, title="Probe filter", body="Hide atlas5 proof burn-in from Justin.")
    assert out["ok"] is True
    assert "laptop only" in out["hint"].lower() or "not sent" in out["hint"].lower()
    assert _list_lessons(m)["count"] >= 2


def test_capture_and_promote_note(tmp_path: Path):
    todos = TodoStore(tmp_path / "t.sqlite")
    cap = _capture_note(todos, "ship the agents tab", tags="alicia")
    assert cap["ok"] is True
    note_id = cap["note"]["id"]
    listed = _list_notes(todos)
    assert listed["count"] == 1

    client = MagicMock()
    client.register.return_value = {"thread": {"external_id": "REV-900"}}
    promo = _promote_note(client, todos, note_id=note_id)
    assert promo["ok"] is True
    assert promo["ticket"] == "REV-900"
    assert todos.get(note_id).promoted_ticket == "REV-900"

    again = _promote_note(client, todos, note_id=note_id)
    assert again["already"] is True
    assert client.register.call_count == 1


def test_promote_by_query(tmp_path: Path):
    todos = TodoStore(tmp_path / "t.sqlite")
    todos.add("fix renewals rollup")
    client = MagicMock()
    client.register.return_value = {"thread": {"id": "abc"}}
    out = _promote_note(client, todos, q="renewals")
    assert out["ok"] is True
    assert out["ticket"] == "abc"


def test_lookup_memory_recipes():
    assert _lookup_intent("capture: look into Lucid retirement")[0] == "capture_note"
    assert _lookup_intent("note: Paula title ladder")[1]["text"].startswith("Paula")
    assert _lookup_intent("promote note abcdef012345") is None
    assert _lookup_intent("promote: fix renewals") is None
    assert _lookup_intent("lesson: Never invent cwd | Empty hint skips.")[0] == "draft_lesson"
    assert _lookup_intent("show my notes")[0] == "list_notes"
    assert _lookup_intent("what did we learn")[0] == "list_lessons"
    assert _lookup_intent("rename pilot deck to pilot one-pager") == (
        "update_note",
        {"q": "pilot deck", "text": "pilot one-pager"},
    )
    assert _lookup_intent("move pilot deck to doing") == (
        "update_note",
        {"q": "pilot deck", "lane": "doing"},
    )
    assert _lookup_intent("mark pilot deck done") == (
        "update_note",
        {"q": "pilot deck", "lane": "done"},
    )
    assert _lookup_intent("done: pilot deck") == (
        "update_note",
        {"q": "pilot deck", "lane": "done"},
    )
    assert _lookup_intent("delete note pilot deck") == (
        "delete_note",
        {"q": "pilot deck"},
    )
    assert _lookup_intent("drop idea abcdef012345") == (
        "delete_note",
        {"note_id": "abcdef012345"},
    )


def test_update_and_delete_note(tmp_path: Path):
    todos = TodoStore(tmp_path / "t.sqlite")
    note_id = _capture_note(todos, "scratch idea")["note"]["id"]
    renamed = _update_note(todos, note_id=note_id, text="kept idea")
    assert renamed["ok"] is True
    assert renamed["note"]["text"] == "kept idea"
    moved = _update_note(todos, q="kept idea", lane="doing")
    assert moved["ok"] is True
    assert moved["note"]["lane"] == "In Progress"
    gone = _delete_note(todos, q="kept idea")
    assert gone["ok"] is True
    assert gone["action"] == "delete"
    assert todos.get(note_id) is None


def test_registry_memory_tools(tmp_path: Path):
    client = MagicMock()
    mem = MemoryStore(tmp_path / "m.sqlite")
    todos = TodoStore(tmp_path / "t.sqlite")
    reg = build_default_registry(client, cfg=_cfg(), memory=mem, todos=todos)
    names = {t["name"] for t in reg.list_schemas()}
    for n in (
        "list_notes",
        "capture_note",
        "update_note",
        "delete_note",
        "list_working_notes",
        "draft_lesson",
        "list_lessons",
    ):
        assert n in names
    ro = build_default_registry(client, cfg=_cfg(), memory=mem, todos=todos, read_only=True)
    ro_names = {t["name"] for t in ro.list_schemas()}
    assert "capture_note" not in ro_names
    assert "update_note" not in ro_names
    assert "delete_note" not in ro_names
    assert "list_notes" in ro_names
    assert "list_lessons" in ro_names
    assert "promote_note" not in names


def test_resolve_capture_recipe(tmp_path: Path):
    client = MagicMock()
    client.status.return_value = {"blocked_justin": [], "completion_alarm": {}}
    client.list_awaiting_input.return_value = []
    mem = MemoryStore(tmp_path / "m.sqlite")
    todos = TodoStore(tmp_path / "t.sqlite")

    def fake_chat(cfg, messages, **_k):
        content = messages[-1]["content"]
        if "Tool result for capture_note" in content:
            return "Captured on your Notes pad."
        return "ok"

    with patch("alicia.chat_resolve.chat_completion", side_effect=fake_chat):
        with patch("alicia.tools.TodoStore", return_value=todos):
            with patch("alicia.tools.MemoryStore", return_value=mem):
                text, raw = resolve_chat_reply(
                    client, _cfg(), "capture: check Lucid org chart links", memory=mem
                )
    assert raw["path"] == "tool_forced"
    assert todos.list()
    assert "Captured" in text or "Notes" in text


def test_lessons_api(tmp_path: Path):
    from fastapi.testclient import TestClient

    from alicia.server import create_app

    cfg = AliciaCfg(watchdog_enabled=False)
    with patch("alicia.server.AtlasClient") as cls:
        cls.return_value = MagicMock()
        with patch("alicia.server.MemoryStore", return_value=MemoryStore(tmp_path / "m.sqlite")):
            app = create_app(cfg, start_watchdog=False)
            c = TestClient(app)
            assert c.get("/api/lessons").json()["lessons"] == []
            r = c.post("/api/lessons", json={"title": "t", "body": "learned X", "tags": "ops"})
            assert r.status_code == 200
            assert r.json()["body"] == "learned X"
            assert len(c.get("/api/lessons").json()["lessons"]) == 1
