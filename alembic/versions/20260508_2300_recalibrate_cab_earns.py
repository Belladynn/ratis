"""recalibrate cab earns — V1.x weekly mission rewards.

Revision ID: 20260508_2300_recalcab
Revises: 20260508_2200_boutiquev1
Create Date: 2026-05-08 23:00:00

Recalibration V1.x of the CAB earns grid (acted 2026-05-08 brainstorm —
boutique V1 + cap fiscal alignment, target profile farmer 100-150€/mois max).

Scope of this migration : weekly ``missions.cab_reward`` only.

| frequency | difficulty | before | after |
|-----------|------------|--------|-------|
| weekly    | easy       |   50   |   20  |
| weekly    | medium     |  150   |   50  |
| weekly    | hard       |  300   |  100  |

Daily missions (5 / 15 / 30) and the per-action ``cab_per_*`` settings are
recalibrated separately :
  - daily missions : unchanged at 5 / 15 / 30 (already calibrated)
  - ``cab_per_*`` : recalibrated in ``ratis_settings.json`` (DB fallback,
    no migration needed — admin-editable section).

Idempotent : conditions ``AND cab_reward = X`` guard against re-running on
already-recalibrated rows AND avoid touching templates that an admin may
have manually adjusted.

Down-migration : exact reverse (20 → 50, 50 → 150, 100 → 300), same
idempotent guards.
"""
from __future__ import annotations

from alembic import op


# revision identifiers, used by Alembic. ID kept ≤ 32 chars (R08).
revision = "20260508_2300_recalcab"
down_revision = "20260508_2200_boutiquev1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Recalibrate weekly easy : 50 → 20
    op.execute(
        "UPDATE missions SET cab_reward = 20 "
        "WHERE frequency = 'weekly' AND difficulty = 'easy' "
        "AND cab_reward = 50"
    )
    # Recalibrate weekly medium : 150 → 50
    op.execute(
        "UPDATE missions SET cab_reward = 50 "
        "WHERE frequency = 'weekly' AND difficulty = 'medium' "
        "AND cab_reward = 150"
    )
    # Recalibrate weekly hard : 300 → 100
    op.execute(
        "UPDATE missions SET cab_reward = 100 "
        "WHERE frequency = 'weekly' AND difficulty = 'hard' "
        "AND cab_reward = 300"
    )


def downgrade() -> None:
    # Reverse weekly easy : 20 → 50
    op.execute(
        "UPDATE missions SET cab_reward = 50 "
        "WHERE frequency = 'weekly' AND difficulty = 'easy' "
        "AND cab_reward = 20"
    )
    # Reverse weekly medium : 50 → 150
    op.execute(
        "UPDATE missions SET cab_reward = 150 "
        "WHERE frequency = 'weekly' AND difficulty = 'medium' "
        "AND cab_reward = 50"
    )
    # Reverse weekly hard : 100 → 300
    op.execute(
        "UPDATE missions SET cab_reward = 300 "
        "WHERE frequency = 'weekly' AND difficulty = 'hard' "
        "AND cab_reward = 100"
    )
