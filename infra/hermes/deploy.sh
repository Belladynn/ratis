#!/usr/bin/env bash
#
# deploy.sh — reconstruct the Hermes-on-Mac-mini stack from this versioned dir.
#
# Idempotent: re-runnable. Copies the versioned (secret-stripped) sources into
# their runtime homes (~/.hermes, ~/hermes, ~/glitchtip), renders the templated
# config from Keychain secrets, and prints the manual steps that cannot be
# automated (OAuth re-auth, Telegram pairing, pasting routines into Claude.ai).
#
# Secrets are READ from macOS Keychain (service `ratis-agent-mcp`), never written
# here. If a secret is missing, the corresponding ${VAR} is left as-is and a
# warning is printed — the run does not abort.
#
# Usage:  bash infra/hermes/deploy.sh
#
set -euo pipefail

# ───────────────────────────── locations ─────────────────────────────
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HERMES_HOME="${HOME}/.hermes"
HERMES_COMPOSE="${HOME}/hermes"
GLITCHTIP_HOME="${HOME}/glitchtip"
KEYCHAIN_SVC="ratis-agent-mcp"

WARN_COUNT=0
warn() { printf '\033[33m[warn]\033[0m %s\n' "$*" >&2; WARN_COUNT=$((WARN_COUNT + 1)); }
info() { printf '\033[36m[deploy]\033[0m %s\n' "$*"; }

# Map a ${VAR} placeholder to its Keychain account name.
keychain_account_for() {
  case "$1" in
    WEBHOOK_SECRET|HERMES_WEBHOOK_SECRET) echo "ops-hermes-webhook-secret" ;;
    TELEGRAM_BOT_TOKEN)                   echo "ops-telegram-bot-token" ;;
    DIGEST_GITHUB_TOKEN)                  echo "ops-hermes-digest-github-token" ;;
    DIGEST_GLITCHTIP_TOKEN)               echo "ops-hermes-digest-glitchtip-token" ;;
    GLITCHTIP_SECRET_KEY|SECRET_KEY)      echo "ops-glitchtip-secret-key" ;;
    POSTGRES_PASSWORD)                    echo "ops-glitchtip-postgres-password" ;;
    *)                                    echo "" ;;
  esac
}

# Read a secret from Keychain ("" if absent).
keychain_get() {
  local account="$1"
  [ -z "$account" ] && return 0
  security find-generic-password -s "$KEYCHAIN_SVC" -a "$account" -w 2>/dev/null || true
}

# Render a .template file → target, substituting every ${VAR} from Keychain.
# Missing secrets are left untouched + warned (no abort).
render_template() {
  local src="$1" dst="$2"
  [ -f "$src" ] || { warn "template missing: $src"; return 0; }

  # Collect distinct ${VAR} placeholders.
  local vars
  vars=$(grep -oE '\$\{[A-Z_][A-Z0-9_]*\}' "$src" | sed 's/[${}]//g' | sort -u || true)

  local rendered; rendered="$(cat "$src")"
  if [ -n "$vars" ]; then
    while IFS= read -r var; do
      [ -z "$var" ] && continue
      local account value
      account="$(keychain_account_for "$var")"
      if [ -z "$account" ]; then
        warn "no Keychain mapping for \${$var} in $(basename "$src") — left as placeholder"
        continue
      fi
      value="$(keychain_get "$account")"
      if [ -z "$value" ]; then
        warn "Keychain secret '$account' (\${$var}) not found — left as placeholder"
        continue
      fi
      # Substitute with python to keep special chars literal.
      rendered="$(VAR="$var" VAL="$value" python3 - "$rendered" <<'PY'
import os, sys
text = sys.argv[1]
print(text.replace("${" + os.environ["VAR"] + "}", os.environ["VAL"]), end="")
PY
)"
    done <<< "$vars"
  fi
  printf '%s' "$rendered" > "$dst"
  info "rendered $(basename "$dst")"
}

# ───────────────────── 1. create runtime dirs ─────────────────────
info "creating runtime directories"
mkdir -p "$HERMES_HOME/scripts" \
         "$HERMES_HOME/skills/ratis" \
         "$HERMES_COMPOSE/glitchtip-proxy" \
         "$HERMES_COMPOSE/scripts" \
         "$GLITCHTIP_HOME/bin"

# ───────────────────── 2. copy versioned sources ─────────────────────
info "copying SOUL.md + cron scripts → ~/.hermes"
cp "$HERE/hermes-home/SOUL.md" "$HERMES_HOME/SOUL.md"
cp "$HERE"/hermes-home/scripts/*.sh "$HERMES_HOME/scripts/" 2>/dev/null || true
cp "$HERE"/hermes-home/scripts/*.py "$HERMES_HOME/scripts/" 2>/dev/null || true

info "copying postmortem skill → ~/.hermes/skills/ratis"
rsync -a --delete \
  --exclude='__pycache__' --exclude='.pytest_cache' --exclude='*.pyc' \
  "$HERE/hermes-home/skills/ratis/claude-code-postmortem/" \
  "$HERMES_HOME/skills/ratis/claude-code-postmortem/"

info "copying hermes compose + proxy → ~/hermes"
cp "$HERE/hermes-compose/docker-compose.yml" "$HERMES_COMPOSE/docker-compose.yml"
cp "$HERE/hermes-compose/glitchtip-proxy/proxy.py" "$HERMES_COMPOSE/glitchtip-proxy/proxy.py"
[ -f "$HERE/hermes-compose/setup-hermes-runtime.sh" ] && \
  cp "$HERE/hermes-compose/setup-hermes-runtime.sh" "$HERMES_COMPOSE/scripts/setup-hermes-runtime.sh"

info "copying glitchtip compose + glt → ~/glitchtip"
cp "$HERE/glitchtip/docker-compose.yml" "$GLITCHTIP_HOME/docker-compose.yml"
cp "$HERE/glitchtip/bin/glt" "$GLITCHTIP_HOME/bin/glt"

# ───────────────────── 3. make scripts executable ─────────────────────
info "marking scripts executable"
chmod +x "$HERMES_HOME"/scripts/*.sh 2>/dev/null || true
chmod +x "$HERMES_HOME"/skills/ratis/claude-code-postmortem/scripts/*.py 2>/dev/null || true
chmod +x "$HERMES_COMPOSE"/scripts/*.sh 2>/dev/null || true
chmod +x "$GLITCHTIP_HOME/bin/glt"

# ───────────────────── 4. render templated config ─────────────────────
info "rendering config.yaml + webhook_subscriptions.json from Keychain"
render_template "$HERE/hermes-home/config.template.yaml" "$HERMES_HOME/config.yaml"
render_template "$HERE/hermes-home/webhook_subscriptions.template.json" "$HERMES_HOME/webhook_subscriptions.json"

# ───────────────────── 5. install faster-whisper in venv ─────────────────────
info "installing faster-whisper in the Hermes container venv (best-effort)"
if docker ps --format '{{.Names}}' 2>/dev/null | grep -q '^ratis-hermes$'; then
  docker exec ratis-hermes sh -c \
    "VIRTUAL_ENV=/opt/hermes/.venv uv pip install faster-whisper" \
    && info "faster-whisper installed" \
    || warn "faster-whisper install failed (run again once the container is healthy)"
else
  warn "container 'ratis-hermes' not running — skipping faster-whisper install (run 'docker compose up -d' in ~/hermes first, then re-run deploy.sh)"
fi

# ───────────────────── 6. recap + manual steps ─────────────────────
echo
info "──────────────────────────────────────────────────────────"
info "deploy.sh done. Warnings: ${WARN_COUNT}"
info "Files restored under: ~/.hermes  ~/hermes  ~/glitchtip"
info "──────────────────────────────────────────────────────────"
cat <<'EOF'

Manual steps remaining (cannot be automated):

  (a) Re-auth the OpenAI Codex provider (OAuth, interactive):
        docker exec -it ratis-hermes hermes auth add openai-codex

  (b) Re-pair Telegram:
        - send /start to the bot from your Telegram account
        - then approve it:  docker exec -it ratis-hermes hermes pairing approve <id>

  (c) Paste the routine prompts into Claude.ai scheduled tasks (NOT auto-restorable):
        infra/hermes/routines/postmortem-deep.md
        infra/hermes/routines/skill-reviewer.md

  (d) Bring the stacks up:
        cd ~/glitchtip && docker compose up -d     # GlitchTip (creates networks)
        cd ~/hermes    && docker compose up -d      # Hermes + glitchtip-proxy
      Then re-run this script once so faster-whisper installs into the live venv.

EOF
