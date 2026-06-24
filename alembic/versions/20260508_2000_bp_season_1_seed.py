"""bp saison 1 — seed 1 season + 30 milestones (data migration).

Revision ID: 20260508_2000_bp_s1
Revises: 20260508_1800_miss_pb
Create Date: 2026-05-08 20:00:00

Data-only migration that seeds the Battle Pass Saison 1 catalogue.
Schema is unchanged — ``battlepass_seasons`` / ``battlepass_milestones``
tables and the ``uq_one_active_season`` partial unique index were
created by ``20260410_1000_f2a3b4c5d6e7_…``.

Calibration source : ``docs/superpowers/specs/2026-05-08-gamif-calibration.xlsx``
- mid-anchor : palier 15 → 10 000 CAB required → 5 € gift card
- end-anchor : palier 30 → 40 000 CAB required → 20 € gift card
- 28 in-pass cab-reward paliers ship a linear CAB reward 20 → ~3000

The seed is delegated to ``ratis_core.seed.bp_season_1.seed_bp_season_1``
so the same pure-Python source of truth feeds the migration, the
``test_bp_season_1_seed`` tests and any future admin-UI re-seed.

R33 — the seed flips any other ``is_active=true`` season to false BEFORE
upserting Saison 1 (``uq_one_active_season`` would reject otherwise).
We never DELETE a season row — historical progress + claims FK-reference
it.

Defensive pattern : the upserts use ``ON CONFLICT DO UPDATE`` so a
re-run on a DB that already has Saison 1 reconverges to the canonical
calibration (idempotent).
"""
from __future__ import annotations

from alembic import op
from sqlalchemy.orm import Session

from ratis_core.seed.bp_season_1 import seed_bp_season_1


# revision identifiers, used by Alembic. ID kept ≤ 32 chars (R08).
revision = "20260508_2000_bp_s1"
down_revision = "20260508_1800_miss_pb"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    session = Session(bind=bind)
    try:
        seed_bp_season_1(session)
        session.flush()
    finally:
        session.close()


def downgrade() -> None:
    # Wipe the 30 milestones first (FK season_id RESTRICT) then the
    # season row. Idempotent : ``IF EXISTS``-style WHERE clauses keep
    # the migration safe on a DB where Saison 1 was never seeded.
    #
    # We DO NOT touch ``user_battlepass_progress`` or
    # ``user_battlepass_claims`` — both reference Saison 1 via FK
    # RESTRICT. If user data already exists, downgrade will fail loudly,
    # which is the desired behaviour : a downgrade past Saison 1 in
    # presence of real user state is a destructive operation that needs
    # explicit operator decision (R05).
    op.execute(
        "DELETE FROM battlepass_milestones m "
        "USING battlepass_seasons s "
        "WHERE m.season_id = s.id AND s.season_number = 1"
    )
    op.execute("DELETE FROM battlepass_seasons WHERE season_number = 1")
