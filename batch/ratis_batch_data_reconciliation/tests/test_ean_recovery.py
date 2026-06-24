"""Tests for Job 1 — ean_recovery.

Tests focus on the recovery cascade decisions :
- Verified consensus exists → scan moves to ``status='matched'``.
- No verified consensus → scan stays ``unresolved``.
- Store has no retailer_id → skipped.
- dry_run never persists.
- Rerun is idempotent (the PNR UNIQUE index handles this naturally).
"""

from __future__ import annotations

import uuid

import pytest
from data_reconciliation.ean_recovery import reconcile_ean_recovery
from sqlalchemy import text


def _seed_unresolved_scan(
    db,
    *,
    user_id: uuid.UUID,
    store_id: uuid.UUID,
    scanned_name: str,
) -> uuid.UUID:
    # CHECK ``receipt_required`` — seed sibling Receipt for the FK.
    receipt_id = uuid.uuid4()
    db.execute(
        text(
            """
        INSERT INTO receipts (id, user_id, store_id, purchased_at,
                              created_at, updated_at)
        VALUES (:id, :uid, :sid, CURRENT_DATE, now(), now())
        """
        ),
        {"id": str(receipt_id), "uid": str(user_id), "sid": str(store_id)},
    )
    scan_id = uuid.uuid4()
    db.execute(
        text(
            """
        INSERT INTO scans (id, user_id, store_id, scanned_name, price,
                           quantity, scan_type, receipt_id, status,
                           rejected_reason, scanned_at, status_updated_at,
                           store_status)
        VALUES (:id, :uid, :sid, :name, 199, 1, 'receipt', :rid,
                'unresolved', 'no_match', now(), now(), 'confirmed')
        """
        ),
        {
            "id": str(scan_id),
            "uid": str(user_id),
            "sid": str(store_id),
            "name": scanned_name,
            "rid": str(receipt_id),
        },
    )
    db.commit()
    return scan_id


def _seed_verified_consensus(
    db,
    *,
    store_id: uuid.UUID,
    label: str,
    ean: str,
    voters: int = 3,
):
    """Insert N barcode-method PNR rows from N distinct users on the
    same (retailer_id via store, source='receipt', label, ean).

    With min_distinct_users=3 + convergence=80 default settings, this
    yields VERIFIED state.
    """
    from ratis_core.identifiers import generate_support_id

    for _i in range(voters):
        uid = uuid.uuid4()
        scan_id = uuid.uuid4()
        db.execute(
            text(
                """
            INSERT INTO users (id, email, support_id, account_type,
                               display_name, is_deleted, is_shadow_banned)
            VALUES (:id, :email, :sid, 'oauth', 'V',
                    false, false)
            """
            ),
            {
                "id": str(uid),
                "email": f"voter_{uid}@example.com",
                "sid": generate_support_id(),
            },
        )
        # CHECK ``receipt_required`` — sibling Receipt for the FK.
        receipt_id = uuid.uuid4()
        db.execute(
            text(
                """
            INSERT INTO receipts (id, user_id, store_id, purchased_at,
                                  created_at, updated_at)
            VALUES (:id, :uid, :sid, CURRENT_DATE, now(), now())
            """
            ),
            {"id": str(receipt_id), "uid": str(uid), "sid": str(store_id)},
        )
        # Seed a parent scan row so the FK on PNR is satisfied.
        db.execute(
            text(
                """
            INSERT INTO scans (id, user_id, store_id, scanned_name, price,
                               quantity, scan_type, receipt_id, status,
                               scanned_at, status_updated_at, store_status,
                               product_ean, match_method)
            VALUES (:id, :uid, :sid, :label, 100, 1, 'receipt', :rid,
                    'matched',
                    now() - interval '1 day', now() - interval '1 day',
                    'confirmed', :ean, 'barcode')
            """
            ),
            {
                "id": str(scan_id),
                "uid": str(uid),
                "sid": str(store_id),
                "label": label,
                "ean": ean,
                "rid": str(receipt_id),
            },
        )
        db.execute(
            text(
                """
            INSERT INTO product_name_resolutions
                (id, scan_id, store_id, normalized_label,
                 product_ean, user_id, match_method, source_type,
                 resolved_at)
            VALUES
                (:id, :sid_scan, :sid, :label, :ean, :uid,
                 'barcode', 'receipt', now() - interval '1 day')
            """
            ),
            {
                "id": str(uuid.uuid4()),
                "sid_scan": str(scan_id),
                "sid": str(store_id),
                "label": label,
                "ean": ean,
                "uid": str(uid),
            },
        )
    db.commit()


@pytest.mark.usefixtures("engine")
def test_unresolved_with_verified_consensus_becomes_matched(db, make_user, make_retailer, make_store, make_product):
    rid = make_retailer()
    sid = make_store(rid)
    ean = make_product(name="Lait demi-écrémé 1L")
    _seed_verified_consensus(db, store_id=sid, label="LAIT DEMI ECREME 1L", ean=ean)

    user_id = make_user()
    scan_id = _seed_unresolved_scan(db, user_id=user_id, store_id=sid, scanned_name="LAIT DEMI ECREME 1L")

    stats = reconcile_ean_recovery(db, dry_run=False)

    assert stats["count_processed"] >= 1
    assert stats["count_resolved"] >= 1

    row = db.execute(
        text("SELECT status, product_ean, match_method FROM scans WHERE id = :id"), {"id": str(scan_id)}
    ).first()
    assert row.status == "matched"
    assert row.product_ean == ean
    assert row.match_method == "consensus_match"


@pytest.mark.usefixtures("engine")
def test_unresolved_without_match_stays_unresolved(db, make_user, make_retailer, make_store):
    rid = make_retailer()
    sid = make_store(rid)
    user_id = make_user()
    scan_id = _seed_unresolved_scan(db, user_id=user_id, store_id=sid, scanned_name="MYSTERY UNKNOWN ITEM")

    stats = reconcile_ean_recovery(db, dry_run=False)

    assert stats["count_skipped"] >= 1
    assert stats["count_resolved"] == 0

    row = db.execute(text("SELECT status FROM scans WHERE id = :id"), {"id": str(scan_id)}).first()
    assert row.status == "unresolved"


@pytest.mark.usefixtures("engine")
def test_skipped_when_no_retailer_id(db, make_user, make_retailer, make_store):
    """Store with retailer_id IS NULL is skipped (out of consensus path)."""
    rid = make_retailer()
    sid = make_store(rid)
    # Detach the store from its retailer to simulate user-suggested
    # pending validation.
    db.execute(text("UPDATE stores SET retailer_id = NULL WHERE id = :id"), {"id": str(sid)})
    db.commit()

    user_id = make_user()
    scan_id = _seed_unresolved_scan(db, user_id=user_id, store_id=sid, scanned_name="ANY LABEL")

    stats = reconcile_ean_recovery(db, dry_run=False)
    assert stats["count_skipped"] >= 1
    assert stats["count_resolved"] == 0

    row = db.execute(text("SELECT status FROM scans WHERE id = :id"), {"id": str(scan_id)}).first()
    assert row.status == "unresolved"


@pytest.mark.usefixtures("engine")
def test_dry_run_does_not_persist(db, make_user, make_retailer, make_store, make_product):
    rid = make_retailer()
    sid = make_store(rid)
    ean = make_product()
    _seed_verified_consensus(db, store_id=sid, label="DRYRUN LABEL", ean=ean)

    user_id = make_user()
    scan_id = _seed_unresolved_scan(db, user_id=user_id, store_id=sid, scanned_name="DRYRUN LABEL")

    stats = reconcile_ean_recovery(db, dry_run=True)
    assert stats["count_resolved"] >= 1

    row = db.execute(text("SELECT status, product_ean FROM scans WHERE id = :id"), {"id": str(scan_id)}).first()
    assert row.status == "unresolved"
    assert row.product_ean is None


@pytest.mark.usefixtures("engine")
def test_idempotent_rerun(db, make_user, make_retailer, make_store, make_product):
    rid = make_retailer()
    sid = make_store(rid)
    ean = make_product()
    _seed_verified_consensus(db, store_id=sid, label="IDEMP LABEL", ean=ean)

    user_id = make_user()
    scan_id = _seed_unresolved_scan(db, user_id=user_id, store_id=sid, scanned_name="IDEMP LABEL")

    first = reconcile_ean_recovery(db, dry_run=False)
    assert first["count_resolved"] >= 1

    # The scan is now matched ; second run finds 0 unresolved candidates
    # for this scan, but the PNR already inserted MUST NOT duplicate.
    pnr_count_after_first = db.execute(
        text("SELECT COUNT(*) AS n FROM product_name_resolutions WHERE scan_id = :sid AND source_type = 'receipt'"),
        {"sid": str(scan_id)},
    ).scalar()

    second = reconcile_ean_recovery(db, dry_run=False)
    assert second["count_processed"] == 0  # scan no longer unresolved

    pnr_count_after_second = db.execute(
        text("SELECT COUNT(*) AS n FROM product_name_resolutions WHERE scan_id = :sid AND source_type = 'receipt'"),
        {"sid": str(scan_id)},
    ).scalar()
    assert pnr_count_after_first == pnr_count_after_second
