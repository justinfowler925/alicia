"""Persistent observation loop for local Claude, Cursor, and Codex work.

The scanner establishes lifecycle truth; :mod:`session_supervisor` decides
whether a change deserves Justin's attention.  This runtime persists byte
cursors and assessments so a restart does not reread whole transcripts or
turn unchanged work into a fresh interruption.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Any

from .agent_sessions import filter_cockpit, read_transcript_delta, scan_agent_sessions
from .paths import state_path
from .session_supervisor import SessionAssessment, assess_session, redact_supervisor_transcript

Judge = Callable[[str], dict[str, Any] | str]

_PRIORITY = {
    "approval_needed": 0,
    "failed": 1,
    "blocked": 2,
    "conflict_or_duplicate": 3,
    "completed_followup": 4,
    "stale": 5,
    "none": 9,
}


class SupervisorRuntime:
    """Observe transcript deltas and retain the latest structured judgment."""

    def __init__(
        self,
        path: Path | str | None = None,
        *,
        scanner: Callable[..., list[dict[str, Any]]] = scan_agent_sessions,
        judge: Judge | None = None,
        stale_after_seconds: float = 45 * 60,
    ) -> None:
        self.path = Path(path) if path else state_path("supervisor.sqlite")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.scanner = scanner
        self.judge = judge
        self.stale_after_seconds = stale_after_seconds
        self._lock = threading.Lock()
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.path), timeout=10)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """CREATE TABLE IF NOT EXISTS observations (
                    agent_id TEXT PRIMARY KEY,
                    cursor_json TEXT NOT NULL DEFAULT '{}',
                    fingerprint TEXT NOT NULL DEFAULT '',
                    lifecycle_state TEXT NOT NULL DEFAULT 'unknown',
                    assessment_json TEXT NOT NULL DEFAULT '{}',
                    observed_at REAL NOT NULL
                )"""
            )

    def _previous(self, agent_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM observations WHERE agent_id=?", (agent_id,)
            ).fetchone()
        if row is None:
            return None
        out = dict(row)
        try:
            out["cursor"] = json.loads(out.pop("cursor_json") or "{}")
        except (json.JSONDecodeError, TypeError):
            out["cursor"] = None
        try:
            out["assessment"] = json.loads(out.pop("assessment_json") or "{}")
        except (json.JSONDecodeError, TypeError):
            out["assessment"] = {}
        return out

    def _save(
        self,
        agent_id: str,
        *,
        cursor: dict[str, Any] | None,
        fingerprint: str,
        lifecycle_state: str,
        assessment: SessionAssessment,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO observations
                       (agent_id, cursor_json, fingerprint, lifecycle_state,
                        assessment_json, observed_at)
                   VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT(agent_id) DO UPDATE SET
                       cursor_json=excluded.cursor_json,
                       fingerprint=excluded.fingerprint,
                       lifecycle_state=excluded.lifecycle_state,
                       assessment_json=excluded.assessment_json,
                       observed_at=excluded.observed_at""",
                (
                    agent_id,
                    json.dumps(cursor or {}, sort_keys=True),
                    fingerprint,
                    lifecycle_state,
                    json.dumps(assessment.to_dict(), sort_keys=True),
                    time.time(),
                ),
            )

    def snapshot(self, *, force: bool = False, limit: int = 40) -> dict[str, Any]:
        """Read catalog and persisted judgments without waiting on a model call."""
        rows = filter_cockpit(self.scanner(force=force))[:max(1, min(limit, 100))]
        sessions = []
        for row in rows:
            agent_id = str(row.get("id") or "")
            if not agent_id:
                continue
            previous = self._previous(agent_id)
            if previous and previous.get("lifecycle_state") == row.get("state"):
                assessment = _assessment_from_dict(previous.get("assessment") or {})
            else:
                assessment = assess_session(row, "")
            sessions.append({
                **{key: row.get(key) for key in ("id", "surface", "title", "state", "age", "mtime", "status_source")},
                "live": bool(row.get("live")),
                "linked_rev": row.get("linked_rev") or "",
                "project": row.get("project") or row.get("cwd") or "",
                "assessment": assessment.to_dict(),
            })
        interventions = [{"session": row, **row["assessment"]} for row in sessions if row["assessment"]["should_intervene"]]
        interventions.sort(key=lambda item: _PRIORITY.get(str(item.get("intervention_type")), 8))
        return {
            "sessions": sessions,
            "counts": {
                "total": len(sessions), "live": sum(row["live"] for row in sessions),
                "needs_attention": len(interventions),
                **{provider: sum(row["surface"] == provider for row in sessions) for provider in ("claude", "cursor", "codex")},
            },
            "assessment": interventions[0] if interventions else None,
            "interventions": interventions, "observed_at": time.time(),
        }

    def observe(self, *, force: bool = False, limit: int = 40) -> dict[str, Any]:
        """Return current sessions and the highest-value earned intervention."""
        with self._lock:
            rows = filter_cockpit(self.scanner(force=force))[: max(1, min(limit, 100))]
            rows.sort(key=lambda row: str(row.get("state")) not in {"approval_needed", "failed", "blocked"})
            sessions: list[dict[str, Any]] = []
            interventions: list[dict[str, Any]] = []
            # Lifecycle policy evaluates the whole population. A sweep may use
            # one provider call to sharpen intent and progress, prioritizing
            # lifecycle decisions. Ordinary updates remain silent.
            judgment_budget = 1
            for row in rows:
                agent_id = str(row.get("id") or "")
                if not agent_id:
                    continue
                previous = self._previous(agent_id)
                path = str(row.get("path") or "")
                delta = read_transcript_delta(
                    path,
                    cursor=(previous or {}).get("cursor"),
                )
                state = str(row.get("state") or "unknown")
                fingerprint = str(delta.get("fingerprint") or row.get("observation_fingerprint") or "")
                now = time.time()
                mtime = float(row.get("mtime") or now)
                stale_at = mtime + self.stale_after_seconds
                stale_crossed = bool(
                    previous is not None
                    and not row.get("live")
                    and float(previous.get("observed_at") or 0) < stale_at <= now
                )
                changed = bool(
                    previous is None
                    or delta.get("bytes_read")
                    or fingerprint != str((previous or {}).get("fingerprint") or "")
                    or state != str((previous or {}).get("lifecycle_state") or "")
                    or stale_crossed
                )
                needs_summary = bool(
                    self.judge is not None and judgment_budget
                    and (previous or {}).get("assessment", {}).get("judgment_profile", "policy") == "policy"
                )
                if changed or needs_summary:
                    # Refresh the bounded context when backfilling a session skipped
                    # by the previous sweep's single-call budget.
                    if needs_summary and not delta.get("excerpt"):
                        delta = read_transcript_delta(path, cursor=None)
                    if stale_crossed and not delta.get("excerpt"):
                        delta = read_transcript_delta(path, cursor=None)
                    age_seconds = 0.0 if row.get("live") else max(0.0, now - mtime)
                    session_record = {
                        **row,
                        "provider": row.get("surface") or "unknown",
                        "status": state,
                        "age_seconds": age_seconds,
                        "existing_ticket": row.get("linked_rev") or "",
                    }
                    evidence = [
                        f"lifecycle={state} source={row.get('status_source') or 'unknown'}",
                        f"observation={fingerprint[:12] or 'none'}",
                    ]
                    # The byte cursor remains over the raw local transcript;
                    # only the model-facing work judgment receives this
                    # minimized excerpt.
                    safe_delta = redact_supervisor_transcript(str(delta.get("excerpt") or ""))
                    assessment = assess_session(
                        session_record,
                        safe_delta,
                        evidence,
                        stale_after_seconds=self.stale_after_seconds,
                    )
                    if self.judge is not None and judgment_budget and safe_delta:
                        assessment = assess_session(
                            session_record,
                            safe_delta,
                            evidence,
                            judge=self.judge,
                            stale_after_seconds=self.stale_after_seconds,
                        )
                        judgment_budget -= 1
                        if assessment.judgment_source != "model":
                            assessment = replace(assessment, judgment_profile="supervisor_unavailable")
                        if assessment.judgment_source == "model":
                            assessment = replace(
                                assessment,
                                judgment_provider="claude",
                            )
                    self._save(
                        agent_id,
                        cursor=delta.get("cursor") or row.get("observation_cursor"),
                        fingerprint=fingerprint,
                        lifecycle_state=state,
                        assessment=assessment,
                    )
                else:
                    assessment = _assessment_from_dict((previous or {}).get("assessment") or {})

                slim = {
                    "id": agent_id,
                    "surface": row.get("surface"),
                    "title": row.get("title"),
                    "state": state,
                    "live": bool(row.get("live")),
                    "age": row.get("age"),
                    "mtime": row.get("mtime"),
                    "project": row.get("project") or row.get("cwd") or "",
                    "status_source": row.get("status_source"),
                    "linked_rev": row.get("linked_rev") or "",
                    "assessment": assessment.to_dict(),
                }
                sessions.append(slim)
                if assessment.should_intervene:
                    interventions.append({"session": slim, **assessment.to_dict()})

            interventions.sort(
                key=lambda item: (
                    _PRIORITY.get(str(item.get("intervention_type")), 8),
                    -float(item.get("confidence") or 0),
                )
            )
            counts = {
                "total": len(sessions),
                "live": sum(1 for row in sessions if row["live"]),
                "needs_attention": len(interventions),
                "claude": sum(1 for row in sessions if row["surface"] == "claude"),
                "cursor": sum(1 for row in sessions if row["surface"] == "cursor"),
                "codex": sum(1 for row in sessions if row["surface"] == "codex"),
            }
            return {
                "sessions": sessions,
                "counts": counts,
                "assessment": interventions[0] if interventions else None,
                "interventions": interventions,
                "observed_at": time.time(),
            }


def _assessment_from_dict(value: dict[str, Any]) -> SessionAssessment:
    """Rehydrate only data previously validated by SessionAssessment."""
    return SessionAssessment(
        goal=str(value.get("goal") or "Observe agent work"),
        verified_progress=tuple(value.get("verified_progress") or ()),
        blocker_or_decision=str(
            value.get("blocker_or_decision") or "No evidence-backed blocker or decision requires attention."
        ),
        recommended_next_action=str(
            value.get("recommended_next_action") or "Let the session continue without interruption."
        ),
        evidence=tuple(value.get("evidence") or ()),
        confidence=float(value.get("confidence") or 0.0),
        intervention_type=str(value.get("intervention_type") or "none"),  # type: ignore[arg-type]
        intervention_reason=str(
            value.get("intervention_reason") or "No evidence-backed blocker or decision requires attention."
        ),
        ticket_disposition=str(value.get("ticket_disposition") or "none"),  # type: ignore[arg-type]
        should_intervene=bool(value.get("should_intervene", False)),
        judgment_source=str(value.get("judgment_source") or "deterministic"),
        judgment_profile=str(value.get("judgment_profile") or "policy"),
        judgment_provider=str(value.get("judgment_provider") or "none"),
    )
