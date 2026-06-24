"""missions phase B — rename barcode_scan, prefixed qualifiers, reward_events.

Revision ID: 20260508_1800_miss_pb
Revises: 20260508_1000_miss_v1
Create Date: 2026-05-08 18:00:00

Phase B brings the V1 missions catalogue to its full shape and adds the
reward_events ledger that powers idempotent ``trigger_action`` calls.

Schema changes :

1. Extend ``missions_action_type_check`` to accept the new
   ``product_identification`` action_type, then UPDATE the 6 catalogue
   rows that historically used ``barcode_scan`` so they carry the new
   name. The legacy value is kept inside the CHECK constraint because
   ``cabecoin_transactions.reason`` already references it on historical
   rows — we simply stop *seeding* it. Same defensive trade-off applied
   in the ``cabecoin_transactions_reason_check`` constraint below.
2. Extend ``cabecoin_transactions_reason_check`` to accept the new
   reasons emitted by the phase B service code :
   ``product_identification`` (rename of ``barcode_scan``),
   ``fill_product_field``, ``scan_distinct``, ``promo_found``.
3. Re-prefix every non-NULL ``missions.qualifier``. The phase A seed
   shipped them unprefixed (``organic``, ``french``, ``category``,
   ``store``) so the runtime that read the rows had to special-case
   each one. Phase B settles on a uniform ``<type>:<value>`` shape :
       organic       → attribute:organic
       french        → attribute:french
       category      → category   (kept unprefixed — type tag, not value)
       store         → store      (idem)
   Events emit the resolved value (``category:dairy``, ``store:<uuid>``)
   and the runtime matches them against the type tag.
4. Flip every catalogue row to ``is_active=true`` — phase B unlocked
   every action_type and qualifier shape.
5. Create ``reward_events`` (idempotency + audit + reconciliation
   ledger). Unique on ``idempotency_key`` so two concurrent writers
   collapse to a single processed event.

Defensive pattern (R07) : every DROP guarded with ``IF EXISTS`` so the
migration is idempotent on repeat upgrade runs.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic. ID kept ≤ 32 chars (R08).
revision = "20260508_1800_miss_pb"
down_revision = "20260508_1000_miss_v1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ------------------------------------------------------------------ #
    # 1. Extend the action_type CHECK to admit product_identification.   #
    #    Keep ``barcode_scan`` accepted so historical references survive #
    #    (cabecoin_transactions.reason carries the legacy name on rows   #
    #    minted before phase B).                                         #
    # ------------------------------------------------------------------ #
    op.execute(
        "ALTER TABLE missions DROP CONSTRAINT IF EXISTS "
        "missions_action_type_check"
    )
    op.create_check_constraint(
        "missions_action_type_check",
        "missions",
        # ``barcode_scan`` stays admitted alongside the new
        # ``product_identification`` name so historical rows minted
        # before phase B don't violate the constraint after the rename
        # UPDATE further down. Future seed rows always use the new name.
        "action_type IN ('receipt_scan', 'label_scan', 'barcode_scan', "
        "'product_identification', 'price_compared', "
        "'fill_product_field', 'scan_distinct', 'promo_found')",
    )

    # ------------------------------------------------------------------ #
    # 2. Extend cabecoin_transactions_reason_check                       #
    #    KP-08 — reason CHECK is synced in 3 places. The frozenset       #
    #    (cab_repository.VALID_REASONS) and the ORM model tuple          #
    #    (gamification._CAB_REASONS) are updated in the same PR.         #
    # ------------------------------------------------------------------ #
    op.execute(
        "ALTER TABLE cabecoin_transactions DROP CONSTRAINT IF EXISTS "
        "cabecoin_transactions_reason_check"
    )
    # Single source of truth for the canonical reason set.
    _reason_set = (
        "'receipt_scan', 'label_scan', 'barcode_scan', "
        "'product_identification', 'fill_product_field', 'scan_distinct', "
        "'promo_found', 'mission_reward', 'battlepass_milestone', "
        "'referral', 'cashback_boost_debit', 'cashback_boost_refund', "
        "'shop_purchase', 'stonks_boost', 'mission_freeze', "
        "'food_reserve_purchase', 'streak_repair', 'challenge_milestone', "
        "'mystery_product', 'admin_adjustment', 'retro_scan'"
    )
    op.create_check_constraint(
        "cabecoin_transactions_reason_check",
        "cabecoin_transactions",
        f"reason IN ({_reason_set})",
    )

    # ------------------------------------------------------------------ #
    # 2b. Extend xp_reason_check on xp_transactions with the same set.   #
    # ------------------------------------------------------------------ #
    op.execute(
        "ALTER TABLE xp_transactions DROP CONSTRAINT IF EXISTS "
        "xp_reason_check"
    )
    _xp_reasons = (
        "'receipt_scan', 'label_scan', 'barcode_scan', "
        "'product_identification', 'fill_product_field', 'scan_distinct', "
        "'promo_found', 'price_compared', 'mission_completed', "
        "'battlepass_milestone', 'referral', 'feed_jack', "
        "'stonks_completion', 'challenge_milestone'"
    )
    op.create_check_constraint(
        "xp_reason_check",
        "xp_transactions",
        f"reason IN ({_xp_reasons})",
    )

    # ------------------------------------------------------------------ #
    # 3a. Prefix non-NULL qualifiers in the missions catalogue.          #
    #     ``category`` and ``store`` stay unprefixed — they're the       #
    #     "type tag" used by scan_distinct (the resolved value lives in  #
    #     user_missions.tracked_values).                                 #
    # ------------------------------------------------------------------ #
    op.execute(
        "UPDATE missions SET qualifier = 'attribute:organic' "
        "WHERE qualifier = 'organic'"
    )
    op.execute(
        "UPDATE missions SET qualifier = 'attribute:french' "
        "WHERE qualifier = 'french'"
    )

    # ------------------------------------------------------------------ #
    # 3b. Rename action_type='barcode_scan' → 'product_identification'   #
    #     on every catalogue row. The new CHECK accepts both names so    #
    #     this UPDATE cannot violate the constraint.                     #
    # ------------------------------------------------------------------ #
    op.execute(
        "UPDATE missions SET action_type = 'product_identification' "
        "WHERE action_type = 'barcode_scan'"
    )

    # ------------------------------------------------------------------ #
    # 4. Activate every template — phase B service code can honour them. #
    # ------------------------------------------------------------------ #
    op.execute("UPDATE missions SET is_active = TRUE WHERE is_active = FALSE")

    # ------------------------------------------------------------------ #
    # 5. ``reward_events`` table — append-only ledger that powers        #
    #    idempotent trigger_action calls.                                #
    # ------------------------------------------------------------------ #
    op.create_table(
        "reward_events",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("action_type", sa.Text(), nullable=False),
        sa.Column("qualifier", sa.Text(), nullable=True),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("idempotency_key", sa.Text(), nullable=False),
        sa.Column(
            "status",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'pending'"),
        ),
        sa.Column(
            "payload",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column(
            "processed_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "quantity > 0", name="reward_events_quantity_positive"
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'processed', 'duplicate', 'failed')",
            name="reward_events_status_check",
        ),
        sa.UniqueConstraint(
            "idempotency_key", name="uq_reward_events_idempotency_key"
        ),
    )
    op.create_index(
        "ix_reward_events_user_action",
        "reward_events",
        ["user_id", "action_type", "created_at"],
    )


def downgrade() -> None:
    # ------------------------------------------------------------------ #
    # 5. (reverse) — drop reward_events.                                  #
    # ------------------------------------------------------------------ #
    op.execute("DROP INDEX IF EXISTS ix_reward_events_user_action")
    op.drop_table("reward_events")

    # ------------------------------------------------------------------ #
    # 4. (reverse) — flip back the templates that phase A had inactive.  #
    #    Heuristic mirrors the phase A rule : only legacy action_types   #
    #    with NULL qualifier stayed active. After phase B downgrade the  #
    #    catalogue is therefore once again a phase-A-shaped catalogue.   #
    # ------------------------------------------------------------------ #
    op.execute(
        "UPDATE missions SET is_active = FALSE "
        "WHERE NOT (action_type IN "
        "('receipt_scan', 'label_scan', 'barcode_scan') "
        "AND qualifier IS NULL)"
    )

    # ------------------------------------------------------------------ #
    # 3b. (reverse) — rename product_identification back to barcode_scan #
    # ------------------------------------------------------------------ #
    op.execute(
        "UPDATE missions SET action_type = 'barcode_scan' "
        "WHERE action_type = 'product_identification'"
    )

    # ------------------------------------------------------------------ #
    # 3a. (reverse) — strip qualifier prefixes.                          #
    # ------------------------------------------------------------------ #
    op.execute(
        "UPDATE missions SET qualifier = 'organic' "
        "WHERE qualifier = 'attribute:organic'"
    )
    op.execute(
        "UPDATE missions SET qualifier = 'french' "
        "WHERE qualifier = 'attribute:french'"
    )

    # ------------------------------------------------------------------ #
    # 2b. (reverse) — restore the phase-A xp_reason_check.               #
    # ------------------------------------------------------------------ #
    op.execute(
        "ALTER TABLE xp_transactions DROP CONSTRAINT IF EXISTS "
        "xp_reason_check"
    )
    _xp_legacy = (
        "'receipt_scan', 'label_scan', 'barcode_scan', 'price_compared', "
        "'mission_completed', 'battlepass_milestone', 'referral', "
        "'feed_jack', 'stonks_completion', 'challenge_milestone'"
    )
    op.create_check_constraint(
        "xp_reason_check",
        "xp_transactions",
        f"reason IN ({_xp_legacy})",
    )

    # ------------------------------------------------------------------ #
    # 2. (reverse) — restore the phase-A reason CHECK.                   #
    # ------------------------------------------------------------------ #
    op.execute(
        "ALTER TABLE cabecoin_transactions DROP CONSTRAINT IF EXISTS "
        "cabecoin_transactions_reason_check"
    )
    _legacy_reasons = (
        "'receipt_scan', 'label_scan', 'barcode_scan', "
        "'mission_reward', 'battlepass_milestone', 'referral', "
        "'cashback_boost_debit', 'cashback_boost_refund', "
        "'shop_purchase', 'stonks_boost', 'mission_freeze', "
        "'food_reserve_purchase', 'streak_repair', 'challenge_milestone', "
        "'mystery_product', 'admin_adjustment', 'retro_scan'"
    )
    op.create_check_constraint(
        "cabecoin_transactions_reason_check",
        "cabecoin_transactions",
        f"reason IN ({_legacy_reasons})",
    )

    # ------------------------------------------------------------------ #
    # 1. (reverse) — restore the phase-A action_type CHECK.              #
    # ------------------------------------------------------------------ #
    op.execute(
        "ALTER TABLE missions DROP CONSTRAINT IF EXISTS "
        "missions_action_type_check"
    )
    op.create_check_constraint(
        "missions_action_type_check",
        "missions",
        "action_type IN ('receipt_scan', 'label_scan', 'barcode_scan', "
        "'price_compared', 'fill_product_field', 'scan_distinct', "
        "'promo_found')",
    )
