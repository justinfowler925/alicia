"""Slice 4: gated Cursor Cloud — default off, attribution cannot claim Forge/Gemma."""

from __future__ import annotations

import pytest

from alicia import cursor_cloud
from alicia.config import AliciaCfg, CursorCloudCfg
from alicia.tools import build_default_registry
from alicia.client import AtlasClient


def test_disabled_by_default_no_cloud_http(monkeypatch):
    cursor_cloud.reset_cloud_http_flag()
    cfg = AliciaCfg(cursor_cloud=CursorCloudCfg(enabled=False))
    out = cursor_cloud.run_cursor_cloud(cfg, "do a thing", repo_url="https://github.com/justinfowler925/alicia")
    assert out["ok"] is False
    assert out["executor"] == "cursor_cloud"
    assert out["cloud_http_attempted"] is False
    assert cursor_cloud.cloud_http_attempted() is False


def test_tool_absent_when_flag_off():
    cfg = AliciaCfg(cursor_cloud=CursorCloudCfg(enabled=False))
    reg = build_default_registry(AtlasClient(cfg), cfg=cfg, read_only=False)
    names = set(reg._tools.keys())
    assert "ask_cursor_cloud" not in names
    assert "ask_forge" in names


def test_enabled_labels_cursor_cloud_not_forge(monkeypatch):
    cursor_cloud.reset_cloud_http_flag()
    cfg = AliciaCfg(cursor_cloud=CursorCloudCfg(enabled=True))

    def fake_prompt(message, **kwargs):
        return {
            "ok": True,
            "agent_id": "bc-test-attribution",
            "reply": "cloud did it",
            "status": "completed",
            "model": "composer-2.5",
            "agents_spawned": 1,
        }

    out = cursor_cloud.run_cursor_cloud(
        cfg,
        "patch README typo",
        repo_url="https://github.com/justinfowler925/alicia",
        prompt_fn=fake_prompt,
    )
    assert out["ok"] is True
    assert out["executor"] == "cursor_cloud"
    assert out["bc_id"] == "bc-test-attribution"
    assert out["attribution"] == "Cursor Cloud"
    assert "gemma" not in out["model"].lower()
    assert out["executor"] != "forge"


def test_mislabel_forge_fails_attribution():
    with pytest.raises(ValueError, match="executor"):
        cursor_cloud.assert_cloud_attribution(
            {"executor": "forge", "model": "composer-2.5"}
        )
    with pytest.raises(ValueError, match="Forge/Gemma|gemma"):
        cursor_cloud.assert_cloud_attribution(
            {
                "executor": "cursor_cloud",
                "model": "/Users/jfstudio/.local/share/atlas-models/gemma4-31b-it-4bit",
            }
        )


def test_ask_forge_does_not_call_cloud(monkeypatch, tmp_path):
    from alicia import forge_local as local
    from alicia.tools import _ask_forge

    cursor_cloud.reset_cloud_http_flag()
    monkeypatch.setattr(local, "STATE", tmp_path / "state")
    monkeypatch.setattr(local, "probe_local_model", lambda **kwargs: (_ for _ in ()).throw(
        RuntimeError("Local Gemma at :8081 is unavailable. No hosted/Cursor fallback.")
    ))
    cloud_calls = []

    def boom(*args, **kwargs):
        cloud_calls.append(1)
        raise AssertionError("cloud must not be called from ask_forge")

    monkeypatch.setattr(cursor_cloud, "run_cursor_cloud", boom)
    out = _ask_forge(AliciaCfg(cursor_cloud=CursorCloudCfg(enabled=True)), "hello")
    assert out["ok"] is False
    assert cloud_calls == []
    assert cursor_cloud.cloud_http_attempted() is False
    assert "No Cursor/cloud fill-in" in out.get("hint", "")


def test_sfdc_repo_url_refused():
    cfg = AliciaCfg(cursor_cloud=CursorCloudCfg(enabled=True))
    out = cursor_cloud.run_cursor_cloud(
        cfg,
        "nope",
        repo_url="https://github.com/ClearspeedRevOps/sfdc",
    )
    assert out["ok"] is False
    assert out["cloud_http_attempted"] is False
