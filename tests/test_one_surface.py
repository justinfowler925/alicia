"""One surface, at one URL.

Three front-ends used to answer "what needs me now": / served 161 KB of ui.py
with its own nav and chat dock, /session served the voice shell, and /mobile
served a third voice UI whose endpoint set was a strict subset of /session's.
~9,200 lines of front-end across three apps and thirteen named pages, all
independently rendering /api/board, /api/todos, /api/session/* and the same
two SSE streams — and on one screen they disagreed about how many projects
exist four different ways.

These tests hold the collapse in place.
"""

from pathlib import Path
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from brutus.config import BrutusCfg
from brutus.server import create_app

_STATIC = Path(__file__).resolve().parents[1] / "brutus" / "static"


def _client() -> TestClient:
    cfg = BrutusCfg(watchdog_enabled=False)
    with patch("brutus.server.AtlasClient") as cls:
        cls.return_value = MagicMock()
        return TestClient(create_app(cfg, start_watchdog=False), follow_redirects=False)


def test_the_front_door_is_the_conversation():
    response = _client().get("/")

    assert response.status_code == 200
    assert "no-store" in response.headers["Cache-Control"]
    assert 'data-cite="spectrum-ai-chat"' in response.text
    assert 'data-testid="voice-stage"' in response.text
    assert "/static/session.js" in response.text


def test_the_mobile_fork_is_gone_and_its_url_lands_on_the_one_surface():
    """A phone-only fork of the same screen is a second answer, not a feature."""
    assert not (_STATIC / "mobile.html").exists()
    assert not (_STATIC / "mobile.js").exists()
    assert not (_STATIC / "mobile.css").exists()

    response = _client().get("/mobile")

    assert response.status_code == 308
    assert response.headers["location"] == "/"


def test_the_retired_mobile_assets_can_no_longer_be_served():
    client = _client()
    for name in ("mobile.js", "mobile.css", "mobile.html"):
        assert client.get(f"/static/{name}").status_code == 404


def test_session_stays_an_alias_because_it_is_bookmarked():
    client = _client()
    assert client.get("/session").text == client.get("/").text


def test_the_console_keeps_the_pages_that_have_not_moved_yet():
    """Retiring a document that holds the only copy of a feature loses the
    feature. Inbox, Projects, Studio, Sites, Avatar and Demos live only there."""
    response = _client().get("/console")

    assert response.status_code == 200
    assert "no-store" in response.headers["Cache-Control"]
    assert "nav-projects" in response.text


def test_the_one_surface_carries_the_running_panel():
    html = _client().get("/").text

    assert 'role="tablist"' in html
    assert 'id="tab-running"' in html
    assert 'id="panel-running"' in html
    assert "/static/operations.js" in html


def test_the_running_panel_is_responsive_which_is_what_the_fork_was_for():
    css = (_STATIC / "session.css").read_text()
    assert "@media (max-width: 40rem)" in css
    assert ".ops-grid" in css and "overflow-x: auto" in css


def test_every_token_the_running_panel_uses_is_defined():
    """A `var()` on an undefined token is dropped silently, so the hit-target
    floor simply would not have applied. `--tap` lived only in ui.py."""
    import re

    css = (_STATIC / "session.css").read_text()
    tokens = (_STATIC / "shine-tokens.css").read_text()
    used = set(re.findall(r"var\((--[a-z0-9-]+)", css))
    defined = set(re.findall(r"(--[a-z0-9-]+)\s*:", css + tokens))

    assert used <= defined, f"undefined tokens: {sorted(used - defined)}"
    assert "--tap" in defined


# --- what only a browser caught ------------------------------------------


def test_the_running_panel_does_not_collide_with_the_shell_script():
    """Both files are classic scripts sharing one global scope.

    The first version of operations.js declared `const $` and so did
    session.js, which is a SyntaxError at parse time — so not one line of the
    panel executed and the tray tab silently did nothing. Every Python test
    passed. The browser said it on the first load.
    """
    ops = (_STATIC / "operations.js").read_text()
    session = (_STATIC / "session.js").read_text()

    assert "const $ = (sel)" in session, "the shell still owns the bare $"
    assert ops.lstrip().startswith("/*")
    assert "(function () {" in ops and ops.rstrip().endswith("})();"), (
        "operations.js must keep its own scope"
    )
    # Nothing but the one deliberate global.
    assert ops.count("window.") == 1


def test_a_grid_cell_cannot_blow_out_past_the_viewport():
    """A grid item defaults to min-width:auto, which is min-content.

    Measured at 375px: the table's own `min-width: 34rem` stretched the cell to
    666px inside a 345px column, so the rows sat off-screen to the right
    instead of scrolling inside their own box.
    """
    css = (_STATIC / "session.css").read_text()
    assert ".ops-groups > .ops-group { min-width: 0; }" in css


def test_the_tray_does_not_push_the_conversation_off_the_page():
    """34rem was a floor for the stage even with the tray open, so reaching the
    tables meant scrolling past 800px of empty transcript."""
    css = (_STATIC / "session.css").read_text()
    assert "grid-template-rows: auto minmax(20rem, auto) auto;" in css
    assert ".work-tray[open] > .tray-panel" in css
    assert "max-height: min(62vh, 46rem);" in css


def test_the_phone_width_is_the_real_surface_now():
    """Deleting /mobile means 375px has to work, not degrade."""
    css = (_STATIC / "session.css").read_text()
    assert "@media (max-width: 30rem)" in css
    phone = css[css.index("@media (max-width: 30rem)"):]
    assert ".topbar {" in phone and "flex-wrap: wrap;" in phone
    assert ".topbar > .title { flex: 1 0 100%; }" in phone
