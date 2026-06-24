"""
Global Ratis settings loader.

DB-first : lit app_settings (section TEXT PK, data JSONB) si DATABASE_URL est défini
et la table est peuplée. Tombe sur ratis_settings.json sinon (import-time, tests, fallback).

Callers cache at module level:
    from ratis_core.settings import load_settings
    _CFG = load_settings()["fuzzy"]

Mutating helper :
    from ratis_core.settings import update_settings_section
    audit_id, status = update_settings_section(db, "rewards", new_data, operator="guillaume", reason="…")
    db.commit()  # caller-managed transaction (ratis pattern)
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from datetime import UTC, datetime, timedelta
from importlib.resources import files as _pkg_files
from typing import TYPE_CHECKING, Any

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

_SETTINGS_REF = _pkg_files("ratis_core") / "config" / "ratis_settings.json"

#: How long an admin has to confirm a high-magnitude change with TOTP
#: before the pending-2FA row is swept by the nightly batch (ARCH § Garde-fous).
PENDING_2FA_GRACE_PERIOD = timedelta(minutes=10)

#: Minimum length enforced both at API level and via the
#: ``chk_reason_min_len`` CHECK constraint on ``admin_settings_audit``.
MIN_REASON_LENGTH = 8


def load_settings() -> dict[str, Any]:
    """
    Load settings — DB first, JSON fallback.

    Returns the full settings dict. Raises FileNotFoundError only when both
    the DB and the JSON file are unavailable (last-resort failure at startup).
    """
    db_result = _try_load_from_db()
    if db_result is not None:
        logger.info("load_settings: loaded from app_settings DB table")
        return db_result
    logger.info("load_settings: DB unavailable or empty — falling back to ratis_settings.json")
    return _load_from_json()


def _try_load_from_db() -> dict[str, Any] | None:
    """
    Attempt to load settings from app_settings table.

    Uses a NullPool connection (no persistent pool) so it's safe to call at
    module import time without interfering with the application connection pool.
    Returns None on any error or if the table is empty.
    """
    url = os.environ.get("DATABASE_URL")
    if not url:
        return None
    try:
        from sqlalchemy import NullPool, create_engine, text

        engine = create_engine(url, poolclass=NullPool)
        with engine.connect() as conn:
            rows = conn.execute(text("SELECT section, data FROM app_settings ORDER BY section")).fetchall()
        if not rows:
            return None
        return {row.section: row.data for row in rows}
    except Exception:
        # Table doesn't exist yet (before migration), DB unreachable, etc.
        # Silent fallback — JSON is always the authoritative default.
        logger.debug("load_settings: DB unavailable, falling back to JSON", exc_info=True)
        return None


def _load_from_json() -> dict[str, Any]:
    """Load settings from the bundled ratis_settings.json."""
    if not _SETTINGS_REF.is_file():
        raise FileNotFoundError(
            f"ratis_settings.json not found in ratis_core package ({_SETTINGS_REF}). "
            "Check that the config directory is included in the package."
        )
    with _SETTINGS_REF.open(encoding="utf-8") as fh:
        return json.load(fh)


# ---------------------------------------------------------------------------
# Mutating helper — admin settings UI
# ---------------------------------------------------------------------------


def _compute_diff(old_data: dict[str, Any] | None, new_data: dict[str, Any]) -> dict[str, list[str]]:
    """Pre-compute a flat shallow diff for the audit row.

    V1 keeps the diff simple : top-level keys added / removed / changed.
    Deeper diffs land V2 (PG trigger or background job per ARCH § Hors
    scope V1). The diff is informational only — the magnitude check
    operates on the raw payloads.
    """
    if old_data is None:
        return {"added": sorted(new_data.keys()), "removed": [], "changed": []}
    added = sorted(k for k in new_data if k not in old_data)
    removed = sorted(k for k in old_data if k not in new_data)
    changed = sorted(k for k in new_data if k in old_data and new_data[k] != old_data[k])
    return {"added": added, "removed": removed, "changed": changed}


def update_settings_section(
    db: "Session",
    section: str,
    new_data: dict[str, Any],
    *,
    operator: str,
    reason: str,
    bypass_2fa: bool = False,
) -> tuple[uuid.UUID, str]:
    """Persist an admin-driven mutation on a section of ``app_settings``.

    Atomically writes one row in ``admin_settings_audit`` and (when no
    breach is detected, or ``bypass_2fa`` is set) upserts the matching
    row in ``app_settings``. Both writes share the caller's transaction —
    no implicit ``commit()`` here, in line with the ratis layering rule
    (R02 : routes commit, services don't).

    The 2FA grace period only kicks in when :func:`detect_magnitude_breach`
    returns a breach **and** ``bypass_2fa`` is ``False``. In that case
    ``app_settings`` is left untouched ; the caller (Bloc B endpoints)
    surfaces the audit id to the operator who must confirm via TOTP
    within :data:`PENDING_2FA_GRACE_PERIOD`.

    :param db: SQLAlchemy session — caller manages ``commit()``.
    :param section: ``app_settings.section`` PK (e.g. ``"rewards"``).
    :param new_data: candidate payload (already JSON-validated).
    :param operator: handle of the logged-in admin (audit trail).
    :param reason: business motivation, ``len() >= 8``. Validated both
        in Python (early ``ValueError``) and via the DB CHECK constraint.
    :param bypass_2fa: set to ``True`` after the TOTP layer has validated
        the change in Bloc B — short-circuits the breach detection.
    :return: ``(audit_id, status)`` where ``status`` is ``"applied"`` or
        ``"pending_2fa"``.
    :raises ValueError: when ``reason`` is too short (fail fast before
        hitting the DB CHECK).
    """
    from sqlalchemy import text

    from ratis_core.models.admin_audit import (
        AdminSettingsAudit,
        AdminSettingsAuditStatus,
    )
    from ratis_core.services.settings_2fa import detect_magnitude_breach

    if reason is None or len(reason) < MIN_REASON_LENGTH:
        raise ValueError(f"reason must be at least {MIN_REASON_LENGTH} characters (got {len(reason) if reason else 0})")

    # Read the current section snapshot — None if the section has never
    # been written. ``FOR UPDATE`` is overkill here (admin = human-paced)
    # but keeps the read+write pair coherent if the caller wraps another
    # writer concurrently. We rely on the caller's transaction.
    row = db.execute(
        text("SELECT data FROM app_settings WHERE section = :section FOR UPDATE"),
        {"section": section},
    ).first()
    old_data: dict[str, Any] | None = row.data if row is not None else None

    breach = False
    if not bypass_2fa:
        breach, _breach_key = detect_magnitude_breach(old_data, new_data)

    diff = _compute_diff(old_data, new_data)
    now = datetime.now(UTC)

    if breach:
        # H2 — auto-cancel any pre-existing pending_2fa row for the same
        # section before opening a new one. The partial UNIQUE INDEX
        # ``uq_admin_settings_audit_one_pending_per_section`` enforces the
        # invariant at the DB level ; this UPDATE is the application-side
        # transition that respects the audit trail (the old row keeps its
        # operator + reason + timestamps but its status flips to
        # ``cancelled`` so the diff history is preserved).
        #
        # Bound by the same caller transaction — the UPDATE + INSERT are
        # atomic from the operator's perspective.
        db.execute(
            text(
                "UPDATE admin_settings_audit"
                " SET status = 'cancelled'"
                " WHERE section = :section AND status = 'pending_2fa'"
            ),
            {"section": section},
        )
        audit = AdminSettingsAudit(
            operator=operator,
            section=section,
            reason=reason,
            old_data=old_data,
            new_data=new_data,
            diff=diff,
            status=AdminSettingsAuditStatus.PENDING_2FA,
            expires_at=now + PENDING_2FA_GRACE_PERIOD,
            applied_at=None,
        )
        db.add(audit)
        db.flush()  # populate audit.id without committing
        return (audit.id, AdminSettingsAuditStatus.PENDING_2FA.value)

    # Happy path : write through to app_settings AND record the audit row
    # in the same transaction. UPSERT mirrors the existing seed_settings()
    # contract (ON CONFLICT DO UPDATE) so first-write and update share
    # the same code path.
    db.execute(
        text(
            "INSERT INTO app_settings (section, data, updated_at)"
            " VALUES (:section, CAST(:data AS JSONB), now())"
            " ON CONFLICT (section) DO UPDATE"
            " SET data = EXCLUDED.data, updated_at = now()"
        ),
        {"section": section, "data": json.dumps(new_data)},
    )
    audit = AdminSettingsAudit(
        operator=operator,
        section=section,
        reason=reason,
        old_data=old_data,
        new_data=new_data,
        diff=diff,
        status=AdminSettingsAuditStatus.APPLIED,
        expires_at=None,
        applied_at=now,
    )
    db.add(audit)
    db.flush()
    return (audit.id, AdminSettingsAuditStatus.APPLIED.value)
