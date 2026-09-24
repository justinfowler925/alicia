from __future__ import annotations

import json
import stat
import urllib.parse
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from alicia.config import AliciaCfg
from alicia.server import create_app
from alicia.zoom_api import ZoomAPIError
from alicia.zoom_user_oauth import ZoomMyNotesClient, ZoomRefreshTokenStore


class _Store:
    def __init__(self, token: str = "refresh-old") -> None:
        self.token = token
        self.writes: list[str] = []

    def read(self) -> str:
        return self.token

    def write(self, token: str) -> None:
        self.token = token
        self.writes.append(token)


def _response(payload: dict):
    cm = MagicMock()
    cm.__enter__ = lambda _: MagicMock(read=lambda: json.dumps(payload).encode())
    cm.__exit__ = lambda *_: False
    return cm


def test_refresh_token_store_round_trips_with_owner_only_permissions(tmp_path) -> None:
    path = tmp_path / "zoom-token"
    store = ZoomRefreshTokenStore(path=path)
    store.write("refresh-one")
    assert store.read() == "refresh-one"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    store.write("refresh-two")
    assert store.read() == "refresh-two"
    assert list(tmp_path.iterdir()) == [path]


def test_pkce_authorization_url_has_s256_state_and_public_client() -> None:
    client = ZoomMyNotesClient(client_id="public-id", redirect_uri="http://127.0.0.1/cb", store=_Store())
    parsed = urllib.parse.urlparse(client.authorization_url())
    query = urllib.parse.parse_qs(parsed.query)
    assert parsed.geturl().startswith("https://zoom.us/oauth/authorize?")
    assert query["client_id"] == ["public-id"]
    assert query["code_challenge_method"] == ["S256"]
    assert query["state"][0]
    assert query["code_challenge"][0]


def test_pkce_callback_exchanges_code_checks_owner_and_persists_refresh() -> None:
    store = _Store()
    client = ZoomMyNotesClient(client_id="public-id", redirect_uri="http://127.0.0.1/cb", store=store)
    query = urllib.parse.parse_qs(urllib.parse.urlparse(client.authorization_url()).query)
    seen = []

    def fake_open(req, timeout=None):
        seen.append(req)
        if req.full_url.endswith("/oauth/token"):
            return _response({"access_token": "access", "refresh_token": "refresh-new", "expires_in": 3600})
        return _response({"email": "justin.fowler@clearspeed.com"})

    with patch("urllib.request.urlopen", fake_open):
        identity = client.accept_callback(
            "auth-code", query["state"][0], expected_email="justin.fowler@clearspeed.com"
        )
    fields = urllib.parse.parse_qs(seen[0].data.decode())
    assert identity["email"].startswith("justin")
    assert fields["client_id"] == ["public-id"]
    assert fields["code_verifier"][0]
    assert seen[0].get_header("Authorization") is None
    assert store.writes == ["refresh-new"]


def test_callback_refuses_wrong_principal_without_storing_token() -> None:
    store = _Store()
    client = ZoomMyNotesClient(client_id="public-id", store=store)
    state = urllib.parse.parse_qs(urllib.parse.urlparse(client.authorization_url()).query)["state"][0]
    with (
        patch(
            "urllib.request.urlopen",
            side_effect=[
                _response({"access_token": "access", "refresh_token": "refresh-new"}),
                _response({"email": "other@example.com"}),
            ],
        ),
        pytest.raises(ZoomAPIError, match="expected justin"),
    ):
        client.accept_callback("auth-code", state, expected_email="justin@example.com")
    assert store.writes == []


def test_refresh_uses_latest_token_and_rotates_keychain_value() -> None:
    store = _Store("refresh-old")
    client = ZoomMyNotesClient(client_id="public-id", store=store)
    seen = []

    def fake_open(req, timeout=None):
        seen.append(req)
        return _response({"access_token": "access", "refresh_token": "refresh-new", "expires_in": 3600})

    with patch("urllib.request.urlopen", fake_open):
        assert client.token() == "access"
        assert client.token() == "access"
    fields = urllib.parse.parse_qs(seen[0].data.decode())
    assert fields == {
        "grant_type": ["refresh_token"],
        "refresh_token": ["refresh-old"],
        "client_id": ["public-id"],
    }
    assert store.writes == ["refresh-new"]
    assert len(seen) == 1


def test_my_notes_calls_user_scoped_endpoints() -> None:
    client = ZoomMyNotesClient(client_id="public-id", store=_Store())
    with patch.object(
        ZoomMyNotesClient,
        "get",
        side_effect=[
            {"notes": [{"note_id": "note one"}]},
            {"note_id": "note one", "transcript": {"items": []}},
            {"email": "justin.fowler@clearspeed.com"},
        ],
    ) as get:
        assert client.list_my_notes()[0]["note_id"] == "note one"
        assert client.get_my_note("note one")["note_id"] == "note one"
        assert client.current_user()["email"].startswith("justin")
    assert get.call_args_list[1].args[0] == "/my_notes/notes/note%20one/content?include=transcript"


def test_canvas_search_discovers_notes_across_pages_and_maps_zoom_titles() -> None:
    client = ZoomMyNotesClient(client_id="public-id", store=_Store())
    with patch.object(
        client,
        "post",
        side_effect=[
            {
                "files": [
                    {
                        "file_id": "note-1",
                        "file_name": "Allison / Justin 1:1 2026-08-27 10:02(GMT-5:00)",
                        "file_link": "https://docs.zoom.us/doc/note-1",
                        "file_type": "note",
                    },
                    {"file_id": "not-a-note", "file_type": "doc"},
                ],
                "next_page_token": "next",
            },
            {
                "files": [
                    {
                        "file_id": "note-2",
                        "file_name": "Untitled note",
                        "file_link": "https://docs.zoom.us/doc/note-2",
                        "file_type": "note",
                    }
                ]
            },
        ],
    ) as post:
        notes = client.search_my_notes("2026-08-20", "2026-08-27")

    assert [note["note_id"] for note in notes] == ["note-1", "note-2"]
    assert notes[0]["created_time"] == "2026-08-27T10:02:00-05:00"
    assert notes[1]["created_time"] == "2026-08-20T00:00:00Z"
    assert post.call_args_list[0].args == (
        "/docs/file_search",
        {
            "file_types": ["note"],
            "page_size": 50,
            "created_time_from": "2026-08-20T00:00:00Z",
            "created_time_to": "2026-08-27T23:59:59Z",
        },
    )
    assert post.call_args_list[1].args[1]["next_page_token"] == "next"


def test_server_wires_oauth_start_and_callback(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("ALICIA_STATE_DIR", str(tmp_path))

    class FakeOAuth:
        accepted = None

        def __init__(self, **_kwargs) -> None:
            pass

        def authorization_url(self) -> str:
            return "https://zoom.us/oauth/authorize?state=s"

        def accept_callback(self, code, state, *, expected_email):
            FakeOAuth.accepted = (code, state, expected_email)
            return {"email": expected_email}

    monkeypatch.setattr("alicia.server.ZoomMyNotesClient", FakeOAuth)
    cfg = AliciaCfg(atlas6_url="http://127.0.0.1:8767", watchdog_enabled=False)
    with patch("alicia.server.AtlasClient") as cls:
        cls.return_value = MagicMock()
        with TestClient(create_app(cfg, start_watchdog=False)) as client:
            start = client.get("/api/zoom/oauth/start", follow_redirects=False)
            callback = client.get("/api/zoom/oauth/callback?code=c&state=s")
    assert start.status_code == 307
    assert start.headers["location"].startswith("https://zoom.us/oauth/authorize")
    assert callback.status_code == 200
    assert "Alicia My Notes is connected" in callback.text
    assert FakeOAuth.accepted == ("c", "s", "justin.fowler@clearspeed.com")


def test_server_logs_safe_oauth_failure_detail(tmp_path, monkeypatch, caplog) -> None:
    monkeypatch.setenv("ALICIA_STATE_DIR", str(tmp_path))

    class FailingOAuth:
        def __init__(self, **_kwargs) -> None:
            pass

        def accept_callback(self, *_args, **_kwargs):
            raise ZoomAPIError("Zoom user token request failed: HTTP 400 (Invalid client_id)")

    monkeypatch.setattr("alicia.server.ZoomMyNotesClient", FailingOAuth)
    cfg = AliciaCfg(atlas6_url="http://127.0.0.1:8767", watchdog_enabled=False)
    with patch("alicia.server.AtlasClient") as cls:
        cls.return_value = MagicMock()
        with TestClient(create_app(cfg, start_watchdog=False)) as client:
            with caplog.at_level("WARNING", logger="alicia.server"):
                response = client.get("/api/zoom/oauth/callback?code=c&state=s")
    assert response.status_code == 400
    assert "Invalid client_id" in caplog.text
