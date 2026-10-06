"""Manager org-chart completion: Canon binding, registry, assign loop, earn, attribution."""

from __future__ import annotations

from pathlib import Path

import pytest

from alicia.canon import CanonStore
from alicia.canon_binding import (
    backfill_orphan_work_items,
    classify_project,
    intake_work,
    project_rollups,
    seed_canon_projects,
)
from alicia.config import AliciaCfg, ClaudeCfg, OpenAICfg
from alicia.earned_autonomy import EarnCfg, may_auto_run, record_success, snapshot
from alicia.manager_loop import assign_and_run
from alicia.openai_lane import run_openai
from alicia.platform_registry import (
    assert_honest_attribution,
    choose_lane,
    registry_snapshot,
)


@pytest.fixture
def canon_tmp(tmp_path, monkeypatch):
    db = tmp_path / "canon.sqlite"
    monkeypatch.setattr("alicia.canon_binding.canon_db_path", lambda: db)
    monkeypatch.setattr("alicia.manager_loop.canon_db_path", lambda: db)
    monkeypatch.setattr("alicia.paths.canon_db_path", lambda: db)
    monkeypatch.setattr(
        "alicia.manager_loop._RECEIPTS", tmp_path / "receipts"
    )
    monkeypatch.setattr(
        "alicia.specialist_router._RECEIPTS", tmp_path / "receipts"
    )
    return db


def test_classify_project_keywords():
    assert classify_project("polish Shine tokens") == "proj-shine"
    assert classify_project("fix Salesforce Flow", hint="") == "proj-atlas"
    assert classify_project("random thought", hint="Shine") == "proj-shine"
    assert classify_project("hello") == "proj-alicia"


def test_intake_sets_project_id_and_rollup(canon_tmp):
    out = intake_work(
        "Add a Shine button token for primary CTA",
        source="test",
        project_hint="Shine",
    )
    assert out["ok"] is True
    assert out["project_id"] == "proj-shine"
    assert out["assignee"] == "alicia"
    assert out["owned_by"] == "alicia"
    rollups = project_rollups()
    shine = next(r for r in rollups if r["id"] == "proj-shine")
    assert shine["work_item_count"] >= 1
    assert out["work_item_id"] in shine["work_item_ids"]


def test_backfill_orphans(canon_tmp):
    store = CanonStore(canon_tmp)
    try:
        seed_canon_projects(store)
        from alicia.canon.models import WorkItem

        orphan = WorkItem(title="Scrape Zoom my notes into Scout", description="")
        store.save(orphan)
    finally:
        store.close()
    result = backfill_orphan_work_items(limit=10)
    assert result["bound"] >= 1
    assert any(i["project_id"] == "proj-scout" for i in result["items"])


def test_platform_registry_and_attribution():
    snap = registry_snapshot()
    assert "forge_local" in snap["platforms"]
    assert "openai" in snap["platforms"]
    assert snap["platforms"]["cursor_cloud"]["hosted"] is True
    assert snap["platforms"]["openai"]["earn_eligible"] is False
    lane, reason = choose_lane("what is 2+2")
    assert lane == "forge_local"
    with pytest.raises(ValueError, match="Forge/Gemma|executor"):
        assert_honest_attribution(
            {"executor": "forge", "model": "gpt-4.1"}, lane="openai"
        )
    assert_honest_attribution(
        {"executor": "openai", "model": "gpt-4.1-mini", "attribution": "OpenAI"},
        lane="openai",
    )


def test_openai_default_off_and_honest(monkeypatch):
    cfg = AliciaCfg(openai=OpenAICfg(enabled=False))
    out = run_openai(cfg, "hello")
    assert out["ok"] is False
    assert out["executor"] == "openai"
    assert out["http_attempted"] is False

    cfg2 = AliciaCfg(openai=OpenAICfg(enabled=True, model="gpt-test"))

    def fake(message, oai):
        return {
            "ok": True,
            "executor": "openai",
            "attribution": "OpenAI",
            "reply": "pong",
            "model": oai.model,
            "http_attempted": True,
        }

    out2 = run_openai(cfg2, "ping", prompt_fn=fake)
    assert out2["ok"] is True
    assert out2["executor"] == "openai"
    assert "gemma" not in out2["model"].lower()


def test_assign_forge_join_handback(canon_tmp, monkeypatch):
    from alicia import forge_local as local
    from alicia import specialist_router as router

    monkeypatch.setattr(local, "STATE", canon_tmp.parent / "forge-state")
    monkeypatch.setattr(local, "probe_local_model", lambda **kwargs: None)
    monkeypatch.setattr(
        local,
        "completion",
        lambda messages, tools: {"role": "assistant", "content": "forge-pong"},
    )

    def inline_kick():
        with local.db() as c:
            ids = [r["id"] for r in c.execute("SELECT id FROM runs WHERE status='queued'")]
        for run_id in ids:
            local.worker(run_id)

    monkeypatch.setattr(local, "kick", inline_kick)
    monkeypatch.setattr(
        "alicia.manager_loop.merged_work_surface",
        lambda timeout_s=4.0: {
            "headline": "test",
            "source": "canon",
        },
    )

    cfg = AliciaCfg()
    out = assign_and_run(
        cfg,
        task="Reply with forge-pong for Alicia manager proof",
        lane="forge_local",
        project_hint="Forge",
        join=True,
    )
    assert out["executor"] == "forge"
    assert out["lane"] == "forge_local"
    assert out["project_id"] == "proj-forge"
    assert out["work_item_id"]
    assert out["handback"]["executor"] == "forge"
    assert Path(out["receipt_path"]).is_file()
    receipts = router.latest_receipts(3)
    assert any(r.get("executor") == "forge" for r in receipts)


def test_assign_openai_opt_in(canon_tmp, monkeypatch):
    monkeypatch.setattr(
        "alicia.manager_loop.merged_work_surface",
        lambda timeout_s=4.0: {"headline": "test", "source": "canon"},
    )

    def fake(message, oai):
        return {
            "ok": True,
            "executor": "openai",
            "attribution": "OpenAI",
            "reply": "hosted-ok",
            "model": "gpt-test",
            "http_attempted": True,
        }

    monkeypatch.setattr(
        "alicia.openai_lane._default_http",
        lambda message, oai: fake(message, oai),
    )
    cfg = AliciaCfg(openai=OpenAICfg(enabled=True, model="gpt-test"))
    out = assign_and_run(
        cfg,
        task="Summarize this in one word: ok",
        lane="openai",
        project_hint="Alicia",
    )
    assert out["ok"] is True
    assert out["executor"] == "openai"
    assert out["result"]["reply"] == "hosted-ok"


def test_local_failure_does_not_call_openai(canon_tmp, monkeypatch):
    called = []

    def boom(*a, **k):
        called.append("openai")
        raise AssertionError("must not call openai")

    monkeypatch.setattr("alicia.openai_lane.run_openai", boom)
    monkeypatch.setattr(
        "alicia.manager_loop._run_forge",
        lambda cfg, message, cwd="": {
            "ok": False,
            "executor": "forge",
            "error": "Local Gemma down. No hosted/Cursor fallback.",
        },
    )
    monkeypatch.setattr(
        "alicia.manager_loop.merged_work_surface",
        lambda timeout_s=4.0: {"headline": "test", "source": "canon"},
    )
    out = assign_and_run(
        AliciaCfg(openai=OpenAICfg(enabled=True)),
        task="hello",
        lane="forge_local",
    )
    assert out["ok"] is False
    assert out["executor"] == "forge"
    assert called == []


def test_earn_rules(tmp_path):
    ledger = tmp_path / "earn.json"
    assert record_success("openai", "forge_trivial", path=ledger)["recorded"] is False
    assert record_success("cursor_cloud", "forge_trivial", path=ledger)["recorded"] is False
    assert record_success("forge_local", "atlas_deploy", path=ledger)["recorded"] is False
    r = record_success("forge_local", "forge_trivial", path=ledger)
    assert r["recorded"] is True
    record_success("forge_local", "forge_trivial", path=ledger)
    record_success("forge_local", "forge_trivial", path=ledger)
    denied = may_auto_run(
        "forge_local",
        "forge_trivial",
        cfg=EarnCfg(enabled=True, auto_run=False, threshold=3),
        path=ledger,
    )
    assert denied["auto_run"] is False
    earned = may_auto_run(
        "forge_local",
        "forge_trivial",
        cfg=EarnCfg(enabled=True, auto_run=True, threshold=3),
        path=ledger,
    )
    assert earned["auto_run"] is True
    hosted = may_auto_run(
        "openai",
        "forge_trivial",
        cfg=EarnCfg(enabled=True, auto_run=True, threshold=1),
        path=ledger,
    )
    assert hosted["auto_run"] is False
    snap = snapshot(path=ledger)
    assert "forge_local:forge_trivial" in snap["counts"]


def test_claude_platform_lane_gate(monkeypatch):
    from alicia.tools import _ask_claude

    cfg = AliciaCfg(claude=ClaudeCfg(enabled=True, platform_lane=False))
    out = _ask_claude(cfg, "hi")
    assert out["ok"] is False
    assert out["executor"] == "claude"
    assert "platform_lane" in out["error"]

    monkeypatch.setattr(
        "alicia.claude.ask_claude",
        lambda cfg, message, **kw: {
            "ok": True,
            "reply": "hi",
            "model": "claude-sonnet",
            "transport": "claude_cli",
        },
    )
    cfg2 = AliciaCfg(claude=ClaudeCfg(enabled=True, platform_lane=True))
    out2 = _ask_claude(cfg2, "hi")
    assert out2["ok"] is True
    assert out2["executor"] == "claude"
