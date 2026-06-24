#!/usr/bin/env bash
# ============================================================
# Ratis — RSA-2048 key pair generator for RS256 JWT signing
# ============================================================
# Usage:
#   ./scripts/gen-jwt-keys.sh            generate into ./secrets/
#   ./scripts/gen-jwt-keys.sh <dir>      generate into <dir>/
#
# Produces <dir>/jwt_private.pem (PKCS8) + <dir>/jwt_public.pem.
# <dir> defaults to ./secrets which is gitignored — keys are NEVER
# committed. Generate one pair per environment (dev / test / prod);
# never share a pair across environments.
# ============================================================
set -euo pipefail

OUT_DIR="${1:-secrets}"
PRIVATE="$OUT_DIR/jwt_private.pem"
PUBLIC="$OUT_DIR/jwt_public.pem"

mkdir -p "$OUT_DIR"

if [ -f "$PRIVATE" ] || [ -f "$PUBLIC" ]; then
  echo "✗ A key pair already exists in $OUT_DIR/ — refusing to overwrite."
  echo "  Delete jwt_private.pem + jwt_public.pem manually to regenerate."
  exit 1
fi

openssl genpkey -algorithm RSA -pkeyopt rsa_keygen_bits:2048 -out "$PRIVATE"
openssl rsa -in "$PRIVATE" -pubout -out "$PUBLIC"
chmod 600 "$PRIVATE"
chmod 644 "$PUBLIC"

echo "✓ Generated $PRIVATE (private — keep on ratis_auth host only)"
echo "✓ Generated $PUBLIC (public — distribute to verifying services)"
