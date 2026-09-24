"""Slack work-signal polling -> Canon InboxItem bridge (REV-516).

Slack credentials and channel selection remain on Atlas6.  Alicia reuses its
existing ``/api/peek/slack`` polling response rather than introducing another
Slack client or webhook consumer.  This module deliberately only captures
immutable InboxItems; promotion remains an explicit owner CLI action.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx

from .models import InboxItem
from .store import CanonStore


@dataclass(frozen=True)
class SlackInboxCaptureResult:
    """Inspectable result of one Atlas6 Slack polling response."""

    captured_ids: tuple[str, ...] = ()
    duplicate_ids: tuple[str, ...] = ()
    ignored: int = 0


def is_slack_notification_channel(notify_channel: str) -> bool:
    """Return whether ``notify_channel`` uses the supported Slack forms.

    ``slack:<channel>`` and ``slack://<channel>`` deliver through
    ``ALICIA_SLACK_WEBHOOK_URL``. A Slack incoming-webhook URL may instead be
    supplied directly (or after either ``slack:`` prefix), which is useful for
    a Watch with its own destination.
    """

    value = notify_channel.strip().lower()
    return value.startswith(("slack:", "slack://", "https://hooks.slack.com/"))


def send_slack_message(notify_channel: str, message: str) -> None:
    """Post a Watch message through a Slack incoming webhook.

    A direct ``https://hooks.slack.com/...`` value takes precedence. Otherwise
    a ``slack:<channel>`` / ``slack://<channel>`` value uses the configured
    ``ALICIA_SLACK_WEBHOOK_URL`` and requests that channel in the webhook
    payload. The configured webhook is intentionally an environment secret,
    not Canon data.
    """

    webhook_url, channel = _notification_destination(notify_channel)
    payload: dict[str, str] = {"text": message}
    if channel:
        payload["channel"] = channel
    with httpx.Client(timeout=10.0) as client:
        response = client.post(webhook_url, json=payload)
        response.raise_for_status()


def _notification_destination(notify_channel: str) -> tuple[str, str | None]:
    """Resolve a supported notification value without exposing webhook secrets."""

    value = notify_channel.strip()
    direct_webhook = _direct_webhook(value)
    if direct_webhook:
        return direct_webhook, None

    if not is_slack_notification_channel(value):
        raise ValueError(f"not a Slack notification channel: {notify_channel!r}")
    webhook_url = os.environ.get("ALICIA_SLACK_WEBHOOK_URL", "").strip()
    if not webhook_url:
        raise RuntimeError(
            "Slack Watch delivery needs ALICIA_SLACK_WEBHOOK_URL or a direct "
            "https://hooks.slack.com/... notify_channel"
        )

    channel = value
    if channel.lower().startswith("slack://"):
        channel = channel[len("slack://") :]
    elif channel.lower().startswith("slack:"):
        channel = channel[len("slack:") :]
    return webhook_url, channel or None


def _direct_webhook(value: str) -> str | None:
    """Return a directly supplied Slack incoming webhook, if present."""

    candidate = value
    if candidate.lower().startswith("slack://"):
        candidate = candidate[len("slack://") :]
    elif candidate.lower().startswith("slack:"):
        candidate = candidate[len("slack:") :]
    if candidate.lower().startswith("https://hooks.slack.com/"):
        return candidate
    return None


def capture_slack_items(store: CanonStore, payload: Any) -> SlackInboxCaptureResult:
    """Persist qualifying Atlas6 Slack work signals as unreviewed InboxItems.

    The existing Atlas6 integration supplies messages from its configured
    capture channels through ``AtlasClient.peek_slack``.  A source JSON record
    made from the Slack conversation, sender, and timestamp is both owner-
    readable provenance and the durable idempotency key for repeated polls.
    """

    existing_by_source = {item.source: item for item in store.list(InboxItem)}
    captured_ids: list[str] = []
    duplicate_ids: list[str] = []
    ignored = 0

    for raw_item in _items_from(payload):
        message = _message_from(raw_item)
        if message is None:
            ignored += 1
            continue

        raw_capture = _message_text(message)
        timestamp = _message_timestamp(message)
        if raw_capture is None or timestamp is None or _is_bot_message(message):
            ignored += 1
            continue

        received_at = _received_at(timestamp)
        if received_at is None:
            ignored += 1
            continue

        source = _source(message, timestamp)
        existing = existing_by_source.get(source)
        if existing is not None:
            duplicate_ids.append(existing.id)
            continue

        item = InboxItem(raw_capture=raw_capture, source=source, received_at=received_at)
        store.save(item)
        existing_by_source[source] = item
        captured_ids.append(item.id)

    return SlackInboxCaptureResult(
        captured_ids=tuple(captured_ids),
        duplicate_ids=tuple(duplicate_ids),
        ignored=ignored,
    )


def _items_from(payload: Any) -> list[Any]:
    if isinstance(payload, list):
        return payload
    if not isinstance(payload, dict):
        return []
    for key in ("items", "messages"):
        items = payload.get(key)
        if isinstance(items, list):
            return items
    # This also permits callers which hand us one Slack Events API-shaped
    # envelope, while Atlas6's normal polling response uses ``items``.
    return [payload]


def _message_from(raw_item: Any) -> dict[str, Any] | None:
    if not isinstance(raw_item, dict):
        return None
    event = raw_item.get("event")
    if isinstance(event, dict):
        return event
    return raw_item


def _message_text(message: dict[str, Any]) -> str | None:
    for key in ("text", "message", "title"):
        value = message.get(key)
        if isinstance(value, str) and value.strip():
            # Do not strip: InboxItem.raw_capture is intentionally verbatim.
            return value
    return None


def _message_timestamp(message: dict[str, Any]) -> str | None:
    for key in ("ts", "timestamp", "received_at", "receivedAt"):
        value = message.get(key)
        if isinstance(value, (str, int, float)) and str(value).strip():
            return str(value)
    return None


def _received_at(timestamp: str) -> datetime | None:
    try:
        return datetime.fromtimestamp(float(timestamp), tz=UTC)
    except (TypeError, ValueError, OverflowError):
        pass
    try:
        parsed = datetime.fromisoformat(timestamp)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _source(message: dict[str, Any], timestamp: str) -> str:
    """Return canonical, structured provenance for a captured Slack message."""

    channel = _first_text(message, "channel_id", "channel", "conversation_id") or "unknown"
    sender = _first_text(message, "user_id", "user", "sender_id", "sender", "username") or "unknown"
    source: dict[str, str] = {
        "channel": channel,
        "sender": sender,
        "timestamp": timestamp,
        "type": "slack",
    }
    channel_name = _first_text(message, "channel_name")
    if channel_name:
        source["channel_name"] = channel_name
    return json.dumps(source, sort_keys=True, separators=(",", ":"))


def _first_text(message: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = message.get(key)
        if isinstance(value, (str, int, float)) and str(value).strip():
            return str(value)
    return None


def _is_bot_message(message: dict[str, Any]) -> bool:
    if message.get("bot_id") or message.get("bot_profile"):
        return True
    return message.get("subtype") in {"bot_message", "message_changed", "message_deleted"}
