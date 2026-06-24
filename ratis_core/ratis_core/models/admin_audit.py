"""``admin_settings_audit`` — audit log for admin-driven settings mutations.

Mirrors the SQL table created in migration
``20260502_1900_admauad`` (see ``ARCH_admin_settings.md`` § Schéma DB).
Append-only by convention ; only ``status`` and ``applied_at`` are
updated when a ``pending_2fa`` row is confirmed / expires / cancelled
(handled in Bloc B).

The CHECK constraints declared here are **redundant** with the ones in
the migration — they exist on the model so that ``Base.metadata.create_all()``
in tests reproduces the same schema and exercises the same invariants.
The migration remains the source of truth at upgrade time.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    TIMESTAMP,
    CheckConstraint,
    Index,
    Text,
    func,
)
from sqlalchemy import (
    Enum as SQLEnum,
)
from sqlalchemy import (
    text as sa_text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from ratis_core.database import Base


class AdminSettingsAuditStatus(enum.StrEnum):
    """Lifecycle states of an audit row.

    See ``ARCH_admin_settings.md`` § Garde-fous V1 § 2 for the full state
    machine. The ``str`` mixin makes the enum interchangeable with the
    raw PG values inside SQL filters and JSON payloads.
    """

    APPLIED = "applied"
    PENDING_2FA = "pending_2fa"
    EXPIRED = "expired"
    CANCELLED = "cancelled"


class AdminSettingsAudit(Base):
    """One row per admin mutation on ``app_settings`` (PUT or seed).

    Append-only. ``status`` transitions captured on the row itself —
    never deleted, never mutated except for the documented status /
    ``applied_at`` updates triggered by TOTP confirmation in Bloc B.
    """

    __tablename__ = "admin_settings_audit"
    __table_args__ = (
        CheckConstraint(
            "length(reason) >= 8",
            name="chk_reason_min_len",
        ),
        CheckConstraint(
            "(status = 'applied' AND applied_at IS NOT NULL)"
            " OR (status = 'pending_2fa' AND expires_at IS NOT NULL"
            " AND applied_at IS NULL)"
            " OR (status IN ('expired', 'cancelled') AND applied_at IS NULL)",
            name="chk_status_2fa_coherence",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=sa_text("gen_random_uuid()"),
    )
    # ``TIMESTAMP(timezone=True)`` is explicit so ``Base.metadata.create_all()``
    # in tests reproduces the migration's ``TIMESTAMPTZ`` schema. The bare
    # ``Mapped[datetime]`` shorthand defaults to naive ``TIMESTAMP`` which
    # silently drifts from prod and forces aware/naive normalization in
    # routes. See ``KNOWN_PROBLEMS.md`` § KP-44.
    timestamp: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    operator: Mapped[str] = mapped_column(Text, nullable=False)
    section: Mapped[str] = mapped_column(Text, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    old_data: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    new_data: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    diff: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    status: Mapped[AdminSettingsAuditStatus] = mapped_column(
        SQLEnum(
            AdminSettingsAuditStatus,
            name="admin_settings_audit_status",
            values_callable=lambda enum_cls: [m.value for m in enum_cls],
            create_type=True,
        ),
        nullable=False,
        server_default=sa_text("'applied'"),
    )
    expires_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    applied_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)


# Indexes mirror the migration so create_all() in test setup has them too.
Index(
    "idx_admin_settings_audit_section_ts",
    AdminSettingsAudit.section,
    AdminSettingsAudit.timestamp.desc(),
)
Index(
    "idx_admin_settings_audit_ts",
    AdminSettingsAudit.timestamp.desc(),
)
# Partial UNIQUE index — H2 guard : at most one ``pending_2fa`` row per
# section. Mirrors the migration ``20260503_1000_uq_p2fa`` so
# ``create_all()`` test setups raise an IntegrityError on the same
# violation that production hits in PG.
Index(
    "uq_admin_settings_audit_one_pending_per_section",
    AdminSettingsAudit.section,
    unique=True,
    postgresql_where=sa_text("status = 'pending_2fa'"),
)
# Partial index for the pending-expiry batch — declared via raw SQL in
# the migration, not reproducible through SQLAlchemy core in a way that
# matches PG's ``WHERE`` clause exactly. Tests that need it should run
# the migration ; ``create_all`` skips it gracefully.
