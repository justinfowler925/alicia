"""Brutus is not Atlas's scheduler — Phase 4 step 4.

The watchdog used to drive Atlas's loop from the laptop: ``/api/status``,
``/api/reconcile`` and ``/api/dispatch/tick``, every 60
seconds. Atlas now runs that loop itself, inside the process that serves
:8767 on the Studio, so a laptop lid stops being load-bearing for a Studio
service.

These tests assert the *absence*, which is the only thing that keeps it absent.
Re-adding a ``client.reconcile()`` to the tick — the obvious "just in case"
edit — has to fail here.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from brutus.config import BrutusCfg, LocalLLMCfg
from brutus.watchdog import SCHEDULER_OWNER, Watchdog


def _wd(**kw) -> tuple[Watchdog, MagicMock]:
    client = MagicMock()
    cfg = BrutusCfg(watchdog_enabled=True, **kw)
    wd = Watchdog(cfg, client=client)
    # __init__ does `client or AtlasClient(cfg)`, which records a __bool__ call.
    # Reset so the assertions below are about the TICK, not construction.
    client.reset_mock()
    return wd, client


def test_tick_never_touches_the_atlas_ledger():
    """One assertion per deleted call, so a partial revert cannot slip through."""
    wd, client = _wd()
    wd.tick_once()
    client.reconcile.assert_not_called()
    client.dispatch_tick.assert_not_called()
    client.status.assert_not_called()
    client.ingest_linear.assert_not_called()
    client.approve.assert_not_called()
    client.requeue_threads.assert_not_called()


def test_tick_makes_no_studio_calls_at_all():
    """Any new attribute access on the client is a new dependency on Atlas."""
    wd, client = _wd()
    wd.tick_once()
    assert client.mock_calls == [], f"watchdog called Atlas: {client.mock_calls}"


def test_tick_does_not_run_the_cursor_lane(monkeypatch):
    called = False

    def _boom(*_a, **_kw):  # pragma: no cover - must never run
        nonlocal called
        called = True
        return {}

    # The import is gone; belt-and-braces in case someone re-adds it.
    wd, _client = _wd()
    wd.tick_once()
    assert called is False


def test_watchdog_module_does_not_import_a_model_runner():
    import brutus.watchdog as mod

    src = open(mod.__file__).read()
    code = "\n".join(
        line for line in src.splitlines() if not line.strip().startswith("#")
    )
    assert "from .openai_chat import" not in code


def test_tick_still_probes_the_local_router(monkeypatch):
    """The laptop concern stays on the laptop."""
    probes: list[int] = []

    def _probe(_cfg):
        probes.append(1)
        return {"ok": True}

    monkeypatch.setattr("brutus.watchdog.probe_generation", _probe)
    wd, _client = _wd(local_llm=LocalLLMCfg(enabled=True))
    snap = wd.tick_once()
    assert probes == [1]
    assert snap["local_llm"]["ok"] is True
    assert snap["last_errors"] == []


def test_snapshot_names_the_standalone_scope():
    """Health must not claim Brutus owns a remote scheduler."""
    wd, _client = _wd()
    assert wd.snapshot()["scheduler"] == SCHEDULER_OWNER
    assert SCHEDULER_OWNER == "standalone-local-only"
    assert wd.tick_once()["scheduler"] == SCHEDULER_OWNER


def test_snapshot_drops_the_stale_ledger_counters():
    """last_counts/gates_waiting can no longer be populated — a frozen 0 is a lie."""
    wd, _client = _wd()
    for dead in ("last_counts", "gates_waiting"):
        assert dead not in wd.snapshot()
        assert dead not in wd.tick_once()


def test_a_failing_probe_does_not_raise_out_of_the_tick(monkeypatch):
    def _boom(_cfg):
        raise RuntimeError("router unreachable")

    monkeypatch.setattr("brutus.watchdog.probe_generation", _boom)
    wd, _client = _wd(local_llm=LocalLLMCfg(enabled=True))
    snap = wd.tick_once()
    assert snap["last_errors"] and "router unreachable" in snap["last_errors"][0]
