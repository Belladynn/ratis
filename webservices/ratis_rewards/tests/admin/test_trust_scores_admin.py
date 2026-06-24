"""Tests for /api/v1/admin/trust-scores + /api/v1/admin/users/{id}/shadow-ban."""

from __future__ import annotations

import uuid

from sqlalchemy import text

from tests.conftest import make_user


def _set_trust(db, uid, *, score: int, total: int, banned: bool = False):
    db.execute(
        text("UPDATE users SET trust_score = :s, total_resolved_scans = :t, is_shadow_banned = :b WHERE id = :uid"),
        {"s": score, "t": total, "b": banned, "uid": str(uid)},
    )
    db.commit()


# ──────────────────────────────────────────────────────────────────────
# GET /admin/trust-scores
# ──────────────────────────────────────────────────────────────────────


class TestListTrustScoresAuth:
    def test_missing_admin_key_returns_403(self, raw_client, db):
        r = raw_client.get("/api/v1/admin/trust-scores")
        assert r.status_code == 403


class TestListTrustScoresFilters:
    def test_all_returns_every_active_user(self, admin_client, db):
        u1 = make_user(db)
        u2 = make_user(db)
        _set_trust(db, u1, score=80, total=120)
        _set_trust(db, u2, score=60, total=120, banned=True)

        r = admin_client.get("/api/v1/admin/trust-scores?status=all&limit=200")
        assert r.status_code == 200
        body = r.json()
        ids = {u["id"] for u in body["users"]}
        assert str(u1) in ids
        assert str(u2) in ids

    def test_warning_filter_returns_warning_band_only(self, admin_client, db):
        ok = make_user(db)
        warn = make_user(db)
        banned = make_user(db)
        under_grace = make_user(db)
        _set_trust(db, ok, score=80, total=200)
        _set_trust(db, warn, score=70, total=200)
        _set_trust(db, banned, score=40, total=200, banned=True)
        # under-grace : low score but not enough scans → not in warning band
        _set_trust(db, under_grace, score=50, total=10)

        r = admin_client.get("/api/v1/admin/trust-scores?status=warning")
        assert r.status_code == 200
        body = r.json()
        ids = {u["id"] for u in body["users"]}
        assert str(warn) in ids
        assert str(ok) not in ids
        assert str(banned) not in ids
        assert str(under_grace) not in ids

    def test_shadow_banned_filter_returns_banned_only(self, admin_client, db):
        ok = make_user(db)
        warn = make_user(db)
        banned = make_user(db)
        _set_trust(db, ok, score=80, total=200)
        _set_trust(db, warn, score=70, total=200)
        _set_trust(db, banned, score=40, total=200, banned=True)

        r = admin_client.get("/api/v1/admin/trust-scores?status=shadow_banned")
        assert r.status_code == 200
        body = r.json()
        ids = {u["id"] for u in body["users"]}
        assert ids == {str(banned)}

    def test_sort_worst_first(self, admin_client, db):
        u1 = make_user(db)
        u2 = make_user(db)
        u3 = make_user(db)
        _set_trust(db, u1, score=90, total=200)
        _set_trust(db, u2, score=50, total=200, banned=True)
        _set_trust(db, u3, score=30, total=200, banned=True)

        r = admin_client.get("/api/v1/admin/trust-scores?status=all&limit=200")
        users = r.json()["users"]
        # First user must be the lowest score among the 3 we made.
        ours = [u for u in users if u["id"] in {str(u1), str(u2), str(u3)}]
        assert next(u["id"] for u in ours) == str(u3)


# ──────────────────────────────────────────────────────────────────────
# PATCH /admin/users/{id}/shadow-ban
# ──────────────────────────────────────────────────────────────────────


class TestPatchShadowBan:
    def test_unknown_user_returns_404(self, admin_client, db):
        r = admin_client.patch(
            f"/api/v1/admin/users/{uuid.uuid4()}/shadow-ban",
            json={"enabled": True, "reason": "test reason"},
            headers={"X-Admin-Operator": "tester"},
        )
        assert r.status_code == 404
        assert r.json()["detail"] == "user_not_found"

    def test_missing_operator_header_422(self, admin_client, db):
        uid = make_user(db)
        r = admin_client.patch(
            f"/api/v1/admin/users/{uid}/shadow-ban",
            json={"enabled": True, "reason": "test reason"},
        )
        # FastAPI validates required headers → 422.
        assert r.status_code == 422

    def test_enable_flips_flag_and_writes_audit(self, admin_client, db):
        uid = make_user(db)
        r = admin_client.patch(
            f"/api/v1/admin/users/{uid}/shadow-ban",
            json={"enabled": True, "reason": "manual_ban_test"},
            headers={"X-Admin-Operator": "alice"},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["is_shadow_banned"] is True
        assert body["previous"] is False

        row = db.execute(
            text("SELECT is_shadow_banned FROM users WHERE id = :uid"),
            {"uid": str(uid)},
        ).first()
        assert row.is_shadow_banned is True

        audit = db.execute(
            text(
                "SELECT payload FROM pipeline_audit_log "
                "WHERE event = 'user_shadow_ban_changed' "
                "  AND payload->>'user_id' = :uid"
            ),
            {"uid": str(uid)},
        ).first()
        assert audit is not None
        assert audit.payload["operator"] == "alice"
        assert audit.payload["reason"] == "manual_ban_test"
        assert audit.payload["new"] is True
        assert audit.payload["previous"] is False

    def test_disable_unbans_user(self, admin_client, db):
        uid = make_user(db)
        _set_trust(db, uid, score=40, total=200, banned=True)
        r = admin_client.patch(
            f"/api/v1/admin/users/{uid}/shadow-ban",
            json={"enabled": False, "reason": "false_positive_recovery"},
            headers={"X-Admin-Operator": "bob"},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["is_shadow_banned"] is False
        assert body["previous"] is True
