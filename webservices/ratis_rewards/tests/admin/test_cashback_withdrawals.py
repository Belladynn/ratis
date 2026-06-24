"""Admin cashback withdrawals endpoints — list / validate / refuse.

Financial-sensitive mutations (validate / refuse) require both
``ADMIN_API_KEY`` and ``X-Admin-TOTP`` (verify_totp_dep, see PR2 infra).

Listing is read-only: ADMIN_API_KEY only, no TOTP.
"""

from __future__ import annotations

import uuid

from sqlalchemy import text

from tests.conftest import make_user


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _seed_withdrawal(
    db,
    *,
    user_id: uuid.UUID,
    amount: int = 1000,
    status: str = "pending",
) -> uuid.UUID:
    """Insert a minimal cashback_withdrawals row in `pending` (default).

    To force-seed a non-pending row (for idempotency tests) we INSERT pending
    then UPDATE — keeps CHECK constraints happy.
    """
    # Need a parent cashback_transactions row (FK RESTRICT).
    tx_id = uuid.uuid4()
    db.execute(
        text(
            "INSERT INTO cashback_transactions "
            "    (id, user_id, type, amount, status, distributed_at, boost_applied) "
            "VALUES (:id, :uid, 'WITHDRAWAL', :amount, 'confirmed', now(), false)"
        ),
        {"id": tx_id, "uid": user_id, "amount": amount},
    )
    wid = uuid.uuid4()
    db.execute(
        text(
            "INSERT INTO cashback_withdrawals "
            "    (id, user_id, cashback_transaction_id, amount, status) "
            "VALUES (:id, :uid, :tx, :amount, 'pending')"
        ),
        {"id": wid, "uid": user_id, "tx": tx_id, "amount": amount},
    )
    if status != "pending":
        if status == "processed":
            db.execute(
                text(
                    "UPDATE cashback_withdrawals "
                    "SET status = 'processed', processed_at = now(), "
                    "    payment_provider_ref = :ref, provider_initiated_at = now() "
                    "WHERE id = :id"
                ),
                {"id": wid, "ref": f"sandbox-{wid}"},
            )
        elif status == "failed":
            db.execute(
                text("UPDATE cashback_withdrawals SET status = 'failed', failure_reason = 'pre-seeded' WHERE id = :id"),
                {"id": wid},
            )
        else:  # pragma: no cover — defensive
            raise ValueError(f"unsupported seed status {status!r}")
    db.commit()
    return wid


def _balance(db, user_id):
    row = db.execute(
        text("SELECT balance FROM user_cashback_balance WHERE user_id = :uid"),
        {"uid": user_id},
    ).first()
    return row.balance if row else None


def _withdrawal(db, wid):
    return db.execute(
        text(
            "SELECT id, status, amount, payment_provider_ref, "
            "       provider_initiated_at, processed_at, failure_reason "
            "FROM cashback_withdrawals WHERE id = :id"
        ),
        {"id": wid},
    ).first()


# ---------------------------------------------------------------------------
# GET /api/v1/admin/cashback/withdrawals
# ---------------------------------------------------------------------------
class TestListWithdrawals:
    def test_list_paginated_default(self, admin_client, db):
        uid = make_user(db)
        for _ in range(3):
            _seed_withdrawal(db, user_id=uid, amount=500)
        resp = admin_client.get("/api/v1/admin/cashback/withdrawals")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["total"] == 3
        assert len(body["withdrawals"]) == 3
        # Each row carries the load-bearing fields.
        for w in body["withdrawals"]:
            assert "id" in w
            assert "user_id" in w
            assert w["amount"] == 500
            assert w["status"] == "pending"
            assert w["requested_at"] is not None

    def test_list_pagination_limit_offset(self, admin_client, db):
        uid = make_user(db)
        for _ in range(5):
            _seed_withdrawal(db, user_id=uid, amount=100)
        r1 = admin_client.get("/api/v1/admin/cashback/withdrawals?limit=2")
        assert r1.status_code == 200
        assert len(r1.json()["withdrawals"]) == 2
        assert r1.json()["total"] == 5
        r2 = admin_client.get("/api/v1/admin/cashback/withdrawals?limit=2&offset=4")
        assert r2.status_code == 200
        assert len(r2.json()["withdrawals"]) == 1

    def test_list_filters_by_status(self, admin_client, db):
        uid = make_user(db)
        _seed_withdrawal(db, user_id=uid, status="pending")
        _seed_withdrawal(db, user_id=uid, status="processed")
        _seed_withdrawal(db, user_id=uid, status="failed")
        r_pending = admin_client.get("/api/v1/admin/cashback/withdrawals?status=pending")
        assert r_pending.status_code == 200
        assert r_pending.json()["total"] == 1
        assert r_pending.json()["withdrawals"][0]["status"] == "pending"
        r_processed = admin_client.get("/api/v1/admin/cashback/withdrawals?status=processed")
        assert r_processed.json()["total"] == 1
        r_failed = admin_client.get("/api/v1/admin/cashback/withdrawals?status=failed")
        assert r_failed.json()["total"] == 1

    def test_list_does_not_require_totp(self, admin_client, db):
        """Read-only listing must work without X-Admin-TOTP header."""
        uid = make_user(db)
        _seed_withdrawal(db, user_id=uid)
        # admin_client bypasses ADMIN_API_KEY ; absence of TOTP header
        # must NOT trigger 401 here (read-only).
        resp = admin_client.get("/api/v1/admin/cashback/withdrawals")
        assert resp.status_code == 200

    def test_list_invalid_status_422(self, admin_client, db):
        resp = admin_client.get("/api/v1/admin/cashback/withdrawals?status=bogus")
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# PATCH /api/v1/admin/cashback/withdrawals/{id}/validate
# ---------------------------------------------------------------------------
class TestValidateWithdrawalTotpGate:
    def test_missing_totp_returns_401(self, admin_client, db, totp_secret):
        uid = make_user(db)
        wid = _seed_withdrawal(db, user_id=uid)
        resp = admin_client.patch(
            f"/api/v1/admin/cashback/withdrawals/{wid}/validate",
            json={},
            headers={"X-Admin-Operator": "test"},
        )
        assert resp.status_code == 401
        assert resp.json()["detail"] == "totp_required"

    def test_invalid_totp_returns_401(self, admin_client, db, totp_secret):
        uid = make_user(db)
        wid = _seed_withdrawal(db, user_id=uid)
        resp = admin_client.patch(
            f"/api/v1/admin/cashback/withdrawals/{wid}/validate",
            json={},
            headers={"X-Admin-Operator": "test", "X-Admin-TOTP": "000000"},
        )
        assert resp.status_code == 401
        assert resp.json()["detail"] == "totp_invalid"


class TestValidateWithdrawalEffects:
    def test_validate_marks_processed_and_records_provider_ref(self, admin_client, db, admin_headers):
        uid = make_user(db)
        wid = _seed_withdrawal(db, user_id=uid, amount=2500)
        resp = admin_client.patch(
            f"/api/v1/admin/cashback/withdrawals/{wid}/validate",
            json={},
            headers=admin_headers,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["status"] == "processed"
        assert body["payment_provider_ref"]  # non-empty
        row = _withdrawal(db, wid)
        assert row.status == "processed"
        assert row.processed_at is not None
        assert row.payment_provider_ref is not None
        assert row.provider_initiated_at is not None
        assert row.failure_reason is None

    def test_validate_calls_payout_provider_sandbox(self, admin_client, db, admin_headers):
        """Without PAYMENT_PROVIDER_KEY, payout_client returns sandbox-<id>."""
        uid = make_user(db)
        wid = _seed_withdrawal(db, user_id=uid)
        resp = admin_client.patch(
            f"/api/v1/admin/cashback/withdrawals/{wid}/validate",
            json={},
            headers=admin_headers,
        )
        assert resp.status_code == 200
        ref = resp.json()["payment_provider_ref"]
        assert ref.startswith("sandbox-")

    def test_validate_idempotent_already_processed_409(self, admin_client, db, admin_headers):
        uid = make_user(db)
        wid = _seed_withdrawal(db, user_id=uid, status="processed")
        resp = admin_client.patch(
            f"/api/v1/admin/cashback/withdrawals/{wid}/validate",
            json={},
            headers=admin_headers,
        )
        assert resp.status_code == 409
        assert resp.json()["detail"] == "already_resolved"

    def test_validate_unknown_id_404(self, admin_client, db, admin_headers):
        ghost = uuid.uuid4()
        resp = admin_client.patch(
            f"/api/v1/admin/cashback/withdrawals/{ghost}/validate",
            json={},
            headers=admin_headers,
        )
        assert resp.status_code == 404
        assert resp.json()["detail"] == "withdrawal_not_found"

    def test_validate_failed_withdrawal_409(self, admin_client, db, admin_headers):
        """Cannot validate a withdrawal already marked failed."""
        uid = make_user(db)
        wid = _seed_withdrawal(db, user_id=uid, status="failed")
        resp = admin_client.patch(
            f"/api/v1/admin/cashback/withdrawals/{wid}/validate",
            json={},
            headers=admin_headers,
        )
        assert resp.status_code == 409
        assert resp.json()["detail"] == "already_resolved"


# ---------------------------------------------------------------------------
# PATCH /api/v1/admin/cashback/withdrawals/{id}/refuse
# ---------------------------------------------------------------------------
class TestRefuseWithdrawalTotpGate:
    def test_missing_totp_returns_401(self, admin_client, db, totp_secret):
        uid = make_user(db)
        wid = _seed_withdrawal(db, user_id=uid)
        resp = admin_client.patch(
            f"/api/v1/admin/cashback/withdrawals/{wid}/refuse",
            json={"reason": "fraud-suspected"},
            headers={"X-Admin-Operator": "test"},
        )
        assert resp.status_code == 401
        assert resp.json()["detail"] == "totp_required"

    def test_invalid_totp_returns_401(self, admin_client, db, totp_secret):
        uid = make_user(db)
        wid = _seed_withdrawal(db, user_id=uid)
        resp = admin_client.patch(
            f"/api/v1/admin/cashback/withdrawals/{wid}/refuse",
            json={"reason": "fraud-suspected"},
            headers={"X-Admin-Operator": "test", "X-Admin-TOTP": "000000"},
        )
        assert resp.status_code == 401
        assert resp.json()["detail"] == "totp_invalid"


class TestRefuseWithdrawalEffects:
    def test_refuse_marks_failed_and_logs_reason(self, admin_client, db, admin_headers):
        uid = make_user(db)
        wid = _seed_withdrawal(db, user_id=uid, amount=1500)
        resp = admin_client.patch(
            f"/api/v1/admin/cashback/withdrawals/{wid}/refuse",
            json={"reason": "kyc-mismatch", "refund_balance": True},
            headers=admin_headers,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["status"] == "failed"
        row = _withdrawal(db, wid)
        assert row.status == "failed"
        assert row.failure_reason == "kyc-mismatch"
        assert row.processed_at is None  # not processed → must remain NULL

    def test_refuse_with_refund_increments_balance(self, admin_client, db, admin_headers):
        uid = make_user(db)
        # Balance is 0 after make_user. Seed a withdrawal of 800 — semantically
        # the user already paid that 800 when initiating, so balance starts
        # 0 here ; after refund it must become 800.
        wid = _seed_withdrawal(db, user_id=uid, amount=800)
        assert _balance(db, uid) == 0
        resp = admin_client.patch(
            f"/api/v1/admin/cashback/withdrawals/{wid}/refuse",
            json={"reason": "kyc-mismatch", "refund_balance": True},
            headers=admin_headers,
        )
        assert resp.status_code == 200
        assert _balance(db, uid) == 800

    def test_refuse_without_refund_keeps_balance(self, admin_client, db, admin_headers):
        uid = make_user(db)
        wid = _seed_withdrawal(db, user_id=uid, amount=800)
        resp = admin_client.patch(
            f"/api/v1/admin/cashback/withdrawals/{wid}/refuse",
            json={"reason": "kyc-mismatch", "refund_balance": False},
            headers=admin_headers,
        )
        assert resp.status_code == 200
        assert _balance(db, uid) == 0  # untouched

    def test_refuse_default_refund_is_true(self, admin_client, db, admin_headers):
        """Body without refund_balance defaults to True (safer for users)."""
        uid = make_user(db)
        wid = _seed_withdrawal(db, user_id=uid, amount=400)
        resp = admin_client.patch(
            f"/api/v1/admin/cashback/withdrawals/{wid}/refuse",
            json={"reason": "compliance-block"},
            headers=admin_headers,
        )
        assert resp.status_code == 200
        assert _balance(db, uid) == 400

    def test_refuse_short_reason_422(self, admin_client, db, admin_headers):
        uid = make_user(db)
        wid = _seed_withdrawal(db, user_id=uid)
        resp = admin_client.patch(
            f"/api/v1/admin/cashback/withdrawals/{wid}/refuse",
            json={"reason": "ab"},
            headers=admin_headers,
        )
        assert resp.status_code == 422

    def test_refuse_long_reason_422(self, admin_client, db, admin_headers):
        uid = make_user(db)
        wid = _seed_withdrawal(db, user_id=uid)
        resp = admin_client.patch(
            f"/api/v1/admin/cashback/withdrawals/{wid}/refuse",
            json={"reason": "x" * 201},
            headers=admin_headers,
        )
        assert resp.status_code == 422

    def test_refuse_idempotent_already_failed_409(self, admin_client, db, admin_headers):
        uid = make_user(db)
        wid = _seed_withdrawal(db, user_id=uid, status="failed")
        resp = admin_client.patch(
            f"/api/v1/admin/cashback/withdrawals/{wid}/refuse",
            json={"reason": "duplicate-action"},
            headers=admin_headers,
        )
        assert resp.status_code == 409
        assert resp.json()["detail"] == "already_resolved"

    def test_refuse_processed_withdrawal_409(self, admin_client, db, admin_headers):
        """Cannot refuse a withdrawal that has already been processed (money sent)."""
        uid = make_user(db)
        wid = _seed_withdrawal(db, user_id=uid, status="processed")
        resp = admin_client.patch(
            f"/api/v1/admin/cashback/withdrawals/{wid}/refuse",
            json={"reason": "too-late"},
            headers=admin_headers,
        )
        assert resp.status_code == 409
        assert resp.json()["detail"] == "already_resolved"

    def test_refuse_unknown_id_404(self, admin_client, db, admin_headers):
        ghost = uuid.uuid4()
        resp = admin_client.patch(
            f"/api/v1/admin/cashback/withdrawals/{ghost}/refuse",
            json={"reason": "ghost-row"},
            headers=admin_headers,
        )
        assert resp.status_code == 404
        assert resp.json()["detail"] == "withdrawal_not_found"
