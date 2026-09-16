"""One-work-item routing, delivery proof, feedback batching, and scorecards.

This is a join layer over Canon and the read-only Brutus scanners. It owns no
queue and no live truth: Canon owns work/evidence, repositories own policy,
and execution/session systems own their current state.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import subprocess
from collections.abc import Mapping, Sequence
from contextlib import closing
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .agent_sessions import (
    CLAUDE_PROJECTS,
    CODEX_DB,
    CURSOR_PROJECTS,
    read_transcript_excerpt,
    scan_agent_sessions,
)
from .canon.identity import CanonError, require_owner
from .canon.models import (
    TERMINAL_STATES,
    Evidence,
    EvidenceType,
    InboxItem,
    InboxStatus,
    WorkItem,
)
from .canon.store import CanonStore
from .projects import PROJECTS_ROOT, canonical_remote

_SPACE = re.compile(r"[^a-z0-9]+")
_STATUS = re.compile(r"\b(?:status|where is|what(?:'s| is) happening|check|find|audit)\b", re.IGNORECASE)
_BUILD = re.compile(
    r"\b(?:build|change|fix|implement|ship|deploy|publish|update|add|remove)\b", re.IGNORECASE
)
_PUSH = re.compile(r"\b(?:continue|finish|complete|deploy|push|production|ship it)\b", re.IGNORECASE)
_META = re.compile(r"\b(?:skill|agent|model|harness|runner|recipe|workflow|dashboard)\b", re.IGNORECASE)
_REPOSITORY_MUTATION = re.compile(
    r"\b(?:add|build|change|commit|create|delete|deploy|edit|fix|implement|install|land|merge|"
    r"modify|patch|publish|push|refactor|release|remove|rename|run tests?|ship|update|write)\b",
    re.IGNORECASE,
)
_EVENT_TYPES = frozenset(
    {
        "observed",
        "started",
        "mutated",
        "tested",
        "delivered",
        "verified",
        "blocked",
        "failed",
        "stopped",
        "completed",
        "notification",
    }
)


def _normalized(value: str) -> str:
    return _SPACE.sub(" ", (value or "").casefold()).strip()


def _name_matches_request(name: str, request_words: set[str]) -> bool:
    """Match complete repository names, including names split by '-' or '_'."""
    tokens = set(_normalized(name).split())
    return bool(tokens) and tokens.issubset(request_words)


class DeliveryRequirement(BaseModel):
    id: str = Field(min_length=1)
    kind: Literal["test", "lint", "visual", "git", "deploy", "readback", "rollback", "other"]
    command: str = ""
    probe: str = ""
    target: str = ""
    required: bool = True
    freshness_hours: float = Field(default=24.0, gt=0)

    @field_validator("id")
    @classmethod
    def normalized_id(cls, value: str) -> str:
        candidate = value.strip()
        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", candidate):
            raise ValueError("requirement id must use lowercase letters, digits, _ or -")
        return candidate


class DeliveryPolicy(BaseModel):
    schema_version: Literal[1]
    repository: str = Field(min_length=1)
    requirements: list[DeliveryRequirement] = Field(min_length=1)

    @field_validator("requirements")
    @classmethod
    def unique_requirements(cls, value: list[DeliveryRequirement]) -> list[DeliveryRequirement]:
        ids = [item.id for item in value]
        if len(ids) != len(set(ids)):
            raise ValueError("delivery requirement ids must be unique")
        if not any(item.kind == "test" and item.required for item in value):
            raise ValueError("delivery policy requires at least one required test")
        if not any(item.kind == "git" and item.required for item in value):
            raise ValueError("delivery policy requires at least one required git receipt")
        return value


class ProfileRequirement(DeliveryRequirement):
    # V1 keeps its historical coercion/extra-field behavior.
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)


class DeliveryProfile(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    requirements: list[ProfileRequirement]


class EffectiveProfilePolicy(DeliveryPolicy):
    schema_version: Literal[2]


class ProfileDeliveryPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    schema_version: Literal[2]
    repository: str = Field(min_length=1)
    requirements: list[ProfileRequirement]
    profiles: dict[str, DeliveryProfile] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_profiles(self) -> ProfileDeliveryPolicy:
        if not self.repository.strip():
            raise ValueError("repository must not be blank")
        for name, profile in self.profiles.items():
            if not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", name):
                raise ValueError("profile name must use lowercase letters, digits, _ or -")
            # An unselected malformed profile is still a malformed policy.
            # Concatenation forbids overrides by duplicate id.
            self.effective(profile)
        return self

    def effective(self, profile: DeliveryProfile) -> EffectiveProfilePolicy:
        return EffectiveProfilePolicy(
            schema_version=2,
            repository=self.repository,
            requirements=[*self.requirements, *profile.requirements],
        )


class _UniquePolicyLoader(yaml.SafeLoader):
    def construct_mapping(self, node: Any, deep: bool = False) -> dict:
        self.flatten_mapping(node)
        result = {}
        for key_node, value_node in node.value:
            key = self.construct_object(key_node, deep=deep)
            if not isinstance(key, str) or key in result:
                raise ValueError("schema2 policy mapping keys must be unique strings")
            result[key] = self.construct_object(value_node, deep=deep)
        return result


@dataclass(frozen=True)
class LoadedPolicy:
    policy: DeliveryPolicy | EffectiveProfilePolicy
    path: str
    digest: str
    profile: str = ""


@dataclass(frozen=True)
class DeliveryProof:
    ok: bool
    required: int
    passed: int
    missing: tuple[str, ...]
    stale: tuple[str, ...]
    failed: tuple[str, ...]
    mismatched: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def load_delivery_policy(repo: str | Path, *, profile: str | None = None) -> LoadedPolicy:
    root = Path(repo).expanduser().resolve()
    path = root / ".codex" / "delivery.yaml"
    if not path.is_file():
        raise FileNotFoundError(f"delivery policy not found: {path}")
    raw = path.read_bytes()
    body = yaml.safe_load(raw) or {}
    if isinstance(body, dict) and body.get("schema_version") == 2:
        if type(body["schema_version"]) is not int:
            raise ValueError("schema_version must be an integer")
        declared = ProfileDeliveryPolicy.model_validate(yaml.load(raw, Loader=_UniquePolicyLoader))
        if not isinstance(profile, str) or not profile or profile not in declared.profiles:
            raise ValueError("schema2 delivery policy requires an explicit known profile")
        policy = declared.effective(declared.profiles[profile])
        # Bind raw bytes AND identity even for profiles with identical gates.
        digest = hashlib.sha256(
            b"brutus-delivery-policy-v2\0" + profile.encode("utf-8") + b"\0" + raw
        ).hexdigest()
        return LoadedPolicy(policy=policy, path=str(path), digest=digest, profile=profile)
    if profile is not None:
        raise ValueError("profile selection requires a schema2 delivery policy")
    policy = DeliveryPolicy.model_validate(body)
    return LoadedPolicy(policy=policy, path=str(path), digest=hashlib.sha256(raw).hexdigest())


def bind_delivery_policy(work_item: WorkItem, loaded: LoadedPolicy) -> WorkItem:
    work_item.repository_id = loaded.policy.repository
    work_item.delivery_policy_ref = loaded.path
    work_item.delivery_policy_digest = loaded.digest
    work_item.delivery_policy_profile = loaded.profile
    work_item.delivery_requirements = [item.id for item in loaded.policy.requirements if item.required]
    work_item.delivery_freshness_hours = {
        item.id: item.freshness_hours for item in loaded.policy.requirements if item.required
    }
    work_item.delivery_targets = {
        item.id: item.target
        for item in loaded.policy.requirements
        if item.required and item.target
    }
    return work_item


def evaluate_delivery_receipts(
    work_item: WorkItem,
    evidence: Sequence[Evidence],
    *,
    now: datetime | None = None,
) -> DeliveryProof:
    required = tuple(dict.fromkeys(work_item.delivery_requirements))
    if not required:
        return DeliveryProof(True, 0, 0, (), (), (), ())
    current = now or datetime.now(UTC)
    by_requirement: dict[str, list[Evidence]] = {item: [] for item in required}
    for receipt in evidence:
        if receipt.requirement_id in by_requirement:
            by_requirement[receipt.requirement_id].append(receipt)

    missing: list[str] = []
    stale: list[str] = []
    failed: list[str] = []
    mismatched: list[str] = []
    passed = 0
    for requirement_id, receipts in by_requirement.items():
        if not receipts:
            missing.append(requirement_id)
            continue
        usable = []
        for receipt in receipts:
            if work_item.delivery_policy_profile and (
                not work_item.delivery_policy_digest
                or receipt.metadata.get("delivery_policy_digest") != work_item.delivery_policy_digest
                or receipt.linked_object_id != work_item.id
                or (
                    receipt.source_repository is not None
                    and receipt.source_repository != work_item.repository_id
                )
            ):
                mismatched.append(requirement_id)
                continue
            captured = receipt.captured_at
            if captured.tzinfo is None:
                captured = captured.replace(tzinfo=UTC)
            max_age = work_item.delivery_freshness_hours.get(requirement_id, 24.0)
            age_seconds = (current - captured).total_seconds()
            if age_seconds > max_age * 3600 or (work_item.delivery_policy_profile and age_seconds < 0):
                stale.append(requirement_id)
                continue
            if work_item.target_artifact_digest and (
                receipt.artifact_digest != work_item.target_artifact_digest
            ):
                mismatched.append(requirement_id)
                continue
            required_target = work_item.delivery_targets.get(requirement_id, "")
            if required_target and receipt.target != required_target:
                mismatched.append(requirement_id)
                continue
            if not receipt.verified or receipt.result != "pass":
                failed.append(requirement_id)
                continue
            usable.append(receipt)
        if usable:
            passed += 1
    return DeliveryProof(
        ok=passed == len(required),
        required=len(required),
        passed=passed,
        missing=tuple(sorted(set(missing))),
        stale=tuple(sorted(set(stale))),
        failed=tuple(sorted(set(failed))),
        mismatched=tuple(sorted(set(mismatched))),
    )


@dataclass(frozen=True)
class RouteResult:
    action: Literal["continue", "link_ticket", "create", "needs_repository"]
    reason: str
    repository_id: str = ""
    repository_path: str = ""
    worktree_path: str = ""
    work_item_id: str = ""
    session_id: str = ""
    ticket_id: str = ""
    broad_root_rejected: bool = False
    needs_worktree: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class RouteGuardDecision:
    allow: bool
    reason: str
    message: str = ""
    repository_path: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _payload_value(payload: Mapping[str, Any], names: set[str]) -> str:
    for key, value in payload.items():
        if key.casefold() in names:
            if isinstance(value, str) and value.strip():
                return value.strip()
            if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
                first = next((str(item) for item in value if str(item).strip()), "")
                if first:
                    return first
        if isinstance(value, Mapping):
            nested = _payload_value(value, names)
            if nested:
                return nested
    return ""


def evaluate_route_guard(
    payload: Mapping[str, Any],
    *,
    projects: Sequence[Mapping[str, Any]] | None = None,
) -> RouteGuardDecision:
    """Deny repository mutations that begin at the broad Projects directory."""
    cwd = _payload_value(
        payload,
        {"cwd", "working_directory", "workingdirectory", "workspace_root", "workspaceroot"},
    )
    if not cwd or Path(cwd).expanduser().resolve() != PROJECTS_ROOT.resolve():
        return RouteGuardDecision(True, "working directory is not the broad Projects root")
    prompt = _payload_value(payload, {"prompt", "user_prompt", "userprompt", "message", "query"})
    if not prompt or not _REPOSITORY_MUTATION.search(prompt):
        return RouteGuardDecision(True, "broad-root prompt is read-only or has no mutation intent")
    repo_hint = _payload_value(payload, {"repository", "repo", "repo_hint", "repohint"})
    candidates = (
        list(projects)
        if projects is not None
        else _route_projects(prompt, repo_hint=repo_hint, cwd=cwd, sessions=())
    )
    repository, _ = _choose_repository(prompt, candidates, repo_hint=repo_hint, cwd=cwd)
    if repository is not None:
        target = str(repository.get("path") or "")
        return RouteGuardDecision(
            False,
            "repository mutation was submitted from the broad Projects root",
            (
                f"Repository-changing work cannot start from {PROJECTS_ROOT}. "
                f"Open or create the task in the saved project at {target}, then resubmit this prompt."
            ),
            target,
        )
    return RouteGuardDecision(
        True,
        "no repository could be identified, so there is nothing to route",
    )


def _choose_repository(
    request: str,
    projects: Sequence[Mapping[str, Any]],
    *,
    repo_hint: str = "",
    cwd: str = "",
) -> tuple[Mapping[str, Any] | None, bool]:
    broad = bool(cwd and Path(cwd).expanduser().resolve() == PROJECTS_ROOT.resolve())
    hint = _normalized(repo_hint)
    if hint:
        exact = [
            item
            for item in projects
            if hint
            in {
                _normalized(str(item.get("name") or "")),
                _normalized(str(item.get("path") or "")),
                _normalized(str(item.get("project_id") or "")),
                _normalized(str(item.get("remote") or "")),
            }
        ]
        if len(exact) == 1:
            return exact[0], broad
    if cwd and not broad:
        current = Path(cwd).expanduser().resolve()
        matches = [
            item
            for item in projects
            if Path(str(item.get("path") or "/nonexistent")).expanduser().resolve() == current
        ]
        if len(matches) == 1:
            return matches[0], broad
    words = set(_normalized(request).split())
    named = [
        item
        for item in projects
        if _name_matches_request(str(item.get("name") or ""), words)
        or _name_matches_request(Path(str(item.get("path") or "")).name, words)
    ]
    project_ids = {str(item.get("project_id") or "") for item in named}
    if len(project_ids) == 1 and named:
        named.sort(
            key=lambda item: (bool(item.get("is_worktree")), -float(item.get("last_commit_epoch") or 0))
        )
        return named[0], broad
    return None, broad


def route_work(
    request: str,
    *,
    store: CanonStore,
    projects: Sequence[Mapping[str, Any]],
    sessions: Sequence[Mapping[str, Any]] = (),
    tickets: Sequence[Mapping[str, Any]] = (),
    repo_hint: str = "",
    cwd: str = "",
    create: bool = False,
) -> RouteResult:
    title = _normalized(request)
    if not title:
        raise ValueError("request is required")
    active = [item for item in store.list(WorkItem) if item.state not in TERMINAL_STATES]
    explicit = [item for item in active if item.id.casefold() in request.casefold()]
    exact = explicit or [item for item in active if _normalized(item.title) == title]
    if exact:
        chosen = exact[0]
        return RouteResult(
            action="continue",
            reason="matching active Canon work item",
            repository_id=chosen.repository_id,
            repository_path=chosen.repository_path,
            worktree_path=chosen.worktree_path,
            work_item_id=chosen.id,
        )
    session_match = next(
        (
            item
            for item in sessions
            if _normalized(str(item.get("title") or "")) == title
            and str(item.get("state") or "") not in {"completed", "failed"}
        ),
        None,
    )
    if session_match:
        return RouteResult(
            action="continue",
            reason="matching active execution session",
            session_id=str(session_match.get("id") or ""),
            repository_path=str(session_match.get("cwd") or ""),
        )
    ticket = next(
        (
            item
            for item in tickets
            if str(item.get("relationship") or "") == "exact"
            and str(item.get("status") or "open").casefold()
            not in {"closed", "completed", "done", "cancelled", "canceled"}
        ),
        None,
    )
    repo, broad = _choose_repository(request, projects, repo_hint=repo_hint, cwd=cwd)
    if repo is None:
        return RouteResult(
            action="needs_repository",
            reason="no unique authoritative saved repository matched",
            ticket_id=str((ticket or {}).get("id") or ""),
            broad_root_rejected=broad,
        )
    same_repo = [item for item in projects if item.get("project_id") == repo.get("project_id")]
    worktrees = [item for item in same_repo if item.get("is_worktree")]
    worktree = next(
        (
            item
            for item in worktrees
            if any(str(session.get("cwd") or "") == str(item.get("path") or "") for session in sessions)
        ),
        None,
    )
    worktree_path = str((worktree or {}).get("path") or "")
    result_action: Literal["link_ticket", "create"] = "link_ticket" if ticket else "create"
    work_item_id = ""
    if create:
        work = WorkItem(
            title=request.strip(),
            description=request.strip(),
            repository_id=str(repo.get("project_id") or ""),
            repository_path=str(repo.get("path") or ""),
            worktree_path=worktree_path,
            bindings={
                key: value
                for key, value in {
                    "linear": str((ticket or {}).get("id") or ""),
                }.items()
                if value
            },
        )
        store.save(work)
        work_item_id = work.id
    return RouteResult(
        action=result_action,
        reason="exact open business ticket" if ticket else "no duplicate active work found",
        repository_id=str(repo.get("project_id") or ""),
        repository_path=str(repo.get("path") or ""),
        worktree_path=worktree_path,
        work_item_id=work_item_id,
        ticket_id=str((ticket or {}).get("id") or ""),
        broad_root_rejected=broad,
        needs_worktree=not bool(worktree_path),
    )


def post_work_event(
    store: CanonStore,
    *,
    work_item_id: str,
    event_id: str,
    event_type: str,
    surface: str,
    source_locator: str,
    result: str = "",
    artifact_digest: str = "",
    metadata: Mapping[str, Any] | None = None,
) -> tuple[Evidence, bool]:
    if event_type not in _EVENT_TYPES:
        raise ValueError(f"unsupported event_type: {event_type}")
    work = store.get(WorkItem, work_item_id)
    if work is None:
        raise CanonError(f"work item '{work_item_id}' was not found")
    existing = next(
        (item for item in store.list(Evidence) if item.source_delivery_id == event_id),
        None,
    )
    if existing:
        if existing.linked_object_id != work_item_id:
            raise CanonError("event id is already bound to another work item")
        expected = (
            event_type,
            surface,
            source_locator,
            result or None,
            artifact_digest or None,
        )
        actual = (
            existing.event_type,
            existing.surface,
            existing.content_ref,
            existing.result,
            existing.artifact_digest,
        )
        if actual != expected:
            raise CanonError("event id was replayed with different content")
        return existing, False
    evidence = Evidence(
        type=EvidenceType.LOG,
        captured_by=surface,
        captured_by_kind="worker",
        linked_object_id=work_item_id,
        content_ref=source_locator,
        source_delivery_id=event_id,
        event_type=event_type,
        surface=surface,
        result=result or None,
        artifact_digest=artifact_digest or None,
        metadata=dict(metadata or {}),
    )
    store.save(evidence)
    work.evidence_refs.append(evidence.id)
    store.save(work)
    return evidence, True


def attach_delivery_receipt(
    store: CanonStore,
    *,
    work_item_id: str,
    requirement_id: str,
    evidence_type: EvidenceType | str,
    content_ref: str,
    result: str,
    target: str = "",
    artifact_digest: str = "",
    captured_by: str = "owner-local-verifier",
    captured_by_kind: str = "human",
) -> Evidence:
    """Attach an owner-verified structured delivery receipt to one Work Item."""
    work = store.get(WorkItem, work_item_id)
    if work is None:
        raise CanonError(f"work item '{work_item_id}' was not found")
    if requirement_id not in work.delivery_requirements:
        raise CanonError(f"'{requirement_id}' is not required by the bound delivery policy")
    if not content_ref.strip():
        raise CanonError("delivery receipt requires a non-empty content reference")
    if not isinstance(captured_by, str) or not captured_by.strip():
        raise CanonError("delivery receipt requires a non-empty captured actor")
    if not isinstance(captured_by_kind, str) or captured_by_kind not in {"human", "worker"}:
        raise CanonError("delivery receipt captured actor kind must be human or worker")
    normalized_result = result.strip().casefold()
    if normalized_result not in {"pass", "fail"}:
        raise CanonError("delivery receipt result must be pass or fail")
    principal = store.identity_registry.owner_principal()
    require_owner(principal.identity, principal, registry=store.identity_registry)
    receipt = Evidence(
        type=EvidenceType(evidence_type),
        captured_by=captured_by,
        captured_by_kind=captured_by_kind,
        linked_object_id=work.id,
        content_ref=content_ref,
        verified=True,
        verified_by=principal.identity,
        source_repository=work.repository_id or None,
        requirement_id=requirement_id,
        result=normalized_result,
        target=target or None,
        artifact_digest=artifact_digest or None,
        metadata={"delivery_policy_digest": work.delivery_policy_digest},
    )
    store.save(receipt, authenticated_principal=principal)
    work.evidence_refs.append(receipt.id)
    store.save(work)
    return receipt


class FeedbackReport(BaseModel):
    id: str = Field(min_length=1)
    raw_capture: str = Field(min_length=1)
    source: str = Field(min_length=1)
    surface: str = Field(min_length=1)
    acceptance_contract: str = ""
    release_path: str = ""
    rollback_path: str = ""
    severity: Literal["critical", "high", "medium", "low"] = "medium"
    verified: bool = False


@dataclass(frozen=True)
class FeedbackBatch:
    id: str
    surface: str
    acceptance_contract: str
    release_path: str
    rollback_path: str
    tier: int
    report_ids: tuple[str, ...]
    dispositions: tuple[tuple[str, str], ...]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def batch_feedback(reports: Sequence[FeedbackReport | Mapping[str, Any]]) -> list[FeedbackBatch]:
    parsed = [
        item if isinstance(item, FeedbackReport) else FeedbackReport.model_validate(item) for item in reports
    ]
    ids = [item.id for item in parsed]
    if len(ids) != len(set(ids)):
        raise ValueError("feedback report ids must be unique")
    groups: dict[tuple[str, str, str, str], list[FeedbackReport]] = {}
    for report in parsed:
        acceptance = _normalized(report.acceptance_contract)
        if not acceptance:
            key = (
                _normalized(report.surface),
                f"unresolved:{report.id}",
                report.release_path,
                report.rollback_path,
            )
        else:
            key = (
                _normalized(report.surface),
                acceptance,
                _normalized(report.release_path),
                _normalized(report.rollback_path),
            )
        groups.setdefault(key, []).append(report)
    out: list[FeedbackBatch] = []
    severity_tier = {"critical": 1, "high": 1, "medium": 2, "low": 3}
    for key, members in sorted(groups.items()):
        acceptance = members[0].acceptance_contract.strip()
        tier = (
            0
            if not all(item.verified for item in members)
            else min(severity_tier[item.severity] for item in members)
        )
        digest = hashlib.sha256("\n".join(sorted(item.id for item in members)).encode()).hexdigest()[:12]
        disposition = "needs_acceptance" if not acceptance else "batched"
        out.append(
            FeedbackBatch(
                id=f"feedback-{digest}",
                surface=members[0].surface,
                acceptance_contract=acceptance,
                release_path=members[0].release_path,
                rollback_path=members[0].rollback_path,
                tier=tier,
                report_ids=tuple(item.id for item in members),
                dispositions=tuple((item.id, disposition) for item in members),
            )
        )
    return out


def persist_feedback_batches(
    store: CanonStore,
    reports: Sequence[FeedbackReport | Mapping[str, Any]],
) -> list[WorkItem]:
    """Preserve raw reports in Inbox and create one deterministic item per batch."""
    parsed = [
        item if isinstance(item, FeedbackReport) else FeedbackReport.model_validate(item) for item in reports
    ]
    by_id = {item.id: item for item in parsed}
    batches = batch_feedback(parsed)
    for report in parsed:
        existing = store.get(InboxItem, report.id)
        candidate = InboxItem(
            id=report.id,
            raw_capture=report.raw_capture,
            source=json.dumps(
                {
                    "source": report.source,
                    "surface": report.surface,
                    "verified": report.verified,
                },
                sort_keys=True,
            ),
            status=InboxStatus.PROMOTED,
        )
        if existing and (existing.raw_capture != candidate.raw_capture or existing.source != candidate.source):
            raise CanonError(f"feedback report id '{report.id}' was replayed with different content")
        if existing is None:
            store.save(candidate)

    work_items: list[WorkItem] = []
    for batch in batches:
        existing = store.get(WorkItem, batch.id)
        if existing:
            if set(existing.feedback_refs) != set(batch.report_ids):
                raise CanonError(f"feedback batch id '{batch.id}' collided with different reports")
            work_items.append(existing)
            continue
        members = [by_id[item_id] for item_id in batch.report_ids]
        acceptance = batch.acceptance_contract or "Acceptance contract must be clarified before execution."
        work = WorkItem(
            id=batch.id,
            title=f"{batch.surface}: {acceptance}",
            description=json.dumps(
                {
                    "acceptance_contract": batch.acceptance_contract,
                    "release_path": batch.release_path,
                    "rollback_path": batch.rollback_path,
                    "reports": [item.model_dump(mode="json") for item in members],
                    "dispositions": list(batch.dispositions),
                },
                sort_keys=True,
            ),
            origin=batch.report_ids[0],
            priority=4 - batch.tier,
            bindings={"feedback_batch": batch.id},
            feedback_refs=list(batch.report_ids),
        )
        store.save(work)
        work_items.append(work)
    return work_items


def _metric(numerator: int, denominator: int) -> dict[str, Any]:
    return {
        "numerator": numerator,
        "denominator": denominator,
        "rate": round(numerator / denominator, 4) if denominator else None,
        "status": "measured" if denominator else "insufficient_evidence",
    }


def build_efficiency_scorecard(
    sessions: Sequence[Mapping[str, Any]],
    work_items: Sequence[WorkItem],
    evidence: Sequence[Evidence],
    *,
    source_coverage: Mapping[str, bool],
    window_start: datetime,
    window_end: datetime,
) -> dict[str, Any]:
    titles = [_normalized(str(item.get("title") or "")) for item in sessions]
    transcripts = []
    for item in sessions:
        supplied = str(item.get("transcript_excerpt") or "")
        path = str(item.get("path") or "")
        transcripts.append(supplied or (read_transcript_excerpt(path, max_chars=4000) if path else ""))
    repo_sessions = [item for item in sessions if str(item.get("cwd") or "")]
    broad = sum(
        1
        for item in repo_sessions
        if Path(str(item.get("cwd"))).expanduser().resolve() == PROJECTS_ROOT.resolve()
    )
    duplicate_members = sum(count for title in set(titles) if title and (count := titles.count(title)) > 1)
    status_only = sum(1 for title in titles if _STATUS.search(title) and not _BUILD.search(title))
    build_change = sum(1 for title in titles if _BUILD.search(title))
    completion_push = sum(1 for text in transcripts if _PUSH.search(text))
    meta = sum(1 for title in titles if _META.search(title))
    completed = [item for item in work_items if item.state.value == "closure" and item.delivery_requirements]
    complete_receipts = sum(
        1 for item in completed if evaluate_delivery_receipts(item, evidence, now=window_end).ok
    )
    notifications = [item for item in evidence if item.event_type == "notification"]
    meaningless = sum(1 for item in notifications if not bool(item.metadata.get("material")))
    gaps = sorted(name for name, available in source_coverage.items() if not available)
    return {
        "schema_version": 1,
        "window": {"start": window_start.isoformat(), "end": window_end.isoformat()},
        "population": {"sessions": len(sessions), "work_items": len(work_items), "evidence": len(evidence)},
        "source_coverage": dict(source_coverage),
        "gaps": gaps,
        "metrics": {
            "broad_root_starts": _metric(broad, len(repo_sessions)),
            "status_only_tasks": _metric(status_only, len(sessions)),
            "build_change_tasks": _metric(build_change, len(sessions)),
            "duplicate_intent_members": _metric(duplicate_members, len(sessions)),
            "completion_push_sessions": _metric(completion_push, len(sessions)),
            "meta_work_sessions": _metric(meta, len(sessions)),
            "delivery_receipt_coverage": _metric(complete_receipts, len(completed)),
            "meaningless_notifications": _metric(meaningless, len(notifications)),
        },
    }


def live_efficiency_scorecard(store: CanonStore, *, days: int = 7) -> dict[str, Any]:
    if days <= 0:
        raise ValueError("days must be greater than zero")
    end = datetime.now(UTC)
    start = end.fromtimestamp(end.timestamp() - days * 86400, tz=UTC)
    rows = [
        row for row in scan_agent_sessions(force=True) if float(row.get("mtime") or 0) >= start.timestamp()
    ]
    source_coverage = {
        "codex": CODEX_DB.is_file(),
        "cursor": CURSOR_PROJECTS.is_dir(),
        "claude": CLAUDE_PROJECTS.is_dir(),
        "canon": True,
        "slack": False,
        "email": False,
        "calendar": False,
    }
    return build_efficiency_scorecard(
        rows,
        store.list(WorkItem),
        store.list(Evidence),
        source_coverage=source_coverage,
        window_start=start,
        window_end=end,
    )


def live_route(
    request: str,
    *,
    store: CanonStore,
    repo_hint: str = "",
    cwd: str = "",
    create: bool = False,
) -> RouteResult:
    sessions = _codex_catalog_sessions()
    return route_work(
        request,
        store=store,
        projects=_route_projects(request, repo_hint=repo_hint, cwd=cwd, sessions=sessions),
        # Intake latency must not pay the cost of parsing every retained
        # transcript. Canon catches durable duplicates; Codex's read-only
        # catalog catches recent execution-task duplicates by title and cwd.
        sessions=sessions,
        repo_hint=repo_hint,
        cwd=cwd,
        create=create,
    )


def _git_value(repo: Path, *args: str) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", str(repo), *args],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return result.stdout.strip() if result.returncode == 0 else ""


def _route_projects(
    request: str,
    *,
    repo_hint: str,
    cwd: str,
    sessions: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Resolve only plausible repositories; never shell across all projects."""
    candidates: list[Path] = []
    hint_path = Path(repo_hint).expanduser() if repo_hint else None
    if hint_path and hint_path.exists():
        candidates.append(hint_path)
    elif repo_hint:
        direct = PROJECTS_ROOT / repo_hint
        if direct.exists():
            candidates.append(direct)
    if cwd and Path(cwd).expanduser().resolve() != PROJECTS_ROOT.resolve():
        root = _git_value(Path(cwd).expanduser(), "rev-parse", "--show-toplevel")
        if root:
            candidates.append(Path(root))
    words = set(_normalized(request).split())
    if PROJECTS_ROOT.is_dir():
        for child in PROJECTS_ROOT.iterdir():
            if child.is_dir() and _name_matches_request(child.name, words):
                candidates.append(child)
    for session in sessions:
        session_cwd = Path(str(session.get("cwd") or "")).expanduser()
        if session_cwd.name and _name_matches_request(session_cwd.name, words):
            candidates.append(session_cwd)

    roots: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        root = _git_value(candidate, "rev-parse", "--show-toplevel")
        if not root or root in seen:
            continue
        seen.add(root)
        roots.append(Path(root))
    rows: list[dict[str, Any]] = []
    for root in roots:
        remote = canonical_remote(_git_value(root, "remote", "get-url", "origin"))
        project_id = remote or f"local/{root.name.casefold()}"
        worktree_output = _git_value(root, "worktree", "list", "--porcelain")
        worktrees = [
            Path(line.removeprefix("worktree "))
            for line in worktree_output.splitlines()
            if line.startswith("worktree ")
        ] or [root]
        for worktree in worktrees:
            rows.append(
                {
                    "name": root.name if worktree == root else worktree.name,
                    "path": str(worktree),
                    "project_id": project_id,
                    "remote": remote,
                    "is_worktree": worktree != root,
                    "last_commit_epoch": 0,
                }
            )
    return rows


def _codex_catalog_sessions(db_path: Path = CODEX_DB) -> list[dict[str, Any]]:
    if not db_path.is_file():
        return []
    try:
        with closing(sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=1)) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """SELECT host_id, thread_id, display_title, cwd, source_recency_at
                   FROM local_thread_catalog
                   WHERE missing_candidate = 0
                   ORDER BY source_recency_at DESC
                   LIMIT 250"""
            ).fetchall()
    except (sqlite3.Error, OSError):
        return []
    return [
        {
            "id": f"codex:{row['host_id']}:{row['thread_id']}",
            "title": str(row["display_title"] or ""),
            "cwd": str(row["cwd"] or ""),
            # The catalog does not expose lifecycle truth. Unknown is eligible
            # for continuation; a user can inspect the task before mutation.
            "state": "unknown",
            "mtime": float(row["source_recency_at"] or 0),
        }
        for row in rows
        if row["thread_id"]
    ]


def work_status(store: CanonStore, work_item_id: str) -> dict[str, Any]:
    work = store.get(WorkItem, work_item_id)
    if work is None:
        raise CanonError(f"work item '{work_item_id}' was not found")
    evidence = [item for item in store.list(Evidence) if item.id in work.evidence_refs]
    proof = evaluate_delivery_receipts(work, evidence)
    return {
        "work_item": work.model_dump(mode="json"),
        "delivery_proof": proof.to_dict(),
        "events": [item.model_dump(mode="json") for item in evidence if item.event_type],
    }


def find_work_by_binding(store: CanonStore, surface: str, external_id: str) -> WorkItem:
    """Resolve one active Canon item by an immutable cross-surface binding."""
    matches = [
        item
        for item in store.list(WorkItem)
        if item.state not in TERMINAL_STATES and item.bindings.get(surface) == external_id
    ]
    if not matches:
        raise CanonError(f"no active work item is bound to {surface}:{external_id}")
    if len(matches) > 1:
        raise CanonError(f"multiple active work items are bound to {surface}:{external_id}")
    return matches[0]


def dumps(value: Any) -> str:
    return json.dumps(value, indent=2, sort_keys=True, default=str)
