"""HSP3 — tests des endpoints admin db-pipeline (build-summary,
compute-flags, apply-graduation).

Auth = INTERNAL_API_KEY. Les tests fournissent le header standard.
"""

from __future__ import annotations

import os

import pytest


@pytest.fixture
def internal_headers():
    key = os.environ.get("INTERNAL_API_KEY", "test-internal")
    return {"Authorization": f"Bearer {key}"}


def test_build_summary_credit_cab_returns_summary(client, internal_headers):
    body = {
        "procedure": "support_credit_cab",
        "manifest": {
            "name": "support_credit_cab",
            "purpose": "Support crédit CAB.",
            "facing": True,
            "direction": "credit",
            "money_tier": "cab",
            "args": [],
            "affects": [{"table": "user_cab_balance", "op": "update", "rows": 1}],
        },
        "args": {"user_id": "00000000-0000-0000-0000-000000004728", "amount_cents": 10000},
    }
    r = client.post(
        "/api/v1/admin/db-pipeline/build-summary",
        json=body,
        headers=internal_headers,
    )
    assert r.status_code == 200
    data = r.json()
    assert "CRÉDITER" in data["summary_fr"]
    assert "10 000 CAB" in data["summary_fr"]  # raw CAB units (pas ÷100)
    assert data["summary_error"] is None


def test_build_summary_multi_entity_returns_error(client, internal_headers):
    body = {
        "procedure": "x",
        "manifest": {
            "name": "x",
            "purpose": "P",
            "facing": True,
            "direction": "credit",
            "money_tier": "cab",
            "args": [],
            "affects": [
                {"table": "users", "op": "update", "rows": 1},
                {"table": "stores", "op": "update", "rows": 1},
            ],
        },
        "args": {"amount_cents": 100},
    }
    r = client.post(
        "/api/v1/admin/db-pipeline/build-summary",
        json=body,
        headers=internal_headers,
    )
    assert r.status_code == 200
    data = r.json()
    assert data["summary_fr"] is None
    assert data["summary_error"]


def test_build_summary_without_internal_key_401(client):
    r = client.post(
        "/api/v1/admin/db-pipeline/build-summary",
        json={"procedure": "x", "manifest": {}, "args": {}},
    )
    assert r.status_code in (401, 403)


def test_compute_flags_returns_6_keys(client, internal_headers):
    import uuid

    body = {
        "procedure": "z",
        "money_tier": "cab",
        "user_id": str(uuid.uuid4()),
        "current_amount_cents": 100,
    }
    r = client.post(
        "/api/v1/admin/db-pipeline/compute-flags",
        json=body,
        headers=internal_headers,
    )
    assert r.status_code == 200
    flags = r.json()["anomaly_flags"]
    assert set(flags.keys()) == {
        "first_use_of_procedure",
        "amount_above_p95",
        "user_repeat_in_24h",
        "approaching_daily_cap",
        "proposed_outside_business_hours",
        "caps_already_warning",
    }
    # first_use_of_procedure doit être True (DB vide pour proc='z').
    assert flags["first_use_of_procedure"] is True


def test_compute_flags_without_internal_key_401(client):
    r = client.post(
        "/api/v1/admin/db-pipeline/compute-flags",
        json={"procedure": "x", "money_tier": "cab", "user_id": "u", "current_amount_cents": 1},
    )
    assert r.status_code in (401, 403)


# ---------------------------------------------------------------------------
# apply-graduation tests (M5)
# ---------------------------------------------------------------------------


def _seed_trust_levels(db, levels: dict):
    import json as _json

    from sqlalchemy import text

    db.execute(
        text(
            "INSERT INTO app_settings (section, data) "
            "VALUES ('db_pipeline_trust_levels', CAST(:d AS jsonb)) "
            "ON CONFLICT (section) DO UPDATE SET data = EXCLUDED.data"
        ),
        {"d": _json.dumps(levels)},
    )
    db.commit()


def test_apply_graduation_updates_trust_level(client, db, internal_headers):
    _seed_trust_levels(db, {"support_credit_cab": "manual"})
    r = client.post(
        "/api/v1/admin/db-pipeline/apply-graduation",
        json={
            "procedure": "support_credit_cab",
            "new_trust_level": "caps_only",
            "money_tier": "cab",
        },
        headers=internal_headers,
    )
    assert r.status_code == 200
    # Re-read le seed.
    from sqlalchemy import text

    data = db.execute(text("SELECT data FROM app_settings WHERE section='db_pipeline_trust_levels'")).scalar_one()
    assert data["support_credit_cab"] == "caps_only"


def test_apply_graduation_refuses_caps_only_for_direct_tier(client, db, internal_headers):
    """Hardcoded : tier=direct + new_level!=manual → 422 refused_for_direct_tier.

    cf design §M5 « Tier direct (argent réel) ne peut jamais graduer »
    """
    _seed_trust_levels(db, {"support_real_credit": "manual"})
    r = client.post(
        "/api/v1/admin/db-pipeline/apply-graduation",
        json={
            "procedure": "support_real_credit",
            "new_trust_level": "caps_only",
            "money_tier": "direct",
        },
        headers=internal_headers,
    )
    assert r.status_code == 422
    assert "refused_for_direct_tier" in r.text


def test_apply_graduation_allows_frozen_even_for_direct(client, db, internal_headers):
    """`frozen` est toujours autorisé (sentinelle d'urgence, ne fait que
    parquer la procédure). Direct accepté pour frozen."""
    _seed_trust_levels(db, {"support_real_credit": "manual"})
    r = client.post(
        "/api/v1/admin/db-pipeline/apply-graduation",
        json={
            "procedure": "support_real_credit",
            "new_trust_level": "frozen",
            "money_tier": "direct",
        },
        headers=internal_headers,
    )
    assert r.status_code == 200
    from sqlalchemy import text

    data = db.execute(text("SELECT data FROM app_settings WHERE section='db_pipeline_trust_levels'")).scalar_one()
    assert data["support_real_credit"] == "frozen"


def test_apply_graduation_rejects_unknown_level(client, db, internal_headers):
    _seed_trust_levels(db, {"x": "manual"})
    r = client.post(
        "/api/v1/admin/db-pipeline/apply-graduation",
        json={"procedure": "x", "new_trust_level": "auto", "money_tier": "cab"},
        headers=internal_headers,
    )
    assert r.status_code == 422


# ─── HSP4 M3 — POST /admin/db-pipeline/validate-args ──────────────────────


def test_validate_args_ok(client, internal_headers):
    body = {
        "manifest": {
            "name": "support_credit_cab",
            "purpose": "test",
            "facing": True,
            "direction": "credit",
            "money_tier": "cab",
            "args": [
                {"name": "p_user_id", "type": "uuid", "required": True},
                {"name": "p_amount", "type": "integer", "required": True, "min": 1, "max": 10000},
            ],
            "affects": [],
            "trust_level_initial": "manual",
            "allowed_callers": ["claude-code-main"],
        },
        "args": {"p_user_id": "00000000-0000-4000-8000-000000000000", "p_amount": 100},
    }
    resp = client.post(
        "/api/v1/admin/db-pipeline/validate-args",
        json=body,
        headers=internal_headers,
    )
    assert resp.status_code == 200
    assert resp.json() == {"ok": True, "detail": None}


def test_validate_args_ko_amount_below_min(client, internal_headers):
    body = {
        "manifest": {
            "name": "support_credit_cab",
            "purpose": "test",
            "facing": True,
            "direction": "credit",
            "money_tier": "cab",
            "args": [
                {"name": "p_user_id", "type": "uuid", "required": True},
                {"name": "p_amount", "type": "integer", "required": True, "min": 1, "max": 10000},
            ],
            "affects": [],
            "trust_level_initial": "manual",
            "allowed_callers": ["claude-code-main"],
        },
        "args": {"p_user_id": "00000000-0000-4000-8000-000000000000", "p_amount": -1},
    }
    resp = client.post(
        "/api/v1/admin/db-pipeline/validate-args",
        json=body,
        headers=internal_headers,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is False
    assert "p_amount" in data["detail"]


def test_validate_args_requires_internal_key(client):
    resp = client.post(
        "/api/v1/admin/db-pipeline/validate-args",
        json={"manifest": {}, "args": {}},
    )
    assert resp.status_code in (401, 403)


# ─── HSP4 M5 — POST /admin/db-pipeline/check-rowcount ─────────────────────


def test_check_rowcount_ok(client, internal_headers, db):
    """Le pipeline post-CALL appelle cet endpoint avec submission_id +
    manifest pour décider COMMIT/ROLLBACK.
    """
    import uuid as _uuid

    from sqlalchemy import text

    sid = _uuid.uuid4()
    # Seed db_change_log avec submission_id connu (1 update user_cab_balance + 1 insert cabecoin_tx).
    db.execute(
        text(
            "INSERT INTO db_change_log (submission_id, table_name, op, new_data) "
            "VALUES (CAST(:s AS uuid), 'user_cab_balance', 'update', '{}'::jsonb), "
            "       (CAST(:s AS uuid), 'cabecoin_transactions', 'insert', '{}'::jsonb)"
        ),
        {"s": str(sid)},
    )
    db.commit()

    body = {
        "submission_id": str(sid),
        "manifest": {
            "name": "support_credit_cab",
            "purpose": "test",
            "facing": True,
            "direction": "credit",
            "money_tier": "cab",
            "args": [],
            "affects": [
                {"table": "user_cab_balance", "op": "update", "rows": 1},
                {"table": "cabecoin_transactions", "op": "insert", "rows": 1},
            ],
            "trust_level_initial": "manual",
            "allowed_callers": ["claude-code-main"],
        },
    }
    resp = client.post(
        "/api/v1/admin/db-pipeline/check-rowcount",
        json=body,
        headers=internal_headers,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["mismatches"] == []


def test_check_rowcount_ko_mismatch(client, internal_headers, db):
    import uuid as _uuid

    from sqlalchemy import text

    sid = _uuid.uuid4()
    db.execute(
        text(
            "INSERT INTO db_change_log (submission_id, table_name, op, new_data) "
            "VALUES (CAST(:s AS uuid), 'user_cab_balance', 'update', '{}'::jsonb), "
            "       (CAST(:s AS uuid), 'user_cab_balance', 'update', '{}'::jsonb)"  # 2× au lieu de 1
        ),
        {"s": str(sid)},
    )
    db.commit()

    body = {
        "submission_id": str(sid),
        "manifest": {
            "name": "support_credit_cab",
            "purpose": "test",
            "facing": True,
            "direction": "credit",
            "money_tier": "cab",
            "args": [],
            "affects": [
                {"table": "user_cab_balance", "op": "update", "rows": 1},
            ],
            "trust_level_initial": "manual",
            "allowed_callers": ["claude-code-main"],
        },
    }
    resp = client.post(
        "/api/v1/admin/db-pipeline/check-rowcount",
        json=body,
        headers=internal_headers,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is False
    assert any("user_cab_balance" in m for m in data["mismatches"])


def test_check_rowcount_requires_internal_key(client):
    resp = client.post(
        "/api/v1/admin/db-pipeline/check-rowcount",
        json={"submission_id": "x", "manifest": {}},
    )
    assert resp.status_code in (401, 403)


# ─── HSP3.1 — POST /admin/db-pipeline/get-trust-level ─────────────────────


def test_get_trust_level_returns_override_when_present(client, db, internal_headers):
    """Override BDD présent → renvoie l'override + source='override'."""
    _seed_trust_levels(db, {"support_credit_cab": "caps_only"})
    r = client.post(
        "/api/v1/admin/db-pipeline/get-trust-level",
        json={
            "procedure": "support_credit_cab",
            "manifest_trust_level_initial": "manual",
        },
        headers=internal_headers,
    )
    assert r.status_code == 200
    data = r.json()
    assert data["effective_trust_level"] == "caps_only"
    assert data["source"] == "override"


def test_get_trust_level_falls_back_to_manifest_when_no_override(client, db, internal_headers):
    """Pas d'override pour la procédure → renvoie le manifest + source='manifest'."""
    _seed_trust_levels(db, {"support_debit_cab": "caps_only"})
    r = client.post(
        "/api/v1/admin/db-pipeline/get-trust-level",
        json={
            "procedure": "support_credit_cab",
            "manifest_trust_level_initial": "frozen",
        },
        headers=internal_headers,
    )
    assert r.status_code == 200
    data = r.json()
    assert data["effective_trust_level"] == "frozen"
    assert data["source"] == "manifest"


def test_get_trust_level_rejects_invalid_effective_level(client, db, internal_headers):
    """Niveau effectif hors {manual,caps_only,frozen} → 422.

    Pas d'override pour la procédure → on retombe sur le manifest, ici
    invalide ('auto') → doit lever 422.
    """
    _seed_trust_levels(db, {"other_proc": "manual"})
    r = client.post(
        "/api/v1/admin/db-pipeline/get-trust-level",
        json={"procedure": "x", "manifest_trust_level_initial": "auto"},
        headers=internal_headers,
    )
    assert r.status_code == 422


def test_get_trust_level_requires_internal_key(client):
    r = client.post(
        "/api/v1/admin/db-pipeline/get-trust-level",
        json={"procedure": "x", "manifest_trust_level_initial": "manual"},
    )
    assert r.status_code in (401, 403)
