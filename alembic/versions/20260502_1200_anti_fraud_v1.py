"""Anti-fraud V1 — users.trust_score + product_name_resolutions.weight_override

Revision ID: 20260502_1200_afv1
Revises: 20260502_1000_nrcF
Create Date: 2026-05-02 12:00:00

Introduces a per-user trust score for the Name-Resolution Consensus (NRC)
anti-fraud V1 mechanism — see ``ARCH_anti_fraud.md``.

Adds to ``users`` :

- ``trust_score`` INT NOT NULL DEFAULT 50 — neutral start (0–100), batch-
  recomputed nightly by ``ratis_batch_trust_score`` from the user's history
  of contributions in ``product_name_resolutions``.
- ``total_resolved_scans`` INT NOT NULL DEFAULT 0 — denormalised count of
  the user's contributing rows on labels that reached a consensus state
  (VERIFIED / UNVERIFIED). Drives the 100-scan grace period gate.
- ``is_shadow_banned`` BOOLEAN NOT NULL DEFAULT FALSE — automatic flag
  flipped to true when ``trust_score < 65`` AND ``total_resolved_scans
  >= 100``. Effect : the user's NRC contributions are persisted with
  ``weight_override = 0`` (audit trail preserved, vote weight zero) and
  CAB scan rewards are skipped silently.
- ``trust_score_updated_at`` TIMESTAMPTZ NULL — last batch run touch
  timestamp ; NULL until the first run completes.

Adds to ``product_name_resolutions`` :

- ``weight_override`` INT NULL — when set (currently only ``0``), this
  value replaces the method-derived weight in the consensus aggregation.
  The append-only ledger keeps the row for audit ; only the *vote weight*
  becomes zero. Future V2 may use other override values for graduated
  signals (e.g. half-weight pending review).

Partial index ``idx_users_trust_score`` accelerates the admin queue
listing of users in the warning / shadow-ban band — these are the only
rows the admin UI ever reads in this dimension.

Downgrade reverses both schema changes ; data is not preserved (the
trust score is recomputable from the ledger on demand).
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision = "20260502_1200_afv1"
down_revision = "20260502_1000_nrcF"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ----- users : trust score columns -----
    op.add_column(
        "users",
        sa.Column(
            "trust_score",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("50"),
        ),
    )
    op.add_column(
        "users",
        sa.Column(
            "total_resolved_scans",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )
    op.add_column(
        "users",
        sa.Column(
            "is_shadow_banned",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.add_column(
        "users",
        sa.Column(
            "trust_score_updated_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.create_check_constraint(
        "users_trust_score_range_chk",
        "users",
        "trust_score >= 0 AND trust_score <= 100",
    )
    # Partial index — admin queue only ever reads users in the warning /
    # shadow-ban band. Keeps the index hot and tiny.
    op.execute(
        """
        CREATE INDEX idx_users_trust_score ON users (trust_score)
        WHERE trust_score < 75 AND total_resolved_scans >= 100
        """
    )

    # ----- product_name_resolutions : weight_override -----
    op.add_column(
        "product_name_resolutions",
        sa.Column(
            "weight_override",
            sa.Integer(),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.execute("ALTER TABLE product_name_resolutions DROP COLUMN IF EXISTS weight_override")
    op.execute("DROP INDEX IF EXISTS idx_users_trust_score")
    op.execute("ALTER TABLE users DROP CONSTRAINT IF EXISTS users_trust_score_range_chk")
    op.drop_column("users", "trust_score_updated_at")
    op.drop_column("users", "is_shadow_banned")
    op.drop_column("users", "total_resolved_scans")
    op.drop_column("users", "trust_score")
