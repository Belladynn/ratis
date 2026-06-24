"""Admin Achievements catalog endpoints — PR 6/8.

Five endpoints (all gated by ``ADMIN_API_KEY`` + ``X-Admin-Operator``
on mutations) :

- ``GET    /api/v1/admin/achievements``                          — list catalog + stats
- ``POST   /api/v1/admin/achievements``                          — create
- ``PATCH  /api/v1/admin/achievements/{id}``                     — update
- ``DELETE /api/v1/admin/achievements/{id}``                     — hard delete (only if 0 unlocks)
- ``POST   /api/v1/admin/users/{user_id}/achievements/{id}/grant`` — manual grant (idempotent)

Audit trail : every mutation writes a ``pipeline_audit_log`` row
(phase='manual') so the action is traceable. Audit events emitted :

- ``achievement_created``         — POST
- ``achievement_updated``         — PATCH (with list of fields modified)
- ``achievement_deleted``         — DELETE (with code of deleted achievement)
- ``achievement_admin_granted``   — POST manual grant (with ``previous`` flag)

Immutable-after-unlock guard : once at least one user has unlocked an
achievement, the fields that drive the unlock condition or the prize
(``trigger_type``, ``target_value``, ``window_days``, ``extra_params``,
``rarity``, ``cab_reward``) become read-only — touching any of them
returns 409 ``achievement_immutable_after_unlock``. Cosmetic fields
(label, description, icon, display_order, is_secret/is_hidden, etc.)
remain editable.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import text

_OP = {"X-Admin-Operator": "test-admin"}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _audit_rows(db, event: str) -> list:
    """Fetch audit rows for a given event ordered by created_at."""
    return db.execute(
        text(
            "SELECT phase, level, event, payload, created_at "
            "FROM pipeline_audit_log "
            "WHERE event = :ev "
            "ORDER BY created_at ASC"
        ),
        {"ev": event},
    ).fetchall()


def _make_minimal_create_body(**overrides) -> dict:
    body = {
        "code": f"_t_{uuid.uuid4().hex[:10]}",
        "label": "Test Achievement",
        "description": "Test description",
        "icon": "trophy",
        "rarity": "bronze",
        "category": "volume",
        "trigger_type": "scan_count",
        "target_value": 10,
        "cab_reward": 50,
    }
    body.update(overrides)
    return body


# ===========================================================================
# GET /api/v1/admin/achievements — list catalog + stats
# ===========================================================================
class TestListAdminAchievements:
    def test_lists_seeded_catalog(self, admin_client, db):
        resp = admin_client.get("/api/v1/admin/achievements")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert "total" in body
        assert "achievements" in body
        # Seed has 23 entries (cf seed_achievements.ACHIEVEMENTS_V1).
        assert body["total"] >= 23

    def test_includes_unlocked_users_and_percentage(self, admin_client, db, achievement_factory, test_user):
        ach = achievement_factory(code="ad_pct_1")
        # No unlocks yet → 0 / 0%.
        r = admin_client.get("/api/v1/admin/achievements")
        item = next(x for x in r.json()["achievements"] if x["code"] == "ad_pct_1")
        assert item["unlocked_users"] == 0
        assert item["unlock_percentage"] == 0.0

        # Insert one unlock manually, recheck.
        db.execute(
            text("INSERT INTO user_achievements     (user_id, achievement_id, cab_granted) VALUES (:uid, :aid, :cab)"),
            {"uid": test_user.id, "aid": ach.id, "cab": ach.cab_reward},
        )
        db.commit()
        r = admin_client.get("/api/v1/admin/achievements")
        item = next(x for x in r.json()["achievements"] if x["code"] == "ad_pct_1")
        assert item["unlocked_users"] == 1
        assert item["unlock_percentage"] > 0.0

    def test_403_without_admin_key(self, raw_client):
        resp = raw_client.get("/api/v1/admin/achievements")
        assert resp.status_code == 403


# ===========================================================================
# POST /api/v1/admin/achievements — create
# ===========================================================================
class TestCreateAdminAchievement:
    def test_creates_basic_achievement(self, admin_client, db):
        body = _make_minimal_create_body(code="ad_create_basic")
        resp = admin_client.post("/api/v1/admin/achievements", json=body, headers=_OP)
        assert resp.status_code == 201, resp.text
        out = resp.json()
        assert out["code"] == "ad_create_basic"
        assert "id" in out

        # Row exists in DB.
        row = db.execute(
            text("SELECT code, label FROM achievements WHERE code = :c"),
            {"c": "ad_create_basic"},
        ).first()
        assert row is not None
        assert row.label == "Test Achievement"

    def test_emits_audit_log_on_create(self, admin_client, db):
        body = _make_minimal_create_body(code="ad_audit_create")
        resp = admin_client.post("/api/v1/admin/achievements", json=body, headers=_OP)
        assert resp.status_code == 201, resp.text
        rows = _audit_rows(db, "achievement_created")
        assert len(rows) >= 1
        latest = rows[-1]
        assert latest.phase == "manual"
        assert latest.payload["operator"] == "test-admin"
        assert latest.payload["code"] == "ad_audit_create"

    def test_rejects_jyetais_category(self, admin_client, db):
        body = _make_minimal_create_body(code="ad_reject_jye", category="j_y_etais")
        resp = admin_client.post("/api/v1/admin/achievements", json=body, headers=_OP)
        assert resp.status_code == 422
        assert resp.json()["detail"] == "cannot_create_jyetais_in_catalog"

    def test_duplicate_code_returns_409(self, admin_client, db, achievement_factory):
        existing = achievement_factory(code="ad_dup_code")
        assert existing.code == "ad_dup_code"
        body = _make_minimal_create_body(code="ad_dup_code")
        resp = admin_client.post("/api/v1/admin/achievements", json=body, headers=_OP)
        assert resp.status_code == 409
        assert resp.json()["detail"] == "achievement_code_taken"

    def test_missing_operator_header_returns_422(self, admin_client, db):
        body = _make_minimal_create_body(code="ad_no_op")
        resp = admin_client.post("/api/v1/admin/achievements", json=body)
        # FastAPI raises 422 when a required Header is missing.
        assert resp.status_code == 422

    def test_403_without_admin_key(self, raw_client):
        body = _make_minimal_create_body(code="ad_no_key")
        resp = raw_client.post("/api/v1/admin/achievements", json=body, headers=_OP)
        assert resp.status_code == 403


# ===========================================================================
# PATCH /api/v1/admin/achievements/{id}
# ===========================================================================
class TestPatchAdminAchievement:
    def test_updates_cosmetic_fields(self, admin_client, db, achievement_factory):
        ach = achievement_factory(code="ad_patch_cos", label="Old label")
        resp = admin_client.patch(
            f"/api/v1/admin/achievements/{ach.id}",
            json={"label": "New label", "description": "New desc"},
            headers=_OP,
        )
        assert resp.status_code == 200, resp.text
        updated_fields = set(resp.json()["updated_fields"])
        assert {"label", "description"}.issubset(updated_fields)

        row = db.execute(
            text("SELECT label, description FROM achievements WHERE id = :id"),
            {"id": ach.id},
        ).first()
        assert row.label == "New label"
        assert row.description == "New desc"

    def test_emits_audit_log_on_update(self, admin_client, db, achievement_factory):
        ach = achievement_factory(code="ad_patch_audit")
        resp = admin_client.patch(
            f"/api/v1/admin/achievements/{ach.id}",
            json={"label": "Newer"},
            headers=_OP,
        )
        assert resp.status_code == 200
        rows = _audit_rows(db, "achievement_updated")
        assert len(rows) >= 1
        latest = rows[-1]
        assert latest.payload["operator"] == "test-admin"
        assert latest.payload["achievement_id"] == str(ach.id)
        assert "label" in latest.payload["fields"]

    def test_blocks_immutable_field_after_unlock(self, admin_client, db, achievement_factory, test_user):
        ach = achievement_factory(code="ad_immut", target_value=5, cab_reward=100)
        db.execute(
            text("INSERT INTO user_achievements     (user_id, achievement_id, cab_granted) VALUES (:uid, :aid, :cab)"),
            {"uid": test_user.id, "aid": ach.id, "cab": 100},
        )
        db.commit()
        resp = admin_client.patch(
            f"/api/v1/admin/achievements/{ach.id}",
            json={"target_value": 10},
            headers=_OP,
        )
        assert resp.status_code == 409
        assert resp.json()["detail"] == "achievement_immutable_after_unlock"

    def test_allows_cosmetic_field_after_unlock(self, admin_client, db, achievement_factory, test_user):
        ach = achievement_factory(code="ad_cos_after_unlock", label="Old")
        db.execute(
            text("INSERT INTO user_achievements     (user_id, achievement_id, cab_granted) VALUES (:uid, :aid, :cab)"),
            {"uid": test_user.id, "aid": ach.id, "cab": ach.cab_reward},
        )
        db.commit()
        resp = admin_client.patch(
            f"/api/v1/admin/achievements/{ach.id}",
            json={"label": "Renamed"},
            headers=_OP,
        )
        assert resp.status_code == 200, resp.text

    def test_unknown_returns_404(self, admin_client):
        ghost = uuid.uuid4()
        resp = admin_client.patch(
            f"/api/v1/admin/achievements/{ghost}",
            json={"label": "x"},
            headers=_OP,
        )
        assert resp.status_code == 404
        assert resp.json()["detail"] == "achievement_not_found"

    def test_403_without_admin_key(self, raw_client, achievement_factory):
        ach = achievement_factory(code="ad_patch_no_key")
        resp = raw_client.patch(
            f"/api/v1/admin/achievements/{ach.id}",
            json={"label": "x"},
            headers=_OP,
        )
        assert resp.status_code == 403


# ===========================================================================
# DELETE /api/v1/admin/achievements/{id}
# ===========================================================================
class TestDeleteAdminAchievement:
    def test_hard_deletes_when_no_unlocks(self, admin_client, db, achievement_factory):
        ach = achievement_factory(code="ad_del_clean")
        ach_id = ach.id
        resp = admin_client.delete(f"/api/v1/admin/achievements/{ach_id}", headers=_OP)
        assert resp.status_code == 200, resp.text
        assert resp.json() == {"ok": True}

        row = db.execute(text("SELECT id FROM achievements WHERE id = :id"), {"id": ach_id}).first()
        assert row is None

    def test_emits_audit_log_on_delete(self, admin_client, db, achievement_factory):
        ach = achievement_factory(code="ad_del_audit")
        resp = admin_client.delete(f"/api/v1/admin/achievements/{ach.id}", headers=_OP)
        assert resp.status_code == 200
        rows = _audit_rows(db, "achievement_deleted")
        assert len(rows) >= 1
        latest = rows[-1]
        assert latest.payload["operator"] == "test-admin"
        assert latest.payload["code"] == "ad_del_audit"

    def test_blocks_when_at_least_one_unlock(self, admin_client, db, achievement_factory, test_user):
        ach = achievement_factory(code="ad_del_with_unlock")
        db.execute(
            text("INSERT INTO user_achievements     (user_id, achievement_id, cab_granted) VALUES (:uid, :aid, :cab)"),
            {"uid": test_user.id, "aid": ach.id, "cab": ach.cab_reward},
        )
        db.commit()
        resp = admin_client.delete(f"/api/v1/admin/achievements/{ach.id}", headers=_OP)
        assert resp.status_code == 409
        assert resp.json()["detail"] == "achievement_has_unlocks"

        # Row still there.
        row = db.execute(text("SELECT id FROM achievements WHERE id = :id"), {"id": ach.id}).first()
        assert row is not None

    def test_unknown_returns_404(self, admin_client):
        ghost = uuid.uuid4()
        resp = admin_client.delete(f"/api/v1/admin/achievements/{ghost}", headers=_OP)
        assert resp.status_code == 404
        assert resp.json()["detail"] == "achievement_not_found"

    def test_403_without_admin_key(self, raw_client, achievement_factory):
        ach = achievement_factory(code="ad_del_no_key")
        resp = raw_client.delete(f"/api/v1/admin/achievements/{ach.id}", headers=_OP)
        assert resp.status_code == 403


# ===========================================================================
# POST /api/v1/admin/users/{user_id}/achievements/{achievement_id}/grant
# ===========================================================================
class TestAdminGrantAchievement:
    def test_grants_first_time_returns_previous_false(self, admin_client, db, achievement_factory, test_user):
        ach = achievement_factory(code="ad_grant_first", cab_reward=75)
        resp = admin_client.post(
            f"/api/v1/admin/users/{test_user.id}/achievements/{ach.id}/grant",
            json={"reason": "compensating support ticket #1234"},
            headers=_OP,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["previous"] is False
        assert body["user_id"] == str(test_user.id)
        assert body["achievement_id"] == str(ach.id)

        # user_achievements row created.
        ua_row = db.execute(
            text("SELECT id, cab_granted FROM user_achievements WHERE user_id = :uid AND achievement_id = :aid"),
            {"uid": test_user.id, "aid": ach.id},
        ).first()
        assert ua_row is not None
        assert ua_row.cab_granted == 75

    def test_grants_idempotent_returns_previous_true(self, admin_client, db, achievement_factory, test_user):
        ach = achievement_factory(code="ad_grant_idem")
        # Pre-existing unlock.
        db.execute(
            text("INSERT INTO user_achievements     (user_id, achievement_id, cab_granted) VALUES (:uid, :aid, :cab)"),
            {"uid": test_user.id, "aid": ach.id, "cab": ach.cab_reward},
        )
        db.commit()

        resp = admin_client.post(
            f"/api/v1/admin/users/{test_user.id}/achievements/{ach.id}/grant",
            json={"reason": "double-check no double grant"},
            headers=_OP,
        )
        assert resp.status_code == 200
        assert resp.json()["previous"] is True

        # Still exactly one row.
        cnt = db.execute(
            text("SELECT COUNT(*) AS n FROM user_achievements WHERE user_id = :uid AND achievement_id = :aid"),
            {"uid": test_user.id, "aid": ach.id},
        ).scalar_one()
        assert cnt == 1

    def test_emits_audit_log_on_grant(self, admin_client, db, achievement_factory, test_user):
        ach = achievement_factory(code="ad_grant_audit")
        resp = admin_client.post(
            f"/api/v1/admin/users/{test_user.id}/achievements/{ach.id}/grant",
            json={"reason": "audit trail test"},
            headers=_OP,
        )
        assert resp.status_code == 200
        rows = _audit_rows(db, "achievement_admin_granted")
        assert len(rows) >= 1
        latest = rows[-1]
        assert latest.payload["operator"] == "test-admin"
        assert latest.payload["user_id"] == str(test_user.id)
        assert latest.payload["achievement_id"] == str(ach.id)
        assert latest.payload["reason"] == "audit trail test"
        assert latest.payload["previous"] is False

    def test_unknown_achievement_returns_404(self, admin_client, test_user):
        ghost = uuid.uuid4()
        resp = admin_client.post(
            f"/api/v1/admin/users/{test_user.id}/achievements/{ghost}/grant",
            json={"reason": "ghost test xx"},
            headers=_OP,
        )
        assert resp.status_code == 404
        assert resp.json()["detail"] == "achievement_not_found"

    def test_short_reason_returns_422(self, admin_client, achievement_factory, test_user):
        ach = achievement_factory(code="ad_grant_short_reason")
        resp = admin_client.post(
            f"/api/v1/admin/users/{test_user.id}/achievements/{ach.id}/grant",
            json={"reason": "x"},  # < 3 chars → fail validation
            headers=_OP,
        )
        assert resp.status_code == 422

    def test_403_without_admin_key(self, raw_client, achievement_factory, test_user):
        ach = achievement_factory(code="ad_grant_no_key")
        resp = raw_client.post(
            f"/api/v1/admin/users/{test_user.id}/achievements/{ach.id}/grant",
            json={"reason": "no key allowed"},
            headers=_OP,
        )
        assert resp.status_code == 403


# ===========================================================================
# Sanity — the audit_until/from window field still survives PATCH
# (regression guard : datetime fields serialize via Pydantic JSON-mode).
# ===========================================================================
class TestPatchDateTimeFields:
    def test_patch_available_until_serializes_iso(self, admin_client, db, achievement_factory):
        ach = achievement_factory(code="ad_patch_until")
        until = (datetime.now(UTC) + timedelta(days=10)).replace(microsecond=0)
        resp = admin_client.patch(
            f"/api/v1/admin/achievements/{ach.id}",
            json={"available_until": until.isoformat()},
            headers=_OP,
        )
        assert resp.status_code == 200, resp.text

        row = db.execute(
            text("SELECT available_until FROM achievements WHERE id = :id"),
            {"id": ach.id},
        ).first()
        assert row.available_until is not None
