"""The owner voice gate must judge the audio that produced the transcript.

Every test here is a window fault that shipped, and each one looked exactly
like a stranger at the microphone: Alicia went silent on the owner's own
speech. The measured cost was a 65% refusal rate across 477 verdicts with a
score mode near 0.0 — noise, not a near miss between two people.
"""

import asyncio
from unittest.mock import MagicMock

from alicia.livekit_agent import OwnerVoiceGate


def _gate(*, accepted=True, score=0.62):
    identity = MagicMock()
    identity.verify_pcm.return_value = {"accepted": accepted, "score": score}
    return OwnerVoiceGate(identity), identity


FRAME_MS = 20


def _speak(gate, seconds):
    """Push `seconds` of audio in real frame sizes, the way observe() would.

    Frame size matters to these tests: eviction drops whole frames, so a single
    giant frame would empty the ring instead of sliding it.
    """
    frame = b"\x01\x00" * int(gate.sample_rate * FRAME_MS / 1000)
    for _ in range(int(seconds * 1000 / FRAME_MS)):
        gate._frames.append(frame)
        gate._received += len(frame)
        while gate._received - gate._evicted > gate._limit:
            gate._evicted += len(gate._frames.popleft())


def test_a_long_utterance_is_judged_on_its_own_audio_not_on_silence():
    """The marker used to index a buffer that evicts from the left.

    Nothing moved it when audio was dropped, so the longest turns verified
    their own tail and then ran off the end of the buffer.
    """
    gate, identity = _gate()
    gate.start_utterance()
    _speak(gate, gate.WINDOW_SECONDS * 2)  # overflow the ring twice over

    verdict = asyncio.run(gate.verify_current_speaker())

    assert verdict.accepted is True
    (pcm, rate), _ = identity.verify_pcm.call_args
    judged = len(pcm) / (rate * 2)
    assert judged > gate.WINDOW_SECONDS - 1, f"only judged {judged:.1f}s of a long turn"


def test_vad_flicker_mid_sentence_does_not_move_the_window_forward():
    """`speaking` fires repeatedly inside one sentence; only the first anchors."""
    gate, identity = _gate()
    gate.start_utterance()
    _speak(gate, 4.0)
    gate.start_utterance()  # flicker
    _speak(gate, 1.0)
    gate.start_utterance()  # flicker again

    asyncio.run(gate.verify_current_speaker())

    (pcm, rate), _ = identity.verify_pcm.call_args
    assert len(pcm) / (rate * 2) > 4.5, "the window skipped the speech it was checking"


def test_a_duplicate_transcript_verifies_the_tail_rather_than_nothing():
    """A late second transcript for one utterance met an emptied buffer."""
    gate, _ = _gate()
    gate.start_utterance()
    _speak(gate, 5.0)
    assert asyncio.run(gate.verify_current_speaker()).accepted is True

    _speak(gate, 2.0)
    second = asyncio.run(gate.verify_current_speaker())

    assert second.accepted is True, second.reason


def test_a_settled_verdict_never_reuses_earlier_audio():
    """The floor is what keeps one speaker's audio out of the next decision."""
    gate, _ = _gate()
    gate.start_utterance()
    _speak(gate, 5.0)
    asyncio.run(gate.verify_current_speaker())

    assert gate._floor == gate._received
    starved = asyncio.run(gate.verify_current_speaker())
    assert starved.accepted is False
    assert "window" in starved.reason


def test_every_verdict_carries_a_reason():
    """A bare False is unreadable in a log and invisible on screen."""
    gate, _ = _gate()
    silent = asyncio.run(gate.verify_current_speaker())
    assert silent.accepted is False
    assert silent.reason

    gate.start_utterance()
    _speak(gate, 3.0)
    scored = asyncio.run(gate.verify_current_speaker())
    assert "threshold" in scored.reason and scored.score is not None


def test_verification_errors_do_not_crash_the_turn():
    gate, identity = _gate()
    identity.verify_pcm.side_effect = RuntimeError("model unavailable")
    gate.start_utterance()
    _speak(gate, 3.0)

    verdict = asyncio.run(gate.verify_current_speaker())

    assert verdict.accepted is False
    assert "model unavailable" in verdict.reason
