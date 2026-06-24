"""Tests for POST /api/v1/account/rings/claim."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from _auth_helpers import oauth_signup
from ratis_core.models.price import PriceConsensus
from ratis_core.models.scan import Scan
from ratis_core.models.store import Store
from ratis_core.models.user import User
from sqlalchemy import text


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _register(client, email: str) -> dict:
    """Mint a user + tokens via OAuth — Ratis is OAuth-only."""
    return oauth_signup(client, email)


def _current_user(db, client, tokens) -> User:
    r = client.get("/api/v1/account/profile", headers=_auth(tokens["access_token"]))
    user_email = r.json()["email"]
    return db.query(User).filter(User.email == user_email).one()


@pytest.fixture
def store(db) -> Store:
    s = Store(
        id=uuid.uuid4(),
        name="Store",
        retailer="retailer",
        address="1 rue",
        city="Paris",
        postal_code="75000",
        lat=Decimal("48.86"),
        lng=Decimal("2.36"),
    )
    db.add(s)
    db.flush()
    db.commit()
    return s


_counter = {"n": 0}


def _setup_user_with_savings(db, user: User, store: Store, *, scans: int, consensus_price: int, paid_price: int):
    """Give user a location near store, create consensus, and `scans` accepted scans."""
    user.ref_lat = Decimal("48.85")
    user.ref_lng = Decimal("2.35")
    db.flush()
    db.commit()
    ean = f"9{uuid.uuid4().int % 10**12:012d}"
    db.execute(
        text("INSERT INTO products (ean, name, source) VALUES (:e, :n, 'off') ON CONFLICT DO NOTHING"),
        {"e": ean, "n": "P"},
    )
    now = datetime.now(UTC)
    db.add(
        PriceConsensus(
            id=uuid.uuid4(),
            store_id=store.id,
            product_ean=ean,
            price=consensus_price,
            trust_score=Decimal("90"),
            first_seen_at=now,
            last_seen_at=now,
        )
    )
    db.flush()
    for _ in range(scans):
        _counter["n"] += 1
        # CHECK receipt_required : seed a receipt for each receipt scan.
        receipt_id = uuid.uuid4()
        db.execute(
            text(
                "INSERT INTO receipts "
                "    (id, user_id, store_id, purchased_at, created_at, updated_at) "
                "VALUES (:id, :uid, :sid, CURRENT_DATE, now(), now())"
            ),
            {"id": receipt_id, "uid": user.id, "sid": store.id},
        )
        db.add(
            Scan(
                id=uuid.uuid4(),
                user_id=user.id,
                store_id=store.id,
                product_ean=ean,
                scanned_name="x",
                scan_type="receipt",
                status="accepted",
                receipt_id=receipt_id,
                price=paid_price,
                quantity=Decimal("1"),
                scanned_at=now - timedelta(seconds=_counter["n"]),
            )
        )
    db.flush()
    db.commit()


# ── shape ─────────────────────────────────────────────────────────────────────


def test_claim_unauthenticated(client):
    r = client.post("/api/v1/account/rings/claim")
    assert r.status_code == 401


def test_claim_nothing_to_claim_for_empty_user(client):
    tokens = _register(client, "ring_empty@example.com")
    r = client.post("/api/v1/account/rings/claim", headers=_auth(tokens["access_token"]))
    assert r.status_code == 200
    body = r.json()
    assert body["animation"] == "nothing_to_claim"
    assert body["rings_consumed"] == 0
    assert body["pending_rings"] == 0
    assert body["subscription_price_cents"] == 799


def test_claim_success_happy_path(client, db, store):
    tokens = _register(client, "ring_happy@example.com")
    user = _current_user(db, client, tokens)
    # 1 scan with 1000c consensus and 100c paid → 900c savings; 900 // 799 = 1 eligible.
    _setup_user_with_savings(db, user, store, scans=1, consensus_price=1000, paid_price=100)

    # First must materialize snapshot → so stats call first is fine, but claim should
    # work without a prior stats call.
    r = client.post("/api/v1/account/rings/claim", headers=_auth(tokens["access_token"]))
    assert r.status_code == 200
    body = r.json()
    assert body["animation"] == "claimed"
    assert body["rings_consumed"] == 1
    assert body["pending_rings"] == 0


def test_claim_multiple_pending(client, db, store):
    """3 eligible rings → 3 successful claims, then nothing_to_claim."""
    tokens = _register(client, "ring_multi@example.com")
    user = _current_user(db, client, tokens)
    # 3 scans × 900c savings = 2700c → 2700 // 799 = 3 eligible rings.
    _setup_user_with_savings(db, user, store, scans=3, consensus_price=1000, paid_price=100)

    for expected in (1, 2, 3):
        r = client.post("/api/v1/account/rings/claim", headers=_auth(tokens["access_token"]))
        body = r.json()
        assert body["animation"] == "claimed"
        assert body["rings_consumed"] == expected

    r = client.post("/api/v1/account/rings/claim", headers=_auth(tokens["access_token"]))
    body = r.json()
    assert body["animation"] == "nothing_to_claim"
    assert body["rings_consumed"] == 3
    assert body["pending_rings"] == 0


def test_claim_cannot_exceed_eligible(client, db, store):
    """Claim is guarded atomically — cannot consume beyond `eligible`."""
    tokens = _register(client, "ring_limit@example.com")
    user = _current_user(db, client, tokens)
    # 1 scan → 900c savings → 1 eligible ring only.
    _setup_user_with_savings(db, user, store, scans=1, consensus_price=1000, paid_price=100)

    r1 = client.post("/api/v1/account/rings/claim", headers=_auth(tokens["access_token"]))
    assert r1.json()["animation"] == "claimed"
    r2 = client.post("/api/v1/account/rings/claim", headers=_auth(tokens["access_token"]))
    assert r2.json()["animation"] == "nothing_to_claim"


def test_claim_pending_rings_decrements_in_stats(client, db, store):
    """After a claim, /account/stats returns pending_rings - 1."""
    tokens = _register(client, "ring_stats@example.com")
    user = _current_user(db, client, tokens)
    _setup_user_with_savings(db, user, store, scans=3, consensus_price=1000, paid_price=100)

    before = client.get("/api/v1/account/stats", headers=_auth(tokens["access_token"])).json()
    assert before["rings"]["pending_rings"] == 3
    assert before["rings"]["rings_consumed"] == 0

    client.post("/api/v1/account/rings/claim", headers=_auth(tokens["access_token"]))

    after = client.get("/api/v1/account/stats", headers=_auth(tokens["access_token"])).json()
    assert after["rings"]["rings_consumed"] == 1
    assert after["rings"]["pending_rings"] == 2
