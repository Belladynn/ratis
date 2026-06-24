import os
from datetime import datetime

# Hermetic test env — DO NOT load_dotenv(.env.local).
# .env.local is a developer file that may contain placeholder secrets
# which silently break tests. Tests own their env. See KP-29.
from ratis_core.test_db import resolve_test_database_url

# Worktree-aware : CI gets its explicit TEST_DATABASE_URL untouched ;
# local dev gets a per-worktree DB suffix so concurrent worktrees do not
# clash on the shared ratis_test DROP/CREATE schema teardown.
TEST_DATABASE_URL = resolve_test_database_url()
# Hard-set (not setdefault) — must be set before `from main import app`
# because require_env() runs in the lifespan, and we shadow any leaked
# value from the developer's shell or earlier env loads.
os.environ["DATABASE_URL"] = TEST_DATABASE_URL
os.environ["INTERNAL_API_KEY"] = "test-internal-key"
os.environ["SENTRY_DSN"] = ""  # DSN vide = Sentry silent en tests
# EXPO_PUSH_URL stays setdefault — it's a non-secret URL that a dev may
# legitimately want to override via shell export to point at a sandbox.
os.environ.setdefault("EXPO_PUSH_URL", "https://exp.host/--/api/v2/push/send")
# REDIS_URL — required by lifespan since V1.1 (push rate-limit SETNX). The
# tests use fakeredis via dependency-override (see ``rate_limiter`` fixture)
# so the URL itself is never dialled, but require_env demands a value.
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")

import fakeredis
import pytest
from fastapi.testclient import TestClient
from main import app
from ratis_core.database import Base, get_db, make_engine
from ratis_core.deps import verify_internal_key
from services.notify_service import get_now, get_rate_limiter
from services.push_rate_limiter import RedisPushRateLimiter
from sqlalchemy import event, text
from sqlalchemy.orm import sessionmaker

engine = make_engine(TEST_DATABASE_URL)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


@pytest.fixture(scope="session", autouse=True)
def setup_db():
    with engine.connect() as conn:
        # Drop user-defined ENUM types defensively (see KP-29).
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
    # Sentinel : fail fast if create_all silently no-op'd.
    with engine.connect() as conn:
        try:
            conn.execute(text("SELECT 1 FROM notification_outbox LIMIT 0"))
        except Exception as exc:
            raise RuntimeError(
                "conftest setup failed : Base.metadata.create_all() did not "
                "produce expected table 'notification_outbox'. Likely a "
                "model module is not imported."
            ) from exc
    yield
    with engine.connect() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public;"))
        conn.commit()


@pytest.fixture
def db():
    """Per-test DB session with full rollback isolation (SAVEPOINT pattern)."""
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


@pytest.fixture
def freeze_time():
    """Override get_now for the duration of one test. Cleanup is guaranteed."""

    def _freeze(fixed: datetime):
        app.dependency_overrides[get_now] = lambda: lambda: fixed

    yield _freeze
    app.dependency_overrides.pop(get_now, None)


@pytest.fixture
def bypass_internal_auth():
    """Bypass inter-service auth for tests that don't exercise auth behaviour."""
    app.dependency_overrides[verify_internal_key] = lambda: None
    try:
        yield
    finally:
        app.dependency_overrides.pop(verify_internal_key, None)


@pytest.fixture
def fake_rate_limiter():
    """Fakeredis-backed PushRateLimiter — atomic SETNX semantics, no network.

    Yields the limiter so individual tests can pre-populate cooldown keys
    (``limiter._client.set(...)``) to assert the "second push is rate-limited"
    branch deterministically.
    """
    fake = fakeredis.FakeStrictRedis()
    limiter = RedisPushRateLimiter(fake)
    app.dependency_overrides[get_rate_limiter] = lambda: limiter
    try:
        yield limiter
    finally:
        app.dependency_overrides.pop(get_rate_limiter, None)
        fake.flushall()


@pytest.fixture
def client(db, bypass_internal_auth, fake_rate_limiter):
    """TestClient with DB override, inter-service auth bypassed, and a
    fakeredis-backed rate-limiter wired in via dependency override."""

    def override_get_db():
        try:
            yield db
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db
    try:
        with TestClient(app) as c:
            yield c
    finally:
        app.dependency_overrides.pop(get_db, None)


@pytest.fixture
def raw_client(db, fake_rate_limiter):
    """TestClient with DB override and rate-limiter override but WITHOUT auth
    bypass — for testing 403 responses."""

    def override_get_db():
        try:
            yield db
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db
    try:
        with TestClient(app) as c:
            yield c
    finally:
        app.dependency_overrides.pop(get_db, None)
