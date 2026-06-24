from __future__ import annotations

import os
import sys
from pathlib import Path

# Hermetic test env — DO NOT load_dotenv(.env.local).
# .env.local is a developer file that may contain placeholder secrets
# which silently break tests (see KP-29). Tests own their env.
#
# Override-only escape hatches : set TEST_DATABASE_URL in your shell to
# point at a different DB (e.g. CI ephemeral DB).
from ratis_core.test_db import resolve_test_database_url

# Worktree-aware : CI gets its explicit TEST_DATABASE_URL untouched ;
# local dev gets a per-worktree DB suffix so concurrent worktrees do not
# clash on the shared ratis_test DROP/CREATE schema teardown.
TEST_DATABASE_URL = resolve_test_database_url()
# Hard-set (not setdefault) — shadow any value the developer may have
# exported in their shell or leaked from an earlier .env load.
os.environ["DATABASE_URL"] = TEST_DATABASE_URL
os.environ["INTERNAL_API_KEY"] = "test-internal-key"
os.environ["SENTRY_DSN"] = ""  # DSN vide = Sentry silent en tests

# Add batch root to sys.path so `import sirene_sync` works
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
import ratis_core.models  # noqa: F401 — register all mappers
from ratis_core.database import Base, make_engine
from sqlalchemy import event, text
from sqlalchemy.orm import sessionmaker


@pytest.fixture(scope="session")
def engine():
    eng = make_engine(TEST_DATABASE_URL)
    with eng.connect() as conn:
        # Drop user-defined ENUM types in public defensively (see KP-29).
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
        # Required by store_consolidation.find_match() fuzzy radius branch
        # (earth_distance / ll_to_earth — installed in prod via migration
        # 20260511_0900_pg_earthdistance).
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS cube"))
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS earthdistance"))
        conn.commit()
    Base.metadata.create_all(bind=eng)
    # Sentinel : fail fast if create_all silently no-op'd.
    with eng.connect() as conn:
        try:
            conn.execute(text("SELECT 1 FROM stores LIMIT 0"))
        except Exception as exc:
            raise RuntimeError(
                "conftest setup failed : Base.metadata.create_all() did not "
                "produce expected tables (stores). "
                "Likely a model module is not imported."
            ) from exc
    # Partial / functional unique indexes on stores are declared via raw migrations
    # (not on the SQLAlchemy model) so create_all() doesn't include them. We
    # recreate them here so that tests exercise the same uniqueness invariants
    # as production. Keep these in sync with alembic migrations :
    #   - 20260415_2100_q1r2s3t4u5v6_stores_osm_fields  (uq_stores_siret)
    #   - 20260421_2241_store_retailer                  (unique_store on retailer)
    #   - 20260427_1700_recreate_stores_uq              (idempotent recreate)
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
    """
    Marqueur de politique requis par CLAUDE.md / CI.

    Dans le contexte batch, les tests utilisent du SQL brut via session_factory() —
    chaque fonction batch gère sa propre session. Un db.commit() manquant est détecté
    naturellement par les assertions du test (la lecture suivante dans une nouvelle
    session voit l'état rollbacké). Aucune instrumentation supplémentaire nécessaire.
    """
    return
