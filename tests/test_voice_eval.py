from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

from alicia.conversation import TurnResult
from alicia.voice_eval import VoiceScenario, VoiceTurn, evaluate_turn, run_scenarios


def _result(spoken: str, *, tool: str | None = None) -> TurnResult:
    return TurnResult("s", "deep", spoken, spoken, 1, tool=tool)


def test_eval_catches_filler_length_machine_speech_and_missing_tool():
    row = evaluate_turn(
        VoiceTurn("status", expect_tool="get_work_surface"),
        _result("Certainly. See https://example.com/" + " word" * 80),
        1.2,
    )
    assert row["pass"] is False
    assert set(row["failures"]) >= {
        "corporate_filler_opener",
        "over_75_spoken_words",
        "machine_furniture_spoken",
        "expected_tool:get_work_surface:got:none",
    }


def test_eval_requires_silence_when_user_asks_for_it():
    assert evaluate_turn(VoiceTurn("wait", must_be_silent=True), _result("Okay."), 0.1)["pass"] is False
    assert evaluate_turn(VoiceTurn("wait", must_be_silent=True), _result(""), 0.1)["pass"] is True


def test_eval_catches_semantically_unhelpful_follow_up():
    row = evaluate_turn(
        VoiceTurn(
            "what is left?",
            require_any=("voice", "conversation"),
            forbid=("which workstream",),
        ),
        _result("Which workstream?"),
        0.1,
    )
    assert row["pass"] is False
    assert set(row["failures"]) == {
        "missing_required_meaning:voice|conversation",
        "forbidden_unhelpful_phrase:which workstream",
    }


def test_eval_does_not_score_backend_failure_as_a_good_short_answer():
    row = evaluate_turn(VoiceTurn("status"), _result("Can't reach a brain right now."), 0.1)
    assert row["pass"] is False
    assert row["failures"] == ["backend_failure_narrated"]


def test_eval_rejects_credential_implementation_noise():
    row = evaluate_turn(
        VoiceTurn("status"),
        _result("The API key is missing from the 1Password vault."),
        0.1,
    )
    assert row["pass"] is False
    assert row["failures"] == ["credential_noise_spoken"]


def test_scenario_runner_preserves_multi_turn_session():
    seen = []
    report = run_scenarios(
        lambda: "same-session",
        lambda sid, text: seen.append((sid, text)) or _result("Direct answer."),
        scenarios=(VoiceScenario("followup", (VoiceTurn("one"), VoiceTurn("two"))),),
    )
    assert report["passed"] == 1
    assert seen == [("same-session", "one"), ("same-session", "two")]


def _media_eval_module():
    path = Path(__file__).parents[1] / "scripts/eval-livekit-voice.py"
    spec = spec_from_file_location("eval_livekit_voice", path)
    assert spec and spec.loader
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_media_eval_reads_live_session_and_legacy_export_turn_text():
    module = _media_eval_module()
    assert module._turn_text({"role": "alicia", "text": "Current"}) == "Current"
    assert module._turn_text({"role": "assistant", "content": "Legacy"}) == "Legacy"


def test_media_eval_grader_requires_exact_transcripts_meaning_audio_and_latency():
    module = _media_eval_module()
    result = {
        "ok": True,
        "transcript": "What needs my attention today?",
        "reply": "REV-490 needs your decision.",
        "barge_in_transcript": "Stop. Say hello in one sentence.",
        "barge_in_reply": "Hey Justin.",
        "speech_to_first_audio_s": 9.349,
    }
    graded = module.grade_result(
        result,
        expected_transcript="What needs my attention today?",
        expected_barge_transcript="Stop say hello in one sentence",
        reply_requires=("REV-490",),
        barge_reply_requires=("Justin",),
        max_latency_s=12.0,
    )
    assert graded["ok"] is True
    assert graded["check_count"] == 6
    assert graded["passed_checks"] == 6


def test_media_eval_grader_fails_each_user_observable_regression():
    module = _media_eval_module()
    graded = module.grade_result(
        {
            "ok": False,
            "transcript": "wrong words",
            "reply": "A generic answer.",
            "barge_in_transcript": "",
            "barge_in_reply": "",
            "speech_to_first_audio_s": 30.0,
        },
        expected_transcript="What needs my attention today?",
        expected_barge_transcript="Stop say hello in one sentence",
        reply_requires=("REV-490",),
        barge_reply_requires=("Justin",),
        max_latency_s=12.0,
    )
    assert graded["ok"] is False
    assert graded["check_count"] == 6
    assert graded["passed_checks"] == 0
    assert {row["name"] for row in graded["checks"]} == {
        "transport_completed",
        "first_transcript_exact",
        "barge_transcript_exact",
        "first_reply_contains:REV-490",
        "barge_reply_contains:Justin",
        "speech_to_first_audio_within_budget",
    }
