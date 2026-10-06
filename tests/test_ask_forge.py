"""ask_forge join: session → Forge → answer; fail-closed; receipt completion."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from alicia import forge_local as local
from alicia import specialist_router as router
from alicia.config import AliciaCfg
from alicia.tools import _ask_forge


@pytest.fixture
def forge_runtime(tmp_path, monkeypatch):
    monkeypatch.setattr(local, "STATE", tmp_path / "state")
    monkeypatch.setattr(router, "_RECEIPTS", tmp_path / "receipts")
    monkeypatch.setattr(local.ToolSession, "discover", lambda self: self.tools)
    return tmp_path


def test_ask_forge_joins_answer(forge_runtime, monkeypatch):
    monkeypatch.setattr(local, "probe_local_model", lambda **kwargs: None)
    monkeypatch.setattr(
        local,
        "completion",
        lambda messages, tools: {"role": "assistant", "content": "pong from gemma"},
    )

    # Run worker inline when kick would spawn a subprocess.
    def inline_kick():
        with local.db() as c:
            ids = [r["id"] for r in c.execute("SELECT id FROM runs WHERE status='queued'")]
        for run_id in ids:
            local.worker(run_id)

    monkeypatch.setattr(local, "kick", inline_kick)
    out = local.ask("Reply with exactly: pong from gemma")
    assert out["ok"] is True
    assert out["executor"] == "forge"
    assert out["model"] == local.MODEL
    assert out["reply"] == "pong from gemma"
    assert out["status"] == "succeeded"


def test_ask_forge_fails_closed_when_8081_down(forge_runtime, monkeypatch):
    def boom(**kwargs):
        raise RuntimeError("Local Gemma at :8081 is unavailable. No hosted/Cursor fallback.")

    monkeypatch.setattr(local, "probe_local_model", boom)
    kicked = []
    monkeypatch.setattr(local, "kick", lambda: kicked.append(1))
    with pytest.raises(RuntimeError, match="No hosted/Cursor fallback"):
        local.ask("hello")
    assert kicked == []


def test_ask_forge_tool_records_receipt(forge_runtime, monkeypatch):
    monkeypatch.setattr(local, "probe_local_model", lambda **kwargs: None)

    def inline_kick():
        with local.db() as c:
            ids = [r["id"] for r in c.execute("SELECT id FROM runs WHERE status='queued'")]
        for run_id in ids:
            local.worker(run_id)

    monkeypatch.setattr(local, "kick", inline_kick)
    monkeypatch.setattr(
        local,
        "completion",
        lambda messages, tools: {"role": "assistant", "content": "joined"},
    )
    result = _ask_forge(AliciaCfg(), "say joined")
    assert result["ok"] is True
    receipts = router.latest_receipts(5)
    assert receipts
    assert receipts[0]["result"]["status"] == "succeeded"
    assert receipts[0]["run_status"] == "succeeded"
    assert receipts[0]["accepted"] is False


def test_receipt_no_longer_queued_after_success(forge_runtime, monkeypatch):
    monkeypatch.setattr(
        local,
        "completion",
        lambda messages, tools: {"role": "assistant", "content": "done"},
    )

    def inline_kick():
        with local.db() as c:
            ids = [r["id"] for r in c.execute("SELECT id FROM runs WHERE status='queued'")]
        for run_id in ids:
            local.worker(run_id)

    monkeypatch.setattr(local, "kick", inline_kick)
    enqueued = router.route_specialist("finish this", specialist="forge", dry_run=False)
    # Enqueue receipt starts queued; kick runs the worker to succeeded.
    assert enqueued["result"]["run_id"]
    synced = router.latest_receipts(1)[0]
    assert synced["result"]["status"] == "succeeded"
    assert synced["run_status"] == "succeeded"
    assert synced["accepted"] is False


def test_ask_forge_rejects_wrong_model(forge_runtime, monkeypatch):
    monkeypatch.setattr(local, "probe_local_model", lambda **kwargs: None)
    run = local.enqueue("forge", "x", cwd=str(forge_runtime), work_item="bad-model")
    # Simulate a terminal run with the wrong model path recorded.
    with local.db() as c:
        c.execute("UPDATE runs SET status=?, model=?, finished=? WHERE id=?",
                  ("succeeded", "gpt-6-astra", 1.0, run["id"]))
    (local.STATE / "runs" / run["id"] / "answer.md").write_text("nope")
    monkeypatch.setattr(local, "enqueue", lambda *a, **k: run)
    monkeypatch.setattr(local, "kick", lambda: None)
    monkeypatch.setattr(local, "await_run", lambda *a, **k: local.get(run["id"]))
    with pytest.raises(RuntimeError, match="unexpected model"):
        local.ask("x")
