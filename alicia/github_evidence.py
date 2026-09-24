"""GitHub webhook -> Canon Evidence bridge (REV-521).

GitHub delivery is deliberately parsed into a small, synchronous service.  The
FastAPI route is only transport: keeping matching and persistence here makes the
trust boundary and webhook behaviour testable without an HTTP server.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from .canon import CanonStore, Evidence, EvidenceType, Run, WorkItem
from .canon.identity import AuthenticatedPrincipal, PrincipalKind
from .security import allowed_github_repositories

_TICKET_RE = re.compile(r"\b([A-Za-z][A-Za-z0-9]+-\d+)\b")


@dataclass(frozen=True)
class WebhookResult:
    """The safe, inspectable outcome of one GitHub webhook delivery."""

    status: str
    evidence_ids: tuple[str, ...] = ()


class GitHubEvidenceReceiver:
    """Capture merged PR and completed CI evidence for known Canon work.

    A branch such as ``justinfowler/rev-521-short-description`` contributes
    ``REV-521`` as its work-item key.  The receiver finds a Canon WorkItem whose
    title, description, or origin mentions that key.  A canonical WorkItem UUID
    placed anywhere in the PR title/body is also an explicit match.

    The receiver never uses the human owner as an implicit verifier.  It can
    only write webhook evidence when it can issue a configured
    ``AUTOMATED_VERIFIER`` principal from the store's identity registry.
    """

    def __init__(self, store: CanonStore, *, verifier_identity: str | None = None) -> None:
        self.store = store
        self.verifier_identity = verifier_identity

    def handle(
        self, event: str | None, payload: Any, *, delivery_id: str = ""
    ) -> WebhookResult:
        """Handle a supported GitHub webhook event without raising on bad input."""

        if not isinstance(payload, dict):
            return WebhookResult("ignored")
        repository = _text(_mapping(payload.get("repository")).get("full_name"))
        if repository.lower() not in allowed_github_repositories():
            return WebhookResult("rejected_repository")
        if not delivery_id.strip():
            return WebhookResult("rejected_delivery")
        duplicate = next(
            (
                evidence
                for evidence in self.store.list(Evidence)
                if evidence.source_delivery_id == delivery_id
            ),
            None,
        )
        if duplicate is not None:
            return WebhookResult("duplicate", (duplicate.id,))
        self._delivery_id = delivery_id
        self._repository = repository
        if event == "pull_request":
            return self._pull_request(payload)
        if event == "check_suite":
            return self._check_suite(payload)
        if event == "workflow_run":
            return self._workflow_run(payload)
        return WebhookResult("ignored")

    def _pull_request(self, payload: dict[str, Any]) -> WebhookResult:
        pull_request = _mapping(payload.get("pull_request"))
        if payload.get("action") != "closed" or not pull_request.get("merged"):
            return WebhookResult("ignored")

        branch = _text(_mapping(pull_request.get("head")).get("ref"))
        work_item = self._work_item_for(branch, pull_request)
        url = _text(pull_request.get("html_url"))
        if work_item is None or not url:
            return WebhookResult("ignored")
        return self._capture(
            work_item,
            evidence_type=EvidenceType.DIFF,
            content_ref=url,
            verified=True,
            source_object_id=str(pull_request.get("id") or pull_request.get("number") or ""),
            source_sha=_text(pull_request.get("merge_commit_sha")),
        )

    def _check_suite(self, payload: dict[str, Any]) -> WebhookResult:
        check_suite = _mapping(payload.get("check_suite"))
        if payload.get("action") != "completed":
            return WebhookResult("ignored")

        branch = _text(check_suite.get("head_branch"))
        work_item = self._work_item_for(branch, check_suite)
        url = _text(check_suite.get("html_url")) or _checks_url(payload, check_suite)
        if work_item is None or not url:
            return WebhookResult("ignored")
        return self._capture(
            work_item,
            evidence_type=EvidenceType.RUN_OUTPUT,
            content_ref=url,
            verified=check_suite.get("conclusion") == "success",
            source_object_id=str(check_suite.get("id") or ""),
            source_sha=_text(check_suite.get("head_sha")),
        )

    def _workflow_run(self, payload: dict[str, Any]) -> WebhookResult:
        workflow_run = _mapping(payload.get("workflow_run"))
        if payload.get("action") != "completed":
            return WebhookResult("ignored")

        branch = _text(workflow_run.get("head_branch"))
        work_item = self._work_item_for(branch, workflow_run)
        url = _text(workflow_run.get("html_url"))
        if work_item is None or not url:
            return WebhookResult("ignored")
        return self._capture(
            work_item,
            evidence_type=EvidenceType.RUN_OUTPUT,
            content_ref=url,
            verified=workflow_run.get("conclusion") == "success",
            source_object_id=str(workflow_run.get("id") or ""),
            source_sha=_text(workflow_run.get("head_sha")),
        )

    def _work_item_for(self, branch: str, event_object: dict[str, Any]) -> WorkItem | None:
        """Resolve a Canon WorkItem from an explicit ID or external ticket key."""

        text = " ".join(
            (
                branch,
                _text(event_object.get("title")),
                _text(event_object.get("body")),
                _text(event_object.get("name")),
            )
        )
        work_items = self.store.list(WorkItem)

        # Canon IDs are strongest: a UUID in the PR title/body is an explicit
        # assertion of the intended object, rather than a fuzzy title match.
        text_lower = text.lower()
        id_matches = [item for item in work_items if item.id.lower() in text_lower]
        if len(id_matches) == 1:
            return id_matches[0]
        if len(id_matches) > 1:
            return None

        ticket_keys = {match.upper() for match in _TICKET_RE.findall(text)}
        if not ticket_keys:
            return None
        matches: list[WorkItem] = []
        for work_item in work_items:
            metadata = f"{work_item.title} {work_item.description} {work_item.origin}"
            metadata_keys = {match.upper() for match in _TICKET_RE.findall(metadata)}
            if ticket_keys & metadata_keys:
                matches.append(work_item)
        return matches[0] if len(matches) == 1 else None

    def _capture(
        self,
        work_item: WorkItem,
        *,
        evidence_type: EvidenceType,
        content_ref: str,
        verified: bool,
        source_object_id: str,
        source_sha: str,
    ) -> WebhookResult:
        principal = self._automated_verifier()
        if principal is None:
            return WebhookResult("ignored")

        # GitHub retries webhook deliveries.  Canon Evidence has no external
        # delivery-id field, so type + URL is the durable idempotency key.
        existing = next(
            (
                evidence
                for evidence in self.store.list(Evidence)
                if evidence.type == evidence_type and evidence.content_ref == content_ref
            ),
            None,
        )
        if existing is not None:
            return WebhookResult("duplicate", (existing.id,))

        run = self._latest_run(work_item)
        evidence = Evidence(
            type=evidence_type,
            captured_by=principal.identity,
            captured_by_kind=principal.kind.value,
            linked_object_id=run.id if run is not None else work_item.id,
            content_ref=content_ref,
            verified=verified,
            verified_by=principal.identity if verified else None,
            source_repository=self._repository,
            source_object_id=source_object_id or None,
            source_sha=source_sha or None,
            source_delivery_id=self._delivery_id,
        )
        self.store.save(evidence, authenticated_principal=principal)

        if run is not None and evidence.id not in run.evidence_refs:
            run.evidence_refs.append(evidence.id)
            self.store.save(run)
        if evidence.id not in work_item.evidence_refs:
            work_item.evidence_refs.append(evidence.id)
            self.store.save(work_item)
        return WebhookResult("captured", (evidence.id,))

    def _automated_verifier(self) -> AuthenticatedPrincipal | None:
        registry = self.store.identity_registry
        identity = self.verifier_identity
        if not identity:
            identities = registry.automated_verifier_identities
            if len(identities) != 1:
                return None
            identity = next(iter(identities))
        try:
            principal = registry.verifier_principal(identity)
        except ValueError:
            return None
        # IdentityRegistry.verifier_principal intentionally also permits the
        # human owner.  GitHub automation must never take that branch.
        if principal.kind != PrincipalKind.AUTOMATED_VERIFIER:
            return None
        return principal

    def _latest_run(self, work_item: WorkItem) -> Run | None:
        runs = [run for run in self.store.list(Run) if run.work_item_id == work_item.id]
        return max(runs, key=lambda run: run.started_at, default=None)


def _mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _checks_url(payload: dict[str, Any], check_suite: dict[str, Any]) -> str:
    """Create a browser-visible checks URL when GitHub omits ``html_url``."""

    repository = _mapping(payload.get("repository"))
    full_name = _text(repository.get("full_name"))
    sha = _text(check_suite.get("head_sha"))
    if full_name and sha:
        return f"https://github.com/{full_name}/commit/{sha}/checks"
    return ""
