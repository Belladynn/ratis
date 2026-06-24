#!/usr/bin/env bash
# /pending_ticket — résumé des tickets kanban non terminés (todo/ready/blocked).
# Exec quick-command : stdout → Telegram, zéro LLM.
set -euo pipefail
hermes kanban list --json --sort priority-desc 2>/dev/null | python3 -c '
import sys, json
try:
    tasks = json.load(sys.stdin)
except Exception:
    print("kanban: lecture impossible"); sys.exit(0)
PENDING = {"todo", "ready", "blocked", "triage", "review"}
EMO = {"todo":"◻", "ready":"▶", "blocked":"⛔", "triage":"🔍", "review":"👁"}
pend = [t for t in tasks if t.get("status") in PENDING]
if not pend:
    print("✅ Aucun ticket en attente."); sys.exit(0)
from collections import Counter
c = Counter(t["status"] for t in pend)
head = " · ".join(f"{EMO.get(s,s)}{s}={n}" for s,n in sorted(c.items()))
print(f"📋 Tickets en attente ({len(pend)}) — {head}\n")
order = {"ready":0,"todo":1,"review":2,"triage":3,"blocked":4}
for t in sorted(pend, key=lambda x:(order.get(x["status"],9), -(x.get("priority") or 0))):
    e = EMO.get(t["status"], "•")
    title = (t.get("title") or "")[:62]
    print(f"{e} {title}")
'
