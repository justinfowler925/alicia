#!/usr/bin/env python3
"""Operate consistent, retained Canon backups and prove they restore."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

from alicia.canon import CanonStore, Evidence, WorkItem
from alicia.paths import canon_db_path


def backup_dir() -> Path:
    return Path(
        os.environ.get("ALICIA_CANON_BACKUP_DIR", "~/.alicia/backups/canon")
    ).expanduser()


def checksum(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def create_backup(*, retain_days: int = 14) -> dict[str, object]:
    source = canon_db_path()
    destination_dir = backup_dir()
    destination_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    destination = destination_dir / f"canon-{stamp}.sqlite"
    store = CanonStore(source)
    try:
        store.backup(destination)
    finally:
        store.close()
    sha = checksum(destination)
    destination.with_suffix(".sqlite.sha256").write_text(
        f"{sha}  {destination.name}\n", encoding="utf-8"
    )
    cutoff = datetime.now(UTC) - timedelta(days=retain_days)
    removed = 0
    for old in destination_dir.glob("canon-*.sqlite"):
        modified = datetime.fromtimestamp(old.stat().st_mtime, UTC)
        if modified < cutoff:
            old.unlink()
            old.with_suffix(".sqlite.sha256").unlink(missing_ok=True)
            removed += 1
    return {"ok": True, "path": str(destination), "sha256": sha, "removed": removed}


def verify_latest(*, max_age_hours: int = 26) -> dict[str, object]:
    candidates = sorted(backup_dir().glob("canon-*.sqlite"), key=lambda p: p.stat().st_mtime)
    if not candidates:
        raise RuntimeError("no Canon backup exists")
    latest = candidates[-1]
    age_hours = (datetime.now(UTC).timestamp() - latest.stat().st_mtime) / 3600
    if age_hours > max_age_hours:
        raise RuntimeError(f"latest Canon backup is stale: {age_hours:.1f}h")
    sidecar = latest.with_suffix(".sqlite.sha256")
    expected = sidecar.read_text(encoding="utf-8").split()[0]
    actual = checksum(latest)
    if not secrets_equal(expected, actual):
        raise RuntimeError("Canon backup checksum mismatch")
    with tempfile.TemporaryDirectory(prefix="alicia-canon-restore-") as tmp:
        restored = CanonStore.restore(latest, Path(tmp) / "restored.sqlite")
        try:
            counts = {
                "work_items": len(restored.list(WorkItem)),
                "evidence": len(restored.list(Evidence)),
            }
        finally:
            restored.close()
    return {"ok": True, "path": str(latest), "age_hours": round(age_hours, 2), **counts}


def secrets_equal(left: str, right: str) -> bool:
    import hmac

    return hmac.compare_digest(left, right)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("backup", "verify"))
    parser.add_argument("--retain-days", type=int, default=14)
    parser.add_argument("--max-age-hours", type=int, default=26)
    args = parser.parse_args()
    result = (
        create_backup(retain_days=args.retain_days)
        if args.command == "backup"
        else verify_latest(max_age_hours=args.max_age_hours)
    )
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
