"""System-wide push-to-talk into Alicia. No page required.

Hold Right Option, speak, release. The serve process already owns Whisper and
ElevenLabs; this is just the key and the mic. Writes still go through artifacts
— the ear proposes, it does not silently approve.
"""

from __future__ import annotations

import logging
import subprocess
import threading
import time
from collections.abc import Callable
from typing import Any

from .voice import (
    DEFAULT_CHANNELS,
    DEFAULT_SAMPLE_RATE,
    HAS_PYAUDIO,
    _is_digital_silence,
    pyaudio,
)

log = logging.getLogger("alicia.ear")

# Importing pynput on macOS queries global input monitoring and can surface an
# Accessibility approval. Keep even this module safe to inspect; only start()
# may load the optional dependency, after the separate ear_enabled opt-in.
keyboard: Any | None = None
HAS_PYNPUT = False


def _load_keyboard() -> bool:
    global HAS_PYNPUT, keyboard
    if keyboard is not None:
        return True
    try:
        from pynput import keyboard as pynput_keyboard
    except Exception:  # noqa: BLE001 — optional; serve must stay up without it
        return False
    keyboard = pynput_keyboard
    HAS_PYNPUT = True
    return True

START_SOUND = "/System/Library/Sounds/Tink.aiff"
END_SOUND = "/System/Library/Sounds/Pop.aiff"
MAX_SECONDS = 20.0
MIN_SECONDS = 0.35
CHUNK = 1024


def _play(path: str) -> None:
    # Cue pings are intentionally silent. Spoken replies use play_mpeg.
    return


class Ear:
    """Hold-to-talk listener. One instance per serve process."""

    def __init__(
        self,
        *,
        on_utterance: Callable[[bytes], None],
        hotkey: str = "alt_r",
    ) -> None:
        self._on_utterance = on_utterance
        self.hotkey = hotkey
        self._listener: Any = None
        self._pa: Any = None
        self._stream: Any = None
        self._frames: list[bytes] = []
        self._lock = threading.Lock()
        self._recording = False
        self._started_at = 0.0
        self._player: subprocess.Popen[bytes] | None = None
        self.last_error = ""
        self.presses = 0
        self.utterances = 0

    @property
    def listening(self) -> bool:
        return self._listener is not None

    @property
    def recording(self) -> bool:
        return self._recording

    def status(self) -> dict[str, Any]:
        return {
            "enabled": HAS_PYNPUT and HAS_PYAUDIO,
            "listening": self.listening,
            "recording": self.recording,
            "hotkey": "Right Option (hold)",
            "pynput": HAS_PYNPUT,
            "pyaudio": HAS_PYAUDIO,
            "presses": self.presses,
            "utterances": self.utterances,
            "last_error": self.last_error,
        }

    def start(self) -> dict[str, Any]:
        if not _load_keyboard():
            self.last_error = "pynput is not installed. pip install pynput"
            log.warning(self.last_error)
            return self.status()
        if not HAS_PYAUDIO:
            self.last_error = "pyaudio is not installed"
            log.warning(self.last_error)
            return self.status()
        if self._listener is not None:
            return self.status()
        assert keyboard is not None
        self._listener = keyboard.Listener(
            on_press=self._on_press,
            on_release=self._on_release,
        )
        self._listener.daemon = True
        self._listener.start()
        log.info("ear listening for Right Option (hold)")
        return self.status()

    def stop(self) -> None:
        self._stop_record(discard=True)
        if self._listener is not None:
            try:
                self._listener.stop()
            except Exception:  # noqa: BLE001
                pass
            self._listener = None
        self._kill_player()

    def play_mpeg(self, audio: bytes) -> None:
        """Speak a reply. Killed automatically on the next press."""
        self._kill_player()
        if not audio:
            return
        try:
            proc = subprocess.Popen(
                ["/usr/bin/afplay", "-"],
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except OSError as exc:
            self.last_error = f"afplay failed: {exc}"
            return
        assert proc.stdin is not None
        try:
            proc.stdin.write(audio)
            proc.stdin.close()
        except BrokenPipeError:
            return
        self._player = proc

    def _target_key(self, key: object) -> bool:
        if not HAS_PYNPUT or keyboard is None:
            return False
        if self.hotkey == "alt_r":
            return key == keyboard.Key.alt_r
        return False

    def _on_press(self, key: object) -> None:
        if not self._target_key(key):
            return
        with self._lock:
            if self._recording:
                return
            self.presses += 1
        self._kill_player()
        self._start_record()

    def _on_release(self, key: object) -> None:
        if not self._target_key(key):
            return
        pcm = self._stop_record()
        if pcm is None:
            return
        threading.Thread(target=self._deliver, args=(pcm,), daemon=True).start()

    def _start_record(self) -> None:
        if pyaudio is None:
            self.last_error = "pyaudio missing at record time"
            return
        try:
            pa = pyaudio.PyAudio()
            stream = pa.open(
                format=pyaudio.paInt16,
                channels=DEFAULT_CHANNELS,
                rate=DEFAULT_SAMPLE_RATE,
                input=True,
                frames_per_buffer=CHUNK,
            )
        except Exception as exc:  # noqa: BLE001 — mic grant / device
            self.last_error = f"mic open failed: {exc}"
            log.warning(self.last_error)
            return
        with self._lock:
            self._pa = pa
            self._stream = stream
            self._frames = []
            self._recording = True
            self._started_at = time.monotonic()
        _play(START_SOUND)
        threading.Thread(target=self._pump, daemon=True).start()

    def _pump(self) -> None:
        while True:
            with self._lock:
                stream = self._stream
                recording = self._recording
                started = self._started_at
            if not recording or stream is None:
                return
            if time.monotonic() - started >= MAX_SECONDS:
                pcm = self._stop_record()
                if pcm is not None:
                    threading.Thread(target=self._deliver, args=(pcm,), daemon=True).start()
                return
            try:
                chunk = stream.read(CHUNK, exception_on_overflow=False)
            except Exception:  # noqa: BLE001
                return
            with self._lock:
                if self._recording:
                    self._frames.append(chunk)

    def _stop_record(self, *, discard: bool = False) -> bytes | None:
        with self._lock:
            if not self._recording:
                return None
            self._recording = False
            stream = self._stream
            pa = self._pa
            frames = self._frames
            started = self._started_at
            self._stream = None
            self._pa = None
            self._frames = []
        if stream is not None:
            try:
                stream.stop_stream()
                stream.close()
            except Exception:  # noqa: BLE001
                pass
        if pa is not None:
            try:
                pa.terminate()
            except Exception:  # noqa: BLE001
                pass
        if discard:
            return None
        elapsed = time.monotonic() - started
        pcm = b"".join(frames)
        if elapsed < MIN_SECONDS or not pcm:
            return None
        if _is_digital_silence(pcm):
            self.last_error = (
                "Microphone returned digital silence. Grant Microphone to the "
                "Alicia process (Privacy & Security → Microphone) and press "
                "Right Option again."
            )
            log.warning(self.last_error)
            return None
        _play(END_SOUND)
        return pcm

    def _deliver(self, pcm: bytes) -> None:
        try:
            self._on_utterance(pcm)
            self.utterances += 1
        except Exception as exc:  # noqa: BLE001 — never kill the listener
            self.last_error = str(exc)
            log.exception("ear utterance failed")

    def _kill_player(self) -> None:
        proc = self._player
        self._player = None
        if proc is not None and proc.poll() is None:
            proc.kill()
