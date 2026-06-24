"""Admin CAB endpoints — adjustment + transactions audit."""

from __future__ import annotations

import uuid

import pyotp
import pytest
from sqlalchemy import text

from tests.conftest import make_user


def _balance(db, user_id):
    row = db.execute(
        text("SELECT balance FROM user_cab_balance WHERE user_id = :uid"),
        {"uid": user_id},
    ).first()
    return row.balance if row else None


# ---------------------------------------------------------------------------
# POST /api/v1/admin/cab/adjustment
# ---------------------------------------------------------------------------
class TestCabAdjustmentTotpGate:
    def test_missing_totp_returns_401(self, admin_client, db, totp_secret):
        uid = make_user(db)
        resp = admin_client.post(
            "/api/v1/admin/cab/adjustment",
            json={
                "user_id": str(uid),
                "direction": "credit",
                "amount_cents": 100,
                "reason": "datafix-001",
            },
            headers={"X-Admin-Operator": "test"},
        )
        assert resp.status_code == 401
        assert resp.json()["detail"] == "totp_required"

    def test_invalid_totp_returns_401(self, admin_client, db, totp_secret):
        uid = make_user(db)
        resp = admin_client.post(
            "/api/v1/admin/cab/adjustment",
            json={
                "user_id": str(uid),
                "direction": "credit",
                "amount_cents": 100,
                "reason": "datafix-001",
            },
            headers={"X-Admin-Operator": "test", "X-Admin-TOTP": "000000"},
        )
        assert resp.status_code == 401
        assert resp.json()["detail"] == "totp_invalid"

    def test_valid_totp_allows_credit(self, admin_client, db, admin_headers):
        uid = make_user(db)
        resp = admin_client.post(
            "/api/v1/admin/cab/adjustment",
            json={
                "user_id": str(uid),
                "direction": "credit",
                "amount_cents": 250,
                "reason": "datafix-002",
            },
            headers=admin_headers,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["status"] == "ok"
        assert "transaction_id" in body


class TestCabAdjustmentEffects:
    def test_credit_increments_balance_and_inserts_admin_tx(self, admin_client, db, admin_headers):
        uid = make_user(db)
        resp = admin_client.post(
            "/api/v1/admin/cab/adjustment",
            json={
                "user_id": str(uid),
                "direction": "credit",
                "amount_cents": 1000,
                "reason": "datafix-credit-001",
            },
            headers=admin_headers,
        )
        assert resp.status_code == 200
        assert _balance(db, uid) == 1000
        row = db.execute(
            text(
                "SELECT direction, amount, reason, reference_type, context "
                "FROM cabecoin_transactions "
                "WHERE user_id = :uid AND reference_type = 'admin'"
            ),
            {"uid": uid},
        ).first()
        assert row is not None
        assert row.direction == "credit"
        assert row.amount == 1000
        assert row.reason == "admin_adjustment"
        assert row.reference_type == "admin"
        assert row.context["operator"] == "test-admin"
        assert row.context["reason"] == "datafix-credit-001"

    def test_debit_decrements_balance(self, admin_client, db, admin_headers):
        uid = make_user(db)
        # Seed a credit first.
        db.execute(
            text("UPDATE user_cab_balance SET balance = 500 WHERE user_id = :uid"),
            {"uid": uid},
        )
        db.commit()

        resp = admin_client.post(
            "/api/v1/admin/cab/adjustment",
            json={
                "user_id": str(uid),
                "direction": "debit",
                "amount_cents": 200,
                "reason": "datafix-debit-001",
            },
            headers=admin_headers,
        )
        assert resp.status_code == 200, resp.text
        assert _balance(db, uid) == 300

    def test_debit_insufficient_balance_409(self, admin_client, db, admin_headers):
        uid = make_user(db)  # balance starts at 0
        resp = admin_client.post(
            "/api/v1/admin/cab/adjustment",
            json={
                "user_id": str(uid),
                "direction": "debit",
                "amount_cents": 100,
                "reason": "datafix-overdraw",
            },
            headers=admin_headers,
        )
        assert resp.status_code == 409
        assert resp.json()["detail"] == "insufficient_balance"
        assert _balance(db, uid) == 0  # no change

    def test_unknown_user_404(self, admin_client, db, admin_headers):
        ghost = uuid.uuid4()
        resp = admin_client.post(
            "/api/v1/admin/cab/adjustment",
            json={
                "user_id": str(ghost),
                "direction": "credit",
                "amount_cents": 100,
                "reason": "datafix-ghost",
            },
            headers=admin_headers,
        )
        assert resp.status_code == 404
        assert resp.json()["detail"] == "user_not_found"

    def test_invalid_direction_422(self, admin_client, db, admin_headers):
        uid = make_user(db)
        resp = admin_client.post(
            "/api/v1/admin/cab/adjustment",
            json={
                "user_id": str(uid),
                "direction": "sideways",
                "amount_cents": 100,
                "reason": "datafix",
            },
            headers=admin_headers,
        )
        assert resp.status_code == 422

    def test_negative_amount_422(self, admin_client, db, admin_headers):
        uid = make_user(db)
        resp = admin_client.post(
            "/api/v1/admin/cab/adjustment",
            json={
                "user_id": str(uid),
                "direction": "credit",
                "amount_cents": -10,
                "reason": "datafix",
            },
            headers=admin_headers,
        )
        assert resp.status_code == 422

    def test_zero_amount_422(self, admin_client, db, admin_headers):
        uid = make_user(db)
        resp = admin_client.post(
            "/api/v1/admin/cab/adjustment",
            json={
                "user_id": str(uid),
                "direction": "credit",
                "amount_cents": 0,
                "reason": "datafix",
            },
            headers=admin_headers,
        )
        assert resp.status_code == 422

    def test_short_reason_422(self, admin_client, db, admin_headers):
        uid = make_user(db)
        resp = admin_client.post(
            "/api/v1/admin/cab/adjustment",
            json={
                "user_id": str(uid),
                "direction": "credit",
                "amount_cents": 100,
                "reason": "ab",
            },
            headers=admin_headers,
        )
        assert resp.status_code == 422

    def test_missing_operator_header_422(self, admin_client, db, totp_secret):
        uid = make_user(db)
        code = pyotp.TOTP(totp_secret).now()
        resp = admin_client.post(
            "/api/v1/admin/cab/adjustment",
            json={
                "user_id": str(uid),
                "direction": "credit",
                "amount_cents": 100,
                "reason": "no-operator",
            },
            headers={"X-Admin-TOTP": code},
        )
        # FastAPI treats missing required Header(...) as 422.
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# GET /api/v1/admin/cab/users/{user_id}/transactions
# ---------------------------------------------------------------------------
class TestCabUserTransactions:
    @pytest.fixture
    def seeded_txns(self, db, admin_client, admin_headers):
        """Seed 3 admin credits via the endpoint so reference_type='admin'."""
        uid = make_user(db)
        for i in range(3):
            r = admin_client.post(
                "/api/v1/admin/cab/adjustment",
                json={
                    "user_id": str(uid),
                    "direction": "credit",
                    "amount_cents": 100 + i,
                    "reason": f"seed-{i}",
                },
                headers=admin_headers,
            )
            assert r.status_code == 200
        return uid

    def test_returns_all_for_user_default_pagination(self, admin_client, seeded_txns):
        resp = admin_client.get(f"/api/v1/admin/cab/users/{seeded_txns}/transactions")
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 3
        assert len(body["transactions"]) == 3

    def test_pagination_limit_offset(self, admin_client, seeded_txns):
        r1 = admin_client.get(f"/api/v1/admin/cab/users/{seeded_txns}/transactions?limit=2")
        assert r1.status_code == 200
        assert len(r1.json()["transactions"]) == 2
        assert r1.json()["total"] == 3
        r2 = admin_client.get(f"/api/v1/admin/cab/users/{seeded_txns}/transactions?limit=2&offset=2")
        assert r2.status_code == 200
        assert len(r2.json()["transactions"]) == 1

    def test_filter_by_direction(self, admin_client, seeded_txns):
        resp = admin_client.get(f"/api/v1/admin/cab/users/{seeded_txns}/transactions?direction=credit")
        assert resp.status_code == 200
        assert resp.json()["total"] == 3

        resp_debit = admin_client.get(f"/api/v1/admin/cab/users/{seeded_txns}/transactions?direction=debit")
        assert resp_debit.status_code == 200
        assert resp_debit.json()["total"] == 0

    def test_filter_by_reference_type_admin(self, admin_client, seeded_txns):
        resp = admin_client.get(f"/api/v1/admin/cab/users/{seeded_txns}/transactions?reference_type=admin")
        assert resp.status_code == 200
        assert resp.json()["total"] == 3
        for tx in resp.json()["transactions"]:
            assert tx["reference_type"] == "admin"
            assert tx["context"]["operator"] == "test-admin"

    def test_unknown_user_returns_empty(self, admin_client, db):
        ghost = uuid.uuid4()
        resp = admin_client.get(f"/api/v1/admin/cab/users/{ghost}/transactions")
        assert resp.status_code == 200
        assert resp.json() == {"transactions": [], "total": 0}

    def test_does_not_require_totp_read_only(self, admin_client, db):
        """The list endpoint is read-only, so no TOTP is required."""
        uid = make_user(db)
        resp = admin_client.get(f"/api/v1/admin/cab/users/{uid}/transactions")
        # admin_client bypasses ADMIN_API_KEY ; absence of X-Admin-TOTP must
        # NOT trigger a 401 here (read-only audit, distinct from the
        # mutation gate).
        assert resp.status_code == 200
