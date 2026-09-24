"""REV-521 GitHub webhook evidence capture."""

from __future__ import annotations

from alicia.canon import CanonStore, Evidence, EvidenceType, IdentityRegistry, Run, WorkItem
from alicia.github_evidence import GitHubEvidenceReceiver
from alicia.security import allowed_github_repositories

OWNER = "justin.fowler@clearspeed.com"
VERIFIER = "github-evidence-verifier"


def test_default_allowed_repository_is_the_public_personal_repo(monkeypatch):
    monkeypatch.delenv("ALICIA_GITHUB_REPOSITORIES", raising=False)

    assert allowed_github_repositories() == frozenset(
        {"justinfowler925/alicia", "justinfowler925/brutus"}
    )


def _store() -> CanonStore:
    registry = IdentityRegistry(
        owner_identity=OWNER,
        worker_identities=frozenset({"atlas6-worker"}),
        automated_verifier_identities=frozenset({VERIFIER}),
    )
    return CanonStore(identity_registry=registry)


def _merged_pr(*, branch: str, title: str = "REV-521 capture evidence") -> dict:
    return {
        "action": "closed",
        "repository": {"full_name": "justinfowler925/alicia"},
        "pull_request": {
            "id": 89,
            "merged": True,
            "merge_commit_sha": "a" * 40,
            "html_url": "https://github.com/justinfowler925/alicia/pull/89",
            "head": {"ref": branch},
            "title": title,
            "body": "",
        },
    }


def test_merged_pr_creates_verified_diff_linked_to_latest_run() -> None:
    store = _store()
    work_item = WorkItem(title="REV-521 auto-capture Evidence from GitHub")
    store.save(work_item)
    run = Run(actor="atlas6-worker", work_item_id=work_item.id)
    store.save(run)

    result = GitHubEvidenceReceiver(store).handle(
        "pull_request",
        _merged_pr(branch="justinfowler/rev-521-github-evidence-automation"),
        delivery_id="delivery-pr-89",
    )

    assert result.status == "captured"
    evidence = store.get(Evidence, result.evidence_ids[0])
    assert evidence is not None
    assert evidence.type == EvidenceType.DIFF
    assert evidence.content_ref.endswith("/pull/89")
    assert evidence.verified is True
    assert evidence.verified_by == VERIFIER
    assert evidence.captured_by == VERIFIER
    assert evidence.captured_by_kind == "automated_verifier"
    assert evidence.linked_object_id == run.id
    assert evidence.id in store.get(Run, run.id).evidence_refs
    assert evidence.id in store.get(WorkItem, work_item.id).evidence_refs


def test_failed_ci_creates_unverified_run_output_linked_to_work_item_without_run() -> None:
    store = _store()
    work_item = WorkItem(title="REV-521 webhook receiver")
    store.save(work_item)

    result = GitHubEvidenceReceiver(store).handle(
        "workflow_run",
        {
            "action": "completed",
            "repository": {"full_name": "justinfowler925/alicia"},
            "workflow_run": {
                "id": 123,
                "head_sha": "b" * 40,
                "head_branch": "justinfowler/rev-521-github-evidence-automation",
                "html_url": "https://github.com/justinfowler925/alicia/actions/runs/123",
                "conclusion": "failure",
                "name": "CI",
            },
        },
        delivery_id="delivery-run-123",
    )

    assert result.status == "captured"
    evidence = store.get(Evidence, result.evidence_ids[0])
    assert evidence is not None
    assert evidence.type == EvidenceType.RUN_OUTPUT
    assert evidence.verified is False
    assert evidence.verified_by is None
    assert evidence.captured_by == VERIFIER
    assert evidence.linked_object_id == work_item.id
    assert evidence.id in store.get(WorkItem, work_item.id).evidence_refs


def test_receiver_requires_an_allowlisted_automated_verifier_not_owner_or_free_text() -> None:
    store = _store()
    work_item = WorkItem(title="REV-521 webhook receiver")
    store.save(work_item)

    owner_result = GitHubEvidenceReceiver(store, verifier_identity=OWNER).handle(
        "pull_request",
        _merged_pr(branch="justinfowler/rev-521-github-evidence-automation"),
        delivery_id="delivery-owner",
    )
    free_text_result = GitHubEvidenceReceiver(store, verifier_identity="spoofed-verifier").handle(
        "pull_request",
        _merged_pr(branch="justinfowler/rev-521-github-evidence-automation"),
        delivery_id="delivery-spoofed",
    )

    assert owner_result.status == "ignored"
    assert free_text_result.status == "ignored"
    assert store.list(Evidence) == []


def test_unrecognized_branch_is_ignored_without_orphan_evidence() -> None:
    store = _store()
    work_item = WorkItem(title="REV-521 webhook receiver")
    store.save(work_item)

    result = GitHubEvidenceReceiver(store).handle(
        "check_suite",
        {
            "action": "completed",
            "repository": {"full_name": "justinfowler925/alicia"},
            "check_suite": {
                "id": 999,
                "head_sha": "c" * 40,
                "head_branch": "justinfowler/rev-999-not-known",
                "html_url": "https://github.com/justinfowler925/alicia/runs/999",
                "conclusion": "success",
            },
        },
        delivery_id="delivery-unknown",
    )

    assert result.status == "ignored"
    assert store.list(Evidence) == []
    assert store.get(WorkItem, work_item.id).evidence_refs == []
