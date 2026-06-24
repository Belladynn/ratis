# batch/ratis_batch_reconciliation/tests/test_reconciliation_deferred_gift_cards.py
"""TDD — reconcile_deferred_gift_card_orders (audit H4, Part 6b).

Covers:
- A pending annual_subscription order with eligible_at in the past → re-issued
  (HTTP POST called, count = 1).
- A pending referral_reward order with eligible_at in the past → IGNORED
  (handled by ratis_batch_referral_payout, not this job).
- A pending annual_subscription order with eligible_at in the future → ignored.
- dry_run=True → no HTTP call, count returned.
"""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock, patch

from reconciliation.deferred_gift_cards import reconcile_deferred_gift_card_orders
from sqlalchemy import text

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _insert_gift_card_brand(session_factory) -> uuid.UUID:
    """Insert a minimal gift_card_brands row."""
    brand_id = uuid.uuid4()
    with session_factory() as db:
        db.execute(
            text(
                "INSERT INTO gift_card_brands "
                "    (id, name, provider_brand_id, is_active, created_at) "
                "VALUES (:id, :name, :pbid, true, now())"
            ),
            {
                "id": brand_id,
                "name": f"Brand {brand_id.hex[:6]}",
                "pbid": f"runa_{brand_id.hex[:6]}",
            },
        )
        db.commit()
    return brand_id


def _seed_deferred_order(
    session_factory,
    *,
    source_type: str = "annual_subscription",
    eligible_at_offset: str = "now() - interval '1 day'",
    denomination: int = 5000,
) -> tuple[uuid.UUID, uuid.UUID]:
    """Seed a user + pending gift-card order with eligible_at set.

    Returns (user_id, order_id).
    """
    brand_id = _insert_gift_card_brand(session_factory)
    user_id = uuid.uuid4()
    order_id = uuid.uuid4()

    with session_factory() as db:
        from ratis_core.identifiers import generate_support_id

        db.execute(
            text(
                "INSERT INTO users (id, email, support_id, account_type, "
                "                  is_deleted) "
                "VALUES (:id, :email, :sid, 'oauth', false)"
            ),
            {
                "id": user_id,
                "email": f"test_deferred_{user_id.hex[:8]}@example.com",
                "sid": generate_support_id(),
            },
        )
        db.execute(
            text("INSERT INTO user_cab_balance (user_id, balance) VALUES (:uid, 0)"),
            {"uid": user_id},
        )
        db.execute(
            text("INSERT INTO user_cashback_balance (user_id, balance) VALUES (:uid, 0)"),
            {"uid": user_id},
        )
        db.execute(
            text(
                "INSERT INTO gift_card_orders "
                "    (id, user_id, brand_id, denomination, status, source_type, "
                f"     source_ref_id, created_at, eligible_at) "
                "VALUES (:id, :uid, :bid, :denom, 'pending', :stype, "
                f"        :sref, now() - interval '2 days', {eligible_at_offset})"
            ),
            {
                "id": order_id,
                "uid": user_id,
                "bid": brand_id,
                "denom": denomination,
                "stype": source_type,
                "sref": str(uuid.uuid4()),
            },
        )
        db.commit()

    return user_id, order_id


# ---------------------------------------------------------------------------
# H4-6b-a: eligible annual_subscription order → re-issued (HTTP POST called)
# ---------------------------------------------------------------------------


def test_deferred_annual_subscription_triggers_issue(session_factory, make_user, assert_no_pending_changes):
    """A pending annual_subscription with eligible_at past → HTTP POST fired, count=1."""
    _user_id, order_id = _seed_deferred_order(
        session_factory,
        source_type="annual_subscription",
        eligible_at_offset="now() - interval '1 day'",
    )

    mock_resp = MagicMock()
    mock_resp.raise_for_status.return_value = None

    with patch("httpx.post", return_value=mock_resp) as mock_post, session_factory() as db:
        count = reconcile_deferred_gift_card_orders(db, dry_run=False)

    assert count == 1
    mock_post.assert_called_once()
    # Verify the call targets the correct endpoint pattern
    call_url = mock_post.call_args[0][0]
    assert f"/rewards/gift-cards/{order_id}/issue" in call_url
    # Verify auth header
    call_kwargs = mock_post.call_args[1]
    assert "Authorization" in call_kwargs["headers"]
    assert call_kwargs["headers"]["Authorization"].startswith("Bearer ")


# ---------------------------------------------------------------------------
# H4-6b-b: referral_reward deferred order → IGNORED
# ---------------------------------------------------------------------------


def test_deferred_referral_reward_ignored(session_factory, make_user, assert_no_pending_changes):
    """A pending referral_reward order with eligible_at past → not touched by this job."""
    _user_id, order_id = _seed_deferred_order(
        session_factory,
        source_type="referral_reward",
        eligible_at_offset="now() - interval '1 day'",
    )

    with patch("httpx.post") as mock_post, session_factory() as db:
        count = reconcile_deferred_gift_card_orders(db, dry_run=False)

    assert count == 0
    mock_post.assert_not_called()

    with session_factory() as db:
        status = db.execute(
            text("SELECT status FROM gift_card_orders WHERE id = :id"),
            {"id": order_id},
        ).scalar()
    assert status == "pending"  # untouched


# ---------------------------------------------------------------------------
# H4-6b-c: order with eligible_at in the future → ignored
# ---------------------------------------------------------------------------


def test_deferred_future_eligible_at_ignored(session_factory, make_user, assert_no_pending_changes):
    """A pending order whose eligible_at is still in the future → not triggered."""
    _user_id, _order_id = _seed_deferred_order(
        session_factory,
        source_type="annual_subscription",
        eligible_at_offset="now() + interval '30 days'",
    )

    with patch("httpx.post") as mock_post, session_factory() as db:
        count = reconcile_deferred_gift_card_orders(db, dry_run=False)

    assert count == 0
    mock_post.assert_not_called()


# ---------------------------------------------------------------------------
# H4-6b-d: dry_run=True → no HTTP call, count returned
# ---------------------------------------------------------------------------


def test_deferred_dry_run_no_http_call(session_factory, make_user, assert_no_pending_changes):
    """dry_run=True → count returned but no HTTP POST fired."""
    _user_id, _order_id = _seed_deferred_order(
        session_factory,
        source_type="annual_subscription",
        eligible_at_offset="now() - interval '1 day'",
    )

    with patch("httpx.post") as mock_post, session_factory() as db:
        count = reconcile_deferred_gift_card_orders(db, dry_run=True)

    assert count == 1
    mock_post.assert_not_called()
