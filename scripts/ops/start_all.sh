#!/usr/bin/env bash
# ============================================================
# Ratis — Démarrer TOUTE la stack du Mac mini (boot orchestrator)
# ============================================================
# Lance, dans l'ordre des dépendances, tout ce qui doit tourner :
#   Ollama (brew) → Docker Desktop → dev stack → itops (n8n+monitoring)
#   → GlitchTip → Hermès ops → Hermès perso (Anna) → runners → ssh-agent
#
# Usage :
#   ./scripts/ops/start_all.sh            → TOUT (défaut)
#   ./scripts/ops/start_all.sh --dev      → dev stack uniquement (postgres + redis)
#   ./scripts/ops/start_all.sh --runners  → runners GH Actions uniquement
#   ./scripts/ops/start_all.sh --ops      → itops + GlitchTip + Hermès ops + Hermès perso
#
# Idempotent : ré-exécutable sans danger. Skip-gracieux si un stack est absent
# (les stacks ~/hermes* et ~/glitchtip sont spécifiques au Mac mini).
# Symétrique : ./scripts/ops/stop_all.sh
# ============================================================
set -euo pipefail

# This script lives at scripts/ops/ — repo root is two levels up.
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

MODE="${1:-all}"

log() { printf '\033[1;36m▸\033[0m %s\n' "$*"; }
ok()  { printf '\033[1;32m✓\033[0m %s\n' "$*"; }
err() { printf '\033[1;31m✗\033[0m %s\n' "$*" >&2; }

# --- Docker Desktop : le démarrer s'il dort (il ne s'auto-lance pas au boot) -
ensure_docker() {
  if docker info >/dev/null 2>&1; then
    ok "Docker daemon déjà up"
    return 0
  fi
  if ! command -v docker >/dev/null 2>&1; then
    err "Docker absent du PATH. Installe Docker Desktop."
    exit 1
  fi
  log "Docker daemon injoignable — démarrage de Docker Desktop…"
  open -a Docker 2>/dev/null || open -a "Docker Desktop" 2>/dev/null || {
    err "Impossible de lancer Docker Desktop (open -a Docker a échoué)"
    exit 1
  }
  log "Attente du daemon (max 120s)…"
  for _ in $(seq 1 60); do
    if docker info >/dev/null 2>&1; then ok "Docker prêt"; return 0; fi
    sleep 2
  done
  err "Docker daemon toujours injoignable après 120s — abandon"
  exit 1
}

# --- Ollama : cerveau Qwen de l'Hermès ops (host, via brew) ------------------
# Non bloquant : si Ollama ne répond pas, seul l'Hermès ops est dégradé.
ensure_ollama() {
  if curl -fs http://localhost:11434/api/tags >/dev/null 2>&1; then
    ok "Ollama up (localhost:11434)"
    return 0
  fi
  if command -v brew >/dev/null 2>&1; then
    log "Démarrage Ollama (brew service)…"
    brew services start ollama >/dev/null 2>&1 || true
    for _ in $(seq 1 15); do
      curl -fs http://localhost:11434/api/tags >/dev/null 2>&1 && { ok "Ollama prêt"; return 0; }
      sleep 1
    done
  fi
  err "Ollama injoignable — l'Hermès ops (Qwen) ne répondra pas tant qu'il n'est pas up"
}

# --- Helper générique : up un stack compose (idempotent, skip-gracieux) ------
# compose_up <label> <dir> [env]   (env = exige un .env, sinon skip)
compose_up() {
  local label="$1" dir="$2" need_env="${3:-}"
  if [ ! -f "$dir/docker-compose.yml" ] && [ ! -f "$dir/compose.yml" ]; then
    err "[$label] compose introuvable dans $dir — skip"
    return 0
  fi
  if [ "$need_env" = "env" ] && [ ! -f "$dir/.env" ]; then
    err "[$label] .env manquant dans $dir (copier .env.example) — skip"
    return 0
  fi
  log "[$label] up → $dir"
  ( cd "$dir" && docker compose up -d )
  ok "[$label] up"
}

# --- Groupes de stacks -------------------------------------------------------
start_dev()    { compose_up "dev (postgres+redis)" "$REPO_ROOT"; }
start_runners(){ compose_up "runners (16× GH Actions)" "$REPO_ROOT/runner" env; }

start_ops() {
  # Ollama d'abord (Hermès ops en dépend), puis l'ordre GlitchTip → Hermès
  # (le glitchtip-proxy embarqué dans ~/hermes attend GlitchTip).
  ensure_ollama
  compose_up "itops (n8n + monitoring)" "$REPO_ROOT/infra/itops" env
  compose_up "glitchtip"                "$HOME/glitchtip"
  compose_up "hermes-ops (+ proxy)"     "$HOME/hermes"
  compose_up "hermes-perso (Anna)"      "$HOME/hermes-perso"
}

# --- ssh-agent + clé prod Hetzner (macOS, via trousseau Keychain) ------------
# Lancé EN DERNIER. Stratégie « passphrase 1× puis silencieux » :
#   - run interactif : si la clé n'est pas encore dans le trousseau, demande la
#     passphrase ET la mémorise (--apple-use-keychain) ;
#   - tout run suivant (y compris un LaunchAgent au boot) : charge la clé
#     silencieusement depuis le trousseau (--apple-load-keychain), zéro prompt.
# La VM Hetzner devient alors SSH-able sans ressaisie après chaque démarrage.
start_ssh_agent() {
  local key_path="$HOME/.ssh/ratis_hetzner_v3"
  # IMPORTANT : on force le ssh-add d'Apple (/usr/bin/ssh-add) qui intègre le
  # trousseau macOS. Celui de Homebrew (souvent 1er dans le PATH) ne connaît
  # PAS --apple-* et casserait le chargement silencieux.
  local ssh_add="/usr/bin/ssh-add"
  [ -x "$ssh_add" ] || ssh_add="$(command -v ssh-add)"

  if [ ! -f "$key_path" ]; then
    err "Clé SSH ratis_hetzner_v3 introuvable ($key_path) — skip ssh-agent"
    return 0
  fi
  if [ -z "${SSH_AUTH_SOCK:-}" ]; then
    eval "$(ssh-agent -s)" >/dev/null
  fi

  # 1) Chargement silencieux depuis le trousseau (passphrase déjà mémorisée).
  "$ssh_add" --apple-load-keychain >/dev/null 2>&1 || true

  # Clé déjà dans l'agent ? (compare les fingerprints)
  local local_fp agent_fps
  local_fp="$(ssh-keygen -lf "$key_path" 2>/dev/null | awk '{print $2}')"
  agent_fps="$("$ssh_add" -l 2>/dev/null | awk '{print $2}')"
  if [ -n "$local_fp" ] && echo "$agent_fps" | grep -qxF "$local_fp"; then
    ok "Clé ratis_hetzner_v3 chargée (trousseau macOS) — SSH Hetzner OK"
    return 0
  fi

  # 2) Pas encore mémorisée (1re fois) : demande la passphrase + la stocke dans
  #    le trousseau → les prochains démarrages seront silencieux.
  if [ ! -t 0 ]; then
    err "Clé pas dans le trousseau et exécution non-interactive (boot/LaunchAgent)."
    err "Lance une fois à la main : /usr/bin/ssh-add --apple-use-keychain $key_path"
    return 0
  fi
  echo
  log "Ajout de la clé ratis_hetzner_v3 (passphrase demandée 1×, puis mémorisée) :"
  "$ssh_add" --apple-use-keychain "$key_path" \
    && ok "Clé ajoutée + passphrase mémorisée — démarrages suivants silencieux" \
    || err "ssh-add a échoué (mauvaise passphrase ?) — retry : /usr/bin/ssh-add --apple-use-keychain $key_path"
}

# --- Orchestration -----------------------------------------------------------
case "$MODE" in
  --dev)
    ensure_docker
    start_dev
    ;;
  --runners)
    ensure_docker
    start_runners
    ;;
  --ops)
    ensure_docker
    start_ops
    ;;
  all|"")
    ensure_docker
    start_dev
    start_ops
    start_runners
    ;;
  *)
    err "Mode inconnu: $MODE"
    echo "Usage: $0 [--dev | --runners | --ops | all]"
    exit 1
    ;;
esac

echo
log "État Docker :"
docker ps --format 'table {{.Names}}\t{{.Status}}' | head -40
echo

start_ssh_agent

echo
ok "Tout est démarré. Arrêt : ./scripts/ops/stop_all.sh"
