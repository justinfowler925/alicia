#!/usr/bin/env bash
# Deploy Brutus to the dedicated service worktree and restart it.
#
# WHY THIS EXISTS
#
# The daemon used to run straight out of ~/Projects/brutus, a shared checkout
# that other sessions switch branches in. Twice in one day it ended up serving a
# different branch than intended — once someone else's unmerged feature branch,
# for half an hour, while a restart reported "up after 2s" and looked perfect.
#
# The service now runs from its own detached worktree at ~/.brutus/app. Nothing
# anyone does in ~/Projects/brutus can change what is running. State lives at
# ~/.brutus/state, outside every checkout, so a redeploy cannot empty it.
#
#   ./scripts/deploy.sh            # deploy origin/main
#   ./scripts/deploy.sh --status   # what is running, and is it current?
#   ./scripts/deploy.sh --ref <r>  # deploy something else, deliberately
#
# The --ref escape hatch exists because the failure this file was written to
# prevent was a SILENT one: the daemon twice served an unmerged branch that
# nobody chose. A ref named on the command line is the opposite of that. It is
# still a hazard, so it announces itself, it is recorded in the deploy manifest,
# and `--status` reports the drift until the commits land on origin/main — at
# which point a plain deploy replaces them.
#
# Use it to try a fix on the real daemon before landing it, and nothing else.
#
# It was FIRST added for a bad reason worth recording: a push had failed with
# "Permission to justinfowler925/brutus.git denied to justin-fowler_cspd", and
# that was read as "this machine has no write access". It is not. `gh` holds one
# active account and this laptop has two; the personal one owns this repo. The
# lesson two paragraphs down, about fetch, is the same lesson. Landing is
# `./scripts/land.sh`, which takes the token from 1Password for one invocation
# and never touches the active account.
set -uo pipefail

SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd -P)
REPO="${BRUTUS_REPO:-$HOME/Projects/brutus}"
APP="${BRUTUS_APP_DIR:-$HOME/.brutus/app}"
# Operator state only. Do NOT inherit ambient BRUTUS_STATE_DIR — a scratch
# probe that exports it makes this script mkdir/verify against a temp dir while
# launchd keeps writing to ~/.brutus/state, and the deploy looks green either way.
# Override deliberately via BRUTUS_DEPLOY_STATE_DIR if you ever need to.
STATE="${BRUTUS_DEPLOY_STATE_DIR:-$HOME/.brutus/state}"
PLIST_NAME=com.clearspeed.brutus.plist
LOADED_PLIST="$HOME/Library/LaunchAgents/$PLIST_NAME"
PORT=8768
VOICE_PORT=8096
CORE_LABEL="com.clearspeed.brutus"
VOICE_AGENT_LABEL="com.clearspeed.brutus-livekit-agent"
DEPLOY_SERVICES_STOPPED="${BRUTUS_DEPLOY_SERVICES_STOPPED:-0}"
DEPLOY_SUCCEEDED=0
TARGET_REF="${BRUTUS_DEPLOY_REF:-origin/main}"

while [ $# -gt 0 ]; do
  case "$1" in
    --ref)
      [ -n "${2:-}" ] || { echo "--ref needs a git ref"; exit 2; }
      TARGET_REF="$2"; export BRUTUS_DEPLOY_REF="$2"; shift 2 ;;
    --ref=*) TARGET_REF="${1#--ref=}"; export BRUTUS_DEPLOY_REF="$TARGET_REF"; shift ;;
    *) break ;;
  esac
done

running_sha () { git -C "$APP" rev-parse --short HEAD 2>/dev/null || echo "-"; }
service_pid () {
  launchctl print "gui/$(id -u)/$CORE_LABEL" 2>/dev/null \
    | grep -oE 'pid = [0-9]+' | grep -oE '[0-9]+' | head -1
}
job_pid () {
  launchctl print "gui/$(id -u)/$1" 2>/dev/null \
    | grep -oE 'pid = [0-9]+' | grep -oE '[0-9]+' | head -1
}
unload_job () {
  local label="$1"
  launchctl bootout "gui/$(id -u)/$label" 2>/dev/null || true
  for _ in $(seq 1 50); do
    launchctl print "gui/$(id -u)/$label" >/dev/null 2>&1 || return 0
    sleep 0.2
  done
  return 1
}
wait_for_port_closed () {
  local port="$1"
  for _ in $(seq 1 50); do
    lsof -nP -iTCP:"$port" -sTCP:LISTEN >/dev/null 2>&1 || return 0
    sleep 0.2
  done
  return 1
}
wait_for_new_actor () {
  local old_pid="$1" stable=0 pid i
  for i in $(seq 1 120); do
    pid=$(service_pid)
    if [ -n "$pid" ] && { [ -z "$old_pid" ] || [ "$pid" != "$old_pid" ]; } \
      && curl -sf -m 3 "http://127.0.0.1:$PORT/api/healthz" >/dev/null 2>&1; then
      stable=$((stable + 1))
      if [ "$stable" -ge 2 ]; then
        echo "    new actor pid=$pid stable after ~${i}s"
        return 0
      fi
    else
      stable=0
    fi
    sleep 1
  done
  return 1
}
wait_for_voice_actor () {
  local old_pid="$1" stable=0 pid i
  for i in $(seq 1 120); do
    pid=$(job_pid "$VOICE_AGENT_LABEL")
    if [ -n "$pid" ] && { [ -z "$old_pid" ] || [ "$pid" != "$old_pid" ]; } \
      && curl -sf -m 3 "http://127.0.0.1:$VOICE_PORT/" >/dev/null 2>&1; then
      stable=$((stable + 1))
      [ "$stable" -ge 2 ] && { echo "    voice actor pid=$pid stable after ~${i}s"; return 0; }
    else
      stable=0
    fi
    sleep 1
  done
  return 1
}
recover_on_failure () {
  [ "$DEPLOY_SERVICES_STOPPED" = "1" ] || return 0
  [ "$DEPLOY_SUCCEEDED" = "1" ] && return 0
  echo "    deploy failed after quiescing services; restoring launchd jobs" >&2
  for label in "$CORE_LABEL" "$VOICE_AGENT_LABEL"; do
    plist="$HOME/Library/LaunchAgents/$label.plist"
    launchctl print "gui/$(id -u)/$label" >/dev/null 2>&1 \
      || launchctl bootstrap "gui/$(id -u)" "$plist" >/dev/null 2>&1 \
      || true
  done
}
trap recover_on_failure EXIT
wait_for_http_200 () {
  local url="$1" code i
  for i in $(seq 1 20); do
    code=$(curl -s -m 8 -o /dev/null -w '%{http_code}' "$url")
    [ "$code" = "200" ] && return 0
    sleep 1
  done
  return 1
}
wait_for_todos () {
  local i
  for i in $(seq 1 20); do
    TODOS=$(curl -s -m 8 -w '\n%{http_code}' "http://127.0.0.1:$PORT/api/todos")
    CODE=${TODOS##*$'\n'}
    IDEAS=$(printf '%s' "${TODOS%$'\n'*}" | "${RUNTIME_VENV:-$APP/.venv}/bin/python" -c \
      'import json,sys
d = json.load(sys.stdin)
t = d.get("todos") if isinstance(d, dict) else d
if not isinstance(t, list): raise SystemExit(1)
print(len(t))' 2>/dev/null)
    [ "$CODE" = "200" ] && [ -n "$IDEAS" ] && return 0
    sleep 1
  done
  return 1
}

if [ "${1:-}" = "--status" ]; then
  echo "app dir:    $APP"
  echo "running:    $(running_sha)"
  git -C "$REPO" fetch -q origin 2>/dev/null
  echo "origin/main: $(git -C "$REPO" rev-parse --short origin/main)"
  DEPLOYED_REF=$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1])).get("ref","origin/main"))' \
    "$APP/.brutus-deploy.json" 2>/dev/null || echo "origin/main")
  echo "deployed ref: $DEPLOYED_REF"
  if [ "$DEPLOYED_REF" != "origin/main" ]; then
    echo "            NOT origin/main — a plain deploy will replace it"
  fi
  echo "state:      $STATE ($(ls "$STATE" 2>/dev/null | wc -l | tr -d ' ') files)"
  printf "service:    "
  curl -s -m 5 -o /dev/null -w "%{http_code}\n" "http://127.0.0.1:$PORT/api/session/list" || echo "down"
  diff -q "$LOADED_PLIST" "$APP/launchd/$PLIST_NAME" >/dev/null 2>&1 \
    && echo "plist:      in sync with the deployed code" \
    || echo "plist:      DRIFTED from the deployed code — run a deploy"
  exit 0
fi

if [ "$TARGET_REF" != "origin/main" ]; then
  echo "############################################################"
  echo "##  DEPLOYING $TARGET_REF — NOT origin/main"
  echo "##  The next plain ./scripts/deploy.sh replaces this with"
  echo "##  origin/main. Land these commits to make it permanent."
  echo "############################################################"
fi

echo "==> state lives at $STATE (outside every checkout)"
mkdir -p "$STATE"

echo "==> updating the service worktree at $APP"
# A fetch that cannot reach the remote used to print `Repository not found` and
# the deploy carried on, redeployed the artifact already running, and announced
# "deployed <old sha>" — a green deploy that deployed nothing, which is the exact
# failure the rest of this file exists to prevent. Observed 2026-08-08, with the
# cause two layers away: `gh` is the git credential helper and it holds ONE
# active account, so a parallel session switching to the personal account makes
# a work repo read as missing rather than forbidden.
if ! FETCH_ERR=$(git -C "$REPO" fetch origin 2>&1); then
  echo "    cannot reach origin — NOT deploying"
  echo "$FETCH_ERR" | sed 's/^/      /'
  echo "      active gh account: $(gh auth status 2>&1 | grep -B2 'Active account: true' | grep -oE 'account [^ ]+' | head -1)"
  echo "      if that is not the work account: gh auth switch -u justin-fowler_cspd"
  exit 1
fi
if [ -e "$APP/.git" ] && [ -n "$(git -C "$APP" status --porcelain --untracked-files=all)" ]; then
  if "$SCRIPT_DIR/check-deploy-drift.sh" "$APP" "$TARGET_REF" --prepare; then
    echo "    dirty deployed checkout already matches $TARGET_REF; target checkout will reconcile it"
  else
    echo "    FATAL: dirty deployed checkout differs from $TARGET_REF — preserve and land or discard it first"
    git -C "$APP" status --short | sed 's/^/      /'
    exit 1
  fi
fi

# No deployed actor may remain alive while its source checkout changes. This
# is idempotent across the one-time self re-exec below.
if [ "$DEPLOY_SERVICES_STOPPED" != "1" ]; then
  PRE_RESTART_PID=$(service_pid)
  PRE_VOICE_PID=$(job_pid "$VOICE_AGENT_LABEL")
  echo "==> quiescing core and voice before changing the release"
  unload_job "$VOICE_AGENT_LABEL" || { echo "    voice job did not stop"; exit 1; }
  wait_for_port_closed "$VOICE_PORT" || { echo "    voice port did not close"; exit 1; }
  unload_job "$CORE_LABEL" || { echo "    core job did not stop"; exit 1; }
  wait_for_port_closed "$PORT" || { echo "    core port did not close"; exit 1; }
  export BRUTUS_DEPLOY_SERVICES_STOPPED=1
  export BRUTUS_PRE_RESTART_PID="${PRE_RESTART_PID:-}"
  export BRUTUS_PRE_VOICE_PID="${PRE_VOICE_PID:-}"
  DEPLOY_SERVICES_STOPPED=1
else
  PRE_RESTART_PID="${BRUTUS_PRE_RESTART_PID:-}"
  PRE_VOICE_PID="${BRUTUS_PRE_VOICE_PID:-}"
fi
if [ ! -d "$APP/.git" ] && [ ! -f "$APP/.git" ]; then
  mkdir -p "$(dirname "$APP")"
  # Detached on purpose: a named branch here would collide with the primary
  # checkout wanting the same branch, and would drift if anyone committed to it.
  git -C "$REPO" worktree add --detach "$APP" "$TARGET_REF" || exit 1
else
  git -C "$APP" checkout -q --detach "$TARGET_REF" || exit 1
fi
echo "    now at $(running_sha)  $(git -C "$APP" log --oneline -1 --format=%s | cut -c1-60)"

WANT=$(git -C "$REPO" rev-parse --short "$TARGET_REF")
if [ "$(running_sha)" != "$WANT" ]; then
  echo "    FATAL: worktree is at $(running_sha), $TARGET_REF is $WANT"; exit 1
fi

# The process names the artifact it serves. Endpoint reachability can otherwise
# bless yesterday's process after a deploy that changed nothing.
DEPLOYED_AT=$(date -u +%Y-%m-%dT%H:%M:%SZ)
CONFIG_HASH=$(shasum -a 256 "$APP/config.yaml" | awk '{print $1}')
printf '{"sha":"%s","deployed_at":"%s","config_sha256":"%s","ref":"%s"}\n' \
  "$(git -C "$APP" rev-parse HEAD)" "$DEPLOYED_AT" "$CONFIG_HASH" "$TARGET_REF" > "$APP/.brutus-deploy.json"

# This script is a file the checkout above just rewrote, and bash reads a script
# incrementally — the deploy that installed the /api/todos check ran the version
# without it, and reported success. Re-exec once so the code deciding whether
# this deploy is good is the code being deployed.
if [ -z "${BRUTUS_DEPLOY_REEXEC:-}" ]; then
  SELF="$APP/scripts/deploy.sh"
  if [ -f "$SELF" ] && ! cmp -s "$SELF" "$0"; then
    echo "    the deploy script itself changed — re-running the new one"
    BRUTUS_DEPLOY_REEXEC=1 exec "$SELF" "$@"
  fi
fi

# Every commit gets an exact, immutable dependency environment. A prior deploy
# rewrote .venv under a week-old voice worker and mixed two AnyIO versions in
# one process. The runtime symlink moves only after the locked environment and
# production suite pass.
TARGET_SHA=$(git -C "$APP" rev-parse HEAD)
RUNTIME_ROOT="$APP/.venvs/$TARGET_SHA"
RUNTIME_VENV="$APP/.runtime-venv"
[ -f "$APP/uv.lock" ] || { echo "    FATAL: uv.lock is required for an exact runtime"; exit 1; }
INSTALL_LOG=$(mktemp)
mkdir -p "$APP/.venvs"
if [ ! -x "$RUNTIME_ROOT/bin/python" ]; then
  echo "    building immutable runtime $TARGET_SHA"
  "${UV:-/opt/homebrew/bin/uv}" venv "$RUNTIME_ROOT" >/dev/null 2>&1 || exit 1
fi
if ! ( cd "$APP" && UV_PROJECT_ENVIRONMENT="$RUNTIME_ROOT" \
        "${UV:-/opt/homebrew/bin/uv}" sync -q --locked --extra dev --extra voice \
        --no-editable --reinstall-package brutus --python "$RUNTIME_ROOT/bin/python" ) >"$INSTALL_LOG" 2>&1; then
  echo "    install failed:"; tail -5 "$INSTALL_LOG" | sed 's/^/      /'; rm -f "$INSTALL_LOG"; exit 1
fi
rm -f "$INSTALL_LOG"
ln -sfn ".venvs/$TARGET_SHA" "$APP/.runtime-venv.next"
# macOS mv follows an existing directory symlink and moves the source INSIDE
# the old runtime. Replace the link itself atomically instead.
"$RUNTIME_ROOT/bin/python" -c 'import os,sys; os.replace(sys.argv[1], sys.argv[2])' \
  "$APP/.runtime-venv.next" "$RUNTIME_VENV" || exit 1

# Prove the pin BEFORE restarting, not after. This is the check whose absence
# let a green deploy run week-old code.
RESOLVED=$(cd "$STATE" && BRUTUS_CONFIG="$APP/config.yaml" "$RUNTIME_VENV/bin/python" -c 'import brutus,os;print(os.path.realpath(brutus.__file__))' 2>/dev/null)
case "$RESOLVED" in
  "$RUNTIME_ROOT"/*) echo "    imports brutus from immutable runtime $TARGET_SHA" ;;
  *) echo "    FATAL: runtime imports brutus from ${RESOLVED:-nowhere}, not $RUNTIME_ROOT"; exit 1 ;;
esac

# uv's default local-project build cache can survive source-only changes.
# A new venv path alone does not prove that its wheel contains this release.
"$RUNTIME_VENV/bin/python" "$APP/scripts/verify-runtime-package.py" \
  "$APP/brutus" "${RESOLVED%/__init__.py}" || exit 1

# Atlas/Codex adapters receive a separate least-authority credential that can
# append idempotent event receipts but cannot exercise owner state gates.
( cd "$STATE" && BRUTUS_CONFIG="$APP/config.yaml" "$RUNTIME_VENV/bin/python" -c \
  'from brutus.security import configured_adapter_token; configured_adapter_token()' ) || exit 1

# Exercise the exact async HTTPX/AnyIO import path that failed in production.
( cd "$STATE" && "$RUNTIME_VENV/bin/python" -c \
  'import asyncio,httpx; asyncio.run(httpx.AsyncClient().aclose())' ) || exit 1

echo "==> tests, against the code about to run"
TEST_LOG="$STATE/deploy-test-$TARGET_SHA.log"
if ! ( cd "$APP" && BRUTUS_CONFIG="$APP/config.yaml" "$RUNTIME_VENV/bin/python" -m pytest tests/ -q -p no:cacheprovider ) >"$TEST_LOG" 2>&1; then
  echo "    test gate failed; full output: $TEST_LOG"
  tail -60 "$TEST_LOG"
  exit 1
fi
tail -1 "$TEST_LOG"

# Declared here, not at the verification block below, because the sibling-plist
# loop can fail before that point — and a later `FAIL=0` would have wiped it.
FAIL=0
# Capture this before either the plist reload or kickstart. The old readiness
# loop could accept one response from the process being terminated, then see
# two 000s while launchd brought up the replacement.
PRE_RESTART_PID="${PRE_RESTART_PID:-${BRUTUS_PRE_RESTART_PID:-}}"

echo "==> syncing the launchd plist"
# From $APP, NOT $REPO. The shared checkout sits on whatever branch someone left
# it on, so reading the plist from there copies an arbitrary old version over the
# new one — which is exactly what happened: the deploy dutifully reverted the
# very plist change it was deploying. The plist must come from the code that is
# actually being deployed.
# `bootout` returns before launchd has finished tearing the job down, and a
# `bootstrap` that lands in that window fails with "service already loaded".
# A fixed `sleep 1` was the guess here, and it lost: the 2026-08-09 deploy
# reported the tunnel "updated but FAILED TO RELOAD — it is now DOWN", and the
# identical bootstrap succeeded by hand seconds later. Wait for the service to
# actually be gone, the same way the readiness poll below does.
# Canon's durability and authenticated GitHub ingestion are required parts of
# the work surface, not optional operator add-ons. Install them on first deploy;
# the generic sibling loop below continues to avoid starting unrelated jobs.
for REQUIRED in com.clearspeed.brutus-canon-backup.plist com.clearspeed.brutus-canon-github.plist com.clearspeed.brutus-livekit.plist com.clearspeed.brutus-livekit-agent.plist com.clearspeed.brutus-my-notes.plist; do
  SRC="$APP/launchd/$REQUIRED"; DEST="$HOME/Library/LaunchAgents/$REQUIRED"
  if [ ! -f "$DEST" ]; then
    cp "$SRC" "$DEST" || { echo "    ${REQUIRED%.plist}: COULD NOT INSTALL"; FAIL=1; continue; }
    if launchctl bootstrap "gui/$(id -u)" "$DEST" >/dev/null 2>&1; then
      echo "    ${REQUIRED%.plist}: installed and loaded"
    else
      echo "    ${REQUIRED%.plist}: installed but FAILED TO LOAD"; FAIL=1
    fi
  fi
done

SRC_PLIST="$APP/launchd/$PLIST_NAME"
if diff -q "$LOADED_PLIST" "$SRC_PLIST" >/dev/null 2>&1; then
  echo "    already in sync"
else
  cp "$SRC_PLIST" "$LOADED_PLIST" && echo "    updated (was drifted)"
  # A changed plist needs a real reload — kickstart re-runs the OLD definition.
  unload_job "com.clearspeed.brutus"
  launchctl bootstrap "gui/$(id -u)" "$LOADED_PLIST" 2>/dev/null
  RELOADED=1
fi

# The SIBLING agents — tunnel, local LLM, Zoom notes feeder — were never synced
# here, so the tracked plists and the loaded ones drifted apart unnoticed: one
# said StartInterval 300 while the job had run hourly for months, and all three
# still executed scripts out of the shared checkout that the daemon itself was
# moved off. Editing a plist in git has to mean something.
#
# Only ALREADY-INSTALLED jobs are touched. A plist in the repo that is absent
# from ~/Library/LaunchAgents is one Justin has not chosen to run, and a deploy
# is no place to start background agents on someone's laptop.
for SRC in "$APP"/launchd/*.plist; do
  NAME=$(basename "$SRC")
  [ "$NAME" = "$PLIST_NAME" ] && continue
  if [ "$NAME" = "com.clearspeed.brutus-tunnel.plist" ]; then
    launchctl disable "gui/$(id -u)/com.clearspeed.brutus-tunnel" 2>/dev/null || true
    unload_job "com.clearspeed.brutus-tunnel" 2>/dev/null || true
    echo "    com.clearspeed.brutus-tunnel: disabled (Atlas ignored)"
    continue
  fi
  DEST="$HOME/Library/LaunchAgents/$NAME"
  [ -f "$DEST" ] || { echo "    ${NAME%.plist}: not installed, skipping"; continue; }
  if diff -q "$DEST" "$SRC" >/dev/null 2>&1; then
    echo "    ${NAME%.plist}: in sync"
    continue
  fi
  LABEL="${NAME%.plist}"
  cp "$SRC" "$DEST" || { echo "    ${LABEL}: COULD NOT UPDATE"; FAIL=1; continue; }
  unload_job "$LABEL" || echo "    ${LABEL}: still loaded 10s after bootout"
  if launchctl bootstrap "gui/$(id -u)" "$DEST" 2>&1 | sed 's/^/      /'; then
    # bootstrap exiting 0 only means launchd accepted the job. Ask whether it
    # is actually there before saying so — the whole point of this section.
    if launchctl print "gui/$(id -u)/$LABEL" >/dev/null 2>&1; then
      echo "    ${LABEL}: updated and reloaded (was drifted)"
    else
      echo "    ${LABEL}: bootstrap reported success but the job is NOT loaded"
      FAIL=1
    fi
  else
    echo "    ${LABEL}: updated but FAILED TO RELOAD — it is now DOWN"
    FAIL=1
  fi
done

echo "==> restarting"
# kickstart only works on a service that is already loaded. If it is not — say
# a previous deploy booted it out and then skipped bootstrap because the plist
# happened to match — kickstart fails with "Could not find service" and the
# deploy leaves Brutus DOWN. Bootstrap covers both cases; ask launchd rather
# than assuming.
if [ -z "${RELOADED:-}" ]; then
  if launchctl print "gui/$(id -u)/com.clearspeed.brutus" >/dev/null 2>&1; then
    launchctl kickstart -k "gui/$(id -u)/com.clearspeed.brutus" || exit 1
  else
    echo "    service was not loaded — bootstrapping"
    launchctl bootstrap "gui/$(id -u)" "$LOADED_PLIST" || exit 1
  fi
fi

# Require the replacement PID and two consecutive health responses. One 200
# from the process being killed is not readiness for the artifact just deployed.
if ! wait_for_new_actor "$PRE_RESTART_PID"; then
  echo "    replacement actor never became stable"
  FAIL=1
fi

# Start voice only after the old job and health listener are gone. The worker
# and core now resolve the same immutable runtime symlink.
if [ -f "$HOME/Library/LaunchAgents/$VOICE_AGENT_LABEL.plist" ]; then
  PRE_VOICE_PID="${PRE_VOICE_PID:-${BRUTUS_PRE_VOICE_PID:-}}"
  unload_job "$VOICE_AGENT_LABEL" || { echo "    voice job did not stop for cutover"; FAIL=1; }
  if launchctl bootstrap "gui/$(id -u)" "$HOME/Library/LaunchAgents/$VOICE_AGENT_LABEL.plist" >/dev/null 2>&1 \
    && wait_for_voice_actor "$PRE_VOICE_PID"; then
    echo "    $VOICE_AGENT_LABEL: restarted on immutable runtime"
  else
    echo "    $VOICE_AGENT_LABEL: failed to become ready"
    FAIL=1
  fi
fi

echo "==> verifying the layer you actually use"
# Two lists, because a URL either renders the surface or points at it. Naming
# each individually is what failed two deploys in a row: /mobile then /console
# each became a redirect while this block still demanded 200, so the release
# installed, both actors restarted, and the deploy failed its own verification.
# A verifier that outlives its routes is the same class of bug as a green
# deploy that deployed nothing.
for path in "/" "/session"; do
  wait_for_http_200 "http://127.0.0.1:$PORT$path" \
    && echo "    $path 200" || { echo "    $path unavailable"; FAIL=1; }
done
for path in "/console" "/mobile"; do
  CODE=$(curl -s -m 5 -o /dev/null -w "%{http_code}" "http://127.0.0.1:$PORT$path")
  [ "$CODE" = "308" ] \
    && echo "    $path 308 -> /" || { echo "    $path answered $CODE, expected 308"; FAIL=1; }
done
wait_for_http_200 "http://127.0.0.1:$PORT/api/supervisor" \
  && echo "    /api/supervisor 200" || { echo "    /api/supervisor unavailable"; FAIL=1; }

# The pages render from static files and would answer 200 with every database on
# fire. Read something out of SQLite through the API, because that is the failure
# that actually happened: /api/todos returned 500 for hours with 181 ideas intact
# on disk, and the pages stayed green throughout. The suite cannot see this — it
# runs against a scratch state dir now, by design — so the endpoint is the check.
if wait_for_todos; then
  echo "    /api/todos 200 — $IDEAS ideas readable"
else
  echo "    /api/todos ${CODE:-000} — the databases are not being served"; FAIL=1
fi

# WHICH CODE is running, not just that something answers. The first version of
# this script checked the endpoint and the databases and passed cleanly while
# launchd was still executing the launcher from the shared checkout — because
# the plist pointed there. A green deploy that deployed nothing is the whole
# failure mode this file exists to prevent.
PID=$(launchctl print "gui/$(id -u)/com.clearspeed.brutus" 2>/dev/null | grep -oE 'pid = [0-9]+' | grep -oE '[0-9]+' | head -1)
CWD=$(lsof -a -p "${PID:-0}" -d cwd -Fn 2>/dev/null | grep '^n' | cut -c2-)
LIVE_SHA=$(git -C "$APP" rev-parse --short HEAD)
if [ "$CWD" = "$APP" ]; then
  echo "    running from $CWD ($LIVE_SHA)"
else
  echo "    running from ${CWD:-unknown} — EXPECTED $APP"
  echo "    (the plist ProgramArguments probably still points at the old checkout)"
  FAIL=1
fi

# The siblings get the same question asked of the LOADED definition, not the
# file on disk. A job reported "in sync" above was never reloaded, so it can
# still be serving an older plist that launchd read months ago — which is how
# three of them ran out of the shared checkout unnoticed in the first place.
for SRC in "$APP"/launchd/*.plist; do
  NAME=$(basename "$SRC"); LABEL="${NAME%.plist}"
  [ "$NAME" = "$PLIST_NAME" ] && continue
  [ "$NAME" = "com.clearspeed.brutus-tunnel.plist" ] && continue
  [ -f "$HOME/Library/LaunchAgents/$NAME" ] || continue
  if ! DEF=$(launchctl print "gui/$(id -u)/$LABEL" 2>/dev/null); then
    echo "    $LABEL: installed but NOT LOADED"; FAIL=1; continue
  fi
  STRAY=$(printf '%s' "$DEF" | grep -oE '/Users/[^[:space:]"]*\.(sh|py)' | grep -v "^$APP/" | head -1)
  if [ -n "$STRAY" ]; then
    echo "    $LABEL: loaded definition still runs $STRAY"; FAIL=1
  else
    echo "    $LABEL: loaded, runs from $APP"
  fi
done

# State must have SURVIVED, not merely exist. An empty notes pad after a deploy
# is the failure this whole change exists to prevent, and it looks like success.
for f in memory.sqlite todos.sqlite sessions.sqlite supervisor.sqlite; do
  if [ -s "$STATE/$f" ]; then echo "    $f $(du -h "$STATE/$f" | cut -f1)"; else echo "    $f MISSING OR EMPTY"; FAIL=1; fi
done

if [ "$FAIL" -eq 0 ]; then
  DEPLOY_SUCCEEDED=1
  echo "==> deployed $(running_sha)"
else
  echo "==> DEPLOY VERIFICATION FAILED"
  exit 1
fi
