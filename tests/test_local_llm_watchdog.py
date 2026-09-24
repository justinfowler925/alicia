"""The router can be dead and still answer /v1/models.

On 2026-08-11 18:45 the mlx_lm server lost Thread-1 (_generate) to a Metal
command-buffer OOM. The process stayed up. `GET /v1/models` returned 200 for the
next fourteen hours, so launchd KeepAlive never fired and `alicia llm-health`
printed green, while every `POST /v1/chat/completions` hung until the client's
read timeout. Justin's questions came back as "timed out, could not finish
thinking" all morning.

`test_zombie_router_passes_list_models_and_fails_the_probe` is the one that
matters: it reproduces that exact split and proves the new probe catches what
the old check waved through. The rest guard the restart from becoming a loop.
"""

from unittest.mock import MagicMock, patch

import httpx

from alicia.config import AliciaCfg, LocalLLMCfg
from alicia.local_llm import list_models, probe_generation, restart_router
from alicia.watchdog import Watchdog


def _cfg(**overrides) -> AliciaCfg:
    llm = LocalLLMCfg(
        enabled=True,
        router_url="http://127.0.0.1:7901",
        model="test-model",
        timeout_s=5.0,
        probe_timeout_s=2.0,
        **overrides,
    )
    return AliciaCfg(local_llm=llm, watchdog_enabled=True)


def _ok_completion() -> MagicMock:
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.json.return_value = {"choices": [{"message": {"content": "ok"}}]}
    return resp


def _watchdog(cfg: AliciaCfg) -> Watchdog:
    return Watchdog(cfg, client=MagicMock())


# --- the probe itself ----------------------------------------------------


def test_probe_generation_asks_for_exactly_one_token():
    cfg = _cfg()
    with patch("alicia.local_llm.httpx.Client") as client_cls:
        client = client_cls.return_value.__enter__.return_value
        client.post.return_value = _ok_completion()
        out = probe_generation(cfg)

    assert out["ok"] is True
    assert "latency_s" in out
    url, = client.post.call_args.args
    assert url == "http://127.0.0.1:7901/v1/chat/completions"
    payload = client.post.call_args.kwargs["json"]
    assert payload["max_tokens"] == 1
    # Thinking on can burn the whole allowance and return zero content tokens,
    # which would read as a failure on a router that is perfectly healthy.
    assert payload["chat_template_kwargs"] == {"enable_thinking": False}
    # Its own short deadline — never the 120s one a real question gets.
    assert client_cls.call_args.kwargs["timeout"] == 2.0


def test_probe_generation_reports_read_timeout():
    cfg = _cfg()
    with patch("alicia.local_llm.httpx.Client") as client_cls:
        client = client_cls.return_value.__enter__.return_value
        client.post.side_effect = httpx.ReadTimeout("timed out")
        out = probe_generation(cfg)

    assert out["ok"] is False
    assert "ReadTimeout" in out["error"]


def test_probe_generation_disabled():
    assert probe_generation(AliciaCfg(local_llm=None))["ok"] is False


def test_zombie_router_passes_list_models_and_fails_the_probe():
    """The 2026-08-11 shape, reproduced: GET fine, POST hangs forever."""
    cfg = _cfg()
    models_resp = MagicMock()
    models_resp.raise_for_status = MagicMock()
    models_resp.json.return_value = {"data": [{"id": "test-model"}]}

    with patch("alicia.local_llm.httpx.Client") as client_cls:
        client = client_cls.return_value.__enter__.return_value
        client.get.return_value = models_resp
        client.post.side_effect = httpx.ReadTimeout("timed out")

        assert list_models(cfg)["ok"] is True, "the old check waved this through"
        assert probe_generation(cfg)["ok"] is False, "the new probe must catch it"


# --- restart plumbing ----------------------------------------------------


def test_restart_router_kickstarts_the_launchd_job():
    cfg = _cfg(autorestart_label="com.clearspeed.alicia-local-llm")
    proc = MagicMock(returncode=0, stdout="", stderr="")
    with patch("alicia.local_llm.subprocess.run", return_value=proc) as run:
        with patch("alicia.local_llm.os.getuid", return_value=501):
            out = restart_router(cfg)

    assert out["ok"] is True
    argv = run.call_args.args[0]
    # kickstart restarts a loaded job; bootstrap loads one. Using bootstrap on a
    # running service leaves it down.
    assert argv == [
        "launchctl",
        "kickstart",
        "-k",
        "gui/501/com.clearspeed.alicia-local-llm",
    ]


def test_restart_router_surfaces_launchctl_failure():
    cfg = _cfg()
    proc = MagicMock(returncode=3, stdout="", stderr="Could not find service")
    with patch("alicia.local_llm.subprocess.run", return_value=proc):
        out = restart_router(cfg)
    assert out["ok"] is False
    assert "Could not find service" in out["error"]


# --- the watchdog decision -----------------------------------------------


def test_one_failed_probe_does_not_restart():
    """A model paged out over a sleep can legitimately miss one deadline."""
    wd = _watchdog(_cfg(probe_failures_before_restart=2))
    with patch("alicia.watchdog.probe_generation", return_value={"ok": False, "error": "x"}):
        with patch("alicia.watchdog.restart_router") as restart:
            out = wd.check_local_llm()

    restart.assert_not_called()
    assert out["consecutive_failures"] == 1


def test_second_consecutive_failure_restarts():
    wd = _watchdog(_cfg(probe_failures_before_restart=2))
    with patch("alicia.watchdog.probe_generation", return_value={"ok": False, "error": "x"}):
        with patch("alicia.watchdog.restart_router", return_value={"ok": True}) as restart:
            wd.check_local_llm()
            out = wd.check_local_llm()

    restart.assert_called_once()
    assert out["restart"]["ok"] is True


def test_a_success_between_failures_resets_the_count():
    wd = _watchdog(_cfg(probe_failures_before_restart=2))
    with patch("alicia.watchdog.restart_router") as restart:
        with patch("alicia.watchdog.probe_generation", return_value={"ok": False, "error": "x"}):
            wd.check_local_llm()
        with patch("alicia.watchdog.probe_generation", return_value={"ok": True}):
            wd.check_local_llm()
        with patch("alicia.watchdog.probe_generation", return_value={"ok": False, "error": "x"}):
            wd.check_local_llm()

    restart.assert_not_called()


def test_cooldown_blocks_a_second_restart():
    """A router broken for a reason a restart cannot fix must not be kicked
    every minute — that is a loop, not a recovery."""
    wd = _watchdog(_cfg(probe_failures_before_restart=1, autorestart_cooldown_s=600))
    with patch("alicia.watchdog.probe_generation", return_value={"ok": False, "error": "x"}):
        with patch("alicia.watchdog.restart_router", return_value={"ok": True}) as restart:
            wd.check_local_llm()
            second = wd.check_local_llm()

    restart.assert_called_once()
    assert second["restart"]["ok"] is False
    assert "cooldown" in second["restart"]["error"]


def test_autorestart_can_be_switched_off():
    wd = _watchdog(_cfg(probe_failures_before_restart=1, autorestart_enabled=False))
    with patch("alicia.watchdog.probe_generation", return_value={"ok": False, "error": "x"}):
        with patch("alicia.watchdog.restart_router") as restart:
            out = wd.check_local_llm()

    restart.assert_not_called()
    assert out["restart"]["ok"] is False


def test_disabled_local_llm_is_not_a_failure():
    wd = _watchdog(AliciaCfg(local_llm=None))
    assert wd.check_local_llm()["ok"] is None


# --- the surface Justin actually reads -----------------------------------


def test_healthz_reports_not_ok_when_generation_is_dead():
    """/api/healthz is what the page renders. It read green for fourteen hours."""
    from fastapi.testclient import TestClient

    from alicia.server import create_app

    cfg = _cfg()
    cfg.watchdog_enabled = False
    models_resp = MagicMock()
    models_resp.raise_for_status = MagicMock()
    models_resp.json.return_value = {"data": [{"id": "test-model"}]}

    with patch("alicia.server.AtlasClient") as cls:
        cls.return_value = MagicMock()
        app = create_app(cfg, start_watchdog=False)
        app.state.watchdog._state["local_llm"] = {"ok": False, "error": "ReadTimeout"}
        with patch("alicia.local_llm.httpx.Client") as client_cls:
            client_cls.return_value.__enter__.return_value.get.return_value = models_resp
            body = TestClient(app).get("/api/healthz").json()

    assert body["local_llm"]["generation"]["ok"] is False
    # The reachability probe said 200; the endpoint must not inherit that verdict.
    assert body["local_llm"]["ok"] is False
