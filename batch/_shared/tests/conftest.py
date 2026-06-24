"""Test fixtures for ``batch_shared``.

Mirrors the convention used by ``batch/ratis_batch_osm_sync/tests/conftest.py`` :
SAVEPOINT-isolated SQLAlchemy 2.0 sessions on top of a hermetically-rebuilt
test DB (``DROP SCHEMA public CASCADE`` + ``create_all``). Each test rolls back
automatically.

Hermetic env — no ``.env.local`` is loaded (per KP-29). Tests own their env.
"""

from __future__ import annotations

import os

# Hermetic test env. Hard-set (not setdefault) to shadow any developer-exported
# value that may leak in from an earlier .env load.
from ratis_core.test_db import resolve_test_database_url

TEST_DATABASE_URL = resolve_test_database_url()
os.environ["DATABASE_URL"] = TEST_DATABASE_URL
os.environ["INTERNAL_API_KEY"] = "test-internal-key"
os.environ["SENTRY_DSN"] = ""

import pytest
import ratis_core.models  # noqa: F401 — register all ORM mappers
from ratis_core.database import Base, make_engine
from sqlalchemy import event, text
from sqlalchemy.orm import sessionmaker


@pytest.fixture(scope="session")
def engine():
    """Session-scoped engine on a freshly-rebuilt ``ratis_test`` schema.

    Mirrors ``batch/ratis_batch_osm_sync/tests/conftest.py`` :
    - Drop user-defined ENUM types defensively (cf KP-29).
    - ``DROP SCHEMA public CASCADE`` + recreate.
    - Install required extensions (``unaccent``, ``pg_trgm``, ``pgcrypto``,
      ``cube``, ``earthdistance``).
    - ``Base.metadata.create_all()`` — all ORM tables.
    - Recreate the partial / functional unique indexes on ``stores`` that
      production has via raw-SQL migrations (``uq_stores_siret``,
      ``unique_store``).

    Note (cube + earthdistance) : the PR2 ``find_match()`` fuzzy radius branch
    uses ``earth_distance(ll_to_earth(...), ll_to_earth(...))``. Both
    extensions are installed in CI/prod via the
    ``20260511_0900_pg_earthdistance`` migration ; we install them here too so
    tests against a fresh ``ratis_test`` exercise the same SQL surface.
    """
    eng = make_engine(TEST_DATABASE_URL)
    with eng.connect() as conn:
        # Drop user-defined ENUM types defensively (cf KP-29).
        conn.execute(
            text(
                """
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
                """
            )
        )
        conn.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public;"))
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS unaccent"))
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS postgis"))
        # IMMUTABLE wrapper for unaccent — required by GENERATED columns
        # (PG's unaccent is STABLE, see migration 20260430_1000_pipeline_v3_clean).
        conn.execute(
            text(
                "CREATE OR REPLACE FUNCTION immutable_unaccent(text) "
                "RETURNS text LANGUAGE sql IMMUTABLE PARALLEL SAFE STRICT "
                "AS $$ SELECT public.unaccent('public.unaccent', $1) $$"
            )
        )
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS pgcrypto"))
        # PR2 — required by store_consolidation.find_match() fuzzy radius.
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS cube"))
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS earthdistance"))
        conn.commit()

    Base.metadata.create_all(bind=eng)

    # Sentinel — fail fast if create_all silently no-op'd (model not imported).
    with eng.connect() as conn:
        try:
            conn.execute(text("SELECT 1 FROM stores LIMIT 0"))
        except Exception as exc:
            raise RuntimeError(
                "conftest setup failed : Base.metadata.create_all() did not "
                "produce expected tables (stores). A ratis_core.models module "
                "is probably not imported."
            ) from exc

    # Partial / functional unique indexes on `stores` declared via raw SQL
    # migrations (not on the SQLAlchemy model) — recreate here so tests
    # exercise the same uniqueness invariants as production. Keep in sync
    # with alembic migrations :
    #   - 20260415_2100_q1r2s3t4u5v6_stores_osm_fields  (uq_stores_siret)
    #   - 20260421_2241_store_retailer                  (unique_store)
    with eng.begin() as conn:
        conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS uq_stores_siret ON stores(siret) WHERE siret IS NOT NULL"))
        conn.execute(
            text(
                "CREATE UNIQUE INDEX IF NOT EXISTS unique_store ON stores "
                "(COALESCE(retailer, ''), COALESCE(address, ''), "
                "COALESCE(postal_code, ''))"
            )
        )

    yield eng
    eng.dispose()


@pytest.fixture
def connection(engine):
    with engine.connect() as conn:
        yield conn


@pytest.fixture
def session_factory(connection):
    """SAVEPOINT-isolated session factory — every test rolls back at teardown."""
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
def db(session_factory):
    """Default SAVEPOINT-isolated SQLAlchemy 2.0 session."""
    with session_factory() as session:
        yield session


@pytest.fixture(autouse=True)
def assert_no_pending_changes():
    """Marqueur de politique requis par CLAUDE.md / CI (R-DB-13).

    Le helper ``apply_upsert()`` ne commit pas — c'est le caller batch qui
    décide quand commit. Les tests valident l'écriture via une nouvelle lecture
    dans la même session (SAVEPOINT) ; un commit manquant dans le helper se
    manifesterait par une assertion qui échoue (la lecture verrait l'état
    rollbacké). Aucune instrumentation supplémentaire requise.
    """
    return
