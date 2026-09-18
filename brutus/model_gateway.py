"""Read-only execution gateway for explicit Brutus model profiles."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

from .claude import ask_claude
from .config import BrutusCfg, ClaudeCfg
from .openai_chat import run_openai_chat
from .model_profiles import ModelCandidate, ModelProfile, select_model_profile


def default_profile(name: str, cfg: BrutusCfg) -> ModelProfile:
    """Return the explicit provider order for a workload, never a hidden fallback."""
    candidates = {
        "conversation": (
            ModelCandidate("openai", cfg.openai.model, {"conversation", "low_latency"}, priority=10),
        ),
        "supervisor": (
            ModelCandidate("claude", "sonnet", {"structured_output", "session_reasoning"}, priority=10),
            ModelCandidate("codex", "gpt-5.6-sol", {"structured_output", "session_reasoning"}, priority=20),
        ),
        "frontier": (
            ModelCandidate("codex", "gpt-5.6-sol", {"frontier_reasoning", "unfog"}, priority=10),
            ModelCandidate("claude", "opus", {"frontier_reasoning", "unfog"}, priority=20),
        ),
        "builder": (
            ModelCandidate("codex", "gpt-5.6-sol", {"workspace_tools", "code_editing"}, priority=20),
            ModelCandidate("claude", "sonnet", {"workspace_tools", "code_editing"}, priority=30),
        ),
    }
    return ModelProfile(name, candidates[name])


def run_profile(
    cfg: BrutusCfg,
    profile_name: str,
    prompt: str,
    *,
    cwd: str | Path | None = None,
) -> dict[str, Any]:
    candidate = select_model_profile(default_profile(profile_name, cfg))
    root = Path(cwd or cfg.openai.reasoning_root).expanduser().resolve()
    if candidate.provider == "openai":
        result = run_openai_chat(cfg, prompt, repo_hint=str(root), system=_system(profile_name))
    elif candidate.provider == "claude":
        claude = ClaudeCfg(
            enabled=True,
            model=candidate.model,
            timeout_s=max(120, cfg.claude.timeout_s),
            max_tokens=cfg.claude.max_tokens,
            effort="high" if profile_name == "frontier" else "low",
        )
        result = ask_claude(claude, prompt, system=_system(profile_name))
    elif candidate.provider == "codex":
        result = _run_codex(candidate, prompt, root, profile_name)
    else:
        return {"ok": False, "error": f"unsupported provider {candidate.provider}"}
    return {**result, "profile": profile_name, "provider": candidate.provider, "requested_model": candidate.model}


def judge_with_profile(
    cfg: BrutusCfg, profile_name: str, prompt: str, *, cwd: str | Path | None = None
) -> str:
    """Adapter for strict judgment seams; failure triggers their deterministic fallback."""
    result = run_profile(cfg, profile_name, prompt, cwd=cwd)
    if not result.get("ok"):
        raise RuntimeError(str(result.get("error") or f"{profile_name} profile unavailable"))
    return str(result.get("reply") or "")


def _system(profile_name: str) -> str:
    if profile_name == "frontier":
        return (
            "Apply Unfog as a rigorous work compiler. Distinguish evidence from inference, "
            "preserve every supplied constraint, resolve competing hypotheses, and return a "
            "concrete execution contract. Do not mutate files, tickets, or external systems."
        )
    return "Judge the work from supplied evidence. Do not summarize the transcript or invent facts."


def _run_codex(
    candidate: ModelCandidate, prompt: str, cwd: Path, profile_name: str
) -> dict[str, Any]:
    binary = shutil.which("codex")
    if not binary:
        return {"ok": False, "error": "Codex CLI is unavailable."}
    command = [
        binary, "exec", "--ephemeral", "--sandbox", "read-only", "--json",
        "--model", candidate.model, "-C", str(cwd),
        f"{_system(profile_name)}\n\n{prompt}",
    ]
    try:
        proc = subprocess.run(command, capture_output=True, text=True, timeout=600, check=False)
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "Codex CLI timed out."}
    except OSError as exc:
        return {"ok": False, "error": f"Codex CLI failed to start: {exc}"}
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "unknown failure").strip()[-600:]
        return {"ok": False, "error": f"Codex CLI exited {proc.returncode}: {detail}"}
    reply = ""
    for line in (proc.stdout or "").splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        item = event.get("item") or {}
        if item.get("type") == "agent_message":
            reply = str(item.get("text") or item.get("content") or "").strip() or reply
    if not reply:
        return {"ok": False, "error": "Codex CLI returned no agent message."}
    return {"ok": True, "reply": reply[:12000], "model": candidate.model, "transport": "codex_cli"}
