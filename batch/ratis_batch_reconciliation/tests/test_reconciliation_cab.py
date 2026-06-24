# batch/ratis_batch_reconciliation/tests/test_reconciliation_cab.py
"""TDD — réconciliation CAB.

reconcile_missing_scan_rewards : détecte scans acceptés sans credit CAB,
  insère la transaction manquante + met à jour user_cab_balance.
check_cab_balance_integrity : détecte les écarts entre user_cab_balance.balance
  et la somme des cabecoin_transactions.
"""

from __future__ import annotations

import uuid

from reconciliation.cab import (
    _credit_scan,
    check_cab_balance_integrity,
    reconcile_missing_scan_rewards,
)
from sqlalchemy import text

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_PRODUCT_EAN = "3017620422003"  # shared EAN used across all scan inserts


def _ensure_product(db) -> None:
    """Insert a product row for _PRODUCT_EAN if not already present."""
    db.execute(
        text("""
        INSERT INTO products (ean, name, source)
        VALUES (:ean, 'Test Product', 'off')
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
        VALUES (:id, 'Test Store', 'test', '1 rue Test', 'Paris', '75001', 48.85, 2.35, false)
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


def _insert_scan(db, user_id, store_id, scan_type, status="accepted", minutes_ago=30, receipt_id=None):
    """Insert an accepted scan — old enough to be detected (> 10 min).

    receipt type requires receipt_id (DB constraint receipt_required).
    electronic_label and manual must have receipt_id=NULL.
    """
    _ensure_product(db)
    scan_id = uuid.uuid4()
    db.execute(
        text("""
        INSERT INTO scans (id, user_id, store_id, scan_type, status, product_ean, price,
                           quantity, receipt_id, scanned_at, status_updated_at)
        VALUES (:id, :uid, :sid, :stype, :status, :ean, 199,
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
            "ean": _PRODUCT_EAN,
            "receipt_id": receipt_id,
            "min": minutes_ago,
        },
    )
    db.commit()
    return scan_id


def _insert_cab_credit(db, user_id, scan_id, amount=50):
    """Insert an existing CAB credit for a given scan."""
    tx_id = uuid.uuid4()
    db.execute(
        text("""
        INSERT INTO cabecoin_transactions
            (id, user_id, amount, direction, reason, reference_id, reference_type, created_at)
        VALUES (:id, :uid, :amount, 'credit', 'receipt_scan', :ref_id, 'scan', now())
    """),
        {"id": tx_id, "uid": user_id, "amount": amount, "ref_id": scan_id},
    )
    db.execute(
        text("UPDATE user_cab_balance SET balance = balance + :amount WHERE user_id = :uid"),
        {"amount": amount, "uid": user_id},
    )
    db.commit()
    return tx_id


# ---------------------------------------------------------------------------
# reconcile_missing_scan_rewards
# ---------------------------------------------------------------------------


def test_reconcile_detects_scan_without_cab(session_factory, make_user, assert_no_pending_changes):
    """Un scan accepté sans credit CAB → 1 ligne détectée, transaction insérée."""
    user_id = make_user()
    with session_factory() as db:
        store_id = _insert_store(db)
        receipt_id = _insert_receipt(db, user_id, store_id)
        scan_id = _insert_scan(db, user_id, store_id, "receipt", receipt_id=receipt_id)

    with session_factory() as db:
        count = reconcile_missing_scan_rewards(db, dry_run=False)

    assert count == 1

    with session_factory() as db:
        tx = db.execute(
            text("""
            SELECT amount, direction, reason, reference_id
            FROM cabecoin_transactions
            WHERE user_id = :uid AND direction = 'credit'
        """),
            {"uid": user_id},
        ).first()
        assert tx is not None
        assert tx.reason == "receipt_scan"
        assert tx.amount == 20  # cab_per_receipt_scan from ratis_settings.json (V1.x recal)
        assert tx.reference_id == scan_id  # ensure correct scan is linked

        bal = db.execute(text("SELECT balance FROM user_cab_balance WHERE user_id = :uid"), {"uid": user_id}).scalar()
        assert bal == 20


def test_reconcile_idempotent(session_factory, make_user, assert_no_pending_changes):
    """Rejouer la réconciliation ne crée pas de doublon (idempotence reference_id)."""
    user_id = make_user()
    with session_factory() as db:
        store_id = _insert_store(db)
        receipt_id = _insert_receipt(db, user_id, store_id)
        _insert_scan(db, user_id, store_id, "receipt", receipt_id=receipt_id)

    with session_factory() as db:
        reconcile_missing_scan_rewards(db, dry_run=False)

    with session_factory() as db:
        count = reconcile_missing_scan_rewards(db, dry_run=False)

    assert count == 0  # deuxième run ne trouve rien

    with session_factory() as db:
        tx_count = db.execute(
            text("SELECT COUNT(*) FROM cabecoin_transactions WHERE user_id = :uid AND direction = 'credit'"),
            {"uid": user_id},
        ).scalar()
        assert tx_count == 1  # un seul credit, pas deux


def test_reconcile_skips_already_rewarded(session_factory, make_user, assert_no_pending_changes):
    """Scan déjà récompensé → non détecté."""
    user_id = make_user()
    with session_factory() as db:
        store_id = _insert_store(db)
        scan_id = _insert_scan(db, user_id, store_id, "electronic_label")
        _insert_cab_credit(db, user_id, scan_id, amount=20)

    with session_factory() as db:
        count = reconcile_missing_scan_rewards(db, dry_run=False)

    assert count == 0


def test_reconcile_dry_run_no_write(session_factory, make_user, assert_no_pending_changes):
    """dry_run=True → détecte mais n'écrit rien."""
    user_id = make_user()
    with session_factory() as db:
        store_id = _insert_store(db)
        receipt_id = _insert_receipt(db, user_id, store_id)
        _insert_scan(db, user_id, store_id, "receipt", receipt_id=receipt_id)

    with session_factory() as db:
        count = reconcile_missing_scan_rewards(db, dry_run=True)

    assert count == 1

    with session_factory() as db:
        tx_count = db.execute(
            text("SELECT COUNT(*) FROM cabecoin_transactions WHERE user_id = :uid"), {"uid": user_id}
        ).scalar()
        assert tx_count == 0  # rien écrit en dry_run


def test_reconcile_all_scan_types(session_factory, make_user, assert_no_pending_changes):
    """receipt → 20 CABs, electronic_label → 3, manual → 1 (V1.x recal)."""
    user_id = make_user()
    with session_factory() as db:
        store_id = _insert_store(db)
        receipt_id = _insert_receipt(db, user_id, store_id)
        _insert_scan(db, user_id, store_id, "receipt", minutes_ago=30, receipt_id=receipt_id)
        _insert_scan(db, user_id, store_id, "electronic_label", minutes_ago=31)
        _insert_scan(db, user_id, store_id, "manual", minutes_ago=32)

    with session_factory() as db:
        count = reconcile_missing_scan_rewards(db, dry_run=False)

    assert count == 3

    with session_factory() as db:
        bal = db.execute(text("SELECT balance FROM user_cab_balance WHERE user_id = :uid"), {"uid": user_id}).scalar()
        assert bal == 24  # 20 + 3 + 1


def test_reconcile_recent_scan_excluded(session_factory, make_user, assert_no_pending_changes):
    """Scan accepté il y a 5 minutes → exclu (trop récent, laisse le temps au service)."""
    user_id = make_user()
    with session_factory() as db:
        store_id = _insert_store(db)
        receipt_id = _insert_receipt(db, user_id, store_id)
        _insert_scan(db, user_id, store_id, "receipt", minutes_ago=5, receipt_id=receipt_id)

    with session_factory() as db:
        count = reconcile_missing_scan_rewards(db, dry_run=False)

    assert count == 0


# ---------------------------------------------------------------------------
# DP-03 — concurrent-runs idempotency (write-side guard)
# ---------------------------------------------------------------------------


def test_credit_scan_inserts_when_no_conflict(session_factory, make_user, assert_no_pending_changes):
    """First _credit_scan call → INSERT + balance update."""
    user_id = make_user()
    with session_factory() as db:
        store_id = _insert_store(db)
        scan_id = _insert_scan(db, user_id, store_id, "electronic_label")

    with session_factory() as db:
        ok = _credit_scan(db, scan_id, user_id, amount=20, reason="label_scan")

    assert ok is True

    with session_factory() as db:
        tx_count = db.execute(
            text("SELECT COUNT(*) FROM cabecoin_transactions WHERE reference_id = :sid"), {"sid": scan_id}
        ).scalar()
        bal = db.execute(text("SELECT balance FROM user_cab_balance WHERE user_id = :uid"), {"uid": user_id}).scalar()
        assert tx_count == 1
        assert bal == 20


def test_credit_scan_skips_balance_when_concurrent_run_won(
    session_factory,
    make_user,
    assert_no_pending_changes,
):
    """DP-03 race : another run already credited this scan → INSERT ON CONFLICT skips,
    and _credit_scan must NOT bump user_cab_balance again (would double-credit)."""
    user_id = make_user()
    with session_factory() as db:
        store_id = _insert_store(db)
        scan_id = _insert_scan(db, user_id, store_id, "electronic_label")
        # Simulate a concurrent run that already credited this scan + bumped balance.
        _insert_cab_credit(db, user_id, scan_id, amount=20)

    with session_factory() as db:
        ok = _credit_scan(db, scan_id, user_id, amount=20, reason="label_scan")

    assert ok is False  # signals "skipped — already credited"

    with session_factory() as db:
        tx_count = db.execute(
            text("SELECT COUNT(*) FROM cabecoin_transactions WHERE reference_id = :sid"), {"sid": scan_id}
        ).scalar()
        bal = db.execute(text("SELECT balance FROM user_cab_balance WHERE user_id = :uid"), {"uid": user_id}).scalar()
        assert tx_count == 1  # still one credit row
        assert bal == 20  # balance NOT double-credited


def test_credit_scan_different_scans_both_succeed(
    session_factory,
    make_user,
    assert_no_pending_changes,
):
    """DP-03 partial-index check : two different scans → both INSERTs succeed."""
    user_id = make_user()
    with session_factory() as db:
        store_id = _insert_store(db)
        scan_a = _insert_scan(db, user_id, store_id, "electronic_label", minutes_ago=30)
        scan_b = _insert_scan(db, user_id, store_id, "manual", minutes_ago=31)

    with session_factory() as db:
        ok_a = _credit_scan(db, scan_a, user_id, amount=20, reason="label_scan")
        ok_b = _credit_scan(db, scan_b, user_id, amount=10, reason="barcode_scan")

    assert ok_a is True
    assert ok_b is True

    with session_factory() as db:
        bal = db.execute(text("SELECT balance FROM user_cab_balance WHERE user_id = :uid"), {"uid": user_id}).scalar()
        assert bal == 30  # 20 + 10


def test_unique_index_blocks_raw_double_insert_for_scan_credit(
    session_factory,
    make_user,
    assert_no_pending_changes,
):
    """DP-03 invariant : the partial UNIQUE INDEX uq_cabtx_scan_credit prevents
    two credit rows for the same scan, regardless of how the second INSERT is issued."""
    from sqlalchemy.exc import IntegrityError

    user_id = make_user()
    with session_factory() as db:
        store_id = _insert_store(db)
        scan_id = _insert_scan(db, user_id, store_id, "electronic_label")
        _insert_cab_credit(db, user_id, scan_id, amount=20)

    raised = False
    with session_factory() as db:
        try:
            db.execute(
                text("""
                INSERT INTO cabecoin_transactions
                    (id, user_id, amount, direction, reason, reference_id, reference_type, created_at)
                VALUES (:id, :uid, 20, 'credit', 'label_scan', :ref_id, 'scan', now())
            """),
                {"id": uuid.uuid4(), "uid": user_id, "ref_id": scan_id},
            )
            db.commit()
        except IntegrityError:
            db.rollback()
            raised = True

    assert raised, "expected IntegrityError on duplicate (reference_id, scan-credit)"


def test_unique_index_allows_credit_and_debit_for_same_reference(
    session_factory,
    make_user,
    assert_no_pending_changes,
):
    """DP-03 partial-index : index is WHERE direction='credit' AND reference_type='scan'.
    A debit on the same reference_id must NOT be blocked (partial index excludes it)."""
    user_id = make_user()
    with session_factory() as db:
        store_id = _insert_store(db)
        scan_id = _insert_scan(db, user_id, store_id, "electronic_label")
        _insert_cab_credit(db, user_id, scan_id, amount=20)

        # A debit row on the same reference_id is permitted (partial index excludes debits).
        db.execute(
            text("""
            INSERT INTO cabecoin_transactions
                (id, user_id, amount, direction, reason, reference_id, reference_type, created_at)
            VALUES (:id, :uid, 5, 'debit', 'cashback_boost_debit', :ref_id, 'scan', now())
        """),
            {"id": uuid.uuid4(), "uid": user_id, "ref_id": scan_id},
        )
        db.commit()

    with session_factory() as db:
        rows = db.execute(
            text("""
            SELECT direction, COUNT(*) AS n
            FROM cabecoin_transactions
            WHERE reference_id = :sid
            GROUP BY direction
        """),
            {"sid": scan_id},
        ).fetchall()

    by_dir = {r.direction: r.n for r in rows}
    assert by_dir.get("credit") == 1
    assert by_dir.get("debit") == 1


# ---------------------------------------------------------------------------
# check_cab_balance_integrity
# ---------------------------------------------------------------------------


def test_check_cab_integrity_no_drift(session_factory, make_user, assert_no_pending_changes):
    """Solde cohérent → liste vide."""
    user_id = make_user()
    with session_factory() as db:
        store_id = _insert_store(db)
        scan_id = _insert_scan(db, user_id, store_id, "electronic_label")
        _insert_cab_credit(db, user_id, scan_id, amount=50)

    with session_factory() as db:
        drifts = check_cab_balance_integrity(db)

    assert all(d["user_id"] != user_id for d in drifts)


def test_check_cab_integrity_detects_drift(session_factory, make_user, assert_no_pending_changes):
    """Solde manipulé directement → drift détecté."""
    user_id = make_user()
    with session_factory() as db:
        store_id = _insert_store(db)
        scan_id = _insert_scan(db, user_id, store_id, "electronic_label")
        _insert_cab_credit(db, user_id, scan_id, amount=50)
        # Manipuler le solde directement (simule une dérive)
        db.execute(text("UPDATE user_cab_balance SET balance = 999 WHERE user_id = :uid"), {"uid": user_id})
        db.commit()

    with session_factory() as db:
        drifts = check_cab_balance_integrity(db)

    user_drift = next((d for d in drifts if d["user_id"] == user_id), None)
    assert user_drift is not None
    assert user_drift["stored_balance"] == 999
    assert user_drift["computed_balance"] == 50
    assert user_drift["drift"] == 949
