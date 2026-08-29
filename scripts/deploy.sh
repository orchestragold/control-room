#!/usr/bin/env bash
# deploy.sh — push, pull, restart, smoke test.
#
# Usage: bash scripts/deploy.sh
#
# Fails fast: smoke test runs after restart and exits non-zero if any route
# returns 500 or if auth/CSRF is broken.  Requires scripts/.smoke_session.

set -euo pipefail

BOLD='\033[1m'
GREEN='\033[32m'
RED='\033[31m'
RESET='\033[0m'

SERVER="j9dc6uyrssfn@198.12.233.20"
SSH_KEY="$HOME/.ssh/id_ed25519"
REMOTE_APP="~/control-room"   # evaluated on the server, not locally

echo -e "\n${BOLD}Deploy → portal.orchestragold.com${RESET}"

# 1. Verify working tree is clean before pushing.
if ! git diff --quiet || ! git diff --cached --quiet; then
  echo -e "${RED}✗ Uncommitted changes — stage and commit first.${RESET}" >&2
  exit 1
fi
UNTRACKED=$(git ls-files --others --exclude-standard app/)
if [ -n "$UNTRACKED" ]; then
  echo -e "${RED}✗ Untracked files in app/ — commit or gitignore them first:${RESET}" >&2
  echo "$UNTRACKED" | sed 's/^/  /' >&2
  exit 1
fi

# 2. Push to GitHub.
echo "Pushing to GitHub..."
git push origin main

# 3. Pull on the server and restart Passenger.
echo "Pulling on server and restarting..."
ssh -i "$SSH_KEY" "$SERVER" \
  'cd ~/control-room && git pull origin main && touch tmp/restart.txt'

# 4. Give Passenger a moment to spawn the new worker.
sleep 4

# 5. Smoke test.
echo ""
python3 scripts/smoke_test.py
