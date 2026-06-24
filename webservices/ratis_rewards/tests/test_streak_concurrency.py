"""Concurrent streak-mutation race tests — F-RW-gamif-1 / F-RW-gamif-2.

Audit findings :

* F-RW-gamif-1 — ``feed_jack`` read ``user_streaks`` without ``FOR
  UPDATE``. Two same-day POST /streak/feed both passed the
  ``last_fed_at != today`` check → double XP + double challenge
  progression. The first-feed case has no row to lock at all.
* F-RW-gamif-2 — ``purchase_reserve`` / ``repair_streak`` had the same
  hole : the last-write-wins ``_upsert_streak`` let the user pay CAB
  twice for one batch / one repair.

Fix : a transaction-scoped ``pg_advisory_xact_lock(hashtext(user_id))``
serialises every streak mutation for a user — it covers the no-row-yet
case that ``FOR UPDATE`` cannot — plus ``FOR UPDATE`` on the row SELECT.

Tests use two independent connections (the savepoint ``db`` fixture
shares one connection and cannot model real row / advisory locks). A
``500ms`` lock_timeout makes the block observable without sleeps.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, OperationalError
from sqlalchemy.orm import Session

from tests.conftest import TestingSessionLocal, make_user


def _open_session() -> Session:
    return TestingSessionLocal()


def _cleanup_user(user_id: uuid.UUID) -> None:
    cleanup = _open_session()
    try:
        cleanup.execute(
            text("DELETE FROM xp_transactions WHERE user_id = :uid"),
            {"uid": user_id},
        )
        cleanup.execute(
            text("DELETE FROM cabecoin_transactions WHERE user_id = :uid"),
            {"uid": user_id},
        )
        cleanup.execute(
            text("DELETE FROM user_streaks WHERE user_id = :uid"),
            {"uid": user_id},
        )
        cleanup.execute(
            text("DELETE FROM user_xp_balance WHERE user_id = :uid"),
            {"uid": user_id},
        )
        cleanup.execute(
            text("DELETE FROM user_cab_balance WHERE user_id = :uid"),
            {"uid": user_id},
        )
        cleanup.execute(text("DELETE FROM users WHERE id = :uid"), {"uid": user_id})
        cleanup.commit()
    finally:
        cleanup.close()


def _make_user_committed() -> uuid.UUID:
    """Create a user in its own committed transaction (visible to both sessions)."""
    setup = _open_session()
    try:
        user_id = make_user(setup)
        setup.commit()
    finally:
        setup.close()
    return user_id


@pytest.fixture
def two_sessions():
    s1 = _open_session()
    s2 = _open_session()
    try:
        yield s1, s2
    finally:
        s1.close()
        s2.close()


# ---------------------------------------------------------------------------
# F-RW-gamif-1 — feed_jack advisory lock + FOR UPDATE
# ---------------------------------------------------------------------------


class TestFeedJackRace:
    """Two concurrent feed_jack on the same user must serialise — even on
    the first-ever feed where no user_streaks row exists yet."""

    def test_first_feed_advisory_lock_blocks_second(self, two_sessions):
        """Session A runs feed_jack (no row yet) and holds the advisory
        lock by NOT committing → session B blocks until A commits.
        """
        from repositories.streak_repository import feed_jack

        s1, s2 = two_sessions
        user_id = _make_user_committed()
        try:
            # A feeds — acquires the advisory lock, creates the row,
            # does NOT commit yet → lock still held.
            feed_jack(s1, user_id, xp_per_feed=5)

            # B sets a short lock timeout and tries to feed the same
            # user — must block on the advisory lock and time out.
            s2.execute(text("SET LOCAL lock_timeout = '500ms'"))
            with pytest.raises((OperationalError, DBAPIError)):
                feed_jack(s2, user_id, xp_per_feed=5)
            s2.rollback()

            s1.commit()  # release the lock

            # B retried fresh : it now sees the row, last_fed_at == today
            # → idempotent same-day, is_new_feed False.
            result_b = feed_jack(s2, user_id, xp_per_feed=5)
            assert result_b.is_new_feed is False
            s2.commit()
        finally:
            _cleanup_user(user_id)

    def test_serialised_feeds_no_double_streak(self, two_sessions):
        """A feeds (streak 1) + commits → B retried fresh observes the
        same-day row and does NOT advance the streak again.

        Pre-fix : both would read "no row" / "not fed today" → both
        create/advance → double XP. Post-fix : B is idempotent.
        """
        from repositories.streak_repository import feed_jack

        s1, s2 = two_sessions
        user_id = _make_user_committed()
        try:
            result_a = feed_jack(s1, user_id, xp_per_feed=5)
            s1.commit()
            assert result_a.is_new_feed is True
            assert result_a.state["streak_days"] == 1

            result_b = feed_jack(s2, user_id, xp_per_feed=5)
            s2.commit()
            assert result_b.is_new_feed is False
            assert result_b.state["streak_days"] == 1  # not 2

            check = _open_session()
            try:
                streak = check.execute(
                    text("SELECT current_streak_days FROM user_streaks WHERE user_id = :uid"),
                    {"uid": user_id},
                ).scalar()
                assert streak == 1
            finally:
                check.close()
        finally:
            _cleanup_user(user_id)


# ---------------------------------------------------------------------------
# F-RW-gamif-2 — purchase_reserve / repair_streak advisory lock + FOR UPDATE
# ---------------------------------------------------------------------------


class TestPurchaseReserveRace:
    """Two concurrent purchase_reserve must serialise — no double CAB
    debit, no lost reserve increment."""

    def test_advisory_lock_blocks_second_purchase(self, two_sessions):
        from repositories.streak_repository import purchase_reserve

        s1, s2 = two_sessions
        user_id = _make_user_committed()
        # Credit enough CAB for two purchases so the lock — not balance —
        # is the gate under test.
        setup = _open_session()
        try:
            setup.execute(
                text("UPDATE user_cab_balance SET balance = 1000 WHERE user_id = :uid"),
                {"uid": user_id},
            )
            setup.commit()
        finally:
            setup.close()
        try:
            purchase_reserve(
                s1,
                user_id,
                quantity=1,
                cost_per_reserve_cab=50,
                max_food_reserves=7,
            )
            s2.execute(text("SET LOCAL lock_timeout = '500ms'"))
            with pytest.raises((OperationalError, DBAPIError)):
                purchase_reserve(
                    s2,
                    user_id,
                    quantity=1,
                    cost_per_reserve_cab=50,
                    max_food_reserves=7,
                )
            s2.rollback()

            s1.commit()
            # B retried fresh : sees food_reserves=1, buys a 2nd → 2.
            result_b = purchase_reserve(
                s2,
                user_id,
                quantity=1,
                cost_per_reserve_cab=50,
                max_food_reserves=7,
            )
            s2.commit()
            assert result_b["food_reserves"] == 2
        finally:
            _cleanup_user(user_id)

    def test_serialised_purchases_no_lost_reserve(self, two_sessions):
        """Two serialised purchases of 1 reserve each → final = 2, CAB
        debited exactly twice (100), not once.

        Pre-fix : both read food_reserves=0, both _upsert to 1 (last
        write wins) → user pays 100 CAB but ends with only 1 reserve.
        """
        from repositories.streak_repository import purchase_reserve

        s1, s2 = two_sessions
        user_id = _make_user_committed()
        setup = _open_session()
        try:
            setup.execute(
                text("UPDATE user_cab_balance SET balance = 1000 WHERE user_id = :uid"),
                {"uid": user_id},
            )
            setup.commit()
        finally:
            setup.close()
        try:
            purchase_reserve(
                s1,
                user_id,
                quantity=1,
                cost_per_reserve_cab=50,
                max_food_reserves=7,
            )
            s1.commit()
            purchase_reserve(
                s2,
                user_id,
                quantity=1,
                cost_per_reserve_cab=50,
                max_food_reserves=7,
            )
            s2.commit()

            check = _open_session()
            try:
                reserves = check.execute(
                    text("SELECT food_reserves FROM user_streaks WHERE user_id = :uid"),
                    {"uid": user_id},
                ).scalar()
                balance = check.execute(
                    text("SELECT balance FROM user_cab_balance WHERE user_id = :uid"),
                    {"uid": user_id},
                ).scalar()
                assert reserves == 2  # both increments survived
                assert balance == 900  # 1000 - 2 × 50
            finally:
                check.close()
        finally:
            _cleanup_user(user_id)


class TestRepairStreakRace:
    """Two concurrent repair_streak must serialise — no double CAB debit."""

    def _seed_repair_state(self, user_id: uuid.UUID) -> None:
        """Put the user in a gap=1/no-reserves repair state with CAB to
        afford TWO repairs (so the lock, not balance, is the gate)."""
        from datetime import datetime, timedelta
        from zoneinfo import ZoneInfo

        two_days_ago = datetime.now(ZoneInfo("Europe/Paris")).date() - timedelta(days=2)
        setup = _open_session()
        try:
            setup.execute(
                text(
                    "INSERT INTO user_streaks "
                    "    (user_id, current_streak_days, last_fed_at, "
                    "     food_reserves, timezone) "
                    "VALUES (:uid, 5, :lfa, 0, 'Europe/Paris')"
                ),
                {"uid": user_id, "lfa": two_days_ago},
            )
            setup.execute(
                text("UPDATE user_cab_balance SET balance = 1000 WHERE user_id = :uid"),
                {"uid": user_id},
            )
            setup.commit()
        finally:
            setup.close()

    def test_advisory_lock_blocks_second_repair(self, two_sessions):
        from repositories.streak_repository import repair_streak

        s1, s2 = two_sessions
        user_id = _make_user_committed()
        self._seed_repair_state(user_id)
        try:
            repair_streak(s1, user_id, repair_cost_cab=100)

            s2.execute(text("SET LOCAL lock_timeout = '500ms'"))
            with pytest.raises((OperationalError, DBAPIError)):
                repair_streak(s2, user_id, repair_cost_cab=100)
            s2.rollback()

            s1.commit()
        finally:
            _cleanup_user(user_id)

    def test_serialised_repairs_no_double_debit(self, two_sessions):
        """A repairs → B retried fresh sees the healed streak and raises
        StreakNotInRepairState → only one 100-CAB debit.

        Pre-fix : both read gap=1/reserves=0 → both debit 100 → user
        pays 200 for one repair.
        """
        from repositories.exceptions import StreakNotInRepairState
        from repositories.streak_repository import repair_streak

        s1, s2 = two_sessions
        user_id = _make_user_committed()
        self._seed_repair_state(user_id)
        try:
            state_a = repair_streak(s1, user_id, repair_cost_cab=100)
            s1.commit()
            assert state_a["streak_days"] == 6

            with pytest.raises(StreakNotInRepairState):
                repair_streak(s2, user_id, repair_cost_cab=100)
            s2.rollback()

            check = _open_session()
            try:
                balance = check.execute(
                    text("SELECT balance FROM user_cab_balance WHERE user_id = :uid"),
                    {"uid": user_id},
                ).scalar()
                assert balance == 900  # 1000 - 1 × 100, not 800
            finally:
                check.close()
        finally:
            _cleanup_user(user_id)


# ---------------------------------------------------------------------------
# SQL-surface regression — the lock clauses must remain present.
# ---------------------------------------------------------------------------


def test_streak_lock_clauses_present_in_repository():
    """Belt + suspenders : the advisory lock + FOR UPDATE must stay in
    the mutating streak functions."""
    import inspect

    from repositories import streak_repository

    for fn_name in ("feed_jack", "repair_streak", "purchase_reserve"):
        src = inspect.getsource(getattr(streak_repository, fn_name))
        assert "_lock_streak_user" in src, f"F-RW-gamif regression : {fn_name} missing advisory lock"
        assert "FOR UPDATE" in src, f"F-RW-gamif regression : {fn_name} SELECT missing FOR UPDATE"
