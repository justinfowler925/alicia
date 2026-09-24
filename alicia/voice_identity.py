"""Local owner speaker enrollment and verification.

The profile contains only a normalized speaker embedding and enrollment metadata;
raw enrollment audio is decoded in a temporary file and deleted.  SpeechBrain's
ECAPA-TDNN model is a speaker-recognition model, not a transcription heuristic.
"""

from __future__ import annotations

import base64
import io
import json
import os
import tempfile
import wave
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np

from .paths import state_path

MODEL_ID = "speechbrain/spkrec-ecapa-voxceleb"
PROFILE_NAME = "voice-owner.json"
MIN_SAMPLES = 3
MIN_SECONDS = 2.0
MIN_VERIFY_SECONDS = 0.75
# ECAPA embeddings from the browser enrollment recorder and LiveKit's Opus
# stream are the same speaker through two different acoustic pipelines. The
# conservative cross-pipeline operating point is lower than same-file scoring;
# 0.45 still rejects unrelated speakers while avoiding a false rejection of the
# enrolled owner at normal microphone distances.
MATCH_THRESHOLD = 0.45


class EnrollmentError(ValueError):
    """The provided enrollment data cannot produce a trusted profile."""


class VoiceIdentity:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or state_path(PROFILE_NAME)
        self._classifier: Any | None = None

    def status(self) -> dict[str, Any]:
        profile = self._load()
        return {
            "enrolled": profile is not None,
            "required_samples": MIN_SAMPLES,
            "model": MODEL_ID if profile else None,
            "enrolled_at": profile.get("enrolled_at") if profile else None,
        }

    def enroll(self, wav_samples: list[bytes]) -> dict[str, Any]:
        if len(wav_samples) < MIN_SAMPLES:
            raise EnrollmentError(f"Record all {MIN_SAMPLES} samples before enrolling.")
        vectors = [self._embedding(data) for data in wav_samples]
        centroid = np.mean(np.stack(vectors), axis=0)
        centroid /= max(float(np.linalg.norm(centroid)), 1e-12)
        profile = {
            "version": 1,
            "model": MODEL_ID,
            "enrolled_at": datetime.now(UTC).isoformat(),
            "sample_count": len(vectors),
            "embedding": base64.b64encode(centroid.astype(np.float32).tobytes()).decode("ascii"),
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(profile, separators=(",", ":")), encoding="utf-8")
        os.chmod(temporary, 0o600)
        temporary.replace(self.path)
        return self.status()

    def verify(self, wav_sample: bytes) -> dict[str, Any]:
        profile = self._load()
        if not profile:
            return {"accepted": False, "reason": "Voice enrollment is required."}
        enrolled = np.frombuffer(base64.b64decode(profile["embedding"]), dtype=np.float32)
        observed = self._embedding(wav_sample, min_seconds=MIN_VERIFY_SECONDS)
        score = float(np.dot(enrolled, observed))
        return {"accepted": score >= MATCH_THRESHOLD, "score": round(score, 4)}

    def verify_pcm(self, pcm: bytes, sample_rate: int) -> dict[str, Any]:
        """Verify int16 mono PCM arriving directly from the LiveKit audio stream."""
        with io.BytesIO() as out:
            with wave.open(out, "wb") as writer:
                writer.setnchannels(1)
                writer.setsampwidth(2)
                writer.setframerate(sample_rate)
                writer.writeframes(pcm)
            return self.verify(out.getvalue())

    def _load(self) -> dict[str, Any] | None:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if data.get("model") != MODEL_ID or not data.get("embedding"):
                return None
            return data
        except (FileNotFoundError, OSError, ValueError, TypeError):
            return None

    def _embedding(self, wav_data: bytes, *, min_seconds: float = MIN_SECONDS) -> np.ndarray:
        waveform, sample_rate = self._decode_wav(wav_data)
        if len(waveform) / sample_rate < min_seconds:
            raise EnrollmentError(f"Each sample must be at least {min_seconds:g} seconds.")
        classifier = self._speaker_model()
        import torch
        import torchaudio

        signal = torch.from_numpy(waveform).unsqueeze(0)
        if sample_rate != 16000:
            signal = torchaudio.functional.resample(signal, sample_rate, 16000)
        embedding = classifier.encode_batch(signal).squeeze().detach().cpu().numpy().astype(np.float32)
        return embedding / max(float(np.linalg.norm(embedding)), 1e-12)

    def _speaker_model(self):
        if self._classifier is None:
            from speechbrain.inference.speaker import EncoderClassifier

            self._classifier = EncoderClassifier.from_hparams(
                source=MODEL_ID, savedir=str(state_path("speaker-model")), run_opts={"device": "cpu"}
            )
        return self._classifier

    @staticmethod
    def _decode_wav(data: bytes) -> tuple[np.ndarray, int]:
        try:
            with tempfile.NamedTemporaryFile(suffix=".wav") as tmp:
                tmp.write(data)
                tmp.flush()
                with wave.open(tmp.name, "rb") as reader:
                    if reader.getsampwidth() != 2 or reader.getnchannels() != 1:
                        raise EnrollmentError("Use the Alicia recorder: it captures mono 16-bit audio.")
                    sample_rate = reader.getframerate()
                    frames = reader.readframes(reader.getnframes())
        except (wave.Error, OSError, EOFError) as exc:
            raise EnrollmentError("The recording was not valid WAV audio. Please record it again.") from exc
        return np.frombuffer(frames, dtype="<i2").astype(np.float32) / 32768.0, sample_rate
