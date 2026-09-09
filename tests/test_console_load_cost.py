"""The console page may not be held open by a machine that is asleep.

Measured on the running daemon before these fixes: /api/nucleus took 38.4s and
returned 1.04 MB, /api/avatar took 35.8s to return 570 bytes, and the browser's
six connections to the origin were both consumed by the two of them. The
Nucleus table read "Loading…" indefinitely — not because anything was broken,
but because everything was waited for.
"""

import subprocess
import time
from unittest.mock import MagicMock, patch

import pytest

from brutus import avatars
from brutus.nucleus import build_nucleus_snapshot, slim_nucleus_snapshot


@pytest.fixture(autouse=True)
def _forget_reachability():
    avatars.reset_studio_reachability()
    yield
    avatars.reset_studio_reachability()


# --- an unreachable peer is remembered, not re-waited-for ------------------


def _unreachable():
    return subprocess.CompletedProcess(
        args=[], returncode=255, stdout="",
        stderr="ssh: connect to host 100.93.125.5 port 22: Operation timed out",
    )


def test_an_unreachable_studio_is_answered_instantly_after_the_first_attempt():
    with patch("brutus.avatars.subprocess.run", return_value=_unreachable()) as run:
        for attempt in (1, 2):
            with pytest.raises(avatars.StudioUnreachable):
                avatars._ssh("echo hi")
            assert run.call_count == 1, f"attempt {attempt} paid the timeout again"

    status = avatars.studio_reachability()
    assert status["reachable"] is False
    assert "connect to host" in status["error"]
    assert status["retry_in_seconds"] > 0


def test_a_command_that_merely_fails_is_not_a_verdict_on_the_host():
    """A nonzero exit from a command that ran means the host is up."""
    failed = subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="no such file")
    with patch("brutus.avatars.subprocess.run", return_value=failed):
        proc = avatars._ssh("cat /nope")
    assert proc.returncode == 1
    assert avatars.studio_reachability()["reachable"] is True


def test_the_connect_timeout_is_short_enough_for_a_page_load():
    with (
        patch("brutus.avatars.subprocess.run", return_value=_unreachable()) as run,
        pytest.raises(avatars.StudioUnreachable),
    ):
        avatars._ssh("echo hi")
    argv = run.call_args[0][0]
    assert "ConnectTimeout=4" in argv


# --- the table is not shipped the detail it does not render ----------------


def test_the_summary_projection_drops_the_thread_bodies_and_keeps_the_counts():
    snapshot = {
        "generated_at": "now",
        "projects": [
            {
                "id": "brutus",
                "name": "brutus",
                "thread_count": 453,
                "thread_counts": {"codex": 400, "cursor": 53},
                "threads": [{"id": i, "body": "x" * 800} for i in range(453)],
                "tickets": [{"ticket": "REV-551"}],
            }
        ],
    }
    slim = slim_nucleus_snapshot(snapshot)
    project = slim["projects"][0]

    assert "threads" not in project and "tickets" not in project
    assert project["thread_count"] == 453
    assert project["thread_counts"] == {"codex": 400, "cursor": 53}
    assert project["detail_available"] is True
    assert slim["generated_at"] == "now"
    # The source snapshot is the brain's, and must not be mutated on the way out.
    assert len(snapshot["projects"][0]["threads"]) == 453


# --- the screen is served stale, never a spinner ---------------------------


def test_the_screen_is_served_the_last_snapshot_while_a_new_one_builds():
    memory = MagicMock()
    memory.list_project_overlays.return_value = {}
    memory.list_agent_overlays.return_value = {}
    graph = {"projects": [{"id": "brutus"}], "summary": {}}

    with patch("brutus.nucleus.scan_projects", return_value=[]), \
         patch("brutus.nucleus.scan_agent_sessions", return_value=[]), \
         patch("brutus.nucleus.merge_overlays", return_value=[]), \
         patch("brutus.nucleus.linear_portfolio", side_effect=RuntimeError("offline")), \
         patch("brutus.nucleus.build_operating_graph", return_value=graph):
        build_nucleus_snapshot(MagicMock(), memory, force=True)

        # Age the cache past its TTL, the state the page nearly always finds.
        import brutus.nucleus as nuc

        nuc._SNAPSHOT_CACHE["at"] = time.time() - nuc._SNAPSHOT_TTL_S - 1

        started = time.monotonic()
        served = build_nucleus_snapshot(MagicMock(), memory, allow_stale=True)
        elapsed = time.monotonic() - started

    assert served["projects"] == [{"id": "brutus"}]
    assert served["stale"] is True and served["refreshing"] is True
    assert elapsed < 0.5, f"a stale read waited {elapsed:.2f}s on a rebuild"


def test_the_brain_still_gets_a_blocking_fresh_build():
    """Numbers a model reasons from are not allowed to be quietly old."""
    import inspect

    signature = inspect.signature(build_nucleus_snapshot)
    assert signature.parameters["allow_stale"].default is False


# --- a control with no observable effect is decoration ----------------------


def test_an_organization_write_is_visible_before_the_rebuild_lands():
    """Pinning a project reported "done" and left the button saying Pin.

    Invalidating the cache alone stopped being enough once the snapshot was
    persisted: the next read found an empty cache, fell back to the copy on
    disk — written before the write — and served the row unchanged.
    """
    from brutus.nucleus import apply_project_overlay, slim_nucleus_snapshot
    import brutus.nucleus as nuc

    snapshot = {"projects": [{"id": "sfdc", "name": "sfdc", "pinned": False, "archived": False}]}
    with patch.object(nuc, "_write_snapshot_to_disk"):
        nuc._SNAPSHOT_CACHE["data"] = snapshot
        nuc._SNAPSHOT_CACHE["at"] = time.time()

        apply_project_overlay("sfdc", {"pinned": True})

        served = slim_nucleus_snapshot(nuc._SNAPSHOT_CACHE["data"])
    assert served["projects"][0]["pinned"] is True
    # And it is still due a real rebuild rather than being treated as fresh.
    assert nuc._SNAPSHOT_CACHE["at"] == 0.0


def test_an_overlay_write_cannot_set_fields_it_does_not_own():
    from brutus.nucleus import apply_project_overlay
    import brutus.nucleus as nuc

    with patch.object(nuc, "_write_snapshot_to_disk"):
        nuc._SNAPSHOT_CACHE["data"] = {"projects": [{"id": "sfdc", "attention_score": 1698}]}
        apply_project_overlay("sfdc", {"attention_score": 0, "pinned": True})
        row = nuc._SNAPSHOT_CACHE["data"]["projects"][0]

    assert row["attention_score"] == 1698, "source-owned fields are not writable"
    assert row["pinned"] is True
