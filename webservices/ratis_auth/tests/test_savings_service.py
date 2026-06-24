"""Tests for ratis_core.savings.compute_savings_for_user.

Lives in ratis_auth/tests so the real DB fixtures (SAVEPOINT-based isolation)
can exercise the function. Tests drive the contract required by the
/account/stats endpoint and the nightly batch.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from ratis_core.models.analytics import UserPreferences
from ratis_core.models.price import PriceConsensus
from ratis_core.models.scan import Scan
from ratis_core.models.store import Store
from ratis_core.models.user import User
from ratis_core.savings import compute_savings_for_user
from sqlalchemy import text

# ── helpers ───────────────────────────────────────────────────────────────────

_counter = {"n": 0}


def _mk_user(db, *, ref_lat: float | None, ref_lng: float | None, radius_km: int = 5) -> User:
    _counter["n"] += 1
    uid = uuid.uuid4()
    u = User(
        id=uid,
        email=f"savings_{_counter['n']}_{uid.hex[:8]}@example.com",
        account_type="oauth",
        ref_lat=Decimal(str(ref_lat)) if ref_lat is not None else None,
        ref_lng=Decimal(str(ref_lng)) if ref_lng is not None else None,
    )
    db.add(u)
    db.flush()
    if radius_km is not None:
        db.add(UserPreferences(user_id=u.id, search_radius_km=radius_km, transport_mode="driving"))
        db.flush()
    db.commit()
    return u


def _mk_store(db, *, lat: float, lng: float, name: str = "S") -> Store:
    s = Store(
        id=uuid.uuid4(),
        name=name,
        retailer="retailer",
        address="1 rue",
        city="Paris",
        postal_code="75000",
        lat=Decimal(str(lat)),
        lng=Decimal(str(lng)),
    )
    db.add(s)
    db.flush()
    db.commit()
    return s


def _mk_product(db, ean: str) -> None:
    db.execute(
        text("INSERT INTO products (ean, name, source) VALUES (:ean, :n, 'off') ON CONFLICT (ean) DO NOTHING"),
        {"ean": ean, "n": "P" + ean},
    )
    db.commit()


def _mk_consensus(db, *, store: Store, ean: str, price: int) -> None:
    now = datetime.now(UTC)
    db.add(
        PriceConsensus(
            id=uuid.uuid4(),
            store_id=store.id,
            product_ean=ean,
            price=price,
            trust_score=Decimal("90"),
            first_seen_at=now,
            last_seen_at=now,
        )
    )
    db.flush()
    db.commit()


def _mk_scan(
    db,
    *,
    user: User,
    store: Store,
    ean: str,
    price: int,
    quantity: Decimal = Decimal("1"),
    status: str = "accepted",
    scan_type: str = "receipt",
    scanned_at: datetime | None = None,
) -> Scan:
    _counter["n"] += 1
    # CHECK ck_scans_non_matched_requires_reason.
    rejected_reason = "test_rejected" if status in ("rejected", "unresolved") else None
    # CHECK receipt_required : seed a receipt for receipt-type scans.
    # CHECK manual_no_scanned_name : manual ⇒ scanned_name NULL.
    receipt_id = None
    if scan_type == "receipt":
        receipt_id = uuid.uuid4()
        from sqlalchemy import text as _t

        db.execute(
            _t(
                "INSERT INTO receipts "
                "    (id, user_id, store_id, purchased_at, created_at, updated_at) "
                "VALUES (:id, :uid, :sid, CURRENT_DATE, now(), now())"
            ),
            {"id": receipt_id, "uid": user.id, "sid": store.id},
        )
    scanned_name = None if scan_type == "manual" else "x"
    s = Scan(
        id=uuid.uuid4(),
        user_id=user.id,
        store_id=store.id,
        product_ean=ean,
        scanned_name=scanned_name,
        scan_type=scan_type,
        status=status,
        rejected_reason=rejected_reason,
        receipt_id=receipt_id,
        price=price,
        quantity=quantity,
        image_url=None,
        scanned_at=scanned_at or (datetime.now(UTC) - timedelta(seconds=_counter["n"])),
    )
    db.add(s)
    db.flush()
    db.commit()
    return s


# ── shape ─────────────────────────────────────────────────────────────────────


def test_user_with_no_ref_lat_returns_zero(db):
    u = _mk_user(db, ref_lat=None, ref_lng=None)
    assert compute_savings_for_user(db, u.id) == 0


def test_user_with_no_scans_returns_zero(db):
    u = _mk_user(db, ref_lat=48.85, ref_lng=2.35)
    assert compute_savings_for_user(db, u.id) == 0


# ── primary formula : nearby consensus ───────────────────────────────────────


def test_savings_nearby_consensus(db):
    """Consensus max = 300c, user paid 200c, qty 2 → savings = 200c."""
    u = _mk_user(db, ref_lat=48.85, ref_lng=2.35, radius_km=10)
    store_near = _mk_store(db, lat=48.86, lng=2.36)  # ~1.3 km away
    store_seller = _mk_store(db, lat=48.87, lng=2.36, name="T")
    _mk_product(db, "1000000000001")
    _mk_consensus(db, store=store_near, ean="1000000000001", price=300)
    _mk_scan(db, user=u, store=store_seller, ean="1000000000001", price=200, quantity=Decimal("2"))

    assert compute_savings_for_user(db, u.id) == 200  # (300-200)*2


def test_savings_uses_MAX_across_nearby_stores(db):
    """Multiple nearby stores — the MAX price wins (best-case "avoided" spend)."""
    u = _mk_user(db, ref_lat=48.85, ref_lng=2.35, radius_km=10)
    s1 = _mk_store(db, lat=48.86, lng=2.36, name="A")
    s2 = _mk_store(db, lat=48.86, lng=2.37, name="B")
    _mk_product(db, "1000000000002")
    _mk_consensus(db, store=s1, ean="1000000000002", price=250)
    _mk_consensus(db, store=s2, ean="1000000000002", price=400)
    _mk_scan(db, user=u, store=s1, ean="1000000000002", price=100)

    # MAX(250, 400) = 400, savings = (400-100)*1 = 300
    assert compute_savings_for_user(db, u.id) == 300


# ── fallback : global when no nearby ─────────────────────────────────────────


def test_savings_fallback_global_when_no_nearby_consensus(db):
    """No consensus in radius — fall back to global MAX."""
    u = _mk_user(db, ref_lat=48.85, ref_lng=2.35, radius_km=1)
    # Seller where user scanned — inside radius, but no consensus there.
    seller = _mk_store(db, lat=48.85, lng=2.35, name="seller")
    # Far-away store with a consensus (Marseille-ish, ~660 km from Paris).
    far = _mk_store(db, lat=43.30, lng=5.40, name="far")
    _mk_product(db, "1000000000003")
    _mk_consensus(db, store=far, ean="1000000000003", price=500)
    _mk_scan(db, user=u, store=seller, ean="1000000000003", price=200)

    # Global fallback MAX = 500 → savings = (500-200)*1 = 300
    assert compute_savings_for_user(db, u.id) == 300


def test_savings_no_consensus_anywhere_yields_zero(db):
    u = _mk_user(db, ref_lat=48.85, ref_lng=2.35, radius_km=10)
    store = _mk_store(db, lat=48.86, lng=2.36)
    _mk_product(db, "1000000000004")
    _mk_scan(db, user=u, store=store, ean="1000000000004", price=200)

    assert compute_savings_for_user(db, u.id) == 0


# ── clamp : never negative ────────────────────────────────────────────────────


def test_savings_clamped_to_zero_when_paid_more_than_max(db):
    u = _mk_user(db, ref_lat=48.85, ref_lng=2.35, radius_km=10)
    store = _mk_store(db, lat=48.86, lng=2.36)
    _mk_product(db, "1000000000005")
    _mk_consensus(db, store=store, ean="1000000000005", price=100)
    _mk_scan(db, user=u, store=store, ean="1000000000005", price=500)

    # (100 - 500) = -400, GREATEST(0, -400) = 0
    assert compute_savings_for_user(db, u.id) == 0


# ── scope filter : receipt only, accepted only ───────────────────────────────


def test_savings_only_receipt_type_counted(db):
    u = _mk_user(db, ref_lat=48.85, ref_lng=2.35, radius_km=10)
    store = _mk_store(db, lat=48.86, lng=2.36)
    _mk_product(db, "1000000000006")
    _mk_consensus(db, store=store, ean="1000000000006", price=300)
    _mk_scan(db, user=u, store=store, ean="1000000000006", price=100, scan_type="electronic_label")
    _mk_scan(db, user=u, store=store, ean="1000000000006", price=100, scan_type="manual")

    assert compute_savings_for_user(db, u.id) == 0


def test_savings_only_accepted_status_counted(db):
    u = _mk_user(db, ref_lat=48.85, ref_lng=2.35, radius_km=10)
    store = _mk_store(db, lat=48.86, lng=2.36)
    _mk_product(db, "1000000000007")
    _mk_consensus(db, store=store, ean="1000000000007", price=300)
    _mk_scan(db, user=u, store=store, ean="1000000000007", price=100, status="pending")
    _mk_scan(db, user=u, store=store, ean="1000000000007", price=100, status="rejected")
    _mk_scan(db, user=u, store=store, ean="1000000000007", price=100, status="unmatched")

    assert compute_savings_for_user(db, u.id) == 0


def test_savings_isolated_per_user(db):
    u1 = _mk_user(db, ref_lat=48.85, ref_lng=2.35, radius_km=10)
    u2 = _mk_user(db, ref_lat=48.85, ref_lng=2.35, radius_km=10)
    store = _mk_store(db, lat=48.86, lng=2.36)
    _mk_product(db, "1000000000008")
    _mk_consensus(db, store=store, ean="1000000000008", price=300)
    _mk_scan(db, user=u1, store=store, ean="1000000000008", price=100)

    assert compute_savings_for_user(db, u1.id) == 200
    assert compute_savings_for_user(db, u2.id) == 0


# ── since filter ─────────────────────────────────────────────────────────────


def test_since_filter(db):
    u = _mk_user(db, ref_lat=48.85, ref_lng=2.35, radius_km=10)
    store = _mk_store(db, lat=48.86, lng=2.36)
    _mk_product(db, "1000000000009")
    _mk_consensus(db, store=store, ean="1000000000009", price=300)

    now = datetime.now(UTC)
    _mk_scan(db, user=u, store=store, ean="1000000000009", price=100, scanned_at=now - timedelta(days=10))
    _mk_scan(db, user=u, store=store, ean="1000000000009", price=100, scanned_at=now - timedelta(hours=1))

    # No filter : both → 400
    assert compute_savings_for_user(db, u.id) == 400
    # Since 30m ago : only the recent one → 200
    # Use 2h cutoff to catch "hours=1" scan but exclude "days=10"
    assert compute_savings_for_user(db, u.id, since=now - timedelta(hours=2)) == 200
    # Since future : none → 0
    assert compute_savings_for_user(db, u.id, since=now + timedelta(hours=1)) == 0


@pytest.mark.parametrize(
    "quantity,expected",
    [
        (Decimal("1"), 200),
        (Decimal("2"), 400),
        (Decimal("0.5"), 100),
    ],
)
def test_savings_respects_quantity(db, quantity, expected):
    u = _mk_user(db, ref_lat=48.85, ref_lng=2.35, radius_km=10)
    store = _mk_store(db, lat=48.86, lng=2.36)
    _mk_product(db, "1000000000010")
    _mk_consensus(db, store=store, ean="1000000000010", price=300)
    _mk_scan(db, user=u, store=store, ean="1000000000010", price=100, quantity=quantity)

    assert compute_savings_for_user(db, u.id) == expected
