"""Alicia manager specialist routing."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from alicia.config import AliciaCfg, LocalLLMCfg
from alicia.gate import GATED, describe
from alicia.specialist_router import choose_specialist, org_chart, preview, route_specialist
from alicia.tools import build_default_registry


def test_org_chart_names_alicia_manager():
    chart = org_chart()
    assert chart["manager"] == "alicia"
    assert chart["specialists"]["atlas"].lower().startswith("salesforce")
    assert "gemma" in chart["specialists"]["forge"].lower()


def test_choose_specialist_keywords():
    assert choose_specialist("Fix Apex trigger on Contact")[0] == "atlas"
    assert choose_specialist("scrape Zoom meeting notes")[0] == "scout"
    assert choose_specialist("transcribe this video")[0] == "hollywood"
    assert choose_specialist("refactor the README")[0] == "forge"
    assert choose_specialist("anything", specialist="scout") == ("scout", "explicit")
    with pytest.raises(ValueError):
        choose_specialist("x", specialist="astra")


def test_preview_is_dry_run():
    out = preview("deploy a Flow in Partial")
    assert out["ok"] is True
    assert out["dry_run"] is True
    assert out["specialist"] == "atlas"
    assert out["would_launch"] is True


def test_live_forge_enqueue(tmp_path, monkeypatch):
    fake_run = {"id": "local-abc", "status": "queued"}
    enqueue = MagicMock(return_value=fake_run)
    kick = MagicMock()
    monkeypatch.setattr("alicia.forge_local.enqueue", enqueue)
    monkeypatch.setattr("alicia.forge_local.kick", kick)
    monkeypatch.setattr(
        "alicia.specialist_router._RECEIPTS", tmp_path / "receipts"
    )
    out = route_specialist("write a helper function", dry_run=False, cwd=str(tmp_path))
    assert out["ok"] is True
    assert out["dry_run"] is False
    assert out["specialist"] == "forge"
    assert out["result"]["run_id"] == "local-abc"
    enqueue.assert_called_once()
    kick.assert_called_once()


def test_live_atlas_uses_studio_agent(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "alicia.specialist_router._RECEIPTS", tmp_path / "receipts"
    )
    monkeypatch.setattr(
        "alicia.specialist_router._STUDIO_AGENT", tmp_path / "studio-agent"
    )
    (tmp_path / "studio-agent").write_text("#!/bin/sh\n")
    (tmp_path / "studio-agent").chmod(0o755)

    class Result:
        returncode = 0
        stdout = '{"id":"run1","agent":"atlas","status":"queued","cwd":"/tmp"}'
        stderr = ""

    with patch("alicia.specialist_router.subprocess.run", return_value=Result()) as run:
        out = route_specialist(
            "Investigate Contact Flow errors",
            specialist="atlas",
            dry_run=False,
            cwd=str(tmp_path),
        )
    assert out["specialist"] == "atlas"
    assert out["result"]["run_id"] == "run1"
    cmd = run.call_args.args[0]
    assert "launch" in cmd and "atlas" in cmd and "--read-only" in cmd


def test_registry_exposes_manager_tools():
    cfg = AliciaCfg(local_llm=LocalLLMCfg(enabled=True, model="m"), atlas_enabled=False)
    reg = build_default_registry(MagicMock(), cfg=cfg, read_only=False)
    names = {t["name"] for t in reg.list_schemas()}
    assert "org_chart" in names
    assert "preview_specialist_route" in names
    assert "route_specialist" in names
    assert "ask_atlas6" not in names


def test_route_specialist_is_gated():
    assert "route_specialist" in GATED
    screen, spoken = describe(
        "route_specialist",
        {"task": "fix Flow", "specialist": "atlas", "dry_run": False},
    )
    assert "LIVE" in screen
    assert "atlas" in spoken
