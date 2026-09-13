"""Claude Code CLI backend for Brutus completion-only turns.

The CLI uses Justin's authenticated Claude subscription. It runs with every
built-in tool disabled, no session persistence, and no project customizations;
Brutus remains the only process allowed to execute Brutus tools or mutations.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

from .config import BrutusCfg, ClaudeCfg


def _model_alias(model: str) -> str:
    value = (model or "").strip().lower()
    if "opus" in value:
        return "opus"
    if "haiku" in value:
        return "haiku"
    return "sonnet"


def ask_claude(
    cfg: BrutusCfg | ClaudeCfg,
    message: str,
    *,
    system: str = "",
    timeout_s: float | None = None,
) -> dict[str, Any]:
    """Run one read-only Claude CLI completion through subscription auth."""
    claude = cfg.claude if isinstance(cfg, BrutusCfg) else cfg
    if not claude.enabled:
        return {"ok": False, "error": "Claude is unavailable."}
    body = (message or "").strip()
    if not body:
        return {"ok": False, "error": "message is required"}
    binary = shutil.which("claude")
    if not binary:
        for candidate in (
            "/opt/homebrew/bin/claude",
            "/usr/local/bin/claude",
            str(Path.home() / ".local" / "bin" / "claude"),
        ):
            if Path(candidate).is_file():
                binary = candidate
                break
    if not binary:
        return {"ok": False, "error": "Claude CLI is unavailable."}

    sys_prompt = (system or "").strip() or (
        "You are helping Justin via Brutus. Be concise and concrete. "
        "Do not invent ticket states, PRs, or approvals. Plain English."
    )
    command = [
        binary, "-p", body, "--safe-mode", "--tools", "",
        "--no-session-persistence", "--output-format", "json",
        "--model", _model_alias(claude.model), "--effort", claude.effort or "low",
        "--system-prompt", sys_prompt,
    ]
    # Serve exports ANTHROPIC_API_KEY for the Messages brain. When that key is
    # out of credits, Claude CLI inherits it and fails immediately — strip API
    # key vars so the CLI uses Justin's subscription auth instead (William-style:
    # conversation runtime must not share a dead API billing path).
    cli_env = {
        key: value
        for key, value in os.environ.items()
        if key not in {"ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "CLAUDE_API_KEY"}
    }
    budget = float(timeout_s if timeout_s is not None else (claude.timeout_s or 120))
    try:
        proc = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=budget,
            check=False,
            env=cli_env,
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "Claude CLI timed out."}
    except OSError as exc:
        return {"ok": False, "error": f"Claude CLI failed to start: {exc}"}
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "unknown failure").strip()[:300]
        return {"ok": False, "error": f"Claude CLI exited {proc.returncode}: {detail}"}
    try:
        data = json.loads(proc.stdout or "{}")
    except json.JSONDecodeError:
        return {"ok": False, "error": "Claude CLI returned invalid JSON."}
    reply = str(data.get("result") or "").strip()
    if data.get("is_error") or not reply:
        return {
            "ok": False,
            "error": str(data.get("result") or data.get("api_error_status") or "Claude CLI returned no reply"),
        }
    model_usage = data.get("modelUsage") or {}
    alias = _model_alias(claude.model)
    used_model = next(
        (name for name in model_usage if alias in str(name).lower()),
        next(iter(model_usage), alias),
    )
    return {
        "ok": True,
        "reply": reply[:6000],
        "model": used_model,
        "stop_reason": data.get("stop_reason") or data.get("terminal_reason"),
        "transport": "claude_cli",
    }
