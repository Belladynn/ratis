"""Tests for batch/purge.py — success + boundary-kept test per purge step."""

import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest
from purge import (
    aggregate_and_purge_sessions,
    expire_community_challenges,
    purge_label_images,
    purge_label_pending_orphans,
    purge_notification_logs,
    purge_optimized_routes,
    purge_photo_hashes,
    purge_receipt_images,
    purge_refresh_tokens,
)
from sqlalchemy import text

# ============================================================
# Helpers
# ============================================================


def _count(db, table: str, where: str, params: dict) -> int:
    return db.execute(text(f"SELECT COUNT(*) FROM {table} WHERE {where}"), params).scalar()


# ============================================================
# refresh_tokens
# ============================================================


def _insert_refresh_token(db, uid: uuid.UUID, expires_delta: timedelta, revoked_delta: timedelta | None = None) -> str:
    """Insert a refresh token. revoked_delta sets revoked_at relative to now (e.g. timedelta(days=-91))."""
    jti = str(uuid.uuid4())
    expires_at = datetime.now(UTC) + expires_delta
    revoked_at = datetime.now(UTC) + revoked_delta if revoked_delta is not None else None
    db.execute(
        text(
            "INSERT INTO refresh_tokens (id, jti, user_id, expires_at, revoked_at)"
            " VALUES (:id, :jti, :uid, :expires_at, :revoked_at)"
        ),
        {"id": str(uuid.uuid4()), "jti": jti, "uid": str(uid), "expires_at": expires_at, "revoked_at": revoked_at},
    )
    return jti


def test_purge_refresh_tokens_expired_deleted(session_factory, make_user):
    uid = make_user()
    with session_factory() as db:
        jti = _insert_refresh_token(db, uid, timedelta(hours=-1))
        db.commit()

    purge_refresh_tokens(session_factory, dry_run=False)

    with session_factory() as db:
        assert _count(db, "refresh_tokens", "jti = :jti", {"jti": jti}) == 0


def test_purge_refresh_tokens_old_revoked_deleted(session_factory, make_user):
    """Tokens revoked more than 90 days ago are purged."""
    uid = make_user()
    with session_factory() as db:
        jti = _insert_refresh_token(db, uid, timedelta(days=30), revoked_delta=timedelta(days=-91))
        db.commit()

    purge_refresh_tokens(session_factory, dry_run=False)

    with session_factory() as db:
        assert _count(db, "refresh_tokens", "jti = :jti", {"jti": jti}) == 0


def test_purge_refresh_tokens_recently_revoked_kept(session_factory, make_user):
    """Tokens revoked recently are kept for 90-day audit window."""
    uid = make_user()
    with session_factory() as db:
        jti = _insert_refresh_token(db, uid, timedelta(days=30), revoked_delta=timedelta(seconds=-1))
        db.commit()

    purge_refresh_tokens(session_factory, dry_run=False)

    with session_factory() as db:
        assert _count(db, "refresh_tokens", "jti = :jti", {"jti": jti}) == 1


def test_purge_refresh_tokens_valid_kept(session_factory, make_user):
    uid = make_user()
    with session_factory() as db:
        jti = _insert_refresh_token(db, uid, timedelta(days=30))
        db.commit()

    purge_refresh_tokens(session_factory, dry_run=False)

    with session_factory() as db:
        assert _count(db, "refresh_tokens", "jti = :jti", {"jti": jti}) == 1


def test_purge_refresh_tokens_dry_run_keeps_expired(session_factory, make_user):
    uid = make_user()
    with session_factory() as db:
        jti = _insert_refresh_token(db, uid, timedelta(hours=-1))
        db.commit()

    purge_refresh_tokens(session_factory, dry_run=True)

    with session_factory() as db:
        assert _count(db, "refresh_tokens", "jti = :jti", {"jti": jti}) == 1


# ============================================================
# optimized_routes
# ============================================================


def _make_route(session_factory, uid: uuid.UUID, expired: bool) -> uuid.UUID:
    list_id = uuid.uuid4()
    route_id = uuid.uuid4()
    expires_at = datetime.now(UTC) + (timedelta(hours=-1) if expired else timedelta(hours=12))
    # PG ``expires_after_computed`` : expires_at > computed_at. When the test
    # simulates an expired route, push computed_at back further so the
    # invariant holds.
    computed_at = expires_at - timedelta(hours=1)
    with session_factory() as db:
        db.execute(
            text("INSERT INTO shopping_lists (id, user_id, name, has_default_name) VALUES (:id, :uid, '', true)"),
            {"id": str(list_id), "uid": str(uid)},
        )
        db.execute(
            text(
                "INSERT INTO optimized_routes"
                " (id, user_id, list_id, total_price, total_savings, steps,"
                "  computed_at, expires_at)"
                " VALUES (:id, :uid, :lid, 10.00, 0.00, '{}'::jsonb,"
                "         :computed_at, :expires_at)"
            ),
            {
                "id": str(route_id),
                "uid": str(uid),
                "lid": str(list_id),
                "computed_at": computed_at,
                "expires_at": expires_at,
            },
        )
        db.commit()
    return route_id


def test_purge_optimized_routes_expired_deleted(session_factory, make_user):
    uid = make_user()
    route_id = _make_route(session_factory, uid, expired=True)

    purge_optimized_routes(session_factory, dry_run=False)

    with session_factory() as db:
        assert _count(db, "optimized_routes", "id = :id", {"id": str(route_id)}) == 0


def test_purge_optimized_routes_valid_kept(session_factory, make_user):
    uid = make_user()
    route_id = _make_route(session_factory, uid, expired=False)

    purge_optimized_routes(session_factory, dry_run=False)

    with session_factory() as db:
        assert _count(db, "optimized_routes", "id = :id", {"id": str(route_id)}) == 1


# ============================================================
# notification_logs
# ============================================================


def test_purge_notification_logs_old_deleted(session_factory, make_user):
    uid = make_user()
    nid = uuid.uuid4()
    with session_factory() as db:
        db.execute(
            text(
                "INSERT INTO notification_logs (id, user_id, type, sent_at)"
                " VALUES (:id, :uid, 'price_alert', now() - interval '91 days')"
            ),
            {"id": str(nid), "uid": str(uid)},
        )
        db.commit()

    purge_notification_logs(session_factory, dry_run=False)

    with session_factory() as db:
        assert _count(db, "notification_logs", "id = :id", {"id": str(nid)}) == 0


def test_purge_notification_logs_recent_kept(session_factory, make_user):
    uid = make_user()
    nid = uuid.uuid4()
    with session_factory() as db:
        db.execute(
            text(
                "INSERT INTO notification_logs (id, user_id, type, sent_at)"
                " VALUES (:id, :uid, 'price_alert', now() - interval '1 day')"
            ),
            {"id": str(nid), "uid": str(uid)},
        )
        db.commit()

    purge_notification_logs(session_factory, dry_run=False)

    with session_factory() as db:
        assert _count(db, "notification_logs", "id = :id", {"id": str(nid)}) == 1


# ============================================================
# user_sessions -> user_session_stats
# ============================================================


def test_aggregate_old_sessions_purged_and_counted(session_factory, make_user):
    uid = make_user()
    with session_factory() as db:
        for platform in ("ios", "ios", "android"):
            db.execute(
                text(
                    "INSERT INTO user_sessions (id, user_id, platform, started_at)"
                    " VALUES (:id, :uid, :platform, now() - interval '91 days')"
                ),
                {"id": str(uuid.uuid4()), "uid": str(uid), "platform": platform},
            )
        db.commit()

    aggregate_and_purge_sessions(session_factory, dry_run=False)

    with session_factory() as db:
        assert _count(db, "user_sessions", "user_id = :uid", {"uid": str(uid)}) == 0
        row = db.execute(
            text("SELECT ios_count, android_count, web_count FROM user_session_stats WHERE user_id = :uid"),
            {"uid": str(uid)},
        ).one()
        assert row.ios_count == 2
        assert row.android_count == 1
        assert row.web_count == 0


def test_aggregate_upsert_adds_to_existing_stats(session_factory, make_user):
    uid = make_user()
    session_date = datetime.now(UTC) - timedelta(days=91)
    with session_factory() as db:
        # Pre-existing stats for the same period as the old session
        db.execute(
            text(
                "INSERT INTO user_session_stats"
                " (user_id, period_year, period_month, ios_count, android_count, web_count)"
                " VALUES (:uid, :year, :month, 5, 0, 0)"
            ),
            {"uid": str(uid), "year": session_date.year, "month": session_date.month},
        )
        db.execute(
            text(
                "INSERT INTO user_sessions (id, user_id, platform, started_at) VALUES (:id, :uid, 'ios', :started_at)"
            ),
            {"id": str(uuid.uuid4()), "uid": str(uid), "started_at": session_date},
        )
        db.commit()

    aggregate_and_purge_sessions(session_factory, dry_run=False)

    with session_factory() as db:
        row = db.execute(
            text(
                "SELECT ios_count FROM user_session_stats"
                " WHERE user_id = :uid AND period_year = :year AND period_month = :month"
            ),
            {"uid": str(uid), "year": session_date.year, "month": session_date.month},
        ).one()
        assert row.ios_count == 6  # 5 pre-existing + 1 aggregated


def test_aggregate_recent_sessions_kept(session_factory, make_user):
    uid = make_user()
    with session_factory() as db:
        db.execute(
            text(
                "INSERT INTO user_sessions (id, user_id, platform, started_at)"
                " VALUES (:id, :uid, 'web', now() - interval '1 day')"
            ),
            {"id": str(uuid.uuid4()), "uid": str(uid)},
        )
        db.commit()

    aggregate_and_purge_sessions(session_factory, dry_run=False)

    with session_factory() as db:
        assert _count(db, "user_sessions", "user_id = :uid", {"uid": str(uid)}) == 1


# ============================================================
# photo_hashes purge
# ============================================================


def _insert_store(db) -> uuid.UUID:
    sid = uuid.uuid4()
    db.execute(
        text("""
        INSERT INTO stores (id, name, retailer, address, city, postal_code, lat, lng, is_disabled)
        VALUES (:id, 'Test', 'test', '1 rue', 'Paris', '75001', 48.85, 2.35, false)
    """),
        {"id": str(sid)},
    )
    db.commit()
    return sid


def _insert_receipt(db, uid, store_id, *, photo_hash=None, age_hours=2) -> uuid.UUID:
    rid = uuid.uuid4()
    db.execute(
        text("""
        INSERT INTO receipts (id, user_id, store_id, purchased_at, created_at, updated_at, photo_hash)
        VALUES (:id, :uid, :sid, now()::date,
                now() - :age * interval '1 hour',
                now() - :age * interval '1 hour',
                :hash)
    """),
        {"id": str(rid), "uid": str(uid), "sid": str(store_id), "age": age_hours, "hash": photo_hash},
    )
    db.commit()
    return rid


def _insert_label_scan(db, uid, store_id, *, photo_hash=None, age_hours=2, status="pending") -> uuid.UUID:
    sid = uuid.uuid4()
    db.execute(
        text("""
        INSERT INTO scans
            (id, user_id, store_id, scan_type, status, price, quantity, scanned_at, status_updated_at, photo_hash)
        VALUES (:id, :uid, :sid, 'electronic_label', :status, 0, 1,
                now() - :age * interval '1 hour',
                now() - :age * interval '1 hour',
                :hash)
    """),
        {"id": str(sid), "uid": str(uid), "sid": str(store_id), "status": status, "age": age_hours, "hash": photo_hash},
    )
    db.commit()
    return sid


class TestPurgePhotoHashes:
    def test_stuck_receipt_hash_cleared(self, session_factory, make_user):
        uid = make_user()
        with session_factory() as db:
            store_id = _insert_store(db)
            rid = _insert_receipt(db, uid, store_id, photo_hash="a" * 64, age_hours=2)

        purge_photo_hashes(session_factory, dry_run=False)

        with session_factory() as db:
            row = db.execute(text("SELECT photo_hash FROM receipts WHERE id = :id"), {"id": str(rid)}).one()
            assert row.photo_hash is None

    def test_recent_receipt_hash_kept(self, session_factory, make_user):
        uid = make_user()
        with session_factory() as db:
            store_id = _insert_store(db)
            rid = _insert_receipt(db, uid, store_id, photo_hash="b" * 64, age_hours=0)

        purge_photo_hashes(session_factory, dry_run=False)

        with session_factory() as db:
            row = db.execute(text("SELECT photo_hash FROM receipts WHERE id = :id"), {"id": str(rid)}).one()
            assert row.photo_hash == "b" * 64

    def test_receipt_with_terminal_scan_hash_kept(self, session_factory, make_user):
        """Receipt whose OCR completed (has a non-pending scan) must keep its hash."""
        uid = make_user()
        with session_factory() as db:
            store_id = _insert_store(db)
            rid = _insert_receipt(db, uid, store_id, photo_hash="c" * 64, age_hours=2)
            db.execute(
                text("""
                INSERT INTO scans
                    (id, user_id, store_id, receipt_id, scan_type, status, price, quantity,
                     scanned_at, status_updated_at)
                VALUES (:id, :uid, :sid, :rid, 'receipt', 'accepted', 100, 1, now(), now())
            """),
                {"id": str(uuid.uuid4()), "uid": str(uid), "sid": str(store_id), "rid": str(rid)},
            )
            db.commit()

        purge_photo_hashes(session_factory, dry_run=False)

        with session_factory() as db:
            row = db.execute(text("SELECT photo_hash FROM receipts WHERE id = :id"), {"id": str(rid)}).one()
            assert row.photo_hash == "c" * 64

    def test_stuck_label_scan_hash_cleared(self, session_factory, make_user):
        uid = make_user()
        with session_factory() as db:
            store_id = _insert_store(db)
            scan_id = _insert_label_scan(db, uid, store_id, photo_hash="d" * 64, age_hours=2)

        purge_photo_hashes(session_factory, dry_run=False)

        with session_factory() as db:
            row = db.execute(text("SELECT photo_hash FROM scans WHERE id = :id"), {"id": str(scan_id)}).one()
            assert row.photo_hash is None

    def test_recent_label_scan_hash_kept(self, session_factory, make_user):
        uid = make_user()
        with session_factory() as db:
            store_id = _insert_store(db)
            scan_id = _insert_label_scan(db, uid, store_id, photo_hash="e" * 64, age_hours=0)

        purge_photo_hashes(session_factory, dry_run=False)

        with session_factory() as db:
            row = db.execute(text("SELECT photo_hash FROM scans WHERE id = :id"), {"id": str(scan_id)}).one()
            assert row.photo_hash == "e" * 64

    def test_accepted_label_scan_hash_kept(self, session_factory, make_user):
        """Accepted label scan must keep its hash (permanent dedup)."""
        uid = make_user()
        with session_factory() as db:
            store_id = _insert_store(db)
            scan_id = _insert_label_scan(db, uid, store_id, photo_hash="f" * 64, age_hours=2, status="accepted")

        purge_photo_hashes(session_factory, dry_run=False)

        with session_factory() as db:
            row = db.execute(text("SELECT photo_hash FROM scans WHERE id = :id"), {"id": str(scan_id)}).one()
            assert row.photo_hash == "f" * 64

    def test_dry_run_does_not_clear(self, session_factory, make_user):
        uid = make_user()
        with session_factory() as db:
            store_id = _insert_store(db)
            rid = _insert_receipt(db, uid, store_id, photo_hash="a" * 64, age_hours=2)

        purge_photo_hashes(session_factory, dry_run=True)

        with session_factory() as db:
            row = db.execute(text("SELECT photo_hash FROM receipts WHERE id = :id"), {"id": str(rid)}).one()
            assert row.photo_hash == "a" * 64


# ============================================================
# Helpers for R2 / community_challenges / label orphan tests
# ============================================================


def _insert_receipt_with_image(db, uid, store_id, *, age_hours=49, deleted=False) -> tuple[uuid.UUID, str]:
    """Insert a receipt with an R2 image. Returns (receipt_id, r2_key)."""
    rid = uuid.uuid4()
    key = f"receipt/{rid}.jpg"
    image_deleted_at = datetime.now(UTC) if deleted else None
    db.execute(
        text("""
        INSERT INTO receipts (id, user_id, store_id, purchased_at, created_at, updated_at,
                              image_r2_key, image_uploaded_at, image_deleted_at)
        VALUES (:id, :uid, :sid, now()::date,
                now() - :age * interval '1 hour',
                now() - :age * interval '1 hour',
                :key,
                now() - :age * interval '1 hour',
                :image_deleted_at)
    """),
        {
            "id": str(rid),
            "uid": str(uid),
            "sid": str(store_id),
            "age": age_hours,
            "key": key,
            "image_deleted_at": image_deleted_at,
        },
    )
    db.commit()
    return rid, key


def _insert_label_scan_with_image(
    db, uid, store_id, *, age_hours=2, expires_hours_from_now=-1
) -> tuple[uuid.UUID, str]:
    """Insert a label scan with an R2 key and expiry. Returns (scan_id, r2_key)."""
    sid = uuid.uuid4()
    key = f"label/{sid}.jpg"
    db.execute(
        text("""
        INSERT INTO scans
            (id, user_id, store_id, scan_type, status, price, quantity,
             scanned_at, status_updated_at, label_r2_key, label_image_expires_at)
        VALUES (:id, :uid, :sid, 'electronic_label', 'accepted', 0, 1,
                now() - :age * interval '1 hour',
                now() - :age * interval '1 hour',
                :key,
                now() + :expires * interval '1 hour')
    """),
        {
            "id": str(sid),
            "uid": str(uid),
            "sid": str(store_id),
            "age": age_hours,
            "key": key,
            "expires": expires_hours_from_now,
        },
    )
    db.commit()
    return sid, key


def _insert_community_challenge(db, *, ended_hours_ago=100, grace_days=3, is_active=True) -> uuid.UUID:
    cid = uuid.uuid4()
    db.execute(
        text("""
        INSERT INTO community_challenges
            (id, title, action_type, objective, starts_at, ends_at, grace_period_days, is_active)
        VALUES (:id, 'Test Challenge', 'scan', 10,
                now() - interval '10 days',
                now() - :ended * interval '1 hour',
                :grace, :active)
    """),
        {"id": str(cid), "ended": ended_hours_ago, "grace": grace_days, "active": is_active},
    )
    db.commit()
    return cid


# ============================================================
# purge_receipt_images
# ============================================================


class TestPurgeReceiptImages:
    def test_old_image_deleted_from_r2_and_marked(self, session_factory, make_user):
        uid = make_user()
        with session_factory() as db:
            store_id = _insert_store(db)
            rid, key = _insert_receipt_with_image(db, uid, store_id, age_hours=49)

        mock_client = MagicMock()
        with patch("purge._get_r2_client", return_value=mock_client):
            purge_receipt_images(session_factory, dry_run=False)

        mock_client.delete_object.assert_called_once()
        assert mock_client.delete_object.call_args.kwargs["Key"] == key

        with session_factory() as db:
            row = db.execute(text("SELECT image_deleted_at FROM receipts WHERE id = :id"), {"id": str(rid)}).one()
            assert row.image_deleted_at is not None

    def test_recent_image_not_deleted(self, session_factory, make_user):
        uid = make_user()
        with session_factory() as db:
            store_id = _insert_store(db)
            rid, _key = _insert_receipt_with_image(db, uid, store_id, age_hours=1)

        mock_client = MagicMock()
        with patch("purge._get_r2_client", return_value=mock_client):
            purge_receipt_images(session_factory, dry_run=False)

        mock_client.delete_object.assert_not_called()

        with session_factory() as db:
            row = db.execute(text("SELECT image_deleted_at FROM receipts WHERE id = :id"), {"id": str(rid)}).one()
            assert row.image_deleted_at is None

    def test_already_deleted_not_reprocessed(self, session_factory, make_user):
        uid = make_user()
        with session_factory() as db:
            store_id = _insert_store(db)
            _rid, _ = _insert_receipt_with_image(db, uid, store_id, age_hours=49, deleted=True)

        mock_client = MagicMock()
        with patch("purge._get_r2_client", return_value=mock_client):
            purge_receipt_images(session_factory, dry_run=False)

        mock_client.delete_object.assert_not_called()

    def test_dry_run_does_not_delete(self, session_factory, make_user):
        uid = make_user()
        with session_factory() as db:
            store_id = _insert_store(db)
            rid, _ = _insert_receipt_with_image(db, uid, store_id, age_hours=49)

        mock_client = MagicMock()
        with patch("purge._get_r2_client", return_value=mock_client):
            purge_receipt_images(session_factory, dry_run=True)

        mock_client.delete_object.assert_not_called()

        with session_factory() as db:
            row = db.execute(text("SELECT image_deleted_at FROM receipts WHERE id = :id"), {"id": str(rid)}).one()
            assert row.image_deleted_at is None

    def test_r2_failure_raises_so_run_is_marked_failed(self, session_factory, make_user):
        """A durable R2 delete failure must surface as an error (RGPD retention
        breach), not be swallowed as a warning."""
        uid = make_user()
        with session_factory() as db:
            store_id = _insert_store(db)
            rid, _ = _insert_receipt_with_image(db, uid, store_id, age_hours=49)

        mock_client = MagicMock()
        mock_client.delete_object.side_effect = RuntimeError("R2 unreachable")
        with patch("purge._get_r2_client", return_value=mock_client):
            with pytest.raises(RuntimeError, match="receipt image"):
                purge_receipt_images(session_factory, dry_run=False)

        # The row stays un-marked — image_deleted_at NULL means PII still on R2.
        with session_factory() as db:
            row = db.execute(text("SELECT image_deleted_at FROM receipts WHERE id = :id"), {"id": str(rid)}).one()
            assert row.image_deleted_at is None


# ============================================================
# purge_label_images
# ============================================================


class TestPurgeLabelImages:
    def test_expired_label_image_deleted_and_key_cleared(self, session_factory, make_user):
        uid = make_user()
        with session_factory() as db:
            store_id = _insert_store(db)
            scan_id, key = _insert_label_scan_with_image(db, uid, store_id, expires_hours_from_now=-1)

        mock_client = MagicMock()
        with patch("purge._get_r2_client", return_value=mock_client):
            purge_label_images(session_factory, dry_run=False)

        mock_client.delete_object.assert_called_once()
        assert mock_client.delete_object.call_args.kwargs["Key"] == key

        with session_factory() as db:
            row = db.execute(text("SELECT label_r2_key FROM scans WHERE id = :id"), {"id": str(scan_id)}).one()
            assert row.label_r2_key is None

    def test_not_yet_expired_label_image_kept(self, session_factory, make_user):
        uid = make_user()
        with session_factory() as db:
            store_id = _insert_store(db)
            scan_id, _ = _insert_label_scan_with_image(db, uid, store_id, expires_hours_from_now=10)

        mock_client = MagicMock()
        with patch("purge._get_r2_client", return_value=mock_client):
            purge_label_images(session_factory, dry_run=False)

        mock_client.delete_object.assert_not_called()

        with session_factory() as db:
            row = db.execute(text("SELECT label_r2_key FROM scans WHERE id = :id"), {"id": str(scan_id)}).one()
            assert row.label_r2_key is not None

    def test_dry_run_does_not_delete_label_image(self, session_factory, make_user):
        uid = make_user()
        with session_factory() as db:
            store_id = _insert_store(db)
            scan_id, _ = _insert_label_scan_with_image(db, uid, store_id, expires_hours_from_now=-1)

        mock_client = MagicMock()
        with patch("purge._get_r2_client", return_value=mock_client):
            purge_label_images(session_factory, dry_run=True)

        mock_client.delete_object.assert_not_called()

        with session_factory() as db:
            row = db.execute(text("SELECT label_r2_key FROM scans WHERE id = :id"), {"id": str(scan_id)}).one()
            assert row.label_r2_key is not None

    def test_r2_failure_raises_so_run_is_marked_failed(self, session_factory, make_user):
        """A durable R2 delete failure on label images must surface as an error."""
        uid = make_user()
        with session_factory() as db:
            store_id = _insert_store(db)
            scan_id, _ = _insert_label_scan_with_image(db, uid, store_id, expires_hours_from_now=-1)

        mock_client = MagicMock()
        mock_client.delete_object.side_effect = RuntimeError("R2 unreachable")
        with patch("purge._get_r2_client", return_value=mock_client):
            with pytest.raises(RuntimeError, match="label image"):
                purge_label_images(session_factory, dry_run=False)

        with session_factory() as db:
            row = db.execute(text("SELECT label_r2_key FROM scans WHERE id = :id"), {"id": str(scan_id)}).one()
            assert row.label_r2_key is not None


# ============================================================
# expire_community_challenges
# ============================================================


class TestExpireCommunityChallenges:
    def test_expired_challenge_deactivated(self, session_factory):
        with session_factory() as db:
            # ended 100h ago, grace 3 days = 72h → 100h > 72h → should deactivate
            cid = _insert_community_challenge(db, ended_hours_ago=100, grace_days=3, is_active=True)

        expire_community_challenges(session_factory, dry_run=False)

        with session_factory() as db:
            row = db.execute(text("SELECT is_active FROM community_challenges WHERE id = :id"), {"id": str(cid)}).one()
            assert row.is_active is False

    def test_within_grace_period_not_deactivated(self, session_factory):
        with session_factory() as db:
            # ended 48h ago, grace 3 days = 72h → 48h < 72h → keep active
            cid = _insert_community_challenge(db, ended_hours_ago=48, grace_days=3, is_active=True)

        expire_community_challenges(session_factory, dry_run=False)

        with session_factory() as db:
            row = db.execute(text("SELECT is_active FROM community_challenges WHERE id = :id"), {"id": str(cid)}).one()
            assert row.is_active is True

    def test_already_inactive_unchanged(self, session_factory):
        with session_factory() as db:
            cid = _insert_community_challenge(db, ended_hours_ago=200, grace_days=3, is_active=False)

        expire_community_challenges(session_factory, dry_run=False)

        with session_factory() as db:
            row = db.execute(text("SELECT is_active FROM community_challenges WHERE id = :id"), {"id": str(cid)}).one()
            assert row.is_active is False

    def test_dry_run_does_not_deactivate(self, session_factory):
        with session_factory() as db:
            cid = _insert_community_challenge(db, ended_hours_ago=100, grace_days=3, is_active=True)

        expire_community_challenges(session_factory, dry_run=True)

        with session_factory() as db:
            row = db.execute(text("SELECT is_active FROM community_challenges WHERE id = :id"), {"id": str(cid)}).one()
            assert row.is_active is True


# ============================================================
# purge_label_pending_orphans
# ============================================================


class TestPurgeLabelPendingOrphans:
    def test_old_pending_label_scan_rejected(self, session_factory, make_user):
        uid = make_user()
        with session_factory() as db:
            store_id = _insert_store(db)
            scan_id = _insert_label_scan(db, uid, store_id, age_hours=3, status="pending")

        purge_label_pending_orphans(session_factory, dry_run=False)

        with session_factory() as db:
            row = db.execute(
                text("SELECT status, rejected_reason, status_updated_at FROM scans WHERE id = :id"),
                {"id": str(scan_id)},
            ).one()
            assert row.status == "rejected"
            assert row.rejected_reason == "ocr_timeout"
            assert row.status_updated_at >= datetime.now(UTC) - timedelta(seconds=5)

    def test_recent_pending_label_scan_kept(self, session_factory, make_user):
        uid = make_user()
        with session_factory() as db:
            store_id = _insert_store(db)
            scan_id = _insert_label_scan(db, uid, store_id, age_hours=1, status="pending")

        purge_label_pending_orphans(session_factory, dry_run=False)

        with session_factory() as db:
            row = db.execute(text("SELECT status FROM scans WHERE id = :id"), {"id": str(scan_id)}).one()
            assert row.status == "pending"

    def test_accepted_label_scan_not_rejected(self, session_factory, make_user):
        uid = make_user()
        with session_factory() as db:
            store_id = _insert_store(db)
            scan_id = _insert_label_scan(db, uid, store_id, age_hours=3, status="accepted")

        purge_label_pending_orphans(session_factory, dry_run=False)

        with session_factory() as db:
            row = db.execute(text("SELECT status FROM scans WHERE id = :id"), {"id": str(scan_id)}).one()
            assert row.status == "accepted"

    def test_dry_run_does_not_reject(self, session_factory, make_user):
        uid = make_user()
        with session_factory() as db:
            store_id = _insert_store(db)
            scan_id = _insert_label_scan(db, uid, store_id, age_hours=3, status="pending")

        purge_label_pending_orphans(session_factory, dry_run=True)

        with session_factory() as db:
            row = db.execute(text("SELECT status FROM scans WHERE id = :id"), {"id": str(scan_id)}).one()
            assert row.status == "pending"


# ============================================================
# unknown_scans — Part B retention purge
# ============================================================

from purge import purge_unknown_scans


def _insert_unknown_scan(db, uid: uuid.UUID, *, scan_type: str, days_ago: int) -> uuid.UUID:
    sid = uuid.uuid4()
    scanned_at = datetime.now(UTC) - timedelta(days=days_ago)
    # CHECK ``manual_no_scanned_name`` : manual scans MUST have
    # ``scanned_name IS NULL`` + ``product_ean NOT NULL``. Other types
    # carry the test sentinel label.
    if scan_type == "manual":
        scanned_name = None
        # FK : real EAN required. Seed lazily — purge test only checks
        # ``scans`` is deleted, the seeded product is rolled back with
        # the savepoint.
        ean = f"{uuid.uuid4().int:013d}"[:13]
        db.execute(
            text(
                "INSERT INTO products (ean, name, source, created_at, updated_at) "
                "VALUES (:e, 'bug6', 'off', now(), now()) "
                "ON CONFLICT (ean) DO NOTHING"
            ),
            {"e": ean},
        )
    else:
        scanned_name = "SCAN"
        ean = None
    db.execute(
        text(
            "INSERT INTO scans "
            "(id, user_id, store_id, store_status, scan_type, scanned_name, "
            " product_ean, price, quantity, status, user_lat, user_lng, "
            " scanned_at) "
            "VALUES (:id, :uid, NULL, 'unknown', :stype, :name, "
            "        :ean, 100, 1, 'pending', 48.86, 2.36, :at)"
        ),
        {
            "id": str(sid),
            "uid": str(uid),
            "stype": scan_type,
            "name": scanned_name,
            "ean": ean,
            "at": scanned_at,
        },
    )
    return sid


def test_purge_unknown_scans_deletes_old_and_aggregates(session_factory, make_user):
    uid = make_user()
    with session_factory() as db:
        # 2 old electronic_label scans + 1 old manual → should be purged + aggregated
        _insert_unknown_scan(db, uid, scan_type="electronic_label", days_ago=10)
        _insert_unknown_scan(db, uid, scan_type="electronic_label", days_ago=12)
        _insert_unknown_scan(db, uid, scan_type="manual", days_ago=14)
        # 1 recent scan — must survive
        recent = _insert_unknown_scan(db, uid, scan_type="electronic_label", days_ago=2)
        db.commit()

    purge_unknown_scans(session_factory, dry_run=False)

    with session_factory() as db:
        # Recent scan kept
        assert _count(db, "scans", "id = :id", {"id": str(recent)}) == 1
        # Old scans deleted
        assert _count(db, "scans", "store_status = 'unknown' AND scanned_at < now() - interval '7 days'", {}) == 0
        # Aggregate populated
        total = db.execute(
            text("SELECT COALESCE(SUM(scan_count), 0)::int FROM unknown_scans_weekly_aggregate")
        ).scalar()
        assert total == 3


def test_purge_unknown_scans_dry_run_does_not_delete(session_factory, make_user):
    uid = make_user()
    with session_factory() as db:
        sid = _insert_unknown_scan(db, uid, scan_type="electronic_label", days_ago=10)
        db.commit()

    purge_unknown_scans(session_factory, dry_run=True)

    with session_factory() as db:
        assert _count(db, "scans", "id = :id", {"id": str(sid)}) == 1


def test_purge_unknown_scans_ignores_confirmed_scans(session_factory, make_user):
    """Only store_status='unknown' scans are eligible — confirmed ones stay."""
    uid = make_user()
    # Create a store so we can write a confirmed scan
    store_id = uuid.uuid4()
    with session_factory() as db:
        db.execute(
            text(
                "INSERT INTO stores (id, name, retailer, lat, lng, is_disabled) "
                "VALUES (:id, 'Test', 'test', 48.86, 2.36, false)"
            ),
            {"id": str(store_id)},
        )
        sid = uuid.uuid4()
        db.execute(
            text(
                "INSERT INTO scans "
                "(id, user_id, store_id, store_status, scan_type, scanned_name, "
                " price, quantity, status, scanned_at) "
                "VALUES (:id, :uid, :sid, 'confirmed', 'electronic_label', 'X', "
                "        100, 1, 'pending', now() - interval '30 days')"
            ),
            {"id": str(sid), "uid": str(uid), "sid": str(store_id)},
        )
        db.commit()

    purge_unknown_scans(session_factory, dry_run=False)

    with session_factory() as db:
        assert _count(db, "scans", "id = :id", {"id": str(sid)}) == 1


# ============================================================
# scan_debug — alpha debug instrumentation purge (PR #126)
# ============================================================

from purge import purge_scan_debug


def _insert_scan_debug(
    db,
    uid: uuid.UUID,
    *,
    purge_after_delta: timedelta,
    processed_image_r2_key: str | None = None,
    processed_images_r2_keys: dict | None = None,
) -> tuple[uuid.UUID, uuid.UUID]:
    """Insert a receipt + a scan_debug row whose purge_after is now()+delta.

    Returns (debug_id, receipt_id). PR #132 schema : scan_debug is
    anchored on receipt_id ; the row is keyed by its own UUID id.
    """
    import json as _json

    receipt_id = uuid.uuid4()
    db.execute(
        text(
            "INSERT INTO receipts (id, user_id, store_id, purchased_at, image_r2_key) "
            "VALUES (:rid, :uid, NULL, current_date, :ikey)"
        ),
        {"rid": str(receipt_id), "uid": str(uid), "ikey": f"receipts/{receipt_id}.jpg"},
    )
    debug_id = uuid.uuid4()
    purge_after = datetime.now(UTC) + purge_after_delta
    db.execute(
        text(
            "INSERT INTO scan_debug "
            "(id, receipt_id, scan_id, rich_blocks, "
            " processed_image_r2_key, processed_images_r2_keys, purge_after) "
            "VALUES (:id, :rid, NULL, '[]'::jsonb, "
            "        :legacy_key, CAST(:keys_map AS jsonb), :pa)"
        ),
        {
            "id": str(debug_id),
            "rid": str(receipt_id),
            "legacy_key": processed_image_r2_key,
            "keys_map": (_json.dumps(processed_images_r2_keys) if processed_images_r2_keys is not None else None),
            "pa": purge_after,
        },
    )
    return debug_id, receipt_id


def test_purge_scan_debug_deletes_expired_rows(session_factory, make_user):
    """PR #132 — purges all 4 R2 keys from the JSONB map, not just one."""
    uid = make_user()
    with session_factory() as db:
        old_id, _old_receipt = _insert_scan_debug(
            db,
            uid,
            purge_after_delta=timedelta(hours=-1),
            processed_images_r2_keys={
                "corrected": f"debug/{uuid.uuid4()}.corrected.jpg",
                "clahe": f"debug/{uuid.uuid4()}.clahe.jpg",
                "binarized": f"debug/{uuid.uuid4()}.binarized.jpg",
                "inverted": f"debug/{uuid.uuid4()}.inverted.jpg",
            },
        )
        recent_id, _ = _insert_scan_debug(db, uid, purge_after_delta=timedelta(hours=24))
        db.commit()

    with patch("purge._get_r2_client") as get_client:
        client = MagicMock()
        get_client.return_value = client
        purge_scan_debug(session_factory, dry_run=False)
        # R2 delete invoked once per pass image of the expired row.
        assert client.delete_object.call_count == 4
        deleted_keys = {kw["Key"] for _, kw in client.delete_object.call_args_list}
        assert all(k.startswith("debug/") for k in deleted_keys)
        assert any(k.endswith(".corrected.jpg") for k in deleted_keys)
        assert any(k.endswith(".clahe.jpg") for k in deleted_keys)
        assert any(k.endswith(".binarized.jpg") for k in deleted_keys)
        assert any(k.endswith(".inverted.jpg") for k in deleted_keys)

    with session_factory() as db:
        assert _count(db, "scan_debug", "id = :id", {"id": str(old_id)}) == 0
        assert _count(db, "scan_debug", "id = :id", {"id": str(recent_id)}) == 1


def test_purge_scan_debug_legacy_single_key_back_compat(session_factory, make_user):
    """Rows written before PR #132 (no JSONB, only the legacy column)
    must still get their R2 image deleted by the purge."""
    uid = make_user()
    with session_factory() as db:
        old_id, _ = _insert_scan_debug(
            db,
            uid,
            purge_after_delta=timedelta(hours=-1),
            processed_image_r2_key="debug/legacy.processed.jpg",
            processed_images_r2_keys=None,
        )
        db.commit()

    with patch("purge._get_r2_client") as get_client:
        client = MagicMock()
        get_client.return_value = client
        purge_scan_debug(session_factory, dry_run=False)
        client.delete_object.assert_called_once()
        _, kwargs = client.delete_object.call_args
        assert kwargs["Key"] == "debug/legacy.processed.jpg"

    with session_factory() as db:
        assert _count(db, "scan_debug", "id = :id", {"id": str(old_id)}) == 0


def test_purge_scan_debug_dry_run_keeps_rows(session_factory, make_user):
    uid = make_user()
    with session_factory() as db:
        debug_id, _ = _insert_scan_debug(db, uid, purge_after_delta=timedelta(hours=-1))
        db.commit()

    with patch("purge._get_r2_client") as get_client:
        purge_scan_debug(session_factory, dry_run=True)
        get_client.assert_not_called()

    with session_factory() as db:
        assert _count(db, "scan_debug", "id = :id", {"id": str(debug_id)}) == 1


# ============================================================
# main() — env validation
# ============================================================

import purge as _purge


class TestMainEnvValidation:
    """R2 env vars must be validated up-front, not lazily mid-run."""

    def test_main_validates_r2_env_before_running_steps(self, monkeypatch):
        """A missing R2 var must fail fast — before any step commits — so the
        run never half-completes with a KeyError mid-flight."""
        monkeypatch.delenv("R2_ENDPOINT_URL", raising=False)
        monkeypatch.setattr("sys.argv", ["purge.py"])

        ran_steps: list[str] = []
        monkeypatch.setattr(
            _purge,
            "STEPS",
            [("sentinel", lambda *a, **k: ran_steps.append("sentinel"))],
        )

        with pytest.raises(RuntimeError, match="R2_ENDPOINT_URL"):
            _purge.main()

        assert ran_steps == []  # fail-fast — no step ran

    def test_main_dry_run_skips_r2_env_validation(self, monkeypatch):
        """--dry-run never touches R2, so a missing R2 var must not block it."""
        monkeypatch.delenv("R2_ENDPOINT_URL", raising=False)
        monkeypatch.setattr("sys.argv", ["purge.py", "--dry-run"])

        ran_steps: list[str] = []
        monkeypatch.setattr(
            _purge,
            "STEPS",
            [("sentinel", lambda *a, **k: ran_steps.append("sentinel"))],
        )

        _purge.main()  # must not raise

        assert ran_steps == ["sentinel"]
