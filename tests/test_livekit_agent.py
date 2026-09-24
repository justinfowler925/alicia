"""LiveKit transport keeps the configured Alicia voice identity."""

import asyncio
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from alicia.livekit_agent import AliciaVoiceAgent, OwnerVoiceGate


def test_livekit_uses_the_same_configured_voice_as_browser_tts():
    source = (Path(__file__).parents[1] / "alicia/livekit_agent.py").read_text()
    assert "from .config import load_config" in source
    assert "load_config().voice.elevenlabs_voice_id.strip()" in source


def test_owner_gate_fails_closed_without_enough_remote_audio():
    gate = OwnerVoiceGate()
    assert asyncio.run(gate.accepts_current_speaker()) is False


def test_owner_gate_consumes_audio_after_each_decision():
    """Audio already judged can never be judged again.

    The buffer itself is no longer emptied — a duplicate transcript needs
    something to look at, and the ring is bounded anyway. The floor is what
    makes the guarantee: it advances past everything a verdict consumed.
    """
    identity = MagicMock()
    identity.verify_pcm.return_value = {"accepted": True, "score": 0.8}
    gate = OwnerVoiceGate(identity)
    gate.start_utterance()
    frame = b"\0\0" * gate.sample_rate  # one second
    for _ in range(2):
        gate._frames.append(frame)
        gate._received += len(frame)

    assert asyncio.run(gate.accepts_current_speaker()) is True
    assert gate._floor == gate._received
    assert gate._utterance_start is None


def test_disconnecting_cancels_the_active_canonical_turn():
    async def scenario() -> None:
        agent = AliciaVoiceAgent("123456abcdef", OwnerVoiceGate())
        active = asyncio.create_task(asyncio.Event().wait())
        agent._active_turn = active
        agent.close()
        assert agent._closed is True
        with pytest.raises(asyncio.CancelledError):
            await active

    asyncio.run(scenario())


def test_livekit_verifies_the_speaker_and_forwards_the_verdict_with_the_turn():
    source = (Path(__file__).parents[1] / "alicia/livekit_agent.py").read_text()
    handler = source[source.index("async def on_user_turn_completed") : source.index("async def llm_node")]
    assert "await self.gate.verify_current_speaker()" in handler
    assert "owner_verified=verdict.accepted" in handler
    assert "raise StopResponse()" in handler


def test_remote_track_callback_never_uses_a_nonexistent_is_local_flag():
    source = (Path(__file__).parents[1] / "alicia/livekit_agent.py").read_text()
    callback = source[
        source.index("def _on_track") : source.index("@session.on", source.index("def _on_track"))
    ]
    assert "participant.is_local" not in callback
    assert "gate.observe(track)" in callback
