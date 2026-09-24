#!/usr/bin/env bash
# Ensure com.clearspeed.alicia-local-llm is loaded AND actually serving.
#
# The old version printed "kickstarted" and exited 0 without checking anything.
# Meanwhile the job crash-looped 1,346 times with `last exit code = 1`, because a
# hand-started MLX (from a Cursor shell) already owned :7901 and the launchd copy
# could never bind it. So the script reported success, the port worked, and the
# supervision Justin actually wanted did not exist — one Cursor restart from
# losing the model entirely.
set -euo pipefail

LABEL="com.clearspeed.alicia-local-llm"
PLIST="${HOME}/Library/LaunchAgents/${LABEL}.plist"
PORT="${ALICIA_LOCAL_LLM_PORT:-7901}"
TAKEOVER="${1:-}"

if [[ ! -f "$PLIST" ]]; then
  echo "missing $PLIST — run scripts/install-alicia.sh first" >&2
  exit 1
fi
uid="$(id -u)"

job_pid() {
  launchctl print "gui/${uid}/${LABEL}" 2>/dev/null \
    | awk -F'= ' '/^[[:space:]]*pid = /{print $2; exit}'
}

# Anyone holding the port who is NOT the launchd job blocks it forever.
foreign_holder() {
  local jpid holder
  jpid="$(job_pid || true)"
  for holder in $(lsof -ti "tcp:${PORT}" 2>/dev/null || true); do
    [[ -n "$jpid" && "$holder" == "$jpid" ]] && continue
    echo "$holder"
  done
}

holders="$(foreign_holder || true)"
if [[ -n "$holders" ]]; then
  echo "port ${PORT} is held by a non-launchd process:"
  for h in $holders; do ps -o pid=,lstart=,command= -p "$h" | cut -c1-140; done
  if [[ "$TAKEOVER" == "--takeover" ]]; then
    echo "==> --takeover: stopping hand-started holder(s) so launchd can own ${PORT}"
    for h in $holders; do kill "$h" 2>/dev/null || true; done
    for _ in $(seq 1 15); do
      sleep 1
      [[ -z "$(foreign_holder || true)" ]] && break
    done
    if [[ -n "$(foreign_holder || true)" ]]; then
      echo "ERROR: holder(s) still up after SIGTERM — refusing to continue" >&2
      exit 1
    fi
  else
    echo "REFUSING to kickstart: the launchd job would crash-loop against it." >&2
    echo "Re-run with --takeover to stop the hand-started process and hand the" >&2
    echo "port to launchd (that is the point of the job — surviving Cursor exit)." >&2
    exit 1
  fi
fi

if launchctl print "gui/${uid}/${LABEL}" >/dev/null 2>&1; then
  launchctl kickstart -k "gui/${uid}/${LABEL}"
  echo "kickstarted ${LABEL}"
else
  launchctl bootstrap "gui/${uid}" "$PLIST" 2>/dev/null || launchctl load "$PLIST"
  launchctl enable "gui/${uid}/${LABEL}" 2>/dev/null || true
  launchctl kickstart -k "gui/${uid}/${LABEL}" 2>/dev/null || true
  echo "loaded ${LABEL}"
fi

# Verify it SERVES. A loaded job that cannot answer is not a working model.
echo -n "waiting for ${LABEL} on :${PORT} "
for i in $(seq 1 60); do
  if curl -sf --max-time 5 "http://127.0.0.1:${PORT}/v1/models" >/dev/null 2>&1; then
    echo
    echo "OK — :${PORT} serving, launchd pid $(job_pid || echo '?')"
    exit 0
  fi
  echo -n "."
  sleep 2
done
echo
echo "ERROR: ${LABEL} is loaded but :${PORT} never answered." >&2
launchctl print "gui/${uid}/${LABEL}" 2>/dev/null | grep -E 'state|last exit code|path' | head -5 >&2
echo "log: ~/.cursor/logs/alicia-mlx-server.log (or the plist's StandardErrorPath)" >&2
exit 1
