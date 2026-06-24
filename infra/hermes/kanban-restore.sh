#!/usr/bin/env bash
# kanban-restore — rebuild the Hermes kanban from kanban-snapshot.json.
# Best-effort: recreates tickets (title/body/priority/assignee) and re-applies
# status (blocked/done/archived). Comments & event history are NOT restored.
# Deterministic, zero LLM.
#
# There is no native `hermes kanban import`, so we loop `hermes kanban create`
# with --idempotency-key = original ticket id → safe to re-run (no duplicates).
#
# Usage: infra/hermes/kanban-restore.sh [path-to-snapshot.json]
#   DRY=1 infra/hermes/kanban-restore.sh   # print actions without applying
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
JSON="${1:-$REPO_ROOT/infra/hermes/kanban-snapshot.json}"
CONTAINER="${HERMES_CONTAINER:-ratis-hermes}"
DRY="${DRY:-0}"

[ -f "$JSON" ] || { echo "snapshot introuvable: $JSON" >&2; exit 1; }

# Emit one shell-escaped `hermes kanban` invocation per line, from the JSON.
python3 - "$JSON" <<'PY' | while IFS= read -r line; do
import json, sys, shlex
tasks = json.load(open(sys.argv[1]))
SKIP = {"archived"}  # recreate archived too? default: yes, then archive
for t in tasks:
    tid = t.get("id") or ""
    title = (t.get("title") or "").strip()
    if not title:
        continue
    body = (t.get("body") or "")
    prio = t.get("priority")
    asg = t.get("assignee")
    status = t.get("status") or "ready"
    create = ["create", title, "--idempotency-key", tid]
    if body:
        create += ["--body", body]
    if asg:
        create += ["--assignee", asg]
    if prio is not None:
        create += ["--priority", str(prio)]
    if status == "blocked":
        create += ["--initial-status", "blocked"]
    print("CREATE\t" + tid + "\t" + "\t".join(create))
    # post-create status fixups
    if status == "done":
        print("POST\t" + tid + "\tcomplete")
    elif status == "archived":
        print("POST\t" + tid + "\tarchive")
PY
  kind="${line%%$'\t'*}"; rest="${line#*$'\t'}"
  tid="${rest%%$'\t'*}"; args="${rest#*$'\t'}"
  # rebuild arg array (tab-separated)
  IFS=$'\t' read -r -a argv <<< "$args"
  if [ "$kind" = "CREATE" ]; then
    echo "▶ create $tid (${argv[1]:0:50}…)"
    [ "$DRY" = "1" ] || docker exec "$CONTAINER" hermes kanban "${argv[@]}" >/dev/null 2>&1 || echo "  ! create échoué $tid"
  else
    # POST: needs the NEW id (idempotency-key returns it). Resolve by idem-key.
    action="${argv[0]}"
    echo "  → $action $tid"
    if [ "$DRY" != "1" ]; then
      newid=$(docker exec "$CONTAINER" hermes kanban create "dummy" --idempotency-key "$tid" --json 2>/dev/null | python3 -c 'import sys,json;print(json.load(sys.stdin).get("id",""))' 2>/dev/null || true)
      [ -n "$newid" ] && docker exec "$CONTAINER" hermes kanban "$action" "$newid" >/dev/null 2>&1 || echo "  ! $action échoué $tid"
    fi
  fi
done
echo "restore terminé (DRY=$DRY)."
