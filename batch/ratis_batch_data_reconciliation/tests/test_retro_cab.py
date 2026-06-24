"""Tests for Job 4 — retro_cab.

Covers :
- Newly matched scans get a CAB credit (reference_type='retro_scan').
- The partial UNIQUE index makes rerun idempotent.
- One notif per user, regardless of how many scans they have.
- dry_run never writes nor notifies.
- NT 5xx / timeout / missing env handled gracefully.
"""

from __future__ import annotations

import uuid

import httpx
import pytest
from data_reconciliation import retro_cab as retro_cab_mod
from data_reconciliation.retro_cab import reconcile_retro_cab
from sqlalchemy import text


def _seed_matched_scan(
    db,
    *,
    user_id: uuid.UUID,
    store_id: uuid.UUID,
    ean: str,
    scan_type: str = "receipt",
) -> uuid.UUID:
    # CHECK ``receipt_required`` — receipt scans need a sibling
    # Receipt ; non-receipt scans MUST have ``receipt_id IS NULL``.
    receipt_id: uuid.UUID | None = None
    if scan_type == "receipt":
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
    # CHECK ``manual_no_scanned_name`` — manual scans MUST have
    # ``scanned_name IS NULL`` (and ``product_ean NOT NULL`` which the
    # caller already provides).
    scanned_name = None if scan_type == "manual" else "LABEL"
    scan_id = uuid.uuid4()
    db.execute(
        text(
            """
        INSERT INTO scans (id, user_id, store_id, product_ean, scanned_name,
                           price, quantity, scan_type, receipt_id, status,
                           match_method,
                           scanned_at, status_updated_at, store_status)
        VALUES (:id, :uid, :sid, :ean, :name, 100, 1, :type, :rid,
                'matched', 'consensus_match', now(), now(), 'confirmed')
        """
        ),
        {
            "id": str(scan_id),
            "uid": str(user_id),
            "sid": str(store_id),
            "ean": ean,
            "type": scan_type,
            "name": scanned_name,
            "rid": str(receipt_id) if receipt_id else None,
        },
    )
    db.commit()
    return scan_id


@pytest.mark.usefixtures("engine")
def test_credits_cab_for_new_match(db, make_user, make_retailer, make_store, make_product, monkeypatch):
    monkeypatch.setenv("NOTIFIER_URL", "http://localhost:9999/api/v1/notify")
    monkeypatch.setenv("INTERNAL_API_KEY", "test-key")

    rid = make_retailer()
    sid = make_store(rid)
    ean = make_product()
    user_id = make_user()
    scan_id = _seed_matched_scan(db, user_id=user_id, store_id=sid, ean=ean, scan_type="receipt")

    notif_calls = []
    monkeypatch.setattr(retro_cab_mod, "notify_user", lambda **kw: notif_calls.append(kw))

    stats = reconcile_retro_cab(db, dry_run=False)

    assert stats["count_users_notified"] == 1
    assert stats["count_cab_credited"] > 0

    row = db.execute(
        text(
            "SELECT amount, direction, reason, reference_type, reference_id "
            "FROM cabecoin_transactions WHERE reference_id = :sid"
        ),
        {"sid": str(scan_id)},
    ).first()
    assert row is not None
    assert row.direction == "credit"
    assert row.reason == "retro_scan"
    assert row.reference_type == "retro_scan"
    assert row.amount > 0

    bal = db.execute(text("SELECT balance FROM user_cab_balance WHERE user_id = :uid"), {"uid": str(user_id)}).scalar()
    assert bal == row.amount

    assert len(notif_calls) == 1
    assert notif_calls[0]["notif_type"] == "retro_cab_gratitude"


@pytest.mark.usefixtures("engine")
def test_idempotent_via_unique_constraint(db, make_user, make_retailer, make_store, make_product, monkeypatch):
    monkeypatch.setenv("NOTIFIER_URL", "http://localhost:9999/api/v1/notify")
    monkeypatch.setenv("INTERNAL_API_KEY", "test-key")
    monkeypatch.setattr(retro_cab_mod, "notify_user", lambda **kw: None)

    rid = make_retailer()
    sid = make_store(rid)
    ean = make_product()
    user_id = make_user()
    scan_id = _seed_matched_scan(db, user_id=user_id, store_id=sid, ean=ean)

    first = reconcile_retro_cab(db, dry_run=False)
    assert first["count_cab_credited"] > 0

    bal_after_first = db.execute(
        text("SELECT balance FROM user_cab_balance WHERE user_id = :uid"), {"uid": str(user_id)}
    ).scalar()

    second = reconcile_retro_cab(db, dry_run=False)
    # Second run: scan already credited via NOT EXISTS filter, so no
    # new candidates. count_cab_credited stays 0.
    assert second["count_cab_credited"] == 0

    bal_after_second = db.execute(
        text("SELECT balance FROM user_cab_balance WHERE user_id = :uid"), {"uid": str(user_id)}
    ).scalar()
    assert bal_after_first == bal_after_second

    txn_count = db.execute(
        text("SELECT COUNT(*) FROM cabecoin_transactions WHERE reference_id = :sid AND reference_type = 'retro_scan'"),
        {"sid": str(scan_id)},
    ).scalar()
    assert txn_count == 1


@pytest.mark.usefixtures("engine")
def test_aggregates_per_user_one_notif(db, make_user, make_retailer, make_store, make_product, monkeypatch):
    monkeypatch.setenv("NOTIFIER_URL", "http://localhost:9999/api/v1/notify")
    monkeypatch.setenv("INTERNAL_API_KEY", "test-key")

    rid = make_retailer()
    sid = make_store(rid)
    user_id = make_user()
    scan_ids = []
    for _ in range(5):
        ean = make_product()
        scan_ids.append(_seed_matched_scan(db, user_id=user_id, store_id=sid, ean=ean))

    notif_calls = []
    monkeypatch.setattr(retro_cab_mod, "notify_user", lambda **kw: notif_calls.append(kw))

    stats = reconcile_retro_cab(db, dry_run=False)

    assert stats["count_users_notified"] == 1
    assert len(notif_calls) == 1
    assert notif_calls[0]["data"]["scans_count"] == 5
    # 5 scans receipt @ 20 CABs each = 100 (V1.x recal)
    assert notif_calls[0]["data"]["cab_total"] == 100


@pytest.mark.usefixtures("engine")
def test_dry_run_no_db_no_notif(db, make_user, make_retailer, make_store, make_product, monkeypatch):
    monkeypatch.setenv("NOTIFIER_URL", "http://localhost:9999/api/v1/notify")
    monkeypatch.setenv("INTERNAL_API_KEY", "test-key")

    rid = make_retailer()
    sid = make_store(rid)
    ean = make_product()
    user_id = make_user()
    scan_id = _seed_matched_scan(db, user_id=user_id, store_id=sid, ean=ean)

    notif_calls = []
    monkeypatch.setattr(retro_cab_mod, "notify_user", lambda **kw: notif_calls.append(kw))

    stats = reconcile_retro_cab(db, dry_run=True)

    # Dry-run still surfaces "would notify".
    assert stats["count_users_notified"] == 1
    assert stats["count_cab_credited"] > 0

    # But no row written, no notif sent.
    txn_count = db.execute(
        text("SELECT COUNT(*) FROM cabecoin_transactions WHERE reference_id = :sid"), {"sid": str(scan_id)}
    ).scalar()
    assert txn_count == 0

    bal = db.execute(text("SELECT balance FROM user_cab_balance WHERE user_id = :uid"), {"uid": str(user_id)}).scalar()
    assert bal == 0

    assert notif_calls == []


@pytest.mark.usefixtures("engine")
def test_handles_nt_5xx_gracefully(db, make_user, make_retailer, make_store, make_product, monkeypatch, caplog):
    """NT 5xx — credit committed, log warning, no raise."""
    monkeypatch.setenv("NOTIFIER_URL", "http://localhost:9999/api/v1/notify")
    monkeypatch.setenv("INTERNAL_API_KEY", "test-key")

    rid = make_retailer()
    sid = make_store(rid)
    ean = make_product()
    user_id = make_user()
    scan_id = _seed_matched_scan(db, user_id=user_id, store_id=sid, ean=ean)

    # Patch the underlying httpx.post inside ratis_core.notifier_client
    # so we exercise the real notify_user error-swallowing path.
    def fake_post(*args, **kwargs):
        request = httpx.Request("POST", "http://localhost:9999/api/v1/notify")
        response = httpx.Response(503, request=request)
        raise httpx.HTTPStatusError("503", request=request, response=response)

    import ratis_core.notifier_client as ncm

    monkeypatch.setattr(ncm.httpx, "post", fake_post)

    stats = reconcile_retro_cab(db, dry_run=False)
    # Notif intent counted as "notified" because notify_user swallows
    # the error (fire-and-forget contract).
    assert stats["count_users_notified"] == 1

    # CAB credit landed.
    txn_count = db.execute(
        text("SELECT COUNT(*) FROM cabecoin_transactions WHERE reference_id = :sid"), {"sid": str(scan_id)}
    ).scalar()
    assert txn_count == 1


@pytest.mark.usefixtures("engine")
def test_handles_nt_timeout_gracefully(db, make_user, make_retailer, make_store, make_product, monkeypatch):
    monkeypatch.setenv("NOTIFIER_URL", "http://localhost:9999/api/v1/notify")
    monkeypatch.setenv("INTERNAL_API_KEY", "test-key")

    rid = make_retailer()
    sid = make_store(rid)
    ean = make_product()
    user_id = make_user()
    scan_id = _seed_matched_scan(db, user_id=user_id, store_id=sid, ean=ean)

    def fake_post(*args, **kwargs):
        raise httpx.TimeoutException("timeout")

    import ratis_core.notifier_client as ncm

    monkeypatch.setattr(ncm.httpx, "post", fake_post)

    stats = reconcile_retro_cab(db, dry_run=False)
    # Same fire-and-forget contract — notify_user swallowed the timeout.
    assert stats["count_cab_credited"] > 0
    txn_count = db.execute(
        text("SELECT COUNT(*) FROM cabecoin_transactions WHERE reference_id = :sid"), {"sid": str(scan_id)}
    ).scalar()
    assert txn_count == 1


@pytest.mark.usefixtures("engine")
def test_skips_if_missing_env(db, make_user, make_retailer, make_store, make_product, monkeypatch, caplog):
    """Missing NOTIFIER_URL → skip clean with logged error, no DB writes."""
    import logging

    monkeypatch.delenv("NOTIFIER_URL", raising=False)
    monkeypatch.setenv("INTERNAL_API_KEY", "test-key")

    rid = make_retailer()
    sid = make_store(rid)
    ean = make_product()
    user_id = make_user()
    scan_id = _seed_matched_scan(db, user_id=user_id, store_id=sid, ean=ean)

    with caplog.at_level(logging.ERROR, logger=retro_cab_mod.log.name):
        stats = reconcile_retro_cab(db, dry_run=False)

    assert stats.get("error") == "missing_env"
    assert stats["count_users_notified"] == 0
    assert stats["count_cab_credited"] == 0

    txn_count = db.execute(
        text("SELECT COUNT(*) FROM cabecoin_transactions WHERE reference_id = :sid"), {"sid": str(scan_id)}
    ).scalar()
    assert txn_count == 0
