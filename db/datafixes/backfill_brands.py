"""
Backfill brands table from products.brands (text field from OFF).

Usage:
    DATABASE_URL=postgresql+psycopg://ratis:ratis@localhost:5432/ratis_dev \  # pragma: allowlist secret\
        uv run python db/datafixes/backfill_brands.py [--dry-run]

What it does:
    1. Reads all distinct products.brands values (text, often comma/semicolon-separated)
    2. Splits and normalises each part into a slug
    3. Inserts brands rows (ON CONFLICT DO NOTHING — idempotent)
    4. Links products.brand_id to the first (primary) brand in the list
       by exact slug match — products already linked are skipped

What it does NOT do:
    - Fuzzy matching (ambiguous cases need manual review)
    - Overwrite existing brand_id links (idempotent)

Run as many times as needed — safe.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import unicodedata

import psycopg

# ---------------------------------------------------------------------------
# Slug helpers
# ---------------------------------------------------------------------------


def slugify(value: str) -> str:
    """Normalise a brand name into a URL-safe slug.

    Examples:
        'Danone'         → 'danone'
        'Yoplait Bio'    → 'yoplait-bio'
        'Nestlé'         → 'nestle'
        'U Bio & Malin'  → 'u-bio-malin'
    """
    # Unicode decomposition — strips accents
    value = unicodedata.normalize("NFKD", value)
    value = value.encode("ascii", "ignore").decode("ascii")
    value = value.lower().strip()
    # Replace any run of non-alphanumeric chars with a single hyphen
    value = re.sub(r"[^a-z0-9]+", "-", value)
    value = value.strip("-")
    return value


def split_brands(raw: str) -> list[str]:
    """Split a raw brands string into individual brand names.

    OFF uses commas as separator; some sources use semicolons.
    Filters out empty strings after split.
    """
    parts = re.split(r"[,;]+", raw)
    return [p.strip() for p in parts if p.strip()]


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def run(db_url: str, dry_run: bool) -> None:
    conn = psycopg.connect(db_url, autocommit=False)

    try:
        with conn.cursor() as cur:
            # ------------------------------------------------------------------
            # 1. Collect all distinct brands text values (non-null)
            # ------------------------------------------------------------------
            cur.execute("SELECT ean, brands FROM products WHERE brands IS NOT NULL AND brands != ''")
            rows = cur.fetchall()
            print(f"Products with brands text: {len(rows)}")

            # Build: slug → canonical name (first occurrence wins)
            slug_to_name: dict[str, str] = {}
            # ean → first slug (primary brand)
            ean_to_primary_slug: dict[str, str] = {}

            for ean, brands_raw in rows:
                parts = split_brands(brands_raw)
                if not parts:
                    continue
                slugs = [slugify(p) for p in parts]
                slugs = [s for s in slugs if s]  # drop empty slugs
                if not slugs:
                    continue

                for name, slug in zip(parts, slugs, strict=False):
                    if slug not in slug_to_name:
                        slug_to_name[slug] = name  # first occurrence = canonical name

                ean_to_primary_slug[ean] = slugs[0]

            print(f"Distinct brand slugs found: {len(slug_to_name)}")

            # ------------------------------------------------------------------
            # 2. Insert brands (idempotent)
            # ------------------------------------------------------------------
            inserted = 0
            skipped = 0
            for slug, name in sorted(slug_to_name.items()):
                if dry_run:
                    inserted += 1
                    continue
                cur.execute(
                    """
                    INSERT INTO brands (name, slug)
                    VALUES (%s, %s)
                    ON CONFLICT (slug) DO NOTHING
                    """,
                    (name, slug),
                )
                if cur.rowcount == 1:
                    inserted += 1
                else:
                    skipped += 1

            print(f"Brands inserted: {inserted}  |  already existed (skipped): {skipped}")

            # ------------------------------------------------------------------
            # 3. Build slug → brand_id map
            # ------------------------------------------------------------------
            if not dry_run:
                cur.execute("SELECT id, slug FROM brands")
                slug_to_id = {slug: brand_id for brand_id, slug in cur.fetchall()}
            else:
                slug_to_id = {}

            # ------------------------------------------------------------------
            # 4. Link products.brand_id (only products without a brand_id)
            # ------------------------------------------------------------------
            linked = 0
            unmatched_slugs: set[str] = set()

            for ean, primary_slug in ean_to_primary_slug.items():
                if dry_run:
                    linked += 1
                    continue
                brand_id = slug_to_id.get(primary_slug)
                if brand_id is None:
                    unmatched_slugs.add(primary_slug)
                    continue
                cur.execute(
                    """
                    UPDATE products
                    SET brand_id = %s
                    WHERE ean = %s AND brand_id IS NULL
                    """,
                    (brand_id, ean),
                )
                if cur.rowcount == 1:
                    linked += 1

            print(f"Products linked to brand: {linked}")

            if unmatched_slugs:
                print(
                    f"WARNING: {len(unmatched_slugs)} slug(s) had no brand_id match "
                    f"(should not happen — investigate): {sorted(unmatched_slugs)[:10]}"
                )

            # ------------------------------------------------------------------
            # 5. Report products still without brand_id
            # ------------------------------------------------------------------
            if not dry_run:
                cur.execute(
                    "SELECT COUNT(*) FROM products WHERE brand_id IS NULL AND brands IS NOT NULL AND brands != ''"
                )
                remaining = cur.fetchone()[0]
                if remaining:
                    print(
                        f"Products with brands text but no brand_id after backfill: {remaining} "
                        f"(slugification failed or slug collision)"
                    )

                cur.execute("SELECT COUNT(*) FROM products WHERE brand_id IS NULL")
                total_unlinked = cur.fetchone()[0]
                print(f"Total products without brand_id (incl. no brands text): {total_unlinked}")

            # ------------------------------------------------------------------
            # 6. Commit or rollback
            # ------------------------------------------------------------------
            if dry_run:
                print("\n[DRY RUN] No changes committed.")
                conn.rollback()
            else:
                conn.commit()
                print("\nDone. Changes committed.")

    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Backfill brands from products.brands text")
    parser.add_argument("--dry-run", action="store_true", help="Report only, no DB writes")
    args = parser.parse_args()

    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        print("ERROR: DATABASE_URL is not set", file=sys.stderr)
        sys.exit(1)

    # psycopg v3 uses postgresql+psycopg:// in SQLAlchemy but plain postgresql:// natively
    db_url = db_url.replace("postgresql+psycopg://", "postgresql://")

    run(db_url, dry_run=args.dry_run)
