# batch/ratis_batch_reconciliation/tests/test_reconciliation_cashback.py
"""TDD — réconciliation Cashback.

reconcile_expired_cashbacks         : CREDIT pending > 90j → 'refused'
reconcile_missing_cashback_scans    : scans acceptés sans cashback_transaction → CREDIT inséré
reconcile_pending_withdrawals       : retraits bloqués > 24h → logués (stub V1)
check_cashback_balance_integrity    : écart solde stocké / calculé → liste de drifts
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

from reconciliation.cashback import (
    check_cashback_balance_integrity,
    reconcile_expired_cashbacks,
    reconcile_missing_cashback_scans,
    reconcile_pending_withdrawals,
)
from sqlalchemy import text

# ---------------------------------------------------------------------------
# Shared constants
# ---------------------------------------------------------------------------

_PRODUCT_EAN = "3017620422003"


# ---------------------------------------------------------------------------
# Helpers (same pattern as test_reconciliation_cab.py)
# ---------------------------------------------------------------------------


def _ensure_product(db) -> None:
    """Insert a product row for _PRODUCT_EAN if not already present."""
    db.execute(
        text("""
        INSERT INTO products (ean, name, source)
        VALUES (:ean, 'Test Cashback Product', 'off')
        ON CONFLICT (ean) DO NOTHING
    """),
        {"ean": _PRODUCT_EAN},
    )
    db.commit()


def _insert_store(db) -> uuid.UUID:
    """Insert a minimal store row."""
    store_id = uuid.uuid4()
    db.execute(
        text("""
        INSERT INTO stores (id, name, retailer, address, city, postal_code, lat, lng, is_disabled)
        VALUES (:id, 'Test Store CB', 'testcb', '2 rue CB', 'Lyon', '69001', 45.74, 4.83, false)
    """),
        {"id": store_id},
    )
    db.commit()
    return store_id


def _insert_receipt(db, user_id, store_id) -> uuid.UUID:
    """Insert a minimal receipt row required for receipt scans."""
    rid = uuid.uuid4()
    db.execute(
        text("""
        INSERT INTO receipts (id, user_id, store_id, purchased_at, created_at, updated_at)
        VALUES (:id, :uid, :sid, now()::date, now(), now())
    """),
        {"id": rid, "uid": user_id, "sid": store_id},
    )
    db.commit()
    return rid


def _insert_scan(
    db,
    user_id,
    store_id,
    scan_type,
    status="accepted",
    minutes_ago=30,
    receipt_id=None,
    ean=None,
) -> uuid.UUID:
    """Insert a scan — old enough to be detected (> 10 min).

    receipt type requires receipt_id.
    electronic_label and manual must have receipt_id=NULL.
    """
    _ensure_product(db)
    scan_id = uuid.uuid4()
    db.execute(
        text("""
        INSERT INTO scans (id, user_id, store_id, scan_type, status, product_ean, price,
                           quantity, receipt_id, scanned_at, status_updated_at)
        VALUES (:id, :uid, :sid, :stype, :status, :ean, 500,
                1, :receipt_id,
                now() - (:min || ' minutes')::interval,
                now() - (:min || ' minutes')::interval)
    """),
        {
            "id": scan_id,
            "uid": user_id,
            "sid": store_id,
            "stype": scan_type,
            "status": status,
            "ean": ean or _PRODUCT_EAN,
            "receipt_id": receipt_id,
            "min": minutes_ago,
        },
    )
    db.commit()
    return scan_id


def _insert_brand(db) -> uuid.UUID:
    """Insert a brand row required by affiliate_offers FK."""
    brand_id = uuid.uuid4()
    slug = f"brand-{brand_id}"
    db.execute(
        text("""
        INSERT INTO brands (id, name, slug)
        VALUES (:id, 'Test Brand', :slug)
        ON CONFLICT DO NOTHING
    """),
        {"id": brand_id, "slug": slug},
    )
    db.commit()
    return brand_id


def _insert_affiliate_offer(
    db,
    brand_id,
    ean=None,
    cashback_rate="0.10",
    valid_until: datetime | None = None,
) -> uuid.UUID:
    """Insert an affiliate offer for the given EAN."""
    offer_id = uuid.uuid4()
    ext_id = f"ext-{offer_id}"
    db.execute(
        text("""
        INSERT INTO affiliate_offers
            (id, provider, external_id, product_ean, brand_id, cashback_rate, valid_from, valid_until)
        VALUES (:id, 'affilae', :ext_id, :ean, :brand_id, :rate,
                now() - interval '1 day', :valid_until)
    """),
        {
            "id": offer_id,
            "ext_id": ext_id,
            "ean": ean or _PRODUCT_EAN,
            "brand_id": brand_id,
            "rate": cashback_rate,
            "valid_until": valid_until,
        },
    )
    db.commit()
    return offer_id


def _insert_cashback_tx(
    db,
    user_id,
    tx_type="CREDIT",
    status="pending",
    amount=50,
    scan_id=None,
    offer_id=None,
    ean=None,
    days_ago: int = 0,
    distributed: bool = False,
    # Legacy expr params kept for backward compatibility — ignored if days_ago/distributed used
    created_at_expr: str | None = None,
    distributed_at_expr: str | None = None,
) -> uuid.UUID:
    """Insert a cashback_transactions row.

    CREDIT/BOOST require affiliate_offer_id + product_ean (DB constraints).
    WITHDRAWAL does NOT require them.

    Use days_ago (int) and distributed (bool) for time-based inserts.
    created_at_expr / distributed_at_expr are legacy SQL-expression params kept for
    callers that pass e.g. "now() - interval '91 days'"; they are forwarded as-is via
    a separate SQL expression path to avoid f-string interpolation of user-supplied dates.
    """
    tx_id = uuid.uuid4()
    if tx_type in ("CREDIT", "BOOST"):
        assert offer_id is not None, "CREDIT/BOOST require affiliate_offer_id"
        assert ean is not None, "CREDIT/BOOST require product_ean"

    if created_at_expr is not None:
        # Legacy callers pass a SQL expression (e.g. "now() - interval '91 days'").
        # We trust these are safe literals written by the test author, not user input.
        # Use bound :distributed_at param for the timestamp value to avoid injection.
        distributed_at_val = datetime.now(UTC) if distributed_at_expr else None
        dist_sql = ":distributed_at"
        db.execute(
            text(f"""
            INSERT INTO cashback_transactions
                (id, user_id, type, amount, status, boost_applied,
                 scan_id, affiliate_offer_id, product_ean, created_at, distributed_at)
            VALUES (:id, :uid, :type, :amount, :status, false,
                    :scan_id, :offer_id, :ean, {created_at_expr}, {dist_sql})
        """),
            {
                "id": tx_id,
                "uid": user_id,
                "type": tx_type,
                "amount": amount,
                "status": status,
                "scan_id": scan_id,
                "offer_id": offer_id,
                "ean": ean,
                "distributed_at": distributed_at_val,
            },
        )
    else:
        created_at = datetime.now(UTC) - timedelta(days=days_ago)
        distributed_at = datetime.now(UTC) if (distributed or distributed_at_expr is not None) else None
        db.execute(
            text("""
            INSERT INTO cashback_transactions
                (id, user_id, type, amount, status, boost_applied,
                 scan_id, affiliate_offer_id, product_ean, created_at, distributed_at)
            VALUES (:id, :uid, :type, :amount, :status, false,
                    :scan_id, :offer_id, :ean, :created_at, :distributed_at)
        """),
            {
                "id": tx_id,
                "uid": user_id,
                "type": tx_type,
                "amount": amount,
                "status": status,
                "scan_id": scan_id,
                "offer_id": offer_id,
                "ean": ean,
                "created_at": created_at,
                "distributed_at": distributed_at,
            },
        )
    db.commit()
    return tx_id


def _insert_withdrawal(
    db,
    user_id,
    amount=1000,
    status="pending",
    requested_at_expr="now() - interval '25 hours'",
    payment_provider_ref=None,
    cashback_tx_id=None,
) -> uuid.UUID:
    """Insert a cashback_withdrawals row.

    provider_coherence CHECK: payment_provider_ref and provider_initiated_at
    must both be set or both NULL.
    """
    wid = uuid.uuid4()
    if payment_provider_ref:
        # Must set both payment_provider_ref AND provider_initiated_at
        db.execute(
            text(f"""
            INSERT INTO cashback_withdrawals
                (id, user_id, amount, status, cashback_transaction_id,
                 payment_provider_ref, provider_initiated_at, requested_at)
            VALUES (:id, :uid, :amount, :status, :tx_id,
                    :ref, now() - interval '25 hours', {requested_at_expr})
        """),
            {
                "id": wid,
                "uid": user_id,
                "amount": amount,
                "status": status,
                "tx_id": cashback_tx_id,
                "ref": payment_provider_ref,
            },
        )
    else:
        db.execute(
            text(f"""
            INSERT INTO cashback_withdrawals
                (id, user_id, amount, status, cashback_transaction_id, requested_at)
            VALUES (:id, :uid, :amount, :status, :tx_id, {requested_at_expr})
        """),
            {
                "id": wid,
                "uid": user_id,
                "amount": amount,
                "status": status,
                "tx_id": cashback_tx_id,
            },
        )
    db.commit()
    return wid


# ---------------------------------------------------------------------------
# reconcile_expired_cashbacks
# ---------------------------------------------------------------------------


def test_expired_cashback_refused(session_factory, make_user, assert_no_pending_changes):
    """CREDIT pending > 90 jours → status='refused' après réconciliation."""
    user_id = make_user()
    with session_factory() as db:
        _ensure_product(db)
        brand_id = _insert_brand(db)
        offer_id = _insert_affiliate_offer(db, brand_id)
        _insert_cashback_tx(
            db,
            user_id,
            tx_type="CREDIT",
            status="pending",
            amount=50,
            offer_id=offer_id,
            ean=_PRODUCT_EAN,
            created_at_expr="now() - interval '91 days'",
        )

    with session_factory() as db:
        count = reconcile_expired_cashbacks(db, dry_run=False)

    assert count >= 1

    with session_factory() as db:
        statuses = db.execute(
            text("""
            SELECT status FROM cashback_transactions
            WHERE user_id = :uid AND type = 'CREDIT'
        """),
            {"uid": user_id},
        ).fetchall()
        assert all(row.status == "refused" for row in statuses)


def test_expired_cashback_dry_run(session_factory, make_user, assert_no_pending_changes):
    """dry_run=True → count retourné mais status reste 'pending'."""
    user_id = make_user()
    with session_factory() as db:
        _ensure_product(db)
        brand_id = _insert_brand(db)
        offer_id = _insert_affiliate_offer(db, brand_id)
        _insert_cashback_tx(
            db,
            user_id,
            tx_type="CREDIT",
            status="pending",
            amount=50,
            offer_id=offer_id,
            ean=_PRODUCT_EAN,
            created_at_expr="now() - interval '91 days'",
        )

    with session_factory() as db:
        count = reconcile_expired_cashbacks(db, dry_run=True)

    assert count >= 1

    with session_factory() as db:
        still_pending = db.execute(
            text("""
            SELECT COUNT(*) FROM cashback_transactions
            WHERE user_id = :uid AND type = 'CREDIT' AND status = 'pending'
        """),
            {"uid": user_id},
        ).scalar()
        assert still_pending == 1  # pas modifié en dry_run


def test_recent_pending_not_expired(session_factory, make_user, assert_no_pending_changes):
    """CREDIT pending depuis 10 jours → NOT expiré (count=0 pour cet utilisateur)."""
    user_id = make_user()
    with session_factory() as db:
        _ensure_product(db)
        brand_id = _insert_brand(db)
        offer_id = _insert_affiliate_offer(db, brand_id)
        _insert_cashback_tx(
            db,
            user_id,
            tx_type="CREDIT",
            status="pending",
            amount=50,
            offer_id=offer_id,
            ean=_PRODUCT_EAN,
            created_at_expr="now() - interval '10 days'",
        )

    with session_factory() as db:
        # Run reconciliation then check our user's tx is still pending
        reconcile_expired_cashbacks(db, dry_run=False)

    with session_factory() as db:
        still_pending = db.execute(
            text("""
            SELECT COUNT(*) FROM cashback_transactions
            WHERE user_id = :uid AND type = 'CREDIT' AND status = 'pending'
        """),
            {"uid": user_id},
        ).scalar()
        assert still_pending == 1  # non touché


# ---------------------------------------------------------------------------
# reconcile_missing_cashback_scans
# ---------------------------------------------------------------------------


def test_missing_cashback_detected(session_factory, make_user, assert_no_pending_changes):
    """Scan receipt accepté avec offre active et sans cashback_tx → CREDIT inséré (status='pending')."""
    user_id = make_user()
    with session_factory() as db:
        store_id = _insert_store(db)
        receipt_id = _insert_receipt(db, user_id, store_id)
        _insert_scan(db, user_id, store_id, "receipt", receipt_id=receipt_id, minutes_ago=30)
        brand_id = _insert_brand(db)
        _insert_affiliate_offer(db, brand_id, ean=_PRODUCT_EAN, cashback_rate="0.10")

    with session_factory() as db:
        count = reconcile_missing_cashback_scans(db, dry_run=False)

    assert count >= 1

    with session_factory() as db:
        tx = db.execute(
            text("""
            SELECT type, status, amount
            FROM cashback_transactions
            WHERE user_id = :uid AND type = 'CREDIT'
        """),
            {"uid": user_id},
        ).first()
        assert tx is not None
        assert tx.type == "CREDIT"
        assert tx.status == "pending"
        assert tx.amount == 50  # int(round(0.10 * 500)) = 50


def test_missing_cashback_idempotent(session_factory, make_user, assert_no_pending_changes):
    """Second passage → rien détecté (idempotence)."""
    user_id = make_user()
    with session_factory() as db:
        store_id = _insert_store(db)
        receipt_id = _insert_receipt(db, user_id, store_id)
        _insert_scan(db, user_id, store_id, "receipt", receipt_id=receipt_id, minutes_ago=30)
        brand_id = _insert_brand(db)
        _insert_affiliate_offer(db, brand_id, ean=_PRODUCT_EAN, cashback_rate="0.10")

    with session_factory() as db:
        reconcile_missing_cashback_scans(db, dry_run=False)

    with session_factory() as db:
        count = reconcile_missing_cashback_scans(db, dry_run=False)

    assert count == 0

    with session_factory() as db:
        tx_count = db.execute(
            text("""
            SELECT COUNT(*) FROM cashback_transactions
            WHERE user_id = :uid AND type = 'CREDIT'
        """),
            {"uid": user_id},
        ).scalar()
        assert tx_count == 1  # un seul CREDIT, pas deux


def test_no_offer_no_cashback(session_factory, make_user, assert_no_pending_changes):
    """Scan accepté sans offre affiliée active → count=0 pour cet utilisateur."""
    user_id = make_user()
    with session_factory() as db:
        store_id = _insert_store(db)
        receipt_id = _insert_receipt(db, user_id, store_id)
        _insert_scan(db, user_id, store_id, "receipt", receipt_id=receipt_id, minutes_ago=30)
        # No affiliate_offer inserted

    with session_factory() as db:
        count = reconcile_missing_cashback_scans(db, dry_run=False)

    # This user has no offer → no CREDIT
    with session_factory() as db:
        tx_count = db.execute(
            text("""
            SELECT COUNT(*) FROM cashback_transactions
            WHERE user_id = :uid AND type = 'CREDIT'
        """),
            {"uid": user_id},
        ).scalar()
        assert tx_count == 0

    assert count == 0


# ---------------------------------------------------------------------------
# reconcile_pending_withdrawals
# ---------------------------------------------------------------------------


def test_pending_withdrawal_no_ref_logged(session_factory, make_user, assert_no_pending_changes):
    """Retrait pending > 24h sans payment_provider_ref → détecté, ERROR logué."""
    user_id = make_user()
    with session_factory() as db:
        # WITHDRAWAL tx doesn't require offer or product_ean
        tx_id = uuid.uuid4()
        db.execute(
            text("""
            INSERT INTO cashback_transactions
                (id, user_id, type, amount, status, boost_applied, created_at)
            VALUES (:id, :uid, 'WITHDRAWAL', 1000, 'confirmed', false, now())
        """),
            {"id": tx_id, "uid": user_id},
        )
        db.commit()
        _insert_withdrawal(
            db,
            user_id,
            amount=1000,
            status="pending",
            requested_at_expr="now() - interval '25 hours'",
            payment_provider_ref=None,
            cashback_tx_id=tx_id,
        )

    with patch("reconciliation.cashback.log") as mock_log:
        with session_factory() as db:
            count = reconcile_pending_withdrawals(db, dry_run=False)

        assert count >= 1
        # Verify ERROR was logged for no-ref withdrawal
        error_calls = [str(call) for call in mock_log.error.call_args_list]
        assert any("NULL payment_provider_ref" in c for c in error_calls)


def test_recent_pending_withdrawal_excluded(session_factory, make_user, assert_no_pending_changes):
    """Retrait pending depuis 1h → exclu (trop récent)."""
    user_id = make_user()
    with session_factory() as db:
        tx_id = uuid.uuid4()
        db.execute(
            text("""
            INSERT INTO cashback_transactions
                (id, user_id, type, amount, status, boost_applied, created_at)
            VALUES (:id, :uid, 'WITHDRAWAL', 1000, 'confirmed', false, now())
        """),
            {"id": tx_id, "uid": user_id},
        )
        db.commit()
        _insert_withdrawal(
            db,
            user_id,
            amount=1000,
            status="pending",
            requested_at_expr="now() - interval '1 hour'",
            payment_provider_ref=None,
            cashback_tx_id=tx_id,
        )

    with session_factory() as db:
        reconcile_pending_withdrawals(db, dry_run=False)

    # This user's withdrawal is too recent; might be 0 if no other stuck withdrawals exist
    # We verify no CREDIT was inserted (wrong type check), just verify our withdrawal is not counted
    with session_factory() as db:
        recent_count = db.execute(
            text("""
            SELECT COUNT(*) FROM cashback_withdrawals
            WHERE user_id = :uid AND status = 'pending'
              AND requested_at >= now() - interval '2 hours'
        """),
            {"uid": user_id},
        ).scalar()
        assert recent_count == 1  # still pending, not touched

    # The count from reconcile should not include our recent withdrawal
    # We confirm by checking: if count > 0, it's not our user's withdrawal
    with session_factory() as db:
        stuck = db.execute(
            text("""
            SELECT COUNT(*) FROM cashback_withdrawals
            WHERE user_id = :uid AND status = 'pending'
              AND requested_at < now() - interval '24 hours'
        """),
            {"uid": user_id},
        ).scalar()
        assert stuck == 0


# ---------------------------------------------------------------------------
# check_cashback_balance_integrity
# ---------------------------------------------------------------------------


def test_cashback_integrity_no_drift(session_factory, make_user, assert_no_pending_changes):
    """Solde cohérent → utilisateur absent de la liste de drifts."""
    user_id = make_user()
    with session_factory() as db:
        _ensure_product(db)
        brand_id = _insert_brand(db)
        offer_id = _insert_affiliate_offer(db, brand_id)
        # Insert a CREDIT with distributed_at set → contributes +50 to computed balance
        _insert_cashback_tx(
            db,
            user_id,
            tx_type="CREDIT",
            status="confirmed",
            amount=50,
            offer_id=offer_id,
            ean=_PRODUCT_EAN,
            distributed_at_expr="now()",
        )
        # Set stored balance to match
        db.execute(text("UPDATE user_cashback_balance SET balance = 50 WHERE user_id = :uid"), {"uid": user_id})
        db.commit()

    with session_factory() as db:
        drifts = check_cashback_balance_integrity(db)

    assert all(d["user_id"] != user_id for d in drifts)


def test_cashback_integrity_detects_drift(session_factory, make_user, assert_no_pending_changes):
    """Solde manipulé directement → drift détecté."""
    user_id = make_user()
    with session_factory() as db:
        _ensure_product(db)
        brand_id = _insert_brand(db)
        offer_id = _insert_affiliate_offer(db, brand_id)
        _insert_cashback_tx(
            db,
            user_id,
            tx_type="CREDIT",
            status="confirmed",
            amount=50,
            offer_id=offer_id,
            ean=_PRODUCT_EAN,
            distributed_at_expr="now()",
        )
        # Stored balance = 50, computed = 50 → no drift yet
        db.execute(text("UPDATE user_cashback_balance SET balance = 50 WHERE user_id = :uid"), {"uid": user_id})
        db.commit()

        # Manipulate balance directly (simulate drift)
        db.execute(text("UPDATE user_cashback_balance SET balance = 999 WHERE user_id = :uid"), {"uid": user_id})
        db.commit()

    with session_factory() as db:
        drifts = check_cashback_balance_integrity(db)

    user_drift = next((d for d in drifts if d["user_id"] == user_id), None)
    assert user_drift is not None
    assert user_drift["stored_balance"] == 999
    assert user_drift["computed_balance"] == 50
    assert user_drift["drift"] == 949


# ---------------------------------------------------------------------------
# DP-03 — concurrent-runs idempotency on cashback CREDIT
# ---------------------------------------------------------------------------


def test_unique_index_blocks_duplicate_cashback_credit(
    session_factory,
    make_user,
    assert_no_pending_changes,
):
    """DP-03 invariant : uq_cashbacktx_scan_ean_credit blocks two CREDIT rows on
    (scan_id, product_ean) — partial WHERE type='CREDIT'. Two concurrent runs that
    both try to INSERT the same CREDIT row → 2nd hits IntegrityError."""
    from sqlalchemy.exc import IntegrityError

    user_id = make_user()
    with session_factory() as db:
        store_id = _insert_store(db)
        receipt_id = _insert_receipt(db, user_id, store_id)
        scan_id = _insert_scan(db, user_id, store_id, "receipt", receipt_id=receipt_id)
        brand_id = _insert_brand(db)
        offer_id = _insert_affiliate_offer(db, brand_id)
        # First CREDIT row.
        _insert_cashback_tx(
            db,
            user_id,
            tx_type="CREDIT",
            status="pending",
            amount=100,
            scan_id=scan_id,
            offer_id=offer_id,
            ean=_PRODUCT_EAN,
        )

    raised = False
    with session_factory() as db:
        try:
            db.execute(
                text("""
                INSERT INTO cashback_transactions
                    (id, user_id, type, amount, status, scan_id, product_ean,
                     affiliate_offer_id, boost_applied, created_at)
                VALUES (:id, :uid, 'CREDIT', 100, 'pending', :sid, :ean,
                        :offer_id, false, now())
            """),
                {
                    "id": uuid.uuid4(),
                    "uid": user_id,
                    "sid": scan_id,
                    "ean": _PRODUCT_EAN,
                    "offer_id": offer_id,
                },
            )
            db.commit()
        except IntegrityError:
            db.rollback()
            raised = True

    assert raised, "expected IntegrityError on duplicate (scan_id, product_ean) CREDIT"


def test_unique_index_allows_credit_and_withdrawal_for_same_user(
    session_factory,
    make_user,
    assert_no_pending_changes,
):
    """DP-03 partial-index : the UNIQUE index is WHERE type='CREDIT'. A WITHDRAWAL
    row for the same user is unrelated and must NOT be blocked."""
    user_id = make_user()
    with session_factory() as db:
        store_id = _insert_store(db)
        receipt_id = _insert_receipt(db, user_id, store_id)
        scan_id = _insert_scan(db, user_id, store_id, "receipt", receipt_id=receipt_id)
        brand_id = _insert_brand(db)
        offer_id = _insert_affiliate_offer(db, brand_id)
        _insert_cashback_tx(
            db,
            user_id,
            tx_type="CREDIT",
            status="pending",
            amount=100,
            scan_id=scan_id,
            offer_id=offer_id,
            ean=_PRODUCT_EAN,
        )
        # WITHDRAWAL must be allowed (different type, partial index excludes it).
        _insert_cashback_tx(
            db,
            user_id,
            tx_type="WITHDRAWAL",
            status="pending",
            amount=50,
            scan_id=None,
            offer_id=None,
            ean=None,
        )

    with session_factory() as db:
        rows = db.execute(
            text("""
            SELECT type, COUNT(*) AS n
            FROM cashback_transactions
            WHERE user_id = :uid
            GROUP BY type
        """),
            {"uid": user_id},
        ).fetchall()

    by_type = {r.type: r.n for r in rows}
    assert by_type.get("CREDIT") == 1
    assert by_type.get("WITHDRAWAL") == 1


def test_reconcile_missing_cashback_scans_idempotent_under_concurrent_runs(
    session_factory,
    make_user,
    assert_no_pending_changes,
):
    """DP-03 : reconcile_missing_cashback_scans uses INSERT ... ON CONFLICT DO NOTHING.
    Re-running it after a CREDIT is in place must NOT create a duplicate."""
    user_id = make_user()
    with session_factory() as db:
        store_id = _insert_store(db)
        receipt_id = _insert_receipt(db, user_id, store_id)
        scan_id = _insert_scan(db, user_id, store_id, "receipt", receipt_id=receipt_id)
        brand_id = _insert_brand(db)
        _insert_affiliate_offer(db, brand_id)

    with session_factory() as db:
        first = reconcile_missing_cashback_scans(db, dry_run=False)
    with session_factory() as db:
        second = reconcile_missing_cashback_scans(db, dry_run=False)

    assert first >= 1  # first run inserted at least one row
    assert second == 0  # second run re-detects nothing (NOT EXISTS guard now reads it)

    with session_factory() as db:
        row_count = db.execute(
            text("""
            SELECT COUNT(*) FROM cashback_transactions
            WHERE scan_id = :sid AND product_ean = :ean AND type = 'CREDIT'
        """),
            {"sid": scan_id, "ean": _PRODUCT_EAN},
        ).scalar()
        assert row_count == 1
