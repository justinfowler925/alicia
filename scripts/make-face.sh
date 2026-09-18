#!/usr/bin/env bash
# Generate an avatar face locally on Atlas5 with mflux.
#
# WHY THIS EXISTS
#
# The recorded pipeline was FLUX.1-dev via router.huggingface.co for the face,
# then Gemini image-editing for wardrobe and framing. It worked, and it was
# rationed: the HF free tier is roughly THREE images a month before HTTP 402,
# and the router caps output at 1280px while Anam's custom avatars need >=1152.
# "Iterate on the face" meant "wait until next month".
#
# mflux runs the same model family locally on the Studio's M3 Ultra. No quota,
# no per-image cost, and --height/--width means the router's cap is irrelevant.
#
# WHAT IS NOT CLAIMED
#
# The original finding was that Gemini-generated faces read as "AI slop" and
# FLUX fixed it. That is a claim about OUTPUT, not hosting, and nothing here
# re-tests it. This replaces the DELIVERY of FLUX, not the judgement that FLUX
# is the right renderer.
#
# Two local mflux passes, then Cursor. The ledger is
# ~/mflux-out/faces/.passes.json on the Studio — same file Atlas uses. Pass 3
# does not re-roll mflux; it POSTs /api/avatar/cursor-pass on this laptop.
# --force-mflux skips the handoff.
#
# HARD-WON DETAILS
#
#   Use --model with the pre-quantized repo. `--base-model dev --quantize 8`
#   resolves to black-forest-labs/FLUX.1-dev, which is NOT cached here, and
#   mflux then dies with "No root_path and no download_url for component: vae"
#   — an error that reads like corrupt weights rather than a missing base model.
#   The dhairyashil repo is self-contained and already on disk.
#
#   Runs on the Studio, always. This laptop cannot reach router.huggingface.co
#   or api.anam.ai at all — HTTP 000, not a timeout — so a local run fails in a
#   way that looks like a bad prompt.
#
#   Dimensions are asserted, not assumed. The two faces already in ~/mflux-out
#   are 1024px, which Anam refuses.
#
#   Memory is reported. A 1152px render peaked at 28.27 GB (measured), on a box
#   that keeps a ~19 GB LLM resident. That is the number the one-resident-model
#   rule needs, so every run prints it.
#
# USAGE
#   ./scripts/make-face.sh "a plain photographic description" [name]
#   ./scripts/make-face.sh --force-mflux "description" [name]
#   ./scripts/make-face.sh --list
set -uo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PY="$ROOT/.venv/bin/python"
[ -x "$PY" ] || PY="python3"
export PYTHONPATH="$ROOT${PYTHONPATH:+:$PYTHONPATH}"

STUDIO_HOST="jfstudio@justins-mac-studio-1.tailbaa084.ts.net"
BRUTUS_URL="${BRUTUS_URL:-http://127.0.0.1:8768}"
ANAM_MIN_PX=1152
SIZE=1152          # square at the floor, so any crop Anam applies still clears
STEPS=25           # dev wants real steps; schnell's 4 is for drafts, not faces
GUIDANCE=3.5
MODEL_REPO=dhairyashil/FLUX.1-dev-mflux-8bit
BASE_MODEL=dev

if [ "${1:-}" = "--list" ]; then
  ssh -o BatchMode=yes "$STUDIO_HOST" 'ls -la "$HOME/mflux-out/faces" 2>/dev/null || echo "(no faces yet)"'
  exit 0
fi

FORCE_MFLUX=0
if [ "${1:-}" = "--force-mflux" ]; then
  FORCE_MFLUX=1
  shift
fi

PROMPT="${1:-}"
NAME="${2:-face}"
if [ -z "$PROMPT" ]; then
  echo "usage: $0 [--force-mflux] \"photographic description\" [name]" >&2
  exit 2
fi

# FLUX wants plain photographic description, not keyword salad. The avatar spec
# IS the stock-photo spec: one person, plain background, even light, facing the
# camera, from the chest up.
FULL_PROMPT="$PROMPT. Photographic headshot from the chest up, facing the camera, neutral expression, even soft studio lighting, plain uncluttered background, sharp focus on the eyes, natural skin texture, shot on an 85mm lens."

export MAKE_FACE_NAME="$NAME"
export MAKE_FACE_PROMPT="$FULL_PROMPT"
export MAKE_FACE_FORCE="$FORCE_MFLUX"

PLAN=$("$PY" - <<'PY'
import json, os, sys
from brutus.avatars import decide_studio_pass
from brutus.image_passes import seed_for_pass
d = decide_studio_pass(
    os.environ["MAKE_FACE_NAME"],
    os.environ["MAKE_FACE_PROMPT"],
    force_mflux=os.environ.get("MAKE_FACE_FORCE") == "1",
)
d["seed"] = seed_for_pass(int(d["n"]))
json.dump(d, sys.stdout)
PY
) || { echo "FAIL: could not read Studio pass ledger" >&2; exit 1; }

ACTION=$("$PY" -c 'import json,sys; print(json.loads(sys.argv[1])["action"])' "$PLAN")
PASS_N=$("$PY" -c 'import json,sys; print(json.loads(sys.argv[1])["n"])' "$PLAN")
SEED=$("$PY" -c 'import json,sys; print(json.loads(sys.argv[1])["seed"])' "$PLAN")

if [ "$ACTION" = "cursor" ]; then
  echo "==> pass ${PASS_N}: two mflux renders already. Handing to Cursor (not another seed)."
  echo "    force_mflux to re-roll locally."
  BODY=$("$PY" -c 'import json,os,sys; d=json.loads(sys.argv[1]); print(json.dumps({"prompt": os.environ["MAKE_FACE_PROMPT"], "name": os.environ["MAKE_FACE_NAME"], "prior": d.get("prior")}))' "$PLAN")
  curl -sS -m 120 -X POST "$BRUTUS_URL/api/avatar/cursor-pass" \
    -H 'content-type: application/json' \
    -d "$BODY" || { echo "FAIL: Brutus cursor-pass unreachable at $BRUTUS_URL" >&2; exit 1; }
  echo
  exit 0
fi

BASE="${NAME}-p${PASS_N}-$(date -u +%Y%m%dT%H%M%SZ)-${SEED}"

echo "==> pass ${PASS_N} on the Studio (never locally — this laptop gets HTTP 000 to HF)"
echo "    ${SIZE}x${SIZE}, ${STEPS} steps, seed=${SEED}, model=${MODEL_REPO}"

# Values are passed as ARGUMENTS with a QUOTED heredoc. An unquoted heredoc
# expands locally, and the escaping needed to keep some things remote and others
# local is a reliable way to send a subtly wrong command — the first version of
# this dropped the model flag and produced the vae error described above.
ssh -o BatchMode=yes -o ConnectTimeout=10 "$STUDIO_HOST" \
  bash -s -- "$BASE" "$MODEL_REPO" "$BASE_MODEL" "$SIZE" "$STEPS" \
             "$GUIDANCE" "$ANAM_MIN_PX" "$FULL_PROMPT" "$SEED" <<'REMOTE'
set -uo pipefail
BASE="$1"; MODEL_REPO="$2"; BASE_MODEL="$3"; SIZE="$4"; STEPS="$5"
GUIDANCE="$6"; FLOOR="$7"; PROMPT="$8"; SEED="$9"
OUT_DIR="$HOME/mflux-out/faces"

mkdir -p "$OUT_DIR"
cd "$OUT_DIR"

# Warn, don't block. Watch COMPRESSOR, not free.
if lsof -nP -iTCP:8081 -sTCP:LISTEN >/dev/null 2>&1; then
  echo "    note: the LLM engine is up on :8081 — ~28 GB render on top of ~19 GB resident"
fi

START=$(date +%s)
"$HOME/.local/bin/mflux-generate" \
  --model "$MODEL_REPO" \
  --base-model "$BASE_MODEL" \
  --lora-style portrait \
  --height "$SIZE" --width "$SIZE" \
  --steps "$STEPS" --guidance "$GUIDANCE" \
  --seed "$SEED" \
  --metadata \
  --output "$BASE.png" \
  --prompt "$PROMPT" >"$BASE.log" 2>&1
RC=$?
ELAPSED=$(( $(date +%s) - START ))

if [ "$RC" -ne 0 ] || [ ! -f "$BASE.png" ]; then
  echo "FAIL: mflux exited $RC and wrote no image"
  tr '\r' '\n' < "$BASE.log" | grep -vE '^[[:space:]]*$' | tail -8
  exit 1
fi

# Read the PNG header directly rather than trusting the flags.
DIMS=$(python3 -c '
import struct, sys
w, h = struct.unpack(">II", open(sys.argv[1], "rb").read(24)[16:24])
print(w, h)
' "$BASE.png")
W=${DIMS% *}; H=${DIMS#* }
if [ "$W" -ge "$FLOOR" ] && [ "$H" -ge "$FLOOR" ]; then
  echo "    $BASE.png  ${W}x${H}  OK for Anam"
  DIMS_RC=0
else
  echo "    $BASE.png  ${W}x${H}  TOO SMALL (Anam needs >= $FLOOR)"
  DIMS_RC=1
fi

echo "    took ${ELAPSED}s"
tr '\r' '\n' < "$BASE.log" | grep -o "Peak MLX memory: .*" | tail -1 | sed 's/^/    /'
exit "$DIMS_RC"
REMOTE
RC=$?

if [ "$RC" -eq 0 ]; then
  export MAKE_FACE_N="$PASS_N"
  export MAKE_FACE_SEED="$SEED"
  export MAKE_FACE_PATH="/Users/jfstudio/mflux-out/faces/$BASE.png"
  "$PY" - <<'PY' || echo "    warn: ledger record failed — image exists"
import os
from brutus.avatars import record_studio_pass
record_studio_pass(
    os.environ["MAKE_FACE_NAME"],
    os.environ["MAKE_FACE_PROMPT"],
    n=int(os.environ["MAKE_FACE_N"]),
    engine="mflux",
    path=os.environ["MAKE_FACE_PATH"],
    seed=int(os.environ["MAKE_FACE_SEED"]),
)
PY
  echo "==> done. Draft: $STUDIO_HOST:\$HOME/mflux-out/faces/$BASE.png"
  echo "    On the Brutus Avatar page: Stage (into faces/looks) or Stage & apply"
  echo "    (Anam holds only 3 — apply is delete-to-swap)."
else
  echo "==> FAILED (log on the Studio: ~/mflux-out/faces/$BASE.log)"
fi
exit "$RC"
