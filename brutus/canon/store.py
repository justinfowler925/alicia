"""Minimal SQLite-backed persistence for the canonical objects.

No ORM — this is intentionally small. One JSON blob column per row keeps
the pydantic models as the object schema source of truth, while ``id`` stays a
real indexed column for lookups. Storage changes are applied from the numbered
SQL files in :mod:`brutus.canon.migrations`.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Optional, Type, TypeVar

from pydantic import BaseModel

from .identity import (
    AuthenticatedPrincipal,
    DEFAULT_IDENTITY_REGISTRY,
    IdentityRegistry,
    require_owner,
    require_verifier,
)
from .migrations import migration_files
from .models import (
    Approval,
    ApprovalStatus,
    Decision,
    Evidence,
    ExecutionCard,
    ExecutionCardStatus,
    InboxItem,
    InboxStatus,
    Project,
    Run,
    Watch,
    WorkItem,
)

T = TypeVar("T", bound=BaseModel)

_TABLES = {
    "inbox_items": InboxItem,
    "projects": Project,
    "work_items": WorkItem,
    "decisions": Decision,
    "evidence": Evidence,
    "runs": Run,
    "approvals": Approval,
    "execution_cards": ExecutionCard,
    "watches": Watch,
}

_BUSY_TIMEOUT_MS = 5_000


class CanonStore:
    """A tiny, dependency-free store for the 8 canonical objects.

    Every file-backed store enables SQLite WAL mode and a five-second busy
    timeout. WAL allows readers to proceed while one writer commits; SQLite
    still permits only one writer at a time, so sustained high-volume
    multi-writer workloads should move to Postgres rather than increasing
    retries indefinitely.

    Back up this system-of-record database with :meth:`backup`, not a raw file
    copy: the SQLite online backup API captures a consistent snapshot while
    writes continue. Take a backup after significant approvals/evidence writes
    and at least daily (or more often to meet the desired recovery-point
    objective), retain it separately from the live database, and verify
    restores periodically with :meth:`restore`.
    """

    def __init__(
        self,
        db_path: str | Path = ":memory:",
        *,
        identity_registry: IdentityRegistry = DEFAULT_IDENTITY_REGISTRY,
    ):
        self.db_path = str(db_path)
        self.identity_registry = identity_registry
        self._conn = sqlite3.connect(self.db_path, timeout=_BUSY_TIMEOUT_MS / 1_000)
        self._conn.row_factory = sqlite3.Row
        self._configure_connection()
        self._run_migrations()

    def _configure_connection(self) -> None:
        """Apply the connection settings required for cooperative writers."""
        self._conn.execute(f"PRAGMA busy_timeout = {_BUSY_TIMEOUT_MS}")
        self._conn.execute("PRAGMA journal_mode=WAL")

    def _run_migrations(self) -> None:
        """Apply each unapplied SQL migration atomically and exactly once."""
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        self._conn.commit()

        for version, name, path in migration_files():
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                applied = self._conn.execute(
                    "SELECT 1 FROM schema_migrations WHERE version = ?",
                    (version,),
                ).fetchone()
                if applied is None:
                    self._execute_migration(path)
                    self._conn.execute(
                        "INSERT INTO schema_migrations (version, name) VALUES (?, ?)",
                        (version, name),
                    )
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise

    def _execute_migration(self, path: Path) -> None:
        """Execute the simple semicolon-delimited DDL statements in ``path``."""
        for statement in path.read_text(encoding="utf-8").split(";"):
            if statement.strip():
                self._conn.execute(statement)

    def close(self) -> None:
        self._conn.close()

    def backup(self, dest_path: str | Path) -> Path:
        """Create a consistent SQLite backup and return its destination path.

        ``dest_path`` must differ from the live database path. The online
        backup API is safe to invoke while other CanonStore instances write.
        See the class docstring for the required backup cadence and restore
        verification guidance.
        """
        destination = Path(dest_path)
        if self.db_path != ":memory:" and destination.resolve() == Path(self.db_path).resolve():
            raise ValueError("backup destination must differ from the live database")
        destination.parent.mkdir(parents=True, exist_ok=True)

        backup_connection = sqlite3.connect(str(destination))
        try:
            self._conn.backup(backup_connection)
        finally:
            backup_connection.close()
        return destination

    @classmethod
    def restore(
        cls,
        source_path: str | Path,
        dest_path: str | Path,
        *,
        identity_registry: IdentityRegistry = DEFAULT_IDENTITY_REGISTRY,
    ) -> CanonStore:
        """Restore a backup into ``dest_path`` and return the ready-to-use store.

        The restored database is upgraded through any migrations that were
        added after the backup was taken. ``source_path`` and ``dest_path``
        must be different files.
        """
        source = Path(source_path)
        if not source.is_file():
            raise FileNotFoundError(source)

        destination = Path(dest_path)
        if destination.resolve() == source.resolve():
            raise ValueError("restore destination must differ from the backup source")
        destination.parent.mkdir(parents=True, exist_ok=True)

        restored = cls(destination, identity_registry=identity_registry)
        source_connection = sqlite3.connect(str(source))
        try:
            source_connection.backup(restored._conn)
        finally:
            source_connection.close()

        restored._configure_connection()
        restored._run_migrations()
        return restored

    # -- generic CRUD -------------------------------------------------

    def save(
        self,
        obj: BaseModel,
        *,
        authenticated_principal: AuthenticatedPrincipal | None = None,
    ) -> None:
        """Persist an object after authorizing any asserted approval/verification.

        Ordinary object writes stay principal-free. A write that records an
        approver or verifier must carry the matching registry-issued principal;
        storing a bare email/name is never treated as proof of that actor.

        Work Item object references are append-only indexes. Before saving a
        Work Item, merge each index with the latest persisted copy so a caller
        holding an older object cannot erase dispatcher- or webhook-added
        evidence, approval, or decision references.
        """
        self._validate_identity_fields(obj, authenticated_principal)
        if isinstance(obj, ExecutionCard):
            self._reject_sealed_execution_card_mutation(obj)
        if isinstance(obj, WorkItem):
            self._merge_work_item_refs(obj)
        table = self._table_for(type(obj))
        payload = obj.model_dump_json()
        self._conn.execute(
            f"INSERT INTO {table} (id, data) VALUES (?, ?) "
            "ON CONFLICT(id) DO UPDATE SET data=excluded.data",
            (obj.id, payload),
        )
        self._conn.commit()
        # Watch evaluation is deliberately a post-persistence seam rather than
        # a hidden state-machine side effect. This keeps REV-513 transition
        # validation pure while every normal WorkItem save observes the current
        # state and lets watches update their own durable idempotency marker.
        if isinstance(obj, WorkItem):
            from .watches import evaluate_watches

            evaluate_watches(self, obj)

    def _reject_sealed_execution_card_mutation(self, card: ExecutionCard) -> None:
        """A sealed Execution Card is append-only except for revoke."""
        existing = self.get(ExecutionCard, card.id)
        if existing is None or existing.status != ExecutionCardStatus.SEALED:
            return
        if card.status == ExecutionCardStatus.REVOKED:
            kept = existing.model_dump()
            kept["status"] = ExecutionCardStatus.REVOKED.value
            incoming = card.model_dump()
            incoming["status"] = ExecutionCardStatus.REVOKED.value
            if kept != incoming:
                raise ValueError("sealed execution card is immutable")
            return
        raise ValueError("sealed execution card is immutable")

    def _merge_work_item_refs(self, work_item: WorkItem) -> None:
        """Preserve append-only Work Item indexes across stale-object saves."""
        persisted = self.get(WorkItem, work_item.id)
        if persisted is None:
            return
        for field_name in (
            "evidence_refs",
            "approval_refs",
            "decision_refs",
            "feedback_refs",
        ):
            latest_refs = getattr(persisted, field_name)
            supplied_refs = getattr(work_item, field_name)
            setattr(work_item, field_name, list(dict.fromkeys([*latest_refs, *supplied_refs])))
        for key, value in persisted.bindings.items():
            supplied = work_item.bindings.get(key)
            if supplied and supplied != value:
                raise ValueError(f"work item binding '{key}' is immutable")
            work_item.bindings.setdefault(key, value)

    def _validate_identity_fields(
        self,
        obj: BaseModel,
        authenticated_principal: AuthenticatedPrincipal | None,
    ) -> None:
        if isinstance(obj, Approval):
            if obj.status == ApprovalStatus.GRANTED and not obj.approved_by:
                raise ValueError("a granted Approval requires approved_by")
            if obj.approved_by:
                require_owner(
                    obj.approved_by,
                    authenticated_principal,
                    registry=self.identity_registry,
                )

        if isinstance(obj, Evidence):
            if obj.verified and not obj.verified_by:
                raise ValueError("verified Evidence requires verified_by")
            if obj.verified_by:
                require_verifier(
                    obj.verified_by,
                    authenticated_principal,
                    registry=self.identity_registry,
                )

    def get(self, model_cls: Type[T], obj_id: str) -> Optional[T]:
        table = self._table_for(model_cls)
        row = self._conn.execute(f"SELECT data FROM {table} WHERE id = ?", (obj_id,)).fetchone()
        if row is None:
            return None
        return model_cls.model_validate_json(row["data"])

    def list(self, model_cls: Type[T]) -> list[T]:
        table = self._table_for(model_cls)
        rows = self._conn.execute(f"SELECT data FROM {table}").fetchall()
        return [model_cls.model_validate_json(r["data"]) for r in rows]

    def delete(self, model_cls: Type[T], obj_id: str) -> None:
        table = self._table_for(model_cls)
        self._conn.execute(f"DELETE FROM {table} WHERE id = ?", (obj_id,))
        self._conn.commit()

    @staticmethod
    def _table_for(model_cls: Type[BaseModel]) -> str:
        for table, cls in _TABLES.items():
            if cls is model_cls:
                return table
        raise ValueError(f"no table registered for {model_cls!r}")

    # -- domain rule: inbox promotion ---------------------------------

    def promote_inbox_item(self, inbox_item: InboxItem, *, reviewed_by: str, work_item: WorkItem) -> WorkItem:
        """Promote an InboxItem to a WorkItem.

        Enforces the spec rule: "cannot auto-promote to Work Item or
        Project. Promotion requires an explicit human review action
        (actor recorded)." `reviewed_by` is required and recorded; there
        is no code path that promotes without it.
        """
        if not reviewed_by:
            raise ValueError("promotion requires an explicit human reviewer (actor recorded)")
        inbox_item.status = InboxStatus.PROMOTED
        work_item.origin = inbox_item.id
        self.save(inbox_item)
        self.save(work_item)
        return work_item
