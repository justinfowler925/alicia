from __future__ import annotations

from unittest.mock import MagicMock, patch

from alicia.linear_surface import TEAM_ID, create_linear_ticket, find_linear_ticket_candidates
from alicia.tools import _create_linear_ticket_from_unfog


def test_create_linear_ticket_sends_one_exact_issue_mutation():
    response = MagicMock()
    response.json.return_value = {
        "data": {
            "issueCreate": {
                "success": True,
                "issue": {"identifier": "REV-900", "title": "Voice supervisor", "url": "https://linear/REV-900"},
            }
        }
    }
    client = MagicMock()
    client.__enter__.return_value = client
    client.post.return_value = response
    with (
        patch.dict("os.environ", {"LINEAR_API_KEY": "secret"}),
        patch("alicia.linear_surface.httpx.Client", return_value=client),
    ):
        result = create_linear_ticket(" Voice supervisor ", " Complete Unfog contract ")

    payload = client.post.call_args.kwargs["json"]
    assert payload["variables"]["input"] == {
        "teamId": TEAM_ID,
        "title": "Voice supervisor",
        "description": "Complete Unfog contract",
    }
    assert result == {
        "ok": True,
        "created": True,
        "ticket": "REV-900",
        "title": "Voice supervisor",
        "url": "https://linear/REV-900",
        "source": "linear_direct",
    }


def test_find_linear_candidates_marks_only_exact_title_as_exact():
    response = MagicMock()
    response.json.return_value = {
        "data": {"team": {"issues": {"nodes": [
            {"identifier": "REV-1", "title": "Voice supervisor", "state": {"type": "started"}},
            {"identifier": "REV-2", "title": "Voice supervisor follow-up", "state": {"type": "backlog"}},
        ]}}}
    }
    client = MagicMock()
    client.__enter__.return_value = client
    client.post.return_value = response
    with (
        patch.dict("os.environ", {"LINEAR_API_KEY": "secret"}),
        patch("alicia.linear_surface.httpx.Client", return_value=client),
    ):
        rows = find_linear_ticket_candidates("Voice supervisor")
    assert [row["relationship"] for row in rows] == ["exact", "related"]


def _contract() -> dict:
    return {
        "title": "Voice supervisor",
        "outcome": "Voice supervisor",
        "target": "Alicia session surface",
        "premise": "No matching ticket or live session exists.",
        "scope": "One production behavior",
        "preservation": "Existing sessions and tickets",
        "acceptance": ["Production probe passes"],
        "delivery": "Commit, deploy, and verify",
        "evidence": [{"claim": "gap", "source": "runtime", "observation": "missing"}],
    }


def test_ticket_execution_rechecks_linear_and_blocks_exact_duplicate():
    with (
        patch("alicia.tools.find_linear_ticket_candidates", return_value=[{
            "ticket_id": "REV-1", "title": "Voice supervisor", "relationship": "exact", "status": "started"
        }]),
        patch("alicia.tools.scan_agent_sessions", return_value=[]),
        patch("alicia.tools.create_linear_ticket") as create,
    ):
        result = _create_linear_ticket_from_unfog(**_contract())
    assert result["blocked"] is True
    assert result["decision"]["action"] == "update_existing"
    create.assert_not_called()


def test_ticket_execution_rechecks_live_sessions_and_blocks_duplicate_work():
    with (
        patch("alicia.tools.find_linear_ticket_candidates", return_value=[]),
        patch("alicia.tools.scan_agent_sessions", return_value=[{
            "id": "codex:one", "title": "Voice supervisor", "live": True
        }]),
        patch("alicia.tools.create_linear_ticket") as create,
    ):
        result = _create_linear_ticket_from_unfog(**_contract())
    assert result["decision"]["action"] == "continue"
    create.assert_not_called()


def test_ticket_execution_builds_description_from_contract_after_clear_recheck():
    with (
        patch("alicia.tools.find_linear_ticket_candidates", return_value=[]),
        patch("alicia.tools.scan_agent_sessions", return_value=[]),
        patch("alicia.tools.create_linear_ticket", return_value={"ok": True, "ticket": "REV-9"}) as create,
    ):
        result = _create_linear_ticket_from_unfog(**_contract())
    assert result["ticket"] == "REV-9"
    description = create.call_args.args[1]
    assert "## Outcome\nVoice supervisor" in description
    assert "Production probe passes" in description
