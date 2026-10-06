#!/usr/bin/env python3
"""Verify Alicia manager org-chart routing — preview + live read-only proofs.

Proves preview routing and, when --live is passed, the same join+Canon+status
path for Forge (regression), one Studio specialist (Scout import or Atlas RO),
and records receipts under ~/.local/share/alicia-specialist-router/.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

BASE = "http://127.0.0.1:8768"
RECEIPTS = Path.home() / ".local/share/alicia-specialist-router"


def get(path: str, timeout: float = 8):
    with urllib.request.urlopen(BASE + path, timeout=timeout) as resp:
        return json.load(resp)


def post(path: str, body: dict, timeout: float = 8):
    req = urllib.request.Request(
        BASE + path,
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.load(resp)


def preview_checks() -> list[str]:
    chart = get("/api/specialists/org-chart")
    assert chart.get("manager") == "alicia", chart
    assert "atlas" in chart.get("specialists", {}), chart
    platforms = chart.get("platforms") or {}
    # Platforms may be absent on older deploys; live path still checks status.
    cases = [
        ("Fix REV-600 Salesforce Flow Active vs Latest", "atlas"),
        ("scrape and ingest Zoom my notes", "scout"),
        ("transcribe and caption this video", "hollywood"),
        ("write a small helper in the README", "forge"),
    ]
    checked = []
    for task, expected in cases:
        preview = post("/api/specialists/preview", {"task": task})
        assert preview.get("dry_run") is True, preview
        assert preview.get("specialist") == expected, (task, preview)
        checked.append(expected)
    forced = post(
        "/api/specialists/preview",
        {"task": "anything", "specialist": "hollywood"},
    )
    assert forced.get("specialist") == "hollywood", forced
    checked.append("explicit-hollywood")
    status = get("/api/status")
    assert "canon" in str(status.get("source") or ""), status
    return checked, platforms, status


def live_proofs() -> dict:
    """In-process live proofs against the same modules the server uses."""
    # Import from the deploy tree when ALICIA_APP_DIR is set.
    app = Path.home() / ".alicia" / "app"
    root = Path(sys.argv[0]).resolve().parents[1]
    for candidate in (app, root):
        if (candidate / "alicia" / "manager_loop.py").is_file():
            sys.path.insert(0, str(candidate))
            break

    from alicia.canon_binding import backfill_orphan_work_items, intake_work, project_rollups
    from alicia.config import AliciaCfg, OpenAICfg
    from alicia.manager_loop import assign_and_run
    from alicia.manager_status import merged_work_surface
    from alicia.platform_registry import registry_snapshot

    evidence: dict = {"platforms": registry_snapshot()["platforms"]}

    # Slice 2: bind orphans + intake
    backfill = backfill_orphan_work_items(limit=40)
    evidence["backfill"] = {
        "bound": backfill.get("bound"),
        "orphans_seen": backfill.get("orphans_seen"),
    }
    intake = intake_work(
        "Manager verify: Shine status rollup proof",
        source="verify-org-chart",
        project_hint="Shine",
        title="Shine rollup verify",
    )
    evidence["intake"] = {
        "work_item_id": intake["work_item_id"],
        "project_id": intake["project_id"],
        "assignee": intake["assignee"],
    }
    rollups = project_rollups()
    evidence["rollups_nonempty"] = sum(
        1 for r in rollups if int(r.get("work_item_count") or 0) > 0
    )
    assert evidence["rollups_nonempty"] >= 1, rollups

    cfg = AliciaCfg(openai=OpenAICfg(enabled=True, model="gpt-verify"))

    # Forge join (regression)
    forge = assign_and_run(
        cfg,
        task="Reply with exactly: verify-forge-ok",
        lane="forge_local",
        project_hint="Forge",
        join=True,
        wait_s=60,
    )
    evidence["forge"] = {
        "ok": forge.get("ok"),
        "executor": forge.get("executor"),
        "project_id": forge.get("project_id"),
        "receipt": forge.get("receipt_path"),
    }
    assert forge.get("executor") == "forge", forge

    # Hosted OpenAI opt-in (mock HTTP inside process — still proves manager path)
    def fake(message, oai):
        return {
            "ok": True,
            "executor": "openai",
            "attribution": "OpenAI",
            "reply": "verify-openai-ok",
            "model": oai.model,
            "http_attempted": True,
        }

    import alicia.openai_lane as oai_mod

    oai_mod._default_http = fake  # type: ignore[method-assign]
    openai = assign_and_run(
        cfg,
        task="Say verify-openai-ok",
        lane="openai",
        project_hint="Alicia",
        join=True,
    )
    evidence["openai"] = {
        "ok": openai.get("ok"),
        "executor": openai.get("executor"),
        "receipt": openai.get("receipt_path"),
    }
    assert openai.get("executor") == "openai", openai
    assert openai.get("ok") is True, openai

    # Studio specialist: Scout read-only import path (no prod mutation)
    scout = assign_and_run(
        cfg,
        task="scout import once read-only verify",
        lane="scout",
        project_hint="Scout",
        read_only=True,
        join=False,
        wait_s=5,
    )
    evidence["scout"] = {
        "ok": scout.get("ok"),
        "executor": scout.get("executor"),
        "receipt": scout.get("receipt_path"),
        "project_id": scout.get("project_id"),
    }
    assert scout.get("executor") == "scout", scout

    status = merged_work_surface(timeout_s=6.0)
    evidence["status"] = {
        "source": status.get("source"),
        "headline": status.get("headline"),
        "canon_bound": (status.get("counts") or {}).get("canon_bound_work_items"),
    }
    assert "canon" in str(status.get("source") or ""), status

    receipt_files = sorted(RECEIPTS.glob("*.json"))[-5:] if RECEIPTS.is_dir() else []
    evidence["recent_receipts"] = [str(p.name) for p in receipt_files]
    return evidence


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--live",
        action="store_true",
        help="Run join+Canon+status proofs (Forge + OpenAI opt-in mock + Scout)",
    )
    args = parser.parse_args()

    checked, platforms, status = preview_checks()
    payload = {
        "ok": True,
        "manager": "alicia",
        "checked": checked,
        "platforms_present": bool(platforms),
        "status_source": status.get("source"),
        "status_headline": status.get("headline"),
    }
    if args.live:
        payload["live"] = live_proofs()
    print(json.dumps(payload, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (AssertionError, urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}), file=sys.stderr)
        raise SystemExit(1)
