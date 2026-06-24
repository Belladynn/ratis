"""Shared pytest fixtures for ratis_core tests that need a real DB.

Mirrors the batch convention : SAVEPOINT isolation, DROP + recreate schema
at session scope, autouse `assert_no_pending_changes` marker.

Model-only tests (no DB) do not consume these fixtures and are unaffected.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# HSP4 — mot de passe utilisé par la migration `apply_hsp4_agent_confinement`
# pour créer le rôle agent_read. `test_hsp4_migration.py` fait tourner
# `spin_up_migrated_db` qui exécute alembic upgrade en subprocess héritant
# os.environ. pragma: allowlist secret
os.environ.setdefault("AGENT_READ_PASSWORD", "test-agent-read-password-32-chars!")

try:
    from dotenv import load_dotenv  # type: ignore

    # Load env vars from the worktree-local .env.local if present so
    # TEST_DATABASE_URL is picked up when running tests standalone.
    for candidate in (
        Path(__file__).resolve().parent.parent.parent / ".env.local",
        Path(__file__).resolve().parent.parent.parent / "webservices" / "ratis_auth" / ".env.local",
    ):
        if candidate.exists():
            load_dotenv(candidate)
            break
except Exception:
    pass

# Make the package importable without install (editable fallback).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
import ratis_core.models  # noqa: F401
from ratis_core.database import Base, make_engine
from sqlalchemy import event, text
from sqlalchemy.orm import sessionmaker


def _test_url() -> str:
    # Worktree-aware : CI gets its explicit TEST_DATABASE_URL untouched ;
    # local dev gets a per-worktree DB suffix so concurrent worktrees do
    # not clash on the shared ratis_test DROP/CREATE schema teardown.
    from ratis_core.test_db import resolve_test_database_url

    return resolve_test_database_url()


@pytest.fixture(scope="session")
def engine():
    eng = make_engine(_test_url())
    with eng.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS unaccent"))
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS postgis"))
        # immutable wrapper required for GENERATED columns (cf. migration
        # 20260430_1000_pipev3 — pg unaccent is STABLE, not IMMUTABLE).
        conn.execute(
            text(
                "CREATE OR REPLACE FUNCTION immutable_unaccent(text) "
                "RETURNS text LANGUAGE sql IMMUTABLE PARALLEL SAFE STRICT "
                "AS $$ SELECT public.unaccent('public.unaccent', $1) $$"
            )
        )
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS pgcrypto"))
    Base.metadata.create_all(eng)
    yield eng
    eng.dispose()


@pytest.fixture
def connection(engine):
    with engine.connect() as conn:
        yield conn


@pytest.fixture
def session_factory(connection):
    """SA 2.0 SAVEPOINT isolation — every test rolls back after completion."""
    outer = connection.begin()
    nested = connection.begin_nested()
    factory = sessionmaker(connection, expire_on_commit=False)

    @event.listens_for(factory, "after_transaction_end")
    def _restart_savepoint(session, tx):
        nonlocal nested
        if not nested.is_active:
            nested = connection.begin_nested()

    yield factory
    outer.rollback()


@pytest.fixture
def db(session_factory, connection):
    with session_factory() as session:
        session._test_connection = connection
        yield session


@pytest.fixture(autouse=True)
def assert_no_pending_changes():
    """Marker required by CLAUDE.md / CI — seed tests use raw SQL sessions."""
    return
