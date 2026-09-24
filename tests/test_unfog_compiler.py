from alicia.unfog_compiler import (
    ActiveWork,
    TicketCandidate,
    UnfogContract,
    WorkEvidence,
    compile_work,
)


def complete_contract(**changes):
    values = {
        "outcome": "Voice reports the one intervention that advances inflight work",
        "target": "Alicia voice supervisor",
        "premise": "Session lifecycle evidence is available",
        "scope": "All 3 configured agent providers; 3 checked, 3 affected",
        "preservation": "Native sessions, existing tickets, and unrelated work",
        "acceptance": (
            "Each provider fixture yields one evidence-backed next action",
            "An unrelated-ticket fixture does not update that ticket",
        ),
        "delivery": "edit, focused tests, integration review, deploy, voice verification",
    }
    values.update(changes)
    return UnfogContract(**values)


def test_existing_ticket_negative_control_drafts_instead_of_updating_unrelated_ticket():
    decision = compile_work(
        complete_contract(),
        existing_tickets=(
            TicketCandidate("REV-9", "Improve mobile colors", "unrelated", evidence="different target"),
        ),
    )

    assert decision.action == "draft_new_ticket"
    assert decision.ticket_id is None
    assert decision.draft is not None
    assert decision.draft.contract.preservation == complete_contract().preservation


def test_exact_open_ticket_is_recommended_for_update_but_never_mutated():
    ticket = TicketCandidate("REV-42", "Build voice supervisor", "exact", evidence="same target and scope")
    decision = compile_work(complete_contract(), existing_tickets=(ticket,))

    assert decision.action == "update_existing"
    assert decision.ticket_id == "REV-42"
    assert not hasattr(decision, "save")
    assert not hasattr(ticket, "update")


def test_under_specified_material_fork_needs_user_input():
    contract = complete_contract(target="", delivery="")
    decision = compile_work(
        contract,
        material_fork="Choose whether production voice or the local prototype is authoritative",
        material_ambiguities=("two possible runtimes",),
    )

    assert decision.action == "needs_input"
    assert decision.missing_fields == ("target", "delivery")
    assert "production voice" in decision.reason
    assert decision.frontier_request is None


def test_frontier_requires_material_justification_and_emits_complete_request():
    evidence = (
        WorkEvidence("session is blocked", "Codex lifecycle hook", "awaiting approval"),
        WorkEvidence("session is running", "process poll", "process exists"),
    )
    decision = compile_work(
        complete_contract(),
        evidence=evidence,
        existing_tickets=(TicketCandidate("REV-42", "Supervisor", "related"),),
        conflicting_evidence=("lifecycle hook says blocked while process poll says running",),
    )

    assert decision.action == "frontier"
    request = decision.frontier_request
    assert request is not None
    assert request.contract.to_dict() == complete_contract().to_dict()
    assert request.evidence == evidence
    assert request.existing_ticket_candidates[0].ticket_id == "REV-42"
    assert "conflicting evidence" in request.justification[0]
    assert set(request.required_output) == {
        "resolved Unfog contract with all seven fields",
        "evidence-backed premise finding",
        "recommended action and competing-hypothesis control",
        "remaining material fork, if any",
    }


def test_frontier_is_not_used_for_an_incomplete_contract_without_material_uncertainty():
    decision = compile_work(complete_contract(acceptance=()))

    assert decision.action == "needs_input"
    assert decision.missing_fields == ("acceptance",)
    assert decision.frontier_request is None


def test_matching_inflight_work_is_continued_before_ticket_intake():
    decision = compile_work(
        complete_contract(),
        active_work=ActiveWork("codex:123", matches_contract=True, status="blocked"),
        existing_tickets=(TicketCandidate("REV-42", "Supervisor", "exact"),),
    )

    assert decision.action == "continue"
    assert "codex:123" in decision.reason


def test_all_unfog_fields_and_evidence_are_preserved_in_draft():
    contract = complete_contract()
    evidence = ({"claim": "three providers exist", "source": "configuration", "detail": "3/3 enabled"},)
    decision = compile_work(contract, evidence=evidence, draft_title="Voice supervisor")

    assert decision.action == "draft_new_ticket"
    assert decision.draft is not None
    assert decision.draft.contract.to_dict() == contract.to_dict()
    assert decision.draft.evidence[0].observation == "3/3 enabled"
