"""DB access for the product text-search endpoint (wave 4 Bug 3 + wave 9 enrich).

The search runs against ``products.name_normalized`` — a GENERATED
column (``UPPER(immutable_unaccent(name))``) indexed via GIN trigram
(``ix_products_name_normalized_trgm``). Brand-only matches piggy-back
on ``products.brands_text``. Rows with ``source='user_suggested'`` are
filtered out — forward-defence even though the products ``source_check``
constraint doesn't include that label today (it's a ``stores`` concept,
cf KP-test_search_includes_internal_source). Surfacing user-typed names
without admin validation is the risk we want to keep covered for the
day the label crosses over.

Ordering rule (wave 9 — PO « pomme de terre » duplicate disambig)
-----------------------------------------------------------------
PO directive 2026-05-13 : « j'ai une liste massive de plein de pomme
de terre et aucun moyen de les identifier précisement ». The user
needs the most informative rows on top so the secondary line of the
dropdown can actually distinguish them. New ORDER BY chain, applied
in this order :

  1. Prefix match (``name_normalized LIKE 'LAIT%'`` before substring).
  2. Branded first : ``brands IS NOT NULL`` — a brand label
     immediately discriminates duplicate names.
  3. Quantified first : ``quantity_text IS NOT NULL`` — specific
     packaging (« 1 kg ») is more useful than no quantity.
  4. French origin first : ``origins_tags @> ARRAY['en:france']`` —
     local-relevance bonus matching the Phase C-2 ``is_french_product``
     signal (see ``services.product_attributes._FRENCH_SIGNALS``).
  5. OFF source quality : ``source='off'`` before ``internal`` and the
     younger OFF-family labels (``obp`` / ``opf`` / ``opff``). OFF =
     curated catalogue ; internal = weighted-loose SKUs with minimal
     metadata.
  6. Shorter name : ``length(name) ASC`` — « Lait » beats « Crème
     dessert lait sucré » for q="lait".
  7. Stable tiebreaker : ``ean ASC``.

Each rule is encoded as a CASE-WHEN bucket so the SQL planner picks
clean (0/1) buckets and ORDER BY remains index-friendly on the leaf
``ean`` tiebreaker.

Quantity sourcing
-----------------
PO confirmed there's no plain ``products.quantity`` column. The closest
fit is ``products.quantity_text`` — added by the OFF multi-field
enrichment migration ``20260501_1000_offmf`` and populated by
``ratis_batch_off_sync`` from the raw OFF ``quantity`` field. Display
strings look like « 1 kg » / « 6 x 33 cl » / « 500 g sachet ». We
expose it as ``quantity`` in the API response — the FE doesn't care
about the internal column name and renaming-on-the-wire keeps the
shape predictable for new clients.
"""

from __future__ import annotations

import unicodedata

from sqlalchemy import text
from sqlalchemy.orm import Session


def _normalize_query(q: str) -> str:
    """Mirror the SQL ``UPPER(immutable_unaccent(...))`` GENERATED column
    in Python so we can feed positional params with the same shape as
    ``name_normalized``."""
    nfd = unicodedata.normalize("NFD", q)
    stripped = "".join(c for c in nfd if not unicodedata.combining(c))
    return stripped.upper().strip()


def search_products(db: Session, *, query: str, limit: int) -> list[dict]:
    """Run the search and return up to ``limit`` rows as a list of dicts.

    The SQL leverages the trigram GIN index for the ``LIKE '%q%'``
    fallback path (PG planner picks it for queries ≥3 chars on indexed
    text columns). For shorter queries the planner falls back to a seq
    scan but the corpus is bounded (catalogue size).

    Empty query (wave 12) — when the caller passes ``q=""`` (or
    whitespace-only), the FE wants a « default suggestions » list to
    show under the AddBar the moment the input is focused (no typing
    yet). We honour that with an alphabetic-sorted scan of the catalogue
    capped by ``limit``. Same ``user_suggested`` exclusion as the typed
    path. Ordering : ``name_normalized ASC`` so accent/case folds match
    the FE expectation.
    """
    qnorm = _normalize_query(query)
    if not qnorm:
        rows = (
            db.execute(
                text(
                    """
                SELECT
                    ean,
                    name,
                    brands,
                    quantity_text AS quantity,
                    categories_tags,
                    labels_tags,
                    origins_tags,
                    source
                FROM products
                WHERE source <> 'user_suggested'
                ORDER BY name_normalized ASC, ean ASC
                LIMIT :lim
                """
                ),
                {"lim": limit},
            )
            .mappings()
            .all()
        )
        return [
            {
                "ean": r["ean"],
                "name": r["name"],
                "brands": r["brands"],
                "quantity": r["quantity"],
                "categories_tags": r["categories_tags"],
                "labels_tags": r["labels_tags"],
                "origins_tags": r["origins_tags"],
                "source": r["source"],
            }
            for r in rows
        ]
    pattern_anywhere = f"%{qnorm}%"
    pattern_prefix = f"{qnorm}%"
    # Each rank_* column is a 0/1 bucket : LOWER value = HIGHER
    # priority. ORDER BY ascending across all of them gives the layered
    # priority chain described in the module docstring.
    rows = (
        db.execute(
            text(
                """
            SELECT
                ean,
                name,
                brands,
                quantity_text AS quantity,
                categories_tags,
                labels_tags,
                origins_tags,
                source,
                CASE
                    WHEN name_normalized LIKE :prefix THEN 0
                    ELSE 1
                END AS rank_prefix,
                CASE WHEN brands IS NOT NULL THEN 0 ELSE 1 END
                    AS rank_branded,
                CASE WHEN quantity_text IS NOT NULL THEN 0 ELSE 1 END
                    AS rank_quantified,
                CASE
                    WHEN origins_tags && ARRAY[
                        'en:france',
                        'fr:france',
                        'en:made-in-france'
                    ]::text[] THEN 0
                    ELSE 1
                END AS rank_french,
                CASE WHEN source = 'off' THEN 0 ELSE 1 END
                    AS rank_source,
                length(name) AS name_len
            FROM products
            WHERE source <> 'user_suggested'
              AND (
                  name_normalized LIKE :anywhere
                  OR UPPER(immutable_unaccent(coalesce(brands_text, ''))) LIKE :anywhere
              )
            ORDER BY
                rank_prefix ASC,
                rank_branded ASC,
                rank_quantified ASC,
                rank_french ASC,
                rank_source ASC,
                name_len ASC,
                ean ASC
            LIMIT :lim
            """
            ),
            {"anywhere": pattern_anywhere, "prefix": pattern_prefix, "lim": limit},
        )
        .mappings()
        .all()
    )
    return [
        {
            "ean": r["ean"],
            "name": r["name"],
            "brands": r["brands"],
            "quantity": r["quantity"],
            "categories_tags": r["categories_tags"],
            "labels_tags": r["labels_tags"],
            "origins_tags": r["origins_tags"],
            "source": r["source"],
        }
        for r in rows
    ]
