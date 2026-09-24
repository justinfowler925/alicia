"""REV-517 Watch trigger evaluation and outbound-dispatch tests."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock, patch

from alicia.canon import CanonStore, Watch, WorkItem, WorkItemState, transition
from alicia.canon.cli import _run_watch_command
from alicia.canon.watches import trigger_state


def _watch(store: CanonStore, work_item: WorkItem, *, condition: str = "blocked") -> Watch:
    watch = Watch(
        target=work_item.id,
        watcher="owner@example.com",
        trigger_condition=condition,
        notify_channel="slack:#canon-alerts",
    )
    store.save(watch)
    return watch


def test_matching_transition_dispatches_slack_with_work_item_context() -> None:
    store = CanonStore()
    try:
        work_item = WorkItem(title="Watch runtime")
        store.save(work_item)
        watch = _watch(store, work_item)

        with patch("alicia.canon.watches.send_slack_message") as send_slack:
            transition(work_item, WorkItemState.BLOCKED, "worker", reason="waiting for API")
            store.save(work_item)

        send_slack.assert_called_once()
        channel, message = send_slack.call_args.args
        assert channel == "slack:#canon-alerts"
        assert f"Watch {watch.id} fired" in message
        assert f"Work Item {work_item.id} (Watch runtime)" in message
        assert "state is blocked" in message
        persisted = store.get(Watch, watch.id)
        assert persisted is not None
        assert persisted.last_fired_state == WorkItemState.BLOCKED
        assert persisted.last_fired_state_history_index == 1
    finally:
        store.close()


def test_non_matching_transition_does_not_dispatch() -> None:
    store = CanonStore()
    try:
        work_item = WorkItem(title="No review alert")
        store.save(work_item)
        _watch(store, work_item, condition="state == review")

        with patch("alicia.canon.watches.send_slack_message") as send_slack:
            transition(work_item, WorkItemState.BLOCKED, "worker", reason="waiting for API")
            store.save(work_item)

        send_slack.assert_not_called()
    finally:
        store.close()


def test_repeat_save_of_same_state_entry_does_not_dispatch_twice() -> None:
    store = CanonStore()
    try:
        work_item = WorkItem(title="One alert only")
        store.save(work_item)
        _watch(store, work_item)

        with patch("alicia.canon.watches.send_slack_message") as send_slack:
            transition(work_item, WorkItemState.BLOCKED, "worker", reason="waiting for API")
            store.save(work_item)
            store.save(work_item)

        send_slack.assert_called_once()
    finally:
        store.close()


def test_watch_cli_lists_shows_and_force_tests_a_watch(capsys) -> None:
    store = CanonStore()
    try:
        work_item = WorkItem(title="CLI target")
        store.save(work_item)
        watch = _watch(store, work_item, condition="triage")

        _run_watch_command(store, SimpleNamespace(watch_command="list"))
        assert watch.id in capsys.readouterr().out

        _run_watch_command(store, SimpleNamespace(watch_command="show", watch_id=watch.id))
        assert '"trigger_condition": "triage"' in capsys.readouterr().out

        sender = Mock()
        with patch("alicia.canon.watches.send_slack_message", sender):
            _run_watch_command(store, SimpleNamespace(watch_command="test", watch_id=watch.id))
        assert "delivered" in capsys.readouterr().out
        sender.assert_called_once()
        # A manual test must not consume the real state entry's idempotency key.
        persisted = store.get(Watch, watch.id)
        assert persisted is not None
        assert persisted.last_fired_state_history_index is None
    finally:
        store.close()


def test_trigger_condition_accepts_state_name_or_explicit_state_expression() -> None:
    assert trigger_state("review") == WorkItemState.REVIEW
    assert trigger_state(" state == review ") == WorkItemState.REVIEW
    assert trigger_state("state=review") is None
    assert trigger_state("priority==1") is None
