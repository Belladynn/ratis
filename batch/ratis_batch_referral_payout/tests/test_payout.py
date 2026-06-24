"""
Tests for ratis_batch_referral_payout.

Covers :
  - fetch_eligible_orders : only pending + eligible_at <= now, joined to referral_uses
  - is_still_subscribed : reads subscriptions, respects status/plan filters
  - run() dry-run : no DB mutations, no HTTP calls, correct stats
  - run() real : churned → mark failed ; still-subscribed → HTTP notify to rewards
"""

from __future__ import annotations

from unittest.mock import patch

from payout import (
    fetch_eligible_orders,
    is_still_subscribed,
    mark_churned,
    run,
)
from sqlalchemy import text


class TestFetchEligibleOrders:
    def test_returns_past_eligible_at_orders(self, session_factory, make_referral_order):
        data = make_referral_order(eligible_delta_hours=-1)

        with session_factory() as db:
            orders = fetch_eligible_orders(db)

        assert len(orders) == 1
        assert orders[0].order_id == str(data["order_id"])
        assert orders[0].referrer_user_id == str(data["referrer_id"])
        assert orders[0].referred_user_id == str(data["referred_id"])

    def test_skips_future_eligible_at_orders(self, session_factory, make_referral_order):
        make_referral_order(eligible_delta_hours=24)  # tomorrow
        with session_factory() as db:
            orders = fetch_eligible_orders(db)
        assert orders == []

    def test_skips_non_pending_orders(self, session_factory, make_referral_order):
        data = make_referral_order(eligible_delta_hours=-1)
        # Flip it to 'issued'
        with session_factory() as db:
            db.execute(
                text("UPDATE gift_card_orders SET status = 'issued', issued_at = now() WHERE id = :oid"),
                {"oid": data["order_id"]},
            )
            db.commit()

        with session_factory() as db:
            orders = fetch_eligible_orders(db)
        assert orders == []


class TestIsStillSubscribed:
    def test_true_for_active_subscription(self, session_factory, make_referral_order):
        data = make_referral_order(subscribed=True)
        with session_factory() as db:
            assert is_still_subscribed(db, str(data["referred_id"])) is True

    def test_false_when_no_subscription(self, session_factory, make_referral_order):
        data = make_referral_order(subscribed=False)
        with session_factory() as db:
            assert is_still_subscribed(db, str(data["referred_id"])) is False

    def test_false_when_subscription_cancelled(self, session_factory, make_referral_order):
        data = make_referral_order(subscribed=True)
        with session_factory() as db:
            db.execute(
                text("UPDATE subscriptions SET status = 'cancelled', cancelled_at = now() WHERE user_id = :uid"),
                {"uid": data["referred_id"]},
            )
            db.commit()

        with session_factory() as db:
            assert is_still_subscribed(db, str(data["referred_id"])) is False


class TestRun:
    def test_dry_run_no_mutations(self, session_factory, make_referral_order):
        make_referral_order(eligible_delta_hours=-24, subscribed=True)
        stats = run(session_factory, dry_run=True)

        assert stats["candidates"] == 1
        assert stats["issued"] == 1  # counted as if we'd issue
        assert stats["churned"] == 0
        assert stats["dry_run"] is True

        # No DB mutation — order still pending
        with session_factory() as db:
            status = db.execute(text("SELECT status FROM gift_card_orders LIMIT 1")).scalar()
            assert status == "pending"

    def test_churned_order_marked_churned(self, session_factory, make_referral_order):
        data = make_referral_order(eligible_delta_hours=-1, subscribed=False)
        stats = run(session_factory, dry_run=False)

        assert stats["candidates"] == 1
        assert stats["churned"] == 1
        assert stats["issued"] == 0

        with session_factory() as db:
            row = db.execute(
                text("SELECT status, failed_at FROM gift_card_orders WHERE id = :oid"),
                {"oid": data["order_id"]},
            ).first()
            # Audit H3: churn must use the dedicated 'churned' status,
            # NOT 'failed', so anti-fraud / fiscal audit can tell them apart.
            assert row.status == "churned"
            assert row.failed_at is not None

    def test_issue_notify_called_for_still_subscribed(self, session_factory, make_referral_order):
        data = make_referral_order(eligible_delta_hours=-1, subscribed=True)

        with patch("payout.notify_rewards_to_issue", return_value=True) as mock_notify:
            stats = run(session_factory, dry_run=False)

        assert stats["candidates"] == 1
        assert stats["issued"] == 1
        assert stats["churned"] == 0
        mock_notify.assert_called_once_with(str(data["order_id"]))

    def test_error_counted_when_notify_fails(self, session_factory, make_referral_order):
        make_referral_order(eligible_delta_hours=-1, subscribed=True)

        with patch("payout.notify_rewards_to_issue", return_value=False):
            stats = run(session_factory, dry_run=False)

        assert stats["errors"] == 1
        assert stats["issued"] == 0

    def test_mixed_batch_handles_both_outcomes(self, session_factory, make_referral_order):
        churned = make_referral_order(eligible_delta_hours=-2, subscribed=False)
        subscribed = make_referral_order(eligible_delta_hours=-1, subscribed=True)

        with patch("payout.notify_rewards_to_issue", return_value=True):
            stats = run(session_factory, dry_run=False)

        assert stats["candidates"] == 2
        assert stats["churned"] == 1
        assert stats["issued"] == 1

        with session_factory() as db:
            s1 = db.execute(
                text("SELECT status FROM gift_card_orders WHERE id = :oid"),
                {"oid": churned["order_id"]},
            ).scalar()
            s2 = db.execute(
                text("SELECT status FROM gift_card_orders WHERE id = :oid"),
                {"oid": subscribed["order_id"]},
            ).scalar()
            # Audit H3: churned orders use the dedicated 'churned' status.
            assert s1 == "churned"
            # subscribed order stays pending — issuance happens async via the
            # background task kicked by the /issue endpoint
            assert s2 == "pending"


class TestMarkChurned:
    def test_sets_churned_status(self, session_factory, make_referral_order):
        """Audit H3: mark_churned must write 'churned', not 'failed'."""
        data = make_referral_order(eligible_delta_hours=-1, subscribed=False)

        with session_factory() as db:
            mark_churned(db, str(data["order_id"]))
            db.commit()

        with session_factory() as db:
            row = db.execute(
                text("SELECT status, failed_at FROM gift_card_orders WHERE id = :oid"),
                {"oid": data["order_id"]},
            ).first()
            assert row.status == "churned"
            assert row.failed_at is not None

    def test_idempotent(self, session_factory, make_referral_order):
        data = make_referral_order(eligible_delta_hours=-1, subscribed=False)

        with session_factory() as db:
            mark_churned(db, str(data["order_id"]))
            # Second call is a no-op because of the WHERE status='pending' guard
            mark_churned(db, str(data["order_id"]))
            db.commit()

        with session_factory() as db:
            count = db.execute(text("SELECT COUNT(*) FROM gift_card_orders WHERE status = 'churned'")).scalar()
            assert count == 1
