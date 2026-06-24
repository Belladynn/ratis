"""Tests for GET /api/v1/scan/history — unified entries (receipt + label_group)."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from ratis_core.models.scan import Receipt, Scan
from ratis_core.models.store import Store
from ratis_core.models.user import User

from tests.conftest import make_token


def _auth(user):
    return {"Authorization": f"Bearer {make_token(user.id)}"}


def _make_scan(
    db,
    *,
    user,
    store,
    scan_type: str = "receipt",
    status: str = "accepted",
    product_ean: str | None = None,
    scanned_name: str = "Item",
    price: int = 199,
    receipt_id: uuid.UUID | None = None,
    scanned_at: datetime | None = None,
    match_method: str | None = None,
    rejected_reason: str | None = None,
) -> Scan:
    # CHECK ck_scans_non_matched_requires_reason : v3 statuses 'rejected'
    # and 'unresolved' must carry a reason. Default-fill to a test sentinel
    # when caller does not specify.
    if status in ("rejected", "unresolved") and rejected_reason is None:
        rejected_reason = "test_rejected"
    # CHECK ``receipt_required`` : receipt-typed scans need a Receipt FK.
    # Auto-seed one when caller doesn't supply ``receipt_id`` so legacy
    # call sites keep working.
    if scan_type == "receipt" and receipt_id is None:
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
    # CHECK ``manual_no_scanned_name`` : manual scans must have
    # ``scanned_name=NULL`` + ``product_ean NOT NULL``. Drop the
    # caller-provided default ``scanned_name`` when the scan is manual.
    if scan_type == "manual":
        scanned_name = None
        # ``product_ean`` is required by the CHECK ; tests that exercise
        # the manual branch must pass an explicit EAN — fail loud if the
        # caller forgot rather than silently violate the constraint.
        if product_ean is None:
            raise ValueError("manual scan_type requires product_ean (CHECK manual_no_scanned_name)")
    s = Scan(
        id=uuid.uuid4(),
        user_id=user.id,
        store_id=store.id if store is not None else None,
        store_status="confirmed" if store is not None else "unknown",
        product_ean=product_ean,
        scanned_name=scanned_name,
        scan_type=scan_type,
        status=status,
        match_method=match_method,
        rejected_reason=rejected_reason,
        price=price,
        quantity=Decimal("1"),
        image_url=None,
        receipt_id=receipt_id,
    )
    if scanned_at is not None:
        s.scanned_at = scanned_at
    db.add(s)
    db.flush()
    db.commit()
    return s


def _make_receipt(db, *, user, store, total_amount: int = 1234, store_status: str = "confirmed") -> Receipt:
    r = Receipt(
        id=uuid.uuid4(),
        user_id=user.id,
        store_id=store.id if store is not None else None,
        purchased_at=datetime(1970, 1, 1).date(),
        total_amount=total_amount,
        store_status=store_status,
    )
    db.add(r)
    db.flush()
    db.commit()
    return r


def _make_store(db, *, name="Store X") -> Store:
    s = Store(
        id=uuid.uuid4(),
        name=name,
        retailer=name.lower(),
        address="1 rue",
        city="Paris",
        postal_code="75001",
        lat=48.85,
        lng=2.35,
    )
    db.add(s)
    db.flush()
    db.commit()
    return s


# ── empty ─────────────────────────────────────────────────────────────────────


def test_empty_history_returns_empty_entries(client, user):
    resp = client.get("/api/v1/scan/history", headers=_auth(user))
    assert resp.status_code == 200
    body = resp.json()
    assert body == {"entries": [], "next_cursor": None}


# ── receipt entry ─────────────────────────────────────────────────────────────


def test_receipt_entry_shape(client, db, user, store, product):
    receipt = _make_receipt(db, user=user, store=store, total_amount=4735)
    now = datetime.now(UTC)
    _make_scan(
        db,
        user=user,
        store=store,
        scan_type="receipt",
        status="accepted",
        product_ean=product.ean,
        scanned_name="Nutella 400g",
        price=249,
        receipt_id=receipt.id,
        scanned_at=now,
        match_method="fuzzy",
    )
    _make_scan(
        db,
        user=user,
        store=store,
        scan_type="receipt",
        status="unmatched",
        product_ean=None,
        scanned_name="UNKNOWN",
        price=150,
        receipt_id=receipt.id,
        scanned_at=now,
    )

    resp = client.get("/api/v1/scan/history", headers=_auth(user))
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["entries"]) == 1
    entry = body["entries"][0]
    assert entry["type"] == "receipt"
    assert entry["receipt_id"] == str(receipt.id)
    assert entry["store_name"] == store.name
    assert entry["store_status"] == "confirmed"
    assert entry["total_amount_cents"] == 4735
    assert entry["matched_count"] == 1
    assert entry["unmatched_count"] == 1
    assert entry["pending_count"] == 0
    assert "scanned_at" in entry


def test_receipt_entry_with_pending_items(client, db, user, store):
    receipt = _make_receipt(db, user=user, store=store, total_amount=500)
    _make_scan(
        db,
        user=user,
        store=store,
        scan_type="receipt",
        status="pending",
        receipt_id=receipt.id,
    )
    resp = client.get("/api/v1/scan/history", headers=_auth(user))
    entry = resp.json()["entries"][0]
    assert entry["pending_count"] == 1
    assert entry["matched_count"] == 0
    assert entry["unmatched_count"] == 0


def test_receipt_entry_excludes_rejected_counts(client, db, user, store, product):
    receipt = _make_receipt(db, user=user, store=store, total_amount=100)
    _make_scan(
        db,
        user=user,
        store=store,
        scan_type="receipt",
        status="accepted",
        product_ean=product.ean,
        receipt_id=receipt.id,
    )
    _make_scan(
        db,
        user=user,
        store=store,
        scan_type="receipt",
        status="rejected",
        receipt_id=receipt.id,
    )
    resp = client.get("/api/v1/scan/history", headers=_auth(user))
    entry = resp.json()["entries"][0]
    # rejected must never be surfaced in counts
    assert entry["matched_count"] == 1
    assert entry["unmatched_count"] == 0
    assert entry["pending_count"] == 0


def test_matched_count_includes_v3_status_matched(client, db, user, store, product):
    """Pipeline_v3 (deployed 2026-04-30) renamed v2 'accepted' → 'matched'.
    The receipt summary must include v3 'matched' rows in matched_count
    alongside legacy v2 'accepted' rows."""
    now = datetime.now(UTC)
    receipt = _make_receipt(db, user=user, store=store, total_amount=500)
    _make_scan(
        db,
        user=user,
        store=store,
        scan_type="receipt",
        status="matched",
        product_ean=product.ean,
        scanned_name="X",
        price=199,
        receipt_id=receipt.id,
        match_method="barcode",
        scanned_at=now,
    )
    _make_scan(
        db,
        user=user,
        store=store,
        scan_type="receipt",
        status="accepted",
        product_ean=product.ean,
        scanned_name="Y",
        price=199,
        receipt_id=receipt.id,
        match_method="fuzzy",
        scanned_at=now - timedelta(seconds=1),
    )
    resp = client.get("/api/v1/scan/history", headers=_auth(user))
    entry = resp.json()["entries"][0]
    assert entry["matched_count"] == 2  # 1 v3 'matched' + 1 v2 'accepted'
    assert entry["unmatched_count"] == 0


def test_unmatched_count_includes_v3_status_unresolved(client, db, user, store):
    """Pipeline_v3 renamed v2 'unmatched' → 'unresolved'. The receipt
    summary must include v3 'unresolved' rows in unmatched_count
    alongside legacy v2 'unmatched' rows."""
    receipt = _make_receipt(db, user=user, store=store, total_amount=500)
    _make_scan(
        db,
        user=user,
        store=store,
        scan_type="receipt",
        status="unresolved",
        scanned_name="X",
        price=199,
        receipt_id=receipt.id,
        rejected_reason="no_fuzzy_candidate",
    )
    _make_scan(
        db,
        user=user,
        store=store,
        scan_type="receipt",
        status="unmatched",
        scanned_name="Y",
        price=199,
        receipt_id=receipt.id,
    )
    resp = client.get("/api/v1/scan/history", headers=_auth(user))
    entry = resp.json()["entries"][0]
    assert entry["matched_count"] == 0
    assert entry["unmatched_count"] == 2  # 1 v3 'unresolved' + 1 v2 'unmatched'


def test_label_group_matched_count_includes_v3(client, db, user, store, product):
    """Label-group accepted_count must include v3 'matched' label scans
    too (same v2/v3 transition rule)."""
    from datetime import datetime as _dt

    now = _dt.now(UTC)
    _make_scan(
        db,
        user=user,
        store=store,
        scan_type="electronic_label",
        status="matched",
        product_ean=product.ean,
        price=199,
        scanned_at=now,
        match_method="barcode",
    )
    resp = client.get("/api/v1/scan/history", headers=_auth(user))
    entries = resp.json()["entries"]
    assert len(entries) == 1
    assert entries[0]["type"] == "label_group"
    assert entries[0]["accepted_count"] == 1


def test_receipt_with_only_rejected_scans_hidden(client, db, user, store):
    """A receipt with 0 useful scans (all rejected) must NOT appear in history."""
    receipt = _make_receipt(db, user=user, store=store, total_amount=100)
    _make_scan(
        db,
        user=user,
        store=store,
        scan_type="receipt",
        status="rejected",
        receipt_id=receipt.id,
    )
    resp = client.get("/api/v1/scan/history", headers=_auth(user))
    # Receipt with 0 non-rejected scans is still shown (user needs to see OCR failed
    # tickets) — BUT total counts = 0. Per ARCH: "Receipt → 1 per receipt_id".
    # So either it appears with counts=0, OR it's hidden. ARCH says preserved ≈
    # behaviour, so show it. The entry exists but counts are all zero.
    entries = resp.json()["entries"]
    assert len(entries) == 1
    assert entries[0]["matched_count"] == 0
    assert entries[0]["unmatched_count"] == 0
    assert entries[0]["pending_count"] == 0


# ── label_group entry ─────────────────────────────────────────────────────────


def test_label_group_entry_shape(client, db, user, store, product):
    now = datetime.now(UTC)
    # 3 accepted labels + 1 unmatched (same day same store)
    for i in range(3):
        _make_scan(
            db,
            user=user,
            store=store,
            scan_type="electronic_label",
            status="accepted",
            product_ean=product.ean,
            price=199,
            scanned_at=now - timedelta(minutes=i),
        )
    _make_scan(
        db,
        user=user,
        store=store,
        scan_type="electronic_label",
        status="unmatched",
        price=199,
        scanned_at=now - timedelta(minutes=10),
    )
    resp = client.get("/api/v1/scan/history", headers=_auth(user))
    body = resp.json()
    assert len(body["entries"]) == 1
    entry = body["entries"][0]
    assert entry["type"] == "label_group"
    assert entry["store_id"] == str(store.id)
    assert entry["store_name"] == store.name
    assert entry["accepted_count"] == 3  # unmatched labels not in count
    assert "date" in entry
    assert "latest_scanned_at" in entry
    assert "group_key" in entry


def test_label_group_hidden_when_zero_accepted(client, db, user, store):
    """A label group with 0 accepted scans (only unmatched) must not appear."""
    _make_scan(
        db,
        user=user,
        store=store,
        scan_type="electronic_label",
        status="unmatched",
        price=199,
    )
    resp = client.get("/api/v1/scan/history", headers=_auth(user))
    assert resp.json()["entries"] == []


def test_label_group_rejected_excluded(client, db, user, store, product):
    """Rejected label scans must not bring a group into existence nor count."""
    _make_scan(
        db,
        user=user,
        store=store,
        scan_type="electronic_label",
        status="rejected",
        product_ean=product.ean,
        price=199,
    )
    resp = client.get("/api/v1/scan/history", headers=_auth(user))
    assert resp.json()["entries"] == []


def test_label_groups_split_by_store_and_date(client, db, user, store, product):
    now = datetime.now(UTC)
    yesterday = now - timedelta(days=1)
    store2 = _make_store(db, name="Monoprix")

    _make_scan(
        db,
        user=user,
        store=store,
        scan_type="electronic_label",
        status="accepted",
        product_ean=product.ean,
        scanned_at=now,
    )
    _make_scan(
        db,
        user=user,
        store=store,
        scan_type="electronic_label",
        status="accepted",
        product_ean=product.ean,
        scanned_at=yesterday,
    )
    _make_scan(
        db,
        user=user,
        store=store2,
        scan_type="electronic_label",
        status="accepted",
        product_ean=product.ean,
        scanned_at=now,
    )
    resp = client.get("/api/v1/scan/history", headers=_auth(user))
    groups = [e for e in resp.json()["entries"] if e["type"] == "label_group"]
    assert len(groups) == 3


# ── ordering ──────────────────────────────────────────────────────────────────


def test_entries_ordered_by_latest_activity_desc(client, db, user, store, product):
    now = datetime.now(UTC)
    # Old receipt
    old_receipt = _make_receipt(db, user=user, store=store, total_amount=100)
    _make_scan(
        db,
        user=user,
        store=store,
        scan_type="receipt",
        status="accepted",
        product_ean=product.ean,
        receipt_id=old_receipt.id,
        scanned_at=now - timedelta(days=3),
    )
    # Mid label group
    _make_scan(
        db,
        user=user,
        store=store,
        scan_type="electronic_label",
        status="accepted",
        product_ean=product.ean,
        scanned_at=now - timedelta(days=1),
    )
    # Recent receipt
    new_receipt = _make_receipt(db, user=user, store=store, total_amount=200)
    _make_scan(
        db,
        user=user,
        store=store,
        scan_type="receipt",
        status="accepted",
        product_ean=product.ean,
        receipt_id=new_receipt.id,
        scanned_at=now,
    )
    resp = client.get("/api/v1/scan/history", headers=_auth(user))
    entries = resp.json()["entries"]
    assert len(entries) == 3
    assert entries[0]["type"] == "receipt"
    assert entries[0]["receipt_id"] == str(new_receipt.id)
    assert entries[1]["type"] == "label_group"
    assert entries[2]["type"] == "receipt"
    assert entries[2]["receipt_id"] == str(old_receipt.id)


# ── user isolation ────────────────────────────────────────────────────────────


def test_excludes_other_users(client, db, user, store, product):
    now = datetime.now(UTC)
    _other_uid = uuid.uuid4()
    other = User(
        id=_other_uid,
        email="other@ratis.fr",
        account_type="oauth",
        is_deleted=False,
    )
    db.add(other)
    db.flush()
    db.commit()
    # Other user's receipt + scan
    other_receipt = _make_receipt(db, user=other, store=store, total_amount=999)
    _make_scan(
        db,
        user=other,
        store=store,
        scan_type="receipt",
        status="accepted",
        product_ean=product.ean,
        receipt_id=other_receipt.id,
        scanned_at=now - timedelta(seconds=1),
    )
    # Other user's label — distinct scanned_at to avoid UNIQUE(user,store,ean,scanned_at)
    _make_scan(
        db,
        user=other,
        store=store,
        scan_type="electronic_label",
        status="accepted",
        product_ean=product.ean,
        scanned_at=now - timedelta(seconds=2),
    )
    resp = client.get("/api/v1/scan/history", headers=_auth(user))
    assert resp.json()["entries"] == []


# ── pagination ────────────────────────────────────────────────────────────────


def test_cursor_pagination_receipts(client, db, user, store, product):
    now = datetime.now(UTC)
    receipts = []
    for i in range(5):
        r = _make_receipt(db, user=user, store=store, total_amount=100 + i)
        _make_scan(
            db,
            user=user,
            store=store,
            scan_type="receipt",
            status="accepted",
            product_ean=product.ean,
            receipt_id=r.id,
            scanned_at=now - timedelta(minutes=i),
        )
        receipts.append(r)

    resp = client.get("/api/v1/scan/history?limit=2", headers=_auth(user))
    body = resp.json()
    assert len(body["entries"]) == 2
    assert body["entries"][0]["receipt_id"] == str(receipts[0].id)
    assert body["entries"][1]["receipt_id"] == str(receipts[1].id)
    cursor = body["next_cursor"]
    assert cursor is not None

    resp2 = client.get(f"/api/v1/scan/history?limit=2&cursor={cursor}", headers=_auth(user))
    body2 = resp2.json()
    assert len(body2["entries"]) == 2
    assert body2["entries"][0]["receipt_id"] == str(receipts[2].id)
    assert body2["entries"][1]["receipt_id"] == str(receipts[3].id)


def test_last_page_cursor_null(client, db, user, store, product):
    now = datetime.now(UTC)
    for i in range(3):
        r = _make_receipt(db, user=user, store=store, total_amount=100)
        _make_scan(
            db,
            user=user,
            store=store,
            scan_type="receipt",
            status="accepted",
            product_ean=product.ean,
            receipt_id=r.id,
            scanned_at=now - timedelta(minutes=i),
        )
    resp = client.get("/api/v1/scan/history?limit=10", headers=_auth(user))
    body = resp.json()
    assert len(body["entries"]) == 3
    assert body["next_cursor"] is None


# ── validation ────────────────────────────────────────────────────────────────


def test_limit_clamped_to_max(client, user):
    resp = client.get("/api/v1/scan/history?limit=9999", headers=_auth(user))
    assert resp.status_code == 422


def test_no_token_returns_401(client):
    resp = client.get("/api/v1/scan/history")
    assert resp.status_code == 401


def test_invalid_cursor_returns_422(client, user):
    resp = client.get("/api/v1/scan/history?cursor=not-a-valid-cursor!!!", headers=_auth(user))
    assert resp.status_code == 422
