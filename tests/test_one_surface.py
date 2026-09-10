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


def test_the_console_is_gone_and_its_url_lands_on_the_one_surface():
    """161 KB and 2,432 lines producing ten pages. Eight are panels here now,
    Sites was six links given a nav entry, and Avatar and Demos were dormant
    UI over endpoints that still work."""
    from pathlib import Path as _P

    assert not (_P(__file__).parents[1] / "brutus" / "ui.py").exists()
    assert not (_P(__file__).parents[1] / "brutus" / "studio_ui.py").exists()

    response = _client().get("/console")
    assert response.status_code == 308
    assert response.headers["location"] == "/"


def test_the_avatar_endpoints_survive_the_ui_that_is_gone():
    """Deleting a page must not delete a capability."""
    routes = {getattr(r, "path", "") for r in _client().app.routes}
    for path in ("/api/avatar", "/api/avatar/apply", "/api/avatar/stage", "/api/avatar/configs"):
        assert path in routes


def test_the_one_surface_carries_every_daily_panel():
    """Running, Work and Projects were three pages on the second document."""
    html = _client().get("/").text

    assert 'role="tablist"' in html
    for key in ("running", "work", "projects", "queue"):
        assert f'id="tab-{key}"' in html
        assert f'id="panel-{key}"' in html
    assert "/static/operations.js" in html


def test_the_panels_share_one_table_engine():
    """A second component for the same object is the duplication being removed.

    The panels are empty sections in the HTML; operations.js builds the toolbar
    and the table for all three from one implementation.
    """
    html = _client().get("/").text
    ops = (_STATIC / "operations.js").read_text()

    for key in ("running", "work", "projects"):
        assert f'data-ops-panel="{key}"' in html
    # One toolbar builder, one table builder, one action runner.
    assert ops.count("function chrome(") == 1
    assert ops.count("function table(") == 1
    assert ops.count("async function runAction(") == 1
    assert ops.count('className = "ops-toolbar"') == 1
    # The HTML carries no per-panel toolbar copies.
    assert 'id="ops-search"' not in html


def test_a_panel_loads_on_first_open_not_on_page_load():
    """The console fetched twelve endpoints for pages nobody had opened, two of
    which took 36 seconds and exhausted the browser's connections."""
    ops = (_STATIC / "operations.js").read_text()
    assert "if (panel) loadPanel(panel);" in ops
    assert "if (pState.loaded && !force) return;" in ops


def test_no_column_is_wired_to_a_field_that_is_always_empty():
    """`active_ticket_count` and `live_thread_count` are 0 on every row — they
    count a narrower thing than the labels promised, and a structurally empty
    column reads as "no tickets" rather than "wrong field"."""
    ops = (_STATIC / "operations.js").read_text()
    projects = ops[ops.index("const PROJECT_GROUPS"):ops.index("/* One canon fetch")]
    # The comment explains which fields were wrong, so check the code.
    projects = "\n".join(
        line for line in projects.splitlines() if not line.lstrip().startswith("//")
    )

    assert "active_ticket_count" not in projects
    assert "live_thread_count" not in projects
    assert 'key: "ticket_count"' in projects
    assert 'key: "recent_thread_count"' in projects


def test_status_chips_are_english_not_field_names():
    ops = (_STATIC / "operations.js").read_text()
    assert 'needs_you: "Needs you"' in ops
    assert 'at_risk: "At risk"' in ops
    assert "chip.textContent = statusWords(text);" in ops


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
    # One deliberate global assignment. `window.open` for a site link is not one.
    assert ops.count("window.brutusOps =") == 1
    assert "window." not in ops.replace("window.brutusOps =", "").replace("window.open(", "")


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


# --- the last two console pages -------------------------------------------


def test_sites_is_a_panel_not_a_page():
    """The console gave six links a nav entry, a page title and 192 lines."""
    html = _client().get("/").text
    ops = (_STATIC / "operations.js").read_text()

    assert 'id="tab-sites"' in html and 'data-ops-panel="sites"' in html
    assert 'const SITE_GROUPS' in ops
    # A configured site with no address is a state to show, not a row to hide.
    assert '"no address configured"' in ops


def test_the_panel_chrome_is_built_once():
    """Building it inside paint() was a race: two paints — one for "loading",
    one for "loaded" — could both find no .ops-groups, both build fresh chrome,
    and the second replaceChildren() throw away the container the first had
    just filled. Sites rendered its toolbar and no rows while its own state
    said `loaded` with six of them."""
    ops = (_STATIC / "operations.js").read_text()

    assert "function mount(panel)" in ops
    assert ops.count("host.replaceChildren(") == 1
    paint = ops[ops.index("function paint(panel)"):ops.index("function table(panel")]
    # The comment in paint() explains the race, so check the code.
    paint = "\n".join(l for l in paint.splitlines() if not l.lstrip().startswith("//"))
    assert "replaceChildren" not in paint
    assert "if (!groupHost) return;" in paint


def test_the_studio_log_is_reachable_from_the_row():
    ops = (_STATIC / "operations.js").read_text()
    html = _client().get("/").text

    assert 'label: "Log"' in ops
    assert "async function showLog(row)" in ops
    assert 'id="ops-log"' in html and 'id="ops-log-body"' in html
