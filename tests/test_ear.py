"""Global ear — no real microphone, no real hotkey daemon."""

from __future__ import annotations

from unittest.mock import patch

from alicia.ear import Ear
from alicia.voice import save_wav


def test_status_reports_missing_pynput() -> None:
    ear = Ear(on_utterance=lambda _pcm: None)
    with patch("alicia.ear._load_keyboard", return_value=False):
        status = ear.start()
    assert status["listening"] is False
    assert "pynput" in (ear.last_error or "")


def test_status_reports_missing_pyaudio() -> None:
    ear = Ear(on_utterance=lambda _pcm: None)
    with patch("alicia.ear._load_keyboard", return_value=True), patch(
        "alicia.ear.HAS_PYAUDIO", False
    ):
        status = ear.start()
    assert status["listening"] is False
    assert "pyaudio" in (ear.last_error or "")


def test_deliver_counts_utterances_and_swallows_handler_errors() -> None:
    seen: list[bytes] = []

    def boom(pcm: bytes) -> None:
        seen.append(pcm)
        raise RuntimeError("handler exploded")

    ear = Ear(on_utterance=boom)
    ear._deliver(b"\x01\x00" * 16)
    assert seen == [b"\x01\x00" * 16]
    assert ear.utterances == 0  # increment happens after success
    assert "exploded" in ear.last_error

    ear2 = Ear(on_utterance=seen.append)
    ear2._deliver(b"ok")
    assert ear2.utterances == 1


def test_stop_record_drops_digital_silence(tmp_path) -> None:
    ear = Ear(on_utterance=lambda _pcm: None)
    silent = b"\x00\x00" * 8000
    wav = tmp_path / "s.wav"
    save_wav(silent, wav)
    ear._recording = True
    ear._frames = [silent]
    ear._started_at = 0.0  # elapsed will be large
    import time

    ear._started_at = time.monotonic() - 1.0
    assert ear._stop_record() is None
    assert "silence" in ear.last_error.lower()


def test_target_key_matches_right_option_only() -> None:
    ear = Ear(on_utterance=lambda _pcm: None, hotkey="alt_r")
    with patch("alicia.ear.HAS_PYNPUT", False):
        assert ear._target_key(object()) is False
