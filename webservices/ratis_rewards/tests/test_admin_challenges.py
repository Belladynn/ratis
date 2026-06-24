"""
TDD tests — admin community challenge endpoints.

POST   /api/v1/admin/challenges
POST   /api/v1/admin/challenges/{id}/milestones
GET    /api/v1/admin/challenges
PATCH  /api/v1/admin/challenges/{id}/activate
PATCH  /api/v1/admin/challenges/{id}/deactivate
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import text

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _challenge_payload(**overrides) -> dict:
    base = {
        "title": "Grand défi",
        "description": "Un grand défi communautaire",
        "action_type": "receipt_scan",
        "objective": 10000,
        "starts_at": datetime.now(UTC).isoformat(),
        "ends_at": (datetime.now(UTC) + timedelta(days=30)).isoformat(),
        "grace_period_days": 3,
    }
    base.update(overrides)
    return base


def _milestone_payload(**overrides) -> dict:
    base = {
        "threshold": 1000,
        "reward_type": "cab",
        "reward_value": {"amount": 500},
        "label": "Palier 1",
        "sort_order": 1,
    }
    base.update(overrides)
    return base


def _insert_challenge(db, *, is_active: bool = False, ends_at=None) -> uuid.UUID:
    """Direct SQL helper — creates challenge + progress row."""
    cid = uuid.uuid4()
    if ends_at is None:
        ends_at = datetime.now(UTC) + timedelta(days=30)
    db.execute(
        text(
            "INSERT INTO community_challenges "
            "    (id, title, action_type, objective, starts_at, ends_at, "
            "     grace_period_days, is_active) "
            "VALUES (:id, 'Test', 'receipt_scan', 1000, now(), :ends_at, 3, :active)"
        ),
        {"id": cid, "ends_at": ends_at, "active": is_active},
    )
    db.execute(
        text("INSERT INTO community_challenge_progress (challenge_id, current_count) VALUES (:cid, 0)"),
        {"cid": cid},
    )
    db.commit()
    return cid


# ---------------------------------------------------------------------------
# POST /admin/challenges
# ---------------------------------------------------------------------------


class TestCreateChallenge:
    def test_create_challenge_returns_201(self, admin_client, db):
        resp = admin_client.post("/api/v1/admin/challenges", json=_challenge_payload())
        assert resp.status_code == 201
        body = resp.json()
        assert "id" in body
        assert body["title"] == "Grand défi"
        assert body["action_type"] == "receipt_scan"

    def test_create_challenge_inserts_progress_row(self, admin_client, db):
        resp = admin_client.post("/api/v1/admin/challenges", json=_challenge_payload())
        assert resp.status_code == 201
        cid = uuid.UUID(resp.json()["id"])
        count = db.execute(
            text("SELECT current_count FROM community_challenge_progress WHERE challenge_id = :cid"),
            {"cid": cid},
        ).scalar()
        assert count == 0

    def test_create_challenge_with_action_filter(self, admin_client, db):
        payload = _challenge_payload(action_filter={"category": "toys"})
        resp = admin_client.post("/api/v1/admin/challenges", json=payload)
        assert resp.status_code == 201
        cid = uuid.UUID(resp.json()["id"])
        row = db.execute(
            text("SELECT action_filter FROM community_challenges WHERE id = :cid"),
            {"cid": cid},
        ).first()
        assert row.action_filter == {"category": "toys"}

    def test_create_challenge_not_active_by_default(self, admin_client, db):
        resp = admin_client.post("/api/v1/admin/challenges", json=_challenge_payload())
        assert resp.status_code == 201
        cid = uuid.UUID(resp.json()["id"])
        active = db.execute(
            text("SELECT is_active FROM community_challenges WHERE id = :cid"),
            {"cid": cid},
        ).scalar()
        assert active is False

    def test_create_challenge_missing_required_field_returns_422(self, admin_client, db):
        payload = _challenge_payload()
        del payload["title"]
        resp = admin_client.post("/api/v1/admin/challenges", json=payload)
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# POST /admin/challenges/{id}/milestones
# ---------------------------------------------------------------------------


class TestCreateMilestone:
    def test_create_milestone_returns_201(self, admin_client, db):
        cid = _insert_challenge(db)
        resp = admin_client.post(
            f"/api/v1/admin/challenges/{cid}/milestones",
            json=_milestone_payload(),
        )
        assert resp.status_code == 201
        body = resp.json()
        assert "id" in body
        assert body["threshold"] == 1000
        assert body["reward_type"] == "cab"

    def test_create_milestone_unknown_challenge_returns_404(self, admin_client, db):
        resp = admin_client.post(
            f"/api/v1/admin/challenges/{uuid.uuid4()}/milestones",
            json=_milestone_payload(),
        )
        assert resp.status_code == 404

    def test_create_milestone_invalid_reward_type_returns_422(self, admin_client, db):
        cid = _insert_challenge(db)
        resp = admin_client.post(
            f"/api/v1/admin/challenges/{cid}/milestones",
            json=_milestone_payload(reward_type="gold"),
        )
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# GET /admin/challenges
# ---------------------------------------------------------------------------


class TestListChallenges:
    def test_list_empty(self, admin_client, db):
        resp = admin_client.get("/api/v1/admin/challenges")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_list_returns_challenges_with_status(self, admin_client, db):
        _insert_challenge(db, is_active=True)
        resp = admin_client.get("/api/v1/admin/challenges")
        assert resp.status_code == 200
        items = resp.json()
        assert len(items) == 1
        assert items[0]["status"] in ("active", "frozen", "expired", "inactive")
        assert "current_count" in items[0]
        assert "milestone_count" in items[0]

    def test_list_inactive_challenge_has_inactive_status(self, admin_client, db):
        _insert_challenge(db, is_active=False)
        resp = admin_client.get("/api/v1/admin/challenges")
        items = resp.json()
        assert items[0]["status"] == "inactive"


# ---------------------------------------------------------------------------
# PATCH /admin/challenges/{id}/activate
# ---------------------------------------------------------------------------


class TestActivateChallenge:
    def test_activate_sets_is_active(self, admin_client, db):
        cid = _insert_challenge(db, is_active=False)
        resp = admin_client.patch(f"/api/v1/admin/challenges/{cid}/activate")
        assert resp.status_code == 200
        active = db.execute(
            text("SELECT is_active FROM community_challenges WHERE id = :cid"),
            {"cid": cid},
        ).scalar()
        assert active is True

    def test_activate_conflict_when_another_already_active(self, admin_client, db):
        _insert_challenge(db, is_active=True)
        cid2 = _insert_challenge(db, is_active=False)
        resp = admin_client.patch(f"/api/v1/admin/challenges/{cid2}/activate")
        assert resp.status_code == 409
        assert resp.json()["detail"] == "active_challenge_conflict"

    def test_activate_unknown_challenge_returns_404(self, admin_client, db):
        resp = admin_client.patch(f"/api/v1/admin/challenges/{uuid.uuid4()}/activate")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# PATCH /admin/challenges/{id}/deactivate
# ---------------------------------------------------------------------------


class TestDeactivateChallenge:
    def test_deactivate_sets_is_active_false(self, admin_client, db):
        cid = _insert_challenge(db, is_active=True)
        resp = admin_client.patch(f"/api/v1/admin/challenges/{cid}/deactivate")
        assert resp.status_code == 200
        active = db.execute(
            text("SELECT is_active FROM community_challenges WHERE id = :cid"),
            {"cid": cid},
        ).scalar()
        assert active is False

    def test_deactivate_unknown_challenge_returns_404(self, admin_client, db):
        resp = admin_client.patch(f"/api/v1/admin/challenges/{uuid.uuid4()}/deactivate")
        assert resp.status_code == 404
