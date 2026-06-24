#!/usr/bin/env bash
# run-prod-batch.sh — run a batch one-shot against the prod DB on Hetzner.
#
# Mirrors the migrate-prod.sh pattern : SSH into the VM, git pull main,
# docker compose --profile batch_<name> run --rm batch_<name> [extra args].
#
# Usage :
#   ./scripts/ops/run-prod-batch.sh <batch-name> [extra args propagated to the script]
#
# Examples :
#   ./scripts/ops/run-prod-batch.sh vrac_seed
#   ./scripts/ops/run-prod-batch.sh consensus --dry-run
#   ./scripts/ops/run-prod-batch.sh purge --dry-run
#   ./scripts/ops/run-prod-batch.sh off_sync --mode delta
#
# Available batches (profile names) :
#   consensus · vrac_seed · off_sync · osm_sync · purge · savings ·
#   referral_payout · mystery_announce · reconciliation · push_receipts ·
#   sirene_sync
#
# Env overrides : PROD_HOST · PROD_USER · PROD_DIR · SSH_KEY · NO_COLOR
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../ops_lib.sh
source "$SCRIPT_DIR/../ops_lib.sh"

print_help() {
  cat <<EOF
run-prod-batch.sh — run a batch one-shot against Hetzner prod DB.

Usage : ./scripts/ops/run-prod-batch.sh <batch-name> [extra args...]

Available batches :
  consensus, vrac_seed, off_sync, osm_sync, purge, savings,
  referral_payout, mystery_announce, reconciliation, push_receipts,
  sirene_sync

Steps :
  1. SSH \$PROD_USER@\$PROD_HOST
  2. cd \$PROD_DIR
  3. git fetch origin main && git pull --ff-only origin main
  4. docker compose --profile batch_<name> run --rm batch_<name> [extra args]

Examples :
  ./scripts/ops/run-prod-batch.sh vrac_seed
  ./scripts/ops/run-prod-batch.sh consensus --dry-run
  ./scripts/ops/run-prod-batch.sh off_sync --mode delta

Env overrides : PROD_HOST · PROD_USER · PROD_DIR · SSH_KEY · NO_COLOR
EOF
}

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
  print_help
  exit 0
fi

if [[ $# -lt 1 ]]; then
  err "Missing batch name."
  print_help
  exit 2
fi

BATCH="$1"; shift

# Validate batch name against the closed list. Catches typos before SSH.
case "$BATCH" in
  consensus|vrac_seed|off_sync|osm_sync|purge|savings|referral_payout|mystery_announce|reconciliation|push_receipts|sirene_sync)
    ;;
  *)
    die "Unknown batch '$BATCH'. Run --help to see available batches."
    ;;
esac

# Quote each remaining arg so spaces / glob chars survive the SSH round-trip
# (printf %q handles bash special chars). Result : single string injected as-is.
ARGS_QUOTED=""
for a in "$@"; do
  ARGS_QUOTED+=" $(printf '%q' "$a")"
done

log "Running batch '$BATCH' on $PROD_USER@$PROD_HOST..."
log "Pulling latest main + executing : $COMPOSE_PROD --profile batch_$BATCH run --rm batch_$BATCH$ARGS_QUOTED"

if ! ssh_prod "set -e; cd $PROD_DIR && git fetch --quiet origin main && git pull --ff-only --quiet origin main && $COMPOSE_PROD --profile batch_$BATCH run --rm batch_$BATCH$ARGS_QUOTED"; then
  die "Batch '$BATCH' failed. Inspect logs : ssh $PROD_USER@$PROD_HOST 'cd $PROD_DIR && docker compose logs batch_$BATCH'"
fi

ok "Batch '$BATCH' completed."
