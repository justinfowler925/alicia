"""Gated Cursor Cloud Agents client — opt-in only, never a Forge/Scout fallback.

Default off. Attribution must name executor ``cursor_cloud``; claiming forge/gemma
for a cloud run is a hard failure.
"""

from __future__ import annotations

import os
import re
import time
from pathlib import Path
from typing import Any, Callable

from .config import AliciaCfg, CursorCloudCfg

EXECUTOR = "cursor_cloud"
FORBIDDEN_ATTRIBUTION = frozenset(
    {
        "forge",
        "gemma",
        "scout",
        "alicia",
        "/users/jfstudio/.local/share/atlas-models/gemma4-31b-it-4bit",
    }
)
PERSONAL_OWNER = "justinfowler925"
_CLOUD_HTTP = False  # flipped true only when a cloud RPC is attempted


PromptFn = Callable[..., Any]


def _cfg(cfg: AliciaCfg | None) -> CursorCloudCfg:
    return (cfg.cursor_cloud if cfg and cfg.cursor_cloud else CursorCloudCfg())


def enabled(cfg: AliciaCfg | None) -> bool:
    return bool(_cfg(cfg).enabled)


def _api_key() -> str:
    return (os.environ.get("CURSOR_API_KEY") or os.environ.get("CURSOR_APIKEY") or "").strip()


def personal_repo_url(url: str) -> bool:
    text = (url or "").strip().lower()
    if not text:
        return False
    return PERSONAL_OWNER in text and "sfdc" not in text and "clearspeed" not in text


def assert_cloud_attribution(payload: dict[str, Any]) -> None:
    """Fail hard if a cloud receipt mislabels as Forge/Gemma/Scout/Alicia."""
    executor = str(payload.get("executor") or "").strip().lower()
    model = str(payload.get("model") or "").strip().lower()
    if executor != EXECUTOR:
        raise ValueError(f"cloud receipt executor must be {EXECUTOR!r}, got {executor!r}")
    if executor in FORBIDDEN_ATTRIBUTION or any(
        bad != EXECUTOR and bad in executor for bad in ("forge", "gemma", "scout")
    ):
        raise ValueError(f"cloud receipt executor must not claim a non-cloud lane: {executor!r}")
    if (
        model in FORBIDDEN_ATTRIBUTION
        or "forge" in model
        or "gemma" in model
        or model.endswith("gemma4-31b-it-4bit")
    ):
        raise ValueError(f"cloud receipt model must not look like Forge/Gemma: {model!r}")


def run_cursor_cloud(
    cfg: AliciaCfg,
    message: str,
    *,
    repo_url: str = "",
    starting_ref: str = "",
    prompt_fn: PromptFn | None = None,
) -> dict[str, Any]:
    """Launch one cloud agent for an explicit Cursor-shaped ask. Never auto-called."""
    global _CLOUD_HTTP
    cloud_cfg = _cfg(cfg)
    if not cloud_cfg.enabled:
        return {
            "ok": False,
            "executor": EXECUTOR,
            "error": "Cursor Cloud is disabled (cursor_cloud.enabled=false). Opt-in only.",
            "cloud_http_attempted": False,
        }

    body = (message or "").strip()
    if not body:
        return {
            "ok": False,
            "executor": EXECUTOR,
            "error": "message is required",
            "cloud_http_attempted": False,
        }

    url = (repo_url or cloud_cfg.default_repo_url or "").strip()
    if not personal_repo_url(url):
        return {
            "ok": False,
            "executor": EXECUTOR,
            "error": (
                "repo_url must be a justinfowler925 personal remote "
                "(never sfdc/Clearspeed). Pass repo_url explicitly."
            ),
            "cloud_http_attempted": False,
        }

    api_key = _api_key()
    if not api_key and prompt_fn is None:
        return {
            "ok": False,
            "executor": EXECUTOR,
            "error": "CURSOR_API_KEY is unset; Cursor Cloud unavailable.",
            "cloud_http_attempted": False,
        }

    started = time.monotonic()
    _CLOUD_HTTP = True
    try:
        if prompt_fn is not None:
            raw = prompt_fn(
                body,
                repo_url=url,
                starting_ref=starting_ref or cloud_cfg.default_starting_ref or "main",
                model=cloud_cfg.model,
                api_key=api_key,
            )
        else:
            raw = _run_sdk(
                body,
                repo_url=url,
                starting_ref=starting_ref or cloud_cfg.default_starting_ref or "main",
                model=cloud_cfg.model,
                api_key=api_key,
                timeout_s=float(cloud_cfg.timeout_s or 900),
            )
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False,
            "executor": EXECUTOR,
            "error": str(exc),
            "repo_url": url,
            "cloud_http_attempted": True,
            "agents_spawned": 0,
        }

    agent_id = str(raw.get("agent_id") or raw.get("bc_id") or "")
    reply = str(raw.get("reply") or raw.get("result") or "")
    payload = {
        "ok": bool(raw.get("ok", True)) and bool(reply.strip() or agent_id),
        "executor": EXECUTOR,
        "model": str(raw.get("model") or cloud_cfg.model or "cursor-cloud"),
        "agent_id": agent_id,
        "bc_id": agent_id,
        "reply": reply,
        "repo_url": url,
        "status": str(raw.get("status") or "completed"),
        "elapsed_s": round(time.monotonic() - started, 2),
        "cloud_http_attempted": True,
        "agents_spawned": int(raw.get("agents_spawned") or 1),
        "second_spawn_reason": str(raw.get("second_spawn_reason") or ""),
        "attribution": "Cursor Cloud",
    }
    if payload["agents_spawned"] > 1 and not payload["second_spawn_reason"]:
        payload["ok"] = False
        payload["error"] = "multiple cloud agents without an explicit second_spawn_reason"
    try:
        assert_cloud_attribution(payload)
    except ValueError as exc:
        payload["ok"] = False
        payload["error"] = str(exc)
    return payload


def _run_sdk(
    message: str,
    *,
    repo_url: str,
    starting_ref: str,
    model: str,
    api_key: str,
    timeout_s: float,
) -> dict[str, Any]:
    from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout

    try:
        from cursor_sdk import Agent, AgentOptions, CloudAgentOptions, CloudRepository
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError("cursor-sdk unavailable for Cursor Cloud") from exc

    def _call() -> dict[str, Any]:
        agent = Agent.create(
            AgentOptions(
                api_key=api_key,
                model=model,
                name="alicia-ask-cursor-cloud",
                cloud=CloudAgentOptions(
                    repos=[CloudRepository(url=repo_url, starting_ref=starting_ref)],
                    auto_create_pr=False,
                ),
            )
        )
        try:
            agent_id = str(getattr(agent, "agent_id", None) or getattr(agent, "id", "") or "")
            result = agent.send(message).wait()
            status = getattr(result, "status", None) or ""
            text = getattr(result, "result", None) or getattr(result, "text", None) or str(result)
            return {
                "ok": True,
                "agent_id": agent_id,
                "reply": str(text),
                "status": str(status or "completed"),
                "model": model,
                "agents_spawned": 1,
            }
        finally:
            with_context = getattr(agent, "close", None)
            if callable(with_context):
                with_context()

    pool = ThreadPoolExecutor(max_workers=1)
    try:
        fut = pool.submit(_call)
        try:
            return fut.result(timeout=timeout_s)
        except FuturesTimeout as exc:
            raise TimeoutError(f"Cursor Cloud timed out after {timeout_s:.0f}s") from exc
    finally:
        pool.shutdown(wait=False, cancel_futures=True)


def cloud_http_attempted() -> bool:
    """Test helper: whether any cloud RPC was attempted in this process."""
    return bool(_CLOUD_HTTP)


def reset_cloud_http_flag() -> None:
    global _CLOUD_HTTP
    _CLOUD_HTTP = False


def remote_url_for_path(path: str | Path) -> str:
    """Best-effort origin URL for a checkout (empty if unavailable)."""
    root = Path(path).expanduser()
    if not root.is_dir():
        return ""
    try:
        import subprocess

        out = subprocess.run(
            ["git", "-C", str(root), "remote", "get-url", "origin"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except Exception:  # noqa: BLE001
        return ""
    if out.returncode != 0:
        return ""
    return (out.stdout or "").strip()


_SFDC_RE = re.compile(r"(?:^|/)sfdc(?:-wt)?(?:/|$)", re.I)
