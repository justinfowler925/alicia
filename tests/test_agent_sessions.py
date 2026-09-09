"""Agent thread cockpit — Cursor + Claude scanners (tmpdir fixtures)."""

from __future__ import annotations

import json
import os
import sqlite3
import time
from pathlib import Path
from unittest.mock import patch

from brutus.agent_sessions import (
    SESSION_STATES,
    active_counts,
    filter_cockpit,
    merge_overlays,
    read_transcript_excerpt,
    read_transcript_delta,
    scan_agent_sessions,
    summarize_transcript,
)
from brutus.memory import MemoryStore


def _write_cursor_session(root: Path, project: str, sid: str, query: str) -> Path:
    d = root / project / "agent-transcripts" / sid
    d.mkdir(parents=True)
    # Nested subagent folder must be ignored by scanner.
    (d / "subagents" / "deadbeef-0000-0000-0000-000000000001").mkdir(parents=True)
    jl = d / f"{sid}.jsonl"
    row = {
        "role": "user",
        "message": {
            "content": [
                {
                    "type": "text",
                    "text": f"<user_query>\n{query}\n</user_query>",
                }
            ]
        },
    }
    jl.write_text(json.dumps(row) + "\n", encoding="utf-8")
    return jl


def _write_claude_session(projects: Path, sessions: Path, project: str, sid: str, title: str, pid: int) -> Path:
    p = projects / project
    p.mkdir(parents=True, exist_ok=True)
    jl = p / f"{sid}.jsonl"
    lines = [
        {"type": "custom-title", "customTitle": title, "sessionId": sid},
        {
            "type": "user",
            "message": {"role": "user", "content": f"please help with {title}"},
        },
    ]
    jl.write_text("\n".join(json.dumps(x) for x in lines) + "\n", encoding="utf-8")
    sessions.mkdir(parents=True, exist_ok=True)
    (sessions / f"{pid}.json").write_text(
        json.dumps(
            {
                "pid": pid,
                "sessionId": sid,
                "cwd": "/Users/justinfowler/Projects/brutus",
                "startedAt": 1_700_000_000_000,
                "name": "brutus-live",
            }
        ),
        encoding="utf-8",
    )
    return jl


def test_scan_cursor_and_claude(tmp_path: Path):
    cursor = tmp_path / "cursor"
    claude_p = tmp_path / "claude_projects"
    claude_s = tmp_path / "claude_sessions"
    sid_c = "11111111-1111-1111-1111-111111111111"
    sid_a = "22222222-2222-2222-2222-222222222222"
    _write_cursor_session(cursor, "Users-justinfowler-Projects-brutus", sid_c, "build the agents tab")
    _write_claude_session(
        claude_p,
        claude_s,
        "-Users-justinfowler-Projects-brutus",
        sid_a,
        "Debug Brutus chat",
        pid=os.getpid(),  # this process is alive
    )

    rows = scan_agent_sessions(
        cursor_root=cursor,
        claude_projects=claude_p,
        claude_sessions=claude_s,
        force=True,
    )
    by_id = {r["id"]: r for r in rows}
    assert f"cursor:{sid_c}" in by_id
    assert by_id[f"cursor:{sid_c}"]["title"] == "build the agents tab"
    assert by_id[f"cursor:{sid_c}"]["surface"] == "cursor"
    # Nested subagent uuid folder must not appear as its own row.
    assert "cursor:deadbeef-0000-0000-0000-000000000001" not in by_id

    assert f"claude:{sid_a}" in by_id
    assert by_id[f"claude:{sid_a}"]["title"] == "Debug Brutus chat"
    assert by_id[f"claude:{sid_a}"]["live"] is True
    assert by_id[f"claude:{sid_a}"]["cwd"] == "/Users/justinfowler/Projects/brutus"


def test_overlay_filter_and_counts(tmp_path: Path):
    cursor = tmp_path / "cursor"
    sid = "33333333-3333-3333-3333-333333333333"
    _write_cursor_session(cursor, "Users-justinfowler-Projects-brutus", sid, "old work")
    with patch("brutus.agent_sessions.subprocess.run") as run:
        rows = scan_agent_sessions(
            cursor_root=cursor,
            claude_projects=tmp_path / "empty_c",
            claude_sessions=tmp_path / "empty_s",
            force=True,
        )
    run.assert_not_called(), "fixture roots must not merge this laptop's live Claude agents"
    mem = MemoryStore(tmp_path / "m.sqlite")
    mem.upsert_agent_overlay(f"cursor:{sid}", archived=True)
    merged = merge_overlays(rows, mem.list_agent_overlays())
    assert filter_cockpit(merged) == []
    shown = filter_cockpit(merged, include_hidden=True)
    assert len(shown) == 1
    assert shown[0]["hidden"] is True

    mem.upsert_agent_overlay(f"cursor:{sid}", archived=False, pinned=True)
    merged = merge_overlays(rows, mem.list_agent_overlays())
    kept = filter_cockpit(merged)
    assert len(kept) == 1
    assert kept[0]["kept"] is True
    counts = active_counts(merged)
    assert counts["cursor"] == 1
    assert counts["total"] == 1


def test_codex_catalog_is_scanned_with_provider_stable_identity(tmp_path: Path):
    db = tmp_path / "codex.sqlite"
    with sqlite3.connect(db) as conn:
        conn.execute(
            """CREATE TABLE local_thread_catalog (
                host_id TEXT, thread_id TEXT, display_title TEXT,
                source_created_at INTEGER, source_updated_at INTEGER,
                source_recency_at INTEGER, cwd TEXT, source_kind TEXT,
                source_detail TEXT, model_provider TEXT, git_branch TEXT,
                observation_sequence INTEGER, missing_candidate INTEGER,
                thread_source TEXT, project_id TEXT, conversation_origin TEXT
            )"""
        )
        conn.execute(
            "INSERT INTO local_thread_catalog VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "local", "abc-123", "Nucleus audit", 100, 200, 200,
                "/Users/justinfowler/Projects/brutus", "local", "", "openai",
                "codex/nucleus", 1, 0, "desktop", "brutus", "desktop",
            ),
        )

    rows = scan_agent_sessions(
        cursor_root=tmp_path / "cursor",
        claude_projects=tmp_path / "claude",
        claude_sessions=tmp_path / "sessions",
        codex_db=db,
        codex_root=tmp_path / "codex-root",
        force=True,
    )

    row = next(item for item in rows if item["surface"] == "codex")
    assert row["id"] == "codex:local:abc-123"
    assert row["host_id"] == "local"
    assert row["session_id"] == "abc-123"
    assert row["state"] == "unknown"
    assert row["status_source"] == "catalog"


def _write_runtime_status(
    root: Path,
    *,
    surface: str,
    thread_id: str,
    state: str,
    observed_at: float,
    version: int = 1,
) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / f"{surface}--{thread_id}.json").write_text(
        json.dumps(
            {
                "version": version,
                "surface": surface,
                "thread_id": thread_id,
                "state": state,
                "observed_at": observed_at,
                "hook_event_name": "UserPromptSubmit" if state == "active" else "Stop",
                "turn_id": "turn-1",
            }
        ),
        encoding="utf-8",
    )


def test_runtime_status_joins_exact_native_id_and_maps_states(tmp_path: Path):
    db = tmp_path / "codex.sqlite"
    with sqlite3.connect(db) as conn:
        conn.execute(
            """CREATE TABLE local_thread_catalog (
                host_id TEXT, thread_id TEXT, display_title TEXT,
                source_created_at INTEGER, source_updated_at INTEGER,
                source_recency_at INTEGER, cwd TEXT, source_kind TEXT,
                source_detail TEXT, model_provider TEXT, git_branch TEXT,
                observation_sequence INTEGER, missing_candidate INTEGER,
                thread_source TEXT, project_id TEXT, conversation_origin TEXT
            )"""
        )
        for i, sid in enumerate(("active-id", "idle-id", "not-loaded-id", "missing-id")):
            conn.execute(
                "INSERT INTO local_thread_catalog VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                ("local", sid, sid, 100, 200 + i, 200 + i, "/tmp", "local", "", "openai", "", 1, 0, "desktop", "p", "desktop"),
            )
    runtime = tmp_path / "runtime"
    now = time.time()
    _write_runtime_status(runtime, surface="codex", thread_id="active-id", state="active", observed_at=now)
    _write_runtime_status(runtime, surface="codex", thread_id="idle-id", state="idle", observed_at=now)
    _write_runtime_status(runtime, surface="codex", thread_id="not-loaded-id", state="not_loaded", observed_at=now)
    rows = scan_agent_sessions(
        cursor_root=tmp_path / "cursor",
        claude_projects=tmp_path / "claude",
        claude_sessions=tmp_path / "sessions",
        codex_db=db,
        codex_root=tmp_path / "root",
        runtime_status_dir=runtime,
        force=True,
    )
    by_id = {row["session_id"]: row for row in rows}
    assert (by_id["active-id"]["state"], by_id["active-id"]["live"]) == ("running", True)
    assert (by_id["idle-id"]["state"], by_id["idle-id"]["live"]) == ("waiting", False)
    assert (by_id["not-loaded-id"]["state"], by_id["not-loaded-id"]["live"]) == ("unknown", False)
    assert by_id["missing-id"]["status_source"] == "catalog"
    assert by_id["active-id"]["status_source"] == "lifecycle_hook"
    assert by_id["active-id"]["status_turn_id"] == "turn-1"


def test_runtime_status_fails_stale_and_malformed_records_safe(tmp_path: Path):
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    _write_runtime_status(
        runtime,
        surface="codex",
        thread_id="stale-id",
        state="active",
        observed_at=time.time() - 46 * 60,
    )
    (runtime / "codex--bad-json.json").write_text("not-json", encoding="utf-8")
    _write_runtime_status(runtime, surface="codex", thread_id="bad-version", state="idle", observed_at=time.time(), version=2)
    db = tmp_path / "codex.sqlite"
    with sqlite3.connect(db) as conn:
        conn.execute(
            """CREATE TABLE local_thread_catalog (
                host_id TEXT, thread_id TEXT, display_title TEXT,
                source_created_at INTEGER, source_updated_at INTEGER,
                source_recency_at INTEGER, cwd TEXT, source_kind TEXT,
                source_detail TEXT, model_provider TEXT, git_branch TEXT,
                observation_sequence INTEGER, missing_candidate INTEGER,
                thread_source TEXT, project_id TEXT, conversation_origin TEXT
            )"""
        )
        for sid in ("stale-id", "bad-json", "bad-version"):
            conn.execute(
                "INSERT INTO local_thread_catalog VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                ("local", sid, sid, 100, 200, 200, "/tmp", "local", "", "openai", "", 1, 0, "desktop", "p", "desktop"),
            )
    rows = scan_agent_sessions(
        cursor_root=tmp_path / "cursor",
        claude_projects=tmp_path / "claude",
        claude_sessions=tmp_path / "sessions",
        codex_db=db,
        codex_root=tmp_path / "root",
        runtime_status_dir=runtime,
        force=True,
    )
    by_id = {row["session_id"]: row for row in rows}
    assert by_id["stale-id"]["state"] == "unknown"
    assert by_id["stale-id"]["live"] is False
    assert by_id["stale-id"]["status_source"] == "lifecycle_hook_stale"
    assert by_id["bad-json"]["status_source"] == "catalog"
    assert by_id["bad-version"]["status_source"] == "catalog"


def test_runtime_directory_change_invalidates_agent_cache(tmp_path: Path):
    cursor = tmp_path / "cursor"
    sid = "55555555-5555-5555-5555-555555555555"
    _write_cursor_session(cursor, "Users-justinfowler-Projects-brutus", sid, "cache status")
    runtime = tmp_path / "runtime"
    rows = scan_agent_sessions(
        cursor_root=cursor,
        claude_projects=tmp_path / "claude",
        claude_sessions=tmp_path / "sessions",
        runtime_status_dir=runtime,
        force=True,
    )
    assert rows[0]["live"] is False
    _write_runtime_status(runtime, surface="cursor", thread_id=sid, state="active", observed_at=time.time())
    rows = scan_agent_sessions(
        cursor_root=cursor,
        claude_projects=tmp_path / "claude",
        claude_sessions=tmp_path / "sessions",
        runtime_status_dir=runtime,
    )
    assert rows[0]["state"] == "running"
    assert rows[0]["live"] is True


def test_transcript_excerpt_and_summarize(tmp_path: Path):
    jl = tmp_path / "t.jsonl"
    rows = [
        {
            "role": "user",
            "message": {"content": [{"type": "text", "text": "<user_query>fix renewals</user_query>"}]},
        },
        {
            "role": "assistant",
            "message": {"content": [{"type": "text", "text": "Looking at Total_of_Cases__c next."}]},
        },
    ]
    jl.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    excerpt = read_transcript_excerpt(jl)
    assert "fix renewals" in excerpt
    assert "Total_of_Cases__c" in excerpt
    out = summarize_transcript(jl, cfg=None)
    assert out["ok"] is True
    assert out["source"] == "excerpt"
    assert "fix renewals" in out["summary"]


def test_normalized_states_require_explicit_evidence_not_recency(tmp_path: Path):
    cursor = tmp_path / "cursor"
    project = "Users-justinfowler-Projects-brutus"
    cases = {
        "10000000-0000-0000-0000-000000000001": ({"type": "turn_ended", "status": "success"}, "completed"),
        "10000000-0000-0000-0000-000000000002": ({"type": "turn_ended", "status": "error"}, "failed"),
        "10000000-0000-0000-0000-000000000003": ({"type": "approval_requested"}, "approval_needed"),
        "10000000-0000-0000-0000-000000000004": ({"type": "task_blocked"}, "blocked"),
        # A fresh user message is a deliberate negative control: it proves
        # neither that a worker is running nor that it is waiting for Justin.
        "10000000-0000-0000-0000-000000000005": ({"role": "user", "message": {"content": "go"}}, "unknown"),
    }
    for sid, (event, _expected) in cases.items():
        path = _write_cursor_session(cursor, project, sid, f"case {sid}")
        with path.open("a", encoding="utf-8") as f:
            if sid.endswith("0005"):
                f.write(json.dumps({"type": "turn_ended", "status": "success"}) + "\n")
            f.write(json.dumps(event) + "\n")
        os.utime(path, (time.time() + 3600, time.time() + 3600))

    runtime = tmp_path / "runtime"
    _write_runtime_status(runtime, surface="cursor", thread_id="10000000-0000-0000-0000-000000000005", state="active", observed_at=time.time())
    waiting_sid = "10000000-0000-0000-0000-000000000006"
    _write_cursor_session(cursor, project, waiting_sid, "wait here")
    _write_runtime_status(runtime, surface="cursor", thread_id=waiting_sid, state="idle", observed_at=time.time())
    unknown_sid = "10000000-0000-0000-0000-000000000007"
    unknown_path = _write_cursor_session(cursor, project, unknown_sid, "recent is not live")
    os.utime(unknown_path, (time.time() + 7200, time.time() + 7200))

    rows = scan_agent_sessions(
        cursor_root=cursor,
        claude_projects=tmp_path / "claude",
        claude_sessions=tmp_path / "sessions",
        runtime_status_dir=runtime,
        force=True,
    )
    by_id = {row["session_id"]: row for row in rows}
    expected = {sid: state for sid, (_event, state) in cases.items()}
    expected["10000000-0000-0000-0000-000000000005"] = "running"
    expected[waiting_sid] = "waiting"
    expected[unknown_sid] = "unknown"

    assert len(expected) == 7, "denominator: seven lifecycle fixtures"
    assert sum(by_id[sid]["state"] == state for sid, state in expected.items()) == 7
    assert {by_id[sid]["state"] for sid in expected} == SESSION_STATES
    assert by_id[unknown_sid]["live"] is False, "future mtime must not manufacture liveness"


def test_scan_exposes_observation_cursor_and_fingerprint(tmp_path: Path):
    cursor = tmp_path / "cursor"
    session_ids = [f"20000000-0000-0000-0000-00000000000{i}" for i in range(1, 4)]
    for sid in session_ids:
        _write_cursor_session(cursor, "Users-justinfowler-Projects-brutus", sid, sid)
    rows = scan_agent_sessions(
        cursor_root=cursor,
        claude_projects=tmp_path / "claude",
        claude_sessions=tmp_path / "sessions",
        force=True,
    )
    assert len(rows) == 3, "denominator: all three readable transcripts"
    assert sum(isinstance(row["observation_cursor"], dict) for row in rows) == 3
    assert sum(len(row["observation_fingerprint"]) == 64 for row in rows) == 3


def test_transcript_delta_reads_tail_then_only_new_content(tmp_path: Path):
    path = tmp_path / "long.jsonl"
    lines = [
        {"role": "user", "message": {"content": f"old-{i}"}}
        for i in range(260)
    ]
    lines.append({"role": "assistant", "message": {"content": "LATEST-MARKER"}})
    path.write_text("\n".join(json.dumps(row) for row in lines) + "\n", encoding="utf-8")

    first = read_transcript_delta(path, max_chars=6000, max_scan_bytes=4096)
    assert first["ok"] is True
    assert first["truncated"] is True
    assert "LATEST-MARKER" in first["excerpt"]
    assert "old-0\n" not in first["excerpt"]

    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"role": "user", "message": {"content": "ONLY-NEW"}}) + "\n")
    second = read_transcript_delta(path, cursor=first["cursor"])
    assert second["excerpt"] == "user: ONLY-NEW"
    assert second["reset"] is False
    unchanged = read_transcript_delta(path, cursor=second["cursor"])
    assert unchanged["excerpt"] == "", "negative control: unchanged files yield no repeated transcript"
    assert unchanged["bytes_read"] == 0


def test_transcript_delta_resets_safely_after_replacement(tmp_path: Path):
    path = tmp_path / "rotated.jsonl"
    path.write_text(json.dumps({"role": "user", "message": {"content": "before"}}) + "\n", encoding="utf-8")
    first = read_transcript_delta(path)
    replacement = tmp_path / "replacement.jsonl"
    replacement.write_text(json.dumps({"role": "assistant", "message": {"content": "after"}}) + "\n", encoding="utf-8")
    replacement.replace(path)

    delta = read_transcript_delta(path, cursor=first["cursor"])
    assert delta["ok"] is True
    assert delta["reset"] is True
    assert delta["excerpt"] == "assistant: after"


# --- searching for work the way it gets asked for ---------------------------


def test_a_spoken_phrase_finds_a_thread_whose_words_are_not_contiguous():
    """From session 7ac0ae3e2421, turn 1816.

    "the status of the UI/UX audit redesign" searched agent threads and
    returned nothing while a thread named "Company page UI/UX audit and
    redesign" was running. Every word was present; the phrase was not, because
    of one "and" — and the match was `query not in blob`, a single contiguous
    substring. Brutus then told him the work did not exist.
    """
    rows = [
        {
            "id": "claude:1",
            "title": "Company page UI/UX audit and redesign",
            "live": True,
            "surface": "claude",
        },
        {"id": "claude:2", "title": "Salesforce deal advancement audit", "live": True},
    ]

    hit = filter_cockpit(rows, q="UI/UX audit redesign")

    assert [r["id"] for r in hit] == ["claude:1"]


def test_word_order_does_not_matter():
    rows = [{"id": "c:1", "title": "Nucleus UX/UI deep dive", "live": True}]
    assert filter_cockpit(rows, q="deep dive nucleus")
    assert filter_cockpit(rows, q="nucleus deep dive")


def test_filler_words_never_exclude_a_real_match():
    """Requiring "and" is how "audit and redesign" missed "audit redesign"."""
    rows = [{"id": "c:1", "title": "UI/UX audit redesign", "live": True}]
    assert filter_cockpit(rows, q="the audit and the redesign")


def test_a_query_still_has_to_match_something():
    rows = [{"id": "c:1", "title": "Company page UI/UX audit and redesign", "live": True}]
    assert not filter_cockpit(rows, q="renewal tracker rewrite")
    # One matching word out of two is not a match.
    assert not filter_cockpit(rows, q="audit spreadsheet")


def test_substring_matching_still_works():
    """The old behaviour is a subset, so nothing that matched stops matching."""
    rows = [{"id": "c:1", "title": "Company page UI/UX audit and redesign", "live": True}]
    assert filter_cockpit(rows, q="page UI/UX audit and redesign")
    assert filter_cockpit(rows, q="redesign")
