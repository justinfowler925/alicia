"""Persistent laptop memory for Alicia conversations and working set.

The Studio ledger owns executing work. This module owns the conversation layer
and the user's working set so Alicia can resume context across sessions.
"""

from __future__ import annotations

import sqlite3
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .paths import state_path


def _now() -> str:
    return datetime.now(UTC).isoformat()


@dataclass
class Conversation:
    id: str
    title: str
    summary: str
    last_user_message: str
    last_alicia_reply: str
    linked_tickets: list[str] = field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "summary": self.summary,
            "last_user_message": self.last_user_message,
            "last_alicia_reply": self.last_alicia_reply,
            "linked_tickets": self.linked_tickets,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


@dataclass
class WorkingNote:
    id: str
    topic: str
    body: str
    ticket_ids: list[str] = field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "topic": self.topic,
            "body": self.body,
            "ticket_ids": self.ticket_ids,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


@dataclass
class Lesson:
    """Local lesson draft — laptop memory only; never auto-publishes."""

    id: str
    title: str
    body: str
    tags: str = ""
    source: str = "chat"
    created_at: str = ""
    updated_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "body": self.body,
            "tags": self.tags,
            "source": self.source,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


class MemoryStore:
    def __init__(self, path: Path | str | None = None) -> None:
        self.path = Path(path) if path else state_path("memory.sqlite")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.path), timeout=10)
        conn.row_factory = sqlite3.Row
        return conn

    def _init(self) -> None:
        with self._conn() as c:
            c.execute(
                """CREATE TABLE IF NOT EXISTS conversations (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL DEFAULT '',
                    summary TEXT NOT NULL DEFAULT '',
                    last_user_message TEXT NOT NULL DEFAULT '',
                    last_alicia_reply TEXT NOT NULL DEFAULT '',
                    linked_tickets TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )"""
            )
            c.execute(
                """CREATE TABLE IF NOT EXISTS working_notes (
                    id TEXT PRIMARY KEY,
                    topic TEXT NOT NULL DEFAULT '',
                    body TEXT NOT NULL DEFAULT '',
                    ticket_ids TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )"""
            )
            c.execute(
                """CREATE TABLE IF NOT EXISTS agent_pins (
                    id TEXT PRIMARY KEY,
                    pinned INTEGER NOT NULL DEFAULT 0,
                    snooze_until TEXT NOT NULL DEFAULT '',
                    archived INTEGER NOT NULL DEFAULT 0,
                    labels TEXT NOT NULL DEFAULT '',
                    linked_rev TEXT NOT NULL DEFAULT '',
                    notes TEXT NOT NULL DEFAULT '',
                    updated_at TEXT NOT NULL
                )"""
            )
            c.execute(
                """CREATE TABLE IF NOT EXISTS project_overlays (
                    id TEXT PRIMARY KEY,
                    pinned INTEGER NOT NULL DEFAULT 0,
                    archived INTEGER NOT NULL DEFAULT 0,
                    objective TEXT NOT NULL DEFAULT '',
                    notes TEXT NOT NULL DEFAULT '',
                    updated_at TEXT NOT NULL
                )"""
            )
            c.execute(
                """CREATE TABLE IF NOT EXISTS lessons (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL DEFAULT '',
                    body TEXT NOT NULL DEFAULT '',
                    tags TEXT NOT NULL DEFAULT '',
                    source TEXT NOT NULL DEFAULT 'chat',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )"""
            )

    def save_conversation(
        self,
        user_message: str,
        alicia_reply: str,
        *,
        conversation_id: str = "",
        title: str = "",
        summary: str = "",
        linked_tickets: list[str] | None = None,
    ) -> Conversation:
        linked = linked_tickets or []
        linked_str = ",".join(linked)
        cid = conversation_id or uuid.uuid4().hex[:12]
        now = _now()

        with self._conn() as c:
            existing = c.execute(
                "SELECT created_at FROM conversations WHERE id=?", (cid,)
            ).fetchone()
            if existing:
                c.execute(
                    "UPDATE conversations SET title=?, summary=?, last_user_message=?, "
                    "last_alicia_reply=?, linked_tickets=?, updated_at=? WHERE id=?",
                    (title, summary, user_message, alicia_reply, linked_str, now, cid),
                )
            else:
                c.execute(
                    "INSERT INTO conversations (id, title, summary, last_user_message, "
                    "last_alicia_reply, linked_tickets, created_at, updated_at) "
                    "VALUES (?,?,?,?,?,?,?,?)",
                    (cid, title, summary, user_message, alicia_reply, linked_str, now, now),
                )
            row = c.execute("SELECT * FROM conversations WHERE id=?", (cid,)).fetchone()
        return self._row_to_conversation(row)

    def get_conversation(self, conversation_id: str) -> Conversation | None:
        with self._conn() as c:
            row = c.execute("SELECT * FROM conversations WHERE id=?", (conversation_id,)).fetchone()
        return self._row_to_conversation(row) if row else None

    def list_conversations(self, *, limit: int = 20) -> list[Conversation]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT * FROM conversations ORDER BY updated_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [self._row_to_conversation(r) for r in rows]

    def default_history(self) -> list[dict[str, str]]:
        """Last conversation as chat turns — used when CLI/MCP send no history.

        Stores only the most recent user/assistant pair per conversation row, so
        this is a continuity bridge, not a full transcript replay.
        """
        convs = self.list_conversations(limit=1)
        if not convs:
            return []
        c = convs[0]
        out: list[dict[str, str]] = []
        if c.last_user_message.strip():
            out.append({"role": "user", "content": c.last_user_message.strip()})
        if c.last_alicia_reply.strip():
            out.append({"role": "assistant", "content": c.last_alicia_reply.strip()})
        return out

    def _row_to_conversation(self, row: sqlite3.Row) -> Conversation:
        return Conversation(
            id=row["id"],
            title=row["title"] or "",
            summary=row["summary"] or "",
            last_user_message=row["last_user_message"] or "",
            last_alicia_reply=row["last_alicia_reply"] or "",
            linked_tickets=[t for t in (row["linked_tickets"] or "").split(",") if t],
            created_at=row["created_at"] or "",
            updated_at=row["updated_at"] or "",
        )

    def add_working_note(
        self,
        topic: str,
        body: str,
        *,
        ticket_ids: list[str] | None = None,
        note_id: str = "",
    ) -> WorkingNote:
        tickets = ticket_ids or []
        now = _now()
        nid = note_id or uuid.uuid4().hex[:12]
        with self._conn() as c:
            c.execute(
                "INSERT INTO working_notes (id, topic, body, ticket_ids, created_at, updated_at) "
                "VALUES (?,?,?,?,?,?)",
                (nid, topic, body, ",".join(tickets), now, now),
            )
            row = c.execute("SELECT * FROM working_notes WHERE id=?", (nid,)).fetchone()
        return self._row_to_note(row)

    def list_working_notes(self, *, limit: int = 50) -> list[WorkingNote]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT * FROM working_notes ORDER BY updated_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [self._row_to_note(r) for r in rows]

    def search_working_notes(self, q: str, *, limit: int = 20) -> list[WorkingNote]:
        qn = (q or "").strip().lower()
        if not qn:
            return self.list_working_notes(limit=limit)
        hits = [
            n
            for n in self.list_working_notes(limit=max(limit * 5, 50))
            if qn in f"{n.topic} {n.body} {' '.join(n.ticket_ids)}".lower()
        ]
        return hits[:limit]

    def _row_to_note(self, row: sqlite3.Row) -> WorkingNote:
        return WorkingNote(
            id=row["id"],
            topic=row["topic"] or "",
            body=row["body"] or "",
            ticket_ids=[t for t in (row["ticket_ids"] or "").split(",") if t],
            created_at=row["created_at"] or "",
            updated_at=row["updated_at"] or "",
        )

    def add_lesson(
        self,
        title: str,
        body: str,
        *,
        tags: str = "",
        source: str = "chat",
        lesson_id: str = "",
    ) -> Lesson:
        title = (title or "").strip()[:200]
        body = (body or "").strip()[:4000]
        if not title and not body:
            raise ValueError("lesson title or body required")
        if not title:
            title = body[:80]
        now = _now()
        lid = lesson_id or uuid.uuid4().hex[:12]
        with self._conn() as c:
            c.execute(
                "INSERT INTO lessons (id, title, body, tags, source, created_at, updated_at) "
                "VALUES (?,?,?,?,?,?,?)",
                (lid, title, body, (tags or "").strip()[:200], source or "chat", now, now),
            )
            row = c.execute("SELECT * FROM lessons WHERE id=?", (lid,)).fetchone()
        return self._row_to_lesson(row)

    def list_lessons(self, *, limit: int = 30) -> list[Lesson]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT * FROM lessons ORDER BY updated_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [self._row_to_lesson(r) for r in rows]

    def search_lessons(self, q: str, *, limit: int = 20) -> list[Lesson]:
        qn = (q or "").strip().lower()
        if not qn:
            return self.list_lessons(limit=limit)
        hits = [
            les
            for les in self.list_lessons(limit=max(limit * 5, 50))
            if qn in f"{les.title} {les.body} {les.tags}".lower()
        ]
        return hits[:limit]

    def _row_to_lesson(self, row: sqlite3.Row) -> Lesson:
        return Lesson(
            id=row["id"],
            title=row["title"] or "",
            body=row["body"] or "",
            tags=row["tags"] or "",
            source=row["source"] or "chat",
            created_at=row["created_at"] or "",
            updated_at=row["updated_at"] or "",
        )

    def list_agent_overlays(self) -> dict[str, dict[str, Any]]:
        """All pin/snooze/archive overlays keyed by agent id (cursor:… / claude:…)."""
        with self._conn() as c:
            rows = c.execute("SELECT * FROM agent_pins").fetchall()
        out: dict[str, dict[str, Any]] = {}
        for r in rows:
            out[r["id"]] = {
                "id": r["id"],
                "pinned": bool(r["pinned"]),
                "snooze_until": r["snooze_until"] or "",
                "archived": bool(r["archived"]),
                "labels": r["labels"] or "",
                "linked_rev": r["linked_rev"] or "",
                "notes": r["notes"] or "",
                "updated_at": r["updated_at"] or "",
            }
        return out

    def get_agent_overlay(self, agent_id: str) -> dict[str, Any] | None:
        with self._conn() as c:
            row = c.execute("SELECT * FROM agent_pins WHERE id=?", (agent_id,)).fetchone()
        if not row:
            return None
        return {
            "id": row["id"],
            "pinned": bool(row["pinned"]),
            "snooze_until": row["snooze_until"] or "",
            "archived": bool(row["archived"]),
            "labels": row["labels"] or "",
            "linked_rev": row["linked_rev"] or "",
            "notes": row["notes"] or "",
            "updated_at": row["updated_at"] or "",
        }

    def upsert_agent_overlay(
        self,
        agent_id: str,
        *,
        pinned: bool | None = None,
        snooze_until: str | None = None,
        archived: bool | None = None,
        labels: str | None = None,
        linked_rev: str | None = None,
        notes: str | None = None,
    ) -> dict[str, Any]:
        agent_id = (agent_id or "").strip()
        if not agent_id:
            raise ValueError("agent id required")
        now = _now()
        with self._conn() as c:
            existing = c.execute("SELECT * FROM agent_pins WHERE id=?", (agent_id,)).fetchone()
            if existing:
                cur = {
                    "pinned": bool(existing["pinned"]),
                    "snooze_until": existing["snooze_until"] or "",
                    "archived": bool(existing["archived"]),
                    "labels": existing["labels"] or "",
                    "linked_rev": existing["linked_rev"] or "",
                    "notes": existing["notes"] or "",
                }
            else:
                cur = {
                    "pinned": False,
                    "snooze_until": "",
                    "archived": False,
                    "labels": "",
                    "linked_rev": "",
                    "notes": "",
                }
            if pinned is not None:
                cur["pinned"] = bool(pinned)
            if snooze_until is not None:
                cur["snooze_until"] = str(snooze_until)
            if archived is not None:
                cur["archived"] = bool(archived)
            if labels is not None:
                cur["labels"] = str(labels)
            if linked_rev is not None:
                cur["linked_rev"] = str(linked_rev)
            if notes is not None:
                cur["notes"] = str(notes)
            c.execute(
                "INSERT INTO agent_pins (id, pinned, snooze_until, archived, labels, "
                "linked_rev, notes, updated_at) VALUES (?,?,?,?,?,?,?,?) "
                "ON CONFLICT(id) DO UPDATE SET pinned=excluded.pinned, "
                "snooze_until=excluded.snooze_until, archived=excluded.archived, "
                "labels=excluded.labels, linked_rev=excluded.linked_rev, "
                "notes=excluded.notes, updated_at=excluded.updated_at",
                (
                    agent_id,
                    int(cur["pinned"]),
                    cur["snooze_until"],
                    int(cur["archived"]),
                    cur["labels"],
                    cur["linked_rev"],
                    cur["notes"],
                    now,
                ),
            )
        return {**cur, "id": agent_id, "updated_at": now}

    def list_project_overlays(self) -> dict[str, dict[str, Any]]:
        """Local portfolio organization keyed by provider-stable project id."""
        with self._conn() as c:
            rows = c.execute("SELECT * FROM project_overlays").fetchall()
        return {
            str(row["id"]): {
                "id": str(row["id"]),
                "pinned": bool(row["pinned"]),
                "archived": bool(row["archived"]),
                "objective": str(row["objective"] or ""),
                "notes": str(row["notes"] or ""),
                "updated_at": str(row["updated_at"] or ""),
            }
            for row in rows
        }

    def upsert_project_overlay(
        self,
        project_id: str,
        *,
        pinned: bool | None = None,
        archived: bool | None = None,
        objective: str | None = None,
        notes: str | None = None,
    ) -> dict[str, Any]:
        project_id = (project_id or "").strip()
        if not project_id:
            raise ValueError("project id required")
        now = _now()
        with self._conn() as c:
            row = c.execute("SELECT * FROM project_overlays WHERE id=?", (project_id,)).fetchone()
            current = {
                "pinned": bool(row["pinned"]) if row else False,
                "archived": bool(row["archived"]) if row else False,
                "objective": str(row["objective"] or "") if row else "",
                "notes": str(row["notes"] or "") if row else "",
            }
            if pinned is not None:
                current["pinned"] = bool(pinned)
            if archived is not None:
                current["archived"] = bool(archived)
            if objective is not None:
                current["objective"] = str(objective).strip()[:1000]
            if notes is not None:
                current["notes"] = str(notes).strip()[:4000]
            c.execute(
                """INSERT INTO project_overlays
                   (id, pinned, archived, objective, notes, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT(id) DO UPDATE SET
                     pinned=excluded.pinned,
                     archived=excluded.archived,
                     objective=excluded.objective,
                     notes=excluded.notes,
                     updated_at=excluded.updated_at""",
                (
                    project_id,
                    int(current["pinned"]),
                    int(current["archived"]),
                    current["objective"],
                    current["notes"],
                    now,
                ),
            )
        return {**current, "id": project_id, "updated_at": now}
