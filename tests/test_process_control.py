"""Stopping things, and refusing to stop the wrong thing.

Brutus could show thirty running agent threads and twenty scheduled jobs and
touch none of them. These are the controls, and most of what follows is about
the ways a control like this kills something it was never pointed at.
"""

import os
import signal
import subprocess
from unittest.mock import MagicMock, patch

import pytest

from brutus import process_control as pc


def _completed(returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


# --- services ---------------------------------------------------------------


def test_only_brutus_services_can_be_addressed():
    labels = pc.known_service_labels()
    assert "com.clearspeed.brutus" in labels
    assert all(label.startswith("com.clearspeed.brutus") for label in labels)

    with pytest.raises(pc.ControlError, match="not a Brutus service"):
        pc.service_action("com.apple.Finder", "stop")
    with pytest.raises(pc.ControlError, match="not a Brutus service"):
        pc.service_action("../../etc/passwd", "restart")


def test_an_unknown_action_is_refused_before_anything_runs():
    with (
        patch("brutus.process_control.subprocess.run") as run,
        pytest.raises(pc.ControlError, match="Unknown service action"),
    ):
        pc.service_action("com.clearspeed.brutus-livekit-agent", "obliterate")
    assert not run.called


def test_restarting_a_worker_service_kickstarts_it_in_place():
    with patch("brutus.process_control.subprocess.run", return_value=_completed()) as run:
        result = pc.service_action("com.clearspeed.brutus-livekit-agent", "restart")
    argv = run.call_args_list[0][0][0]
    assert argv[:3] == ["/bin/launchctl", "kickstart", "-k"]
    assert result["action"] == "restart"


def test_launchctl_noise_about_a_no_op_is_not_a_failure():
    """Booting out something already gone is the end state that was asked for."""
    gone = _completed(returncode=3, stderr="Boot-out failed: 3: No such process")
    with patch("brutus.process_control.subprocess.run", return_value=gone):
        assert pc.service_action("com.clearspeed.brutus-my-notes", "stop")["ok"] is True


def test_restarting_the_core_waits_for_its_own_response_to_land():
    """Booting the core out inline kills the worker mid-reply.

    The UI then sees a network error and cannot tell a clean restart from a
    crash, so the restart is handed to a detached child and announced.
    """
    with patch("brutus.process_control.subprocess.Popen") as popen:
        result = pc.service_action(pc.CORE_LABEL, "restart")

    command = popen.call_args[0][0][2]
    assert command.startswith("sleep 1;")
    assert "kickstart -k" in command
    assert popen.call_args.kwargs["start_new_session"] is True
    assert result["deferred"] is True
    assert "connection will drop" in result["detail"]


# --- agent threads ----------------------------------------------------------


AGENT_ROW = {"id": "claude:abc", "pid": 4242, "surface": "claude", "live": True}


def test_cancelling_an_agent_signals_it_and_waits_before_killing():
    with patch("brutus.process_control._command_of", side_effect=["claude --resume", ""]), \
         patch("brutus.process_control.os.kill") as kill:
        result = pc.cancel_agent("claude:abc", [AGENT_ROW])

    kill.assert_called_once_with(4242, signal.SIGTERM)
    assert result["signal"] == "SIGTERM"


def test_an_agent_that_ignores_sigterm_is_killed():
    with patch("brutus.process_control._command_of", return_value="claude --resume"), \
         patch("brutus.process_control.os.kill") as kill, \
         patch("brutus.process_control.time.monotonic", side_effect=[0.0, 99.0]), \
         patch("brutus.process_control.time.sleep"):
        result = pc.cancel_agent("claude:abc", [AGENT_ROW])

    assert [call[0][1] for call in kill.call_args_list] == [signal.SIGTERM, signal.SIGKILL]
    assert result["signal"] == "SIGKILL"


def test_a_recycled_pid_is_refused_rather_than_signalled():
    """This is the one that matters. A stale row plus an unlucky recycle is how
    a cancel button kills the user's editor."""
    with (
        patch("brutus.process_control._command_of", return_value="/Applications/Xcode.app/xcode"),
        patch("brutus.process_control.os.kill") as kill,
        pytest.raises(pc.ControlError, match="different program"),
    ):
        pc.cancel_agent("claude:abc", [AGENT_ROW])
    assert not kill.called


def test_an_already_exited_thread_is_a_success_not_an_error():
    with patch("brutus.process_control._command_of", return_value=""), \
         patch("brutus.process_control.os.kill") as kill:
        result = pc.cancel_agent("claude:abc", [AGENT_ROW])
    assert result["ok"] is True and "already exited" in result["detail"]
    assert not kill.called


def test_a_thread_that_is_not_on_the_board_cannot_be_cancelled():
    with pytest.raises(pc.ControlError, match="no longer on the board"):
        pc.cancel_agent("claude:ghost", [AGENT_ROW])


def test_a_row_without_a_pid_is_explained_not_signalled():
    with pytest.raises(pc.ControlError, match="already finished"):
        pc.cancel_agent("claude:abc", [{"id": "claude:abc", "pid": None}])


def test_brutus_refuses_to_signal_itself():
    row = {"id": "claude:self", "pid": os.getpid()}
    with pytest.raises(pc.ControlError, match="Brutus itself"):
        pc.cancel_agent("claude:self", [row])


# --- scheduled jobs on the Studio ------------------------------------------


JOB = {"id": "com.clearspeed.atlas-signals-partial", "status": "failure"}


def test_a_job_label_must_come_from_the_collectors_own_snapshot():
    """A label straight from a browser is a shell injection with a job title."""
    with (
        patch("brutus.process_control.subprocess.run") as run,
        pytest.raises(pc.ControlError, match="not in the current Studio snapshot"),
    ):
        pc.studio_job_action("$(rm -rf ~)", "disable", [JOB], "studio")
    assert not run.called


def test_a_bad_ssh_target_is_refused():
    with pytest.raises(pc.ControlError, match="Invalid Studio SSH target"):
        pc.studio_job_action(JOB["id"], "disable", [JOB], "-oProxyCommand=evil")


@pytest.mark.parametrize(
    ("action", "expected"),
    [("disable", "bootout"), ("enable", "kickstart"), ("run", "kickstart -k")],
)
def test_each_job_action_maps_to_the_right_launchctl_verb(action, expected):
    with patch("brutus.process_control.subprocess.run", return_value=_completed()) as run:
        assert pc.studio_job_action(JOB["id"], action, [JOB], "studio")["ok"] is True
    remote_cmd = run.call_args[0][0][-1]
    assert expected in remote_cmd
    assert JOB["id"] in remote_cmd


def test_a_failing_studio_command_surfaces_its_own_message():
    with (
        patch(
            "brutus.process_control.subprocess.run",
            return_value=_completed(returncode=1, stderr="Could not find service"),
        ),
        pytest.raises(pc.ControlError, match="Could not find service"),
    ):
        pc.studio_job_action(JOB["id"], "enable", [JOB], "studio")


# --- the HTTP surface -------------------------------------------------------


def _app():
    from brutus.config import BrutusCfg
    from brutus.server import create_app

    with patch("brutus.server.AtlasClient") as atlas:
        atlas.return_value = MagicMock()
        return create_app(BrutusCfg(watchdog_enabled=False), start_watchdog=False)


def test_the_services_endpoint_reports_every_brutus_service():
    from fastapi.testclient import TestClient

    client = TestClient(_app())
    payload = client.get("/api/services").json()
    labels = [service["label"] for service in payload["services"]]

    assert "com.clearspeed.brutus" in labels
    core = next(s for s in payload["services"] if s["is_core"])
    assert set(core) >= {"loaded", "running", "pid", "plist_installed"}


def test_controlling_a_service_that_is_not_ours_is_a_400():
    from fastapi.testclient import TestClient

    client = TestClient(_app())
    response = client.post("/api/services/com.apple.Finder/stop")

    assert response.status_code == 400
    assert "not a Brutus service" in response.json()["detail"]


def test_cancelling_a_thread_the_board_does_not_have_is_a_400():
    from fastapi.testclient import TestClient

    with patch("brutus.server.scan_agent_sessions", return_value=[]):
        client = TestClient(_app())
        response = client.post("/api/agents/claude:ghost/cancel")

    assert response.status_code == 400
    assert "no longer on the board" in response.json()["detail"]


def test_the_service_list_does_not_depend_on_how_brutus_was_installed():
    """The release installs a non-editable wheel.

    Deriving the allowlist from `__file__` therefore pointed at site-packages,
    where there is no launchd/ — and /api/services answered {"services": []} on
    the live daemon while eight jobs were loaded. The installed LaunchAgents
    directory is the authority, and the label prefix is the allowlist.
    """
    import inspect

    source = inspect.getsource(pc.known_service_labels)
    assert "INSTALLED_AGENTS" in source
    assert pc.LAUNCH_PREFIX == "com.clearspeed.brutus"
    # Whatever this machine looks like, the core service is addressable.
    assert "com.clearspeed.brutus" in pc.known_service_labels()
