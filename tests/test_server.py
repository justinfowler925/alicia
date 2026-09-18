"""Brutus laptop HTTP face smoke."""

import re
import time
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from brutus import focus
from brutus.config import BrutusCfg, LocalLLMCfg
from brutus.memory import MemoryStore
from brutus.server import create_app
from brutus.watchdog import Watchdog


def test_healthz_reports_openai_credential_from_actor_process(monkeypatch):
    cfg = BrutusCfg(watchdog_enabled=False)
    with patch("brutus.server.AtlasClient") as cls:
        cls.return_value = MagicMock()
        app = create_app(cfg, start_watchdog=False)
        client = TestClient(app)

        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY_LEGACY", raising=False)
        brain = client.get("/api/healthz").json()["brain"]
        assert brain["openai_credential_loaded"] is False

        monkeypatch.setenv("OPENAI_API_KEY", "test-key")
        brain = client.get("/api/healthz").json()["brain"]
        assert brain["openai_credential_loaded"] is True


def test_supervisor_endpoint_forwards_force_and_returns_structured_snapshot():
    cfg = BrutusCfg(watchdog_enabled=False)
    with patch("brutus.server.AtlasClient") as cls:
        cls.return_value = MagicMock()
        app = create_app(cfg, start_watchdog=False)
        expected = {"sessions": [], "counts": {"total": 0}, "assessment": None}
        app.state.supervisor.snapshot = MagicMock(return_value=expected)
        response = TestClient(app).get("/api/supervisor?force=true")
    assert response.status_code == 200
    assert response.json() == expected
    app.state.supervisor.snapshot.assert_called_once_with(force=True)


def test_focus_endpoint():
    cfg = BrutusCfg(watchdog_enabled=False, linear_workspace="clearspeed")
    surface = {"headline": "1 in review", "needs_you": [{"ticket": "REV-9", "title": "Gate"}], "working": [], "queued": [], "stuck": [], "counts": {}, "actions": []}
    with patch("brutus.server.linear_work_surface", return_value=surface), patch("brutus.server.AtlasClient") as cls:
        inst = MagicMock()
        inst.status.return_value = {
            "blocked_justin": [
                {
                    "id": "g1",
                    "external_id": "REV-9",
                    "title": "Gate",
                    "blocker": "x",
                    "evidence": "job_ledger:x",
                    "updated_at": "2026-07-28T01:00:00+00:00",
                }
            ],
            "in_flight": [],
            "frontier_pending": [],
            "cursor_pending": [],
            "counts": {},
        }
        cls.return_value = inst
        app = create_app(cfg, start_watchdog=False)
        c = TestClient(app)
        out = c.get("/api/focus")
        assert out.status_code == 200
        body = out.json()
        assert body["atlas_ignored"] is True
        assert body["needs_you"][0]["ticket"] == "REV-9"
        inst.status.assert_not_called()


def test_chat_uses_resolve():
    cfg = BrutusCfg(local_llm=LocalLLMCfg(enabled=True), watchdog_enabled=False)
    with patch("brutus.server.AtlasClient") as cls:
        cls.return_value = MagicMock()
        app = create_app(cfg, start_watchdog=False)
        client = TestClient(app)
        with patch(
            "brutus.server.resolve_chat_reply",
            return_value=("hello from laptop", {"atlas5_busy": True}),
        ) as resolve:
            out = client.post(
                "/api/chat",
                json={
                    "message": "hi",
                    "conversation_id": "abc123",
                    "history": [{"role": "user", "content": "prior"}],
                },
            )
        assert out.status_code == 200
        body = out.json()
        assert body["reply"] == "hello from laptop"
        assert body["source"] == "brutus"
        assert body["conversation_id"] == "abc123"
        assert resolve.call_args.kwargs["history"] == [{"role": "user", "content": "prior"}]
        assert resolve.call_args.kwargs["memory"] is not None


def test_agents_api_patch_and_promote_notes(tmp_path, monkeypatch):
    import json

    from brutus import agent_sessions as ag
    from brutus.memory import MemoryStore

    cursor = tmp_path / "cursor"
    sid = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    d = cursor / "Users-justinfowler-Projects-brutus" / "agent-transcripts" / sid
    d.mkdir(parents=True)
    (d / f"{sid}.jsonl").write_text(
        json.dumps(
            {
                "role": "user",
                "message": {"content": [{"type": "text", "text": "<user_query>ship it</user_query>"}]},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(ag, "CURSOR_PROJECTS", cursor)
    monkeypatch.setattr(ag, "CLAUDE_PROJECTS", tmp_path / "cp")
    monkeypatch.setattr(ag, "CLAUDE_SESSIONS", tmp_path / "cs")
    ag._CACHE["data"] = []
    ag._CACHE["at"] = 0.0

    cfg = BrutusCfg(watchdog_enabled=False)
    with patch("brutus.server.AtlasClient") as cls:
        cls.return_value = MagicMock()
        with patch("brutus.server.MemoryStore", return_value=MemoryStore(tmp_path / "mem.sqlite")):
            with patch("brutus.server.TodoStore") as ts:
                from brutus.todos import TodoStore

                ts.side_effect = lambda *a, **k: TodoStore(tmp_path / "todos.sqlite")
                app = create_app(cfg, start_watchdog=False)
                c = TestClient(app)
                listed = c.get("/api/agents")
                assert listed.status_code == 200
                agents = listed.json()["agents"]
                assert any(a["id"] == f"cursor:{sid}" for a in agents)
                patched = c.patch(f"/api/agents/cursor:{sid}", json={"kept": True})
                assert patched.status_code == 200
                assert patched.json()["pinned"] is True
                promo = c.post(f"/api/agents/cursor:{sid}/promote", json={"to": "notes"})
                assert promo.status_code == 200
                assert promo.json()["to"] == "notes"
                assert promo.json()["todo_id"]


def test_agents_api_preserves_native_runtime_status(tmp_path):
    row = {
        "id": "codex:local:native-task-id",
        "surface": "codex",
        "session_id": "native-task-id",
        "host_id": "local",
        "title": "Runtime task",
        "cwd": "/Users/justinfowler/Projects/brutus",
        "project": "brutus",
        "mtime": time.time(),
        "age": "0m ago",
        "live": True,
        "state": "running",
        "status_source": "lifecycle_hook",
        "status_observed_at": time.time(),
        "path": "",
        "pid": None,
    }
    cfg = BrutusCfg(watchdog_enabled=False)
    with patch("brutus.server.AtlasClient") as atlas, patch(
        "brutus.server.scan_agent_sessions", return_value=[row]
    ), patch(
        "brutus.server.MemoryStore",
        return_value=MemoryStore(tmp_path / "mem.sqlite"),
    ):
        atlas.return_value = MagicMock()
        response = TestClient(create_app(cfg, start_watchdog=False)).get("/api/agents?surface=codex")

    assert response.status_code == 200
    agent = response.json()["agents"][0]
    assert agent["session_id"] == "native-task-id"
    assert agent["state"] == "running"
    assert agent["live"] is True
    assert agent["status_source"] == "lifecycle_hook"
    assert agent["status_observed_at"] > 0


def test_nucleus_api_and_project_overlay_share_exact_project_id(tmp_path, monkeypatch):
    monkeypatch.setenv("BRUTUS_STATE_DIR", str(tmp_path / "state"))
    snapshot = {
        "summary": {"projects": 1},
        "source_status": {"linear": {"state": "fresh", "count": 1}},
        "projects": [{"id": "github.com/o/r", "name": "r"}],
    }
    cfg = BrutusCfg(watchdog_enabled=False)
    with patch("brutus.server.AtlasClient") as cls, patch(
        "brutus.server.build_nucleus_snapshot", return_value=snapshot
    ):
        cls.return_value = MagicMock()
        with TestClient(create_app(cfg, start_watchdog=False)) as client:
            assert client.get("/api/nucleus").json()["projects"][0]["id"] == "github.com/o/r"
            response = client.patch(
                "/api/nucleus/projects/github.com/o/r",
                json={"pinned": True, "objective": "Ship Nucleus"},
            )

    assert response.status_code == 200
    assert response.json()["project_id"] == "github.com/o/r"
    assert response.json()["overlay"]["pinned"] is True
    assert response.json()["source_records_changed"] is False


def test_session_ideas_build_plan_markers():
    """DoD markers from docs/SESSION_IDEAS_BUILD_PLAN.md (HTML smoke, no JS)."""
    cfg = BrutusCfg(watchdog_enabled=False)
    with patch("brutus.server.AtlasClient") as cls:
        cls.return_value = MagicMock()
        app = create_app(cfg, start_watchdog=False)
        html = TestClient(app).get("/session").text
        # The voice surface is primary. Queue controls remain available under a
        # deliberate workspace-tools disclosure, never as a competing board.
        assert 'aria-label="Workspace tools"' in html
        # There is no separate Ledger region any more. Ledger rows and personal
        # captures sit in the same staged queue, told apart by a source badge —
        # two boards for one pipeline was the thing that made neither readable.
        assert 'aria-label="Ledger"' not in html
        assert 'id="ideas-list"' in html
        assert 'id="ideas-add"' in html
        assert 'data-cite="spectrum-ai-chat"' in html
        assert 'data-testid="voice-stage"' in html
        assert 'data-testid="voice-control"' in html
        assert 'data-testid="supervisor-guidance"' in html
        assert 'data-testid="work-tray"' in html
        assert 'id="send" class="primary">Send</button>' in html
        assert '<button type="submit">Capture</button>' in html
        assert "Session slots" in html
        assert "Message or capture" in html
        assert 'aria-label="Status"' not in html
        assert ">Board<" not in html
        assert 'aria-label="Captured"' not in html
        css = TestClient(app).get("/static/session.css").text
        assert ".topbar .title a" in css
        assert "text-decoration: underline" in css
        assert ".voice-shell:has(.work-tray[open])" in css
        assert ".work-tray[open] .queue-board" in css
        js = TestClient(app).get("/static/session.js").text
        assert "initIdeas" in js
        assert "ideaDelete" in js
        assert "events?workspace=true" in js
        assert 'case "idea":' in js
        assert "/api/supervisor" in js
        assert 'case "supervisor":' in js
        assert "renderSupervisor(event)" in js
        assert js.count("new EventSource(") == 1


def test_session_ideas_wave2_markers():
    """DoD markers from docs/SESSION_IDEAS_WAVE2_BUILD_PLAN.md (HTML + JS smoke)."""
    cfg = BrutusCfg(watchdog_enabled=False)
    with patch("brutus.server.AtlasClient") as cls:
        cls.return_value = MagicMock()
        app = create_app(cfg, start_watchdog=False)
        client = TestClient(app)
        html = client.get("/session").text
        assert 'id="ideas-search"' in html
        assert 'data-filter="active"' in html
        assert "Active" in html
        assert 'class="ops-link"' not in html
        assert 'href="/"' not in html
        assert 'class="ideas-filters"' in html
        assert 'id="ledger-detail"' in html
        assert 'id="ledger-detail-link"' in html
        assert 'id="ledger-detail-actions"' in html
        assert 'id="ideas-sort"' in html
        js = client.get("/static/session.js").text
        assert "ideaSaveEdit" in js
        assert "ideaPromote" in js
        assert "ideaStage" in js
        assert "showLedgerDetail" in js
        assert "ledgerAnswer" in js
        assert "ledgerDecide" in js
        assert "sortCards" in js
        assert "Couldn't reach Linear" in js
        assert "/promote" in js
        assert "isMeetingDump" in js
        assert "filterCards" in js
        assert "card-actions" in js
        assert 'textContent = "Edit"' in js
        assert 'textContent = "Delete"' in js
        # Paging says how many are behind it ("12 more"), not just "More" — the
        # count is the reason to click.
        assert "qcol-more" in js



def test_session_shine_audit_markers():
    """DoD markers from docs/SESSION_UI_SHINE_BUILD_PLAN.md Waves A–B + D."""
    cfg = BrutusCfg(watchdog_enabled=False)
    with patch("brutus.server.AtlasClient") as cls:
        cls.return_value = MagicMock()
        app = create_app(cfg, start_watchdog=False)
        client = TestClient(app)
        html = client.get("/session").text
        assert 'id="conversation-empty"' in html
        assert ">offline<" in html
        assert 'aria-label="Start a new session"' in html
        assert 'aria-label="Toggle spoken replies"' in html
        css = client.get("/static/session.css").text
        assert "min-height: 2.5rem" in css
        assert "button.danger" in css
        assert ".ideas-retry" in css
        js = client.get("/static/session.js").text
        assert "setLiveConnected" in js
        # Cards are real buttons now, not divs painted with role="button". The
        # negative is the assertion worth keeping: a real button brings keyboard
        # activation and focus for free, and hand-rolling that is where the old
        # cards lost Enter and Space.
        assert 'role", "button"' not in js
        assert "card-open" in js
        # Each stage says what belongs in it while empty, instead of one blank
        # "nothing here" for the whole board.
        assert "STAGE_BLURB" in js
        assert "Nothing matching" in js
        assert "Start a new session?" in js
        assert "loadError" in js
        assert 'className = "ideas-retry"' in js


def test_shine_tokens_include_light_theme():
    css = (
        __import__("pathlib").Path(__file__).resolve().parents[1]
        / "brutus"
        / "static"
        / "shine-tokens.css"
    ).read_text()
    assert '[data-theme="light"]' in css
    assert "--shine-color-bg: var(--shine-color-stone-50)" in css


def test_gate_reason_is_not_silently_truncated():
    """A decide card must never show a half-sentence with no way to read the rest."""
    long_q = (
        "Was the deactivation of the trigger in production a deliberate business "
        "decision taken by the revenue operations team after the incident review, "
        "or was it an accidental side effect of the sandbox refresh that nobody "
        "noticed until the weekly audit flagged it as missing from the org?"
    ) * 2
    label, why = focus.human_reason(long_q)
    assert label == "Waiting on an answer"
    assert why == long_q.strip(), "human_reason must return the reason untruncated"
    shown = focus.clip(why)
    assert shown.endswith("…"), "a clipped reason must mark the cut"
    assert len(shown) < len(why)
    assert not shown[:-1].endswith(" "), "clip should not leave a dangling space"


def test_speak_requires_voice_config():
    cfg = BrutusCfg(watchdog_enabled=False)
    with patch("brutus.server.AtlasClient") as cls:
        cls.return_value = MagicMock()
        app = create_app(cfg, start_watchdog=False)
        out = TestClient(app).post("/api/speak", json={"text": "hi"})
        assert out.status_code == 503


def test_speak_returns_audio_when_configured():
    from brutus.config import VoiceCfg

    cfg = BrutusCfg(
        watchdog_enabled=False,
        voice=VoiceCfg(enabled=True, elevenlabs_api_key="fake-key"),
    )
    with patch("brutus.server.AtlasClient") as cls:
        cls.return_value = MagicMock()
        app = create_app(cfg, start_watchdog=False)
        with patch("brutus.server.voice_speak", return_value=b"ID3fakeaudio"):
            out = TestClient(app).post("/api/speak", json={"text": "hello"})
        assert out.status_code == 200
        assert out.headers["content-type"].startswith("audio/mpeg")
        assert out.content == b"ID3fakeaudio"


def test_speak_refuses_a_uuid_doorbell():
    from brutus.config import VoiceCfg

    cfg = BrutusCfg(
        watchdog_enabled=False,
        voice=VoiceCfg(enabled=True, elevenlabs_api_key="fake-key"),
    )
    with patch("brutus.server.AtlasClient") as cls:
        cls.return_value = MagicMock()
        app = create_app(cfg, start_watchdog=False)
        with patch("brutus.server.voice_speak", return_value=b"ID3fakeaudio") as speak:
            out = TestClient(app).post(
                "/api/speak",
                json={"text": "6d0b8f2a-7e2b-4e4f-b19c-6dc5f6fd80fe needs you."},
            )
        assert out.status_code == 400
        speak.assert_not_called()


def test_transcribe_returns_text_when_whisper_available():
    from brutus.config import VoiceCfg

    cfg = BrutusCfg(watchdog_enabled=False, voice=VoiceCfg(enabled=True))
    with patch("brutus.server.AtlasClient") as cls:
        cls.return_value = MagicMock()
        app = create_app(cfg, start_watchdog=False)
        with patch("brutus.server.HAS_WHISPER", True), patch(
            "brutus.server.voice_transcribe", return_value="what is next"
        ):
            out = TestClient(app).post(
                "/api/transcribe",
                files={"file": ("clip.webm", b"fakeaudio", "audio/webm")},
            )
        assert out.status_code == 200
        assert out.json()["text"] == "what is next"


def test_status_degraded_when_linear_down_without_atlas_fallback():
    cfg = BrutusCfg(local_llm=LocalLLMCfg(enabled=False), watchdog_enabled=False)
    with patch("brutus.server.linear_work_surface", side_effect=ConnectionError("down")), patch("brutus.server.AtlasClient") as cls:
        inst = MagicMock()
        inst.status.side_effect = ConnectionError("down")
        cls.return_value = inst
        app = create_app(cfg, start_watchdog=False)
        c = TestClient(app)
        st = c.get("/api/status")
        assert st.status_code == 200
        body = st.json()
        assert body["atlas_ignored"] is True
        assert body["source"] == "linear_direct"
        assert body["error"] == "down"
        inst.status.assert_not_called()
        assert "digest_markdown" not in body


def test_watchdog_endpoint_reports_the_local_probe():
    """The endpoint reports only the local standalone probe."""
    cfg = BrutusCfg(watchdog_enabled=True, watchdog_interval_s=60, stale_inflight_minutes=45)
    client = MagicMock()

    wd = Watchdog(cfg, client)
    snap = wd.tick_once()
    assert "gates_waiting" not in snap
    assert "last_counts" not in snap
    assert snap["scheduler"] == "standalone-local-only"
    client.reconcile.assert_not_called()
    client.dispatch_tick.assert_not_called()

    with patch("brutus.server.AtlasClient") as cls:
        cls.return_value = client
        app = create_app(cfg, start_watchdog=False)
        app.state.watchdog = wd
        c = TestClient(app)
        out = c.get("/api/watchdog")
        assert out.status_code == 200
        assert out.json()["local_llm"] is not None


def test_watchdog_tick_endpoint_drives_no_atlas_work():
    """POST /api/watchdog/tick is the UI's "check now" button.

    It used to force a reconcile+dispatch from the laptop. It must not any
    more, or the button quietly reinstates the second scheduler.
    """
    cfg = BrutusCfg(watchdog_enabled=True)
    client = MagicMock()
    with patch("brutus.server.AtlasClient") as cls:
        cls.return_value = client
        app = create_app(cfg, start_watchdog=False)
        c = TestClient(app)
        client.reset_mock()
        out = c.post("/api/watchdog/tick")
    assert out.status_code == 200
    client.status.assert_not_called()
    client.reconcile.assert_not_called()
    client.dispatch_tick.assert_not_called()


def test_steer_retriage_steers_each_ticket_and_reports_failures():
    cfg = BrutusCfg(watchdog_enabled=False, local_llm=LocalLLMCfg(enabled=False))
    with patch("brutus.server.AtlasClient") as cls:
        inst = MagicMock()

        def steer(tid, body, **_k):
            assert "schema-grounding fix" in body  # the canned nudge, not empty
            if tid == "REV-292":
                raise RuntimeError("atlas5 down")
            return {"ok": True, "resumed": True}

        inst.answer_steering.side_effect = steer
        cls.return_value = inst
        app = create_app(cfg, start_watchdog=False)
        c = TestClient(app)

        r = c.post("/api/steer_retriage", json={"ticket_ids": ["REV-291", "REV-292"]})
        assert r.status_code == 200
        body = r.json()
        assert body["steered"] == ["REV-291"]
        assert body["count"] == 1
        assert body["ok"] is False
        assert body["failed"][0]["ticket_id"] == "REV-292"

        assert c.post("/api/steer_retriage", json={"ticket_ids": []}).status_code == 400


def test_version_reads_explicit_deploy_manifest(tmp_path, monkeypatch):
    manifest = tmp_path / "deploy.json"
    manifest.write_text(
        '{"sha":"abc123","deployed_at":"2026-09-07T00:00:00Z","config_sha256":"cfg"}'
    )
    monkeypatch.setenv("BRUTUS_DEPLOY_MANIFEST", str(manifest))
    cfg = BrutusCfg(watchdog_enabled=False)
    with patch("brutus.server.AtlasClient") as atlas:
        atlas.return_value = MagicMock()
        payload = TestClient(create_app(cfg, start_watchdog=False)).get("/version").json()
    assert payload["sha"] == "abc123"
