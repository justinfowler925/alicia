"""Earned autonomy ledger — local / Studio safe classes only.

Hosted platforms (Cursor Cloud, OpenAI, Claude) never earn auto-run.
Writes, deploys, and sends never auto-run.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .platform_registry import LANES, normalize_lane

_LEDGER = Path.home() / ".local/share/alicia-specialist-router" / "earn-ledger.json"

# Classes that may eventually skip preview chatter after N successes.
SAFE_CLASSES = frozenset(
    {
        "forge_trivial",
        "atlas_read_only",
        "scout_import",
        "scout_read_only",
        "hollywood_inspect",
        "hollywood_speak_safe",
    }
)

NEVER_EARN = frozenset(
    {
        "atlas_writable",
        "atlas_deploy",
        "scout_promote",
        "hollywood_destructive",
        "outbound_slack",
        "outbound_email",
        "outbound_linear",
        "overnight_free",
        "cursor_cloud",
        "openai",
        "claude",
        "hosted_spawn",
    }
)


@dataclass
class EarnCfg:
    enabled: bool = True
    auto_run: bool = False  # off until thresholds met AND Justin leaves it on
    threshold: int = 3


def ledger_path() -> Path:
    return _LEDGER


def _load(path: Path | None = None) -> dict[str, Any]:
    target = path or _LEDGER
    if not target.is_file():
        return {"counts": {}, "updated_at": None}
    try:
        data = json.loads(target.read_text())
    except (OSError, json.JSONDecodeError):
        return {"counts": {}, "updated_at": None}
    if not isinstance(data, dict):
        return {"counts": {}, "updated_at": None}
    counts = data.get("counts")
    if not isinstance(counts, dict):
        counts = {}
    return {"counts": counts, "updated_at": data.get("updated_at")}


def _save(data: dict[str, Any], path: Path | None = None) -> None:
    target = path or _LEDGER
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    payload = {
        "counts": data.get("counts") or {},
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    tmp = target.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=2) + "\n")
    tmp.chmod(0o600)
    tmp.replace(target)


def _key(lane: str, work_class: str) -> str:
    return f"{lane}:{work_class}"


def record_success(
    lane: str,
    work_class: str,
    *,
    path: Path | None = None,
) -> dict[str, Any]:
    lane_id = normalize_lane(lane) or (lane or "").strip().lower()
    work_class = (work_class or "").strip().lower()
    if work_class in NEVER_EARN or lane_id in {"cursor_cloud", "openai", "claude"}:
        return {
            "ok": False,
            "recorded": False,
            "reason": "class_or_lane_never_earns",
            "lane": lane_id,
            "class": work_class,
        }
    meta = LANES.get(lane_id or "")
    if meta is not None and not meta.get("earn_eligible"):
        return {
            "ok": False,
            "recorded": False,
            "reason": "lane_not_earn_eligible",
            "lane": lane_id,
            "class": work_class,
        }
    if work_class not in SAFE_CLASSES:
        return {
            "ok": False,
            "recorded": False,
            "reason": "class_not_safe",
            "lane": lane_id,
            "class": work_class,
        }
    data = _load(path)
    key = _key(lane_id, work_class)
    counts = data["counts"]
    counts[key] = int(counts.get(key) or 0) + 1
    _save(data, path)
    return {
        "ok": True,
        "recorded": True,
        "lane": lane_id,
        "class": work_class,
        "count": counts[key],
    }


def may_auto_run(
    lane: str,
    work_class: str,
    *,
    cfg: EarnCfg | None = None,
    path: Path | None = None,
) -> dict[str, Any]:
    """Return whether this class may skip preview for a live local/Studio run.

    Hosted lanes always False. auto_run config must be on AND threshold met.
    First live of a class is still gated by Alicia's tool gate elsewhere.
    """
    cfg = cfg or EarnCfg()
    lane_id = normalize_lane(lane) or (lane or "").strip().lower()
    work_class = (work_class or "").strip().lower()
    base = {
        "lane": lane_id,
        "class": work_class,
        "auto_run": False,
        "ledger_enabled": bool(cfg.enabled),
    }
    if work_class in NEVER_EARN or lane_id in {"cursor_cloud", "openai", "claude"}:
        return {**base, "reason": "never_eligible_hosted_or_destructive"}
    if not cfg.enabled:
        return {**base, "reason": "ledger_disabled"}
    if not cfg.auto_run:
        return {**base, "reason": "auto_run_config_off"}
    if work_class not in SAFE_CLASSES:
        return {**base, "reason": "class_not_safe"}
    meta = LANES.get(lane_id or "")
    if meta is not None and not meta.get("earn_eligible"):
        return {**base, "reason": "lane_not_earn_eligible"}
    data = _load(path)
    count = int((data["counts"] or {}).get(_key(lane_id, work_class)) or 0)
    if count < int(cfg.threshold):
        return {
            **base,
            "reason": "below_threshold",
            "count": count,
            "threshold": cfg.threshold,
        }
    return {
        **base,
        "auto_run": True,
        "reason": "earned",
        "count": count,
        "threshold": cfg.threshold,
    }


def snapshot(*, path: Path | None = None) -> dict[str, Any]:
    data = _load(path)
    return {
        "ok": True,
        "counts": dict(data.get("counts") or {}),
        "updated_at": data.get("updated_at"),
        "safe_classes": sorted(SAFE_CLASSES),
        "never_earn": sorted(NEVER_EARN),
        "note": "Hosted platforms never earn auto-run; writes/deploys/sends never auto-run",
    }
