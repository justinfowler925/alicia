"""Pydantic models for the 8 canonical objects (REV-510 spec, v1).

Field lists match the accepted spec document exactly. Enums are the
versioned contract referenced by REV-513's acceptance criterion
"Object definitions and required fields are versioned" — add a numbered
SQLite migration under `alicia/canon/migrations/` whenever persisted storage
must change.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, field_validator


def _new_id() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class InboxStatus(str, Enum):
    UNCATEGORIZED = "uncategorized"
    REVIEWED = "reviewed"
    PROMOTED = "promoted"
    DISCARDED = "discarded"


class ProjectStatus(str, Enum):
    ACTIVE = "active"
    PAUSED = "paused"
    ARCHIVED = "archived"


class WorkItemType(str, Enum):
    TASK = "task"
    DECISION = "decision"
    INVESTIGATION = "investigation"
    POLICY = "policy"
    COMMUNICATION = "communication"


class WorkItemState(str, Enum):
    TRIAGE = "triage"
    CLARIFICATION = "clarification"
    PLANNING = "planning"
    DECISION = "decision"
    EXECUTION = "execution"
    VALIDATION = "validation"
    REVIEW = "review"
    ACCEPTANCE = "acceptance"
    MONITORING = "monitoring"
    CLOSURE = "closure"
    # side states, reachable from any non-terminal state above
    BLOCKED = "blocked"
    CANCELED = "canceled"
    SUPERSEDED = "superseded"


TERMINAL_STATES = {WorkItemState.CLOSURE, WorkItemState.CANCELED, WorkItemState.SUPERSEDED}

# Linear progression of the standard "happy path". Side states (blocked/
# canceled/superseded) are reachable from any non-terminal state and are
# handled separately in state_machine.transition. The state machine also has
# an explicitly audited, low-risk TASK-only shortcut from triage or planning
# to execution; it is intentionally not represented here as a silent edge.
HAPPY_PATH: list[WorkItemState] = [
    WorkItemState.TRIAGE,
    WorkItemState.CLARIFICATION,
    WorkItemState.PLANNING,
    WorkItemState.DECISION,
    WorkItemState.EXECUTION,
    WorkItemState.VALIDATION,
    WorkItemState.REVIEW,
    WorkItemState.ACCEPTANCE,
    WorkItemState.MONITORING,
    WorkItemState.CLOSURE,
]


class EvidenceType(str, Enum):
    LOG = "log"
    SCREENSHOT = "screenshot"
    DIFF = "diff"
    RUN_OUTPUT = "run_output"
    DOC_LINK = "doc_link"
    EXTERNAL_URL = "external_url"


class RunStatus(str, Enum):
    # Worker/agent-settable only. accepted/validated/deployed/closed are
    # intentionally absent from this enum — see state_machine.py.
    IMPLEMENTATION_ATTEMPTED = "implementation_attempted"
    BLOCKED = "blocked"
    FAILED = "failed"
    READY_FOR_REVIEW = "ready_for_review"


class ProveVerdict(str, Enum):
    """Persisted outcome of the Canon Hands/Prove receipt checks."""

    PASS = "PASS"
    FAIL = "FAIL"
    UNSURE = "UNSURE"


class ApprovalStatus(str, Enum):
    PENDING = "pending"
    GRANTED = "granted"
    DENIED = "denied"
    REVOKED = "revoked"


# ---------------------------------------------------------------------------
# Objects
# ---------------------------------------------------------------------------


class InboxItem(BaseModel):
    id: str = Field(default_factory=_new_id)
    raw_capture: str  # verbatim text/attachment refs — never edited, only annotated
    source: str  # channel, capturer, timestamp packed as a string/JSON by caller
    received_at: datetime = Field(default_factory=_now)
    status: InboxStatus = InboxStatus.UNCATEGORIZED


class Project(BaseModel):
    id: str = Field(default_factory=_new_id)
    name: str
    objective: str
    owner: str
    created_at: datetime = Field(default_factory=_now)
    status: ProjectStatus = ProjectStatus.ACTIVE
    work_item_ids: list[str] = Field(default_factory=list)


class StateHistoryEntry(BaseModel):
    state: WorkItemState
    actor: str
    time: datetime = Field(default_factory=_now)
    reason: str = ""
    evidence_ref: Optional[str] = None


class WorkItem(BaseModel):
    id: str = Field(default_factory=_new_id)
    title: str
    description: str = ""
    project_id: Optional[str] = None
    origin: str = "direct"  # inbox_item_id or "direct"
    type: WorkItemType = WorkItemType.TASK
    priority: int = 0
    assignee: Optional[str] = None

    state: WorkItemState = WorkItemState.TRIAGE
    # This is the timestamp for the state currently held in ``state``. The
    # append-only history remains the detailed audit trail; this field makes
    # the initial triage state ageable before its first transition.
    state_entered_at: Optional[datetime] = Field(default_factory=_now)
    state_history: list[StateHistoryEntry] = Field(default_factory=list)

    evidence_refs: list[str] = Field(default_factory=list)
    approval_refs: list[str] = Field(default_factory=list)
    decision_refs: list[str] = Field(default_factory=list)

    # Cross-surface identity and repository-delivery contract. These fields
    # live on the canonical Work Item so routing adapters and execution
    # surfaces do not need a second binding database.
    bindings: dict[str, str] = Field(default_factory=dict)
    repository_id: str = ""
    repository_path: str = ""
    worktree_path: str = ""
    contract_id: str = ""
    delivery_policy_ref: str = ""
    delivery_policy_digest: str = ""
    delivery_policy_profile: str = ""
    delivery_requirements: list[str] = Field(default_factory=list)
    delivery_freshness_hours: dict[str, float] = Field(default_factory=dict)
    delivery_targets: dict[str, str] = Field(default_factory=dict)
    target_artifact_digest: str = ""
    feedback_refs: list[str] = Field(default_factory=list)


class Decision(BaseModel):
    id: str = Field(default_factory=_new_id)
    question: str
    options_considered: list[str] = Field(default_factory=list)
    chosen_option: str
    rationale: str
    decided_by: str
    decided_at: datetime = Field(default_factory=_now)
    linked_work_item_ids: list[str] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)


class Evidence(BaseModel):
    id: str = Field(default_factory=_new_id)
    type: EvidenceType
    captured_at: datetime = Field(default_factory=_now)
    captured_by: str  # human or worker identity
    captured_by_kind: str = "human"  # "human" | "worker" — tagged distinctly per spec
    linked_object_id: str
    content_ref: str
    verified: bool = False
    verified_by: Optional[str] = None
    source_repository: Optional[str] = None
    source_object_id: Optional[str] = None
    source_sha: Optional[str] = None
    source_delivery_id: Optional[str] = None
    # Structured receipt/event metadata. Optional defaults keep all evidence
    # written before workflow-control v1 readable.
    requirement_id: Optional[str] = None
    result: Optional[str] = None
    target: Optional[str] = None
    artifact_digest: Optional[str] = None
    event_type: Optional[str] = None
    surface: Optional[str] = None
    metadata: dict = Field(default_factory=dict)


class Run(BaseModel):
    id: str = Field(default_factory=_new_id)
    actor: str  # worker/agent identity
    work_item_id: str
    started_at: datetime = Field(default_factory=_now)
    ended_at: Optional[datetime] = None
    status: RunStatus = RunStatus.IMPLEMENTATION_ATTEMPTED
    # Optional preserves compatibility with Runs persisted before REV-518.
    # Its values intentionally mirror alicia_stack.types.Verdict without
    # making the canonical model depend on the portable conversation package.
    prove_verdict: Optional[ProveVerdict] = None
    target: str = ""
    scope: str = ""
    evidence_refs: list[str] = Field(default_factory=list)


class Approval(BaseModel):
    id: str = Field(default_factory=_new_id)
    work_item_id: Optional[str] = None
    run_id: Optional[str] = None
    requested_by: str
    approved_by: Optional[str] = None  # must be the accountable human owner, never a worker/agent
    scope: str  # exact action approved, not a blanket grant
    granted_at: Optional[datetime] = None
    expires_at: Optional[datetime] = None
    status: ApprovalStatus = ApprovalStatus.PENDING


class ExecutionCardStatus(str, Enum):
    DRAFT = "draft"
    SEALED = "sealed"
    REVOKED = "revoked"


class ExecutionCard(BaseModel):
    """Immutable dispatch payload for write-capable work.

    A sealed card is the Execution Card the project charter asked for: exact
    scope, target, owner, and a frozen snapshot. The store refuses mutation
    after seal except a status flip to revoked.
    """

    id: str = Field(default_factory=_new_id)
    work_item_id: str
    approval_id: Optional[str] = None
    run_id: Optional[str] = None
    scope: str
    target: str = ""
    sealed_by: Optional[str] = None
    sealed_at: Optional[datetime] = None
    status: ExecutionCardStatus = ExecutionCardStatus.DRAFT
    snapshot: dict = Field(default_factory=dict)


class Watch(BaseModel):
    id: str = Field(default_factory=_new_id)
    target: str  # work item/project/external resource
    watcher: str
    # Minimal v1 grammar: either a canonical state name (for example
    # ``review``) or ``state==<canonical state>`` (for example
    # ``state==review``). See watches.py for evaluation and dispatch.
    trigger_condition: str
    notify_channel: str
    active: bool = True
    # The state-history position is an idempotency key for a WorkItem state
    # entry. It lets a watch fire again if the item later re-enters the state,
    # while repeat saves of the same object do not send duplicate notices.
    last_fired_state_history_index: Optional[int] = None
    last_fired_state: Optional[WorkItemState] = None

    @field_validator("notify_channel")
    @classmethod
    def slack_channels_only(cls, value: str) -> str:
        channel = value.strip()
        if not (
            channel.startswith("slack:")
            or channel.startswith("slack://")
            or channel.startswith("https://hooks.slack.com/services/")
        ):
            raise ValueError("Canon Watches support Slack channels only")
        return channel
