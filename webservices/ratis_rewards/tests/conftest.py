import os
import uuid

# Hermetic test env — DO NOT load_dotenv(.env.local).
# .env.local is a developer file that may contain placeholder secrets
# which silently break tests (e.g. a JWT_SECRET placeholder leaks via
# load_dotenv → tokens minted with the test constant fail validation).
# Tests own their env. See KP-29.
from ratis_core.test_db import resolve_test_database_url

# Worktree-aware : CI gets its explicit TEST_DATABASE_URL untouched ;
# local dev gets a per-worktree DB suffix so concurrent worktrees do not
# clash on the shared ratis_test DROP/CREATE schema teardown.
TEST_DATABASE_URL = resolve_test_database_url()
# Hard-set (not setdefault) — the app code reads these at import time and
# we must shadow any value the developer may have exported in their shell
# or leaked from an earlier .env load.
os.environ["DATABASE_URL"] = TEST_DATABASE_URL
# RS256 JWT keys — rewards verifies tokens (JWT_PUBLIC_KEY_PATH). Tests
# bypass real JWT auth via the bypass_user_auth fixture, but the app
# reads JWT env at import, so an ephemeral public key must be present.
import tempfile as _tempfile
from pathlib import Path as _Path

from ratis_core.testing import generate_test_jwt_keypair as _gen_keypair

_jwt_key_dir = _Path(_tempfile.mkdtemp(prefix="ratis-jwt-keys-"))
_private_pem, _public_pem = _gen_keypair()
(_jwt_key_dir / "jwt_public.pem").write_text(_public_pem)
os.environ["JWT_PUBLIC_KEY_PATH"] = str(_jwt_key_dir / "jwt_public.pem")
os.environ["JWT_AUDIENCE"] = "ratis"
os.environ["INTERNAL_API_KEY"] = "test-internal-key"
os.environ["ADMIN_API_KEY"] = "test-admin-key-padded-to-32-chars-min"
# pragma: allowlist secret — fixed test TOTP secret (base32) for deterministic codes.
os.environ["ADMIN_TOTP_SECRET"] = "JBSWY3DPEHPK3PXP"
# Per-provider webhook secrets (AUDIT 2026-05-17 M-finding) — one secret
# per affiliate network so a leak is contained to a single provider.
os.environ["CASHBACK_WEBHOOK_SECRET_AFFILAE"] = "test-webhook-secret-affilae"
os.environ["CASHBACK_WEBHOOK_SECRET_AWIN"] = "test-webhook-secret-awin"
os.environ["CASHBACK_WEBHOOK_SECRET_CJ"] = "test-webhook-secret-cj"
# Empty by default — overlap-rotation tests will populate via monkeypatch.
os.environ.setdefault("CASHBACK_WEBHOOK_SECRET_AFFILAE_PREV", "")
os.environ.setdefault("CASHBACK_WEBHOOK_SECRET_AWIN_PREV", "")
os.environ.setdefault("CASHBACK_WEBHOOK_SECRET_CJ_PREV", "")
os.environ["GIFT_CARD_PROVIDER_KEY"] = "test-gift-card-key"
# REDIS_URL — OTT session-bootstrap (Module 10 PR 5). The dep is overridden
# per-test via get_redis override, so this value is never actually connected.
os.environ["REDIS_URL"] = "redis://localhost:6379/0"
os.environ["SENTRY_DSN"] = ""  # DSN vide = Sentry silent en tests

import pytest
from fastapi.testclient import TestClient
from limiter import limiter
from main import app
from ratis_core.database import Base, get_db, make_engine
from ratis_core.deps import verify_admin_key, verify_internal_key
from sqlalchemy import event, text
from sqlalchemy.orm import Session, sessionmaker

engine = make_engine(TEST_DATABASE_URL)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


@pytest.fixture(scope="session", autouse=True)
def setup_db():
    with engine.connect() as conn:
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
    Base.metadata.create_all(bind=engine)
    # Sentinel : fail fast if create_all silently no-op'd (model module not
    # imported transitively) instead of letting hundreds of tests fail.
    with engine.connect() as conn:
        try:
            conn.execute(text("SELECT 1 FROM users LIMIT 0"))
            conn.execute(text("SELECT 1 FROM cabecoin_transactions LIMIT 0"))
        except Exception as exc:
            raise RuntimeError(
                "conftest setup failed : Base.metadata.create_all() did not "
                "produce expected tables (users/cabecoin_transactions). "
                "Likely a model module is not imported."
            ) from exc
    # Seed app_settings from JSON so DB-first path is exercised in tests
    _SL = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    _seed_db = _SL()
    try:
        from ratis_core.seed_settings import seed_settings

        seed_settings(_seed_db)
    finally:
        _seed_db.close()
    # Seed achievements catalog (mirror of prod alembic
    # 20260510_1030_ach_seed). Same module, same data — tests cannot drift
    # from the migration.
    _seed_db = _SL()
    try:
        from ratis_core.seed_achievements import seed_achievements

        seed_achievements(_seed_db)
    finally:
        _seed_db.close()
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
    Detect silent rollbacks — route handlers that leave writes uncommitted.

    Only applies to tests that exercise HTTP endpoints (client / user_client /
    raw_client / admin_client fixtures). Direct repository-function tests are
    excluded — they call flush() without commit by design.

    Strategy: monkey-patch db.commit() and db.rollback() to clear _writes on
    explicit calls only. A SQL-event approach would also clear on internal
    SQLAlchemy auto-rollbacks (e.g. session error recovery), masking the bug.
    Here, only explicit Python calls clear the list — silent rollbacks leave
    _writes non-empty and the fixture fails.

    db.flush() from test helpers (make_user, etc.) does NOT clear _writes.
    Route handlers must call db.commit() (success) or db.rollback() (error).
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

    _original_commit = db.commit
    _original_rollback = db.rollback

    def _tracking_commit():
        _writes.clear()
        return _original_commit()

    def _tracking_rollback():
        _writes.clear()
        return _original_rollback()

    db.commit = _tracking_commit
    db.rollback = _tracking_rollback

    yield

    db.commit = _original_commit
    db.rollback = _original_rollback
    event.remove(conn, "before_cursor_execute", _track)

    if _writes:
        lines = "\n".join(f"  {w}" for w in _writes)
        pytest.fail(
            "Uncommitted writes detected after test — missing db.commit() or db.rollback()"
            f" in a route handler?\n{lines}"
        )


@pytest.fixture(autouse=True)
def reset_rate_limiter():
    """Reset in-memory rate-limit counters between tests to prevent
    cross-test pollution (slowapi keeps a process-global storage by
    default — without this any test that hits a rate-limited route
    would poison the next).
    """
    limiter._storage.reset()


@pytest.fixture
def bypass_admin_auth():
    """Bypass admin key auth for tests that don't exercise auth behaviour."""
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
    try:
        with TestClient(app) as c:
            yield c
    finally:
        app.dependency_overrides.pop(get_db, None)


@pytest.fixture
def bypass_internal_auth():
    """Bypass inter-service auth for tests that don't exercise auth behaviour."""
    app.dependency_overrides[verify_internal_key] = lambda: None
    try:
        yield
    finally:
        app.dependency_overrides.pop(verify_internal_key, None)


@pytest.fixture
def bypass_user_auth(db):
    """
    Override the user JWT dependency for a given user_id.
    Usage: uid = bypass_user_auth(some_user_id)
    """
    from deps import get_current_user
    from ratis_core.models.user import User

    _current_uid: list[uuid.UUID] = []

    def _set(user_id: uuid.UUID) -> None:
        _current_uid.clear()
        _current_uid.append(user_id)

    def _override():
        uid = _current_uid[0] if _current_uid else None
        if uid is None:
            raise Exception("bypass_user_auth not configured — call the fixture with a user_id")
        user = db.query(User).filter(User.id == uid).first()
        return user

    app.dependency_overrides[get_current_user] = _override
    try:
        yield _set
    finally:
        app.dependency_overrides.pop(get_current_user, None)


@pytest.fixture
def client(db, bypass_internal_auth):
    """TestClient with DB override and inter-service auth bypassed."""

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
def user_client(db, bypass_user_auth):
    """TestClient with DB override and user JWT auth bypassed (for user-facing endpoints)."""

    def override_get_db():
        try:
            yield db
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db
    try:
        with TestClient(app) as c:
            yield c, bypass_user_auth
    finally:
        app.dependency_overrides.pop(get_db, None)


@pytest.fixture
def raw_client(db):
    """TestClient with DB override but WITHOUT auth bypass — for 403 tests."""

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


# ---------------------------------------------------------------------------
# DB helper factories
# ---------------------------------------------------------------------------


def make_user(db: Session, *, email: str | None = None) -> uuid.UUID:
    """Insert a minimal user row with user_cab_balance and user_cashback_balance rows."""
    from ratis_core.identifiers import generate_support_id

    user_id = uuid.uuid4()
    email = email or f"user_{user_id.hex[:8]}@test.com"
    db.execute(
        text(
            "INSERT INTO users (id, email, support_id, account_type, "
            "                  created_at, updated_at) "
            "VALUES (:id, :email, :sid, 'oauth', now(), now())"
        ),
        {"id": user_id, "email": email, "sid": generate_support_id()},
    )
    db.execute(
        text("INSERT INTO user_cab_balance (user_id, balance, updated_at) VALUES (:uid, 0, now())"),
        {"uid": user_id},
    )
    db.execute(
        text("INSERT INTO user_cashback_balance (user_id, balance, updated_at) VALUES (:uid, 0, now())"),
        {"uid": user_id},
    )
    db.commit()
    return user_id


def make_season(
    db: Session,
    *,
    is_active: bool = True,
    season_number: int = 1,
) -> uuid.UUID:
    """Insert a battlepass season row."""
    season_id = uuid.uuid4()
    db.execute(
        text(
            "INSERT INTO battlepass_seasons "
            "    (id, season_number, name, started_at, ends_at, is_active) "
            "VALUES (:id, :num, :name, now(), now() + interval '90 days', :active)"
        ),
        {
            "id": season_id,
            "num": season_number,
            "name": f"Saison {season_number}",
            "active": is_active,
        },
    )
    db.commit()
    return season_id


def make_milestone(
    db: Session,
    *,
    season_id: uuid.UUID,
    milestone_number: int = 1,
    cab_required: int = 200,
    reward_type: str = "cab",
    reward_value: int = 100,
    subscriber_only: bool = False,
) -> uuid.UUID:
    """Insert a battlepass milestone row."""
    milestone_id = uuid.uuid4()
    db.execute(
        text(
            "INSERT INTO battlepass_milestones "
            "    (id, season_id, milestone_number, cab_required, reward_type, reward_value, subscriber_only) "
            "VALUES (:id, :sid, :num, :cab, :rtype, :rval, :sub)"
        ),
        {
            "id": milestone_id,
            "sid": season_id,
            "num": milestone_number,
            "cab": cab_required,
            "rtype": reward_type,
            "rval": reward_value,
            "sub": subscriber_only,
        },
    )
    db.commit()
    return milestone_id


def make_subscription(
    db: Session,
    user_id: uuid.UUID,
) -> uuid.UUID:
    """Insert an active subscription row."""
    sub_id = uuid.uuid4()
    db.execute(
        text(
            "INSERT INTO subscriptions "
            "    (id, user_id, status, plan, price, paid_with, payment_ref, started_at, expires_at) "
            "VALUES (:id, :uid, 'active', 'monthly', 11.99, 'stripe', 'test_ref_sub', "
            "        now(), now() + interval '30 days')"
        ),
        {"id": sub_id, "uid": user_id},
    )
    db.commit()
    return sub_id


def make_mission(
    db: Session,
    *,
    action_type: str = "receipt_scan",
    frequency: str = "daily",
    difficulty: str = "easy",
    target_count: int = 1,
    cab_reward: int = 50,
    is_active: bool = True,
    qualifier: str | None = None,
) -> uuid.UUID:
    """Insert a mission catalogue row.

    ``qualifier`` is optional — defaults to NULL for V0-shaped missions.
    Phase B catalogue rows carry a non-NULL qualifier
    (``attribute:organic``, ``category``, ``store`` …).
    """
    mission_id = uuid.uuid4()
    db.execute(
        text(
            "INSERT INTO missions "
            "    (id, action_type, qualifier, frequency, difficulty, "
            "     target_count, cab_reward, is_active) "
            "VALUES (:id, :action, :qualifier, :freq, :diff, "
            "        :target, :reward, :active)"
        ),
        {
            "id": mission_id,
            "action": action_type,
            "qualifier": qualifier,
            "freq": frequency,
            "diff": difficulty,
            "target": target_count,
            "reward": cab_reward,
            "active": is_active,
        },
    )
    db.commit()
    return mission_id


def make_store(db: Session) -> uuid.UUID:
    """Insert a minimal store row."""
    store_id = uuid.uuid4()
    db.execute(
        text(
            "INSERT INTO stores (id, name, lat, lng, is_disabled, created_at, updated_at) "
            "VALUES (:id, 'Test Store', 48.8566, 2.3522, false, now(), now())"
        ),
        {"id": store_id},
    )
    db.flush()
    return store_id


def make_scan(
    db: Session,
    *,
    user_id: uuid.UUID | None = None,
    product_ean: str | None = None,
    scan_type: str = "receipt",
    status: str = "accepted",
) -> uuid.UUID:
    """Insert a minimal scan row. Creates a store automatically.

    When ``scan_type='receipt'`` the row also needs a sibling receipt row to
    satisfy the PG ``receipt_required`` CHECK. The receipt is auto-created
    here so callers don't have to thread one in for the common case.
    """
    scan_id = uuid.uuid4()
    store_id = make_store(db)
    receipt_id: uuid.UUID | None = None
    if scan_type == "receipt":
        receipt_id = uuid.uuid4()
        db.execute(
            text(
                "INSERT INTO receipts "
                "    (id, user_id, store_id, purchased_at, "
                "     created_at, updated_at) "
                "VALUES (:id, :uid, :store_id, CURRENT_DATE, "
                "        now(), now())"
            ),
            {"id": receipt_id, "uid": user_id, "store_id": store_id},
        )
    db.execute(
        text(
            "INSERT INTO scans "
            "    (id, user_id, store_id, product_ean, price, quantity, scan_type, status, "
            "     receipt_id, scanned_at, status_updated_at) "
            "VALUES (:id, :uid, :store_id, :ean, 0, 1, :scan_type, :status, "
            "        :receipt_id, now(), now())"
        ),
        {
            "id": scan_id,
            "uid": user_id,
            "store_id": store_id,
            "ean": product_ean,
            "scan_type": scan_type,
            "status": status,
            "receipt_id": receipt_id,
        },
    )
    db.flush()
    return scan_id


def make_brand(
    db: Session,
    *,
    name: str = "Test Brand",
    slug: str | None = None,
) -> uuid.UUID:
    """Insert a brand row."""
    brand_id = uuid.uuid4()
    slug = slug or f"{name.lower().replace(' ', '-')}-{brand_id.hex[:6]}"
    db.execute(
        text("INSERT INTO brands (id, name, slug) VALUES (:id, :name, :slug)"),
        {"id": brand_id, "name": name, "slug": slug},
    )
    db.flush()
    return brand_id


def make_product(
    db: Session,
    *,
    ean: str | None = None,
    name: str = "Test Product",
    brand_id: uuid.UUID | None = None,
) -> str:
    """Insert a product row. Returns the EAN.

    Defaults to ``source='off'`` (the dominant case in production) so the
    ``internal_*`` CHECK constraints — which require an internal-SKU EAN
    prefix of ``2`` and a non-NULL unit — do not fire on generic fixtures.
    """
    ean = ean or str(uuid.uuid4().int)[:13]
    db.execute(
        text(
            "INSERT INTO products (ean, name, source, brand_id, created_at, updated_at) "
            "VALUES (:ean, :name, 'off', :brand_id, now(), now())"
        ),
        {"ean": ean, "name": name, "brand_id": brand_id},
    )
    db.flush()
    return ean


def make_affiliate_offer(
    db: Session,
    *,
    product_ean: str,
    brand_id: uuid.UUID,
    cashback_rate: float = 0.10,
    # Default provider matches one of the values allowed by the PG
    # ``provider_check`` constraint (`affilae|awin|cj`). Pre-Pattern A
    # this fixture used ``'direct'`` which would have failed in prod.
    provider: str = "affilae",
    external_id: str | None = None,
) -> uuid.UUID:
    """Insert an active affiliate offer row. Returns the offer ID."""
    offer_id = uuid.uuid4()
    external_id = external_id or f"ext_{uuid.uuid4().hex[:8]}"
    db.execute(
        text(
            "INSERT INTO affiliate_offers "
            "    (id, provider, external_id, product_ean, brand_id, cashback_rate, valid_from) "
            "VALUES (:id, :provider, :ext_id, :ean, :bid, :rate, now() - interval '1 hour')"
        ),
        {
            "id": offer_id,
            "provider": provider,
            "ext_id": external_id,
            "ean": product_ean,
            "bid": brand_id,
            "rate": cashback_rate,
        },
    )
    db.flush()
    return offer_id


def make_cashback_credit(
    db: Session,
    *,
    user_id: uuid.UUID,
    amount: int,
    status: str = "confirmed",
    boost_applied: bool = False,
    days_ago: int = 0,
    product_ean: str | None = None,
    affiliate_offer_id: uuid.UUID | None = None,
) -> uuid.UUID:
    """Insert a CREDIT ``cashback_transactions`` row that satisfies the PG
    ``credit_requires_offer`` / ``credit_requires_product`` CHECKs.

    Auto-seeds a brand + product + active affiliate offer when the caller
    doesn't supply them — keeps the call site to a single line for tests
    that only care about (amount, status, days_ago). Callers that need
    cross-row coupling (e.g. boost parent/child sharing the same offer)
    can pass ``product_ean=`` + ``affiliate_offer_id=`` explicitly.

    Returns the new transaction ID.
    """
    if product_ean is None or affiliate_offer_id is None:
        brand_id = make_brand(db)
        if product_ean is None:
            product_ean = make_product(db, brand_id=brand_id)
        if affiliate_offer_id is None:
            affiliate_offer_id = make_affiliate_offer(db, product_ean=product_ean, brand_id=brand_id)

    tx_id = uuid.uuid4()
    created_expr = f"now() - (interval '{int(days_ago)} days')" if days_ago > 0 else "now()"
    db.execute(
        text(
            "INSERT INTO cashback_transactions "
            "    (id, user_id, type, amount, status, product_ean, affiliate_offer_id, "
            "     boost_applied, created_at) "
            f"VALUES (:id, :uid, 'CREDIT', :amount, :status, :ean, :oid, :boosted, {created_expr})"
        ),
        {
            "id": tx_id,
            "uid": user_id,
            "amount": amount,
            "status": status,
            "ean": product_ean,
            "oid": affiliate_offer_id,
            "boosted": boost_applied,
        },
    )
    db.flush()
    return tx_id


def make_gift_card_brand(
    db: Session,
    *,
    name: str | None = None,
    provider_brand_id: str | None = None,
    is_active: bool = True,
) -> uuid.UUID:
    """Insert a gift_card_brands row. Returns brand ID.

    ``name`` defaults to a UUID-suffixed unique value so multiple calls in
    the same test never clash on the boutique-V1 UNIQUE(name) constraint.
    Pass an explicit ``name`` when the test asserts on it.
    """
    brand_id = uuid.uuid4()
    name = name or f"Brand {brand_id.hex[:8]}"
    provider_brand_id = provider_brand_id or f"runa_{brand_id.hex[:8]}"
    db.execute(
        text(
            "INSERT INTO gift_card_brands (id, name, provider_brand_id, is_active, created_at) "
            "VALUES (:id, :name, :pbid, :active, now())"
        ),
        {"id": brand_id, "name": name, "pbid": provider_brand_id, "active": is_active},
    )
    db.commit()
    return brand_id


def make_gift_card_order(
    db: Session,
    *,
    user_id: uuid.UUID,
    brand_id: uuid.UUID,
    denomination: int = 2000,
    status: str = "pending",
    source_type: str = "annual_subscription",
    source_ref_id: str | None = None,
    code: str | None = None,
) -> uuid.UUID:
    """Insert a gift_card_orders row. Returns order ID."""
    order_id = uuid.uuid4()
    source_ref_id = source_ref_id or uuid.uuid4().hex
    db.execute(
        text(
            "INSERT INTO gift_card_orders "
            "    (id, user_id, brand_id, denomination, status, source_type, source_ref_id, "
            "     code, created_at) "
            "VALUES (:id, :uid, :bid, :denom, :status, :stype, :sref, :code, now())"
        ),
        {
            "id": order_id,
            "uid": user_id,
            "bid": brand_id,
            "denom": denomination,
            "status": status,
            "stype": source_type,
            "sref": source_ref_id,
            "code": code,
        },
    )
    db.commit()
    return order_id


def make_price_consensus(db: Session, *, ean: str, store_id=None) -> None:
    """Insert a price_consensus row with last_seen_at = now() — required for mystery product eligibility."""
    if store_id is None:
        store_id = make_store(db)
    pc_id = uuid.uuid4()
    db.execute(
        text(
            "INSERT INTO price_consensus "
            "  (id, product_ean, store_id, price, trust_score, first_seen_at, last_seen_at, computed_at) "
            "VALUES (:id, :ean, :store_id, 199, 80, now(), now(), now()) "
            "ON CONFLICT (store_id, product_ean) DO UPDATE SET last_seen_at = now()"
        ),
        {"id": pc_id, "ean": ean, "store_id": store_id},
    )
    db.flush()


# ---------------------------------------------------------------------------
# Achievement V1 fixtures (PR2)
# ---------------------------------------------------------------------------


def make_achievement(
    db: Session,
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
    """Insert a single achievement row, returns the ORM instance.

    ``code`` defaults to a unique slug to avoid clashes with the seeded
    catalog (23 entries autoloaded by ``setup_db``).
    """
    from ratis_core.models.achievement import Achievement

    code = code or f"_t_{uuid.uuid4().hex[:10]}"
    ach = Achievement(
        code=code,
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
    db.add(ach)
    # Flush BEFORE commit so the INSERT statement fires (and registers in
    # the assert_no_pending_changes tracker, if enabled) before the
    # patched commit() clears the write-tracker. Without this, calling
    # ``achievement_factory`` from inside an HTTP-fixture test would
    # leave the INSERT in the tracker AFTER commit cleared it, falsely
    # tripping the "uncommitted writes" guard at teardown.
    db.flush()
    db.commit()
    db.refresh(ach)
    return ach


@pytest.fixture
def test_user(db):
    """A vanilla user (not banned, not deleted) with empty CAB balance."""
    from ratis_core.models.user import User

    uid = make_user(db)
    return db.get(User, uid)


@pytest.fixture
def shadow_banned_user(db):
    from ratis_core.models.user import User

    uid = make_user(db)
    db.execute(
        text("UPDATE users SET is_shadow_banned = true WHERE id = :uid"),
        {"uid": uid},
    )
    db.commit()
    return db.get(User, uid)


@pytest.fixture
def deleted_user(db):
    from ratis_core.models.user import User

    uid = make_user(db)
    db.execute(
        text("UPDATE users SET is_deleted = true WHERE id = :uid"),
        {"uid": uid},
    )
    db.commit()
    return db.get(User, uid)


@pytest.fixture
def achievement_factory(db):
    """Factory that returns an Achievement row with overridable defaults."""

    def _make(**kwargs):
        return make_achievement(db, **kwargs)

    return _make


@pytest.fixture
def scan_factory(db):
    """Factory inserting a Scan row with overridable defaults.

    Default ``status='accepted'`` for symmetry with ``accepted_scan_factory``,
    but callers passing ``status='pending'``/``status='unmatched'`` exercise
    the negative case in ``_eval_scan_count`` (only 'accepted'/'matched' count).

    Returns the scan id (UUID).
    """

    def _make(
        *,
        user_id=None,
        product_ean=None,
        scan_type="receipt",
        status="accepted",
        store_id=None,
        rejected_reason=None,
    ):
        scan_id = uuid.uuid4()
        sid = store_id or make_store(db)
        # CHECK ck_scans_non_matched_requires_reason — provide a default
        # reason when caller asks for unresolved/rejected without one.
        if status in ("unresolved", "rejected") and rejected_reason is None:
            rejected_reason = "test_default"
        # CHECK receipt_required — a receipt-type scan must reference a
        # receipt row. Auto-seed one when the caller doesn't pass it.
        receipt_id: uuid.UUID | None = None
        if scan_type == "receipt":
            receipt_id = uuid.uuid4()
            db.execute(
                text(
                    "INSERT INTO receipts "
                    "    (id, user_id, store_id, purchased_at, created_at, updated_at) "
                    "VALUES (:id, :uid, :store_id, CURRENT_DATE, now(), now())"
                ),
                {"id": receipt_id, "uid": user_id, "store_id": sid},
            )
        db.execute(
            text(
                "INSERT INTO scans "
                "    (id, user_id, store_id, product_ean, price, quantity, scan_type, status, "
                "     rejected_reason, receipt_id, scanned_at, status_updated_at) "
                "VALUES (:id, :uid, :store_id, :ean, 0, 1, :scan_type, :status, "
                "        :reason, :receipt_id, now(), now())"
            ),
            {
                "id": scan_id,
                "uid": user_id,
                "store_id": sid,
                "ean": product_ean,
                "scan_type": scan_type,
                "status": status,
                "reason": rejected_reason,
                "receipt_id": receipt_id,
            },
        )
        db.commit()
        return scan_id

    return _make


@pytest.fixture
def accepted_scan_factory(scan_factory):
    """Factory inserting a Scan with status='accepted'.

    Thin wrapper around ``scan_factory`` for readability in tests that only
    care about counting eligible (accepted) scans.
    """

    def _make(**kwargs):
        kwargs.setdefault("status", "accepted")
        return scan_factory(**kwargs)

    return _make


def make_mystery_challenge(db: Session, *, product_ean: str, status: str = "active") -> uuid.UUID:
    """Insert a mystery challenge directly (bypasses service logic). Returns challenge id."""
    import json

    challenge_id = uuid.uuid4()
    reward_tiers = json.dumps(
        [
            {"min_rank": 1, "max_rank": 1, "cab": 500},
            {"min_rank": 2, "max_rank": None, "cab": 10},
        ]
    )
    db.execute(
        text(
            "INSERT INTO mystery_challenges (id, product_ean, starts_at, ends_at, status, reward_tiers) "
            "VALUES (:id, :ean, now() - interval '1 day', now() + interval '6 days', :status, "
            "  CAST(:tiers AS jsonb))"
        ),
        {"id": challenge_id, "ean": product_ean, "status": status, "tiers": reward_tiers},
    )
    db.commit()
    return challenge_id
