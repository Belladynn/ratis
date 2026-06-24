#!/usr/bin/env bash
# scripts/db-sandbox/sandbox-reap.sh — destroy sandbox containers older than
# REAP_AGE_SECONDS. Anti-leak net if a dry-run crashes before sandbox-down.
#
# Age is read from the creation epoch embedded in the container name
# (`ratis-db-sandbox-<epoch>-<rand>`) — no cross-platform date parsing.
#
# Run : ./scripts/db-sandbox/sandbox-reap.sh
set -euo pipefail

_DBS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$_DBS_DIR/_common.sh"

now="$(date +%s)"
reaped=0

# `while read` instead of `mapfile` — the latter is bash 4+, the Mac mini ships
# bash 3.2.
names=()
while IFS= read -r line; do
  names+=("$line")
done < <(docker ps -a --filter "label=$SANDBOX_LABEL" --format '{{.Names}}' || true)
for name in "${names[@]}"; do
  [[ -z "$name" ]] && continue
  # name = ratis-db-sandbox-<epoch>-<rand> → 4th `-`-field is the epoch.
  epoch="$(printf '%s' "$name" | cut -d- -f4)"
  rand="$(printf '%s' "$name" | cut -d- -f5)"
  if [[ ! "$epoch" =~ ^[0-9]+$ ]]; then
    warn "skip $name — cannot parse epoch"
    continue
  fi
  age=$(( now - epoch ))
  if [[ "$age" -ge "$REAP_AGE_SECONDS" ]]; then
    docker rm -f "$name" >/dev/null 2>&1 || true
    # M6 quick win — also tear down the isolated network, if present.
    if [[ -n "$rand" ]]; then
      docker network rm "${SANDBOX_NETWORK_PREFIX}_${epoch}-${rand}" >/dev/null 2>&1 || true
    fi
    log "Reaped $name (age ${age}s)"
    reaped=$(( reaped + 1 ))
  fi
done

# Also reap any orphan isolated network whose container is already gone (a
# sandbox-down.sh failure between the container kill and the network rm would
# leave the network behind). Match by label.
orphan_networks=()
while IFS= read -r line; do
  orphan_networks+=("$line")
done < <(docker network ls --filter "label=$SANDBOX_LABEL" --format '{{.Name}}' || true)
for net in "${orphan_networks[@]}"; do
  [[ -z "$net" ]] && continue
  # If no container is attached, `docker network rm` succeeds ; otherwise it
  # silently fails — we tolerate either outcome (idempotent).
  if docker network rm "$net" >/dev/null 2>&1; then
    log "Reaped orphan network $net"
  fi
done

ok "Reap done — $reaped orphan sandbox(es) destroyed"
