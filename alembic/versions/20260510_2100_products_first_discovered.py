"""products_first_discovered

Revision ID: 20260510_2100_pfd
Revises: 20260510_1100_ach_unlock_rsn
Create Date: 2026-05-10 21:00:00.000000

V1.1 follow-up #1 — KP-75 / DP-achievements-v1-followups item 1.

Adds ``products.first_discovered_by_user_id UUID NULL`` (FK
``users(id) ON DELETE SET NULL``) so that the achievement handler
``_eval_unique_products_discovered_count`` can answer "is this user the
first one on Ratis to have scanned this EAN ?" in O(1).

Schema additions
----------------
1. ``products.first_discovered_by_user_id UUID NULL`` (no default).
2. FK ``fk_products_first_discovered`` → ``users(id)`` ON DELETE SET NULL.
   SET NULL (not RESTRICT) is correct here : when a user is hard-deleted
   the historical "first discovery" attribution is forgotten, the row
   keeps existing for everyone else.
3. Partial index ``idx_products_first_discovered`` on the column,
   ``WHERE first_discovered_by_user_id IS NOT NULL`` — the achievement
   handler only ever filters on a user_id, so a partial index is both
   smaller (most products are OFF-seeded with NULL discoverer) and
   keeps the count query plan tight.

Backfill
--------
For every existing accepted/matched scan from a non-banned, non-deleted
user, attribute the product to the EARLIEST such scanner. ``DISTINCT
ON (s.product_ean)`` + ``ORDER BY s.product_ean, s.scanned_at ASC``
gives one row per product = the first eligible discoverer.

Notes :
* ``scans.product_ean`` is the FK to ``products.ean`` (text) — NOT
  ``product_id`` as the brief suggested. Adapted accordingly.
* Status filter mirrors ``_ACCEPTED_SCAN_STATUSES`` from
  ``achievement_service`` (``matched`` for v3 + ``accepted`` for legacy
  v2 — both still appear in production data).
* Banned / deleted users are excluded so they cannot retroactively
  steal "first discoverer" credit (mirrors the dispatcher's anti-ban
  guard in ``check_achievements``).
* Backfill is wrapped in a single statement — atomic, no batching
  needed at current data volume (Mac mini dev = O(thousands) rows ;
  prod V1 launch = comparable scale).

KP-42 audit (per SA_DEV pitfalls) : the UPDATE matches only rows where
``products.id`` (well, ``ean``) appears in the eligible-scans subquery.
Products with no eligible scan stay NULL — semantically correct, no
risk of clobbering manually-curated state (no such state exists for
this brand-new column).

Downgrade is a clean DROP INDEX → DROP CONSTRAINT → DROP COLUMN.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers (≤32 chars per R08).
revision = "20260510_2100_pfd"
down_revision = "20260510_1100_ach_unlock_rsn"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Add the column (NULL allowed — historical products have no
    # known discoverer until the backfill below assigns them one).
    op.add_column(
        "products",
        sa.Column(
            "first_discovered_by_user_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
    )

    # 2. Foreign key — SET NULL on user delete (RGPD § "DELETE /account
    # → in-place anonymize + PII-comportemental delete" : the row stays
    # but the attribution disappears).
    op.create_foreign_key(
        "fk_products_first_discovered",
        "products",
        "users",
        ["first_discovered_by_user_id"],
        ["id"],
        ondelete="SET NULL",
    )

    # 3. Partial index — most rows stay NULL forever (OFF-seeded
    # products never scanned). Indexing only the populated subset
    # keeps the index tiny and the achievement handler fast.
    op.create_index(
        "idx_products_first_discovered",
        "products",
        ["first_discovered_by_user_id"],
        postgresql_where=sa.text("first_discovered_by_user_id IS NOT NULL"),
    )

    # 4. Backfill — for each product, attribute it to the earliest
    # eligible scanner (accepted/matched scan from a non-banned,
    # non-deleted user). DISTINCT ON keeps only the first row per
    # product_ean given the ORDER BY.
    op.execute(
        """
        UPDATE products p
        SET first_discovered_by_user_id = subq.user_id
        FROM (
            SELECT DISTINCT ON (s.product_ean)
                s.product_ean AS product_ean,
                s.user_id     AS user_id
            FROM scans s
            JOIN users u ON u.id = s.user_id
            WHERE s.status IN ('matched', 'accepted')
              AND s.product_ean IS NOT NULL
              AND s.user_id IS NOT NULL
              AND u.is_deleted = false
              AND u.is_shadow_banned = false
            ORDER BY s.product_ean, s.scanned_at ASC
        ) AS subq
        WHERE p.ean = subq.product_ean
        """
    )


def downgrade() -> None:
    # Reverse order : index → FK → column. ``IF EXISTS`` per R07.
    op.execute("DROP INDEX IF EXISTS idx_products_first_discovered")
    op.execute(
        "ALTER TABLE products DROP CONSTRAINT IF EXISTS "
        "fk_products_first_discovered"
    )
    op.drop_column("products", "first_discovered_by_user_id")
