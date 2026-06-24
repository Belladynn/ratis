"""Concurrent-boost race test — audit RW-money finding 4.

``boost_cashback`` read ``boost_applied`` with a plain SELECT and only
flipped the flag at the very end (``mark_boost_applied``). Two parallel
``POST /cashback/boost`` on the same CREDIT both saw ``boost_applied=false``,
both debited CAB, both inserted a BOOST row and both credited the cashback
balance — the user got the boost twice for one CREDIT.

Fix : ``mark_boost_applied`` is now an atomic conditional UPDATE
(``WHERE id=:tx AND boost_applied=false``) returning whether it won the
race, and ``boost_cashback`` flips the flag FIRST — before debiting CAB,
inserting the BOOST row or crediting the balance. The flag is the gate.

Harness mirrors ``test_missions_concurrency.py`` : two independent DB
sessions on the live engine (the savepoint ``db`` fixture shares one
connection and cannot model a real row-level race).
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text

from tests.conftest import (
    TestingSessionLocal,
    make_affiliate_offer,
    make_brand,
    make_product,
    make_user,
)

_REWARDS_CFG = {
    "cashback_boost_window_hours": 12,
    "cashback_boost_cab_rate": 1.2,
}


def _open_session():
    return TestingSessionLocal()


def _seed_boostable_credit(setup, *, cab_balance: int = 1000):
    """Insert user (with CAB) + brand + product + offer + a fresh CREDIT tx.

    Returns ``(user_id, tx_id, brand_id, mission_cleanup_ids)``.
    """
    user_id = make_user(setup)
    setup.execute(
        text("UPDATE user_cab_balance SET balance = :b WHERE user_id = :uid"),
        {"b": cab_balance, "uid": user_id},
    )
    brand_id = make_brand(setup)
    ean = make_product(setup, brand_id=brand_id)
    offer_id = make_affiliate_offer(setup, product_ean=ean, brand_id=brand_id)
    tx_id = uuid.uuid4()
    setup.execute(
        text(
            "INSERT INTO cashback_transactions "
            "    (id, user_id, type, amount, status, product_ean, "
            "     affiliate_offer_id, boost_applied, created_at) "
            "VALUES (:id, :uid, 'CREDIT', 25, 'pending', :ean, :oid, "
            "        false, now())"
        ),
        {"id": tx_id, "uid": user_id, "ean": ean, "oid": offer_id},
    )
    setup.commit()
    return user_id, tx_id, brand_id, ean


def _cleanup(user_id, ean, brand_id):
    c = _open_session()
    try:
        c.execute(
            text("DELETE FROM cashback_transactions WHERE user_id = :uid"),
            {"uid": user_id},
        )
        c.execute(
            text("DELETE FROM cabecoin_transactions WHERE user_id = :uid"),
            {"uid": user_id},
        )
        c.execute(
            text("DELETE FROM affiliate_offers WHERE product_ean = :ean"),
            {"ean": ean},
        )
        c.execute(text("DELETE FROM products WHERE ean = :ean"), {"ean": ean})
        c.execute(text("DELETE FROM brands WHERE id = :bid"), {"bid": brand_id})
        c.execute(
            text("DELETE FROM user_cashback_balance WHERE user_id = :uid"),
            {"uid": user_id},
        )
        c.execute(
            text("DELETE FROM user_cab_balance WHERE user_id = :uid"),
            {"uid": user_id},
        )
        c.execute(text("DELETE FROM users WHERE id = :uid"), {"uid": user_id})
        c.commit()
    finally:
        c.close()


def test_concurrent_boost_credits_only_once():
    """True interleaved race → exactly one BOOST, one debit, one credit.

    Both sessions read ``boost_applied=false`` (the plain pre-check) BEFORE
    either commits — the realistic two-request race. Session A then runs
    the full boost and commits. Session B runs second : the atomic
    ``mark_boost_applied`` flag-flip — now the FIRST mutation — matches 0
    rows (A already set it) → B raises ``AlreadyBoosted`` and performs NO
    debit / NO insert / NO credit.

    Pre-fix the flag was flipped LAST with an unconditional UPDATE : B
    would debit CAB, insert a second BOOST and double-credit the balance.
    """
    from repositories.cashback_repository import get_cashback_tx
    from services.cashback_service import AlreadyBoosted, boost_cashback

    setup = _open_session()
    try:
        user_id, tx_id, brand_id, ean = _seed_boostable_credit(setup)
    finally:
        setup.close()

    s1 = _open_session()
    s2 = _open_session()
    try:
        # Both sessions observe the pre-boost state (boost_applied=false)
        # before either mutates — the genuine concurrent-request window.
        assert get_cashback_tx(s1, tx_id)["boost_applied"] is False
        assert get_cashback_tx(s2, tx_id)["boost_applied"] is False

        result_a = boost_cashback(s1, user_id, tx_id, _REWARDS_CFG)
        s1.commit()
        assert result_a["boost_amount"] == 25
        assert result_a["boost_cost_cab"] == 30  # round(25 * 1.2)

        # B runs the full boost AFTER A committed. The atomic gate must
        # stop it cold — no CAB debit, no second BOOST row.
        with pytest.raises(AlreadyBoosted):
            boost_cashback(s2, user_id, tx_id, _REWARDS_CFG)
        s2.rollback()

        check = _open_session()
        try:
            # Exactly one BOOST row.
            n_boost = check.execute(
                text(
                    "SELECT COUNT(*) FROM cashback_transactions WHERE parent_transaction_id = :pid AND type = 'BOOST'"
                ),
                {"pid": tx_id},
            ).scalar()
            assert n_boost == 1
            # CAB debited exactly once : 1000 - 30 = 970.
            cab = check.execute(
                text("SELECT balance FROM user_cab_balance WHERE user_id = :uid"),
                {"uid": user_id},
            ).scalar()
            assert cab == 970
            # Cashback balance credited exactly once : 25.
            cb = check.execute(
                text("SELECT balance FROM user_cashback_balance WHERE user_id = :uid"),
                {"uid": user_id},
            ).scalar()
            assert cb == 25
        finally:
            check.close()
    finally:
        s1.close()
        s2.close()
        _cleanup(user_id, ean, brand_id)


def test_mark_boost_applied_is_atomic_conditional():
    """``mark_boost_applied`` returns True only for the first caller.

    A second call on an already-boosted tx returns False — proves the
    UPDATE carries the ``boost_applied=false`` guard.
    """
    from repositories.cashback_repository import mark_boost_applied

    setup = _open_session()
    try:
        user_id, tx_id, brand_id, ean = _seed_boostable_credit(setup)
    finally:
        setup.close()

    s = _open_session()
    try:
        assert mark_boost_applied(s, tx_id) is True
        # Second flip on the same tx — already true → no row matched.
        assert mark_boost_applied(s, tx_id) is False
        s.commit()
    finally:
        s.close()
        _cleanup(user_id, ean, brand_id)
