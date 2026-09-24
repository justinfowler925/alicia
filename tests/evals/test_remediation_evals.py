"""Hostile acceptance probes for the Alicia remediation.

These tests were written before the implementation and are deliberately aimed
at the transport/operations layers that the existing 623-test suite did not
exercise.
"""

from __future__ import annotations

import hashlib
import hmac
import inspect
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from alicia.canon import CanonStore, Evidence, IdentityRegistry, WorkItem
from alicia.config import AliciaCfg
from alicia.github_evidence import GitHubEvidenceReceiver
from alicia.server import create_app


def _client(tmp_path: Path, monkeypatch) -> TestClient:
    monkeypatch.setenv("ALICIA_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("ALICIA_CANON_DB_PATH", str(tmp_path / "canon.sqlite"))
    monkeypatch.setenv("ALICIA_OWNER_TOKEN", "eval-owner-token")
    monkeypatch.setenv("ALICIA_GITHUB_WEBHOOK_SECRET", "eval-webhook-secret")
    with patch("alicia.server.AtlasClient") as atlas:
        atlas.return_value = MagicMock()
        return TestClient(create_app(AliciaCfg(watchdog_enabled=False), start_watchdog=False))


def test_owner_mutation_rejects_reachability_as_identity(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    unauthenticated = client.post(
        "/api/canon/inbox",
        json={"raw_capture": "forged local owner action", "source": "eval"},
    )
    wrong = client.post(
        "/api/canon/inbox",
        headers={"X-Alicia-Owner-Token": "wrong"},
        json={"raw_capture": "wrong token", "source": "eval"},
    )
    valid = client.post(
        "/api/canon/inbox",
        headers={"X-Alicia-Owner-Token": "eval-owner-token"},
        json={"raw_capture": "real owner action", "source": "eval"},
    )
    assert unauthenticated.status_code == 401
    assert wrong.status_code == 401
    assert valid.status_code == 200


def test_owner_browser_session_requires_csrf(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    paired = client.post("/api/auth/session", json={"token": "eval-owner-token"})
    assert paired.status_code == 200
    csrf = paired.json()["csrf"]
    missing_csrf = client.post(
        "/api/canon/inbox",
        json={"raw_capture": "cookie alone", "source": "eval"},
    )
    valid = client.post(
        "/api/canon/inbox",
        headers={"X-Alicia-CSRF": csrf},
        json={"raw_capture": "cookie and csrf", "source": "eval"},
    )
    assert missing_csrf.status_code == 403
    assert valid.status_code == 200


def test_unsigned_and_modified_github_payloads_are_rejected(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    payload = {"action": "completed", "repository": {"full_name": "justinfowler925/alicia"}}
    raw = json.dumps(payload, separators=(",", ":")).encode()
    signature = "sha256=" + hmac.new(b"eval-webhook-secret", raw, hashlib.sha256).hexdigest()

    unsigned = client.post(
        "/webhooks/github",
        content=raw,
        headers={"content-type": "application/json", "X-GitHub-Event": "workflow_run", "X-GitHub-Delivery": "d1"},
    )
    modified = client.post(
        "/webhooks/github",
        content=raw + b" ",
        headers={"content-type": "application/json", "X-GitHub-Event": "workflow_run", "X-GitHub-Delivery": "d2", "X-Hub-Signature-256": signature},
    )
    signed = client.post(
        "/webhooks/github",
        content=raw,
        headers={"content-type": "application/json", "X-GitHub-Event": "workflow_run", "X-GitHub-Delivery": "d3", "X-Hub-Signature-256": signature},
    )
    assert unsigned.status_code == 401
    assert modified.status_code == 401
    assert signed.status_code == 202


def test_ambiguous_ticket_key_never_links_first_match(monkeypatch):
    monkeypatch.setenv("ALICIA_GITHUB_REPOSITORIES", "justinfowler925/alicia")
    registry = IdentityRegistry(
        owner_identity="owner",
        automated_verifier_identities=frozenset({"github"}),
    )
    store = CanonStore(identity_registry=registry)
    store.save(WorkItem(title="REV-777 first"))
    store.save(WorkItem(title="REV-777 second"))
    receiver = GitHubEvidenceReceiver(store, verifier_identity="github")
    payload = {
        "action": "closed",
        "repository": {"full_name": "justinfowler925/alicia"},
        "pull_request": {
            "id": 77,
            "merged": True,
            "merge_commit_sha": "a" * 40,
            "html_url": "https://github.com/justinfowler925/alicia/pull/77",
            "head": {"ref": "codex/rev-777-fix"},
            "title": "REV-777 fix",
            "body": "",
        },
    }
    kwargs = {"delivery_id": "ambiguous-1"} if "delivery_id" in inspect.signature(receiver.handle).parameters else {}
    result = receiver.handle("pull_request", payload, **kwargs)
    assert result.status == "ignored"
    assert store.list(Evidence) == []


def test_operations_are_shipped_not_documented_only():
    root = Path(__file__).resolve().parents[2]
    deploy = (root / "scripts" / "deploy.sh").read_text(encoding="utf-8")
    assert "dirty deployed checkout" in deploy
    assert "check-deploy-drift.sh" in deploy
    assert (root / "scripts" / "canon-backup.py").is_file()
    assert (root / "launchd" / "com.clearspeed.alicia-canon-backup.plist").is_file()
