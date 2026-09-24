from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from alicia.supervisor_runtime import SupervisorRuntime


def _row(path: Path, *, state: str = "running") -> dict:
    return {
        "id": "codex:local:one",
        "session_id": "one",
        "surface": "codex",
        "title": "Build voice surface",
        "state": state,
        "live": state == "running",
        "mtime": path.stat().st_mtime,
        "age": "now",
        "status_source": "test",
        "path": str(path),
    }


def _write(path: Path, role: str, text: str) -> None:
    with path.open("a") as handle:
        handle.write(json.dumps({"role": role, "message": {"content": text}}) + "\n")


def test_runtime_persists_cursor_and_unchanged_progress_stays_silent(tmp_path: Path):
    transcript = tmp_path / "one.jsonl"
    _write(transcript, "assistant", "Implemented the parser and tests are running.")
    scans = 0

    def scanner(**_):
        nonlocal scans
        scans += 1
        return [_row(transcript)]

    runtime = SupervisorRuntime(tmp_path / "supervisor.sqlite", scanner=scanner)
    first = runtime.observe()
    second = runtime.observe()

    assert scans == 2
    assert first["counts"] == {
        "total": 1,
        "live": 1,
        "needs_attention": 0,
        "claude": 0,
        "cursor": 0,
        "codex": 1,
    }
    assert first["assessment"] is None
    assert second["assessment"] is None
    with runtime._connect() as conn:
        stored = conn.execute("SELECT cursor_json FROM observations").fetchone()
    assert json.loads(stored[0])["offset"] == transcript.stat().st_size


def test_runtime_surfaces_one_ranked_intervention_with_evidence(tmp_path: Path):
    approval = tmp_path / "approval.jsonl"
    failed = tmp_path / "failed.jsonl"
    _write(approval, "assistant", "Approval is needed before I can continue.")
    _write(failed, "assistant", "The build failed with exit 1.")

    def scanner(**_):
        return [
            _row(approval, state="approval_needed"),
            {**_row(failed, state="failed"), "id": "claude:two", "surface": "claude"},
        ]

    out = SupervisorRuntime(tmp_path / "s.sqlite", scanner=scanner).observe()

    assert out["counts"]["total"] == 2
    assert out["counts"]["needs_attention"] == 2
    assert len(out["interventions"]) == 2
    assert out["assessment"]["intervention_type"] == "approval_needed"
    assert out["assessment"]["session"]["id"] == "codex:local:one"
    assert out["assessment"]["evidence"]


def test_runtime_does_not_turn_recent_unknown_into_live(tmp_path: Path):
    transcript = tmp_path / "unknown.jsonl"
    _write(transcript, "assistant", "I am writing a fluent progress update.")

    def scanner(**_):
        return [{**_row(transcript, state="unknown"), "live": False}]

    out = SupervisorRuntime(tmp_path / "s.sqlite", scanner=scanner).observe()
    assert out["sessions"][0]["state"] == "unknown"
    assert out["sessions"][0]["live"] is False
    assert out["assessment"] is None


def test_runtime_uses_at_most_one_model_judgment_per_sweep(tmp_path: Path):
    one = tmp_path / "one.jsonl"
    two = tmp_path / "two.jsonl"
    _write(one, "assistant", "Approval requested.")
    _write(two, "assistant", "Build stopped.")
    calls = 0

    def scanner(**_):
        return [
            _row(one, state="approval_needed"),
            {**_row(two, state="failed"), "id": "claude:two", "surface": "claude"},
        ]

    def judge(_prompt: str):
        nonlocal calls
        calls += 1
        return {
            "goal": "Resolve the earned intervention",
            "verified_progress": [],
            "blocker_or_decision": "Approval is required.",
            "recommended_next_action": "Approve or reject the pending action.",
            "evidence": ["provider lifecycle"],
            "confidence": 0.9,
            "intervention_type": "approval_needed",
            "intervention_reason": "Approval is required.",
            "ticket_disposition": "continue",
            "should_intervene": True,
        }

    out = SupervisorRuntime(tmp_path / "s.sqlite", scanner=scanner, judge=judge).observe()
    assert calls == 1
    assert out["sessions"][0]["assessment"]["judgment_source"] == "model"
    assert out["sessions"][0]["assessment"]["judgment_provider"] == "claude"
    assert out["sessions"][1]["assessment"]["judgment_source"] == "deterministic"


def test_runtime_never_sends_raw_transcript_secrets_to_the_judge(tmp_path: Path):
    transcript = tmp_path / "secret.jsonl"
    _write(transcript, "assistant", "Approval requested. token=super-secret-value-123")
    prompts: list[str] = []

    def judge(prompt: str):
        prompts.append(prompt)
        return {
            "goal": "Resolve approval",
            "verified_progress": [],
            "blocker_or_decision": "Approval is required.",
            "recommended_next_action": "Approve or reject it.",
            "evidence": ["lifecycle"],
            "confidence": 0.9,
            "intervention_type": "approval_needed",
            "intervention_reason": "Approval is required.",
            "ticket_disposition": "continue",
            "should_intervene": True,
        }

    SupervisorRuntime(
        tmp_path / "s.sqlite", scanner=lambda **_: [_row(transcript, state="approval_needed")], judge=judge
    ).observe()
    assert len(prompts) == 1
    assert "super-secret-value-123" not in prompts[0]
    assert "[redacted]" in prompts[0]


def test_force_refresh_does_not_rejudge_unchanged_session(tmp_path: Path):
    transcript = tmp_path / "one.jsonl"
    _write(transcript, "assistant", "Approval requested.")
    calls = 0

    def judge(_prompt: str):
        nonlocal calls
        calls += 1
        raise RuntimeError("provider unavailable")

    runtime = SupervisorRuntime(
        tmp_path / "s.sqlite",
        scanner=lambda **_: [_row(transcript, state="approval_needed")],
        judge=judge,
    )
    first = runtime.observe()
    runtime.observe(force=True)
    assert calls == 1
    assert first["assessment"]["judgment_source"] == "deterministic"


def test_runtime_reassesses_when_unchanged_work_crosses_stale_threshold(tmp_path: Path):
    transcript = tmp_path / "one.jsonl"
    _write(transcript, "assistant", "Still need to implement reconnect.")
    clock = [150.0]

    def scanner(**_):
        return [
            {
                **_row(transcript, state="unknown"),
                "live": False,
                "mtime": 100.0,
            }
        ]

    runtime = SupervisorRuntime(
        tmp_path / "s.sqlite", scanner=scanner, stale_after_seconds=100
    )
    with patch("alicia.supervisor_runtime.time.time", side_effect=lambda: clock[0]):
        assert runtime.observe()["assessment"] is None
        clock[0] = 250.0
        assert runtime.observe()["assessment"]["intervention_type"] == "stale"
