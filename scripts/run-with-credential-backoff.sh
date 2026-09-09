#!/usr/bin/env bash
# Keep launchd from turning a fail-closed credential check into a restart storm.
set -u

if (( $# < 3 )) || [[ "$2" != "--" ]]; then
  printf '%s\n' "usage: $0 <profile> -- <command> [args...]" >&2
  exit 64
fi

profile="$1"
shift 2
credential_run="${CREDENTIAL_RUN:-$HOME/fowler-brain/scripts/credential-run}"
retry_seconds="${BRUTUS_CREDENTIAL_RETRY_SECONDS:-900}"
max_attempts="${BRUTUS_CREDENTIAL_MAX_ATTEMPTS:-0}"
attempts=0

# A service token does not stop op from probing the desktop app's protected
# settings at startup. That probe triggers macOS app-data permission dialogs
# attributed to credential-run's Python process on every daemon restart.
# Background services must use headless credentials, never desktop integration.
export OP_LOAD_DESKTOP_APP_SETTINGS=false
export OP_BIOMETRIC_UNLOCK_ENABLED=false

# launchd does not source ~/.zshenv. Load the existing read-only 1Password
# service account from the login keychain so credential-run never falls back to
# desktop-app authorization prompts. Linux/CI safely skips this macOS step.
security_bin="${BRUTUS_SECURITY_BIN:-/usr/bin/security}"
if [[ -z "${OP_SERVICE_ACCOUNT_TOKEN:-}" && -x "$security_bin" ]]; then
  op_service_account_token="$(
    "$security_bin" find-generic-password \
      -a "${USER:-$(id -un)}" -s OP_SERVICE_ACCOUNT_TOKEN -w 2>/dev/null
  )" || true
  if [[ -n "$op_service_account_token" ]]; then
    export OP_SERVICE_ACCOUNT_TOKEN="$op_service_account_token"
  fi
  unset op_service_account_token
fi

while true; do
  if "$credential_run" "$profile" -- "$@"; then
    exit 0
  else
    rc=$?
  fi

  # EX_CONFIG means the fail-closed credential profile is unavailable. Keep
  # one harmless shell process alive and retry slowly instead of letting
  # launchd respawn a new Python process every ten seconds.
  if (( rc != 78 )); then
    exit "$rc"
  fi
  attempts=$((attempts + 1))
  if (( max_attempts > 0 && attempts >= max_attempts )); then
    exit "$rc"
  fi
  printf '%s\n' "credential profile $profile unavailable; retrying in ${retry_seconds}s" >&2
  sleep "$retry_seconds"
done
