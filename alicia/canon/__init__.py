"""Canonical Work Object model (REV-513).

Implements the minimum object model and state machine from the accepted
spec: "Alicia Canonical Work Objects & State Machine — v1"
(Linear REV-510, document 07601bfa36fe).

This is Alicia/Atlas's portfolio-and-evidence layer. Linear remains the
system of record for delivery tickets — nothing here migrates or replaces
Linear issues.
"""

from .identity import (
    DEFAULT_IDENTITY_REGISTRY,
    AuthenticatedPrincipal,
    IdentityRegistry,
    PrincipalKind,
    require_owner,
    require_verifier,
    verify_actor,
)
from .migrations import LATEST_SCHEMA_VERSION
from .models import (
    Approval,
    ApprovalStatus,
    Decision,
    Evidence,
    EvidenceType,
    ExecutionCard,
    ExecutionCardStatus,
    InboxItem,
    InboxStatus,
    Project,
    ProjectStatus,
    ProveVerdict,
    Run,
    RunStatus,
    Watch,
    WorkItem,
    WorkItemState,
    WorkItemType,
)
from .state_machine import (
    CanonError,
    StateHistoryEntry,
    completion_proof_ok,
    transition,
)
from .store import CanonStore
from .watches import WatchEvaluation, evaluate_watch, evaluate_watches, trigger_state, watch_matches

__all__ = [
    "DEFAULT_IDENTITY_REGISTRY",
    "Approval",
    "ApprovalStatus",
    "AuthenticatedPrincipal",
    "CanonError",
    "CanonStore",
    "Decision",
    "Evidence",
    "EvidenceType",
    "ExecutionCard",
    "ExecutionCardStatus",
    "IdentityRegistry",
    "InboxItem",
    "InboxStatus",
    "PrincipalKind",
    "Project",
    "ProjectStatus",
    "ProveVerdict",
    "Run",
    "RunStatus",
    "StateHistoryEntry",
    "Watch",
    "WatchEvaluation",
    "WorkItem",
    "WorkItemState",
    "WorkItemType",
    "completion_proof_ok",
    "evaluate_watch",
    "evaluate_watches",
    "require_owner",
    "require_verifier",
    "transition",
    "trigger_state",
    "verify_actor",
    "watch_matches",
]

# Compatibility export for callers which previously read this value directly.
# The migration files are now the source of truth; adding a migration updates
# this value automatically.
SCHEMA_VERSION = LATEST_SCHEMA_VERSION
