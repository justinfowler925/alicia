"""Attention-budget guarantees: the surface must stay answerable.

These lock in the three failures that made the old surface unusable — 34 cards,
a third of them synthetic probes, and one dependency fault fanned out into 12
identical gates.
"""

from alicia.focus import attach_focus, build_board, build_focus, human_reason, is_probe


def _thread(ext: str, *, blocker: str = "", title: str = "Real work") -> dict:
    return {
        "id": f"id-{ext}",
        "external_id": ext,
        "title": title,
        "status": "blocked_justin",
        "blocker": blocker,
        "executor": "justin",
        "goal": "",
    }


def test_probe_threads_are_detected():
    assert is_probe({"title": "[Atlas5 proof burn-in] dlrs_rollup Questionnaire__c"})
    assert is_probe({"title": "[Atlas5 proof] lwc_bundle probe nob1"})
    assert not is_probe({"title": "Docs: Project Weekly Tracker feature overview"})


def test_probes_are_hidden_but_counted():
    gates = [_thread("REV-1", blocker="x")]
    gates += [
        _thread(f"REV-{i}", blocker="x", title="[Atlas5 proof burn-in] probe")
        for i in range(2, 8)
    ]
    focus = build_focus({"blocked_justin": gates, "counts": {}})
    assert focus["probes_hidden"] == 6
    assert focus["summary"]["probes_hidden"] == 6
    titles = " ".join(str(a["title"]) for a in focus["actions"])
    assert "proof burn-in" not in titles


def test_one_cause_collapses_to_one_card():
    """12 threads down for one reason is ONE decision, not 12."""
    gates = [
        _thread(f"REV-{i}", blocker="Atlas5 unhealthy — fail-closed to Justin")
        for i in range(1, 13)
    ]
    focus = build_focus({"blocked_justin": gates, "counts": {}})
    gate_cards = [a for a in focus["actions"] if a["kind"] == "gate"]
    assert len(gate_cards) == 1
    assert len(gate_cards[0]["items"]) == 12
    assert len(gate_cards[0]["thread_ids"]) == 12
    assert focus["summary"]["gate_causes"] == 1


def test_distinct_causes_stay_distinct():
    gates = [
        _thread("REV-1", blocker="Atlas5 unhealthy"),
        _thread("REV-2", blocker="autonomy=prod_gate blocks auto Atlas5 dispatch"),
    ]
    focus = build_focus({"blocked_justin": gates, "counts": {}})
    assert focus["summary"]["gate_causes"] == 2


def test_hard_cap_defers_overflow_without_losing_it():
    # Distinct non-numeric causes — _norm_blocker folds digits, so "cause 1" and
    # "cause 2" are deliberately ONE group.
    words = "alpha bravo charlie delta echo foxtrot golf hotel india juliet "\
            "kilo lima mike november oscar papa quebec romeo sierra tango".split()
    gates = [_thread(f"REV-{i}", blocker=f"blocked on {w}") for i, w in enumerate(words, 1)]
    focus = build_focus({"blocked_justin": gates, "counts": {}}, max_actions=7)
    non_info = [a for a in focus["actions"] if not a.get("informational")]
    assert len(non_info) <= 7
    overflow = [a for a in focus["actions"] if a["id"] == "deferred:overflow"]
    assert len(overflow) == 1
    assert focus["deferred_count"] > 0
    assert overflow[0]["deferred_titles"]


def test_alarm_is_carried_to_the_surface():
    alarm = {"alarm": True, "done_total": 0, "open": 27, "in_flight": 6, "window_hours": 6}
    focus = build_focus({"counts": {}}, alarm=alarm)
    assert focus["alarm"]["alarm"] is True
    assert focus["alarm"]["done_total"] == 0


def test_numeric_variants_of_one_cause_collapse():
    """"stale >7200s" and "stale >3600s" are the same decision, not two."""
    gates = [
        _thread("REV-1", blocker="stale in_flight >7200s with no VH receipt"),
        _thread("REV-2", blocker="stale in_flight >3600s with no VH receipt"),
    ]
    focus = build_focus({"blocked_justin": gates, "counts": {}})
    assert focus["summary"]["gate_causes"] == 1


def test_probe_questions_are_filtered_by_ticket_id():
    """Atlas5 awaiting_input rows are titled with the question, not the ticket.

    They must still be recognised as probes via the ledger's external_id.
    """
    status = {
        "in_flight": [
            {
                "id": "p1",
                "external_id": "REV-274",
                "title": "[Atlas5 proof burn-in] dlrs_rollup probe",
                "status": "in_flight",
            }
        ],
        "counts": {},
    }
    awaiting = [
        {"ticket_id": "REV-274", "question": "What is the specific Apex class name?" * 2},
        {"ticket_id": "REV-296", "question": "Which sandbox alias should the query run against?"},
    ]
    focus = build_focus(status, awaiting_input=awaiting)
    tickets = [a.get("ticket_id") for a in focus["actions"] if a.get("ticket_id")]
    assert "REV-296" in tickets
    assert "REV-274" not in tickets
    assert focus["probes_hidden"] >= 2


def test_duplicate_cursor_jobs_collapse_per_ticket():
    cursor = [
        {"external_id": "REV-271", "thread_id": "t1", "_path": "/q/a.json", "reason": "triage"},
        {"external_id": "REV-271", "thread_id": "t1", "_path": "/q/b.json", "reason": "triage"},
        {"external_id": "REV-302", "thread_id": "t2", "_path": "/q/c.json", "reason": "triage"},
    ]
    focus = build_focus({"cursor_pending": cursor, "counts": {}})
    cards = [a for a in focus["actions"] if a["kind"] == "needs_code"]
    assert len(cards) == 2
    rev271 = [c for c in cards if "REV-271" in c["title"]][0]
    assert "(2 queued)" in rev271["title"]
    assert len(rev271["paths"]) == 2


def test_no_grounding_questions_become_retriage_not_decisions():
    """The worker reporting it lacked grounding is a re-run, not a human answer."""
    awaiting = [
        {"ticket_id": "REV-218", "question": "The provided research contains no grounding data (no Salesforce object, field, or flow descriptions). Please provide the specific API names."},
        {"ticket_id": "REV-222", "question": "The provided research section contains no grounding data (no Salesforce object/field descriptions or API access)."},
        {"ticket_id": "REV-296", "question": "Which sandbox alias should the verification query run against?"},
    ]
    focus = build_focus({"counts": {}}, awaiting_input=awaiting)
    gate_tickets = [a.get("ticket_id") for a in focus["actions"] if a["kind"] == "gate"]
    assert gate_tickets == ["REV-296"], "only genuinely answerable questions are gates"
    retriage = [a for a in focus["actions"] if a["id"] == "retriage:no_grounding"]
    assert len(retriage) == 1
    assert sorted(retriage[0]["ticket_ids"]) == ["REV-218", "REV-222"]


def test_studio_down_never_reports_atlas5_healthy():
    """Regression for the original lie: an outage must not look like an empty queue."""
    from alicia.client import question_is_real, question_needs_retriage

    assert question_needs_retriage("The provided research contains no grounding data (x)")
    assert not question_needs_retriage("Which sandbox alias should I use?")
    assert question_is_real("Which sandbox alias should I use for partial-justin?")
    assert not question_is_real("<the missing input>")


def test_board_retriage_parked_rows_get_a_visible_steer_group():
    """The 2026-07-28 gap: awaiting rows parked on scoping-failure questions
    were dropped from needs_you and appeared in NO board section — REV-218/222/
    291/292 sat frozen 15 days. They must land in Stuck as a bot-side group
    whose remedy is steering (they have no atlas6 thread_ids to requeue)."""
    from alicia.focus import build_board

    status = {
        "blocked_justin": [],
        "blocked_frontier": [
            {"id": "id-9", "external_id": "REV-9", "title": "Ledger stall",
             "blocker": "no VH receipt"},
        ],
        "in_flight": [
            {"id": "id-p", "external_id": "REV-280",
             "title": "[Atlas5 proof burn-in] flow_create probe"},
        ],
        "ready": [],
    }
    awaiting = [
        {"ticket_id": "REV-291", "title": "",
         "question": "The provided research contains no grounding data (no Salesforce "
                     "object, field, or flow metadata).", "age_s": 1_300_000},
        {"ticket_id": "REV-50", "title": "Real ask",
         "question": "Which team owns the renewal pipeline report?", "age_s": 600},
        {"ticket_id": "REV-280", "title": "",
         "question": "The research section indicates no grounding available.",
         "age_s": 1_300_000},
    ]

    board = build_board(status, awaiting_input=awaiting)

    # The real question is still the human's.
    assert [r["ticket"] for r in board["needs_you"]] == ["REV-50"]

    steer = [g for g in board["stuck"] if g.get("unstick") == "steer"]
    assert len(steer) == 1
    g = steer[0]
    assert g["tickets"] == ["REV-291"]  # probe REV-280 stays hidden
    assert g["thread_ids"] == []  # nothing for /api/requeue_stale to target
    assert g["count"] == 1
    assert "look up" in g["reason"].lower() or "look them up" in g["why"].lower()

    # Ledger groups keep the requeue remedy.
    requeue = [x for x in board["stuck"] if x.get("unstick") == "requeue"]
    assert len(requeue) == 1 and requeue[0]["thread_ids"] == ["id-9"]

    # The group is counted, so the headline can't read "all quiet".
    assert board["stuck_total"] == 2
    assert board["counts"]["stuck"] == 2


def test_no_artifact_is_human_and_batches():
    """Live failure: 13× no_artifact became 13 peer Approve buttons."""
    label, why = human_reason("no_artifact")
    assert label == "No handback artifact"
    assert "Approve" in why
    gates = [
        _thread(f"REV-{i}", blocker="no_artifact", title=f"Real ticket {i}")
        for i in range(1, 14)
    ]
    focus = build_focus({"blocked_justin": gates, "counts": {}})
    gate_cards = [a for a in focus["actions"] if a["kind"] == "gate"]
    assert len(gate_cards) == 1
    assert len(gate_cards[0]["thread_ids"]) == 13
    assert "No handback artifact" in gate_cards[0]["title"]
    assert gate_cards[0]["why"] != "no_artifact"


def test_attach_focus_makes_board_headline_match_focus():
    gates = [
        _thread(f"REV-{i}", blocker="no_artifact", title=f"Ticket {i}")
        for i in range(1, 6)
    ]
    status = {"blocked_justin": gates, "in_flight": [], "ready": [], "counts": {}}
    board = build_board(status)
    focus = build_focus(status)
    merged = attach_focus(board, focus)
    assert merged["headline"] == focus["headline"]
    assert merged["counts"]["needs_you"] == 1  # one batched decision
    assert merged["counts"]["needs_you_items"] == 5  # five ticket rows kept
    assert len(merged["actions"]) >= 1
    assert "No handback artifact" in merged["actions"][0]["title"]


def test_answer_card_title_is_not_the_question():
    status = {
        "blocked_justin": [
            _thread("REV-408", blocker="awaiting", title="Unmute Sergii for metadata deploys"),
        ],
        "counts": {},
    }
    question = (
        "The ticket provides no specific business context, error logs, or user reports "
        "regarding 'Operator resume after'"
    )
    awaiting = [
        {"ticket_id": "REV-408", "title": question, "question": question, "age_s": 600},
    ]
    board = build_board(status, awaiting_input=awaiting)
    row = board["needs_you"][0]
    assert row["ticket"] == "REV-408"
    assert row["title"] == "Unmute Sergii for metadata deploys"
    assert row["question"] == question
