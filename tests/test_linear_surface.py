from unittest.mock import MagicMock, patch

import pytest

from alicia.linear_surface import linear_work_surface
from alicia.tools import _work_surface


def _response():
    return {
        "data": {
            "team": {
                "issues": {
                    "nodes": [
                        {"identifier": "REV-507", "title": "Fix prod", "priority": 1, "updatedAt": "2026-08-24T15:00:00Z", "url": "https://linear/507", "state": {"name": "In Review", "type": "started"}},
                        {"identifier": "REV-544", "title": "Benchmark", "priority": 1, "updatedAt": "2026-08-24T14:00:00Z", "url": "https://linear/544", "state": {"name": "In Progress", "type": "started"}},
                        {"identifier": "REV-58", "title": "ROI", "priority": 4, "updatedAt": "2026-08-23T14:00:00Z", "url": "https://linear/58", "state": {"name": "Backlog", "type": "backlog"}},
                    ]
                }
            }
        }
    }


def test_linear_surface_classifies_current_work(monkeypatch):
    monkeypatch.setenv("LINEAR_API_KEY", "test-key")
    response = MagicMock()
    response.json.return_value = _response()
    response.raise_for_status.return_value = None
    client = MagicMock()
    client.__enter__.return_value.post.return_value = response
    with patch("alicia.linear_surface.httpx.Client", return_value=client):
        surface = linear_work_surface()
    assert surface["source"] == "linear_direct"
    assert surface["needs_you"][0]["ticket"] == "REV-507"
    assert surface["working"][0]["ticket"] == "REV-544"
    assert surface["queued"][0]["ticket"] == "REV-58"


def test_work_surface_uses_linear_without_probing_retired_atlas(monkeypatch):
    atlas = MagicMock()
    fallback = {"source": "linear_direct", "needs_you": [{"ticket": "REV-507", "title": "Fix prod", "reason": "In Review"}], "working": [], "stuck": [], "queued": [], "actions": []}
    with patch("alicia.tools.linear_work_surface", return_value=fallback):
        surface = _work_surface(atlas)
    assert surface["source"] == "linear_direct"
    assert surface["next_decision"].startswith("REV-507")
    atlas.status.assert_not_called()


def test_work_surface_fails_honestly_without_atlas_rollback():
    atlas = MagicMock()
    atlas.status.return_value = {
        "blocked_justin": [], "in_flight": [], "ready": [],
        "blocked_frontier": [], "completion_alarm": {}, "counts": {},
    }
    atlas.list_awaiting_input.return_value = []
    with patch("alicia.tools.linear_work_surface", side_effect=RuntimeError("offline")), pytest.raises(RuntimeError, match="offline"):
        _work_surface(atlas)
    atlas.status.assert_not_called()


def test_missing_linear_key_fails_without_naming_credentials(monkeypatch):
    monkeypatch.delenv("LINEAR_API_KEY", raising=False)
    try:
        linear_work_surface()
    except RuntimeError as exc:
        message = str(exc).casefold()
    else:
        raise AssertionError("missing key must fail")
    assert "key" not in message and "credential" not in message and "token" not in message
