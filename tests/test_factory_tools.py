"""Wave 4 — factory chat verbs, probe filter, completion alarm."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from alicia.chat_resolve import _alarm_line, _board_summary, _lookup_intent, resolve_chat_reply
from alicia.config import AliciaCfg, LocalLLMCfg
from alicia.tools import (
    _answer_steering,
    _dispatch_tick,
    _get_digest,
    _reconcile,
    _work_surface,
    build_default_registry,
)


def _cfg() -> AliciaCfg:
    return AliciaCfg(local_llm=LocalLLMCfg(enabled=True, model="m"))


def test_lookup_excludes_atlas_factory_verbs():
    assert _lookup_intent("reconcile handbacks") is None
    assert _lookup_intent("dispatch a tick now") is None
    assert _lookup_intent("run a live tick dispatch for real") is None
    assert _lookup_intent("show me the wip digest")[0] == "get_digest"
    assert _lookup_intent("answer REV-256: use partial sandbox")[0] != "answer_steering"


def test_alarm_line():
    assert _alarm_line(None) == ""
    assert _alarm_line({"alarm": {}}) == ""
    assert "ever finished" in _alarm_line({"alarm": {"alarm": True, "done_total": 0}})
    line = _alarm_line({"alarm": {"alarm": True, "done_total": 3, "window_hours": 8, "in_flight": 2}})
    assert "8h" in line and "2" in line
    summary = _board_summary(
        {"headline": "Nothing needs you.", "alarm": {"alarm": True, "done_total": 0}, "stuck": []}
    )
    assert summary.startswith("FACTORY ALARM")


def test_work_surface_includes_alarm_and_hides_probes():
    client = MagicMock()
    client.status.return_value = {
        "blocked_justin": [
            {"id": "g1", "external_id": "REV-1", "title": "Real gate", "blocker": "need approve"},
            {
                "id": "g2",
                "external_id": "REV-PROBE",
                "title": "[atlas5 proof burn-in] synthetic",
                "blocker": "need approve",
            },
        ],
        "in_flight": [],
        "ready": [],
        "blocked_frontier": [],
        "completion_alarm": {"alarm": True, "done_total": 0, "open": 5},
        "counts": {},
    }
    client.list_awaiting_input.return_value = []
    surface = {"needs_you": [{"ticket": "REV-1"}], "working": [], "queued": [], "stuck": [], "counts": {}, "alarm": {}}
    with patch("alicia.tools.linear_work_surface", return_value=surface):
        out = _work_surface(client)
    assert out["atlas_ignored"] is True
    client.status.assert_not_called()


def test_get_digest_uses_board():
    client = MagicMock()
    client.status.return_value = {
        "blocked_justin": [],
        "in_flight": [],
        "ready": [],
        "blocked_frontier": [],
        "completion_alarm": {},
        "counts": {},
    }
    client.list_awaiting_input.return_value = []
    client.digest.return_value = {"digest_markdown": "# WIP\n" + ("x" * 2000)}
    surface = {"headline": "1 in review", "needs_you": [], "working": [], "queued": [], "stuck": [], "counts": {}, "alarm": {}}
    with patch("alicia.tools.linear_work_surface", return_value=surface):
        out = _get_digest(client)
    assert out["ok"] is True
    assert out["source"] == "linear_direct"
    client.digest.assert_not_called()


def test_dispatch_defaults_dry_run():
    client = MagicMock()
    client.dispatch_tick.return_value = {"summary": "would dispatch 2", "ok": True}
    out = _dispatch_tick(client)
    assert out["ok"] is True
    assert out["dry_run"] is True
    client.dispatch_tick.assert_called_once_with(dry_run=True, ingest_linear=False)


def test_reconcile_and_steering():
    client = MagicMock()
    client.reconcile.return_value = {"ok": True, "closed": 3}
    assert _reconcile(client)["closed"] == 3
    client.answer_steering.return_value = {"ok": True, "resumed": True}
    out = _answer_steering(client, "rev-10", "use partial")
    assert out["ok"] is True
    assert out["ticket_id"] == "REV-10"
    client.answer_steering.assert_called_once()


def test_registry_has_linear_and_local_tools_only():
    client = MagicMock()
    reg = build_default_registry(client, cfg=_cfg(), read_only=False)
    names = {t["name"] for t in reg.list_schemas()}
    assert "get_digest" in names
    for n in ("dispatch_tick", "reconcile", "answer_steering", "register_thread", "ask_atlas6", "ask_claude"):
        assert n not in names
    ro = build_default_registry(client, cfg=_cfg(), read_only=True)
    ro_names = {t["name"] for t in ro.list_schemas()}
    assert "dispatch_tick" not in ro_names
    assert "get_digest" in ro_names


def test_resolve_forces_digest_and_surfaces_alarm():
    client = MagicMock()
    client.status.return_value = {
        "blocked_justin": [],
        "in_flight": [],
        "ready": [],
        "blocked_frontier": [],
        "completion_alarm": {"alarm": True, "done_total": 0, "open": 9},
        "counts": {},
    }
    client.list_awaiting_input.return_value = []
    client.digest.return_value = {"digest_markdown": "short"}

    captured = {}

    def fake_chat(cfg, messages, **_k):
        captured["content"] = messages[-1]["content"]
        return "Factory alarm: nothing has ever finished. Board is quiet otherwise."

    with patch("alicia.chat_resolve.chat_completion", side_effect=fake_chat):
        text, raw = resolve_chat_reply(client, _cfg(), "show the wip digest")
    assert raw["path"] == "next_decision"
    assert "?" in text or "Nothing needs you" in text
