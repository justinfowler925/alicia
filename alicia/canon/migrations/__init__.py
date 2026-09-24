"""Versioned SQLite migrations for :mod:`alicia.canon`.

Add one ordered ``NNNN_description.sql`` file for each schema change. The
store discovers the files at runtime and records their versions in
``schema_migrations`` after a successful transaction; do not edit a migration
that may already have been applied.
"""

from __future__ import annotations

import re
from pathlib import Path

MIGRATIONS_DIR = Path(__file__).parent
_MIGRATION_NAME = re.compile(r"^(?P<version>\d{4,})_(?P<name>[a-z0-9_]+)\.sql$")


def migration_files() -> list[tuple[int, str, Path]]:
    """Return versioned migration files in application order."""
    migrations: list[tuple[int, str, Path]] = []
    for path in MIGRATIONS_DIR.glob("*.sql"):
        match = _MIGRATION_NAME.fullmatch(path.name)
        if match is None:
            continue
        migrations.append((int(match["version"]), path.name, path))

    migrations.sort()
    versions = [version for version, _, _ in migrations]
    if len(versions) != len(set(versions)):
        raise RuntimeError("Canon migration versions must be unique")
    return migrations


LATEST_SCHEMA_VERSION = max((version for version, _, _ in migration_files()), default=0)
