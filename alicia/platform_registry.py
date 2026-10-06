"""Platform executor registry under Alicia — not Studio-only specialists.

Each lane has an assign path, join/poll contract, receipt schema, Canon Run
link, and an honest attribution label. Hosted lanes are Justin opt-in only.
"""

from __future__ import annotations

from typing import Any

# Canonical lane ids used in receipts, Canon Runs, and /api/status.
LANES: dict[str, dict[str, Any]] = {
    "forge_local": {
        "executor": "forge",
        "label": "Forge (local Gemma)",
        "role": "Resident Gemma via #forge / ask_forge",
        "gate": "local_default",
        "hosted": False,
        "earn_eligible": True,
        "earn_classes": ("forge_trivial",),
    },
    "cursor_local": {
        "executor": "cursor",
        "label": "Cursor (local SDK)",
        "role": "Local Cursor SDK on personal allowlist",
        "gate": "personal_roots",
        "hosted": False,
        "earn_eligible": False,
        "earn_classes": (),
    },
    "cursor_cloud": {
        "executor": "cursor_cloud",
        "label": "Cursor Cloud",
        "role": "Cloud Agents",
        "gate": "justin_opt_in",
        "hosted": True,
        "earn_eligible": False,
        "earn_classes": (),
    },
    "openai": {
        "executor": "openai",
        "label": "OpenAI / Codex",
        "role": "Hosted OpenAI lane",
        "gate": "justin_opt_in",
        "hosted": True,
        "earn_eligible": False,
        "earn_classes": (),
    },
    "claude": {
        "executor": "claude",
        "label": "Claude",
        "role": "Hosted Claude lane",
        "gate": "justin_opt_in",
        "hosted": True,
        "earn_eligible": False,
        "earn_classes": (),
    },
    "atlas": {
        "executor": "atlas",
        "label": "Atlas",
        "role": "Salesforce / RevOps Studio specialist",
        "gate": "route_specialist",
        "hosted": False,
        "earn_eligible": True,
        "earn_classes": ("atlas_read_only",),
    },
    "scout": {
        "executor": "scout",
        "label": "Scout",
        "role": "Research / scrape / ingest",
        "gate": "route_specialist",
        "hosted": False,
        "earn_eligible": True,
        "earn_classes": ("scout_import", "scout_read_only"),
    },
    "hollywood": {
        "executor": "hollywood",
        "label": "Hollywood",
        "role": "Media / speak-safe Studio",
        "gate": "route_specialist",
        "hosted": False,
        "earn_eligible": True,
        "earn_classes": ("hollywood_inspect", "hollywood_speak_safe"),
    },
}

# Values that must never appear as executor for hosted / non-Forge output.
FORGE_ONLY_MARKERS = frozenset(
    {
        "forge",
        "gemma",
        "forge_local",
        "/users/jfstudio/.local/share/atlas-models/gemma4-31b-it-4bit",
    }
)

ALIASES = {
    "forge": "forge_local",
    "gemma": "forge_local",
    "cursor": "cursor_local",
    "codex": "openai",
    "openai_codex": "openai",
}


def normalize_lane(lane: str | None) -> str | None:
    raw = (lane or "").strip().lower()
    if not raw:
        return None
    if raw in LANES:
        return raw
    return ALIASES.get(raw)


def lane_meta(lane: str) -> dict[str, Any]:
    key = normalize_lane(lane)
    if not key:
        raise ValueError(f"unknown platform lane: {lane!r}")
    return {"id": key, **LANES[key]}


def executor_for(lane: str) -> str:
    return str(lane_meta(lane)["executor"])


def assert_honest_attribution(payload: dict[str, Any], *, lane: str) -> None:
    """Fail CI / runtime if a receipt mislabels the real platform.

    Hard rule: never claim forge/Gemma for Cursor / OpenAI / Claude / cloud.
    Never claim Alicia 'thought this' for hosted output.
    """
    meta = lane_meta(lane)
    expected = str(meta["executor"])
    executor = str(payload.get("executor") or "").strip().lower()
    model = str(payload.get("model") or "").strip().lower()
    attribution = str(payload.get("attribution") or "").strip().lower()

    if not executor:
        raise ValueError(f"receipt for lane {lane!r} missing executor")
    if executor != expected:
        raise ValueError(
            f"lane {lane!r} executor must be {expected!r}, got {executor!r}"
        )

    if meta["hosted"] or expected in {"cursor", "cursor_cloud", "openai", "claude"}:
        if executor in FORGE_ONLY_MARKERS or "forge" in executor or "gemma" in executor:
            raise ValueError(
                f"hosted/non-Forge lane {lane!r} must not claim Forge/Gemma "
                f"(executor={executor!r})"
            )
        if any(bad in model for bad in ("forge", "gemma", "gemma4-31b")):
            raise ValueError(
                f"lane {lane!r} model must not look like Forge/Gemma: {model!r}"
            )
        if "alicia thought" in attribution or attribution == "alicia":
            raise ValueError(
                f"lane {lane!r} must not attribute hosted output to Alicia thinking"
            )

    if expected == "forge":
        if executor != "forge":
            raise ValueError(f"Forge lane executor must be 'forge', got {executor!r}")


def choose_lane(
    task: str,
    *,
    lane: str | None = None,
    repo_hint: str = "",
) -> tuple[str, str]:
    """Policy: local-first Q&A → Forge; personal code → Cursor; SF → Atlas; etc.

    Explicit lane wins when valid. Hosted lanes are never auto-chosen.
    """
    forced = normalize_lane(lane)
    if forced:
        if LANES[forced]["hosted"] and forced not in {"cursor_cloud", "openai", "claude"}:
            pass
        return forced, "explicit"

    text = (task or "").lower()
    hint = (repo_hint or "").lower()

    if any(k in text for k in ("salesforce", "revops", "apex", "lwc", "sfdc", "soql", "rev-")):
        return "atlas", "salesforce_or_revops_keywords"
    if any(k in text for k in ("scrape", "ingest", "zoom notes", "meeting notes", "catalog")):
        return "scout", "scrape_or_data_keywords"
    if any(k in text for k in ("hollywood", "transcribe", "caption", "ffmpeg", "voiceover")):
        return "hollywood", "media_keywords"
    if hint or any(
        k in text for k in ("refactor", "pull request", "commit", "typescript", "python module")
    ):
        if "sfdc" in hint or "clearspeed" in hint:
            raise ValueError("Cursor/OpenAI/Claude code lanes refuse sfdc/company roots")
        return "cursor_local", "personal_code_keywords"
    return "forge_local", "local_first_default"


def registry_snapshot() -> dict[str, Any]:
    return {
        "manager": "alicia",
        "control_plane": ["alicia", "canon"],
        "platforms": {
            lane_id: {
                "executor": meta["executor"],
                "label": meta["label"],
                "role": meta["role"],
                "gate": meta["gate"],
                "hosted": meta["hosted"],
                "earn_eligible": meta["earn_eligible"],
            }
            for lane_id, meta in LANES.items()
        },
        "rule": (
            "Alicia manages; platform executors run work; hosted lanes are "
            "Justin opt-in only; never attribute non-Forge output as Gemma"
        ),
    }
