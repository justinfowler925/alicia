"""Repeatable, transport-independent evaluation for Alicia's spoken coworker behavior."""

from __future__ import annotations

import re
import time
from dataclasses import asdict, dataclass
from typing import Callable

from .conversation import TurnResult


BANNED_OPENERS = re.compile(
    r"^(certainly|absolutely|of course|great question|happy to help|i'd be happy to|as an ai)\b",
    re.I,
)
MACHINE_FURNITURE = re.compile(r"https?://|(?:^|\s)(?:/|~/)[\w./-]+|\b[0-9a-f]{12,40}\b", re.I)
FAILURE_NARRATION = re.compile(r"\b(?:can't reach a brain|brain unavailable|try again later)\b", re.I)
CREDENTIAL_NOISE = re.compile(
    r"\b(?:api[_ -]?key|credential|secret|token|1password|vault|authenticate|authentication)\b",
    re.I,
)


@dataclass(frozen=True)
class VoiceTurn:
    text: str
    expect_tool: str | None = None
    must_be_silent: bool = False
    require_any: tuple[str, ...] = ()
    forbid: tuple[str, ...] = ()


@dataclass(frozen=True)
class VoiceScenario:
    id: str
    turns: tuple[VoiceTurn, ...]


SCENARIOS = (
    VoiceScenario(
        "human_greeting",
        (VoiceTurn("Morning, Alicia. How are we looking?", expect_tool="get_work_surface"),),
    ),
    VoiceScenario(
        "one_decision",
        (VoiceTurn("What actually needs me first?", expect_tool="get_work_surface"),),
    ),
    VoiceScenario(
        "blunt_status",
        (VoiceTurn(
            "Give me the blunt version: is Alicia ready for me to rely on every day?",
            expect_tool="get_work_surface",
        ),),
    ),
    VoiceScenario(
        "follow_up_memory",
        (
            VoiceTurn("Let's talk about REV-490.", expect_tool="get_thread"),
            VoiceTurn(
                "What's still missing, and don't give me a status dump.",
                require_any=("voice", "conversation", "turn"),
                forbid=("which workstream", "what ticket", "what thread"),
            ),
        ),
    ),
    VoiceScenario(
        "frustration_recovery",
        (VoiceTurn(
            "No, that's not what I asked. Stop and give me the one decision.",
            expect_tool="get_work_surface",
            require_any=("rev-490",),
            forbid=("ask it fresh", "i don't have the context"),
        ),),
    ),
    VoiceScenario(
        "respect_thinking_pause",
        (VoiceTurn("Give me a second to think.", must_be_silent=True),),
    ),
    VoiceScenario(
        "topic_change",
        (
            VoiceTurn("Let's talk about REV-490.", expect_tool="get_thread"),
            VoiceTurn(
                "Actually, different question. What needs me first?",
                expect_tool="get_work_surface",
            ),
        ),
    ),
)


def evaluate_turn(spec: VoiceTurn, result: TurnResult, elapsed_s: float) -> dict[str, object]:
    spoken = (result.spoken or "").strip()
    failures: list[str] = []
    if spec.must_be_silent:
        if spoken:
            failures.append("spoke_when_user_asked_for_silence")
    else:
        if not spoken:
            failures.append("empty_spoken_reply")
        if len(re.findall(r"\b\w+\b", spoken)) > 75:
            failures.append("over_75_spoken_words")
        if len(re.findall(r"[.!?](?:\s|$)", spoken)) > 3:
            failures.append("over_3_spoken_sentences")
        if BANNED_OPENERS.search(spoken):
            failures.append("corporate_filler_opener")
        if MACHINE_FURNITURE.search(spoken):
            failures.append("machine_furniture_spoken")
        if FAILURE_NARRATION.search(spoken):
            failures.append("backend_failure_narrated")
        if CREDENTIAL_NOISE.search(spoken):
            failures.append("credential_noise_spoken")
    if spec.expect_tool and result.tool != spec.expect_tool:
        failures.append(f"expected_tool:{spec.expect_tool}:got:{result.tool or 'none'}")
    folded = f"{result.reply}\n{spoken}".casefold()
    if spec.require_any and not any(needle.casefold() in folded for needle in spec.require_any):
        failures.append("missing_required_meaning:" + "|".join(spec.require_any))
    for needle in spec.forbid:
        if needle.casefold() in folded:
            failures.append("forbidden_unhelpful_phrase:" + needle)
    return {
        "input": spec.text,
        "reply": result.reply,
        "spoken": spoken,
        "tool": result.tool,
        "elapsed_s": round(elapsed_s, 3),
        "pass": not failures,
        "failures": failures,
    }


def run_scenarios(
    open_session: Callable[[], str],
    turn: Callable[[str, str], TurnResult],
    *,
    scenarios: tuple[VoiceScenario, ...] = SCENARIOS,
) -> dict[str, object]:
    rows: list[dict[str, object]] = []
    for scenario in scenarios:
        session_id = open_session()
        turn_rows = []
        for spec in scenario.turns:
            started = time.monotonic()
            result = turn(session_id, spec.text)
            turn_rows.append(evaluate_turn(spec, result, time.monotonic() - started))
        rows.append({"id": scenario.id, "pass": all(r["pass"] for r in turn_rows), "turns": turn_rows})
    total = len(rows)
    passed = sum(bool(row["pass"]) for row in rows)
    latencies = [float(t["elapsed_s"]) for row in rows for t in row["turns"]]
    return {
        "scenario_count": total,
        "passed": passed,
        "failed": total - passed,
        "pass_rate": round(passed / total, 3) if total else 0.0,
        "mean_turn_latency_s": round(sum(latencies) / len(latencies), 3) if latencies else 0.0,
        "scenarios": rows,
        "definitions": [asdict(s) for s in scenarios],
    }
