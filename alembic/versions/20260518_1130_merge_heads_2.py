"""merge gc_cap_resv and merge_heads heads

Revision ID: 20260518_1130_merge_heads_2
Revises: 20260518_0737_merge_heads, 20260518_1000_gc_cap_resv
Create Date: 2026-05-18 11:30:00.000000+00:00

PRs #510 (``20260518_0737_merge_heads``) and #511
(``20260518_1000_gc_cap_resv``) both branched off the same parent pair
``20260517_1600_gc_churned`` / ``20260517_1700_optroute_uq`` and were
merged into ``main`` independently — #511 had been opened before #510
landed, so it carried a stale parent. The revision tree ends up with two
heads again and ``alembic upgrade head`` fails with "Multiple head
revisions are present", blocking CI for every open PR.

This is a pure merge revision: it reconciles the two heads into a single
linear head with no schema change. ``upgrade()`` / ``downgrade()`` are
intentionally empty.
"""
from __future__ import annotations

# revision identifiers (≤32 chars per R08).
revision = "20260518_1130_merge_heads_2"
down_revision = ("20260518_0737_merge_heads", "20260518_1000_gc_cap_resv")
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Pure merge revision — no schema change.
    pass


def downgrade() -> None:
    # Pure merge revision — no schema change.
    pass
