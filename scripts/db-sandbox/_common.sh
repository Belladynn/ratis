#!/usr/bin/env bash
# scripts/db-sandbox/_common.sh — shared constants for the db-sandbox scripts.
#
# Source it from each db-sandbox script :
#   _DBS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
#   source "$_DBS_DIR/_common.sh"
#
# Strict mode is set by the caller (set -euo pipefail).

_DBS_COMMON_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# ops_lib.sh provides log/ok/warn/err/die + colors + ssh_prod + PROD_DIR + COMPOSE_PROD.
source "$_DBS_COMMON_DIR/../ops_lib.sh"

# Where snapshots live on the Mac mini.
SANDBOX_ROOT="${SANDBOX_ROOT:-$HOME/.local/share/ratis/db-sandbox}"
SNAPSHOT_DIR="$SANDBOX_ROOT/snapshots"

# Ephemeral sandbox containers.
CONTAINER_PREFIX="ratis-db-sandbox"
SANDBOX_LABEL="ratis.db-sandbox=1"
PG_IMAGE="postgres:16"

# Tunables.
# M6 quick win — RGPD-friendly retention. Was `SNAPSHOT_KEEP=7` (7 daily dumps).
# Switched to age-based (24 h default) so a `DELETE /account` propagates to the
# snapshot tree within one cron cycle. Daily snapshot.sh always writes a fresh
# dump → at least one snapshot is always available within the window.
SNAPSHOT_MAX_AGE_MINUTES="${SNAPSHOT_MAX_AGE_MINUTES:-1440}"  # 24 h
MAX_SANDBOXES="${MAX_SANDBOXES:-3}"        # concurrent sandbox cap
REAP_AGE_SECONDS="${REAP_AGE_SECONDS:-7200}"  # 2 h — orphan reap threshold

# Docker network name for an isolated sandbox (M6 quick win). One ephemeral
# network per sandbox keeps containers off the default bridge — no other
# container on the Mac mini can reach the restored prod copy.
SANDBOX_NETWORK_PREFIX="ratis_sandbox_isolated"
