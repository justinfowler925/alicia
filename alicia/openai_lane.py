"""Hosted OpenAI / Codex platform lane — Justin opt-in only, default off.

Never a Forge/Cursor/Scout fallback. Attribution executor is always ``openai``.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any, Callable

from .config import AliciaCfg, OpenAICfg
from .platform_registry import assert_honest_attribution
from .redact import gate_text

EXECUTOR = "openai"
FORBIDDEN = frozenset({"forge", "gemma", "scout", "alicia", "cursor_cloud"})


def _openai_cfg(cfg: AliciaCfg | None) -> OpenAICfg:
    raw = getattr(cfg, "openai", None) if cfg else None
    if isinstance(raw, OpenAICfg):
        return raw
    return OpenAICfg()


def enabled(cfg: AliciaCfg | None) -> bool:
    return bool(_openai_cfg(cfg).enabled)


PromptFn = Callable[[str, OpenAICfg], dict[str, Any]]


def assert_openai_attribution(payload: dict[str, Any]) -> None:
    assert_honest_attribution(payload, lane="openai")
    executor = str(payload.get("executor") or "").strip().lower()
    if executor in FORBIDDEN:
        raise ValueError(f"openai receipt must not claim {executor!r}")


def _default_http(message: str, oai: OpenAICfg) -> dict[str, Any]:
    key = (os.environ.get("OPENAI_API_KEY") or "").strip()
    if not key:
        return {
            "ok": False,
            "executor": EXECUTOR,
            "error": "OPENAI_API_KEY unset; OpenAI lane unavailable",
            "http_attempted": False,
        }
    body = json.dumps(
        {
            "model": oai.model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are a hosted executor for Alicia. Be concise. "
                        "Do not invent ticket states or approvals."
                    ),
                },
                {"role": "user", "content": message},
            ],
        }
    ).encode()
    req = urllib.request.Request(
        f"{oai.base_url.rstrip('/')}/chat/completions",
        data=body,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {key}",
            "User-Agent": "alicia-openai-lane/1.0",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=oai.timeout_s) as resp:
            data = json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8", errors="replace")[:300]
        except Exception:  # noqa: BLE001
            detail = str(exc)
        return {
            "ok": False,
            "executor": EXECUTOR,
            "error": f"OpenAI HTTP {exc.code}: {detail}",
            "http_attempted": True,
            "model": oai.model,
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False,
            "executor": EXECUTOR,
            "error": f"OpenAI request failed: {exc}",
            "http_attempted": True,
            "model": oai.model,
        }
    choices = data.get("choices") or []
    reply = ""
    if choices and isinstance(choices[0], dict):
        msg = choices[0].get("message") or {}
        reply = str(msg.get("content") or "").strip()
    if not reply:
        return {
            "ok": False,
            "executor": EXECUTOR,
            "error": "OpenAI returned empty reply",
            "http_attempted": True,
            "model": str(data.get("model") or oai.model),
        }
    return {
        "ok": True,
        "executor": EXECUTOR,
        "attribution": "OpenAI",
        "reply": gate_text(reply[:6000]),
        "model": str(data.get("model") or oai.model),
        "http_attempted": True,
    }


def run_openai(
    cfg: AliciaCfg,
    message: str,
    *,
    prompt_fn: PromptFn | None = None,
) -> dict[str, Any]:
    oai = _openai_cfg(cfg)
    if not oai.enabled:
        return {
            "ok": False,
            "executor": EXECUTOR,
            "error": "OpenAI lane is disabled (openai.enabled=false). Opt-in only.",
            "http_attempted": False,
        }
    body = (message or "").strip()
    if not body:
        return {
            "ok": False,
            "executor": EXECUTOR,
            "error": "message is required",
            "http_attempted": False,
        }
    if prompt_fn is not None:
        payload = prompt_fn(body, oai)
    else:
        payload = _default_http(body, oai)
    if not isinstance(payload, dict):
        payload = {"ok": False, "error": "invalid openai payload", "executor": EXECUTOR}
    payload.setdefault("executor", EXECUTOR)
    payload.setdefault("attribution", "OpenAI")
    if payload.get("ok"):
        assert_openai_attribution(payload)
    return payload
