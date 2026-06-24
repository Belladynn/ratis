#!/usr/bin/env bash
# last-receipts.sh — show the 5 most recent receipts on prod.
#
# Outputs an aligned table : id_short, id_full, created_at, total_amount, has_store.
#
# Usage : ./scripts/ops/last-receipts.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../ops_lib.sh
source "$SCRIPT_DIR/../ops_lib.sh"

print_help() {
  cat <<EOF
last-receipts.sh — show 5 most recent receipts on prod.

Usage : ./scripts/ops/last-receipts.sh [--help]
EOF
}

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
  print_help
  exit 0
fi

if [[ $# -gt 0 ]]; then
  err "Unexpected argument : $1"; print_help; exit 2
fi

log "Querying 5 most recent receipts on $PROD_USER@$PROD_HOST..."

SQL="select substring(id::text,1,8) as id_short, id::text as id_full, created_at, total_amount, store_id is not null as has_store from receipts order by created_at desc limit 5"

# Use the aligned table form (with header) for human-readable output.
ssh_psql_table "$SQL"
