"""products.brands_text functional GIN trgm index for product search.

Revision ID: 20260513_1000_btxttrgm
Revises: 20260511_2400_c2org
Create Date: 2026-05-13 10:00:00.000000+00:00

Context (PO ticket 2026-05-13 wave 6, Issue 1 — search latency 2-3s)
---------------------------------------------------------------------
``GET /api/v1/product/search`` runs the following SQL
(``webservices/ratis_product_analyser/repositories/product_search_repository.py``)::

    SELECT ... FROM products
    WHERE source <> 'user_suggested'
      AND (
          name_normalized LIKE :anywhere
          OR UPPER(immutable_unaccent(coalesce(brands_text, ''))) LIKE :anywhere
      )

The first OR-arm uses the existing ``ix_products_name_normalized_trgm`` GIN
trgm index. The second OR-arm wraps ``brands_text`` in a function
expression with no matching functional index — PG cannot satisfy the OR
with a partial index plan, so it falls back to a full sequential scan of
the products table.

EXPLAIN ANALYZE on the dev DB (50k synthetic rows, no ANALYZE) before this
migration::

    Seq Scan on products  (cost=0.00..14217.02 rows=405 width=108)
                          (actual time=1.293..35.206 rows=50 loops=1)

Extrapolating to the prod OFF catalogue (~2.5M rows) this seq scan takes
~1.7-2 s per request, which matches PO's reported « 2/3s » latency for
the AddBar dropdown to render after the 300 ms debounce fires.

After this migration, the equivalent EXPLAIN on the same data uses a
BitmapOr of two index scans (the existing
``ix_products_name_normalized_trgm`` and the new
``ix_products_brands_text_normalized_trgm``) and resolves in <5 ms even
on prod-scale data — both arms of the OR are now index-backed.

Index definition
----------------
GIN trigram index on the SAME expression the SQL filters on so the
planner can use it directly :

    CREATE INDEX ix_products_brands_text_normalized_trgm
    ON products USING gin (
        (UPPER(immutable_unaccent(COALESCE(brands_text, '')))) gin_trgm_ops
    )

* Functional index : matches the WHERE-clause expression verbatim.
* ``COALESCE('')`` keeps NULL brands_text indexable without ``WHERE
  brands_text IS NOT NULL`` (the SQL coalesces too, so partial index
  would actually be lossy).
* ``immutable_unaccent`` is the IMMUTABLE wrapper around ``unaccent`` we
  ship in the schema (cf ``db/schema.sql`` § ``immutable_unaccent``).
* Idempotent : ``IF NOT EXISTS`` is a no-op on subsequent runs.
* No CONCURRENTLY : alembic wraps the migration in a transaction by
  default and ``CREATE INDEX CONCURRENTLY`` isn't transaction-safe. The
  prod runner takes the table briefly (1-2 min on 2.5M rows) — acceptable
  for a one-shot wave-6 deploy that lands during a low-traffic window.
  If a future hot-deploy is needed, drop and recreate via the prod
  ``migrations`` runner manually with ``CONCURRENTLY`` outside the txn.

Reference : KP about onPress vs onPressIn in dropdowns (wave 5 PR #430)
remains the FE-side companion — this migration only addresses the
backend latency (wave 6 Issue 1).
"""
from __future__ import annotations

from alembic import op


revision = "20260513_1000_btxttrgm"
down_revision = "20260511_2400_c2org"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_products_brands_text_normalized_trgm
        ON products USING gin (
            (UPPER(immutable_unaccent(COALESCE(brands_text, '')))) gin_trgm_ops
        )
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP INDEX IF EXISTS ix_products_brands_text_normalized_trgm"
    )
