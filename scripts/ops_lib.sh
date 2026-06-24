#!/usr/bin/env bash
# scripts/ops_lib.sh — shared helpers for the ./*.sh ops scripts at repo root.
#
# Source this file early in each ops script :
#   SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
#   source "$SCRIPT_DIR/scripts/ops_lib.sh"
#
# Cross-OS : works on Linux, macOS, and Windows git-bash (MINGW).
# Strict mode is set by the caller (set -euo pipefail) — don't override here.

# --- colors (disabled if NO_COLOR=1 or stdout is not a TTY) -----------------
if [[ "${NO_COLOR:-0}" == "1" ]] || [[ ! -t 1 ]]; then
  C_RESET=""; C_RED=""; C_GREEN=""; C_YELLOW=""; C_CYAN=""; C_BOLD=""
else
  C_RESET=$'\033[0m'
  C_RED=$'\033[1;31m'
  C_GREEN=$'\033[1;32m'
  C_YELLOW=$'\033[1;33m'
  C_CYAN=$'\033[1;36m'
  C_BOLD=$'\033[1m'
fi

log()  { printf '%s>%s %s\n' "$C_CYAN"  "$C_RESET" "$*"; }
ok()   { printf '%s+%s %s\n' "$C_GREEN" "$C_RESET" "$*"; }
warn() { printf '%s!%s %s\n' "$C_YELLOW" "$C_RESET" "$*" >&2; }
err()  { printf '%sx%s %s\n' "$C_RED"   "$C_RESET" "$*" >&2; }
die()  { err "$*"; exit 1; }

# --- prod SSH ---------------------------------------------------------------
PROD_HOST="${PROD_HOST:-46.225.63.79}"
PROD_USER="${PROD_USER:-root}"
# Default to /root/ratis — that's where the live Hetzner VM actually runs
# the repo (vérifié 2026-04-27). The "correct" linux convention would be
# /opt/ratis (cf cloud-init-hetzner.yaml + ARCH_deployment.md), and a future
# migration is planned. Until then, scripts must reflect reality.
PROD_DIR="${PROD_DIR:-/root/ratis}"
SSH_KEY="${SSH_KEY:-$HOME/.ssh/ratis_hetzner_v3}"

# Standard flags for every `docker compose` invocation on prod.
# `-f docker-compose.prod.yml` selects the prod compose file, and
# `--env-file .env.prod` makes secret interpolation work (POSTGRES_PASSWORD,
# JWT_SECRET, INTERNAL_API_KEY, ...) — without it, even read-only commands
# like `ps` fail with "required variable X is missing a value".
COMPOSE_PROD="docker compose -f docker-compose.prod.yml --env-file .env.prod"

# Detect the Windows git-bash environment. The bash-bundled OpenSSH client
# (/usr/bin/ssh from MSYS2) cannot read the Windows ssh-agent service's
# named pipe \\.\pipe\openssh-ssh-agent — only Windows-native ssh.exe
# (C:\Windows\System32\OpenSSH\ssh.exe) can. So when running from git-bash,
# we explicitly invoke ssh.exe to inherit the agent state set up by
# start_all.sh. On Linux/macOS the regular `ssh` is used.
#
# Resolution order :
#   1. If MSYSTEM is set (git-bash signature) AND `ssh.exe` is on PATH → use it.
#   2. Otherwise fall back to plain `ssh`.
_resolve_ssh_bin() {
  # On Windows git-bash (MSYSTEM=MINGW64), `which ssh.exe` / `command -v ssh.exe`
  # resolves to `/usr/bin/ssh.exe` — the git-bash BUNDLED OpenSSH. That binary
  # CANNOT access the Windows ssh-agent service (named pipe
  # `\\.\pipe\openssh-ssh-agent`). We MUST use the Windows-native OpenSSH at
  # /c/Windows/System32/OpenSSH/ssh.exe which knows how to read that named pipe.
  #
  # Lesson 2026-04-27 — PR #148 missed this : `command -v ssh.exe` returns the
  # wrong binary because of PATH ordering in git-bash. Hard-code the Windows
  # OpenSSH path. User can override via `SSH_BIN` env var if their install lives
  # elsewhere (e.g. `C:\Program Files\Git\usr\bin\ssh.exe` is exactly what we
  # DON'T want).
  if [[ -n "${MSYSTEM:-}" ]]; then
    if [[ -n "${SSH_BIN:-}" ]] && [[ -x "$SSH_BIN" ]]; then
      echo "$SSH_BIN"
      return
    fi
    local win_ssh="/c/Windows/System32/OpenSSH/ssh.exe"
    if [[ -x "$win_ssh" ]]; then
      echo "$win_ssh"
      return
    fi
  fi
  echo "ssh"
}

# Run a remote command. Falls back with a clear message if SSH fails.
# Usage : ssh_prod 'cd /root/ratis && docker compose ps'
ssh_prod() {
  local cmd="$1"
  if [[ ! -f "$SSH_KEY" ]]; then
    die "SSH key not found at $SSH_KEY. Set SSH_KEY env var or generate the key."
  fi
  local ssh_bin
  ssh_bin="$(_resolve_ssh_bin)"
  # -o BatchMode=yes : fail fast if the agent doesn't have the key (no prompt loop)
  # -o ConnectTimeout=10 : avoid 75s default in case the host is down
  # We allow the user to pre-load the key in ssh-agent (see start_all.sh) so no
  # passphrase prompt is needed in steady state.
  if ! "$ssh_bin" -i "$SSH_KEY" \
        -o BatchMode=yes \
        -o ConnectTimeout=10 \
        -o StrictHostKeyChecking=accept-new \
        "$PROD_USER@$PROD_HOST" "$cmd"; then
    err "SSH failed. Check $SSH_KEY + ssh-agent (Windows : run start_all.sh to load the key)."
    return 1
  fi
}

# Run psql on prod against ratis_prod DB. Quiet, no banner, expanded format off.
# Usage : ssh_psql 'select 1'
ssh_psql() {
  local sql="$1"
  ssh_prod "cd $PROD_DIR && $COMPOSE_PROD exec -T postgres psql -U ratis -d ratis_prod -At -F $'\t' -c \"$sql\""
}

# Same as ssh_psql but with an aligned table output (no -A flag, with header).
# Usage : ssh_psql_table 'select id, created_at from receipts limit 5'
ssh_psql_table() {
  local sql="$1"
  ssh_prod "cd $PROD_DIR && $COMPOSE_PROD exec -T postgres psql -U ratis -d ratis_prod -c \"$sql\""
}

# --- env loading ------------------------------------------------------------
# Load tools/.env.local into the current shell (export-only known keys).
# Idempotent : if already loaded, no-op.
load_tools_env() {
  local env_file="${1:-tools/.env.local}"
  if [[ ! -f "$env_file" ]]; then
    return 0
  fi
  # shellcheck disable=SC1090
  set -a
  source "$env_file"
  set +a
}

# --- generic helpers --------------------------------------------------------
confirm() {
  # Prompt Y/N. Return 0 if yes, 1 otherwise. Honors RATIS_YES=1 for non-interactive.
  local prompt="${1:-Continue?}"
  if [[ "${RATIS_YES:-0}" == "1" ]]; then
    log "$prompt [auto-yes via RATIS_YES=1]"
    return 0
  fi
  local reply
  printf '%s%s%s [y/N] ' "$C_YELLOW" "$prompt" "$C_RESET"
  # Read from /dev/tty so stdin pipes don't break the prompt
  if [[ -r /dev/tty ]]; then
    read -r reply </dev/tty
  else
    read -r reply
  fi
  [[ "$reply" =~ ^[Yy]$ ]]
}

# Verify HEAD is on origin/main and clean. Aborts if not.
require_main_clean() {
  log "Checking git state (HEAD == origin/main + clean tree)..."
  git fetch --quiet origin main || die "git fetch failed."
  local head origin_main
  head=$(git rev-parse HEAD)
  origin_main=$(git rev-parse origin/main)
  if [[ "$head" != "$origin_main" ]]; then
    die "HEAD ($head) != origin/main ($origin_main). Switch to main + pull before running this script."
  fi
  if [[ -n "$(git status --porcelain)" ]]; then
    die "Working tree is dirty. Commit or stash before running this script."
  fi
  ok "git state clean — HEAD == origin/main ($head)"
}

# Print a help block read from a heredoc style. Usage : show_help "$@"
# scripts call : if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then print_help; exit 0; fi
# Each script defines its own print_help function.

# --- repo root --------------------------------------------------------------
# Resolve the repo root from the calling script. Use as :
#   REPO_ROOT="$(repo_root)"
repo_root() {
  git rev-parse --show-toplevel 2>/dev/null || pwd
}
