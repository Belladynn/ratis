#!/usr/bin/env bash
#
# Ratis client — dev start script
#
# Enchaîne dans le bon ordre :
#   1. (si --hard) clean node_modules + caches Metro/Babel/Expo
#   2. npm install si node_modules manquant
#   3. npx expo install --fix      → aligne les versions natives sur le SDK
#   4. npx expo start --clear      → démarre Metro avec cache vidé
#
# Usage :
#   ./start.sh           # démarrage normal
#   ./start.sh --hard    # reset complet avant start (quand ça déconne)
#   ./start.sh -h        # aide
#
# Fonctionne sur Git Bash (Windows), WSL, macOS, Linux.

set -euo pipefail

cd "$(dirname "$0")"

HARD=0
for arg in "$@"; do
  case "$arg" in
    --hard|--clean) HARD=1 ;;
    -h|--help)
      echo "Usage: ./start.sh [--hard]"
      echo
      echo "  (no arg) : start normal (install si besoin, fix versions, Expo --clear)"
      echo "  --hard   : supprime node_modules + caches + reinstall complet"
      echo "  -h       : cette aide"
      exit 0
      ;;
    *)
      echo "✗ Argument inconnu : $arg"
      echo "  Utilise ./start.sh --help"
      exit 1
      ;;
  esac
done

# ─── Sanity check ────────────────────────────────────────────────────────
if [ ! -f package.json ]; then
  echo "✗ package.json introuvable (pas dans ratis_client ?)"
  exit 1
fi

if ! command -v node >/dev/null 2>&1; then
  echo "✗ Node n'est pas installé / pas dans le PATH"
  exit 1
fi

echo "▶ Ratis client — dev start"
echo "  node    : $(node --version)"
echo "  npm     : $(npm --version)"
[ "$HARD" = "1" ] && echo "  mode    : HARD"
echo

# ─── 1. (optionnel) Reset complet ────────────────────────────────────────
if [ "$HARD" = "1" ]; then
  echo "▶ Suppression node_modules + caches..."
  rm -rf node_modules
  rm -rf .expo .metro-cache
  # Metro crée son cache dans $TMPDIR ou /tmp
  rm -rf "${TMPDIR:-/tmp}/metro-cache" 2>/dev/null || true
  rm -rf "${TMPDIR:-/tmp}/haste-map-"* 2>/dev/null || true
  rm -rf "${TMPDIR:-/tmp}/react-"* 2>/dev/null || true
  echo "  ✓ clean done"
  echo
fi

# ─── 2. Installation deps si manquantes ──────────────────────────────────
if [ ! -d node_modules ]; then
  echo "▶ node_modules absent → npm install..."
  npm install --no-audit --no-fund
  echo
fi

# ─── 3. Alignement versions natives Expo ─────────────────────────────────
# Critique : `npm install <pkg>` prend la dernière version qui peut
# être incompatible avec le SDK installé (ex : expo-crypto@55 sur SDK 54
# → erreur ExpoCryptoAES native module not found).
# `expo install --fix` remet toutes les versions compatibles.
echo "▶ Sync versions Expo (expo install --fix)..."
npx expo install --fix --no-audit 2>&1 | grep -v "^npm warn" || true
echo

# ─── 3.5. Doctor check : scan mismatches + duplicates ────────────────────
# expo-doctor détecte les packages en version incompatible (ex : package
# 55.x sur SDK 54) et les doublons dans node_modules qui peuvent causer
# des erreurs "native module not found" mystérieuses.
echo "▶ Doctor (expo-doctor)..."
DOCTOR_OUTPUT=$(npx expo-doctor 2>&1 || true)
if echo "$DOCTOR_OUTPUT" | grep -q "No issues detected"; then
  echo "  ✓ 17/17 checks passed"
else
  echo "$DOCTOR_OUTPUT" | tail -20
  echo
  echo "  ⚠ expo-doctor a trouvé des problèmes. Continue quand même ? [y/N]"
  read -r answer
  if [ "$answer" != "y" ] && [ "$answer" != "Y" ]; then
    echo "  ✗ Abandon. Corrige les packages avant de relancer."
    exit 1
  fi
fi
echo

# ─── 4. Démarrage Metro avec cache vidé ──────────────────────────────────
echo "▶ Démarrage Expo (cache Metro clearé)..."
echo "  Raccourcis utiles :"
echo "    a → ouvrir Android emulator / dev device"
echo "    i → ouvrir iOS simulator / dev device"
echo "    r → reload"
echo "    m → toggle dev menu"
echo
exec npx expo start --clear
