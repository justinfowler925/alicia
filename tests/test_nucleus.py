from __future__ import annotations

from unittest.mock import MagicMock, patch

from alicia.config import AliciaCfg
from alicia.memory import MemoryStore
from alicia.nucleus import build_operating_graph, invalidate_nucleus_cache, nucleus_view
from alicia.tools import build_default_registry


def test_operating_graph_joins_native_projects_threads_and_tickets():
    projects = [
        {
            "project_id": "github.com/clearspeedrevops/alicia",
            "name": "alicia",
            "path": "/Users/justinfowler/Projects/alicia",
            "workspace": "alicia",
            "dirty": 2,
            "unpushed": 1,
            "last_commit_epoch": 100,
            "activity": "hot",
        }
    ]
    agents = [
        {
            "id": "codex:local:abc",
            "surface": "codex",
            "title": "build nucleus",
            "cwd": "/Users/justinfowler/Projects/alicia",
            "mtime": 200,
            "state": "waiting",
            "live": False,
        }
    ]
    issues = [
        {
            "id": "linear-1",
            "ticket": "REV-900",
            "title": "Optimize alicia",
            "description": "Alicia Nucleus command center",
            "state": "In Review",
            "state_type": "started",
            "assignee_email": "justin.fowler@clearspeed.com",
            "project_id": "",
            "project_name": "",
            "labels": [],
        }
    ]

    graph = build_operating_graph(projects, agents, issues)
    project = next(row for row in graph["projects"] if row["id"].endswith("/alicia"))

    assert project["ticket_count"] == 1
    assert project["thread_counts"] == {"codex": 1, "cursor": 0, "claude": 0}
    assert project["waiting_thread_count"] == 1
    assert project["status"] == "needs_you"
    assert project["tickets"][0]["ticket"] == "REV-900"
    assert project["threads"][0]["id"] == "codex:local:abc"


def test_ambiguous_work_is_never_guessed_into_a_project():
    projects = [
        {"project_id": "github.com/a/alpha", "name": "alpha", "path": "/tmp/a", "workspace": "alpha"},
        {"project_id": "github.com/b/alpha", "name": "alpha", "path": "/tmp/b", "workspace": "alpha"},
    ]
    issue = {
        "id": "l-2",
        "ticket": "REV-901",
        "title": "Alpha issue",
        "description": "alpha",
        "project_id": "",
        "project_name": "alpha",
        "labels": [],
    }

    graph = build_operating_graph(projects, [], [issue])

    assert graph["summary"]["unmapped_tickets"] == 1
    unmapped = next(row for row in graph["projects"] if row["id"] == "unmapped")
    assert unmapped["tickets"][0]["ticket"] == "REV-901"


def test_explicit_linear_project_mapping_sets_the_operating_name():
    projects = [
        {
            "project_id": "github.com/o/runtime",
            "name": "runtime",
            "path": "/tmp/runtime",
            "workspace": "runtime",
        }
    ]
    issue = {
        "id": "l-3",
        "ticket": "REV-902",
        "title": "Build the operating graph",
        "description": "",
        "project_id": "linear-native-id",
        "project_name": "Project Holloway",
        "project_url": "https://linear.app/project/holloway",
        "labels": [],
    }

    with patch(
        "alicia.nucleus._links",
        return_value={"linear_project_to_git": {"linear-native-id": "github.com/o/runtime"}},
    ):
        graph = build_operating_graph(projects, [], [issue])

    project = next(row for row in graph["projects"] if row["id"] == "github.com/o/runtime")
    assert project["name"] == "Project Holloway"
    assert project["linear_project_id"] == "linear-native-id"
    assert project["ticket_count"] == 1


def test_nucleus_view_preserves_exact_ids_while_filtering():
    snapshot = build_operating_graph(
        [{"project_id": "github.com/o/alicia", "name": "alicia", "path": "/tmp/alicia", "workspace": "alicia"}],
        [{"id": "cursor:xyz", "surface": "cursor", "title": "fix ui", "cwd": "/tmp/alicia", "mtime": 1}],
        [],
    )

    view = nucleus_view(snapshot, surface="cursor", limit=1)

    assert view["count"] == 1
    assert view["projects"][0]["id"] == "github.com/o/alicia"
    assert view["projects"][0]["threads"][0]["id"] == "cursor:xyz"


def test_read_only_brain_tool_reads_the_same_snapshot_shape(tmp_path):
    snapshot = build_operating_graph(
        [{"project_id": "github.com/o/alicia", "name": "alicia", "path": "/tmp/alicia", "workspace": "alicia"}],
        [],
        [],
    )
    memory = MemoryStore(tmp_path / "memory.sqlite")
    registry = build_default_registry(MagicMock(), AliciaCfg(), memory=memory, read_only=True)

    with patch("alicia.tools.build_nucleus_snapshot", return_value=snapshot):
        receipt = registry.call("get_nucleus", {"q": "alicia"})

    assert receipt["ok"] is True
    assert receipt["result"]["projects"][0]["id"] == "github.com/o/alicia"
    invalidate_nucleus_cache()
