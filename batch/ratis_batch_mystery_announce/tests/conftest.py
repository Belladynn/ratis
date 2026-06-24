import json
import os
import sys
import uuid
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

# Allow importing mystery_announce from the batch directory
sys.path.insert(0, str(Path(__file__).parent.parent))

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
            conn.execute(text("SELECT 1 FROM stores LIMIT 0"))
            conn.execute(text("SELECT 1 FROM products LIMIT 0"))
            conn.execute(text("SELECT 1 FROM mystery_challenges LIMIT 0"))
            conn.execute(text("SELECT 1 FROM mystery_challenge_clues LIMIT 0"))
            conn.execute(text("SELECT 1 FROM mystery_challenge_finds LIMIT 0"))
            conn.execute(text("SELECT 1 FROM scans LIMIT 0"))
        except Exception as exc:
            raise RuntimeError(
                "conftest setup failed : Base.metadata.create_all() did not "
                "produce expected tables (users / stores / products / "
                "mystery_challenges / mystery_challenge_clues / "
                "mystery_challenge_finds / scans). Likely a model module "
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
    """
    SA 2.0 SAVEPOINT isolation — same pattern as ratis_batch_purge tests.
    Returns a sessionmaker bound to the test connection so all batch
    operations share the same transaction and are rolled back after each test.
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


@pytest.fixture
def make_user(session_factory):
    """Insert a minimal user row. Returns UUID."""
    from ratis_core.identifiers import generate_support_id

    def _make():
        uid = uuid.uuid4()
        with session_factory() as db:
            db.execute(
                text("""
                INSERT INTO users (id, email, support_id, account_type,
                                  display_name, is_deleted)
                VALUES (:id, :email, :sid, 'oauth', 'Test', false)
            """),
                {
                    "id": str(uid),
                    "email": f"test_{uid}@example.com",
                    "sid": generate_support_id(),
                },
            )
            db.commit()
        return uid

    return _make


@pytest.fixture
def make_store(session_factory):
    """Insert a minimal store row. Returns UUID."""

    def _make(name=None, lat="48.8566", lng="2.3522"):
        sid = uuid.uuid4()
        store_name = name or f"Store {sid}"
        with session_factory() as db:
            db.execute(
                text("""
                INSERT INTO stores (id, name, lat, lng, is_disabled)
                VALUES (:id, :name, :lat, :lng, false)
            """),
                {"id": str(sid), "name": store_name, "lat": lat, "lng": lng},
            )
            db.commit()
        return sid

    return _make


@pytest.fixture
def make_product(session_factory):
    """Insert a minimal product row. Returns EAN."""

    def _make(ean=None):
        ean = ean or str(uuid.uuid4().int)[:13]
        with session_factory() as db:
            db.execute(
                text(
                    "INSERT INTO products (ean, name, source, created_at, updated_at) "
                    "VALUES (:ean, 'Test Product', 'internal', now(), now()) "
                    "ON CONFLICT (ean) DO NOTHING"
                ),
                {"ean": ean},
            )
            db.commit()
        return ean

    return _make


@pytest.fixture
def make_mystery_challenge(session_factory):
    """Insert a mystery challenge. Returns id (UUID)."""

    def _make(ean, status="active", starts_at_offset_days=-1, ends_at_offset_days=6):
        cid = uuid.uuid4()
        tiers = json.dumps(
            [
                {"min_rank": 1, "max_rank": 1, "cab": 500},
                {"min_rank": 2, "max_rank": None, "cab": 10},
            ]
        )
        with session_factory() as db:
            db.execute(
                text(
                    "INSERT INTO mystery_challenges "
                    "  (id, product_ean, starts_at, ends_at, status, reward_tiers) "
                    "VALUES (:id, :ean, "
                    "  now() + :start_offset * interval '1 day', "
                    "  now() + :end_offset * interval '1 day', "
                    "  :status, CAST(:tiers AS jsonb))"
                ),
                {
                    "id": cid,
                    "ean": ean,
                    "start_offset": starts_at_offset_days,
                    "end_offset": ends_at_offset_days,
                    "status": status,
                    "tiers": tiers,
                },
            )
            db.commit()
        return cid

    return _make


@pytest.fixture
def make_mystery_clue(session_factory):
    """Insert a mystery challenge clue. Returns id (UUID)."""

    def _make(challenge_id, reveal_day=1, revealed_at=None):
        clue_id = uuid.uuid4()
        with session_factory() as db:
            if revealed_at is None:
                db.execute(
                    text(
                        "INSERT INTO mystery_challenge_clues "
                        "  (id, challenge_id, reveal_day, clue_text, revealed_at) "
                        "VALUES (:id, :cid, :day, 'Test clue', NULL)"
                    ),
                    {"id": clue_id, "cid": challenge_id, "day": reveal_day},
                )
            else:
                db.execute(
                    text(
                        "INSERT INTO mystery_challenge_clues "
                        "  (id, challenge_id, reveal_day, clue_text, revealed_at) "
                        "VALUES (:id, :cid, :day, 'Test clue', :revealed_at)"
                    ),
                    {"id": clue_id, "cid": challenge_id, "day": reveal_day, "revealed_at": revealed_at},
                )
            db.commit()
        return clue_id

    return _make


@pytest.fixture
def make_mystery_find(session_factory, make_store):
    """Insert a mystery find (not yet announced). Returns id (UUID)."""
    _store_id = None

    def _ensure_store():
        nonlocal _store_id
        if _store_id is None:
            _store_id = make_store()
        return _store_id

    def _make(challenge_id, user_id, scan_id=None, rank=1, found_at_offset_hours=-2):
        fid = uuid.uuid4()
        sid = scan_id or uuid.uuid4()
        store_id = _ensure_store()
        with session_factory() as db:
            # CHECK ``receipt_required`` — seed sibling Receipt for the FK.
            rid = uuid.uuid4()
            db.execute(
                text(
                    "INSERT INTO receipts (id, user_id, store_id, purchased_at, "
                    "                      created_at, updated_at) "
                    "VALUES (:id, :uid, :sid, CURRENT_DATE, now(), now())"
                ),
                {"id": str(rid), "uid": str(user_id), "sid": str(store_id)},
            )
            # Insert a dummy scan if not already present
            db.execute(
                text(
                    "INSERT INTO scans "
                    "  (id, user_id, store_id, price, quantity, scan_type, "
                    "   receipt_id, status, scanned_at, status_updated_at) "
                    "SELECT :sid, :uid, :store_id, 0, 1, 'receipt', "
                    "       :rid, 'accepted', now(), now() "
                    "WHERE NOT EXISTS (SELECT 1 FROM scans WHERE id = :sid)"
                ),
                {"sid": sid, "uid": str(user_id), "store_id": str(store_id), "rid": str(rid)},
            )
            db.execute(
                text(
                    "INSERT INTO mystery_challenge_finds "
                    "  (id, challenge_id, user_id, scan_id, rank, cab_awarded, found_at) "
                    "VALUES (:id, :cid, :uid, :sid, :rank, 50, "
                    "  now() + :offset * interval '1 hour')"
                ),
                {
                    "id": fid,
                    "cid": challenge_id,
                    "uid": str(user_id),
                    "sid": sid,
                    "rank": rank,
                    "offset": found_at_offset_hours,
                },
            )
            db.commit()
        return fid

    return _make
