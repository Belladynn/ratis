#!/usr/bin/env bash
# kanban-snapshot — dump the Hermes kanban to versioned JSON + a human-readable
# MD, then commit (and push) if changed. Deterministic, zero LLM.
#
# WHY: the kanban tickets double as living documentation (decisions, follow-ups,
# pending arbitrations). The kanban.db lives only in the container (~/.hermes,
# gitignored, local) — a machine loss = tickets gone. This snapshot makes the
# ticket DATA versioned + git-diffable + re-importable (see kanban-restore.sh).
#
# The cron/automation around this is disposable (trivial to recreate). The DATA
# is what matters — so this writes into the git repo, the durable source.
#
# Run: from the repo root, or via a host scheduler. NOT in-container (needs git).
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
JSON="$REPO_ROOT/infra/hermes/kanban-snapshot.json"
MD="$REPO_ROOT/infra/hermes/kanban-snapshot.md"
CONTAINER="${HERMES_CONTAINER:-ratis-hermes}"
PUSH="${KANBAN_SNAPSHOT_PUSH:-1}"   # set 0 to commit without pushing

# 1. Export (all statuses incl. archived) — deterministic, no LLM.
docker exec "$CONTAINER" hermes kanban list --json --archived --sort priority-desc \
  > "$JSON.tmp" 2>/dev/null
python3 -m json.tool "$JSON.tmp" > "$JSON" && rm -f "$JSON.tmp"   # pretty-print, stable diffs

# 2. Human-readable MD (so the snapshot is readable in a PR / on GitHub).
python3 - "$JSON" "$MD" <<'PY'
import json, sys, datetime
tasks = json.load(open(sys.argv[1]))
out = sys.argv[2]
from collections import Counter, defaultdict
by = defaultdict(list)
for t in tasks:
    by[t.get("status", "?")].append(t)
order = ["ready", "todo", "running", "review", "triage", "blocked", "scheduled", "done", "archived"]
emo = {"ready":"▶","todo":"◻","running":"⏳","review":"👁","triage":"🔍",
       "blocked":"⛔","scheduled":"🕒","done":"✅","archived":"🗄"}
lines = [
    "# Kanban snapshot",
    "",
    f"> Auto-généré par `infra/hermes/kanban-snapshot.sh`. Source de vérité = ce JSON",
    f"> (`kanban-snapshot.json`), ré-importable via `kanban-restore.sh`. Ne PAS éditer à la main.",
    "",
    f"- Total tickets : **{len(tasks)}**",
    "- Par statut : " + " · ".join(f"{emo.get(s,s)}{s}={len(by[s])}" for s in order if by.get(s)),
    "",
]
for s in order:
    if not by.get(s):
        continue
    lines.append(f"## {emo.get(s,s)} {s} ({len(by[s])})")
    for t in sorted(by[s], key=lambda x: -(x.get("priority") or 0)):
        tid = t.get("id", "?")
        title = (t.get("title") or "").strip()
        asg = t.get("assignee") or "—"
        lines.append(f"- `{tid}` · {title}  _(assignee: {asg})_")
    lines.append("")
open(out, "w").write("\n".join(lines) + "\n")
print(f"{len(tasks)} tickets → {out}")
PY

# 3. Commit only if changed; push unless disabled.
cd "$REPO_ROOT"
git add infra/hermes/kanban-snapshot.json infra/hermes/kanban-snapshot.md
if git diff --cached --quiet; then
  echo "kanban-snapshot: aucun changement, rien à committer."
  exit 0
fi
STAMP="$(date -u +%Y-%m-%d)"
git commit -m "chore(kanban): snapshot $STAMP" >/dev/null
echo "kanban-snapshot: committé ($STAMP)."
if [ "$PUSH" = "1" ]; then
  git push >/dev/null 2>&1 && echo "kanban-snapshot: pushé." || echo "kanban-snapshot: push échoué (commit local OK)."
fi
