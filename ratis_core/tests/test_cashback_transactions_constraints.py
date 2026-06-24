"""Direct PG-level CHECK assertion tests for ``cashback_transactions`` — Bug 4 + Pattern A.

The two CHECKs covered here :

* ``credit_requires_offer``  : ``type IN ('CREDIT','BOOST')`` ⇒ ``affiliate_offer_id NOT NULL``
* ``credit_requires_product`` : ``type IN ('CREDIT','BOOST')`` ⇒ ``product_ean NOT NULL``

WITHDRAWAL rows are explicitly excluded from both CHECK predicates — the
withdrawal accounting fact has no offer / product context. These tests pin
that behaviour so a future migration cannot tighten the rule by accident.

Pre-Pattern A roll-out these CHECKs lived only in PG ; the ORM mirror lands
together with this test file (cf. ``DEFERRED_PG_ONLY_CONSTRAINTS`` cleanup
in ``test_schema_sync``).
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

# ---------------------------------------------------------------------------
# Local seed helpers — ratis_core/tests does not share factories with the
# rewards service. Keep the inserts minimal and CHECK-respecting so the test
# scenario can isolate exactly one constraint at a time.
# ---------------------------------------------------------------------------


def _make_user(db: Any) -> uuid.UUID:
    from ratis_core.identifiers import generate_support_id

    uid = uuid.uuid4()
    db.execute(
        text(
            "INSERT INTO users "
            "    (id, email, support_id, account_type, "
            "     is_deleted, created_at, updated_at) "
            "VALUES (:id, :email, :sid, 'oauth', false, now(), now())"
        ),
        {
            "id": uid,
            "email": f"u-{uid.hex[:8]}@example.com",
            "sid": generate_support_id(),
        },
    )
    return uid


def _make_brand(db: Any) -> uuid.UUID:
    bid = uuid.uuid4()
    db.execute(
        text("INSERT INTO brands (id, name, slug) VALUES (:id, :name, :slug)"),
        {"id": bid, "name": f"Brand-{bid.hex[:6]}", "slug": f"brand-{bid.hex[:6]}"},
    )
    return bid


def _make_product(db: Any, *, brand_id: uuid.UUID) -> str:
    ean = str(uuid.uuid4().int)[:13]
    db.execute(
        text(
            "INSERT INTO products (ean, name, source, brand_id, created_at, updated_at) "
            "VALUES (:ean, 'p', 'off', :bid, now(), now())"
        ),
        {"ean": ean, "bid": brand_id},
    )
    return ean


def _make_offer(db: Any, *, product_ean: str, brand_id: uuid.UUID) -> uuid.UUID:
    oid = uuid.uuid4()
    db.execute(
        text(
            "INSERT INTO affiliate_offers "
            "    (id, provider, external_id, product_ean, brand_id, cashback_rate, valid_from) "
            "VALUES (:id, 'affilae', :ext, :ean, :bid, 0.10, now() - interval '1 hour')"
        ),
        {
            "id": oid,
            "ext": f"ext-{oid.hex[:8]}",
            "ean": product_ean,
            "bid": brand_id,
        },
    )
    return oid


def _insert_cashback_tx(
    db: Any,
    *,
    user_id: uuid.UUID,
    type_: str,
    product_ean: str | None,
    affiliate_offer_id: uuid.UUID | None,
    amount: int = 100,
    status: str = "pending",
) -> uuid.UUID:
    """Insert + flush a ``cashback_transactions`` row.

    Caller is expected to seed any FK parents up-front. ``flush()`` surfaces
    any CHECK violation immediately rather than at COMMIT-time which would
    taint the outer SAVEPOINT.
    """
    tx_id = uuid.uuid4()
    db.execute(
        text(
            "INSERT INTO cashback_transactions "
            "    (id, user_id, type, amount, status, product_ean, affiliate_offer_id, "
            "     boost_applied, created_at) "
            "VALUES (:id, :uid, :type, :amount, :status, :ean, :oid, false, now())"
        ),
        {
            "id": tx_id,
            "uid": user_id,
            "type": type_,
            "amount": amount,
            "status": status,
            "ean": product_ean,
            "oid": affiliate_offer_id,
        },
    )
    db.flush()
    return tx_id


# ============================================================
# credit_requires_offer
# ============================================================


def test_credit_without_offer_violates_credit_requires_offer(db):
    """CREDIT row with affiliate_offer_id NULL must be rejected at flush()."""
    uid = _make_user(db)
    bid = _make_brand(db)
    ean = _make_product(db, brand_id=bid)
    with pytest.raises(IntegrityError) as exc_info:
        _insert_cashback_tx(
            db,
            user_id=uid,
            type_="CREDIT",
            product_ean=ean,
            affiliate_offer_id=None,
        )
    msg = str(exc_info.value.orig).lower()
    assert "credit_requires_offer" in msg or "check constraint" in msg
    db.rollback()


def test_boost_without_offer_violates_credit_requires_offer(db):
    """BOOST row with affiliate_offer_id NULL must be rejected at flush()."""
    uid = _make_user(db)
    bid = _make_brand(db)
    ean = _make_product(db, brand_id=bid)
    with pytest.raises(IntegrityError) as exc_info:
        _insert_cashback_tx(
            db,
            user_id=uid,
            type_="BOOST",
            product_ean=ean,
            affiliate_offer_id=None,
        )
    msg = str(exc_info.value.orig).lower()
    assert "credit_requires_offer" in msg or "check constraint" in msg
    db.rollback()


# ============================================================
# credit_requires_product
# ============================================================


def test_credit_without_product_violates_credit_requires_product(db):
    """CREDIT row with product_ean NULL must be rejected at flush()."""
    uid = _make_user(db)
    bid = _make_brand(db)
    ean = _make_product(db, brand_id=bid)
    oid = _make_offer(db, product_ean=ean, brand_id=bid)
    with pytest.raises(IntegrityError) as exc_info:
        _insert_cashback_tx(
            db,
            user_id=uid,
            type_="CREDIT",
            product_ean=None,
            affiliate_offer_id=oid,
        )
    msg = str(exc_info.value.orig).lower()
    assert "credit_requires_product" in msg or "check constraint" in msg
    db.rollback()


def test_boost_without_product_violates_credit_requires_product(db):
    """BOOST row with product_ean NULL must be rejected at flush()."""
    uid = _make_user(db)
    bid = _make_brand(db)
    ean = _make_product(db, brand_id=bid)
    oid = _make_offer(db, product_ean=ean, brand_id=bid)
    with pytest.raises(IntegrityError) as exc_info:
        _insert_cashback_tx(
            db,
            user_id=uid,
            type_="BOOST",
            product_ean=None,
            affiliate_offer_id=oid,
        )
    msg = str(exc_info.value.orig).lower()
    assert "credit_requires_product" in msg or "check constraint" in msg
    db.rollback()


def test_credit_with_both_null_violates_a_check(db):
    """CREDIT row with BOTH columns NULL must be rejected — either CHECK fires first."""
    uid = _make_user(db)
    with pytest.raises(IntegrityError) as exc_info:
        _insert_cashback_tx(
            db,
            user_id=uid,
            type_="CREDIT",
            product_ean=None,
            affiliate_offer_id=None,
        )
    msg = str(exc_info.value.orig).lower()
    assert "credit_requires_offer" in msg or "credit_requires_product" in msg or "check constraint" in msg
    db.rollback()


# ============================================================
# Happy paths — accepted shapes
# ============================================================


def test_credit_with_offer_and_product_succeeds(db):
    """CREDIT row with both columns populated flushes cleanly."""
    uid = _make_user(db)
    bid = _make_brand(db)
    ean = _make_product(db, brand_id=bid)
    oid = _make_offer(db, product_ean=ean, brand_id=bid)
    tx_id = _insert_cashback_tx(
        db,
        user_id=uid,
        type_="CREDIT",
        product_ean=ean,
        affiliate_offer_id=oid,
    )
    assert tx_id is not None


def test_boost_with_offer_and_product_succeeds(db):
    """BOOST row with both columns populated flushes cleanly."""
    uid = _make_user(db)
    bid = _make_brand(db)
    ean = _make_product(db, brand_id=bid)
    oid = _make_offer(db, product_ean=ean, brand_id=bid)
    tx_id = _insert_cashback_tx(
        db,
        user_id=uid,
        type_="BOOST",
        product_ean=ean,
        affiliate_offer_id=oid,
    )
    assert tx_id is not None


def test_withdrawal_without_offer_or_product_succeeds(db):
    """WITHDRAWAL rows are not constrained — both columns may be NULL.

    The CHECK predicates are explicitly scoped to (CREDIT, BOOST). This test
    pins that behaviour : a withdrawal accounting fact carries no product /
    offer context, only the user's balance debit.
    """
    uid = _make_user(db)
    tx_id = _insert_cashback_tx(
        db,
        user_id=uid,
        type_="WITHDRAWAL",
        product_ean=None,
        affiliate_offer_id=None,
        status="confirmed",
    )
    assert tx_id is not None
