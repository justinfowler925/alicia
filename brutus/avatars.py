"""Avatar control — which face, which outfit, which service, saved as configs.

The demo's face lives in three places at once and they must agree:

* **Anam** holds at most 3 custom avatars ("one-shot" cap on the Explorer
  plan), so changing persona or outfit means delete + re-enroll, which mints a
  NEW avatar id every time.
* **Vercel env** carries that id (`ANAM_AVATAR_ID`), the backup face
  (`LIVEAVATAR_AVATAR_ID`) and which tier leads (`PRIMARY_TIER`).
* A **redeploy** is required before an env change reaches a running function.

Enrollment is proxied through the Studio: this laptop cannot reach
`api.anam.ai` at all (every request returns 000), while Atlas5 can. Vercel is
reachable from here, so token + env work happens locally.
"""

from __future__ import annotations

from .paths import state_path

import json
import re
import subprocess
import time
from pathlib import Path
from typing import Any

import httpx

STUDIO = "https://justins-mac-studio.tailbaa084.ts.net:8930"
STUDIO_HOST = "jfstudio@100.93.125.5"
VERCEL_PROJECT = "clearspeed-demos"
CONFIG_PATH = state_path("avatar_configs.json")
# mflux writes to ~/mflux-out/faces (make-face.sh). The live Studio daemon
# is ~/anam-avatar-chatbot/server.mjs (NOT ~/Projects/anam-avatar-chatbot) —
# enroll only reads that checkout's faces/. Staging is the glue that was
# still manual after Phase 4.
ANAM_MIN_PX = 1152

# Knobs worth exposing. Anything not listed is deliberately not a dial.
TIERS = [
    ("anam", "Anam leads (Cara-4 faces, 10-min sessions)"),
    ("heygen", "HeyGen leads (pinned — deterministic single greeting)"),
]
TRANSPORTS = [("textbelt", "TextBelt (works in the US today)"), ("twilio", "Twilio (blocked until A2P clears)")]
_LOOKS = ("businesscasual", "professional", "military", "original", "formal", "default")
_SAFE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def _persona_and_look(face: str) -> tuple[str, str]:
    """`looks/analyst_professional.jpg` -> ("analyst", "professional")."""
    stem = Path(face).stem
    for suffix in ("_businesscasual", "_professional", "_military", "_original", "_formal"):
        if stem.endswith(suffix):
            return stem[: -len(suffix)], suffix.lstrip("_")
    return stem, "default"


def _draft_persona_hint(name: str) -> str:
    """`analyst-20260806T151135Z.png` -> `analyst`."""
    stem = Path(name).stem
    return re.sub(r"-\d{8}T\d{6}Z$", "", stem) or stem


# The Studio is a Tailscale peer that is often simply asleep, and every console
# page load used to pay for that in full: three sequential ssh calls, each
# waiting out the TCP timeout, ~36 seconds of a synchronous handler for 570
# bytes of JSON. Two of those in flight also exhausted the browser's six
# connections to the origin, so the Nucleus table sat on "Loading…" behind an
# avatar listing nobody had opened.
#
# So an unreachable host is remembered. The first attempt after the cooldown
# pays a short timeout; the rest are told instantly, which is the honest answer
# anyway — the host is down, and waiting 12 seconds does not make it truer.
_STUDIO_UNREACHABLE_FOR = 120.0
_studio_down: tuple[float, str] | None = None
# One breaker covers SSH and HTTP because both talk to the same sleeping Mac.
# Only the ssh path was guarded at first, so the endpoint still cost 15s every
# time: the concurrent gather is bounded by its slowest member, and studio_faces
# (10s) and studio_avatars (15s) were both still waiting out their own timeouts
# against a host the ssh probe had already established was down.
_STUDIO_HTTP_TIMEOUT = 4.0


class StudioUnreachable(RuntimeError):
    """The Studio peer did not answer, and we are not going to wait on it again."""


def studio_reachability() -> dict[str, Any]:
    """What the screen should say about the Studio peer, with no network call."""
    if _studio_down is None:
        return {"reachable": True, "error": ""}
    since, error = _studio_down
    remaining = max(0.0, _STUDIO_UNREACHABLE_FOR - (time.monotonic() - since))
    if not remaining:
        return {"reachable": True, "error": ""}
    return {"reachable": False, "error": error, "retry_in_seconds": round(remaining)}


def reset_studio_reachability() -> None:
    """Forget the cooldown, so an explicit retry really retries."""
    global _studio_down
    _studio_down = None


def _note_studio_down(error: str) -> StudioUnreachable:
    global _studio_down
    _studio_down = (time.monotonic(), error.strip()[-240:] or "Studio is unreachable")
    return StudioUnreachable(_studio_down[1])


def _require_studio() -> None:
    if not studio_reachability()["reachable"]:
        raise StudioUnreachable(_studio_down[1] if _studio_down else "Studio is unreachable")


async def _studio_get(path: str) -> Any:
    """One GET to the Studio, behind the same breaker the ssh calls use."""
    global _studio_down
    _require_studio()
    try:
        async with httpx.AsyncClient(timeout=_STUDIO_HTTP_TIMEOUT, verify=True) as client:
            response = await client.get(f"{STUDIO}{path}")
            response.raise_for_status()
            payload = response.json()
    except (httpx.TimeoutException, httpx.TransportError) as exc:
        # httpx connect errors often stringify to nothing, so name the class.
        why = str(exc).strip() or type(exc).__name__
        raise _note_studio_down(f"Studio did not answer {path}: {why}") from exc
    # A woken host clears the cooldown for the ssh callers too.
    _studio_down = None
    return payload


def _ssh(remote: str, timeout: int = 15) -> subprocess.CompletedProcess[str]:
    global _studio_down
    _require_studio()
    try:
        proc = subprocess.run(
            ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=4", STUDIO_HOST, remote],
            capture_output=True, text=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise _note_studio_down(f"Studio did not answer within {timeout}s") from exc
    # Only a connection failure is a host verdict. A command that runs and
    # exits nonzero means the host is up and the caller owns the error.
    stderr = (proc.stderr or "").lower()
    if proc.returncode == 255 and ("connect to host" in stderr or "timed out" in stderr):
        raise _note_studio_down(proc.stderr or "ssh failed")
    _studio_down = None
    return proc


async def studio_faces() -> list[dict[str, str]]:
    """Face masters staged on the Studio, grouped into persona + look."""
    faces = (await _studio_get("/api/faces")).get("faces", [])
    out = []
    for f in faces:
        persona, look = _persona_and_look(f)
        out.append({"face": f, "persona": persona, "look": look})
    return sorted(out, key=lambda x: (x["persona"], x["look"]))


async def studio_avatars() -> dict[str, Any]:
    """What Anam currently holds, and which id the app defaults to."""
    return await _studio_get("/api/avatars/custom")


def list_mflux_drafts() -> list[dict[str, Any]]:
    """PNGs sitting in ~/mflux-out/faces on the Studio — not yet enrollable.

    Phase 4 generates here. Apply only reads faces/looks|roster. This list is
    what makes the gap visible on the avatar page instead of an scp recipe.
    """
    remote = r'''python3 - <<'PY'
import json, struct
from pathlib import Path
root = Path.home() / "mflux-out" / "faces"
out = []
if root.is_dir():
    for p in sorted(root.glob("*.png"), key=lambda x: x.stat().st_mtime, reverse=True):
        try:
            w, h = struct.unpack(">II", p.read_bytes()[16:24])
        except Exception:
            w = h = 0
        out.append({
            "name": p.name,
            "bytes": p.stat().st_size,
            "width": w,
            "height": h,
            "mtime": int(p.stat().st_mtime),
            "anam_ok": w >= 1152 and h >= 1152,
        })
print(json.dumps(out))
PY'''
    try:
        proc = _ssh(remote)
    except (subprocess.TimeoutExpired, OSError) as exc:
        raise RuntimeError(f"studio ssh failed: {exc}") from exc
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or proc.stdout or "ssh failed").strip()[-240:])
    drafts = json.loads(proc.stdout or "[]")
    for d in drafts:
        d["persona_hint"] = _draft_persona_hint(d["name"])
    return drafts


def stage_mflux_draft(draft: str, persona: str, look: str = "professional") -> dict[str, Any]:
    """Copy an mflux PNG into faces/looks so Apply can enroll it.

    Returns the relative face path the enroll endpoint already understands
    (`looks/analyst_professional.png`). Overwrites an existing master of the
    same persona/look — that is the point of iterating on a face.
    """
    draft = Path(draft).name
    persona = re.sub(r"[^a-z0-9]+", "", (persona or "").lower())
    look = re.sub(r"[^a-z0-9]+", "", (look or "professional").lower()) or "professional"
    if look not in _LOOKS:
        look = "professional"
    if not _SAFE.match(draft) or not draft.lower().endswith(".png"):
        return {"ok": False, "error": "bad draft name"}
    if not persona:
        return {"ok": False, "error": "persona required"}

    dest_rel = f"looks/{persona}_{look}.png"
    # Quoted paths, basename-only draft — never interpolate a raw path into ssh.
    # DEST is the LIVE daemon's faces dir (~/anam-avatar-chatbot), not Projects/.
    remote = (
        f'set -euo pipefail; '
        f'SRC="$HOME/mflux-out/faces/{draft}"; '
        f'DEST="$HOME/anam-avatar-chatbot/faces/{dest_rel}"; '
        f'test -f "$SRC" || {{ echo "missing draft"; exit 2; }}; '
        f'mkdir -p "$(dirname "$DEST")"; '
        f'cp -f "$SRC" "$DEST"; '
        f'python3 -c \'import struct,sys; w,h=struct.unpack(">II", open(sys.argv[1],"rb").read(24)[16:24]); '
        f'print(w,h); assert w>={ANAM_MIN_PX} and h>={ANAM_MIN_PX}, "too small"\' "$DEST"; '
        f'echo staged'
    )
    try:
        proc = _ssh(remote, timeout=60)
    except (StudioUnreachable, subprocess.TimeoutExpired, OSError) as exc:
        return {"ok": False, "error": f"studio ssh failed: {exc}"}
    if proc.returncode != 0:
        return {"ok": False, "error": (proc.stderr or proc.stdout or "stage failed").strip()[-240:]}
    dims = ""
    for line in (proc.stdout or "").splitlines():
        if re.match(r"^\d+ \d+$", line.strip()):
            dims = line.strip()
    return {"ok": True, "face": dest_rel, "draft": draft, "dims": dims, "detail": "staged"}

def _vercel_token() -> str:
    """1Password Atlas via op-session.sh. Empty if unmirrored or missing."""
    helper = Path.home() / "fowler-brain" / "scripts" / "op-session.sh"
    try:
        out = subprocess.run(
            ["bash", str(helper), "read", "op://Atlas/VERCEL_TOKEN/shared"],
            capture_output=True, text=True, timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return (out.stdout or "").strip()


async def vercel_env() -> dict[str, str]:
    """What the LIVE demo is actually serving.

    Read from the deployed app, not from Vercel's env API — that returns the
    values still encrypted, and more importantly the env API describes what is
    *configured*, while these endpoints describe what is *running* (an env
    change does not take effect until a redeploy).
    """
    base = "https://www.clearspeeddemos.com/api/anam"
    out: dict[str, str] = {}
    async with httpx.AsyncClient(timeout=25) as c:
        try:
            a = (await c.get(f"{base}/avatars")).json()
            out["ANAM_AVATAR_ID"] = a.get("defaultId", "")
        except Exception:  # noqa: BLE001
            pass
        try:
            p = (await c.get(f"{base}/probe")).json()
            out["PRIMARY_TIER"] = p.get("primaryTier", "")
            out["TEXT_TRANSPORT"] = p.get("textTransport", "")
            if p.get("textbeltQuota") is not None:
                out["TEXTBELT_QUOTA"] = str(p["textbeltQuota"])
            out["ANAM_HEALTH"] = p.get("verdict", "")
        except Exception:  # noqa: BLE001
            pass
    return out


async def apply_config(face: str | None, tier: str | None, transport: str | None,
                       replace_id: str | None = None, redeploy: bool = True) -> dict[str, Any]:
    """Enroll the chosen face, point the app at it, and redeploy.

    Returns a step-by-step report — every stage is reported honestly, including
    partial success, because a half-applied config is worse than a failed one.
    """
    steps: list[dict[str, Any]] = []
    new_id = None

    if face:
        try:
            async with httpx.AsyncClient(timeout=120) as c:
                r = await c.post(f"{STUDIO}/api/avatars/enroll",
                                 json={"face": face, "replaceId": replace_id})
                data = r.json()
            new_id = data.get("id")
            steps.append({"step": "enroll", "ok": bool(new_id), "detail": data.get("error") or new_id})
        except Exception as exc:  # noqa: BLE001
            steps.append({"step": "enroll", "ok": False, "detail": str(exc)})

    updates = {}
    if new_id:
        updates["ANAM_AVATAR_ID"] = new_id
    if tier:
        updates["PRIMARY_TIER"] = tier
    if transport:
        updates["TEXT_TRANSPORT"] = transport

    if updates:
        token = _vercel_token()
        if not token:
            steps.append({"step": "vercel-env", "ok": False, "detail": "no VERCEL_TOKEN from 1Password"})
        else:
            try:
                async with httpx.AsyncClient(timeout=30) as c:
                    h = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
                    pid = (await c.get(f"https://api.vercel.com/v9/projects/{VERCEL_PROJECT}", headers=h)).json()["id"]
                    body = [{"key": k, "value": v, "type": "encrypted",
                             "target": ["production", "preview"]} for k, v in updates.items()]
                    r = await c.post(f"https://api.vercel.com/v10/projects/{pid}/env?upsert=true",
                                     headers=h, json=body)
                steps.append({"step": "vercel-env", "ok": r.status_code < 300,
                              "detail": ", ".join(updates)})
            except Exception as exc:  # noqa: BLE001
                steps.append({"step": "vercel-env", "ok": False, "detail": str(exc)})

        if redeploy:
            # Env changes only reach NEW deployments — without this the page
            # keeps serving the old face, which looks like the change failed.
            try:
                out = subprocess.run(
                    ["git", "commit", "--allow-empty", "-m",
                     f"avatar config: {face or 'tier/transport'} ({time.strftime('%H:%M')})"],
                    cwd=str(Path.home() / "Projects" / "clearspeed-demos"),
                    capture_output=True, text=True, timeout=60)
                push = subprocess.run(["git", "push"], cwd=str(Path.home() / "Projects" / "clearspeed-demos"),
                                      capture_output=True, text=True, timeout=120)
                steps.append({"step": "redeploy", "ok": push.returncode == 0,
                              "detail": (push.stderr or out.stdout or "pushed").strip()[-120:]})
            except Exception as exc:  # noqa: BLE001
                steps.append({"step": "redeploy", "ok": False, "detail": str(exc)})

    return {"ok": all(s["ok"] for s in steps) if steps else False, "avatar_id": new_id, "steps": steps}


# ---- saved configs -------------------------------------------------------
def load_configs() -> list[dict[str, Any]]:
    try:
        return json.loads(CONFIG_PATH.read_text())
    except Exception:  # noqa: BLE001
        return []


def save_config(name: str, face: str, tier: str, transport: str) -> list[dict[str, Any]]:
    """Named, re-appliable loadouts — the face id is deliberately NOT stored.

    Ids change on every re-enroll, so a saved config records the *intent*
    (which master, which tier) and re-enrolls on apply.
    """
    name = re.sub(r"\s+", " ", name).strip()[:40] or "unnamed"
    configs = [c for c in load_configs() if c["name"] != name]
    configs.append({"name": name, "face": face, "tier": tier, "transport": transport,
                    "saved_at": time.strftime("%Y-%m-%d %H:%M")})
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(configs, indent=2))
    return configs


def delete_config(name: str) -> list[dict[str, Any]]:
    configs = [c for c in load_configs() if c["name"] != name]
    CONFIG_PATH.write_text(json.dumps(configs, indent=2))
    return configs


def fetch_studio_ledger() -> dict[str, Any]:
    """The pass counter lives on the Studio, next to the PNGs — never on this laptop."""
    remote = r'''python3 - <<'PY'
import json
from pathlib import Path
p = Path.home() / "mflux-out" / "faces" / ".passes.json"
if not p.is_file():
    print('{"keys":{}}')
else:
    print(p.read_text(encoding="utf-8") or '{"keys":{}}')
PY'''
    proc = _ssh(remote)
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or proc.stdout or "ssh failed").strip()[-240:])
    try:
        data = json.loads(proc.stdout or "{}")
    except json.JSONDecodeError as e:
        raise RuntimeError(f"studio ledger was not JSON: {e}") from e
    if not isinstance(data, dict) or not isinstance(data.get("keys"), dict):
        return {"keys": {}}
    return data


def push_studio_ledger(store: dict[str, Any]) -> None:
    payload = json.dumps(store)
    # Arguments, not interpolation — the JSON is stdin to remote python.
    proc = subprocess.run(
        [
            "ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", STUDIO_HOST,
            "python3 -c \"import sys,pathlib; p=pathlib.Path.home()/'mflux-out'/'faces'/'.passes.json'; p.parent.mkdir(parents=True, exist_ok=True); p.write_text(sys.stdin.read())\"",
        ],
        input=payload,
        capture_output=True, text=True, timeout=30,
    )
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or proc.stdout or "ssh failed").strip()[-240:])


def decide_studio_pass(name: str, prompt: str, *, force_mflux: bool = False) -> dict[str, Any]:
    from .image_passes import decide, key_for

    store = fetch_studio_ledger()
    key = key_for(name=name, prompt=prompt)
    d = decide(store, key, prompt, force_mflux=force_mflux)
    return {
        "key": key,
        "action": d.action,
        "n": d.n,
        "prior": d.prior,
        "cursor_prompt": d.cursor_prompt,
        "handoff": d.handoff,
    }


def record_studio_pass(name: str, prompt: str, *, n: int, engine: str, path: str = "", seed: int | None = None) -> None:
    from .image_passes import key_for, record

    store = fetch_studio_ledger()
    record(store, key_for(name=name, prompt=prompt), prompt=prompt, n=n, engine=engine, path=path, seed=seed)
    push_studio_ledger(store)


def run_cursor_image_pass(cfg: Any, *, prompt: str, name: str, prior: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Pass 3: Cursor tries a different image. Keep it only if it is better."""
    from .config import BrutusCfg
    from .cursor_runner import run_cursor_chat
    from .image_passes import build_cursor_prompt

    cfg = cfg or BrutusCfg()
    cursor_prompt = build_cursor_prompt(prompt, prior or [])
    result = run_cursor_chat(cfg, cursor_prompt, repo_hint="brutus")
    result["cursor_prompt"] = cursor_prompt
    result["name"] = name
    if result.get("ok"):
        try:
            record_studio_pass(name, prompt, n=len(prior or []) + 1, engine="cursor")
        except Exception as exc:  # noqa: BLE001 — image exists even if the counter lags
            result["ledger_error"] = str(exc)
    return result
