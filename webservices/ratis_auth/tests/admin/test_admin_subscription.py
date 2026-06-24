"""Admin subscription manage endpoints — TDD coverage.

Endpoints under test :

- ``GET    /api/v1/admin/users/{user_id}/subscription`` — read-only state
- ``PATCH  /api/v1/admin/users/{user_id}/subscription/activate`` — TOTP-gated
- ``PATCH  /api/v1/admin/users/{user_id}/subscription/deactivate`` — TOTP-gated
- ``PATCH  /api/v1/admin/users/{user_id}/subscription/extend`` — TOTP-gated

Subscription = ``NEVER PURGE`` (legal). Mutations UPDATE the row, never DELETE.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import text

from tests.admin.conftest import make_subscription, make_user


def _isoplus_days(days: int) -> str:
    return (datetime.now(UTC) + timedelta(days=days)).date().isoformat()


# =============================================================================
# GET /admin/users/{user_id}/subscription
# =============================================================================
class TestAdminGetSubscription:
    def test_get_returns_state(self, admin_client, db):
        uid = make_user(db)
        make_subscription(db, uid, status="active", plan="monthly")

        resp = admin_client.get(f"/api/v1/admin/users/{uid}/subscription")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["user_id"] == str(uid)
        assert body["status"] == "active"
        assert body["plan"] == "monthly"
        assert body["paid_with"] == "stripe"
        assert body["payment_ref"] == "test_ref_admin"
        assert body["started_at"] is not None
        assert body["expires_at"] is not None
        assert body["cancelled_at"] is None

    def test_get_404_when_no_subscription(self, admin_client, db):
        uid = make_user(db)
        resp = admin_client.get(f"/api/v1/admin/users/{uid}/subscription")
        assert resp.status_code == 404
        assert resp.json()["detail"] == "subscription_not_found"


# =============================================================================
# PATCH /admin/users/{user_id}/subscription/activate
# =============================================================================
class TestAdminActivateSubscription:
    def test_activate_creates_when_no_subscription(self, admin_client, db, admin_headers):
        uid = make_user(db)
        until = _isoplus_days(180)
        resp = admin_client.patch(
            f"/api/v1/admin/users/{uid}/subscription/activate",
            json={
                "reason": "alpha-grant",
                "until_date": until,
                "source": "manual_admin",
            },
            headers=admin_headers,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["status"] == "active"
        assert body["paid_with"] == "manual_admin"
        # expires_at should match until_date
        assert body["expires_at"].startswith(until)

        # DB row exists with the right shape
        row = db.execute(
            text("SELECT status, paid_with, payment_ref FROM subscriptions WHERE user_id = :uid"),
            {"uid": uid},
        ).first()
        assert row is not None
        assert row.status == "active"
        assert row.paid_with == "manual_admin"
        # payment_ref must be present to satisfy payment_ref_coherence on active
        assert row.payment_ref is not None
        assert row.payment_ref.startswith("manual_admin:")

    def test_activate_updates_existing(self, admin_client, db, admin_headers):
        uid = make_user(db)
        # Seed a pending row (no payment_ref needed for pending)
        make_subscription(db, uid, status="pending", plan="monthly", payment_ref=None)
        until = _isoplus_days(60)
        resp = admin_client.patch(
            f"/api/v1/admin/users/{uid}/subscription/activate",
            json={
                "reason": "promote-pending",
                "until_date": until,
                "source": "manual_admin",
            },
            headers=admin_headers,
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == "active"
        # Only one row, status=active
        rows = db.execute(
            text("SELECT status FROM subscriptions WHERE user_id = :uid"),
            {"uid": uid},
        ).all()
        assert len(rows) == 1
        assert rows[0].status == "active"

    def test_activate_requires_totp(self, admin_client, db, totp_secret):
        uid = make_user(db)
        resp = admin_client.patch(
            f"/api/v1/admin/users/{uid}/subscription/activate",
            json={"reason": "no-totp", "source": "manual_admin"},
            headers={"X-Admin-Operator": "test"},
        )
        assert resp.status_code == 401
        assert resp.json()["detail"] == "totp_required"

    def test_activate_invalid_totp_rejected(self, admin_client, db, totp_secret):
        uid = make_user(db)
        resp = admin_client.patch(
            f"/api/v1/admin/users/{uid}/subscription/activate",
            json={"reason": "bad-totp", "source": "manual_admin"},
            headers={"X-Admin-Operator": "test", "X-Admin-TOTP": "000000"},
        )
        assert resp.status_code == 401
        assert resp.json()["detail"] == "totp_invalid"

    def test_activate_requires_admin_operator_header(self, admin_client, db, valid_totp_code):
        uid = make_user(db)
        # No X-Admin-Operator → FastAPI Header(...) → 422
        resp = admin_client.patch(
            f"/api/v1/admin/users/{uid}/subscription/activate",
            json={"reason": "no-operator", "source": "manual_admin"},
            headers={"X-Admin-TOTP": valid_totp_code},
        )
        assert resp.status_code == 422

    def test_activate_409_when_already_active_future_expiry(self, admin_client, db, admin_headers):
        uid = make_user(db)
        # Already active for 60 days
        make_subscription(db, uid, status="active", expires_in_days=60)
        until = _isoplus_days(120)
        resp = admin_client.patch(
            f"/api/v1/admin/users/{uid}/subscription/activate",
            json={
                "reason": "redundant",
                "until_date": until,
                "source": "manual_admin",
            },
            headers=admin_headers,
        )
        assert resp.status_code == 409
        assert resp.json()["detail"] == "already_active"

    def test_activate_uses_default_until_date_1year(self, admin_client, db, admin_headers):
        uid = make_user(db)
        resp = admin_client.patch(
            f"/api/v1/admin/users/{uid}/subscription/activate",
            json={"reason": "default-1yr", "source": "manual_admin"},
            headers=admin_headers,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        # ~1 year out — give a generous tolerance to dodge clock-skew flakiness.
        expires = datetime.fromisoformat(body["expires_at"])
        target = datetime.now(UTC) + timedelta(days=365)
        delta_days = abs((expires - target).total_seconds()) / 86400
        assert delta_days < 2, f"expires_at not ~1y out : {body['expires_at']}"

    def test_activate_logs_reason(self, admin_client, db, admin_headers, caplog):
        uid = make_user(db)
        with caplog.at_level("INFO"):
            resp = admin_client.patch(
                f"/api/v1/admin/users/{uid}/subscription/activate",
                json={
                    "reason": "audit-trail-marker-XYZ",
                    "source": "manual_admin",
                },
                headers=admin_headers,
            )
        assert resp.status_code == 200
        # Reason + operator must end up in the logs (structured audit trail).
        joined = "\n".join(r.getMessage() for r in caplog.records)
        assert "audit-trail-marker-XYZ" in joined
        assert "test-admin" in joined

    def test_activate_short_reason_422(self, admin_client, db, admin_headers):
        uid = make_user(db)
        resp = admin_client.patch(
            f"/api/v1/admin/users/{uid}/subscription/activate",
            json={"reason": "ab", "source": "manual_admin"},
            headers=admin_headers,
        )
        assert resp.status_code == 422


# =============================================================================
# PATCH /admin/users/{user_id}/subscription/deactivate
# =============================================================================
class TestAdminDeactivateSubscription:
    def test_deactivate_immediate_cancels_now(self, admin_client, db, admin_headers):
        uid = make_user(db)
        make_subscription(db, uid, status="active", expires_in_days=30)
        resp = admin_client.patch(
            f"/api/v1/admin/users/{uid}/subscription/deactivate",
            json={"reason": "abuse-detected", "effective": "immediate"},
            headers=admin_headers,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["status"] == "cancelled"
        assert body["cancelled_at"] is not None
        # Row in DB
        row = db.execute(
            text("SELECT status, cancelled_at FROM subscriptions WHERE user_id = :uid"),
            {"uid": uid},
        ).first()
        assert row.status == "cancelled"
        assert row.cancelled_at is not None

    def test_deactivate_end_of_period_keeps_expiry(self, admin_client, db, admin_headers):
        uid = make_user(db)
        make_subscription(db, uid, status="active", expires_in_days=20)
        # capture original expires_at
        before = (
            db.execute(
                text("SELECT expires_at FROM subscriptions WHERE user_id = :uid"),
                {"uid": uid},
            )
            .first()
            .expires_at
        )
        resp = admin_client.patch(
            f"/api/v1/admin/users/{uid}/subscription/deactivate",
            json={"reason": "user-asked", "effective": "end_of_period"},
            headers=admin_headers,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        # Status remains 'active' (Stripe webhook will flip it later) — the
        # admin marks the row's intent via cancelled_at staying NULL ; alt :
        # we expose 'cancelling' if we add it. For V1 we stick to active +
        # set a flag in payment_ref or context. We choose : status stays
        # 'active', expires_at unchanged, cancelled_at stays NULL ; the
        # admin operator records the intent in audit logs only.
        # The contract therefore : subscription unchanged from a DB POV,
        # response carries effective=end_of_period as confirmation.
        assert body["status"] == "active"
        assert body["expires_at"].startswith(before.date().isoformat())

    def test_deactivate_requires_totp(self, admin_client, db, totp_secret):
        uid = make_user(db)
        make_subscription(db, uid, status="active")
        resp = admin_client.patch(
            f"/api/v1/admin/users/{uid}/subscription/deactivate",
            json={"reason": "no-totp", "effective": "immediate"},
            headers={"X-Admin-Operator": "test"},
        )
        assert resp.status_code == 401
        assert resp.json()["detail"] == "totp_required"

    def test_deactivate_404_when_no_subscription(self, admin_client, db, admin_headers):
        uid = make_user(db)
        resp = admin_client.patch(
            f"/api/v1/admin/users/{uid}/subscription/deactivate",
            json={"reason": "nothing-to-cancel", "effective": "immediate"},
            headers=admin_headers,
        )
        assert resp.status_code == 404
        assert resp.json()["detail"] == "subscription_not_found"

    def test_deactivate_validates_effective_enum(self, admin_client, db, admin_headers):
        uid = make_user(db)
        make_subscription(db, uid, status="active")
        resp = admin_client.patch(
            f"/api/v1/admin/users/{uid}/subscription/deactivate",
            json={"reason": "bad-enum", "effective": "yesterday"},
            headers=admin_headers,
        )
        assert resp.status_code == 422


# =============================================================================
# PATCH /admin/users/{user_id}/subscription/extend
# =============================================================================
class TestAdminExtendSubscription:
    def test_extend_pushes_expires_at(self, admin_client, db, admin_headers):
        uid = make_user(db)
        make_subscription(db, uid, status="active", expires_in_days=10)
        new_date = _isoplus_days(90)
        resp = admin_client.patch(
            f"/api/v1/admin/users/{uid}/subscription/extend",
            json={"new_expires_at": new_date, "reason": "trial-grace"},
            headers=admin_headers,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["expires_at"].startswith(new_date)

    def test_extend_requires_totp(self, admin_client, db, totp_secret):
        uid = make_user(db)
        make_subscription(db, uid, status="active")
        resp = admin_client.patch(
            f"/api/v1/admin/users/{uid}/subscription/extend",
            json={
                "new_expires_at": _isoplus_days(90),
                "reason": "no-totp",
            },
            headers={"X-Admin-Operator": "test"},
        )
        assert resp.status_code == 401
        assert resp.json()["detail"] == "totp_required"

    def test_extend_422_when_new_date_in_past_or_before_current(self, admin_client, db, admin_headers):
        uid = make_user(db)
        make_subscription(db, uid, status="active", expires_in_days=30)
        # Past date
        past = _isoplus_days(-1)
        resp = admin_client.patch(
            f"/api/v1/admin/users/{uid}/subscription/extend",
            json={"new_expires_at": past, "reason": "past"},
            headers=admin_headers,
        )
        assert resp.status_code == 422
        # Before current expiry (10 days < 30 days)
        too_short = _isoplus_days(10)
        resp = admin_client.patch(
            f"/api/v1/admin/users/{uid}/subscription/extend",
            json={"new_expires_at": too_short, "reason": "shorter"},
            headers=admin_headers,
        )
        assert resp.status_code == 422

    def test_extend_404_when_no_subscription(self, admin_client, db, admin_headers):
        uid = make_user(db)
        resp = admin_client.patch(
            f"/api/v1/admin/users/{uid}/subscription/extend",
            json={
                "new_expires_at": _isoplus_days(60),
                "reason": "phantom",
            },
            headers=admin_headers,
        )
        assert resp.status_code == 404
        assert resp.json()["detail"] == "subscription_not_found"


# =============================================================================
# Auth gate (router mount + ADMIN_API_KEY) sanity checks via raw_client
# =============================================================================
class TestAdminAuthGate:
    def test_get_without_admin_key_returns_403(self, raw_client, db):
        uid = make_user(db)
        resp = raw_client.get(f"/api/v1/admin/users/{uid}/subscription")
        assert resp.status_code == 403
        assert resp.json()["detail"] == "forbidden"

    def test_get_with_correct_admin_key_passes_auth(self, raw_client, db):
        # Correct key but the user has no subscription → 404 (not 403)
        uid = make_user(db)
        resp = raw_client.get(
            f"/api/v1/admin/users/{uid}/subscription",
            headers={"Authorization": "Bearer test-admin-key-padded-to-32-chars-min"},
        )
        assert resp.status_code == 404
        assert resp.json()["detail"] == "subscription_not_found"

    def test_random_user_id_returns_404_not_500(self, admin_client):
        ghost = uuid.uuid4()
        resp = admin_client.get(f"/api/v1/admin/users/{ghost}/subscription")
        assert resp.status_code == 404
