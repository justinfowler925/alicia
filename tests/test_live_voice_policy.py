"""One voice provider and provider-specific recovery through real entry points."""
from unittest.mock import Mock, patch

import pytest
from fastapi.testclient import TestClient

from alicia import brain, cursor_cli
from alicia.config import AliciaCfg, VoiceCfg
from alicia.server import create_app
from alicia.tools import ToolRegistry


def test_live_voice_refuses_legacy_audio_even_with_valid_tts_config(monkeypatch):
    monkeypatch.setenv('ALICIA_VOICE_PROVIDER', 'openai_live')
    cfg = AliciaCfg(watchdog_enabled=False, voice=VoiceCfg(enabled=True, elevenlabs_api_key='test-key'))
    with patch('alicia.server.AtlasClient'), patch('alicia.server.voice_speak', return_value=b'ID3audio') as tts:
        client = TestClient(create_app(cfg, start_watchdog=False))
        response = client.post('/api/speak', json={'text': 'Late answer after Stop.'})
        assert response.status_code == 409
        assert 'GPT-Live' in response.json()['detail']
        assert client.get('/api/voice').json()['provider'] == 'openai_live'
        tts.assert_not_called()
        # The same gate must still permit the explicitly selected legacy provider.
        monkeypatch.setenv('ALICIA_VOICE_PROVIDER', 'legacy')
        assert client.post('/api/speak', json={'text': 'Legacy answer.'}).content == b'ID3audio'
        tts.assert_called_once()


@pytest.mark.parametrize('diagnostic,expected_code', [
    ('upstream unavailable', 'brain_service_unavailable'),
    ('Not authenticated. Please log in.', 'brain_auth_unavailable'),
])
def test_cursor_failure_never_prescribes_claude_login(monkeypatch, tmp_path, diagnostic, expected_code):
    monkeypatch.setenv('ALICIA_CONVERSATION_PROVIDER', 'cursor')
    monkeypatch.setenv('ALICIA_REASONING_ROOT', str(tmp_path))
    process = Mock(returncode=1)
    process.communicate.return_value = ('', diagnostic)
    monkeypatch.setattr(cursor_cli.subprocess, 'Popen', Mock(return_value=process))
    with patch('alicia.claude.ask_claude') as claude:
        reply, meta = brain.brain_reply(AliciaCfg(), ToolRegistry(),
            history=[{'role': 'user', 'content': 'Explain gravity.'}], channel='voice')
    assert 'claude' not in reply.lower()
    assert 'Cursor' in reply
    assert meta['backend'] == 'cursor'
    assert meta['cli_error'] == expected_code
    assert diagnostic in meta['error']
    claude.assert_not_called()


def test_cursor_failure_diagnostic_redacts_credentials(monkeypatch, tmp_path):
    monkeypatch.setenv('ALICIA_REASONING_ROOT', str(tmp_path))
    monkeypatch.setenv('CURSOR_API_KEY', 'private-example-value')
    process = Mock(returncode=1)
    process.communicate.return_value = ('', 'upstream unavailable; token=private-example-value')
    monkeypatch.setattr(cursor_cli.subprocess, 'Popen', Mock(return_value=process))
    with pytest.raises(RuntimeError) as error:
        cursor_cli.complete('hello')
    assert 'private-example-value' not in str(error.value)
    assert 'upstream unavailable' in str(error.value)
