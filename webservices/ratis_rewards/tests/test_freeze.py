"""Tests for mission freeze (POST /gamification/missions/{id}/freeze).

Extracted from test_stonks.py during the Buffer + Burst refonte
(spec ``docs/superpowers/specs/2026-05-09-buffer-burst-design.md``).

Mission freeze is unrelated to Stonks/Buffer/Burst — it survived the
refonte unchanged. Tests live here for clarity (= the rest of
test_stonks.py was removed alongside the dropped feature).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from repositories.cab_repository import award_cab, get_balance
from sqlalchemy import text

from tests.conftest import make_user


def _make_mission(
    db,
    *,
    action_type: str = "label_scan",
    frequency: str = "daily",
    difficulty: str = "easy",
    target_count: int = 3,
    cab_reward: int = 50,
    is_active: bool = True,
) -> uuid.UUID:
    """Insert a catalogue mission row."""
    mission_id = uuid.uuid4()
    db.execute(
        text(
            "INSERT INTO missions "
            "    (id, action_type, frequency, difficulty, "
            "     target_count, cab_reward, is_active) "
            "VALUES (:id, :action, :freq, :diff, :target, :reward, :active)"
        ),
        {
            "id": mission_id,
            "action": action_type,
            "freq": frequency,
            "diff": difficulty,
            "target": target_count,
            "reward": cab_reward,
            "active": is_active,
        },
    )
    db.commit()
    return mission_id


def _make_user_mission(
    db,
    *,
    user_id: uuid.UUID,
    mission_id: uuid.UUID,
    status: str = "pending",
    cab_reward: int = 50,
    freeze_count: int = 0,
    frozen_until=None,
) -> uuid.UUID:
    """Insert a user_missions row with full Buffer + Burst columns."""
    um_id = uuid.uuid4()
    period_start = datetime.now(UTC).date()
    db.execute(
        text(
            "INSERT INTO user_missions "
            "    (id, user_id, mission_id, period_start, current_count, status, "
            "     target_count, cab_reward, xp_reward, buffer_count, "
            "     burst_count, burst_locked, portions_claimed, freeze_count, "
            "     frozen_until) "
            "VALUES (:id, :uid, :mid, :period, 0, :status, "
            "        3, :cab, 10, 0, 0, false, 0, :freeze, :frozen)"
        ),
        {
            "id": um_id,
            "uid": user_id,
            "mid": mission_id,
            "period": period_start,
            "status": status,
            "cab": cab_reward,
            "freeze": freeze_count,
            "frozen": frozen_until,
        },
    )
    db.commit()
    return um_id


class TestMissionFreeze:
    def test_freeze_debits_cab(self, db, user_client):
        client, set_user = user_client
        uid = make_user(db)
        set_user(uid)
        award_cab(db, uid, 500, "receipt_scan")
        db.commit()
        mission_id = _make_mission(db)
        um_id = _make_user_mission(db, user_id=uid, mission_id=mission_id)

        resp = client.post(f"/api/v1/gamification/missions/{um_id}/freeze")
        assert resp.status_code == 200
        # Default freeze cost is 100 CABs (from settings)
        assert get_balance(db, uid) == 400

    def test_freeze_sets_frozen_until_next_month(self, db, user_client):
        client, set_user = user_client
        uid = make_user(db)
        set_user(uid)
        award_cab(db, uid, 500, "receipt_scan")
        db.commit()
        mission_id = _make_mission(db)
        um_id = _make_user_mission(db, user_id=uid, mission_id=mission_id)

        now = datetime.now(UTC)
        resp = client.post(f"/api/v1/gamification/missions/{um_id}/freeze")
        assert resp.status_code == 200

        data = resp.json()
        # frozen_until should be the first day of the next month
        frozen_until = datetime.fromisoformat(data["frozen_until"].replace("Z", "+00:00"))
        assert frozen_until.day == 1
        assert frozen_until.month in (now.month % 12 + 1, 1)

    def test_freeze_increments_freeze_count(self, db, user_client):
        client, set_user = user_client
        uid = make_user(db)
        set_user(uid)
        award_cab(db, uid, 500, "receipt_scan")
        db.commit()
        mission_id = _make_mission(db)
        um_id = _make_user_mission(db, user_id=uid, mission_id=mission_id)

        client.post(f"/api/v1/gamification/missions/{um_id}/freeze")

        row = db.execute(
            text("SELECT freeze_count FROM user_missions WHERE id = :id"),
            {"id": um_id},
        ).first()
        assert row.freeze_count == 1

    def test_freeze_already_frozen(self, db, user_client):
        client, set_user = user_client
        uid = make_user(db)
        set_user(uid)
        award_cab(db, uid, 500, "receipt_scan")
        db.commit()
        mission_id = _make_mission(db)
        frozen_until = datetime(2099, 5, 1, tzinfo=UTC)
        um_id = _make_user_mission(
            db,
            user_id=uid,
            mission_id=mission_id,
            frozen_until=frozen_until,
        )

        resp = client.post(f"/api/v1/gamification/missions/{um_id}/freeze")
        assert resp.status_code == 409
        assert resp.json()["detail"] == "mission_already_frozen"

    def test_freeze_limit_reached(self, db, user_client):
        client, set_user = user_client
        uid = make_user(db)
        set_user(uid)
        award_cab(db, uid, 500, "receipt_scan")
        db.commit()
        mission_id = _make_mission(db)
        um_id = _make_user_mission(
            db,
            user_id=uid,
            mission_id=mission_id,
            freeze_count=1,
        )

        resp = client.post(f"/api/v1/gamification/missions/{um_id}/freeze")
        assert resp.status_code == 409
        assert resp.json()["detail"] == "freeze_limit_reached"

    def test_freeze_insufficient_balance(self, db, user_client):
        client, set_user = user_client
        uid = make_user(db)
        set_user(uid)
        # No CABs
        mission_id = _make_mission(db)
        um_id = _make_user_mission(db, user_id=uid, mission_id=mission_id)

        resp = client.post(f"/api/v1/gamification/missions/{um_id}/freeze")
        assert resp.status_code == 422
        assert resp.json()["detail"] == "insufficient_cab_balance"

    def test_freeze_requires_auth(self, raw_client):
        resp = raw_client.post(f"/api/v1/gamification/missions/{uuid.uuid4()}/freeze")
        assert resp.status_code == 401
