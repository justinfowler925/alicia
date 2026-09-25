"""Import what Scout ingested on Studio into Alicia's own ledgers.

Justin, 2026-09-25: nothing is scheduled on the laptop; Scout owns scraping and
ingestion. Scout (fowler-brain ``scripts/scout-routines``) pulls Zoom meeting
summaries, Zoom My Notes, Salesforce Meeting_Notes__c action items and GitHub
facts on Studio and serves them from its private HTTP service. Alicia reads
them whenever it is running — at startup and, throttled, when the board is
loaded — and runs the same ingestion code it always has, so dedupe, ledgers
and cards are unchanged. The same pass hands Scout a daily Canon backup.

No timer lives here: an import happens only because Alicia started or someone
opened the board.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable

from .paths import alicia_home, canon_db_path, state_dir

log = logging.getLogger(__name__)

SCOUT_URL = os.environ.get("ALICIA_SCOUT_URL", "http://100.102.92.119:8973").rstrip("/")
KINDS = ("zoom-summary", "zoom-my-note", "sf-meeting-note", "github-canon")
OWNERS = ["justin"]
JUSTIN_EMAIL = "justin.fowler@clearspeed.com"
IMPORT_EVERY_SECONDS = 300
BACKUP_EVERY_SECONDS = 20 * 3600

_lock = threading.Lock()
_last_attempt = 0.0


def _token() -> str:
    path = Path(os.environ.get("ALICIA_SCOUT_TOKEN_FILE") or state_dir() / "scout-service-token")
    return path.read_text(encoding="utf-8").strip()


def _cursor_path() -> Path:
    return state_dir() / "scout-import.json"


def _load_cursor() -> dict[str, Any]:
    try:
        return json.loads(_cursor_path().read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return {}


def _save_cursor(cursor: dict[str, Any]) -> None:
    path = _cursor_path()
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(cursor, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(tmp, path)


def _request(path: str, *, method: str = "GET", data: bytes | None = None, headers: dict[str, str] | None = None, timeout: float = 60) -> dict[str, Any]:
    req = urllib.request.Request(f"{SCOUT_URL}{path}", data=data, method=method,
                                 headers={"Authorization": f"Bearer {_token()}", **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as res:
        return json.loads(res.read() or b"{}")


# ---- per-kind application (the same code paths the laptop pollers used) ----------

_PLACEHOLDERS = {"<imperative phrase>", "imperative phrase", "null", "n/a"}


def _meeting_note_ledger() -> Path:
    home = alicia_home()
    return home / "zoom_notes_ledger.jsonl"


def _apply_meeting_note(rec: dict[str, Any], todos: Any) -> list[str]:
    """Salesforce Meeting_Notes__c → Inbox notes; ledger-compatible with the old feeder."""
    from importlib import util

    feeder_path = Path(__file__).resolve().parent.parent / "scripts" / "feed_zoom_to_alicia_notes.py"
    spec = util.spec_from_file_location("_zoom_feeder", feeder_path)
    feeder = util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(feeder)
    ledger = _meeting_note_ledger()
    seen = feeder.load_ledger(ledger)
    host = (rec.get("Host_Email__c") or "").lower()
    name = rec.get("Name") or rec["Id"]
    created: list[str] = []
    for it in feeder.items_from_note(rec):
        action = it["action"].strip()
        if not action or action.lower() in _PLACEHOLDERS or (action.startswith("<") and action.endswith(">")):
            continue
        if not (host == JUSTIN_EMAIL or it["owner_email"] == JUSTIN_EMAIL):
            continue
        key = feeder.item_key(rec["Id"], it["idx"], action)
        if key in seen:
            continue
        todo = todos.add(f"{action} — {name} ({rec['Id']})", tags="zoom,auto", lane="Inbox", source="zoom")
        feeder.append_ledger(ledger, {"key": key, "meeting_note_id": rec["Id"], "action": action,
                                      "todo_id": todo.id, "host_email": host, "owner_email": it["owner_email"]})
        seen.add(key)
        created.append(todo.id)
    return created


def _apply(kind: str, payload: dict[str, Any], *, cfg: Any, todos: Any, zoom_store: Any) -> list[str]:
    if kind == "zoom-summary":
        from .zoom_ingest import ingest_assets

        result = ingest_assets(payload, todos, zoom_store, owners=OWNERS)
        return [str(i.get("todo_id")) for i in result.get("items") or [] if i.get("todo_id")]
    if kind == "zoom-my-note":
        from .zoom_my_notes import sync_my_note

        result = sync_my_note(cfg, payload["meta"], payload["content"], todos, zoom_store, owners=OWNERS)
        return [t for t in result.get("todo_ids") or [] if t]
    if kind == "sf-meeting-note":
        return _apply_meeting_note(payload, todos)
    if kind == "github-canon":
        from .canon import CanonStore
        from .github_evidence import GitHubEvidenceReceiver

        store = CanonStore(canon_db_path())
        try:
            GitHubEvidenceReceiver(store).handle(payload["event"], payload["payload"], delivery_id=payload["delivery_id"])
        finally:
            store.close()
        return []
    raise ValueError(f"unknown Scout record kind {kind}")


def _hand_over_backup(cursor: dict[str, Any]) -> dict[str, Any] | None:
    if time.time() - float(cursor.get("canon_backup_at") or 0) < BACKUP_EVERY_SECONDS:
        return None
    from .canon import CanonStore

    with tempfile.TemporaryDirectory(prefix="alicia-canon-backup-") as tmp:
        target = Path(tmp) / "canon.sqlite"
        store = CanonStore(canon_db_path())
        try:
            store.backup(target)
        finally:
            store.close()
        data = target.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    result = _request("/v1/routines/canon-backup", method="PUT", data=data, timeout=300,
                      headers={"Content-Type": "application/octet-stream", "X-SHA256": digest})
    cursor["canon_backup_at"] = time.time()
    return result


def import_once(*, cfg: Any, todos: Any, zoom_store: Any, publish: Callable[[str], None] | None = None) -> dict[str, Any]:
    """One pass over every kind. A failing record stops that kind so it is retried, never skipped."""
    cursor = _load_cursor()
    summary: dict[str, Any] = {"imported": {}, "errors": {}}
    for kind in KINDS:
        after = int(cursor.get(kind) or 0)
        count = 0
        try:
            while True:
                page = _request(f"/v1/routines/records?kind={kind}&after={after}")
                for rec in page.get("records") or []:
                    for todo_id in _apply(kind, rec["payload"], cfg=cfg, todos=todos, zoom_store=zoom_store):
                        if publish:
                            publish(todo_id)
                    after = int(rec["seq"])
                    cursor[kind] = after
                    _save_cursor(cursor)
                    count += 1
                if not page.get("more"):
                    break
        except (urllib.error.URLError, TimeoutError, OSError, ValueError, KeyError) as exc:
            summary["errors"][kind] = str(exc)[:300]
        summary["imported"][kind] = count
    try:
        backup = _hand_over_backup(cursor)
        if backup:
            summary["canon_backup"] = backup
            _save_cursor(cursor)
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        summary["errors"]["canon-backup"] = str(exc)[:300]
    cursor["last_import_at"] = time.time()
    cursor["last_summary"] = summary
    _save_cursor(cursor)
    return summary


def maybe_import_in_background(**kwargs: Any) -> bool:
    """Throttled, non-blocking: at most one pass every five minutes, never two at once."""
    global _last_attempt
    now = time.time()
    if now - _last_attempt < IMPORT_EVERY_SECONDS or not _lock.acquire(blocking=False):
        return False
    _last_attempt = now

    def run() -> None:
        try:
            result = import_once(**kwargs)
            if result["errors"]:
                log.warning("Scout import incomplete: %s", result["errors"])
        except Exception:  # noqa: BLE001 — the board must never fail because Scout is away
            log.exception("Scout import failed")
        finally:
            _lock.release()

    threading.Thread(target=run, name="scout-import", daemon=True).start()
    return True


def status() -> dict[str, Any]:
    cursor = _load_cursor()
    return {"scout_url": SCOUT_URL, "cursor": {k: cursor.get(k, 0) for k in KINDS},
            "last_import_at": cursor.get("last_import_at"), "last_summary": cursor.get("last_summary"),
            "canon_backup_at": cursor.get("canon_backup_at")}
