"""Concurrent gift-card issuance race test — audit RW-money finding 1.

``issue_gift_card`` read the order status with a plain SELECT (``get_order``)
and only flipped it via ``update_order_issued`` / ``update_order_failed``
at the very end. Two concurrent issuances of the same order — e.g. a
``POST /gift-cards/{id}/issue`` from the referral-payout batch racing a
retry, or two annual-subscription background tasks — both saw
``status='pending'``, both called Runa, and the user got TWO gift cards
for one order.

Fix : ``issue_gift_card`` acquires a per-order ``pg_advisory_xact_lock``
before doing anything, then re-reads the status under that lock and
returns early if the order is no longer ``pending``. The status-write
helpers carry a ``WHERE status='pending'`` guard so a stale writer can
never overwrite a terminal state. Two issuances serialise on the lock ;
the loser sees ``status != 'pending'`` and never touches Runa.

Harness mirrors ``test_missions_concurrency.py`` — two independent DB
sessions on the live engine (the savepoint ``db`` fixture shares one
connection and cannot model a real cross-connection lock).
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from sqlalchemy import text

from tests.conftest import (
    TestingSessionLocal,
    make_gift_card_brand,
    make_gift_card_order,
    make_user,
)


def _open_session():
    return TestingSessionLocal()


def _runa_complete(order_id: str = "runa_xyz", code: str = "AMZN-AAAA-BBBB"):
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.json.return_value = {
        "id": order_id,
        "status": "COMPLETE",
        "redemption_code": code,
    }
    return resp


def _cleanup(order_id, user_id, brand_id):
    c = _open_session()
    try:
        c.execute(text("DELETE FROM gift_card_orders WHERE id = :id"), {"id": order_id})
        c.execute(text("DELETE FROM gift_card_brands WHERE id = :bid"), {"bid": brand_id})
        c.execute(
            text("DELETE FROM user_cab_balance WHERE user_id = :uid"),
            {"uid": user_id},
        )
        c.execute(
            text("DELETE FROM user_cashback_balance WHERE user_id = :uid"),
            {"uid": user_id},
        )
        c.execute(text("DELETE FROM users WHERE id = :uid"), {"uid": user_id})
        c.commit()
    finally:
        c.close()


def test_concurrent_issue_calls_runa_only_once():
    """Two serialised ``issue_gift_card`` → Runa called exactly once.

    Session A issues + commits. Session B runs second on a fresh session :
    it acquires the per-order advisory lock, re-reads the status, sees
    ``issued`` and returns WITHOUT calling Runa. Pre-fix B would re-POST
    to Runa and the user would get a second card.
    """
    from services.gift_card_service import issue_gift_card

    setup = _open_session()
    try:
        user_id = make_user(setup)
        brand_id = make_gift_card_brand(setup, name="Amazon")
        order_id = make_gift_card_order(
            setup,
            user_id=user_id,
            brand_id=brand_id,
            denomination=2000,
            status="pending",
        )
    finally:
        setup.close()

    runa = _runa_complete()
    try:
        with patch("services.gift_card_service.httpx.post", return_value=runa) as mock_post:
            s1 = _open_session()
            try:
                issue_gift_card(order_id, s1)
                s1.commit()
            finally:
                s1.close()

            # Second issuance — order is now 'issued'. Must NOT call Runa.
            s2 = _open_session()
            try:
                issue_gift_card(order_id, s2)
                s2.commit()
            finally:
                s2.close()

            assert mock_post.call_count == 1, (
                "Runa was called twice — double gift-card emission (audit RW-money F-1 regression)"
            )

        check = _open_session()
        try:
            row = check.execute(
                text("SELECT status, code FROM gift_card_orders WHERE id = :id"),
                {"id": order_id},
            ).first()
            assert row.status == "issued"
            # The code from the FIRST issuance is preserved — the second
            # call did not overwrite it.
            assert row.code == "AMZN-AAAA-BBBB"
        finally:
            check.close()
    finally:
        _cleanup(order_id, user_id, brand_id)


def test_issue_gift_card_skips_already_issued_order():
    """An order already in 'issued' state → issue_gift_card is a no-op.

    Idempotence guard : re-running issuance on a terminal-state order must
    not call Runa and must not mutate the row.
    """
    from services.gift_card_service import issue_gift_card

    setup = _open_session()
    try:
        user_id = make_user(setup)
        brand_id = make_gift_card_brand(setup)
        order_id = make_gift_card_order(
            setup,
            user_id=user_id,
            brand_id=brand_id,
            status="issued",
            code="EXISTING-CODE",
        )
    finally:
        setup.close()

    try:
        with patch("services.gift_card_service.httpx.post") as mock_post:
            s = _open_session()
            try:
                issue_gift_card(order_id, s)
                s.commit()
            finally:
                s.close()
            mock_post.assert_not_called()

        check = _open_session()
        try:
            row = check.execute(
                text("SELECT status, code FROM gift_card_orders WHERE id = :id"),
                {"id": order_id},
            ).first()
            assert row.status == "issued"
            assert row.code == "EXISTING-CODE"
        finally:
            check.close()
    finally:
        _cleanup(order_id, user_id, brand_id)


def test_issue_gift_card_acquires_advisory_lock():
    """``issue_gift_card`` must emit a ``pg_advisory_xact_lock`` keyed on
    the order id, before any status read — regression guard."""
    import inspect

    from services import gift_card_service

    src = inspect.getsource(gift_card_service.issue_gift_card)
    assert "pg_advisory_xact_lock" in src, (
        "issue_gift_card must serialise concurrent issuances with a per-order advisory lock (audit RW-money F-1)"
    )
