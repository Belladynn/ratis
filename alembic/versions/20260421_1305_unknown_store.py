"""scans: nullable store_id + user_lat/user_lng + store_status

Revision ID: 20260421_1305_unknown_store
Revises: fav20260420001
Create Date: 2026-04-21 13:05:00.000000+00:00

Part A of the "graceful unknown store" sequence.

- ``scans.store_id`` becomes nullable: a label scan with no store matched in
  geographical radius is persisted with ``store_id=NULL``, no CAB/XP awarded,
  waiting for receipt-based reconciliation (Part B).
- ``scans.store_status`` mirrors the column already present on ``receipts``.
  Default ``'confirmed'`` for rows that existed before this migration — they
  all had a non-null ``store_id`` and were confirmed by construction.
- ``scans.user_lat`` / ``scans.user_lng`` persist the user's position at
  shutter time (RGPD: PII, never logged) so Part B can geo-match a future
  receipt against pending 'unknown' scans.
- ``label_sessions.store_id`` also becomes nullable: a batch with no store
  matched still produces a session (all scans aggregated there).

FK ``scans.store_id`` stays RESTRICT — a known store still cannot be deleted
while it is referenced. Unknown-store scans do not reference any store.
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260421_1305_unknown_store"
down_revision = "fav20260420001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. scans.store_id → nullable
    op.alter_column("scans", "store_id", existing_type=sa.UUID(), nullable=True)

    # 2. label_sessions.store_id → nullable
    op.alter_column(
        "label_sessions", "store_id", existing_type=sa.UUID(), nullable=True
    )

    # 3. scans.store_status — mirrors receipts.store_status
    op.add_column(
        "scans",
        sa.Column(
            "store_status",
            sa.Text(),
            server_default=sa.text("'confirmed'"),
            nullable=False,
        ),
    )
    op.execute(
        "ALTER TABLE scans ADD CONSTRAINT ck_scans_store_status "
        "CHECK (store_status IN ('confirmed', 'pending', 'unknown'))"
    )
    # Consistency guard: unknown ⇔ store_id IS NULL
    op.execute(
        "ALTER TABLE scans ADD CONSTRAINT ck_scans_store_status_consistency "
        "CHECK ("
        "(store_status = 'unknown' AND store_id IS NULL) OR "
        "(store_status <> 'unknown' AND store_id IS NOT NULL)"
        ")"
    )

    # 4. scans.user_lat / scans.user_lng — same precision as stores.lat/lng
    op.add_column("scans", sa.Column("user_lat", sa.Numeric(9, 6), nullable=True))
    op.add_column("scans", sa.Column("user_lng", sa.Numeric(9, 6), nullable=True))


def downgrade() -> None:
    op.drop_column("scans", "user_lng")
    op.drop_column("scans", "user_lat")

    op.execute(
        "ALTER TABLE scans DROP CONSTRAINT IF EXISTS ck_scans_store_status_consistency"
    )
    op.execute("ALTER TABLE scans DROP CONSTRAINT IF EXISTS ck_scans_store_status")
    op.drop_column("scans", "store_status")

    # Backfill before re-imposing NOT NULL — impossible to recover store_id,
    # so delete any rows that have NULL store_id (only created by Part A).
    op.execute("DELETE FROM scans WHERE store_id IS NULL")
    op.alter_column("scans", "store_id", existing_type=sa.UUID(), nullable=False)

    op.execute("DELETE FROM label_sessions WHERE store_id IS NULL")
    op.alter_column(
        "label_sessions", "store_id", existing_type=sa.UUID(), nullable=False
    )
