"""Portfolio reporting queries for Canon Projects, Work Items, and Runs."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from .models import Project, Run, RunStatus, WorkItem, WorkItemState

DEFAULT_STUCK_AFTER = timedelta(hours=48)
DEFAULT_FAILED_RUN_LOOKBACK = timedelta(days=7)
STUCK_STATES = frozenset({WorkItemState.REVIEW, WorkItemState.VALIDATION})


@dataclass(frozen=True)
class ProjectStateRollup:
    """State totals and blocked Work Items for one Project."""

    project: Project
    state_counts: dict[WorkItemState, int]
    blocked_items: tuple[WorkItem, ...]


@dataclass(frozen=True)
class WorkItemAging:
    """The current-state age and stuck assessment for one Work Item."""

    work_item: WorkItem
    state_entered_at: datetime | None
    age: timedelta | None
    is_stuck: bool


@dataclass(frozen=True)
class PortfolioReport:
    """The data shown by the owner-facing portfolio report."""

    generated_at: datetime
    project_rollups: tuple[ProjectStateRollup, ...]
    work_item_aging: tuple[WorkItemAging, ...]
    failed_runs: tuple[Run, ...]
    stuck_after: timedelta
    failed_run_lookback: timedelta


def _utc(value: datetime) -> datetime:
    """Normalize persisted timestamps, including older naive values, to UTC."""
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def current_state_entered_at(work_item: WorkItem) -> datetime | None:
    """Return the timestamp at which ``work_item`` entered its current state.

    StateHistoryEntry already records every state transition.  It takes
    precedence because it preserves the actual transition time for manually
    constructed or pre-REV-522 objects.  ``state_entered_at`` covers a newly
    created item's initial triage state, which has no transition entry.
    """
    matching_entries = [entry.time for entry in work_item.state_history if entry.state == work_item.state]
    if matching_entries:
        return max(_utc(entry_time) for entry_time in matching_entries)
    if work_item.state_entered_at is not None:
        return _utc(work_item.state_entered_at)
    return None


def age_work_item(
    work_item: WorkItem,
    *,
    now: datetime | None = None,
    stuck_after: timedelta = DEFAULT_STUCK_AFTER,
) -> WorkItemAging:
    """Calculate a Work Item's current-state age and configured stuck flag."""
    if stuck_after < timedelta():
        raise ValueError("stuck_after must not be negative")
    current_time = _utc(now or datetime.now(UTC))
    entered_at = current_state_entered_at(work_item)
    age = None if entered_at is None else max(timedelta(), current_time - entered_at)
    is_stuck = (
        age is not None
        and work_item.state in STUCK_STATES
        and age >= stuck_after
    )
    return WorkItemAging(
        work_item=work_item,
        state_entered_at=entered_at,
        age=age,
        is_stuck=is_stuck,
    )


def project_state_rollups(
    projects: Iterable[Project],
    work_items: Iterable[WorkItem],
) -> list[ProjectStateRollup]:
    """Return each Project's Work Item totals grouped by current state.

    ``Project.work_item_ids`` is the canonical membership list.  The
    WorkItem.project_id link is included as a backwards-compatible reverse
    lookup, so older callers that only set that field remain reportable.
    """
    items = list(work_items)
    rollups: list[ProjectStateRollup] = []
    for project in projects:
        project_item_ids = set(project.work_item_ids)
        project_items = [
            item
            for item in items
            if item.id in project_item_ids or item.project_id == project.id
        ]
        state_counts: dict[WorkItemState, int] = {}
        for item in project_items:
            state_counts[item.state] = state_counts.get(item.state, 0) + 1
        blocked_items = tuple(item for item in project_items if item.state == WorkItemState.BLOCKED)
        rollups.append(
            ProjectStateRollup(
                project=project,
                state_counts=state_counts,
                blocked_items=blocked_items,
            )
        )
    return rollups


def failed_runs_within(
    runs: Iterable[Run],
    *,
    lookback: timedelta = DEFAULT_FAILED_RUN_LOOKBACK,
    now: datetime | None = None,
) -> list[Run]:
    """List failed Runs whose end time (or start time if still unset) is recent."""
    if lookback < timedelta():
        raise ValueError("lookback must not be negative")
    current_time = _utc(now or datetime.now(UTC))
    since = current_time - lookback
    failed_runs = [
        run
        for run in runs
        if run.status == RunStatus.FAILED
        and since <= _utc(run.ended_at or run.started_at) <= current_time
    ]
    return sorted(failed_runs, key=lambda run: _utc(run.ended_at or run.started_at), reverse=True)


def build_portfolio_report(
    projects: Iterable[Project],
    work_items: Iterable[WorkItem],
    runs: Iterable[Run],
    *,
    now: datetime | None = None,
    stuck_after: timedelta = DEFAULT_STUCK_AFTER,
    failed_run_lookback: timedelta = DEFAULT_FAILED_RUN_LOOKBACK,
) -> PortfolioReport:
    """Build project rollups, current-state aging, stuck items, and failed runs."""
    generated_at = _utc(now or datetime.now(UTC))
    items = list(work_items)
    aging = tuple(
        age_work_item(item, now=generated_at, stuck_after=stuck_after)
        for item in items
    )
    return PortfolioReport(
        generated_at=generated_at,
        project_rollups=tuple(project_state_rollups(projects, items)),
        work_item_aging=aging,
        failed_runs=tuple(
            failed_runs_within(runs, lookback=failed_run_lookback, now=generated_at)
        ),
        stuck_after=stuck_after,
        failed_run_lookback=failed_run_lookback,
    )
