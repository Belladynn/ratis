"""DB upsert for ingested products (OFF / OBP / OPF / OPFF).

The SQL is generated from _SYNC_COLS so that adding a field here
(+ extractor.py + an Alembic migration) is the only change needed.

Source isolation : the WHERE clause restricts updates to rows whose
existing source matches the active Source.name. This guarantees that
ingestion of source X never overrides rows owned by source Y (e.g. OBP
cannot overwrite an OFF-classified product, internal vrac products are
also protected as their source='internal').

Array columns (pg_type ending with "[]"):
  Python-side: each product's list is serialized to a PG array literal string
  e.g. ["en:gluten", "en:milk"] → '{"en:gluten","en:milk"}'
  SQL-side: unnest(CAST(:col AS text[]))::text[]
    — unnest gives one literal string per product row, cast restores the text[] type.
  This avoids the unnest(text[][]) pitfall where PostgreSQL flattens all dimensions.
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.orm import Session

from off_sync.sources import Source

# (column_name, postgres_type) — drives both the upsert SQL and param binding.
# To add a field: append a tuple here, update extractor.py, create migration.
_SYNC_COLS: list[tuple[str, str]] = [
    ("ean", "text"),
    ("name", "text"),
    ("photo_url", "text"),
    ("product_quantity", "numeric"),
    ("product_quantity_unit", "text"),
    ("quantity_raw", "text"),
    ("storage_type", "text"),
    ("allergens_tags", "text[]"),
    ("ingredients_tags", "text[]"),
    ("categories_tags", "text[]"),
    ("labels_tags", "text[]"),
    # Phase C-2 — origins_tags feeds the ``attribute:french`` mission
    # qualifier emit in the PA reconciliation trigger (cf
    # services.product_attributes.is_french_product).
    ("origins_tags", "text[]"),
    ("brands", "text"),
    ("photo_url_small", "text"),
    # OFF multi-field enrichment — feeds ratis_core.products.pick_display_name.
    # Note : we don't store ``product_name`` (international) as a separate column
    # because the existing ``name`` column already holds the best-of-FR/EN
    # (extractor: product_name_fr > product_name > NULL). Adding a separate
    # ``product_name`` would only duplicate the international fallback.
    ("product_name_fr", "text"),
    ("generic_name_fr", "text"),
    ("brands_text", "text"),
    ("quantity_text", "text"),
]

_ARRAY_COLS: frozenset[str] = frozenset(col for col, pg_type in _SYNC_COLS if pg_type.endswith("[]"))


def _to_pg_array_literal(items: list[str] | None) -> str:
    """Serialize a Python list to a PostgreSQL array literal string.

    Examples:
        []                          → '{}'
        ["en:gluten", "en:milk"]    → '{"en:gluten","en:milk"}'
    """
    if not items:
        return "{}"
    escaped = []
    for item in items:
        if item is None:
            continue
        item = item.replace("\\", "\\\\").replace('"', '\\"')
        escaped.append(f'"{item}"')
    return "{" + ",".join(escaped) + "}"


# Allowed sources — also enforced by the Source registry. Hardcoded here as a
# defence-in-depth check against accidental injection via a buggy caller.
_ALLOWED_SOURCE_NAMES: frozenset[str] = frozenset({"off", "obp", "opf", "opff"})


def _build_upsert_sql(source_name: str) -> str:
    if source_name not in _ALLOWED_SOURCE_NAMES:
        raise ValueError(f"Refused unknown source {source_name!r} — accepted: {sorted(_ALLOWED_SOURCE_NAMES)}")
    col_names = [c for c, _ in _SYNC_COLS]
    non_pk = [c for c in col_names if c != "ean"]

    unnest_exprs = []
    for col, pg_type in _SYNC_COLS:
        if pg_type.endswith("[]"):
            # Array column: param holds PG array literals as text[];
            # cast each literal back to the target array type.
            unnest_exprs.append("unnest(CAST(:" + col + " AS text[]))::" + pg_type)
        else:
            unnest_exprs.append("unnest(CAST(:" + col + " AS " + pg_type + "[]))")

    select_unnest = ",\n                ".join(unnest_exprs)
    insert_cols = ", ".join(col_names) + ", source"
    update_set = ",\n                ".join(c + " = EXCLUDED." + c for c in non_pk)

    # Column names sourced exclusively from _SYNC_COLS (internal constant — no user input).
    # source_name validated via _ALLOWED_SOURCE_NAMES above (defence-in-depth even though
    # only callers pass values from the Source registry).
    # nosec B608 suppresses false positives: every f-string interpolates only column names
    # from _SYNC_COLS and a whitelisted source_name — never user-supplied values.
    # ⚠️  DO NOT COPY THIS PATTERN without understanding why # nosec B608 is safe here.
    sql = f"INSERT INTO products ({insert_cols})\n"  # nosec B608
    sql += f"        SELECT\n                {select_unnest},\n"  # nosec B608
    sql += f"                '{source_name}'\n"  # nosec B608 — source_name validated
    sql += f"        ON CONFLICT (ean) DO UPDATE SET\n                {update_set},\n"  # nosec B608
    sql += "                updated_at = now()\n"
    sql += f"        WHERE products.source = '{source_name}'\n"  # nosec B608 — same
    sql += "        RETURNING (xmax = 0) AS inserted\n"
    return sql


# Build once per source on first use. Keys = source name; values = sqlalchemy `text` clauses.
_UPSERT_SQL_CACHE: dict[str, "text"] = {}


def _get_upsert_sql(source_name: str):
    if source_name not in _UPSERT_SQL_CACHE:
        _UPSERT_SQL_CACHE[source_name] = text(_build_upsert_sql(source_name))
    return _UPSERT_SQL_CACHE[source_name]


def upsert_products(
    db: Session,
    products: list[dict],
    *,
    source: Source,
) -> tuple[int, int, int]:
    """Bulk upsert products. Rows where existing source != source.name are skipped.

    Returns (inserted, updated, skipped).
    """
    if not products:
        return 0, 0, 0

    # Deduplicate by EAN — sources can emit the same EAN multiple times in one
    # page/chunk. ON CONFLICT DO UPDATE raises CardinalityViolation if the same
    # constrained value appears twice in a single INSERT. Keep the last
    # occurrence (most recent in the source).
    seen: dict[str, dict] = {}
    for p in products:
        seen[p["ean"]] = p
    products = list(seen.values())

    col_names = [c for c, _ in _SYNC_COLS]
    params: dict[str, list] = {}
    for col in col_names:
        if col in _ARRAY_COLS:
            params[col] = [_to_pg_array_literal(p[col]) for p in products]
        else:
            params[col] = [p[col] for p in products]

    rows = db.execute(_get_upsert_sql(source.name), params).fetchall()
    inserted = sum(1 for r in rows if r.inserted)
    updated = len(rows) - inserted
    skipped = len(products) - len(rows)
    return inserted, updated, skipped
