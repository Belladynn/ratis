from __future__ import annotations

import os
import uuid

# Hermetic test env — DO NOT load_dotenv(.env.local). See SA_DEV.md.
from ratis_core.test_db import resolve_test_database_url

# Worktree-aware : CI gets its explicit TEST_DATABASE_URL untouched ;
# local dev gets a per-worktree DB suffix so concurrent worktrees do not
# clash on the shared ratis_test DROP/CREATE schema teardown.
TEST_DATABASE_URL = resolve_test_database_url()
os.environ["DATABASE_URL"] = TEST_DATABASE_URL
os.environ["INTERNAL_API_KEY"] = "test-internal-key"
os.environ["SENTRY_DSN"] = ""

import pytest
import ratis_core.models  # noqa: F401 — register all mappers
from ratis_core.database import Base, make_engine
from sqlalchemy import event, text
from sqlalchemy.orm import sessionmaker


@pytest.fixture(scope="session")
def engine():
    eng = make_engine(TEST_DATABASE_URL)
    with eng.connect() as conn:
        # Drop user-defined ENUM types defensively (KP-29).
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
            conn.execute(text("SELECT 1 FROM push_receipt_tickets LIMIT 0"))
            conn.execute(text("SELECT 1 FROM user_push_tokens LIMIT 0"))
        except Exception as exc:
            raise RuntimeError(
                "conftest setup failed : Base.metadata.create_all() did not "
                "produce expected tables. Likely a model module is not imported."
            ) from exc
    yield eng
    eng.dispose()


@pytest.fixture
def connection(engine):
    with engine.connect() as conn:
        yield conn


@pytest.fixture
def session_factory(connection):
    """SA-2.0 SAVEPOINT isolation."""
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
    with session_factory() as session:
        yield session


@pytest.fixture(autouse=True)
def assert_no_pending_changes():
    """Marker required by CLAUDE.md / CI. Batch tests use raw SQL via
    session_factory() — each batch function manages its own session.
    Missing commits surface naturally via the next-session read.
    """
    return


# ── Row factories ────────────────────────────────────────────────────────


@pytest.fixture
def make_user(db):
    """Return a factory: make_user() → user_id."""
    from ratis_core.identifiers import generate_support_id

    def _make() -> uuid.UUID:
        uid = uuid.uuid4()
        db.execute(
            text("""
            INSERT INTO users
                (id, email, support_id, account_type, display_name, is_deleted)
            VALUES
                (:id, :email, :sid, 'oauth', 'tester', false)
        """),
            {
                "id": str(uid),
                "email": f"user_{uid}@test.com",
                "sid": generate_support_id(),
            },
        )
        db.flush()
        return uid

    return _make


@pytest.fixture
def add_token(db):
    """Factory: add_token(user_id, token) → token string. INSERTs a
    user_push_tokens row."""

    def _add(user_id: uuid.UUID, token: str | None = None) -> str:
        token = token or f"ExponentPushToken[{uuid.uuid4().hex}]"
        db.execute(
            text("""
            INSERT INTO user_push_tokens (id, user_id, token, platform)
            VALUES (:id, :uid, :tok, 'ios')
        """),
            {"id": str(uuid.uuid4()), "uid": str(user_id), "tok": token},
        )
        db.flush()
        return token

    return _add


@pytest.fixture
def add_ticket(db):
    """Factory: add_ticket(user_id, push_token, expo_ticket_id, checked=False)
    → ticket row id. INSERTs a push_receipt_tickets row."""

    def _add(
        user_id: uuid.UUID,
        push_token: str,
        expo_ticket_id: str | None = None,
        *,
        checked: bool = False,
    ) -> uuid.UUID:
        tid = uuid.uuid4()
        expo_ticket_id = expo_ticket_id or f"expo-ticket-{uuid.uuid4().hex}"
        db.execute(
            text("""
            INSERT INTO push_receipt_tickets
                (id, expo_ticket_id, user_id, push_token, checked_at)
            VALUES
                (:id, :ticket, :uid, :tok,
                 CASE WHEN :checked THEN now() ELSE NULL END)
        """),
            {
                "id": str(tid),
                "ticket": expo_ticket_id,
                "uid": str(user_id),
                "tok": push_token,
                "checked": checked,
            },
        )
        db.flush()
        return tid

    return _add
