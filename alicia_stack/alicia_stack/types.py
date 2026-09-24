from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable


class Verdict(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    UNSURE = "UNSURE"


ClaimProbe = Callable[["Claim"], "ClaimVerdict"]


@dataclass
class Claim:
    kind: str
    text: str
    evidence: dict[str, Any] = field(default_factory=dict)


@dataclass
class ClaimVerdict:
    claim: Claim
    verdict: Verdict
    receipt: str
    detail: str = ""


@dataclass
class ProveReport:
    verdict: Verdict
    claims: list[ClaimVerdict] = field(default_factory=list)
    speak: str = ""

    @property
    def ok(self) -> bool:
        return self.verdict == Verdict.PASS


@dataclass
class HandsResult:
    """Worker handoff. Narration is untrusted — Prove must verify."""

    job_id: str = ""
    summary: str = ""
    claims: list[str] = field(default_factory=list)
    evidence: dict[str, Any] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)
