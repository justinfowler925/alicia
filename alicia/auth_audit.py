"""Append-only auth audit log Justin can read via /api/auth/audit."""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any

from .paths import state_path

_LOCK = threading.Lock()
AUDIT_FILE = "auth-audit.jsonl"
DEFAULT_MAX_LINES = 500


def audit_path() -> Path:
    override = os.environ.get("ALICIA_AUTH_AUDIT", "").strip()
    return Path(override) if override else state_path(AUDIT_FILE)


def record(
    event: str,
    *,
    ok: bool,
    detail: str = "",
    via: str = "",
    path: str = "",
    login: str = "",
    peer: str = "",
    extra: dict[str, Any] | None = None,
) -> None:
    """Best-effort append. Never raises into the request path."""

    row: dict[str, Any] = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "event": event,
        "ok": bool(ok),
    }
    if detail:
        row["detail"] = detail[:240]
    if via:
        row["via"] = via
    if path:
        row["path"] = path[:200]
    if login:
        row["login"] = login[:120]
    if peer:
        row["peer"] = peer[:120]
    if extra:
        for key, value in extra.items():
            if key in row:
                continue
            row[key] = value
    try:
        target = audit_path()
        target.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(row, separators=(",", ":"), sort_keys=True) + "\n"
        with _LOCK:
            with target.open("a", encoding="utf-8") as handle:
                handle.write(line)
            try:
                os.chmod(target, 0o600)
            except OSError:
                pass
    except OSError:
        return


def read_recent(*, limit: int = 100) -> list[dict[str, Any]]:
    limit = max(1, min(int(limit or 100), DEFAULT_MAX_LINES))
    target = audit_path()
    try:
        text = target.read_text(encoding="utf-8")
    except FileNotFoundError:
        return []
    except OSError:
        return []
    rows: list[dict[str, Any]] = []
    for line in text.splitlines()[-limit:]:
        line = line.strip()
        if not line:
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict):
            rows.append(item)
    return rows
