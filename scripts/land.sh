#!/usr/bin/env bash
# Push this checkout to its own remote, with the credential 1Password holds.
#
# WHY THIS EXISTS
#
# `gh` is the git credential helper and it holds exactly ONE active account.
# This laptop has two: justin-fowler_cspd (work) and justinfowler925 (personal,
# which owns this repo). With the work account active, a push here returns
#
#   remote: Permission to justinfowler925/alicia.git denied to justin-fowler_cspd
#
# and it reads like a missing credential rather than the wrong one. deploy.sh
# already carries that lesson for `fetch` — the same mistake cost half an hour
# on 2026-08-08 — and a push had no equivalent.
#
# `gh auth switch` is the wrong fix. It mutates global state that every other
# session on this machine shares, and deploy.sh names that exact hazard: a
# parallel session switching accounts makes a work repo read as missing. This
# script never touches it. The token comes from 1Password for one invocation
# and reaches git through a credential helper reading the environment, so it is
# never in argv, never in a temp file, and never in the keyring.
#
#   ./scripts/land.sh              # push HEAD to main on the matching remote
#   ./scripts/land.sh --branch b   # push HEAD to some other branch
set -euo pipefail

BRANCH="main"
while [ $# -gt 0 ]; do
  case "$1" in
    --branch) BRANCH="${2:?--branch needs a name}"; shift 2 ;;
    --branch=*) BRANCH="${1#--branch=}"; shift ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

CREDENTIAL_RUN="${CREDENTIAL_RUN:-$HOME/Projects/fowler-brain/scripts/credential-run}"
[ -x "$CREDENTIAL_RUN" ] || { echo "no credential-run at $CREDENTIAL_RUN" >&2; exit 1; }

REMOTE_URL=$(git remote get-url origin)
OWNER=$(printf '%s\n' "$REMOTE_URL" | sed -E 's#.*github\.com[:/]([^/]+)/.*#\1#')

# Which 1Password profile holds the token for this owner. Both are already
# declared in fowler-brain/credentials/profiles.json; this only chooses.
case "$OWNER" in
  justinfowler925) PROFILE="github-personal-mcp"; VAR="GITHUB_PERSONAL_ACCESS_TOKEN" ;;
  clearspeedrevops|clearspeed) PROFILE="atlas-core"; VAR="GH_TOKEN" ;;
  *) echo "no credential profile declared for github.com/$OWNER" >&2; exit 1 ;;
esac

echo "==> $OWNER/$(basename "${REMOTE_URL%.git}")  <-  $(git rev-parse --short HEAD) -> $BRANCH"
echo "    credential: 1Password profile $PROFILE (not the gh keyring, not the active account)"

BEFORE=$(git rev-parse --short "origin/$BRANCH" 2>/dev/null || echo "-")

# The helper reads the token from its own environment, so it never appears in
# argv where `ps` would show it.
# The empty value first is load-bearing: `credential.helper` is a multi-valued
# config, so `-c` APPENDS. Without the reset, git asks the `gh` helper first, it
# answers with the active account, and the 1Password token is never consulted —
# which is exactly the 403 this script exists to avoid, reproduced from inside
# the fix.
"$CREDENTIAL_RUN" "$PROFILE" -- git \
  -c credential.helper= \
  -c "credential.helper=!f() { echo username=x-access-token; echo \"password=\${$VAR}\"; }; f" \
  push origin "HEAD:$BRANCH"

git fetch -q origin
AFTER=$(git rev-parse --short "origin/$BRANCH")
echo "    origin/$BRANCH: $BEFORE -> $AFTER"
[ "$AFTER" = "$(git rev-parse --short HEAD)" ] \
  || { echo "    FATAL: origin/$BRANCH is $AFTER, HEAD is $(git rev-parse --short HEAD)" >&2; exit 1; }
echo "==> landed"
