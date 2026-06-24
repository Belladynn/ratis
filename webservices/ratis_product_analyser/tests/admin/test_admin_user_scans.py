"""Admin user-scans listing endpoint — TDD coverage (ARCH_admin_endpoints PR6).

Endpoint under test :

- ``GET /api/v1/admin/users/{user_id}/scans``  paginated scan list for
  one user, with optional filters (``scan_type``, ``status``,
  ``since``).

Auth pattern : ``ADMIN_API_KEY`` only (read-only — no TOTP). Joins on
``stores.name`` so the operator sees the human label rather than the
raw ``store_id`` UUID.
"""

from __future__ import annotations

import itertools
import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from ratis_core.models.scan import Receipt, Scan
from ratis_core.models.store import Store
from ratis_core.models.user import User
from sqlalchemy import text  # noqa: F401 — kept for convenience in future seeds

# Bumps the default ``scanned_at`` for each helper-created scan so the
# UNIQUE(user_id, store_id, product_ean, scanned_at) constraint never trips
# when a single test seeds multiple scans in tight succession.
_scan_at_counter = itertools.count(0)


def _next_scanned_at() -> datetime:
    """Microsecond-spaced UTC timestamps so seed inserts don't collide."""
    base = datetime(2026, 4, 30, 12, 0, 0, tzinfo=UTC)
    return base + timedelta(microseconds=next(_scan_at_counter))


# ---------------------------------------------------------------------------
# Seed helpers
# ---------------------------------------------------------------------------


def _make_store(db, *, name: str = "Lidl Test", retailer: str = "lidl") -> Store:
    s = Store(
        id=uuid.uuid4(),
        name=name,
        retailer=retailer,
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


def _make_user(db, *, email: str | None = None) -> User:
    uid = uuid.uuid4()
    u = User(
        id=uid,
        email=email or f"u_{uid.hex[:8]}@test.com",
        account_type="oauth",
        is_deleted=False,
    )
    db.add(u)
    db.flush()
    db.commit()
    return u


def _make_scan(
    db,
    *,
    user: User,
    store: Store | None = None,
    status: str = "matched",
    match_method: str | None = "barcode",
    product_ean: str | None = None,
    scan_type: str = "receipt",
    scanned_name: str = "NUTELLA",
    price: int = 250,
    rejected_reason: str | None = None,
    scanned_at: datetime | None = None,
) -> Scan:
    """Insert a scan respecting the v3 invariants (matched ⟹ ean+method ;
    unresolved/rejected ⟹ rejected_reason).

    EAN defaults to NULL — pass ``product_ean=p.ean`` (with a seeded
    Product) when the test asserts on the field. Tests that don't care
    about a real product use status='matched' but pass an EAN that
    matches the conftest ``product`` fixture seed.
    """
    if status in ("unresolved", "rejected") and rejected_reason is None:
        rejected_reason = "test-reason"
    if status not in ("matched",):
        # When the test wants e.g. "unresolved", drop EAN+method so the
        # CHECK ck_scans_matched_requires_ean_method does not fire.
        product_ean = product_ean if status == "accepted" else None
        match_method = match_method if status in ("accepted", "unmatched") else None

    # CHECK ``receipt_required`` : receipt scans need a sibling Receipt ;
    # non-receipt scans MUST have ``receipt_id IS NULL``.
    receipt_id = None
    if scan_type == "receipt":
        r = Receipt(
            id=uuid.uuid4(),
            user_id=user.id,
            store_id=store.id if store is not None else None,
            purchased_at=date.today(),
            store_status="confirmed" if store is not None else "unknown",
        )
        db.add(r)
        db.flush()
        receipt_id = r.id
    # CHECK ``manual_no_scanned_name`` : manual scans MUST have
    # ``scanned_name=NULL`` and ``product_ean NOT NULL``.
    if scan_type == "manual":
        scanned_name = None
        if product_ean is None:
            raise ValueError("manual scan_type requires product_ean (CHECK manual_no_scanned_name)")

    s = Scan(
        id=uuid.uuid4(),
        user_id=user.id,
        store_id=store.id if store is not None else None,
        scanned_name=scanned_name,
        price=price,
        quantity=Decimal("1"),
        scan_type=scan_type,
        receipt_id=receipt_id,
        status=status,
        match_method=match_method,
        product_ean=product_ean,
        rejected_reason=rejected_reason,
        store_status="confirmed" if store is not None else "unknown",
    )
    final_scanned_at = scanned_at if scanned_at is not None else _next_scanned_at()
    s.scanned_at = final_scanned_at
    s.status_updated_at = final_scanned_at
    db.add(s)
    db.flush()
    db.commit()
    return s


# =============================================================================
# Auth gate
# =============================================================================
class TestAuthGate:
    def test_403_without_admin_key(self, raw_client):
        resp = raw_client.get(f"/api/v1/admin/users/{uuid.uuid4()}/scans")
        assert resp.status_code == 403
        assert resp.json()["detail"] == "forbidden"

    def test_403_with_wrong_admin_key(self, raw_client):
        resp = raw_client.get(
            f"/api/v1/admin/users/{uuid.uuid4()}/scans",
            headers={"Authorization": "Bearer wrong-key"},
        )
        assert resp.status_code == 403


# =============================================================================
# GET /admin/users/{user_id}/scans
# =============================================================================
class TestAdminUserScans:
    def test_returns_paginated_scans_for_user(self, admin_client, db, product):
        store = _make_store(db, name="Carrefour Centre")
        user = _make_user(db)
        s1 = _make_scan(db, user=user, store=store, status="matched", product_ean=product.ean)
        s2 = _make_scan(
            db,
            user=user,
            store=store,
            status="unresolved",
            scanned_name="OREO",
            price=180,
        )

        resp = admin_client.get(f"/api/v1/admin/users/{user.id}/scans")
        assert resp.status_code == 200, resp.text
        body = resp.json()

        assert "scans" in body
        assert isinstance(body["scans"], list)
        assert body["total"] >= 2
        assert "limit" in body
        assert "offset" in body

        ids = {s["id"] for s in body["scans"]}
        assert str(s1.id) in ids
        assert str(s2.id) in ids

        # Per-scan shape — store name joined, never just the UUID.
        for s in body["scans"]:
            assert set(s.keys()) >= {
                "id",
                "scan_type",
                "status",
                "scanned_name",
                "product_ean",
                "store_id",
                "store_name",
                "match_method",
                "created_at",
                "image_deleted_at",
            }
        # store_name surfaced (join hit)
        store_names = {s["store_name"] for s in body["scans"]}
        assert "Carrefour Centre" in store_names

    def test_filters_by_other_user(self, admin_client, db, product):
        """Listing endpoint is strict — only returns scans of the asked user."""
        store = _make_store(db)
        user_a = _make_user(db, email="a@test.com")
        user_b = _make_user(db, email="b@test.com")
        sa = _make_scan(db, user=user_a, store=store, product_ean=product.ean)
        sb = _make_scan(db, user=user_b, store=store, product_ean=product.ean)

        resp = admin_client.get(f"/api/v1/admin/users/{user_a.id}/scans")
        assert resp.status_code == 200
        body = resp.json()
        ids = {s["id"] for s in body["scans"]}
        assert str(sa.id) in ids
        # No leak from user_b's scans.
        assert str(sb.id) not in ids
        # Total should reflect the per-user filter — not the table-wide row count.
        assert body["total"] == len(body["scans"])

    def test_filter_scan_type(self, admin_client, db, product):
        store = _make_store(db)
        user = _make_user(db)
        receipt_scan = _make_scan(db, user=user, store=store, scan_type="receipt", product_ean=product.ean)
        _make_scan(
            db,
            user=user,
            store=store,
            scan_type="electronic_label",
            product_ean=product.ean,
        )

        resp = admin_client.get(f"/api/v1/admin/users/{user.id}/scans?scan_type=receipt")
        assert resp.status_code == 200
        body = resp.json()
        ids = {s["id"] for s in body["scans"]}
        assert str(receipt_scan.id) in ids
        for s in body["scans"]:
            assert s["scan_type"] == "receipt"

    def test_filter_status(self, admin_client, db, product):
        store = _make_store(db)
        user = _make_user(db)
        matched = _make_scan(db, user=user, store=store, status="matched", product_ean=product.ean)
        _make_scan(db, user=user, store=store, status="unresolved")

        resp = admin_client.get(f"/api/v1/admin/users/{user.id}/scans?status=matched")
        assert resp.status_code == 200
        body = resp.json()
        ids = {s["id"] for s in body["scans"]}
        assert str(matched.id) in ids
        for s in body["scans"]:
            assert s["status"] == "matched"

    def test_filter_since(self, admin_client, db, product):
        store = _make_store(db)
        user = _make_user(db)
        old_at = datetime(2024, 1, 1, tzinfo=UTC)
        recent_at = datetime.now(UTC) - timedelta(days=2)
        old = _make_scan(db, user=user, store=store, scanned_at=old_at, product_ean=product.ean)
        recent = _make_scan(db, user=user, store=store, scanned_at=recent_at, product_ean=product.ean)

        cutoff = (datetime.now(UTC) - timedelta(days=7)).date().isoformat()
        resp = admin_client.get(f"/api/v1/admin/users/{user.id}/scans?since={cutoff}")
        assert resp.status_code == 200
        ids = {s["id"] for s in resp.json()["scans"]}
        assert str(recent.id) in ids
        assert str(old.id) not in ids

    def test_pagination_limit_offset(self, admin_client, db, product):
        store = _make_store(db)
        user = _make_user(db)
        for _ in range(3):
            _make_scan(db, user=user, store=store, product_ean=product.ean)

        resp = admin_client.get(f"/api/v1/admin/users/{user.id}/scans?limit=2&offset=0")
        assert resp.status_code == 200
        body = resp.json()
        assert body["limit"] == 2
        assert body["offset"] == 0
        assert len(body["scans"]) == 2
        assert body["total"] == 3

        resp2 = admin_client.get(f"/api/v1/admin/users/{user.id}/scans?limit=2&offset=2")
        assert resp2.status_code == 200
        assert len(resp2.json()["scans"]) == 1

    def test_limit_capped_at_200(self, admin_client, db):
        user = _make_user(db)
        resp = admin_client.get(f"/api/v1/admin/users/{user.id}/scans?limit=500")
        assert resp.status_code == 422

    def test_returns_empty_for_unknown_user(self, admin_client):
        """Unknown user_id is NOT a 404 here — an empty list is the
        natural representation of 'no scans for this user'. The detail
        404 lives on the AU side ; here we list a relation."""
        resp = admin_client.get(f"/api/v1/admin/users/{uuid.uuid4()}/scans")
        assert resp.status_code == 200
        body = resp.json()
        assert body["scans"] == []
        assert body["total"] == 0

    def test_handles_scan_without_store(self, admin_client, db):
        """Label scans in geo-unknown context are persisted with store_id=NULL.
        The admin list must surface them with store_name=None (no JOIN drop)."""
        user = _make_user(db)
        # status_updated_at handled by server_default
        s = Scan(
            id=uuid.uuid4(),
            user_id=user.id,
            store_id=None,
            scanned_name="UNKNOWN PRODUCT",
            price=0,
            quantity=Decimal("1"),
            scan_type="electronic_label",
            status="pending",
            store_status="unknown",
        )
        db.add(s)
        db.flush()
        db.commit()

        resp = admin_client.get(f"/api/v1/admin/users/{user.id}/scans")
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 1
        assert body["scans"][0]["store_id"] is None
        assert body["scans"][0]["store_name"] is None
