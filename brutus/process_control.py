"""Turning things off.

Brutus could show you thirty running agent threads, twenty scheduled jobs and
eight of its own services, and do nothing about any of them. There was no
cancel, stop, disable, restart or run-now anywhere in the codebase — the only
button named "Cancel" dismissed a confirmation dialog. Watching work you
cannot stop is worse than not watching it, because it reads as control.

Three things move here, and each has a different safe way to be touched:

  Agent threads are ordinary processes with a pid, so they take a signal. A
  pid is a reused integer, though, and a stale row plus an unlucky recycle is
  how you SIGKILL your own editor. So a pid is only ever signalled after the
  live scan still claims it AND the running command still looks like the agent
  it claims to be.

  Scheduled jobs live in launchd on another Mac. `launchctl` takes a label, and
  a label from a browser is a shell injection with a job title. Only labels the
  collector actually reported are accepted, matched against its own snapshot.

  Brutus's own services include the one answering this request. Restarting the
  core through launchd kills the process mid-response, so that one is handed to
  a detached child that waits for the reply to land first, and the caller is
  told the connection is about to drop rather than left guessing.
"""

from __future__ import annotations

import os
import re
import shlex
import signal
import subprocess
import time
from pathlib import Path
from typing import Any

APP_DIR = Path(__file__).resolve().parent.parent
PLIST_DIR = APP_DIR / "launchd"
INSTALLED_AGENTS = Path.home() / "Library" / "LaunchAgents"

# The service that is answering the request. Booting it out of launchd from
# inside itself drops the response on the floor.
CORE_LABEL = "com.clearspeed.brutus"
SERVICE_ACTIONS = ("start", "stop", "restart")
JOB_ACTIONS = ("enable", "disable", "run")

# A cancelled agent gets a chance to exit cleanly before it is killed.
TERM_GRACE_SECONDS = 3.0
# Command fragments that identify a process as one of the agent surfaces the
# board scans. A pid whose command matches none of these is not ours to touch,
# whatever the row says.
_AGENT_COMMANDS = ("claude", "codex", "cursor-agent", "cursor")


class ControlError(RuntimeError):
    """The action was refused. The message is safe to show on screen."""


# --- Brutus's own launchd services -----------------------------------------


def known_service_labels() -> list[str]:
    """Only the services this checkout ships a plist for."""
    return sorted(p.stem for p in PLIST_DIR.glob("com.clearspeed.brutus*.plist"))


def _launchctl(*args: str, timeout: float = 15.0) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["/bin/launchctl", *args], capture_output=True, text=True, timeout=timeout, check=False
    )


def _service_state(label: str) -> dict[str, Any]:
    printed = _launchctl("print", f"gui/{os.getuid()}/{label}")
    if printed.returncode != 0:
        return {"loaded": False, "pid": None, "last_exit": None, "running": False}
    text = printed.stdout
    pid = re.search(r"^\s*pid = (\d+)$", text, re.MULTILINE)
    exit_code = re.search(r"^\s*last exit code = (\d+)$", text, re.MULTILINE)
    return {
        "loaded": True,
        "pid": int(pid.group(1)) if pid else None,
        "last_exit": int(exit_code.group(1)) if exit_code else None,
        "running": bool(pid),
    }


def list_services() -> list[dict[str, Any]]:
    """Every Brutus service, whether it is loaded, and whether it is up."""
    services = []
    for label in known_service_labels():
        state = _service_state(label)
        services.append(
            {
                "label": label,
                "name": label.removeprefix("com.clearspeed.").replace("-", " "),
                "is_core": label == CORE_LABEL,
                "plist_installed": (INSTALLED_AGENTS / f"{label}.plist").exists(),
                **state,
            }
        )
    return services


def service_action(label: str, action: str) -> dict[str, Any]:
    """start | stop | restart one Brutus service."""
    if action not in SERVICE_ACTIONS:
        raise ControlError(f"Unknown service action: {action}")
    if label not in known_service_labels():
        raise ControlError(f"{label} is not a Brutus service")

    domain = f"gui/{os.getuid()}"
    if label == CORE_LABEL and action in ("stop", "restart"):
        return _defer_core_action(label, action)

    if action == "stop":
        result = _launchctl("bootout", f"{domain}/{label}")
    elif action == "start":
        plist = INSTALLED_AGENTS / f"{label}.plist"
        if not plist.exists():
            raise ControlError(f"{label} has no installed plist to load")
        result = _launchctl("bootstrap", domain, str(plist))
    else:
        result = _launchctl("kickstart", "-k", f"{domain}/{label}")

    # launchctl is noisy about no-ops: booting out something already gone, or
    # bootstrapping something already loaded, is the requested end state.
    stderr = (result.stdout + result.stderr).strip()
    benign = ("No such process" in stderr, "already loaded" in stderr, "Bootstrap failed: 37" in stderr)
    if result.returncode != 0 and not any(benign):
        raise ControlError(stderr[-240:] or f"launchctl {action} failed")
    return {"ok": True, "label": label, "action": action, "state": _service_state(label)}


def _defer_core_action(label: str, action: str) -> dict[str, Any]:
    """Restart the service that is answering this request, after it answers.

    Doing it inline kills the worker mid-response, so the UI sees a network
    error and cannot tell a successful restart from a crash.
    """
    verb = "kickstart -k" if action == "restart" else "bootout"
    # The sleep is the whole point: it outlives this response by a second.
    command = f"sleep 1; exec /bin/launchctl {verb} gui/{os.getuid()}/{label}"
    # Fixed argv; the label was allowlisted above.
    subprocess.Popen(
        ["/bin/sh", "-c", command],
        start_new_session=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return {
        "ok": True,
        "label": label,
        "action": action,
        "deferred": True,
        "detail": "Brutus is restarting itself — this connection will drop for a few seconds.",
    }


# --- agent threads ----------------------------------------------------------


def _command_of(pid: int) -> str:
    proc = subprocess.run(
        ["/bin/ps", "-o", "command=", "-p", str(pid)], capture_output=True, text=True,
        timeout=5, check=False,
    )
    return (proc.stdout or "").strip()


def cancel_agent(agent_id: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Signal one agent thread, but only if it is still the process it claims.

    `rows` is the live scan, passed in rather than re-derived, so the identity
    check runs against what the caller just showed on screen.
    """
    row = next((r for r in rows if r.get("id") == agent_id), None)
    if row is None:
        raise ControlError("That agent thread is no longer on the board")
    pid = row.get("pid")
    if not pid or int(pid) <= 0:
        raise ControlError("That thread has no live process — it has already finished")
    pid = int(pid)
    if pid == os.getpid():
        raise ControlError("That pid is Brutus itself")

    command = _command_of(pid)
    if not command:
        return {"ok": True, "agent_id": agent_id, "pid": pid, "detail": "already exited"}
    if not any(name in command.lower() for name in _AGENT_COMMANDS):
        # A recycled pid. Refusing is the only safe answer: the row is stale
        # and the integer now belongs to something else entirely.
        raise ControlError(
            "That pid now belongs to a different program — refresh the board before cancelling"
        )

    os.kill(pid, signal.SIGTERM)
    deadline = time.monotonic() + TERM_GRACE_SECONDS
    while time.monotonic() < deadline:
        if not _command_of(pid):
            return {"ok": True, "agent_id": agent_id, "pid": pid, "signal": "SIGTERM"}
        time.sleep(0.2)
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        return {"ok": True, "agent_id": agent_id, "pid": pid, "signal": "SIGTERM"}
    return {"ok": True, "agent_id": agent_id, "pid": pid, "signal": "SIGKILL"}


# --- scheduled jobs on the Studio ------------------------------------------


def studio_job_action(job_id: str, action: str, jobs: list[dict[str, Any]], ssh_host: str) -> dict[str, Any]:
    """enable | disable | run one scheduled job, by a label the collector reported."""
    if action not in JOB_ACTIONS:
        raise ControlError(f"Unknown job action: {action}")
    job = next((j for j in jobs if j.get("id") == job_id), None)
    if job is None:
        raise ControlError("That job is not in the current Studio snapshot")
    label = str(job.get("id") or "")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", label):
        raise ControlError("That job label cannot be addressed safely")
    if not re.fullmatch(r"[a-zA-Z0-9_.@-]+", ssh_host) or ssh_host.startswith("-"):
        raise ControlError("Invalid Studio SSH target")

    # `label` matched a strict charset above and came from the collector's own
    # snapshot, so it cannot carry shell syntax into the remote command.
    target = f"gui/$(id -u)/{shlex.quote(label)}"
    remote_cmd = {
        "disable": f"launchctl bootout {target}",
        "enable": f"launchctl kickstart {target}",
        "run": f"launchctl kickstart -k {target}",
    }[action]
    result = subprocess.run(
        [
            "/usr/bin/ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5",
            ssh_host, remote_cmd,
        ],
        capture_output=True, text=True, timeout=20, check=False,
    )
    output = (result.stdout + result.stderr).strip()
    if result.returncode != 0 and "No such process" not in output:
        raise ControlError(output[-240:] or f"Studio {action} failed")
    return {"ok": True, "job_id": job_id, "action": action, "detail": output[-240:]}
