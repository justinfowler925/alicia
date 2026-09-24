"""Focus analyzer ranking."""

from alicia.focus import KIND_CURSOR, KIND_FRONTIER, KIND_GATE, KIND_UNSTICK, KIND_WAIT, build_focus, linear_url


def test_linear_url():
    assert linear_url("REV-283", "clearspeed") == "https://linear.app/clearspeed/issue/REV-283"
    assert linear_url("nope", "clearspeed") is None


def test_focus_ranking_and_frontier_collapse():
    status = {
        "counts": {"ready": 0, "in_flight": 3, "blocked_justin": 1},
        "blocked_justin": [
            {
                "id": "g1",
                "external_id": "REV-1",
                "title": "Gate me",
                "blocker": "needs approve",
                "goal": "## Goal\nDo the thing",
                "evidence": "job_ledger:REV-1:build:failed",
                "updated_at": "2026-07-28T01:00:00+00:00",
            }
        ],
        "in_flight": [
            {
                "id": "s1",
                "external_id": "REV-2",
                "title": "Stale",
                "executor": "atlas5",
                "blocker": "reaped: stale",
                "evidence": "inbox:/Users/jfstudio/x",
                "last_dispatched_at": "2026-07-28T01:00:00+00:00",
                "updated_at": "2026-07-28T01:00:00+00:00",
            },
            {
                "id": "f1",
                "external_id": "REV-3",
                "title": "Fresh",
                "executor": "atlas5",
                "blocker": "",
                "evidence": "inbox:/Users/jfstudio/y",
                "last_dispatched_at": "2099-01-01T00:00:00+00:00",
                "updated_at": "2099-01-01T00:00:00+00:00",
            },
        ],
        "frontier_pending": [
            {"thread_id": "t1", "external_id": "REV-10", "title": "A", "reason": "triage routed to escalate/unknown", "_path": "a.json"},
            {"thread_id": "t2", "external_id": "REV-11", "title": "B", "reason": "triage routed to escalate/unknown", "_path": "b.json"},
            {"thread_id": "t3", "external_id": "REV-12", "title": "C", "reason": "other", "_path": "c.json"},
        ],
        "cursor_pending": [
            {"thread_id": "c1", "external_id": "REV-20", "title": "Code", "reason": "cursor", "_path": "cur.json", "prompt": "do code"},
        ],
    }
    out = build_focus(status, stale_inflight_minutes=45, linear_workspace="clearspeed")
    kinds = [a["kind"] for a in out["actions"]]
    assert kinds.index(KIND_GATE) < kinds.index(KIND_FRONTIER)
    assert KIND_UNSTICK in kinds
    assert KIND_CURSOR in kinds
    assert KIND_WAIT in kinds
    frontier_actions = [a for a in out["actions"] if a["kind"] == KIND_FRONTIER]
    assert len(frontier_actions) == 2  # collapsed by reason
    big = max(frontier_actions, key=lambda a: len(a["items"]))
    assert len(big["items"]) == 2
    assert out["charts"]["by_kind"][KIND_GATE] == 1
    assert out["charts"]["stale_vs_fresh"]["stale"] >= 1
    # Linear link present; no filesystem hrefs in links
    gate = next(a for a in out["actions"] if a["kind"] == KIND_GATE)
    assert gate["links"][0]["href"].startswith("https://linear.app/")
    blob = str(out)
    assert "href=\"inbox:" not in blob
    assert "/Users/jfstudio" not in str(gate.get("links"))


def test_awaiting_input_ranks_above_ledger_gates():
    status = {
        "counts": {"ready": 0, "in_flight": 0, "blocked_justin": 1},
        "blocked_justin": [
            {
                "id": "g1",
                "external_id": "REV-296",
                "title": "dup",
                "blocker": "old",
                "evidence": "",
                "updated_at": "2026-07-28T01:00:00+00:00",
            }
        ],
        "in_flight": [],
        "frontier_pending": [],
        "cursor_pending": [],
    }
    awaiting = [
        {
            "ticket_id": "REV-296",
            "action": "investigate",
            "title": "Sandbox",
            "question": "Which Salesforce username or alias for the partial-justin sandbox?",
            "linear_url": "https://linear.app/clearspeed/issue/REV-296",
        },
        {
            "ticket_id": "REV-121",
            "question": "<the missing input>",  # filtered by client normally; focus still shows if passed
        },
    ]
    # Client filters phantoms; focus should still handle real ones and dedupe REV-296
    real = [a for a in awaiting if a["ticket_id"] == "REV-296"]
    out = build_focus(status, awaiting_input=real, linear_workspace="clearspeed")
    gates = [a for a in out["actions"] if a["kind"] == KIND_GATE]
    assert len(gates) == 1
    assert gates[0]["recommended_verb"] == "answer_input"
    assert "partial-justin" in gates[0]["why"]
    assert out["summary"]["gates"] == 1


def test_board_rows_carry_an_advance_signal():
    """Session board was title-only — Justin could not see what advances the ticket."""
    from alicia.focus import build_board

    status = {
        "blocked_justin": [
            {
                "id": "g1",
                "external_id": "REV-1",
                "title": "Gate me",
                "blocker": "needs Justin approve before dispatch",
            }
        ],
        "blocked_frontier": [
            {
                "id": "f1",
                "external_id": "REV-2",
                "title": "Stuck bot",
                "blocker": "no VH receipt",
            }
        ],
        "in_flight": [
            {
                "id": "w1",
                "external_id": "REV-3",
                "title": "Working",
                "next_action": "wait on Marcus reply",
                "goal": "## Goal\nShip the renewals tracker",
            }
        ],
        "ready": [
            {
                "id": "q1",
                "external_id": "REV-4",
                "title": "Queued",
                "goal": "## Goal\nDraft the collateral pack",
            }
        ],
    }
    awaiting = [
        {
            "ticket_id": "REV-50",
            "title": "Real ask",
            "question": "Which team owns the renewal pipeline report?",
            "age_s": 600,
        }
    ]
    board = build_board(status, awaiting_input=awaiting)
    needs = {r["ticket"]: r for r in board["needs_you"]}
    assert "Which team owns" in needs["REV-50"]["signal"]
    assert needs["REV-1"]["signal"].startswith("Decide:")
    working = {r["ticket"]: r for r in board["working"]}
    assert working["REV-3"]["signal"] == "wait on Marcus reply"
    queued = {r["ticket"]: r for r in board["queued"]}
    assert "collateral" in queued["REV-4"]["signal"].lower()
    stuck_rows = [r for g in board["stuck"] for r in g["rows"]]
    assert any(r.get("signal") for r in stuck_rows)


def test_spoken_next_decision_is_one_question():
    from alicia.focus import spoken_next_decision

    board = {
        "actions": [
            {
                "kind": "gate",
                "title": "13 tickets — No handback artifact",
                "recommended_verb": "decide_gate",
                "why": "The worker stopped without leaving a receipt.",
            },
            {
                "kind": "gate",
                "title": "REV-352 — Held for WIP limit",
                "recommended_verb": "decide_gate",
            },
        ],
        "needs_you": [{"ticket": "REV-385", "verb": "decide", "reason": "No handback"}],
    }
    out = spoken_next_decision(board)
    assert out.startswith("13 tickets")
    assert "Approve, reject, or start over?" in out
    assert "Then 1 more." in out
    # Must not dump the second card.
    assert "REV-352" not in out
    assert spoken_next_decision({}) == "Nothing needs you right now."



def test_spoken_next_decision_falls_to_frontier_not_nothing():
    from alicia.focus import spoken_next_decision

    board = {
        "actions": [
            {
                "kind": "needs_judgement",
                "title": "6 tickets need a judgement call",
                "why": "No deterministic route",
            }
        ],
        "needs_you": [],
        "queued": [{"ticket": "REV-1", "title": "should not be spoken while frontier exists"}],
    }
    out = spoken_next_decision(board)
    assert "judgement" in out.lower()
    assert "REV-1" not in out
    assert "Then " not in out


def test_spoken_next_decision_falls_to_one_ready():
    from alicia.focus import spoken_next_decision

    board = {
        "actions": [],
        "needs_you": [],
        "queued": [
            {"ticket": "REV-10", "title": "Renewal copier"},
            {"ticket": "REV-11", "title": "Should stay unspoken"},
        ],
    }
    out = spoken_next_decision(board)
    assert out.startswith("REV-10")
    assert "dispatch" in out.lower()
    assert "REV-11" not in out
    assert "Then " not in out


def test_spoken_next_decision_does_not_say_a_uuid():
    from alicia.focus import spoken_next_decision

    board = {
        "actions": [],
        "needs_you": [
            {
                "ticket": "6d0b8f2a-7e2b-4e4f-b19c-6dc5f6fd80fe",
                "title": "Approve GitHub Actions Salesforce CI run for SFDC Prod",
                "verb": "decide",
                "reason": "Needs the prod click",
            }
        ],
    }
    out = spoken_next_decision(board)
    assert "6d0b8f2a" not in out.lower()
    assert "Approve" in out


def test_thread_without_external_id_is_not_double_carded():
    """A thread with no external_id is keyed in Atlas5 by its FULL id. Comparing
    a truncated 8-char prefix never matched, so it rendered twice — once as a
    gate ("Approve/Reject") and once as an answer card — two contradictory verbs
    for one thread. Observed live on 387df8b3-0b2f-… (2026-08-20)."""
    tid = "387df8b3-267f-42c9-ad4d-9904b74f5a60"
    status = {
        "counts": {"ready": 0, "in_flight": 0, "blocked_justin": 1},
        "blocked_justin": [
            {
                "id": tid,
                "external_id": None,
                "title": "Capture Tool question",
                "blocker": "awaiting_input: the ticket references 'Capture Tool'",
                "evidence": "",
                "updated_at": "2026-08-20T01:00:00+00:00",
            }
        ],
        "in_flight": [],
        "frontier_pending": [],
        "cursor_pending": [],
    }
    awaiting = [
        {
            "ticket_id": tid.upper(),
            "action": "investigate",
            "title": "Capture Tool question",
            "question": "Which Capture Tool instance does David mean here?",
        }
    ]
    out = build_focus(status, awaiting_input=awaiting, linear_workspace="clearspeed")
    gates = [a for a in out["actions"] if a["kind"] == KIND_GATE]
    assert len(gates) == 1, f"one thread must yield one card, got {len(gates)}"
    assert gates[0]["recommended_verb"] == "answer_input"
