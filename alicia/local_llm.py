"""OpenAI-compatible local LLM client (mlx_lm.server on laptop)."""

from __future__ import annotations

import os
import re
import subprocess
import time
from typing import Any

import httpx

from .config import AliciaCfg, LocalLLMCfg


class LocalLLMError(Exception):
    """Local router unreachable or returned an error."""


_THINK_RE = re.compile(r"<think>.*?</think>", re.S)


def _strip_thinking(text: str) -> str:
    """Drop Qwen3 <think> blocks — mlx_lm.server returns them inline.

    The chat template can start generation already inside a think block (no
    opening tag; keep what follows the last closer), and max_tokens can cut
    generation mid-thought (opener with no closer; nothing after it is answer).
    """
    text = _THINK_RE.sub("", text)
    if "</think>" in text:
        text = text.rsplit("</think>", 1)[1]
    if "<think>" in text:
        text = text.split("<think>", 1)[0]
    return text.strip()


def _router_base(cfg: LocalLLMCfg) -> str:
    return cfg.router_url.rstrip("/")


def probe_generation(cfg: AliciaCfg | LocalLLMCfg) -> dict[str, Any]:
    """Generate one token — the only probe that proves the router still answers.

    `list_models` is not liveness. mlx_lm serves /v1/models straight off the
    config dict without touching the model, so a server whose generation thread
    has died keeps returning 200 forever. That happened on 2026-08-11 18:45: a
    Metal command buffer failed with kIOGPUCommandBufferCallbackErrorOutOfMemory,
    Thread-1 (_generate) died, the process stayed up, launchd KeepAlive never
    fired, `alicia llm-health` read green — and every chat request hung until its
    read timeout for the next fourteen hours.

    So this posts a real completion with max_tokens=1 and thinking off, on its
    own short deadline. Cost is one token per watchdog tick; the alternative is a
    dead assistant that reports healthy.
    """
    llm = cfg.local_llm if isinstance(cfg, AliciaCfg) else cfg
    if llm is None or not llm.enabled:
        return {"ok": False, "error": "local_llm.enabled is false"}
    url = f"{_router_base(llm)}/v1/chat/completions"
    payload: dict[str, Any] = {
        "model": llm.model,
        "messages": [{"role": "user", "content": "ping"}],
        "max_tokens": 1,
        "temperature": 0.0,
        "chat_template_kwargs": {"enable_thinking": False},
    }
    started = time.monotonic()
    try:
        with httpx.Client(timeout=llm.probe_timeout_s) as client:
            r = client.post(url, json=payload)
            r.raise_for_status()
            r.json()
    except Exception as exc:
        return {
            "ok": False,
            "router_url": llm.router_url,
            "latency_s": round(time.monotonic() - started, 2),
            # The class name matters: ReadTimeout is the zombie shape, ConnectError
            # is a router that is simply down and launchd will handle on its own.
            "error": f"{type(exc).__name__}: {exc}",
        }
    return {
        "ok": True,
        "router_url": llm.router_url,
        "latency_s": round(time.monotonic() - started, 2),
    }


def restart_router(cfg: AliciaCfg | LocalLLMCfg) -> dict[str, Any]:
    """`launchctl kickstart -k` the router's launchd job.

    kickstart restarts a loaded job; bootstrap loads one. They are not
    interchangeable and using the wrong one on a running service leaves it down.
    """
    llm = cfg.local_llm if isinstance(cfg, AliciaCfg) else cfg
    if llm is None:
        return {"ok": False, "error": "no local_llm config"}
    target = f"gui/{os.getuid()}/{llm.autorestart_label}"
    try:
        proc = subprocess.run(
            ["launchctl", "kickstart", "-k", target],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except Exception as exc:
        return {"ok": False, "target": target, "error": f"{type(exc).__name__}: {exc}"}
    if proc.returncode != 0:
        return {
            "ok": False,
            "target": target,
            "error": (proc.stderr or proc.stdout or "").strip()[:200],
        }
    return {"ok": True, "target": target}


def list_models(cfg: AliciaCfg | LocalLLMCfg) -> dict[str, Any]:
    """GET /v1/models — reachability only. NOT liveness; see `probe_generation`."""
    llm = cfg.local_llm if isinstance(cfg, AliciaCfg) else cfg
    if not llm.enabled:
        return {"ok": False, "error": "local_llm.enabled is false"}
    url = f"{_router_base(llm)}/v1/models"
    try:
        with httpx.Client(timeout=llm.timeout_s) as client:
            r = client.get(url)
            r.raise_for_status()
            body = r.json()
            return {"ok": True, "router_url": llm.router_url, "models": body}
    except httpx.ConnectError as exc:
        return {
            "ok": False,
            "router_url": llm.router_url,
            "error": f"cannot connect to local LLM router ({exc})",
        }
    except httpx.HTTPStatusError as exc:
        return {
            "ok": False,
            "router_url": llm.router_url,
            "error": f"HTTP {exc.response.status_code}",
        }


def chat_completion(
    cfg: AliciaCfg,
    messages: list[dict[str, str]],
    *,
    temperature: float = 0.3,
    max_tokens: int = 2048,
    thinking: bool | None = None,
    timeout_s: float | None = None,
) -> str:
    """POST /v1/chat/completions; returns assistant text.

    `thinking` sets Qwen's reasoning budget for THIS call. It is a per-question
    knob, never a per-surface one: a lookup ("what needs me") is classification
    and needs none, a real question deserves the full budget however it arrived.

    Measured on :7901 with the production prompt shape (~813 tokens of tool
    catalog plus history): thinking on took 13.1s and returned `finish_reason:
    length` with **zero content tokens** — deliberation consumed the whole
    allowance. The same prompt with thinking off: 1.37s, 223 chars, 8/8 routing.
    So leaving this at None (the model's default) is fine for open-ended work and
    wrong for anything on a conversational clock.

    `timeout_s` overrides the config default for this call. Deep conductor passes
    use a shorter deadline so a contended mlx queue fails honest instead of
    leaving only an ack for the full 120s.
    """
    llm = cfg.local_llm
    if not llm.enabled:
        raise LocalLLMError("local_llm fallback is disabled (set local_llm.enabled: true).")

    url = f"{_router_base(llm)}/v1/chat/completions"
    payload: dict[str, Any] = {
        "model": llm.model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if thinking is not None:
        # `/no_think` in the prompt does not reliably survive a router; the
        # template kwarg does.
        payload["chat_template_kwargs"] = {"enable_thinking": thinking}
    deadline = float(timeout_s) if timeout_s is not None else float(llm.timeout_s)
    try:
        with httpx.Client(timeout=deadline) as client:
            r = client.post(url, json=payload)
            r.raise_for_status()
            data = r.json()
    except httpx.ConnectError as exc:
        raise LocalLLMError(
            f"Local LLM router not reachable at {llm.router_url} ({exc}). "
            "Run scripts/pull-local-model.sh and start mlx_lm.server on port 7901."
        ) from exc
    except httpx.TimeoutException as exc:
        raise LocalLLMError(
            f"Local LLM timed out after {deadline:g}s ({type(exc).__name__})."
        ) from exc
    except httpx.HTTPStatusError as exc:
        snippet = exc.response.text[:200]
        raise LocalLLMError(f"Local LLM HTTP {exc.response.status_code}: {snippet}") from exc

    choices = data.get("choices") or []
    if not choices:
        raise LocalLLMError("Local LLM returned no choices.")
    content = (choices[0].get("message") or {}).get("content")
    text = _strip_thinking(str(content or ""))
    if not text:
        raise LocalLLMError("Local LLM returned empty content (thinking-only reply).")
    return text
