#!/usr/bin/env bash
# eas-build.sh — kick off an Android EAS build (preview or production).
#
# Pre-flight gates : working tree clean + HEAD == origin/main + Y/N confirm.
# Runs --no-wait so the script returns immediately with a build URL.
#
# Usage : ./scripts/ops/eas-build.sh [preview|production]
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../ops_lib.sh
source "$SCRIPT_DIR/../ops_lib.sh"

print_help() {
  cat <<EOF
eas-build.sh — start an Android EAS build.

Usage : ./scripts/ops/eas-build.sh [preview|production]
        (defaults to 'preview' if no arg)

Pre-flight gates :
  1. git fetch && working tree clean
  2. HEAD == origin/main
  3. User confirmation (Y/N)

Then runs :
  eas build --platform=android --profile=<X> --non-interactive --no-wait

Env : RATIS_YES=1 to skip confirmation prompt.
EOF
}

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
  print_help
  exit 0
fi

PROFILE="${1:-preview}"
case "$PROFILE" in
  preview|production) ;;
  *) err "Invalid profile : $PROFILE"; print_help; exit 2 ;;
esac

REPO_ROOT="$(repo_root)"
cd "$REPO_ROOT"

require_main_clean

CL_DIR="$REPO_ROOT/ratis_client"
[[ -d "$CL_DIR" ]] || die "ratis_client/ not found at $CL_DIR."

COMMIT_SHA=$(git rev-parse --short HEAD)
COMMIT_SUBJECT=$(git log -1 --pretty=%s)
echo
echo "${C_BOLD}About to start EAS build :${C_RESET}"
echo "  commit  : $COMMIT_SHA  $COMMIT_SUBJECT"
echo "  profile : $PROFILE"
echo "  platform: android"
echo
if ! confirm "Continue?"; then
  die "Aborted by user."
fi

log "Running 'eas build --platform=android --profile=$PROFILE --non-interactive --no-wait'..."
# eas build --no-wait prints the build URL on stdout. Capture + display it.
( cd "$CL_DIR" && eas build --platform=android --profile="$PROFILE" --non-interactive --no-wait )

ok "Build started. Watch progress on https://expo.dev — the build URL is printed above."
