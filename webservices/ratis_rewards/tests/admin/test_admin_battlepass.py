"""Admin BattlePass endpoints — seasons + milestones (tiers) administration."""

from __future__ import annotations

import uuid
from datetime import UTC

from sqlalchemy import text

from tests.conftest import make_milestone, make_season

_OP = {"X-Admin-Operator": "test-admin"}


def _future_iso(days: int) -> str:
    """Return an ISO timestamp ``days`` from now (UTC)."""
    from datetime import datetime, timedelta

    return (datetime.now(UTC) + timedelta(days=days)).isoformat()


# ===========================================================================
# GET /api/v1/admin/battlepass/seasons
# ===========================================================================
class TestListSeasons:
    def test_empty_returns_empty_list(self, admin_client, db):
        resp = admin_client.get("/api/v1/admin/battlepass/seasons")
        assert resp.status_code == 200
        body = resp.json()
        assert body == {"seasons": [], "total": 0}

    def test_returns_active_and_inactive(self, admin_client, db):
        s_active = make_season(db, season_number=2, is_active=True)
        s_draft = make_season(db, season_number=1, is_active=False)
        resp = admin_client.get("/api/v1/admin/battlepass/seasons")
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 2
        # Ordered by season_number desc → active (2) first, draft (1) second.
        ids = [s["id"] for s in body["seasons"]]
        assert ids == [str(s_active), str(s_draft)]
        assert body["seasons"][0]["is_active"] is True
        assert body["seasons"][1]["is_active"] is False

    def test_no_totp_required_read_only(self, admin_client, db):
        make_season(db, season_number=1, is_active=False)
        # admin_client bypasses ADMIN_API_KEY ; missing X-Admin-TOTP must NOT
        # trigger 401 — read-only endpoint, distinct from mutation gate.
        resp = admin_client.get("/api/v1/admin/battlepass/seasons")
        assert resp.status_code == 200


# ===========================================================================
# POST /api/v1/admin/battlepass/seasons
# ===========================================================================
class TestCreateSeason:
    def test_creates_draft_season(self, admin_client, db):
        resp = admin_client.post(
            "/api/v1/admin/battlepass/seasons",
            json={
                "name": "Saison 1 — Pilot",
                "season_number": 1,
                "started_at": _future_iso(1),
                "ends_at": _future_iso(91),
            },
            headers=_OP,
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["season_number"] == 1
        assert body["name"] == "Saison 1 — Pilot"
        # Draft : not active until explicit activate.
        assert body["is_active"] is False

        row = db.execute(
            text("SELECT season_number, name, is_active FROM battlepass_seasons WHERE id = :sid"),
            {"sid": uuid.UUID(body["id"])},
        ).first()
        assert row is not None
        assert row.season_number == 1
        assert row.is_active is False

    def test_missing_operator_header_422(self, admin_client, db):
        resp = admin_client.post(
            "/api/v1/admin/battlepass/seasons",
            json={
                "name": "Saison X",
                "season_number": 1,
                "started_at": _future_iso(1),
                "ends_at": _future_iso(30),
            },
            # No X-Admin-Operator header.
        )
        assert resp.status_code == 422

    def test_ends_before_start_422(self, admin_client, db):
        resp = admin_client.post(
            "/api/v1/admin/battlepass/seasons",
            json={
                "name": "Saison Bad",
                "season_number": 7,
                "started_at": _future_iso(30),
                "ends_at": _future_iso(1),  # ends before start
            },
            headers=_OP,
        )
        assert resp.status_code == 422

    def test_ends_equal_start_422(self, admin_client, db):
        ts = _future_iso(5)
        resp = admin_client.post(
            "/api/v1/admin/battlepass/seasons",
            json={
                "name": "Saison Bad",
                "season_number": 7,
                "started_at": ts,
                "ends_at": ts,
            },
            headers=_OP,
        )
        assert resp.status_code == 422

    def test_duplicate_season_number_409(self, admin_client, db):
        make_season(db, season_number=3)
        resp = admin_client.post(
            "/api/v1/admin/battlepass/seasons",
            json={
                "name": "Collision",
                "season_number": 3,
                "started_at": _future_iso(1),
                "ends_at": _future_iso(30),
            },
            headers=_OP,
        )
        assert resp.status_code == 409
        assert resp.json()["detail"] == "season_number_conflict"

    def test_blank_name_422(self, admin_client, db):
        resp = admin_client.post(
            "/api/v1/admin/battlepass/seasons",
            json={
                "name": "",
                "season_number": 9,
                "started_at": _future_iso(1),
                "ends_at": _future_iso(30),
            },
            headers=_OP,
        )
        assert resp.status_code == 422

    def test_zero_season_number_422(self, admin_client, db):
        resp = admin_client.post(
            "/api/v1/admin/battlepass/seasons",
            json={
                "name": "Saison 0",
                "season_number": 0,
                "started_at": _future_iso(1),
                "ends_at": _future_iso(30),
            },
            headers=_OP,
        )
        assert resp.status_code == 422


# ===========================================================================
# PATCH /api/v1/admin/battlepass/seasons/{id}/activate
# ===========================================================================
class TestActivateSeason:
    def test_activate_draft_marks_active(self, admin_client, db):
        sid = make_season(db, season_number=1, is_active=False)
        resp = admin_client.patch(
            f"/api/v1/admin/battlepass/seasons/{sid}/activate",
            headers=_OP,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body == {"id": str(sid), "is_active": True}

        row = db.execute(
            text("SELECT is_active FROM battlepass_seasons WHERE id = :sid"),
            {"sid": sid},
        ).first()
        assert row is not None
        assert row.is_active is True

    def test_already_active_idempotent(self, admin_client, db):
        sid = make_season(db, season_number=1, is_active=True)
        resp = admin_client.patch(
            f"/api/v1/admin/battlepass/seasons/{sid}/activate",
            headers=_OP,
        )
        assert resp.status_code == 200

    def test_unknown_season_404(self, admin_client, db):
        ghost = uuid.uuid4()
        resp = admin_client.patch(
            f"/api/v1/admin/battlepass/seasons/{ghost}/activate",
            headers=_OP,
        )
        assert resp.status_code == 404
        assert resp.json()["detail"] == "season_not_found"

    def test_other_active_returns_409(self, admin_client, db):
        active = make_season(db, season_number=1, is_active=True)
        draft = make_season(db, season_number=2, is_active=False)
        resp = admin_client.patch(
            f"/api/v1/admin/battlepass/seasons/{draft}/activate",
            headers=_OP,
        )
        assert resp.status_code == 409
        assert resp.json()["detail"] == "active_season_conflict"

        # Original active row untouched.
        row = db.execute(
            text("SELECT is_active FROM battlepass_seasons WHERE id = :sid"),
            {"sid": active},
        ).first()
        assert row.is_active is True

    def test_missing_operator_header_422(self, admin_client, db):
        sid = make_season(db, season_number=1, is_active=False)
        resp = admin_client.patch(
            f"/api/v1/admin/battlepass/seasons/{sid}/activate",
        )
        assert resp.status_code == 422


# ===========================================================================
# POST /api/v1/admin/battlepass/seasons/{id}/tiers
# ===========================================================================
class TestCreateMilestone:
    def test_create_milestone_in_season(self, admin_client, db):
        sid = make_season(db, season_number=1, is_active=False)
        resp = admin_client.post(
            f"/api/v1/admin/battlepass/seasons/{sid}/tiers",
            json={
                "milestone_number": 1,
                "cab_required": 200,
                "reward_type": "cab",
                "reward_value": 100,
                "subscriber_only": False,
            },
            headers=_OP,
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["season_id"] == str(sid)
        assert body["milestone_number"] == 1
        assert body["reward_type"] == "cab"

        row = db.execute(
            text(
                "SELECT season_id, milestone_number, cab_required, reward_type "
                "FROM battlepass_milestones WHERE id = :mid"
            ),
            {"mid": uuid.UUID(body["id"])},
        ).first()
        assert row is not None
        assert row.season_id == sid
        assert row.cab_required == 200

    def test_unknown_season_404(self, admin_client, db):
        ghost = uuid.uuid4()
        resp = admin_client.post(
            f"/api/v1/admin/battlepass/seasons/{ghost}/tiers",
            json={
                "milestone_number": 1,
                "cab_required": 100,
                "reward_type": "cab",
                "reward_value": 50,
            },
            headers=_OP,
        )
        assert resp.status_code == 404
        assert resp.json()["detail"] == "season_not_found"

    def test_duplicate_milestone_number_409(self, admin_client, db):
        sid = make_season(db, season_number=1)
        make_milestone(db, season_id=sid, milestone_number=1)
        resp = admin_client.post(
            f"/api/v1/admin/battlepass/seasons/{sid}/tiers",
            json={
                "milestone_number": 1,
                "cab_required": 500,
                "reward_type": "gift_card",
                "reward_value": 1000,
            },
            headers=_OP,
        )
        assert resp.status_code == 409
        assert resp.json()["detail"] == "milestone_number_conflict"

    def test_invalid_reward_type_422(self, admin_client, db):
        sid = make_season(db, season_number=1)
        resp = admin_client.post(
            f"/api/v1/admin/battlepass/seasons/{sid}/tiers",
            json={
                "milestone_number": 5,
                "cab_required": 100,
                "reward_type": "bitcoin",  # not in Literal
                "reward_value": 50,
            },
            headers=_OP,
        )
        assert resp.status_code == 422

    def test_negative_cab_required_422(self, admin_client, db):
        sid = make_season(db, season_number=1)
        resp = admin_client.post(
            f"/api/v1/admin/battlepass/seasons/{sid}/tiers",
            json={
                "milestone_number": 5,
                "cab_required": -10,
                "reward_type": "cab",
                "reward_value": 50,
            },
            headers=_OP,
        )
        assert resp.status_code == 422

    def test_missing_operator_header_422(self, admin_client, db):
        sid = make_season(db, season_number=1)
        resp = admin_client.post(
            f"/api/v1/admin/battlepass/seasons/{sid}/tiers",
            json={
                "milestone_number": 1,
                "cab_required": 100,
                "reward_type": "cab",
                "reward_value": 50,
            },
        )
        assert resp.status_code == 422


# ===========================================================================
# Auth — raw_client (no admin bypass)
# ===========================================================================
class TestAdminAuth:
    def test_list_seasons_without_admin_key_403(self, raw_client, db):
        resp = raw_client.get("/api/v1/admin/battlepass/seasons")
        assert resp.status_code == 403

    def test_create_season_without_admin_key_403(self, raw_client, db):
        resp = raw_client.post(
            "/api/v1/admin/battlepass/seasons",
            json={
                "name": "Saison 1",
                "season_number": 1,
                "started_at": _future_iso(1),
                "ends_at": _future_iso(30),
            },
            headers=_OP,
        )
        assert resp.status_code == 403
