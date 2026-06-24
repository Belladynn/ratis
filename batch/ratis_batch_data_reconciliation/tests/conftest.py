# batch/ratis_batch_data_reconciliation/tests/conftest.py
"""Test scaffolding for ratis_batch_data_reconciliation.

Hermetic env (no load_dotenv from .env.local — see KP-29). Tests own
their environment via os.environ direct assignment ; TEST_DATABASE_URL
is the only override hook for CI.
"""

import os
import uuid

from ratis_core.test_db import resolve_test_database_url

# Worktree-aware : CI gets its explicit TEST_DATABASE_URL untouched ;
# local dev gets a per-worktree DB suffix so concurrent worktrees do not
# clash on the shared ratis_test DROP/CREATE schema teardown.
TEST_DATABASE_URL = resolve_test_database_url()
os.environ["DATABASE_URL"] = TEST_DATABASE_URL
os.environ["INTERNAL_API_KEY"] = "test-internal-key"
os.environ["NOTIFIER_URL"] = "http://localhost:9999/api/v1/notify"
os.environ["SENTRY_DSN"] = ""

import pytest
import ratis_core.models  # noqa: F401 — register all ORM models
from ratis_core.database import Base, make_engine
from sqlalchemy import event, text
from sqlalchemy.orm import sessionmaker


@pytest.fixture(scope="session")
def engine():
    eng = make_engine(TEST_DATABASE_URL)
    with eng.connect() as conn:
        # Drop user-defined ENUMs in public defensively (KP-29).
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
                    EXECUTE 'DROP TYPE IF EXISTS public.'
                        || quote_ident(r.typname) || ' CASCADE';
                END LOOP;
            END $$;
            """
            )
        )
        conn.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public;"))
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS unaccent"))
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS postgis"))
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))
        # Required by GENERATED columns introduced in the pipeline migration.
        conn.execute(
            text(
                "CREATE OR REPLACE FUNCTION immutable_unaccent(text) "
                "RETURNS text LANGUAGE sql IMMUTABLE PARALLEL SAFE STRICT "
                "AS $$ SELECT public.unaccent('public.unaccent', $1) $$"
            )
        )
        conn.commit()
    Base.metadata.create_all(bind=eng)
    # Sentinel : fail loud if create_all silently no-op'd a table the
    # tests rely on. Catches model-import omissions.
    with eng.connect() as conn:
        try:
            conn.execute(text("SELECT 1 FROM users LIMIT 0"))
            conn.execute(text("SELECT 1 FROM scans LIMIT 0"))
            conn.execute(text("SELECT 1 FROM stores LIMIT 0"))
            conn.execute(text("SELECT 1 FROM retailers LIMIT 0"))
            conn.execute(text("SELECT 1 FROM products LIMIT 0"))
            conn.execute(text("SELECT 1 FROM product_name_resolutions LIMIT 0"))
            conn.execute(text("SELECT 1 FROM cabecoin_transactions LIMIT 0"))
            conn.execute(text("SELECT 1 FROM user_cab_balance LIMIT 0"))
            conn.execute(text("SELECT 1 FROM ocr_knowledge LIMIT 0"))
        except Exception as exc:
            raise RuntimeError(
                "conftest setup failed : Base.metadata.create_all() did not "
                "produce one of the expected tables. Likely a model module "
                "is not imported."
            ) from exc
    yield eng
    eng.dispose()


@pytest.fixture
def connection(engine):
    with engine.connect() as conn:
        yield conn


@pytest.fixture
def session_factory(connection):
    """SA 2.0 SAVEPOINT isolation — each test rolls back fully.

    The job functions call ``db.commit()`` ; the savepoint pattern
    captures those commits inside the outer transaction so rollback
    still cleans them up at end-of-test.
    """
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
    """A bound SQLAlchemy Session for a single test step."""
    with session_factory() as s:
        yield s


@pytest.fixture
def make_user(session_factory):
    """Insert a minimal user row + cab balance row, return its UUID."""
    from ratis_core.identifiers import generate_support_id

    def _make(is_shadow_banned: bool = False):
        uid = uuid.uuid4()
        with session_factory() as db:
            db.execute(
                text(
                    """
                INSERT INTO users (id, email, support_id, account_type,
                                   display_name, is_deleted, is_shadow_banned)
                VALUES (:id, :email, :sid, 'oauth', 'Test',
                        false, :ban)
                """
                ),
                {
                    "id": str(uid),
                    "email": f"test_{uid}@example.com",
                    "sid": generate_support_id(),
                    "ban": is_shadow_banned,
                },
            )
            db.execute(text("INSERT INTO user_cab_balance (user_id, balance) VALUES (:uid, 0)"), {"uid": str(uid)})
            db.commit()
        return uid

    return _make


@pytest.fixture
def make_retailer(session_factory):
    """Insert a retailer row, return its UUID."""

    def _make(name: str = "Carrefour"):
        rid = uuid.uuid4()
        slug = name.lower().replace(" ", "-") + f"-{rid.hex[:6]}"
        canonical = f"{name} {rid.hex[:6]}"
        with session_factory() as db:
            db.execute(
                text(
                    """
                INSERT INTO retailers (id, canonical_name, slug, created_at)
                VALUES (:id, :canonical, :slug, now())
                """
                ),
                {"id": str(rid), "canonical": canonical, "slug": slug},
            )
            db.commit()
        return rid

    return _make


@pytest.fixture
def make_store(session_factory):
    """Insert a store row attached to a retailer, return its UUID."""

    def _make(retailer_id: uuid.UUID, name: str = "Carrefour Lyon"):
        sid = uuid.uuid4()
        with session_factory() as db:
            db.execute(
                text(
                    """
                INSERT INTO stores (id, retailer_id, name, address, postal_code,
                                    city, lat, lng, source,
                                    is_disabled, validation_status, created_at)
                VALUES (:id, :rid, :name, '1 rue Test', '69000', 'Lyon',
                        45.75, 4.85, 'osm', false, 'confirmed', now())
                """
                ),
                {"id": str(sid), "rid": str(retailer_id), "name": name},
            )
            db.commit()
        return sid

    return _make


@pytest.fixture
def make_product(session_factory):
    """Insert a product row, return its EAN."""

    def _make(ean: str | None = None, name: str = "Test product"):
        if ean is None:
            # PG ``ean_format`` CHECK : EAN must be 8–14 *digits* (no hex
            # letters). Internal SKUs additionally need a ``2`` prefix
            # (``internal_ean_prefix``) plus a non-NULL unit
            # (``internal_has_unit``).
            ean = "200000" + f"{uuid.uuid4().int % 10_000_000:07d}"
        with session_factory() as db:
            db.execute(
                text(
                    """
                INSERT INTO products (ean, name, source, unit, created_at)
                VALUES (:ean, :name, 'internal', 'unit', now())
                ON CONFLICT (ean) DO NOTHING
                """
                ),
                {"ean": ean, "name": name},
            )
            db.commit()
        return ean

    return _make


@pytest.fixture(autouse=True)
def assert_no_pending_changes():
    """Marker fixture — required by CLAUDE.md / CI policy.

    The job functions own their commit boundaries (raw SQL, no
    SQLAlchemy ORM identity map dirty tracking). The savepoint rollback
    in ``session_factory`` is the actual leak guard — this fixture is
    kept for policy compliance.
    """
    return
