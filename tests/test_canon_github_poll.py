"""Authenticated GitHub poller wiring and durable idempotency."""

from __future__ import annotations

import importlib.util
from pathlib import Path

from alicia.canon import CanonStore, Evidence, WorkItem


def _module():
    path = Path(__file__).resolve().parents[1] / "scripts" / "canon-github-poll.py"
    spec = importlib.util.spec_from_file_location("canon_github_poll", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_default_repository_is_the_public_personal_repo(monkeypatch):
    monkeypatch.delenv("ALICIA_GITHUB_REPOSITORY", raising=False)

    assert _module().REPOSITORY == "justinfowler925/alicia"


def test_poll_captures_pr_and_ci_once(tmp_path, monkeypatch):
    db = tmp_path / "canon.sqlite"
    store = CanonStore(db)
    item = WorkItem(title="REV-888 trusted GitHub poll")
    store.save(item)
    store.close()
    poller = _module()
    monkeypatch.setattr(poller, "canon_db_path", lambda: db)

    def fake_gh(args):
        if args[:2] == ["pr", "list"]:
            return [{
                "number": 88,
                "url": "https://github.com/justinfowler925/alicia/pull/88",
                "headRefName": "codex/rev-888-trusted-poll",
                "title": "REV-888 trusted poll",
                "body": "",
                "mergeCommit": {"oid": "a" * 40},
                "mergedAt": "2026-08-24T01:00:00Z",
            }]
        return [{
            "databaseId": 8800,
            "headBranch": "codex/rev-888-trusted-poll",
            "headSha": "a" * 40,
            "url": "https://github.com/justinfowler925/alicia/actions/runs/8800",
            "conclusion": "success",
            "status": "completed",
            "workflowName": "alicia-ci",
            "updatedAt": "2026-08-24T01:02:00Z",
        }]

    monkeypatch.setattr(poller, "gh_json", fake_gh)
    first = poller.poll()
    second = poller.poll()

    assert first["captured"] == 2
    assert second["duplicate"] == 2
    checked = CanonStore(db)
    evidence = checked.list(Evidence)
    checked.close()
    assert len(evidence) == 2
    assert {row.source_object_id for row in evidence} == {"88", "8800"}
    assert all(row.source_repository == "justinfowler925/alicia" for row in evidence)
    assert all(row.source_sha == "a" * 40 for row in evidence)
