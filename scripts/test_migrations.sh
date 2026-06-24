#!/usr/bin/env bash
# ============================================================
# test_migrations.sh — Valide la chaîne Alembic sur base fraîche
# ============================================================
# Simule exactement ce que fait la CI : DROP SCHEMA + upgrade head.
# À lancer avant tout push contenant une migration.
#
# Usage :
#   ./scripts/test_migrations.sh
#
# Prérequis : docker compose up -d (postgres en cours)
#
# Ce script détecte les problèmes de nommage de contraintes,
# colonnes ou index qui divergent entre le dev DB organique
# et une base reconstruite depuis zéro via les migrations.
# ============================================================

set -euo pipefail

# --- Pre-flight : single alembic head -----------------------------------
# A second head makes `alembic upgrade head` fail with "Multiple head
# revisions are present". Catch it here with a fast check before spinning
# up a DB, so the failure mode is obvious rather than buried in DB output.
echo "→ Vérification : une seule tête alembic..."
head_count=$(uv run alembic heads 2>/dev/null | grep -cE '\(head\)' || true)
if [ "$head_count" -ne 1 ]; then
    echo ""
    echo "✗ ÉCHEC — $head_count têtes alembic (attendu : 1)."
    echo "  'alembic upgrade head' échouera (Multiple head revisions)."
    echo "  Fix : re-parenter le down_revision de la nouvelle migration sur"
    echo "  la tête actuelle, ou lancer 'uv run alembic merge heads'."
    uv run alembic heads 2>/dev/null || true
    exit 1
fi

POSTGRES_URL="postgresql://ratis:ratis@localhost:5432/postgres"  # pragma: allowlist secret
FRESH_DB="ratis_migration_test"
FRESH_URL="postgresql+psycopg://ratis:ratis@localhost:5432/$FRESH_DB"  # pragma: allowlist secret

cleanup() {
    echo "→ Nettoyage de $FRESH_DB..."
    psql "$POSTGRES_URL" -c "DROP DATABASE IF EXISTS $FRESH_DB;" 2>/dev/null || true
}

# Toujours nettoyer en sortie (succès ou erreur)
trap cleanup EXIT

echo "============================================"
echo " test_migrations.sh — chaîne Alembic fraîche"
echo "============================================"

# Supprimer si elle existe déjà (run précédent interrompu)
psql "$POSTGRES_URL" -c "DROP DATABASE IF EXISTS $FRESH_DB;" 2>/dev/null

echo "→ Création de $FRESH_DB..."
psql "$POSTGRES_URL" -c "CREATE DATABASE $FRESH_DB;"

echo "→ alembic upgrade head..."
export DATABASE_URL="$FRESH_URL"
if uv run alembic upgrade head; then
    echo ""
    echo "✓ Toutes les migrations passent sur base fraîche."
else
    echo ""
    echo "✗ ÉCHEC — une migration a planté sur base fraîche."
    echo "  Vérifier les noms de contraintes/colonnes/index."
    echo "  Règle : toujours IF EXISTS sur les DROP."
    exit 1
fi
