"""Evidence-gated judgment for normalized agent sessions.

This module deliberately has no knowledge of transcript storage, HTTP, tools, or
providers.  Callers normalize those concerns before passing a session here.
The language-model seam improves the wording and work judgment; deterministic
policy owns whether Brutus is allowed to interrupt.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, replace
from typing import Any, Literal

TicketDisposition = Literal[
    "continue", "update_existing", "new_ticket", "frontier", "none"
]
InterventionType = Literal[
    "approval_needed",
    "blocked",
    "failed",
    "stale",
    "conflict_or_duplicate",
    "completed_followup",
    "none",
]

_TICKET_DISPOSITIONS = {
    "continue", "update_existing", "new_ticket", "frontier", "none"
}
_INTERVENTION_TYPES = {
    "approval_needed",
    "blocked",
    "failed",
    "stale",
    "conflict_or_duplicate",
    "completed_followup",
    "none",
}
_TERMINAL = {"completed", "complete", "done", "success", "succeeded"}
_APPROVAL = {"approval_needed", "awaiting_approval", "needs_approval"}
_BLOCKED = {"blocked", "waiting_for_input", "awaiting_input", "needs_input"}
_FAILED = {"failed", "failure", "error", "errored", "aborted"}
_UNFINISHED_RE = re.compile(
    r"\b(?:todo|still (?:need|needs|missing|failing)|not (?:done|finished|implemented)|"
    r"remaining work|next step|unresolved|unfinished|pending)\b",
    re.IGNORECASE,
)
_FOLLOWUP_RE = re.compile(
    r"\b(?:follow[- ]?up|required next step|still need|needs? (?:review|merge|deploy|verification)|"
    r"open (?:a |the )?(?:pr|ticket)|create (?:a |the )?ticket|ready for review)\b",
    re.IGNORECASE,
)
_SENSITIVE_PATTERNS = (
    # Explicit credentials and auth headers. Keep the surrounding sentence so
    # work-state signals such as "failed" and "approval" still survive.
    (re.compile(r"\b(?:sk-[A-Za-z0-9_-]{16,}|gh[pousr]_[A-Za-z0-9_]{16,}|github_pat_[A-Za-z0-9_]{16,}|AKIA[0-9A-Z]{16}|xox[baprs]-[A-Za-z0-9-]{12,})\b"), "[redacted credential]"),
    (re.compile(r"(?i)(\b(?:authorization|bearer)\s*[: ]\s*)([^\s,;]{8,})"), r"\1[redacted]"),
    (re.compile(r"(?i)(\b(?:api[_ -]?key|token|secret|password|passwd)\s*[=:]\s*)([^\s,;]{6,})"), r"\1[redacted]"),
    (re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"), "[redacted email]"),
)


class AssessmentValidationError(ValueError):
    """A proposed assessment does not satisfy the strict output contract."""


def redact_supervisor_transcript(value: str | Sequence[str]) -> str:
    """Remove common secrets and personal email before a transcript reaches a model.

    This is intentionally a narrow transport boundary, not a lossy transcript
    sanitizer: local cursors still advance over the original bytes and the
    deterministic lifecycle scanner remains authoritative.
    """
    text = _normalize_delta(value)
    for pattern, replacement in _SENSITIVE_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


@dataclass(frozen=True)
class NormalizedSession:
    id: str
    provider: str
    status: str
    title: str = ""
    cwd: str = ""
    age_seconds: float | None = None
    stale: bool = False
    needs_approval: bool = False
    blocked: bool = False
    failed: bool = False
    conflict_or_duplicate: bool = False
    completed_followup: bool = False
    existing_ticket: str = ""

    @classmethod
    def from_record(cls, record: Mapping[str, Any] | NormalizedSession) -> NormalizedSession:
        if isinstance(record, cls):
            return record
        if not isinstance(record, Mapping):
            raise TypeError("session must be a mapping or NormalizedSession")
        return cls(
            id=_required_text(record, "id", "session_id", "agent_id"),
            provider=_required_text(record, "provider", "source"),
            status=_required_text(record, "status", "state").casefold(),
            title=_first_text(record, "title", "name", "goal"),
            cwd=_first_text(record, "cwd", "workspace", "project_path"),
            age_seconds=_optional_float(record.get("age_seconds")),
            stale=bool(record.get("stale", False)),
            needs_approval=bool(record.get("needs_approval", False)),
            blocked=bool(record.get("blocked", False)),
            failed=bool(record.get("failed", False)),
            conflict_or_duplicate=bool(
                record.get("conflict_or_duplicate", record.get("duplicated_work", False))
            ),
            completed_followup=bool(record.get("completed_followup", False)),
            existing_ticket=_first_text(record, "existing_ticket", "ticket_id", "work_item"),
        )


@dataclass(frozen=True)
class SessionAssessment:
    goal: str
    verified_progress: tuple[str, ...]
    blocker_or_decision: str
    recommended_next_action: str
    evidence: tuple[str, ...]
    confidence: float
    intervention_type: InterventionType
    intervention_reason: str
    ticket_disposition: TicketDisposition
    should_intervene: bool
    judgment_source: str = "deterministic"
    judgment_profile: str = "policy"
    judgment_provider: str = "none"

    def __post_init__(self) -> None:
        for field_name in (
            "goal", "blocker_or_decision", "recommended_next_action", "intervention_reason"
        ):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise AssessmentValidationError(f"{field_name} must be a non-empty string")
        if not isinstance(self.verified_progress, tuple) or not all(
            isinstance(item, str) and item.strip() for item in self.verified_progress
        ):
            raise AssessmentValidationError("verified_progress must be a tuple of non-empty strings")
        if not isinstance(self.evidence, tuple) or not all(
            isinstance(item, str) and item.strip() for item in self.evidence
        ):
            raise AssessmentValidationError("evidence must be a tuple of non-empty strings")
        if self.intervention_type not in _INTERVENTION_TYPES:
            raise AssessmentValidationError("invalid intervention_type")
        if self.ticket_disposition not in _TICKET_DISPOSITIONS:
            raise AssessmentValidationError("invalid ticket_disposition")
        if isinstance(self.confidence, bool) or not isinstance(self.confidence, (int, float)):
            raise AssessmentValidationError("confidence must be numeric")
        if not 0.0 <= float(self.confidence) <= 1.0:
            raise AssessmentValidationError("confidence must be between 0 and 1")
        if self.should_intervene != (self.intervention_type != "none"):
            raise AssessmentValidationError("should_intervene must match intervention_type")
        if self.judgment_source not in {"deterministic", "model"}:
            raise AssessmentValidationError("invalid judgment_source")

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


Judge = Callable[[str], Mapping[str, Any] | str]


def assess_session(
    session: Mapping[str, Any] | NormalizedSession,
    transcript_delta: str | Sequence[str] = "",
    evidence: Sequence[str | Mapping[str, Any]] = (),
    *,
    judge: Judge | None = None,
    stale_after_seconds: float = 45 * 60,
) -> SessionAssessment:
    """Assess one session while keeping intervention authority deterministic.

    ``judge`` receives a judgment-oriented prompt and returns either a mapping or
    a JSON object string. Invalid or unavailable model output falls back to a
    deterministic assessment. The model cannot turn ordinary progress into an
    interruption, nor suppress a policy-backed intervention.
    """
    normalized = NormalizedSession.from_record(session)
    delta = _normalize_delta(transcript_delta)
    evidence_lines = _normalize_evidence(evidence)
    signal = _intervention_signal(normalized, delta, evidence_lines, stale_after_seconds)
    fallback = _fallback_assessment(normalized, delta, evidence_lines, signal)
    if judge is None:
        return fallback
    try:
        raw = judge(build_judgment_prompt(normalized, delta, evidence_lines, signal))
        candidate = _parse_model_assessment(raw)
    # The injected seam may be a network-backed provider and can fail with
    # provider-specific exception classes. Assessment must remain available.
    except Exception:  # noqa: BLE001
        return fallback
    return _enforce_policy(
        replace(candidate, judgment_source="model", judgment_profile="supervisor"),
        fallback,
        signal,
    )


def build_judgment_prompt(
    session: NormalizedSession,
    transcript_delta: str,
    evidence: tuple[str, ...],
    signal: InterventionType,
) -> str:
    """Build a prompt for work judgment, explicitly excluding transcript summary."""
    schema = {
        "goal": "string",
        "verified_progress": ["evidence-backed fact"],
        "blocker_or_decision": "string",
        "recommended_next_action": "one concrete action",
        "evidence": ["compact evidence reference"],
        "confidence": 0.0,
        "intervention_type": signal,
        "intervention_reason": "why interruption is or is not warranted",
        "ticket_disposition": "continue|update_existing|new_ticket|frontier|none",
        "should_intervene": signal != "none",
    }
    return (
        "Act as a work supervisor, not a transcript summarizer. Determine what was actually "
        "verified, what decision or blocker exists, and the single action that advances the work. "
        "Do not produce status bullets, narration, praise, or generic advice. Do not infer facts "
        "absent from the supplied evidence. Return exactly one JSON object matching the schema. "
        "Write goal as a short plain-language statement of the user's intended outcome. "
        "Use concise complete sentences for progress and next action. Distinguish agent-reported "
        "activity from verified results; never turn a claim of completion into verification. "
        "The policy intervention_type and should_intervene values are fixed and must be preserved.\n"
        f"SCHEMA: {json.dumps(schema, sort_keys=True)}\n"
        f"SESSION: {json.dumps(asdict(session), sort_keys=True)}\n"
        f"POLICY_SIGNAL: {signal}\n"
        f"TRANSCRIPT_DELTA: {transcript_delta or '[none]'}\n"
        f"EVIDENCE: {json.dumps(evidence)}"
    )


def _intervention_signal(
    session: NormalizedSession,
    delta: str,
    evidence: tuple[str, ...],
    stale_after_seconds: float,
) -> InterventionType:
    status = session.status
    combined = "\n".join((delta, *evidence))
    # User prompts frequently discuss failure, blockers, and approval as the
    # subject of work. When roles are available, only agent-authored transcript
    # lines may establish those signals; lifecycle evidence remains included.
    role_lines = combined.splitlines()
    has_roles = any(line.startswith(("user:", "assistant:")) for line in role_lines)
    decision_text = "\n".join(
        line for line in role_lines if not has_roles or not line.startswith("user:")
    )
    if session.needs_approval or status in _APPROVAL:
        return "approval_needed"
    if session.failed or status in _FAILED:
        return "failed"
    if session.blocked or status in _BLOCKED:
        return "blocked"
    # Transcript words alone do not establish approval, failure, blockage, or
    # conflicting ownership. They are common subjects of implementation work.
    # Those interventions require normalized lifecycle/overlay evidence above.
    if session.conflict_or_duplicate:
        return "conflict_or_duplicate"
    completed = status in _TERMINAL
    if completed and (session.completed_followup or _FOLLOWUP_RE.search(decision_text)):
        return "completed_followup"
    is_stale = session.stale or (
        session.age_seconds is not None and session.age_seconds >= stale_after_seconds
    )
    if is_stale and bool(_UNFINISHED_RE.search(decision_text)):
        return "stale"
    return "none"


def _fallback_assessment(
    session: NormalizedSession,
    delta: str,
    evidence: tuple[str, ...],
    signal: InterventionType,
) -> SessionAssessment:
    goal = session.title or f"Advance {session.provider} session {session.id}"
    cited = evidence or ((delta[:240],) if delta else ())
    messages = {
        "approval_needed": (
            "A human approval is required before the session can continue.",
            "Review the pending action and approve or reject it.",
            "continue",
        ),
        "failed": (
            "The session reports a failure that stops the current path.",
            "Inspect the failing evidence and decide whether to retry, repair, or redirect.",
            "continue",
        ),
        "blocked": (
            "The session is waiting on an input or decision.",
            "Answer the blocking question with the smallest decision that unblocks the work.",
            "continue",
        ),
        "conflict_or_duplicate": (
            "Evidence indicates overlapping or conflicting work.",
            "Choose one owner and reconcile the overlapping changes before either session continues.",
            "update_existing" if session.existing_ticket else "continue",
        ),
        "completed_followup": (
            "The implementation completed but an explicit follow-up remains.",
            "Complete or assign the named follow-up.",
            "update_existing" if session.existing_ticket else "new_ticket",
        ),
        "stale": (
            "The session is stale and contains evidence of unfinished work.",
            "Resume the session with the unresolved next step or close it explicitly.",
            "continue",
        ),
        "none": (
            "No evidence-backed blocker or decision requires attention.",
            "Let the session continue without interruption.",
            "none",
        ),
    }
    blocker, action, disposition = messages[signal]
    return SessionAssessment(
        goal=goal,
        verified_progress=(),
        blocker_or_decision=blocker,
        recommended_next_action=action,
        evidence=cited,
        confidence=0.9 if signal != "none" else 0.8,
        intervention_type=signal,
        intervention_reason=blocker,
        ticket_disposition=disposition,  # type: ignore[arg-type]
        should_intervene=signal != "none",
    )


def _parse_model_assessment(raw: Mapping[str, Any] | str) -> SessionAssessment:
    if isinstance(raw, str):
        value = raw.strip()
        if value.startswith("```") and value.endswith("```"):
            value = re.sub(r"^```(?:json)?\s*", "", value, flags=re.IGNORECASE)
            value = re.sub(r"\s*```$", "", value).strip()
        obj = json.loads(value)
    elif isinstance(raw, Mapping):
        obj = dict(raw)
    else:
        raise AssessmentValidationError("judge output must be a mapping or JSON object")
    required = {
        "goal",
        "verified_progress",
        "blocker_or_decision",
        "recommended_next_action",
        "evidence",
        "confidence",
        "intervention_type",
        "intervention_reason",
        "ticket_disposition",
        "should_intervene",
    }
    if set(obj) != required:
        missing = required - set(obj)
        extra = set(obj) - required
        raise AssessmentValidationError(f"judge output schema mismatch; missing={missing}, extra={extra}")
    if not isinstance(obj["verified_progress"], list) or not isinstance(obj["evidence"], list):
        raise AssessmentValidationError("judge list fields must be JSON arrays")
    if not isinstance(obj["should_intervene"], bool):
        raise AssessmentValidationError("should_intervene must be boolean")
    return SessionAssessment(
        goal=obj["goal"],
        verified_progress=tuple(obj["verified_progress"]),
        blocker_or_decision=obj["blocker_or_decision"],
        recommended_next_action=obj["recommended_next_action"],
        evidence=tuple(obj["evidence"]),
        confidence=obj["confidence"],
        intervention_type=obj["intervention_type"],
        intervention_reason=obj["intervention_reason"],
        ticket_disposition=obj["ticket_disposition"],
        should_intervene=obj["should_intervene"],
    )


def _enforce_policy(
    candidate: SessionAssessment,
    fallback: SessionAssessment,
    signal: InterventionType,
) -> SessionAssessment:
    # The judge may enrich substance, never intervention authority. Also avoid a
    # ticket mutation recommendation when policy found no reason to interrupt.
    disposition: TicketDisposition = candidate.ticket_disposition
    if signal == "none":
        disposition = "none"
    elif disposition == "none":
        disposition = fallback.ticket_disposition
    return replace(
        candidate,
        intervention_type=signal,
        should_intervene=signal != "none",
        intervention_reason=(
            candidate.intervention_reason if signal != "none" else fallback.intervention_reason
        ),
        ticket_disposition=disposition,
    )


def _normalize_delta(value: str | Sequence[str]) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, Sequence):
        return "\n".join(str(item).strip() for item in value if str(item).strip())
    raise TypeError("transcript_delta must be a string or sequence of strings")


def _normalize_evidence(values: Sequence[str | Mapping[str, Any]]) -> tuple[str, ...]:
    if isinstance(values, (str, bytes)):
        raise TypeError("evidence must be a sequence, not a single string")
    out: list[str] = []
    for item in values:
        if isinstance(item, str):
            text = item.strip()
        elif isinstance(item, Mapping):
            text = json.dumps(dict(item), sort_keys=True, default=str)
        else:
            raise TypeError("each evidence item must be a string or mapping")
        if text:
            out.append(text)
    return tuple(out)


def _required_text(record: Mapping[str, Any], *keys: str) -> str:
    value = _first_text(record, *keys)
    if not value:
        raise ValueError(f"session requires one of: {', '.join(keys)}")
    return value


def _first_text(record: Mapping[str, Any], *keys: str) -> str:
    for key in keys:
        value = record.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def _optional_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    result = float(value)
    if result < 0:
        raise ValueError("age_seconds cannot be negative")
    return result
