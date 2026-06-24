"""Admin RewardConfig CRUD endpoints — gamification config administration.

Calque on tests/admin/test_admin_missions.py — RewardConfig is a per
``action_type`` base CAB amount used by the rewards engine (see
``ratis_core.models.gamification.RewardConfig``).

The schema is minimal :

    action_type   text UNIQUE
    base_amount   integer  (≥ 0 — value is in CAB-cents-equivalent)

No ``is_active`` / ``is_archived`` column on the model, so DELETE is a hard
delete with a ``pipeline_audit_log`` row stamped (phase='manual') for traceability.
"""

from __future__ import annotations

import uuid

from sqlalchemy import text

_OP = {"X-Admin-Operator": "test-admin"}


def _make_reward_config(
    db,
    *,
    action_type: str = "receipt_scan",
    base_amount: int = 50,
) -> uuid.UUID:
    """Insert a reward_config row directly."""
    rc_id = uuid.uuid4()
    db.execute(
        text("INSERT INTO reward_config (id, action_type, base_amount) VALUES (:id, :a, :b)"),
        {"id": rc_id, "a": action_type, "b": base_amount},
    )
    db.commit()
    return rc_id


# ===========================================================================
# GET /api/v1/admin/rewards/configs
# ===========================================================================
class TestListRewardConfigs:
    def test_empty_returns_empty_list(self, admin_client, db):
        resp = admin_client.get("/api/v1/admin/rewards/configs")
        assert resp.status_code == 200
        body = resp.json()
        assert body == {"configs": [], "total": 0}

    def test_returns_all(self, admin_client, db):
        c1 = _make_reward_config(db, action_type="receipt_scan", base_amount=50)
        c2 = _make_reward_config(db, action_type="label_scan", base_amount=20)
        resp = admin_client.get("/api/v1/admin/rewards/configs")
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 2
        ids = {r["id"] for r in body["configs"]}
        assert ids == {str(c1), str(c2)}

    def test_pagination(self, admin_client, db):
        _make_reward_config(db, action_type="receipt_scan", base_amount=50)
        _make_reward_config(db, action_type="label_scan", base_amount=20)
        _make_reward_config(db, action_type="barcode_scan", base_amount=10)
        r1 = admin_client.get("/api/v1/admin/rewards/configs?limit=2")
        assert r1.status_code == 200
        assert len(r1.json()["configs"]) == 2
        assert r1.json()["total"] == 3

        r2 = admin_client.get("/api/v1/admin/rewards/configs?limit=2&offset=2")
        assert r2.status_code == 200
        assert len(r2.json()["configs"]) == 1


# ===========================================================================
# GET /api/v1/admin/rewards/configs/{id}
# ===========================================================================
class TestGetRewardConfig:
    def test_returns_config(self, admin_client, db):
        cid = _make_reward_config(db, action_type="receipt_scan", base_amount=50)
        resp = admin_client.get(f"/api/v1/admin/rewards/configs/{cid}")
        assert resp.status_code == 200
        body = resp.json()
        assert body["id"] == str(cid)
        assert body["action_type"] == "receipt_scan"
        assert body["base_amount"] == 50

    def test_unknown_returns_404(self, admin_client, db):
        ghost = uuid.uuid4()
        resp = admin_client.get(f"/api/v1/admin/rewards/configs/{ghost}")
        assert resp.status_code == 404
        assert resp.json()["detail"] == "reward_config_not_found"


# ===========================================================================
# POST /api/v1/admin/rewards/configs
# ===========================================================================
class TestCreateRewardConfig:
    def test_creates_basic(self, admin_client, db):
        resp = admin_client.post(
            "/api/v1/admin/rewards/configs",
            json={"action_type": "receipt_scan", "base_amount": 75},
            headers=_OP,
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["action_type"] == "receipt_scan"
        assert body["base_amount"] == 75

        row = db.execute(
            text("SELECT action_type, base_amount FROM reward_config WHERE id = :id"),
            {"id": uuid.UUID(body["id"])},
        ).first()
        assert row is not None
        assert row.action_type == "receipt_scan"
        assert row.base_amount == 75

    def test_duplicate_action_type_409(self, admin_client, db):
        _make_reward_config(db, action_type="receipt_scan", base_amount=50)
        resp = admin_client.post(
            "/api/v1/admin/rewards/configs",
            json={"action_type": "receipt_scan", "base_amount": 100},
            headers=_OP,
        )
        assert resp.status_code == 409
        assert resp.json()["detail"] == "reward_config_uniqueness_conflict"

    def test_negative_base_amount_422(self, admin_client, db):
        resp = admin_client.post(
            "/api/v1/admin/rewards/configs",
            json={"action_type": "receipt_scan", "base_amount": -10},
            headers=_OP,
        )
        assert resp.status_code == 422

    def test_missing_operator_header_422(self, admin_client, db):
        resp = admin_client.post(
            "/api/v1/admin/rewards/configs",
            json={"action_type": "receipt_scan", "base_amount": 50},
        )
        assert resp.status_code == 422


# ===========================================================================
# PATCH /api/v1/admin/rewards/configs/{id}
# ===========================================================================
class TestPatchRewardConfig:
    def test_patch_base_amount(self, admin_client, db):
        cid = _make_reward_config(db, action_type="receipt_scan", base_amount=50)
        resp = admin_client.patch(
            f"/api/v1/admin/rewards/configs/{cid}",
            json={"base_amount": 100},
            headers=_OP,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["base_amount"] == 100
        assert body["action_type"] == "receipt_scan"

        row = db.execute(
            text("SELECT base_amount FROM reward_config WHERE id = :id"),
            {"id": cid},
        ).first()
        assert row.base_amount == 100

    def test_patch_to_collide_returns_409(self, admin_client, db):
        c1 = _make_reward_config(db, action_type="receipt_scan", base_amount=50)
        c2 = _make_reward_config(db, action_type="label_scan", base_amount=20)
        # Trying to change c2.action_type → 'receipt_scan' would collide with c1.
        resp = admin_client.patch(
            f"/api/v1/admin/rewards/configs/{c2}",
            json={"action_type": "receipt_scan"},
            headers=_OP,
        )
        assert resp.status_code == 409
        assert resp.json()["detail"] == "reward_config_uniqueness_conflict"
        # c2 row untouched.
        row = db.execute(
            text("SELECT action_type FROM reward_config WHERE id = :id"),
            {"id": c2},
        ).first()
        assert row.action_type == "label_scan"
        assert c1 is not None

    def test_unknown_returns_404(self, admin_client, db):
        ghost = uuid.uuid4()
        resp = admin_client.patch(
            f"/api/v1/admin/rewards/configs/{ghost}",
            json={"base_amount": 25},
            headers=_OP,
        )
        assert resp.status_code == 404
        assert resp.json()["detail"] == "reward_config_not_found"

    def test_empty_patch_returns_200(self, admin_client, db):
        cid = _make_reward_config(db, base_amount=42)
        resp = admin_client.patch(
            f"/api/v1/admin/rewards/configs/{cid}",
            json={},
            headers=_OP,
        )
        assert resp.status_code == 200
        assert resp.json()["base_amount"] == 42

    def test_invalid_value_422(self, admin_client, db):
        cid = _make_reward_config(db)
        resp = admin_client.patch(
            f"/api/v1/admin/rewards/configs/{cid}",
            json={"base_amount": -3},
            headers=_OP,
        )
        assert resp.status_code == 422


# ===========================================================================
# DELETE /api/v1/admin/rewards/configs/{id}
# ===========================================================================
class TestDeleteRewardConfig:
    def test_delete_removes_row(self, admin_client, db):
        cid = _make_reward_config(db, action_type="receipt_scan")
        resp = admin_client.delete(
            f"/api/v1/admin/rewards/configs/{cid}",
            headers=_OP,
        )
        assert resp.status_code == 204
        row = db.execute(
            text("SELECT 1 FROM reward_config WHERE id = :id"),
            {"id": cid},
        ).first()
        assert row is None

    def test_delete_writes_audit_row(self, admin_client, db):
        cid = _make_reward_config(db, action_type="receipt_scan", base_amount=50)
        resp = admin_client.delete(
            f"/api/v1/admin/rewards/configs/{cid}",
            headers=_OP,
        )
        assert resp.status_code == 204

        # Audit row written with phase='manual'
        audit = db.execute(
            text("SELECT phase, event, payload FROM pipeline_audit_log WHERE event = 'reward_config_deleted'"),
        ).first()
        assert audit is not None
        assert audit.phase == "manual"
        assert audit.payload["reward_config_id"] == str(cid)
        assert audit.payload["operator"] == "test-admin"
        assert audit.payload["action_type"] == "receipt_scan"
        assert audit.payload["base_amount"] == 50

    def test_unknown_returns_404(self, admin_client, db):
        ghost = uuid.uuid4()
        resp = admin_client.delete(
            f"/api/v1/admin/rewards/configs/{ghost}",
            headers=_OP,
        )
        assert resp.status_code == 404
        assert resp.json()["detail"] == "reward_config_not_found"


# ===========================================================================
# Auth — raw_client (no admin bypass)
# ===========================================================================
class TestAdminAuth:
    def test_list_without_admin_key_403(self, raw_client, db):
        resp = raw_client.get("/api/v1/admin/rewards/configs")
        assert resp.status_code == 403

    def test_create_without_admin_key_403(self, raw_client, db):
        resp = raw_client.post(
            "/api/v1/admin/rewards/configs",
            json={"action_type": "receipt_scan", "base_amount": 50},
            headers=_OP,
        )
        assert resp.status_code == 403

    def test_delete_without_admin_key_403(self, raw_client, db):
        resp = raw_client.delete(
            f"/api/v1/admin/rewards/configs/{uuid.uuid4()}",
            headers=_OP,
        )
        assert resp.status_code == 403
