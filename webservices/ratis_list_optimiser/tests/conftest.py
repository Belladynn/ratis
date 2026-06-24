import os
from urllib.parse import urlparse

# Hermetic test env — DO NOT load_dotenv(.env.local).
# .env.local is a developer file that may contain placeholder secrets
# (e.g. JWT_SECRET=<random-256-bit-secret>) which silently break tests :
# tokens minted with the test secret can't be validated against the
# placeholder secret leaked from .env.local → 401 storms. Tests own
# their env. See KP-29.
from ratis_core.test_db import resolve_test_database_url

# Worktree-aware : CI gets its explicit TEST_DATABASE_URL untouched ;
# local dev gets a per-worktree DB suffix so concurrent worktrees do not
# clash on the shared ratis_test DROP/CREATE schema teardown.
TEST_DATABASE_URL = resolve_test_database_url()
# Hard-set (not setdefault) for security-sensitive values — shadow any
# leaked value from the shell or earlier env loads. Defense in depth.
os.environ["DATABASE_URL"] = TEST_DATABASE_URL
# RS256 JWT keys — ephemeral pair per test session. LO verifies tokens
# (JWT_PUBLIC_KEY_PATH); make_token() below mints test tokens with the
# in-process private PEM.
import tempfile as _tempfile
from pathlib import Path as _Path

from ratis_core.testing import generate_test_jwt_keypair as _gen_keypair

_jwt_key_dir = _Path(_tempfile.mkdtemp(prefix="ratis-jwt-keys-"))
JWT_TEST_PRIVATE_PEM, _public_pem = _gen_keypair()
(_jwt_key_dir / "jwt_public.pem").write_text(_public_pem)
os.environ["JWT_PUBLIC_KEY_PATH"] = str(_jwt_key_dir / "jwt_public.pem")
os.environ["JWT_AUDIENCE"] = "ratis"
# Non-secret URLs stay setdefault — a dev may legitimately want to override
# via shell export to point at local OSRM/Redis instances.
os.environ.setdefault("OSRM_BASE_URL", "http://localhost:5000")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from main import app
from ratis_core.database import Base, get_db, make_engine
from ratis_core.models.product import Product
from ratis_core.models.store import Store
from ratis_core.models.user import User
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import sessionmaker


def _ensure_test_db_exists(url: str) -> None:
    """Create the test database if it doesn't exist (needed in CI)."""
    parsed = urlparse(url)
    db_name = parsed.path.lstrip("/")
    # Build URL pointing at the default 'postgres' database
    admin_url = url.rsplit("/", 1)[0] + "/postgres"
    admin_engine = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    with admin_engine.connect() as conn:
        exists = conn.execute(
            text("SELECT 1 FROM pg_database WHERE datname = :name"),
            {"name": db_name},
        ).scalar()
        if not exists:
            # Use raw SQL — CREATE DATABASE cannot use parameters
            conn.execute(text(f'CREATE DATABASE "{db_name}"'))
    admin_engine.dispose()


_ensure_test_db_exists(TEST_DATABASE_URL)

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
            conn.execute(text("SELECT 1 FROM shopping_lists LIMIT 0"))
            conn.execute(text("SELECT 1 FROM optimized_routes LIMIT 0"))
        except Exception as exc:
            raise RuntimeError(
                "conftest setup failed : Base.metadata.create_all() did not "
                "produce expected tables (shopping_lists/optimized_routes). "
                "Likely a model module is not imported."
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
    session = TestingSessionLocal(bind=connection, join_transaction_mode="create_savepoint")
    session._test_connection = connection
    yield session
    session.close()
    outer_txn.rollback()
    connection.close()


@pytest.fixture(autouse=True)
def assert_no_pending_changes(db, request):
    """
    Detect missing db.commit() in route handlers.
    Only applies to tests using HTTP client fixtures.
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
def store(db) -> Store:
    s = Store(
        id=uuid.uuid4(),
        name="Lidl Test",
        retailer="lidl",
        address="1 rue du Test",
        city="Paris",
        postal_code="75001",
        lat=Decimal("48.857"),
        lng=Decimal("2.352"),
    )
    db.add(s)
    db.flush()
    db.commit()
    return s


@pytest.fixture
def user(db) -> User:
    uid = uuid.uuid4()
    u = User(
        id=uid,
        email="test@ratis.fr",
        display_name="TestUser",
        account_type="oauth",
        is_deleted=False,
    )
    db.add(u)
    db.flush()
    db.commit()
    return u


def make_token(user_id: uuid.UUID) -> str:
    """Generate a valid JWT access token for tests.

    Mirrors the prod token contract (ratis_auth): exp + iat + sub are all
    required by ratis_core.jwt.decode_access_token.
    """
    now = datetime.now(UTC)
    payload = {
        "sub": str(user_id),
        "type": "access",
        "aud": "ratis",
        "iat": now,
        "exp": now + timedelta(minutes=60),
    }
    from ratis_core.testing import make_test_token

    return make_test_token(payload, JWT_TEST_PRIVATE_PEM)


@pytest.fixture
def product(db) -> Product:
    p = Product(ean="3017620422003", name="Nutella 400g", source="off")
    db.add(p)
    db.flush()
    db.commit()
    return p


@pytest.fixture
def client(db):
    """TestClient with DB override."""

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
def user_client(db, user):
    """TestClient with DB override and JWT auth for test user."""

    def override_get_db():
        try:
            yield db
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db
    token = make_token(user.id)
    with TestClient(app) as c:
        c.headers["Authorization"] = f"Bearer {token}"
        yield c
    app.dependency_overrides.clear()
