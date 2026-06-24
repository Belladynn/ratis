"""Drop legacy scans.status_check (vestige) — was blocking pipeline_v3 INSERTs

Revision ID: 20260501_0700_dropck
Revises: 20260430_1900_v3barcode
Create Date: 2026-05-01 07:00:00

The migration ``20260430_1000_pipeline_v3_clean.py`` (PR #195) tried to drop
``scans_status_check`` (with the table-prefix). But the actual constraint name
in the production schema was just ``status_check`` (no prefix). The DROP IF
EXISTS silently no-op'd and the legacy CHECK kept blocking any INSERT with
``status='matched'`` — pipeline_v3's canonical value.

Symptom : every receipt scan via pipeline_v3 in prod failed Phase 4 persist
with ``CheckViolation: new row for relation "scans" violates check constraint
"status_check"`` even though all upstream phases (extract/comprehend/match)
succeeded. Hot-fix applied manually via psql 2026-05-01 — this migration
synchronises git/prod so a fresh DB rebuild doesn't reintroduce the bug.

Cf. KP-NN to log post-merge.
"""

from alembic import op


# revision identifiers, used by Alembic.
revision = "20260501_0700_dropck"
down_revision = "20260430_1900_v3barcode"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE scans DROP CONSTRAINT IF EXISTS status_check")


def downgrade() -> None:
    # Recreate the legacy CHECK with the v2 enum values, in case someone
    # ever needs to roll back to pre-pipeline_v3 schema. The v3 enum
    # constraint ``scans_status_check_v3`` already accepts the v2 values
    # as a superset, so this is purely belt-and-suspenders.
    op.execute(
        "ALTER TABLE scans ADD CONSTRAINT status_check "
        "CHECK (status IN ('pending', 'unmatched', 'accepted', 'rejected'))"
    )
