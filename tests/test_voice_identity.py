from pathlib import Path
from unittest.mock import patch

import numpy as np

from brutus.voice_identity import MATCH_THRESHOLD, MIN_VERIFY_SECONDS, VoiceIdentity


def test_owner_profile_stores_embedding_not_raw_recordings(tmp_path: Path):
    identity = VoiceIdentity(tmp_path / "voice-owner.json")
    identity._embedding = lambda _, **_kwargs: np.array([1.0, 0.0], dtype=np.float32)  # type: ignore[method-assign]
    result = identity.enroll([b"one", b"two", b"three"])
    assert result["enrolled"] is True
    saved = (tmp_path / "voice-owner.json").read_text()
    assert "one" not in saved and "two" not in saved and "three" not in saved
    assert identity.verify(b"again")["accepted"] is True


def test_cross_transport_owner_threshold_is_explicit_and_nontrivial():
    assert 0.4 <= MATCH_THRESHOLD < 0.6
    assert 0.5 <= MIN_VERIFY_SECONDS < 2.0


def test_verification_accepts_short_commands_without_weakening_enrollment(tmp_path: Path):
    identity = VoiceIdentity(tmp_path / "voice-owner.json")
    identity._load = lambda: {"embedding": "ignored"}  # type: ignore[method-assign]
    seen: list[float] = []
    identity._embedding = lambda _data, *, min_seconds=2.0: (  # type: ignore[method-assign]
        seen.append(min_seconds) or np.array([1.0], dtype=np.float32)
    )
    with patch(
        "brutus.voice_identity.base64.b64decode", return_value=np.array([1.0], dtype=np.float32).tobytes()
    ):
        assert identity.verify(b"short command")["accepted"] is True
    assert seen == [MIN_VERIFY_SECONDS]


def test_decode_rejects_non_wav_input(tmp_path: Path):
    identity = VoiceIdentity(tmp_path / "voice-owner.json")
    try:
        identity._decode_wav(b"not wav")
    except ValueError as exc:
        assert "valid WAV" in str(exc)
    else:
        raise AssertionError("invalid audio was accepted")
