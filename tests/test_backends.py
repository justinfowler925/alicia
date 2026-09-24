"""Wave 3 backends — ask_cursor / ask_claude / ask_atlas6 slim + Atlas-down."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

from alicia.claude import ask_claude
from alicia.config import AliciaCfg, ClaudeCfg, CursorRunnerCfg
from alicia.cursor_runner import build_chat_prompt, run_cursor_chat
from alicia.tools import _ask_atlas6, _slim_atlas6_result, build_default_registry


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
    assert "ask_cursor" in out["hint"]
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


def test_run_cursor_chat_disabled():
    cfg = AliciaCfg(cursor_runner=CursorRunnerCfg(enabled=False))
    out = run_cursor_chat(cfg, "refactor me")
    assert out["ok"] is False
    assert out["error"] == "Cursor runner is unavailable."


def test_run_cursor_chat_success(tmp_path: Path):
    repo = tmp_path / "alicia"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "feature/x", str(repo)], check=True)
    cfg = AliciaCfg(
        cursor_runner=CursorRunnerCfg(enabled=True, allowlist_roots=[str(repo)], timeout_s=30)
    )

    def fake_prompt(prompt, *, cwd, model, api_key):
        assert "UNTRUSTED" not in prompt  # chat prompt, not queue envelope
        assert "refactor the resolver" in prompt
        assert Path(cwd) == repo.resolve()
        return {"status": "completed", "result": "Renamed the helper and added a test."}

    out = run_cursor_chat(
        cfg, "refactor the resolver", repo_hint="alicia", prompt_fn=fake_prompt
    )
    assert out["ok"] is True
    assert "Renamed the helper" in out["reply"]
    assert out["cwd"] == str(repo.resolve())


def test_run_cursor_chat_refuses_sfdc(tmp_path: Path):
    sfdc = tmp_path / "sfdc"
    sfdc.mkdir()
    cfg = AliciaCfg(
        cursor_runner=CursorRunnerCfg(enabled=True, allowlist_roots=[str(sfdc)])
    )
    out = run_cursor_chat(cfg, "deploy", repo_hint="sfdc", prompt_fn=lambda *a, **k: {})
    assert out["ok"] is False
    assert "allowlist" in out["error"].lower() or "resolve" in out["error"].lower()


def test_build_chat_prompt_no_verdict_contract():
    p = build_chat_prompt("fix the bug")
    assert "fix the bug" in p
    assert "CURSOR_VERDICT" not in p or "No CURSOR_VERDICT" in p


def test_ask_claude_disabled():
    cfg = AliciaCfg(claude=ClaudeCfg(enabled=False))
    out = ask_claude(cfg, "draft a reply")
    assert out["ok"] is False
    assert out["error"] == "Claude is unavailable."


def test_ask_claude_missing_cli(monkeypatch):
    monkeypatch.setattr("alicia.claude.shutil.which", lambda _name: None)
    monkeypatch.setattr("alicia.claude.Path.is_file", lambda _path: False)
    cfg = AliciaCfg(claude=ClaudeCfg(enabled=True, api_key=""))
    out = ask_claude(cfg, "draft a reply")
    assert out["ok"] is False
    assert out["error"] == "Claude CLI is unavailable."


def test_ask_claude_cli_success(monkeypatch):
    monkeypatch.setattr("alicia.claude.shutil.which", lambda _name: "/opt/homebrew/bin/claude")
    cfg = AliciaCfg(claude=ClaudeCfg(enabled=True, api_key=""))
    payload = {
        "is_error": False,
        "result": "Here is a draft reply.",
        "stop_reason": "end_turn",
        "modelUsage": {"claude-sonnet-5": {"inputTokens": 2}},
    }
    proc = subprocess.CompletedProcess([], 0, stdout=json.dumps(payload), stderr="")
    with patch("alicia.claude.subprocess.run", return_value=proc) as run:
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
    monkeypatch.setattr("alicia.claude.shutil.which", lambda _name: "/opt/homebrew/bin/claude")
    cfg = AliciaCfg(claude=ClaudeCfg(enabled=True))
    proc = subprocess.CompletedProcess([], 1, stdout="", stderr="subscription exhausted")
    with patch("alicia.claude.subprocess.run", return_value=proc):
        out = ask_claude(cfg, "draft a reply")
    assert out["ok"] is False
    assert "subscription exhausted" in out["error"]


def test_registry_wires_backends(tmp_path):
    from alicia.memory import MemoryStore
    from alicia.todos import TodoStore

    client = MagicMock()
    client.chat.side_effect = ConnectionError("down")
    reg = build_default_registry(
        client, cfg=AliciaCfg(cursor_runner=CursorRunnerCfg(enabled=False)),
        memory=MemoryStore(tmp_path / "memory.sqlite"), todos=TodoStore(tmp_path / "todos.sqlite"),
    )
    names = {t["name"] for t in reg.list_schemas()}
    assert "ask_cursor" in names
    assert "ask_claude" not in names
    assert "ask_atlas6" not in names
    atlas = reg.call("ask_atlas6", {"message": "hi"})
    assert atlas["ok"] is False
    client.chat.assert_not_called()
