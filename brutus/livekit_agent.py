"""LiveKit voice worker that keeps Brutus's canonical conversation manager as the brain."""

from __future__ import annotations

import asyncio
import logging
import os
import re
from collections import deque
from typing import NamedTuple

import httpx
from livekit import agents, rtc
from livekit.agents import Agent, AgentSession, StopResponse, llm
from livekit.plugins import elevenlabs, silero
from livekit.plugins.elevenlabs import VoiceSettings

from .config import load_config
from .voice_identity import MATCH_THRESHOLD, MIN_VERIFY_SECONDS, VoiceIdentity

log = logging.getLogger("brutus.livekit")
BRUTUS_URL = os.environ.get("BRUTUS_URL", "http://127.0.0.1:8768")


class VoiceVerdict(NamedTuple):
    """A verification result that always carries the reason it came out that way."""

    accepted: bool
    reason: str
    score: float | None = None


class OwnerVoiceGate:
    """Verify the audio that actually produced the current transcript.

    The window is the part that is easy to get wrong, and getting it wrong is
    indistinguishable from a stranger at the microphone. Three separate window
    faults were live in the same eight-second ring buffer:

      The marker drifted. `_utterance_start` indexed the joined buffer, but the
      buffer evicts from the left as audio arrives, and eviction never moved
      the marker. Any sentence long enough to fill the ring therefore verified
      its own tail, then a slice of silence past the end. The owner's longest
      turns failed hardest, which is the opposite of what a speaker check
      should do.

      VAD flicker moved the marker. Every `speaking` transition re-anchored it,
      including the ones that fire in the middle of a sentence, so a verdict
      often covered the half second of pre-roll *after* the speech it was
      supposed to check.

      A second transcript for one utterance verified an already-emptied
      buffer, which can only ever reject.

    The evidence: across 477 logged verdicts the enrolled owner scored a median
    0.369 against a 0.45 threshold and was refused 65% of the time, with a mode
    near 0.0. Cosine similarity near zero is not a near miss between two
    humans; it is noise, the signature of scoring the wrong audio rather than
    the wrong speaker.

    Every offset below is an absolute position in the received stream, so
    eviction cannot move it, and the floor advances past each settled verdict
    so no decision can inherit an earlier speaker's audio.
    """

    # Eight seconds truncated ordinary speech. Thirty covers a spoken paragraph
    # without the ring ever sliding out from under an anchored marker.
    WINDOW_SECONDS = 30
    PRE_ROLL_SECONDS = 0.5
    # What to verify when a late duplicate transcript arrives with no marker.
    FALLBACK_SECONDS = 3.0

    def __init__(self, identity: VoiceIdentity | None = None) -> None:
        self.identity = identity or VoiceIdentity()
        self._frames: deque[bytes] = deque()
        self.sample_rate = 16000
        self._limit = int(self.sample_rate * 2 * self.WINDOW_SECONDS)
        self._received = 0  # absolute bytes ever appended
        self._evicted = 0  # absolute bytes dropped off the left
        self._utterance_start: int | None = None
        self._floor = 0  # audio at or before here has already been judged

    @property
    def buffered_bytes(self) -> int:
        return self._received - self._evicted

    def start_utterance(self) -> None:
        """Anchor the window at speech onset, keeping a little acoustic pre-roll.

        Only the first onset after a verdict anchors. Mid-sentence VAD flicker
        used to re-anchor and hand the verifier the end of the sentence it was
        meant to check.
        """
        if self._utterance_start is not None:
            return
        pre_roll = int(self.sample_rate * 2 * self.PRE_ROLL_SECONDS)
        self._utterance_start = max(self._floor, self._received - pre_roll)

    async def observe(self, track: rtc.AudioTrack) -> None:
        stream = rtc.AudioStream(track, sample_rate=self.sample_rate, num_channels=1)
        try:
            async for event in stream:
                data = bytes(event.frame.data)
                self._frames.append(data)
                self._received += len(data)
                while self._received - self._evicted > self._limit:
                    self._evicted += len(self._frames.popleft())
        finally:
            await stream.aclose()

    async def verify_current_speaker(self) -> VoiceVerdict:
        """Score the anchored utterance. Never returns a verdict without a reason."""
        start = self._utterance_start
        if start is None:
            # A duplicate transcript for one utterance. Judge the tail rather
            # than an emptied buffer, which could only ever reject.
            start = self._received - int(self.sample_rate * 2 * self.FALLBACK_SECONDS)
        start = max(start, self._floor, self._evicted)
        end = self._received
        self._utterance_start = None
        self._floor = end

        buffer = b"".join(self._frames)
        pcm = buffer[max(start - self._evicted, 0) : max(end - self._evicted, 0)]
        seconds = len(pcm) / (self.sample_rate * 2)
        if seconds < MIN_VERIFY_SECONDS:
            return VoiceVerdict(
                False, f"only {seconds:.2f}s of owner audio in the verification window"
            )
        try:
            verdict = await asyncio.to_thread(self.identity.verify_pcm, pcm, self.sample_rate)
        except Exception as exc:  # noqa: BLE001 — an unverifiable turn is not a crash
            log.warning("owner voice verification failed: %s", exc)
            return VoiceVerdict(False, f"verification error: {exc}")
        score = float(verdict.get("score") or 0.0)
        return VoiceVerdict(
            bool(verdict.get("accepted")),
            f"score {score:.3f} against threshold {MATCH_THRESHOLD} over {seconds:.2f}s",
            score,
        )

    async def accepts_current_speaker(self) -> bool:
        return (await self.verify_current_speaker()).accepted


def session_id_from_room(room_name: str) -> str:
    match = re.fullmatch(r"brutus-([0-9a-f]{12})-[0-9a-f]{8}", room_name)
    if not match:
        raise ValueError(f"invalid Brutus voice room: {room_name}")
    return match.group(1)


class BrutusVoiceAgent(Agent):
    def __init__(self, session_id: str, gate: OwnerVoiceGate) -> None:
        super().__init__(instructions="Brutus voice transport; the canonical manager supplies every reply.")
        self.session_id = session_id
        self.gate = gate
        self._active_turn: asyncio.Task | None = None
        self._closed = False

    def close(self) -> None:
        """Cancel canonical work when the participant or room disconnects."""
        self._closed = True
        if self._active_turn and not self._active_turn.done():
            self._active_turn.cancel()

    async def _reply(self, message: str, *, owner_verified: bool) -> str:
        async with httpx.AsyncClient(timeout=150.0) as client:
            response = await client.post(
                f"{BRUTUS_URL}/api/session/{self.session_id}/say",
                json={
                    "message": message,
                    "channel": "voice",
                    "read_only": False,
                    "wait": True,
                    "owner_verified": owner_verified,
                },
            )
            response.raise_for_status()
            payload = response.json()
        return str(payload.get("reply") or "I couldn't finish that turn. Please try again.")

    async def on_user_turn_completed(self, turn_ctx, new_message) -> None:
        """Hand the finalized transcript to Brutus and schedule its reply directly.

        An unrecognized voice no longer silences the conversation. Dropping the
        turn was the wrong control and the wrong shape of failure: the brain's
        tool surface is reads and the notepad, every gated write already needs
        an artifact approved on screen, and the only thing a stranger's voice
        can actually spend is the spoken "yes" that settles one. So the turn is
        always answered, the verdict rides along with it, and the server
        refuses to settle a pending artifact for a voice it cannot place.

        Silence, meanwhile, is unreadable. When two thirds of the owner's own
        turns vanished with no reply and no reason, the product looked dead
        rather than cautious.
        """
        current = asyncio.current_task()
        previous = self._active_turn
        self._active_turn = current
        if previous and previous is not current and not previous.done():
            previous.cancel()
        message = (new_message.text_content or "").strip()
        try:
            if self._closed:
                raise StopResponse()
            if message:
                verdict = await self.gate.verify_current_speaker()
                log.info(
                    "owner voice session=%s accepted=%s chars=%s (%s)",
                    self.session_id,
                    verdict.accepted,
                    len(message),
                    verdict.reason,
                )
                reply = await self._reply(message, owner_verified=verdict.accepted)
                if not self._closed and self._active_turn is current:
                    self.session.say(reply, allow_interruptions=True, add_to_chat_ctx=True)
            raise StopResponse()
        finally:
            if self._active_turn is current:
                self._active_turn = None

    async def llm_node(self, chat_ctx, tools, model_settings):
        """Fallback for explicit session.generate_reply calls."""
        user_messages = [m for m in chat_ctx.messages() if m.role == "user" and m.text_content]
        if not user_messages:
            return ""
        return await self._reply(
            user_messages[-1].text_content.strip(), owner_verified=False
        )


class CanonicalBrainMarker(llm.LLM):
    """Makes LiveKit schedule LLM turns; BrutusVoiceAgent.llm_node owns generation."""

    @property
    def model(self) -> str:
        return "brutus-canonical-manager"

    @property
    def provider(self) -> str:
        return "brutus"

    def chat(self, **kwargs):
        raise RuntimeError("canonical replies must pass through BrutusVoiceAgent.llm_node")


async def entrypoint(ctx: agents.JobContext) -> None:
    await ctx.connect()
    session_id = session_id_from_room(ctx.room.name)
    api_key = os.environ["ELEVENLABS_API_KEY"]
    # The fallback browser player and LiveKit must use the same configured
    # identity. An environment-only LiveKit value made one conversation start
    # in one voice and continue in another after transport handoff.
    configured_voice_id = load_config().voice.elevenlabs_voice_id.strip()
    voice_id = configured_voice_id or os.environ.get("ELEVENLABS_VOICE_ID") or "hpp4J3VqNfWAUOO0d1Us"
    gate = OwnerVoiceGate()
    agent = BrutusVoiceAgent(session_id, gate)
    session = AgentSession(
        stt=elevenlabs.STT(
            api_key=api_key,
            # Batch-on-local-VAD is deliberate. ElevenLabs realtime Scribe
            # produced interim text but failed to commit the final turn in the
            # production-shaped LiveKit eval; exact transcripts matter more
            # here than shaving the STT call below one second.
            model="scribe_v2",
            language_code="en",
            tag_audio_events=False,
        ),
        tts=elevenlabs.TTS(
            api_key=api_key,
            model="eleven_flash_v2_5",
            voice_id=voice_id,
            voice_settings=VoiceSettings(stability=0.5, similarity_boost=0.8, speed=1.0),
            streaming_latency=2,
        ),
        llm=CanonicalBrainMarker(),
        vad=silero.VAD.load(),
        # Browser WebRTC already supplies echo cancellation. LiveKit's default
        # three-second warmup replaces mic audio with silence at the STT input,
        # which makes early barge-in look detected by VAD but lose every word.
        aec_warmup_duration=None,
        turn_handling={
            "endpointing": {"min_delay": 0.45, "max_delay": 2.5},
            "interruption": {
                "enabled": True,
                # Batch Scribe has no interim words. The adaptive overlap
                # classifier can therefore suppress the finalized batch as a
                # backchannel. VAD mode stops speech after 250 ms and retains
                # the full batch for the next canonical turn.
                "mode": "vad",
                "min_duration": 0.25,
                "min_words": 0,
                "false_interruption_timeout": 1.2,
                "resume_false_interruption": True,
                "backchannel_boundary": None,
            },
            "preemptive_generation": {"enabled": False},
        },
    )

    @ctx.room.on("track_subscribed")
    def _on_track(track, _publication, _participant) -> None:
        # `track_subscribed` only reports remote tracks. RemoteParticipant has
        # no `is_local` property; checking it threw before audio reached the
        # gate and made every owner turn fail closed.
        if track.kind != rtc.TrackKind.KIND_AUDIO:
            return
        asyncio.create_task(gate.observe(track))

    @session.on("user_state_changed")
    def _on_user_state(event) -> None:
        log.info("voice user state session=%s state=%s", session_id, event.new_state)
        if str(event.new_state).casefold().endswith("speaking"):
            gate.start_utterance()

    @session.on("user_input_transcribed")
    def _on_transcript(event) -> None:
        log.info(
            "voice transcript session=%s final=%s chars=%s",
            session_id,
            event.is_final,
            len(event.transcript or ""),
        )

    @ctx.room.on("disconnected")
    def _on_disconnected(*_args) -> None:
        agent.close()

    await session.start(agent=agent, room=ctx.room)
    log.info("voice room connected room=%s session=%s", ctx.room.name, session_id)


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    agents.cli.run_app(
        agents.WorkerOptions(
            entrypoint_fnc=entrypoint,
            host=os.environ.get("BRUTUS_VOICE_HEALTH_HOST", "127.0.0.1"),
            port=int(os.environ.get("BRUTUS_VOICE_HEALTH_PORT", "8096")),
        )
    )


if __name__ == "__main__":
    main()
