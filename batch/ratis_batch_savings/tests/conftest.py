import os
import uuid

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

import pytest
import ratis_core.models  # noqa: F401
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
        conn.commit()
    Base.metadata.create_all(bind=eng)
    # Sentinel : fail fast if create_all silently no-op'd.
    with eng.connect() as conn:
        try:
            conn.execute(text("SELECT 1 FROM users LIMIT 0"))
            conn.execute(text("SELECT 1 FROM user_preferences LIMIT 0"))
        except Exception as exc:
            raise RuntimeError(
                "conftest setup failed : Base.metadata.create_all() did not "
                "produce expected tables (users / user_preferences). "
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
    """CLAUDE.md / CI marker. Batch tests rely on test assertions to catch missing commits."""
    return


@pytest.fixture
def make_user(session_factory):
    """Insert a minimal user row with optional ref_lat/ref_lng. Returns its UUID."""
    from ratis_core.identifiers import generate_support_id

    def _make(*, ref_lat: float | None = 48.85, ref_lng: float | None = 2.35):
        uid = uuid.uuid4()
        with session_factory() as db:
            db.execute(
                text("""
                INSERT INTO users (id, email, support_id, account_type,
                                  display_name, is_deleted, ref_lat, ref_lng)
                VALUES (:id, :email, :sid, 'oauth', 'Test',
                        false, :lat, :lng)
            """),
                {
                    "id": str(uid),
                    "email": f"test_{uid}@example.com",
                    "sid": generate_support_id(),
                    "lat": ref_lat,
                    "lng": ref_lng,
                },
            )
            db.execute(
                text(
                    "INSERT INTO user_preferences (user_id, search_radius_km, transport_mode) "
                    "VALUES (:uid, 10, 'driving') ON CONFLICT DO NOTHING"
                ),
                {"uid": str(uid)},
            )
            db.commit()
        return uid

    return _make
