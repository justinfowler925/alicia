from datetime import UTC, datetime, timedelta

import pytest

from brutus.canon import (
    DEFAULT_IDENTITY_REGISTRY,
    CanonError,
    CanonStore,
    Decision,
    Evidence,
    EvidenceType,
    InboxItem,
    WorkItem,
    WorkItemState,
    transition,
)
from brutus.workflow_control import (
    attach_delivery_receipt,
    batch_feedback,
    bind_delivery_policy,
    build_efficiency_scorecard,
    evaluate_delivery_receipts,
    evaluate_route_guard,
    find_work_by_binding,
    load_delivery_policy,
    persist_feedback_batches,
    post_work_event,
    route_work,
)

OWNER = DEFAULT_IDENTITY_REGISTRY.owner_identity


def _advance_to_validation(work: WorkItem) -> None:
    transition(work, WorkItemState.CLARIFICATION, OWNER)
    transition(work, WorkItemState.PLANNING, OWNER)
    transition(work, WorkItemState.DECISION, OWNER)
    decision = Decision(
        question="ship?",
        chosen_option="yes",
        rationale="requested",
        decided_by=OWNER,
    )
    transition(work, WorkItemState.EXECUTION, OWNER, decision=decision)
    transition(work, WorkItemState.VALIDATION, OWNER)


def _policy(tmp_path):
    policy_dir = tmp_path / ".codex"
    policy_dir.mkdir()
    (policy_dir / "delivery.yaml").write_text(
        """schema_version: 1
repository: github.com/example/repo
requirements:
  - id: tests
    kind: test
    command: pytest -q
    freshness_hours: 2
  - id: git
    kind: git
    probe: origin/main contains the commit
    freshness_hours: 2
  - id: production
    kind: readback
    probe: version endpoint returns the commit
    freshness_hours: 1
""",
        encoding="utf-8",
    )
    return load_delivery_policy(tmp_path)


def _receipt(work, requirement_id, *, captured_at=None, digest="sha-1", result="pass"):
    return Evidence(
        type=EvidenceType.RUN_OUTPUT if requirement_id == "tests" else EvidenceType.DIFF,
        captured_by="independent-verifier",
        captured_by_kind="worker",
        linked_object_id=work.id,
        content_ref=f"receipt:{requirement_id}",
        verified=True,
        verified_by=OWNER,
        requirement_id=requirement_id,
        result=result,
        artifact_digest=digest,
        target="local-brutus" if requirement_id in {"deploy", "production"} else None,
        captured_at=captured_at or datetime.now(UTC),
    )


def test_delivery_policy_is_bound_and_all_receipts_are_required(tmp_path):
    loaded = _policy(tmp_path)
    work = bind_delivery_policy(WorkItem(title="Ship it"), loaded)
    work.target_artifact_digest = "sha-1"
    evidence = [_receipt(work, requirement) for requirement in work.delivery_requirements]

    proof = evaluate_delivery_receipts(work, evidence)

    assert proof.ok is True
    assert proof.required == proof.passed == 3
    assert work.delivery_policy_digest == loaded.digest


def test_delivery_gate_rejects_missing_stale_failed_and_wrong_artifact(tmp_path):
    work = bind_delivery_policy(WorkItem(title="Ship it"), _policy(tmp_path))
    work.target_artifact_digest = "sha-1"
    evidence = [
        _receipt(work, "tests", captured_at=datetime.now(UTC) - timedelta(hours=3)),
        _receipt(work, "git", result="fail"),
        _receipt(work, "production", digest="other-sha"),
    ]

    proof = evaluate_delivery_receipts(work, evidence)

    assert proof.ok is False
    assert proof.stale == ("tests",)
    assert proof.failed == ("git",)
    assert proof.mismatched == ("production",)


def test_canon_validation_to_review_enforces_bound_delivery_policy(tmp_path):
    work = bind_delivery_policy(WorkItem(title="Ship it"), _policy(tmp_path))
    work.target_artifact_digest = "sha-1"
    _advance_to_validation(work)
    generic = [
        Evidence(
            type=EvidenceType.DIFF,
            captured_by="verifier",
            linked_object_id=work.id,
            content_ref="commit:sha-1",
            verified=True,
            verified_by=OWNER,
        ),
        Evidence(
            type=EvidenceType.RUN_OUTPUT,
            captured_by="verifier",
            linked_object_id=work.id,
            content_ref="pytest:pass",
            verified=True,
            verified_by=OWNER,
        ),
    ]
    with pytest.raises(CanonError, match="repository delivery policy failed"):
        transition(work, WorkItemState.REVIEW, OWNER, evidence=generic)

    policy_receipts = [_receipt(work, requirement) for requirement in work.delivery_requirements]
    transition(work, WorkItemState.REVIEW, OWNER, evidence=[*generic, *policy_receipts])
    assert work.state == WorkItemState.REVIEW


def test_router_continues_exact_canon_work_and_does_not_create_duplicate():
    store = CanonStore()
    existing = WorkItem(title="Build workflow control")
    store.save(existing)
    projects = [{"name": "brutus", "path": "/repo/brutus", "project_id": "github/brutus"}]

    result = route_work(
        "Build workflow control",
        store=store,
        projects=projects,
        repo_hint="brutus",
        create=True,
    )

    assert result.action == "continue"
    assert result.work_item_id == existing.id
    assert len(store.list(WorkItem)) == 1


def test_router_rejects_broad_root_and_selects_active_worktree():
    store = CanonStore()
    projects = [
        {
            "name": "brutus",
            "path": "/repo/brutus",
            "project_id": "github/brutus",
            "is_worktree": False,
            "last_commit_epoch": 10,
        },
        {
            "name": "brutus-feature",
            "path": "/repo/brutus-wt/feature",
            "project_id": "github/brutus",
            "is_worktree": True,
            "last_commit_epoch": 20,
        },
    ]
    sessions = [{"id": "codex:1", "title": "Other", "state": "running", "cwd": "/repo/brutus-wt/feature"}]

    result = route_work(
        "Fix Brutus delivery",
        store=store,
        projects=projects,
        sessions=sessions,
        repo_hint="/repo/brutus",
        cwd=str(__import__("pathlib").Path.home() / "Projects"),
    )

    assert result.action == "create"
    assert result.broad_root_rejected is True
    assert result.repository_path == "/repo/brutus"
    assert result.worktree_path == "/repo/brutus-wt/feature"
    assert result.needs_worktree is False


@pytest.mark.parametrize(
    ("prompt_text", "expected"),
    [
        ("Fix Brutus delivery", "/repo/brutus"),
        ("Run tests in Brutus", "/repo/brutus"),
        ("Deploy Brutus", "/repo/brutus"),
        ("Update Fowler Brain rules", "/repo/fowler-brain"),
        ("Fix the Fowler Brain workflow", "/repo/fowler-brain"),
        ("Commit Fowler Brain changes", "/repo/fowler-brain"),
        ("Build CRO Suite reporting", "/repo/cro-suite"),
        ("Patch CRO Suite collector", "/repo/cro-suite"),
        ("Run tests in CRO Suite", "/repo/cro-suite"),
        ("Update Nucleus", "/repo/nucleus"),
        ("Ship Nucleus", "/repo/nucleus"),
        ("Refactor Nucleus", "/repo/nucleus"),
        ("Fix Atlas Direct", "/repo/atlas-direct"),
        ("Deploy Atlas Direct", "/repo/atlas-direct"),
        ("Change Atlas Direct", "/repo/atlas-direct"),
        ("Build Hollywood", "/repo/hollywood"),
        ("Publish Hollywood", "/repo/hollywood"),
        ("Update Hollywood", "/repo/hollywood"),
        ("Fix Clarity", "/repo/clarity"),
        ("Release Clarity", "/repo/clarity"),
        ("Write Clarity tests", "/repo/clarity"),
        ("Implement SHINE", "/repo/shine"),
        ("Install SHINE", "/repo/shine"),
        ("Patch SHINE", "/repo/shine"),
    ],
)
def test_route_guard_routes_labeled_repository_mutations(prompt_text, expected):
    names = ("brutus", "fowler-brain", "cro-suite", "nucleus", "atlas-direct", "hollywood", "clarity", "shine")
    projects = [
        {"name": name, "path": f"/repo/{name}", "project_id": f"github/{name}"}
        for name in names
    ]

    decision = evaluate_route_guard(
        {"cwd": str(__import__("pathlib").Path.home() / "Projects"), "prompt": prompt_text},
        projects=projects,
    )

    assert decision.allow is False
    assert decision.repository_path == expected


def test_route_guard_blocks_ambiguous_mutation_but_allows_read_only_broad_work():
    broad = str(__import__("pathlib").Path.home() / "Projects")
    projects = [{"name": "brutus", "path": "/repo/brutus", "project_id": "github/brutus"}]

    ambiguous = evaluate_route_guard({"cwd": broad, "prompt": "fix this"}, projects=projects)
    inspection = evaluate_route_guard(
        {"cwd": broad, "prompt": "inspect all repositories for stale branches"}, projects=projects
    )

    assert ambiguous.allow is False
    assert ambiguous.repository_path == ""
    assert inspection.allow is True


def test_router_does_not_treat_unrelated_ticket_as_a_match():
    store = CanonStore()
    projects = [{"name": "brutus", "path": "/repo/brutus", "project_id": "github/brutus"}]
    result = route_work(
        "Fix Brutus delivery",
        store=store,
        projects=projects,
        tickets=[{"id": "REV-1", "relationship": "unrelated", "status": "open"}],
        repo_hint="brutus",
    )
    assert result.action == "create"
    assert result.ticket_id == ""


def test_binding_lookup_resolves_only_one_active_item():
    store = CanonStore()
    work = WorkItem(title="Linear work", bindings={"linear": "REV-42"})
    store.save(work)
    assert find_work_by_binding(store, "linear", "REV-42").id == work.id
    with pytest.raises(CanonError, match="no active work item"):
        find_work_by_binding(store, "linear", "REV-404")


def test_work_events_are_idempotent_and_cannot_change_canon_state():
    store = CanonStore()
    work = WorkItem(title="Bound work")
    store.save(work)
    first, created = post_work_event(
        store,
        work_item_id=work.id,
        event_id="atlas:event:1",
        event_type="completed",
        surface="atlas",
        source_locator="atlas://jobs/1",
    )
    second, created_again = post_work_event(
        store,
        work_item_id=work.id,
        event_id="atlas:event:1",
        event_type="completed",
        surface="atlas",
        source_locator="atlas://jobs/1",
    )
    assert created is True and created_again is False
    assert first.id == second.id
    assert store.get(WorkItem, work.id).state == WorkItemState.TRIAGE
    with pytest.raises(CanonError, match="different content"):
        post_work_event(
            store,
            work_item_id=work.id,
            event_id="atlas:event:1",
            event_type="failed",
            surface="atlas",
            source_locator="atlas://jobs/1",
        )


def test_feedback_batches_only_shared_acceptance_and_delivery_paths():
    reports = [
        {
            "id": "a",
            "raw_capture": "button dead",
            "source": "voice",
            "surface": "checkout",
            "acceptance_contract": "button submits",
            "release_path": "web",
            "rollback_path": "revert",
            "severity": "high",
            "verified": True,
        },
        {
            "id": "b",
            "raw_capture": "button label",
            "source": "screenshot",
            "surface": "checkout",
            "acceptance_contract": "button submits",
            "release_path": "web",
            "rollback_path": "revert",
            "severity": "low",
            "verified": True,
        },
        {
            "id": "c",
            "raw_capture": "native button",
            "source": "text",
            "surface": "checkout",
            "acceptance_contract": "button submits",
            "release_path": "ios",
            "rollback_path": "app-store",
            "severity": "high",
            "verified": True,
        },
    ]
    batches = batch_feedback(reports)
    assert len(batches) == 2
    assert sorted(len(batch.report_ids) for batch in batches) == [1, 2]
    assert sum(len(batch.dispositions) for batch in batches) == 3


def test_feedback_persistence_preserves_raw_reports_and_is_idempotent():
    store = CanonStore()
    reports = [
        {
            "id": "voice-1",
            "raw_capture": "button dead exactly as spoken",
            "source": "voice",
            "surface": "checkout",
            "acceptance_contract": "button submits",
            "release_path": "web",
            "rollback_path": "revert",
            "verified": True,
        },
        {
            "id": "image-1",
            "raw_capture": "screenshot://capture-1",
            "source": "screenshot",
            "surface": "checkout",
            "acceptance_contract": "button submits",
            "release_path": "web",
            "rollback_path": "revert",
            "verified": True,
        },
    ]
    first = persist_feedback_batches(store, reports)
    second = persist_feedback_batches(store, reports)
    assert [item.id for item in first] == [item.id for item in second]
    assert len(store.list(WorkItem)) == 1
    assert len(store.list(InboxItem)) == 2
    assert store.get(InboxItem, "voice-1").raw_capture == "button dead exactly as spoken"
    assert set(first[0].feedback_refs) == {"voice-1", "image-1"}


def test_structured_delivery_receipt_is_verified_and_linked(tmp_path):
    store = CanonStore()
    work = bind_delivery_policy(WorkItem(title="Ship it"), _policy(tmp_path))
    store.save(work)
    receipt = attach_delivery_receipt(
        store,
        work_item_id=work.id,
        requirement_id="tests",
        evidence_type="run_output",
        content_ref="pytest: 817 passed",
        result="pass",
        artifact_digest="sha-1",
    )
    assert receipt.verified is True
    assert receipt.requirement_id == "tests"
    assert receipt.id in store.get(WorkItem, work.id).evidence_refs
    with pytest.raises(CanonError, match="not required"):
        attach_delivery_receipt(
            store,
            work_item_id=work.id,
            requirement_id="invented",
            evidence_type="log",
            content_ref="nope",
            result="pass",
        )


def test_scorecard_exposes_denominators_and_source_gaps():
    work = WorkItem(
        title="Done",
        state=WorkItemState.CLOSURE,
        delivery_requirements=["tests"],
        target_artifact_digest="sha-1",
    )
    receipt = _receipt(work, "tests")
    sessions = [
        {
            "title": "Check project status",
            "cwd": str(__import__("pathlib").Path.home() / "Projects"),
            "transcript_excerpt": "please continue and finish",
        },
        {"title": "Build router", "cwd": "/repo/brutus", "transcript_excerpt": "done"},
        {"title": "Build router", "cwd": "/repo/brutus", "transcript_excerpt": "done"},
    ]
    card = build_efficiency_scorecard(
        sessions,
        [work],
        [receipt],
        source_coverage={"codex": True, "slack": False},
        window_start=datetime(2026, 9, 1, tzinfo=UTC),
        window_end=datetime.now(UTC),
    )
    assert card["gaps"] == ["slack"]
    assert card["metrics"]["broad_root_starts"]["denominator"] == 3
    assert card["metrics"]["duplicate_intent_members"]["numerator"] == 2
    assert card["metrics"]["delivery_receipt_coverage"]["rate"] == 1.0
    assert card["metrics"]["meaningless_notifications"]["status"] == "insufficient_evidence"
