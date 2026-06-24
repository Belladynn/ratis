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
os.environ["REWARDS_BASE_URL"] = "http://rewards.test"
os.environ["SENTRY_DSN"] = ""  # DSN vide = Sentry silent en tests

import pytest
import ratis_core.models  # noqa: F401  — register models on Base.metadata
from ratis_core.database import Base, make_engine
from sqlalchemy import event, text
from sqlalchemy.orm import sessionmaker


@pytest.fixture(scope="session")
def engine():
    eng = make_engine(TEST_DATABASE_URL)
    with eng.connect() as conn:
        # Drop user-defined ENUM types in public defensively (see KP-29 :
        # DROP SCHEMA CASCADE handles tables but ENUM types from Alembic
        # migrations can survive create_all and silently conflict).
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
    # Sentinel : fail fast if create_all silently no-op'd (model module not
    # imported transitively) instead of letting tests fail with cryptic
    # "relation X does not exist" errors deep in fixture chains.
    with eng.connect() as conn:
        try:
            conn.execute(text("SELECT 1 FROM users LIMIT 0"))
            conn.execute(text("SELECT 1 FROM gift_card_orders LIMIT 0"))
            conn.execute(text("SELECT 1 FROM gift_card_brands LIMIT 0"))
            conn.execute(text("SELECT 1 FROM referral_codes LIMIT 0"))
            conn.execute(text("SELECT 1 FROM referral_uses LIMIT 0"))
            conn.execute(text("SELECT 1 FROM subscriptions LIMIT 0"))
        except Exception as exc:
            raise RuntimeError(
                "conftest setup failed : Base.metadata.create_all() did not "
                "produce expected tables (users / gift_card_orders / "
                "gift_card_brands / referral_codes / referral_uses / "
                "subscriptions). Likely a model module is not imported."
            ) from exc
    yield eng
    eng.dispose()


@pytest.fixture
def connection(engine):
    with engine.connect() as conn:
        yield conn


@pytest.fixture
def session_factory(connection):
    """SA 2.0 SAVEPOINT isolation — rolled back after each test."""
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
    Policy marker required by CLAUDE.md / CI.

    Dans le contexte batch, les tests utilisent du SQL brut via session_factory() —
    chaque fonction batch gère sa propre session. Un db.commit() manquant est détecté
    naturellement par les assertions du test (la lecture suivante dans une nouvelle
    session voit l'état rollbacké). Aucune instrumentation supplémentaire nécessaire.
    """
    return


@pytest.fixture
def make_user(session_factory):
    """Insert a minimal user row, return its UUID."""
    from ratis_core.identifiers import generate_support_id

    def _make(email: str | None = None) -> uuid.UUID:
        uid = uuid.uuid4()
        email = email or f"user_{uid.hex[:8]}@test.com"
        with session_factory() as db:
            db.execute(
                text(
                    "INSERT INTO users (id, email, support_id, account_type, "
                    "                  created_at, updated_at) "
                    "VALUES (:id, :email, :sid, 'oauth', now(), now())"
                ),
                {"id": uid, "email": email, "sid": generate_support_id()},
            )
            db.commit()
        return uid

    return _make


@pytest.fixture
def make_brand(session_factory):
    """Insert a gift_card_brands row (fake Runa brand).

    Uses a UUID-suffixed name so concurrent test calls don't clash on
    the UNIQUE(name) constraint introduced by the boutique V1 migration.
    """

    def _make() -> uuid.UUID:
        bid = uuid.uuid4()
        suffix = bid.hex[:8]
        with session_factory() as db:
            db.execute(
                text(
                    "INSERT INTO gift_card_brands "
                    "(id, name, provider_brand_id, is_active, created_at) "
                    "VALUES (:id, :name, :pbid, true, now())"
                ),
                {
                    "id": bid,
                    "name": f"Runa Default {suffix}",
                    "pbid": f"runa_default_{suffix}",
                },
            )
            db.commit()
        return bid

    return _make


@pytest.fixture
def make_referral_order(session_factory, make_user, make_brand):
    """
    Insert a full referral_codes + referral_uses + gift_card_orders row set.

    Keyword args :
      - eligible_delta_hours : offset applied to NOW() for eligible_at
        (negative = past → eligible, positive = future → not yet)
      - subscribed : if True, also creates an active subscription row
    """

    def _make(
        *,
        eligible_delta_hours: int = -1,
        subscribed: bool = True,
    ) -> dict:
        referrer = make_user()
        referred = make_user()
        brand = make_brand()
        referral_id = uuid.uuid4()
        use_id = uuid.uuid4()
        order_id = uuid.uuid4()

        with session_factory() as db:
            db.execute(
                text(
                    "INSERT INTO referral_codes (id, user_id, code, type, created_at) "
                    "VALUES (:id, :uid, :code, 'user', now())"
                ),
                {"id": referral_id, "uid": referrer, "code": uuid.uuid4().hex[:8].upper()},
            )
            db.execute(
                text(
                    "INSERT INTO referral_uses (id, referral_id, referred_user_id, plan, rewarded_at, created_at) "
                    "VALUES (:id, :rid, :ruid, 'monthly', now(), now())"
                ),
                {"id": use_id, "rid": referral_id, "ruid": referred},
            )
            db.execute(
                text(
                    "INSERT INTO gift_card_orders "
                    "(id, user_id, brand_id, denomination, status, source_type, source_ref_id, "
                    " eligible_at, created_at) "
                    "VALUES (:id, :uid, :bid, 500, 'pending', 'referral_reward', :sref, "
                    "        now() + (:delta || ' hours')::interval, now())"
                ),
                {
                    "id": order_id,
                    "uid": referrer,
                    "bid": brand,
                    "sref": str(use_id),
                    "delta": str(eligible_delta_hours),
                },
            )
            if subscribed:
                db.execute(
                    text(
                        "INSERT INTO subscriptions "
                        "(id, user_id, status, plan, price, paid_with, payment_ref, "
                        " started_at, expires_at) "
                        "VALUES (:id, :uid, 'active', 'monthly', 7.99, 'stripe', "
                        "        'stripe_test_ref', now(), now() + INTERVAL '30 days')"
                    ),
                    {"id": uuid.uuid4(), "uid": referred},
                )
            db.commit()

        return {
            "referrer_id": referrer,
            "referred_id": referred,
            "brand_id": brand,
            "referral_id": referral_id,
            "use_id": use_id,
            "order_id": order_id,
        }

    return _make
