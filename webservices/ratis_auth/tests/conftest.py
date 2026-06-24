import os

# Hermetic test env — DO NOT load_dotenv(.env.local).
# .env.local is a developer file that may contain placeholder secrets
# (e.g. JWT_SECRET=<random-256-bit-secret>) which silently break tests :
# tokens minted with the test secret can't be validated against the
# placeholder secret leaked from .env.local → 401 storms across all
# auth-protected tests. Tests own their env. See KP-29.
#
# Override-only escape hatches : set TEST_DATABASE_URL in your shell to
# point at a different DB (e.g. CI ephemeral DB).
from ratis_core.test_db import resolve_test_database_url

# Worktree-aware : CI gets its explicit TEST_DATABASE_URL untouched ;
# local dev gets a per-worktree DB suffix so concurrent worktrees do not
# clash on the shared ratis_test DROP/CREATE schema teardown.
TEST_DATABASE_URL = resolve_test_database_url()
# Hard-set (not setdefault) — the app code reads these at import time and
# we must shadow any value the developer may have exported in their shell
# or that may have leaked from an earlier import of a sibling .env file.
os.environ["DATABASE_URL"] = TEST_DATABASE_URL
# RS256 JWT keys — generate an ephemeral pair per test session, write
# both PEMs to a tempdir, and point the app at them. No key is ever
# committed. ratis_auth is the issuer, so it needs the private key too.
import tempfile as _tempfile
from pathlib import Path as _Path

from ratis_core.testing import generate_test_jwt_keypair as _gen_keypair

_jwt_key_dir = _Path(_tempfile.mkdtemp(prefix="ratis-jwt-keys-"))
_private_pem, _public_pem = _gen_keypair()
(_jwt_key_dir / "jwt_private.pem").write_text(_private_pem)
(_jwt_key_dir / "jwt_public.pem").write_text(_public_pem)
os.environ["JWT_PRIVATE_KEY_PATH"] = str(_jwt_key_dir / "jwt_private.pem")
os.environ["JWT_PUBLIC_KEY_PATH"] = str(_jwt_key_dir / "jwt_public.pem")
os.environ["JWT_AUDIENCE"] = "ratis"
os.environ["INTERNAL_API_KEY"] = "test-internal-key"
os.environ["STRIPE_SECRET_KEY"] = "sk_test_placeholder"
os.environ["STRIPE_WEBHOOK_SECRET"] = "whsec_test_placeholder"
# Token TTL knobs — values are not security-sensitive but the app reads
# them at import; force-set so they are deterministic across dev shells.
os.environ["ACCESS_TOKEN_EXPIRE_MINUTES"] = "60"
os.environ["REFRESH_TOKEN_EXPIRE_DAYS"] = "30"
# OAuth client IDs — forced so the auth_service issuer guards see a value.
os.environ["GOOGLE_CLIENT_ID"] = "test-google-client-id"
os.environ["APPLE_CLIENT_ID"] = "test-apple-client-id"
# Admin endpoints (PR11) — ADMIN_API_KEY set at import so the /admin/*
# router is mounted (defense-in-depth gate in main.py). ADMIN_TOTP_SECRET
# is the standard fixed test secret (also used by RW admin tests).
# pragma: allowlist secret — fixed test secrets, never deployed.
os.environ["ADMIN_API_KEY"] = "test-admin-key-padded-to-32-chars-min"
os.environ["ADMIN_TOTP_SECRET"] = "JBSWY3DPEHPK3PXP"
# REDIS_URL — OTT session-bootstrap (Module 10 PR 5). The dep is overridden
# per-test via get_redis override, so this value is never actually connected.
os.environ["REDIS_URL"] = "redis://localhost:6379/0"
os.environ["SENTRY_DSN"] = ""  # DSN vide = Sentry silent en tests
# RGPD anonymize salt (audit F-AU-3) — fixed test value, never deployed.
# pragma: allowlist secret — fixed test fixture.
os.environ["RGPD_ANONYMIZE_SALT"] = "test-rgpd-salt-fixture-fixed-value"

import pytest
from fastapi.testclient import TestClient
from limiter import limiter
from main import app
from ratis_core.database import Base, get_db, make_engine
from sqlalchemy import event, text
from sqlalchemy.orm import sessionmaker

engine = make_engine(TEST_DATABASE_URL)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


@pytest.fixture(scope="session", autouse=True)
def setup_db():
    with engine.connect() as conn:
        # Drop user-defined ENUM types in public — DROP SCHEMA CASCADE handles
        # tables/views/sequences but ENUM types created by Alembic migrations
        # outside of SQLAlchemy metadata can survive recreate_schema/create_all
        # and silently conflict on re-run. Defensive : strip them first.
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
    Base.metadata.create_all(bind=engine)
    # Seed RGPD anon sentinel users row (mirrors migration
    # ``20260511_1000_rgpd_anon_completeness``). delete_account assigns
    # NEVER-PURGE financial table user_id to this sentinel ; the FK on
    # those tables requires the row to exist. ``gift_card_redeemed_ytd_cents``
    # is NOT NULL with no server_default in prod (cf migration
    # 20260508_2200_boutique_v1 which drops the default post-backfill) —
    # must be explicit ; tests via create_all may still have the default but
    # we mirror prod for parity.
    with engine.connect() as conn:
        conn.execute(
            text(
                "INSERT INTO users (id, email, support_id, account_type, "
                "display_name, is_deleted, gift_card_redeemed_ytd_cents) VALUES "
                "('00000000-0000-0000-0000-000000000001', "
                "'anon@deleted.invalid', 'RTS-ANON00', 'internal', "
                "'ratis anon (rgpd)', true, 0) ON CONFLICT (id) DO NOTHING"
            )
        )
        conn.commit()
    # Sentinel : if create_all silently no-op'd (e.g. a model module wasn't
    # imported transitively), fail fast with an explicit message instead of
    # letting hundreds of tests pile up confusing errors.
    with engine.connect() as conn:
        try:
            conn.execute(text("SELECT 1 FROM users LIMIT 0"))
        except Exception as exc:
            raise RuntimeError(
                "conftest setup failed : Base.metadata.create_all() did not "
                "produce expected table 'users'. Likely a model module is not "
                "imported. Check imports at top of conftest."
            ) from exc
    yield
    with engine.connect() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public;"))
        conn.commit()


@pytest.fixture
def db():
    """
    Per-test DB session with full rollback isolation (SQLAlchemy 2.0 SAVEPOINT pattern).

    The outer BEGIN is never committed — service-layer commits only release the
    SAVEPOINT. The event listener reopens the SAVEPOINT after each release so the
    next commit in the same test also stays within the outer transaction.
    """
    connection = engine.connect()
    outer_txn = connection.begin()
    nested = connection.begin_nested()
    session = TestingSessionLocal(bind=connection)

    @event.listens_for(session, "after_transaction_end")
    def restart_savepoint(session, transaction):
        nonlocal nested
        if not nested.is_active:
            nested = connection.begin_nested()

    session._test_connection = connection  # expose for assert_no_pending_changes
    yield session
    session.close()
    outer_txn.rollback()
    connection.close()


@pytest.fixture(autouse=True)
def assert_no_pending_changes(db, request):
    """
    Detect missing db.commit() in route handlers.
    Only applies to tests using HTTP client fixtures (client / user_client / raw_client).
    Uses before_cursor_execute connection event — no monkey-patching.
    """
    _http_fixtures = {"client", "user_client", "raw_client"}
    if not _http_fixtures.intersection(request.fixturenames):
        yield
        return

    _writes: list[str] = []
    conn = db._test_connection

    @event.listens_for(conn, "before_cursor_execute")
    def _track(conn, cursor, statement, parameters, context, executemany):
        sql = statement.strip().upper()
        if sql.startswith(("INSERT ", "UPDATE ", "DELETE ")):
            _writes.append(statement[:80])
        elif sql.startswith("RELEASE SAVEPOINT") or sql.startswith("ROLLBACK TO SAVEPOINT"):
            _writes.clear()

    yield

    event.remove(conn, "before_cursor_execute", _track)

    if _writes:
        lines = "\n".join(f"  {w}" for w in _writes)
        pytest.fail(f"Uncommitted writes detected after test — missing db.commit() in a route handler?\n{lines}")


@pytest.fixture(autouse=True)
def reset_rate_limiter():
    """Reset in-memory rate limit counters between tests to prevent cross-test pollution."""
    limiter._storage.reset()


@pytest.fixture
def client(db):
    def override_get_db():
        try:
            yield db
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture
def raw_client(db):
    """TestClient with DB override but WITHOUT auth bypass — for 401/403 tests."""

    def override_get_db():
        try:
            yield db
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.pop(get_db, None)
