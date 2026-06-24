"""Schema-shape tests for ``AdminSettingsAudit``.

Guards against the model/migration drift discovered post-V1 : the
SQLAlchemy ``Mapped[datetime]`` shorthand produces ``TIMESTAMP WITHOUT
TIME ZONE`` by default, while the Alembic migration explicitly creates
``TIMESTAMPTZ``. The drift forced ``confirm-2fa`` / ``cancel-pending``
routes to normalize aware/naive comparisons at runtime — these tests
lock the model to ``TIMESTAMP(timezone=True)`` so a future refactor
cannot silently regress.

See ``KNOWN_PROBLEMS.md`` § KP-44.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from ratis_core.models.admin_audit import (
    AdminSettingsAudit,
    AdminSettingsAuditStatus,
)
from sqlalchemy import inspect, text


def test_admin_audit_timestamp_columns_are_tzaware(db):
    """All three datetime columns must be TIMESTAMPTZ in the live schema.

    Reads the catalog via ``information_schema.columns`` — the source of
    truth for what PostgreSQL actually created (model + migration drift
    invisible at the SQLAlchemy layer).
    """
    rows = db.execute(
        text(
            "SELECT column_name, data_type"
            " FROM information_schema.columns"
            " WHERE table_name = 'admin_settings_audit'"
            " AND column_name IN ('timestamp', 'expires_at', 'applied_at')"
            " ORDER BY column_name"
        )
    ).fetchall()

    by_name = {r.column_name: r.data_type for r in rows}
    assert by_name == {
        "applied_at": "timestamp with time zone",
        "expires_at": "timestamp with time zone",
        "timestamp": "timestamp with time zone",
    }, f"drift detected: {by_name}"


def test_admin_audit_inserted_row_returns_aware_datetimes(db):
    """Round-trip an audit row and assert tzinfo is non-None on read.

    Documents the runtime contract Bloc B relies on : after insert via
    the ORM, ``timestamp``, ``expires_at`` and ``applied_at`` come back
    aware. Kept distinct from the catalog test so a future driver-level
    regression (psycopg returning naive despite TIMESTAMPTZ) is caught.
    """
    now = datetime.now(UTC)
    row = AdminSettingsAudit(
        id=uuid.uuid4(),
        timestamp=now,
        operator="tests",
        section="rewards",
        reason="round-trip aware datetime smoke test",
        old_data=None,
        new_data={"k": 1},
        diff={"added": ["k"], "removed": [], "changed": []},
        status=AdminSettingsAuditStatus.PENDING_2FA.value,
        expires_at=now + timedelta(minutes=10),
        applied_at=None,
    )
    db.add(row)
    db.flush()

    fetched = db.query(AdminSettingsAudit).filter(AdminSettingsAudit.id == row.id).one()
    assert fetched.timestamp.tzinfo is not None, "timestamp came back naive"
    assert fetched.expires_at is not None
    assert fetched.expires_at.tzinfo is not None, "expires_at came back naive"

    # Now flip to applied with an aware ``applied_at`` and re-check.
    fetched.status = AdminSettingsAuditStatus.APPLIED.value
    fetched.applied_at = datetime.now(UTC)
    db.flush()
    refetched = db.query(AdminSettingsAudit).filter(AdminSettingsAudit.id == row.id).one()
    assert refetched.applied_at is not None
    assert refetched.applied_at.tzinfo is not None, "applied_at came back naive"

    # Cleanup — keep the test row out of subsequent assertions.
    db.delete(refetched)
    db.flush()


def test_admin_audit_model_columns_declare_timezone_true():
    """SQLAlchemy column metadata must carry ``timezone=True``.

    Reading the SA-level metadata (not the DB) catches a regression at
    edit time even when a developer runs ``create_all()`` against a fresh
    in-memory engine. Mirror of the catalog assertion.
    """
    insp = inspect(AdminSettingsAudit)
    for col_name in ("timestamp", "expires_at", "applied_at"):
        col = insp.columns[col_name]
        assert getattr(col.type, "timezone", False) is True, (
            f"{col_name} declared without timezone=True (drift will return)"
        )
