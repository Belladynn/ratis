"""widen products.source CHECK + off_no_unit for OBP/OPF/OPFF

Revision ID: 20260511_0900_obp_opf
Revises: 20260510_2200_sirene_schema
Create Date: 2026-05-11 09:00:00.000000

OBP/OPF/OPFF integration — PR2 prereq (cf.
``docs/superpowers/plans/2026-05-10-obp-opf-impl.md`` § PR2 + DP entry
``DP-obp-opf-impl-check-constraints``).

Schema changes
--------------
1. **``products.source_check`` widened** : the original enum was
   ``('off', 'internal')`` (migration ``20250401_0000_0001_initial_schema``).
   The multi-source batch (``batch/ratis_batch_off_sync`` since PR #379)
   needs to write rows with ``source IN ('obp', 'opf', 'opff')`` too, in
   addition to the existing ``'off'`` and ``'internal'``. The CHECK
   constraint is widened to accept all five values.

2. **``products.off_no_unit`` widened** : the original constraint
   ``source != 'off' OR unit IS NULL`` enforced that OFF rows must have
   NULL ``unit`` (OFF only carries packaged products with EAN, no per-kg
   vrac). The new Open*Facts sources (OBP cosmetics, OPF generic
   non-food, OPFF pet food) share the same characteristic — they are all
   packaged products keyed by EAN, no per-unit vrac. The constraint is
   widened to enforce NULL unit for all four catalogue sources, leaving
   ``internal`` (vrac) as the only source that may set ``unit``.

   Renamed to ``catalogue_no_unit`` to reflect its broader scope.

Backfill / data risk (KP-42 audit)
----------------------------------
None. Both changes are *widenings* — every value previously accepted is
still accepted. No existing row can violate the new constraints.

The current production data only contains ``source IN ('off',
'internal')`` because the batch hasn't been run with ``--source
obp/opf/opff`` yet. After this migration deploys, PR2's cron will be
allowed to insert OBP rows.

CONCURRENTLY note
-----------------
``ALTER TABLE ... ADD CONSTRAINT`` takes an ACCESS EXCLUSIVE lock but
the validation is a fast table scan on a small table (V1 has <10M
products). Acceptable.

Downgrade
---------
Restore the original two-value enum + ``off_no_unit`` name. Will fail
if any non-``off``/``internal`` row exists — operator must purge those
rows first (``DELETE FROM products WHERE source IN ('obp','opf','opff')``).
"""

from __future__ import annotations

from alembic import op


# revision identifiers (≤32 chars per R-DB-08).
revision = "20260511_0900_obp_opf"
down_revision = "20260510_2200_sirene_schema"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Widen products.source_check to allow the three new Open*Facts
    # sources. IF EXISTS guard per R-DB-07 — original constraint comes
    # from migration ``20250401_0000_0001_initial_schema``.
    op.execute("ALTER TABLE products DROP CONSTRAINT IF EXISTS source_check")
    op.create_check_constraint(
        "source_check",
        "products",
        "source IN ('off', 'obp', 'opf', 'opff', 'internal')",
    )

    # 2. Replace off_no_unit with catalogue_no_unit covering all four
    # external catalogue sources (OFF/OBP/OPF/OPFF). They are all
    # packaged products with EAN — none should have ``unit`` set.
    op.execute("ALTER TABLE products DROP CONSTRAINT IF EXISTS off_no_unit")
    op.execute("ALTER TABLE products DROP CONSTRAINT IF EXISTS catalogue_no_unit")
    op.create_check_constraint(
        "catalogue_no_unit",
        "products",
        "source NOT IN ('off', 'obp', 'opf', 'opff') OR unit IS NULL",
    )


def downgrade() -> None:
    # Restore the original two-value enum. Will fail loudly if any
    # OBP/OPF/OPFF row exists — that's the desired behaviour (operator
    # has to choose how to handle stale catalogue rows before reverting).
    op.execute("ALTER TABLE products DROP CONSTRAINT IF EXISTS catalogue_no_unit")
    op.create_check_constraint(
        "off_no_unit",
        "products",
        "source != 'off' OR unit IS NULL",
    )

    op.execute("ALTER TABLE products DROP CONSTRAINT IF EXISTS source_check")
    op.create_check_constraint(
        "source_check",
        "products",
        "source IN ('off', 'internal')",
    )
