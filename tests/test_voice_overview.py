from pathlib import Path

from test_supervisor_runtime import _row, _write

from brutus.supervisor_runtime import SupervisorRuntime


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
