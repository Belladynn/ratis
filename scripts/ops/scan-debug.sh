#!/usr/bin/env bash
# scan-debug.sh — fetch admin debug payload + worker logs for a receipt.
#
# If no receipt_id is given, picks the most recent one from prod DB.
# Saves worker logs to .tmp-worker-logs-<short_id>.txt and runs the Python
# viewer (scripts/scan_debug_viewer.py) which writes .scan-debug-<short_id>.html
# embedding the worker logs.
#
# Usage :
#   ./scripts/ops/scan-debug.sh                    # auto-pick latest receipt
#   ./scripts/ops/scan-debug.sh <receipt_id>       # specific UUID
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../ops_lib.sh
source "$SCRIPT_DIR/../ops_lib.sh"

print_help() {
  cat <<EOF
scan-debug.sh — render a debug view for a prod receipt.

Usage :
  ./scripts/ops/scan-debug.sh                  # latest receipt (queried via SSH psql)
  ./scripts/ops/scan-debug.sh <receipt_id>     # explicit UUID

Outputs (in cwd) :
  .tmp-worker-logs-<short>.txt   raw worker logs from last 24h
  .scan-debug-<short>.html       admin debug payload + worker logs (open in browser)

Requires :
  - tools/.env.local with ADMIN_API_KEY
  - SSH access to $PROD_USER@$PROD_HOST
EOF
}

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
  print_help
  exit 0
fi

if [[ $# -gt 1 ]]; then
  err "Too many args."
  print_help
  exit 2
fi

REPO_ROOT="$(repo_root)"
cd "$REPO_ROOT"

load_tools_env "tools/.env.local"

if [[ -z "${ADMIN_API_KEY:-}" ]]; then
  die "ADMIN_API_KEY missing. Add it to tools/.env.local."
fi

RECEIPT_ID="${1:-}"
if [[ -z "$RECEIPT_ID" ]]; then
  log "No receipt_id given — fetching latest from prod..."
  RECEIPT_ID=$(ssh_psql "select id from receipts order by created_at desc limit 1" | tr -d '[:space:]')
  if [[ -z "$RECEIPT_ID" ]]; then
    die "No receipts found in prod DB."
  fi
  ok "Latest receipt : $RECEIPT_ID"
fi

# Basic UUID sanity check (8-4-4-4-12 hex)
if ! [[ "$RECEIPT_ID" =~ ^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$ ]]; then
  die "'$RECEIPT_ID' does not look like a UUID."
fi

SHORT_ID="${RECEIPT_ID:0:8}"
LOG_FILE="$REPO_ROOT/.tmp-worker-logs-$SHORT_ID.txt"

log "Pulling worker logs (last 24h, filtered) into $LOG_FILE..."
# We grep on prod side to keep payload small. Patterns chosen to capture both
# the receipt id mentions and the relevant pipeline stages.
GREP_PAT="${RECEIPT_ID}|ocr.rich_blocks|llm.filter|Receipt.*processed"
ssh_prod "cd $PROD_DIR && $COMPOSE_PROD logs product_analyser_worker --since 24h 2>&1 | grep -E '$GREP_PAT' || true" \
  > "$LOG_FILE"

LOG_BYTES=$(wc -c < "$LOG_FILE" | tr -d '[:space:]')
ok "Worker logs saved ($LOG_BYTES bytes) -> $LOG_FILE"

log "Running scan_debug_viewer.py..."
# Force UTF-8 stdio on Windows cp1252 consoles. ASCII-only output means this
# is mostly defensive, but cheap insurance.
PYTHONIOENCODING=utf-8 python "$REPO_ROOT/scripts/scan_debug_viewer.py" "$RECEIPT_ID"

ok "Done. Open .scan-debug-$SHORT_ID.html in your browser."
log "Worker logs (raw) : $LOG_FILE"
