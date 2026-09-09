"""Read-only scanner for Codex, Cursor, and Claude agent threads on this laptop.

The Work tab is Atlas. This module is the cockpit for every Cursor Agent and
Claude Code session living on the MacBook — titles, cwd, recency, live pid.
Never mutates transcripts or kills processes.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import subprocess
import time
from contextlib import closing
from pathlib import Path
from typing import Any

from .paths import state_path

_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.I,
)
_USER_QUERY_RE = re.compile(r"<user_query>\s*(.*?)\s*</user_query>", re.S | re.I)

CURSOR_PROJECTS = Path.home() / ".cursor" / "projects"
CLAUDE_PROJECTS = Path.home() / ".claude" / "projects"
CLAUDE_SESSIONS = Path.home() / ".claude" / "sessions"
CODEX_ROOT = Path.home() / ".codex"
CODEX_DB = CODEX_ROOT / "sqlite" / "codex-dev.db"

_CACHE: dict[str, Any] = {"at": 0.0, "data": []}
_CACHE_TTL_S = 45.0
_RUNTIME_VERSION = 1
SESSION_STATES = {
    "running",
    "waiting",
    "approval_needed",
    "blocked",
    "completed",
    "failed",
    "unknown",
}
_RUNTIME_STATES = SESSION_STATES | {"active", "idle", "not_loaded"}
_ACTIVE_STATUS_TTL_S = 45 * 60
_SETTLED_STATUS_TTL_S = 24 * 3600

# Default cockpit window: recent activity, kept items, or live sessions.
RECENT_HOURS = 48


def _age_phrase(epoch: float) -> str:
    if not epoch:
        return ""
    d = max(0.0, time.time() - epoch)
    if d < 3600:
        return f"{int(d // 60)}m ago"
    if d < 172800:
        return f"{int(round(d / 3600))}h ago"
    return f"{int(round(d / 86400))}d ago"


def _pid_alive(pid: int | None) -> bool:
    if not pid or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False


def _slug_to_cwd(slug: str) -> str:
    """Best-effort path guess from Cursor/Claude project folder names."""
    s = (slug or "").lstrip("-")
    if not s or s in ("empty-window",) or s.startswith("var-folders") or s[0].isdigit():
        return ""
    guesses: list[Path] = []
    if "-Projects-" in s and s.startswith("Users-"):
        user, _, tail = s[len("Users-") :].partition("-Projects-")
        base = Path("/Users") / user / "Projects"
        guesses.append(base / tail)
        guesses.append(base / tail.replace("-", "/"))
        parts = tail.split("-")
        for i in range(1, min(len(parts), 6)):
            guesses.append(base / "-".join(parts[:i]) / "-".join(parts[i:]))
    elif "-Documents-" in s and s.startswith("Users-"):
        user, _, tail = s[len("Users-") :].partition("-Documents-")
        base = Path("/Users") / user / "Documents"
        guesses.append(base / tail)
        guesses.append(base / tail.replace("-", "/"))
    for g in guesses:
        if g.exists():
            return str(g)
    return str(guesses[0]) if guesses else ""


def _extract_text_chunks(obj: Any) -> list[str]:
    out: list[str] = []
    if isinstance(obj, str):
        out.append(obj)
    elif isinstance(obj, list):
        for item in obj:
            out.extend(_extract_text_chunks(item))
    elif isinstance(obj, dict):
        if obj.get("type") == "text" and isinstance(obj.get("text"), str):
            out.append(obj["text"])
        elif isinstance(obj.get("text"), str):
            out.append(obj["text"])
        elif isinstance(obj.get("content"), (str, list, dict)):
            out.extend(_extract_text_chunks(obj["content"]))
        elif isinstance(obj.get("message"), dict):
            out.extend(_extract_text_chunks(obj["message"]))
    return out


def _title_from_user_text(text: str) -> str:
    text = (text or "").strip()
    if not text:
        return ""
    m = _USER_QUERY_RE.search(text)
    if m:
        text = m.group(1).strip()
    # Drop leading timestamp wrappers Claude/Cursor sometimes embed.
    text = re.sub(r"<timestamp>.*?</timestamp>\s*", "", text, flags=re.S | re.I).strip()
    text = re.sub(r"\s+", " ", text)
    return text[:140]


def _cursor_title(jsonl: Path) -> str:
    try:
        with jsonl.open(encoding="utf-8", errors="replace") as f:
            for i, line in enumerate(f):
                if i > 40:
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if obj.get("role") != "user":
                    continue
                for chunk in _extract_text_chunks(obj.get("message") or obj):
                    title = _title_from_user_text(chunk)
                    if title:
                        return title
    except OSError:
        return ""
    return ""


def _explicit_transcript_state(jsonl: Path) -> str:
    """Return lifecycle state only when a structured transcript event proves it.

    Message recency and role order are intentionally ignored: a recently written
    user message does not prove a worker is running, and an assistant message does
    not prove that a task is complete.
    """
    state = "unknown"
    try:
        with jsonl.open(encoding="utf-8", errors="replace") as f:
            for line in f:
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                payload = obj.get("payload") if isinstance(obj.get("payload"), dict) else {}
                event_type = str(obj.get("type") or "").strip().lower()
                payload_type = str(payload.get("type") or "").strip().lower()
                subtype = str(obj.get("subtype") or payload.get("subtype") or "").strip().lower()
                status = str(obj.get("status") or payload.get("status") or "").strip().lower()
                role = str(obj.get("role") or "").strip().lower()

                # A new turn invalidates terminal evidence from a previous turn,
                # but it still does not establish liveness by itself.
                if role == "user" or event_type == "user" or payload_type == "user_message":
                    state = "unknown"
                elif event_type == "event_msg" and payload_type == "task_started":
                    state = "unknown"
                elif event_type == "turn_started":
                    state = "unknown"
                elif event_type == "turn_ended":
                    state = {
                        "success": "completed",
                        "completed": "completed",
                        "error": "failed",
                        "failed": "failed",
                        "aborted": "failed",
                        "cancelled": "failed",
                        "canceled": "failed",
                        "blocked": "blocked",
                    }.get(status, "unknown")
                elif event_type == "event_msg" and payload_type == "task_complete":
                    state = "failed" if status in {"error", "failed"} else "completed"
                elif event_type == "result":
                    if obj.get("is_error") is True or subtype in {"error", "failed"}:
                        state = "failed"
                    elif subtype in {"success", "completed"}:
                        state = "completed"
                elif event_type in {"approval_requested", "permission_request"}:
                    state = "approval_needed"
                elif event_type in {"blocked", "task_blocked"} or status == "blocked":
                    state = "blocked"
    except OSError:
        return "unknown"
    return state


def _transcript_observation(path: str | Path) -> tuple[dict[str, int] | None, str]:
    """Return a resumable byte cursor and a content fingerprint."""
    p = Path(path)
    try:
        stat = p.stat()
        if not p.is_file():
            return None, ""
        with p.open("rb") as f:
            f.seek(max(0, stat.st_size - 65536))
            tail = f.read()
    except OSError:
        return None, ""
    cursor = {
        "version": 1,
        "device": int(stat.st_dev),
        "inode": int(stat.st_ino),
        "offset": int(stat.st_size),
    }
    identity = f"{stat.st_dev}:{stat.st_ino}:{stat.st_size}:".encode()
    return cursor, hashlib.sha256(identity + tail).hexdigest()


def _attach_observation(row: dict[str, Any]) -> dict[str, Any]:
    cursor, fingerprint = _transcript_observation(str(row.get("path") or ""))
    return {
        **row,
        "observation_cursor": cursor,
        "observation_fingerprint": fingerprint,
    }


def _claude_meta(jsonl: Path) -> tuple[str, str]:
    """Return (title, first_user_snippet) from a Claude project jsonl."""
    custom = ""
    ai = ""
    first_user = ""
    try:
        with jsonl.open(encoding="utf-8", errors="replace") as f:
            for i, line in enumerate(f):
                if i > 80 and (custom or ai or first_user):
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                t = obj.get("type")
                if t == "custom-title" and obj.get("customTitle"):
                    custom = str(obj["customTitle"]).strip()[:140]
                elif t == "ai-title":
                    ai = str(obj.get("title") or obj.get("aiTitle") or "").strip()[:140]
                elif t == "user" and not first_user:
                    chunks = _extract_text_chunks(obj.get("message") or obj)
                    for chunk in chunks:
                        # Skip tool_result-only user rows.
                        if isinstance(obj.get("message"), dict):
                            content = obj["message"].get("content")
                            if isinstance(content, list) and content and isinstance(content[0], dict):
                                if content[0].get("type") == "tool_result":
                                    continue
                        title = _title_from_user_text(chunk)
                        if title and not title.startswith("[{"):
                            first_user = title
                            break
    except OSError:
        pass
    return custom or ai or first_user, first_user


def _scan_cursor(root: Path) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if not root.is_dir():
        return out
    for proj in root.iterdir():
        if not proj.is_dir() or proj.name.startswith("."):
            continue
        transcripts = proj / "agent-transcripts"
        if not transcripts.is_dir():
            continue
        project = proj.name
        cwd = _slug_to_cwd(project)
        for sess in transcripts.iterdir():
            if not sess.is_dir() or not _UUID_RE.match(sess.name):
                continue
            # Parent UUID folders only — skip nested subagents/.
            jsonl = sess / f"{sess.name}.jsonl"
            if not jsonl.is_file():
                continue
            try:
                mtime = jsonl.stat().st_mtime
            except OSError:
                continue
            title = _cursor_title(jsonl) or f"Cursor session {sess.name[:8]}"
            state = _explicit_transcript_state(jsonl)
            out.append(
                {
                    "id": f"cursor:{sess.name}",
                    "surface": "cursor",
                    "session_id": sess.name,
                    "title": title,
                    "cwd": cwd,
                    "project": project,
                    "mtime": mtime,
                    "age": _age_phrase(mtime),
                    "live": False,
                    "state": state,
                    "status_source": "transcript",
                    "path": str(jsonl),
                    "pid": None,
                }
            )
    return out


def _scan_claude(
    projects_root: Path,
    sessions_root: Path,
    *,
    include_active_cli: bool = True,
) -> list[dict[str, Any]]:
    live_by_id: dict[str, dict[str, Any]] = {}
    if sessions_root.is_dir():
        for sf in sessions_root.glob("*.json"):
            try:
                data = json.loads(sf.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            sid = str(data.get("sessionId") or "").strip()
            if not sid:
                continue
            pid = data.get("pid")
            try:
                pid_i = int(pid) if pid is not None else None
            except (TypeError, ValueError):
                pid_i = None
            started = data.get("startedAt")
            try:
                # Claude stores ms epoch.
                started_f = float(started) / 1000.0 if started else 0.0
            except (TypeError, ValueError):
                started_f = 0.0
            live_by_id[sid] = {
                "cwd": str(data.get("cwd") or ""),
                "pid": pid_i,
                "live": _pid_alive(pid_i),
                "name": str(data.get("name") or ""),
                "started_at": started_f,
            }

    # Current Claude Code exposes active agents through the CLI. Merge it over
    # legacy session files; an empty result is an honest zero, not a failure.
    active_rows: Any = []
    if include_active_cli:
        try:
            proc = subprocess.run(
                ["claude", "agents", "--json"],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            active_rows = json.loads(proc.stdout) if proc.returncode == 0 else []
        except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
            active_rows = []
    if isinstance(active_rows, dict):
        active_rows = active_rows.get("agents") or []
    for item in active_rows if isinstance(active_rows, list) else []:
        if not isinstance(item, dict):
            continue
        sid = str(item.get("sessionId") or item.get("session_id") or item.get("id") or "")
        if not sid:
            continue
        pid = item.get("pid")
        try:
            pid_i = int(pid) if pid is not None else None
        except (TypeError, ValueError):
            pid_i = None
        live_by_id[sid] = {
            **(live_by_id.get(sid) or {}),
            "cwd": str(item.get("cwd") or (live_by_id.get(sid) or {}).get("cwd") or ""),
            "pid": pid_i,
            "live": True,
            "name": str(item.get("name") or item.get("title") or ""),
            "started_at": float((live_by_id.get(sid) or {}).get("started_at") or time.time()),
        }

    by_id: dict[str, dict[str, Any]] = {}
    if projects_root.is_dir():
        for proj in projects_root.iterdir():
            if not proj.is_dir():
                continue
            project = proj.name
            cwd_guess = _slug_to_cwd(project)
            for jsonl in proj.glob("*.jsonl"):
                sid = jsonl.stem
                if not _UUID_RE.match(sid):
                    continue
                try:
                    mtime = jsonl.stat().st_mtime
                except OSError:
                    continue
                title, _ = _claude_meta(jsonl)
                live = live_by_id.get(sid) or {}
                title = title or live.get("name") or f"Claude session {sid[:8]}"
                by_id[sid] = {
                    "id": f"claude:{sid}",
                    "surface": "claude",
                    "session_id": sid,
                    "title": title,
                    "cwd": live.get("cwd") or cwd_guess,
                    "project": project,
                    "mtime": max(mtime, float(live.get("started_at") or 0)),
                    "age": _age_phrase(max(mtime, float(live.get("started_at") or 0))),
                    "live": bool(live.get("live")),
                    "state": "running" if live.get("live") else _explicit_transcript_state(jsonl),
                    "status_source": "claude_agents" if live.get("live") else "transcript",
                    "path": str(jsonl),
                    "pid": live.get("pid"),
                }

    # Live sessions with no project jsonl yet still belong in the cockpit.
    for sid, live in live_by_id.items():
        if sid in by_id:
            continue
        mtime = float(live.get("started_at") or 0) or time.time()
        by_id[sid] = {
            "id": f"claude:{sid}",
            "surface": "claude",
            "session_id": sid,
            "title": live.get("name") or f"Claude session {sid[:8]}",
            "cwd": live.get("cwd") or "",
            "project": "",
            "mtime": mtime,
            "age": _age_phrase(mtime),
            "live": bool(live.get("live")),
            "state": "running" if live.get("live") else "unknown",
            "status_source": "claude_agents" if live.get("live") else "history",
            "path": str(sessions_root / f"{live.get('pid') or sid}.json"),
            "pid": live.get("pid"),
        }
    return list(by_id.values())


def _codex_rollouts(root: Path) -> dict[str, Path]:
    """Best-effort transcript locations; the SQLite catalog remains identity truth."""
    paths: dict[str, Path] = {}
    for base in (root / "sessions", root / "archived_sessions"):
        if not base.is_dir():
            continue
        for path in base.rglob("*.jsonl"):
            match = re.search(
                r"([0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12})",
                path.name,
                re.I,
            )
            if match:
                paths.setdefault(match.group(1), path)
    return paths


def _runtime_dir_mtime_ns(root: Path) -> int:
    try:
        return root.stat().st_mtime_ns
    except OSError:
        return 0


def _load_runtime_statuses(root: Path) -> dict[tuple[str, str], dict[str, Any]]:
    """Read strict task-owned lifecycle records; one bad record cannot hide peers."""
    out: dict[tuple[str, str], dict[str, Any]] = {}
    if not root.is_dir():
        return out
    for path in root.glob("*.json"):
        try:
            item = json.loads(path.read_text(encoding="utf-8"))
            version = item.get("version")
            surface = str(item.get("surface") or "").strip()
            thread_id = str(item.get("thread_id") or "").strip()
            state = str(item.get("state") or "").strip()
            observed_at = float(item.get("observed_at") or 0)
        except (OSError, json.JSONDecodeError, TypeError, ValueError, AttributeError):
            continue
        if (
            version != _RUNTIME_VERSION
            or surface not in ("codex", "cursor", "claude")
            or not thread_id
            or state not in _RUNTIME_STATES
            or observed_at <= 0
        ):
            continue
        expected = f"{surface}--{thread_id}.json"
        if path.name != expected:
            continue
        out[(surface, thread_id)] = {
            "state": state,
            "observed_at": observed_at,
            "hook_event_name": str(item.get("hook_event_name") or ""),
            "turn_id": str(item.get("turn_id") or ""),
        }
    return out


def _apply_runtime_status(
    row: dict[str, Any],
    statuses: dict[tuple[str, str], dict[str, Any]],
    *,
    now: float,
) -> dict[str, Any]:
    status = statuses.get((str(row.get("surface") or ""), str(row.get("session_id") or "")))
    if not status:
        return row
    age = max(0.0, now - float(status["observed_at"]))
    ttl = _ACTIVE_STATUS_TTL_S if status["state"] == "active" else _SETTLED_STATUS_TTL_S
    if age > ttl:
        if row.get("surface") == "codex":
            return {
                **row,
                "state": "unknown",
                "live": False,
                "status_source": "lifecycle_hook_stale",
                "status_observed_at": status["observed_at"],
            }
        return row
    state, live = {
        "active": ("running", True),
        "idle": ("waiting", False),
        "not_loaded": ("unknown", False),
        "running": ("running", True),
        "waiting": ("waiting", False),
        "approval_needed": ("approval_needed", False),
        "blocked": ("blocked", False),
        "completed": ("completed", False),
        "failed": ("failed", False),
        "unknown": ("unknown", False),
    }[status["state"]]
    return {
        **row,
        "state": state,
        "live": live,
        "status_source": "lifecycle_hook",
        "status_observed_at": status["observed_at"],
        "status_event": status["hook_event_name"],
        "status_turn_id": status["turn_id"],
    }


def _scan_codex(db_path: Path, root: Path) -> list[dict[str, Any]]:
    """Read Codex's current local catalog without claiming unsupported liveness.

    The old scanner keyed off ``session_index.jsonl``. The desktop app now owns
    a versioned SQLite catalog with host/project ids and stable native UUIDs.
    Its catalog has no execution status column, so rows are deliberately marked
    ``unknown`` rather than turning recency into a false "live" signal.
    """
    if not db_path.is_file():
        return []
    rollouts = _codex_rollouts(root)
    try:
        uri = f"file:{db_path}?mode=ro"
        with closing(sqlite3.connect(uri, uri=True, timeout=2)) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """SELECT host_id, thread_id, display_title, source_created_at,
                          source_updated_at, source_recency_at, cwd, source_kind,
                          source_detail, model_provider, git_branch, project_id,
                          conversation_origin
                   FROM local_thread_catalog
                   WHERE missing_candidate = 0
                   ORDER BY source_recency_at DESC, source_created_at DESC"""
            ).fetchall()
    except (sqlite3.Error, OSError):
        return []

    out: list[dict[str, Any]] = []
    for row in rows:
        sid = str(row["thread_id"] or "").strip()
        host_id = str(row["host_id"] or "").strip()
        if not sid or not host_id:
            continue
        mtime = float(row["source_recency_at"] or row["source_updated_at"] or 0)
        cwd = str(row["cwd"] or "")
        rollout = rollouts.get(sid)
        transcript_state = _explicit_transcript_state(rollout) if rollout else "unknown"
        out.append(
            {
                "id": f"codex:{host_id}:{sid}",
                "surface": "codex",
                "session_id": sid,
                "host_id": host_id,
                "title": str(row["display_title"] or f"Codex task {sid[:8]}")[:140],
                "cwd": cwd,
                "project": str(row["project_id"] or "") or Path(cwd).name,
                "project_id": str(row["project_id"] or ""),
                "mtime": mtime,
                "created_at_epoch": float(row["source_created_at"] or 0),
                "age": _age_phrase(mtime),
                "live": False,
                "state": transcript_state,
                "status_source": "transcript" if transcript_state != "unknown" else "catalog",
                "path": str(rollout or ""),
                "pid": None,
                "source_kind": str(row["source_kind"] or ""),
                "source_detail": str(row["source_detail"] or ""),
                "model_provider": str(row["model_provider"] or ""),
                "git_branch": str(row["git_branch"] or ""),
                "conversation_origin": str(row["conversation_origin"] or ""),
            }
        )
    return out


def scan_agent_sessions(
    *,
    cursor_root: Path | None = None,
    claude_projects: Path | None = None,
    claude_sessions: Path | None = None,
    codex_db: Path | None = None,
    codex_root: Path | None = None,
    runtime_status_dir: Path | None = None,
    force: bool = False,
) -> list[dict[str, Any]]:
    """Unified Codex + Cursor + Claude rows, newest first. Cached ~45s."""
    now = time.time()
    runtime_root = Path(runtime_status_dir) if runtime_status_dir else state_path("agent-runtime")
    runtime_mtime_ns = _runtime_dir_mtime_ns(runtime_root)
    if (
        not force
        and _CACHE["data"]
        and now - _CACHE["at"] < _CACHE_TTL_S
        and _CACHE.get("runtime_mtime_ns") == runtime_mtime_ns
    ):
        return list(_CACHE["data"])
    rows = _scan_cursor(Path(cursor_root) if cursor_root else CURSOR_PROJECTS)
    rows.extend(
        _scan_claude(
            Path(claude_projects) if claude_projects else CLAUDE_PROJECTS,
            Path(claude_sessions) if claude_sessions else CLAUDE_SESSIONS,
            include_active_cli=claude_projects is None and claude_sessions is None,
        )
    )
    # Fixture callers that override the legacy roots are defining a complete
    # synthetic population. Do not leak this laptop's real Codex catalog into
    # those tests; an explicit codex_db opts it back in.
    custom_legacy_roots = any(value is not None for value in (cursor_root, claude_projects, claude_sessions))
    if codex_db is not None or not custom_legacy_roots:
        rows.extend(
            _scan_codex(
                Path(codex_db) if codex_db else CODEX_DB,
                Path(codex_root) if codex_root else CODEX_ROOT,
            )
        )
    statuses = _load_runtime_statuses(runtime_root)
    rows = [_attach_observation(_apply_runtime_status(row, statuses, now=now)) for row in rows]
    rows.sort(key=lambda r: (-bool(r.get("live")), -float(r.get("mtime") or 0)))
    _CACHE["at"], _CACHE["data"] = now, rows
    _CACHE["runtime_mtime_ns"] = runtime_mtime_ns
    return list(rows)


def merge_overlays(
    rows: list[dict[str, Any]],
    overlays: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """Attach pin/snooze/archive fields from SQLite overlays."""
    now = time.time()
    out: list[dict[str, Any]] = []
    for r in rows:
        o = overlays.get(r["id"]) or {}
        snooze_until = str(o.get("snooze_until") or "")
        snoozed = False
        if snooze_until:
            try:
                # ISO or epoch seconds
                if snooze_until.replace(".", "", 1).isdigit():
                    snoozed = float(snooze_until) > now
                else:
                    from datetime import datetime

                    snoozed = datetime.fromisoformat(snooze_until.replace("Z", "+00:00")).timestamp() > now
            except (ValueError, TypeError, OSError):
                snoozed = False
        merged = {
            **r,
            "kept": bool(o.get("pinned")),
            "hidden": bool(o.get("archived")),
            "snooze_until": snooze_until,
            "snoozed": snoozed,
            "labels": str(o.get("labels") or ""),
            "linked_rev": str(o.get("linked_rev") or ""),
            "notes": str(o.get("notes") or ""),
        }
        out.append(merged)
    return out


# Words that appear in a spoken phrase and carry no search intent. Requiring
# them EXCLUDES real matches: "audit and redesign" would miss a thread titled
# "UI/UX audit redesign" over the missing "and".
_QUERY_NOISE = frozenset(
    {"a", "an", "and", "the", "of", "for", "on", "in", "to", "my", "our", "that", "this", "it"}
)


def _matches_query(query: str, blob: str) -> bool:
    """Every meaningful word present, in any order.

    This was `query not in blob` — one contiguous substring — and a spoken
    phrase is almost never a substring of a thread title. Asked for "the status
    of the UI/UX audit redesign", Brutus searched agent threads with those
    words and got zero hits, while a thread named "Company page UI/UX audit and
    redesign" sat there running: every word present, the phrase absent, because
    of one "and". It then told him the work did not exist.

    Substring matching is a subset of this, so nothing that used to match stops
    matching.
    """
    if query in blob:
        return True
    words = [w for w in re.split(r"[^a-z0-9/._-]+", query) if len(w) > 1 and w not in _QUERY_NOISE]
    return bool(words) and all(word in blob for word in words)


def filter_cockpit(
    rows: list[dict[str, Any]],
    *,
    include_hidden: bool = False,
    surface: str = "",
    q: str = "",
    recent_hours: float = RECENT_HOURS,
) -> list[dict[str, Any]]:
    """Default view: live, kept, or recently active — hide parked/hidden."""
    cutoff = time.time() - recent_hours * 3600
    qn = (q or "").strip().lower()
    surf = (surface or "").strip().lower()
    out: list[dict[str, Any]] = []
    for r in rows:
        if r.get("hidden") and not include_hidden:
            continue
        if r.get("snoozed") and not r.get("kept"):
            continue
        if surf and r.get("surface") != surf:
            continue
        if not (r.get("live") or r.get("kept") or float(r.get("mtime") or 0) >= cutoff):
            if not include_hidden:
                continue
        if qn:
            blob = " ".join(
                str(r.get(k) or "") for k in ("title", "cwd", "project", "session_id", "labels")
            ).lower()
            if not _matches_query(qn, blob):
                continue
        out.append(r)
    return out


def active_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    """Counts for nav badge / Work strip (non-hidden cockpit rows)."""
    visible = filter_cockpit(rows)
    return {
        "total": len(visible),
        "codex": sum(1 for r in visible if r.get("surface") == "codex"),
        "cursor": sum(1 for r in visible if r.get("surface") == "cursor"),
        "claude": sum(1 for r in visible if r.get("surface") == "claude"),
        "live": sum(1 for r in visible if r.get("live")),
    }


def _message_excerpt(raw: bytes, *, max_chars: int) -> str:
    """Extract human/model messages from complete JSONL records."""
    parts: list[str] = []
    total = 0
    for raw_line in raw.splitlines():
        if total >= max_chars:
            break
        line = raw_line.decode("utf-8", errors="replace").strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        # Codex rollouts wrap messages in event_msg / response_item payloads.
        if obj.get("type") in ("event_msg", "response_item"):
            payload = obj.get("payload") or {}
            role = payload.get("role") or payload.get("type") or ""
            source = payload
        else:
            role = obj.get("role") or obj.get("type") or ""
            source = obj
        if role == "user_message":
            role = "user"
        elif role == "agent_message":
            role = "assistant"
        if role not in ("user", "assistant"):
            continue
        texts = _extract_text_chunks(source.get("message") or source)
        body = " ".join(t.strip() for t in texts if t and t.strip())
        body = _USER_QUERY_RE.sub(lambda m: m.group(1), body)
        body = re.sub(r"\s+", " ", body).strip()
        if not body:
            continue
        chunk = f"{role}: {body[:800]}"
        parts.append(chunk)
        total += len(chunk)
    return "\n".join(parts)[:max_chars]


def read_transcript_delta(
    path: str | Path,
    *,
    cursor: dict[str, Any] | None = None,
    max_chars: int = 6000,
    max_scan_bytes: int = 1024 * 1024,
) -> dict[str, Any]:
    """Read new JSONL content, or a bounded tail on the first observation.

    The returned cursor is safe to persist. Rotation, replacement, truncation,
    malformed cursors, and oversized deltas fail back to a bounded tail instead
    of reading an unbounded file or silently skipping replacement content.
    """
    p = Path(path)
    if not p.is_file():
        return {
            "ok": False,
            "excerpt": "",
            "cursor": None,
            "fingerprint": "",
            "reset": False,
            "truncated": False,
            "bytes_read": 0,
        }
    try:
        stat = p.stat()
        size = int(stat.st_size)
        cursor_matches = bool(
            isinstance(cursor, dict)
            and cursor.get("version") == 1
            and cursor.get("device") == int(stat.st_dev)
            and cursor.get("inode") == int(stat.st_ino)
            and isinstance(cursor.get("offset"), int)
            and 0 <= int(cursor["offset"]) <= size
        )
        reset = cursor is not None and not cursor_matches
        requested_start = int(cursor["offset"]) if cursor_matches else 0
        start = requested_start
        truncated = False
        if size - start > max_scan_bytes:
            start = size - max_scan_bytes
            truncated = True
        with p.open("rb") as f:
            f.seek(start)
            raw = f.read(size - start)
        # Starting in a bounded tail may split a JSON record. Drop that one
        # partial line; a true incremental cursor always starts at a record end.
        if start > requested_start or (not cursor_matches and start > 0):
            _, separator, raw = raw.partition(b"\n")
            if not separator:
                raw = b""
    except OSError:
        return {
            "ok": False,
            "excerpt": "",
            "cursor": None,
            "fingerprint": "",
            "reset": False,
            "truncated": False,
            "bytes_read": 0,
        }
    next_cursor, fingerprint = _transcript_observation(p)
    return {
        "ok": True,
        "excerpt": _message_excerpt(raw, max_chars=max_chars),
        "cursor": next_cursor,
        "fingerprint": fingerprint,
        "reset": reset,
        "truncated": truncated,
        "bytes_read": len(raw),
    }


def read_transcript_excerpt(path: str | Path, *, max_chars: int = 6000) -> str:
    """Capped tail excerpt for compatibility with existing callers."""
    return str(read_transcript_delta(path, max_chars=max_chars)["excerpt"])


def summarize_transcript(
    path: str | Path,
    *,
    cfg: Any | None = None,
    max_chars: int = 6000,
) -> dict[str, Any]:
    """Summarize a capped transcript via Sonnet (Cursor alternate), or the excerpt."""
    excerpt = read_transcript_excerpt(path, max_chars=max_chars)
    if not excerpt:
        return {"ok": False, "error": "no readable transcript at that path"}
    if cfg is not None:
        try:
            from .brain import complete

            summary = complete(
                cfg,
                [
                    {
                        "role": "system",
                        "content": (
                            "Summarize this agent transcript in 5-8 plain bullets. "
                            "Only use facts present. Never invent tickets or outcomes."
                        ),
                    },
                    {"role": "user", "content": excerpt},
                ],
            )
            return {"ok": True, "summary": summary, "source": "brain"}
        except Exception as exc:  # noqa: BLE001 — honest fallback
            return {
                "ok": True,
                "summary": excerpt[:2000],
                "source": "excerpt",
                "note": f"brain unavailable ({exc}); returning capped excerpt",
            }
    return {"ok": True, "summary": excerpt[:2000], "source": "excerpt"}
