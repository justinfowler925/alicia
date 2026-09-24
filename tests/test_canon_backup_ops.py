"""Operational Canon backup: real bytes, checksum, restore, and stale alarm."""

from __future__ import annotations

import importlib.util
import os
import time
from pathlib import Path

import pytest

from alicia.canon import CanonStore, WorkItem


def _module():
    path = Path(__file__).resolve().parents[1] / "scripts" / "canon-backup.py"
    spec = importlib.util.spec_from_file_location("canon_backup_ops", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_backup_checksum_and_restore_are_real(tmp_path, monkeypatch):
    live = tmp_path / "state" / "canon.sqlite"
    backups = tmp_path / "backups"
    monkeypatch.setenv("ALICIA_CANON_DB_PATH", str(live))
    monkeypatch.setenv("ALICIA_CANON_BACKUP_DIR", str(backups))
    live.parent.mkdir(parents=True)
    store = CanonStore(live)
    store.save(WorkItem(title="survives restore"))
    store.close()

    ops = _module()
    created = ops.create_backup(retain_days=14)
    verified = ops.verify_latest(max_age_hours=1)

    assert created["ok"] is True
    assert Path(created["path"]).is_file()
    assert Path(created["path"]).with_suffix(".sqlite.sha256").is_file()
    assert verified["work_items"] == 1


def test_stale_and_tampered_backups_fail_closed(tmp_path, monkeypatch):
    live = tmp_path / "state" / "canon.sqlite"
    backups = tmp_path / "backups"
    monkeypatch.setenv("ALICIA_CANON_DB_PATH", str(live))
    monkeypatch.setenv("ALICIA_CANON_BACKUP_DIR", str(backups))
    live.parent.mkdir(parents=True)
    CanonStore(live).close()
    ops = _module()
    created = ops.create_backup()
    path = Path(created["path"])

    old = time.time() - 3 * 3600
    os.utime(path, (old, old))
    with pytest.raises(RuntimeError, match="stale"):
        ops.verify_latest(max_age_hours=1)

    os.utime(path, None)
    path.write_bytes(path.read_bytes() + b"tampered")
    with pytest.raises(RuntimeError, match="checksum"):
        ops.verify_latest(max_age_hours=1)
