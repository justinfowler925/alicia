"""Pure Unfog work compiler for Alicia supervision and ticket drafting.

The compiler makes a recommendation; it has no client, network, or persistence
dependency and therefore cannot create or mutate a ticket.  Callers must apply
their own authorization gates before acting on a returned draft.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from typing import Any, Literal

WorkAction = Literal[
    "continue", "update_existing", "draft_new_ticket", "frontier", "needs_input"
]
TicketRelationship = Literal["exact", "related", "unrelated"]


@dataclass(frozen=True)
class UnfogContract:
    """The seven fields that must survive work compilation unchanged."""

    outcome: str
    target: str
    premise: str
    scope: str
    preservation: str
    acceptance: tuple[str, ...]
    delivery: str

    def __post_init__(self) -> None:
        for name in ("outcome", "target", "premise", "scope", "preservation", "delivery"):
            object.__setattr__(self, name, str(getattr(self, name) or "").strip())
        object.__setattr__(
            self,
            "acceptance",
            tuple(str(item).strip() for item in self.acceptance if str(item).strip()),
        )

    @property
    def missing_fields(self) -> tuple[str, ...]:
        missing = [
            name
            for name in ("outcome", "target", "premise", "scope", "preservation", "delivery")
            if not getattr(self, name)
        ]
        if not self.acceptance:
            missing.append("acceptance")
        return tuple(missing)

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class WorkEvidence:
    claim: str
    source: str
    observation: str


@dataclass(frozen=True)
class TicketCandidate:
    ticket_id: str
    title: str
    relationship: TicketRelationship = "unrelated"
    status: str = "open"
    evidence: str = ""

    @property
    def is_open(self) -> bool:
        return self.status.strip().lower() not in {
            "closed", "complete", "completed", "cancelled", "canceled", "done", "resolved"
        }


@dataclass(frozen=True)
class ActiveWork:
    work_id: str
    matches_contract: bool
    status: str = "inflight"
    evidence: str = ""

    @property
    def is_inflight(self) -> bool:
        return self.status.strip().lower() in {
            "active", "in_progress", "in-progress", "inflight", "running", "waiting", "blocked"
        }


@dataclass(frozen=True)
class TicketDraft:
    """An inert draft.  It deliberately contains no save or submit behavior."""

    title: str
    contract: UnfogContract
    evidence: tuple[WorkEvidence, ...]


@dataclass(frozen=True)
class FrontierRequest:
    """Complete, provider-neutral request for a frontier Unfog pass."""

    task: str
    justification: tuple[str, ...]
    contract: UnfogContract
    evidence: tuple[WorkEvidence, ...]
    existing_ticket_candidates: tuple[TicketCandidate, ...]
    required_output: tuple[str, ...] = (
        "resolved Unfog contract with all seven fields",
        "evidence-backed premise finding",
        "recommended action and competing-hypothesis control",
        "remaining material fork, if any",
    )


@dataclass(frozen=True)
class WorkDecision:
    action: WorkAction
    reason: str
    contract: UnfogContract
    evidence: tuple[WorkEvidence, ...]
    ticket_id: str | None = None
    draft: TicketDraft | None = None
    frontier_request: FrontierRequest | None = None
    missing_fields: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.action == "update_existing" and not self.ticket_id:
            raise ValueError("update_existing requires a ticket_id")
        if self.action == "draft_new_ticket" and self.draft is None:
            raise ValueError("draft_new_ticket requires an inert draft")
        if self.action == "frontier" and self.frontier_request is None:
            raise ValueError("frontier requires a complete frontier request")


def _coerce_evidence(value: WorkEvidence | Mapping[str, Any]) -> WorkEvidence:
    if isinstance(value, WorkEvidence):
        return value
    return WorkEvidence(
        claim=str(value.get("claim") or "").strip(),
        source=str(value.get("source") or "").strip(),
        observation=str(value.get("observation") or value.get("detail") or "").strip(),
    )


def _coerce_ticket(value: TicketCandidate | Mapping[str, Any]) -> TicketCandidate:
    if isinstance(value, TicketCandidate):
        return value
    return TicketCandidate(
        ticket_id=str(value.get("ticket_id") or value.get("id") or "").strip(),
        title=str(value.get("title") or "").strip(),
        relationship=str(value.get("relationship") or "unrelated").strip().lower(),  # type: ignore[arg-type]
        status=str(value.get("status") or "open").strip(),
        evidence=str(value.get("evidence") or "").strip(),
    )


def _coerce_active(value: ActiveWork | Mapping[str, Any] | None) -> ActiveWork | None:
    if value is None or isinstance(value, ActiveWork):
        return value
    return ActiveWork(
        work_id=str(value.get("work_id") or value.get("id") or "").strip(),
        matches_contract=bool(value.get("matches_contract", False)),
        status=str(value.get("status") or "inflight").strip(),
        evidence=str(value.get("evidence") or "").strip(),
    )


def compile_work(
    contract: UnfogContract,
    *,
    evidence: Sequence[WorkEvidence | Mapping[str, Any]] = (),
    existing_tickets: Sequence[TicketCandidate | Mapping[str, Any]] = (),
    active_work: ActiveWork | Mapping[str, Any] | None = None,
    material_ambiguities: Sequence[str] = (),
    material_risks: Sequence[str] = (),
    conflicting_evidence: Sequence[str] = (),
    material_fork: str = "",
    draft_title: str = "",
) -> WorkDecision:
    """Compile evidence and a contract into one non-mutating work decision.

    ``material_fork`` is reserved for choices only the user can authorize (for
    example, a different user-visible target or data policy), so it wins over
    frontier escalation.  Frontier is otherwise used only when ambiguity,
    risk, or evidence conflict is explicitly material.
    """

    observations = tuple(_coerce_evidence(item) for item in evidence)
    tickets = tuple(_coerce_ticket(item) for item in existing_tickets)
    current = _coerce_active(active_work)
    missing = contract.missing_fields
    fork = material_fork.strip()
    ambiguities = tuple(str(item).strip() for item in material_ambiguities if str(item).strip())
    risks = tuple(str(item).strip() for item in material_risks if str(item).strip())
    conflicts = tuple(str(item).strip() for item in conflicting_evidence if str(item).strip())

    if fork:
        return WorkDecision(
            action="needs_input",
            reason=f"A material user decision is required: {fork}",
            contract=contract,
            evidence=observations,
            missing_fields=missing,
        )

    frontier_reasons = tuple(
        [*(f"material ambiguity: {item}" for item in ambiguities)]
        + [*(f"material risk: {item}" for item in risks)]
        + [*(f"conflicting evidence: {item}" for item in conflicts)]
    )
    if frontier_reasons:
        request = FrontierRequest(
            task=(
                "Apply Unfog to resolve the material uncertainty without expanding authorization. "
                "Preserve the supplied contract fields, distinguish evidence from inference, and "
                "return the required structured output."
            ),
            justification=frontier_reasons,
            contract=contract,
            evidence=observations,
            existing_ticket_candidates=tickets,
        )
        return WorkDecision(
            action="frontier",
            reason="; ".join(frontier_reasons),
            contract=contract,
            evidence=observations,
            frontier_request=request,
            missing_fields=missing,
        )

    if missing:
        return WorkDecision(
            action="needs_input",
            reason=f"Unfog contract is missing: {', '.join(missing)}",
            contract=contract,
            evidence=observations,
            missing_fields=missing,
        )

    if current is not None and current.matches_contract and current.is_inflight:
        return WorkDecision(
            action="continue",
            reason=f"Matching work {current.work_id} is already {current.status}; avoid duplicate intake.",
            contract=contract,
            evidence=observations,
        )

    exact_open = tuple(
        ticket for ticket in tickets if ticket.relationship == "exact" and ticket.is_open
    )
    if exact_open:
        chosen = exact_open[0]
        return WorkDecision(
            action="update_existing",
            reason=f"Existing open ticket {chosen.ticket_id} is explicitly evidenced as an exact match.",
            contract=contract,
            evidence=observations,
            ticket_id=chosen.ticket_id,
        )

    title = draft_title.strip() or contract.outcome
    draft = TicketDraft(title=title, contract=contract, evidence=observations)
    return WorkDecision(
        action="draft_new_ticket",
        reason="No inflight work or explicitly exact open ticket matches this complete contract.",
        contract=contract,
        evidence=observations,
        draft=draft,
    )
