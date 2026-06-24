"""Tests for GET /api/v1/scan/label-group — detail view of a (store, date) group."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from ratis_core.models.scan import Receipt, Scan
from ratis_core.models.user import User

from tests.conftest import make_token


def _auth(user):
    return {"Authorization": f"Bearer {make_token(user.id)}"}


def _mk_scan(
    db,
    *,
    user,
    store,
    status="accepted",
    product_ean=None,
    scanned_at=None,
    price=199,
    match_method="barcode_ean",
    scanned_name="Yaourt",
    rejected_reason=None,
):
    # CHECK ck_scans_non_matched_requires_reason : v3 statuses 'rejected'
    # and 'unresolved' must carry a reason.
    if status in ("rejected", "unresolved") and rejected_reason is None:
        rejected_reason = "test_rejected"
    s = Scan(
        id=uuid.uuid4(),
        user_id=user.id,
        store_id=store.id,
        scan_type="electronic_label",
        status=status,
        store_status="confirmed",
        product_ean=product_ean,
        scanned_name=scanned_name,
        price=price,
        quantity=Decimal("1"),
        match_method=match_method,
        rejected_reason=rejected_reason,
    )
    if scanned_at is not None:
        s.scanned_at = scanned_at
    db.add(s)
    db.flush()
    db.commit()
    return s


def test_returns_accepted_scans_for_store_and_date(client, db, user, store, product):
    day = datetime(2026, 4, 24, 9, 0, tzinfo=UTC)
    s1 = _mk_scan(
        db,
        user=user,
        store=store,
        status="accepted",
        product_ean=product.ean,
        scanned_at=day,
        match_method="barcode_ean",
    )
    s2 = _mk_scan(
        db,
        user=user,
        store=store,
        status="accepted",
        product_ean=product.ean,
        scanned_at=day + timedelta(minutes=30),
        match_method="fuzzy",
    )
    resp = client.get(
        f"/api/v1/scan/label-group?store_id={store.id}&date=2026-04-24",
        headers=_auth(user),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["items"]) == 2
    # Ordered ASC chronological
    assert body["items"][0]["scan_id"] == str(s1.id)
    assert body["items"][1]["scan_id"] == str(s2.id)
    # Shape
    item = body["items"][0]
    assert item["product_name"] == product.name
    assert item["product_ean"] == product.ean
    assert item["price_cents"] == 199
    assert item["match_method"] == "barcode_ean"
    assert "scanned_at" in item


def test_excludes_unmatched_scans(client, db, user, store):
    day = datetime(2026, 4, 24, 10, 0, tzinfo=UTC)
    _mk_scan(
        db,
        user=user,
        store=store,
        status="unmatched",
        product_ean=None,
        scanned_at=day,
        match_method=None,
    )
    resp = client.get(
        f"/api/v1/scan/label-group?store_id={store.id}&date=2026-04-24",
        headers=_auth(user),
    )
    assert resp.status_code == 404
    assert resp.json()["detail"] == "group_not_found"


def test_excludes_rejected_scans(client, db, user, store, product):
    day = datetime(2026, 4, 24, 10, 0, tzinfo=UTC)
    _mk_scan(
        db,
        user=user,
        store=store,
        status="rejected",
        product_ean=product.ean,
        scanned_at=day,
    )
    resp = client.get(
        f"/api/v1/scan/label-group?store_id={store.id}&date=2026-04-24",
        headers=_auth(user),
    )
    assert resp.status_code == 404


def test_excludes_receipt_scans(client, db, user, store, product):
    """Only electronic_label scans belong to a label-group."""
    day = datetime(2026, 4, 24, 10, 0, tzinfo=UTC)
    # CHECK ``receipt_required`` — seed sibling Receipt for the FK.
    r = Receipt(
        id=uuid.uuid4(),
        user_id=user.id,
        store_id=store.id,
        purchased_at=date.today(),
    )
    db.add(r)
    db.flush()
    s = Scan(
        id=uuid.uuid4(),
        user_id=user.id,
        store_id=store.id,
        receipt_id=r.id,
        scan_type="receipt",  # NOT a label
        status="accepted",
        store_status="confirmed",
        product_ean=product.ean,
        price=199,
        quantity=Decimal("1"),
    )
    s.scanned_at = day
    db.add(s)
    db.flush()
    db.commit()
    resp = client.get(
        f"/api/v1/scan/label-group?store_id={store.id}&date=2026-04-24",
        headers=_auth(user),
    )
    assert resp.status_code == 404


def test_different_date_not_returned(client, db, user, store, product):
    day_23 = datetime(2026, 4, 23, 10, 0, tzinfo=UTC)
    _mk_scan(
        db,
        user=user,
        store=store,
        status="accepted",
        product_ean=product.ean,
        scanned_at=day_23,
    )
    resp = client.get(
        f"/api/v1/scan/label-group?store_id={store.id}&date=2026-04-24",
        headers=_auth(user),
    )
    assert resp.status_code == 404


def test_other_user_scans_excluded(client, db, user, store, product):
    day = datetime(2026, 4, 24, 10, 0, tzinfo=UTC)
    _other_uid = uuid.uuid4()
    other = User(id=_other_uid, email="x@x.fr", account_type="oauth", is_deleted=False)
    db.add(other)
    db.flush()
    db.commit()
    _mk_scan(
        db,
        user=other,
        store=store,
        status="accepted",
        product_ean=product.ean,
        scanned_at=day,
    )
    resp = client.get(
        f"/api/v1/scan/label-group?store_id={store.id}&date=2026-04-24",
        headers=_auth(user),
    )
    assert resp.status_code == 404


def test_no_token_returns_401(client, store):
    resp = client.get(f"/api/v1/scan/label-group?store_id={store.id}&date=2026-04-24")
    assert resp.status_code == 401


def test_invalid_date_returns_422(client, user, store):
    resp = client.get(
        f"/api/v1/scan/label-group?store_id={store.id}&date=bogus",
        headers=_auth(user),
    )
    assert resp.status_code == 422


def test_missing_date_returns_422(client, user, store):
    resp = client.get(
        f"/api/v1/scan/label-group?store_id={store.id}",
        headers=_auth(user),
    )
    assert resp.status_code == 422


def test_invalid_store_id_returns_422(client, user):
    resp = client.get(
        "/api/v1/scan/label-group?store_id=notuuid&date=2026-04-24",
        headers=_auth(user),
    )
    assert resp.status_code == 422


# ============================================================
# consensus_state surfacing (NRC bloc E)
# ============================================================


def test_label_group_items_consensus_state_null_when_no_ledger_row(
    client,
    db,
    user,
    store,
    product,
):
    """Without a ``product_name_resolutions`` row, the item carries
    ``consensus_state=None`` so the frontend renders no badge."""
    day = datetime(2026, 4, 24, 9, 0, tzinfo=UTC)
    _mk_scan(
        db,
        user=user,
        store=store,
        status="accepted",
        product_ean=product.ean,
        scanned_at=day,
        match_method="barcode_ean",
        scanned_name="LAIT 1L",
    )
    resp = client.get(
        f"/api/v1/scan/label-group?store_id={store.id}&date=2026-04-24",
        headers=_auth(user),
    )
    body = resp.json()
    assert len(body["items"]) == 1
    assert "consensus_state" in body["items"][0]
    assert body["items"][0]["consensus_state"] is None


def test_label_group_items_consensus_state_pending_when_one_validator(
    client,
    db,
    user,
    store,
    product,
):
    """A single ledger row for the scan ⇒ ``PENDING`` (quorum miss)."""
    from ratis_core.models.name_resolution import ProductNameResolution

    day = datetime(2026, 4, 24, 9, 0, tzinfo=UTC)
    s = _mk_scan(
        db,
        user=user,
        store=store,
        status="accepted",
        product_ean=product.ean,
        scanned_at=day,
        match_method="barcode_ean",
        scanned_name="LAIT 1L",
    )
    db.add(
        ProductNameResolution(
            id=uuid.uuid4(),
            scan_id=s.id,
            store_id=store.id,
            normalized_label="lait 1l",
            product_ean=product.ean,
            user_id=user.id,
            match_method="barcode",
        )
    )
    db.flush()
    db.commit()

    resp = client.get(
        f"/api/v1/scan/label-group?store_id={store.id}&date=2026-04-24",
        headers=_auth(user),
    )
    body = resp.json()
    assert body["items"][0]["consensus_state"] == "pending"
