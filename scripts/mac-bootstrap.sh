#!/usr/bin/env bash
# ============================================================
# Ratis — Mac mini M4 Pro bootstrap (dev environment)
# ============================================================
# Idempotent : peut être ré-exécuté sans casser l'état.
# Cible : macOS arm64 (Apple Silicon) — M4 Pro 48 GB.
#
# Usage :
#   bash scripts/mac-bootstrap.sh
#   bash scripts/mac-bootstrap.sh --skip-homebrew
#   bash scripts/mac-bootstrap.sh --skip-docker --skip-paddle-check
#
# Phases :
#   1. Sanity checks (macOS arm64 + Xcode CLI tools)
#   2. Homebrew + CLI tools (uv, node, gh, jq, ripgrep, fd, fzf, psql, openssh)
#   3. GUI casks (Docker Desktop, Cursor)
#   4. Node tooling (eas-cli)
#   5. Git config interactif (user.name + user.email + pull.rebase)
#   6. SSH key generation + GitHub auth
#   7. Repo clone (~/Cursor/Ratis par défaut)
#   8. Workspace deps (npm ci + uv sync)
#   9. Remote Login activation (SSH server Mac)
#  10. Docker Desktop warm + dev stack pull
#  11. PaddleOCR ARM compat probe (optionnel)
#  12. Final hints (étapes manuelles restantes)
# ============================================================

set -euo pipefail

# --- ANSI colors ---------------------------------------------------------------
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

# --- Flags ---------------------------------------------------------------------
SKIP_HOMEBREW=false
SKIP_DOCKER=false
SKIP_PADDLE_CHECK=false

for arg in "$@"; do
  case "$arg" in
    --skip-homebrew)     SKIP_HOMEBREW=true ;;
    --skip-docker)       SKIP_DOCKER=true ;;
    --skip-paddle-check) SKIP_PADDLE_CHECK=true ;;
    -h|--help)
      grep -E '^# ' "$0" | sed 's/^# \{0,1\}//'
      exit 0
      ;;
    *)
      err "Unknown flag: $arg"
      err "Use --help to see available flags."
      exit 2
      ;;
  esac
done

# --- 1. Sanity checks ----------------------------------------------------------
hdr "1. Sanity checks"

UNAME_OUT=$(uname -sm)
if [[ "$UNAME_OUT" != "Darwin arm64" ]]; then
  err "This script targets macOS arm64 (Apple Silicon). Got: $UNAME_OUT"
  exit 1
fi
ok "Running on macOS arm64."

if ! xcode-select -p >/dev/null 2>&1; then
  warn "Xcode Command Line Tools not installed."
  info "Triggering xcode-select --install (popup will appear, accept it then re-run this script)."
  xcode-select --install || true
  err "Re-run this script after the CLI tools install completes."
  exit 1
fi
ok "Xcode Command Line Tools detected at $(xcode-select -p)."

# --- 2. Homebrew + CLI tools ---------------------------------------------------
hdr "2. Homebrew + CLI tools"

if $SKIP_HOMEBREW; then
  warn "Skipping Homebrew install (--skip-homebrew)."
else
  if ! command -v brew >/dev/null 2>&1; then
    info "Installing Homebrew..."
    /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
    ok "Homebrew installed."
  else
    ok "Homebrew already installed."
  fi

  # Add brew shellenv to ~/.zprofile if missing (Apple Silicon path).
  BREW_LINE='eval "$(/opt/homebrew/bin/brew shellenv)"'
  if ! grep -Fxq "$BREW_LINE" ~/.zprofile 2>/dev/null; then
    info "Appending brew shellenv to ~/.zprofile."
    echo "$BREW_LINE" >> ~/.zprofile
  fi

  # Refresh PATH for the rest of this script.
  if [[ -x /opt/homebrew/bin/brew ]]; then
    eval "$(/opt/homebrew/bin/brew shellenv)"
  fi

  info "Installing CLI tools (python@3.12 uv node gh jq ripgrep fd fzf postgresql@16 openssh)..."
  brew install \
    python@3.12 \
    uv \
    node \
    gh \
    jq \
    ripgrep \
    fd \
    fzf \
    postgresql@16 \
    openssh
  ok "CLI tools installed."
fi

# --- 3. GUI casks --------------------------------------------------------------
hdr "3. GUI apps (Docker Desktop, Cursor)"

if $SKIP_HOMEBREW; then
  warn "Skipping casks (--skip-homebrew)."
else
  for cask in docker cursor; do
    if brew list --cask "$cask" >/dev/null 2>&1; then
      ok "$cask already installed."
    else
      info "Installing cask: $cask"
      brew install --cask "$cask"
    fi
  done
fi

# --- 4. Node tooling -----------------------------------------------------------
hdr "4. Node tooling (eas-cli)"

if command -v npm >/dev/null 2>&1; then
  if npm list -g eas-cli >/dev/null 2>&1; then
    ok "eas-cli already installed globally."
  else
    info "Installing eas-cli globally..."
    npm install -g eas-cli
    ok "eas-cli installed."
  fi
else
  warn "npm not found in PATH. Install Node via 'brew install node' if needed, then 'npm install -g eas-cli'."
fi

# --- 5. Git config interactif --------------------------------------------------
hdr "5. Git config (interactive)"

CURRENT_NAME=$(git config --global user.name 2>/dev/null || echo "")
CURRENT_EMAIL=$(git config --global user.email 2>/dev/null || echo "")

read -r -p "Git user.name [$CURRENT_NAME]: " GIT_NAME
GIT_NAME=${GIT_NAME:-$CURRENT_NAME}
if [[ -n "$GIT_NAME" ]]; then
  git config --global user.name "$GIT_NAME"
  ok "git user.name = $GIT_NAME"
else
  warn "Empty user.name — skipped."
fi

read -r -p "Git user.email [$CURRENT_EMAIL]: " GIT_EMAIL
GIT_EMAIL=${GIT_EMAIL:-$CURRENT_EMAIL}
if [[ -n "$GIT_EMAIL" ]]; then
  git config --global user.email "$GIT_EMAIL"
  ok "git user.email = $GIT_EMAIL"
else
  warn "Empty user.email — skipped."
fi

git config --global pull.rebase false
ok "git pull.rebase = false (cohérent workflow Ratis)."

# --- 6. SSH key generation + GitHub auth ---------------------------------------
hdr "6. SSH key + GitHub auth"

SSH_KEY_PATH="$HOME/.ssh/id_ed25519"
if [[ -f "$SSH_KEY_PATH" ]]; then
  ok "SSH key already present at $SSH_KEY_PATH."
else
  info "Generating ed25519 SSH key..."
  mkdir -p "$HOME/.ssh"
  chmod 700 "$HOME/.ssh"
  ssh-keygen -t ed25519 -C "${GIT_EMAIL:-mac-mini}" -f "$SSH_KEY_PATH" -N ""
  ok "SSH key generated."
fi

printf "\n%s---- Public key (copy to GitHub) ----%s\n" "$C_BOLD" "$C_RESET"
cat "${SSH_KEY_PATH}.pub"
printf "%s-------------------------------------%s\n\n" "$C_BOLD" "$C_RESET"

info "Add this key on GitHub: https://github.com/settings/ssh/new"
read -r -p "Press enter once the key is added on GitHub..."

info "Testing SSH auth to GitHub..."
ssh -T -o StrictHostKeyChecking=accept-new git@github.com || true
ok "SSH GitHub probe done (look for 'successfully authenticated' above)."

# --- 7. Repo clone -------------------------------------------------------------
hdr "7. Clone Ratis repo"

DEFAULT_REPO_PATH="$HOME/Cursor/Ratis"
read -r -p "Repo path [$DEFAULT_REPO_PATH]: " REPO_PATH
REPO_PATH=${REPO_PATH:-$DEFAULT_REPO_PATH}

if [[ -d "$REPO_PATH/.git" ]]; then
  ok "Repo already cloned at $REPO_PATH."
else
  info "Cloning Belladynn/ratis into $REPO_PATH..."
  mkdir -p "$(dirname "$REPO_PATH")"
  git clone git@github.com:Belladynn/ratis.git "$REPO_PATH"
  ok "Repo cloned."
fi

cd "$REPO_PATH"

# --- 8. Workspace deps ---------------------------------------------------------
hdr "8. Workspace deps (npm + uv)"

if command -v npm >/dev/null 2>&1; then
  if [[ -d "$REPO_PATH/ratis_client" ]]; then
    info "Running npm ci in ratis_client (locked install)..."
    (cd "$REPO_PATH/ratis_client" && npm ci)
    ok "npm ci done."
  else
    warn "ratis_client/ not found — skipping npm ci."
  fi
else
  warn "npm not found in PATH (open a new shell after Homebrew shellenv was added)."
fi

if command -v uv >/dev/null 2>&1; then
  info "Running uv sync at workspace root..."
  uv sync
  ok "uv sync done."
else
  warn "uv not found in PATH — open a new shell or 'eval \"\$(/opt/homebrew/bin/brew shellenv)\"' first."
fi

# --- 9. Remote Login (SSH server) ----------------------------------------------
hdr "9. Activate Remote Login (SSH server)"

REMOTE_LOGIN_STATUS=$(systemsetup -getremotelogin 2>/dev/null || echo "Remote Login: Off")
if echo "$REMOTE_LOGIN_STATUS" | grep -qi "on"; then
  ok "Remote Login already enabled."
else
  info "Enabling Remote Login (sudo prompt)..."
  if sudo systemsetup -setremotelogin on 2>/dev/null; then
    ok "Remote Login enabled."
  else
    warn "sudo systemsetup blocked (TCC/Full Disk Access on macOS 14+)."
    warn "Manual fallback : System Settings -> General -> Sharing -> Remote Login (toggle on)."
  fi
fi

LOCAL_IP=$(ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null || echo "<unknown>")
info "Mac mini local IP: ${C_BOLD}${LOCAL_IP}${C_RESET}"
info "From Windows : ssh $(whoami)@${LOCAL_IP}"

# --- 10. Docker Desktop --------------------------------------------------------
hdr "10. Docker Desktop + dev stack"

if $SKIP_DOCKER; then
  warn "Skipping Docker phase (--skip-docker)."
else
  warn "Launch Docker Desktop manually now (Cmd+Space → Docker)."
  read -r -p "Press enter once Docker Desktop is running (whale icon in menubar)..."

  if ! docker info >/dev/null 2>&1; then
    err "docker info failed — Docker Desktop not running yet?"
    err "Start Docker Desktop and re-run this script with --skip-homebrew."
    exit 1
  fi
  ok "Docker daemon reachable."

  info "Pulling dev stack images (postgres, redis, ...)"
  (cd "$REPO_PATH" && docker compose pull) || warn "docker compose pull had issues — review above."

  info "Starting postgres + redis in detached mode..."
  (cd "$REPO_PATH" && docker compose up -d postgres redis)
  ok "Dev stack core services started."
  (cd "$REPO_PATH" && docker compose ps)
fi

# --- 11. PaddleOCR ARM compat probe --------------------------------------------
hdr "11. PaddleOCR ARM macOS compat probe"

if $SKIP_PADDLE_CHECK; then
  warn "Skipping paddle check (--skip-paddle-check)."
else
  PROBE_DIR="/tmp/paddle-check-$$"
  info "Probing paddlepaddle>=3.0 + paddleocr install in $PROBE_DIR..."
  mkdir -p "$PROBE_DIR"
  (
    cd "$PROBE_DIR"
    if uv init paddle-check --python 3.12 >/dev/null 2>&1 \
       && cd paddle-check \
       && uv add 'paddlepaddle>=3.0' paddleocr >/dev/null 2>&1; then
      ok "paddlepaddle + paddleocr installed cleanly on ARM macOS."
    else
      warn "paddlepaddle/paddleocr install failed on ARM macOS."
      warn "Fallback : run OCR inside Docker container (multi-arch image, x86 emulation OK)."
      warn "Action : keep PA service Docker-only on Mac mini, do not run worker natively."
    fi
  )
  rm -rf "$PROBE_DIR" 2>/dev/null || true
fi

# --- 12. Final hints -----------------------------------------------------------
hdr "12. Manual steps remaining"

cat <<EOF

${C_BOLD}Next steps (manual, not automatable) :${C_RESET}

  1. ${C_BOLD}Xcode${C_RESET} (App Store, ~15 GB) — required for iOS dev / EAS local builds.

  2. ${C_BOLD}Cursor settings sync${C_RESET} — open Cursor, sign in to your account.

  3. ${C_BOLD}eas login${C_RESET} :
       eas login

  4. ${C_BOLD}Claude Code CLI${C_RESET} — install + auth via your usual method.

  5. ${C_BOLD}SSH bidirectionnel Windows ↔ Mac${C_RESET} :
     - Windows side : enable OpenSSH Server (Settings → Apps → Optional features).
     - Add Windows public key to ~/.ssh/authorized_keys on Mac.
     - Add Mac public key to C:\\Users\\<user>\\.ssh\\authorized_keys on Windows.
     - See docs/mac-mini-migration.md § Phase 3.

  6. ${C_BOLD}Restore env files & local state${C_RESET} via tar/SSH from Windows :
     See docs/mac-mini-migration.md § Phase 4.

  7. ${C_BOLD}Recreate active worktrees${C_RESET} :
     See docs/mac-mini-migration.md § Phase 5.

  8. ${C_BOLD}Restore ~/.claude/${C_RESET} folder (memory + settings + MCP tokens) :
     See docs/mac-mini-migration.md § Phase 8.

  9. ${C_BOLD}Start 16 self-hosted GitHub runners${C_RESET} :
       bash $REPO_PATH/scripts/mac-runners-bootstrap.sh

${C_GREEN}Bootstrap done.${C_RESET} Local IP: ${LOCAL_IP}
EOF
