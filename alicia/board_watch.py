"""Diff the board into transitions, so the screen can report change instead of state.

Election-night coverage doesn't re-read the whole tally every thirty seconds. It
says what *moved*. That's the difference between a board you glance at and a
board you stop looking at.

Two disciplines, and both are about not becoming noise:

  Diff, don't report.  A count going 2 → 2 is silence. Only transitions exist
                       here: a thread arrives at your desk, a thread finishes,
                       the alarm flips.

  Aggregate at scale.  At 1000 streams, per-item narration is unusable. Above a
                       threshold the line becomes rates and exceptions —
                       "twelve finished, three need you" — and the individual
                       events stay visual only.

The board is a screen. Movement flashes on the card. Nothing in this module
reaches the speaker — unsolicited UUID readouts ("fee needs you") were the
parrot. Phrase() still returns a human line so a future opt-in has somewhere
safe to start; observe() leaves spoken empty.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Literal

from .speechify import speakable_name

log = logging.getLogger("alicia.board_watch")

Kind = Literal["needs_you", "done", "alarm_on", "alarm_off"]

# Kept so callers that pass speak_every= still construct. The doorbell does
# not speak; the screen is the cue.
SPEAK_EVERY_SECONDS = 60.0

# Past this many transitions in one tick, stop naming them and start counting
# them. Chosen low: hearing four ticket ids in a row is already too many.
AGGREGATE_ABOVE = 3


@dataclass(frozen=True)
class Transition:
    kind: Kind
    ticket: str = ""
    title: str = ""

    def phrase(self) -> str:
        """How this reads as a human line. Never a UUID."""
        who = speakable_name(self.ticket, self.title, fallback="a thread")
        if self.kind == "needs_you":
            return f"{who} needs you."
        if self.kind == "done":
            return f"{who} finished."
        if self.kind == "alarm_on":
            return "The factory alarm just went off."
        return "The factory alarm cleared."


@dataclass
class BoardSnapshot:
    """The parts of a board payload that transitions are computed from."""

    needs_you: dict[str, str] = field(default_factory=dict)  # ticket -> title
    open_tickets: dict[str, str] = field(default_factory=dict)
    alarm: bool = False

    @classmethod
    def from_board(cls, board: dict[str, Any]) -> "BoardSnapshot":
        def rows(key: str) -> Iterable[dict[str, Any]]:
            value = board.get(key)
            return value if isinstance(value, list) else []

        def ident(row: dict[str, Any]) -> str:
            # A row without a ticket id still has to be trackable, or every
            # unnamed stuck row looks like a brand new arrival on every tick.
            return str(row.get("ticket") or row.get("thread_id") or row.get("title") or "").strip()

        needs = {ident(r): str(r.get("title") or "") for r in rows("needs_you") if ident(r)}
        everything: dict[str, str] = {}
        for key in ("needs_you", "working", "queued"):
            for r in rows(key):
                if ident(r):
                    everything[ident(r)] = str(r.get("title") or "")
        for group in rows("stuck"):
            for r in group.get("rows", []) if isinstance(group, dict) else []:
                if ident(r):
                    everything[ident(r)] = str(r.get("title") or "")
        alarm = bool((board.get("alarm") or {}).get("alarm"))
        return cls(needs_you=needs, open_tickets=everything, alarm=alarm)


def diff(previous: BoardSnapshot | None, current: BoardSnapshot) -> list[Transition]:
    """What moved between two snapshots. Empty when nothing did."""
    if previous is None:
        # The first tick after a restart is not news. Everything would look new.
        return []

    out: list[Transition] = []
    for ticket, title in current.needs_you.items():
        if ticket not in previous.needs_you:
            out.append(Transition("needs_you", ticket, title))

    # Left the board entirely = finished. A thread that merely moved between
    # queued and working is still open and is not an event.
    for ticket, title in previous.open_tickets.items():
        if ticket not in current.open_tickets:
            out.append(Transition("done", ticket, title))

    if current.alarm != previous.alarm:
        out.append(Transition("alarm_on" if current.alarm else "alarm_off"))
    return out


def spoken_line(transitions: list[Transition]) -> str:
    """One sentence for the ear. Deterministic — no model in this path."""
    if not transitions:
        return ""
    if len(transitions) <= AGGREGATE_ABOVE:
        return " ".join(t.phrase() for t in transitions)

    # Aggregate. Counts first, and the alarm always survives compression
    # because it is the only one of the three that genuinely needs you now.
    done = sum(1 for t in transitions if t.kind == "done")
    needs = sum(1 for t in transitions if t.kind == "needs_you")
    parts: list[str] = []
    if done:
        parts.append(f"{done} finished")
    if needs:
        parts.append(f"{needs} now need you")
    line = ", ".join(parts) if parts else "The board moved"
    alarm = next((t for t in transitions if t.kind in ("alarm_on", "alarm_off")), None)
    return f"{line}. {alarm.phrase()}" if alarm else f"{line}."


class BoardWatcher:
    """Holds the previous snapshot and decides what, if anything, to say."""

    def __init__(
        self,
        *,
        on_event: Callable[[str, dict[str, Any]], None] | None = None,
        clock: Callable[[], float] = time.monotonic,
        speak_every: float = SPEAK_EVERY_SECONDS,
    ) -> None:
        self._on_event = on_event or (lambda _k, _p: None)
        self._clock = clock
        self._speak_every = speak_every
        self._previous: BoardSnapshot | None = None

    def observe(self, board: dict[str, Any]) -> dict[str, Any]:
        """Take a board payload; return what moved. `spoken` is always empty."""
        current = BoardSnapshot.from_board(board)
        transitions = diff(self._previous, current)
        self._previous = current

        payload = {
            "session_id": "board",  # the bus keys on this; the board is global
            "transitions": [
                {"kind": t.kind, "ticket": t.ticket, "title": t.title} for t in transitions
            ],
            "spoken": "",  # visual only — the mouth does not parrot the board
            "counts": board.get("counts") or {},
            "headline": board.get("headline") or "",
            "alarm": current.alarm,
        }
        # Always publish movement. The screen flashes it; spoken stays empty.
        if transitions:
            # One line in the serve log is the only way to tell a quiet board
            # from a broken poller — the UI can only prove the happy path.
            kinds = ",".join(t.kind for t in transitions)
            log.info("doorbell %s visual=%s", kinds, len(transitions))
            self._on_event("board", payload)
        return payload
