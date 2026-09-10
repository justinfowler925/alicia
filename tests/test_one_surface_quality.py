"""Quality gates ported off the deleted console document.

test_server.py carried seven tests that asserted on `BRUTUS_HTML`. Most were
markers from build plans that have shipped, but several were real gates earned
by real regressions, and deleting the page they watched is not a reason to stop
watching. They are restated here against the one surface.
"""

import re
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from brutus.config import BrutusCfg
from brutus.server import create_app

_STATIC = Path(__file__).resolve().parents[1] / "brutus" / "static"


def _html() -> str:
    with patch("brutus.server.AtlasClient") as atlas:
        atlas.return_value = MagicMock()
        return TestClient(create_app(BrutusCfg(watchdog_enabled=False), start_watchdog=False)).get("/").text


# --- the scripts parse -------------------------------------------------------


def test_every_script_the_surface_loads_parses(tmp_path):
    """The console's JS lived inside a Python string, where a bare \\n escape
    became a real newline and broke the parse. These are real files, so the
    hazard is gone — but a surface whose script does not parse renders as a
    dead page, which is how the Running panel shipped doing nothing."""
    for name in ("session.js", "operations.js"):
        subprocess.run(["node", "--check", str(_STATIC / name)], check=True)


# --- tokens, not values ------------------------------------------------------


def test_no_raw_hex_colors_in_the_surface():
    """Every colour comes from the token layer or the theme cannot switch."""
    css = (_STATIC / "session.css").read_text()
    body = re.sub(r"/\*.*?\*/", "", css, flags=re.DOTALL)
    hexes = re.findall(r"#[0-9a-fA-F]{3,8}\b", body)
    assert not hexes, f"raw hex colors in session.css: {sorted(set(hexes))}"


def test_the_surface_paints_from_the_token_layer():
    html, css = _html(), (_STATIC / "session.css").read_text()
    assert "/static/shine-tokens.css" in html
    for token in ("--shine-color-bg", "--shine-color-fg", "--shine-color-border", "--shine-color-primary"):
        assert f"var({token})" in css


def test_the_theme_is_declared_and_switchable():
    html = _html()
    assert "brutus.theme" in html
    assert 'id="theme-toggle"' in html


# --- narrow width: order is the fix, not existence ---------------------------


def test_the_phone_override_comes_after_the_rule_it_overrides():
    """Asserting a rule merely *exists* is what let the old one regress for
    months: the media query sat above the base block at equal specificity, so
    the base rule won and the rail rendered as a whole extra screen on phones.
    Order is the whole fix, so assert the order."""
    css = re.sub(r"/\*.*?\*/", "", (_STATIC / "session.css").read_text(), flags=re.DOTALL)

    base = css.index(".topbar {")
    phone = css.index("@media (max-width: 30rem)")
    assert phone > base, "the phone block must come after the base .topbar rule"
    assert css.count("@media (max-width: 30rem)") == 1, "one phone block, so order stays provable"


# --- the controls the console had --------------------------------------------


def test_live_state_and_spoken_replies_are_both_reachable():
    html, js = _html(), (_STATIC / "session.js").read_text()
    assert 'id="live"' in html
    assert 'id="mute"' in html
    assert "/api/speak" in js


def test_work_state_arrives_on_the_event_stream_not_a_timer():
    """Results arrive, they are not polled."""
    js = (_STATIC / "session.js").read_text()
    assert "events?workspace=true" in js
    assert 'case "board":' in js
    assert "applyBoardEvent(event)" in js
    assert "setInterval(loadBoard" not in js


# --- nothing on screen is a dead end -----------------------------------------


def test_no_href_the_browser_cannot_follow():
    html = _html()
    assert 'href="inbox:' not in html
    assert 'href="/Users/' not in html
    assert "<a onclick=" not in html


def test_no_browser_prompt_stands_in_for_a_control():
    for name in ("session.js", "operations.js"):
        assert "prompt(" not in (_STATIC / name).read_text()


def test_the_accessibility_scaffolding_is_present():
    html = _html()
    for marker in ('aria-live="polite"', "sr-only", 'role="tablist"', 'role="tab"', "aria-selected"):
        assert marker in html, marker
    assert ":focus-visible" in (_STATIC / "session.css").read_text()


def test_disclosure_state_is_native_rather_than_asserted_twice():
    """The console carried `aria-expanded` on its own disclosure widgets. This
    surface uses <details>/<summary>, where the browser exposes that state
    itself — adding the attribute would be a second, hand-maintained copy of a
    fact the element already reports, and the two drift."""
    html = _html()
    assert "<details" in html and "<summary" in html
    assert "aria-expanded" not in html


def test_every_record_table_declares_the_table_contract():
    ops = (_STATIC / "operations.js").read_text()
    assert 'setAttribute("data-shine-contract", "table")' in ops
    assert ops.count('grid.setAttribute("data-shine-contract", "table")') == 1
