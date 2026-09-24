"""Only the deployed artifact may reshape the databases the daemon is serving.

2026-08-08: a feature branch run by hand against ~/.alicia/state added seven
columns to the live `todos` table. Deployed main raised TypeError on every read
from that second on — /api/todos 500, the Ideas pad blank, 181 rows intact and
unreachable. The reader was made forward-compatible the same night; this is the
other half, the one that stops the write.
"""

import os
import sqlite3
from pathlib import Path

import pytest

from alicia import paths
from alicia.todos import TodoStore


def _shared(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Stand in for ~/.alicia/state, so the test never goes near the real one."""
    shared = tmp_path / "state"
    shared.mkdir()
    monkeypatch.setattr(paths, "SHARED_STATE_DIR", shared)
    monkeypatch.setattr(paths, "DEPLOYED_APP", tmp_path / "app")
    monkeypatch.delenv("ALICIA_ALLOW_SCHEMA_MIGRATION", raising=False)
    return shared


def test_a_branch_cannot_add_a_column_to_the_shared_table(tmp_path, monkeypatch):
    shared = _shared(tmp_path, monkeypatch)
    db = shared / "todos.sqlite"
    with sqlite3.connect(db) as c:  # the table as the daemon knows it, pre-migration
        c.execute(
            "CREATE TABLE todos (id TEXT PRIMARY KEY, text TEXT NOT NULL, "
            "status TEXT NOT NULL DEFAULT 'todo', created_at TEXT NOT NULL, "
            "updated_at TEXT NOT NULL, promoted_ticket TEXT NOT NULL DEFAULT '')"
        )

    with pytest.raises(RuntimeError, match="not the deployed artifact"):
        TodoStore(db)

    with sqlite3.connect(db) as c:
        cols = {r[1] for r in c.execute("PRAGMA table_info(todos)")}
    assert "tags" not in cols, "the refusal must leave the table exactly as it found it"


def test_a_scratch_state_dir_is_yours_to_reshape(tmp_path, monkeypatch):
    _shared(tmp_path, monkeypatch)
    s = TodoStore(tmp_path / "scratch" / "todos.sqlite")  # outside the shared dir
    assert s.add("branch work belongs here").text == "branch work belongs here"


def test_the_deployed_artifact_still_migrates(tmp_path, monkeypatch):
    shared = _shared(tmp_path, monkeypatch)
    monkeypatch.setattr(paths, "DEPLOYED_APP", Path(paths.__file__).resolve().parent.parent)
    db = shared / "todos.sqlite"
    s = TodoStore(db)
    assert s.add("the daemon may still upgrade its own schema").lane == "Inbox"


def test_the_suite_never_resolves_to_the_live_state_dir():
    """conftest's isolation, asserted rather than assumed."""
    assert os.environ.get("ALICIA_STATE_DIR"), "conftest must pin a scratch state dir"
    assert paths.default_state_dir() != Path.home() / ".alicia" / "state"
