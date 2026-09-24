"""Watch trigger evaluation and notification dispatch (REV-517).

The v1 ``Watch.trigger_condition`` grammar deliberately stays small and
deterministic:

* ``review`` matches a Work Item whose current state is ``review``.
* ``state==review`` is the explicit equivalent.

State names are values of :class:`~alicia.canon.models.WorkItemState`; whitespace
around the condition and ``==`` is ignored. Invalid expressions never match and
are logged rather than breaking a canonical state write.

``Watch.target`` is a Work Item id for this first runtime. A Watch fires once
for each matching state-history entry. Persisting the last fired history index
keeps unrelated repeat saves from producing duplicate notifications, while a
later re-entry into the same state can notify again.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

from .models import Watch, WorkItem, WorkItemState
from .slack import is_slack_notification_channel, send_slack_message

if TYPE_CHECKING:
    from .store import CanonStore

logger = logging.getLogger(__name__)
_STATE_CONDITION = re.compile(r"^state\s*==\s*(?P<state>[a-z_]+)$")


@dataclass(frozen=True)
class WatchEvaluation:
    """The inspectable result of evaluating one Watch."""

    watch_id: str
    matched: bool
    delivered: bool
    reason: str


def trigger_state(trigger_condition: str) -> WorkItemState | None:
    """Parse the minimal v1 state trigger grammar, returning ``None`` if invalid."""

    condition = trigger_condition.strip()
    explicit_match = _STATE_CONDITION.fullmatch(condition)
    if explicit_match:
        condition = explicit_match["state"]
    elif "=" in condition:
        return None
    try:
        return WorkItemState(condition)
    except ValueError:
        return None


def watch_matches(watch: Watch, work_item: WorkItem) -> bool:
    """Return whether an active Work Item Watch matches its current state."""

    expected_state = trigger_state(watch.trigger_condition)
    return bool(watch.active and watch.target == work_item.id and expected_state == work_item.state)


def watch_message(watch: Watch, work_item: WorkItem) -> str:
    """Build the concise, owner-readable outbound notification body."""

    return (
        f"Watch {watch.id} fired for Work Item {work_item.id} ({work_item.title}): "
        f"state is {work_item.state.value} (condition: {watch.trigger_condition}). "
        f"Watcher: {watch.watcher}."
    )


def evaluate_watches(store: CanonStore, work_item: WorkItem) -> list[WatchEvaluation]:
    """Evaluate active Watches targeted at a persisted Work Item.

    CanonStore calls this after writing a Work Item. Dispatch failures are
    contained and logged: a notification outage must never roll back or reject
    a legitimate canonical state transition.
    """

    return [
        evaluate_watch(store, watch, work_item)
        for watch in store.list(Watch)
        if watch.target == work_item.id
    ]


def evaluate_watch(
    store: CanonStore,
    watch: Watch,
    work_item: WorkItem,
    *,
    force: bool = False,
) -> WatchEvaluation:
    """Evaluate and dispatch one Watch; ``force`` supports CLI test delivery."""

    if not watch.active:
        return WatchEvaluation(watch.id, matched=False, delivered=False, reason="watch is inactive")
    if watch.target != work_item.id:
        return WatchEvaluation(watch.id, matched=False, delivered=False, reason="target does not match")

    expected_state = trigger_state(watch.trigger_condition)
    if expected_state is None:
        logger.warning(
            "Watch %s has invalid trigger_condition %r; expected a state name or state==<state>",
            watch.id,
            watch.trigger_condition,
        )
        return WatchEvaluation(watch.id, matched=False, delivered=False, reason="invalid trigger condition")
    if expected_state != work_item.state:
        return WatchEvaluation(watch.id, matched=False, delivered=False, reason="state does not match")

    history_index = len(work_item.state_history)
    if not force and watch.last_fired_state_history_index == history_index:
        return WatchEvaluation(watch.id, matched=True, delivered=False, reason="already fired for this state entry")

    message = watch_message(watch, work_item)
    try:
        delivered = _dispatch_notification(watch.notify_channel, message)
    except Exception:
        logger.exception("Watch %s delivery to %r failed", watch.id, watch.notify_channel)
        return WatchEvaluation(watch.id, matched=True, delivered=False, reason="delivery failed")

    if not delivered:
        return WatchEvaluation(
            watch.id,
            matched=True,
            delivered=False,
            reason="no outbound integration for notification channel",
        )

    if not force:
        watch.last_fired_state_history_index = history_index
        watch.last_fired_state = work_item.state
        store.save(watch)
    return WatchEvaluation(watch.id, matched=True, delivered=True, reason="delivered")


def _dispatch_notification(notify_channel: str, message: str) -> bool:
    """Dispatch the Slack-only Watch contract enforced by the model."""

    if is_slack_notification_channel(notify_channel):
        send_slack_message(notify_channel, message)
        return True
    logger.warning("Watch notification channel bypassed model validation: %r", notify_channel)
    return False
