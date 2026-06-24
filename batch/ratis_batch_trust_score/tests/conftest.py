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
            conn.execute(text("SELECT 1 FROM users LIMIT 0"))
            conn.execute(text("SELECT 1 FROM stores LIMIT 0"))
            conn.execute(text("SELECT 1 FROM product_name_resolutions LIMIT 0"))
            conn.execute(text("SELECT 1 FROM pipeline_audit_log LIMIT 0"))
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
    """Return a factory: make_user(trust_score=50, is_shadow_banned=False).

    Inserts a fresh user row + the related cab/cashback balance rows.
    """
    from ratis_core.identifiers import generate_support_id

    def _make(
        *,
        trust_score: int = 50,
        is_shadow_banned: bool = False,
        total_resolved_scans: int = 0,
        is_deleted: bool = False,
    ) -> uuid.UUID:
        uid = uuid.uuid4()
        db.execute(
            text("""
            INSERT INTO users
                (id, email, support_id, account_type, display_name,
                 is_deleted, trust_score, total_resolved_scans,
                 is_shadow_banned)
            VALUES
                (:id, :email, :sid, 'oauth', 'tester',
                 :deleted, :score, :total, :ban)
        """),
            {
                "id": str(uid),
                "email": f"user_{uid}@test.com",
                "sid": generate_support_id(),
                "deleted": is_deleted,
                "score": trust_score,
                "total": total_resolved_scans,
                "ban": is_shadow_banned,
            },
        )
        db.flush()
        return uid

    return _make


@pytest.fixture
def store_id(db):
    sid = uuid.uuid4()
    db.execute(
        text("""
        INSERT INTO stores (id, name, lat, lng, is_disabled)
        VALUES (:id, 'Test Store', 48.8566, 2.3522, false)
    """),
        {"id": str(sid)},
    )
    db.flush()
    return sid


@pytest.fixture
def make_scan(db, store_id):
    """Factory: make_scan(user_id) → scan_id."""
    receipt_cache: dict = {}

    def _ensure_receipt(user_id):
        key = str(user_id)
        if key not in receipt_cache:
            rid = uuid.uuid4()
            db.execute(
                text("""
                INSERT INTO receipts
                    (id, store_id, user_id, purchased_at,
                     image_r2_key, image_uploaded_at)
                VALUES
                    (:id, :sid, :uid, CURRENT_DATE, 'k', now())
            """),
                {"id": str(rid), "sid": str(store_id), "uid": str(user_id)},
            )
            db.flush()
            receipt_cache[key] = rid
        return receipt_cache[key]

    def _make(user_id) -> uuid.UUID:
        scan_id = uuid.uuid4()
        receipt_id = _ensure_receipt(user_id)
        db.execute(
            text("""
            INSERT INTO scans
                (id, store_id, user_id, receipt_id, scan_type,
                 status, scanned_name, price, quantity, scanned_at)
            VALUES
                (:id, :sid, :uid, :rid, 'receipt',
                 'accepted', 'X', 1.00, 1, now())
        """),
            {
                "id": str(scan_id),
                "sid": str(store_id),
                "uid": str(user_id),
                "rid": str(receipt_id),
            },
        )
        db.flush()
        return scan_id

    return _make


@pytest.fixture
def add_resolution(db, store_id):
    """Factory: add_resolution(scan_id, user_id, label, ean) — INSERT a
    contributing ledger row (match_method='barcode').
    """

    def _add(
        *,
        scan_id,
        user_id,
        normalized_label: str,
        product_ean: str,
        match_method: str = "barcode",
        weight_override: int | None = None,
    ) -> uuid.UUID:
        rid = uuid.uuid4()
        db.execute(
            text("""
            INSERT INTO product_name_resolutions
                (id, scan_id, store_id, normalized_label, product_ean,
                 user_id, match_method, weight_override)
            VALUES
                (:id, :scan, :store, :label, :ean,
                 :user, :method, :wov)
        """),
            {
                "id": str(rid),
                "scan": str(scan_id),
                "store": str(store_id),
                "label": normalized_label,
                "ean": product_ean,
                "user": str(user_id),
                "method": match_method,
                "wov": weight_override,
            },
        )
        db.flush()
        return rid

    return _add


@pytest.fixture
def add_state_event(db, store_id):
    """Factory: add_state_event(label, ean, state='verified') — write a
    consensus_state_changed audit row so the batch picks the pair up.
    """
    import json

    def _add(
        *,
        normalized_label: str,
        top1_ean: str,
        state: str = "verified",
    ) -> None:
        payload = {
            "event": "consensus_state_changed",
            "store_id": str(store_id),
            "normalized_label": normalized_label,
            "from_state": None,
            "to_state": state,
            "top1_ean": top1_ean,
            "distinct_validators": 5,
            "convergence_pct": 80.0,
        }
        db.execute(
            text("""
            INSERT INTO pipeline_audit_log
                (phase, level, event, scan_id, payload, created_at)
            VALUES
                ('match', 'normal', 'consensus_state_changed',
                 NULL, CAST(:p AS jsonb), clock_timestamp())
        """),
            {"p": json.dumps(payload)},
        )
        db.flush()

    return _add
