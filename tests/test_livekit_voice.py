from __future__ import annotations

import asyncio
import base64
import json
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from brutus.config import BrutusCfg, VoiceCfg
from brutus.livekit_agent import BrutusVoiceAgent, OwnerVoiceGate, session_id_from_room
from brutus.server import create_app


def test_room_name_round_trip_and_rejects_unscoped_rooms():
    assert session_id_from_room("brutus-123456abcdef-a1b2c3d4") == "123456abcdef"
    with pytest.raises(ValueError, match="invalid Brutus voice room"):
        session_id_from_room("some-other-room")


def test_voice_token_is_disabled_without_complete_local_config():
    cfg = BrutusCfg(watchdog_enabled=False, voice=VoiceCfg(enabled=True))
    with patch("brutus.server.AtlasClient") as atlas:
        atlas.return_value = MagicMock()
        client = TestClient(create_app(cfg, start_watchdog=False))
        sid = client.post("/api/session/open", json={"title": "voice eval"}).json()["session_id"]
        out = client.post(f"/api/session/{sid}/voice-token")
    assert out.status_code == 200
    assert out.json()["enabled"] is False
    assert out.json()["owner_enrollment_required"] is True


def test_voice_token_is_room_scoped_and_short_lived():
    cfg = BrutusCfg(
        watchdog_enabled=False,
        voice=VoiceCfg(
            enabled=True,
            livekit_url="ws://127.0.0.1:7880",
            livekit_api_key="local-key",
            livekit_api_secret="a-local-secret-long-enough-for-signing",
        ),
    )
    with patch("brutus.server.AtlasClient") as atlas:
        atlas.return_value = MagicMock()
        app = create_app(cfg, start_watchdog=False)
        app.state.voice_identity = MagicMock()
        app.state.voice_identity.status.return_value = {"enrolled": True}
        client = TestClient(app)
        sid = client.post("/api/session/open", json={"title": "voice eval"}).json()["session_id"]
        payload = client.post(f"/api/session/{sid}/voice-token").json()
    assert payload["enabled"] is True
    assert payload["url"] == "ws://127.0.0.1:7880"
    assert payload["room"].startswith(f"brutus-{sid}-")
    assert payload["token"].count(".") == 2
    body = payload["token"].split(".")[1]
    claims = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
    assert 0 < claims["exp"] - claims["nbf"] <= 600


def test_livekit_agent_calls_canonical_session_endpoint():
    message = MagicMock(role="user", text_content="What needs me today?")
    chat_ctx = MagicMock()
    chat_ctx.messages.return_value = [message]
    response = MagicMock()
    response.raise_for_status.return_value = None
    response.json.return_value = {"reply": "Two decisions need you."}
    client = AsyncMock()
    client.post.return_value = response
    client.__aenter__.return_value = client

    with patch("brutus.livekit_agent.httpx.AsyncClient", return_value=client):
        reply = asyncio.run(
            BrutusVoiceAgent("123456abcdef", OwnerVoiceGate()).llm_node(chat_ctx, [], MagicMock())
        )

    assert reply == "Two decisions need you."
    client.post.assert_awaited_once_with(
        "http://127.0.0.1:8768/api/session/123456abcdef/say",
        json={
            "message": "What needs me today?",
            "channel": "voice",
            "read_only": False,
            "wait": True,
            # The fallback path cannot say whose voice this was, so it claims
            # nothing. A turn that claims nothing still gets answered; it just
            # cannot approve a pending write by saying yes.
            "owner_verified": False,
        },
    )


def test_launchers_start_real_worker_and_deploy_installs_both_jobs():
    root = Path(__file__).parents[1]
    agent = (root / "scripts/brutus-livekit-agent.sh").read_text()
    livekit_server = (root / "scripts/brutus-livekit-server.sh").read_text()
    server = (root / "scripts/brutus-serve.sh").read_text()
    deploy = (root / "scripts/deploy.sh").read_text()
    assert "$HOME/fowler-brain/scripts/credential-run" in agent
    assert "brutus-core" in agent
    assert '"$RUNTIME_VENV/bin/python" -m brutus.livekit_agent start' in agent
    assert 'BRUTUS_CONFIG="${BRUTUS_CONFIG:-$BRUTUS_APP_DIR/config.yaml}"' in agent
    assert "run-with-credential-backoff.sh" in agent
    assert "run-with-credential-backoff.sh" in server
    assert "secrets_softload" not in agent + server
    assert "--key-file" in livekit_server
    assert "--keys" not in livekit_server
    assert "--rtc.tcp_port 0" in livekit_server
    assert "com.clearspeed.brutus-livekit.plist" in deploy
    assert "com.clearspeed.brutus-livekit-agent.plist" in deploy


def test_livekit_health_listener_defaults_to_loopback():
    source = (Path(__file__).parents[1] / "brutus/livekit_agent.py").read_text()
    assert 'host=os.environ.get("BRUTUS_VOICE_HEALTH_HOST", "127.0.0.1")' in source


def test_voice_startup_never_loads_global_input_monitoring_without_opt_in(tmp_path: Path):
    root = Path(__file__).parents[1]
    probe = """
import sys
from fastapi.testclient import TestClient
from brutus.config import BrutusCfg, VoiceCfg
from brutus.server import create_app

cfg = BrutusCfg(
    watchdog_enabled=False,
    voice=VoiceCfg(enabled=True, ear_enabled=False),
)
with TestClient(create_app(cfg, start_watchdog=True)):
    assert "brutus.ear" not in sys.modules
    assert "pynput" not in sys.modules
"""
    env = os.environ.copy()
    env["BRUTUS_STATE_DIR"] = str(tmp_path / "state")
    result = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=root,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_credential_config_failure_backs_off_instead_of_exiting(tmp_path: Path):
    root = Path(__file__).parents[1]
    fake = tmp_path / "credential-run"
    calls = tmp_path / "calls"
    fake.write_text(f"#!/usr/bin/env bash\nprintf 'x\\n' >> {calls}\nexit 78\n")
    fake.chmod(0o755)
    env = os.environ.copy()
    env.update(
        CREDENTIAL_RUN=str(fake),
        BRUTUS_CREDENTIAL_RETRY_SECONDS="0",
        BRUTUS_CREDENTIAL_MAX_ATTEMPTS="2",
    )
    result = subprocess.run(
        [
            str(root / "scripts/run-with-credential-backoff.sh"),
            "brutus-core",
            "--",
            "/usr/bin/true",
        ],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 78
    assert calls.read_text().splitlines() == ["x", "x"]
    assert "retrying in 0s" in result.stderr


def test_launchd_helper_loads_service_account_from_keychain(tmp_path: Path):
    root = Path(__file__).parents[1]
    security = tmp_path / "security"
    security.write_text("#!/usr/bin/env bash\nprintf 'test-service-account-token\\n'\n")
    security.chmod(0o755)
    credential_run = tmp_path / "credential-run"
    credential_run.write_text(
        '#!/usr/bin/env bash\n[[ "$OP_SERVICE_ACCOUNT_TOKEN" == test-service-account-token ]]\n'
    )
    credential_run.chmod(0o755)
    env = os.environ.copy()
    env.pop("OP_SERVICE_ACCOUNT_TOKEN", None)
    env.update(
        CREDENTIAL_RUN=str(credential_run),
        BRUTUS_SECURITY_BIN=str(security),
        BRUTUS_CREDENTIAL_MAX_ATTEMPTS="1",
    )
    result = subprocess.run(
        [
            str(root / "scripts/run-with-credential-backoff.sh"),
            "brutus-core",
            "--",
            "/usr/bin/true",
        ],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_batch_stt_uses_vad_only_barge_in_gate():
    source = (Path(__file__).parents[1] / "brutus/livekit_agent.py").read_text()
    assert 'model="scribe_v2"' in source
    assert '"mode": "vad"' in source
    assert '"min_words": 0' in source
    assert "aec_warmup_duration=None" in source
