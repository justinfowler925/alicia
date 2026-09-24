"""mflux draft → faces/looks stage → Anam enroll glue."""

from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from alicia import avatars
from alicia.config import AliciaCfg
from alicia.server import create_app


def test_draft_persona_hint_strips_utc_stamp():
    assert avatars._draft_persona_hint("analyst-20260806T151135Z.png") == "analyst"
    assert avatars._draft_persona_hint("marine.png") == "marine"


def test_list_mflux_drafts_parses_studio_json():
    payload = (
        '[{"name":"analyst-20260806T151135Z.png","bytes":10,"width":1152,'
        '"height":1152,"mtime":1,"anam_ok":true}]'
    )
    proc = MagicMock(returncode=0, stdout=payload, stderr="")
    with patch("alicia.avatars._ssh", return_value=proc) as ssh:
        drafts = avatars.list_mflux_drafts()
    assert ssh.called
    assert drafts[0]["name"] == "analyst-20260806T151135Z.png"
    assert drafts[0]["persona_hint"] == "analyst"
    assert drafts[0]["anam_ok"] is True


def test_stage_rejects_bad_names():
    assert avatars.stage_mflux_draft("../x.png", "analyst")["ok"] is False
    assert avatars.stage_mflux_draft("ok.png", "")["ok"] is False


def test_stage_copies_into_looks_path():
    proc = MagicMock(returncode=0, stdout="1152 1152\nstaged\n", stderr="")
    with patch("alicia.avatars._ssh", return_value=proc) as ssh:
        out = avatars.stage_mflux_draft(
            "analyst-20260806T151135Z.png", "Analyst!", "Professional"
        )
    assert out["ok"] is True
    assert out["face"] == "looks/analyst_professional.png"
    remote = ssh.call_args.args[0]
    assert "analyst-20260806T151135Z.png" in remote
    assert "looks/analyst_professional.png" in remote
    assert "mflux-out/faces" in remote
    assert "anam-avatar-chatbot/faces" in remote
    assert "Projects/anam-avatar-chatbot" not in remote


def test_avatar_state_includes_drafts():
    cfg = AliciaCfg(watchdog_enabled=False)
    with patch("alicia.server.AtlasClient") as cls, \
         patch("alicia.avatars.studio_faces", return_value=[]), \
         patch("alicia.avatars.studio_avatars", return_value={"avatars": []}), \
         patch("alicia.avatars.vercel_env", return_value={}), \
         patch("alicia.avatars.list_mflux_drafts", return_value=[
             {"name": "a.png", "persona_hint": "a", "anam_ok": True,
              "width": 1152, "height": 1152, "bytes": 1, "mtime": 1}
         ]):
        cls.return_value = MagicMock()
        app = create_app(cfg, start_watchdog=False)
        r = TestClient(app).get("/api/avatar")
    assert r.status_code == 200
    assert r.json()["drafts"][0]["name"] == "a.png"


def test_stage_endpoint_can_chain_apply():
    cfg = AliciaCfg(watchdog_enabled=False)
    staged = {"ok": True, "face": "looks/analyst_professional.png", "draft": "a.png"}
    applied = {"ok": True, "avatar_id": "av-1", "steps": [{"step": "enroll", "ok": True, "detail": "av-1"}]}
    with patch("alicia.server.AtlasClient") as cls, \
         patch("alicia.avatars.stage_mflux_draft", return_value=staged) as stage, \
         patch("alicia.avatars.apply_config", return_value=applied) as apply:
        cls.return_value = MagicMock()
        app = create_app(cfg, start_watchdog=False)
        r = TestClient(app).post("/api/avatar/stage", json={
            "draft": "a.png", "persona": "analyst", "look": "professional",
            "enroll": True, "replace_id": "old",
        })
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["face"] == "looks/analyst_professional.png"
    assert body["avatar_id"] == "av-1"
    stage.assert_called_once()
    apply.assert_called_once()
