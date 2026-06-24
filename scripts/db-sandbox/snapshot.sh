#!/usr/bin/env bash
# scripts/db-sandbox/snapshot.sh — dump the prod DB to the Mac mini.
#
# SSHes to prod, runs `pg_dump | gzip` inside the postgres container, streams
# the gzipped dump back, writes it under SNAPSHOT_DIR, then rotates (keeps the
# SNAPSHOT_KEEP most recent). Read-only on prod.
#
# Run : ./scripts/db-sandbox/snapshot.sh
set -euo pipefail

_DBS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$_DBS_DIR/_common.sh"

# M6 quick win — owner-only perms on the snapshot tree (idempotent).
# Snapshots contain a full prod dump in clear ; restrict to the operator user.
mkdir -p "$SNAPSHOT_DIR"
chmod 700 "$SANDBOX_ROOT" "$SNAPSHOT_DIR"

ts="$(date +%Y%m%d_%H%M%S)"
out="$SNAPSHOT_DIR/ratis_prod_${ts}.sql.gz"

log "Dumping prod DB → $out ..."
# gzip runs ON prod so only the compressed stream traverses SSH.
if ! ssh_prod "cd $PROD_DIR && $COMPOSE_PROD exec -T postgres pg_dump -U ratis -d ratis_prod | gzip -9" > "$out"; then
  rm -f "$out"
  die "snapshot failed — prod dump did not complete"
fi

# A valid gzip of a non-trivial DB is comfortably over 1 KB ; guard against a
# truncated/empty file masquerading as success.
if [[ ! -s "$out" ]] || ! gzip -t "$out" 2>/dev/null || [[ "$(wc -c < "$out")" -lt 1024 ]]; then
  rm -f "$out"
  die "snapshot failed — output missing, too small, or not valid gzip"
fi
chmod 600 "$out"
ok "Snapshot written ($(wc -c < "$out") bytes)"

# --- rotation : drop snapshots older than SNAPSHOT_MAX_AGE_MINUTES ---------
# M6 quick win — RGPD-friendly retention (24 h by default, was 7 days). The
# daily snapshot cron always leaves at least one fresh dump available.
# `-mmin +N` matches files strictly older than N minutes ; default 1440 = 24h.
rotated=0
while IFS= read -r stale; do
  [[ -z "$stale" ]] && continue
  rm -f "$stale"
  log "Rotated out $stale"
  rotated=$(( rotated + 1 ))
done < <(find "$SNAPSHOT_DIR" -maxdepth 1 -type f -name 'ratis_prod_*.sql.gz' -mmin "+${SNAPSHOT_MAX_AGE_MINUTES}" 2>/dev/null || true)
ok "Snapshot rotation done (older than ${SNAPSHOT_MAX_AGE_MINUTES}min purged ; $rotated removed)"
