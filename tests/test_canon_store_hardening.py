"""REV-520 durability and concurrent-writer tests for CanonStore."""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

from alicia.canon import CanonStore, WorkItem
from alicia.canon.migrations import migration_files


def _migration_rows(db_path: Path) -> list[tuple[int, str]]:
    connection = sqlite3.connect(db_path)
    try:
        return connection.execute(
            "SELECT version, name FROM schema_migrations ORDER BY version"
        ).fetchall()
    finally:
        connection.close()


def _expected_migration_rows() -> list[tuple[int, str]]:
    return [(version, name) for version, name, _ in migration_files()]


def test_migrations_apply_to_fresh_database_and_are_noop_when_reopened(tmp_path: Path) -> None:
    db_path = tmp_path / "canon.sqlite"

    first_store = CanonStore(db_path)
    applied_on_first_open = _migration_rows(db_path)
    assert applied_on_first_open == _expected_migration_rows()
    first_store.close()

    reopened_store = CanonStore(db_path)
    assert _migration_rows(db_path) == applied_on_first_open
    reopened_store.close()


def test_migration_runner_preserves_populated_pre_migration_database(tmp_path: Path) -> None:
    db_path = tmp_path / "legacy-canon.sqlite"
    work_item = WorkItem(id="legacy-work-item", title="Already persisted")

    legacy_connection = sqlite3.connect(db_path)
    try:
        legacy_connection.execute(
            "CREATE TABLE work_items (id TEXT PRIMARY KEY, data TEXT NOT NULL)"
        )
        legacy_connection.execute(
            "INSERT INTO work_items (id, data) VALUES (?, ?)",
            (work_item.id, work_item.model_dump_json()),
        )
        legacy_connection.commit()
    finally:
        legacy_connection.close()

    store = CanonStore(db_path)
    restored_work_item = store.get(WorkItem, work_item.id)
    assert restored_work_item is not None
    assert restored_work_item.title == "Already persisted"
    assert _migration_rows(db_path) == _expected_migration_rows()
    store.close()


def test_backup_and_restore_round_trip_data(tmp_path: Path) -> None:
    live_path = tmp_path / "live.sqlite"
    backup_path = tmp_path / "backups" / "canon.sqlite"
    restore_path = tmp_path / "restored.sqlite"
    work_item = WorkItem(id="backup-work-item", title="Protect this evidence")

    live_store = CanonStore(live_path)
    live_store.save(work_item)
    assert live_store.backup(backup_path) == backup_path
    live_store.close()

    restored_store = CanonStore.restore(backup_path, restore_path)
    restored_work_item = restored_store.get(WorkItem, work_item.id)
    assert restored_work_item is not None
    assert restored_work_item.model_dump() == work_item.model_dump()
    restored_store.close()


def test_concurrent_writers_use_wal_without_corrupting_data(tmp_path: Path) -> None:
    db_path = tmp_path / "concurrent.sqlite"
    bootstrap_store = CanonStore(db_path)
    bootstrap_store.close()

    writer_count = 2
    writes_per_writer = 30
    start_writing = threading.Barrier(writer_count + 1)
    failures: list[Exception] = []

    def write_work_items(prefix: str) -> None:
        store = CanonStore(db_path)
        try:
            start_writing.wait()
            for index in range(writes_per_writer):
                store.save(WorkItem(id=f"{prefix}-{index}", title=f"{prefix} write {index}"))
        except (sqlite3.Error, RuntimeError) as error:  # pragma: no cover - asserted in the main thread
            failures.append(error)
        finally:
            store.close()

    writers = [
        threading.Thread(target=write_work_items, args=(f"writer-{index}",))
        for index in range(writer_count)
    ]
    for writer in writers:
        writer.start()
    start_writing.wait()
    for writer in writers:
        writer.join()

    assert not failures

    verification_store = CanonStore(db_path)
    try:
        assert verification_store._conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert verification_store._conn.execute("PRAGMA busy_timeout").fetchone()[0] == 5_000
        saved_ids = {item.id for item in verification_store.list(WorkItem)}
        expected_ids = {
            f"writer-{writer_index}-{item_index}"
            for writer_index in range(writer_count)
            for item_index in range(writes_per_writer)
        }
        assert saved_ids == expected_ids
    finally:
        verification_store.close()
