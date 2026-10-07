"""Unit coverage for Nucleus Deal Desk nudge client + gate wiring."""

from __future__ import annotations

from alicia import cro_nudge
from alicia.brain import BRAIN_READS, PROPOSABLE
from alicia.forge_tools import CAPABILITIES, skill_home
from alicia.gate import GATED, describe


def test_brain_exposes_list_draft_and_gated_send():
    assert "cro_nudge_list" in BRAIN_READS
    assert "cro_nudge_draft" in BRAIN_READS
    assert "cro_nudge_send" in PROPOSABLE
    assert "cro_nudge_send" in GATED
    assert "cro_nudge_send" not in BRAIN_READS


def test_gate_describes_bulk_and_all_open():
    summary, spoken = describe(
        "cro_nudge_send",
        {"all_open": True, "message": "Please review your open pipeline items that need a forecast correction."},
    )
    assert "every open Deal Desk nudge" in summary
    assert "identical" in summary.casefold()
    assert spoken.endswith("?")

    summary, _ = describe(
        "cro_nudge_send",
        {
            "opportunity_ids": ["a", "b", "c"],
            "message": "Please review your open pipeline items that need a forecast correction.",
        },
    )
    assert "3 selected opportunities" in summary


def test_forge_capability_points_at_installed_skill(tmp_path, monkeypatch):
    assert CAPABILITIES["nucleus-nudge"]["kind"] == "skill"
    path = skill_home("nucleus-nudge")
    assert path.name == "SKILL.md"
    assert "nucleus-nudge" in str(path)


def test_draft_requires_target(monkeypatch):
    monkeypatch.setenv("CRO_BRIEF_MACHINE_SECRET", "test-secret")
    monkeypatch.setenv("NUCLEUS_URL", "https://nucleus.test")
    result = cro_nudge.draft_nudge()
    assert result["ok"] is False
    assert "opportunity" in result["error"].casefold() or "all_open" in result["error"].casefold()


def test_send_requires_message_length(monkeypatch):
    monkeypatch.setenv("CRO_BRIEF_MACHINE_SECRET", "test-secret")
    result = cro_nudge.send_nudge(all_open=True, message="too short")
    assert result["ok"] is False
    assert "20" in result["error"]


def test_client_posts_preview_body(monkeypatch):
    captured: dict = {}

    class FakeResponse:
        status_code = 200
        is_success = True

        def json(self):
            return {"ok": True, "action": "preview", "attempted": 2, "message": "x" * 40}

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def request(self, method, url, json=None):
            captured["method"] = method
            captured["url"] = url
            captured["json"] = json
            return FakeResponse()

    monkeypatch.setenv("CRO_BRIEF_MACHINE_SECRET", "test-secret")
    monkeypatch.setenv("NUCLEUS_URL", "https://nucleus.test")
    monkeypatch.setattr(cro_nudge.httpx, "Client", FakeClient)
    result = cro_nudge.draft_nudge(opportunity_ids=["a", "b"])
    assert result["ok"] is True
    assert captured["method"] == "POST"
    assert captured["url"].endswith("/api/cro/deal-nudge/machine")
    assert captured["json"]["action"] == "preview"
    assert captured["json"]["opportunityIds"] == ["a", "b"]
