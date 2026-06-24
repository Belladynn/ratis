#!/usr/bin/env bash
# prod-status.sh — health snapshot of prod : services + alembic + today's counts.
#
# Sections :
#   1. docker compose ps                  (services + their state)
#   2. select version_num from alembic_version
#   3. counts of receipts/scans/users created today
#
# Usage : ./scripts/ops/prod-status.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../ops_lib.sh
source "$SCRIPT_DIR/../ops_lib.sh"

print_help() {
  cat <<EOF
prod-status.sh — quick health snapshot of Hetzner prod.

Usage : ./scripts/ops/prod-status.sh [--help]

Prints :
  - docker compose ps (services + state)
  - alembic version_num
  - today's counts (receipts, scans, users)
EOF
}

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
  print_help
  exit 0
fi

if [[ $# -gt 0 ]]; then
  err "Unexpected argument : $1"; print_help; exit 2
fi

echo "${C_BOLD}--- Services ---${C_RESET}"
# `docker compose ps` interpolates env vars from services.<x>.environment:
# (POSTGRES_PASSWORD, JWT_SECRET, INTERNAL_API_KEY, ...) which live in
# .env.prod — without --env-file the command bails out with
#   "required variable POSTGRES_PASSWORD is missing a value".
ssh_prod "cd $PROD_DIR && $COMPOSE_PROD ps"

echo
echo "${C_BOLD}--- Alembic version ---${C_RESET}"
ssh_psql "select version_num from alembic_version" || warn "Could not read alembic_version."

echo
echo "${C_BOLD}--- Today's activity (UTC) ---${C_RESET}"
# Note : scans has scanned_at (not created_at) — see db/schema.sql.
# users + receipts both have created_at (verified 2026-04-27).
SQL="select 'receipts' as kind, count(*) from receipts where created_at >= current_date
     union all
     select 'scans',    count(*) from scans    where scanned_at >= current_date
     union all
     select 'users',    count(*) from users    where created_at >= current_date"
ssh_psql_table "$SQL" || warn "Could not query today's counts."

echo
ok "Prod status snapshot complete."
