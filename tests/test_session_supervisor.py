import json

import pytest

from alicia.session_supervisor import (
    AssessmentValidationError,
    NormalizedSession,
    SessionAssessment,
    assess_session,
    redact_supervisor_transcript,
)


def session(**changes):
    value = {
        "id": "codex:abc",
        "provider": "codex",
        "status": "running",
        "title": "Build the voice work surface",
        "age_seconds": 30,
    }
    value.update(changes)
    return value


def model_result(**changes):
    value = {
        "goal": "Ship the voice work surface",
        "verified_progress": ["Focused tests passed"],
        "blocker_or_decision": "No blocker",
        "recommended_next_action": "Continue implementation",
        "evidence": ["pytest: 12 passed"],
        "confidence": 0.86,
        "intervention_type": "none",
        "intervention_reason": "No human decision is required",
        "ticket_disposition": "none",
        "should_intervene": False,
    }
    value.update(changes)
    return value


@pytest.mark.parametrize("status", ["running", "active", "in_progress", "idle", "unknown"])
def test_ordinary_progress_stays_silent(status):
    result = assess_session(
        session(status=status),
        "Implemented the parser. Running the focused tests now.",
        ["edited alicia/parser.py", "pytest still running"],
    )

    assert result.should_intervene is False
    assert result.intervention_type == "none"


def test_running_lifecycle_beats_failure_words_in_transcript():
    result = assess_session(
        {"id": "claude:1", "provider": "claude", "status": "running", "title": "Audit failure handling"},
        "user: Inspect the failing evidence and decide whether failure recovery is correct.\nassistant: I am auditing the policy now.",
    )
    assert result.intervention_type == "none"
    assert result.should_intervene is False
    assert result.ticket_disposition == "none"
    assert "without interruption" in result.recommended_next_action


def test_running_session_repairing_failed_test_stays_silent():
    result = assess_session(
        session(status="running"),
        "assistant: The test command failed with exit 1; I am repairing it now.",
    )
    assert result.intervention_type == "none"


def test_supervisor_redacts_credentials_and_email_before_model_transport():
    safe = redact_supervisor_transcript(
        "assistant: deploy failed; token=super-secret-value-123 and email=justin@example.com "
        "Authorization: Bearer abcdefghijklmnop"
    )

    assert "super-secret-value-123" not in safe
    assert "justin@example.com" not in safe
    assert "abcdefghijklmnop" not in safe
    assert "deploy failed" in safe


def test_model_cannot_manufacture_interruption_for_ordinary_progress():
    result = assess_session(
        session(),
        "Implemented one module and moving to tests.",
        ["git diff shows expected edit"],
        judge=lambda _: model_result(
            intervention_type="blocked",
            intervention_reason="Interrupt anyway",
            ticket_disposition="new_ticket",
            should_intervene=True,
        ),
    )

    assert result.should_intervene is False
    assert result.intervention_type == "none"
    assert result.ticket_disposition == "none"
    assert result.intervention_reason == "No evidence-backed blocker or decision requires attention."


@pytest.mark.parametrize(
    ("record", "delta", "expected"),
    [
        ({"status": "approval_needed"}, "", "approval_needed"),
        ({"status": "blocked"}, "", "blocked"),
        ({"status": "failed"}, "", "failed"),
        (
            {"status": "running", "conflict_or_duplicate": True},
            "",
            "conflict_or_duplicate",
        ),
        (
            {"status": "completed", "completed_followup": True},
            "",
            "completed_followup",
        ),
        (
            {"status": "completed"},
            "Implementation is done but still needs review.",
            "completed_followup",
        ),
    ],
)
def test_policy_backed_trigger_intervenes(record, delta, expected):
    result = assess_session(session(**record), delta)

    assert result.should_intervene is True
    assert result.intervention_type == expected
    assert result.ticket_disposition != "none"


@pytest.mark.parametrize(
    "delta",
    [
        "This needs your approval before I can continue.",
        "Waiting for your decision on the API shape.",
        "The session failed with exit 1.",
        "Both sessions are already being worked against the same ticket.",
    ],
)
def test_transcript_words_without_lifecycle_evidence_stay_silent(delta):
    assert assess_session(session(status="unknown"), delta).intervention_type == "none"


def test_stale_requires_both_age_and_unfinished_evidence():
    old_but_clean = assess_session(
        session(age_seconds=7200),
        "Finished the requested change and verified it.",
        ["tests passed"],
    )
    fresh_but_unfinished = assess_session(
        session(age_seconds=10),
        "Still need to implement reconnect.",
    )
    stale_unfinished = assess_session(
        session(age_seconds=7200),
        "Still need to implement reconnect.",
        ["No transcript activity for two hours"],
    )

    assert old_but_clean.should_intervene is False
    assert fresh_but_unfinished.should_intervene is False
    assert stale_unfinished.intervention_type == "stale"
    assert stale_unfinished.should_intervene is True


def test_completed_without_explicit_followup_stays_silent():
    result = assess_session(
        session(status="completed", age_seconds=7200),
        "Implemented the change. All focused tests passed.",
        ["pytest: 18 passed"],
    )

    assert result.should_intervene is False
    assert result.intervention_type == "none"


def test_model_receives_judgment_prompt_and_can_enrich_assessment():
    prompts = []

    def judge(prompt):
        prompts.append(prompt)
        return json.dumps(
            model_result(
                blocker_or_decision="Choose whether to grant filesystem access",
                recommended_next_action="Approve the scoped filesystem request",
                intervention_type="approval_needed",
                intervention_reason="The agent cannot proceed without approval",
                ticket_disposition="continue",
                should_intervene=True,
            )
        )

    result = assess_session(
        session(status="approval_needed"),
        "Approval needed for the scoped filesystem operation.",
        ["tool request: write /tmp/result.json"],
        judge=judge,
    )

    assert result.recommended_next_action == "Approve the scoped filesystem request"
    assert result.verified_progress == ("Focused tests passed",)
    assert "work supervisor, not a transcript summarizer" in prompts[0]
    assert '"intervention_type": "approval_needed"' in prompts[0]
    assert "status bullets" in prompts[0]


@pytest.mark.parametrize(
    "bad_output",
    [
        "not json",
        {},
        model_result(confidence=2),
        model_result(verified_progress="a summary"),
        model_result(extra="not allowed"),
    ],
)
def test_invalid_model_output_falls_back_deterministically(bad_output):
    result = assess_session(
        session(status="blocked"),
        "Waiting for your input on the schema.",
        judge=lambda _: bad_output,
    )

    assert result.intervention_type == "blocked"
    assert result.should_intervene is True
    assert result.recommended_next_action.startswith("Answer the blocking question")


def test_model_exception_falls_back_deterministically():
    def unavailable(_prompt):
        raise RuntimeError("provider unavailable")

    result = assess_session(session(status="approval_needed"), judge=unavailable)

    assert result.intervention_type == "approval_needed"
    assert result.should_intervene is True


def test_model_cannot_suppress_required_intervention():
    result = assess_session(
        session(status="failed"),
        "Build failed.",
        judge=lambda _: model_result(),
    )

    assert result.intervention_type == "failed"


def test_strict_model_contract_accepts_one_json_markdown_fence():
    payload = model_result(
        intervention_type="failed",
        should_intervene=True,
        ticket_disposition="continue",
    )
    result = assess_session(
        session(status="failed"),
        judge=lambda _: f"```json\n{json.dumps(payload)}\n```",
    )
    assert result.judgment_source == "model"
    assert result.intervention_type == "failed"
    assert result.should_intervene is True
    assert result.ticket_disposition == "continue"


def test_completed_followup_ticket_disposition_uses_existing_ticket_when_known():
    existing = assess_session(
        session(status="completed", completed_followup=True, ticket_id="REV-490")
    )
    new = assess_session(session(status="completed", completed_followup=True))

    assert existing.ticket_disposition == "update_existing"
    assert new.ticket_disposition == "new_ticket"


def test_evidence_mappings_are_stable_json_and_exposed():
    result = assess_session(
        session(status="blocked"),
        evidence=[{"source": "tool", "exit_code": 1}],
    )

    assert result.evidence == ('{"exit_code": 1, "source": "tool"}',)


def test_normalized_aliases_and_strict_input_validation():
    normalized = NormalizedSession.from_record(
        {"agent_id": "cursor:1", "source": "cursor", "state": "running", "name": "Work"}
    )
    assert normalized.id == "cursor:1"
    assert normalized.provider == "cursor"
    assert normalized.title == "Work"

    with pytest.raises(ValueError):
        NormalizedSession.from_record({"provider": "codex", "status": "running"})
    with pytest.raises(ValueError):
        NormalizedSession.from_record(session(age_seconds=-1))


def test_assessment_contract_rejects_inconsistent_intervention():
    with pytest.raises(AssessmentValidationError):
        SessionAssessment(
            goal="Work",
            verified_progress=(),
            blocker_or_decision="None",
            recommended_next_action="Continue",
            evidence=(),
            confidence=0.5,
            intervention_type="none",
            intervention_reason="None",
            ticket_disposition="none",
            should_intervene=True,
        )
