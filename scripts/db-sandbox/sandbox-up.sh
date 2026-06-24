#!/usr/bin/env bash
# scripts/db-sandbox/sandbox-up.sh — spin a fresh ephemeral Postgres sandbox.
#
# Starts a new `postgres:16` container, restores the most recent snapshot into
# it, and prints `{"sandbox_id": "...", "container": "..."}` on stdout.
# The pipeline (SP4) reaches the DB via `docker exec <container> psql`.
#
# The sandbox_id embeds the creation epoch (`<epoch>-<rand>`) so sandbox-reap.sh
# can compute age without cross-platform date parsing.
#
# Run : ./scripts/db-sandbox/sandbox-up.sh
set -euo pipefail

_DBS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$_DBS_DIR/_common.sh"

# --- concurrency cap -------------------------------------------------------
running="$(docker ps -q --filter "label=$SANDBOX_LABEL" | wc -l | tr -d ' ')"
if [[ "$running" -ge "$MAX_SANDBOXES" ]]; then
  die "sandbox cap reached ($running/$MAX_SANDBOXES) — destroy a sandbox first"
fi

# --- latest snapshot -------------------------------------------------------
latest="$(ls -1t "$SNAPSHOT_DIR"/ratis_prod_*.sql.gz 2>/dev/null | head -1 || true)"
if [[ -z "$latest" ]]; then
  die "no snapshot found in $SNAPSHOT_DIR — run snapshot.sh first"
fi

sandbox_id="$(date +%s)-$RANDOM"
container="$CONTAINER_PREFIX-$sandbox_id"
network="${SANDBOX_NETWORK_PREFIX}_${sandbox_id}"

# --- isolated Docker network (M6 quick win) --------------------------------
# Create a dedicated bridge for *this* sandbox so the restored prod copy is
# unreachable from any other container on the Mac mini. Idempotent — `docker
# network create` errors if the name already exists, which is fine to ignore
# since the sandbox_id embeds epoch+random and collisions are practically zero.
log "Creating isolated network $network ..."
if ! docker network create "$network" --label "$SANDBOX_LABEL" >/dev/null 2>&1; then
  # Tolerate "already exists" (extremely unlikely with epoch-random ids) but
  # die if creation failed for any other reason — we MUST NOT fall back to the
  # default bridge.
  if ! docker network inspect "$network" >/dev/null 2>&1; then
    die "could not create or find network $network"
  fi
  warn "network $network already existed — reusing"
fi

log "Starting sandbox $container ..."
# NOTE — no `-p` host port mapping by design (M6 quick win). The container is
# reachable ONLY via `docker exec` from the host ; nothing is bound on the
# Mac mini's host network.
docker run -d \
  --name "$container" \
  --label "$SANDBOX_LABEL" \
  --network "$network" \
  --memory=1g --cpus=2 \
  -e POSTGRES_USER=ratis \
  -e POSTGRES_PASSWORD=sandbox \
  -e POSTGRES_DB=ratis_prod \
  "$PG_IMAGE" >/dev/null

# --- wait for readiness ----------------------------------------------------
ready=0
for _ in $(seq 1 30); do
  if docker exec "$container" pg_isready -U ratis -d ratis_prod -q 2>/dev/null; then
    ready=1
    break
  fi
  sleep 1
done
if [[ "$ready" -ne 1 ]]; then
  docker rm -f "$container" >/dev/null 2>&1 || true
  docker network rm "$network" >/dev/null 2>&1 || true
  die "sandbox $container did not become ready in time"
fi

# --- restore the snapshot --------------------------------------------------
log "Restoring $latest into $container ..."
if ! gunzip -c "$latest" | docker exec -i "$container" psql -U ratis -d ratis_prod -v ON_ERROR_STOP=1 -q; then
  docker rm -f "$container" >/dev/null 2>&1 || true
  docker network rm "$network" >/dev/null 2>&1 || true
  die "restore failed — sandbox $container destroyed"
fi

ok "Sandbox ready: $container"
printf '{"sandbox_id": "%s", "container": "%s"}\n' "$sandbox_id" "$container"
