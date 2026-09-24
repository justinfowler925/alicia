"""The transport half of the Zoom AI Companion lane.

`scripts/zoom_notes_to_alicia.py` only moves already-fetched payloads to
`POST /api/zoom/ingest`; extraction and dedupe are tested in `test_zoom_ingest`.
What matters here is that it accepts the shapes a caller actually produces, drops
the transcript, and stays quiet when the daemon is down — a launchd job that
exits non-zero on a stopped service spams the log forever.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "zoom_notes_to_alicia.py"
FIXTURES = Path(__file__).parent / "fixtures"


def _load_module():
    spec = importlib.util.spec_from_file_location("zoom_notes_to_alicia", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def mod():
    return _load_module()


def test_script_exists_and_is_executable() -> None:
    assert SCRIPT.is_file()
    assert SCRIPT.stat().st_mode & 0o111, "launchd and shell wrappers need the exec bit"


def test_strip_transcript_leaves_the_notes_intact(mod) -> None:
    assets = json.loads((FIXTURES / "zoom_assets_product_sync.json").read_text())
    assets["my_notes"]["transcript"] = {"transcript_items": [{"text": "hold on"} for _ in range(500)]}
    trimmed = mod.strip_transcript(assets)
    assert "transcript" not in trimmed["my_notes"]
    assert trimmed["my_notes"]["content_markdown"] == assets["my_notes"]["content_markdown"]
    # The input must not be mutated — callers reuse it.
    assert "transcript" in assets["my_notes"]


def test_loads_a_single_object(mod, tmp_path: Path) -> None:
    p = tmp_path / "one.json"
    p.write_text(json.dumps({"meeting_uuid": "A", "topic": "t"}))
    assert [x["meeting_uuid"] for x in mod.load_payloads([str(p)])] == ["A"]


def test_loads_a_bare_list_and_a_meetings_wrapper(mod, tmp_path: Path) -> None:
    lst = tmp_path / "list.json"
    lst.write_text(json.dumps([{"meeting_uuid": "A"}, {"meeting_uuid": "B"}]))
    wrapped = tmp_path / "wrapped.json"
    wrapped.write_text(json.dumps({"meetings": [{"meeting_uuid": "C"}]}))
    got = [x["meeting_uuid"] for x in mod.load_payloads([str(lst), str(wrapped)])]
    assert got == ["A", "B", "C"]


def test_payload_without_uuid_is_skipped_not_fatal(mod, tmp_path: Path) -> None:
    """A meeting with no uuid cannot be deduped, so it must never be sent."""
    p = tmp_path / "bad.json"
    p.write_text(json.dumps([{"topic": "no uuid"}, {"meeting_uuid": "A"}]))
    assert [x["meeting_uuid"] for x in mod.load_payloads([str(p)])] == ["A"]


def test_unparseable_file_is_skipped_not_fatal(mod, tmp_path: Path) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text("not json at all")
    good = tmp_path / "good.json"
    good.write_text(json.dumps({"meeting_uuid": "A"}))
    assert [x["meeting_uuid"] for x in mod.load_payloads([str(bad), str(good)])] == ["A"]


def test_exits_zero_when_alicia_is_down(mod, tmp_path: Path, monkeypatch, capsys) -> None:
    """launchd must not be told a stopped daemon is a failure."""
    p = tmp_path / "one.json"
    p.write_text(json.dumps({"meeting_uuid": "A", "topic": "t"}))
    monkeypatch.setattr(mod, "alicia_up", lambda base: False)
    monkeypatch.setattr(mod.sys, "argv", ["prog", str(p), "--execute"])

    def _never(*a, **k):
        raise AssertionError("must not POST when Alicia is down")

    monkeypatch.setattr(mod, "post_ingest", _never)
    assert mod.main() == 0
    assert "not reachable" in capsys.readouterr().out


def test_dry_run_wins_over_execute(mod, tmp_path: Path, monkeypatch) -> None:
    p = tmp_path / "one.json"
    p.write_text(json.dumps({"meeting_uuid": "A", "topic": "t"}))
    sent: dict = {}
    monkeypatch.setattr(mod, "alicia_up", lambda base: True)
    monkeypatch.setattr(mod, "post_ingest", lambda base, body, **k: sent.update(body) or {"ok": True})
    monkeypatch.setattr(mod.sys, "argv", ["prog", str(p), "--execute", "--dry-run"])
    assert mod.main() == 0
    assert sent["dry_run"] is True


def test_owners_and_mode_reach_the_request(mod, tmp_path: Path, monkeypatch) -> None:
    p = tmp_path / "one.json"
    p.write_text(json.dumps({"meeting_uuid": "A", "topic": "t"}))
    sent: dict = {}
    monkeypatch.setattr(mod, "alicia_up", lambda base: True)
    monkeypatch.setattr(mod, "post_ingest", lambda base, body, **k: sent.update(body) or {"ok": True})
    monkeypatch.setattr(
        mod.sys, "argv", ["prog", str(p), "--execute", "--owners", "justin, nicole", "--mode", "both"]
    )
    assert mod.main() == 0
    assert sent["owners"] == ["justin", "nicole"]
    assert sent["mode"] == "both"
    assert sent["dry_run"] is False


def test_nothing_to_ingest_is_not_an_error(mod, tmp_path: Path, monkeypatch, capsys) -> None:
    empty = tmp_path / "empty.json"
    empty.write_text("[]")
    monkeypatch.setattr(mod.sys, "argv", ["prog", str(empty), "--execute"])
    assert mod.main() == 0
    assert "nothing to ingest" in capsys.readouterr().out


# --- the launchd wrapper ---------------------------------------------------

WRAPPER = Path(__file__).resolve().parent.parent / "scripts" / "zoom-poll.sh"


def _embedded_python(text: str) -> str:
    """Pull the `python3 -c '...'` body out of a shell script."""
    marker = "python3 -c '"
    start = text.index(marker) + len(marker)
    return text[start : text.index("\n'", start)]


def test_wrapper_exists_and_is_executable() -> None:
    assert WRAPPER.is_file()
    assert WRAPPER.stat().st_mode & 0o111


def test_wrapper_embedded_python_compiles() -> None:
    """A quoting slip here fails at run time, in a log nobody reads.

    The first version used escaped quotes inside an f-string; inside a
    single-quoted shell string those reach python as a line continuation, so
    every run printed a SyntaxError instead of the result — while the job still
    exited 0 and looked healthy.
    """
    body = _embedded_python(WRAPPER.read_text())
    assert body.strip(), "no embedded python found — this test would be vacuous"
    compile(body, str(WRAPPER), "exec")
    assert '\\"' not in body, "escaped quotes do not survive the shell's single quotes"


def test_wrapper_reports_a_real_response(tmp_path: Path) -> None:
    """Feed the embedded reporter an actual poll response and read its summary."""
    import subprocess

    body = _embedded_python(WRAPPER.read_text())
    payload = {
        "created": 2,
        "skipped_duplicate": 1,
        "mine": 3,
        "summaries_listed": 609,
        "already_resolved": 12,
        "window": {"from": "2026-08-04", "to": "2026-08-11"},
        "errors": [],
        "results": [{"items": [{"text": "Justin Fowler: Ship the ingest runbook"}]}],
    }
    out = subprocess.run(
        ["python3", "-c", body], input=json.dumps(payload), capture_output=True, text=True, check=True
    )
    assert "created=2" in out.stdout
    assert "listed=609" in out.stdout
    assert "window=2026-08-04..2026-08-11" in out.stdout
    assert "Ship the ingest runbook" in out.stdout


def test_wrapper_reports_an_error_detail_without_crashing() -> None:
    import subprocess

    body = _embedded_python(WRAPPER.read_text())
    out = subprocess.run(
        ["python3", "-c", body],
        input=json.dumps({"detail": "missing Zoom credentials: ZOOM_CLIENT_SECRET"}),
        capture_output=True,
        text=True,
        check=True,
    )
    assert "warn:" in out.stdout
    assert "ZOOM_CLIENT_SECRET" in out.stdout
