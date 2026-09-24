from alicia.ticket_intake import compile_ticket_intake


def _history(*turns):
    return [{"role": role, "content": content} for role, content in turns]


def test_complete_labelled_ticket_contract_is_reconstructed_from_user_turns():
    intake = compile_ticket_intake(_history(
        ("user", "new ticket: title: Voice action intake\noutcome: Draft tickets from voice\ntarget: Alicia voice surface\npremise: Text tool calls are unreliable\nscope: Explicit labelled contracts only\npreservation: Existing approval gate\nacceptance: A draft artifact exists; no Linear mutation before yes\ndelivery: test and deploy"),
    ))

    assert intake.ready
    assert intake.args()["title"] == "Voice action intake"
    assert intake.args()["acceptance"] == (
        "A draft artifact exists", "no Linear mutation before yes",
    )


def test_incomplete_ticket_never_invents_missing_contract_fields():
    intake = compile_ticket_intake(_history(("user", "draft a ticket: outcome: Make voice useful")))

    assert intake.requested
    assert intake.missing == ("target", "acceptance")
    assert intake.fields["preservation"] == "existing issues, active sessions, and unrelated work"


def test_natural_voice_intake_collects_one_material_field_at_a_time():
    first = compile_ticket_intake(_history(
        ("user", "draft a ticket to make voice action intake reliable"),
    ))
    assert first.fields["outcome"] == "make voice action intake reliable"
    assert first.missing == ("target", "acceptance")

    second = compile_ticket_intake(_history(
        ("user", "draft a ticket to make voice action intake reliable"),
        ("assistant", "For that ticket draft, what is the target?"),
        ("user", "the Alicia voice work surface"),
    ))
    assert second.fields["target"] == "the Alicia voice work surface"
    assert second.missing == ("acceptance",)

    complete = compile_ticket_intake(_history(
        ("user", "draft a ticket to make voice action intake reliable"),
        ("assistant", "For that ticket draft, what is the target?"),
        ("user", "the Alicia voice work surface"),
        ("assistant", "For that ticket draft, what proves acceptance?"),
        ("user", "a draft is visible and no mutation happens before yes"),
    ))
    assert complete.ready
    assert complete.args()["acceptance"] == ("a draft is visible and no mutation happens before yes",)


def test_unrelated_follow_up_does_not_reopen_an_old_ticket_request():
    intake = compile_ticket_intake(_history(
        ("user", "draft a ticket: outcome: Something"),
        ("assistant", "What is the target?"),
        ("user", "what needs me now?"),
    ))

    assert intake.requested is False
