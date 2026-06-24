"""seed_achievements_v1 — 23 initial entries (cf spec catalog seed table).

Revision ID: 20260510_1030_ach_seed
Revises: 20260510_1020_ach_cab_ref
Create Date: 2026-05-10 10:30:00.000000

Runs LAST in the achievements V1 chain (after both the schema creation
``20260510_1000_ach_v1`` and the cabecoin CHECK extension
``20260510_1020_ach_cab_ref``) — seeding the catalog after the schema is
fully migrated is the logically correct order : every constraint enforced
on ``achievements`` (in particular ``ck_achievements_no_jyetais_in_catalog``)
is already in place when the rows are inserted.

Source-of-truth catalog : ``ratis_core.seed_achievements.ACHIEVEMENTS_V1``.
We reuse the same Python module that conftest.py uses for tests, so
prod migration + test fixture cannot drift.
"""
from alembic import op
from sqlalchemy.orm import Session

from ratis_core.seed_achievements import SEED_CODES, seed_achievements

# revision identifiers (≤32 chars per R08).
revision = "20260510_1030_ach_seed"
down_revision = "20260510_1020_ach_cab_ref"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    session = Session(bind=bind)
    try:
        seed_achievements(session)
    finally:
        session.close()


def downgrade() -> None:
    # Hard delete only the seeded codes — leaves any admin-added catalog
    # rows untouched. UserAchievements have FK RESTRICT on achievement_id,
    # so this fails-safe if a user has already unlocked one of the seed
    # entries (operator must clean ``user_achievements`` manually first).
    #
    # SEED_CODES is a static, hard-coded Python tuple of identifier strings
    # (no user input ever reaches it) — S608 false positive, hence noqa.
    placeholders = ", ".join(f"'{c}'" for c in SEED_CODES)
    # noqa S608 : SEED_CODES is a hard-coded module constant, no SQL
    # injection vector.
    sql = f"DELETE FROM achievements WHERE code IN ({placeholders})"  # noqa: S608
    op.execute(sql)
