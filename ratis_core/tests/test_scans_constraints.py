"""Direct PG-level CHECK assertion tests for ``scans`` — Bug 6 + Pattern A.

The two CHECKs covered here :

* ``receipt_required``         : ``scan_type='receipt'`` ⇔ ``receipt_id NOT NULL``
* ``manual_no_scanned_name``   : ``scan_type='manual'`` ⇒ ``product_ean NOT NULL`` AND ``scanned_name IS NULL``

Pre-Pattern A roll-out these CHECKs lived only in PG ; the ORM mirror
lands together with this test file (cf ``DEFERRED_PG_ONLY_CONSTRAINTS``
cleanup in ``test_schema_sync``). Tests are at the PG level so they
exercise the real CHECK rather than the ORM Python-side validation,
making them robust against future model refactors.

Regression coverage (Bug 6) :
* ``test_manual_scan_with_scanned_name_violates_check`` — pins that the
  admin synthetic-anchor scan prod path (``_create_admin_anchor_scan``
  in ``name_resolution_admin_service``) cannot regress to a shape that
  PG rejects.
"""

from __future__ import annotations

import uuid
from datetime import date
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

# ---------------------------------------------------------------------------
# Local seed helpers — keep inserts minimal and CHECK-respecting so each
# scenario isolates exactly one constraint at a time.
# ---------------------------------------------------------------------------


def _make_user(db: Any) -> uuid.UUID:
    from ratis_core.identifiers import generate_support_id

    uid = uuid.uuid4()
    db.execute(
        text(
            "INSERT INTO users "
            "    (id, email, support_id, account_type, "
            "     is_deleted, created_at, updated_at) "
            "VALUES (:id, :email, :sid, 'oauth', false, now(), now())"
        ),
        {
            "id": uid,
            "email": f"u-{uid.hex[:8]}@example.com",
            "sid": generate_support_id(),
        },
    )
    return uid


def _make_store(db: Any) -> uuid.UUID:
    sid = uuid.uuid4()
    db.execute(
        text(
            "INSERT INTO stores "
            "    (id, name, retailer, address, city, postal_code, lat, lng, "
            "     is_disabled, created_at, updated_at) "
            "VALUES (:id, 'S', 'lidl', '1 rue', 'Paris', '75001', "
            "        48.85, 2.35, false, now(), now())"
        ),
        {"id": sid},
    )
    return sid


def _make_product(db: Any) -> str:
    ean = str(uuid.uuid4().int)[:13]
    db.execute(
        text(
            "INSERT INTO products (ean, name, source, created_at, updated_at) VALUES (:ean, 'p', 'off', now(), now())"
        ),
        {"ean": ean},
    )
    return ean


def _make_receipt(db: Any, *, user_id: uuid.UUID, store_id: uuid.UUID) -> uuid.UUID:
    rid = uuid.uuid4()
    db.execute(
        text(
            "INSERT INTO receipts "
            "    (id, user_id, store_id, purchased_at, created_at, updated_at) "
            "VALUES (:id, :uid, :sid, :pat, now(), now())"
        ),
        {"id": rid, "uid": user_id, "sid": store_id, "pat": date.today()},
    )
    return rid


def _insert_scan(
    db: Any,
    *,
    user_id: uuid.UUID,
    store_id: uuid.UUID | None,
    scan_type: str,
    receipt_id: uuid.UUID | None,
    product_ean: str | None,
    scanned_name: str | None,
    status: str = "pending",
    store_status: str | None = None,
) -> uuid.UUID:
    """Insert + flush a ``scans`` row, surfacing any CHECK violation at
    flush time (rather than COMMIT, which would taint the SAVEPOINT)."""
    if store_status is None:
        store_status = "confirmed" if store_id is not None else "unknown"
    sid = uuid.uuid4()
    db.execute(
        text(
            "INSERT INTO scans "
            "    (id, user_id, store_id, store_status, product_ean, "
            "     scanned_name, price, quantity, scan_type, receipt_id, "
            "     status, scanned_at, status_updated_at) "
            "VALUES (:id, :uid, :store, :ss, :ean, :name, "
            "        0, 1, :type, :rid, :status, now(), now())"
        ),
        {
            "id": sid,
            "uid": user_id,
            "store": store_id,
            "ss": store_status,
            "ean": product_ean,
            "name": scanned_name,
            "type": scan_type,
            "rid": receipt_id,
            "status": status,
        },
    )
    db.flush()
    return sid


# ============================================================
# receipt_required
# ============================================================


def test_receipt_scan_without_receipt_id_violates_receipt_required(db):
    """scan_type='receipt' AND receipt_id IS NULL → CheckViolation."""
    uid = _make_user(db)
    sid = _make_store(db)
    with pytest.raises(IntegrityError) as exc_info:
        _insert_scan(
            db,
            user_id=uid,
            store_id=sid,
            scan_type="receipt",
            receipt_id=None,
            product_ean=None,
            scanned_name="LINE",
        )
    msg = str(exc_info.value.orig).lower()
    assert "receipt_required" in msg or "check constraint" in msg


def test_receipt_scan_with_receipt_id_succeeds(db):
    """scan_type='receipt' AND receipt_id NOT NULL → succeeds."""
    uid = _make_user(db)
    sid = _make_store(db)
    rid = _make_receipt(db, user_id=uid, store_id=sid)
    scan_id = _insert_scan(
        db,
        user_id=uid,
        store_id=sid,
        scan_type="receipt",
        receipt_id=rid,
        product_ean=None,
        scanned_name="LINE",
    )
    assert scan_id is not None


def test_manual_scan_with_receipt_id_violates_receipt_required(db):
    """scan_type='manual' AND receipt_id NOT NULL → CheckViolation.

    The receipt_id column is meaningless for manual scans and would
    mislead consensus / scan-history aggregations if populated.
    """
    uid = _make_user(db)
    sid = _make_store(db)
    rid = _make_receipt(db, user_id=uid, store_id=sid)
    ean = _make_product(db)
    with pytest.raises(IntegrityError) as exc_info:
        _insert_scan(
            db,
            user_id=uid,
            store_id=sid,
            scan_type="manual",
            receipt_id=rid,
            product_ean=ean,
            scanned_name=None,
        )
    msg = str(exc_info.value.orig).lower()
    assert "receipt_required" in msg or "check constraint" in msg


def test_electronic_label_scan_with_receipt_id_violates_receipt_required(db):
    """scan_type='electronic_label' AND receipt_id NOT NULL → CheckViolation."""
    uid = _make_user(db)
    sid = _make_store(db)
    rid = _make_receipt(db, user_id=uid, store_id=sid)
    with pytest.raises(IntegrityError) as exc_info:
        _insert_scan(
            db,
            user_id=uid,
            store_id=sid,
            scan_type="electronic_label",
            receipt_id=rid,
            product_ean=None,
            scanned_name="LINE",
        )
    msg = str(exc_info.value.orig).lower()
    assert "receipt_required" in msg or "check constraint" in msg


def test_electronic_label_scan_without_receipt_id_succeeds(db):
    """scan_type='electronic_label' AND receipt_id IS NULL → succeeds."""
    uid = _make_user(db)
    sid = _make_store(db)
    scan_id = _insert_scan(
        db,
        user_id=uid,
        store_id=sid,
        scan_type="electronic_label",
        receipt_id=None,
        product_ean=None,
        scanned_name="LINE",
    )
    assert scan_id is not None


# ============================================================
# manual_no_scanned_name
# ============================================================


def test_manual_scan_without_product_ean_violates_check(db):
    """scan_type='manual' AND product_ean IS NULL → CheckViolation.

    Manual scans are admin-anchor rows resolved toward a known EAN ;
    they MUST carry the resolved EAN, never a NULL.
    """
    uid = _make_user(db)
    sid = _make_store(db)
    with pytest.raises(IntegrityError) as exc_info:
        _insert_scan(
            db,
            user_id=uid,
            store_id=sid,
            scan_type="manual",
            receipt_id=None,
            product_ean=None,
            scanned_name=None,
        )
    msg = str(exc_info.value.orig).lower()
    assert "manual_no_scanned_name" in msg or "check constraint" in msg


def test_manual_scan_with_scanned_name_violates_check(db):
    """scan_type='manual' AND scanned_name NOT NULL → CheckViolation.

    REGRESSION for Bug 6 prod path :
    ``name_resolution_admin_service._create_admin_anchor_scan``
    previously inserted ``scanned_name=normalized_label`` + NULL EAN,
    which silently violated this CHECK in production (the ORM mirror
    was missing so tests with ``Base.metadata.create_all`` accepted
    the row). This test pins the fixed shape.
    """
    uid = _make_user(db)
    sid = _make_store(db)
    ean = _make_product(db)
    with pytest.raises(IntegrityError) as exc_info:
        _insert_scan(
            db,
            user_id=uid,
            store_id=sid,
            scan_type="manual",
            receipt_id=None,
            product_ean=ean,
            scanned_name="Bug6 pre-fix shape",
        )
    msg = str(exc_info.value.orig).lower()
    assert "manual_no_scanned_name" in msg or "check constraint" in msg


def test_manual_scan_with_ean_and_null_name_succeeds(db):
    """scan_type='manual' AND product_ean NOT NULL AND scanned_name IS NULL → succeeds.

    The post-Bug-6 admin synthetic anchor shape : the resolved EAN is
    on the scan, the label lives on the sibling
    ``product_name_resolutions.normalized_label`` row.
    """
    uid = _make_user(db)
    sid = _make_store(db)
    ean = _make_product(db)
    scan_id = _insert_scan(
        db,
        user_id=uid,
        store_id=sid,
        scan_type="manual",
        receipt_id=None,
        product_ean=ean,
        scanned_name=None,
    )
    assert scan_id is not None
