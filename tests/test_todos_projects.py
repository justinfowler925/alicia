"""Capture pad + project scanner."""

import subprocess
from pathlib import Path

from alicia.projects import scan_projects
from alicia.todos import TodoStore


def test_todo_lifecycle(tmp_path: Path):
    s = TodoStore(tmp_path / "t.sqlite")
    a = s.add("look into HubSpot sync flakiness")
    assert a.status == "todo"
    s.update(a.id, status="doing")
    assert s.list()[0].status == "doing"
    s.update(a.id, status="done")
    assert s.list() == []                       # done hidden by default
    assert s.list(include_done=True)[0].status == "done"
    assert s.delete(a.id) is True


def test_status_write_moves_the_stage(tmp_path: Path):
    """"Mark it done" has to leave the board.

    The screen groups by stage; the voice and chat paths write `status`. While
    those were independent, closing an item by phrase set status='done' and left
    stage='Captured', so it stayed in the queue looking untouched — closed by one
    reader, open to the one Justin was actually looking at.
    """
    s = TodoStore(tmp_path / "t.sqlite")
    t = s.add("look into HubSpot sync flakiness")
    assert t.stage == "Captured"

    s.update(t.id, status="doing")
    assert s.get(t.id).stage == "Working"

    s.update(t.id, status="done")
    assert s.get(t.id).stage == "Done"
    assert s.by_stage() == {"Captured": [], "Refining": [], "Ready": [], "Working": []}

    # Reopening returns it to Ready rather than back to Captured: it has a title
    # by now, so sending it to the front of the pipeline would ask for one again.
    s.update(t.id, status="todo")
    assert s.get(t.id).stage == "Ready"

    # A capture still waiting on a title keeps its place instead of jumping the
    # queue to Ready.
    fresh = s.add("something new")
    s.update(fresh.id, status="todo")
    assert s.get(fresh.id).stage == "Captured"


def test_todo_rejects_garbage(tmp_path: Path):
    s = TodoStore(tmp_path / "t.sqlite")
    import pytest
    with pytest.raises(ValueError):
        s.add("   ")
    t = s.add("x" * 900)
    assert len(t.text) == 500                    # capped, not crashed
    with pytest.raises(ValueError):
        s.update(t.id, status="banana")


def test_todo_promote_records_ticket(tmp_path: Path):
    s = TodoStore(tmp_path / "t.sqlite")
    t = s.add("ship the thing")
    s.update(t.id, promoted_ticket="REV-999")
    assert s.list()[0].promoted_ticket == "REV-999"


def test_todo_work_stream_items(tmp_path: Path):
    s = TodoStore(tmp_path / "t.sqlite")
    a = s.add("fix the thing", tags="dsr, urgent", lane="In Progress")
    assert a.lane == "In Progress"
    assert a.status == "doing"
    assert a.tags == "dsr, urgent"
    s.update(a.id, lane="Blocked")
    item = s.list()[0]
    assert item.lane == "Blocked"
    assert item.status == "doing"
    s.update(a.id, tags="dsr, urgent, blocked")
    assert s.list()[0].tags == "dsr, urgent, blocked"


def test_scan_projects_reads_real_git(tmp_path: Path):
    repo = tmp_path / "myproj"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "feature/x", str(repo)], check=True)
    (repo / "a.txt").write_text("hello")
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.email=t@t", "-c", "user.name=t",
                    "commit", "-qm", "first cut of the widget"], check=True)
    (repo / "b.txt").write_text("uncommitted")
    (tmp_path / "not-a-repo").mkdir()            # must be skipped

    out = scan_projects(tmp_path, force=True)
    assert len(out) == 1
    p = out[0]
    assert p["name"] == "myproj"
    assert p["branch"] == "feature/x"
    assert p["last_commit"] == "first cut of the widget"
    assert p["dirty"] == 1
    assert p["at_risk"] is True
    assert p["activity"] == "hot"


def test_todo_survives_a_column_it_has_never_heard_of(tmp_path: Path):
    """A newer writer's column must not blind an older reader.

    On 2026-08-08 a feature branch run against the shared ~/.alicia/state added
    seven columns to `todos`; main's `Todo(**dict(row))` then raised TypeError on
    every read, /api/todos returned 500 and the Ideas pad went blank. Reading a row
    is forward-compatible or it is a time bomb on a shared database.
    """
    import sqlite3

    db = tmp_path / "t.sqlite"
    s = TodoStore(db)
    t = s.add("survives a schema it does not know")

    # Columns from a version that does not exist yet. The original test used
    # `stage`/`blocked`, which this version has since grown — so it started
    # failing on "duplicate column" and stopped exercising the thing it names.
    # A forward-compatibility test has to invent a future, not borrow one that
    # has already arrived.
    with sqlite3.connect(db) as c:
        c.execute("ALTER TABLE todos ADD COLUMN assignee TEXT NOT NULL DEFAULT ''")
        c.execute("ALTER TABLE todos ADD COLUMN effort_minutes INTEGER NOT NULL DEFAULT 0")

    assert s.get(t.id).text == "survives a schema it does not know"
    assert [x.id for x in s.list()] == [t.id]
    assert s.update(t.id, status="doing").status == "doing"
