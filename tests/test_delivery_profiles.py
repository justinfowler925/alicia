"""Delivery profile contracts through YAML, persistence, evaluation and CLI."""

import hashlib
import json
from datetime import UTC, datetime, timedelta

import pytest
import yaml

from brutus import __main__ as cli
from brutus.canon import CanonError, CanonStore, Evidence, WorkItem, WorkItemState, transition
from brutus.canon.identity import DEFAULT_IDENTITY_REGISTRY, IdentityRegistry
from brutus.canon.models import Approval, ApprovalStatus, WorkItemType
from brutus.workflow_control import (
    attach_delivery_receipt,
    bind_delivery_policy,
    evaluate_delivery_receipts,
    load_delivery_policy,
)


def policy_body():
    return {
        "schema_version": 2,
        "repository": "github.com/example/brain",
        "requirements": [
            {"id": "tests", "kind": "test", "freshness_hours": 2},
            {"id": "git", "kind": "git"},
            {"id": "owner-review", "kind": "other", "probe": "Owner reviews the diff"},
        ],
        "profiles": {
            "launcher": {"requirements": []},
            "same-gates": {"requirements": []},
            "salesforce": {
                "requirements": [
                    {"id": "sandbox", "kind": "readback", "target": "partial"},
                ]
            },
        },
    }


def write_policy(tmp_path, body=None):
    path = tmp_path / ".codex" / "delivery.yaml"
    path.parent.mkdir(exist_ok=True)
    path.write_text(yaml.safe_dump(body if body is not None else policy_body()))
    return path


def bind(tmp_path, profile="launcher"):
    write_policy(tmp_path)
    return bind_delivery_policy(
        WorkItem(title="Launcher task", target_artifact_digest="commit-1"),
        load_delivery_policy(tmp_path, profile=profile),
    )


def attach(store, work, requirement="tests", **kwargs):
    return attach_delivery_receipt(
        store,
        work_item_id=work.id,
        requirement_id=requirement,
        evidence_type="diff" if requirement == "git" else "run_output",
        content_ref="commit:commit-1 / pytest passed",
        result="pass",
        artifact_digest="commit-1",
        target=work.delivery_targets.get(requirement, ""),
        **kwargs,
    )


def invoke(monkeypatch, db, *args):
    monkeypatch.setattr(cli.sys, "argv", ["brutus", "workflow", "--db", str(db), *args])
    cli.main()


def test_v1_raw_digest_legacy_data_and_receipts(tmp_path):
    body = policy_body()
    body["schema_version"] = 1
    del body["profiles"]
    path = write_policy(tmp_path, body)
    loaded = load_delivery_policy(tmp_path)
    assert loaded.digest == hashlib.sha256(path.read_bytes()).hexdigest()
    assert loaded.profile == ""
    legacy = WorkItem.model_validate({"title": "Old record"})
    assert legacy.delivery_policy_profile == ""
    work = bind_delivery_policy(legacy, loaded)
    store = CanonStore(tmp_path / "legacy.db")
    try:
        store.save(work)
        receipts = [attach(store, work, item) for item in work.delivery_requirements]
        for receipt in receipts:
            receipt.metadata = {}  # Real old receipts predate the digest stamp.
            receipt.source_repository = None
            store.save(receipt, authenticated_principal=store.identity_registry.owner_principal())
        assert evaluate_delivery_receipts(store.get(WorkItem, work.id), store.list(Evidence)).ok
    finally:
        store.close()
    with pytest.raises(ValueError, match="profile selection"):
        load_delivery_policy(tmp_path, profile="launcher")
    with pytest.raises(ValueError, match="profile selection"):
        load_delivery_policy(tmp_path, profile="")


@pytest.mark.parametrize("profile", [None, "", "unknown", " launcher", "Launcher", 2, []])
def test_selection_fails_closed(tmp_path, profile):
    write_policy(tmp_path)
    with pytest.raises(ValueError, match="explicit known profile"):
        load_delivery_policy(tmp_path, profile=profile)


@pytest.mark.parametrize(
    "case",
    [
        "profiles-list",
        "profiles-empty",
        "profile-list",
        "missing-requirements",
        "unknown-profile-key",
        "unknown-top-key",
        "unknown-requirement-key",
        "bad-name",
        "blank-name",
        "requirements-null",
        "required-string",
        "nan-age",
        "blank-repository",
        "duplicate-common",
        "duplicate-profile",
        "override-common",
        "missing-test",
        "optional-test",
        "missing-git",
        "optional-git",
        "bad-unselected",
    ],
)
def test_malformed_policies_fail_at_load(tmp_path, case):
    body = policy_body()
    profile = body["profiles"]["launcher"]
    if case == "profiles-list":
        body["profiles"] = []
    elif case == "profiles-empty":
        body["profiles"] = {}
    elif case == "profile-list":
        body["profiles"]["launcher"] = []
    elif case == "missing-requirements":
        profile.clear()
    elif case == "unknown-profile-key":
        profile["override"] = True
    elif case == "unknown-top-key":
        body["default_profile"] = "launcher"
    elif case == "unknown-requirement-key":
        body["requirements"][0]["requiredd"] = False
    elif case in {"bad-name", "blank-name"}:
        body["profiles"]["BAD NAME" if case == "bad-name" else ""] = profile
    elif case == "requirements-null":
        profile["requirements"] = None
    elif case == "required-string":
        body["requirements"][0]["required"] = "false"
    elif case == "nan-age":
        body["requirements"][0]["freshness_hours"] = float("nan")
    elif case == "blank-repository":
        body["repository"] = " "
    elif case == "duplicate-common":
        body["requirements"].append(dict(body["requirements"][0]))
    elif case == "duplicate-profile":
        profile["requirements"] = [{"id": "extra", "kind": "other"}] * 2
    elif case == "override-common":
        profile["requirements"] = [{"id": "tests", "kind": "test", "required": False}]
    elif case.startswith("missing-"):
        body["requirements"] = [r for r in body["requirements"] if r["kind"] != case[8:]]
    elif case.startswith("optional-"):
        next(r for r in body["requirements"] if r["kind"] == case[9:])["required"] = False
    elif case == "bad-unselected":
        body["profiles"]["salesforce"]["unknown"] = True
    write_policy(tmp_path, body)
    with pytest.raises(ValueError):
        load_delivery_policy(tmp_path, profile="launcher")


def test_duplicate_yaml_keys_cannot_hide_common_gates(tmp_path):
    path = write_policy(tmp_path)
    with path.open("a") as output:
        output.write("requirements: []\n")
    with pytest.raises(ValueError, match="unique strings"):
        load_delivery_policy(tmp_path, profile="launcher")


def test_mandatory_requirements_can_live_in_effective_union(tmp_path):
    body = policy_body()
    body["profiles"] = {"launcher": {"requirements": body["requirements"]}}
    body["requirements"] = []
    write_policy(tmp_path, body)
    assert len(load_delivery_policy(tmp_path, profile="launcher").policy.requirements) == 3


def test_binding_survives_store_reopen_and_preserves_common_gates(tmp_path):
    work = bind(tmp_path, "salesforce")
    work.approval_refs = ["existing-approval"]
    db = tmp_path / "canon.db"
    store = CanonStore(db)
    store.save(work)
    store.close()
    store = CanonStore(db)
    try:
        saved = store.get(WorkItem, work.id)
        assert saved.delivery_policy_profile == "salesforce"
        assert saved.delivery_policy_digest == work.delivery_policy_digest
        assert saved.delivery_requirements == ["tests", "git", "owner-review", "sandbox"]
        assert saved.delivery_targets == {"sandbox": "partial"}
        bind_delivery_policy(saved, load_delivery_policy(tmp_path, profile="launcher"))
        store.save(saved)
        assert saved.approval_refs == ["existing-approval"]
        assert saved.delivery_requirements == ["tests", "git", "owner-review"]
        receipts = [attach(store, saved, item) for item in ["tests", "git"]]
        proof = evaluate_delivery_receipts(saved, receipts)
        assert not proof.ok and proof.missing == ("owner-review",)
    finally:
        store.close()


def test_digest_separates_identical_profiles_and_changed_policy_bytes(tmp_path):
    path = write_policy(tmp_path)
    first = load_delivery_policy(tmp_path, profile="launcher")
    second = load_delivery_policy(tmp_path, profile="same-gates")
    assert first.policy == second.policy
    assert first.digest != second.digest
    assert load_delivery_policy(tmp_path, profile="launcher").digest == first.digest
    path.write_bytes(path.read_bytes() + b"# policy revision\n")
    assert load_delivery_policy(tmp_path, profile="launcher").digest != first.digest


@pytest.mark.parametrize(
    ("mutation", "category"),
    [
        ("missing-digest", "mismatched"),
        ("cross-profile", "mismatched"),
        ("wrong-item", "mismatched"),
        ("wrong-repository", "mismatched"),
        ("wrong-artifact", "mismatched"),
        ("wrong-target", "mismatched"),
        ("stale", "stale"),
        ("failed", "failed"),
        ("unverified", "failed"),
    ],
)
def test_receipts_rejected_through_evaluation_and_transition(tmp_path, mutation, category):
    work = bind(tmp_path, "salesforce")
    work.state = WorkItemState.VALIDATION
    store = CanonStore(tmp_path / "canon.db")
    try:
        store.save(work)
        receipts = [attach(store, work, item) for item in work.delivery_requirements]
        assert evaluate_delivery_receipts(work, receipts).ok
        bad = receipts[-1]
        if mutation == "missing-digest":
            bad.metadata.clear()
        elif mutation == "cross-profile":
            bad.metadata["delivery_policy_digest"] = load_delivery_policy(tmp_path, profile="launcher").digest
        elif mutation == "wrong-item":
            bad.linked_object_id = "another-work-item"
        elif mutation == "wrong-repository":
            bad.source_repository = "github.com/other/repo"
        elif mutation == "wrong-artifact":
            bad.artifact_digest = "commit-2"
        elif mutation == "wrong-target":
            bad.target = "production"
        elif mutation == "stale":
            bad.captured_at = datetime.now(UTC) - timedelta(hours=25)
        elif mutation == "failed":
            bad.result = "fail"
        elif mutation == "unverified":
            bad.verified = False
        store.save(bad, authenticated_principal=store.identity_registry.owner_principal())
        persisted = store.list(Evidence)
        proof = evaluate_delivery_receipts(work, persisted)
        assert not proof.ok and "sandbox" in getattr(proof, category)
        with pytest.raises(CanonError, match="repository delivery policy failed"):
            transition(work, WorkItemState.REVIEW, store.identity_registry.owner_identity, evidence=persisted)
    finally:
        store.close()


def test_rebinding_does_not_reuse_receipts_with_overlapping_ids(tmp_path):
    work = bind(tmp_path)
    store = CanonStore(tmp_path / "canon.db")
    try:
        store.save(work)
        receipts = [attach(store, work, item) for item in work.delivery_requirements]
        assert evaluate_delivery_receipts(work, receipts).ok
        bind_delivery_policy(work, load_delivery_policy(tmp_path, profile="same-gates"))
        store.save(work)
        assert not evaluate_delivery_receipts(store.get(WorkItem, work.id), store.list(Evidence)).ok
        fresh = [attach(store, work, item) for item in work.delivery_requirements]
        assert evaluate_delivery_receipts(work, fresh).ok
    finally:
        store.close()


@pytest.mark.parametrize(
    ("actor", "kind"),
    [("", "worker"), (" \n", "human"), (None, "human"), ("codex", "admin"), ("codex", []), ("codex", "")],
)
def test_invalid_capture_provenance_does_not_write(tmp_path, actor, kind):
    work = bind(tmp_path)
    store = CanonStore(tmp_path / "canon.db")
    try:
        store.save(work)
        with pytest.raises(CanonError, match="captured actor"):
            attach(store, work, captured_by=actor, captured_by_kind=kind)
        assert store.list(Evidence) == []
        assert store.get(WorkItem, work.id).evidence_refs == []
    finally:
        store.close()


def test_worker_provenance_does_not_confer_verifier_or_owner_authority(tmp_path, monkeypatch):
    registry = IdentityRegistry(
        owner_identity="owner",
        worker_identities=frozenset({"codex"}),
        automated_verifier_identities=frozenset({"ci"}),
    )
    store = CanonStore(tmp_path / "canon.db", identity_registry=registry)
    work = bind(tmp_path)
    try:
        store.save(work)
        receipt = attach(store, work, captured_by="codex", captured_by_kind="worker")
        assert receipt.captured_by == "codex" and receipt.captured_by_kind == "worker"
        assert receipt.verified_by == "owner"
        worker = registry.worker_principal("codex")
        with pytest.raises(CanonError, match="does not match"):
            store.save(receipt, authenticated_principal=worker)
        receipt.verified_by = "codex"
        with pytest.raises(CanonError, match="not an authenticated verifier"):
            store.save(receipt, authenticated_principal=worker)
        with pytest.raises(CanonError, match="authenticated principal"):
            store.save(receipt)
        foreign = IdentityRegistry(owner_identity="owner").owner_principal()
        receipt.verified_by = "owner"
        with pytest.raises(CanonError, match="not issued by this registry"):
            store.save(receipt, authenticated_principal=foreign)
        work.state = WorkItemState.REVIEW
        configured_worker = DEFAULT_IDENTITY_REGISTRY.worker_principal(
            next(iter(DEFAULT_IDENTITY_REGISTRY.worker_identities))
        )
        with pytest.raises(CanonError, match="not the authenticated owner"):
            transition(
                work,
                WorkItemState.ACCEPTANCE,
                configured_worker.identity,
                authenticated_principal=configured_worker,
            )
        for principal in (worker, registry.verifier_principal("ci")):
            monkeypatch.setattr(registry, "owner_principal", lambda principal=principal: principal)
            with pytest.raises(CanonError, match="not the authenticated owner"):
                attach(store, work, captured_by="codex", captured_by_kind="worker")
        assert len(store.list(Evidence)) == 1
    finally:
        store.close()


def test_profile_does_not_bypass_communication_approval(tmp_path):
    work = bind(tmp_path)
    work.type = WorkItemType.COMMUNICATION
    work.state = WorkItemState.VALIDATION
    store = CanonStore(tmp_path / "canon.db")
    try:
        store.save(work)
        receipts = [attach(store, work, item) for item in work.delivery_requirements]
        assert evaluate_delivery_receipts(work, receipts).ok
        with pytest.raises(CanonError, match="requires an Approval"):
            transition(work, WorkItemState.REVIEW, store.identity_registry.owner_identity, evidence=receipts)
        approval = Approval(
            work_item_id=work.id,
            requested_by="codex",
            scope="send",
            status=ApprovalStatus.GRANTED,
            approved_by=store.identity_registry.owner_identity,
            granted_at=datetime.now(UTC) - timedelta(minutes=1),
        )
        store.save(approval, authenticated_principal=store.identity_registry.owner_principal())
        transition(
            work,
            WorkItemState.REVIEW,
            store.identity_registry.owner_identity,
            evidence=receipts,
            approval=approval,
        )
        assert work.state == WorkItemState.REVIEW
    finally:
        store.close()


def test_cli_policy_receipts_and_proof_round_trip(tmp_path, monkeypatch, capsys):
    write_policy(tmp_path)
    db = tmp_path / "cli.db"
    store = CanonStore(db)
    work = WorkItem(title="CLI task", target_artifact_digest="commit-1")
    store.save(work)
    store.close()
    for args in ([], ["--profile", "unknown"]):
        with pytest.raises(ValueError, match="explicit known profile"):
            invoke(monkeypatch, db, "policy", str(tmp_path), *args, "--bind", work.id)
    invoke(monkeypatch, db, "policy", str(tmp_path), "--profile", "launcher", "--bind", work.id)
    policy = json.loads(capsys.readouterr().out)
    assert policy["profile"] == "launcher" and policy["bound_work_item_id"] == work.id
    with pytest.raises(SystemExit) as exc:
        invoke(monkeypatch, db, "delivery", work.id)
    assert exc.value.code == 2
    assert json.loads(capsys.readouterr().out)["missing"] == ["git", "owner-review", "tests"]
    for requirement in ("tests", "git", "owner-review"):
        invoke(
            monkeypatch,
            db,
            "receipt",
            work.id,
            "--requirement-id",
            requirement,
            "--type",
            "run_output",
            "--content-ref",
            "pytest: pass",
            "--result",
            "pass",
            "--artifact-digest",
            "commit-1",
            "--captured-by",
            "codex",
            "--captured-by-kind",
            "worker",
        )
        receipt = json.loads(capsys.readouterr().out)
        assert receipt["captured_by"] == "codex" and receipt["captured_by_kind"] == "worker"
        assert receipt["verified_by"] != "codex"
        assert receipt["metadata"]["delivery_policy_digest"] == policy["digest"]
    invoke(monkeypatch, db, "delivery", work.id)
    assert json.loads(capsys.readouterr().out)["ok"] is True
    invoke(monkeypatch, db, "policy", str(tmp_path), "--profile", "same-gates", "--bind", work.id)
    capsys.readouterr()
    with pytest.raises(SystemExit) as exc:
        invoke(monkeypatch, db, "delivery", work.id)
    assert exc.value.code == 2
    assert len(json.loads(capsys.readouterr().out)["mismatched"]) == 3


def test_cli_defaults_and_invalid_provenance(tmp_path, monkeypatch, capsys):
    work = bind(tmp_path)
    db = tmp_path / "cli.db"
    store = CanonStore(db)
    store.save(work)
    store.close()
    args = [
        "receipt",
        work.id,
        "--requirement-id",
        "tests",
        "--type",
        "run_output",
        "--content-ref",
        "pytest: pass",
        "--result",
        "pass",
    ]
    invoke(monkeypatch, db, *args)
    receipt = json.loads(capsys.readouterr().out)
    assert receipt["captured_by"] == "owner-local-verifier" and receipt["captured_by_kind"] == "human"
    with pytest.raises(SystemExit) as exc:
        invoke(monkeypatch, db, *args, "--captured-by-kind", "automated_verifier")
    assert exc.value.code == 2
    with pytest.raises(CanonError, match="non-empty captured actor"):
        invoke(monkeypatch, db, *args, "--captured-by", " ")


def test_profile_receipts_cannot_be_dated_in_the_future(tmp_path):
    work = bind(tmp_path)
    store = CanonStore(tmp_path / "future.db")
    try:
        store.save(work)
        receipts = [attach(store, work, item) for item in work.delivery_requirements]
        now = datetime.now(UTC)
        assert evaluate_delivery_receipts(work, receipts, now=now).ok
        for receipt in receipts:
            receipt.captured_at = now + timedelta(seconds=1)
        proof = evaluate_delivery_receipts(work, receipts, now=now)
        assert not proof.ok and set(proof.stale) == set(work.delivery_requirements)
    finally:
        store.close()
