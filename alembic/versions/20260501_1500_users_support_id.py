"""Add users.support_id (RTS-XXXXXX, public non-PII).

Revision ID: 20260501_1500_supid
Revises: 20260501_1200_nrcA
Create Date: 2026-05-01 15:00:00

Introduces a third user identifier ``support_id`` of shape ``RTS-XXXXXX``
(see :mod:`ratis_core.identifiers`) for support workflows :

- ``users.id``      — UUID, internal stable identity, ugly to dictate.
- ``users.email``   — PII, unsafe to share publicly.
- ``users.support_id`` — public, non-PII, compact, dictation-friendly.

Strategy : three-step in a single migration to avoid a window where the
column would be NOT NULL on an empty table and break inserts in flight.

Step 1 : ADD COLUMN nullable.
Step 2 : Backfill in Python with ``generate_support_id`` and a per-row
         retry on UNIQUE violation (max 5 attempts ; theoretical collision
         rate ~ N/32^6 = ~1e-3 at N=1M users).
Step 3 : ALTER COLUMN ... SET NOT NULL + create the UNIQUE index.

The downgrade reverses in the opposite order : drop the index, then the
column. No data is preserved on downgrade — the support_id values would
be regenerated on a re-upgrade and a stale offline copy would be useless.
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError

from ratis_core.identifiers import generate_support_id


# revision identifiers, used by Alembic.
revision = "20260501_1500_supid"
down_revision = "20260501_1200_nrcA"
branch_labels = None
depends_on = None


# Maximum retries when a UNIQUE collision happens while backfilling. With
# a 32^6 ≈ 1.07B keyspace, the practical collision probability per row is
# ~1e-3 even at 1M users — 5 retries is comfortably defensive.
_MAX_RETRIES = 5


def upgrade() -> None:
    # ----- Step 1 : add the column nullable -----
    op.add_column(
        "users",
        sa.Column("support_id", sa.Text(), nullable=True),
    )

    # ----- Step 2 : backfill existing rows -----
    bind = op.get_bind()
    user_ids = [row[0] for row in bind.execute(sa.text("SELECT id FROM users")).fetchall()]
    for user_id in user_ids:
        for attempt in range(1, _MAX_RETRIES + 1):
            candidate = generate_support_id()
            try:
                # Use a SAVEPOINT so a single collision doesn't poison the
                # whole transaction — the retry is meaningless otherwise.
                with bind.begin_nested():
                    bind.execute(
                        sa.text("UPDATE users SET support_id = :sid WHERE id = :uid"),
                        {"sid": candidate, "uid": user_id},
                    )
                break
            except IntegrityError:
                if attempt == _MAX_RETRIES:
                    raise
                # else : try a fresh candidate.
                continue

    # ----- Step 3 : enforce NOT NULL + UNIQUE -----
    op.alter_column("users", "support_id", existing_type=sa.Text(), nullable=False)
    op.create_index(
        "uq_users_support_id",
        "users",
        ["support_id"],
        unique=True,
    )


def downgrade() -> None:
    # Drop in reverse order — index first (it depends on the column).
    op.drop_index("uq_users_support_id", table_name="users")
    op.drop_column("users", "support_id")
