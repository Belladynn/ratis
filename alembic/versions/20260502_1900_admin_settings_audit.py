"""admin_settings_audit — append-only audit log for app_settings mutations.

Revision ID: 20260502_1900_admauad
Revises: 20260502_1700_consmatch
Create Date: 2026-05-02 19:00:00

Foundational table for the admin settings UI rollout — see
``ARCH_admin_settings.md`` § Schéma DB. Every PUT on a section of
``app_settings`` records one row here ; rows transition between four
states governed by an ENUM :

- ``applied``      — value written through to ``app_settings``
- ``pending_2fa``  — magnitude breach detected (``> 50 %`` variation on a
                     numeric leaf), value buffered, ``expires_at`` set 10
                     minutes ahead. The TOTP confirmation in Bloc B flips
                     the row to ``applied`` and writes ``app_settings``.
- ``expired``      — the 10-min grace window lapsed, value abandoned.
- ``cancelled``    — the operator cancelled the pending mutation.

Two CHECK constraints guarantee status / timestamp coherence at the DB
level (cf. ARCH § Schéma DB) :

- ``applied`` ⇒ ``applied_at IS NOT NULL``
- ``pending_2fa`` ⇒ ``expires_at IS NOT NULL`` AND ``applied_at IS NULL``
- ``expired`` / ``cancelled`` ⇒ ``applied_at IS NULL``

Plus a ``length(reason) >= 8`` minimum so the operator must always
provide a real motivation — typo "fix" / "test" rejected at write time.

Indexes :

- ``(section, timestamp DESC)`` for the per-section audit history page.
- ``(timestamp DESC)`` for the global audit feed.
- Partial ``(expires_at) WHERE status = 'pending_2fa'`` for the nightly
  expiry sweep batch.

The table is append-only by convention (no model-level enforcement V1).
The Bloc B service updates only ``status`` and ``applied_at`` on TOTP
confirmation / expiry / cancellation.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "20260502_1900_admauad"
down_revision = "20260502_1700_consmatch"
branch_labels = None
depends_on = None


_AUDIT_STATUS_VALUES = ("applied", "pending_2fa", "expired", "cancelled")


def upgrade() -> None:
    # Native PostgreSQL ENUM — owned by the table, dropped explicitly on
    # downgrade. ``create_type=False`` is NOT used here because the type
    # does not pre-exist ; alembic creates it as a side effect of the
    # column definition only when ``checkfirst`` is set, so we declare it
    # explicitly for clarity.
    audit_status = postgresql.ENUM(
        *_AUDIT_STATUS_VALUES,
        name="admin_settings_audit_status",
        create_type=False,
    )
    audit_status.create(op.get_bind(), checkfirst=False)

    op.create_table(
        "admin_settings_audit",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "timestamp",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("operator", sa.Text(), nullable=False),
        sa.Column("section", sa.Text(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("old_data", postgresql.JSONB(), nullable=True),
        sa.Column("new_data", postgresql.JSONB(), nullable=False),
        sa.Column("diff", postgresql.JSONB(), nullable=True),
        sa.Column(
            "status",
            audit_status,
            nullable=False,
            server_default=sa.text("'applied'"),
        ),
        sa.Column("expires_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("applied_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.CheckConstraint(
            "length(reason) >= 8",
            name="chk_reason_min_len",
        ),
        sa.CheckConstraint(
            "(status = 'applied' AND applied_at IS NOT NULL)"
            " OR (status = 'pending_2fa' AND expires_at IS NOT NULL"
            " AND applied_at IS NULL)"
            " OR (status IN ('expired', 'cancelled') AND applied_at IS NULL)",
            name="chk_status_2fa_coherence",
        ),
    )

    op.create_index(
        "idx_admin_settings_audit_section_ts",
        "admin_settings_audit",
        ["section", sa.text("timestamp DESC")],
    )
    op.create_index(
        "idx_admin_settings_audit_ts",
        "admin_settings_audit",
        [sa.text("timestamp DESC")],
    )
    # Partial index — pending-2FA rows are the only ones the expiry batch
    # ever scans. Tiny, hot, indexed on the relevant predicate.
    op.execute(
        "CREATE INDEX idx_admin_settings_audit_pending"
        " ON admin_settings_audit (expires_at)"
        " WHERE status = 'pending_2fa'"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_admin_settings_audit_pending")
    op.execute("DROP INDEX IF EXISTS idx_admin_settings_audit_ts")
    op.execute("DROP INDEX IF EXISTS idx_admin_settings_audit_section_ts")
    op.execute("DROP TABLE IF EXISTS admin_settings_audit")
    op.execute("DROP TYPE IF EXISTS admin_settings_audit_status")
