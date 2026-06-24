#!/usr/bin/env bash
# scripts/tests/test_deploy_prod_dry_run.sh — regression tests for the
# deploy-prod.sh CLI surface, using --dry-run so nothing touches prod.
#
# The script prints every SSH command it WOULD run as
#   DRY-RUN ssh_prod: <command>
# so we can grep the output for the expected docker compose / pg_dump
# invocations across different --services + --skip-backup combinations.
#
# Run : ./scripts/tests/test_deploy_prod_dry_run.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
DEPLOY="$REPO_ROOT/deploy-prod.sh"

if [[ ! -x "$DEPLOY" ]]; then
  echo "FAIL: $DEPLOY missing or not executable" >&2
  exit 1
fi

# Force colors off so log output is grep-friendly.
export NO_COLOR=1

PASS=0
FAIL=0

assert_contains() {
  local label="$1" haystack="$2" needle="$3"
  if [[ "$haystack" == *"$needle"* ]]; then
    echo "  ok  $label"
    PASS=$((PASS + 1))
  else
    echo "  FAIL  $label"
    echo "        missing : $needle"
    FAIL=$((FAIL + 1))
  fi
}

assert_not_contains() {
  local label="$1" haystack="$2" needle="$3"
  if [[ "$haystack" != *"$needle"* ]]; then
    echo "  ok  $label"
    PASS=$((PASS + 1))
  else
    echo "  FAIL  $label"
    echo "        unexpected : $needle"
    FAIL=$((FAIL + 1))
  fi
}

assert_exit() {
  local label="$1" expected="$2" actual="$3"
  if [[ "$expected" == "$actual" ]]; then
    echo "  ok  $label"
    PASS=$((PASS + 1))
  else
    echo "  FAIL  $label (expected exit $expected, got $actual)"
    FAIL=$((FAIL + 1))
  fi
}

# --- 1) --services pa --dry-run --------------------------------------------
echo "== --services pa --dry-run =="
out=$("$DEPLOY" --services pa --dry-run 2>&1) || true
assert_contains "builds product_analyser + worker" \
  "$out" \
  "docker compose -f docker-compose.prod.yml --env-file .env.prod build product_analyser product_analyser_worker"
assert_contains "includes pg_dump pipeline (default = backup on)" "$out" "pg_dump -U ratis -d ratis_prod"
assert_contains "writes to /var/backups/ratis/pre_deploy_" "$out" "/var/backups/ratis/pre_deploy_"

# --- 2) --services auth,rewards --dry-run ----------------------------------
echo "== --services auth,rewards --dry-run =="
out=$("$DEPLOY" --services auth,rewards --dry-run 2>&1) || true
assert_contains "builds auth + rewards"  "$out" "build auth rewards"
assert_contains "ups -d auth + rewards"  "$out" "up -d auth rewards"
assert_not_contains "no product_analyser in CSV subset" "$out" "product_analyser"

# --- 3) --services unknown --dry-run ---------------------------------------
echo "== --services unknown --dry-run (must exit 2) =="
set +e
out=$("$DEPLOY" --services unknown --dry-run 2>&1)
rc=$?
set -e
assert_exit "exits 2 on unknown shorthand" "2" "$rc"
assert_contains "errors out on unknown shorthand" "$out" "Unknown service shorthand"

# --- 4) --skip-backup --services pa --dry-run ------------------------------
echo "== --skip-backup --services pa --dry-run =="
out=$("$DEPLOY" --skip-backup --services pa --dry-run 2>&1) || true
# Match the actual SSH invocation (not the warning text that mentions
# "pg_dump pre-deploy backup SKIPPED"). The real command starts with
# 'DRY-RUN ssh_prod: ' and contains 'pg_dump -U ratis'.
assert_not_contains "no pg_dump SSH invocation when --skip-backup" \
  "$out" "pg_dump -U ratis"
assert_not_contains "no /var/backups path when --skip-backup" \
  "$out" "/var/backups/ratis/pre_deploy_"
assert_contains    "still builds PA stack" \
  "$out" "build product_analyser product_analyser_worker"

# --- 5) default (no --services) --dry-run ----------------------------------
echo "== default (all 5 services) --dry-run =="
out=$("$DEPLOY" --dry-run 2>&1) || true
assert_contains "builds all 5 (auth)"            "$out" "auth"
assert_contains "builds all 5 (pa api)"          "$out" "product_analyser"
assert_contains "builds all 5 (pa worker)"       "$out" "product_analyser_worker"
assert_contains "builds all 5 (rewards)"         "$out" "rewards"
assert_contains "builds all 5 (notifier)"        "$out" "notifier"
assert_contains "builds all 5 (list_optimiser)"  "$out" "list_optimiser"
assert_contains "builds all 5 (lo worker)"       "$out" "list_optimiser_worker"
# Migrations run by default (between git-pull and build) — see step 4.5.
assert_contains "default builds migrations image" \
  "$out" "--profile migrate build migrations"
assert_contains "default runs alembic upgrade head" \
  "$out" "--profile migrate run --rm migrations"
assert_contains "default prints alembic_version SELECT" \
  "$out" "SELECT version_num FROM alembic_version"

# --- 6) --skip-migrations --dry-run ----------------------------------------
echo "== --skip-migrations --dry-run =="
out=$("$DEPLOY" --skip-migrations --dry-run 2>&1) || true
# The pre-flight step (alembic heads) still runs — that's not the upgrade.
# The upgrade-specific commands must NOT be emitted.
assert_not_contains "no migrations build when --skip-migrations" \
  "$out" "--profile migrate build migrations"
assert_not_contains "no 'run --rm migrations' (upgrade) when --skip-migrations" \
  "$out" "--profile migrate run --rm migrations\""
assert_not_contains "no alembic_version SELECT when --skip-migrations" \
  "$out" "SELECT version_num FROM alembic_version"
assert_contains "still builds services when --skip-migrations" \
  "$out" "build auth"

# --- 7) --skip-migrations --services pa --dry-run --------------------------
echo "== --skip-migrations --services pa --dry-run =="
out=$("$DEPLOY" --skip-migrations --services pa --dry-run 2>&1) || true
assert_not_contains "no migrations build for PA-only + skip-migrations" \
  "$out" "--profile migrate build migrations"
assert_not_contains "no alembic_version SELECT for PA-only + skip-migrations" \
  "$out" "SELECT version_num FROM alembic_version"
assert_contains "still builds PA stack" \
  "$out" "build product_analyser product_analyser_worker"
assert_contains "still ups PA stack" \
  "$out" "up -d product_analyser product_analyser_worker"

# --- summary ---------------------------------------------------------------
echo
echo "== summary =="
echo "  pass : $PASS"
echo "  fail : $FAIL"

if [[ "$FAIL" -gt 0 ]]; then
  exit 1
fi
exit 0
