"""Where Alicia keeps the things it must not lose.

State used to live at `<repo>/state/`, resolved relative to this package. That
made Alicia's memory a property of WHICH CHECKOUT was running it — and the
service runs from a shared repo that other sessions switch branches in. Twice in
one day the daemon ended up serving a different branch than intended; the same
mechanism, one step further, would have handed it an empty notes pad and an
empty conversation history with no error anywhere.

State now lives outside every checkout, at `~/.alicia/state` (override with
`ALICIA_STATE_DIR`). A worktree, a branch switch, a fresh clone — none of them
can change what Alicia remembers.

Migration is automatic and one-way: if the target has no databases and a legacy
`<repo>/state/` does, the files are copied across once. That matters because the
alternative failure — starting clean and looking healthy — is exactly the shape
this module exists to prevent.
"""

from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path

log = logging.getLogger("alicia.paths")

# Everything the daemon must survive a redeploy with.
_STATEFUL = (
    "memory.sqlite",
    "todos.sqlite",
    "sessions.sqlite",
    "avatar_configs.json",
    "canon.sqlite",
)

_LEGACY_DIR = Path(__file__).resolve().parent.parent / "state"

_migrated = False


def alicia_home() -> Path:
    """``~/.alicia``, or the legacy ``~/.brutus`` when only that exists.

    The Brutus -> Alicia rename moves the directory at cutover. Until then a
    machine that still has only ``~/.brutus`` keeps working against it; the
    first resolution logs a migration hint. ``ALICIA_HOME`` overrides both.
    """
    explicit = os.environ.get("ALICIA_HOME", "").strip()
    if explicit:
        return Path(explicit).expanduser()
    home = Path.home()
    new, legacy = home / ".alicia", home / ".brutus"
    if not new.exists() and legacy.is_dir():
        global _hinted
        if not _hinted:
            _hinted = True
            log.warning(
                "using legacy state home %s; migrate with `mv %s %s` "
                "(see the Alicia cutover runbook)", legacy, legacy, new,
            )
        return legacy
    return new


def home_path(tilde: str) -> str:
    """Rewrite a ``~/.alicia/...`` default onto whichever home is live."""
    prefix = "~/.alicia"
    if tilde == prefix or tilde.startswith(prefix + "/"):
        return str(alicia_home()) + tilde[len(prefix):]
    return tilde


_hinted = False

# The databases the daemon serves, and the one checkout allowed to reshape them.
SHARED_STATE_DIR = alicia_home() / "state"
DEPLOYED_APP = alicia_home() / "app"


def default_state_dir() -> Path:
    explicit = os.environ.get("ALICIA_STATE_DIR", "").strip()
    if explicit:
        return Path(explicit).expanduser()
    return SHARED_STATE_DIR


def state_dir() -> Path:
    """The state directory, created if needed, migrated from the repo once."""
    target = default_state_dir()
    target.mkdir(parents=True, exist_ok=True)
    _migrate_once(target)
    return target


def state_path(name: str) -> Path:
    return state_dir() / name


def canon_db_path() -> Path:
    """Single Canon SQLite path for CLI, daemon, webhooks, and tools.

    Override with ALICIA_CANON_DB_PATH. Relative overrides stay cwd-relative
    for tests; the default is always ~/.alicia/state/canon.sqlite so the
    laptop has one store, not one per working directory.
    """
    override = os.environ.get("ALICIA_CANON_DB_PATH", "").strip()
    if override:
        path = Path(override).expanduser()
        return path if path.is_absolute() else path.resolve()
    return state_path("canon.sqlite")


def may_migrate_shared_schema(db: Path) -> bool:
    """May the code running right now ALTER the shape of `db`?

    Only the deployed artifact may. State deliberately outlives every checkout
    (see the module docstring), which means a feature branch run by hand — a
    worktree, a test, a `python -m alicia` from ~/Projects/alicia — reaches the
    same databases the daemon is serving. On 2026-08-08 one did: a branch adding
    a work-queue screen ran its own `_init`, put seven columns into the live
    `todos` table, and deployed main went blind to its own database. 181 ideas
    intact, /api/todos 500, the pad blank, and nothing anywhere said why.

    Adding a column is not additive when someone else is reading the table.
    Branch work gets its own state: `ALICIA_STATE_DIR=/tmp/scratch-state`.
    """
    if os.environ.get("ALICIA_ALLOW_SCHEMA_MIGRATION", "").strip() == "1":
        return True
    try:
        shared = db.resolve().is_relative_to(SHARED_STATE_DIR.resolve())
    except (OSError, ValueError):
        return True
    if not shared:
        return True  # a scratch dir is yours to reshape
    try:
        return Path(__file__).resolve().is_relative_to(DEPLOYED_APP.resolve())
    except (OSError, ValueError):
        return False


def _migrate_once(target: Path) -> None:
    """Copy legacy in-repo state across the first time, and only then.

    Guarded on the TARGET being empty rather than on a marker file: a marker can
    be deleted, and re-copying over live databases would silently roll Alicia's
    memory back to whatever the old checkout happened to hold.
    """
    global _migrated
    if _migrated:
        return
    _migrated = True

    try:
        if not _LEGACY_DIR.is_dir() or _LEGACY_DIR.resolve() == target.resolve():
            return
        if any((target / name).exists() for name in _STATEFUL):
            return  # target already has state — never overwrite it
        moved = []
        for name in _STATEFUL:
            src = _LEGACY_DIR / name
            if src.is_file():
                shutil.copy2(src, target / name)
                moved.append(name)
        if moved:
            log.warning(
                "migrated Alicia state out of the checkout: %s -> %s (%s). "
                "The originals are left in place; delete them once this looks right.",
                _LEGACY_DIR,
                target,
                ", ".join(moved),
            )
    except Exception as exc:  # noqa: BLE001 — never block startup on migration
        log.warning("state migration skipped: %s", exc)
