"""
TDD tests for Feed Jack streak system.

Covers:
  - GET  /api/v1/gamification/streak
  - POST /api/v1/gamification/streak/feed
  - POST /api/v1/gamification/streak/repair
  - POST /api/v1/gamification/streak/purchase-reserve

Streak mechanics (DA-09 / DA-10 / DA-11):
  - gap_days = (today - last_fed_at).days - 1
  - gap_days <= 0  → normal feed, streak += 1
  - 0 < gap_days <= food_reserves → auto-freeze, consume reserves, streak += 1
  - gap_days == 1 AND food_reserves == 0 → needs_repair: true
  - gap_days >= 2 (without coverage) → streak resets to 1 on next feed
  - Multiplier: min(streak_days * 0.05, 1.0)
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import text

from tests.conftest import make_user

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

#: Default timezone of the user_streaks rows created by _insert_streak and
#: by a first feed. The streak "day" boundary is now computed in this zone
#: (streak_repository._today_in_tz), so tests must anchor their relative
#: dates here too — anchoring on UTC breaks around midnight when the host
#: is east of Greenwich (e.g. 23:30 UTC = 01:30 next-day in Paris).
_STREAK_TZ = "Europe/Paris"


def _today() -> date:
    """Return today's date in the streak rows' timezone (Europe/Paris)."""
    return datetime.now(ZoneInfo(_STREAK_TZ)).date()


def _insert_streak(
    db,
    user_id: uuid.UUID,
    *,
    current_streak_days: int = 0,
    last_fed_at: date | None = None,
    food_reserves: int = 0,
    timezone: str = "Europe/Paris",
) -> None:
    """Insert (or upsert) a user_streaks row directly for test setup."""
    db.execute(
        text(
            "INSERT INTO user_streaks "
            "    (user_id, current_streak_days, last_fed_at, food_reserves, timezone) "
            "VALUES (:uid, :days, :last_fed, :reserves, :tz) "
            "ON CONFLICT (user_id) DO UPDATE SET "
            "    current_streak_days = EXCLUDED.current_streak_days, "
            "    last_fed_at         = EXCLUDED.last_fed_at, "
            "    food_reserves       = EXCLUDED.food_reserves, "
            "    timezone            = EXCLUDED.timezone"
        ),
        {
            "uid": user_id,
            "days": current_streak_days,
            "last_fed": last_fed_at,
            "reserves": food_reserves,
            "tz": timezone,
        },
    )
    db.commit()


def _get_streak_row(db, user_id: uuid.UUID) -> dict:
    row = db.execute(
        text("SELECT current_streak_days, last_fed_at, food_reserves, timezone FROM user_streaks WHERE user_id = :uid"),
        {"uid": user_id},
    ).first()
    if row is None:
        return {}
    return {
        "current_streak_days": row.current_streak_days,
        "last_fed_at": row.last_fed_at,
        "food_reserves": row.food_reserves,
        "timezone": row.timezone,
    }


def _give_cab(db, user_id: uuid.UUID, amount: int) -> None:
    """Give user some CABs so they can spend them in tests."""
    db.execute(
        text("UPDATE user_cab_balance SET balance = balance + :amount WHERE user_id = :uid"),
        {"amount": amount, "uid": user_id},
    )
    db.commit()


# ---------------------------------------------------------------------------
# GET /api/v1/gamification/streak  — read state
# ---------------------------------------------------------------------------


class TestGetStreak:
    def test_no_streak_row_returns_zeroes(self, user_client, db):
        client, set_user = user_client
        uid = make_user(db)
        set_user(uid)

        resp = client.get("/api/v1/gamification/streak")
        assert resp.status_code == 200
        body = resp.json()
        assert body["streak_days"] == 0
        assert body["multiplier"] == 0.0
        assert body["food_reserves"] == 0
        assert body["already_fed_today"] is False
        assert body["needs_repair"] is False
        assert body["frozen_days_used"] == 0

    def test_existing_streak_returned(self, user_client, db):
        client, set_user = user_client
        uid = make_user(db)
        set_user(uid)
        # Anchor on today-in-Europe/Paris : streak_repository.get_streak
        # now compares last_fed_at to the date in the row's stored
        # timezone (not UTC). _today() matches that boundary.
        today = _today()
        _insert_streak(db, uid, current_streak_days=5, last_fed_at=today, food_reserves=2)

        resp = client.get("/api/v1/gamification/streak")
        assert resp.status_code == 200
        body = resp.json()
        assert body["streak_days"] == 5
        assert body["multiplier"] == pytest.approx(0.25)  # 5 * 0.05
        assert body["food_reserves"] == 2
        assert body["already_fed_today"] is True

    def test_multiplier_capped_at_1(self, user_client, db):
        client, set_user = user_client
        uid = make_user(db)
        set_user(uid)
        today = _today()
        _insert_streak(db, uid, current_streak_days=25, last_fed_at=today)  # would be 125%

        resp = client.get("/api/v1/gamification/streak")
        assert resp.json()["multiplier"] == pytest.approx(1.0)

    def test_needs_repair_flag(self, user_client, db):
        """gap_days == 1 AND food_reserves == 0 → needs_repair: true."""
        client, set_user = user_client
        uid = make_user(db)
        set_user(uid)
        yesterday = _today() - timedelta(days=2)  # last_fed 2 days ago → gap=1
        _insert_streak(db, uid, current_streak_days=3, last_fed_at=yesterday, food_reserves=0)

        resp = client.get("/api/v1/gamification/streak")
        body = resp.json()
        assert body["needs_repair"] is True
        assert body["already_fed_today"] is False


# ---------------------------------------------------------------------------
# POST /api/v1/gamification/streak/feed  — feed Jack
# ---------------------------------------------------------------------------


class TestFeedJack:
    def test_first_feed_creates_streak(self, user_client, db):
        """New user feeds Jack for the first time → streak = 1."""
        client, set_user = user_client
        uid = make_user(db)
        set_user(uid)

        resp = client.post("/api/v1/gamification/streak/feed", json={})
        assert resp.status_code == 200
        body = resp.json()
        assert body["streak_days"] == 1
        assert body["multiplier"] == pytest.approx(0.05)
        assert body["already_fed_today"] is True

        row = _get_streak_row(db, uid)
        assert row["current_streak_days"] == 1
        assert row["last_fed_at"] == _today()

    def test_consecutive_feed_increments_streak(self, user_client, db):
        """Feeding on the day after last_fed → streak += 1."""
        client, set_user = user_client
        uid = make_user(db)
        set_user(uid)
        yesterday = _today() - timedelta(days=1)
        _insert_streak(db, uid, current_streak_days=4, last_fed_at=yesterday)

        resp = client.post("/api/v1/gamification/streak/feed", json={})
        assert resp.status_code == 200
        body = resp.json()
        assert body["streak_days"] == 5
        assert body["multiplier"] == pytest.approx(0.25)

    def test_idempotent_same_day(self, user_client, db):
        """Feeding twice in the same day → second call returns already_fed_today=True with no change."""
        client, set_user = user_client
        uid = make_user(db)
        set_user(uid)
        today = _today()
        _insert_streak(db, uid, current_streak_days=3, last_fed_at=today)

        resp = client.post("/api/v1/gamification/streak/feed", json={})
        assert resp.status_code == 200
        body = resp.json()
        assert body["already_fed_today"] is True
        assert body["streak_days"] == 3  # unchanged

    def test_auto_freeze_consumes_reserves(self, user_client, db):
        """gap_days=2 with 3 reserves → consumes 2 reserves, streak continues."""
        client, set_user = user_client
        uid = make_user(db)
        set_user(uid)
        three_days_ago = _today() - timedelta(days=3)  # gap = 2
        _insert_streak(db, uid, current_streak_days=10, last_fed_at=three_days_ago, food_reserves=3)

        resp = client.post("/api/v1/gamification/streak/feed", json={})
        assert resp.status_code == 200
        body = resp.json()
        assert body["streak_days"] == 11
        assert body["frozen_days_used"] == 2
        assert body["food_reserves"] == 1

        row = _get_streak_row(db, uid)
        assert row["food_reserves"] == 1

    def test_auto_freeze_exact_reserves(self, user_client, db):
        """gap_days == food_reserves → consumes all reserves, streak survives."""
        client, set_user = user_client
        uid = make_user(db)
        set_user(uid)
        five_days_ago = _today() - timedelta(days=5)  # gap = 4
        _insert_streak(db, uid, current_streak_days=7, last_fed_at=five_days_ago, food_reserves=4)

        resp = client.post("/api/v1/gamification/streak/feed", json={})
        assert resp.status_code == 200
        body = resp.json()
        assert body["streak_days"] == 8
        assert body["frozen_days_used"] == 4
        assert body["food_reserves"] == 0

    def test_gap_exceeds_reserves_resets_streak(self, user_client, db):
        """gap_days > food_reserves → streak resets to 1."""
        client, set_user = user_client
        uid = make_user(db)
        set_user(uid)
        five_days_ago = _today() - timedelta(days=5)  # gap = 4
        _insert_streak(db, uid, current_streak_days=8, last_fed_at=five_days_ago, food_reserves=2)

        resp = client.post("/api/v1/gamification/streak/feed", json={})
        assert resp.status_code == 200
        body = resp.json()
        assert body["streak_days"] == 1
        assert body["frozen_days_used"] == 0
        assert body["food_reserves"] == 0  # reserves consumed (reset still burns them)

    def test_needs_repair_blocks_normal_feed(self, user_client, db):
        """gap_days==1, food_reserves==0 → 409 with needs_repair_required."""
        client, set_user = user_client
        uid = make_user(db)
        set_user(uid)
        two_days_ago = _today() - timedelta(days=2)  # gap = 1
        _insert_streak(db, uid, current_streak_days=5, last_fed_at=two_days_ago, food_reserves=0)

        resp = client.post("/api/v1/gamification/streak/feed", json={})
        assert resp.status_code == 409
        assert resp.json()["detail"] == "needs_repair_required"

    def test_gap_2_no_reserves_resets(self, user_client, db):
        """gap_days==2, food_reserves==0 → streak resets to 1 (no repair offered)."""
        client, set_user = user_client
        uid = make_user(db)
        set_user(uid)
        three_days_ago = _today() - timedelta(days=3)  # gap = 2
        _insert_streak(db, uid, current_streak_days=12, last_fed_at=three_days_ago, food_reserves=0)

        resp = client.post("/api/v1/gamification/streak/feed", json={})
        assert resp.status_code == 200
        body = resp.json()
        assert body["streak_days"] == 1

    def test_timezone_stored_on_first_feed(self, user_client, db):
        """Sending timezone on first feed → stored in user_streaks."""
        client, set_user = user_client
        uid = make_user(db)
        set_user(uid)

        resp = client.post(
            "/api/v1/gamification/streak/feed",
            json={"timezone": "America/New_York"},
        )
        assert resp.status_code == 200
        row = _get_streak_row(db, uid)
        assert row["timezone"] == "America/New_York"

    def test_feed_day_boundary_uses_stored_timezone(self, user_client, db):
        """F-RW-gamif-7 — the streak "day" follows the stored IANA zone.

        Two users feed at the same instant : one in Pacific/Kiritimati
        (UTC+14), one in Pacific/Pago_Pago (UTC-11). Those zones are 25h
        apart so "now" is always a different calendar date in each →
        last_fed_at must differ. A UTC-only implementation would store
        the same date for both.
        """
        from zoneinfo import ZoneInfo

        client, set_user = user_client

        uid_east = make_user(db)
        set_user(uid_east)
        resp = client.post(
            "/api/v1/gamification/streak/feed",
            json={"timezone": "Pacific/Kiritimati"},
        )
        assert resp.status_code == 200
        east_date = _get_streak_row(db, uid_east)["last_fed_at"]

        uid_west = make_user(db)
        set_user(uid_west)
        resp = client.post(
            "/api/v1/gamification/streak/feed",
            json={"timezone": "Pacific/Pago_Pago"},
        )
        assert resp.status_code == 200
        west_date = _get_streak_row(db, uid_west)["last_fed_at"]

        # 25h apart → always a different calendar date.
        assert east_date != west_date
        assert east_date == datetime.now(ZoneInfo("Pacific/Kiritimati")).date()
        assert west_date == datetime.now(ZoneInfo("Pacific/Pago_Pago")).date()

    def test_feed_invalid_timezone_falls_back_to_utc(self, user_client, db):
        """An unrecognised IANA zone must not 500 — fall back to UTC."""
        client, set_user = user_client
        uid = make_user(db)
        set_user(uid)
        # Pre-seed a row carrying a bogus timezone so feed_jack reads it.
        _insert_streak(
            db,
            uid,
            current_streak_days=0,
            last_fed_at=None,
            food_reserves=0,
            timezone="Not/AZone",
        )

        resp = client.post("/api/v1/gamification/streak/feed", json={})
        assert resp.status_code == 200
        assert resp.json()["streak_days"] == 1
        row = _get_streak_row(db, uid)
        assert row["last_fed_at"] == datetime.now(UTC).date()

    def test_xp_awarded_on_feed(self, user_client, db):
        """Feeding Jack should insert an XP transaction."""
        client, set_user = user_client
        uid = make_user(db)
        set_user(uid)

        client.post("/api/v1/gamification/streak/feed", json={})

        row = db.execute(
            text("SELECT amount FROM xp_transactions WHERE user_id = :uid AND reason = 'feed_jack'"),
            {"uid": uid},
        ).first()
        assert row is not None
        assert row.amount > 0


# ---------------------------------------------------------------------------
# POST /api/v1/gamification/streak/repair  — manual repair
# ---------------------------------------------------------------------------


class TestRepairStreak:
    def test_repair_restores_streak(self, user_client, db):
        """gap=1, no reserves, has CABs → repair deducts CABs, restores streak."""
        client, set_user = user_client
        uid = make_user(db)
        set_user(uid)
        two_days_ago = _today() - timedelta(days=2)
        _insert_streak(db, uid, current_streak_days=5, last_fed_at=two_days_ago, food_reserves=0)
        _give_cab(db, uid, 200)  # ensure enough balance

        resp = client.post("/api/v1/gamification/streak/repair")
        assert resp.status_code == 200
        body = resp.json()
        assert body["streak_days"] == 6
        assert body["needs_repair"] is False

    def test_repair_deducts_cab(self, user_client, db):
        """Repair should create a CAB debit transaction."""
        client, set_user = user_client
        uid = make_user(db)
        set_user(uid)
        two_days_ago = _today() - timedelta(days=2)
        _insert_streak(db, uid, current_streak_days=3, last_fed_at=two_days_ago, food_reserves=0)
        _give_cab(db, uid, 200)

        client.post("/api/v1/gamification/streak/repair")

        row = db.execute(
            text(
                "SELECT amount FROM cabecoin_transactions "
                "WHERE user_id = :uid AND direction = 'debit' AND reason = 'streak_repair'"
            ),
            {"uid": uid},
        ).first()
        assert row is not None
        assert row.amount > 0

    def test_repair_fails_not_in_repair_state(self, user_client, db):
        """Repair not allowed when streak is already healthy (gap=0)."""
        client, set_user = user_client
        uid = make_user(db)
        set_user(uid)
        today = _today()
        _insert_streak(db, uid, current_streak_days=5, last_fed_at=today, food_reserves=0)
        _give_cab(db, uid, 200)

        resp = client.post("/api/v1/gamification/streak/repair")
        assert resp.status_code == 409
        assert resp.json()["detail"] == "streak_not_in_repair_state"

    def test_repair_fails_insufficient_cab(self, user_client, db):
        """Repair fails when user has no CABs."""
        client, set_user = user_client
        uid = make_user(db)
        set_user(uid)
        two_days_ago = _today() - timedelta(days=2)
        _insert_streak(db, uid, current_streak_days=5, last_fed_at=two_days_ago, food_reserves=0)
        # no CABs given → balance = 0

        resp = client.post("/api/v1/gamification/streak/repair")
        assert resp.status_code == 402
        assert resp.json()["detail"] == "insufficient_cab_balance"


# ---------------------------------------------------------------------------
# POST /api/v1/gamification/streak/purchase-reserve  — buy food reserves
# ---------------------------------------------------------------------------


class TestPurchaseReserve:
    def test_purchase_reserve_success(self, user_client, db):
        """Buy 1 reserve → food_reserves += 1, CABs debited."""
        client, set_user = user_client
        uid = make_user(db)
        set_user(uid)
        _insert_streak(db, uid, current_streak_days=1, food_reserves=0)
        _give_cab(db, uid, 500)

        resp = client.post("/api/v1/gamification/streak/purchase-reserve", json={"quantity": 1})
        assert resp.status_code == 200
        body = resp.json()
        assert body["food_reserves"] == 1
        assert body["cab_spent"] > 0

        row = _get_streak_row(db, uid)
        assert row["food_reserves"] == 1

    def test_purchase_multiple_reserves(self, user_client, db):
        client, set_user = user_client
        uid = make_user(db)
        set_user(uid)
        _insert_streak(db, uid, current_streak_days=1, food_reserves=2)
        _give_cab(db, uid, 500)

        resp = client.post("/api/v1/gamification/streak/purchase-reserve", json={"quantity": 3})
        assert resp.status_code == 200
        assert resp.json()["food_reserves"] == 5

    def test_purchase_exceeds_max_reserves(self, user_client, db):
        """Cannot exceed max_food_reserves (default 7)."""
        client, set_user = user_client
        uid = make_user(db)
        set_user(uid)
        _insert_streak(db, uid, current_streak_days=1, food_reserves=6)
        _give_cab(db, uid, 500)

        resp = client.post("/api/v1/gamification/streak/purchase-reserve", json={"quantity": 3})
        assert resp.status_code == 409
        assert resp.json()["detail"] == "reserve_limit_exceeded"

    def test_purchase_reserve_insufficient_cab(self, user_client, db):
        """No CABs → 402."""
        client, set_user = user_client
        uid = make_user(db)
        set_user(uid)
        _insert_streak(db, uid, current_streak_days=1, food_reserves=0)
        # no CABs

        resp = client.post("/api/v1/gamification/streak/purchase-reserve", json={"quantity": 1})
        assert resp.status_code == 402
        assert resp.json()["detail"] == "insufficient_cab_balance"

    def test_purchase_invalid_quantity(self, user_client, db):
        """quantity=0 is invalid."""
        client, set_user = user_client
        uid = make_user(db)
        set_user(uid)

        resp = client.post("/api/v1/gamification/streak/purchase-reserve", json={"quantity": 0})
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Multiplier applied to CAB and XP awards
# ---------------------------------------------------------------------------


class TestStreakMultiplier:
    def test_multiplier_applied_to_award_cab(self, db):
        """award_cab with active streak applies multiplier to effective amount."""
        from repositories.cab_repository import award_cab

        uid = make_user(db)
        # streak 10 days → multiplier 50%
        _insert_streak(db, uid, current_streak_days=10, last_fed_at=_today())

        award_cab(db, uid, 100, "receipt_scan")
        db.commit()

        # Balance should reflect boosted amount: 100 * (1 + 0.50) = 150
        balance_row = db.execute(
            text("SELECT balance FROM user_cab_balance WHERE user_id = :uid"),
            {"uid": uid},
        ).first()
        assert balance_row.balance == 150

    def test_multiplier_applied_to_award_xp(self, db):
        """award_xp with active streak applies multiplier."""
        from repositories.xp_repository import award_xp

        uid = make_user(db)
        _insert_streak(db, uid, current_streak_days=20, last_fed_at=_today())  # 100%

        award_xp(db, uid, 10, "receipt_scan")
        db.commit()

        xp_row = db.execute(
            text("SELECT balance FROM user_xp_balance WHERE user_id = :uid"),
            {"uid": uid},
        ).first()
        # 10 * (1 + 1.0) = 20
        assert int(xp_row.balance) == 20

    def test_no_streak_no_multiplier(self, db):
        """award_cab with no streak row → no multiplier (amount unchanged)."""
        from repositories.cab_repository import award_cab

        uid = make_user(db)

        award_cab(db, uid, 100, "receipt_scan")
        db.commit()

        balance_row = db.execute(
            text("SELECT balance FROM user_cab_balance WHERE user_id = :uid"),
            {"uid": uid},
        ).first()
        assert balance_row.balance == 100


# ===========================================================================
# Achievements V1 — hook in feed_jack route (PR4)
# ===========================================================================


class TestAchievementHookStreakExtended:
    """`POST /api/v1/gamification/streak/feed` must fire `check_achievements`
    with event_type='streak_extended' when `feed_jack` returns
    `is_new_feed=True`. Idempotent same-day calls (already_fed_today) must
    NOT re-fire the hook."""

    def _spy(self, monkeypatch):
        from services import achievement_service

        calls: list[dict] = []
        original = achievement_service.check_achievements

        def wrapper(*args, **kwargs):
            calls.append({"args": args, "kwargs": kwargs})
            return original(*args, **kwargs)

        monkeypatch.setattr(achievement_service, "check_achievements", wrapper)
        return calls

    def test_first_feed_fires_streak_extended(self, user_client, db, monkeypatch):
        client, set_user = user_client
        uid = make_user(db)
        set_user(uid)
        calls = self._spy(monkeypatch)

        resp = client.post("/api/v1/gamification/streak/feed", json={})
        assert resp.status_code == 200
        streak_calls = [c for c in calls if c["kwargs"].get("event_type") == "streak_extended"]
        assert len(streak_calls) == 1
        assert streak_calls[0]["kwargs"].get("user_id") == uid

    def test_idempotent_same_day_does_not_refire(self, user_client, db, monkeypatch):
        client, set_user = user_client
        uid = make_user(db)
        set_user(uid)
        today = _today()
        _insert_streak(db, uid, current_streak_days=3, last_fed_at=today)

        calls = self._spy(monkeypatch)
        resp = client.post("/api/v1/gamification/streak/feed", json={})
        assert resp.status_code == 200
        # already_fed_today=True → is_new_feed=False → no hook.
        streak_calls = [c for c in calls if c["kwargs"].get("event_type") == "streak_extended"]
        assert streak_calls == []
