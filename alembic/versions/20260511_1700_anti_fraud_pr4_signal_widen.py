"""anti_fraud_pr4 — widen fraud_suspicions.detection_signal CHECK

Revision ID: 20260511_1700_afpr4
Revises: 20260511_1500_afpr1
Create Date: 2026-05-11 17:00:00

PR4 of the anti-fraud receipt-pipeline sprint — adds the
``daily_soft_burst`` value to the ``ck_fraud_suspicions_signal`` CHECK
constraint so the application code (``worker/pipeline_v3/persist.py``
post-INSERT block) can persist a fraud_suspicion row when a user crosses
the soft daily cap (7 ≤ count < 14 receipts/day per
``pipeline.anti_fraud.receipts_soft_warn_per_day``).

Pattern : DROP CONSTRAINT IF EXISTS → CREATE CONSTRAINT (cf R07). The
DROP uses IF EXISTS so the migration is replay-safe and the downgrade
can recreate the narrower CHECK cleanly.

Cf ARCH_receipt_pipeline.md § "Implem sprint suggéré" — PR4 cross-user
policy + caps + device. The 4 pre-existing values stay valid ; we only
add a 5th.
"""
from __future__ import annotations

from alembic import op


# revision identifiers, used by Alembic. ≤32 chars per R08.
revision = "20260511_1700_afpr4"
down_revision = "20260511_1500_afpr1"
branch_labels = None
depends_on = None


# Canonical signal list — kept inline (not imported from ORM) so the
# migration stays runnable even if the ORM module changes shape later.
_NEW_SIGNALS = (
    "'phash', 'fp_global_strict', 'fp_global_minute', "
    "'device_shared', 'daily_soft_burst'"
)
_OLD_SIGNALS = (
    "'phash', 'fp_global_strict', 'fp_global_minute', 'device_shared'"
)


def upgrade() -> None:
    op.execute(
        "ALTER TABLE fraud_suspicions "
        "DROP CONSTRAINT IF EXISTS ck_fraud_suspicions_signal"
    )
    op.execute(
        f"ALTER TABLE fraud_suspicions "
        f"ADD CONSTRAINT ck_fraud_suspicions_signal "
        f"CHECK (detection_signal IN ({_NEW_SIGNALS}))"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE fraud_suspicions "
        "DROP CONSTRAINT IF EXISTS ck_fraud_suspicions_signal"
    )
    op.execute(
        f"ALTER TABLE fraud_suspicions "
        f"ADD CONSTRAINT ck_fraud_suspicions_signal "
        f"CHECK (detection_signal IN ({_OLD_SIGNALS}))"
    )
