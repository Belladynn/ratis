"""Tests for the PA admin pipeline stats endpoint (PR8).

Covers ``GET /api/v1/admin/pipeline/stats`` :

- Default window (last 7 days)
- Empty DB returns zeros
- Match-rate calculation across matched / unresolved / rejected
- Top rejected reasons aggregation
- Match-method distribution
- Store-status distribution
- Window honoring (scans outside the window excluded)
- Validation : ``from > to`` → 422

Uses the parent conftest fixtures (``db``, ``admin_client``, ``store``,
``user``).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import text


def _insert_scan(
    db,
    *,
    user_id: uuid.UUID,
    store_id: uuid.UUID,
    status: str,
    match_method: str | None = None,
    rejected_reason: str | None = None,
    product_ean: str | None = None,
    scanned_at: datetime | None = None,
    store_status: str = "confirmed",
    scan_type: str = "receipt",
    receipt_id: uuid.UUID | None = None,
) -> uuid.UUID:
    """Insert a scan row at a precise scanned_at — for windowing tests.

    Mirrors the schema CHECK constraints :
      - matched ⇒ ean + match_method NOT NULL
      - unresolved/rejected ⇒ rejected_reason NOT NULL
      - scan_type='receipt' ⇒ receipt_id NOT NULL
    """
    if scanned_at is None:
        scanned_at = datetime.now(UTC)

    # Receipt rows for receipt-typed scans (CHECK constraint).
    if scan_type == "receipt" and receipt_id is None:
        receipt_id = uuid.uuid4()
        db.execute(
            text(
                "INSERT INTO receipts "
                "  (id, store_id, purchased_at, image_r2_key) "
                "VALUES (:id, :sid, :purchased_at, :key)"
            ),
            {
                "id": receipt_id,
                "sid": store_id,
                "purchased_at": scanned_at.date(),
                "key": f"fake-receipt-{receipt_id.hex[:8]}.jpg",
            },
        )

    scan_id = uuid.uuid4()
    db.execute(
        text(
            "INSERT INTO scans "
            "  (id, user_id, store_id, product_ean, scanned_name, price, "
            "   quantity, scan_type, receipt_id, status, match_method, "
            "   rejected_reason, scanned_at, status_updated_at, store_status) "
            "VALUES (:id, :uid, :sid, :ean, 'TEST', 250, 1, :stype, :rid, "
            "        :status, :mm, :rr, :ts, :ts, :ss)"
        ),
        {
            "id": scan_id,
            "uid": user_id,
            "sid": store_id if store_status != "unknown" else None,
            "ean": product_ean,
            "stype": scan_type,
            "rid": receipt_id,
            "status": status,
            "mm": match_method,
            "rr": rejected_reason,
            "ts": scanned_at,
            "ss": store_status,
        },
    )
    db.commit()
    return scan_id


# ---------------------------------------------------------------------------
# Default behaviour
# ---------------------------------------------------------------------------
class TestDefaults:
    def test_empty_db_returns_zero_summary(self, admin_client, db):
        resp = admin_client.get("/api/v1/admin/pipeline/stats")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        s = body["summary"]
        assert s["scan_count"] == 0
        assert s["matched_count"] == 0
        assert s["unresolved_count"] == 0
        assert s["rejected_count"] == 0
        assert s["match_rate_pct"] == 0.0
        assert body["top_rejected_reasons"] == []
        assert body["by_match_method"] == []
        assert body["by_store_status"] == {"confirmed": 0, "pending": 0, "unknown": 0}

    def test_default_window_is_7_days(self, admin_client, db):
        resp = admin_client.get("/api/v1/admin/pipeline/stats")
        assert resp.status_code == 200
        body = resp.json()
        # Both ISO strings, ``to - from = 7 days`` ± microseconds
        f = datetime.fromisoformat(body["from"])
        t = datetime.fromisoformat(body["to"])
        delta = t - f
        assert timedelta(days=7) - timedelta(seconds=2) <= delta <= timedelta(days=7) + timedelta(seconds=2)


# ---------------------------------------------------------------------------
# Aggregation correctness
# ---------------------------------------------------------------------------
class TestAggregation:
    def test_match_rate_pct_calculation(self, admin_client, db, user, store, product):
        """matched=3, unresolved=1, rejected=1 → 3/5 = 60.0 %."""
        for _ in range(3):
            _insert_scan(
                db,
                user_id=user.id,
                store_id=store.id,
                status="matched",
                match_method="barcode",
                product_ean=product.ean,
            )
        _insert_scan(
            db,
            user_id=user.id,
            store_id=store.id,
            status="unresolved",
            rejected_reason="no_fuzzy_candidate",
        )
        _insert_scan(
            db,
            user_id=user.id,
            store_id=store.id,
            status="rejected",
            rejected_reason="ocr_garbage",
        )
        resp = admin_client.get("/api/v1/admin/pipeline/stats")
        assert resp.status_code == 200
        s = resp.json()["summary"]
        assert s["scan_count"] == 5
        assert s["matched_count"] == 3
        assert s["unresolved_count"] == 1
        assert s["rejected_count"] == 1
        assert s["match_rate_pct"] == 60.0

    def test_pending_scans_excluded_from_match_rate(self, admin_client, db, user, store, product):
        """Pending rows must not skew the rate denominator."""
        _insert_scan(
            db,
            user_id=user.id,
            store_id=store.id,
            status="matched",
            match_method="barcode",
            product_ean=product.ean,
        )
        # 5 pending scans
        for _ in range(5):
            _insert_scan(db, user_id=user.id, store_id=store.id, status="pending")
        resp = admin_client.get("/api/v1/admin/pipeline/stats")
        assert resp.status_code == 200
        s = resp.json()["summary"]
        assert s["scan_count"] == 6
        assert s["matched_count"] == 1
        assert s["match_rate_pct"] == 100.0  # 1 matched / 1 terminal

    def test_legacy_status_accepted_counts_as_matched(self, admin_client, db, user, store, product):
        """Legacy v2 ``accepted`` rows fold into ``matched_count`` so the
        dashboard surface stays consistent during the v2→v3 migration."""
        _insert_scan(
            db,
            user_id=user.id,
            store_id=store.id,
            status="accepted",
            match_method="fuzzy_confirmed",
            product_ean=product.ean,
        )
        resp = admin_client.get("/api/v1/admin/pipeline/stats")
        assert resp.status_code == 200
        assert resp.json()["summary"]["matched_count"] == 1


class TestTopRejectedReasons:
    def test_aggregates_and_sorts_desc(self, admin_client, db, user, store):
        _insert_scan(
            db,
            user_id=user.id,
            store_id=store.id,
            status="unresolved",
            rejected_reason="no_fuzzy_candidate",
        )
        _insert_scan(
            db,
            user_id=user.id,
            store_id=store.id,
            status="unresolved",
            rejected_reason="no_fuzzy_candidate",
        )
        _insert_scan(
            db,
            user_id=user.id,
            store_id=store.id,
            status="rejected",
            rejected_reason="ocr_garbage",
        )
        resp = admin_client.get("/api/v1/admin/pipeline/stats")
        rows = resp.json()["top_rejected_reasons"]
        assert rows[0] == {"reason": "no_fuzzy_candidate", "count": 2}
        assert rows[1] == {"reason": "ocr_garbage", "count": 1}

    def test_excludes_null_reasons(self, admin_client, db, user, store, product):
        # matched scans have rejected_reason=NULL → must not appear
        _insert_scan(
            db,
            user_id=user.id,
            store_id=store.id,
            status="matched",
            match_method="knowledge",
            product_ean=product.ean,
        )
        resp = admin_client.get("/api/v1/admin/pipeline/stats")
        assert resp.json()["top_rejected_reasons"] == []


class TestByMatchMethod:
    def test_aggregates_by_method(self, admin_client, db, user, store, product):
        _insert_scan(
            db,
            user_id=user.id,
            store_id=store.id,
            status="matched",
            match_method="barcode",
            product_ean=product.ean,
        )
        _insert_scan(
            db,
            user_id=user.id,
            store_id=store.id,
            status="matched",
            match_method="barcode",
            product_ean=product.ean,
        )
        _insert_scan(
            db,
            user_id=user.id,
            store_id=store.id,
            status="matched",
            match_method="fuzzy_strict",
            product_ean=product.ean,
        )
        resp = admin_client.get("/api/v1/admin/pipeline/stats")
        rows = resp.json()["by_match_method"]
        assert rows[0] == {"method": "barcode", "count": 2}
        assert rows[1] == {"method": "fuzzy_strict", "count": 1}

    def test_excludes_null_method(self, admin_client, db, user, store):
        # Unresolved scans have match_method=NULL → not included.
        _insert_scan(
            db,
            user_id=user.id,
            store_id=store.id,
            status="unresolved",
            rejected_reason="no_fuzzy_candidate",
        )
        resp = admin_client.get("/api/v1/admin/pipeline/stats")
        assert resp.json()["by_match_method"] == []


class TestByStoreStatus:
    def test_distribution_includes_all_three_keys(self, admin_client, db, user, store, product):
        _insert_scan(
            db,
            user_id=user.id,
            store_id=store.id,
            status="matched",
            match_method="barcode",
            product_ean=product.ean,
            store_status="confirmed",
        )
        _insert_scan(
            db,
            user_id=user.id,
            store_id=store.id,
            status="unresolved",
            rejected_reason="no_fuzzy_candidate",
            store_status="pending",
        )
        _insert_scan(
            db,
            user_id=user.id,
            store_id=store.id,
            status="unresolved",
            rejected_reason="no_fuzzy_candidate",
            store_status="unknown",
        )
        resp = admin_client.get("/api/v1/admin/pipeline/stats")
        st = resp.json()["by_store_status"]
        assert st == {"confirmed": 1, "pending": 1, "unknown": 1}


# ---------------------------------------------------------------------------
# Window filtering
# ---------------------------------------------------------------------------
class TestWindow:
    def test_excludes_scans_outside_window(self, admin_client, db, user, store, product):
        old = datetime(2020, 1, 1, tzinfo=UTC)
        recent = datetime.now(UTC) - timedelta(hours=1)
        _insert_scan(
            db,
            user_id=user.id,
            store_id=store.id,
            status="matched",
            match_method="barcode",
            product_ean=product.ean,
            scanned_at=old,
        )
        _insert_scan(
            db,
            user_id=user.id,
            store_id=store.id,
            status="matched",
            match_method="barcode",
            product_ean=product.ean,
            scanned_at=recent,
        )
        resp = admin_client.get("/api/v1/admin/pipeline/stats")
        assert resp.status_code == 200
        # Only the recent scan is in the default 7-day window.
        assert resp.json()["summary"]["scan_count"] == 1

    def test_explicit_from_to_window(self, admin_client, db, user, store, product):
        in_window = datetime(2025, 6, 15, 12, 0, tzinfo=UTC)
        out_of_window = datetime(2025, 5, 31, tzinfo=UTC)
        _insert_scan(
            db,
            user_id=user.id,
            store_id=store.id,
            status="matched",
            match_method="barcode",
            product_ean=product.ean,
            scanned_at=in_window,
        )
        _insert_scan(
            db,
            user_id=user.id,
            store_id=store.id,
            status="matched",
            match_method="barcode",
            product_ean=product.ean,
            scanned_at=out_of_window,
        )
        resp = admin_client.get("/api/v1/admin/pipeline/stats?from=2025-06-01T00:00:00Z&to=2025-06-30T23:59:59Z")
        assert resp.status_code == 200
        assert resp.json()["summary"]["scan_count"] == 1


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------
class TestValidation:
    def test_from_after_to_returns_422(self, admin_client, db):
        resp = admin_client.get("/api/v1/admin/pipeline/stats?from=2025-12-31T00:00:00Z&to=2025-01-01T00:00:00Z")
        assert resp.status_code == 422
        assert resp.json()["detail"] == "invalid_date_range"

    def test_invalid_datetime_format_returns_422(self, admin_client, db):
        resp = admin_client.get("/api/v1/admin/pipeline/stats?from=not-a-datetime")
        assert resp.status_code == 422
