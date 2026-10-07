"""Hourly/daily spend caps for hosted Cursor Cloud and OpenAI lanes."""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any

from .paths import state_path

_LOCK = threading.Lock()
LEDGER_FILE = "hosted-spend.json"


def _ledger_path() -> Path:
    override = os.environ.get("ALICIA_SPEND_LEDGER", "").strip()
    return Path(override) if override else state_path(LEDGER_FILE)


def _load(path: Path) -> dict[str, Any]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {"lanes": {}}
    if not isinstance(raw, dict):
        return {"lanes": {}}
    lanes = raw.get("lanes")
    if not isinstance(lanes, dict):
        raw["lanes"] = {}
    return raw


def _save(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def _prune(timestamps: list[float], *, now: float, window_s: float) -> list[float]:
    cutoff = now - window_s
    return [t for t in timestamps if t >= cutoff]


def check_and_record(
    lane: str,
    *,
    max_per_hour: int,
    max_per_day: int,
    now: float | None = None,
) -> dict[str, Any]:
    """Atomically enforce caps and record one attempt when allowed.

    Caps of 0 or negative mean "no limit" for that window. When both windows
    are unlimited, this is a no-op success (still records when either is set).
    """

    hour_cap = int(max_per_hour or 0)
    day_cap = int(max_per_day or 0)
    if hour_cap <= 0 and day_cap <= 0:
        return {"ok": True, "enforced": False, "lane": lane}

    ts = float(time.time() if now is None else now)
    path = _ledger_path()
    with _LOCK:
        data = _load(path)
        lanes = data.setdefault("lanes", {})
        entry = lanes.get(lane) if isinstance(lanes.get(lane), dict) else {}
        stamps = [float(x) for x in (entry.get("timestamps") or []) if isinstance(x, (int, float))]
        stamps = _prune(stamps, now=ts, window_s=86400.0)
        hour = _prune(stamps, now=ts, window_s=3600.0)
        day = stamps
        if hour_cap > 0 and len(hour) >= hour_cap:
            return {
                "ok": False,
                "enforced": True,
                "lane": lane,
                "error": f"{lane} spend cap: {len(hour)}/{hour_cap} runs in the last hour",
                "hour_count": len(hour),
                "day_count": len(day),
                "max_per_hour": hour_cap,
                "max_per_day": day_cap,
            }
        if day_cap > 0 and len(day) >= day_cap:
            return {
                "ok": False,
                "enforced": True,
                "lane": lane,
                "error": f"{lane} spend cap: {len(day)}/{day_cap} runs in the last day",
                "hour_count": len(hour),
                "day_count": len(day),
                "max_per_hour": hour_cap,
                "max_per_day": day_cap,
            }
        stamps.append(ts)
        lanes[lane] = {"timestamps": stamps[-500:]}
        data["lanes"] = lanes
        data["updated_at"] = ts
        _save(path, data)
        return {
            "ok": True,
            "enforced": True,
            "lane": lane,
            "hour_count": len(hour) + 1,
            "day_count": len(day) + 1,
            "max_per_hour": hour_cap,
            "max_per_day": day_cap,
        }
