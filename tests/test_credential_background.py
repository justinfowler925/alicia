import os
import subprocess
from pathlib import Path

import pytest


WRAPPER = Path(__file__).resolve().parents[1] / "scripts/run-with-credential-backoff.sh"


@pytest.mark.parametrize("credential_status", [0, 78])
def test_background_credentials_cannot_enable_desktop_prompts(tmp_path, credential_status):
    reader = tmp_path / "credential-run"
    reader.write_text(
        '#!/bin/bash\n'
        'test "$OP_LOAD_DESKTOP_APP_SETTINGS" = false || exit 90\n'
        'test "$OP_BIOMETRIC_UNLOCK_ENABLED" = false || exit 91\n'
        'printf "headless\\n"\n'
        f'exit {credential_status}\n'
    )
    reader.chmod(0o700)
    result = subprocess.run(
        ["bash", str(WRAPPER), "brutus-core", "--", "/usr/bin/true"],
        env={
            **os.environ,
            "CREDENTIAL_RUN": str(reader),
            "BRUTUS_SECURITY_BIN": "/nonexistent",
            "BRUTUS_CREDENTIAL_MAX_ATTEMPTS": "1",
            "OP_BIOMETRIC_UNLOCK_ENABLED": "true",
            "OP_LOAD_DESKTOP_APP_SETTINGS": "true",
        },
        capture_output=True,
        text=True,
        timeout=5,
    )
    assert result.returncode == credential_status, result.stderr
    assert result.stdout == "headless\n"
