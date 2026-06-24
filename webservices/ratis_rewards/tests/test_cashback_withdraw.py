"""
TDD — POST /rewards/cashback/withdraw

Amounts are INTEGER centimes. 10.00€ = 1000 centimes.
Minimum withdrawal: 1000 centimes (cashback_min_withdrawal in ratis_settings.json).
"""

from __future__ import annotations

import uuid

from sqlalchemy import text

from tests.conftest import make_user

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _set_cashback_balance(db, user_id: uuid.UUID, amount: int) -> None:
    db.execute(
        text("UPDATE user_cashback_balance SET balance = :bal WHERE user_id = :uid"),
        {"bal": amount, "uid": user_id},
    )
    db.commit()


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_withdraw_success(user_client, db):
    """Balance 1500, withdraw 1000 → balance 500, records created."""
    client_inst, bypass = user_client
    user_id = make_user(db)
    _set_cashback_balance(db, user_id, 1500)
    bypass(user_id)

    resp = client_inst.post("/api/v1/rewards/cashback/withdraw", json={"amount": 1000})
    assert resp.status_code == 201
    data = resp.json()
    assert data["status"] == "pending"
    assert data["amount"] == 1000
    assert "withdrawal_id" in data

    # Balance debited
    bal = db.execute(
        text("SELECT balance FROM user_cashback_balance WHERE user_id = :uid"),
        {"uid": user_id},
    ).scalar()
    assert bal == 500

    # cashback_transaction: type=WITHDRAWAL, status=confirmed, distributed_at set
    tx = db.execute(
        text(
            "SELECT type, status, amount, distributed_at "
            "FROM cashback_transactions WHERE user_id = :uid AND type = 'WITHDRAWAL'"
        ),
        {"uid": user_id},
    ).first()
    assert tx is not None
    assert tx.status == "confirmed"
    assert tx.amount == 1000
    assert tx.distributed_at is not None

    # cashback_withdrawals: status=pending, cashback_transaction_id linked
    wd = db.execute(
        text("SELECT status, cashback_transaction_id, amount FROM cashback_withdrawals WHERE user_id = :uid"),
        {"uid": user_id},
    ).first()
    assert wd is not None
    assert wd.status == "pending"
    assert wd.cashback_transaction_id is not None
    assert wd.amount == 1000


def test_withdraw_exact_minimum(user_client, db):
    """Withdraw exactly the minimum (1000 centimes) → success, balance zeroed."""
    client_inst, bypass = user_client
    user_id = make_user(db)
    _set_cashback_balance(db, user_id, 1000)
    bypass(user_id)

    resp = client_inst.post("/api/v1/rewards/cashback/withdraw", json={"amount": 1000})
    assert resp.status_code == 201

    bal = db.execute(
        text("SELECT balance FROM user_cashback_balance WHERE user_id = :uid"),
        {"uid": user_id},
    ).scalar()
    assert bal == 0


# ---------------------------------------------------------------------------
# Error cases
# ---------------------------------------------------------------------------


def test_withdraw_below_minimum(user_client, db):
    """Amount below minimum → 422 below_minimum, balance untouched."""
    client_inst, bypass = user_client
    user_id = make_user(db)
    _set_cashback_balance(db, user_id, 1500)
    bypass(user_id)

    resp = client_inst.post("/api/v1/rewards/cashback/withdraw", json={"amount": 500})
    assert resp.status_code == 422
    assert resp.json()["detail"] == "below_minimum"

    bal = db.execute(
        text("SELECT balance FROM user_cashback_balance WHERE user_id = :uid"),
        {"uid": user_id},
    ).scalar()
    assert bal == 1500


def test_withdraw_insufficient_balance(user_client, db):
    """Balance below requested amount → 422 insufficient_balance."""
    client_inst, bypass = user_client
    user_id = make_user(db)
    _set_cashback_balance(db, user_id, 500)
    bypass(user_id)

    resp = client_inst.post("/api/v1/rewards/cashback/withdraw", json={"amount": 1000})
    assert resp.status_code == 422
    assert resp.json()["detail"] == "insufficient_balance"

    # No withdrawal created
    count = db.execute(
        text("SELECT COUNT(*) FROM cashback_withdrawals WHERE user_id = :uid"),
        {"uid": user_id},
    ).scalar()
    assert count == 0


def test_withdraw_atomic_second_depletes_balance(user_client, db):
    """Two withdrawals on exact balance: first succeeds, second fails atomically."""
    client_inst, bypass = user_client
    user_id = make_user(db)
    _set_cashback_balance(db, user_id, 1000)
    bypass(user_id)

    resp1 = client_inst.post("/api/v1/rewards/cashback/withdraw", json={"amount": 1000})
    assert resp1.status_code == 201

    resp2 = client_inst.post("/api/v1/rewards/cashback/withdraw", json={"amount": 1000})
    assert resp2.status_code == 422
    assert resp2.json()["detail"] == "insufficient_balance"

    # Only one withdrawal created
    count = db.execute(
        text("SELECT COUNT(*) FROM cashback_withdrawals WHERE user_id = :uid"),
        {"uid": user_id},
    ).scalar()
    assert count == 1


def test_withdraw_stores_payment_provider_ref(user_client, db):
    """Après le retrait, payment_provider_ref et provider_initiated_at doivent être non-NULL."""
    client_inst, bypass = user_client
    user_id = make_user(db)
    _set_cashback_balance(db, user_id, 2000)
    bypass(user_id)

    resp = client_inst.post("/api/v1/rewards/cashback/withdraw", json={"amount": 1000})
    assert resp.status_code == 201
    withdrawal_id = resp.json()["withdrawal_id"]

    wd = db.execute(
        text("SELECT payment_provider_ref, provider_initiated_at FROM cashback_withdrawals WHERE id = :wid"),
        {"wid": withdrawal_id},
    ).first()
    assert wd is not None
    assert wd.payment_provider_ref is not None
    assert wd.payment_provider_ref.startswith("sandbox-")
    assert wd.provider_initiated_at is not None


def test_withdraw_payout_error_keeps_pending_with_null_ref(user_client, db, monkeypatch):
    """PayoutError → route returns 201, withdrawal stays pending with NULL ref (reconciliation batch picks it up)."""
    from ratis_core.payout_client import PayoutError

    def _raise(*a, **kw):
        raise PayoutError("stripe down")

    monkeypatch.setattr("routes.rewards.cashback_withdraw.initiate_payout", _raise)

    client_inst, bypass = user_client
    user_id = make_user(db)
    _set_cashback_balance(db, user_id, 2000)
    bypass(user_id)

    resp = client_inst.post("/api/v1/rewards/cashback/withdraw", json={"amount": 1000})
    assert resp.status_code == 201  # still succeeds

    wd = db.execute(
        text("SELECT payment_provider_ref FROM cashback_withdrawals WHERE user_id = :uid"),
        {"uid": user_id},
    ).first()
    assert wd is not None
    assert wd.payment_provider_ref is None  # ref NOT stored — reconciliation will retry


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------


def test_withdraw_requires_auth(raw_client):
    resp = raw_client.post("/api/v1/rewards/cashback/withdraw", json={"amount": 1000})
    assert resp.status_code == 401
