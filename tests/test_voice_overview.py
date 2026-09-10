from pathlib import Path

from test_supervisor_runtime import _row, _write

from brutus.server import _supervisor_signature
from brutus.supervisor_runtime import SupervisorRuntime


def test_visual_progress_changes_publish_without_a_spoken_intervention():
    before = {"sessions": [{"id": "one", "assessment": {"goal": "Build"}}], "assessment": None}
    after = {"sessions": [{"id": "one", "assessment": {"goal": "Build", "verified_progress": ["Tests passed"]}}], "assessment": None}
    assert _supervisor_signature(before) != _supervisor_signature(after)
    assert _supervisor_signature(after) == _supervisor_signature({**after, "observed_at": 1234})


def test_snapshot_never_waits_for_summary_lock_or_calls_provider(tmp_path):
    source = tmp_path / "session.jsonl"
    _write(source, "assistant", "Checking layout.")
    def judge(_):
        raise AssertionError("A UI read must never call a model")
    runtime = SupervisorRuntime(tmp_path / "s.sqlite", scanner=lambda **_: [_row(source)], judge=judge)
    with runtime._lock:
        result = runtime.snapshot()
    assert result["sessions"][0]["title"] == "Build voice surface"
    assert result["counts"]["total"] == 1


def test_ordinary_sessions_receive_summaries_without_interruptions(tmp_path: Path):
    one, two = tmp_path / "one.jsonl", tmp_path / "two.jsonl"
    _write(one, "assistant", "The parser tests passed.")
    _write(two, "assistant", "The layout tests passed.")
    calls = []

    def judge(prompt):
        calls.append(prompt)
        return {
            "goal": "Keep the conversation readable",
            "verified_progress": ["Focused tests passed."],
            "blocker_or_decision": "No blocker.",
            "recommended_next_action": "Check narrow windows.",
            "evidence": ["Focused test result in the transcript."],
            "confidence": 0.8,
            "intervention_type": "none",
            "intervention_reason": "Ordinary progress.",
            "ticket_disposition": "none",
            "should_intervene": False,
        }

    runtime = SupervisorRuntime(tmp_path / "summary.sqlite", judge=judge, scanner=lambda **_: [
        _row(one), {**_row(two), "id": "cursor:two", "surface": "cursor"},
    ])
    first = runtime.observe()
    assert len(calls) == 1
    assert first["assessment"] is None
    second = runtime.observe()
    assert len(calls) == 2
    assert all(s["assessment"]["judgment_source"] == "model" for s in second["sessions"])
    assert second["interventions"] == []
    runtime.observe(force=True)
    assert len(calls) == 2
