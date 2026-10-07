"""Nucleus Deal Desk nudge client for Alicia.

Calls the machine-authenticated CRO nudge API so Alicia can list open items,
draft an identical message for one/many/all-open, and send after Justin confirms.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any

import httpx

DEFAULT_NUCLEUS_URL = "https://nucleus.clearspeed.com"
SECRET_REF = "op://Atlas/CRO_BRIEF_MACHINE_SECRET/shared"


def _op_read(ref: str) -> str:
    helper = Path.home() / "fowler-brain" / "scripts" / "op-session.sh"
    if not helper.is_file():
        return ""
    try:
        out = subprocess.run(
            ["bash", str(helper), "read", ref],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return (out.stdout or "").strip()


def nucleus_base_url() -> str:
    return (
        os.environ.get("NUCLEUS_URL")
        or os.environ.get("NUCLEUS_APP_URL")
        or DEFAULT_NUCLEUS_URL
    ).rstrip("/")


def machine_secret() -> str:
    env = (os.environ.get("CRO_BRIEF_MACHINE_SECRET") or "").strip()
    if env:
        return env
    return _op_read(SECRET_REF)


def _headers() -> dict[str, str]:
    secret = machine_secret()
    if not secret:
        raise RuntimeError(
            "CRO_BRIEF_MACHINE_SECRET is not available (env or 1Password Atlas)"
        )
    return {
        "Authorization": f"Bearer {secret}",
        "Content-Type": "application/json",
        "User-Agent": "alicia-cro-nudge/1",
    }


def _request(method: str, path: str, *, json_body: dict[str, Any] | None = None) -> dict[str, Any]:
    url = f"{nucleus_base_url()}{path}"
    with httpx.Client(timeout=60.0, headers=_headers()) as client:
        response = client.request(method, url, json=json_body)
    try:
        payload = response.json()
    except ValueError:
        payload = {"error": response.text[:300] or f"HTTP {response.status_code}"}
    if not isinstance(payload, dict):
        payload = {"error": "unexpected response", "raw": payload}
    payload.setdefault("http_status", response.status_code)
    payload.setdefault("ok", response.is_success)
    if not response.is_success and "error" not in payload:
        payload["error"] = f"Nucleus returned HTTP {response.status_code}"
    return payload


def list_open_nudges() -> dict[str, Any]:
    """Return the open Deal Desk nudge queue (follow-through included)."""
    try:
        return _request("GET", "/api/cro/deal-nudge/machine")
    except RuntimeError as exc:
        return {"ok": False, "error": str(exc)}


def draft_nudge(
    *,
    opportunity_id: str = "",
    opportunity_ids: list[str] | None = None,
    all_open: bool = False,
    message: str = "",
) -> dict[str, Any]:
    """Preview recipients + identical draft. Nothing is delivered."""
    body: dict[str, Any] = {"action": "preview"}
    if all_open:
        body["allOpen"] = True
    elif opportunity_ids:
        body["opportunityIds"] = list(opportunity_ids)
    elif opportunity_id.strip():
        body["opportunityId"] = opportunity_id.strip()
    else:
        return {
            "ok": False,
            "error": "Provide opportunity_id, opportunity_ids, or all_open=true",
        }
    if message.strip():
        body["message"] = message.strip()
    try:
        return _request("POST", "/api/cro/deal-nudge/machine", json_body=body)
    except RuntimeError as exc:
        return {"ok": False, "error": str(exc)}


def send_nudge(
    *,
    opportunity_id: str = "",
    opportunity_ids: list[str] | None = None,
    all_open: bool = False,
    message: str = "",
) -> dict[str, Any]:
    """Deliver one identical message. Call only after Justin confirms."""
    if len((message or "").strip()) < 20:
        return {
            "ok": False,
            "error": "Compose and edit the nudge before sending (message must be at least 20 characters).",
        }
    body: dict[str, Any] = {"action": "send", "message": message.strip()}
    if all_open:
        body["allOpen"] = True
    elif opportunity_ids:
        body["opportunityIds"] = list(opportunity_ids)
    elif opportunity_id.strip():
        body["opportunityId"] = opportunity_id.strip()
    else:
        return {
            "ok": False,
            "error": "Provide opportunity_id, opportunity_ids, or all_open=true",
        }
    try:
        return _request("POST", "/api/cro/deal-nudge/machine", json_body=body)
    except RuntimeError as exc:
        return {"ok": False, "error": str(exc)}
