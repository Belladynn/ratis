#!/usr/bin/env bash
# ============================================================
# Ratis — Mac mini self-hosted GitHub runners bootstrap
# ============================================================
# Idempotent : peut être ré-exécuté.
# Prérequis : `mac-bootstrap.sh` déjà run (repo cloné, Docker Desktop up).
#
# Usage :
#   bash scripts/mac-runners-bootstrap.sh
#   bash scripts/mac-runners-bootstrap.sh --repo-path ~/Cursor/Ratis
#
# Démarre 16 runners self-hosted (ratis-runner-1 … ratis-runner-16)
# via runner/docker-compose.yml.
# ============================================================

set -euo pipefail

if [[ -t 1 ]]; then
  C_RESET=$'\033[0m'
  C_GREEN=$'\033[0;32m'
  C_YELLOW=$'\033[0;33m'
  C_RED=$'\033[0;31m'
  C_BLUE=$'\033[0;34m'
  C_BOLD=$'\033[1m'
else
  C_RESET="" C_GREEN="" C_YELLOW="" C_RED="" C_BLUE="" C_BOLD=""
fi

ok()    { printf "%s[OK]%s %s\n" "$C_GREEN" "$C_RESET" "$*"; }
info()  { printf "%s[..]%s %s\n" "$C_BLUE"  "$C_RESET" "$*"; }
warn()  { printf "%s[!!]%s %s\n" "$C_YELLOW" "$C_RESET" "$*"; }
err()   { printf "%s[XX]%s %s\n" "$C_RED"   "$C_RESET" "$*" >&2; }
hdr()   { printf "\n%s== %s ==%s\n" "$C_BOLD" "$*" "$C_RESET"; }

REPO_PATH="${HOME}/Cursor/Ratis"
EXPECTED_RUNNERS=16

while [[ $# -gt 0 ]]; do
  case "$1" in
    --repo-path)
      REPO_PATH="$2"
      shift 2
      ;;
    -h|--help)
      grep -E '^# ' "$0" | sed 's/^# \{0,1\}//'
      exit 0
      ;;
    *)
      err "Unknown flag: $1"
      exit 2
      ;;
  esac
done

# --- 1. Repo presence ---------------------------------------------------------
hdr "1. Verify repo + runner dir"

if [[ ! -d "$REPO_PATH/runner" ]]; then
  err "Runner directory not found: $REPO_PATH/runner"
  err "Run scripts/mac-bootstrap.sh first, or pass --repo-path <abs_path>."
  exit 1
fi
ok "Found $REPO_PATH/runner"

# --- 2. Docker daemon ---------------------------------------------------------
hdr "2. Verify Docker is running"

if ! docker info >/dev/null 2>&1; then
  err "docker info failed — Docker Desktop not running?"
  err "Start Docker Desktop (whale icon in menubar) then re-run this script."
  exit 1
fi
ok "Docker daemon reachable."

# --- 3. PAT GitHub ------------------------------------------------------------
hdr "3. GitHub Personal Access Token"

ENV_FILE="$REPO_PATH/runner/.env"

if [[ -f "$ENV_FILE" ]] && grep -q "^ACCESS_TOKEN=" "$ENV_FILE" \
     && [[ -n "$(grep '^ACCESS_TOKEN=' "$ENV_FILE" | cut -d'=' -f2-)" ]]; then
  ok "ACCESS_TOKEN already present in runner/.env"
else
  cat <<EOF

${C_BOLD}Create a GitHub PAT (classic) :${C_RESET}
  1. Open: https://github.com/settings/tokens
  2. Generate new token (classic)
  3. Scope required : ${C_BOLD}repo${C_RESET}
  4. Copy the token (starts with 'ghp_').

EOF
  read -r -s -p "Paste GitHub PAT (input hidden): " PAT
  echo
  if [[ -z "$PAT" ]]; then
    err "Empty PAT — aborting."
    exit 1
  fi
  printf "ACCESS_TOKEN=%s\n" "$PAT" > "$ENV_FILE"
  chmod 600 "$ENV_FILE"
  ok "ACCESS_TOKEN written to runner/.env (chmod 600)."
fi

# --- 4. Compose up ------------------------------------------------------------
hdr "4. docker compose up -d (16 runners)"

(cd "$REPO_PATH/runner" && docker compose up -d)
ok "Compose up issued. Waiting 10s for containers to boot..."
sleep 10

# --- 5. Verify count ----------------------------------------------------------
hdr "5. Verify runner count"

(cd "$REPO_PATH/runner" && docker compose ps)

ACTUAL=$(docker ps --format '{{.Names}}' | grep -c '^ratis-runner-' || true)
if [[ "$ACTUAL" -eq "$EXPECTED_RUNNERS" ]]; then
  ok "$ACTUAL/$EXPECTED_RUNNERS runners up."
else
  warn "Expected $EXPECTED_RUNNERS runners, found $ACTUAL."
  warn "Inspect with: cd $REPO_PATH/runner && docker compose logs --tail 50"
fi

# --- 6. Final hints -----------------------------------------------------------
hdr "6. Verify on GitHub"

cat <<EOF

Open: ${C_BOLD}https://github.com/Belladynn/ratis/settings/actions/runners${C_RESET}

You should see ${C_BOLD}ratis-runner-1 … ratis-runner-${EXPECTED_RUNNERS}${C_RESET} all ${C_GREEN}online (idle)${C_RESET}.

If some are missing :
  - Check container logs : ${C_BOLD}cd $REPO_PATH/runner && docker compose logs runner-N${C_RESET}
  - Verify PAT scope = repo
  - Run cleanup : ${C_BOLD}bash scripts/cleanup-ghost-runners.sh${C_RESET}

To stop all runners cleanly (proper deregistration, ~120s) :
  cd $REPO_PATH/runner && docker compose down

${C_GREEN}Runners bootstrap done.${C_RESET}
EOF
