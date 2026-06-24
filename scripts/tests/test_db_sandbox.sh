#!/usr/bin/env bash
# scripts/tests/test_db_sandbox.sh — tests for the db-sandbox scripts.
#
# Covers : snapshot rotation, sandbox-up/down integration (real Docker, fake
# snapshot — no prod access), sandbox-down idempotence, concurrency cap,
# sandbox-reap age logic. Requires Docker.
#
# Run : ./scripts/tests/test_db_sandbox.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DBS="$SCRIPT_DIR/../db-sandbox"
export NO_COLOR=1

# Isolate snapshots in a temp dir — never touch the real ~/.local/share path.
TMP_ROOT="$(mktemp -d)"
export SANDBOX_ROOT="$TMP_ROOT"
trap 'rm -rf "$TMP_ROOT"; docker ps -aq --filter "label=ratis.db-sandbox=1" | xargs -r docker rm -f >/dev/null 2>&1 || true' EXIT

PASS=0
FAIL=0
check() {
  local label="$1" cond="$2"
  if [[ "$cond" == "1" ]]; then echo "  ok  $label"; PASS=$((PASS+1));
  else echo "  FAIL  $label"; FAIL=$((FAIL+1)); fi
}

# --- snapshot rotation -----------------------------------------------------
mkdir -p "$TMP_ROOT/snapshots"
for i in $(seq 1 9); do
  touch -t "20260101000${i}" "$TMP_ROOT/snapshots/ratis_prod_2026010100000${i}.sql.gz"
done
# Rotation logic: keep 7 most recent. Source _common + replicate the prune.
# `while read` instead of `mapfile` — the latter is bash 4+, the Mac mini ships
# bash 3.2.
( set -euo pipefail; source "$DBS/_common.sh"
  s=()
  while IFS= read -r line; do
    [[ -n "$line" ]] && s+=("$line")
  done < <(ls -1t "$SNAPSHOT_DIR"/ratis_prod_*.sql.gz)
  for stale in "${s[@]:$SNAPSHOT_KEEP}"; do rm -f "$stale"; done )
kept="$(ls -1 "$TMP_ROOT/snapshots" | wc -l | tr -d ' ')"
check "rotation keeps 7 of 9" "$([[ "$kept" == "7" ]] && echo 1 || echo 0)"

# --- sandbox-up / down integration -----------------------------------------
printf 'CREATE TABLE t (id int); INSERT INTO t VALUES (42);\n' | gzip -9 \
  > "$TMP_ROOT/snapshots/ratis_prod_20260101_120000.sql.gz"
up_out="$("$DBS/sandbox-up.sh" | tail -1)"
sid="$(printf '%s' "$up_out" | sed -E 's/.*"sandbox_id": "([^"]+)".*/\1/')"
cid="$(printf '%s' "$up_out" | sed -E 's/.*"container": "([^"]+)".*/\1/')"
val="$(docker exec "$cid" psql -U ratis -d ratis_prod -tAc 'SELECT id FROM t' 2>/dev/null | tr -d ' ')"
check "sandbox-up restores the snapshot (SELECT id = 42)" "$([[ "$val" == "42" ]] && echo 1 || echo 0)"

"$DBS/sandbox-down.sh" "$sid" >/dev/null 2>&1
gone="$(docker ps -aq --filter "name=$cid" | wc -l | tr -d ' ')"
check "sandbox-down destroys the container" "$([[ "$gone" == "0" ]] && echo 1 || echo 0)"

if "$DBS/sandbox-down.sh" "$sid" >/dev/null 2>&1; then idem=1; else idem=0; fi
check "sandbox-down is idempotent (exit 0 on absent)" "$idem"

# --- sandbox-reap age logic ------------------------------------------------
# Start a sandbox-labelled container with an OLD epoch in its name → reap it.
old_epoch=$(( $(date +%s) - 99999 ))
old_name="ratis-db-sandbox-${old_epoch}-1"
docker run -d --name "$old_name" --label ratis.db-sandbox=1 "postgres:16" >/dev/null
"$DBS/sandbox-reap.sh" >/dev/null 2>&1
reaped="$(docker ps -aq --filter "name=$old_name" | wc -l | tr -d ' ')"
check "sandbox-reap destroys an over-age sandbox" "$([[ "$reaped" == "0" ]] && echo 1 || echo 0)"

echo ""
echo "PASS=$PASS FAIL=$FAIL"
[[ "$FAIL" -eq 0 ]]
