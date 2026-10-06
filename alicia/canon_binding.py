"""Canon project binding — intake sets project_id; rollups are real.

Closes the Slice-3 theater gap: 7 projects with empty work_item_ids.
"""

from __future__ import annotations

import re
from typing import Any

from .canon import CanonStore, Project, WorkItem, DEFAULT_IDENTITY_REGISTRY, require_owner
from .canon.models import WorkItemType
from .canon.cli import capture_manual_inbox_item
from .paths import canon_db_path

# Personal streams Justin cares about for the MVP status rollup.
SEED_PROJECTS: tuple[dict[str, str], ...] = (
    {
        "id": "proj-alicia",
        "name": "Alicia",
        "objective": "Manager control plane + Forge/Cursor orchestration",
        "owner": "justin",
    },
    {
        "id": "proj-atlas",
        "name": "Atlas",
        "objective": "Salesforce / RevOps specialist (personal atlas-direct)",
        "owner": "justin",
    },
    {
        "id": "proj-forge",
        "name": "Forge",
        "objective": "Local Gemma general worker on Alicia #forge",
        "owner": "justin",
    },
    {
        "id": "proj-localai",
        "name": "LocalAI",
        "objective": "Studio local inference + routing (local-ai-stack)",
        "owner": "justin",
    },
    {
        "id": "proj-brain",
        "name": "Fowler Brain",
        "objective": "Durable policy and learned context",
        "owner": "justin",
    },
    {
        "id": "proj-scout",
        "name": "Scout",
        "objective": "Data scrape / ingest specialist",
        "owner": "justin",
    },
    {
        "id": "proj-shine",
        "name": "Shine",
        "objective": "Design system and UI craft",
        "owner": "justin",
    },
)

# Keyword → seeded project id. First match wins; order is intentional.
_PROJECT_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("proj-shine", re.compile(r"\b(?:shine|design.?system|design.lint|token)\b", re.I)),
    ("proj-atlas", re.compile(r"\b(?:atlas|salesforce|revops|sfdc|apex|lwc)\b", re.I)),
    ("proj-scout", re.compile(r"\b(?:scout|scrape|ingest|zoom.?notes?)\b", re.I)),
    ("proj-forge", re.compile(r"\b(?:forge|#forge|gemma)\b", re.I)),
    ("proj-localai", re.compile(r"\b(?:local.?ai|localai|mlx|inference.?stack)\b", re.I)),
    ("proj-brain", re.compile(r"\b(?:fowler.?brain|brain\s+rule|org.?chart)\b", re.I)),
    ("proj-alicia", re.compile(r"\b(?:alicia|brutus|canon|manager)\b", re.I)),
)

_NAME_TO_ID = {row["name"].casefold(): row["id"] for row in SEED_PROJECTS}
_NAME_TO_ID.update({row["id"].casefold(): row["id"] for row in SEED_PROJECTS})
_NAME_TO_ID.update(
    {
        "brain": "proj-brain",
        "fowler brain": "proj-brain",
        "localai": "proj-localai",
        "local-ai": "proj-localai",
    }
)


def seed_canon_projects(store: CanonStore | None = None) -> dict[str, Any]:
    """Ensure MVP personal Project rows exist. Idempotent by fixed ids."""
    own = store is None
    store = store or CanonStore(canon_db_path())
    created = 0
    existing = {p.id for p in store.list(Project)}
    for row in SEED_PROJECTS:
        if row["id"] in existing:
            continue
        store.save(
            Project(
                id=row["id"],
                name=row["name"],
                objective=row["objective"],
                owner=row["owner"],
            )
        )
        created += 1
    projects = [p.model_dump(mode="json") for p in store.list(Project)]
    if own:
        store.close()
    return {"ok": True, "created": created, "projects": projects, "count": len(projects)}


def classify_project(text: str, *, hint: str = "") -> str:
    """Return a seeded project_id for capture text or an explicit hint."""
    if hint:
        key = hint.strip().casefold()
        if key in _NAME_TO_ID:
            return _NAME_TO_ID[key]
        for row in SEED_PROJECTS:
            if key in {row["id"].casefold(), row["name"].casefold(), row["id"].removeprefix("proj-")}:
                return row["id"]
    body = text or ""
    for project_id, pattern in _PROJECT_RULES:
        if pattern.search(body):
            return project_id
    return "proj-alicia"


def _link_work_item(store: CanonStore, work: WorkItem) -> WorkItem:
    """Ensure project_id is set and the project rollup lists this work item."""
    if not work.project_id:
        work.project_id = classify_project(f"{work.title}\n{work.description}")
    project = store.get(Project, work.project_id)
    if project is None:
        seed_canon_projects(store)
        project = store.get(Project, work.project_id)
    if project is None:
        project = Project(
            id=work.project_id,
            name=work.project_id,
            objective="auto-created for intake binding",
            owner="justin",
        )
        store.save(project)
    if work.id not in project.work_item_ids:
        project.work_item_ids.append(work.id)
        store.save(project)
    store.save(work)
    return work


def intake_work(
    raw_capture: str,
    *,
    source: str = "alicia:chat",
    project_hint: str = "",
    title: str = "",
    store: CanonStore | None = None,
) -> dict[str, Any]:
    """Justin dumps work → Alicia owns it under a Canon project until done/escalated."""
    text = (raw_capture or "").strip()
    if not text:
        raise ValueError("intake requires non-empty capture text")
    own = store is None
    store = store or CanonStore(canon_db_path())
    try:
        seed_canon_projects(store)
        project_id = classify_project(text, hint=project_hint)
        inbox = capture_manual_inbox_item(store, raw_capture=text, source=source)
        principal = DEFAULT_IDENTITY_REGISTRY.owner_principal()
        require_owner(principal.identity, principal, registry=DEFAULT_IDENTITY_REGISTRY)
        work_title = (title or "").strip() or text.splitlines()[0][:120]
        work = WorkItem(
            title=work_title,
            description=text,
            project_id=project_id,
            origin=inbox.id,
            type=WorkItemType.TASK,
            assignee="alicia",
            bindings={"intake_source": source, "owned_by": "alicia"},
        )
        work = store.promote_inbox_item(
            inbox, reviewed_by=principal.identity, work_item=work
        )
        work.project_id = project_id
        work.assignee = work.assignee or "alicia"
        work = _link_work_item(store, work)
        return {
            "ok": True,
            "inbox_item_id": inbox.id,
            "work_item_id": work.id,
            "project_id": work.project_id,
            "title": work.title,
            "assignee": work.assignee,
            "state": work.state.value if hasattr(work.state, "value") else str(work.state),
            "owned_by": "alicia",
            "note": "Alicia owns this until done or escalated",
        }
    finally:
        if own:
            store.close()


def bind_existing_work_item(
    store: CanonStore, work_item_id: str, *, project_hint: str = ""
) -> WorkItem:
    work = store.get(WorkItem, work_item_id)
    if work is None:
        raise ValueError(f"work item {work_item_id!r} not found")
    if project_hint or not work.project_id:
        work.project_id = classify_project(
            f"{work.title}\n{work.description}", hint=project_hint
        )
    return _link_work_item(store, work)


def backfill_orphan_work_items(
    *,
    limit: int = 80,
    store: CanonStore | None = None,
) -> dict[str, Any]:
    """Classify a meaningful subset of orphan work_items onto seeded projects."""
    own = store is None
    store = store or CanonStore(canon_db_path())
    try:
        seed_canon_projects(store)
        orphans = [w for w in store.list(WorkItem) if not w.project_id]
        bound: list[dict[str, str]] = []
        for work in orphans[: max(0, limit)]:
            work.project_id = classify_project(f"{work.title}\n{work.description}")
            _link_work_item(store, work)
            bound.append({"id": work.id, "project_id": work.project_id, "title": work.title[:80]})
        projects = []
        for project in store.list(Project):
            projects.append(
                {
                    "id": project.id,
                    "name": project.name,
                    "work_item_count": len(project.work_item_ids or []),
                }
            )
        return {
            "ok": True,
            "orphans_seen": len(orphans),
            "bound": len(bound),
            "items": bound,
            "projects": projects,
        }
    finally:
        if own:
            store.close()


def project_rollups(store: CanonStore | None = None) -> list[dict[str, Any]]:
    own = store is None
    store = store or CanonStore(canon_db_path())
    try:
        seed_canon_projects(store)
        out: list[dict[str, Any]] = []
        for project in store.list(Project):
            ids = list(project.work_item_ids or [])
            for work in store.list(WorkItem):
                if work.project_id == project.id and work.id not in ids:
                    ids.append(work.id)
            if ids != list(project.work_item_ids or []):
                project.work_item_ids = ids
                store.save(project)
            out.append(
                {
                    "id": project.id,
                    "name": project.name,
                    "objective": project.objective,
                    "status": getattr(project.status, "value", str(project.status)),
                    "work_item_ids": ids,
                    "work_item_count": len(ids),
                }
            )
        return out
    finally:
        if own:
            store.close()
