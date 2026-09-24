"""mflux gets two local passes; the third is a Cursor handoff.

Atlas's local FLUX (mflux) is cheap and on-box. After two renders of the same
brief it is repeating itself — same model, same prompt family, a different seed.
Pass 3 asks Cursor to try a *different* image with *its* model, and to keep that
image only if it is actually better. A third mflux re-roll is `force_mflux=True`.

The ledger lives next to the images (`~/mflux-out/faces/.passes.json` by default)
so `mflux_generate` on Studio and `make-face.sh` on the laptop agree on the count.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ATLAS_PASSES = 2
DEFAULT_LEDGER = Path.home() / "mflux-out" / "faces" / ".passes.json"
HANDOFF_NAME = ".cursor-handoff.json"


def ledger_path() -> Path:
    override = (os.environ.get("ATLAS_IMAGE_PASSES") or "").strip()
    return Path(override).expanduser() if override else DEFAULT_LEDGER


def key_for(*, name: str = "", prompt: str = "") -> str:
    """Persona/name wins; otherwise a stable hash of the normalized prompt."""
    named = (name or "").strip().lower()
    if named:
        return named
    blob = " ".join((prompt or "").lower().split())
    if not blob:
        raise ValueError("name or prompt required")
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


@dataclass(frozen=True)
class Decision:
    action: str  # "mflux" | "cursor"
    n: int
    prior: list[dict[str, Any]] = field(default_factory=list)
    cursor_prompt: str = ""

    @property
    def handoff(self) -> bool:
        return self.action == "cursor"


def load(path: Path | None = None) -> dict[str, Any]:
    p = path or ledger_path()
    if not p.is_file():
        return {"keys": {}}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"keys": {}}
    if not isinstance(data, dict) or not isinstance(data.get("keys"), dict):
        return {"keys": {}}
    return data


def save(store: dict[str, Any], path: Path | None = None) -> Path:
    p = path or ledger_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(store, indent=2) + "\n", encoding="utf-8")
    return p


def decide(
    store: dict[str, Any],
    key: str,
    prompt: str,
    *,
    force_mflux: bool = False,
) -> Decision:
    row = (store.get("keys") or {}).get(key) or {"prompt": prompt, "passes": []}
    prior = list(row.get("passes") or [])
    n = len(prior) + 1
    if force_mflux or n <= ATLAS_PASSES:
        return Decision(action="mflux", n=n, prior=prior)
    return Decision(
        action="cursor",
        n=n,
        prior=prior,
        cursor_prompt=build_cursor_prompt(prompt, prior),
    )


def seed_for_pass(n: int, base: int = 42) -> int:
    """Pass 2 must not silently re-render pass 1's default seed."""
    return base + (n - 1) * 17


def record(
    store: dict[str, Any],
    key: str,
    *,
    prompt: str,
    n: int,
    engine: str,
    path: str = "",
    seed: int | None = None,
) -> dict[str, Any]:
    keys = store.setdefault("keys", {})
    row = keys.setdefault(key, {"prompt": prompt, "passes": []})
    row["prompt"] = prompt
    row["passes"].append(
        {
            "n": n,
            "engine": engine,
            "path": path,
            "seed": seed,
            "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
    )
    return store


def build_cursor_prompt(prompt: str, prior: list[dict[str, Any]]) -> str:
    """Instruction Cursor gets on pass 3. The 'if better' gate is in the prompt
    because Cursor's model is the one that can see the pixels."""
    lines = [
        "Pass 3 of an Atlas image job. Atlas already ran two local mflux "
        "(FLUX.1-dev on the Studio) renders of this brief. Do not re-roll "
        "the same headshot with a new seed.",
        "",
        "Brief:",
        prompt.strip() or "(empty)",
        "",
        "Atlas already produced:",
    ]
    if not prior:
        lines.append("  (no paths recorded — treat this as a fresh request)")
    for p in prior:
        loc = p.get("path") or "(path missing)"
        lines.append(f"  pass {p.get('n')}: {loc}  engine={p.get('engine')} seed={p.get('seed')}")
    lines += [
        "",
        "Your job:",
        "1. Use GenerateImage to create something DIFFERENT — a different angle, "
        "wardrobe, or lighting. Still a photographic headshot: one person, chest-up, "
        "facing the camera, even studio light, plain background, natural skin, "
        "sharp eyes. Square, at least 1152×1152 (Anam will refuse smaller).",
        "2. Compare against Atlas's two. Keep yours only if it is clearly better "
        "(more natural skin, sharper eyes, less plastic, better likeness to the brief). "
        "If it is not better, say so in one sentence and do not claim a win — Atlas's "
        "best of the two stays the draft.",
        "3. Save the PNG and report the absolute path. If you can scp it to the "
        "Studio at ~/mflux-out/faces/<name>-<utc>-cursor.png, do that; otherwise "
        "leave it in the workspace and report the local path.",
        "",
        "Do not commit, push, or enroll the face. Staging is a human click on the "
        "Alicia Avatar page.",
    ]
    return "\n".join(lines)


def write_handoff(
    decision: Decision,
    *,
    key: str,
    prompt: str,
    dest_dir: Path | None = None,
) -> Path:
    """Drop a job file next to the drafts so Alicia/a human can see the handoff."""
    root = dest_dir or (ledger_path().parent)
    root.mkdir(parents=True, exist_ok=True)
    path = root / HANDOFF_NAME
    payload = {
        "key": key,
        "pass": decision.n,
        "prompt": prompt,
        "prior": decision.prior,
        "cursor_prompt": decision.cursor_prompt,
        "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return path
