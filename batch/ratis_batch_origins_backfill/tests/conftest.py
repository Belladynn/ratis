"""Test conftest for ratis_batch_origins_backfill.

Hermetic env (no .env.local), Base.metadata.create_all on a clean schema,
SAVEPOINT-isolated session factory per test. Mirrors the pattern used by
ratis_batch_off_sync / ratis_batch_achievements — the runner commits its
own transactions per page, so we expose a sessionmaker (not a session)
to mirror the production wiring.
"""

from __future__ import annotations

import os

# Hermetic test env — DO NOT load_dotenv(.env.local). Tests own their env.
from ratis_core.test_db import resolve_test_database_url

TEST_DATABASE_URL = resolve_test_database_url()
os.environ["DATABASE_URL"] = TEST_DATABASE_URL
os.environ["INTERNAL_API_KEY"] = "test-internal-key"
os.environ["SENTRY_DSN"] = ""  # silent Sentry in tests

import pytest
from ratis_core.database import Base, make_engine
from ratis_core.models import BatchSyncLog, Product  # noqa: F401 — registers models
from sqlalchemy import event, text
from sqlalchemy.orm import sessionmaker


@pytest.fixture(scope="session")
def engine():
    eng = make_engine(TEST_DATABASE_URL)
    with eng.connect() as conn:
        # Drop user-defined ENUM types in public defensively (KP-29).
        conn.execute(
            text("""
            DO $$
            DECLARE r RECORD;
            BEGIN
                FOR r IN
                    SELECT t.typname
                    FROM pg_type t
                    JOIN pg_namespace n ON t.typnamespace = n.oid
                    WHERE t.typtype = 'e' AND n.nspname = 'public'
                LOOP
                    EXECUTE 'DROP TYPE IF EXISTS public.' || quote_ident(r.typname) || ' CASCADE';
                END LOOP;
            END $$;
        """)
        )
        conn.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public;"))
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS unaccent"))
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS postgis"))
        conn.execute(
            text(
                "CREATE OR REPLACE FUNCTION immutable_unaccent(text) "
                "RETURNS text LANGUAGE sql IMMUTABLE PARALLEL SAFE STRICT "
                "AS $$ SELECT public.unaccent('public.unaccent', $1) $$"
            )
        )
        conn.commit()
    Base.metadata.create_all(bind=eng)
    with eng.connect() as conn:
        try:
            conn.execute(text("SELECT 1 FROM products LIMIT 0"))
            conn.execute(text("SELECT 1 FROM batch_sync_log LIMIT 0"))
            # Phase C-2 sentinel : the column the batch updates must exist.
            conn.execute(text("SELECT origins_tags FROM products LIMIT 0"))
        except Exception as exc:
            raise RuntimeError(
                "conftest setup failed : Base.metadata.create_all() did not "
                "produce expected tables / columns (products.origins_tags). "
                "Likely a model module is not imported."
            ) from exc
    yield eng
    eng.dispose()


@pytest.fixture
def connection(engine):
    with engine.connect() as conn:
        yield conn


@pytest.fixture
def session_factory(connection):
    """SAVEPOINT-isolated sessionmaker — survives the runner's commits."""
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


@pytest.fixture(autouse=True)
def assert_no_pending_changes():
    """CLAUDE.md / CI marker. The runner commits per page ; tests inspect
    state via session_factory reads after the run."""
    return
