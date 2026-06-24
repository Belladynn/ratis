"""Admin Missions endpoints — mission catalogue (templates) administration."""

from __future__ import annotations

import uuid

import pytest
from repositories.exceptions import MissionUniquenessConflict
from repositories.missions_repository import admin_create_mission
from sqlalchemy import text

from tests.conftest import make_mission

_OP = {"X-Admin-Operator": "test-admin"}


# ===========================================================================
# Repository — admin_create_mission uniqueness contract (RW-08)
# ===========================================================================
class TestCreateMissionRepository:
    def test_duplicate_raises_typed_conflict_not_integrity_error(self, db):
        """A duplicate uq_mission tuple surfaces as MissionUniquenessConflict,
        never a raw IntegrityError — the repository relies on the DB UNIQUE
        constraint and catches the violation (RW-08, KP-64)."""
        kwargs = {
            "action_type": "receipt_scan",
            "frequency": "daily",
            "difficulty": "easy",
            "target_count": 1,
            "cab_reward": 50,
        }
        admin_create_mission(db, **kwargs)
        db.commit()
        with pytest.raises(MissionUniquenessConflict):
            admin_create_mission(db, **kwargs)
        db.rollback()


# ===========================================================================
# GET /api/v1/admin/missions/templates
# ===========================================================================
class TestListTemplates:
    def test_empty_returns_empty_list(self, admin_client, db):
        resp = admin_client.get("/api/v1/admin/missions/templates")
        assert resp.status_code == 200
        body = resp.json()
        assert body == {"templates": [], "total": 0}

    def test_returns_all(self, admin_client, db):
        m1 = make_mission(
            db,
            action_type="receipt_scan",
            frequency="daily",
            difficulty="easy",
        )
        m2 = make_mission(
            db,
            action_type="label_scan",
            frequency="weekly",
            difficulty="medium",
        )
        resp = admin_client.get("/api/v1/admin/missions/templates")
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 2
        ids = {t["id"] for t in body["templates"]}
        assert ids == {str(m1), str(m2)}

    def test_filter_by_frequency(self, admin_client, db):
        make_mission(
            db,
            action_type="receipt_scan",
            frequency="daily",
            difficulty="easy",
        )
        make_mission(
            db,
            action_type="receipt_scan",
            frequency="weekly",
            difficulty="easy",
        )
        resp = admin_client.get("/api/v1/admin/missions/templates?frequency=daily")
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 1
        assert body["templates"][0]["frequency"] == "daily"

    def test_filter_by_active_false(self, admin_client, db):
        make_mission(
            db,
            action_type="receipt_scan",
            frequency="daily",
            difficulty="easy",
            is_active=True,
        )
        make_mission(
            db,
            action_type="label_scan",
            frequency="daily",
            difficulty="hard",
            is_active=False,
        )
        resp = admin_client.get("/api/v1/admin/missions/templates?active=false")
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 1
        assert body["templates"][0]["is_active"] is False

    def test_pagination(self, admin_client, db):
        # Schema enforces UNIQUE(action_type, frequency, difficulty), so
        # build up to 3 distinct rows.
        make_mission(
            db,
            action_type="receipt_scan",
            frequency="daily",
            difficulty="easy",
        )
        make_mission(
            db,
            action_type="receipt_scan",
            frequency="daily",
            difficulty="medium",
        )
        make_mission(
            db,
            action_type="receipt_scan",
            frequency="daily",
            difficulty="hard",
        )
        r1 = admin_client.get("/api/v1/admin/missions/templates?limit=2")
        assert r1.status_code == 200
        assert len(r1.json()["templates"]) == 2
        assert r1.json()["total"] == 3

        r2 = admin_client.get("/api/v1/admin/missions/templates?limit=2&offset=2")
        assert r2.status_code == 200
        assert len(r2.json()["templates"]) == 1

    def test_no_totp_required(self, admin_client, db):
        make_mission(db)
        resp = admin_client.get("/api/v1/admin/missions/templates")
        assert resp.status_code == 200


# ===========================================================================
# POST /api/v1/admin/missions/templates
# ===========================================================================
class TestCreateTemplate:
    def test_creates_active_default(self, admin_client, db):
        resp = admin_client.post(
            "/api/v1/admin/missions/templates",
            json={
                "action_type": "receipt_scan",
                "frequency": "daily",
                "difficulty": "easy",
                "target_count": 1,
                "cab_reward": 50,
            },
            headers=_OP,
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["action_type"] == "receipt_scan"
        assert body["frequency"] == "daily"
        assert body["difficulty"] == "easy"
        assert body["is_active"] is True
        assert body["is_boostable"] is True

        row = db.execute(
            text(
                "SELECT action_type, frequency, difficulty, target_count, "
                "       cab_reward, is_active "
                "FROM missions WHERE id = :id"
            ),
            {"id": uuid.UUID(body["id"])},
        ).first()
        assert row is not None
        assert row.target_count == 1
        assert row.cab_reward == 50

    def test_creates_inactive_when_requested(self, admin_client, db):
        resp = admin_client.post(
            "/api/v1/admin/missions/templates",
            json={
                "action_type": "label_scan",
                "frequency": "weekly",
                "difficulty": "hard",
                "target_count": 5,
                "cab_reward": 200,
                "is_active": False,
                "is_boostable": False,
            },
            headers=_OP,
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["is_active"] is False
        assert body["is_boostable"] is False

    def test_duplicate_unique_tuple_409(self, admin_client, db):
        make_mission(
            db,
            action_type="receipt_scan",
            frequency="daily",
            difficulty="easy",
        )
        resp = admin_client.post(
            "/api/v1/admin/missions/templates",
            json={
                "action_type": "receipt_scan",
                "frequency": "daily",
                "difficulty": "easy",
                "target_count": 3,
                "cab_reward": 60,
            },
            headers=_OP,
        )
        assert resp.status_code == 409
        assert resp.json()["detail"] == "mission_uniqueness_conflict"

    def test_invalid_action_type_422(self, admin_client, db):
        resp = admin_client.post(
            "/api/v1/admin/missions/templates",
            json={
                "action_type": "stargazing",  # not in Literal
                "frequency": "daily",
                "difficulty": "easy",
                "target_count": 1,
                "cab_reward": 50,
            },
            headers=_OP,
        )
        assert resp.status_code == 422

    def test_invalid_frequency_422(self, admin_client, db):
        resp = admin_client.post(
            "/api/v1/admin/missions/templates",
            json={
                "action_type": "receipt_scan",
                "frequency": "yearly",  # not in Literal
                "difficulty": "easy",
                "target_count": 1,
                "cab_reward": 50,
            },
            headers=_OP,
        )
        assert resp.status_code == 422

    def test_zero_target_count_422(self, admin_client, db):
        resp = admin_client.post(
            "/api/v1/admin/missions/templates",
            json={
                "action_type": "receipt_scan",
                "frequency": "daily",
                "difficulty": "easy",
                "target_count": 0,
                "cab_reward": 50,
            },
            headers=_OP,
        )
        assert resp.status_code == 422

    def test_negative_cab_reward_422(self, admin_client, db):
        resp = admin_client.post(
            "/api/v1/admin/missions/templates",
            json={
                "action_type": "receipt_scan",
                "frequency": "daily",
                "difficulty": "easy",
                "target_count": 1,
                "cab_reward": -10,
            },
            headers=_OP,
        )
        assert resp.status_code == 422

    def test_missing_operator_header_422(self, admin_client, db):
        resp = admin_client.post(
            "/api/v1/admin/missions/templates",
            json={
                "action_type": "receipt_scan",
                "frequency": "daily",
                "difficulty": "easy",
                "target_count": 1,
                "cab_reward": 50,
            },
        )
        assert resp.status_code == 422


# ===========================================================================
# PATCH /api/v1/admin/missions/templates/{id}
# ===========================================================================
class TestPatchTemplate:
    def test_patch_target_count(self, admin_client, db):
        mid = make_mission(
            db,
            action_type="receipt_scan",
            frequency="daily",
            difficulty="easy",
            target_count=1,
            cab_reward=50,
        )
        resp = admin_client.patch(
            f"/api/v1/admin/missions/templates/{mid}",
            json={"target_count": 5},
            headers=_OP,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["target_count"] == 5
        # Untouched fields preserved.
        assert body["cab_reward"] == 50
        assert body["action_type"] == "receipt_scan"

        row = db.execute(
            text("SELECT target_count, cab_reward FROM missions WHERE id = :id"),
            {"id": mid},
        ).first()
        assert row.target_count == 5
        assert row.cab_reward == 50

    def test_patch_active_flag(self, admin_client, db):
        mid = make_mission(db, is_active=True)
        resp = admin_client.patch(
            f"/api/v1/admin/missions/templates/{mid}",
            json={"is_active": False},
            headers=_OP,
        )
        assert resp.status_code == 200
        assert resp.json()["is_active"] is False

    def test_patch_to_collide_returns_409(self, admin_client, db):
        m1 = make_mission(
            db,
            action_type="receipt_scan",
            frequency="daily",
            difficulty="easy",
        )
        m2 = make_mission(
            db,
            action_type="label_scan",
            frequency="daily",
            difficulty="easy",
        )
        # Trying to change m2.action_type → 'receipt_scan' would collide with m1.
        resp = admin_client.patch(
            f"/api/v1/admin/missions/templates/{m2}",
            json={"action_type": "receipt_scan"},
            headers=_OP,
        )
        assert resp.status_code == 409
        assert resp.json()["detail"] == "mission_uniqueness_conflict"
        # m2 row untouched.
        row = db.execute(
            text("SELECT action_type FROM missions WHERE id = :id"),
            {"id": m2},
        ).first()
        assert row.action_type == "label_scan"
        # Silence the unused var warning.
        assert m1 is not None

    def test_unknown_mission_404(self, admin_client, db):
        ghost = uuid.uuid4()
        resp = admin_client.patch(
            f"/api/v1/admin/missions/templates/{ghost}",
            json={"target_count": 9},
            headers=_OP,
        )
        assert resp.status_code == 404
        assert resp.json()["detail"] == "mission_not_found"

    def test_empty_patch_returns_200(self, admin_client, db):
        mid = make_mission(db, target_count=2)
        resp = admin_client.patch(
            f"/api/v1/admin/missions/templates/{mid}",
            json={},
            headers=_OP,
        )
        assert resp.status_code == 200
        assert resp.json()["target_count"] == 2

    def test_invalid_value_422(self, admin_client, db):
        mid = make_mission(db)
        resp = admin_client.patch(
            f"/api/v1/admin/missions/templates/{mid}",
            json={"target_count": -3},
            headers=_OP,
        )
        assert resp.status_code == 422

    def test_missing_operator_header_422(self, admin_client, db):
        mid = make_mission(db)
        resp = admin_client.patch(
            f"/api/v1/admin/missions/templates/{mid}",
            json={"target_count": 5},
        )
        assert resp.status_code == 422


# ===========================================================================
# Auth — raw_client (no admin bypass)
# ===========================================================================
class TestAdminAuth:
    def test_list_without_admin_key_403(self, raw_client, db):
        resp = raw_client.get("/api/v1/admin/missions/templates")
        assert resp.status_code == 403

    def test_create_without_admin_key_403(self, raw_client, db):
        resp = raw_client.post(
            "/api/v1/admin/missions/templates",
            json={
                "action_type": "receipt_scan",
                "frequency": "daily",
                "difficulty": "easy",
                "target_count": 1,
                "cab_reward": 50,
            },
            headers=_OP,
        )
        assert resp.status_code == 403
