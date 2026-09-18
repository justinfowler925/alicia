"""Laptop watchdog — the local router's health, and nothing else.

This used to be Atlas's fast scheduler. Every ``watchdog_interval_s`` (60s) it
called ``/api/status``, ``/api/reconcile``, ``/api/dispatch/tick`` and the
Cursor tick against the Studio conductor, which made a laptop lid
load-bearing for a Studio service. That driving moved to
``atlas/conductor/scheduler.py`` in Phase 4 step 4: Atlas runs its own loop in
the process that serves :8767, so it survives the lid.

Two things worth writing down, because the shape of the fix depended on both:

* The Studio was **already** reconciling and dispatching without the laptop —
  its launchd tick logged ~190 records/day for five straight days, overnight
  included. What the lid actually cost was cadence (60s → 900s) and the cursor
  lane. So this was mostly deletion, not migration.
* The reconcile/dispatch calls deleted here had no equivalent that Atlas lacked.
  ``POST /api/reconcile`` is ``reconcile_inflight``; ``POST /api/dispatch/tick``
  is ``dispatch_tick`` — literally the same two functions the Studio tick calls.
  The ``ready > 0`` guard made Brutus do *less*, never more.

What stays is a genuine laptop concern: the local MLX router runs here, so
probing it and restarting it belongs here. Nothing in Atlas depends on this
loop any more — Brutus can be stopped, and Atlas keeps moving.

The Cursor SDK runner that this used to drive has been deleted outright; model
work goes to OpenAI through ``brutus/openai_chat.py``, which edits nothing.
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import UTC, datetime
from typing import Any

from .client import AtlasClient
from .config import BrutusCfg
from .local_llm import probe_generation, restart_router

log = logging.getLogger("brutus.watchdog")

# This actor owns only local health. Remote scheduling is outside Brutus.
SCHEDULER_OWNER = "standalone-local-only"


def _now() -> str:
    return datetime.now(UTC).isoformat()


class Watchdog:
    def __init__(self, cfg: BrutusCfg, client: AtlasClient | None = None) -> None:
        self.cfg = cfg
        self.client = client or AtlasClient(cfg)
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._state: dict[str, Any] = {
            "enabled": cfg.watchdog_enabled,
            "interval_s": cfg.watchdog_interval_s,
            "running": False,
            "last_run_at": None,
            "last_actions": [],
            "last_errors": [],
            "local_llm": {"ok": None, "checked_at": None},
            "scheduler": SCHEDULER_OWNER,
        }
        self._llm_failures = 0
        self._llm_last_restart: float | None = None

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._state)

    def check_local_llm(self) -> dict[str, Any]:
        """Probe generation; restart the router after N consecutive failures.

        The strike count and the cooldown both exist to keep this from becoming
        a restart loop: a cold model that has been paged out over a sleep can
        legitimately blow one deadline, and a router that is broken for a reason
        a restart cannot fix (no disk, no GPU) must not be kicked every minute.
        """
        llm = self.cfg.local_llm
        if llm is None or not llm.enabled:
            return {"ok": None, "checked_at": _now(), "reason": "local_llm disabled"}

        result = probe_generation(self.cfg)
        result["checked_at"] = _now()
        if result.get("ok"):
            if self._llm_failures:
                log.info("local llm recovered after %s failed probe(s)", self._llm_failures)
            self._llm_failures = 0
            return result

        self._llm_failures += 1
        result["consecutive_failures"] = self._llm_failures
        log.warning(
            "local llm probe failed (%s/%s): %s",
            self._llm_failures,
            llm.probe_failures_before_restart,
            result.get("error"),
        )
        if not llm.autorestart_enabled:
            result["restart"] = {"ok": False, "error": "autorestart disabled"}
            return result
        if self._llm_failures < llm.probe_failures_before_restart:
            return result

        now = time.monotonic()
        if (
            self._llm_last_restart is not None
            and now - self._llm_last_restart < llm.autorestart_cooldown_s
        ):
            waited = int(now - self._llm_last_restart)
            result["restart"] = {
                "ok": False,
                "error": f"in cooldown ({waited}s of {int(llm.autorestart_cooldown_s)}s)",
            }
            return result

        self._llm_last_restart = now
        # Reset the strike count so the next tick judges the restarted router on
        # its own merits rather than immediately kicking it again.
        self._llm_failures = 0
        restart = restart_router(self.cfg)
        result["restart"] = restart
        log.warning("local llm restart via launchctl: %s", restart)
        return result

    def start(self) -> None:
        if not self.cfg.watchdog_enabled:
            log.info("watchdog disabled in config")
            return
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="brutus-watchdog", daemon=True)
        self._thread.start()
        with self._lock:
            self._state["running"] = True
        log.info("watchdog started interval=%ss", self.cfg.watchdog_interval_s)

    def stop(self) -> None:
        self._stop.set()
        with self._lock:
            self._state["running"] = False

    def tick_once(self) -> dict[str, Any]:
        actions: list[str] = []
        errors: list[str] = []

        try:
            llm_state = self.check_local_llm()
        except Exception as exc:
            llm_state = {"ok": False, "checked_at": _now(), "error": f"probe: {exc}"}
        if llm_state.get("ok") is False:
            errors.append(f"local_llm: {llm_state.get('error')}")
            if llm_state.get("restart", {}).get("ok"):
                actions.append("local_llm router restarted (generation probe failed)")
        elif llm_state.get("ok") is True:
            actions.append("local_llm generation ok")
        else:
            actions.append(str(llm_state.get("reason") or "local_llm not probed"))

        snap = {
            "enabled": self.cfg.watchdog_enabled,
            "interval_s": self.cfg.watchdog_interval_s,
            "running": not self._stop.is_set() and self.cfg.watchdog_enabled,
            "last_run_at": _now(),
            "last_actions": actions,
            "last_errors": errors,
            "local_llm": llm_state,
            "scheduler": SCHEDULER_OWNER,
        }
        with self._lock:
            self._state = snap
        return snap

    def _loop(self) -> None:
        # First tick soon after boot.
        time.sleep(2)
        while not self._stop.is_set():
            try:
                self.tick_once()
            except Exception as exc:
                log.exception("watchdog tick failed: %s", exc)
                with self._lock:
                    self._state["last_run_at"] = _now()
                    self._state["last_errors"] = [str(exc)]
            self._stop.wait(self.cfg.watchdog_interval_s)
