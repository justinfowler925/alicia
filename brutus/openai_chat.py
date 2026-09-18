"""One-shot OpenAI chat completions — the single model backend for Brutus.

Replaces the Cursor SDK runner. That runner could edit an allowlisted working
tree; this cannot touch the filesystem at all, so the branch/allowlist guards it
carried are gone with it. ``repo_hint`` survives only as context in the prompt.
"""

from __future__ import annotations

import logging
import os
from typing import Any

import httpx

from .config import BrutusCfg, OpenAICfg

log = logging.getLogger("brutus.openai_chat")

_ENDPOINT = "https://api.openai.com/v1/chat/completions"
_SYSTEM = (
    "You are helping Justin via Brutus. Be concise and concrete. "
    "Do not invent ticket states, PRs, or approvals. Plain English."
)


def build_chat_prompt(message: str, repo_hint: str = "") -> str:
    """Prepend the repo the question is about, when one was named."""
    body = (message or "").strip()
    hint = (repo_hint or "").strip()
    return f"Repository context: {hint}\n\n{body}" if hint else body


def run_openai_chat(
    cfg: BrutusCfg | OpenAICfg,
    message: str,
    *,
    repo_hint: str = "",
    system: str = "",
    timeout_s: float | None = None,
) -> dict[str, Any]:
    conf = cfg.openai if isinstance(cfg, BrutusCfg) else cfg
    if not conf.enabled:
        return {"ok": False, "error": "OpenAI backend is disabled."}
    body = build_chat_prompt(message, repo_hint)
    if not body:
        return {"ok": False, "error": "message is required"}
    api_key = (os.environ.get("OPENAI_API_KEY") or conf.api_key or "").strip()
    if not api_key:
        return {"ok": False, "error": "OPENAI_API_KEY not set"}
    payload = {
        "model": conf.model,
        "messages": [
            {"role": "system", "content": (system or "").strip() or _SYSTEM},
            {"role": "user", "content": body},
        ],
    }
    try:
        resp = httpx.post(
            _ENDPOINT,
            json=payload,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=timeout_s or conf.timeout_s,
        )
    except httpx.TimeoutException:
        return {"ok": False, "error": "OpenAI request timed out."}
    except httpx.HTTPError as exc:
        return {"ok": False, "error": f"OpenAI request failed: {exc}"}
    if resp.status_code != 200:
        detail = (resp.text or "")[:300]
        return {"ok": False, "error": f"OpenAI HTTP {resp.status_code}: {detail}"}
    choices = resp.json().get("choices") or []
    if not choices:
        return {"ok": False, "error": "OpenAI returned no choices"}
    reply = str((choices[0].get("message") or {}).get("content") or "").strip()
    return {"ok": True, "reply": reply, "model": conf.model}
