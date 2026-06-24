"""bp saison 1 — switch reward_value to "waves" pattern.

Revision ID: 20260509_0000_bp_s1_w
Revises: 20260508_2300_recalcab
Create Date: 2026-05-09 00:00:00

Data-only migration that replaces the original linear ``reward_value``
curve (peak palier 29 ≈ 2 897 CAB, total ~40 000 CAB redistributed —
deemed over-generous by product) with a hardcoded "waves" pattern :

- segment 1 (paliers 1-14)  : crest at palier 8  → 250 CAB (sum = 1 900)
- palier 15 unchanged (gift card 5 €)
- segment 2 (paliers 16-29) : crest at palier 22 → 400 CAB (sum = 3 200)
- palier 30 unchanged (gift card 20 €)
- total redistributed across the 28 cab-reward paliers = 5 100 CAB.

All values are multiples of 50. Acted 2026-05-08, source of truth is
``ratis_core.seed.bp_season_1._CAB_REWARDS`` — this migration mirrors
the same dict so an already-seeded prod DB converges to the new grid
without re-running the full seed (which would also touch the season
row + cab_required values, both unchanged here).

Idempotent : ``UPDATE ... WHERE reward_type='cab' AND milestone_number=N``
re-running on a DB that already holds the new values is a no-op (the
``UPDATE`` simply rewrites identical values). Skip-paliers 15/30 means
the gift-card rows are never touched.

Down-migration : revert the 28 cab-reward paliers to the original linear
curve (palier 1 = 20 CAB, palier 29 ≈ 2 897 CAB, slope (3000-20)/29).
We re-derive the formula here rather than depending on the seed module
so a future refactor of the seed cannot break the rollback path.
"""
from __future__ import annotations

from alembic import op
from sqlalchemy import text


# revision identifiers, used by Alembic. ID kept ≤ 32 chars (R08).
revision = "20260509_0000_bp_s1_w"
down_revision = "20260508_2300_recalcab"
branch_labels = None
depends_on = None


# Hardcoded "waves" pattern — mirrors ratis_core.seed.bp_season_1._CAB_REWARDS.
# Paliers 15 and 30 are intentionally absent (gift_card rows untouched).
_NEW_REWARDS: dict[int, int] = {
    # Segment 1 (sum = 1 900 CAB).
    1: 50, 2: 50, 3: 100, 4: 150, 5: 150,
    6: 200, 7: 200, 8: 250, 9: 200, 10: 200,
    11: 150, 12: 100, 13: 50, 14: 50,
    # Segment 2 (sum = 3 200 CAB).
    16: 100, 17: 150, 18: 200, 19: 250, 20: 300,
    21: 350, 22: 400, 23: 350, 24: 300, 25: 250,
    26: 200, 27: 150, 28: 100, 29: 100,
}


# Bound-parameter UPDATE — single statement reused for every palier.
# Filter on reward_type='cab' is a defence-in-depth so we never rewrite a
# gift-card row by accident (paliers 15, 30 are also absent from the
# loop, so this is belt + suspenders).
_UPDATE_SQL = text(
    "UPDATE battlepass_milestones "
    "SET reward_value = :rval "
    "WHERE season_id = ("
    "  SELECT id FROM battlepass_seasons WHERE season_number = 1"
    ") "
    "AND milestone_number = :num "
    "AND reward_type = 'cab'"
)


def upgrade() -> None:
    bind = op.get_bind()
    for milestone_number, new_value in _NEW_REWARDS.items():
        bind.execute(
            _UPDATE_SQL,
            {"rval": new_value, "num": milestone_number},
        )


def downgrade() -> None:
    # Revert to the original linear curve : palier 1 → 20 CAB, palier 29
    # ≈ 2 897 CAB, slope = (3000 - 20) / 29. Formula re-derived locally so
    # the rollback never imports from the seed module (which now hardcodes
    # the new pattern). round() matches Python 3 banker's rounding — same
    # behaviour as the original ``cab_reward_for_palier`` implementation.
    cab_reward_first = 20
    cab_reward_last = 3000
    end_palier = 30
    cab_paliers = [p for p in range(1, end_palier + 1) if p not in (15, 30)]
    bind = op.get_bind()
    for milestone_number in cab_paliers:
        legacy_value = round(
            cab_reward_first
            + (milestone_number - 1)
            * (cab_reward_last - cab_reward_first) / 29
        )
        bind.execute(
            _UPDATE_SQL,
            {"rval": legacy_value, "num": milestone_number},
        )
