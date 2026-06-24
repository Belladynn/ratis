"""merge gc_churned and optroute_uq heads

Revision ID: 20260518_0737_merge_heads
Revises: 20260517_1600_gc_churned, 20260517_1700_optroute_uq
Create Date: 2026-05-18 07:37:51.390135+00:00

PRs #505 (``20260517_1600_gc_churned``) and #506
(``20260517_1700_optroute_uq``) both branched off the same parent
``20260517_1500_gc_refund`` and were merged into ``main`` independently,
leaving the revision tree with two heads. ``alembic upgrade head`` then
fails with "Multiple head revisions are present" and blocks CI for every
open PR.

This is a pure merge revision: it reconciles the two heads into a single
linear head with no schema change. ``upgrade()`` / ``downgrade()`` are
intentionally empty.
"""
from __future__ import annotations

# revision identifiers (≤32 chars per R08).
revision = "20260518_0737_merge_heads"
down_revision = ("20260517_1600_gc_churned", "20260517_1700_optroute_uq")
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Pure merge revision — no schema change.
    pass


def downgrade() -> None:
    # Pure merge revision — no schema change.
    pass
