"""Local LLM config parsing."""

from pathlib import Path

import yaml

from brutus.config import BrutusCfg, LocalLLMCfg, load_config


def test_default_cfg():
    cfg = BrutusCfg()
    assert "8767" in cfg.atlas6_url
    assert cfg.local_llm is not None
    assert cfg.local_llm.enabled is False
    assert cfg.local_llm.router_url == "http://127.0.0.1:7901"
    assert cfg.watchdog_enabled is True
    assert cfg.serve_port == 8768
    assert cfg.claude.model == "claude-sonnet-5"
    assert cfg.claude.max_tokens == 8192
    assert cfg.voice.ear_enabled is False


def test_load_config_falls_back():
    cfg = load_config()
    assert cfg.atlas6_url
    assert cfg.atlas_enabled is False
    assert cfg.openai.reasoning_root == "~/.brutus/app"
    assert cfg.timeout_s > 0
    assert cfg.local_llm.model


def test_parse_local_llm_block(tmp_path: Path):
    p = tmp_path / "config.yaml"
    p.write_text(
        """
atlas6_url: "http://studio:8767"
watchdog_enabled: false
watchdog_interval_s: 30
stale_inflight_minutes: 20
local_llm:
  enabled: true
  router_url: "http://127.0.0.1:7901"
  model: "mlx-community/Qwen3-14B-4bit"
  timeout_s: 90
"""
    )
    cfg = load_config(p)
    assert cfg.atlas6_url == "http://studio:8767"
    assert cfg.local_llm.enabled is True
    assert cfg.local_llm.timeout_s == 90.0
    assert cfg.local_llm.model == "mlx-community/Qwen3-14B-4bit"
    assert cfg.watchdog_enabled is False
    assert cfg.watchdog_interval_s == 30.0
    assert cfg.stale_inflight_minutes == 20


def test_local_llm_defaults():
    llm = LocalLLMCfg()
    assert llm.router_url.endswith("7901")


def test_parse_claude_block(tmp_path: Path):
    p = tmp_path / "config.yaml"
    p.write_text(
        """
claude:
  enabled: true
  model: "claude-sonnet-5"
  timeout_s: 90
  max_tokens: 1024
"""
    )
    cfg = load_config(p)
    assert cfg.claude.enabled is True
    assert cfg.claude.model == "claude-sonnet-5"
    assert cfg.claude.timeout_s == 90.0
    assert cfg.claude.max_tokens == 1024


def test_system_wide_ear_requires_separate_opt_in(tmp_path: Path):
    p = tmp_path / "config.yaml"
    p.write_text("voice:\n  enabled: true\n")
    assert load_config(p).voice.ear_enabled is False

    p.write_text("voice:\n  enabled: true\n  ear_enabled: true\n")
    assert load_config(p).voice.ear_enabled is True
