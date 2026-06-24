"""missions catalog V1 — qualifier + tracked_values + 41 templates.

Revision ID: 20260508_1000_miss_v1
Revises: 20260503_1000_uq_p2fa
Create Date: 2026-05-08 10:00:00

Phase A of the V1 missions catalogue : schema-only changes plus the seed
of the 41 templates validated by product. Phase B (service code that
honours the new ``qualifier`` filter, the ``scan_distinct`` aggregator,
the ``promo_found`` event and the ``fill_product_field`` event) ships in
a separate PR — every template that depends on phase B is seeded with
``is_active=false`` so the runtime never offers a mission it cannot
honour.

Schema changes :

1. ``missions.qualifier TEXT NULL`` — optional filter on the action
   (NULL = no filter, V0 behaviour).
2. ``user_missions.tracked_values JSONB NULL`` — bag of distinct values
   observed during the period (used by ``scan_distinct`` only).
3. ``missions_action_type_check`` is replaced to admit three new
   action_types : ``fill_product_field``, ``scan_distinct``,
   ``promo_found``.
4. ``uq_mission`` UNIQUE constraint is dropped and re-added with
   ``qualifier`` included in the natural key — two templates may now
   coexist with the same (action_type, frequency, difficulty) tuple
   when their qualifiers differ.
5. ``uq_missions_active_action_frequency`` partial unique index is
   dropped — the V1 catalogue intentionally exposes multiple active
   missions per (action_type, frequency) pair (e.g. one per difficulty
   tier), which the partial index forbids. The narrower
   ``uq_mission`` constraint (now including qualifier + difficulty)
   continues to enforce single-row uniqueness on the natural key.

Defensive pattern (R07) : every DROP guarded with ``IF EXISTS`` so the
migration is idempotent on repeat upgrade runs and survives DBs whose
constraint names diverge from the canonical schema.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic. ID kept ≤ 32 chars (R08).
revision = "20260508_1000_miss_v1"
down_revision = "20260503_1000_uq_p2fa"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ------------------------------------------------------------------ #
    # 1. New columns                                                     #
    # ------------------------------------------------------------------ #
    op.add_column(
        "missions",
        sa.Column("qualifier", sa.Text(), nullable=True),
    )
    op.add_column(
        "user_missions",
        sa.Column(
            "tracked_values",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )

    # ------------------------------------------------------------------ #
    # 2. Extend the action_type CHECK constraint                         #
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

    # ------------------------------------------------------------------ #
    # 3. Re-key the unique constraint to include qualifier               #
    # ------------------------------------------------------------------ #
    # Names differ across DB lineages (fresh CREATE-via-create_all vs
    # historical Alembic CREATE). Drop both candidates idempotently.
    op.execute("ALTER TABLE missions DROP CONSTRAINT IF EXISTS uq_mission")
    op.execute(
        "ALTER TABLE missions DROP CONSTRAINT IF EXISTS "
        "missions_action_type_frequency_difficulty_key"
    )
    # NULLS NOT DISTINCT (PG 15+) — without this clause, two rows with
    # qualifier=NULL on the same (action_type, frequency, difficulty)
    # tuple would be treated as distinct (PG default) and the constraint
    # would silently let duplicates through. The seed and runtime treat
    # NULL as a real value ("no filter"), so the constraint must too.
    op.execute(
        "ALTER TABLE missions ADD CONSTRAINT uq_mission "
        "UNIQUE NULLS NOT DISTINCT "
        "(action_type, qualifier, frequency, difficulty)"
    )

    # ------------------------------------------------------------------ #
    # 4. Drop the legacy partial unique index on (action_type, freq)     #
    # ------------------------------------------------------------------ #
    # The V0 partial index uq_missions_active_action_frequency forbade
    # more than one ACTIVE mission per (action_type, frequency) pair.
    # The V1 catalogue ships several active difficulty tiers per pair
    # (e.g. label_scan/daily/easy, /medium, /hard all is_active=true)
    # so the partial index must go. Uniqueness on the natural key is
    # still enforced by the broader uq_mission constraint above.
    op.execute("DROP INDEX IF EXISTS uq_missions_active_action_frequency")

    # ------------------------------------------------------------------ #
    # 5. Seed the 41 V1 templates                                        #
    #                                                                    #
    # Inline templates rather than importing                            #
    # ``ratis_core.seed.missions_v1`` : the canonical Python seed has    #
    # since evolved (phase B PR #325 renamed ``barcode_scan`` to         #
    # ``product_identification`` and prefixed qualifiers). Phase A must  #
    # still seed the *phase-A-shaped* catalogue so a fresh migration     #
    # chain stays consistent with its own CHECK constraint — phase B's   #
    # data migration will rename the rows in place a few minutes later.  #
    # ------------------------------------------------------------------ #
    _PHASE_A_TEMPLATES = (
        # (action_type, qualifier, frequency, difficulty, target, reward)
        ("receipt_scan", None, "daily", "easy", 1, 5),
        ("receipt_scan", None, "weekly", "easy", 3, 50),
        ("label_scan", None, "daily", "easy", 1, 5),
        ("label_scan", None, "daily", "medium", 3, 15),
        ("label_scan", None, "daily", "hard", 5, 30),
        ("label_scan", None, "weekly", "easy", 10, 50),
        ("label_scan", None, "weekly", "medium", 15, 150),
        ("label_scan", None, "weekly", "hard", 20, 300),
        ("barcode_scan", None, "daily", "easy", 1, 5),
        ("barcode_scan", None, "daily", "medium", 3, 15),
        ("barcode_scan", None, "daily", "hard", 5, 30),
        ("barcode_scan", None, "weekly", "easy", 5, 50),
        ("barcode_scan", None, "weekly", "medium", 10, 150),
        ("barcode_scan", None, "weekly", "hard", 15, 300),
        ("barcode_scan", "organic", "daily", "easy", 1, 5),
        ("barcode_scan", "organic", "weekly", "easy", 3, 50),
        ("barcode_scan", "organic", "weekly", "medium", 5, 150),
        ("barcode_scan", "french", "daily", "easy", 1, 5),
        ("barcode_scan", "french", "weekly", "easy", 3, 50),
        ("barcode_scan", "french", "weekly", "medium", 5, 150),
        ("fill_product_field", None, "daily", "easy", 2, 5),
        ("fill_product_field", None, "daily", "medium", 4, 15),
        ("fill_product_field", None, "daily", "hard", 6, 30),
        ("fill_product_field", None, "weekly", "easy", 10, 50),
        ("fill_product_field", None, "weekly", "medium", 12, 150),
        ("fill_product_field", None, "weekly", "hard", 15, 300),
        ("fill_product_field", "organic", "daily", "easy", 1, 5),
        ("fill_product_field", "organic", "weekly", "easy", 2, 50),
        ("fill_product_field", "organic", "weekly", "medium", 4, 150),
        ("scan_distinct", "category", "daily", "easy", 2, 5),
        ("scan_distinct", "category", "daily", "medium", 3, 15),
        ("scan_distinct", "category", "daily", "hard", 5, 30),
        ("scan_distinct", "category", "weekly", "easy", 5, 50),
        ("scan_distinct", "category", "weekly", "medium", 8, 150),
        ("scan_distinct", "category", "weekly", "hard", 12, 300),
        ("scan_distinct", "store", "weekly", "easy", 2, 50),
        ("scan_distinct", "store", "weekly", "medium", 3, 150),
        ("promo_found", None, "daily", "easy", 1, 5),
        ("promo_found", None, "weekly", "easy", 1, 50),
        ("promo_found", None, "weekly", "medium", 2, 150),
        ("promo_found", None, "weekly", "hard", 3, 300),
    )
    _LEGACY_ACTIVE = {"receipt_scan", "label_scan", "barcode_scan"}
    bind = op.get_bind()
    for (action_type, qualifier, frequency, difficulty,
            target_count, cab_reward) in _PHASE_A_TEMPLATES:
        is_active = (action_type in _LEGACY_ACTIVE) and (qualifier is None)
        is_boostable = action_type != "receipt_scan"
        bind.execute(
            sa.text(
                "INSERT INTO missions "
                "  (id, action_type, qualifier, frequency, difficulty, "
                "   target_count, cab_reward, is_active, is_boostable) "
                "VALUES (gen_random_uuid(), :action_type, :qualifier, "
                "        :frequency, :difficulty, "
                "        :target_count, :cab_reward, :is_active, "
                "        :is_boostable) "
                "ON CONFLICT (action_type, qualifier, frequency, difficulty) "
                "DO UPDATE SET "
                "  target_count = EXCLUDED.target_count, "
                "  cab_reward = EXCLUDED.cab_reward, "
                "  is_active = EXCLUDED.is_active, "
                "  is_boostable = EXCLUDED.is_boostable"
            ),
            {
                "action_type": action_type,
                "qualifier": qualifier,
                "frequency": frequency,
                "difficulty": difficulty,
                "target_count": target_count,
                "cab_reward": cab_reward,
                "is_active": is_active,
                "is_boostable": is_boostable,
            },
        )
    assert len(_PHASE_A_TEMPLATES) == 41, (
        f"phase A seed must hold 41 templates, got {len(_PHASE_A_TEMPLATES)}"
    )


def downgrade() -> None:
    # ------------------------------------------------------------------ #
    # 5. (reverse) — wipe the entire missions catalogue.                 #
    #                                                                    #
    # The V0 catalogue was lazy-generated by the rewards service on      #
    # first request — it never lived as a static seed in the DB. The     #
    # V1 migration injects 41 templates wholesale, and the V1 catalogue  #
    # ships several active difficulty tiers per (action_type, frequency) #
    # pair (e.g. label_scan/daily/easy, /medium, /hard all active),      #
    # which the V0 partial unique index ``uq_missions_active_action_     #
    # frequency`` (recreated below in step 4-rev) forbids. Selective     #
    # deletion would therefore leave conflicting rows behind. Truncating #
    # the whole table is the only path back to a V0-compatible state ;   #
    # the rewards service will lazy-regenerate any rows it needs.        #
    #                                                                    #
    # UserMission rows reference missions via FK RESTRICT — drop them    #
    # first.                                                              #
    # ------------------------------------------------------------------ #
    op.execute("DELETE FROM user_missions")
    op.execute("DELETE FROM missions")

    # ------------------------------------------------------------------ #
    # 4. (reverse) — recreate the partial unique index                   #
    # ------------------------------------------------------------------ #
    # Defensive : drop in case a re-up created another copy in between.
    op.execute("DROP INDEX IF EXISTS uq_missions_active_action_frequency")
    op.execute(
        "CREATE UNIQUE INDEX uq_missions_active_action_frequency "
        "ON missions (action_type, frequency) "
        "WHERE is_active = TRUE"
    )

    # ------------------------------------------------------------------ #
    # 3. (reverse) — restore the unique constraint without qualifier     #
    # ------------------------------------------------------------------ #
    op.execute("ALTER TABLE missions DROP CONSTRAINT IF EXISTS uq_mission")
    op.create_unique_constraint(
        "uq_mission",
        "missions",
        ["action_type", "frequency", "difficulty"],
    )

    # ------------------------------------------------------------------ #
    # 2. (reverse) — restore the original action_type CHECK              #
    # ------------------------------------------------------------------ #
    op.execute(
        "ALTER TABLE missions DROP CONSTRAINT IF EXISTS "
        "missions_action_type_check"
    )
    op.create_check_constraint(
        "missions_action_type_check",
        "missions",
        "action_type IN ('receipt_scan', 'label_scan', 'barcode_scan', "
        "'price_compared')",
    )

    # ------------------------------------------------------------------ #
    # 1. (reverse) — drop the new columns                                #
    # ------------------------------------------------------------------ #
    op.drop_column("user_missions", "tracked_values")
    op.drop_column("missions", "qualifier")
