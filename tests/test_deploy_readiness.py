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

    # Two lists, not a line per URL. Naming each individually failed two
    # deploys in a row: /mobile then /console each became a redirect while the
    # verifier still demanded 200 from it.
    assert 'for path in "/" "/session"; do' in source
    assert 'wait_for_http_200 "http://127.0.0.1:$PORT$path"' in source
    assert 'for path in "/console" "/mobile"; do' in source
    assert '[ "$CODE" = "308" ]' in source
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
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert active.resolve() == new
    assert list(old.iterdir()) == []


def test_release_payload_gate_rejects_cached_wheel_and_missing_modules(tmp_path):
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "runtime_verify", ROOT / "scripts/verify-runtime-package.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    source, installed = tmp_path / "source", tmp_path / "installed"
    source.mkdir()
    installed.mkdir()
    (source / "ui.py").write_text("new release")
    (installed / "ui.py").write_text("old wheel")
    import pytest

    with pytest.raises(ValueError, match="ui.py"):
        module.verify(source, installed)
    (installed / "ui.py").write_text("new release")
    assert module.verify(source, installed) == 1
    (source / "studio_ui.py").write_text("new module")
    with pytest.raises(ValueError, match="studio_ui.py"):
        module.verify(source, installed)
    text = DEPLOY.read_text()
    assert "--reinstall-package alicia" in text
    assert "scripts/verify-runtime-package.py" in text
