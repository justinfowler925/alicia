#!/usr/bin/env python3
"""Drive the production LiveKit entry point with a prerecorded WAV; never touches the mic."""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import time
import wave
from pathlib import Path

import httpx
from livekit import rtc


def _turn_text(turn: dict) -> str:
    """Read current session turns and older exported transcript shapes."""
    return str(turn.get("text") or turn.get("content") or "")


def _normalized(text: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", text.casefold()))


def grade_result(
    result: dict,
    *,
    expected_transcript: str = "",
    expected_barge_transcript: str = "",
    reply_requires: tuple[str, ...] = (),
    barge_reply_requires: tuple[str, ...] = (),
    max_latency_s: float | None = None,
) -> dict:
    """Grade user-observable behavior; every requested assertion must bite."""
    checks: list[dict[str, object]] = []

    def check(name: str, passed: bool, actual: object) -> None:
        checks.append({"name": name, "pass": bool(passed), "actual": actual})

    check("transport_completed", bool(result.get("ok")), result.get("ok"))
    if expected_transcript:
        actual = _normalized(str(result.get("transcript") or ""))
        check("first_transcript_exact", actual == _normalized(expected_transcript), actual)
    if expected_barge_transcript:
        actual = _normalized(str(result.get("barge_in_transcript") or ""))
        check("barge_transcript_exact", actual == _normalized(expected_barge_transcript), actual)
    for needle in reply_requires:
        actual = str(result.get("reply") or "")
        check(f"first_reply_contains:{needle}", needle.casefold() in actual.casefold(), actual)
    for needle in barge_reply_requires:
        actual = str(result.get("barge_in_reply") or "")
        check(f"barge_reply_contains:{needle}", needle.casefold() in actual.casefold(), actual)
    if max_latency_s is not None:
        actual = result.get("speech_to_first_audio_s")
        check("speech_to_first_audio_within_budget", actual is not None and float(actual) <= max_latency_s, actual)
    graded = dict(result)
    graded["checks"] = checks
    graded["check_count"] = len(checks)
    graded["passed_checks"] = sum(bool(row["pass"]) for row in checks)
    graded["ok"] = bool(checks) and all(bool(row["pass"]) for row in checks)
    return graded


async def run(
    base_url: str,
    wav_path: Path,
    timeout: float,
    media_only: bool = False,
    barge_in_wav: Path | None = None,
) -> dict:
    async with httpx.AsyncClient(timeout=20.0) as http:
        opened = (await http.post(f"{base_url}/api/session/open", json={"title": "REV-490 audio eval"})).json()
        sid = opened["session_id"]
        token = (await http.post(f"{base_url}/api/session/{sid}/voice-token")).json()
        if not token.get("enabled"):
            raise RuntimeError("LiveKit transport is disabled")

    room = rtc.Room()
    audio_frames = 0
    first_audio_at: float | None = None
    audio_done = asyncio.Event()

    @room.on("track_subscribed")
    def on_track(track, publication, participant):
        if track.kind != rtc.TrackKind.KIND_AUDIO:
            return

        async def consume() -> None:
            nonlocal audio_frames, first_audio_at
            async for event in rtc.AudioStream(track):
                samples = event.frame.data
                if samples and max(abs(int(sample)) for sample in samples) > 20:
                    if first_audio_at is None:
                        first_audio_at = time.monotonic()
                    audio_frames += 1
                if audio_frames >= 5:
                    audio_done.set()

        asyncio.create_task(consume())

    await room.connect(token["url"], token["token"])
    deadline = time.monotonic() + 15
    while not room.remote_participants and time.monotonic() < deadline:
        await asyncio.sleep(0.05)
    if not room.remote_participants:
        raise RuntimeError("LiveKit worker did not join the room")
    # Participant presence precedes the agent pipeline's subscription by a
    # fraction of a second. A real browser naturally spends this time granting
    # the mic; the file publisher must model it explicitly or amputate word one.
    await asyncio.sleep(1.5)
    with wave.open(str(wav_path), "rb") as probe:
        sample_rate = probe.getframerate()
        channels = probe.getnchannels()
        sample_width = probe.getsampwidth()
    if channels != 1 or sample_width != 2 or sample_rate not in (16000, 48000):
        raise ValueError("eval WAV must be 16/48kHz, mono, 16-bit PCM")
    source = rtc.AudioSource(sample_rate, 1)
    track = rtc.LocalAudioTrack.create_audio_track("eval-microphone", source)
    options = rtc.TrackPublishOptions()
    options.source = rtc.TrackSource.SOURCE_MICROPHONE
    await room.local_participant.publish_track(track, options)

    async def publish(path: Path) -> float:
        with wave.open(str(path), "rb") as wav:
            if (wav.getframerate(), wav.getnchannels(), wav.getsampwidth()) != (sample_rate, 1, 2):
                raise ValueError("all eval WAVs must share sample rate and be mono 16-bit PCM")
            began = time.monotonic()
            while chunk := wav.readframes(sample_rate // 50):
                frame = rtc.AudioFrame(chunk, sample_rate, 1, len(chunk) // 2)
                await source.capture_frame(frame)
                await asyncio.sleep(frame.duration)
        silence = b"\x00\x00" * (sample_rate // 50)
        for _ in range(125):
            await source.capture_frame(rtc.AudioFrame(silence, sample_rate, 1, sample_rate // 50))
            await asyncio.sleep(0.02)
        return began

    started = await publish(wav_path)
    frames_before_barge: int | None = None
    if barge_in_wav:
        await asyncio.wait_for(audio_done.wait(), timeout=timeout)
        frames_before_barge = audio_frames
        await publish(barge_in_wav)

    snapshot = {}
    deadline = time.monotonic() + timeout
    try:
        async with httpx.AsyncClient(timeout=20.0) as http:
            while time.monotonic() < deadline:
                snapshot = (await http.get(f"{base_url}/api/session/{sid}")).json()
                turns = snapshot.get("turns") or []
                expected = 2 if barge_in_wav else 1
                assistants = [t for t in turns if t.get("role") in {"alicia", "assistant"}]
                users = [t for t in turns if t.get("role") == "user"]
                enough_audio = audio_frames >= ((frames_before_barge or 0) + 5)
                if len(users) >= expected and (media_only or len(assistants) >= expected) and enough_audio:
                    break
                await asyncio.sleep(0.25)
    finally:
        await room.disconnect()

    turns = snapshot.get("turns") or []
    user_turns = [t for t in turns if t.get("role") == "user"]
    assistant_turns = [t for t in turns if t.get("role") in {"alicia", "assistant"}]
    expected = 2 if barge_in_wav else 1
    return {
        "ok": bool(
            audio_frames
            and len(user_turns) >= expected
            and (media_only or len(assistant_turns) >= expected)
            and (not barge_in_wav or audio_frames >= ((frames_before_barge or 0) + 5))
        ),
        "session_id": sid,
        "room": token["room"],
        "transcript": _turn_text(user_turns[0]) if user_turns else "",
        "reply": _turn_text(assistant_turns[0]) if assistant_turns else "",
        "turn_count": len(turns),
        "barge_in_transcript": _turn_text(user_turns[-1]) if barge_in_wav and len(user_turns) > 1 else "",
        "barge_in_reply": _turn_text(assistant_turns[-1]) if barge_in_wav and len(assistant_turns) > 1 else "",
        "frames_before_barge": frames_before_barge,
        "response_audio_frames": audio_frames,
        "speech_to_first_audio_s": round((first_audio_at - started), 3) if first_audio_at else None,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("wav", type=Path)
    parser.add_argument("--base-url", default="http://127.0.0.1:8768")
    parser.add_argument("--timeout", type=float, default=180.0)
    parser.add_argument("--media-only", action="store_true")
    parser.add_argument("--barge-in-wav", type=Path)
    parser.add_argument("--expect-transcript", default="")
    parser.add_argument("--expect-barge-transcript", default="")
    parser.add_argument("--reply-requires", action="append", default=[])
    parser.add_argument("--barge-reply-requires", action="append", default=[])
    parser.add_argument("--max-latency-s", type=float)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = asyncio.run(
        run(args.base_url.rstrip("/"), args.wav, args.timeout, args.media_only, args.barge_in_wav)
    )
    graded = grade_result(
        result,
        expected_transcript=args.expect_transcript,
        expected_barge_transcript=args.expect_barge_transcript,
        reply_requires=tuple(args.reply_requires),
        barge_reply_requires=tuple(args.barge_reply_requires),
        max_latency_s=args.max_latency_s,
    )
    rendered = json.dumps(graded, indent=2)
    if args.output:
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    raise SystemExit(0 if graded["ok"] else 1)


if __name__ == "__main__":
    main()
