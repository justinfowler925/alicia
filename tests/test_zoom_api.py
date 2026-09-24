"""The autonomous Zoom lane: Server-to-Server OAuth + the summaries API.

No network here. What is asserted is the shape of the requests and the handling
of the two things the live API actually did during development: a summary that
exists with no content, and UUIDs that must be double-encoded.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from alicia.zoom_api import (
    MAX_RANGE_DAYS,
    ZoomAPIError,
    ZoomClient,
    ZoomCredentials,
    assets_from_summary,
    default_window,
    double_encode,
)
from alicia.zoom_ingest import extract_items, parse_api_next_steps

CREDS = ZoomCredentials(account_id="acct", client_id="cid", client_secret="csec")


# --- credentials -----------------------------------------------------------


def test_credentials_prefer_the_environment(monkeypatch) -> None:
    monkeypatch.setenv("ZOOM_ACCOUNT_ID", "A")
    monkeypatch.setenv("ZOOM_CLIENT_ID", "B")
    monkeypatch.setenv("ZOOM_CLIENT_SECRET", "C")
    creds = ZoomCredentials.load()
    assert (creds.account_id, creds.client_id, creds.client_secret) == ("A", "B", "C")


def test_missing_env_vars_raise_zoom_api_error(monkeypatch) -> None:
    monkeypatch.setenv("ZOOM_ACCOUNT_ID", "A")
    monkeypatch.delenv("ZOOM_CLIENT_ID", raising=False)
    monkeypatch.delenv("ZOOM_CLIENT_SECRET", raising=False)
    with pytest.raises(ZoomAPIError, match="missing Zoom credentials"):
        ZoomCredentials.load()


def test_missing_credentials_raise_rather_than_call_zoom(monkeypatch) -> None:
    for var in ("ZOOM_ACCOUNT_ID", "ZOOM_CLIENT_ID", "ZOOM_CLIENT_SECRET"):
        monkeypatch.delenv(var, raising=False)
    with pytest.raises(ZoomAPIError, match="missing Zoom credentials"):
        ZoomCredentials.load()


# --- uuid encoding ---------------------------------------------------------


def test_uuid_is_double_encoded() -> None:
    """Real UUIDs contain / and +; single-encoding routes to the wrong path."""
    assert double_encode("8/ii/6XgSzSfvW0lyVs8yg==") == "8%252Fii%252F6XgSzSfvW0lyVs8yg%253D%253D"
    assert double_encode("8ZHzz9PRRP6R2ToX5D3YKg==") == "8ZHzz9PRRP6R2ToX5D3YKg%253D%253D"


# --- token handling --------------------------------------------------------


def _fake_urlopen(payload: dict, recorder: list | None = None):
    def _open(req, timeout=None):
        if recorder is not None:
            recorder.append(req)
        cm = MagicMock()
        cm.__enter__ = lambda s: MagicMock(read=lambda: json.dumps(payload).encode(), status=200)
        cm.__exit__ = lambda *a: False
        return cm

    return _open


def test_token_is_requested_with_basic_auth_and_account_id() -> None:
    reqs: list = []
    c = ZoomClient(CREDS)
    with patch("urllib.request.urlopen", _fake_urlopen({"access_token": "T", "expires_in": 3600}, reqs)):
        assert c.token() == "T"
    assert "account_id=acct" in reqs[0].full_url
    assert reqs[0].get_header("Authorization").startswith("Basic ")


def test_token_is_reused_until_it_nearly_expires() -> None:
    reqs: list = []
    c = ZoomClient(CREDS)
    with patch("urllib.request.urlopen", _fake_urlopen({"access_token": "T", "expires_in": 3600}, reqs)):
        c.token()
        c.token()
        c.token()
    assert len(reqs) == 1, "one token should serve a whole poll"


def test_token_response_without_a_token_is_an_error() -> None:
    c = ZoomClient(CREDS)
    with (
        patch("urllib.request.urlopen", _fake_urlopen({"no": "token"})),
        pytest.raises(ZoomAPIError, match="no access_token"),
    ):
        c.token()


# --- list / get ------------------------------------------------------------


def test_list_summaries_follows_pagination() -> None:
    pages = [
        {"summaries": [{"meeting_uuid": "a"}], "next_page_token": "p2"},
        {"summaries": [{"meeting_uuid": "b"}], "next_page_token": ""},
    ]
    c = ZoomClient(CREDS)
    c._token, c._token_expires_at = "T", 1e18
    with patch.object(ZoomClient, "get", side_effect=pages) as g:
        got = c.list_summaries("2026-08-01", "2026-08-11")
    assert [x["meeting_uuid"] for x in got] == ["a", "b"]
    assert "next_page_token=p2" in g.call_args_list[1].args[0]


def test_a_404_is_empty_not_an_exception() -> None:
    """A meeting with no summary must not sink a whole poll."""
    import urllib.error

    c = ZoomClient(CREDS)
    c._token, c._token_expires_at = "T", 1e18
    err = urllib.error.HTTPError("u", 404, "nf", {}, None)
    with patch("urllib.request.urlopen", side_effect=err):
        assert c.get("/meetings/x/meeting_summary") == {}


def test_other_http_errors_raise() -> None:
    import urllib.error

    c = ZoomClient(CREDS)
    c._token, c._token_expires_at = "T", 1e18
    with (
        patch("urllib.request.urlopen", side_effect=urllib.error.HTTPError("u", 401, "no", {}, None)),
        pytest.raises(ZoomAPIError, match="HTTP 401"),
    ):
        c.get("/meetings/x/meeting_summary")


def test_participant_emails_are_lowercased() -> None:
    c = ZoomClient(CREDS)
    c._token, c._token_expires_at = "T", 1e18
    payload = {"participants": [{"user_email": "Alex.Example@example.com"}, {"name": "Guest"}]}
    with patch.object(ZoomClient, "get", return_value=payload):
        assert "alex.example@example.com" in c.participant_emails("u")


# --- window ----------------------------------------------------------------


def test_window_is_clamped_to_the_month_zoom_allows() -> None:
    frm, to = default_window(999)
    assert (
        __import__("datetime").date.fromisoformat(to)
        - __import__("datetime").date.fromisoformat(frm)
    ).days == MAX_RANGE_DAYS
    assert default_window(0) != (to, to) or True  # never a zero-width window
    frm1, to1 = default_window(0)
    assert frm1 < to1


# --- adapting the API shape to the ingest shape ---------------------------


def test_assets_from_summary_maps_the_fields_ingest_reads() -> None:
    assets = assets_from_summary(
        {
            "meeting_uuid": "synthetic-product-sync-001",
            "meeting_topic": "Product sync",
            "meeting_start_time": "2026-01-15T16:15:00Z",
            "meeting_host_email": "host@example.com",
            "next_steps": ["Alex Example: Share the interface review checklist."],
        }
    )
    assert assets["meeting_uuid"] == "synthetic-product-sync-001"
    assert assets["topic"] == "Product sync"
    assert assets["start_time"].startswith("2026-01-15")
    items = extract_items(assets)
    assert len(items) == 1
    assert items[0].owner == "Alex Example"
    assert items[0].text == "Share the interface review checklist"


def test_summary_with_no_content_yields_nothing() -> None:
    """Metadata-only meetings yield no invented work."""
    assets = assets_from_summary(
        {
            "meeting_uuid": "t2qfzoZ5RJetBqmctr4Usw==",
            "meeting_topic": "Data Project Update",
            "meeting_start_time": "2026-08-11T14:00:00Z",
            "next_steps": None,
        }
    )
    assert assets["next_steps"] == []
    assert extract_items(assets) == []


# --- parsing the flat list -----------------------------------------------


def test_flat_next_steps_split_owner_from_text() -> None:
    items = parse_api_next_steps(
        [
            "Jimmy Gibson: Prepare and serve the conversational demo.",
            "Jules Ehrlich: Confirm with Alex whether Bill Farr attended.",
        ]
    )
    assert [i.owner for i in items] == ["Jimmy Gibson", "Jules Ehrlich"]
    assert all(i.source == "next_steps" for i in items)


def test_a_sentence_with_a_colon_keeps_its_whole_text() -> None:
    """Only a name-shaped prefix becomes an owner, or text gets truncated."""
    items = parse_api_next_steps(
        ["Decide the following: whether to ship the demo on one screen or two."]
    )
    assert len(items) == 1
    assert items[0].owner == ""
    assert items[0].text.startswith("Decide the following")


def test_flat_list_handles_none_and_junk() -> None:
    assert parse_api_next_steps(None) == []
    assert parse_api_next_steps([]) == []
    assert parse_api_next_steps(["", "   "]) == []
    assert parse_api_next_steps(["Next steps were not generated due to insufficient transcript."]) == []


@pytest.mark.parametrize(
    "head,is_owner",
    [
        ("Jimmy Gibson", True),
        ("Justin", True),
        ("Jimmy + Patrick + Maria", True),
        ("Rob / Swapna", True),
        ("Team (Jimmy Gibson, Patrick Smyth)", True),
        ("Decide the following", False),
        ("Note that the demo", False),
        ("One thing to confirm", False),
    ],
)
def test_owner_shaped_prefixes(head: str, is_owner: bool) -> None:
    """Real prefixes seen in Zoom output, and sentence openings that are not names."""
    from alicia.zoom_ingest import _looks_like_owner

    assert _looks_like_owner(head) is is_owner
