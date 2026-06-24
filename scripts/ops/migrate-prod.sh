#!/usr/bin/env bash
# migrate-prod.sh — run alembic upgrade head against prod DB.
#
# Uses the dedicated `migrations` profile of docker-compose.prod.yml (see PR #141).
# If the migrations service does not exist yet on prod (PR #141 not merged),
# the script falls back to running alembic via the auth container, with a warning.
#
# Default flow (post KP-81 fix) :
#   1. SSH prod, refresh git tree to origin/main (ff-only, refuses divergence)
#   2. Rebuild the migrations image so it embeds the latest alembic/versions/*
#   3. Run `docker compose --profile migrate run --rm migrations`
#   4. Print current alembic version for confirmation
#
# Why the rebuild step (KP-81) :
#   `docker compose run` uses the image as currently built on the host. If prod's
#   git tree is stale, or if the image was built before the new migration file
#   landed in alembic/versions/, the container ships a stale filesystem and
#   `alembic upgrade head` silently no-ops (reports success at the previous head).
#   Pull + rebuild guarantees the container sees the new migration file.
#
# Flags :
#   --no-pull      : skip git pull (use current prod tree as-is — edge cases only)
#   --no-rebuild   : skip migrations image rebuild (use cached image as-is)
#   --help, -h     : show help
#
# Usage : ./scripts/ops/migrate-prod.sh [--no-pull] [--no-rebuild]
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../ops_lib.sh
source "$SCRIPT_DIR/../ops_lib.sh"

print_help() {
  cat <<EOF
migrate-prod.sh — run alembic upgrade head on Hetzner prod.

Usage : ./scripts/ops/migrate-prod.sh [--no-pull] [--no-rebuild] [--help]

Default steps :
  1. SSH $PROD_USER@$PROD_HOST
  2. cd $PROD_DIR
  3. git fetch origin main && git pull --ff-only origin main
  4. docker compose --profile migrate build migrations
  5. docker compose --profile migrate run --rm migrations
     (falls back to : docker compose exec auth uv run alembic upgrade head
      if the migrations profile is not yet defined — PR #141)
  6. Print 'select version_num from alembic_version' to confirm

Flags :
  --no-pull     : skip step 3 (use current prod tree as-is, edge cases only)
  --no-rebuild  : skip step 4 (use cached migrations image as-is)
  --help, -h    : show this help

Why default does pull + rebuild (KP-81) :
  Without git pull, prod's alembic/versions/ may lack newly merged migration
  files. Without image rebuild, the migrations container ships a stale
  filesystem. Both lead to silent no-op « upgrade succeeded » at the previous
  head. Default does both to guarantee correctness ; flags exist for the rare
  cases where you genuinely want to re-run with the existing image (alembic
  metadata corruption recovery, dev DB replay, etc.).

Env overrides : PROD_HOST · PROD_USER · PROD_DIR · SSH_KEY · NO_COLOR
EOF
}

# Argument parsing — accept --no-pull and --no-rebuild in any order.
DO_PULL=1
DO_REBUILD=1
while [[ $# -gt 0 ]]; do
  case "$1" in
    --help|-h)
      print_help
      exit 0
      ;;
    --no-pull)
      DO_PULL=0
      shift
      ;;
    --no-rebuild)
      DO_REBUILD=0
      shift
      ;;
    *)
      err "Unexpected argument : $1"
      print_help
      exit 2
      ;;
  esac
done

log "Running alembic migrations on $PROD_USER@$PROD_HOST"

# Step 3 : refresh git tree on prod. We use --ff-only to refuse divergence —
# if prod has commits that aren't on origin/main, we want a human to inspect
# rather than silently rebase or merge. The `git merge-base --is-ancestor`
# check is belt-and-braces : --ff-only would already refuse, but this gives
# a clearer error message.
if [[ "$DO_PULL" -eq 1 ]]; then
  log "Refreshing git tree on prod (git fetch + ff-only pull)..."
  if ! ssh_prod "set -e; cd $PROD_DIR && git fetch origin main"; then
    die "git fetch failed on prod. Network / SSH key / repo perms?"
  fi
  # Refuse if local prod tree has diverged from origin/main.
  if ! ssh_prod "cd $PROD_DIR && git merge-base --is-ancestor HEAD origin/main"; then
    die "Prod git tree has commits not on origin/main — divergence. Inspect manually before migrating."
  fi
  if ! ssh_prod "set -e; cd $PROD_DIR && git pull --ff-only origin main"; then
    die "git pull --ff-only failed on prod. Possible local edits or divergence."
  fi
  ok "Prod git tree synced to origin/main"
else
  warn "Skipping git pull (--no-pull). Prod tree may be stale relative to origin/main."
fi

# Step 4 : rebuild the migrations image so it sees the latest alembic/versions/.
# Without this, `docker compose run` would use the previously-built image which
# contains a snapshot of the alembic dir from the last build — stale migrations
# directory → silent no-op (KP-81).
if [[ "$DO_REBUILD" -eq 1 ]]; then
  log "Rebuilding migrations image so it embeds the latest alembic/versions/..."
  if ! ssh_prod "set -e; cd $PROD_DIR && $COMPOSE_PROD --profile migrate build migrations"; then
    die "docker compose build migrations failed. Inspect : ssh root@$PROD_HOST 'cd $PROD_DIR && $COMPOSE_PROD build migrations'"
  fi
  ok "migrations image rebuilt"
else
  warn "Skipping image rebuild (--no-rebuild). Migration container ships a possibly stale alembic dir."
fi

# Step 5 : detect whether the migrations profile exists. We grep the prod
# compose file for `migrate` to decide which command to run. Legacy fallback
# kept for any old prod host that hasn't picked up PR #141.
log "Detecting migrations service on prod..."
if ssh_prod "grep -q 'migrate' $PROD_DIR/docker-compose.prod.yml" 2>/dev/null; then
  log "Found migrations profile — using docker compose --profile migrate run --rm migrations"
  if ! ssh_prod "set -e; cd $PROD_DIR && $COMPOSE_PROD --profile migrate run --rm migrations"; then
    die "Migration failed. Inspect logs : ssh root@$PROD_HOST 'cd $PROD_DIR && docker compose logs migrations'"
  fi
else
  warn "No migrations profile found in docker-compose.prod.yml (PR #141 not merged yet)."
  warn "Falling back to : docker compose exec auth uv run alembic upgrade head"
  if ! ssh_prod "set -e; cd $PROD_DIR && $COMPOSE_PROD exec -T auth uv run alembic upgrade head"; then
    die "Migration failed (fallback path). Inspect : ssh root@$PROD_HOST 'cd $PROD_DIR && docker compose logs auth'"
  fi
fi
ok "alembic upgrade head succeeded"

log "Current alembic_version :"
ssh_psql "select version_num from alembic_version" \
  || die "Could not query alembic_version. DB unreachable?"

ok "Migration complete."
