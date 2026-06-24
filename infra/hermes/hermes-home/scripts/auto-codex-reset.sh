#!/usr/bin/env bash
# Auto-reset Codex credential when quota has expired (i.e. last_error_reset_at
# has passed) — Hermes does NOT do this on its own, so without this cron the
# operator would have to manually `hermes auth reset openai-codex` or re-login
# every time Codex hits 429.
#
# Idempotent: silent no-op when status is null/active. Logs to stderr only
# when an actual reset happens (visible in `hermes cron list` last-run output).
set -euo pipefail

AUTH_JSON="/opt/data/auth.json"

if [ ! -f "$AUTH_JSON" ]; then
  exit 0  # nothing to do
fi

# Use Hermes' own Python (3.13) to inspect the JSON — no jq dependency.
NEEDS_RESET=$(python3 -c "
import json, time, sys
with open('$AUTH_JSON') as f:
    auth = json.load(f)
creds = auth.get('credential_pool', {}).get('openai-codex', [])
if not creds:
    print('no-credential'); sys.exit(0)
c = creds[0]
status = c.get('last_status')
reset_at = c.get('last_error_reset_at')
now = time.time()
if status == 'exhausted' and reset_at is not None and reset_at < now:
    print('yes')
else:
    print('no')
")

case "$NEEDS_RESET" in
  yes)
    echo "[auto-codex-reset] last_status=exhausted but reset_at has passed — clearing flag" >&2
    hermes auth reset openai-codex >&2
    echo "[auto-codex-reset] reset OK"
    ;;
  no-credential)
    echo "[auto-codex-reset] no openai-codex credential — skipping" >&2
    ;;
  no)
    # silent — most common case
    :
    ;;
esac
