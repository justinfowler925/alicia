"""Wave 3 backends — ask_model / ask_claude / ask_atlas6 slim + Atlas-down."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

from brutus.claude import ask_claude
from brutus.config import BrutusCfg, ClaudeCfg, OpenAICfg
from brutus.openai_chat import build_chat_prompt, run_openai_chat
from brutus.tools import _ask_atlas6, _slim_atlas6_result, build_default_registry


def test_slim_atlas6_drops_digest_noise():
    slim = _slim_atlas6_result(
        {
            "reply": "Registered REV-999",
            "skill": {
                "route": "ledger",
                "ticket_id": "REV-999",
                "digest_markdown": "# WIP\n" + ("x" * 5000),
            },
        }
    )
    assert slim["ok"] is True
    assert slim["reply"] == "Registered REV-999"
    assert slim["skill"]["ticket_id"] == "REV-999"
    assert "digest_markdown" not in slim["skill"]


def test_ask_atlas6_unreachable_is_honest():
    client = MagicMock()
    client.chat.side_effect = ConnectionError("connection refused")
    out = _ask_atlas6(client, "status please")
    assert out["ok"] is False
    assert out["atlas6_unreachable"] is True
    assert "ask_model" in out["hint"]
    assert "ask_claude" in out["hint"]


def test_ask_atlas6_slims_success():
    client = MagicMock()
    client.chat.return_value = {
        "reply": "done",
        "skill": {"route": "worker", "digest_markdown": "NOPE"},
    }
    out = _ask_atlas6(client, "go")
    assert out["ok"] is True
    assert out["reply"] == "done"
    assert "digest_markdown" not in out.get("skill", {})


def test_run_openai_chat_disabled():
    cfg = BrutusCfg(openai=OpenAICfg(enabled=False))
    out = run_openai_chat(cfg, "refactor me")
    assert out["ok"] is False
    assert out["error"] == "OpenAI backend is disabled."


def test_run_openai_chat_requires_a_key(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    cfg = BrutusCfg(openai=OpenAICfg(enabled=True))
    out = run_openai_chat(cfg, "refactor me")
    assert out["ok"] is False
    assert "OPENAI_API_KEY" in out["error"]


def test_run_openai_chat_success(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    cfg = BrutusCfg(openai=OpenAICfg(enabled=True, model="gpt-5.5", timeout_s=30))
    seen: dict = {}

    class _Resp:
        status_code = 200

        @staticmethod
        def json():
            return {"choices": [{"message": {"content": "Renamed the helper."}}]}

    def fake_post(url, *, json, headers, timeout):
        seen["url"] = url
        seen["model"] = json["model"]
        seen["prompt"] = json["messages"][-1]["content"]
        seen["auth"] = headers["Authorization"]
        return _Resp()

    monkeypatch.setattr("brutus.openai_chat.httpx.post", fake_post)
    out = run_openai_chat(cfg, "refactor the resolver", repo_hint="brutus")

    assert out["ok"] is True
    assert "Renamed the helper" in out["reply"]
    assert seen["model"] == "gpt-5.5"
    assert seen["auth"] == "Bearer sk-test"
    assert "refactor the resolver" in seen["prompt"]
    assert "brutus" in seen["prompt"]


def test_run_openai_chat_surfaces_http_errors(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    cfg = BrutusCfg(openai=OpenAICfg(enabled=True))

    class _Resp:
        status_code = 401
        text = "invalid api key"

    monkeypatch.setattr("brutus.openai_chat.httpx.post", lambda *a, **k: _Resp())
    out = run_openai_chat(cfg, "hello")
    assert out["ok"] is False
    assert "401" in out["error"]


def test_build_chat_prompt_no_verdict_contract():
    p = build_chat_prompt("fix the bug")
    assert "fix the bug" in p
    assert "VERDICT" not in p


def test_ask_claude_disabled():
    cfg = BrutusCfg(claude=ClaudeCfg(enabled=False))
    out = ask_claude(cfg, "draft a reply")
    assert out["ok"] is False
    assert out["error"] == "Claude is unavailable."


def test_ask_claude_missing_cli(monkeypatch):
    monkeypatch.setattr("brutus.claude.shutil.which", lambda _name: None)
    cfg = BrutusCfg(claude=ClaudeCfg(enabled=True, api_key=""))
    out = ask_claude(cfg, "draft a reply")
    assert out["ok"] is False
    assert out["error"] == "Claude CLI is unavailable."


def test_ask_claude_cli_success(monkeypatch):
    monkeypatch.setattr("brutus.claude.shutil.which", lambda _name: "/opt/homebrew/bin/claude")
    cfg = BrutusCfg(claude=ClaudeCfg(enabled=True, api_key=""))
    payload = {
        "is_error": False,
        "result": "Here is a draft reply.",
        "stop_reason": "end_turn",
        "modelUsage": {"claude-sonnet-5": {"inputTokens": 2}},
    }
    proc = subprocess.CompletedProcess([], 0, stdout=json.dumps(payload), stderr="")
    with patch("brutus.claude.subprocess.run", return_value=proc) as run:
        out = ask_claude(cfg, "draft a reply", system="Be direct.")
    assert out["ok"] is True
    assert out["reply"] == "Here is a draft reply."
    assert out["transport"] == "claude_cli"
    command = run.call_args.args[0]
    assert command[:3] == ["/opt/homebrew/bin/claude", "-p", "draft a reply"]
    assert command[command.index("--tools") + 1] == ""
    assert "--no-session-persistence" in command
    assert command[command.index("--system-prompt") + 1] == "Be direct."


def test_ask_claude_cli_failure(monkeypatch):
    monkeypatch.setattr("brutus.claude.shutil.which", lambda _name: "/opt/homebrew/bin/claude")
    cfg = BrutusCfg(claude=ClaudeCfg(enabled=True))
    proc = subprocess.CompletedProcess([], 1, stdout="", stderr="subscription exhausted")
    with patch("brutus.claude.subprocess.run", return_value=proc):
        out = ask_claude(cfg, "draft a reply")
    assert out["ok"] is False
    assert "subscription exhausted" in out["error"]


def test_registry_wires_backends():
    client = MagicMock()
    client.chat.side_effect = ConnectionError("down")
    reg = build_default_registry(client, cfg=BrutusCfg(openai=OpenAICfg(enabled=False)))
    names = {t["name"] for t in reg.list_schemas()}
    assert "ask_model" in names
    assert "ask_claude" not in names
    assert "ask_atlas6" not in names
    atlas = reg.call("ask_atlas6", {"message": "hi"})
    assert atlas["ok"] is False
    client.chat.assert_not_called()
