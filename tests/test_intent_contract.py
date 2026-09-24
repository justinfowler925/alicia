import pytest

from alicia.intent_contract import IntentNotReady, compile_proposal


@pytest.mark.parametrize(
    ("tool", "args", "missing"),
    [
        ("approve_gate", {}, "ticket"),
        ("answer_steering", {"ticket_id": "REV-9"}, "body"),
        ("delete_note", {}, "note_id or id or q"),
        ("ask_atlas6", {}, "message or question"),
        ("organize_agent_thread", {"agent_id": "codex:local:1"}, "organization field"),
        ("organize_project", {"project_id": "github.com/o/r"}, "organization field"),
    ],
)
def test_materially_incomplete_proposals_are_rejected(tool, args, missing):
    with pytest.raises(IntentNotReady, match=missing):
        compile_proposal(tool, args)


def test_exact_gate_target_and_acceptance_are_compiled():
    contract = compile_proposal(
        "approve_gate", {"ticket": "REV-412", "decision": "reject"}
    )

    assert contract.outcome == "reject the waiting decision for REV-412"
    assert contract.target == "Atlas gate REV-412"
    assert contract.scope == "one named gate"
    assert "receipt" in contract.acceptance[0]


def test_title_only_registration_is_bounded_intake_not_fake_execution_scope():
    contract = compile_proposal("register_thread", {"title": "Make the spec usable"})

    assert contract.outcome == "Make the spec usable"
    assert contract.assumptions == (
        "Title is intake only; Atlas must scope it before execution.",
    )


def test_dispatch_contract_distinguishes_preview_from_live():
    preview = compile_proposal("dispatch_tick", {"dry_run": True})
    live = compile_proposal("dispatch_tick", {"dry_run": False})

    assert preview.outcome.startswith("preview")
    assert live.outcome.startswith("run one live")
    assert "denominator" in live.scope


def test_local_organization_contracts_preserve_source_records():
    thread = compile_proposal("organize_agent_thread", {"agent_id": "codex:local:1", "pinned": False})
    project = compile_proposal("organize_project", {"project_id": "github.com/o/r", "objective": "ship it"})

    assert "native Codex" in thread.preserve
    assert any("source_records_changed=false" in item for item in project.acceptance)
