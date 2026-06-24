"""Tests for the RW admin stats endpoint (PR8).

Covers ``GET /api/v1/admin/stats/cab`` :

- Default window (last 30 days) with no params
- Explicit ``from`` / ``to`` params
- ``group_by`` switching (reason / day / user)
- Edge cases : empty DB, ``from > to``, invalid ``group_by``
- Window honoring : transactions outside the window are excluded
- Aggregations correctness : totals, counts, top earners

Uses the parent conftest fixtures (``db``, ``admin_client``, ``make_user``).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import text

from tests.conftest import make_user


def _insert_tx(
    db,
    *,
    user_id: uuid.UUID,
    direction: str,
    amount: int,
    reason: str = "receipt_scan",
    created_at: datetime | None = None,
) -> uuid.UUID:
    """Insert a cabecoin_transactions row at a precise created_at for windowing tests."""
    tx_id = uuid.uuid4()
    if created_at is None:
        created_at = datetime.now(UTC)
    db.execute(
        text(
            "INSERT INTO cabecoin_transactions "
            "  (id, user_id, direction, amount, reason, created_at) "
            "VALUES (:id, :uid, :dir, :amt, :reason, :ts)"
        ),
        {
            "id": tx_id,
            "uid": user_id,
            "dir": direction,
            "amt": amount,
            "reason": reason,
            "ts": created_at,
        },
    )
    db.commit()
    return tx_id


# ---------------------------------------------------------------------------
# Default behaviour (no query params)
# ---------------------------------------------------------------------------
class TestDefaults:
    def test_empty_db_returns_zero_summary(self, admin_client, db):
        resp = admin_client.get("/api/v1/admin/stats/cab")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["summary"]["total_credit_cents"] == 0
        assert body["summary"]["total_debit_cents"] == 0
        assert body["summary"]["net_emission_cents"] == 0
        assert body["summary"]["transaction_count"] == 0
        assert body["summary"]["user_count_active"] == 0
        # Default group_by is reason → breakdown_by_reason key exists.
        assert "breakdown_by_reason" in body
        assert body["breakdown_by_reason"] == []
        assert body["top_earners"] == []

    def test_default_window_is_30_days(self, admin_client, db):
        """No params → window = today-30d → today, both inclusive."""
        resp = admin_client.get("/api/v1/admin/stats/cab")
        assert resp.status_code == 200
        body = resp.json()
        today = datetime.now(UTC).date()
        assert body["to"] == today.isoformat()
        assert body["from"] == (today - timedelta(days=30)).isoformat()


# ---------------------------------------------------------------------------
# Aggregation correctness
# ---------------------------------------------------------------------------
class TestAggregation:
    def test_summary_sums_credits_and_debits(self, admin_client, db):
        uid1 = make_user(db)
        uid2 = make_user(db)
        _insert_tx(db, user_id=uid1, direction="credit", amount=500, reason="receipt_scan")
        _insert_tx(db, user_id=uid1, direction="credit", amount=300, reason="mission_reward")
        _insert_tx(db, user_id=uid2, direction="debit", amount=200, reason="shop_purchase")
        resp = admin_client.get("/api/v1/admin/stats/cab")
        assert resp.status_code == 200
        s = resp.json()["summary"]
        assert s["total_credit_cents"] == 800
        assert s["total_debit_cents"] == 200
        assert s["net_emission_cents"] == 600
        assert s["transaction_count"] == 3
        assert s["user_count_active"] == 2

    def test_breakdown_by_reason_groups_correctly(self, admin_client, db):
        uid = make_user(db)
        _insert_tx(db, user_id=uid, direction="credit", amount=100, reason="receipt_scan")
        _insert_tx(db, user_id=uid, direction="credit", amount=200, reason="receipt_scan")
        _insert_tx(db, user_id=uid, direction="credit", amount=50, reason="mission_reward")
        resp = admin_client.get("/api/v1/admin/stats/cab?group_by=reason")
        assert resp.status_code == 200
        rows = resp.json()["breakdown_by_reason"]
        # Sorted by total volume DESC.
        assert rows[0]["reason"] == "receipt_scan"
        assert rows[0]["credit_cents"] == 300
        assert rows[0]["count"] == 2
        assert rows[1]["reason"] == "mission_reward"
        assert rows[1]["credit_cents"] == 50
        assert rows[1]["count"] == 1

    def test_top_earners_limited_to_10_and_sorted_desc(self, admin_client, db):
        # 12 users, increasing credit amounts → only top 10 returned, descending.
        users = []
        for i in range(12):
            u = make_user(db)
            users.append(u)
            _insert_tx(db, user_id=u, direction="credit", amount=(i + 1) * 100)
        resp = admin_client.get("/api/v1/admin/stats/cab")
        assert resp.status_code == 200
        top = resp.json()["top_earners"]
        assert len(top) == 10
        # First entry = highest amount = 1200 (i=11)
        assert top[0]["credit_cents"] == 1200
        assert top[-1]["credit_cents"] == 300  # 10th-highest = i=2 → 300


# ---------------------------------------------------------------------------
# Window filtering
# ---------------------------------------------------------------------------
class TestWindow:
    def test_excludes_transactions_outside_window(self, admin_client, db):
        uid = make_user(db)
        # Far past — should be excluded
        old = datetime(2020, 1, 1, tzinfo=UTC)
        _insert_tx(db, user_id=uid, direction="credit", amount=999, created_at=old)
        # In window
        recent = datetime.now(UTC) - timedelta(days=1)
        _insert_tx(db, user_id=uid, direction="credit", amount=100, created_at=recent)
        resp = admin_client.get("/api/v1/admin/stats/cab")
        assert resp.status_code == 200
        s = resp.json()["summary"]
        assert s["total_credit_cents"] == 100  # the old 999 is excluded
        assert s["transaction_count"] == 1

    def test_explicit_from_to_window(self, admin_client, db):
        uid = make_user(db)
        # In window
        in_window = datetime(2025, 6, 15, 12, 0, tzinfo=UTC)
        _insert_tx(db, user_id=uid, direction="credit", amount=42, created_at=in_window)
        # Out of window (before)
        before = datetime(2025, 5, 31, 23, 59, tzinfo=UTC)
        _insert_tx(db, user_id=uid, direction="credit", amount=100, created_at=before)
        # Out of window (after)
        after = datetime(2025, 7, 1, 0, 0, tzinfo=UTC)
        _insert_tx(db, user_id=uid, direction="credit", amount=200, created_at=after)
        resp = admin_client.get("/api/v1/admin/stats/cab?from=2025-06-01&to=2025-06-30")
        assert resp.status_code == 200
        s = resp.json()["summary"]
        assert s["total_credit_cents"] == 42
        assert s["transaction_count"] == 1

    def test_to_is_end_of_day_inclusive(self, admin_client, db):
        """``to=2025-06-30`` includes all of 2025-06-30 (up to 23:59:59)."""
        uid = make_user(db)
        end_of_day = datetime(2025, 6, 30, 23, 30, tzinfo=UTC)
        _insert_tx(db, user_id=uid, direction="credit", amount=77, created_at=end_of_day)
        resp = admin_client.get("/api/v1/admin/stats/cab?from=2025-06-01&to=2025-06-30")
        assert resp.status_code == 200
        assert resp.json()["summary"]["transaction_count"] == 1


# ---------------------------------------------------------------------------
# group_by switching
# ---------------------------------------------------------------------------
class TestGroupBy:
    def test_group_by_day(self, admin_client, db):
        uid = make_user(db)
        d1 = datetime(2025, 6, 10, 10, 0, tzinfo=UTC)
        d2 = datetime(2025, 6, 11, 10, 0, tzinfo=UTC)
        _insert_tx(db, user_id=uid, direction="credit", amount=100, created_at=d1)
        _insert_tx(db, user_id=uid, direction="credit", amount=200, created_at=d1)
        _insert_tx(db, user_id=uid, direction="credit", amount=50, created_at=d2)
        resp = admin_client.get("/api/v1/admin/stats/cab?from=2025-06-01&to=2025-06-30&group_by=day")
        assert resp.status_code == 200
        body = resp.json()
        assert "breakdown_by_day" in body
        days = body["breakdown_by_day"]
        assert len(days) == 2
        # Sorted chronologically.
        assert days[0]["day"] == "2025-06-10"
        assert days[0]["credit_cents"] == 300
        assert days[0]["count"] == 2
        assert days[1]["day"] == "2025-06-11"
        assert days[1]["credit_cents"] == 50

    def test_group_by_user_returns_breakdown(self, admin_client, db):
        u1 = make_user(db)
        u2 = make_user(db)
        _insert_tx(db, user_id=u1, direction="credit", amount=100)
        _insert_tx(db, user_id=u2, direction="credit", amount=500)
        resp = admin_client.get("/api/v1/admin/stats/cab?group_by=user")
        assert resp.status_code == 200
        body = resp.json()
        assert "breakdown_by_user" in body
        rows = body["breakdown_by_user"]
        assert rows[0]["user_id"] == str(u2)
        assert rows[0]["credit_cents"] == 500
        assert rows[1]["user_id"] == str(u1)

    def test_invalid_group_by_returns_422(self, admin_client, db):
        resp = admin_client.get("/api/v1/admin/stats/cab?group_by=galaxy")
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Validation errors
# ---------------------------------------------------------------------------
class TestValidation:
    def test_from_after_to_returns_422(self, admin_client, db):
        resp = admin_client.get("/api/v1/admin/stats/cab?from=2025-12-31&to=2025-01-01")
        assert resp.status_code == 422
        assert resp.json()["detail"] == "invalid_date_range"

    def test_invalid_date_format_returns_422(self, admin_client, db):
        resp = admin_client.get("/api/v1/admin/stats/cab?from=not-a-date")
        # Pydantic rejects non-ISO date.
        assert resp.status_code == 422
