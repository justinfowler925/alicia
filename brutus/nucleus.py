"""Project Nucleus read model: source-owned work, joined without a second ledger.

Linear owns issues, Git owns workspace state, each agent host owns its tasks,
Brutus ignores Atlas execution overlays. This module gives the screen and the
conversation brain one deterministic projection over those records.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from .agent_sessions import merge_overlays, scan_agent_sessions
from .paths import state_path
from .projects import scan_projects

LINEAR_API = "https://api.linear.app/graphql"
LINEAR_TEAM = "83fae885-2c96-4ea3-986b-c23d6ca71ea6"
JUSTIN_EMAIL = "justin.fowler@clearspeed.com"
LINKS_PATH = Path(__file__).with_name("project_links.json")
_SNAPSHOT_TTL_S = 60.0
_SNAPSHOT_CACHE: dict[str, Any] = {"at": 0.0, "data": None}
log = logging.getLogger("brutus.nucleus")
_SNAPSHOT_LOCK = threading.Lock()

LINEAR_QUERY = """
query NucleusPortfolio($team: String!, $first: Int!, $after: String) {
  team(id: $team) {
    issues(
      first: $first
      after: $after
      orderBy: updatedAt
      filter: { state: { type: { nin: ["completed", "canceled"] } } }
    ) {
      nodes {
        id identifier title description url priority createdAt updatedAt
        state { id name type }
        assignee { id name email }
        project { id name url }
        labels { nodes { id name } }
      }
      pageInfo { hasNextPage endCursor }
    }
  }
}
"""


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _norm(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (value or "").casefold()).strip()


def _links() -> dict[str, Any]:
    try:
        data = json.loads(LINKS_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"linear_project_to_git": {}}
    return data if isinstance(data, dict) else {"linear_project_to_git": {}}


def _linear_issue(node: dict[str, Any]) -> dict[str, Any]:
    state = node.get("state") or {}
    assignee = node.get("assignee") or {}
    project = node.get("project") or {}
    labels = (node.get("labels") or {}).get("nodes") or []
    return {
        "id": str(node.get("id") or ""),
        "ticket": str(node.get("identifier") or ""),
        "title": str(node.get("title") or ""),
        "description": str(node.get("description") or "")[:2000],
        "url": str(node.get("url") or ""),
        "priority": int(node.get("priority") or 0),
        "created_at": str(node.get("createdAt") or ""),
        "updated_at": str(node.get("updatedAt") or ""),
        "state_id": str(state.get("id") or ""),
        "state": str(state.get("name") or ""),
        "state_type": str(state.get("type") or ""),
        "assignee_id": str(assignee.get("id") or ""),
        "assignee": str(assignee.get("name") or ""),
        "assignee_email": str(assignee.get("email") or ""),
        "project_id": str(project.get("id") or ""),
        "project_name": str(project.get("name") or ""),
        "project_url": str(project.get("url") or ""),
        "labels": [
            {"id": str(label.get("id") or ""), "name": str(label.get("name") or "")}
            for label in labels
            if isinstance(label, dict)
        ],
        "source": "linear",
    }


def linear_portfolio(*, timeout_s: float = 15.0, page_size: int = 100) -> dict[str, Any]:
    """All open RevOps issues, paged and identity-complete."""
    key = os.environ.get("LINEAR_API_KEY", "").strip()
    if not key:
        raise RuntimeError("Linear read path is not configured")
    issues: list[dict[str, Any]] = []
    after: str | None = None
    pages = 0
    with httpx.Client(
        timeout=timeout_s,
        headers={
            "Authorization": key,
            "Content-Type": "application/json",
            "User-Agent": "brutus-nucleus/1",
        },
    ) as client:
        while True:
            response = client.post(
                LINEAR_API,
                json={
                    "query": LINEAR_QUERY,
                    "variables": {
                        "team": LINEAR_TEAM,
                        "first": max(1, min(int(page_size or 100), 100)),
                        "after": after,
                    },
                },
            )
            response.raise_for_status()
            payload = response.json()
            if payload.get("errors"):
                raise RuntimeError(
                    str((payload["errors"][0] or {}).get("message") or "Linear query failed")
                )
            connection = ((((payload.get("data") or {}).get("team") or {}).get("issues")) or {})
            nodes = connection.get("nodes")
            if not isinstance(nodes, list):
                raise TypeError("Linear returned no issue population")
            issues.extend(_linear_issue(node) for node in nodes if isinstance(node, dict))
            pages += 1
            page = connection.get("pageInfo") or {}
            after = str(page.get("endCursor") or "") or None
            if not page.get("hasNextPage"):
                break
            if not after or pages >= 10:
                raise RuntimeError("Linear pagination did not converge")
    return {
        "source": "linear",
        "freshness": "fresh",
        "fetched_at": _now(),
        "pages": pages,
        "count": len(issues),
        "issues": issues,
    }


def atlas_issues(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Lossy fallback rows are explicit about being an execution overlay."""
    out: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        ticket = str(row.get("external_id") or row.get("id") or "")
        if not ticket:
            continue
        out.append(
            {
                "id": str(row.get("id") or ticket),
                "ticket": ticket,
                "title": str(row.get("title") or ticket),
                "description": str(row.get("goal") or "")[:2000],
                "url": "",
                "priority": int(row.get("priority") or 0),
                "created_at": str(row.get("created_at") or ""),
                "updated_at": str(row.get("updated_at") or ""),
                "state": str(row.get("status") or row.get("next_action") or ""),
                "state_type": "atlas_overlay",
                "assignee": "",
                "assignee_email": "",
                "project_id": "",
                "project_name": "",
                "project_url": "",
                "labels": [],
                "source": "atlas_fallback",
                "atlas": {
                    "thread_id": str(row.get("id") or ""),
                    "next_action": str(row.get("next_action") or ""),
                    "status": str(row.get("status") or ""),
                },
            }
        )
    return out


def _workspace_aliases(project: dict[str, Any]) -> set[str]:
    aliases = {_norm(str(project.get("name") or ""))}
    remote = str(project.get("project_id") or "")
    if remote:
        aliases.add(_norm(remote.rsplit("/", 1)[-1]))
    for workspace in project.get("workspaces") or []:
        aliases.add(_norm(str(workspace.get("workspace") or workspace.get("name") or "")))
    return {alias for alias in aliases if len(alias) >= 4}


def _ticket_needs_user(issue: dict[str, Any]) -> bool:
    if str(issue.get("assignee_email") or "").casefold() != JUSTIN_EMAIL:
        return False
    state = str(issue.get("state") or "").casefold()
    labels = " ".join(str(label.get("name") or "") for label in issue.get("labels") or []).casefold()
    return state in {"in review", "blocked", "needs info", "needs input"} or "needs justin" in labels


def build_operating_graph(
    project_rows: list[dict[str, Any]],
    agent_rows: list[dict[str, Any]],
    issues: list[dict[str, Any]],
    *,
    project_overlays: dict[str, dict[str, Any]] | None = None,
    source_status: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Join by native IDs and explicit aliases; ambiguous work stays unmapped."""
    overlays = project_overlays or {}
    grouped: dict[str, dict[str, Any]] = {}
    path_to_project: list[tuple[str, str]] = []
    for workspace in project_rows:
        project_id = str(workspace.get("project_id") or f"local/{workspace.get('name')}")
        row = grouped.setdefault(
            project_id,
            {
                "id": project_id,
                "name": str(workspace.get("name") or project_id.rsplit("/", 1)[-1]),
                "kind": "git",
                "linear_project_id": "",
                "linear_project_url": "",
                "workspaces": [],
                "tickets": [],
                "threads": [],
            },
        )
        row["workspaces"].append(workspace)
        path = str(workspace.get("path") or "").rstrip("/")
        if path:
            path_to_project.append((path, project_id))
        # Prefer the least nested checkout name as the human label.
        if str(workspace.get("name") or "").count("/") < str(row["name"]).count("/"):
            row["name"] = str(workspace.get("name") or row["name"])

    path_to_project.sort(key=lambda item: len(item[0]), reverse=True)
    alias_to_project: dict[str, set[str]] = {}
    for pid, project in grouped.items():
        for alias in _workspace_aliases(project):
            alias_to_project.setdefault(alias, set()).add(pid)

    manual = (_links().get("linear_project_to_git") or {})
    linear_project_names: dict[str, str] = {}
    unmapped_tickets: list[dict[str, Any]] = []
    for issue in issues:
        linear_id = str(issue.get("project_id") or "")
        linear_name = str(issue.get("project_name") or "")
        if linear_id and linear_name:
            linear_project_names[linear_id] = linear_name
        manual_project_id = str(manual.get(linear_id) or "") if linear_id else ""
        project_id = manual_project_id
        if project_id and project_id not in grouped:
            project_id = ""
        if not project_id and linear_name:
            matches = alias_to_project.get(_norm(linear_name)) or set()
            if len(matches) == 1:
                project_id = next(iter(matches))
        if not project_id:
            haystack = _norm(f"{issue.get('title')} {issue.get('description')}")
            matches = {
                pid
                for alias, pids in alias_to_project.items()
                if alias and alias in haystack
                for pid in pids
            }
            if len(matches) == 1:
                project_id = next(iter(matches))
        if not project_id and linear_id:
            project_id = f"linear:{linear_id}"
            grouped.setdefault(
                project_id,
                {
                    "id": project_id,
                    "name": linear_name or "Linear project",
                    "kind": "linear",
                    "linear_project_id": linear_id,
                    "linear_project_url": str(issue.get("project_url") or ""),
                    "workspaces": [],
                    "tickets": [],
                    "threads": [],
                },
            )
        if project_id:
            issue["project_ref"] = project_id
            target = grouped[project_id]
            target["tickets"].append(issue)
            if linear_id and not target.get("linear_project_id"):
                target["linear_project_id"] = linear_id
                target["linear_project_url"] = str(issue.get("project_url") or "")
            # An explicit native-id mapping says this repo implements the
            # named operating project. Lead with that portfolio name while
            # retaining every Git workspace underneath it.
            if manual_project_id and linear_name:
                target["name"] = linear_name
        else:
            issue["project_ref"] = "unmapped"
            unmapped_tickets.append(issue)

    unmapped_threads: list[dict[str, Any]] = []
    for thread in agent_rows:
        cwd = str(thread.get("cwd") or "").rstrip("/")
        project_id = ""
        for path, candidate in path_to_project:
            if cwd == path or cwd.startswith(f"{path}/"):
                project_id = candidate
                break
        if not project_id:
            alias = _norm(str(thread.get("project") or ""))
            matches = alias_to_project.get(alias) or set()
            if len(matches) == 1:
                project_id = next(iter(matches))
        if project_id:
            thread["project_ref"] = project_id
            grouped[project_id]["threads"].append(thread)
        else:
            thread["project_ref"] = "unmapped"
            unmapped_threads.append(thread)

    if unmapped_tickets or unmapped_threads:
        grouped["unmapped"] = {
            "id": "unmapped",
            "name": "Unmapped work",
            "kind": "unmapped",
            "linear_project_id": "",
            "linear_project_url": "",
            "workspaces": [],
            "tickets": unmapped_tickets,
            "threads": unmapped_threads,
        }

    now = time.time()
    projects: list[dict[str, Any]] = []
    for project_id, project in grouped.items():
        workspaces = project["workspaces"]
        tickets = project["tickets"]
        threads = project["threads"]
        overlay = overlays.get(project_id) or {}
        dirty = sum(int(workspace.get("dirty") or 0) for workspace in workspaces)
        unpushed = sum(int(workspace.get("unpushed") or 0) for workspace in workspaces)
        never_pushed = sum(1 for workspace in workspaces if workspace.get("never_pushed"))
        active_tickets = sum(1 for ticket in tickets if ticket.get("state_type") == "started")
        needs_you = sum(1 for ticket in tickets if _ticket_needs_user(ticket))
        recent_threads = sum(
            1 for thread in threads if now - float(thread.get("mtime") or 0) <= 48 * 3600
        )
        live_threads = sum(1 for thread in threads if thread.get("live"))
        waiting_threads = sum(1 for thread in threads if thread.get("state") == "waiting")
        reasons: list[str] = []
        if needs_you:
            reasons.append(f"{needs_you} Linear issue{'s' if needs_you != 1 else ''} need you")
        if waiting_threads:
            reasons.append(f"{waiting_threads} agent task{'s' if waiting_threads != 1 else ''} await input")
        if never_pushed:
            reasons.append(f"{never_pushed} workspace{'s' if never_pushed != 1 else ''} only on this Mac")
        if dirty:
            reasons.append(f"{dirty} unsaved file{'s' if dirty != 1 else ''}")
        if unpushed:
            reasons.append(f"{unpushed} unpushed commit{'s' if unpushed != 1 else ''}")
        score = needs_you * 100 + waiting_threads * 80 + never_pushed * 70 + dirty * 3 + unpushed * 12
        score += live_threads * 40 + recent_threads * 5 + active_tickets * 4
        if overlay.get("pinned"):
            score += 120
        if needs_you or waiting_threads:
            status = "needs_you"
        elif dirty or unpushed or never_pushed:
            status = "at_risk"
        elif recent_threads or active_tickets or any(w.get("activity") == "hot" for w in workspaces):
            status = "active"
        else:
            status = "quiet"
        projects.append(
            {
                **project,
                "pinned": bool(overlay.get("pinned")),
                "archived": bool(overlay.get("archived")),
                "objective": str(overlay.get("objective") or ""),
                "notes": str(overlay.get("notes") or ""),
                "status": status,
                "attention_score": score,
                "attention_reasons": reasons,
                "workspace_count": len(workspaces),
                "dirty": dirty,
                "unpushed": unpushed,
                "never_pushed": never_pushed,
                "ticket_count": len(tickets),
                "active_ticket_count": active_tickets,
                "needs_you_count": needs_you,
                "thread_count": len(threads),
                "recent_thread_count": recent_threads,
                "live_thread_count": live_threads,
                "waiting_thread_count": waiting_threads,
                "thread_counts": {
                    surface: sum(1 for thread in threads if thread.get("surface") == surface)
                    for surface in ("codex", "cursor", "claude")
                },
                "last_activity_epoch": max(
                    [float(w.get("last_commit_epoch") or 0) for w in workspaces]
                    + [float(t.get("mtime") or 0) for t in threads]
                    + [0.0]
                ),
            }
        )

    projects.sort(
        key=lambda row: (
            bool(row.get("archived")),
            -int(row.get("attention_score") or 0),
            -float(row.get("last_activity_epoch") or 0),
            str(row.get("name") or "").casefold(),
        )
    )
    real_projects = [project for project in projects if project.get("kind") != "unmapped"]
    # Three quantities on one screen were all labelled "projects": 37 in the
    # metric tile (tracked), 38 in the pager (tracked plus the synthetic
    # unmapped row), and 40 in the nav badge — which was actually the count of
    # at-risk git workspaces out of 136. None of them was wrong; the word was.
    # Each figure is now named for what it counts, so a reader can tell which
    # one a sentence means and click through to the rows behind it.
    summary = {
        "projects": len(real_projects),
        "projects_tracked": len(real_projects),
        "projects_listed": len(projects),
        "projects_needing_you": sum(1 for project in real_projects if project["status"] == "needs_you"),
        "projects_at_risk": sum(1 for project in real_projects if project["status"] == "at_risk"),
        "tickets": len(issues),
        "mapped_tickets": len(issues) - len(unmapped_tickets),
        "unmapped_tickets": len(unmapped_tickets),
        "threads": len(agent_rows),
        "mapped_threads": len(agent_rows) - len(unmapped_threads),
        "unmapped_threads": len(unmapped_threads),
        "recent_threads": sum(int(project.get("recent_thread_count") or 0) for project in projects),
        "agent_surfaces": {
            surface: sum(1 for thread in agent_rows if thread.get("surface") == surface)
            for surface in ("codex", "cursor", "claude")
        },
        "git_workspaces": len(project_rows),
    }
    return {
        "generated_at": _now(),
        "summary": summary,
        "source_status": source_status or {},
        "projects": projects,
        "linear_projects": linear_project_names,
    }


_SNAPSHOT_FILE = "nucleus-snapshot.json"
_REFRESHING = threading.Event()


def _building_snapshot() -> dict[str, Any]:
    """A named state for "there is no graph yet", not an empty success."""
    return {
        "generated_at": None,
        "summary": {},
        "source_status": {},
        "projects": [],
        "linear_projects": {},
        "building": True,
        "refreshing": True,
    }


def _write_snapshot_to_disk(snapshot: dict[str, Any]) -> None:
    """Survive a restart. Rebuilding from cold cost 24 seconds of dead page."""
    try:
        path = state_path(_SNAPSHOT_FILE)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(snapshot, separators=(",", ":")), encoding="utf-8")
        temporary.replace(path)
    except (OSError, TypeError, ValueError):
        log.warning("could not persist the nucleus snapshot", exc_info=True)


def _read_snapshot_from_disk() -> dict[str, Any] | None:
    try:
        data = json.loads(state_path(_SNAPSHOT_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and isinstance(data.get("projects"), list) else None


def _refresh_nucleus_in_background(client: Any, memory: Any) -> None:
    """Rebuild the snapshot once, behind whoever was just served stale data."""
    if _REFRESHING.is_set():
        return
    _REFRESHING.set()

    def run() -> None:
        try:
            build_nucleus_snapshot(client, memory, force=True)
        except Exception:  # a failed refresh keeps the stale copy on screen
            log.exception("nucleus background refresh failed")
        finally:
            _REFRESHING.clear()

    threading.Thread(target=run, name="nucleus-refresh", daemon=True).start()


def warm_nucleus_snapshot(client: Any, memory: Any) -> None:
    """Build the graph at startup so no page load ever pays for a cold cache."""
    _refresh_nucleus_in_background(client, memory)


def invalidate_nucleus_cache() -> None:
    """Organization writes invalidate the shared screen/chat projection."""
    with _SNAPSHOT_LOCK:
        _SNAPSHOT_CACHE["at"] = 0.0
        _SNAPSHOT_CACHE["data"] = None


def apply_project_overlay(project_id: str, changes: dict[str, Any]) -> None:
    """Fold an organization write into the snapshot the screen will read next.

    Invalidating alone was not enough once the snapshot was persisted: the
    next read found an empty cache, fell back to the copy on disk — written
    before the write — and served the row unchanged. Pinning a project
    reported "done" and left the button saying Pin, which is a control with no
    observable effect, which is decoration.

    The rebuild behind it is still the authority; this only keeps the screen
    from lying for the thirty seconds that takes.
    """
    allowed = {"pinned", "archived", "objective", "notes"}
    patch = {key: value for key, value in changes.items() if key in allowed}
    if not patch:
        return
    with _SNAPSHOT_LOCK:
        snapshot = _SNAPSHOT_CACHE.get("data") or _read_snapshot_from_disk()
        if not snapshot:
            return
        projects = []
        for project in snapshot.get("projects") or []:
            projects.append({**project, **patch} if project.get("id") == project_id else project)
        snapshot = {**snapshot, "projects": projects}
        _SNAPSHOT_CACHE["data"] = snapshot
        _SNAPSHOT_CACHE["at"] = 0.0  # still due a rebuild; just not a blank one
    _write_snapshot_to_disk(snapshot)


def build_nucleus_snapshot(
    client: Any, memory: Any, *, force: bool = False, allow_stale: bool = False
) -> dict[str, Any]:
    """The one cached source path used by HTTP and conversation tools.

    A cold build walks every checkout on the laptop, scans agent sessions and
    pages Linear: measured at 38 seconds. With a 60-second TTL the cache is
    cold most of the times anyone actually opens the page, so the screen showed
    "Loading…" for well over half a minute and looked broken rather than busy.

    `allow_stale` is what the screen wants: answer instantly with the last
    snapshot and refresh behind it. Slightly old numbers with a timestamp beat
    a spinner, and the brain — which needs the freshest graph it can get and
    has no user waiting on it — keeps the blocking path.

    A cold process has no snapshot to be stale with, and that case was still
    blocking for 24 seconds — a restart made the page dead again. So the last
    snapshot is written to disk and read back on the first request, and if even
    that is missing the screen is told the graph is building rather than being
    held open. "Building" is a state a reader can understand; a spinner that
    lasts half a minute is not.
    """
    now = time.time()
    with _SNAPSHOT_LOCK:
        cached = _SNAPSHOT_CACHE.get("data")
        fresh = cached is not None and now - float(_SNAPSHOT_CACHE.get("at") or 0) < _SNAPSHOT_TTL_S
        if not force and fresh:
            return cached
        if allow_stale:
            if cached is None:
                cached = _read_snapshot_from_disk()
            if cached is not None:
                _refresh_nucleus_in_background(client, memory)
                return {**cached, "stale": True, "refreshing": True}
            _refresh_nucleus_in_background(client, memory)
            return _building_snapshot()
    projects = scan_projects(force=force)
    agents = merge_overlays(scan_agent_sessions(force=force), memory.list_agent_overlays())
    source_status: dict[str, Any] = {
        "git": {"state": "fresh", "count": len(projects)},
        "agents": {"state": "fresh", "count": len(agents)},
    }
    try:
        linear = linear_portfolio()
        issues = linear["issues"]
        source_status["linear"] = {
            "state": "fresh",
            "count": len(issues),
            "pages": linear["pages"],
            "fetched_at": linear["fetched_at"],
        }
    except Exception as exc:  # noqa: BLE001 — degraded is data, not an empty success
        issues = []
        source_status["linear"] = {
            "state": "error",
            "count": 0,
            "error": str(exc),
            "atlas_ignored": True,
        }
    snapshot = build_operating_graph(
        projects,
        agents,
        issues,
        project_overlays=memory.list_project_overlays(),
        source_status=source_status,
    )
    with _SNAPSHOT_LOCK:
        _SNAPSHOT_CACHE["at"] = time.time()
        _SNAPSHOT_CACHE["data"] = snapshot
    _write_snapshot_to_disk(snapshot)
    return snapshot


def nucleus_view(
    snapshot: dict[str, Any],
    *,
    project: str = "",
    q: str = "",
    status: str = "",
    surface: str = "",
    limit: int = 20,
) -> dict[str, Any]:
    """Capped brain view; exact ids survive every filter."""
    rows = list(snapshot.get("projects") or [])
    project_q = project.strip().casefold()
    qn = q.strip().casefold()
    status_q = status.strip().casefold()
    surface_q = surface.strip().casefold()
    if project_q:
        rows = [
            row for row in rows
            if project_q in f"{row.get('id')} {row.get('name')}".casefold()
        ]
    if qn:
        rows = [
            row for row in rows
            if qn in (
                f"{row.get('id')} {row.get('name')} {row.get('objective')} "
                + " ".join(str(reason) for reason in row.get("attention_reasons") or [])
                + " ".join(str(ticket.get("ticket")) + " " + str(ticket.get("title")) for ticket in row.get("tickets") or [])
                + " ".join(str(thread.get("title")) for thread in row.get("threads") or [])
            ).casefold()
        ]
    if status_q:
        rows = [row for row in rows if str(row.get("status") or "").casefold() == status_q]
    if surface_q:
        rows = [
            row for row in rows
            if any(str(thread.get("surface") or "").casefold() == surface_q for thread in row.get("threads") or [])
        ]
    cap = max(1, min(int(limit or 20), 50))
    return {
        "generated_at": snapshot.get("generated_at"),
        "summary": snapshot.get("summary") or {},
        "source_status": snapshot.get("source_status") or {},
        "count": len(rows),
        "projects": rows[:cap],
    }


# The screen's project table renders a name, a status, counts and a timestamp.
# It was shipped 1.04 MB to do that, because one project embedded 453 agent
# threads — 410 KB in a single record, 98% of the response, none of it on
# screen until you open that project. The counts it does render are already
# computed alongside, so the bodies can wait for a click.
_PROJECT_DETAIL_KEYS = ("threads", "tickets")


def slim_nucleus_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    """The same graph without the per-project detail bodies."""
    projects = []
    for project in snapshot.get("projects") or []:
        slim = {key: value for key, value in project.items() if key not in _PROJECT_DETAIL_KEYS}
        slim["detail_available"] = any(project.get(key) for key in _PROJECT_DETAIL_KEYS)
        projects.append(slim)
    return {**snapshot, "projects": projects, "detail": "summary"}


def nucleus_project_detail(snapshot: dict[str, Any], project_id: str) -> dict[str, Any] | None:
    """One project's full record, including the threads and tickets."""
    for project in snapshot.get("projects") or []:
        if project.get("id") == project_id:
            return project
    return None
