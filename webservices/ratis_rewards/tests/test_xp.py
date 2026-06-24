"""
Tests for the XP system: award_xp, _compute_level, GET /gamification/xp/balance.

TDD — written before implementation.
"""

from __future__ import annotations

import re
import uuid

import pytest
from repositories.xp_repository import (
    _compute_level,
    award_xp,
    get_xp_balance,
)
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from tests.conftest import make_user

# ===========================================================================
# _compute_level — pure integer arithmetic
# ===========================================================================


class TestComputeLevel:
    """Threshold for level n: level_base * (2^n - 1) cumulative XP."""

    def test_zero_xp_is_level_0(self):
        assert _compute_level(0, 100) == 0

    def test_negative_xp_is_level_0(self):
        assert _compute_level(-1, 100) == 0

    def test_just_below_threshold_level1(self):
        assert _compute_level(99, 100) == 0

    def test_exactly_at_threshold_level1(self):
        # threshold(1) = 100 * (2^1 - 1) = 100
        assert _compute_level(100, 100) == 1

    def test_just_below_threshold_level2(self):
        # threshold(2) = 100 * (2^2 - 1) = 300
        assert _compute_level(299, 100) == 1

    def test_exactly_at_threshold_level2(self):
        assert _compute_level(300, 100) == 2

    def test_exactly_at_threshold_level3(self):
        # threshold(3) = 100 * (2^3 - 1) = 700
        assert _compute_level(700, 100) == 3

    def test_exactly_at_threshold_level4(self):
        # threshold(4) = 100 * (2^4 - 1) = 1500
        assert _compute_level(1500, 100) == 4

    def test_huge_xp_does_not_overflow(self):
        # 2^200 — Python handles big integers natively
        # level ≈ 200 - log2(100) ≈ 193 (exact is 193)
        huge = 2**200
        level = _compute_level(huge, 100)
        assert level == 193  # no float overflow — pure integer arithmetic

    def test_level_base_1(self):
        # threshold(n) = 2^n - 1
        assert _compute_level(0, 1) == 0
        assert _compute_level(1, 1) == 1  # threshold(1) = 1
        assert _compute_level(2, 1) == 1  # threshold(2) = 3
        assert _compute_level(3, 1) == 2


# ===========================================================================
# award_xp — repository function
# ===========================================================================


class TestAwardXp:
    def test_creates_xp_balance_row(self, db):
        uid = make_user(db)
        award_xp(db, uid, 10, "receipt_scan")
        db.flush()
        row = db.execute(
            text("SELECT balance FROM user_xp_balance WHERE user_id = :uid"),
            {"uid": uid},
        ).first()
        assert row is not None
        assert int(row.balance) == 10

    def test_accumulates_on_second_call(self, db):
        uid = make_user(db)
        award_xp(db, uid, 10, "receipt_scan")
        award_xp(db, uid, 8, "label_scan")
        db.flush()
        assert get_xp_balance(db, uid)["balance"] == 18

    def test_inserts_transaction_row(self, db):
        uid = make_user(db)
        ref_id = uuid.uuid4()
        award_xp(db, uid, 10, "receipt_scan", reference_id=ref_id, reference_type="scan")
        db.flush()
        row = db.execute(
            text("SELECT amount, reason, reference_id, reference_type FROM xp_transactions WHERE user_id = :uid"),
            {"uid": uid},
        ).first()
        assert row is not None
        assert int(row.amount) == 10
        assert row.reason == "receipt_scan"
        assert row.reference_id == ref_id
        assert row.reference_type == "scan"

    def test_rejects_zero_amount(self, db):
        uid = make_user(db)
        with pytest.raises(ValueError, match="amount must be positive"):
            award_xp(db, uid, 0, "receipt_scan")

    def test_rejects_negative_amount(self, db):
        uid = make_user(db)
        with pytest.raises(ValueError, match="amount must be positive"):
            award_xp(db, uid, -5, "receipt_scan")

    def test_rejects_invalid_reason(self, db):
        uid = make_user(db)
        with pytest.raises(ValueError, match="Invalid XP reason"):
            award_xp(db, uid, 10, "invalid_reason")

    def test_updates_level_on_threshold_cross(self, db):
        uid = make_user(db)
        # threshold(1) = level_base (100 by default)
        # award 100 XP → should be level 1
        award_xp(db, uid, 100, "receipt_scan")
        db.flush()
        result = get_xp_balance(db, uid)
        assert result["level"] == 1

    def test_returns_level_up_info(self, db):
        uid = make_user(db)
        result = award_xp(db, uid, 100, "receipt_scan")
        assert result["old_level"] == 0
        assert result["new_level"] == 1
        assert result["leveled_up"] is True

    def test_no_level_up_below_threshold(self, db):
        uid = make_user(db)
        result = award_xp(db, uid, 50, "receipt_scan")
        assert result["old_level"] == 0
        assert result["new_level"] == 0
        assert result["leveled_up"] is False

    def test_all_valid_reasons_accepted(self, db):
        uid = make_user(db)
        valid_reasons = [
            "receipt_scan",
            "label_scan",
            "barcode_scan",
            "price_compared",
            "mission_completed",
            "battlepass_milestone",
            "referral",
            "feed_jack",
            "stonks_completion",
        ]
        for reason in valid_reasons:
            award_xp(db, uid, 1, reason)
        db.flush()
        count = db.execute(
            text("SELECT COUNT(*) FROM xp_transactions WHERE user_id = :uid"),
            {"uid": uid},
        ).scalar()
        assert count == len(valid_reasons)


# ===========================================================================
# get_xp_balance
# ===========================================================================


class TestGetXpBalance:
    def test_returns_zero_when_no_row(self, db):
        uid = make_user(db)
        result = get_xp_balance(db, uid)
        assert result == {"balance": 0, "level": 0}

    def test_returns_balance_and_level(self, db):
        uid = make_user(db)
        award_xp(db, uid, 300, "receipt_scan")
        db.flush()
        result = get_xp_balance(db, uid)
        assert result["balance"] == 300
        assert result["level"] == 2  # threshold(2) = 300


# ===========================================================================
# GET /api/v1/gamification/xp/balance — HTTP endpoint
# ===========================================================================


class TestXpBalanceEndpoint:
    def test_returns_balance_and_level(self, db, user_client):
        client, set_user = user_client
        uid = make_user(db)
        set_user(uid)
        award_xp(db, uid, 100, "receipt_scan")
        db.commit()

        resp = client.get("/api/v1/gamification/xp/balance")
        assert resp.status_code == 200
        data = resp.json()
        assert data["balance"] == "100"  # returned as string to avoid float overflow
        assert data["level"] == 1

    def test_returns_zeros_for_new_user(self, db, user_client):
        client, set_user = user_client
        uid = make_user(db)
        set_user(uid)

        resp = client.get("/api/v1/gamification/xp/balance")
        assert resp.status_code == 200
        data = resp.json()
        assert data["balance"] == "0"
        assert data["level"] == 0

    def test_requires_auth(self, raw_client):
        resp = raw_client.get("/api/v1/gamification/xp/balance")
        assert resp.status_code == 401


# ===========================================================================
# RW-04 — xp_transactions CHECK constraints (reference_type allowlist +
# reference consistency), mirroring cabecoin_transactions
# ===========================================================================


def _insert_xp_tx(db, *, reference_id, reference_type):
    """Raw INSERT into xp_transactions — bypasses award_xp so the DB CHECK
    constraints are exercised in isolation."""
    db.execute(
        text(
            "INSERT INTO xp_transactions "
            "    (id, user_id, amount, reason, reference_id, reference_type) "
            "VALUES (:id, :uid, 10, 'receipt_scan', :ref_id, :ref_type)"
        ),
        {
            "id": uuid.uuid4(),
            "uid": make_user(db),
            "ref_id": reference_id,
            "ref_type": reference_type,
        },
    )
    db.flush()


class TestXpTransactionReferenceChecks:
    def test_reference_type_allowlist_constraint_exists(self, db):
        """The xp_transactions table must carry a reference_type allowlist
        CHECK — previously absent (audit RW-04)."""
        defn = db.execute(
            text(
                "SELECT pg_get_constraintdef(oid) AS d FROM pg_constraint "
                "WHERE conname = 'xp_transactions_reference_type_check'"
            )
        ).scalar_one_or_none()
        assert defn is not None, "missing xp_transactions_reference_type_check"

    def test_reference_consistency_constraint_exists(self, db):
        """The xp_transactions table must carry a reference consistency
        CHECK — previously absent (audit RW-04)."""
        defn = db.execute(
            text(
                "SELECT pg_get_constraintdef(oid) AS d FROM pg_constraint "
                "WHERE conname = 'xp_transactions_reference_consistency_check'"
            )
        ).scalar_one_or_none()
        assert defn is not None, "missing reference_consistency_check"

    def test_xp_reference_type_allowlist_matches_cab(self, db):
        """PO decision : the xp_transactions reference_type allowlist is the
        SAME literal set as cabecoin_transactions."""
        xp_defn = db.execute(
            text(
                "SELECT pg_get_constraintdef(oid) AS d FROM pg_constraint "
                "WHERE conname = 'xp_transactions_reference_type_check'"
            )
        ).scalar_one()
        cab_defn = db.execute(
            text(
                "SELECT pg_get_constraintdef(oid) AS d FROM pg_constraint "
                "WHERE conname = 'cabecoin_transactions_reference_type_check'"
            )
        ).scalar_one()
        assert frozenset(re.findall(r"'([^']+)'", xp_defn)) == frozenset(re.findall(r"'([^']+)'", cab_defn))

    def test_unknown_reference_type_rejected(self, db):
        """An unknown reference_type must be rejected by the CHECK."""
        with pytest.raises(IntegrityError):
            _insert_xp_tx(db, reference_id=uuid.uuid4(), reference_type="not_a_real_type")

    def test_valid_reference_type_accepted(self, db):
        """A reference_type in the allowlist + matching reference_id is OK."""
        _insert_xp_tx(db, reference_id=uuid.uuid4(), reference_type="user_mission")

    def test_reference_id_without_type_rejected(self, db):
        """reference_id set but reference_type NULL violates consistency."""
        with pytest.raises(IntegrityError):
            _insert_xp_tx(db, reference_id=uuid.uuid4(), reference_type=None)

    def test_reference_type_without_id_rejected(self, db):
        """reference_type set but reference_id NULL violates consistency."""
        with pytest.raises(IntegrityError):
            _insert_xp_tx(db, reference_id=None, reference_type="scan")

    def test_both_null_accepted(self, db):
        """Both NULL is a valid unreferenced XP row."""
        _insert_xp_tx(db, reference_id=None, reference_type=None)
