from unittest.mock import patch

from fastapi.testclient import TestClient

from alicia.config import AliciaCfg, ClaudeCfg
from alicia.resilience import run_canaries
from alicia.server import create_app
from alicia.voice_readiness import VoiceReadiness


def test_readiness_proves_generation_and_recovers_after_cache_expires():
    readiness = VoiceReadiness(AliciaCfg(claude=ClaudeCfg(enabled=True)))
    assert readiness.snapshot()["ready"] is None
    with patch("alicia.voice_readiness.ask_claude", side_effect=[
        {"ok": False, "error": "OAuth session expired"},
        {"ok": True, "reply": "pong"},
    ]) as probe:
        failed = readiness.check()
        assert failed["ready"] is False
        assert failed["retryable"] is False
        assert "claude auth login" in failed["reason"]
        assert readiness.check() == failed
        assert probe.call_count == 1
        readiness._checked -= 31
        assert readiness.snapshot()["ready"] is None
        assert readiness.check()["ready"] is True
        assert probe.call_count == 2


def test_healthy_supervisor_does_not_hide_failed_conversation():
    with patch("alicia.resilience.api_killed", return_value=True):
        result = run_canaries(cli_probe=lambda: {"ok": False}, supervisor_probe=lambda: {"ok": True})
    assert result["overall_ok"] is False


def test_voice_ready_and_health_report_same_engine_proof():
    app = create_app(AliciaCfg(watchdog_enabled=False), start_watchdog=False)
    with patch("alicia.voice_readiness.ask_claude", return_value={"ok": False, "error": "OAuth session expired"}) as probe:
        client = TestClient(app)
        ready = client.get("/api/voice-ready").json()
        health = client.get("/api/healthz").json()
    assert ready["ready"] is False
    assert health["brain"]["readiness"] == ready
    assert probe.call_count == 1
