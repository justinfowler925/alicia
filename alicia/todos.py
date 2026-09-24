"""Justin's work queue — everything he says, turned into something workable.

This is deliberately laptop-local. The Studio ledger is the SSOT for *tracked
work the bots execute*; this is the layer BEFORE that — half-formed thoughts,
reminders, "look into X" — which must be capturable in two seconds without
deciding anything. An item graduates into the real ledger via promote(), which
registers it as a manual thread the conductor then triages like any ticket.

The pipeline is the point. A capture lands verbatim in `raw` and moves:

    Captured -> Refining -> Ready -> Working -> Done

`Captured` is untouched speech. `Refining` means a title and a one-line summary
have been drafted and the open questions listed, but nobody has confirmed them —
a draft is never allowed to masquerade as a decision. `Ready` means there is
enough detail to actually start. `Working` covers in-flight items, blocked or
not; blocked is a flag on the row and a written word on screen, never a stage of
its own and never a colour alone.

Three fields carry the text, because one field could not:

    raw      the verbatim capture, never rewritten — the record of what was said
    text     the short human-facing line (a drafted title once refined)
    summary  one line of what it actually is

Storage is one sqlite file under the state dir (outside every checkout). Losing
it loses nothing the bots were doing — by design, per the invariant that the
laptop never owns the work ledger.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass, field, fields
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .paths import may_migrate_shared_schema, state_path

STATUSES = ("todo", "doing", "done")

#: The pipeline, in order. Column order on screen is this order.
STAGES = ("Captured", "Refining", "Ready", "Working", "Done")

#: Kept because promote(), the MCP tools and the old board still speak lanes.
LANES = ("Inbox", "In Progress", "Blocked", "Done")

_STAGE_FOR_LANE = {
    "Inbox": "Captured",
    "In Progress": "Working",
    "Blocked": "Working",
    "Done": "Done",
}

_LANE_FOR_STAGE = {
    "Captured": "Inbox",
    "Refining": "Inbox",
    "Ready": "Inbox",
    "Working": "In Progress",
    "Done": "Done",
}

_STATUS_FOR_STAGE = {
    "Captured": "todo",
    "Refining": "todo",
    "Ready": "todo",
    "Working": "doing",
    "Done": "done",
}

#: A title has to fit one line of a card at the narrowest column we ship.
TITLE_MAX = 90
SUMMARY_MAX = 240
RAW_MAX = 4000


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _status_for_lane(lane: str) -> str:
    return {"Inbox": "todo", "In Progress": "doing", "Blocked": "doing", "Done": "done"}.get(lane, "todo")


def stage_index(stage: str) -> int:
    try:
        return STAGES.index(stage)
    except ValueError:
        return 0


@dataclass
class Todo:
    id: str
    text: str
    status: str
    created_at: str
    updated_at: str
    promoted_ticket: str = ""
    tags: str = ""
    lane: str = ""
    stage: str = "Captured"
    raw: str = ""
    summary: str = ""
    missing: list[str] = field(default_factory=list)
    blocked: int = 0
    refined_at: str = ""
    source: str = ""

    @classmethod
    def from_row(cls, row: Any) -> "Todo":
        """Build from a `SELECT *` row, ignoring columns this version does not know.

        `Todo(**dict(row))` raises TypeError the moment the table gains a column, and
        the table is shared: a feature branch run against ~/.alicia/state added
        stage/raw/summary/missing/blocked/refined_at/source on 2026-08-08 and every
        read path on main died with it — /api/todos returned 500 and the Ideas pad went
        blank, on a database the deployed code had never been told about. A row mapper
        must survive a column it has not heard of; forward-compatibility here is the
        difference between an older reader degrading and an older reader crashing.
        """
        known = {f.name for f in fields(cls)}
        d = {k: v for k, v in dict(row).items() if k in known}
        # `missing` is a JSON list in the column and a list on the dataclass. It
        # is decoded here rather than at each call site so the two mappers that
        # used to exist cannot disagree about it — and tolerantly, because a
        # malformed value should cost the open questions on one card, not every
        # read of the table.
        raw_missing = d.get("missing") or ""
        if isinstance(raw_missing, str):
            try:
                parsed = json.loads(raw_missing) if raw_missing else []
            except (TypeError, ValueError):
                parsed = []
            d["missing"] = [str(x) for x in parsed] if isinstance(parsed, list) else []
        return cls(**d)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "text": self.text,
            "status": self.status,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "promoted_ticket": self.promoted_ticket,
            "tags": self.tags,
            "lane": self.lane,
            "stage": self.stage,
            "raw": self.raw,
            "summary": self.summary,
            "missing": list(self.missing or []),
            "blocked": bool(self.blocked),
            "refined_at": self.refined_at,
            "source": self.source,
        }


class TodoStore:
    def __init__(self, path: Path | str | None = None) -> None:
        self.path = Path(path) if path else state_path("todos.sqlite")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.path), timeout=10)
        conn.row_factory = sqlite3.Row
        return conn

    def _init(self) -> None:
        with self._conn() as c:
            c.execute(
                """CREATE TABLE IF NOT EXISTS todos (
                    id TEXT PRIMARY KEY,
                    text TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'todo',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    promoted_ticket TEXT NOT NULL DEFAULT ''
                )"""
            )
            # Migration for work-stream items (REV-364): tags + kanban lanes,
            # then the pipeline columns the staged queue reads.
            #
            # Guarded, and the guard matters more now than it did for two columns:
            # a branch checkout that adds seven of them to the live database
            # breaks the running daemon's reads on the next query. Branch work
            # runs against its own state — ALICIA_STATE_DIR=/tmp/alicia-scratch.
            have = {r["name"] for r in c.execute("PRAGMA table_info(todos)")}
            want = [
                ("tags", "TEXT NOT NULL DEFAULT ''"),
                ("lane", "TEXT NOT NULL DEFAULT ''"),
                ("stage", "TEXT NOT NULL DEFAULT ''"),
                ("raw", "TEXT NOT NULL DEFAULT ''"),
                ("summary", "TEXT NOT NULL DEFAULT ''"),
                ("missing", "TEXT NOT NULL DEFAULT ''"),
                ("blocked", "INTEGER NOT NULL DEFAULT 0"),
                ("refined_at", "TEXT NOT NULL DEFAULT ''"),
                ("source", "TEXT NOT NULL DEFAULT ''"),
            ]
            missing = [(col, dtype) for col, dtype in want if col not in have]
            if missing and not may_migrate_shared_schema(self.path):
                raise RuntimeError(
                    f"refusing to add {', '.join(col for col, _ in missing)} to the shared "
                    f"todos table at {self.path}: this code is not the deployed artifact. "
                    "A column added here breaks the daemon's reads immediately. Run branch "
                    "work against its own state: ALICIA_STATE_DIR=/tmp/alicia-scratch"
                )
            for col, dtype in missing:
                c.execute(f"ALTER TABLE todos ADD COLUMN {col} {dtype}")
            # Backfill legacy rows so the UI always has a lane.
            c.execute(
                "UPDATE todos SET lane = CASE status WHEN 'doing' THEN 'In Progress' "
                "WHEN 'done' THEN 'Done' ELSE 'Inbox' END WHERE lane = ''"
            )
            # Every pre-pipeline row is an unrefined capture. Derive its stage
            # from the lane it already had rather than dumping 160 rows into one
            # column, and keep the verbatim text as `raw` so refining later has
            # the original to work from instead of a truncated summary.
            c.execute("UPDATE todos SET blocked = 1 WHERE lane = 'Blocked' AND blocked = 0")
            c.execute(
                "UPDATE todos SET stage = CASE lane "
                "WHEN 'In Progress' THEN 'Working' WHEN 'Blocked' THEN 'Working' "
                "WHEN 'Done' THEN 'Done' ELSE 'Captured' END WHERE stage = ''"
            )
            c.execute("UPDATE todos SET raw = text WHERE raw = ''")

    # --- writes ----------------------------------------------------------

    def add(
        self,
        text: str,
        tags: str = "",
        lane: str = "",
        *,
        stage: str = "",
        source: str = "",
        raw: str = "",
    ) -> Todo:
        """Land a capture. Never rejects for missing detail — that is what
        refining is for, and a pad that argues at capture time is a pad you
        stop using."""
        text = (text or "").strip()
        if not text:
            raise ValueError("empty todo")
        raw = (raw or text).strip()[:RAW_MAX]

        stage = (stage or "").strip()
        if stage not in STAGES:
            lane_in = (lane or "").strip()
            stage = _STAGE_FOR_LANE.get(lane_in, "Captured")
        blocked = 1 if (lane or "").strip() == "Blocked" else 0

        t = Todo(
            id=uuid.uuid4().hex[:12],
            text=text[:500],
            status=_STATUS_FOR_STAGE[stage],
            lane=_LANE_FOR_STAGE[stage],
            stage=stage,
            raw=raw,
            blocked=blocked,
            tags=(tags or "").strip(),
            source=(source or "").strip()[:40],
            created_at=_now(),
            updated_at=_now(),
        )
        with self._conn() as c:
            c.execute(
                "INSERT INTO todos (id, text, status, created_at, updated_at, promoted_ticket, "
                "tags, lane, stage, raw, summary, missing, blocked, refined_at, source) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    t.id, t.text, t.status, t.created_at, t.updated_at, t.promoted_ticket,
                    t.tags, t.lane, t.stage, t.raw, "", "", t.blocked, "", t.source,
                ),
            )
        return t

    def refine(
        self,
        todo_id: str,
        *,
        title: str,
        summary: str = "",
        missing: list[str] | None = None,
        tags: str | None = None,
    ) -> Todo | None:
        """Attach a drafted title/summary and move the item to Refining.

        A draft never lands in Ready. Confirming it is a separate act, so a
        summariser's guess can't quietly become the thing you work from.
        """
        title = (title or "").strip()[:TITLE_MAX]
        if not title:
            raise ValueError("refine needs a title")
        current = self.get(todo_id)
        if current is None:
            return None
        # Anything already past refining stays where it is; only the words change.
        stage = current.stage if stage_index(current.stage) > stage_index("Refining") else "Refining"
        return self.update(
            todo_id,
            text=title,
            summary=(summary or "").strip()[:SUMMARY_MAX],
            missing=list(missing or []),
            stage=stage,
            tags=tags,
            refined_at=_now(),
        )

    def update(
        self,
        todo_id: str,
        *,
        text: str | None = None,
        status: str | None = None,
        lane: str | None = None,
        stage: str | None = None,
        tags: str | None = None,
        promoted_ticket: str | None = None,
        raw: str | None = None,
        summary: str | None = None,
        missing: list[str] | None = None,
        blocked: bool | None = None,
        refined_at: str | None = None,
    ) -> Todo | None:
        sets, vals = ["updated_at=?"], [_now()]

        if text is not None:
            sets.append("text=?"); vals.append(text.strip()[:500])
        if raw is not None:
            sets.append("raw=?"); vals.append(raw.strip()[:RAW_MAX])
        if summary is not None:
            sets.append("summary=?"); vals.append(summary.strip()[:SUMMARY_MAX])
        if missing is not None:
            sets.append("missing=?"); vals.append(json.dumps([str(x)[:200] for x in missing]))
        if blocked is not None:
            sets.append("blocked=?"); vals.append(1 if blocked else 0)
        if refined_at is not None:
            sets.append("refined_at=?"); vals.append(refined_at)
        if promoted_ticket is not None:
            sets.append("promoted_ticket=?"); vals.append(promoted_ticket)
            # Handing an item to the ledger settles the title question. Without
            # this, promoted items kept the "no title yet" badge forever: the
            # refiner only sweeps stage='Captured', so nothing would ever clear
            # it, and the queue accused work already underway of being a draft.
            if promoted_ticket.strip() and refined_at is None:
                current = self.get(todo_id)
                if current and not current.refined_at:
                    sets.append("refined_at=?"); vals.append(_now())
        if tags is not None:
            sets.append("tags=?"); vals.append(tags.strip())

        # Stage is the authority. Lane and status are derived so every legacy
        # reader keeps working without a second source of truth to drift from.
        if stage is not None:
            stage = stage.strip()
            if stage not in STAGES:
                raise ValueError(f"stage must be one of {STAGES}")
            sets.append("stage=?"); vals.append(stage)
            sets.append("lane=?"); vals.append(_LANE_FOR_STAGE[stage])
            sets.append("status=?"); vals.append(_STATUS_FOR_STAGE[stage])
        elif lane is not None:
            lane = lane.strip()
            if lane not in LANES:
                raise ValueError(f"lane must be one of {LANES}")
            sets.append("lane=?"); vals.append(lane)
            sets.append("status=?"); vals.append(_status_for_lane(lane))
            sets.append("stage=?"); vals.append(_STAGE_FOR_LANE[lane])
            if lane == "Blocked":
                sets.append("blocked=?"); vals.append(1)
        elif status is not None:
            if status not in STATUSES:
                raise ValueError(f"status must be one of {STATUSES}")
            sets.append("status=?"); vals.append(status)
            # Carry the stage with it. The board filters on stage, so a bare
            # status write left "mark it done" — the phrasing the voice path
            # actually uses — showing the item forever: closed by one reader,
            # still open to the one on screen.
            #
            # Only the two unambiguous directions move. Reopening says nothing
            # about how far along an item was, so it returns to Ready rather
            # than dragging a refined item back to Captured, and an item still
            # waiting on a title keeps that place in the queue.
            if status == "done":
                sets.append("stage=?"); vals.append("Done")
            elif status == "doing":
                sets.append("stage=?"); vals.append("Working")
            else:
                current = self.get(todo_id)
                if current and current.stage in ("Working", "Done"):
                    sets.append("stage=?"); vals.append("Ready")

        vals.append(todo_id)
        with self._conn() as c:
            c.execute(f"UPDATE todos SET {', '.join(sets)} WHERE id=?", vals)
            row = c.execute("SELECT * FROM todos WHERE id=?", (todo_id,)).fetchone()
        return Todo.from_row(row) if row else None

    def delete(self, todo_id: str) -> bool:
        with self._conn() as c:
            cur = c.execute("DELETE FROM todos WHERE id=?", (todo_id,))
        return cur.rowcount > 0

    # --- reads -----------------------------------------------------------

    def list(self, *, include_done: bool = False) -> list[Todo]:
        q = "SELECT * FROM todos"
        if not include_done:
            q += " WHERE stage != 'Done'"
        # Pipeline order, newest first inside each stage. Blocked floats to the
        # top of its stage because a stalled item is the one worth seeing.
        q += (
            " ORDER BY CASE stage "
            "WHEN 'Working' THEN 0 WHEN 'Ready' THEN 1 WHEN 'Refining' THEN 2 "
            "WHEN 'Captured' THEN 3 ELSE 4 END, blocked DESC, created_at DESC"
        )
        with self._conn() as c:
            return [Todo.from_row(r) for r in c.execute(q).fetchall()]

    def by_stage(self, *, include_done: bool = False) -> dict[str, list[Todo]]:
        """The board, grouped. Every stage is present even when empty — a
        column that vanishes when it empties makes the pipeline unreadable."""
        out: dict[str, list[Todo]] = {s: [] for s in STAGES if include_done or s != "Done"}
        for t in self.list(include_done=include_done):
            out.setdefault(t.stage, []).append(t)
        return out

    def counts(self) -> dict[str, int]:
        with self._conn() as c:
            rows = c.execute("SELECT stage, COUNT(*) n FROM todos GROUP BY stage").fetchall()
        counts = {s: 0 for s in STAGES}
        for r in rows:
            counts[str(r["stage"])] = int(r["n"])
        return counts

    def get(self, todo_id: str) -> Todo | None:
        with self._conn() as c:
            row = c.execute("SELECT * FROM todos WHERE id=?", (todo_id,)).fetchone()
        return Todo.from_row(row) if row else None

    def find(self, q: str, *, include_done: bool = False) -> list[Todo]:
        """Substring match for chat promote-by-phrase.

        Searches `raw` as well as the display text: once an item is refined its
        text is a short title, so matching only on that would stop finding items
        by the words actually spoken when capturing them.
        """
        qn = (q or "").strip().lower()
        if not qn:
            return []
        return [
            t
            for t in self.list(include_done=include_done)
            if qn in f"{t.text} {t.summary} {t.raw} {t.tags} {t.id}".lower()
        ]

    def needing_refinement(self, limit: int = 50) -> list[Todo]:
        """Captures with no drafted title yet — what the refiner picks up."""
        with self._conn() as c:
            rows = c.execute(
                "SELECT * FROM todos WHERE stage = 'Captured' AND refined_at = '' "
                "ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [Todo.from_row(r) for r in rows]
