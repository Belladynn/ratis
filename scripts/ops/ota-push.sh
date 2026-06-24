#!/usr/bin/env bash
# ota-push.sh — eas update with channel-match guard + .env leak guard.
#
# Why the channel guard : an OTA pushed on the wrong channel ships a broken
# bundle to the wrong APK. R34 (CL/EAS discipline). Pre-flight checks :
#   - git fetch + clean tree
#   - HEAD == origin/main
#   - Last APK build channel == arg channel (warns if mismatch)
#   - User confirmation Y/N
#
# Why the .env leak guard (KP-44, lesson 2026-04-30) : Metro/Expo bundler picks
# up `.env` and `.env.local` at the root of the Expo project AT BUNDLE TIME.
# Their EXPO_PUBLIC_* keys override the eas.json `env` block silently. A dev
# `.env.local` pointing to `192.168.1.75:8005` poisoned a `--channel preview`
# bundle and POST uploads went to a non-existent local IP for 30+ minutes —
# Caddy never saw the requests, the cause was invisible without breadcrumbs.
# This script now :
#   1. Temporarily moves .env / .env.local out of ratis_client/ before push
#   2. Passes `--environment <channel>` so EAS picks the right eas.json profile
#   3. Restores via trap on EXIT (success OR failure, even ctrl-C)
#
# Usage : ./scripts/ops/ota-push.sh [preview|production]
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../ops_lib.sh
source "$SCRIPT_DIR/../ops_lib.sh"

print_help() {
  cat <<EOF
ota-push.sh — push an OTA update via 'eas update'.

Usage : ./scripts/ops/ota-push.sh [preview|production]
        (defaults to 'preview' if no arg)

Pre-flight gates :
  1. git fetch && working tree clean
  2. HEAD == origin/main (refuse to OTA from a feature branch)
  3. Last installed APK channel matches the channel argument
  4. User confirmation (Y/N) with commit + channel summary
  5. Stash .env / .env.local out of ratis_client/ (KP-44 leak guard)
     and restore via trap on exit.

Then runs :
  eas update --channel <X> --environment <X> \
             --message "ota: <last commit subject>" --non-interactive

Env : RATIS_YES=1 to skip confirmation prompt.
EOF
}

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
  print_help
  exit 0
fi

CHANNEL="${1:-preview}"
case "$CHANNEL" in
  preview|production) ;;
  *) err "Invalid channel : $CHANNEL"; print_help; exit 2 ;;
esac

REPO_ROOT="$(repo_root)"
cd "$REPO_ROOT"

# Gate 1+2 : git state
require_main_clean

# Gate 3 : channel-match check via 'eas build:list'
log "Checking last installed APK channel via 'eas build:list'..."
CL_DIR="$REPO_ROOT/ratis_client"
if [[ ! -d "$CL_DIR" ]]; then
  die "ratis_client/ not found at $CL_DIR. Are you in the repo root?"
fi

# eas-cli emits human-formatted output by default. Use --json + jq if jq is
# present, otherwise fall back to a grep-based parse. Keep both paths working
# so this script doesn't hard-depend on jq.
#
# Timeout : `eas build:list` can hang for several minutes in some cases
# (eas-cli init + Expo auth refresh, especially on Windows git-bash). Cap
# the call with `timeout` — if it doesn't return by then, fall through to
# the "Could not detect" warning path. Lesson 2026-04-30 : without timeout,
# OTA push #4 was blocked indefinitely until manual `kill`. Override via
# EAS_BUILD_LIST_TIMEOUT env var if 30s isn't enough on a slow link.
EAS_BUILD_LIST_TIMEOUT="${EAS_BUILD_LIST_TIMEOUT:-30}"

LAST_BUILD_CHANNEL=""
if command -v jq >/dev/null 2>&1; then
  if LAST_BUILD_JSON=$(cd "$CL_DIR" && timeout "$EAS_BUILD_LIST_TIMEOUT" eas build:list --platform=android --limit=1 --json --non-interactive 2>/dev/null); then
    LAST_BUILD_CHANNEL=$(echo "$LAST_BUILD_JSON" | jq -r '.[0].channel // empty')
  fi
fi
if [[ -z "$LAST_BUILD_CHANNEL" ]]; then
  # Fallback : parse the human output for "Channel: X"
  if LAST_BUILD_TXT=$(cd "$CL_DIR" && timeout "$EAS_BUILD_LIST_TIMEOUT" eas build:list --platform=android --limit=1 --non-interactive 2>/dev/null); then
    LAST_BUILD_CHANNEL=$(echo "$LAST_BUILD_TXT" | grep -E '^[[:space:]]*Channel:' | head -1 | sed -E 's/.*Channel:[[:space:]]*//')
  fi
fi

if [[ -z "$LAST_BUILD_CHANNEL" ]]; then
  warn "Could not detect last APK channel from 'eas build:list'. Skipping channel-match guard."
  warn "Verify manually that the channel '$CHANNEL' matches the APK installed on test devices."
elif [[ "$LAST_BUILD_CHANNEL" != "$CHANNEL" ]]; then
  err "Channel MISMATCH : last APK was built on channel '$LAST_BUILD_CHANNEL' but you are pushing OTA to '$CHANNEL'."
  err "If you proceed, the OTA will NOT be picked up by the installed APK."
  die "Aborting. Either rebuild the APK on '$CHANNEL' first, or pass the correct channel arg."
else
  ok "Channel match : last APK channel = '$LAST_BUILD_CHANNEL' = OTA target"
fi

# Gate 4 : confirmation
COMMIT_SHA=$(git rev-parse --short HEAD)
COMMIT_SUBJECT=$(git log -1 --pretty=%s)
echo
echo "${C_BOLD}About to push OTA :${C_RESET}"
echo "  commit  : $COMMIT_SHA  $COMMIT_SUBJECT"
echo "  channel : $CHANNEL"
echo
if ! confirm "Continue?"; then
  die "Aborted by user."
fi

# Gate 5 : .env leak guard (KP-44 — see header).
# Move .env / .env.local out of CL_DIR while the bundle is computed, restore
# them via trap on EXIT (success OR failure). Without this, Metro picks up
# the dev URLs at bundle time and the eas.json env block is silently overridden.
declare -a STASHED_ENV_FILES=()
restore_env_files() {
  local rc=$?
  for stashed in "${STASHED_ENV_FILES[@]}"; do
    local original="${stashed%.ota-push-stash-*}"
    if [[ -f "$stashed" ]]; then
      mv -- "$stashed" "$original"
      log "Restored $(basename "$original")"
    fi
  done
  return "$rc"
}
trap restore_env_files EXIT INT TERM

for env_file in "$CL_DIR/.env" "$CL_DIR/.env.local"; do
  if [[ -f "$env_file" ]]; then
    stash_path="${env_file}.ota-push-stash-$$"
    mv -- "$env_file" "$stash_path"
    STASHED_ENV_FILES+=("$stash_path")
    warn "Stashed $(basename "$env_file") aside (will restore after push). KP-44 guard."
  fi
done

# Run the update — pass --environment so EAS reads the right eas.json profile.
log "Running 'eas update --channel $CHANNEL --environment $CHANNEL'..."
( cd "$CL_DIR" && eas update --channel "$CHANNEL" --environment "$CHANNEL" --message "ota: $COMMIT_SUBJECT" --non-interactive )

ok "OTA published on channel '$CHANNEL'."
log "Verify on device : force-stop app -> relaunch (downloads bundle) -> force-stop -> relaunch (applies bundle)."
# trap restores .env / .env.local on script exit.
