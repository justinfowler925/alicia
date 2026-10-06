"""Slice 2: personal Cursor roots; refuse sfdc; no Forge fallback."""

from __future__ import annotations

import subprocess
from pathlib import Path

from alicia.config import AliciaCfg, CursorRunnerCfg
from alicia.cursor_runner import allowed_roots, resolve_cwd, run_cursor_chat
from alicia.tools import _ask_cursor


def _git_personal(repo: Path, name: str = "justinfowler925") -> None:
    subprocess.run(["git", "init", "-q", "-b", "feature/x", str(repo)], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "remote", "add", "origin",
         f"git@github.com:{name}/{repo.name}.git"],
        check=True,
    )


def test_personal_roots_resolve(tmp_path: Path):
    roots = []
    for name in ("alicia", "atlas-direct", "fowler-brain", "local-ai-stack"):
        d = tmp_path / name
        d.mkdir()
        _git_personal(d)
        roots.append(str(d))
    reasoning = tmp_path / "reasoning"
    reasoning.mkdir()
    roots.append(str(reasoning))

    allowed = allowed_roots(roots)
    assert len(allowed) == 5
    assert resolve_cwd("alicia", roots) == (tmp_path / "alicia").resolve()
    assert resolve_cwd("atlas", roots) == (tmp_path / "atlas-direct").resolve()
    assert resolve_cwd("brain", roots) == (tmp_path / "fowler-brain").resolve()
    assert resolve_cwd("local-ai-stack", roots) == (tmp_path / "local-ai-stack").resolve()
    assert resolve_cwd("reasoning", roots) == reasoning.resolve()


def test_sfdc_refused_even_if_configured(tmp_path: Path):
    sfdc = tmp_path / "sfdc"
    sfdc.mkdir()
    _git_personal(sfdc, name="ClearspeedRevOps")
    assert allowed_roots([str(sfdc)]) == []
    assert resolve_cwd("sfdc", [str(sfdc)]) is None
    out = _ask_cursor(AliciaCfg(), "touch nothing", repo_hint="sfdc")
    assert out["ok"] is False
    assert "sfdc" in out["error"].lower()


def test_company_remote_refused(tmp_path: Path):
    other = tmp_path / "alicia"
    other.mkdir()
    _git_personal(other, name="ClearspeedRevOps")
    assert allowed_roots([str(other)]) == []


def test_empty_repo_hint_refused_no_forge_fallback(tmp_path: Path):
    repo = tmp_path / "alicia"
    repo.mkdir()
    _git_personal(repo)
    cfg = AliciaCfg(
        cursor_runner=CursorRunnerCfg(enabled=True, allowlist_roots=[str(repo)])
    )
    out = run_cursor_chat(cfg, "hello", repo_hint="")
    assert out["ok"] is False
    assert "invent" in out["error"].lower() or "required" in out["error"].lower()
    assert "forge" not in out.get("executor", "").lower()


def test_missing_api_key_does_not_mention_forge_as_path(tmp_path: Path, monkeypatch):
    repo = tmp_path / "alicia"
    repo.mkdir()
    _git_personal(repo)
    monkeypatch.delenv("CURSOR_API_KEY", raising=False)
    monkeypatch.delenv("CURSOR_APIKEY", raising=False)
    cfg = AliciaCfg(
        cursor_runner=CursorRunnerCfg(enabled=True, allowlist_roots=[str(repo)])
    )
    out = _ask_cursor(cfg, "inspect README", repo_hint="alicia")
    assert out["ok"] is False
    assert "will not reroute to Forge" in out["error"]
