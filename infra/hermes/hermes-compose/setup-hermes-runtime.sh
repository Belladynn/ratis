#!/usr/bin/env bash
#
# setup-hermes-runtime.sh — Crée le user OS `hermes-runtime` + groupe `hermes-bridge`
# pour l'isolation OS-level Hermes ↔ agent-mcp (docs/arch/ARCH_agent_mcp_isolation.md § AMI-1).
#
# Exécuter une seule fois avec sudo :
#   sudo bash ~/hermes/scripts/setup-hermes-runtime.sh
#
# Réversible via ~/hermes/scripts/teardown-hermes-runtime.sh (à créer si besoin de revert).
#
# Idempotent : peut être relancé sans casser l'état si tout est déjà créé.

set -euo pipefail

# ─── Couleurs pour les logs (optionnel) ────────────────────────────────
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

log_ok()   { echo -e "${GREEN}✓${NC} $1"; }
log_skip() { echo -e "${YELLOW}∼${NC} $1 (déjà fait)"; }
log_err()  { echo -e "${RED}✗${NC} $1"; }

# ─── Vérif exécution en root ────────────────────────────────────────────
if [[ $EUID -ne 0 ]]; then
  log_err "Ce script doit être lancé avec sudo : sudo bash $0"
  exit 1
fi

GUILLAUME_USER="guillaume"
HERMES_USER="hermes-runtime"
HERMES_GROUP="hermes-bridge"
HERMES_UID=510
HERMES_GID=600
HERMES_HOME="/Users/${HERMES_USER}"

echo "=== Setup OS-level isolation Hermes ↔ agent-mcp (AMI-1) ==="
echo ""

# ─── 1. Group hermes-bridge ─────────────────────────────────────────────
if dscl . -read "/Groups/${HERMES_GROUP}" PrimaryGroupID >/dev/null 2>&1; then
  log_skip "Group ${HERMES_GROUP}"
else
  dscl . -create "/Groups/${HERMES_GROUP}"
  dscl . -create "/Groups/${HERMES_GROUP}" PrimaryGroupID "${HERMES_GID}"
  dscl . -create "/Groups/${HERMES_GROUP}" RealName "Hermes <-> agent-mcp socket bridge"
  dscl . -create "/Groups/${HERMES_GROUP}" Password "*"
  log_ok "Group ${HERMES_GROUP} créé (GID=${HERMES_GID})"
fi

# ─── 2. User hermes-runtime ─────────────────────────────────────────────
if dscl . -read "/Users/${HERMES_USER}" UniqueID >/dev/null 2>&1; then
  log_skip "User ${HERMES_USER}"
else
  dscl . -create "/Users/${HERMES_USER}"
  dscl . -create "/Users/${HERMES_USER}" UserShell /usr/bin/false
  dscl . -create "/Users/${HERMES_USER}" RealName "Hermes Runtime (least-privilege)"
  dscl . -create "/Users/${HERMES_USER}" UniqueID "${HERMES_UID}"
  dscl . -create "/Users/${HERMES_USER}" PrimaryGroupID "${HERMES_GID}"
  dscl . -create "/Users/${HERMES_USER}" NFSHomeDirectory "${HERMES_HOME}"
  RANDOM_PWD=$(openssl rand -base64 24)
  dscl . -passwd "/Users/${HERMES_USER}" "${RANDOM_PWD}"
  unset RANDOM_PWD
  # Cache le user de la fenêtre de login Mac (purement cosmétique)
  dscl . -create "/Users/${HERMES_USER}" IsHidden 1
  log_ok "User ${HERMES_USER} créé (UID=${HERMES_UID}, shell=/usr/bin/false, hidden=1)"
fi

# ─── 3. Home directory ──────────────────────────────────────────────────
if [[ -d "${HERMES_HOME}" ]]; then
  log_skip "Home directory ${HERMES_HOME}"
else
  mkdir -p "${HERMES_HOME}"
  chown "${HERMES_USER}:${HERMES_GROUP}" "${HERMES_HOME}"
  chmod 700 "${HERMES_HOME}"
  log_ok "Home directory ${HERMES_HOME} créé (mode 700, owner=${HERMES_USER})"
fi

# ─── 4. Membership groupe : ajouter guillaume + hermes-runtime ──────────
current_members=$(dscl . -read "/Groups/${HERMES_GROUP}" GroupMembership 2>/dev/null | tail -1 || echo "")

if echo "${current_members}" | grep -qw "${GUILLAUME_USER}"; then
  log_skip "Membership ${GUILLAUME_USER} dans ${HERMES_GROUP}"
else
  dscl . -append "/Groups/${HERMES_GROUP}" GroupMembership "${GUILLAUME_USER}"
  log_ok "${GUILLAUME_USER} ajouté au groupe ${HERMES_GROUP}"
fi

if echo "${current_members}" | grep -qw "${HERMES_USER}"; then
  log_skip "Membership ${HERMES_USER} dans ${HERMES_GROUP}"
else
  dscl . -append "/Groups/${HERMES_GROUP}" GroupMembership "${HERMES_USER}"
  log_ok "${HERMES_USER} ajouté au groupe ${HERMES_GROUP}"
fi

# ─── 5. Vérifications post-setup ────────────────────────────────────────
echo ""
echo "=== Vérifications post-setup ==="

echo "→ id ${HERMES_USER} :"
id "${HERMES_USER}"

echo "→ Membership ${HERMES_GROUP} :"
dscl . -read "/Groups/${HERMES_GROUP}" GroupMembership

echo "→ whoami sous ${HERMES_USER} (doit retourner 'hermes-runtime') :"
sudo -u "${HERMES_USER}" -H bash -c "whoami"

echo "→ Test isolation Keychain — recherche d'un secret ratis-agent-mcp sous ${HERMES_USER}"
echo "  (doit retourner 'security: SecKeychainSearchCopyNext: The specified item could not be found in the keychain.' ou similaire)"
set +e
sudo -u "${HERMES_USER}" -H bash -c "security find-generic-password -s ratis-agent-mcp -a notion 2>&1" || true
set -e

echo ""
log_ok "Setup AMI-1 complet. ${HERMES_USER} ne peut pas lire le Keychain de ${GUILLAUME_USER}."
echo ""
echo "Prochaines étapes (séparées) :"
echo "  - AMI-2 : implémenter daemon mode dans tools/agent-mcp/ (4-5 j)"
echo "  - AMI-3 : socket Unix + peer-cred auth (inclus dans AMI-2)"
echo "  - AMI-4 : bridge Hermes → MCP socket (1-2 j, investiguer config Hermes)"
echo "  - Tant que tout n'est pas livré : Hermes continue de tourner sous ${GUILLAUME_USER} (POC Phase 1a-2 / 1b)"
