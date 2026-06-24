#!/usr/bin/env bash
# ============================================================
# init_test_db.sh — Create empty ratis_test DB + pg_trgm extension
# ============================================================
# Cold-start helper for a fresh dev machine. Pytest's session
# fixture (DROP SCHEMA + create_all) handles per-run isolation,
# so this script only needs to exist *once*, to bootstrap an
# empty database and the pg_trgm extension.
#
# DO NOT run `alembic upgrade head` here : the schema produced
# by Alembic is immediately torn down by the conftest, and any
# leftover ENUM types / sequences would silently shadow what
# SQLAlchemy expects.
#
# Usage :
#   ./scripts/init_test_db.sh
#
# Prereq : `docker compose up -d` (postgres healthy).
# Escape hatch : if the DB is corrupted, `DROP DATABASE ratis_test`
# manually and rerun this script.
# ============================================================

set -euo pipefail

POSTGRES_ADMIN_URL="postgresql://ratis:ratis@localhost:5432/postgres"  # pragma: allowlist secret
TEST_DB="ratis_test"

echo "→ Creating database $TEST_DB (if absent)..."
psql "$POSTGRES_ADMIN_URL" -c "CREATE DATABASE $TEST_DB;" 2>/dev/null \
  && echo "  ✓ $TEST_DB created" \
  || echo "  ℹ $TEST_DB already exists, continuing"

echo "→ Ensuring pg_trgm extension..."
TEST_DB_URL="postgresql://ratis:ratis@localhost:5432/$TEST_DB"  # pragma: allowlist secret
psql "$TEST_DB_URL" -c "CREATE EXTENSION IF NOT EXISTS pg_trgm;" >/dev/null

echo "✓ $TEST_DB ready. Pytest will manage the schema per session."
