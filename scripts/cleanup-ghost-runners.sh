#!/usr/bin/env bash
# ============================================================
# Cleanup ghost (offline) GitHub Actions runners.
# ============================================================
# Symptom: `docker compose down/up` accumulates offline runner
# entries on GitHub over time. Usually caused by:
#  - Missing stop_grace_period → SIGKILL before deregistration
#  - Wrong RUNNER_NAME propagation → fallback to random suffix
#
# Usage:
#   scripts/cleanup-ghost-runners.sh                 # dry-run (shows count)
#   scripts/cleanup-ghost-runners.sh --confirm       # actually delete
#
# Requires `gh` CLI authenticated with admin scope on the repo.
# ============================================================
set -euo pipefail

REPO="${REPO:-Belladynn/ratis}"
CONFIRM="${1:-}"

echo "Repo: $REPO"
echo "Fetching offline runners..."

ids=$(gh api "repos/$REPO/actions/runners" --paginate \
  --jq '.runners[] | select(.status == "offline") | .id')

count=$(echo "$ids" | grep -c . || true)
echo "Offline runners found: $count"

if [ "$count" = "0" ]; then
  echo "Nothing to clean. ✅"
  exit 0
fi

if [ "$CONFIRM" != "--confirm" ]; then
  echo ""
  echo "Dry-run. Run with --confirm to actually delete."
  echo "Example: $0 --confirm"
  exit 0
fi

echo "Deleting $count offline runners..."
deleted=0
for id in $ids; do
  if gh api --method DELETE "repos/$REPO/actions/runners/$id" --silent 2>/dev/null; then
    deleted=$((deleted + 1))
    [ $((deleted % 50)) -eq 0 ] && echo "  $deleted / $count deleted..."
  fi
done
echo "✅ Deleted $deleted / $count offline runners."
