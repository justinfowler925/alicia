"""Brutus -> Alicia rename: legacy names keep working for one release."""

import os
import subprocess
import sys


def test_legacy_env_vars_are_adopted():
    env = {k: v for k, v in os.environ.items() if not k.startswith(("ALICIA_", "BRUTUS_"))}
    env["BRUTUS_STATE_DIR"] = "/tmp/legacy-state"
    env["BRUTUS_PORT"] = "1"
    env["ALICIA_PORT"] = "2"
    out = subprocess.run(
        [sys.executable, "-c", "import os, alicia; print(os.environ['ALICIA_STATE_DIR'], os.environ['ALICIA_PORT'])"],
        env=env, capture_output=True, text=True, check=True,
    )
    assert out.stdout.split() == ["/tmp/legacy-state", "2"]
    assert "BRUTUS_STATE_DIR" in out.stderr


def test_brutus_alias_warns_and_forwards(monkeypatch, capsys):
    import alicia.__main__ as cli
    from alicia import legacy

    called = []
    monkeypatch.setattr(cli, "main", lambda: called.append(True))
    legacy.brutus_main()
    assert called == [True]
    assert "renamed to `alicia`" in capsys.readouterr().err


def test_legacy_adapter_header_is_accepted(monkeypatch):
    from alicia import security

    monkeypatch.setenv("ALICIA_ADAPTER_TOKEN", "tok")
    security.require_adapter_token(x_alicia_adapter_token=None, x_brutus_adapter_token="tok")
