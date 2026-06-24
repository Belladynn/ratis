"""Tests for GET /api/v1/account/stats."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from _auth_helpers import oauth_signup
from ratis_core.models.scan import Scan
from ratis_core.models.store import Store
from ratis_core.models.user import User
from sqlalchemy import text


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _register(client, email: str, password: str = "password123") -> dict:
    """Mint a user + tokens via OAuth. ``password`` kept for call-site
    compatibility but ignored — Ratis is OAuth-only."""
    return oauth_signup(client, email)


@pytest.fixture
def store(db) -> Store:
    s = Store(
        id=uuid.uuid4(),
        name="Store",
        retailer="lidl",
        address="1 rue",
        city="Paris",
        postal_code="75000",
        lat=48.85,
        lng=2.35,
    )
    db.add(s)
    db.flush()
    db.commit()
    return s


def _current_user(db, client, tokens) -> User:
    """Fetch the User row created by registration."""
    r = client.get("/api/v1/account/profile", headers=_auth(tokens["access_token"]))
    assert r.status_code == 200
    user_email = r.json()["email"]
    return db.query(User).filter(User.email == user_email).one()


_counter = {"n": 0}


def _insert_scan(
    db,
    *,
    user: User,
    store: Store,
    product_ean: str | None,
    status: str = "accepted",
    scan_type: str = "receipt",
):
    _counter["n"] += 1
    # CHECK ck_scans_non_matched_requires_reason : v3 statuses 'rejected'
    # and 'unresolved' must carry a reason.
    rejected_reason = "test_rejected" if status in ("rejected", "unresolved") else None
    # CHECK receipt_required : a receipt-type scan must reference a receipt.
    # CHECK manual_no_scanned_name : manual scans must NOT carry a
    # scanned_name (the user picked the product themselves).
    receipt_id = None
    if scan_type == "receipt":
        receipt_id = uuid.uuid4()
        db.execute(
            text(
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
        product_ean=product_ean,
        scanned_name=scanned_name,
        scan_type=scan_type,
        status=status,
        rejected_reason=rejected_reason,
        receipt_id=receipt_id,
        price=100,
        quantity=Decimal("1"),
        image_url=None,
        scanned_at=datetime.now(UTC) - timedelta(seconds=_counter["n"]),
    )
    db.add(s)
    db.flush()
    db.commit()


def _ensure_product(db, ean: str):
    db.execute(
        text("INSERT INTO products (ean, name, source) VALUES (:ean, :n, 'off') ON CONFLICT (ean) DO NOTHING"),
        {"ean": ean, "n": "Test Product " + ean},
    )
    db.commit()


# ── shape ─────────────────────────────────────────────────────────────────────


def test_stats_empty_user(client):
    tokens = _register(client, "stats_empty@example.com")
    r = client.get("/api/v1/account/stats", headers=_auth(tokens["access_token"]))
    assert r.status_code == 200
    body = r.json()
    assert body["total_scans"] == 0
    assert body["unique_products"] == 0
    assert body["total_savings_cents"] == 0
    assert "member_since" in body
    # member_since is an ISO date (YYYY-MM-DD) or datetime prefix
    assert body["member_since"].startswith("20")


def test_stats_counts_all_scans(client, db, store):
    tokens = _register(client, "stats_scans@example.com")
    user = _current_user(db, client, tokens)
    _ensure_product(db, "1111111111111")
    _ensure_product(db, "2222222222222")
    _insert_scan(db, user=user, store=store, product_ean="1111111111111", status="accepted")
    _insert_scan(db, user=user, store=store, product_ean="1111111111111", status="accepted")
    _insert_scan(db, user=user, store=store, product_ean="2222222222222", status="accepted")
    _insert_scan(db, user=user, store=store, product_ean=None, status="unmatched")
    _insert_scan(db, user=user, store=store, product_ean=None, status="rejected")

    r = client.get("/api/v1/account/stats", headers=_auth(tokens["access_token"]))
    body = r.json()
    assert body["total_scans"] == 5
    # unique products counts DISTINCT product_ean from accepted scans only
    assert body["unique_products"] == 2


def test_stats_isolated_per_user(client, db, store):
    tokens_a = _register(client, "stats_a@example.com")
    tokens_b = _register(client, "stats_b@example.com")
    user_a = _current_user(db, client, tokens_a)
    user_b = _current_user(db, client, tokens_b)
    _ensure_product(db, "3333333333333")
    _insert_scan(db, user=user_a, store=store, product_ean="3333333333333")
    _insert_scan(db, user=user_b, store=store, product_ean="3333333333333")
    _insert_scan(db, user=user_b, store=store, product_ean="3333333333333")

    r = client.get("/api/v1/account/stats", headers=_auth(tokens_a["access_token"]))
    assert r.json()["total_scans"] == 1
    r = client.get("/api/v1/account/stats", headers=_auth(tokens_b["access_token"]))
    assert r.json()["total_scans"] == 2


def test_stats_member_since_matches_user_created_at(client, db):
    tokens = _register(client, "stats_since@example.com")
    user = _current_user(db, client, tokens)
    r = client.get("/api/v1/account/stats", headers=_auth(tokens["access_token"]))
    body = r.json()
    # Must be an ISO string with the same calendar date as user.created_at
    assert body["member_since"].startswith(user.created_at.date().isoformat())


def test_stats_unauthenticated(client):
    r = client.get("/api/v1/account/stats")
    assert r.status_code == 401


# ── savings + rings fields (added 2026-04-21) ────────────────────────────────


def test_stats_exposes_savings_and_rings_fields(client):
    """Shape contract : /account/stats returns the new fields."""
    tokens = _register(client, "stats_shape@example.com")
    r = client.get("/api/v1/account/stats", headers=_auth(tokens["access_token"]))
    body = r.json()
    assert "today_savings_cents" in body
    assert "location_missing" in body
    assert "rings" in body
    rings = body["rings"]
    assert {"rings_consumed", "pending_rings", "subscription_price_cents"} <= set(rings.keys())


def test_stats_location_missing_flag_true_for_new_user(client):
    """A freshly registered user has no ref_lat → location_missing=true, savings=0."""
    tokens = _register(client, "stats_loc_missing@example.com")
    r = client.get("/api/v1/account/stats", headers=_auth(tokens["access_token"]))
    body = r.json()
    assert body["location_missing"] is True
    assert body["total_savings_cents"] == 0
    assert body["today_savings_cents"] == 0
    assert body["rings"]["pending_rings"] == 0
    assert body["rings"]["rings_consumed"] == 0


def test_stats_rings_use_subscription_price_from_settings(client, db):
    """Subscription price MUST come from ratis_settings — returned verbatim."""
    tokens = _register(client, "stats_price@example.com")
    r = client.get("/api/v1/account/stats", headers=_auth(tokens["access_token"]))
    body = r.json()
    # Default from ratis_settings.json — currently 799 cents (7.99€).
    assert body["rings"]["subscription_price_cents"] == 799


def test_stats_pending_rings_from_savings(client, db, store):
    """With ref_lat + consensus + scans, pending_rings = floor(savings / price)."""
    tokens = _register(client, "stats_pending@example.com")
    user = _current_user(db, client, tokens)
    # Give user a location near the store.
    from decimal import Decimal as D

    user.ref_lat = D("48.85")
    user.ref_lng = D("2.35")
    db.flush()
    db.commit()
    # Move the store near the user, ensure it's not disabled.
    store.lat = D("48.86")
    store.lng = D("2.36")
    db.flush()
    db.commit()

    _ensure_product(db, "8000000000001")
    # Consensus = 1000c, user paid 100c → savings = 900c per scan.
    from datetime import datetime

    from ratis_core.models.price import PriceConsensus

    now = datetime.now(UTC)
    db.add(
        PriceConsensus(
            id=uuid.uuid4(),
            store_id=store.id,
            product_ean="8000000000001",
            price=1000,
            trust_score=D("90"),
            first_seen_at=now,
            last_seen_at=now,
        )
    )
    db.commit()
    # One scan → 900c savings. 900 // 799 = 1 eligible ring.
    _insert_scan(db, user=user, store=store, product_ean="8000000000001")

    r = client.get("/api/v1/account/stats", headers=_auth(tokens["access_token"]))
    body = r.json()
    assert body["location_missing"] is False
    assert body["total_savings_cents"] == 900
    assert body["rings"]["pending_rings"] == 1
    assert body["rings"]["rings_consumed"] == 0
