"""Fan out session events to whoever is watching the screen.

Election-night coverage means results *arrive*. A page that polls shows you a
snapshot on a timer; a page fed by this shows you the moment. Same reason the
deep lane can afford to be slow — you can see it working.

Two properties matter and both are about not hurting the turn:

  Never block.   A slow or dead browser must not stall the conversation, so
                 every queue is bounded and a full one drops its oldest item
                 rather than applying backpressure.

  Never throw.   publish() is called from inside the turn pipeline. A listener
                 that has gone away is normal, not an error.
"""

from __future__ import annotations

import asyncio
import json
import threading
from typing import Any

# Per-subscriber depth. Deep enough to ride out a re-render, shallow enough
# that a backgrounded tab can't accumulate a minute of stale events to replay
# at you when it wakes up.
QUEUE_DEPTH = 64


class SessionEventBus:
    """In-process pub/sub, keyed by session id. No broker, no persistence."""

    def __init__(self) -> None:
        self._subs: dict[str, list[asyncio.Queue]] = {}
        self._loop: asyncio.AbstractEventLoop | None = None
        self._lock = threading.Lock()

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Remember the serving loop so worker threads can publish into it."""
        self._loop = loop

    # --- producer side (may be called from any thread) --------------------

    def publish(self, kind: str, payload: dict[str, Any]) -> None:
        session_id = str(payload.get("session_id") or "")
        if not session_id:
            return
        event = {"kind": kind, **payload}
        with self._lock:
            queues = list(self._subs.get(session_id, ()))
        if not queues:
            return
        loop = self._loop
        for q in queues:
            if loop and not loop.is_closed():
                # The deep lane runs on a worker thread; hop to the loop.
                loop.call_soon_threadsafe(self._offer, q, event)
            else:
                self._offer(q, event)

    @staticmethod
    def _offer(q: asyncio.Queue, event: dict[str, Any]) -> None:
        """Drop the oldest rather than block. Stale is worse than missing."""
        try:
            q.put_nowait(event)
        except asyncio.QueueFull:
            try:
                q.get_nowait()
                q.put_nowait(event)
            except Exception:  # noqa: BLE001
                pass
        except Exception:  # noqa: BLE001
            pass

    # --- consumer side ----------------------------------------------------

    def subscribe(self, session_id: str, queue: asyncio.Queue | None = None) -> asyncio.Queue:
        q = queue if queue is not None else asyncio.Queue(maxsize=QUEUE_DEPTH)
        with self._lock:
            self._subs.setdefault(session_id, []).append(q)
        return q

    def unsubscribe(self, session_id: str, q: asyncio.Queue) -> None:
        with self._lock:
            queues = self._subs.get(session_id)
            if not queues:
                return
            if q in queues:
                queues.remove(q)
            if not queues:
                self._subs.pop(session_id, None)

    def subscriber_count(self, session_id: str) -> int:
        with self._lock:
            return len(self._subs.get(session_id, ()))


def sse(event: dict[str, Any]) -> str:
    """One server-sent event frame."""
    return f"data: {json.dumps(event, default=str)}\n\n"
