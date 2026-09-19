"""Resilience controls: kill files, outbox, canaries, billing planes.

No single vendor may brick a turn. Kill files are the hard stop; canaries are
the visible proof; the outbox keeps user words durable before a model runs.
"""

from __future__ import annotations

import json
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .paths import default_state_dir

STATE_DIR = default_state_dir()
KILL_VOICE = STATE_DIR / "voice.off"
KILL_API = STATE_DIR / "brain.api.off"
KILL_CONVAI = STATE_DIR / "voice.convai.off"
OUTBOX_DIR = STATE_DIR / "say-outbox"

# Wall clocks for each leg. Voice turns get more runway — thinking face holds
# the silence; a rushed wrong answer is worse than a longer correct one.
TURN_TIMEOUT_S = 180.0
CLI_TIMEOUT_S = 150.0
CURSOR_TIMEOUT_S = 120.0
API_TIMEOUT_S = 90.0
CONVAI_CONNECT_TIMEOUT_S = 40.0
CANARY_TIMEOUT_S = 20.0
# Voice ask_brutus retries before we let ConvAI speak anything.
VOICE_BRAIN_ATTEMPTS = 3
VOICE_BRAIN_RETRY_GAP_S = 2.5


def ensure_state_dirs() -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    OUTBOX_DIR.mkdir(parents=True, exist_ok=True)


def timeouts() -> dict[str, float]:
    return {
        "turn_s": TURN_TIMEOUT_S,
        "brain_cli_s": CLI_TIMEOUT_S,
        "brain_cursor_s": CURSOR_TIMEOUT_S,
        "brain_api_s": API_TIMEOUT_S,
        "convai_connect_s": CONVAI_CONNECT_TIMEOUT_S,
        "canary_s": CANARY_TIMEOUT_S,
        "voice_brain_attempts": float(VOICE_BRAIN_ATTEMPTS),
        "voice_brain_retry_gap_s": VOICE_BRAIN_RETRY_GAP_S,
    }


def kill_file_on(name: str) -> bool:
    """True when ~/.brutus/state/<name> exists."""
    return (STATE_DIR / name).exists()


def ensure_api_killed_by_default() -> None:
    """Empty Anthropic API credits must not be the happy path. Touch once."""
    ensure_state_dirs()
    if not KILL_API.exists():
        KILL_API.write_text(
            "Anthropic Messages API disabled by Brutus resilience policy.\n"
            "Remove this file AND set claude.transport=api + api_enabled=true to re-enable.\n"
        )


def voice_killed() -> bool:
    return KILL_VOICE.exists()


def api_killed() -> bool:
    """Anthropic Messages API is off — by kill file or by policy."""
    return KILL_API.exists()


def convai_killed() -> bool:
    return KILL_CONVAI.exists()


def kill_reasons() -> dict[str, bool]:
    return {
        "voice.off": voice_killed(),
        "brain.api.off": api_killed(),
        "voice.convai.off": convai_killed(),
    }


@dataclass
class OutboxItem:
    id: str
    session_id: str
    message: str
    channel: str
    created_at: float
    status: str  # pending | running | done | failed
    error: str = ""
    reply: str = ""

    def path(self) -> Path:
        return OUTBOX_DIR / f"{self.id}.json"

    def write(self) -> None:
        ensure_state_dirs()
        self.path().write_text(
            json.dumps(
                {
                    "id": self.id,
                    "session_id": self.session_id,
                    "message": self.message,
                    "channel": self.channel,
                    "created_at": self.created_at,
                    "status": self.status,
                    "error": self.error,
                    "reply": self.reply,
                },
                indent=2,
            )
            + "\n"
        )


def enqueue_say(session_id: str, message: str, channel: str) -> OutboxItem:
    ensure_state_dirs()
    item = OutboxItem(
        id=f"{int(time.time() * 1000)}-{uuid.uuid4().hex[:8]}",
        session_id=session_id,
        message=message,
        channel=channel,
        created_at=time.time(),
        status="pending",
    )
    item.write()
    return item


def mark_outbox(item: OutboxItem, *, status: str, reply: str = "", error: str = "") -> None:
    item.status = status
    item.reply = reply
    item.error = error
    item.write()


def pending_outbox(limit: int = 20) -> list[dict[str, Any]]:
    ensure_state_dirs()
    rows: list[dict[str, Any]] = []
    for path in sorted(OUTBOX_DIR.glob("*.json"), reverse=True)[:limit]:
        try:
            rows.append(json.loads(path.read_text()))
        except (OSError, json.JSONDecodeError):
            continue
    return rows


def billing_planes(cfg: Any) -> dict[str, Any]:
    """Name the money planes so empty API credits are never mysterious."""
    voice = getattr(cfg, "voice", None)
    claude = getattr(cfg, "claude", None)
    openai_cfg = getattr(cfg, "openai", None)
    transport = str(getattr(claude, "transport", "cli") or "cli")
    api_enabled = bool(getattr(claude, "api_enabled", False)) and not api_killed()
    return {
        "conversation": {
            "plane": "claude_subscription_cli" if transport == "cli" else "anthropic_api",
            "transport": transport,
            "api_enabled": api_enabled,
            "api_killed": api_killed(),
        },
        "openai": {
            "plane": "openai_api",
            "enabled": bool(openai_cfg and openai_cfg.enabled),
        },
        "voice_tts": {
            "plane": "elevenlabs",
            "configured": bool(voice and (voice.elevenlabs_api_key or "").strip()),
        },
        "voice_convai": {
            "plane": "elevenlabs_convai",
            "agent_id": bool(voice and (getattr(voice, "elevenlabs_agent_id", "") or "").strip()),
            "killed": convai_killed(),
            "product_owned_brain": True,
            "custom_llm_endpoint": "/api/convai/llm/v1/chat/completions",
        },
        "voice_livekit": {
            "plane": "livekit_local",
            "configured": bool(
                voice
                and voice.livekit_url
                and voice.livekit_api_key
                and voice.livekit_api_secret
            ),
        },
        "kill_files": kill_reasons(),
        "how_to_kill": {
            "voice": f"touch {KILL_VOICE}",
            "anthropic_api": f"touch {KILL_API}",
            "convai": f"touch {KILL_CONVAI}",
        },
    }


def run_canaries(
    *,
    cli_probe: Callable[[], dict[str, Any]] | None = None,
    supervisor_probe: Callable[[], dict[str, Any]] | None = None,
    convai_probe: Callable[[], dict[str, Any]] | None = None,
    api_probe: Callable[[], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Exercise each plane. Proof, not hope."""
    started = time.time()
    results: dict[str, Any] = {"checked_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}

    def _run(name: str, fn: Callable[[], dict[str, Any]] | None) -> None:
        if fn is None:
            results[name] = {"ok": False, "skipped": True}
            return
        t0 = time.time()
        try:
            payload = fn()
            results[name] = {
                "ok": bool(payload.get("ok")),
                "ms": int((time.time() - t0) * 1000),
                **{k: v for k, v in payload.items() if k != "ok"},
            }
        except Exception as exc:  # noqa: BLE001
            results[name] = {"ok": False, "ms": int((time.time() - t0) * 1000), "error": str(exc)[:200]}

    _run("cli", cli_probe)
    _run("supervisor", supervisor_probe)
    _run("convai", convai_probe)
    if api_killed():
        results["anthropic_api"] = {"ok": False, "skipped": True, "reason": "brain.api.off"}
    else:
        _run("anthropic_api", api_probe)
    results["ms"] = int((time.time() - started) * 1000)
    results["overall_ok"] = bool(
        results.get("cli", {}).get("ok") or results.get("supervisor", {}).get("ok")
    )
    return results
