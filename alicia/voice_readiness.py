"""Bounded, process-local proof that the selected conversation engine answers."""

import os
import threading
import time
from typing import Any

from .claude import ask_claude


class VoiceReadiness:
    def __init__(self, cfg):
        self.cfg = cfg
        self._lock = threading.Lock()
        self._checked = 0.0
        self._result: dict[str, Any] = {"ready": None, "reason": "Voice readiness has not been checked."}

    def snapshot(self):
        result = dict(self._result)
        if time.monotonic() - self._checked >= 30:
            result["ready"] = None
            result["reason"] = "Voice readiness needs a fresh check."
        return result

    def check(self):
        # Coalesce simultaneous tabs; polling must not spawn parallel CLI turns.
        with self._lock:
            if time.monotonic() - self._checked < 30:
                return dict(self._result)
            if os.environ.get("ALICIA_CONVERSATION_PROVIDER") == "cursor":
                from .cursor_cli import complete
                try:
                    reply = complete("Reply with exactly: pong", timeout=25)
                    ready = reply.strip().lower().rstrip(".") == "pong"
                except RuntimeError:
                    ready = False
                self._result = {"ready": ready, "provider": "openai_live",
                    "scope": "cursor_conversation_engine", "reason": "" if ready else "Studio's Cursor connection needs attention."}
                self._checked = time.monotonic()
                return dict(self._result)
            if self.cfg.claude.transport != "cli":
                return {"ready": None, "reason": "CLI readiness does not verify the selected API transport."}
            result = ask_claude(
                self.cfg, "Reply with exactly: pong",
                system="You are a readiness check. Reply with exactly one word: pong",
                timeout_s=20,
            )
            ready = bool(result.get("ok") and str(result.get("reply") or "").strip().lower() == "pong")
            error = str(result.get("error") or "")
            auth = any(word in error.lower() for word in ("auth", "credential", "sign in", "login"))
            self._result = {
                "ready": ready,
                "checked_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "scope": "conversation_engine",
                "reason": "" if ready else (
                    "Claude sign-in needs attention. Run claude auth login on this Mac, then start voice again."
                    if auth else "The answer engine is unavailable. Try starting voice again shortly."
                ),
                "error_code": None if ready else ("brain_auth_unavailable" if auth else "brain_service_unavailable"),
                "retryable": not auth,
            }
            self._checked = time.monotonic()
            return dict(self._result)
