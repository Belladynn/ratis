#!/usr/bin/env bash
# scripts/db-sandbox/sandbox-down.sh — destroy a sandbox. Idempotent.
#
# Run : ./scripts/db-sandbox/sandbox-down.sh <sandbox_id>
set -euo pipefail

_DBS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$_DBS_DIR/_common.sh"

if [[ "$#" -ne 1 ]] || [[ -z "${1:-}" ]]; then
  die "usage: sandbox-down.sh <sandbox_id>"
fi
container="$CONTAINER_PREFIX-$1"
network="${SANDBOX_NETWORK_PREFIX}_$1"

# Idempotent by design — re-destroying an already-gone sandbox is a success.
# `docker rm -f` exit codes for an absent container differ across Docker
# versions (older: non-zero ; recent: zero), so probe existence explicitly
# rather than branching on its exit code.
if [[ -n "$(docker ps -aq --filter "name=^${container}$")" ]]; then
  docker rm -f "$container" >/dev/null 2>&1 || true
  ok "Sandbox $container destroyed"
else
  log "Sandbox $container already gone — nothing to do"
fi

# M6 quick win — tear down the isolated network after the container is gone.
# `docker network rm` refuses to delete a network with attached containers, so
# do it AFTER the container removal. Tolerate "no such network" (idempotent).
if [[ -n "$(docker network ls -q --filter "name=^${network}$")" ]]; then
  docker network rm "$network" >/dev/null 2>&1 || true
  ok "Network $network removed"
else
  log "Network $network already gone — nothing to do"
fi

exit 0
