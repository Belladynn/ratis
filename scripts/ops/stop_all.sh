#!/usr/bin/env bash
# ============================================================
# Ratis — Arrêter TOUTE la stack du Mac mini
# ============================================================
# Ordre inverse de start_all.sh : runners (désinscription GitHub propre)
# → Hermès perso → Hermès ops → GlitchTip → itops → dev stack.
# Ne touche PAS Docker Desktop ni Ollama (services hôte).
#
# Usage :
#   ./scripts/ops/stop_all.sh            → arrête tout
#   ./scripts/ops/stop_all.sh --dev      → dev stack uniquement
#   ./scripts/ops/stop_all.sh --runners  → runners uniquement
#   ./scripts/ops/stop_all.sh --ops      → itops + GlitchTip + Hermès ops + Hermès perso
#   ./scripts/ops/stop_all.sh --purge    → dev + runners + SUPPRIME leurs volumes (DATA PERDUE)
#                              (ne purge JAMAIS les stacks ops : kanban/incidents)
# ============================================================
set -euo pipefail

# This script lives at scripts/ops/ — repo root is two levels up.
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

MODE="${1:-all}"

log() { printf '\033[1;36m▸\033[0m %s\n' "$*"; }
ok()  { printf '\033[1;32m✓\033[0m %s\n' "$*"; }
err() { printf '\033[1;31m✗\033[0m %s\n' "$*" >&2; }

# compose_down <label> <dir>   (skip-gracieux si absent)
compose_down() {
  local label="$1" dir="$2"
  if [ ! -f "$dir/docker-compose.yml" ] && [ ! -f "$dir/compose.yml" ]; then
    return 0
  fi
  log "[$label] down"
  ( cd "$dir" && docker compose down )
  ok "[$label] down"
}

stop_dev()     { compose_down "dev (postgres+redis)" "$REPO_ROOT"; }
stop_runners() {
  log "Arrêt runners (stop_grace_period — désinscription GitHub propre)"
  compose_down "runners" "$REPO_ROOT/runner"
}
stop_ops() {
  compose_down "hermes-perso (Anna)"  "$HOME/hermes-perso"
  compose_down "hermes-ops (+ proxy)" "$HOME/hermes"
  compose_down "glitchtip"            "$HOME/glitchtip"
  compose_down "itops (n8n + monitoring)" "$REPO_ROOT/infra/itops"
}

purge_dev_runners() {
  log "PURGE — dev + runners + suppression de LEURS volumes (Postgres data perdue)"
  err "Les stacks ops (kanban Hermès, incidents GlitchTip) ne sont PAS purgés."
  read -r -p "Confirmer (oui/NON): " answer
  if [ "$answer" != "oui" ]; then err "Annulé"; exit 1; fi
  ( cd "$REPO_ROOT/runner" && docker compose down -v ) 2>/dev/null || true
  ( cd "$REPO_ROOT"        && docker compose down -v )
  ok "Purge dev + runners complète"
}

case "$MODE" in
  --dev)     stop_dev ;;
  --runners) stop_runners ;;
  --ops)     stop_ops ;;
  --purge)   purge_dev_runners ;;
  all|"")
    # Runners d'abord (grace period → désinscription GitHub), puis le reste.
    stop_runners
    stop_ops
    stop_dev
    ;;
  *)
    err "Mode inconnu: $MODE"
    echo "Usage: $0 [--dev | --runners | --ops | --purge | all]"
    exit 1
    ;;
esac

echo
ok "Arrêté."
