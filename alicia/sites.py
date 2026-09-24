"""Justin's sites — every live app and console he owns, with liveness.

This is the registry behind the hub's "Chatbots" page and the quick links. Add
a site by appending one entry to SITES: name, url, what it is, and a category
("chatbot" shows on the Chatbots page, "console" shows as a quick link).
Health is a GET with a short timeout, cached ~60s, checked concurrently.

URLs verified live 2026-07-29. The Voicemaker URL is tailnet-only — it works
from Justin's machines, nowhere else, which is the point.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import httpx

SITES: list[dict[str, str]] = [
    # --- chatbots & demos -------------------------------------------------
    {
        "name": "Voicemaker Studio",
        "url": "https://justins-mac-studio.tailbaa084.ts.net:8790/",
        "what": "TTS demo maker — publishes into the demo library",
        "category": "chatbot",
    },
    {
        "name": "Clearspeed Demos",
        "url": "https://www.clearspeeddemos.com/",
        "what": "Public demo library (Affinity Insurance / Bank, voice flows)",
        "category": "chatbot",
    },
    {
        "name": "FNOL avatar (HeyGen)",
        "url": "",
        "what": "Claims-interview avatar — runs from ~/Projects/anam-avatar-chatbot",
        "category": "chatbot",
        "repo": "anam-avatar-chatbot",
    },
    {
        "name": "Avatar Lab",
        "url": "",
        "what": "Self-hosted LiveKit avatar experiments — ~/Projects/avatar-lab",
        "category": "chatbot",
        "repo": "avatar-lab",
    },
    # --- consoles ---------------------------------------------------------
    {
        "name": "Atlas5 console",
        "url": "http://127.0.0.1:8766/",
        "what": "The Salesforce worker's own full dashboard",
        "category": "console",
    },
    {
        "name": "Conductor board",
        "url": "http://127.0.0.1:8767/board",
        "what": "Atlas6 thread ledger, raw view",
        "category": "console",
    },
]

_CACHE: dict[str, Any] = {"at": 0.0, "data": []}
_TTL_S = 60.0


async def _check(client: httpx.AsyncClient, site: dict[str, str]) -> dict[str, Any]:
    out: dict[str, Any] = {**site}
    if not site.get("url"):
        out["live"] = None  # local project, nothing to ping
        return out
    try:
        r = await client.get(site["url"])
        out["live"] = r.status_code < 500
    except Exception:
        out["live"] = False
    return out


async def check_sites(*, force: bool = False) -> list[dict[str, Any]]:
    now = time.time()
    if not force and _CACHE["data"] and now - _CACHE["at"] < _TTL_S:
        return _CACHE["data"]
    async with httpx.AsyncClient(timeout=5.0, verify=False, follow_redirects=True) as client:
        results = await asyncio.gather(*(_check(client, s) for s in SITES))
    _CACHE["at"], _CACHE["data"] = now, list(results)
    return _CACHE["data"]
