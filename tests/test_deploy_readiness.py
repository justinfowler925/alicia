import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEPLOY = ROOT / "scripts" / "deploy.sh"


def test_deploy_waits_for_the_replacement_actor_before_probing_surfaces():
    source = DEPLOY.read_text()

    assert source.index("PRE_RESTART_PID=$(service_pid)") < source.index(
        'echo "==> syncing the launchd plist"'
    )
    assert 'wait_for_new_actor "$PRE_RESTART_PID"' in source
    assert 'pid != "$old_pid"' not in source  # the executable comparison is POSIX `[ ... ]`
    assert '[ "$pid" != "$old_pid" ]' in source
    assert 'stable="$((stable + 1))"' not in source
    assert "stable=$((stable + 1))" in source
    assert '[ "$stable" -ge 2 ]' in source
    assert "/api/healthz" in source


def test_user_facing_endpoints_retry_after_actor_stability():
    source = DEPLOY.read_text()

    assert 'wait_for_http_200 "http://127.0.0.1:$PORT/session"' in source
    assert 'wait_for_http_200 "http://127.0.0.1:$PORT/mobile"' in source
    assert "if wait_for_todos; then" in source
    assert "if not isinstance(t, list): raise SystemExit(1)" in source


def test_deploy_script_is_valid_bash():
    result = subprocess.run(
        ["bash", "-n", str(DEPLOY)],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr


def test_deploy_builds_locked_immutable_runtime_and_recycles_voice():
    source = DEPLOY.read_text()

    assert "uv.lock" in source and "--locked" in source
    assert 'RUNTIME_ROOT="$APP/.venvs/$TARGET_SHA"' in source
    assert 'ln -sfn ".venvs/$TARGET_SHA" "$APP/.runtime-venv.next"' in source
    assert 'unload_job "$VOICE_AGENT_LABEL"' in source
    assert 'wait_for_voice_actor "$PRE_VOICE_PID"' in source
    assert "left running; restart separately" not in source


def test_runtime_switch_replaces_directory_symlink_without_following_it(tmp_path):
    import os
    import sys

    app = tmp_path / "app"
    old = app / ".venvs" / "old"
    new = app / ".venvs" / "new"
    old.mkdir(parents=True)
    (new / "bin").mkdir(parents=True)
    (new / "bin" / "python").symlink_to(sys.executable)
    active = app / ".runtime-venv"
    active.symlink_to(".venvs/old")
    source = DEPLOY.read_text()
    start = source.index('ln -sfn ".venvs/$TARGET_SHA"')
    end = source.index("# Prove the pin", start)
    result = subprocess.run(
        ["bash", "-c", source[start:end]],
        env=dict(os.environ, APP=str(app), TARGET_SHA="new", RUNTIME_ROOT=str(new), RUNTIME_VENV=str(active)),
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stderr
    assert active.resolve() == new
    assert list(old.iterdir()) == []
