"""widen reward_config.action_type + notification_logs.type CHECK enums.

Revision ID: 20260511_1100_widen
Revises: 938ee29f5c5f
Create Date: 2026-05-11 11:00:00

Two latent prod bugs surfaced by the Pattern A schema-sync audit
(PR #400) : both ``reward_config.action_type_check`` and
``notification_logs.type_check`` pin legacy enum values that the
application stack stopped emitting long ago. Any INSERT/UPDATE of a
current value (e.g. ``receipt_scan``, ``achievement_unlocked``)
raises ``CheckViolation`` in prod.

This migration widens both CHECKs to the live canonical sets :

1. ``reward_config_action_type_check`` — mirrored on the canonical
   missions catalogue set
   (``receipt_scan / label_scan / barcode_scan /
   product_identification / price_compared / fill_product_field /
   scan_distinct / promo_found``). Same set as
   ``missions_action_type_check`` so admins can configure rewards
   for any action_type the missions catalogue supports.

2. ``notification_logs_type_check`` — mirrored on every value the
   notifier currently emits, captured via :
     - ``NotifType`` Literal in ``webservices/ratis_notifier/routes/notify.py``
     - legacy ``notify_user()`` calls in ratis_list_optimiser and
       ratis_batch_trust_score

   The legacy enum values (``price_drop``, ``streak_reminder``,
   ``weekly_recap``, ``challenge_available``, ``cashback_credited``,
   ``level_up``) are dropped — none of them are emitted by current
   code. ``notification_logs`` rows would have been impossible to
   INSERT historically anyway given the stale CHECK, so no data
   migration is needed.

Defensive pattern (R07) : every DROP guarded with ``IF EXISTS`` so
the migration is idempotent on repeat upgrade runs.
"""
from __future__ import annotations

from alembic import op


# revision identifiers, used by Alembic. ID kept ≤ 32 chars (R08).
revision = "20260511_1100_widen"
down_revision = "938ee29f5c5f"
branch_labels = None
depends_on = None


# Canonical sets — kept as module constants so the down-revision can
# restore the legacy values exactly. Updated in sync with the ORM
# ``__table_args__`` and the ``NotifType`` Literal in
# ``webservices/ratis_notifier/routes/notify.py``.
_REWARD_ACTION_TYPES = (
    "'receipt_scan', 'label_scan', 'barcode_scan', "
    "'product_identification', 'price_compared', "
    "'fill_product_field', 'scan_distinct', 'promo_found'"
)

_NOTIF_TYPES = (
    "'scan_done', 'cashback_available', 'badge_unlocked', "
    "'price_alert', 'route_ready', 'battlepass_milestone_unlocked', "
    "'challenge_milestone_unlocked', 'mystery_product_found', "
    "'store_validated', 'retro_cab_gratitude', "
    "'achievement_unlocked', 'trust_score_warning'"
)

_LEGACY_REWARD_ACTION_TYPES = (
    "'DAILY_LOGIN', 'SCAN_RECEIPT', 'VIDEO_SCAN', 'PRICE_CHALLENGE'"
)

_LEGACY_NOTIF_TYPES = (
    "'price_drop', 'streak_reminder', 'weekly_recap', "
    "'challenge_available', 'cashback_credited', 'level_up'"
)


def upgrade() -> None:
    # ------------------------------------------------------------------ #
    # 1. reward_config.action_type_check — widen to current snake_case   #
    #    set used by the admin endpoints + missions runtime.             #
    # ------------------------------------------------------------------ #
    op.execute(
        "ALTER TABLE reward_config DROP CONSTRAINT IF EXISTS action_type_check"
    )
    op.execute(
        "ALTER TABLE reward_config DROP CONSTRAINT IF EXISTS "
        "reward_config_action_type_check"
    )
    op.create_check_constraint(
        "reward_config_action_type_check",
        "reward_config",
        f"action_type IN ({_REWARD_ACTION_TYPES})",
    )

    # ------------------------------------------------------------------ #
    # 2. notification_logs.type_check — widen to current notifier set.   #
    # ------------------------------------------------------------------ #
    op.execute(
        "ALTER TABLE notification_logs DROP CONSTRAINT IF EXISTS type_check"
    )
    op.execute(
        "ALTER TABLE notification_logs DROP CONSTRAINT IF EXISTS "
        "notification_logs_type_check"
    )
    op.create_check_constraint(
        "notification_logs_type_check",
        "notification_logs",
        f"type IN ({_NOTIF_TYPES})",
    )


def downgrade() -> None:
    # Restore the legacy stale CHECKs. The downgrade is only meaningful
    # for environments that were never exercised under the prod traffic
    # (dev / CI). Any row inserted with a modern value would block the
    # downgrade — that is intentional, we should never re-narrow in prod.
    op.execute(
        "ALTER TABLE reward_config DROP CONSTRAINT IF EXISTS "
        "reward_config_action_type_check"
    )
    op.create_check_constraint(
        "action_type_check",
        "reward_config",
        f"action_type IN ({_LEGACY_REWARD_ACTION_TYPES})",
    )

    op.execute(
        "ALTER TABLE notification_logs DROP CONSTRAINT IF EXISTS "
        "notification_logs_type_check"
    )
    op.create_check_constraint(
        "type_check",
        "notification_logs",
        f"type IN ({_LEGACY_NOTIF_TYPES})",
    )
