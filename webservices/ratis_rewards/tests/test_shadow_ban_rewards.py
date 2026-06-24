"""Anti-fraud V1 — verify scan rewards are skipped for shadow-banned users.

The shadow ban is meant to be silent — the API still returns 200, the
caller (PA) doesn't see anything different, but no CAB is awarded.
"""

from __future__ import annotations

import uuid

from repositories.cab_repository import get_balance
from sqlalchemy import text

from tests.conftest import make_user


def _ban(db, user_id: uuid.UUID) -> None:
    db.execute(
        text("UPDATE users SET is_shadow_banned = true WHERE id = :uid"),
        {"uid": str(user_id)},
    )
    db.commit()


class TestShadowBanSkipsRewards:
    def test_normal_user_earns_cab(self, client, db):
        """Sanity check : non-banned user receives the configured CAB."""
        uid = make_user(db)
        resp = client.post(
            "/api/v1/rewards/events/action",
            json={
                "user_id": str(uid),
                "action_type": "receipt_scan",
                "idempotency_key": str(uuid.uuid4()),
            },
        )
        assert resp.status_code == 200
        # cab_per_receipt_scan from seeded settings (V1.x recal).
        assert get_balance(db, uid) == 20

    def test_shadow_banned_user_earns_nothing(self, client, db):
        """Banned user : same 200, but balance stays at 0."""
        uid = make_user(db)
        _ban(db, uid)
        resp = client.post(
            "/api/v1/rewards/events/action",
            json={
                "user_id": str(uid),
                "action_type": "receipt_scan",
                "idempotency_key": str(uuid.uuid4()),
            },
        )
        # Endpoint is silent — same 200 to the PA caller.
        assert resp.status_code == 200
        assert get_balance(db, uid) == 0

    def test_shadow_banned_user_no_xp_either(self, client, db):
        uid = make_user(db)
        _ban(db, uid)
        client.post(
            "/api/v1/rewards/events/action",
            json={
                "user_id": str(uid),
                "action_type": "receipt_scan",
                "idempotency_key": str(uuid.uuid4()),
            },
        )
        row = db.execute(
            text("SELECT balance FROM user_xp_balance WHERE user_id = :uid"),
            {"uid": str(uid)},
        ).first()
        # Either no row was created or the balance is 0 — both are fine
        # (the row may pre-exist from a fixture seed). Strong assertion :
        # balance never moved.
        assert row is None or row.balance == 0

    def test_unbanning_via_admin_restores_rewards(self, client, admin_client, db):
        """End-to-end : ban → no reward → admin un-ban → reward."""
        uid = make_user(db)
        _ban(db, uid)
        client.post(
            "/api/v1/rewards/events/action",
            json={
                "user_id": str(uid),
                "action_type": "receipt_scan",
                "idempotency_key": str(uuid.uuid4()),
            },
        )
        assert get_balance(db, uid) == 0

        # Admin un-bans.
        admin_client.patch(
            f"/api/v1/admin/users/{uid}/shadow-ban",
            json={"enabled": False, "reason": "false_positive"},
            headers={"X-Admin-Operator": "alice"},
        )

        # Now scans earn again.
        client.post(
            "/api/v1/rewards/events/action",
            json={
                "user_id": str(uid),
                "action_type": "receipt_scan",
                "idempotency_key": str(uuid.uuid4()),
            },
        )
        assert get_balance(db, uid) == 20
