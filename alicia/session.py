"""A conversation that actually persists, plus the work it captures.

`memory.conversations` stores one `(last_user_message, last_alicia_reply)` pair
per conversation id, overwritten every turn. That is enough to bridge a single
turn into the next CLI invocation and nothing else — the real multi-turn context
has always lived in the browser's own transcript and died with the tab.

A conversation you can walk away from and come back to needs three things this
adds:

  turns      — append-only, both sides, with how each one arrived (voice/text)
  fields     — what the conversation has captured so far, one row per field
  artifacts  — the structured draft a session produces, and its approval state

The `fields` table is deliberately one row per field rather than a JSON blob.
An extractor told to emit "one line per X" into a single column silently kept
one of three answers on a live FNOL call and nobody noticed, because the
transcript looked perfect. Separate rows make a dropped field a missing row.
"""

from __future__ import annotations

from .paths import state_path

import json
import sqlite3
import uuid
from dataclasses import dataclass, field as dc_field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

Channel = Literal["voice", "text"]
Role = Literal["user", "alicia"]

# Deep-lane thinking acks — filler that proves the turn landed, not an answer.
# Must stay in sync with ConversationManager._ACKS / _FIND_ACK.
_THINKING_ACK_TEXTS = frozenset(
    {
        "On it — digging now.",
        "Give me a sec.",
        "Hang on.",
        "One sec — looking.",
        "Yeah, let me check.",
        "Alright, pulling that up.",
        "Gotchu — looking.",
        "Let me dig.",
        "Let me go find that mutha fucka.",
    }
)


def _is_thinking_ack(text: str, meta: dict[str, Any] | None) -> bool:
    if (meta or {}).get("thinking"):
        return True
    return (text or "").strip() in _THINKING_ACK_TEXTS


# Resolved lazily via state_path() so the location is never baked in at import.


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class Turn:
    id: int
    session_id: str
    role: Role
    text: str
    channel: Channel
    at: str
    meta: dict[str, Any] = dc_field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "role": self.role,
            "text": self.text,
            "channel": self.channel,
            "at": self.at,
            "meta": self.meta,
        }


@dataclass
class CapturedField:
    name: str
    value: str
    at: str
    source_turn: int | None = None
    confidence: str = "stated"  # stated | inferred

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "value": self.value,
            "at": self.at,
            "source_turn": self.source_turn,
            "confidence": self.confidence,
        }


class SessionStore:
    """Append-only conversation state. One sqlite file, no ORM, no migrations."""

    def __init__(self, path: Path | str | None = None) -> None:
        self.path = Path(path) if path else state_path("sessions.sqlite")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.path), timeout=10)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL DEFAULT '',
                    kind TEXT NOT NULL DEFAULT 'work',
                    state TEXT NOT NULL DEFAULT 'open',
                    opened_at TEXT NOT NULL,
                    closed_at TEXT
                )"""
            )
            conn.execute(
                """CREATE TABLE IF NOT EXISTS turns (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    text TEXT NOT NULL,
                    channel TEXT NOT NULL DEFAULT 'text',
                    at TEXT NOT NULL,
                    meta TEXT NOT NULL DEFAULT '{}'
                )"""
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_turns_session ON turns(session_id, id)")
            # One row per field. Not a JSON blob — see the module docstring.
            conn.execute(
                """CREATE TABLE IF NOT EXISTS fields (
                    session_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    value TEXT NOT NULL,
                    at TEXT NOT NULL,
                    source_turn INTEGER,
                    confidence TEXT NOT NULL DEFAULT 'stated',
                    PRIMARY KEY (session_id, name)
                )"""
            )
            conn.execute(
                """CREATE TABLE IF NOT EXISTS artifacts (
                    id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    tool TEXT NOT NULL,
                    args TEXT NOT NULL,
                    summary TEXT NOT NULL DEFAULT '',
                    state TEXT NOT NULL DEFAULT 'draft',
                    created_at TEXT NOT NULL,
                    settled_at TEXT,
                    result TEXT
                )"""
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_artifacts_session ON artifacts(session_id)"
            )

    # --- sessions ---------------------------------------------------------

    def open_session(self, *, title: str = "", kind: str = "work", session_id: str = "") -> str:
        sid = session_id or uuid.uuid4().hex[:12]
        with self._connect() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO sessions (id, title, kind, state, opened_at) "
                "VALUES (?, ?, ?, 'open', ?)",
                (sid, title, kind, _now()),
            )
        return sid

    def close_session(self, session_id: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE sessions SET state='closed', closed_at=? WHERE id=?",
                (_now(), session_id),
            )

    def get_session(self, session_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM sessions WHERE id=?", (session_id,)).fetchone()
        return dict(row) if row else None

    def list_sessions(self, *, limit: int = 25, open_only: bool = False) -> list[dict[str, Any]]:
        sql = "SELECT * FROM sessions"
        if open_only:
            sql += " WHERE state='open'"
        sql += " ORDER BY opened_at DESC LIMIT ?"
        with self._connect() as conn:
            return [dict(r) for r in conn.execute(sql, (limit,)).fetchall()]

    # --- turns ------------------------------------------------------------

    def append_turn(
        self,
        session_id: str,
        role: Role,
        text: str,
        *,
        channel: Channel = "text",
        meta: dict[str, Any] | None = None,
    ) -> Turn:
        at = _now()
        with self._connect() as conn:
            cur = conn.execute(
                "INSERT INTO turns (session_id, role, text, channel, at, meta) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (session_id, role, text, channel, at, json.dumps(meta or {})),
            )
            turn_id = int(cur.lastrowid or 0)
        return Turn(turn_id, session_id, role, text, channel, at, meta or {})

    def transcript(self, session_id: str, *, limit: int | None = None) -> list[Turn]:
        sql = "SELECT * FROM turns WHERE session_id=? ORDER BY id"
        args: tuple[Any, ...] = (session_id,)
        with self._connect() as conn:
            rows = conn.execute(sql, args).fetchall()
        turns = [
            Turn(
                r["id"], r["session_id"], r["role"], r["text"], r["channel"], r["at"],
                json.loads(r["meta"] or "{}"),
            )
            for r in rows
        ]
        # Trim from the END — the recent turns are the ones that carry context.
        return turns[-limit:] if limit else turns

    def history_for_model(self, session_id: str, *, keep: int = 8) -> list[dict[str, str]]:
        """The transcript in the {role, content} shape resolve_chat_reply wants.

        Drop deep-lane thinking acks ("Hang on.", "Let me dig.", …). Feeding
        those as assistant history made the model stall and mimic filler.
        Pull extra rows so filtering still yields up to `keep` useful turns.
        """
        # Over-fetch: acks are dense around deep turns.
        pool = self.transcript(session_id, limit=max(keep * 4, keep + 16))
        out: list[dict[str, str]] = []
        for t in pool:
            if t.role == "alicia" and _is_thinking_ack(t.text, t.meta):
                continue
            out.append(
                {"role": "user" if t.role == "user" else "assistant", "content": t.text}
            )
        return out[-keep:]

    # --- captured fields --------------------------------------------------

    def capture_field(
        self,
        session_id: str,
        name: str,
        value: str,
        *,
        source_turn: int | None = None,
        confidence: str = "stated",
    ) -> CapturedField:
        at = _now()
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO fields (session_id, name, value, at, source_turn, confidence) "
                "VALUES (?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(session_id, name) DO UPDATE SET "
                "value=excluded.value, at=excluded.at, source_turn=excluded.source_turn, "
                "confidence=excluded.confidence",
                (session_id, name, value, at, source_turn, confidence),
            )
        return CapturedField(name, value, at, source_turn, confidence)

    def fields(self, session_id: str) -> list[CapturedField]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM fields WHERE session_id=? ORDER BY at, name", (session_id,)
            ).fetchall()
        return [
            CapturedField(r["name"], r["value"], r["at"], r["source_turn"], r["confidence"])
            for r in rows
        ]

    def field_map(self, session_id: str) -> dict[str, str]:
        return {f.name: f.value for f in self.fields(session_id)}

    def missing_fields(self, session_id: str, required: list[str]) -> list[str]:
        """Which required fields are still unanswered, in the caller's order.

        This is the slot tracker, and it lives in code on purpose. Asking a
        model which question to ask next is a routing decision dressed as
        judgement, and it scored 0 of 8 the last time it was tried in prose.
        """
        have = set(self.field_map(session_id))
        return [name for name in required if name not in have]

    # --- artifacts --------------------------------------------------------

    def draft_artifact(
        self,
        session_id: str,
        *,
        kind: str,
        tool: str,
        args: dict[str, Any],
        summary: str = "",
    ) -> dict[str, Any]:
        aid = uuid.uuid4().hex[:12]
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO artifacts (id, session_id, kind, tool, args, summary, state, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, 'draft', ?)",
                (aid, session_id, kind, tool, json.dumps(args, sort_keys=True), summary, _now()),
            )
        return self.get_artifact(aid) or {}

    def get_artifact(self, artifact_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM artifacts WHERE id=?", (artifact_id,)).fetchone()
        if not row:
            return None
        out = dict(row)
        out["args"] = json.loads(out["args"] or "{}")
        out["result"] = json.loads(out["result"]) if out["result"] else None
        return out

    def artifacts(self, session_id: str) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id FROM artifacts WHERE session_id=? ORDER BY created_at", (session_id,)
            ).fetchall()
        return [a for a in (self.get_artifact(r["id"]) for r in rows) if a]

    def settle_artifact(
        self, artifact_id: str, *, state: str, result: dict[str, Any] | None = None
    ) -> dict[str, Any] | None:
        """Mark an artifact approved/rejected/executed. Single-use by design.

        Returns None if it was already settled, so a double-approve — from a
        repeated click or a re-heard "yes" — cannot execute twice.
        """
        with self._connect() as conn:
            cur = conn.execute(
                "UPDATE artifacts SET state=?, settled_at=?, result=? "
                "WHERE id=? AND state='draft'",
                (state, _now(), json.dumps(result) if result is not None else None, artifact_id),
            )
            if cur.rowcount == 0:
                return None
        return self.get_artifact(artifact_id)

    def claim_artifact(self, artifact_id: str) -> dict[str, Any] | None:
        """Atomically reserve one draft before crossing an external write boundary."""
        with self._connect() as conn:
            cur = conn.execute(
                "UPDATE artifacts SET state='executing' WHERE id=? AND state='draft'",
                (artifact_id,),
            )
            if cur.rowcount == 0:
                return None
        return self.get_artifact(artifact_id)

    def finish_artifact(
        self, artifact_id: str, *, state: str, result: dict[str, Any] | None = None
    ) -> dict[str, Any] | None:
        """Settle an artifact previously reserved by :meth:`claim_artifact`."""
        if state not in {"executed", "failed"}:
            raise ValueError("an executing artifact can only finish as executed or failed")
        with self._connect() as conn:
            cur = conn.execute(
                "UPDATE artifacts SET state=?, settled_at=?, result=? "
                "WHERE id=? AND state='executing'",
                (state, _now(), json.dumps(result) if result is not None else None, artifact_id),
            )
            if cur.rowcount == 0:
                return None
        return self.get_artifact(artifact_id)

    # --- the whole board, for the screen ----------------------------------

    def snapshot(self, session_id: str) -> dict[str, Any]:
        """Everything the screen needs for one session, in one read."""
        session = self.get_session(session_id)
        if not session:
            return {}
        return {
            "session": session,
            "turns": [t.as_dict() for t in self.transcript(session_id)],
            "fields": [f.as_dict() for f in self.fields(session_id)],
            "artifacts": self.artifacts(session_id),
        }
