#!/usr/bin/env python3
"""Verify Alicia manager org-chart routing on Studio loopback."""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8768"


def get(path: str):
    with urllib.request.urlopen(BASE + path, timeout=8) as resp:
        return json.load(resp)


def post(path: str, body: dict):
    req = urllib.request.Request(
        BASE + path,
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=8) as resp:
        return json.load(resp)


def main() -> int:
    chart = get("/api/specialists/org-chart")
    assert chart.get("manager") == "alicia", chart
    assert "atlas" in chart.get("specialists", {}), chart

    cases = [
        ("Fix REV-600 Salesforce Flow Active vs Latest", "atlas"),
        ("scrape and ingest Zoom my notes", "scout"),
        ("transcribe and caption this video", "hollywood"),
        ("write a small helper in the README", "forge"),
    ]
    for task, expected in cases:
        preview = post("/api/specialists/preview", {"task": task})
        assert preview.get("dry_run") is True, preview
        assert preview.get("specialist") == expected, (task, preview)

    # Explicit override
    forced = post(
        "/api/specialists/preview",
        {"task": "anything", "specialist": "hollywood"},
    )
    assert forced.get("specialist") == "hollywood", forced

    print(
        json.dumps(
            {
                "ok": True,
                "manager": chart["manager"],
                "checked": [c[1] for c in cases] + ["explicit-hollywood"],
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (AssertionError, urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}), file=sys.stderr)
        raise SystemExit(1)
