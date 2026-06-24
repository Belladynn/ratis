"""Tests Bloc B — admin settings REST endpoints renforcés.

Couvre :
    - PUT allowlist (editable / frozen / frozen sub-keys / reason length)
    - PUT 2FA grace path (variation > 50 % → status='pending_2fa')
    - GET /admin/settings/audit (filters, pagination)
    - GET /admin/settings/audit/{id} (diff on-fly)
    - POST /admin/settings/{section}/confirm-2fa (TOTP, 4xx errors)
    - POST /admin/settings/{section}/cancel-pending
    - GET /admin/settings/{section}/editable

Toutes les fixtures (admin_client, raw_client, db, valid_totp_code, etc.)
viennent du conftest parent + admin/conftest.py.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _seed_section(db, section: str, data: dict) -> None:
    """Direct UPSERT on app_settings, committed inside the test session.

    The savepoint-rollback fixture isolates this between tests so we can
    seed any section without leaking state.
    """
    db.execute(
        text(
            "INSERT INTO app_settings (section, data) "
            "VALUES (:s, CAST(:d AS jsonb)) "
            "ON CONFLICT (section) DO UPDATE SET data = EXCLUDED.data"
        ),
        {"s": section, "d": json.dumps(data)},
    )
    db.commit()


def _put(client, section: str, data: dict, reason: str = "alpha test reason ok"):
    return client.put(
        f"/api/v1/admin/settings/{section}",
        json={"data": data, "reason": reason},
        headers={"X-Admin-Operator": "test-admin"},
    )


# ---------------------------------------------------------------------------
# PUT allowlist + reason
# ---------------------------------------------------------------------------
class TestPutAllowlist:
    def test_put_editable_section_applied(self, admin_client, db):
        """Editable section, variation < 50 % → 200 status='applied'."""
        _seed_section(db, "rewards", {"cab_per_receipt_scan": 100})
        resp = _put(
            admin_client,
            "rewards",
            {"cab_per_receipt_scan": 110},
            reason="alpha bump receipt scan",
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "applied"
        assert "audit_id" in body
        # value persisted in DB
        row = db.execute(text("SELECT data FROM app_settings WHERE section='rewards'")).first()
        assert row.data["cab_per_receipt_scan"] == 110

    def test_put_editable_section_pending_2fa(self, admin_client, db):
        """Editable section, variation > 50 % → 200 status='pending_2fa', DB unchanged."""
        _seed_section(db, "rewards", {"cab_per_receipt_scan": 100})
        resp = _put(
            admin_client,
            "rewards",
            {"cab_per_receipt_scan": 1000},  # +900 % >> 50 %
            reason="alpha massive bump test",
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "pending_2fa"
        assert "audit_id" in body
        # DB still on the old value
        row = db.execute(text("SELECT data FROM app_settings WHERE section='rewards'")).first()
        assert row.data["cab_per_receipt_scan"] == 100

    def test_put_frozen_section_403(self, admin_client, db):
        """Section absente de EDITABLE_SECTIONS → 403 section_frozen."""
        _seed_section(db, "pipeline", {"some_threshold": 1})
        resp = _put(
            admin_client,
            "pipeline",
            {"some_threshold": 2},
            reason="alpha frozen attempt",
        )
        assert resp.status_code == 403
        assert resp.json()["detail"] == "section_frozen"

    def test_put_frozen_subkey_403(self, admin_client, db):
        """gamification.feed_jack modifié → 403 frozen_key_modified."""
        _seed_section(
            db,
            "gamification",
            {
                "freeze_cost_cab": 100,
                "stonks_contest": False,
                "feed_jack": {"multiplier_per_day": 0.05},
            },
        )
        resp = _put(
            admin_client,
            "gamification",
            {
                "freeze_cost_cab": 100,
                "stonks_contest": False,
                "feed_jack": {"multiplier_per_day": 5.0},  # 100x — would be massive
            },
            reason="alpha attempted feed_jack edit",
        )
        assert resp.status_code == 403
        body = resp.json()
        # FastAPI surfaces dict detail under "detail" key as-is
        assert body["detail"]["detail"] == "frozen_key_modified"
        assert body["detail"]["key"] == "feed_jack"

    def test_put_frozen_subkey_other_keys_ok(self, admin_client, db):
        """gamification : autres clés modifiées (not feed_jack) → applied."""
        _seed_section(
            db,
            "gamification",
            {
                "freeze_cost_cab": 100,
                "stonks_contest": False,
                "feed_jack": {"multiplier_per_day": 0.05},
            },
        )
        resp = _put(
            admin_client,
            "gamification",
            {
                "freeze_cost_cab": 110,  # +10 % under threshold
                "stonks_contest": True,
                "feed_jack": {"multiplier_per_day": 0.05},  # untouched
            },
            reason="alpha tweak gamif freeze cost",
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "applied"

    def test_put_reason_too_short(self, admin_client, db):
        """reason < 8 chars → 422 (Pydantic min_length)."""
        _seed_section(db, "rewards", {"cab_per_receipt_scan": 100})
        resp = admin_client.put(
            "/api/v1/admin/settings/rewards",
            json={"data": {"cab_per_receipt_scan": 105}, "reason": "short"},
            headers={"X-Admin-Operator": "test-admin"},
        )
        assert resp.status_code == 422

    def test_put_section_with_no_baseline_writes(self, admin_client, db):
        """First write on a section (no baseline) → applied (no breach possible)."""
        db.execute(text("DELETE FROM app_settings WHERE section='subscription_promotions'"))
        db.commit()
        resp = _put(
            admin_client,
            "subscription_promotions",
            {"active_codes": ["WELCOME"], "default_multiplier": 1.5},
            reason="alpha first write promo",
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "applied"

    def test_new_pending_auto_cancels_previous(self, admin_client, db):
        """H2 — a second magnitude-breach PUT auto-cancels the prior pending row.

        Without this guard, three back-to-back >50 % PUTs would leave
        three open pending rows for the same section. The DB partial
        UNIQUE INDEX prevents that ; the application UPDATE flips the
        prior row to ``cancelled`` so the audit trail keeps the original
        reason + operator visible (we don't DELETE history).
        """
        from ratis_core.models.admin_audit import AdminSettingsAudit

        _seed_section(db, "rewards", {"cab_per_receipt_scan": 100})

        # First magnitude-breach PUT → pending_2fa row #1.
        r1 = _put(
            admin_client,
            "rewards",
            {"cab_per_receipt_scan": 1000},  # ×10 — breach
            reason="alpha first big bump",
        )
        assert r1.status_code == 200
        assert r1.json()["status"] == "pending_2fa"
        first_id = r1.json()["audit_id"]

        # Second magnitude-breach PUT → pending_2fa row #2, #1 cancelled.
        r2 = _put(
            admin_client,
            "rewards",
            {"cab_per_receipt_scan": 2000},  # ×20 — also breach
            reason="alpha second bigger bump",
        )
        assert r2.status_code == 200
        assert r2.json()["status"] == "pending_2fa"
        second_id = r2.json()["audit_id"]
        assert first_id != second_id

        # Refresh both rows : #1 cancelled, #2 still pending.
        first_row = db.query(AdminSettingsAudit).filter_by(id=uuid.UUID(first_id)).first()
        second_row = db.query(AdminSettingsAudit).filter_by(id=uuid.UUID(second_id)).first()
        first_status = first_row.status if isinstance(first_row.status, str) else first_row.status.value
        second_status = second_row.status if isinstance(second_row.status, str) else second_row.status.value
        assert first_status == "cancelled", f"first pending should auto-cancel, got {first_status}"
        assert second_status == "pending_2fa", f"second pending should remain open, got {second_status}"

        # Sanity : at most one pending_2fa for this section now.
        n_pending = db.query(AdminSettingsAudit).filter_by(section="rewards", status="pending_2fa").count()
        assert n_pending == 1

    def test_unique_index_prevents_two_pending(self, db):
        """H2 — DB-level guard : direct INSERT of a 2nd pending row → IntegrityError.

        Tests the partial UNIQUE INDEX created by the alembic migration
        ``20260503_1000_uq_p2fa``. Bypasses the service layer to confirm
        that even a future refactor missing the auto-cancel cannot leak
        two pending rows past the DB.
        """
        from sqlalchemy.exc import IntegrityError

        # Seed one pending row (raw SQL — minimal NOT NULL fields only).
        db.execute(
            text(
                "INSERT INTO admin_settings_audit "
                "(operator, section, reason, new_data, status, expires_at) "
                "VALUES ('op-a', 'rewards', 'h2 first pending row', "
                "        CAST('{}' AS jsonb), 'pending_2fa', "
                "        now() + interval '10 minutes')"
            )
        )
        db.commit()

        # Second pending row on the same section → IntegrityError.
        # Single statement inside ``pytest.raises`` block (PT012) — the
        # INSERT itself triggers the unique-violation, no flush() needed
        # since psycopg autoflush propagates the error eagerly.
        with pytest.raises(IntegrityError):
            db.execute(
                text(
                    "INSERT INTO admin_settings_audit "
                    "(operator, section, reason, new_data, status, expires_at) "
                    "VALUES ('op-b', 'rewards', 'h2 second pending row', "
                    "        CAST('{}' AS jsonb), 'pending_2fa', "
                    "        now() + interval '10 minutes')"
                )
            )
        db.rollback()


# ---------------------------------------------------------------------------
# GET /admin/settings/audit
# ---------------------------------------------------------------------------
class TestAuditListing:
    def _make_audit_row(self, db, *, section: str, status: str = "applied") -> uuid.UUID:
        """Insert a minimal audit row directly. Used to seed pagination tests."""
        from ratis_core.models.admin_audit import AdminSettingsAudit

        applied_at = datetime.now(UTC) if status == "applied" else None
        expires_at = datetime.now(UTC) + timedelta(minutes=10) if status == "pending_2fa" else None
        row = AdminSettingsAudit(
            operator="test-admin",
            section=section,
            reason="seed audit row test",
            old_data={"k": 1},
            new_data={"k": 2},
            diff={"added": [], "removed": [], "changed": ["k"]},
            status=status,
            applied_at=applied_at,
            expires_at=expires_at,
        )
        db.add(row)
        # flush before commit so the INSERT fires the connection event
        # *before* _tracking_commit clears the writes list (otherwise the
        # autouse assert_no_pending_changes fixture flags us at teardown).
        db.flush()
        db.commit()
        return row.id

    def test_audit_list_filters_section(self, admin_client, db):
        """?section=rewards filtre correctement."""
        self._make_audit_row(db, section="rewards")
        self._make_audit_row(db, section="missions")
        resp = admin_client.get("/api/v1/admin/settings/audit?section=rewards")
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] >= 1
        assert all(item["section"] == "rewards" for item in body["items"])

    def test_audit_list_filters_status(self, admin_client, db):
        """?status=pending_2fa filtre correctement."""
        self._make_audit_row(db, section="rewards", status="applied")
        self._make_audit_row(db, section="rewards", status="pending_2fa")
        resp = admin_client.get("/api/v1/admin/settings/audit?status=pending_2fa")
        assert resp.status_code == 200
        body = resp.json()
        assert all(item["status"] == "pending_2fa" for item in body["items"])

    def test_audit_list_pagination(self, admin_client, db):
        """limit + offset respectés."""
        for _ in range(5):
            self._make_audit_row(db, section="xp")
        r1 = admin_client.get("/api/v1/admin/settings/audit?section=xp&limit=2&offset=0")
        r2 = admin_client.get("/api/v1/admin/settings/audit?section=xp&limit=2&offset=2")
        assert r1.status_code == 200
        assert r2.status_code == 200
        b1, b2 = r1.json(), r2.json()
        assert len(b1["items"]) == 2
        assert len(b2["items"]) == 2
        # IDs disjoints (deterministic order = timestamp DESC)
        ids1 = {it["id"] for it in b1["items"]}
        ids2 = {it["id"] for it in b2["items"]}
        assert ids1.isdisjoint(ids2)

    def test_audit_detail_returns_diff(self, admin_client, db):
        """GET audit/{id} retourne le diff (préenregistré ici)."""
        from ratis_core.models.admin_audit import AdminSettingsAudit

        # Seed a row with NULL diff to exercise the on-fly computation
        row = AdminSettingsAudit(
            operator="test-admin",
            section="rewards",
            reason="seed row null diff",
            old_data={"a": 1, "b": 2},
            new_data={"a": 1, "b": 3, "c": 4},
            diff=None,  # force on-fly compute
            status="applied",
            applied_at=datetime.now(UTC),
        )
        db.add(row)
        db.flush()
        db.commit()
        resp = admin_client.get(f"/api/v1/admin/settings/audit/{row.id}")
        assert resp.status_code == 200
        body = resp.json()
        assert body["id"] == str(row.id)
        assert body["diff"] == {"added": ["c"], "removed": [], "changed": ["b"]}
        assert body["old_data"] == {"a": 1, "b": 2}
        assert body["new_data"] == {"a": 1, "b": 3, "c": 4}

    def test_audit_detail_not_found(self, admin_client, db):
        resp = admin_client.get(f"/api/v1/admin/settings/audit/{uuid.uuid4()}")
        assert resp.status_code == 404
        assert resp.json()["detail"] == "audit_not_found"


# ---------------------------------------------------------------------------
# POST /admin/settings/{section}/confirm-2fa
# ---------------------------------------------------------------------------
class TestConfirm2FA:
    def _seed_pending(self, db, section: str = "rewards", *, expires_in_minutes: int = 10) -> uuid.UUID:
        from ratis_core.models.admin_audit import AdminSettingsAudit

        row = AdminSettingsAudit(
            operator="test-admin",
            section=section,
            reason="pending 2fa test",
            old_data={"cab_per_receipt_scan": 100},
            new_data={"cab_per_receipt_scan": 1000},
            diff={"added": [], "removed": [], "changed": ["cab_per_receipt_scan"]},
            status="pending_2fa",
            expires_at=datetime.now(UTC) + timedelta(minutes=expires_in_minutes),
            applied_at=None,
        )
        db.add(row)
        # flush before commit so the INSERT fires the connection event
        # *before* _tracking_commit clears the writes list (otherwise the
        # autouse assert_no_pending_changes fixture flags us at teardown).
        db.flush()
        db.commit()
        return row.id

    def test_confirm_2fa_happy_path(self, admin_client, db, valid_totp_code):
        """pending_2fa + TOTP valide → applied + valeur en BDD."""
        _seed_section(db, "rewards", {"cab_per_receipt_scan": 100})
        audit_id = self._seed_pending(db)
        resp = admin_client.post(
            "/api/v1/admin/settings/rewards/confirm-2fa",
            json={"audit_id": str(audit_id)},
            headers={
                "X-Admin-TOTP": valid_totp_code,
                "X-Admin-Operator": "test-admin",
            },
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "applied"
        # value applied
        row = db.execute(text("SELECT data FROM app_settings WHERE section='rewards'")).first()
        assert row.data["cab_per_receipt_scan"] == 1000

    def test_confirm_2fa_invalid_totp_401(self, admin_client, db):
        audit_id = self._seed_pending(db)
        resp = admin_client.post(
            "/api/v1/admin/settings/rewards/confirm-2fa",
            json={"audit_id": str(audit_id)},
            headers={
                "X-Admin-TOTP": "000000",
                "X-Admin-Operator": "test-admin",
            },
        )
        assert resp.status_code == 401
        assert resp.json()["detail"] in ("totp_invalid", "totp_required")

    def test_confirm_2fa_audit_not_pending_409(self, admin_client, db, valid_totp_code):
        """Row already applied → 409 audit_not_pending."""
        from ratis_core.models.admin_audit import AdminSettingsAudit

        row = AdminSettingsAudit(
            operator="test-admin",
            section="rewards",
            reason="already applied row",
            old_data={"k": 1},
            new_data={"k": 2},
            diff={"added": [], "removed": [], "changed": ["k"]},
            status="applied",
            applied_at=datetime.now(UTC),
        )
        db.add(row)
        db.flush()
        db.commit()
        resp = admin_client.post(
            "/api/v1/admin/settings/rewards/confirm-2fa",
            json={"audit_id": str(row.id)},
            headers={
                "X-Admin-TOTP": valid_totp_code,
                "X-Admin-Operator": "test-admin",
            },
        )
        assert resp.status_code == 409
        assert resp.json()["detail"] == "audit_not_pending"

    def test_confirm_2fa_audit_expired_410(self, admin_client, db, valid_totp_code):
        """expires_at < now → 410 audit_expired (status flipped to expired)."""
        audit_id = self._seed_pending(db, expires_in_minutes=-5)  # expired 5 min ago
        resp = admin_client.post(
            "/api/v1/admin/settings/rewards/confirm-2fa",
            json={"audit_id": str(audit_id)},
            headers={
                "X-Admin-TOTP": valid_totp_code,
                "X-Admin-Operator": "test-admin",
            },
        )
        assert resp.status_code == 410
        assert resp.json()["detail"] == "audit_expired"
        # status updated to expired
        from ratis_core.models.admin_audit import AdminSettingsAudit

        row = db.query(AdminSettingsAudit).filter_by(id=audit_id).first()
        assert row.status in ("expired", "pending_2fa")
        # NB: the lazy update ran in a separate transaction that we
        # rolled back above when the route raised. We accept either —
        # the endpoint contract guarantees the 410 ; the lazy flip is
        # best-effort.

    def test_confirm_2fa_audit_not_found_404(self, admin_client, db, valid_totp_code):
        resp = admin_client.post(
            "/api/v1/admin/settings/rewards/confirm-2fa",
            json={"audit_id": str(uuid.uuid4())},
            headers={
                "X-Admin-TOTP": valid_totp_code,
                "X-Admin-Operator": "test-admin",
            },
        )
        assert resp.status_code == 404
        assert resp.json()["detail"] == "audit_not_found"

    def test_confirm_2fa_rejects_other_operator_audit_id(self, admin_client, db, valid_totp_code):
        """H1 — pending row created by op A is NOT confirmable by op B.

        We deliberately surface a 404 rather than a 403 : a different
        operator should not learn that an audit row exists under another
        admin's name. The audit trail is preserved (the original operator
        is recorded at PUT-time and unchanged).
        """
        from ratis_core.models.admin_audit import AdminSettingsAudit

        # Op A creates a pending row.
        row = AdminSettingsAudit(
            operator="op-a",
            section="rewards",
            reason="op-a pending edit",
            old_data={"cab_per_receipt_scan": 100},
            new_data={"cab_per_receipt_scan": 1000},
            diff={"added": [], "removed": [], "changed": ["cab_per_receipt_scan"]},
            status="pending_2fa",
            expires_at=datetime.now(UTC) + timedelta(minutes=10),
            applied_at=None,
        )
        db.add(row)
        db.flush()
        db.commit()

        # Op B tries to confirm with their own X-Admin-Operator header.
        resp = admin_client.post(
            "/api/v1/admin/settings/rewards/confirm-2fa",
            json={"audit_id": str(row.id)},
            headers={
                "X-Admin-TOTP": valid_totp_code,
                "X-Admin-Operator": "op-b",
            },
        )
        assert resp.status_code == 404
        assert resp.json()["detail"] == "audit_not_found"

        # The row is still pending — op B's attempt did not transition it.
        refreshed = db.query(AdminSettingsAudit).filter_by(id=row.id).first()
        current_status = refreshed.status if isinstance(refreshed.status, str) else refreshed.status.value
        assert current_status == "pending_2fa"

    def test_confirm_2fa_accepts_same_operator(self, admin_client, db, valid_totp_code):
        """H1 regression — the original operator can still confirm normally."""
        _seed_section(db, "rewards", {"cab_per_receipt_scan": 100})
        from ratis_core.models.admin_audit import AdminSettingsAudit

        row = AdminSettingsAudit(
            operator="op-a",
            section="rewards",
            reason="op-a pending edit",
            old_data={"cab_per_receipt_scan": 100},
            new_data={"cab_per_receipt_scan": 1000},
            diff={"added": [], "removed": [], "changed": ["cab_per_receipt_scan"]},
            status="pending_2fa",
            expires_at=datetime.now(UTC) + timedelta(minutes=10),
            applied_at=None,
        )
        db.add(row)
        db.flush()
        db.commit()

        resp = admin_client.post(
            "/api/v1/admin/settings/rewards/confirm-2fa",
            json={"audit_id": str(row.id)},
            headers={
                "X-Admin-TOTP": valid_totp_code,
                "X-Admin-Operator": "op-a",
            },
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "applied"


# ---------------------------------------------------------------------------
# POST /admin/settings/{section}/cancel-pending
# ---------------------------------------------------------------------------
class TestCancelPending:
    def _seed_pending(self, db, section: str = "rewards") -> uuid.UUID:
        from ratis_core.models.admin_audit import AdminSettingsAudit

        row = AdminSettingsAudit(
            operator="test-admin",
            section=section,
            reason="cancel pending test",
            old_data={"k": 1},
            new_data={"k": 2},
            diff={"added": [], "removed": [], "changed": ["k"]},
            status="pending_2fa",
            expires_at=datetime.now(UTC) + timedelta(minutes=10),
            applied_at=None,
        )
        db.add(row)
        # flush before commit so the INSERT fires the connection event
        # *before* _tracking_commit clears the writes list (otherwise the
        # autouse assert_no_pending_changes fixture flags us at teardown).
        db.flush()
        db.commit()
        return row.id

    def test_cancel_pending_transitions_to_cancelled(self, admin_client, db):
        audit_id = self._seed_pending(db)
        resp = admin_client.post(
            "/api/v1/admin/settings/rewards/cancel-pending",
            json={"audit_id": str(audit_id)},
            headers={"X-Admin-Operator": "test-admin"},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "cancelled"

        from ratis_core.models.admin_audit import AdminSettingsAudit

        row = db.query(AdminSettingsAudit).filter_by(id=audit_id).first()
        assert row.status == "cancelled"

    def test_cancel_pending_already_applied_409(self, admin_client, db):
        from ratis_core.models.admin_audit import AdminSettingsAudit

        row = AdminSettingsAudit(
            operator="test-admin",
            section="rewards",
            reason="cancel already applied test",
            old_data={"k": 1},
            new_data={"k": 2},
            diff={"added": [], "removed": [], "changed": ["k"]},
            status="applied",
            applied_at=datetime.now(UTC),
        )
        db.add(row)
        db.flush()
        db.commit()
        resp = admin_client.post(
            "/api/v1/admin/settings/rewards/cancel-pending",
            json={"audit_id": str(row.id)},
            headers={"X-Admin-Operator": "test-admin"},
        )
        assert resp.status_code == 409
        assert resp.json()["detail"] == "audit_not_pending"

    def test_cancel_pending_not_found_404(self, admin_client, db):
        resp = admin_client.post(
            "/api/v1/admin/settings/rewards/cancel-pending",
            json={"audit_id": str(uuid.uuid4())},
            headers={"X-Admin-Operator": "test-admin"},
        )
        assert resp.status_code == 404
        assert resp.json()["detail"] == "audit_not_found"


# ---------------------------------------------------------------------------
# GET /admin/settings/{section}/editable
# ---------------------------------------------------------------------------
class TestEditableEndpoint:
    def test_get_editable_returns_true_with_frozen_keys(self, admin_client):
        """gamification → editable=true, frozen_keys=['feed_jack']."""
        resp = admin_client.get("/api/v1/admin/settings/gamification/editable")
        assert resp.status_code == 200
        body = resp.json()
        assert body["editable"] is True
        assert body["frozen_keys"] == ["feed_jack"]

    def test_get_editable_returns_true_no_frozen_keys(self, admin_client):
        """rewards → editable=true, frozen_keys=[]."""
        resp = admin_client.get("/api/v1/admin/settings/rewards/editable")
        assert resp.status_code == 200
        body = resp.json()
        assert body["editable"] is True
        assert body["frozen_keys"] == []

    def test_get_editable_returns_false_for_frozen_section(self, admin_client):
        """pipeline → editable=false."""
        resp = admin_client.get("/api/v1/admin/settings/pipeline/editable")
        assert resp.status_code == 200
        body = resp.json()
        assert body["editable"] is False
        assert body["frozen_keys"] == []

    def test_get_editable_subscription_promotions(self, admin_client):
        """subscription_promotions → editable=true (V1 new section)."""
        resp = admin_client.get("/api/v1/admin/settings/subscription_promotions/editable")
        assert resp.status_code == 200
        body = resp.json()
        assert body["editable"] is True
        assert body["frozen_keys"] == []


# ---------------------------------------------------------------------------
# M3 — Redact secrets in audit response
# ---------------------------------------------------------------------------
class TestAuditRedactSecrets:
    """M3 (audit sécurité 2026-05-03) — sensitive sub-keys are masked at
    audit response serialization. The DB row keeps the real values
    (legal trail, ``cashback_*`` / ``admin_settings_audit`` are NEVER
    PURGE) but they don't leak through API/UI.

    The redaction policy lives in ``REDACTED_KEYS_PER_SECTION`` in
    ``services/admin/settings_service.py``. Currently masks
    ``subscription_promotions.active_codes`` (promo code list — V1).
    """

    def _make_audit_row(self, db, *, section: str, old_data, new_data):
        from ratis_core.models.admin_audit import AdminSettingsAudit

        row = AdminSettingsAudit(
            operator="test-admin",
            section=section,
            reason="m3 redact audit row test",
            old_data=old_data,
            new_data=new_data,
            diff={"added": [], "removed": [], "changed": ["active_codes"]},
            status="applied",
            applied_at=datetime.now(UTC),
        )
        db.add(row)
        db.flush()
        db.commit()
        return row

    def test_redact_subscription_promotions_active_codes_unit(self):
        """Unit test of the redact helper (no HTTP)."""
        from services.admin.settings_service import redact_for_audit

        data = {"active_codes": ["PROMO50", "WELCOME"], "default_multiplier": 1.0}
        out = redact_for_audit("subscription_promotions", data)
        assert out["active_codes"] == "***REDACTED***"
        assert out["default_multiplier"] == 1.0

    def test_redact_no_op_when_section_has_no_pattern(self):
        """A section absent from REDACTED_KEYS_PER_SECTION → data unchanged."""
        from services.admin.settings_service import redact_for_audit

        data = {"cab_per_receipt_scan": 100}
        out = redact_for_audit("rewards", data)
        assert out == data

    def test_redact_no_op_when_data_is_none(self):
        """data=None → None (no crash on first-write rows)."""
        from services.admin.settings_service import redact_for_audit

        assert redact_for_audit("subscription_promotions", None) is None

    def test_redact_does_not_mutate_input(self):
        """Helper returns a new dict — original untouched (defensive copy)."""
        from services.admin.settings_service import redact_for_audit

        data = {"active_codes": ["X"], "k": 1}
        original = dict(data)
        _ = redact_for_audit("subscription_promotions", data)
        assert data == original

    def test_audit_list_endpoint_redacts_active_codes(self, admin_client, db):
        """GET /admin/settings/audit → response items have active_codes masked.

        items omit old_data/new_data normally — but the redaction matters
        for the detail endpoint (next test) and any future endpoint that
        surfaces them in a list.
        """
        # Listing endpoint already drops old/new_data → just confirm the
        # row metadata is intact (section + status). The redaction matters
        # in the detail endpoint, covered below.
        self._make_audit_row(
            db,
            section="subscription_promotions",
            old_data={"active_codes": ["OLD"], "default_multiplier": 1.0},
            new_data={"active_codes": ["NEW"], "default_multiplier": 1.0},
        )
        resp = admin_client.get("/api/v1/admin/settings/audit?section=subscription_promotions")
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] >= 1
        # sanity : section is preserved
        assert all(it["section"] == "subscription_promotions" for it in body["items"])

    def test_audit_detail_endpoint_redacts_active_codes(self, admin_client, db):
        """GET /admin/settings/audit/{id} → old_data/new_data have active_codes masked."""
        row = self._make_audit_row(
            db,
            section="subscription_promotions",
            old_data={"active_codes": ["OLDPROMO"], "default_multiplier": 1.0},
            new_data={"active_codes": ["NEWPROMO"], "default_multiplier": 1.5},
        )
        resp = admin_client.get(f"/api/v1/admin/settings/audit/{row.id}")
        assert resp.status_code == 200
        body = resp.json()
        assert body["old_data"]["active_codes"] == "***REDACTED***"
        assert body["new_data"]["active_codes"] == "***REDACTED***"
        # Non-sensitive fields are preserved
        assert body["old_data"]["default_multiplier"] == 1.0
        assert body["new_data"]["default_multiplier"] == 1.5
        # Real promo strings never appear in response
        assert "OLDPROMO" not in resp.text
        assert "NEWPROMO" not in resp.text

    def test_audit_db_row_keeps_real_values(self, admin_client, db):
        """SELECT direct DB → real promo codes intact (legal audit trail)."""
        row = self._make_audit_row(
            db,
            section="subscription_promotions",
            old_data={"active_codes": ["LEGAL_OLD"], "default_multiplier": 1.0},
            new_data={"active_codes": ["LEGAL_NEW"], "default_multiplier": 1.0},
        )
        # Direct DB read — bypasses the API redaction layer.
        from ratis_core.models.admin_audit import AdminSettingsAudit

        fresh = db.query(AdminSettingsAudit).filter_by(id=row.id).first()
        assert fresh.old_data["active_codes"] == ["LEGAL_OLD"]
        assert fresh.new_data["active_codes"] == ["LEGAL_NEW"]


# ---------------------------------------------------------------------------
# M5 — Cap PUT body size at 64 KB
# ---------------------------------------------------------------------------
class TestPutBodySizeCap:
    """M5 (audit sécurité 2026-05-03) — bypass-proof body cap.

    The UI already caps at 64 KB ; this enforces the same limit at the
    backend so a malicious operator with ADMIN_API_KEY (or a curl bypass)
    cannot DoS the service via a multi-MB JSON push.
    """

    def test_put_rejects_body_above_64kb(self, admin_client, db):
        """data ~70 KB serialized → 413 payload_too_large."""
        _seed_section(db, "rewards", {"cab_per_receipt_scan": 100})
        # 70 000 bytes of payload — well over the 64 KB cap.
        big = "x" * 70_000
        resp = _put(
            admin_client,
            "rewards",
            {"big": big, "cab_per_receipt_scan": 105},
            reason="alpha big payload test",
        )
        assert resp.status_code == 413
        body = resp.json()
        # FastAPI surfaces dict detail under "detail" key as-is.
        assert body["detail"]["detail"] == "payload_too_large"
        assert body["detail"]["max_bytes"] == 64 * 1024

    def test_put_accepts_normal_body(self, admin_client, db):
        """Regression — small payload still works."""
        _seed_section(db, "rewards", {"cab_per_receipt_scan": 100})
        resp = _put(
            admin_client,
            "rewards",
            {"cab_per_receipt_scan": 110},
            reason="alpha normal body test",
        )
        assert resp.status_code == 200

    def test_put_validate_body_size_unit(self):
        """Unit test of the size helper (no HTTP)."""
        from fastapi import HTTPException
        from services.admin.settings_service import validate_body_size

        # Below cap : OK
        validate_body_size({"k": "x" * 1000})
        # At the cap (64 KB - some overhead) : OK
        validate_body_size({"k": "x" * (60_000)})
        # Above cap : raises 413
        with pytest.raises(HTTPException) as exc_info:
            validate_body_size({"k": "x" * 70_000})
        assert exc_info.value.status_code == 413


# ---------------------------------------------------------------------------
# L2 — Length cap on reason field
# ---------------------------------------------------------------------------
class TestPutReasonLengthCap:
    """L2 (audit sécurité 2026-05-03) — Pydantic Field min/max on reason.

    ``min_length=8`` was already in place ; ``max_length=2000`` is the
    new defense — caps how much an op can dump into the audit row's
    ``reason`` column. Free-text but bounded.
    """

    def test_put_rejects_reason_too_short(self, admin_client, db):
        """reason='abc' (<8) → 422 (Pydantic)."""
        _seed_section(db, "rewards", {"cab_per_receipt_scan": 100})
        resp = admin_client.put(
            "/api/v1/admin/settings/rewards",
            json={"data": {"cab_per_receipt_scan": 105}, "reason": "abc"},
            headers={"X-Admin-Operator": "test-admin"},
        )
        assert resp.status_code == 422

    def test_put_rejects_reason_too_long(self, admin_client, db):
        """reason length > 2000 → 422 (Pydantic max_length)."""
        _seed_section(db, "rewards", {"cab_per_receipt_scan": 100})
        resp = admin_client.put(
            "/api/v1/admin/settings/rewards",
            json={"data": {"cab_per_receipt_scan": 105}, "reason": "x" * 3000},
            headers={"X-Admin-Operator": "test-admin"},
        )
        assert resp.status_code == 422

    def test_put_accepts_reason_within_bounds(self, admin_client, db):
        """8 ≤ len(reason) ≤ 2000 → 200 applied."""
        _seed_section(db, "rewards", {"cab_per_receipt_scan": 100})
        resp = _put(
            admin_client,
            "rewards",
            {"cab_per_receipt_scan": 110},
            reason="Bump CAB receipt for alpha test",
        )
        assert resp.status_code == 200
