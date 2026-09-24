"""Voice module — STT/TTS paths without requiring real microphone or GPU."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from alicia.config import AliciaCfg, VoiceCfg
from alicia.voice import load_wav, save_wav, speak


def test_save_and_load_wav_roundtrip(tmp_path: Path) -> None:
    pcm = b"\x00\x01\x02\x03" * 1000
    wav_path = tmp_path / "test.wav"
    save_wav(pcm, wav_path, sample_rate=16000, channels=1)
    assert wav_path.exists()
    loaded = load_wav(wav_path)
    assert loaded == pcm


def test_record_audio_raises_without_pyaudio() -> None:
    with patch("alicia.voice.HAS_PYAUDIO", False):
        with patch("alicia.voice.pyaudio", None):
            from alicia.voice import record_audio

            with pytest.raises(RuntimeError, match="pyaudio"):
                record_audio()


def test_transcribe_raises_without_whisper(tmp_path: Path) -> None:
    with patch("alicia.voice.HAS_WHISPER", False):
        with patch("alicia.voice.WhisperModel", None):
            from alicia.voice import transcribe

            wav_path = tmp_path / "empty.wav"
            save_wav(b"\x00" * 1000, wav_path)
            with pytest.raises(RuntimeError, match="faster-whisper"):
                transcribe(wav_path)


def test_transcribe_reuses_loaded_whisper_model(tmp_path: Path) -> None:
    from alicia import voice

    voice._whisper_model.cache_clear()
    wav_path = tmp_path / "voice.wav"
    save_wav(b"\x01\x00" * 1600, wav_path)
    model = MagicMock()
    model.transcribe.return_value = ([MagicMock(text=" hello ")], None)
    with patch("alicia.voice.HAS_WHISPER", True), patch("alicia.voice.WhisperModel", return_value=model) as cls:
        assert voice.transcribe(wav_path) == "hello"
        assert voice.transcribe(wav_path) == "hello"
    assert cls.call_count == 1, "a turn must not reload Whisper from disk"


def test_speak_calls_elevenlabs_api() -> None:
    fake_audio = b"fake-mp3-bytes"

    class FakeResponse:
        content = fake_audio

        def raise_for_status(self) -> None:
            pass

    class FakeClient:
        def __init__(self, **kwargs: object) -> None:
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc: object) -> None:
            pass

        def post(self, *args: object, **kwargs: object) -> FakeResponse:
            return FakeResponse()

    with patch("alicia.voice.httpx.Client", FakeClient):
        result = speak("hello alicia", api_key="fake-key")

    assert result == fake_audio


def test_listen_routes_read_only_query(tmp_path: Path) -> None:
    pcm = b"\x00" * 32000  # 1 second of 16-bit silence at 16kHz mono
    wav_path = tmp_path / "input.wav"
    save_wav(pcm, wav_path, sample_rate=16000, channels=1)

    client = MagicMock()
    client.status.return_value = {"blocked_justin": [], "completion_alarm": {}}
    client.list_awaiting_input.return_value = []
    client.list_threads.return_value = {"threads": []}

    cfg = AliciaCfg(voice=VoiceCfg(enabled=True))

    with patch("alicia.voice.record_audio", return_value=pcm):
        with patch("alicia.voice.transcribe", return_value="what is the status"):
            with patch("alicia.voice.resolve_chat_reply", return_value=("15 items need you.", {})) as resolver:
                from alicia.voice import listen

                result = listen(client, cfg, duration=1.0, read_only=True)

    assert result["ok"] is True
    assert result["transcription"] == "what is the status"
    assert result["reply"] == "15 items need you."
    assert result["path"] == "voice_read_only"
    resolver.assert_called_once()
    _, _, kwargs = resolver.mock_calls[0]
    assert kwargs["read_only"] is True


# --- a blocked microphone must not look like a quiet room -----------------
#
# macOS hands a process with no microphone grant a stream of zero samples
# rather than an error. listen() used to return {"ok": True, "transcription":
# ""} for that, which is success-colored silence: the exact shape a working mic
# in a silent room produces, so a permissions failure was undiagnosable.


def test_digital_silence_is_detected() -> None:
    from alicia.voice import _is_digital_silence

    assert _is_digital_silence(b"\x00" * 32000) is True
    assert _is_digital_silence(b"") is True


def test_room_noise_is_not_digital_silence() -> None:
    """A working mic in a silent room still carries preamp noise."""
    import struct

    from alicia.voice import _is_digital_silence

    noise = b"".join(struct.pack("<h", (i % 7) - 3 + (40 if i % 500 == 0 else 0)) for i in range(16000))
    assert _is_digital_silence(noise) is False


def test_blocked_mic_reports_failure_not_success(tmp_path: Path) -> None:
    silence = b"\x00" * 32000
    cfg = AliciaCfg(voice=VoiceCfg(enabled=True))

    with (
        patch("alicia.voice.record_audio", return_value=silence),
        patch("alicia.voice.transcribe", return_value=""),
    ):
        from alicia.voice import listen

        result = listen(MagicMock(), cfg, duration=1.0)

    assert result["ok"] is False, "a blocked microphone must never report ok=true"
    assert result["mic_blocked"] is True
    assert result["path"] == "mic_blocked"
    assert "Privacy" in result["error"]


def test_quiet_room_is_reported_as_no_speech_not_a_blocked_mic(tmp_path: Path) -> None:
    import struct

    noise = b"".join(struct.pack("<h", (i % 11) - 5) for i in range(16000))
    cfg = AliciaCfg(voice=VoiceCfg(enabled=True))

    with (
        patch("alicia.voice.record_audio", return_value=noise),
        patch("alicia.voice.transcribe", return_value=""),
    ):
        from alicia.voice import listen

        result = listen(MagicMock(), cfg, duration=1.0)

    assert result["ok"] is False
    assert result["mic_blocked"] is False
    assert result["path"] == "no_speech"
