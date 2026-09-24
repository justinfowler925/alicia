"""Sergii prove layer — receipts or it didn't happen."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Any, Callable, Optional

from .types import Claim, ClaimVerdict, HandsResult, ProveReport, Verdict

# Phrases that count as done-claims in worker narration
CLAIM_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("merged", re.compile(r"\b(merged|on main|shipped to main)\b", re.I)),
    ("tested", re.compile(r"\b(tests? passed|ci green|all checks passed)\b", re.I)),
    ("deployed", re.compile(r"\b(deployed|live in|production deploy)\b", re.I)),
    ("flow_live", re.compile(r"\b(flow (is )?live|activated flow|activeversion)\b", re.I)),
    ("atlas_did", re.compile(r"\b(atlas (did|ran|completed)|inbox job)\b", re.I)),
    ("fixed", re.compile(r"\b(fixed|done|complete[d]?|shipped)\b", re.I)),
    ("workspace_clean", re.compile(r"\b(workspace clean|git status clean|dod)\b", re.I)),
]


ProbeFn = Callable[[Claim, dict[str, Any]], ClaimVerdict]


KNOWN_KINDS = {kind for kind, _ in CLAIM_PATTERNS}


def extract_claims(result: HandsResult) -> list[Claim]:
    claims: list[Claim] = []
    seen: set[str] = set()

    for raw in result.claims:
        kind = raw if raw in KNOWN_KINDS else _classify(raw)
        if kind not in seen:
            seen.add(kind)
            claims.append(Claim(kind=kind, text=raw, evidence=dict(result.evidence)))

    blob = result.summary or ""
    for kind, pat in CLAIM_PATTERNS:
        if pat.search(blob) and kind not in seen:
            # Prefer specific kinds over generic "fixed"
            if kind == "fixed" and any(
                k in seen for k in ("merged", "deployed", "tested", "flow_live", "atlas_did")
            ):
                continue
            seen.add(kind)
            claims.append(Claim(kind=kind, text=blob, evidence=dict(result.evidence)))

    return claims


def _classify(text: str) -> str:
    if text in KNOWN_KINDS:
        return text
    for kind, pat in CLAIM_PATTERNS:
        if pat.search(text):
            return kind
    return "fixed"


def prove(
    result: HandsResult,
    *,
    probes: Optional[dict[str, ProbeFn]] = None,
    cwd: Optional[Path] = None,
) -> ProveReport:
    """Fail closed: missing receipt → FAIL. Worker narration is not evidence."""
    claims = extract_claims(result)
    if not claims:
        # No done-claim → nothing to prove; treat as informational PASS
        speak = compress_result_speak(result, None)
        return ProveReport(verdict=Verdict.PASS, claims=[], speak=speak)

    probe_map = {**default_probes(cwd=cwd), **(probes or {})}
    verdicts: list[ClaimVerdict] = []

    for claim in claims:
        fn = probe_map.get(claim.kind, _missing_probe)
        verdicts.append(fn(claim, result.evidence))

    overall = _rollup(verdicts)
    speak = format_prove_speak(overall, verdicts, result)
    return ProveReport(verdict=overall, claims=verdicts, speak=speak)


def _rollup(verdicts: list[ClaimVerdict]) -> Verdict:
    if any(v.verdict == Verdict.FAIL for v in verdicts):
        return Verdict.FAIL
    if any(v.verdict == Verdict.UNSURE for v in verdicts):
        return Verdict.UNSURE
    return Verdict.PASS


def format_prove_speak(
    overall: Verdict,
    verdicts: list[ClaimVerdict],
    result: HandsResult,
) -> str:
    if overall == Verdict.PASS:
        ids = []
        for v in verdicts:
            if v.receipt:
                ids.append(v.receipt)
        if result.job_id:
            ids.append(f"job {result.job_id}")
        evidence = "; ".join(ids[:3]) if ids else "receipts ok"
        return f"Done. {evidence}."
    if overall == Verdict.FAIL:
        missing = [
            f"{v.claim.kind}: {v.detail or 'no receipt'}"
            for v in verdicts
            if v.verdict == Verdict.FAIL
        ]
        return "Not done. " + " ".join(missing)
    # UNSURE
    unsure = next(v for v in verdicts if v.verdict == Verdict.UNSURE)
    return f"Unsure. Run this to settle it: {unsure.detail or unsure.receipt}"


def compress_result_speak(result: HandsResult, report: Optional[ProveReport]) -> str:
    if report:
        return report.speak
    s = (result.summary or "").strip()
    if not s:
        return "Hands finished. No summary."
    # One line max for non-claim results
    return s.split("\n")[0][:240]


def default_probes(*, cwd: Optional[Path] = None) -> dict[str, ProbeFn]:
    root = cwd or Path.cwd()

    def merged(claim: Claim, evidence: dict[str, Any]) -> ClaimVerdict:
        sha = evidence.get("sha") or evidence.get("merge_sha") or claim.evidence.get("sha")
        if not sha:
            return ClaimVerdict(
                claim=claim,
                verdict=Verdict.FAIL,
                receipt="",
                detail="merged claim needs sha that is ancestor of origin/main",
            )
        ok, detail = _sha_on_main(str(sha), root)
        if ok:
            return ClaimVerdict(claim=claim, verdict=Verdict.PASS, receipt=str(sha)[:12], detail=detail)
        return ClaimVerdict(claim=claim, verdict=Verdict.FAIL, receipt="", detail=detail)

    def tested(claim: Claim, evidence: dict[str, Any]) -> ClaimVerdict:
        if evidence.get("test_exit_code") == 0 and evidence.get("test_command"):
            return ClaimVerdict(
                claim=claim,
                verdict=Verdict.PASS,
                receipt=str(evidence["test_command"]),
                detail="exit 0",
            )
        ci = evidence.get("ci_url") or evidence.get("ci_run_url")
        if ci and evidence.get("ci_ran") is True:
            return ClaimVerdict(claim=claim, verdict=Verdict.PASS, receipt=str(ci), detail="ci ran")
        return ClaimVerdict(
            claim=claim,
            verdict=Verdict.FAIL,
            receipt="",
            detail="need test_command+exit 0 or ci_url with ci_ran=true",
        )

    def deployed(claim: Claim, evidence: dict[str, Any]) -> ClaimVerdict:
        env = evidence.get("env") or evidence.get("target_org")
        deploy_id = evidence.get("deploy_id")
        if env and deploy_id:
            return ClaimVerdict(
                claim=claim,
                verdict=Verdict.PASS,
                receipt=f"{env}:{deploy_id}",
                detail="deploy id present",
            )
        if evidence.get("user_visible_probe"):
            return ClaimVerdict(
                claim=claim,
                verdict=Verdict.PASS,
                receipt=str(evidence["user_visible_probe"]),
                detail="user-visible probe",
            )
        return ClaimVerdict(
            claim=claim,
            verdict=Verdict.FAIL,
            receipt="",
            detail="need env+deploy_id or user_visible_probe",
        )

    def flow_live(claim: Claim, evidence: dict[str, Any]) -> ClaimVerdict:
        active = evidence.get("active_version")
        latest = evidence.get("latest_version")
        if active is None or latest is None:
            return ClaimVerdict(
                claim=claim,
                verdict=Verdict.FAIL,
                receipt="",
                detail="need active_version and latest_version from Tooling",
            )
        if active == latest:
            return ClaimVerdict(
                claim=claim,
                verdict=Verdict.PASS,
                receipt=f"v{active}",
                detail="Active==Latest",
            )
        return ClaimVerdict(
            claim=claim,
            verdict=Verdict.FAIL,
            receipt="",
            detail=f"Active={active} Latest={latest}",
        )

    def atlas_did(claim: Claim, evidence: dict[str, Any]) -> ClaimVerdict:
        job = evidence.get("inbox_job_id") or evidence.get("job_id")
        if job:
            return ClaimVerdict(claim=claim, verdict=Verdict.PASS, receipt=str(job), detail="inbox job")
        return ClaimVerdict(
            claim=claim,
            verdict=Verdict.FAIL,
            receipt="",
            detail="need inbox_job_id — Cursor narration does not count",
        )

    def workspace_clean(claim: Claim, evidence: dict[str, Any]) -> ClaimVerdict:
        if evidence.get("git_status_clean") is True:
            return ClaimVerdict(claim=claim, verdict=Verdict.PASS, receipt="clean", detail="git_status_clean")
        ok, detail = _git_clean(root)
        if ok:
            return ClaimVerdict(claim=claim, verdict=Verdict.PASS, receipt="clean", detail=detail)
        return ClaimVerdict(claim=claim, verdict=Verdict.FAIL, receipt="", detail=detail)

    def fixed(claim: Claim, evidence: dict[str, Any]) -> ClaimVerdict:
        # Generic "fixed/done" without a more specific claim still needs *some* receipt
        if evidence.get("sha") or evidence.get("deploy_id") or evidence.get("inbox_job_id"):
            return ClaimVerdict(
                claim=claim,
                verdict=Verdict.UNSURE,
                receipt="",
                detail="generic done-claim — map to merged/deployed/atlas_did with explicit evidence",
            )
        return ClaimVerdict(
            claim=claim,
            verdict=Verdict.FAIL,
            receipt="",
            detail="said done with no sha/deploy_id/inbox_job_id",
        )

    return {
        "merged": merged,
        "tested": tested,
        "deployed": deployed,
        "flow_live": flow_live,
        "atlas_did": atlas_did,
        "workspace_clean": workspace_clean,
        "fixed": fixed,
    }


def _missing_probe(claim: Claim, evidence: dict[str, Any]) -> ClaimVerdict:
    return ClaimVerdict(
        claim=claim,
        verdict=Verdict.FAIL,
        receipt="",
        detail=f"no probe registered for {claim.kind}",
    )


def _sha_on_main(sha: str, root: Path) -> tuple[bool, str]:
    try:
        r = subprocess.run(
            ["git", "merge-base", "--is-ancestor", sha, "origin/main"],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=15,
        )
        if r.returncode == 0:
            return True, "ancestor of origin/main"
        return False, "sha is not ancestor of origin/main"
    except (OSError, subprocess.TimeoutExpired) as e:
        return False, f"git probe failed: {e}"


def _git_clean(root: Path) -> tuple[bool, str]:
    try:
        r = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=15,
        )
        if r.returncode != 0:
            return False, "git status failed"
        if r.stdout.strip():
            return False, "working tree dirty"
        return True, "working tree clean"
    except (OSError, subprocess.TimeoutExpired) as e:
        return False, f"git status failed: {e}"
