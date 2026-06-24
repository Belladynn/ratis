"""
Tests for the user-facing referral routes + admin datafix.

- GET /rewards/referral/code      — JWT
- GET /rewards/referral/history   — JWT
- POST /admin/referral/link       — ADMIN_API_KEY
"""

from __future__ import annotations

import uuid

from repositories.cab_repository import get_balance
from sqlalchemy import text

from tests.conftest import make_user


def _seed_code(db, user_id: uuid.UUID, code: str) -> uuid.UUID:
    """Insert a referral_codes row for user. Returns referral_id."""
    referral_id = uuid.uuid4()
    db.execute(
        text(
            "INSERT INTO referral_codes (id, user_id, code, type, created_at) VALUES (:id, :uid, :code, 'user', now())"
        ),
        {"id": referral_id, "uid": user_id, "code": code.upper()},
    )
    db.flush()
    return referral_id


def _seed_use(
    db,
    referral_id: uuid.UUID,
    referred_user_id: uuid.UUID,
    *,
    plan: str | None = None,
    rewarded: bool = False,
) -> uuid.UUID:
    """Insert a referral_uses row. Returns use_id."""
    use_id = uuid.uuid4()
    db.execute(
        text(
            "INSERT INTO referral_uses "
            "(id, referral_id, referred_user_id, plan, rewarded_at, created_at) "
            "VALUES (:id, :rid, :ruid, :plan, "
            "        CASE WHEN :rew THEN now() ELSE NULL END, now())"
        ),
        {
            "id": use_id,
            "rid": referral_id,
            "ruid": referred_user_id,
            "plan": plan,
            "rew": rewarded,
        },
    )
    db.flush()
    return use_id


def _set_user_subscribed(db, user_id: uuid.UUID, plan: str = "monthly") -> None:
    """Seed an active subscription for a user so the admin /link triggers the referrer reward."""
    db.execute(
        text(
            "INSERT INTO subscriptions "
            "(id, user_id, status, plan, price, paid_with, payment_ref, "
            " started_at, expires_at) "
            "VALUES (:id, :uid, 'active', :plan, 7.99, 'stripe', 'stripe_sess_test', "
            "        now(), now() + INTERVAL '30 days')"
        ),
        {"id": uuid.uuid4(), "uid": user_id, "plan": plan},
    )
    db.flush()


# ============================================================
# GET /rewards/referral/code
# ============================================================


class TestGetReferralCode:
    def test_returns_code_if_exists(self, db, user_client):
        http, set_user = user_client
        uid = make_user(db)
        _seed_code(db, uid, "EXISTING")
        db.commit()
        set_user(uid)

        resp = http.get("/api/v1/rewards/referral/code")
        assert resp.status_code == 200
        data = resp.json()
        assert data["code"] == "EXISTING"
        assert "created_at" in data

    def test_lazy_creates_if_missing(self, db, user_client):
        http, set_user = user_client
        uid = make_user(db)
        db.commit()
        set_user(uid)

        resp = http.get("/api/v1/rewards/referral/code")
        assert resp.status_code == 200
        data = resp.json()
        # 8-char hex uppercase
        assert len(data["code"]) == 8
        assert data["code"] == data["code"].upper()

        # Second call returns the same code (not a new one)
        resp2 = http.get("/api/v1/rewards/referral/code")
        assert resp2.json()["code"] == data["code"]

    def test_requires_auth(self, raw_client):
        resp = raw_client.get("/api/v1/rewards/referral/code")
        assert resp.status_code in (401, 403)


# ============================================================
# GET /rewards/referral/history
# ============================================================


class TestGetReferralHistory:
    def test_empty_history_when_no_uses(self, db, user_client):
        http, set_user = user_client
        uid = make_user(db)
        db.commit()
        set_user(uid)

        resp = http.get("/api/v1/rewards/referral/history")
        assert resp.status_code == 200
        data = resp.json()
        # Code lazy-created on first /history call too
        assert len(data["code"]) == 8
        assert data["stats"] == {"total_uses": 0, "rewarded_uses": 0, "total_cab_earned": 0}
        assert data["uses"] == []

    def test_counts_rewarded_and_pending(self, db, user_client):
        http, set_user = user_client
        uid = make_user(db)
        referral_id = _seed_code(db, uid, "HISTORY1")
        pending_y = make_user(db, email="pending@test.com")
        rewarded_monthly_y = make_user(db, email="rmonth@test.com")
        rewarded_annual_y = make_user(db, email="rann@test.com")
        for yid, name in [
            (pending_y, "PendingGuy"),
            (rewarded_monthly_y, "MonthlyGuy"),
            (rewarded_annual_y, "AnnualGuy"),
        ]:
            db.execute(
                text("UPDATE users SET display_name = :n WHERE id = :uid"),
                {"n": name, "uid": yid},
            )
        _seed_use(db, referral_id, pending_y, plan=None, rewarded=False)
        _seed_use(db, referral_id, rewarded_monthly_y, plan="monthly", rewarded=True)
        _seed_use(db, referral_id, rewarded_annual_y, plan="annual", rewarded=True)
        db.commit()
        set_user(uid)

        resp = http.get("/api/v1/rewards/referral/history")
        assert resp.status_code == 200
        data = resp.json()

        assert data["stats"]["total_uses"] == 3
        assert data["stats"]["rewarded_uses"] == 2
        # 500 monthly + 750 annual = 1250
        assert data["stats"]["total_cab_earned"] == 1250
        assert len(data["uses"]) == 3

    def test_exposes_display_name_only_not_email(self, db, user_client):
        http, set_user = user_client
        uid = make_user(db)
        referral_id = _seed_code(db, uid, "PRIV1234")
        filleul = make_user(db, email="secret.email@test.com")
        db.execute(
            text("UPDATE users SET display_name = 'Alice' WHERE id = :uid"),
            {"uid": filleul},
        )
        _seed_use(db, referral_id, filleul, plan="monthly", rewarded=True)
        db.commit()
        set_user(uid)

        resp = http.get("/api/v1/rewards/referral/history")
        use = resp.json()["uses"][0]
        assert use["referred_user_display_name"] == "Alice"
        body = resp.text
        assert "secret.email" not in body
        assert str(filleul) not in body

    def test_hides_display_name_for_deleted_user(self, db, user_client):
        http, set_user = user_client
        uid = make_user(db)
        referral_id = _seed_code(db, uid, "DELETED1")
        deleted = make_user(db, email="deleted@test.com")
        db.execute(
            text("UPDATE users SET display_name = 'Ghost', is_deleted = true WHERE id = :uid"),
            {"uid": deleted},
        )
        _seed_use(db, referral_id, deleted, plan="monthly", rewarded=True)
        db.commit()
        set_user(uid)

        resp = http.get("/api/v1/rewards/referral/history")
        use = resp.json()["uses"][0]
        assert use["referred_user_display_name"] is None
        assert use["plan"] == "monthly"
        assert use["status"] == "rewarded"

    def test_requires_auth(self, raw_client):
        resp = raw_client.get("/api/v1/rewards/referral/history")
        assert resp.status_code in (401, 403)


# ============================================================
# POST /admin/referral/link
# ============================================================


class TestAdminReferralLink:
    def test_creates_link_and_awards_signup_bonus(self, db, admin_client):
        referrer = make_user(db, email="referrer@test.com")
        referred = make_user(db, email="referred@test.com")
        _seed_code(db, referrer, "ADMLINK1")
        db.commit()

        resp = admin_client.post(
            "/api/v1/admin/referral/link",
            json={
                "referred_user_id": str(referred),
                "code": "ADMLINK1",
                "admin_operator_id": "support@ratis.app",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["detail"] == "link_created"
        assert data["signup_bonus_awarded"] == 150
        assert data["subscription_reward_triggered"] is False
        # Y got the signup bonus
        assert get_balance(db, referred) == 150
        # X got nothing yet (not subscribed)
        assert get_balance(db, referrer) == 0

    def test_triggers_subscription_reward_if_already_subscribed(self, db, admin_client):
        referrer = make_user(db, email="already-x@test.com")
        referred = make_user(db, email="already-y@test.com")
        _seed_code(db, referrer, "ALREADY1")
        _set_user_subscribed(db, referred, plan="annual")
        db.commit()

        resp = admin_client.post(
            "/api/v1/admin/referral/link",
            json={
                "referred_user_id": str(referred),
                "code": "ALREADY1",
                "admin_operator_id": "support@ratis.app",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["detail"] == "link_created_and_rewarded"
        assert data["subscription_reward_triggered"] is True
        assert data["cab_awarded_to_referrer"] == 750
        # X got 750 (annual), Y got 150 (signup)
        assert get_balance(db, referrer) == 750
        assert get_balance(db, referred) == 150

    def test_rejects_self_parrainage(self, db, admin_client):
        user = make_user(db, email="self@test.com")
        _seed_code(db, user, "SELFCODE")
        db.commit()

        resp = admin_client.post(
            "/api/v1/admin/referral/link",
            json={
                "referred_user_id": str(user),
                "code": "SELFCODE",
                "admin_operator_id": "support@ratis.app",
            },
        )
        assert resp.status_code == 400
        assert resp.json()["detail"] == "self_parrainage"

    def test_rejects_invalid_code(self, db, admin_client):
        referred = make_user(db, email="invalid@test.com")
        db.commit()

        resp = admin_client.post(
            "/api/v1/admin/referral/link",
            json={
                "referred_user_id": str(referred),
                "code": "NOPE1234",
                "admin_operator_id": "support@ratis.app",
            },
        )
        assert resp.status_code == 400
        assert resp.json()["detail"] == "invalid_code"

    def test_rejects_user_not_found(self, db, admin_client):
        referrer = make_user(db, email="lonely@test.com")
        _seed_code(db, referrer, "LONELY01")
        db.commit()

        resp = admin_client.post(
            "/api/v1/admin/referral/link",
            json={
                "referred_user_id": str(uuid.uuid4()),
                "code": "LONELY01",
                "admin_operator_id": "support@ratis.app",
            },
        )
        assert resp.status_code == 404
        assert resp.json()["detail"] == "user_not_found"

    def test_rejects_already_linked(self, db, admin_client):
        referrer1 = make_user(db, email="first@test.com")
        referrer2 = make_user(db, email="second@test.com")
        referred = make_user(db, email="filleul@test.com")
        ref1 = _seed_code(db, referrer1, "FIRSTONE")
        _seed_code(db, referrer2, "SECOND01")
        _seed_use(db, ref1, referred, plan=None, rewarded=False)
        db.commit()

        resp = admin_client.post(
            "/api/v1/admin/referral/link",
            json={
                "referred_user_id": str(referred),
                "code": "SECOND01",
                "admin_operator_id": "support@ratis.app",
            },
        )
        assert resp.status_code == 409
        assert resp.json()["detail"] == "already_linked"

    def test_requires_admin_key(self, db, raw_client):
        resp = raw_client.post(
            "/api/v1/admin/referral/link",
            json={
                "referred_user_id": str(uuid.uuid4()),
                "code": "NOAUTH01",
                "admin_operator_id": "support@ratis.app",
            },
        )
        # raw_client has no auth headers at all → 401/403
        assert resp.status_code in (401, 403)
