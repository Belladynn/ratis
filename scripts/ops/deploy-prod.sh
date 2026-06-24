#!/usr/bin/env bash
# deploy-prod.sh — git pull + (optional pg_dump backup) + rebuild + restart a
# configurable subset of Ratis webservices on the prod VM.
#
# What it does (in order, on the prod VM at $PROD_DIR — defaults to /root/ratis) :
#   1.   Pre-flight  : prod HEAD must be ancestor of origin/main (refuse non-FF)
#   2.   Pre-flight  : alembic graph must be single-headed
#   3.   pg_dump | gzip -9 → /var/backups/ratis/pre_deploy_<TS>.sql.gz
#        (skipped if --skip-backup is passed)
#   4.   git pull --ff-only origin main
#   4.5. docker compose --profile migrate build migrations
#        + docker compose --profile migrate run --rm migrations (alembic upgrade head)
#        + print 'SELECT version_num FROM alembic_version' for confirmation
#        (skipped if --skip-migrations is passed)
#   5.   docker compose build  <selected services>
#   6.   docker compose up -d  <selected services>
#   7.   Tail last 10 lines of logs per service
#   8.   Print summary table (service · last-log-line · OK/FAIL)
#
# The migration step runs AFTER git pull (so the new alembic chain is on
# prod) and BEFORE service rebuild/restart (so the new service code boots
# against the new schema). The standalone ./scripts/ops/migrate-prod.sh remains for
# ad-hoc migrations that don't require a service redeploy.
#
# Service shorthand mapping (see --help) :
#   auth            → auth
#   pa              → product_analyser + product_analyser_worker  (paired)
#   rewards         → rewards
#   notifier        → notifier
#   list_optimiser  → list_optimiser + list_optimiser_worker  (paired)
#
# Default (no --services) = all 5 shorthand keys.
#
# Usage : ./scripts/ops/deploy-prod.sh [--services CSV] [--skip-backup] [--skip-migrations] [--dry-run] [--help]
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../ops_lib.sh
source "$SCRIPT_DIR/../ops_lib.sh"

# --- shorthand → compose-service-name mapping ------------------------------
# One shorthand can resolve to several compose services (e.g. pa = api + worker).
# Order matters for build/up commands (api before worker, by convention).
resolve_services() {
  case "$1" in
    auth)            echo "auth" ;;
    pa)              printf '%s\n' "product_analyser" "product_analyser_worker" ;;
    rewards)         echo "rewards" ;;
    notifier)        echo "notifier" ;;
    list_optimiser)  printf '%s\n' "list_optimiser" "list_optimiser_worker" ;;
    *) return 1 ;;
  esac
}

ALL_SHORTHAND=(auth pa rewards notifier list_optimiser)

print_help() {
  cat <<EOF
deploy-prod.sh — rebuild + restart Ratis prod services (with pre-deploy DB backup).

  Synopsis
    ./scripts/ops/deploy-prod.sh                                       # all 5 services (default)
    ./scripts/ops/deploy-prod.sh --services pa                         # PA + PA worker only
    ./scripts/ops/deploy-prod.sh --services pa,rewards                 # subset (CSV)
    ./scripts/ops/deploy-prod.sh --services auth,notifier,pa,rewards,list_optimiser
    ./scripts/ops/deploy-prod.sh --skip-backup --services pa           # opt out of pg_dump
    ./scripts/ops/deploy-prod.sh --skip-migrations --services pa       # redeploy PA only, no schema change
    ./scripts/ops/deploy-prod.sh --dry-run                             # print SSH commands, do not execute
    ./scripts/ops/deploy-prod.sh --help

  Service shorthand mapping
    auth            → auth
    pa              → product_analyser + product_analyser_worker  (paired)
    rewards         → rewards
    notifier        → notifier
    list_optimiser  → list_optimiser + list_optimiser_worker  (paired)

  Steps performed
    1.   Pre-flight : prod HEAD must be ancestor of origin/main (FF only).
    2.   Pre-flight : 'alembic heads' on prod must return a single head.
    3.   pg_dump | gzip -9 → /var/backups/ratis/pre_deploy_<TS>.sql.gz
         (skipped if --skip-backup is passed). Snapshot stays on the prod VM ;
         use 'scp' afterwards if you need a local copy.
    4.   git pull --ff-only origin main on prod.
    4.5. docker compose --profile migrate build migrations
         + docker compose --profile migrate run --rm migrations (alembic upgrade head)
         + SELECT version_num FROM alembic_version (confirmation)
         (skipped if --skip-migrations is passed).
    5.   docker compose build  <selected services>
    6.   docker compose up -d  <selected services>
    7.   Tail last 10 log lines per service.
    8.   Print summary table : service · last-log-line · OK/FAIL marker.

  Flags
    --services CSV     comma-separated shorthand keys (see mapping above).
                       Defaults to all 5 (auth,pa,rewards,notifier,list_optimiser).
    --skip-backup      skip the pg_dump pre-deploy snapshot. Use for emergency
                       redeploys where a snapshot already exists for this state.
    --skip-migrations  skip the alembic upgrade head step. Use for emergency
                       redeploys where no schema change is involved AND
                       alembic_version is already at the right head.
    --dry-run          print every SSH command instead of executing — for CI /
                       manual validation. Exits 0 once all commands printed.
    --help / -h        this message.

  Relation to migrate-prod.sh
    Alembic upgrade head is now part of ./scripts/ops/deploy-prod.sh by default. The
    standalone ./scripts/ops/migrate-prod.sh remains for ad-hoc migrations that don't
    require a service redeploy.

  Env overrides
    PROD_HOST · PROD_USER · PROD_DIR · SSH_KEY · NO_COLOR · RATIS_YES

  WARNING — touching prod
    Sanity check 'git status' clean + 'HEAD == origin/main' BEFORE running.
    Backup path on prod VM : /var/backups/ratis/pre_deploy_<TS>.sql.gz
EOF
}

# --- CLI parsing ------------------------------------------------------------
SERVICES_CSV=""
SKIP_BACKUP=0
SKIP_MIGRATIONS=0
DRY_RUN=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --help|-h)
      print_help
      exit 0
      ;;
    --services)
      [[ $# -ge 2 ]] || { err "--services requires a value (CSV)."; exit 2; }
      SERVICES_CSV="$2"
      shift 2
      ;;
    --services=*)
      SERVICES_CSV="${1#--services=}"
      shift
      ;;
    --skip-backup)
      SKIP_BACKUP=1
      shift
      ;;
    --skip-migrations)
      SKIP_MIGRATIONS=1
      shift
      ;;
    --dry-run)
      DRY_RUN=1
      shift
      ;;
    --)
      shift
      break
      ;;
    *)
      err "Unexpected argument : $1"
      print_help >&2
      exit 2
      ;;
  esac
done

# Default when no --services arg : all 5 shorthand keys.
if [[ -z "$SERVICES_CSV" ]]; then
  SERVICES_CSV="$(IFS=,; echo "${ALL_SHORTHAND[*]}")"
fi

# Resolve shorthand → compose service names. Reject unknown tokens.
COMPOSE_SERVICES=()
IFS=',' read -r -a SHORTHAND_TOKENS <<< "$SERVICES_CSV"
for token in "${SHORTHAND_TOKENS[@]}"; do
  # trim whitespace
  token="${token#"${token%%[![:space:]]*}"}"
  token="${token%"${token##*[![:space:]]}"}"
  [[ -n "$token" ]] || continue
  if ! mapped=$(resolve_services "$token"); then
    err "Unknown service shorthand : '$token'. Valid : ${ALL_SHORTHAND[*]}"
    exit 2
  fi
  while IFS= read -r svc; do
    [[ -n "$svc" ]] && COMPOSE_SERVICES+=("$svc")
  done <<< "$mapped"
done

if [[ ${#COMPOSE_SERVICES[@]} -eq 0 ]]; then
  err "No services resolved from --services '$SERVICES_CSV'."
  exit 2
fi

SERVICES_STR="${COMPOSE_SERVICES[*]}"

# --- helper : run-or-print --------------------------------------------------
# In --dry-run mode we print 'DRY-RUN ssh_prod: <cmd>' and do NOT execute.
run_ssh_prod() {
  local cmd="$1"
  if [[ "$DRY_RUN" == "1" ]]; then
    printf 'DRY-RUN ssh_prod: %s\n' "$cmd"
    return 0
  fi
  ssh_prod "$cmd"
}

# --- announce ---------------------------------------------------------------
if [[ "$DRY_RUN" == "1" ]]; then
  log "DRY-RUN mode — no SSH command will be executed."
fi
log "Target : $PROD_USER@$PROD_HOST:$PROD_DIR"
log "Services : $SERVICES_STR"
if [[ "$SKIP_BACKUP" == "1" ]]; then
  warn "pg_dump pre-deploy backup SKIPPED (--skip-backup)."
else
  log "pg_dump pre-deploy backup : enabled (gzip -9 → /var/backups/ratis/pre_deploy_<TS>.sql.gz)"
fi
if [[ "$SKIP_MIGRATIONS" == "1" ]]; then
  warn "alembic upgrade head SKIPPED (--skip-migrations)."
else
  log "migrations : enabled (alembic upgrade head between git-pull and build)"
fi

# --- Step 1 — pre-flight : FF ancestor --------------------------------------
log "Pre-flight : prod HEAD must be ancestor of origin/main..."
if ! run_ssh_prod "set -e; cd $PROD_DIR && git fetch origin main && git merge-base --is-ancestor HEAD origin/main"; then
  die "Prod HEAD is not an ancestor of origin/main (diverged or ahead). Resolve manually : ssh into prod and inspect git log."
fi
ok "Prod is fast-forward-able"

# --- Step 2 — pre-flight : alembic single-head ------------------------------
log "Pre-flight : alembic graph must be single-headed..."
if [[ "$DRY_RUN" == "1" ]]; then
  run_ssh_prod "set -e; cd $PROD_DIR && $COMPOSE_PROD --profile migrate run --rm migrations alembic heads"
else
  heads_count="$(ssh_prod "set -e; cd $PROD_DIR && $COMPOSE_PROD --profile migrate run --rm migrations alembic heads 2>/dev/null | grep -c '(head)' || true")"
  heads_count="$(printf '%s' "$heads_count" | tr -d '[:space:]')"
  if [[ -z "$heads_count" ]] || [[ "$heads_count" == "0" ]]; then
    warn "Could not determine alembic heads count (output empty). Proceeding — verify manually if you doubt."
  elif [[ "$heads_count" != "1" ]]; then
    die "Alembic graph has $heads_count heads (expected 1). Divergent migration branches — fix before deploying."
  else
    ok "Alembic single-headed"
  fi
fi

# --- Step 3 — pg_dump backup ------------------------------------------------
# We run 'docker compose exec -T postgres pg_dump | gzip -9' so pg_dump
# executes INSIDE the postgres container (no pg_dump binary required on
# the host). The whole pipeline stays remote so the dump doesn't traverse SSH.
# Output goes to /var/backups/ratis/pre_deploy_<TS>.sql.gz on the prod VM ;
# operator can scp it elsewhere afterwards if needed.
if [[ "$SKIP_BACKUP" != "1" ]]; then
  log "Snapshotting prod DB pre-deploy..."
  ts="$(date +%Y%m%d_%H%M%S)"
  backup_path="/var/backups/ratis/pre_deploy_${ts}.sql.gz"
  run_ssh_prod "set -e; mkdir -p /var/backups/ratis && cd $PROD_DIR && $COMPOSE_PROD exec -T postgres pg_dump -U ratis -d ratis_prod | gzip -9 > $backup_path && ls -lh $backup_path"
  ok "DB snapshot saved to prod:$backup_path"
else
  warn "Skipping pg_dump (per --skip-backup)."
fi

# --- Step 4 — pull main on prod ---------------------------------------------
log "Pulling latest main on prod..."
run_ssh_prod "set -e; cd $PROD_DIR && git pull --ff-only origin main"
ok "Prod is on latest origin/main"

# --- Step 4.5 — apply pending migrations -----------------------------------
# Build the migrations image FIRST (it carries the latest alembic versions/),
# then run alembic upgrade head. Must happen AFTER git pull (so the new
# alembic chain is on prod) and BEFORE service rebuild/restart (so new
# service code starts against the new schema).
#
# Skippable via --skip-migrations for emergency redeploys where no schema
# change is involved AND alembic_version is already at the right head.
if [[ "$SKIP_MIGRATIONS" != "1" ]]; then
  log "Building migrations image..."
  run_ssh_prod "set -e; cd $PROD_DIR && $COMPOSE_PROD --profile migrate build migrations"
  ok "Migrations image built"

  log "Running alembic upgrade head..."
  run_ssh_prod "set -e; cd $PROD_DIR && $COMPOSE_PROD --profile migrate run --rm migrations"
  ok "Migrations applied"

  # Sanity : print the current head so operator can confirm it matches
  # alembic/versions/<latest>.py revision id.
  log "Current alembic version on prod :"
  run_ssh_prod "cd $PROD_DIR && $COMPOSE_PROD exec -T postgres psql -U ratis -d ratis_prod -At -c \"SELECT version_num FROM alembic_version;\""
else
  warn "Skipping alembic upgrade (per --skip-migrations). Service restart will use new code against the schema as it currently stands — make sure that's intentional."
fi

# --- Step 5 — build images --------------------------------------------------
log "Building images for : $SERVICES_STR"
run_ssh_prod "set -e; cd $PROD_DIR && $COMPOSE_PROD build $SERVICES_STR"
ok "Images built"

# --- Step 6 — restart services ----------------------------------------------
log "Restarting services : $SERVICES_STR"
run_ssh_prod "set -e; cd $PROD_DIR && $COMPOSE_PROD up -d $SERVICES_STR"
ok "Services restarted"

# --- Step 7 — tail logs (per service) ---------------------------------------
log "Tailing last 10 log lines per service (boot confirmation)..."
for svc in "${COMPOSE_SERVICES[@]}"; do
  printf '\n--- %s ---\n' "$svc"
  run_ssh_prod "cd $PROD_DIR && $COMPOSE_PROD logs --tail=10 $svc" \
    || warn "logs command failed for $svc (service may still be starting)"
done

# --- Step 8 — summary table -------------------------------------------------
if [[ "$DRY_RUN" != "1" ]]; then
  log "Summary :"
  printf '\n  %-30s %-6s %s\n' "service" "state" "last log line"
  printf '  %-30s %-6s %s\n'   "-------" "-----" "-------------"
  for svc in "${COMPOSE_SERVICES[@]}"; do
    last_line="$(ssh_prod "cd $PROD_DIR && $COMPOSE_PROD logs --tail=1 $svc 2>/dev/null" 2>/dev/null || true)"
    last_line="${last_line#*| }"
    if [[ ${#last_line} -gt 70 ]]; then
      last_line="${last_line:0:67}..."
    fi
    if [[ -n "$last_line" ]]; then
      printf '  %-30s %-6s %s\n' "$svc" "OK" "$last_line"
    else
      printf '  %-30s %-6s %s\n' "$svc" "FAIL" "(no logs returned)"
    fi
  done
  echo
fi

ok "Deploy complete."
