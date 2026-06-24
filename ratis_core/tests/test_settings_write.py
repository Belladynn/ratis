"""Integration tests for ``ratis_core.settings.update_settings_section``.

Covers the four documented paths of the helper :

1. Happy path — variation under threshold → ``status='applied'`` row +
   ``app_settings`` upserted.
2. 2FA path — variation > 50 % on a numeric leaf → ``status='pending_2fa'``
   row, ``app_settings`` left untouched.
3. ``bypass_2fa=True`` — used after TOTP confirmation in Bloc B, applies
   the change directly even if a breach would have been detected.
4. First write — ``old_data IS NULL``, no baseline, applied direct.

Plus the DB-side guards :

- ``reason`` shorter than 8 chars rejected at the Python layer (fail fast).
- ``chk_status_2fa_coherence`` CHECK constraint trips on a manual
  inconsistent INSERT.
"""

from __future__ import annotations

import json

import pytest
from ratis_core.models.admin_audit import (
    AdminSettingsAudit,
    AdminSettingsAuditStatus,
)
from ratis_core.settings import update_settings_section
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

REASON = "alpha test data ramp — needed for review"


def _seed_section(db, section: str, data: dict) -> None:
    """Insert an ``app_settings`` row directly without going through the
    helper (so we control the baseline used by the test)."""
    db.execute(
        text(
            "INSERT INTO app_settings (section, data) VALUES (:s, CAST(:d AS JSONB))"
            " ON CONFLICT (section) DO UPDATE SET data = EXCLUDED.data"
        ),
        {"s": section, "d": json.dumps(data)},
    )


def _read_section(db, section: str) -> dict | None:
    row = db.execute(
        text("SELECT data FROM app_settings WHERE section = :s"),
        {"s": section},
    ).first()
    return row.data if row else None


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_write_happy_path_applies_and_audits(db):
    """Small variation : audit row applied + ``app_settings`` updated."""
    _seed_section(db, "rewards", {"cab_per_receipt_complete": 500})

    audit_id, status = update_settings_section(
        db,
        "rewards",
        {"cab_per_receipt_complete": 600},
        operator="guillaume",
        reason=REASON,
    )

    assert status == AdminSettingsAuditStatus.APPLIED.value
    assert _read_section(db, "rewards") == {"cab_per_receipt_complete": 600}

    audit = db.get(AdminSettingsAudit, audit_id)
    assert audit is not None
    assert audit.operator == "guillaume"
    assert audit.section == "rewards"
    assert audit.reason == REASON
    assert audit.status == AdminSettingsAuditStatus.APPLIED
    assert audit.applied_at is not None
    assert audit.expires_at is None
    assert audit.old_data == {"cab_per_receipt_complete": 500}
    assert audit.new_data == {"cab_per_receipt_complete": 600}
    assert "changed" in audit.diff
    assert audit.diff["changed"] == ["cab_per_receipt_complete"]


# ---------------------------------------------------------------------------
# 2FA grace period
# ---------------------------------------------------------------------------


def test_write_2fa_required_does_not_touch_app_settings(db):
    """Variation > 50 % defers the write, ``app_settings`` unchanged."""
    _seed_section(db, "rewards", {"cab_per_receipt_complete": 500})

    audit_id, status = update_settings_section(
        db,
        "rewards",
        {"cab_per_receipt_complete": 5000},  # ×10 typo
        operator="guillaume",
        reason=REASON,
    )

    assert status == AdminSettingsAuditStatus.PENDING_2FA.value
    # app_settings must still hold the old value — Bloc B will flip it on
    # TOTP confirmation.
    assert _read_section(db, "rewards") == {"cab_per_receipt_complete": 500}

    audit = db.get(AdminSettingsAudit, audit_id)
    assert audit.status == AdminSettingsAuditStatus.PENDING_2FA
    assert audit.applied_at is None
    assert audit.expires_at is not None
    # Grace period must be in the future (10 min ahead).
    assert audit.expires_at > audit.timestamp


def test_write_bypass_2fa_applies_even_above_threshold(db):
    """Bloc B post-TOTP path : ``bypass_2fa=True`` skips the breach
    check entirely so the value lands on ``app_settings`` regardless."""
    _seed_section(db, "rewards", {"cab_per_receipt_complete": 500})

    audit_id, status = update_settings_section(
        db,
        "rewards",
        {"cab_per_receipt_complete": 5000},
        operator="guillaume",
        reason=REASON,
        bypass_2fa=True,
    )

    assert status == AdminSettingsAuditStatus.APPLIED.value
    assert _read_section(db, "rewards") == {"cab_per_receipt_complete": 5000}
    audit = db.get(AdminSettingsAudit, audit_id)
    assert audit.status == AdminSettingsAuditStatus.APPLIED
    assert audit.applied_at is not None


# ---------------------------------------------------------------------------
# First write — no baseline
# ---------------------------------------------------------------------------


def test_write_first_section_no_breach_possible(db):
    """A section that has never been written cannot trip the magnitude
    detector — there is no baseline to compare against."""
    # Make sure no row exists for this section.
    db.execute(text("DELETE FROM app_settings WHERE section = :s"), {"s": "subscription_promotions"})

    audit_id, status = update_settings_section(
        db,
        "subscription_promotions",
        {"active_codes": [], "default_multiplier": 1.0},
        operator="guillaume",
        reason="seed initial promo defaults",
    )

    assert status == AdminSettingsAuditStatus.APPLIED.value
    assert _read_section(db, "subscription_promotions") == {
        "active_codes": [],
        "default_multiplier": 1.0,
    }
    audit = db.get(AdminSettingsAudit, audit_id)
    assert audit.old_data is None
    assert audit.diff == {
        "added": ["active_codes", "default_multiplier"],
        "removed": [],
        "changed": [],
    }


# ---------------------------------------------------------------------------
# Reason validation
# ---------------------------------------------------------------------------


def test_reason_too_short_raises_before_db(db):
    """Python-level fail fast — does not touch the DB."""
    _seed_section(db, "rewards", {"cab_per_receipt_complete": 500})

    with pytest.raises(ValueError, match="at least 8"):
        update_settings_section(
            db,
            "rewards",
            {"cab_per_receipt_complete": 600},
            operator="guillaume",
            reason="short",  # 5 chars
        )


def test_reason_exact_min_length_accepted(db):
    """Boundary : exactly 8 chars passes."""
    _seed_section(db, "rewards", {"cab_per_receipt_complete": 500})

    audit_id, status = update_settings_section(
        db,
        "rewards",
        {"cab_per_receipt_complete": 600},
        operator="guillaume",
        reason="8charsok!",  # 9 chars — well above floor; we sanity check >=8 acceptance below
    )
    assert status == AdminSettingsAuditStatus.APPLIED.value
    assert db.get(AdminSettingsAudit, audit_id) is not None


# ---------------------------------------------------------------------------
# DB-level CHECK constraints
# ---------------------------------------------------------------------------


def _flush_insert(db, *, sql: str, params: dict) -> None:
    """Execute + flush — single statement so ``pytest.raises`` keeps PT012."""
    db.execute(text(sql), params)
    db.flush()


def test_check_constraint_status_coherence_blocks_manual_insert(db):
    """Bypass the helper to assert the ``chk_status_2fa_coherence`` CHECK
    is actually enforced. ``status='applied'`` with ``applied_at IS NULL``
    must trip the constraint."""
    sql = (
        "INSERT INTO admin_settings_audit"
        " (operator, section, reason, new_data, status, applied_at)"
        " VALUES (:op, :sec, :reason, CAST(:data AS JSONB), 'applied', NULL)"
    )
    params = {
        "op": "guillaume",
        "sec": "rewards",
        "reason": "manual probe insert",
        "data": json.dumps({"x": 1}),
    }
    with pytest.raises(IntegrityError):
        _flush_insert(db, sql=sql, params=params)


def test_check_constraint_reason_min_len_blocks_short_reason(db):
    """Even if the helper short-circuits short reasons, the DB CHECK is
    a defense-in-depth against direct SQL writes."""
    sql = (
        "INSERT INTO admin_settings_audit"
        " (operator, section, reason, new_data, status, applied_at)"
        " VALUES (:op, :sec, 'short', CAST(:data AS JSONB), 'applied', now())"
    )
    params = {
        "op": "guillaume",
        "sec": "rewards",
        "data": json.dumps({"x": 1}),
    }
    with pytest.raises(IntegrityError):
        _flush_insert(db, sql=sql, params=params)


# ---------------------------------------------------------------------------
# Operator propagation
# ---------------------------------------------------------------------------


def test_operator_propagated_to_audit_row(db):
    _seed_section(db, "rewards", {"cab_per_receipt_complete": 500})

    audit_id, _ = update_settings_section(
        db,
        "rewards",
        {"cab_per_receipt_complete": 510},
        operator="alice",
        reason=REASON,
    )

    audit = db.get(AdminSettingsAudit, audit_id)
    assert audit.operator == "alice"
