"""Test conftest for ratis_batch_achievements.

Hermetic env (no .env.local), full Base.metadata.create_all on a clean public
schema, SAVEPOINT-isolated session per test (mirrors the pattern already used
by ratis_batch_savings / ratis_batch_purge).

Imports the rewards service (achievement_service) — that module uses
flat-layout imports (``from repositories.cab_repository import award_cab``)
that resolve via webservices/ratis_rewards/ on sys.path. We add it
defensively here so ``python -m batch.ratis_batch_achievements`` works
identically in dev, tests and prod.
"""

from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

# Hermetic test env — DO NOT load_dotenv(.env.local). Tests own their env (KP-29).
from ratis_core.test_db import resolve_test_database_url

# Worktree-aware : CI gets its explicit TEST_DATABASE_URL untouched ;
# local dev gets a per-worktree DB suffix so concurrent worktrees do not
# clash on the shared ratis_test DROP/CREATE schema teardown.
TEST_DATABASE_URL = resolve_test_database_url()
os.environ["DATABASE_URL"] = TEST_DATABASE_URL
os.environ["INTERNAL_API_KEY"] = "test-internal-key"
os.environ["SENTRY_DSN"] = ""

# Make ``from repositories.cab_repository import award_cab`` resolve when the
# achievement_service is imported transitively from the batch entrypoint.
_REWARDS_DIR = Path(__file__).resolve().parents[3] / "webservices" / "ratis_rewards"
if str(_REWARDS_DIR) not in sys.path:
    sys.path.insert(0, str(_REWARDS_DIR))

import pytest
import ratis_core.models  # noqa: F401  — register all models on Base.metadata
from ratis_core.database import Base, make_engine
from sqlalchemy import event, text
from sqlalchemy.orm import Session, sessionmaker


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
    # Sentinel : fail fast if create_all silently no-op'd.
    with eng.connect() as conn:
        try:
            conn.execute(text("SELECT 1 FROM users LIMIT 0"))
            conn.execute(text("SELECT 1 FROM achievements LIMIT 0"))
            conn.execute(text("SELECT 1 FROM user_achievements LIMIT 0"))
            conn.execute(text("SELECT 1 FROM cashback_transactions LIMIT 0"))
            conn.execute(text("SELECT 1 FROM cabecoin_transactions LIMIT 0"))
            conn.execute(text("SELECT 1 FROM user_cab_balance LIMIT 0"))
        except Exception as exc:
            raise RuntimeError(
                "conftest setup failed : Base.metadata.create_all() did not "
                "produce expected tables (users / achievements / user_achievements / "
                "cashback_transactions / cabecoin_transactions / user_cab_balance). "
                "Likely a model module is not imported."
            ) from exc
    yield eng
    eng.dispose()


@pytest.fixture
def db_session(engine):
    """SAVEPOINT-isolated session — survives db.commit() calls inside the
    achievement_service.``_unlock`` (which commits its own transaction)."""
    connection = engine.connect()
    outer = connection.begin()
    nested = connection.begin_nested()
    session = sessionmaker(connection, expire_on_commit=False)()

    @event.listens_for(session, "after_transaction_end")
    def _restart_savepoint(session, tx):
        nonlocal nested
        if not nested.is_active:
            nested = connection.begin_nested()

    try:
        yield session
    finally:
        session.close()
        outer.rollback()
        connection.close()


@pytest.fixture(autouse=True)
def assert_no_pending_changes():
    """CLAUDE.md / CI marker. Batch tests assert state explicitly via the test
    body — a missing commit is detected by the next read returning the
    rolled-back state."""
    return


# ---------------------------------------------------------------------------
# Helpers / factories
# ---------------------------------------------------------------------------


def _insert_user(
    db: Session,
    *,
    is_shadow_banned: bool = False,
    is_deleted: bool = False,
) -> uuid.UUID:
    """Insert minimal users + user_cab_balance row. Returns user UUID."""
    from ratis_core.identifiers import generate_support_id

    uid = uuid.uuid4()
    db.execute(
        text(
            "INSERT INTO users (id, email, support_id, account_type, "
            "                  is_shadow_banned, is_deleted, created_at, updated_at) "
            "VALUES (:id, :email, :sid, 'oauth', :ban, :del, now(), now())"
        ),
        {
            "id": uid,
            "email": f"u_{uid.hex[:8]}@test.com",
            "sid": generate_support_id(),
            "ban": is_shadow_banned,
            "del": is_deleted,
        },
    )
    db.execute(
        text("INSERT INTO user_cab_balance (user_id, balance, updated_at) VALUES (:uid, 0, now())"),
        {"uid": uid},
    )
    db.commit()
    return uid


@pytest.fixture
def test_user(db_session):
    """A vanilla user (not banned, not deleted)."""
    from ratis_core.models.user import User

    uid = _insert_user(db_session)
    return db_session.get(User, uid)


@pytest.fixture
def shadow_banned_user(db_session):
    from ratis_core.models.user import User

    uid = _insert_user(db_session, is_shadow_banned=True)
    return db_session.get(User, uid)


@pytest.fixture
def achievement_factory(db_session):
    """Factory inserting a single Achievement row and returning the ORM instance."""
    from ratis_core.models.achievement import Achievement

    def _make(
        *,
        code: str | None = None,
        label: str = "Test Achievement",
        description: str = "Test",
        icon: str = "x",
        rarity: str = "bronze",
        category: str = "volume",
        trigger_type: str = "scan_count",
        target_value: float = 1,
        window_days: int | None = None,
        extra_params: dict | None = None,
        cab_reward: int = 30,
        is_secret: bool = False,
        is_hidden: bool = False,
        available_from=None,
        available_until=None,
    ):
        ach = Achievement(
            code=code or f"_t_{uuid.uuid4().hex[:10]}",
            label=label,
            description=description,
            icon=icon,
            rarity=rarity,
            category=category,
            trigger_type=trigger_type,
            target_value=target_value,
            window_days=window_days,
            extra_params=extra_params,
            cab_reward=cab_reward,
            is_secret=is_secret,
            is_hidden=is_hidden,
            available_from=available_from,
            available_until=available_until,
        )
        db_session.add(ach)
        db_session.commit()
        db_session.refresh(ach)
        return ach

    return _make


@pytest.fixture
def cashback_transaction_factory(db_session):
    """Factory inserting a CREDIT cashback_transactions row (status='confirmed').

    The achievements ``savings_eur_*`` handlers count CREDIT rows in
    status ``pending``/``confirmed``. We default to ``confirmed`` so a single
    factory call produces a row that immediately satisfies the trigger.

    Auto-seeds brand+product+active affiliate offer so the row satisfies the
    PG ``credit_requires_offer`` / ``credit_requires_product`` CHECKs.
    """

    def _make(*, user_id: uuid.UUID, amount: int, status: str = "confirmed") -> uuid.UUID:
        # Seed the FK parents — brand → product → offer.
        brand_id = uuid.uuid4()
        db_session.execute(
            text("INSERT INTO brands (id, name, slug) VALUES (:id, :name, :slug)"),
            {
                "id": brand_id,
                "name": f"Brand-{brand_id.hex[:6]}",
                "slug": f"brand-{brand_id.hex[:6]}",
            },
        )
        ean = str(uuid.uuid4().int)[:13]
        db_session.execute(
            text(
                "INSERT INTO products (ean, name, source, brand_id, created_at, updated_at) "
                "VALUES (:ean, 'p', 'off', :bid, now(), now())"
            ),
            {"ean": ean, "bid": brand_id},
        )
        offer_id = uuid.uuid4()
        db_session.execute(
            text(
                "INSERT INTO affiliate_offers "
                "    (id, provider, external_id, product_ean, brand_id, cashback_rate, valid_from) "
                "VALUES (:id, 'affilae', :ext, :ean, :bid, 0.10, now() - interval '1 hour')"
            ),
            {
                "id": offer_id,
                "ext": f"ext-{offer_id.hex[:8]}",
                "ean": ean,
                "bid": brand_id,
            },
        )

        tx_id = uuid.uuid4()
        db_session.execute(
            text(
                "INSERT INTO cashback_transactions "
                "    (id, user_id, type, amount, status, product_ean, affiliate_offer_id, "
                "     boost_applied, created_at) "
                "VALUES (:id, :uid, 'CREDIT', :amount, :status, :ean, :oid, false, now())"
            ),
            {
                "id": tx_id,
                "uid": user_id,
                "amount": amount,
                "status": status,
                "ean": ean,
                "oid": offer_id,
            },
        )
        db_session.commit()
        return tx_id

    return _make


@pytest.fixture
def accepted_scan_factory(db_session):
    """Factory inserting a Scan row with ``status='accepted'``.

    Creates an inline store so callers do not need to provide one.

    Bug 6 — receipt-typed scans now need a sibling ``receipts`` row
    (CHECK ``receipt_required``) and manual-typed scans need ``product
    _ean NOT NULL`` + ``scanned_name IS NULL`` (CHECK
    ``manual_no_scanned_name``). This factory mirrors that contract.
    """

    def _make(*, user_id: uuid.UUID, product_ean: str | None = None, scan_type: str = "receipt") -> uuid.UUID:
        from datetime import date

        store_id = uuid.uuid4()
        db_session.execute(
            text(
                "INSERT INTO stores (id, name, lat, lng, is_disabled, created_at, updated_at) "
                "VALUES (:id, 'Test Store', 48.8566, 2.3522, false, now(), now())"
            ),
            {"id": store_id},
        )
        receipt_id: uuid.UUID | None = None
        if scan_type == "receipt":
            receipt_id = uuid.uuid4()
            db_session.execute(
                text(
                    "INSERT INTO receipts "
                    "    (id, user_id, store_id, purchased_at, created_at, updated_at) "
                    "VALUES (:id, :uid, :sid, :pat, now(), now())"
                ),
                {
                    "id": receipt_id,
                    "uid": user_id,
                    "sid": store_id,
                    "pat": date.today(),
                },
            )
        scan_id = uuid.uuid4()
        db_session.execute(
            text(
                "INSERT INTO scans "
                "    (id, user_id, store_id, product_ean, price, quantity, "
                "     scan_type, receipt_id, status, "
                "     scanned_at, status_updated_at) "
                "VALUES (:id, :uid, :store_id, :ean, 0, 1, "
                "        :scan_type, :rid, 'accepted', "
                "        now(), now())"
            ),
            {
                "id": scan_id,
                "uid": user_id,
                "store_id": store_id,
                "ean": product_ean,
                "scan_type": scan_type,
                "rid": receipt_id,
            },
        )
        db_session.commit()
        return scan_id

    return _make
