"""Tests for the nightly savings snapshot batch."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from savings_batch import recompute_all_user_snapshots
from sqlalchemy import text


def _mk_store(db, *, name: str = "s") -> uuid.UUID:
    sid = uuid.uuid4()
    db.execute(
        text("""
        INSERT INTO stores (id, name, retailer, address, city, postal_code, lat, lng, is_disabled)
        VALUES (:id, :n, 'b', '1 r', 'Paris', '75000', 48.86, 2.36, false)
    """),
        {"id": str(sid), "n": name},
    )
    db.commit()
    return sid


def _mk_product(db, ean: str) -> None:
    db.execute(
        text("INSERT INTO products (ean, name, source) VALUES (:e, 'P', 'off') ON CONFLICT DO NOTHING"),
        {"e": ean},
    )
    db.commit()


def _mk_consensus(db, *, store_id: uuid.UUID, ean: str, price: int) -> None:
    now = datetime.now(UTC)
    db.execute(
        text("""
        INSERT INTO price_consensus (id, store_id, product_ean, price, trust_score, first_seen_at, last_seen_at)
        VALUES (:id, :sid, :e, :p, 90, :n, :n)
    """),
        {"id": str(uuid.uuid4()), "sid": str(store_id), "e": ean, "p": price, "n": now},
    )
    db.commit()


_scan_counter = {"n": 0}


def _mk_scan(
    db,
    *,
    user_id: uuid.UUID,
    store_id: uuid.UUID,
    ean: str,
    price: int,
    status: str = "accepted",
    scan_type: str = "receipt",
) -> None:
    _scan_counter["n"] += 1
    # CHECK ``receipt_required`` : receipt scans need a sibling Receipt.
    receipt_id: uuid.UUID | None = None
    if scan_type == "receipt":
        receipt_id = uuid.uuid4()
        db.execute(
            text("""
            INSERT INTO receipts (id, user_id, store_id, purchased_at,
                                  created_at, updated_at)
            VALUES (:id, :uid, :sid, CURRENT_DATE, now(), now())
        """),
            {"id": str(receipt_id), "uid": str(user_id), "sid": str(store_id)},
        )
    db.execute(
        text("""
        INSERT INTO scans (id, user_id, store_id, product_ean, scanned_name, price, quantity,
                           scan_type, receipt_id, status, scanned_at)
        VALUES (:id, :uid, :sid, :e, 'x', :p, 1, :st, :rid, :status,
                now() - (:n * interval '1 second'))
    """),
        {
            "id": str(uuid.uuid4()),
            "uid": str(user_id),
            "sid": str(store_id),
            "e": ean,
            "p": price,
            "st": scan_type,
            "status": status,
            "rid": str(receipt_id) if receipt_id else None,
            "n": _scan_counter["n"],
        },
    )
    db.commit()


def test_batch_inserts_snapshot_for_user_with_savings(session_factory, make_user):
    user_id = make_user()
    with session_factory() as db:
        sid = _mk_store(db)
        _mk_product(db, "7000000000001")
        _mk_consensus(db, store_id=sid, ean="7000000000001", price=500)
        _mk_scan(db, user_id=user_id, store_id=sid, ean="7000000000001", price=100)

    with session_factory() as db:
        count = recompute_all_user_snapshots(db)
        assert count == 1

    with session_factory() as db:
        row = (
            db.execute(
                text("SELECT lifetime_savings_cents, rings_consumed FROM user_savings_snapshot WHERE user_id=:u"),
                {"u": str(user_id)},
            )
            .mappings()
            .one()
        )
        assert row["lifetime_savings_cents"] == 400  # (500-100)*1
        assert row["rings_consumed"] == 0


def test_batch_upserts_existing_snapshot_preserving_rings_consumed(session_factory, make_user):
    """Re-running the batch updates lifetime but MUST NOT reset rings_consumed."""
    user_id = make_user()
    with session_factory() as db:
        sid = _mk_store(db)
        _mk_product(db, "7000000000002")
        _mk_consensus(db, store_id=sid, ean="7000000000002", price=500)
        _mk_scan(db, user_id=user_id, store_id=sid, ean="7000000000002", price=100)
        # Pre-existing snapshot with rings_consumed=2
        db.execute(
            text("""
            INSERT INTO user_savings_snapshot (user_id, lifetime_savings_cents, rings_consumed, last_computed_at)
            VALUES (:u, 0, 2, now())
        """),
            {"u": str(user_id)},
        )
        db.commit()

    with session_factory() as db:
        recompute_all_user_snapshots(db)

    with session_factory() as db:
        row = (
            db.execute(
                text("SELECT lifetime_savings_cents, rings_consumed FROM user_savings_snapshot WHERE user_id=:u"),
                {"u": str(user_id)},
            )
            .mappings()
            .one()
        )
        assert row["lifetime_savings_cents"] == 400
        assert row["rings_consumed"] == 2  # preserved


def test_batch_skips_users_with_no_accepted_receipt_scans(session_factory, make_user):
    """Users with only pending / rejected / electronic_label scans must not be processed."""
    user_id = make_user()
    with session_factory() as db:
        sid = _mk_store(db)
        _mk_product(db, "7000000000003")
        _mk_scan(db, user_id=user_id, store_id=sid, ean="7000000000003", price=100, status="pending")
        _mk_scan(
            db,
            user_id=user_id,
            store_id=sid,
            ean="7000000000003",
            price=100,
            status="accepted",
            scan_type="electronic_label",
        )

    with session_factory() as db:
        count = recompute_all_user_snapshots(db)
        assert count == 0

    with session_factory() as db:
        row = db.execute(
            text("SELECT COUNT(*) FROM user_savings_snapshot WHERE user_id=:u"),
            {"u": str(user_id)},
        ).scalar_one()
        assert row == 0


def test_batch_dry_run_writes_nothing(session_factory, make_user):
    user_id = make_user()
    with session_factory() as db:
        sid = _mk_store(db)
        _mk_product(db, "7000000000004")
        _mk_consensus(db, store_id=sid, ean="7000000000004", price=500)
        _mk_scan(db, user_id=user_id, store_id=sid, ean="7000000000004", price=100)

    with session_factory() as db:
        recompute_all_user_snapshots(db, dry_run=True)

    with session_factory() as db:
        row = db.execute(
            text("SELECT COUNT(*) FROM user_savings_snapshot WHERE user_id=:u"),
            {"u": str(user_id)},
        ).scalar_one()
        assert row == 0


def test_batch_commits_per_chunk_so_a_crash_keeps_partial_progress(session_factory, make_user, monkeypatch):
    """A crash mid-run must not throw away the chunks already processed —
    each chunk of users is committed independently."""
    import savings_batch

    users = [make_user() for _ in range(5)]
    with session_factory() as db:
        sid = _mk_store(db)
        _mk_product(db, "7000000000005")
        _mk_consensus(db, store_id=sid, ean="7000000000005", price=500)
        for uid in users:
            _mk_scan(db, user_id=uid, store_id=sid, ean="7000000000005", price=100)

    real_compute = savings_batch.compute_savings_for_user
    calls = {"n": 0}

    def _flaky_compute(db, uid, since=None):
        calls["n"] += 1
        if calls["n"] == 4:  # crash on the 4th user (second chunk)
            raise RuntimeError("simulated crash mid-run")
        return real_compute(db, uid, since=since)

    monkeypatch.setattr(savings_batch, "compute_savings_for_user", _flaky_compute)

    with session_factory() as db:
        try:
            recompute_all_user_snapshots(db, chunk_size=3)
        except RuntimeError:
            pass

    # First chunk (3 users) was committed before the crash in chunk 2.
    with session_factory() as db:
        persisted = db.execute(text("SELECT COUNT(*) FROM user_savings_snapshot")).scalar_one()
    assert persisted == 3
