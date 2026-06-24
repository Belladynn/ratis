"""add_partial_unique_idx_one_computing_route_per_list

Revision ID: 20260517_1700_optroute_uq
Revises: 20260517_1500_gc_refund
Create Date: 2026-05-17 17:00:00.000000+00:00

Audit H7 — prevent the double-tap race condition on POST
/lists/{list_id}/optimize.

Without a DB constraint, two concurrent requests can both pass the
sequential ``get_computing_route`` idempotency check and each INSERT an
``optimized_routes`` row with ``status='computing'``, spawning two Celery
tasks for the same list.

A partial UNIQUE index on ``optimized_routes (list_id) WHERE
status = 'computing'`` makes "at most one computing route per list" a hard
DB invariant.  The route handler catches the resulting ``IntegrityError``
on the losing concurrent request and returns the winner's in-flight route
(202) instead of propagating a 500.

The index is intentionally partial so that historical rows with any other
status (``ready``, ``failed``, ``updating``) for the same list are
unaffected.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers (≤32 chars per R08).
revision = "20260517_1700_optroute_uq"
down_revision = "20260517_1500_gc_refund"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "uq_optimized_routes_one_computing_per_list",
        "optimized_routes",
        ["list_id"],
        unique=True,
        postgresql_where=sa.text("status = 'computing'"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_optimized_routes_one_computing_per_list",
        table_name="optimized_routes",
    )
