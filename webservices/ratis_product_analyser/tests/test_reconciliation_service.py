"""Tests for services.reconciliation_service."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from ratis_core.models.scan import Receipt, Scan
from ratis_core.models.store import Store
from services.reconciliation_service import (
    NOTIFICATION_TYPE,
    reconcile_unknown_scans_for_receipt,
)
from sqlalchemy import text


@pytest.fixture
def target_store(db):
    s = Store(
        id=uuid.uuid4(),
        name="Monoprix République",
        retailer="monoprix",
        address="21 place de la République, Paris",
        city="Paris",
        postal_code="75003",
        lat=Decimal("48.8676"),
        lng=Decimal("2.3631"),
    )
    db.add(s)
    db.flush()
    db.commit()
    return s


def _make_receipt(db, user, store) -> Receipt:
    r = Receipt(
        id=uuid.uuid4(),
        user_id=user.id,
        store_id=store.id,
        store_status="confirmed",
        purchased_at=datetime.now(UTC).date(),
    )
    db.add(r)
    db.flush()
    return r


def _make_unknown_scan(db, user, *, lat: Decimal, lng: Decimal, days_ago: int = 1) -> Scan:
    scanned_at = datetime.now(UTC) - timedelta(days=days_ago)
    scan = Scan(
        id=uuid.uuid4(),
        user_id=user.id,
        store_id=None,
        store_status="unknown",
        scan_type="electronic_label",
        scanned_name="NUTELLA 400G",
        price=299,
        quantity=1.0,
        status="pending",
        user_lat=lat,
        user_lng=lng,
        scanned_at=scanned_at,
    )
    db.add(scan)
    db.flush()
    return scan


def test_reconciles_scans_within_radius_and_window(db, user, target_store):
    # Three scans near the store, all within 7 days
    close_scans = [
        _make_unknown_scan(db, user, lat=Decimal("48.86770"), lng=Decimal("2.36320"), days_ago=1),
        _make_unknown_scan(db, user, lat=Decimal("48.86750"), lng=Decimal("2.36300"), days_ago=3),
        _make_unknown_scan(db, user, lat=Decimal("48.86740"), lng=Decimal("2.36310"), days_ago=6),
    ]
    # Two scans that must NOT be reconciled: one >100m, one >7d
    far_scan = _make_unknown_scan(db, user, lat=Decimal("48.85000"), lng=Decimal("2.35000"), days_ago=1)
    old_scan = _make_unknown_scan(db, user, lat=Decimal("48.86770"), lng=Decimal("2.36320"), days_ago=10)
    db.commit()

    receipt = _make_receipt(db, user, target_store)

    calls: list[tuple] = []
    # Phase C-1 — reward_trigger now receives a keyword-only
    # ``labels_tags`` kwarg. The stub accepts it via **kwargs without
    # asserting on it (the dedicated organic-enrichment e2e suite
    # covers the labels_tags branch).
    result = reconcile_unknown_scans_for_receipt(
        db,
        receipt,
        reward_trigger=lambda uid, sid, stype, **kwargs: calls.append((uid, sid, stype)),
    )
    db.commit()

    assert result is not None
    assert result.reconciled_count == 3
    assert result.store_name == target_store.name
    assert set(result.scan_ids) == {s.id for s in close_scans}

    # rewards were triggered for each reconciled scan
    assert len(calls) == 3
    triggered_ids = {c[1] for c in calls}
    assert triggered_ids == {s.id for s in close_scans}

    # reconciled scans now point at the store; PII cleared
    for s in close_scans:
        db.refresh(s)
        assert s.store_id == target_store.id
        assert s.store_status == "confirmed"
        assert s.user_lat is None
        assert s.user_lng is None

    # far / old scans are untouched
    db.refresh(far_scan)
    db.refresh(old_scan)
    assert far_scan.store_id is None
    assert far_scan.store_status == "unknown"
    assert far_scan.user_lat is not None
    assert old_scan.store_id is None
    assert old_scan.store_status == "unknown"


def test_enqueues_store_validated_notification(db, user, target_store):
    _make_unknown_scan(db, user, lat=Decimal("48.86770"), lng=Decimal("2.36320"), days_ago=1)
    db.commit()

    receipt = _make_receipt(db, user, target_store)
    reconcile_unknown_scans_for_receipt(db, receipt, reward_trigger=lambda *a, **k: None)
    db.commit()

    row = db.execute(
        text("""SELECT type, data->>'store_name' AS name,
                       (data->>'reconciled_count')::int AS cnt
                FROM notification_outbox WHERE user_id = :uid"""),
        {"uid": str(user.id)},
    ).first()
    assert row is not None
    assert row.type == NOTIFICATION_TYPE
    assert row.name == target_store.name
    assert row.cnt == 1


def test_no_scans_to_reconcile_returns_none(db, user, target_store):
    receipt = _make_receipt(db, user, target_store)
    result = reconcile_unknown_scans_for_receipt(db, receipt, reward_trigger=lambda *a, **k: None)
    db.commit()
    assert result is None

    row = db.execute(
        text("SELECT 1 FROM notification_outbox WHERE user_id = :uid"),
        {"uid": str(user.id)},
    ).first()
    assert row is None


def test_skips_when_receipt_store_status_not_confirmed(db, user, target_store):
    _make_unknown_scan(db, user, lat=Decimal("48.86770"), lng=Decimal("2.36320"), days_ago=1)
    db.commit()

    receipt = _make_receipt(db, user, target_store)
    receipt.store_status = "pending"
    db.flush()

    result = reconcile_unknown_scans_for_receipt(db, receipt, reward_trigger=lambda *a, **k: None)
    db.commit()
    assert result is None


def test_skips_scans_of_other_users(db, user, target_store):
    other_user_id = uuid.uuid4()
    from ratis_core.models.user import User

    other = User(
        id=other_user_id,
        email="other@ratis.fr",
        display_name="Other",
        account_type="oauth",
        is_deleted=False,
    )
    db.add(other)
    db.flush()
    _make_unknown_scan(db, other, lat=Decimal("48.86770"), lng=Decimal("2.36320"), days_ago=1)
    db.commit()

    receipt = _make_receipt(db, user, target_store)
    result = reconcile_unknown_scans_for_receipt(db, receipt, reward_trigger=lambda *a, **k: None)
    db.commit()
    assert result is None
