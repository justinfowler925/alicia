"""Slice 3: merged /api/status is not Linear-only theater."""

from __future__ import annotations

from alicia.manager_status import SEED_PROJECTS, merged_work_surface, seed_canon_projects
from alicia.canon import CanonStore, Project


def test_seed_canon_projects_idempotent(tmp_path):
    store = CanonStore(tmp_path / "canon.sqlite")
    first = seed_canon_projects(store)
    second = seed_canon_projects(store)
    assert first["count"] >= len(SEED_PROJECTS)
    assert second["created"] == 0
    names = {p.name for p in store.list(Project)}
    assert "Alicia" in names and "Shine" in names and "Forge" in names
    store.close()


def test_merged_surface_includes_canon_and_can_include_forge(tmp_path, monkeypatch):
    store_path = tmp_path / "canon.sqlite"
    monkeypatch.setattr(
        "alicia.manager_status.canon_db_path", lambda: store_path
    )
    monkeypatch.setattr(
        "alicia.manager_status.linear_work_surface",
        lambda **kwargs: {
            "headline": "0 in review, 0 in progress.",
            "needs_you": [],
            "working": [],
            "stuck": [],
            "queued": [],
            "stuck_total": 0,
            "hidden": 0,
            "alarm": {},
            "counts": {"needs_you": 0, "working": 0, "queued": 0},
            "actions": [],
            "justin_touchable_count": 0,
            "include_probes": False,
            "source": "linear_direct",
        },
    )
    monkeypatch.setattr("alicia.manager_status._forge_open_runs", lambda limit=12: [
        {
            "ticket": "local-test",
            "title": "Forge running: test",
            "signal": "running",
            "reason": "Local Gemma Forge run",
            "source": "forge",
            "executor": "forge",
            "model": "gemma",
            "project_id": "proj-forge",
            "updated_at": "",
            "link": "",
        }
    ])
    monkeypatch.setattr("alicia.manager_status._cursor_live_rows", lambda limit=8: [])
    body = merged_work_surface()
    assert body["source"] != "linear_direct"
    assert "forge" in body["source"]
    assert "canon" in body["source"]
    assert body["counts"]["forge_open"] == 1
    assert body["counts"]["canon_projects"] >= len(SEED_PROJECTS)
    assert any(r.get("source") == "forge" for r in body["working"])
