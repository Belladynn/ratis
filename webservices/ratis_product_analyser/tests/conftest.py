import os

# Hermetic test env — DO NOT load_dotenv(.env.local).
# .env.local is a developer file that may contain placeholder secrets
# (e.g. JWT_SECRET=<random-256-bit-secret> or DB URLs with <port>) which
# silently break tests : tokens minted with the test secret can't be
# validated against the placeholder secret leaked from .env.local → 401
# storms across all auth-protected tests. Tests own their env.
#
# Override-only escape hatches : set TEST_DATABASE_URL in your shell to
# point at a different DB (e.g. CI ephemeral DB).
from ratis_core.test_db import resolve_test_database_url

# Worktree-aware : CI gets its explicit TEST_DATABASE_URL untouched ;
# local dev gets a per-worktree DB suffix so concurrent worktrees do not
# clash on the shared ratis_test DROP/CREATE schema teardown.
TEST_DATABASE_URL = resolve_test_database_url()
# Hard-set (not setdefault) — the app code reads these at import time and
# we must shadow any value the developer may have exported in their shell.
os.environ["DATABASE_URL"] = TEST_DATABASE_URL
# RS256 JWT keys — ephemeral pair per test session. PA only verifies
# tokens (JWT_PUBLIC_KEY_PATH), but make_token() below mints test
# tokens, so the private PEM is kept in-process for that.
import tempfile as _tempfile
from pathlib import Path as _Path

from ratis_core.testing import generate_test_jwt_keypair as _gen_keypair

_jwt_key_dir = _Path(_tempfile.mkdtemp(prefix="ratis-jwt-keys-"))
JWT_TEST_PRIVATE_PEM, _public_pem = _gen_keypair()
(_jwt_key_dir / "jwt_public.pem").write_text(_public_pem)
os.environ["JWT_PUBLIC_KEY_PATH"] = str(_jwt_key_dir / "jwt_public.pem")
os.environ["JWT_AUDIENCE"] = "ratis"
os.environ["R2_ENDPOINT_URL"] = "https://fake.r2.cloudflarestorage.com"
os.environ["R2_ACCESS_KEY_ID"] = "test-key-id"
os.environ["R2_SECRET_ACCESS_KEY"] = "test-secret"
os.environ["R2_BUCKET_NAME"] = "ratis-ocr-images"
os.environ["REDIS_URL"] = "redis://localhost:6379/0"
os.environ["SENTRY_DSN"] = ""  # DSN vide = Sentry silent en tests
# Langfuse keys empty = LLM tracing no-op in tests/CI (init_langfuse early-returns,
# @observe stays inert). Mirrors SENTRY_DSN="". cf ARCH_llm_observability.md.
os.environ["LANGFUSE_PUBLIC_KEY"] = ""
os.environ["LANGFUSE_SECRET_KEY"] = ""
os.environ["LANGFUSE_HOST"] = ""
# Hard-disable the langfuse SDK in tests. ``@observe`` lazily auto-initialises
# the global langfuse client on first call even with empty keys ; that client
# registers an OTEL span processor whose exporter then logs a noisy
# "Failed to export span batch" at interpreter shutdown (bad URL, no host).
# ``LANGFUSE_TRACING_ENABLED=false`` is the SDK's own kill-switch → the
# decorator becomes a true pass-through, zero background threads, zero network.
os.environ["LANGFUSE_TRACING_ENABLED"] = "false"
# LLM filter — Phase 2h retired the LLM_FILTER_ENABLED gate. Tests that
# don't want the LLM path either inject ``_llm=None`` into the worker
# task or rely on the ``FakeLlmFilter`` being silent (default empty
# denoise output).
# Force mistral provider deterministically. Tests for the anthropic
# provider explicitly monkeypatch.setenv("LLM_PROVIDER", "anthropic")
# and provide their own LLM_API_KEY shape.
os.environ["LLM_PROVIDER"] = "mistral"
os.environ.setdefault("LLM_BASE_URL", "https://fake.llm.test/v1")
os.environ.setdefault("LLM_API_KEY", "test-llm-key")
os.environ.setdefault("LLM_MODEL", "mistral-small-latest")
# Admin API key — required for the admin router to be mounted (PR #126).
# Hard-set so tests always exercise the same code path as production with
# the flag enabled.
os.environ["ADMIN_API_KEY"] = "test-admin-key-padded-to-32-chars-min"
# Cross-service URL for the admin mini UI → AU calls (UI-1.5). Tests mock
# the underlying ``au_get`` helper so the URL is never actually hit, but
# the ``require_env`` lifespan check needs a non-empty value.
os.environ["AU_BASE_URL"] = "http://ratis_auth.test:8001"
# Cross-service URL for the admin mini UI → RW calls (admin-settings
# Bloc C). Tests mock the underlying ``rw_get/rw_put/rw_post`` helpers
# (or stub via httpx.MockTransport), but the lifespan ``require_env``
# check needs a non-empty value when ADMIN_API_KEY is set.
os.environ["RW_BASE_URL"] = "http://ratis_rewards.test:8004"
# HSP3 — N8N_RESUME_SECRET partagé n8n↔PA pour la reprise inversée (cf
# design §M2 Reprise n8n inversée). 32 chars min comme ADMIN_API_KEY.
os.environ["N8N_RESUME_SECRET"] = "test-n8n-resume-secret-padded-32-chars"
# HSP3 — INTERNAL_API_KEY pour les endpoints machine→machine (verify_internal_key).
# Doit correspondre à la valeur fournie dans les fixtures de test `internal_headers`.
os.environ["INTERNAL_API_KEY"] = "test-internal"
# HSP4 — mot de passe utilisé par la migration `apply_hsp4_agent_confinement`
# pour créer le rôle agent_read. Les tests qui font tourner alembic upgrade
# (via spin_up_migrated_db) en ont besoin. pragma: allowlist secret
os.environ["AGENT_READ_PASSWORD"] = "test-agent-read-password-32-chars!"
# STORE_DEBUG default for tests : off. Tests that need debug persistence
# enable it via monkeypatch.setenv (see test_admin_debug.py).
os.environ.setdefault("STORE_DEBUG", "false")

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from main import app
from ratis_core.database import Base, get_db, make_engine
from ratis_core.models.db_change_log import DbChangeLog  # noqa: F401 — register for create_all (HSP4)
from ratis_core.models.product import Product
from ratis_core.models.retailer import Retailer
from ratis_core.models.scan_debug import ScanDebug  # noqa: F401 — register table for create_all
from ratis_core.models.store import Store
from ratis_core.models.user import User
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
        # pg_trgm required BEFORE create_all() — GIN trgm indexes declared
        # in models (e.g. ProductNameResolution.idx_pnr_norm_label_trgm)
        # need gin_trgm_ops at table-creation time. Bloc A NRC cross-retailer
        # added an index in the model itself, so the extension must exist
        # before the metadata.create_all() call below.
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))
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
    # Sentinel : if create_all silently no-op'd (e.g. a model module wasn't
    # imported transitively), fail fast with an explicit message instead of
    # letting hundreds of tests pile up confusing errors.
    with engine.connect() as conn:
        try:
            conn.execute(text("SELECT 1 FROM products LIMIT 0"))
            conn.execute(text("SELECT 1 FROM scans LIMIT 0"))
            conn.execute(text("SELECT 1 FROM users LIMIT 0"))
        except Exception as exc:
            raise RuntimeError(
                "conftest setup failed : Base.metadata.create_all() did not "
                "produce expected tables (products/scans/users). Likely a "
                "model module is not imported. Check imports at top of conftest."
            ) from exc
    with engine.connect() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))
        conn.execute(
            text("""
            CREATE INDEX IF NOT EXISTS gin_products_name
            ON products USING gin (name gin_trgm_ops)
        """)
        )
        conn.execute(
            text("""
            DROP VIEW IF EXISTS product_observed_names;
            CREATE VIEW product_observed_names AS
            SELECT
                s.store_id,
                s.product_ean,
                s.scanned_name,
                COUNT(*) AS frequency
            FROM scans s
            WHERE s.status = 'accepted'
              AND s.product_ean IS NOT NULL
              AND s.scanned_name IS NOT NULL
            GROUP BY s.store_id, s.product_ean, s.scanned_name
        """)
        )
        conn.commit()
    yield
    with engine.connect() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public;"))
        conn.commit()


@pytest.fixture
def db():
    connection = engine.connect()
    outer_txn = connection.begin()
    # join_transaction_mode="create_savepoint": the session issues SAVEPOINT/
    # ROLLBACK TO SAVEPOINT instead of a full ROLLBACK when session.rollback()
    # is called, preserving the outer transaction across task-level rollbacks.
    session = TestingSessionLocal(bind=connection, join_transaction_mode="create_savepoint")
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
    _http_fixtures = {"client", "user_client", "raw_client", "admin_client"}
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
def retailer(db) -> Retailer:
    """Default retailer attached to the ``store`` fixture.

    Bloc B (cross-retailer consensus) consumes ``stores.retailer_id`` —
    we seed a real Retailer row so the trigger ``fn_sync_pnr_retailer_id``
    can denorm the FK on every ``product_name_resolutions`` insert. Tests
    that need multiple retailers create their own additional rows.
    """
    # canonical_name "Lidl" + slug "lidl" — matches the legacy
    # ``store.retailer`` text the conftest exposed before Bloc B
    # (``retailer="lidl"``), so existing tests that depend on
    # ``store.retailer.lower() == "lidl"`` keep working with the
    # trigger-rewritten value. Savepoint isolation gives each test its
    # own row, so the fixed slug/canonical_name don't accumulate.
    r = Retailer(
        id=uuid.uuid4(),
        canonical_name="Lidl",
        slug="lidl",
        country_code="FR",
    )
    db.add(r)
    db.flush()
    db.commit()
    return r


@pytest.fixture
def store(db, retailer) -> Store:
    s = Store(
        id=uuid.uuid4(),
        name="Lidl Test",
        retailer="lidl",
        retailer_id=retailer.id,
        address="1 rue du Test",
        city="Paris",
        postal_code="75001",
        lat=48.8566,
        lng=2.3522,
    )
    db.add(s)
    db.flush()
    db.commit()
    return s


@pytest.fixture
def user(db) -> User:
    # Since H2 Phase 2 the OAuth identity lives in ``user_identities`` ;
    # the ``users`` row only carries an ``account_type`` state.
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
def mock_upload(monkeypatch):
    """Replace R2 upload with a no-op."""
    monkeypatch.setattr("services.scan_service.upload_receipt_image", lambda *a, **kw: None)


@pytest.fixture
def mock_enqueue(monkeypatch):
    """Replace Celery enqueue with a no-op."""
    monkeypatch.setattr("services.scan_service.enqueue_ocr_job", lambda receipt_id: None)


@pytest.fixture
def mock_upload_label(monkeypatch):
    """Replace R2 label upload with a no-op."""
    monkeypatch.setattr("services.label_service.upload_label_image", lambda *a, **kw: None)


@pytest.fixture
def mock_enqueue_label(monkeypatch):
    """Replace Celery label enqueue with a no-op."""
    monkeypatch.setattr("services.label_service.enqueue_label_job", lambda scan_id, **kw: None)


@pytest.fixture(autouse=True)
def reset_rate_limiter():
    """Reset in-memory rate limit counters between tests to prevent cross-test pollution."""
    from limiter import limiter

    limiter._storage.reset()


@pytest.fixture
def client(db, mock_upload, mock_enqueue, mock_upload_label, mock_enqueue_label):
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


@pytest.fixture
def bypass_admin_auth():
    """Bypass admin key auth for tests that don't exercise auth behaviour."""
    from ratis_core.deps import verify_admin_key

    app.dependency_overrides[verify_admin_key] = lambda: None
    try:
        yield
    finally:
        app.dependency_overrides.pop(verify_admin_key, None)


@pytest.fixture
def admin_client(db, bypass_admin_auth):
    """TestClient with DB override and admin key auth bypassed."""

    def override_get_db():
        try:
            yield db
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.pop(get_db, None)
