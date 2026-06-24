# batch/ratis_batch_reconciliation/tests/test_reconciliation_processing_gift_cards.py
"""TDD — reconcile_processing_gift_card_orders (audit H4 — Runa PROCESSING re-poll).

A non-shop gift-card order left ``pending`` because Runa answered
``PROCESSING`` has ``eligible_at`` NULL and is touched by neither
``reconcile_pending_gift_card_orders`` (shop-only) nor
``reconcile_deferred_gift_card_orders`` (``eligible_at`` non-null) — it
stays stuck forever, holding its fiscal-cap reservation. This job
re-triggers issuance via the internal ``/issue`` endpoint.

Covers:
- A stuck PROCESSING non-shop order (eligible_at NULL, old) → re-triggered.
- A shop_purchase pending order → IGNORED (left to reconcile_pending).
- A deferred order (eligible_at set) → IGNORED (left to reconcile_deferred).
- A fresh non-shop order under the grace threshold → IGNORED.
- dry_run=True → no HTTP call, count returned.
"""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock, patch

from reconciliation.processing_gift_cards import reconcile_processing_gift_card_orders
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


def _seed_order(
    session_factory,
    *,
    source_type: str = "annual_subscription",
    created_offset: str = "now() - interval '2 hours'",
    eligible_at: str = "NULL",
    denomination: int = 5000,
) -> tuple[uuid.UUID, uuid.UUID]:
    """Seed a user + pending gift-card order.

    ``created_offset`` and ``eligible_at`` are inlined SQL expressions.
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
                "email": f"test_processing_{user_id.hex[:8]}@example.com",
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
                f"VALUES (:id, :uid, :bid, :denom, 'pending', :stype, "
                f"        :sref, {created_offset}, {eligible_at})"
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
# a: stuck PROCESSING non-shop order → re-triggered
# ---------------------------------------------------------------------------


def test_stuck_processing_order_is_retriggered(session_factory, make_user, assert_no_pending_changes):
    """A pending non-shop order, eligible_at NULL, older than the grace
    interval → HTTP POST fired to the internal /issue endpoint, count=1."""
    _user_id, order_id = _seed_order(
        session_factory,
        source_type="annual_subscription",
        created_offset="now() - interval '3 hours'",
        eligible_at="NULL",
    )

    mock_resp = MagicMock()
    mock_resp.raise_for_status.return_value = None

    with patch("httpx.post", return_value=mock_resp) as mock_post, session_factory() as db:
        count = reconcile_processing_gift_card_orders(db, dry_run=False)

    assert count == 1
    mock_post.assert_called_once()
    call_url = mock_post.call_args[0][0]
    assert f"/rewards/gift-cards/{order_id}/issue" in call_url
    call_kwargs = mock_post.call_args[1]
    assert call_kwargs["headers"]["Authorization"].startswith("Bearer ")


def test_battlepass_milestone_order_is_retriggered(session_factory, make_user, assert_no_pending_changes):
    """The other non-shop source type — battlepass_milestone — is also covered."""
    _user_id, order_id = _seed_order(
        session_factory,
        source_type="battlepass_milestone",
        created_offset="now() - interval '6 hours'",
        eligible_at="NULL",
    )

    mock_resp = MagicMock()
    mock_resp.raise_for_status.return_value = None

    with patch("httpx.post", return_value=mock_resp) as mock_post, session_factory() as db:
        count = reconcile_processing_gift_card_orders(db, dry_run=False)

    assert count == 1
    mock_post.assert_called_once()
    assert f"/rewards/gift-cards/{order_id}/issue" in mock_post.call_args[0][0]


# ---------------------------------------------------------------------------
# b: shop_purchase pending order → IGNORED (left to reconcile_pending)
# ---------------------------------------------------------------------------


def test_shop_purchase_order_is_ignored(session_factory, make_user, assert_no_pending_changes):
    """A pending shop_purchase order → not touched by this job."""
    _user_id, order_id = _seed_order(
        session_factory,
        source_type="shop_purchase",
        created_offset="now() - interval '3 hours'",
        eligible_at="NULL",
    )

    with patch("httpx.post") as mock_post, session_factory() as db:
        count = reconcile_processing_gift_card_orders(db, dry_run=False)

    assert count == 0
    mock_post.assert_not_called()

    with session_factory() as db:
        status = db.execute(
            text("SELECT status FROM gift_card_orders WHERE id = :id"),
            {"id": order_id},
        ).scalar()
    assert status == "pending"  # untouched


# ---------------------------------------------------------------------------
# c: deferred order (eligible_at set) → IGNORED (left to reconcile_deferred)
# ---------------------------------------------------------------------------


def test_deferred_order_is_ignored(session_factory, make_user, assert_no_pending_changes):
    """A pending non-shop order with eligible_at set → handled by
    reconcile_deferred_gift_card_orders, not this job."""
    _user_id, _order_id = _seed_order(
        session_factory,
        source_type="annual_subscription",
        created_offset="now() - interval '3 hours'",
        eligible_at="now() - interval '1 day'",
    )

    with patch("httpx.post") as mock_post, session_factory() as db:
        count = reconcile_processing_gift_card_orders(db, dry_run=False)

    assert count == 0
    mock_post.assert_not_called()


# ---------------------------------------------------------------------------
# d: fresh non-shop order under the grace threshold → IGNORED
# ---------------------------------------------------------------------------


def test_fresh_order_under_threshold_is_ignored(session_factory, make_user, assert_no_pending_changes):
    """A non-shop order created minutes ago (background issuance possibly
    still running) → not disturbed."""
    _user_id, _order_id = _seed_order(
        session_factory,
        source_type="annual_subscription",
        created_offset="now() - interval '5 minutes'",
        eligible_at="NULL",
    )

    with patch("httpx.post") as mock_post, session_factory() as db:
        count = reconcile_processing_gift_card_orders(db, dry_run=False)

    assert count == 0
    mock_post.assert_not_called()


# ---------------------------------------------------------------------------
# e: dry_run=True → no HTTP call, count returned
# ---------------------------------------------------------------------------


def test_dry_run_no_http_call(session_factory, make_user, assert_no_pending_changes):
    """dry_run=True → count returned but no HTTP POST fired."""
    _user_id, _order_id = _seed_order(
        session_factory,
        source_type="annual_subscription",
        created_offset="now() - interval '3 hours'",
        eligible_at="NULL",
    )

    with patch("httpx.post") as mock_post, session_factory() as db:
        count = reconcile_processing_gift_card_orders(db, dry_run=True)

    assert count == 1
    mock_post.assert_not_called()
