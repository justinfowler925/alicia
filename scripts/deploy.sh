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

running_sha () { git -C "$APP" rev-parse --short HEAD 2>/dev/null || echo "-"; }
service_pid () {
  launchctl print "gui/$(id -u)/com.clearspeed.brutus" 2>/dev/null \
    | grep -oE 'pid = [0-9]+' | grep -oE '[0-9]+' | head -1
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
    IDEAS=$(printf '%s' "${TODOS%$'\n'*}" | "$APP/.venv/bin/python" -c \
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
  echo "state:      $STATE ($(ls "$STATE" 2>/dev/null | wc -l | tr -d ' ') files)"
  printf "service:    "
  curl -s -m 5 -o /dev/null -w "%{http_code}\n" "http://127.0.0.1:$PORT/api/session/list" || echo "down"
  diff -q "$LOADED_PLIST" "$APP/launchd/$PLIST_NAME" >/dev/null 2>&1 \
    && echo "plist:      in sync with the deployed code" \
    || echo "plist:      DRIFTED from the deployed code — run a deploy"
  exit 0
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
  if "$SCRIPT_DIR/check-deploy-drift.sh" "$APP" origin/main --prepare; then
    echo "    dirty deployed checkout already matches origin/main; target checkout will reconcile it"
  else
    echo "    FATAL: dirty deployed checkout differs from origin/main — preserve and land or discard it first"
    git -C "$APP" status --short | sed 's/^/      /'
    exit 1
  fi
fi
if [ ! -d "$APP/.git" ] && [ ! -f "$APP/.git" ]; then
  mkdir -p "$(dirname "$APP")"
  # Detached on purpose: a named branch here would collide with the primary
  # checkout wanting the same branch, and would drift if anyone committed to it.
  git -C "$REPO" worktree add --detach "$APP" origin/main || exit 1
else
  git -C "$APP" checkout -q --detach origin/main || exit 1
fi
echo "    now at $(running_sha)  $(git -C "$APP" log --oneline -1 --format=%s | cut -c1-60)"

WANT=$(git -C "$REPO" rev-parse --short origin/main)
if [ "$(running_sha)" != "$WANT" ]; then
  echo "    FATAL: worktree is at $(running_sha), origin/main is $WANT"; exit 1
fi

# The process names the artifact it serves. Endpoint reachability can otherwise
# bless yesterday's process after a deploy that changed nothing.
DEPLOYED_AT=$(date -u +%Y-%m-%dT%H:%M:%SZ)
CONFIG_HASH=$(shasum -a 256 "$APP/config.yaml" | awk '{print $1}')
printf '{"sha":"%s","deployed_at":"%s","config_sha256":"%s"}\n' \
  "$(git -C "$APP" rev-parse HEAD)" "$DEPLOYED_AT" "$CONFIG_HASH" > "$APP/.brutus-deploy.json"

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

# The service gets its OWN venv, editable-installed against $APP.
#
# Symlinking the shared venv looked tidy and was a trap: that venv carries an
# editable install pinned to $REPO via a .pth import hook, so `import brutus`
# resolved to the SHARED CHECKOUT no matter where the process ran from. The
# deploy's cwd check passed and meant nothing — Python does not import from the
# working directory when a .pth says otherwise. PYTHONPATH does not override it
# either; the hook wins.
if [ ! -x "$APP/.venv/bin/python" ]; then
  [ -L "$APP/.venv" ] && rm -f "$APP/.venv"
  echo "    building the service venv"
  "${UV:-/opt/homebrew/bin/uv}" venv "$APP/.venv" >/dev/null 2>&1 \
    || python3 -m venv "$APP/.venv" || exit 1
fi
# Re-install every deploy: cheap when nothing changed, and it is what keeps the
# pin pointing at $APP after any checkout.
# [dev,voice]: the deploy runs the suite in this venv, so it needs pytest, and
# the daemon needs the voice extras. A bare `-e .` installs neither.
INSTALL_LOG=$(mktemp)
# The RELATIVE form, run from $APP. Two other spellings were tried and both
# failed while looking like something else:
#   -e "$APP[dev,voice]"    uv: "Empty field is not allowed for PEP508" — and it
#                           left the venv unable to resolve brutus at all, so
#                           the NEXT check blamed the shared checkout.
#   -e "$APP" --extra dev   exits 2, installs nothing.
# `.[dev,voice]` keeps the declaration in pyproject rather than duplicating the
# package list here, where it would drift.
if ! ( cd "$APP" && "${UV:-/opt/homebrew/bin/uv}" pip install -q \
        --python "$APP/.venv/bin/python" -e ".[dev,voice]" ) >"$INSTALL_LOG" 2>&1; then
  echo "    install failed:"; tail -5 "$INSTALL_LOG" | sed 's/^/      /'; rm -f "$INSTALL_LOG"; exit 1
fi
rm -f "$INSTALL_LOG"

# Prove the pin BEFORE restarting, not after. This is the check whose absence
# let a green deploy run week-old code.
# Run the probe FROM $APP. sys.path[0] is the process cwd, so a deploy invoked
# from another brutus checkout (a worktree, ~/Projects/brutus) makes
# `import brutus` resolve there even when the .pth correctly points at $APP —
# and the check then FATAL's on a healthy venv. Earned 2026-08-08.
RESOLVED=$(cd "$APP" && "$APP/.venv/bin/python" -c 'import brutus,os;print(os.path.realpath(brutus.__file__))' 2>/dev/null)
case "$RESOLVED" in
  "$(cd "$APP" && pwd -P)"/*) echo "    imports brutus from $APP" ;;
  *) echo "    FATAL: venv imports brutus from ${RESOLVED:-nowhere}, not $APP"; exit 1 ;;
esac

# Atlas/Codex adapters receive a separate least-authority credential that can
# append idempotent event receipts but cannot exercise owner state gates.
( cd "$APP" && "$APP/.venv/bin/python" -c \
  'from brutus.security import configured_adapter_token; configured_adapter_token()' ) || exit 1

echo "==> tests, against the code about to run"
( cd "$APP" && "$APP/.venv/bin/python" -m pytest tests/ -q -p no:cacheprovider 2>&1 | tail -1 ) || exit 1

# Declared here, not at the verification block below, because the sibling-plist
# loop can fail before that point — and a later `FAIL=0` would have wiped it.
FAIL=0
# Capture this before either the plist reload or kickstart. The old readiness
# loop could accept one response from the process being terminated, then see
# two 000s while launchd brought up the replacement.
PRE_RESTART_PID=$(service_pid)

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
unload_job () {
  local label="$1"
  launchctl bootout "gui/$(id -u)/$label" 2>/dev/null
  for _ in $(seq 1 50); do
    launchctl print "gui/$(id -u)/$label" >/dev/null 2>&1 || return 0
    sleep 0.2
  done
  return 1  # still there after 10s — let the caller report it
}

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

# The LiveKit worker owns child processes and an HTTP health port. Its lifecycle
# is intentionally not folded into a web deploy: forced launchctl restarts can
# overlap the old child and create a Python restart storm. The worker launcher
# reads the deployed app path on its next clean start; keep this deploy quiet.
VOICE_AGENT_LABEL="com.clearspeed.brutus-livekit-agent"
if [ -f "$HOME/Library/LaunchAgents/$VOICE_AGENT_LABEL.plist" ]; then
  echo "    $VOICE_AGENT_LABEL: left running; restart separately after the port is clear"
fi

echo "==> verifying the layer you actually use"
wait_for_http_200 "http://127.0.0.1:$PORT/session" \
  && echo "    /session 200" || { echo "    /session unavailable"; FAIL=1; }
wait_for_http_200 "http://127.0.0.1:$PORT/mobile" \
  && echo "    /mobile 200" || { echo "    /mobile unavailable"; FAIL=1; }
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

[ "$FAIL" -eq 0 ] && echo "==> deployed $(running_sha)" || { echo "==> DEPLOY VERIFICATION FAILED"; exit 1; }
