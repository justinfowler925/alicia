"""Merged manager status for Alicia — one surface, not Linear-only theater.

Extends the existing /api/status + get_work_surface path with Canon projects,
live Forge runs, and Cursor/agent rows. Process success is still not acceptance.
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from typing import Any

from .agent_sessions import scan_agent_sessions
from .canon_binding import SEED_PROJECTS, seed_canon_projects
from .linear_surface import linear_work_surface

# Re-export for callers/tests that imported from manager_status.
__all__ = ["SEED_PROJECTS", "seed_canon_projects", "merged_work_surface"]

_FORGE_DB = Path.home() / ".local/share/studio-agents/forge-local.sqlite3"
_OPEN_FORGE = frozenset({"queued", "running"})


def _forge_open_runs(limit: int = 12) -> list[dict[str, Any]]:
    if not _FORGE_DB.is_file():
        return []
    try:
        with sqlite3.connect(f"file:{_FORGE_DB}?mode=ro", uri=True) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT id, status, model, work_item, created, started "
                "FROM runs WHERE status IN ('queued','running') "
                "ORDER BY created DESC LIMIT ?",
                (max(1, min(limit, 50)),),
            ).fetchall()
    except sqlite3.Error:
        return []
    out: list[dict[str, Any]] = []
    for row in rows:
        out.append(
            {
                "ticket": str(row["id"]),
                "title": f"Forge {row['status']}: {row['work_item'] or row['id']}",
                "signal": str(row["status"]),
                "reason": "Local Gemma Forge run",
                "source": "forge",
                "executor": "forge",
                "model": str(row["model"] or ""),
                "project_id": "proj-forge",
                "updated_at": time.strftime(
                    "%Y-%m-%dT%H:%M:%SZ",
                    time.gmtime(float(row["started"] or row["created"] or time.time())),
                ),
                "link": "http://127.0.0.1:8768/#forge",
            }
        )
    return out


def _cursor_live_rows(limit: int = 8) -> list[dict[str, Any]]:
    try:
        sessions = scan_agent_sessions()
    except Exception:  # noqa: BLE001 — status must degrade, not crash
        return []
    live: list[dict[str, Any]] = []
    for row in sessions:
        if not isinstance(row, dict):
            continue
        surface = str(row.get("surface") or "").lower()
        if surface != "cursor":
            continue
        state = str(row.get("state") or row.get("status") or "").strip().lower()
        is_live = bool(row.get("live")) or state in {"active", "running", "in_progress"}
        if not is_live:
            continue
        live.append(
            {
                "ticket": str(row.get("session_id") or row.get("id") or "")[:64],
                "title": str(row.get("title") or "Cursor session")[:200],
                "signal": state or "active",
                "reason": "Live Cursor / agent session",
                "source": "cursor",
                "executor": "cursor",
                "project_id": str(row.get("project") or row.get("cwd") or "")[:120],
                "updated_at": str(row.get("mtime") or ""),
                "link": "",
            }
        )
        if len(live) >= limit:
            break
    return live


def _canon_project_rows(store: CanonStore | None = None) -> list[dict[str, Any]]:
    from .canon_binding import project_rollups

    try:
        return project_rollups(store)
    except Exception:  # noqa: BLE001
        return []


def merged_work_surface(*, timeout_s: float = 8.0) -> dict[str, Any]:
    """Same shape as linear_work_surface, plus Forge/Cursor/Canon lanes."""
    linear_error = ""
    try:
        base = linear_work_surface(timeout_s=timeout_s)
    except Exception as exc:  # noqa: BLE001
        linear_error = str(exc)
        base = {
            "headline": "Linear unavailable.",
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
            "error": linear_error,
        }

    forge_rows = _forge_open_runs()
    cursor_rows = _cursor_live_rows()
    projects = _canon_project_rows()
    bound = sum(int(p.get("work_item_count") or 0) for p in projects)
    non_empty = [p for p in projects if int(p.get("work_item_count") or 0) > 0]

    working = list(base.get("working") or [])
    working.extend(forge_rows)
    working.extend(cursor_rows)

    sources = ["linear"] if not linear_error else []
    if forge_rows:
        sources.append("forge")
    if cursor_rows:
        sources.append("cursor")
    if projects:
        sources.append("canon")

    source = "+".join(sources) if sources else "empty"
    needs = list(base.get("needs_you") or [])
    queued = list(base.get("queued") or [])
    headline = (
        f"{len(needs)} need you, {len(working)} working "
        f"({len(forge_rows)} Forge, {len(cursor_rows)} Cursor), "
        f"{len(projects)} Canon projects ({bound} bound work items, "
        f"{len(non_empty)} with rollups)."
    )

    return {
        **base,
        "headline": headline,
        "working": working,
        "counts": {
            "needs_you": len(needs),
            "working": len(working),
            "queued": len(queued),
            "forge_open": len(forge_rows),
            "cursor_live": len(cursor_rows),
            "canon_projects": len(projects),
            "canon_bound_work_items": bound,
            "canon_projects_with_work": len(non_empty),
        },
        "source": source,
        "lanes": {
            "linear": not bool(linear_error),
            "forge_open": forge_rows,
            "cursor_live": cursor_rows,
            "canon_projects": projects,
        },
        "linear_error": linear_error or None,
        "accepted": False,
        "note": "Merged manager status; process success is not acceptance.",
    }
