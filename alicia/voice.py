"""Voice input/output for Alicia — local Whisper STT + ElevenLabs TTS.

All heavy dependencies are optional. If they are missing, the tools return a clear
error telling the user how to install them.
"""

from __future__ import annotations

import tempfile
import wave
from functools import lru_cache
from pathlib import Path
from typing import Any

import httpx

from .chat_resolve import resolve_chat_reply
from .client import AtlasClient
from .config import AliciaCfg
from .memory import MemoryStore

try:
    import pyaudio

    HAS_PYAUDIO = True
except Exception:  # noqa: BLE001 — optional dependency
    HAS_PYAUDIO = False
    pyaudio = None  # type: ignore[misc,assignment]

try:
    from faster_whisper import WhisperModel

    HAS_WHISPER = True
except Exception:  # noqa: BLE001 — optional dependency
    HAS_WHISPER = False
    WhisperModel = None  # type: ignore[misc,assignment]


DEFAULT_SAMPLE_RATE = 16000
DEFAULT_CHANNELS = 1
DEFAULT_FORMAT = "int16"


def _missing(name: str, install: str) -> RuntimeError:
    return RuntimeError(
        f"{name} is not available. Install voice dependencies: {install}"
    )


def record_audio(
    duration: float = 5.0,
    sample_rate: int = DEFAULT_SAMPLE_RATE,
    channels: int = DEFAULT_CHANNELS,
) -> bytes:
    """Record raw PCM audio from the default microphone for `duration` seconds."""
    if not HAS_PYAUDIO or pyaudio is None:
        raise _missing("pyaudio", "pip install pyaudio (requires portaudio: brew install portaudio)")

    fmt = pyaudio.paInt16
    chunk = 1024
    pa = pyaudio.PyAudio()
    try:
        stream = pa.open(
            format=fmt,
            channels=channels,
            rate=sample_rate,
            input=True,
            frames_per_buffer=chunk,
        )
        frames: list[bytes] = []
        for _ in range(int(sample_rate / chunk * duration)):
            frames.append(stream.read(chunk, exception_on_overflow=False))
        stream.stop_stream()
        stream.close()
    finally:
        pa.terminate()

    return b"".join(frames)


def _is_digital_silence(pcm: bytes, *, threshold: int = 4) -> bool:
    """True when the recording is all-zero — the signature of a blocked mic.

    macOS hands a process with no microphone grant a stream of zero samples
    rather than an error, so an empty transcript alone cannot tell a denied
    permission apart from a quiet room. A working mic in a silent room still
    picks up preamp noise, so peak amplitude separates the two cleanly.
    """
    if not pcm:
        return True
    peak = max(
        abs(int.from_bytes(pcm[i : i + 2], "little", signed=True))
        for i in range(0, len(pcm) - 1, 2)
    )
    return peak <= threshold


def save_wav(
    pcm: bytes,
    path: Path,
    sample_rate: int = DEFAULT_SAMPLE_RATE,
    channels: int = DEFAULT_CHANNELS,
) -> Path:
    """Save raw 16-bit PCM audio as a WAV file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm)
    return path


def load_wav(path: Path) -> bytes:
    """Load WAV file raw audio."""
    with wave.open(str(path), "rb") as wf:
        return wf.readframes(wf.getnframes())


@lru_cache(maxsize=3)
def _whisper_model(model_size: str, device: str, compute_type: str):
    if not HAS_WHISPER or WhisperModel is None:
        raise _missing("faster-whisper", "pip install faster-whisper (first run downloads the model)")
    return WhisperModel(model_size, device=device, compute_type=compute_type)


def transcribe(
    audio_path: Path,
    model_size: str = "base",
    device: str = "cpu",
    compute_type: str = "int8",
) -> str:
    """Transcribe a WAV file using local faster-whisper."""
    if not HAS_WHISPER or WhisperModel is None:
        raise _missing(
            "faster-whisper",
            "pip install faster-whisper (first run downloads the model)",
        )

    model = _whisper_model(model_size, device, compute_type)
    segments, _ = model.transcribe(str(audio_path), language="en", condition_on_previous_text=False)
    return " ".join(s.text.strip() for s in segments).strip()


def speak(
    text: str,
    api_key: str,
    voice_id: str | None = None,
    model_id: str = "eleven_multilingual_v2",
) -> bytes:
    """Generate speech from text using the ElevenLabs API."""
    voice_id = voice_id or "21m00Tcm4TlvDq8ikWAM"  # default ElevenLabs voice
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
    headers = {
        "Accept": "audio/mpeg",
        "Content-Type": "application/json",
        "xi-api-key": api_key,
    }
    payload = {
        "text": text,
        "model_id": model_id,
        "voice_settings": {"stability": 0.5, "similarity_boost": 0.75},
    }
    with httpx.Client(timeout=60.0) as c:
        r = c.post(url, json=payload, headers=headers)
        r.raise_for_status()
        return r.content


def listen(
    client: AtlasClient,
    cfg: AliciaCfg,
    *,
    duration: float = 5.0,
    read_only: bool = True,
) -> dict[str, Any]:
    """Record microphone audio, transcribe it, and send it to Alicia.

    If read_only=True (recommended), the transcription is routed through
    alicia_query so it cannot approve or dispatch anything.
    """
    pcm = record_audio(duration=duration)
    with tempfile.TemporaryDirectory() as tmp:
        wav_path = Path(tmp) / "alicia_input.wav"
        save_wav(pcm, wav_path)
        text = transcribe(wav_path)
        if not text:
            # This is NOT a success. A denied microphone produces exactly this
            # shape — all-silence PCM, empty transcript — and reporting ok=true
            # makes a permissions failure indistinguishable from a quiet room.
            # Distinguish them by looking at the audio itself: real silence from
            # a working mic still carries noise, a blocked mic returns zeros.
            silent = _is_digital_silence(pcm)
            return {
                "ok": False,
                "transcription": "",
                "error": (
                    "The microphone returned pure digital silence — the recording device is "
                    "most likely blocked. Grant microphone access to the process running "
                    "Alicia (System Settings, Privacy & Security, Microphone) and restart it."
                    if silent
                    else "No speech detected in the recording."
                ),
                "mic_blocked": silent,
                "reply": "",
                "path": "mic_blocked" if silent else "no_speech",
            }

        memory = MemoryStore()
        reply, _ = resolve_chat_reply(
            client,
            cfg,
            text,
            mode="manager",
            memory=memory,
            read_only=read_only,
        )
        memory.save_conversation(text, reply, title=(text or "Alicia voice")[:80])
        return {
            "ok": True,
            "transcription": text,
            "reply": reply,
            "path": "voice" + ("_read_only" if read_only else "_action"),
        }
