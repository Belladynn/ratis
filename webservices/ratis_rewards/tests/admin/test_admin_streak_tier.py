"""Admin StreakTier CRUD endpoints — gamification streak tier administration.

Calque on tests/admin/test_admin_reward_config.py — StreakTier maps a
``days`` count (UNIQUE) to a CAB ``multiplier`` (NUMERIC(4,2)) and a
``label`` (string). See ``ratis_core.models.gamification.StreakTier``.

Like RewardConfig, the model has no ``is_active`` / ``is_archived`` column,
so DELETE is a hard delete with a ``pipeline_audit_log`` row stamped
(phase='manual') for traceability.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

from sqlalchemy import text

_OP = {"X-Admin-Operator": "test-admin"}


def _make_streak_tier(
    db,
    *,
    days: int = 7,
    multiplier: str = "1.10",
    label: str = "1 week",
) -> uuid.UUID:
    """Insert a streak_tiers row directly."""
    tier_id = uuid.uuid4()
    db.execute(
        text("INSERT INTO streak_tiers (id, days, multiplier, label) VALUES (:id, :d, :m, :l)"),
        {"id": tier_id, "d": days, "m": Decimal(multiplier), "l": label},
    )
    db.commit()
    return tier_id


# ===========================================================================
# GET /api/v1/admin/rewards/streak-tiers
# ===========================================================================
class TestListStreakTiers:
    def test_empty_returns_empty_list(self, admin_client, db):
        resp = admin_client.get("/api/v1/admin/rewards/streak-tiers")
        assert resp.status_code == 200
        body = resp.json()
        assert body == {"tiers": [], "total": 0}

    def test_returns_all(self, admin_client, db):
        t1 = _make_streak_tier(db, days=7, label="1w")
        t2 = _make_streak_tier(db, days=30, label="1m")
        resp = admin_client.get("/api/v1/admin/rewards/streak-tiers")
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 2
        ids = {r["id"] for r in body["tiers"]}
        assert ids == {str(t1), str(t2)}

    def test_pagination(self, admin_client, db):
        _make_streak_tier(db, days=7, label="1w")
        _make_streak_tier(db, days=14, label="2w")
        _make_streak_tier(db, days=30, label="1m")
        r1 = admin_client.get("/api/v1/admin/rewards/streak-tiers?limit=2")
        assert r1.status_code == 200
        assert len(r1.json()["tiers"]) == 2
        assert r1.json()["total"] == 3

        r2 = admin_client.get("/api/v1/admin/rewards/streak-tiers?limit=2&offset=2")
        assert r2.status_code == 200
        assert len(r2.json()["tiers"]) == 1


# ===========================================================================
# GET /api/v1/admin/rewards/streak-tiers/{id}
# ===========================================================================
class TestGetStreakTier:
    def test_returns_tier(self, admin_client, db):
        tid = _make_streak_tier(db, days=7, multiplier="1.50", label="weekly")
        resp = admin_client.get(f"/api/v1/admin/rewards/streak-tiers/{tid}")
        assert resp.status_code == 200
        body = resp.json()
        assert body["id"] == str(tid)
        assert body["days"] == 7
        assert Decimal(body["multiplier"]) == Decimal("1.50")
        assert body["label"] == "weekly"

    def test_unknown_returns_404(self, admin_client, db):
        ghost = uuid.uuid4()
        resp = admin_client.get(f"/api/v1/admin/rewards/streak-tiers/{ghost}")
        assert resp.status_code == 404
        assert resp.json()["detail"] == "streak_tier_not_found"


# ===========================================================================
# POST /api/v1/admin/rewards/streak-tiers
# ===========================================================================
class TestCreateStreakTier:
    def test_creates_basic(self, admin_client, db):
        resp = admin_client.post(
            "/api/v1/admin/rewards/streak-tiers",
            json={"days": 7, "multiplier": "1.10", "label": "1 week"},
            headers=_OP,
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["days"] == 7
        assert Decimal(body["multiplier"]) == Decimal("1.10")
        assert body["label"] == "1 week"

        row = db.execute(
            text("SELECT days, multiplier, label FROM streak_tiers WHERE id = :id"),
            {"id": uuid.UUID(body["id"])},
        ).first()
        assert row is not None
        assert row.days == 7
        assert row.multiplier == Decimal("1.10")
        assert row.label == "1 week"

    def test_duplicate_days_409(self, admin_client, db):
        _make_streak_tier(db, days=7)
        resp = admin_client.post(
            "/api/v1/admin/rewards/streak-tiers",
            json={"days": 7, "multiplier": "2.00", "label": "another"},
            headers=_OP,
        )
        assert resp.status_code == 409
        assert resp.json()["detail"] == "streak_tier_uniqueness_conflict"

    def test_zero_days_422(self, admin_client, db):
        resp = admin_client.post(
            "/api/v1/admin/rewards/streak-tiers",
            json={"days": 0, "multiplier": "1.10", "label": "zero"},
            headers=_OP,
        )
        assert resp.status_code == 422

    def test_negative_multiplier_422(self, admin_client, db):
        resp = admin_client.post(
            "/api/v1/admin/rewards/streak-tiers",
            json={"days": 7, "multiplier": "-1.0", "label": "bad"},
            headers=_OP,
        )
        assert resp.status_code == 422

    def test_missing_operator_header_422(self, admin_client, db):
        resp = admin_client.post(
            "/api/v1/admin/rewards/streak-tiers",
            json={"days": 7, "multiplier": "1.10", "label": "1 week"},
        )
        assert resp.status_code == 422


# ===========================================================================
# PATCH /api/v1/admin/rewards/streak-tiers/{id}
# ===========================================================================
class TestPatchStreakTier:
    def test_patch_multiplier(self, admin_client, db):
        tid = _make_streak_tier(db, days=7, multiplier="1.10", label="1w")
        resp = admin_client.patch(
            f"/api/v1/admin/rewards/streak-tiers/{tid}",
            json={"multiplier": "2.00"},
            headers=_OP,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert Decimal(body["multiplier"]) == Decimal("2.00")
        assert body["days"] == 7
        assert body["label"] == "1w"

        row = db.execute(
            text("SELECT multiplier FROM streak_tiers WHERE id = :id"),
            {"id": tid},
        ).first()
        assert row.multiplier == Decimal("2.00")

    def test_patch_label_only(self, admin_client, db):
        tid = _make_streak_tier(db, days=14, label="old")
        resp = admin_client.patch(
            f"/api/v1/admin/rewards/streak-tiers/{tid}",
            json={"label": "new"},
            headers=_OP,
        )
        assert resp.status_code == 200
        assert resp.json()["label"] == "new"
        assert resp.json()["days"] == 14

    def test_patch_to_collide_returns_409(self, admin_client, db):
        t1 = _make_streak_tier(db, days=7, label="1w")
        t2 = _make_streak_tier(db, days=14, label="2w")
        # Trying to change t2.days → 7 would collide with t1.
        resp = admin_client.patch(
            f"/api/v1/admin/rewards/streak-tiers/{t2}",
            json={"days": 7},
            headers=_OP,
        )
        assert resp.status_code == 409
        assert resp.json()["detail"] == "streak_tier_uniqueness_conflict"
        # t2 row untouched.
        row = db.execute(
            text("SELECT days FROM streak_tiers WHERE id = :id"),
            {"id": t2},
        ).first()
        assert row.days == 14
        assert t1 is not None

    def test_unknown_returns_404(self, admin_client, db):
        ghost = uuid.uuid4()
        resp = admin_client.patch(
            f"/api/v1/admin/rewards/streak-tiers/{ghost}",
            json={"label": "x"},
            headers=_OP,
        )
        assert resp.status_code == 404
        assert resp.json()["detail"] == "streak_tier_not_found"

    def test_empty_patch_returns_200(self, admin_client, db):
        tid = _make_streak_tier(db, days=7, label="1w")
        resp = admin_client.patch(
            f"/api/v1/admin/rewards/streak-tiers/{tid}",
            json={},
            headers=_OP,
        )
        assert resp.status_code == 200
        assert resp.json()["days"] == 7

    def test_invalid_value_422(self, admin_client, db):
        tid = _make_streak_tier(db, days=7)
        resp = admin_client.patch(
            f"/api/v1/admin/rewards/streak-tiers/{tid}",
            json={"days": -3},
            headers=_OP,
        )
        assert resp.status_code == 422


# ===========================================================================
# DELETE /api/v1/admin/rewards/streak-tiers/{id}
# ===========================================================================
class TestDeleteStreakTier:
    def test_delete_removes_row(self, admin_client, db):
        tid = _make_streak_tier(db, days=7, label="1w")
        resp = admin_client.delete(
            f"/api/v1/admin/rewards/streak-tiers/{tid}",
            headers=_OP,
        )
        assert resp.status_code == 204
        row = db.execute(
            text("SELECT 1 FROM streak_tiers WHERE id = :id"),
            {"id": tid},
        ).first()
        assert row is None

    def test_delete_writes_audit_row(self, admin_client, db):
        tid = _make_streak_tier(db, days=7, multiplier="1.10", label="1 week")
        resp = admin_client.delete(
            f"/api/v1/admin/rewards/streak-tiers/{tid}",
            headers=_OP,
        )
        assert resp.status_code == 204

        audit = db.execute(
            text("SELECT phase, event, payload FROM pipeline_audit_log WHERE event = 'streak_tier_deleted'"),
        ).first()
        assert audit is not None
        assert audit.phase == "manual"
        assert audit.payload["streak_tier_id"] == str(tid)
        assert audit.payload["operator"] == "test-admin"
        assert audit.payload["days"] == 7
        assert audit.payload["label"] == "1 week"

    def test_unknown_returns_404(self, admin_client, db):
        ghost = uuid.uuid4()
        resp = admin_client.delete(
            f"/api/v1/admin/rewards/streak-tiers/{ghost}",
            headers=_OP,
        )
        assert resp.status_code == 404
        assert resp.json()["detail"] == "streak_tier_not_found"


# ===========================================================================
# Auth — raw_client (no admin bypass)
# ===========================================================================
class TestAdminAuth:
    def test_list_without_admin_key_403(self, raw_client, db):
        resp = raw_client.get("/api/v1/admin/rewards/streak-tiers")
        assert resp.status_code == 403

    def test_create_without_admin_key_403(self, raw_client, db):
        resp = raw_client.post(
            "/api/v1/admin/rewards/streak-tiers",
            json={"days": 7, "multiplier": "1.10", "label": "1w"},
            headers=_OP,
        )
        assert resp.status_code == 403

    def test_delete_without_admin_key_403(self, raw_client, db):
        resp = raw_client.delete(
            f"/api/v1/admin/rewards/streak-tiers/{uuid.uuid4()}",
            headers=_OP,
        )
        assert resp.status_code == 403
