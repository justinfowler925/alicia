"""Two mflux passes, then Cursor. Pass 3 must not re-roll the local renderer."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from alicia.config import AliciaCfg
from alicia.image_passes import decide, key_for, record
from alicia.server import create_app


PROMPT = "photographic headshot of a man in a navy shirt, chest up, facing camera"


def test_third_pass_is_cursor_not_another_mflux_seed():
    store: dict = {"keys": {}}
    key = key_for(name="justin", prompt=PROMPT)
    record(store, key, prompt=PROMPT, n=1, engine="mflux", path="/a.png")
    record(store, key, prompt=PROMPT, n=2, engine="mflux", path="/b.png")
    d = decide(store, key, PROMPT)
    assert d.action == "cursor" and d.n == 3
    assert "/a.png" in d.cursor_prompt and "DIFFERENT" in d.cursor_prompt


def test_cursor_pass_endpoint_runs_the_handoff_not_mflux():
    cfg = AliciaCfg(watchdog_enabled=False)
    with patch("alicia.server.AtlasClient") as cls, \
         patch("alicia.avatars.run_cursor_image_pass", return_value={
             "ok": True, "name": "justin", "engine": "cursor"
         }) as run:
        cls.return_value = MagicMock()
        app = create_app(cfg, start_watchdog=False)
        r = TestClient(app).post("/api/avatar/cursor-pass", json={
            "prompt": PROMPT, "name": "justin",
        })
    assert r.status_code == 200
    assert r.json()["ok"] is True
    run.assert_called_once()
    assert run.call_args.kwargs["prompt"] == PROMPT
    assert run.call_args.kwargs["name"] == "justin"


def test_photo_lock_script_is_executable():
    script = Path(__file__).resolve().parents[1] / "scripts" / "photo-lock.sh"
    assert script.is_file()
    assert script.stat().st_mode & 0o111
    text = script.read_text()
    assert "atlas.photo_lock" in text
    assert "--studio" in text


def test_seed_for_pass_changes_on_pass_two():
    from alicia.image_passes import seed_for_pass

    assert seed_for_pass(1) == 42
    assert seed_for_pass(2) == 59
    assert seed_for_pass(3) != seed_for_pass(1)


def test_make_face_consults_the_ledger_before_mflux():
    """Pass 3 must not be another local seed. The script is the gate."""
    script = Path(__file__).resolve().parents[1] / "scripts" / "make-face.sh"
    text = script.read_text()
    assert text.index("decide_studio_pass") < text.index("mflux-generate")
    assert text.index('"$ACTION" = "cursor"') < text.index("mflux-generate")
    assert "/api/avatar/cursor-pass" in text
    assert "record_studio_pass" in text
    assert "--force-mflux" in text
    assert "--seed" in text
    assert "MAKE_FACE_PATH=\"$HOME/" not in text
    assert "/Users/jfstudio/mflux-out/faces/" in text

