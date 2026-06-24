"""Idempotent seed for the `retailers` + `retailer_aliases` tables (DA-34).

Consumes ``ratis_core/config/retailers_fr.json`` (or a caller-provided path) and
UPSERTs retailer rows, then resolves ``parent_slug`` → ``parent_id`` in a
second pass (self-referencing FK can only be filled after the parent row
exists).

Called from:

- Alembic data migration ``20260422_0945_retailers_seed`` — one-shot initial
  seed so a fresh DB is immediately usable by ``batch_osm_sync``.
- Tests (fixture).
- Future admin re-seed endpoint.

The function is purely SQL-driven (no ORM dependency on Retailer/RetailerAlias)
so it can run inside an Alembic migration where the mapped models may not be
fully registered.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.orm import Session

_log = logging.getLogger(__name__)

_DEFAULT_SEED_PATH = Path(__file__).resolve().parent.parent / "config" / "retailers_fr.json"


def _normalize_alias(alias: str) -> str:
    """Aliases are stored lowercased / whitespace-trimmed for cheap lookup."""
    return alias.strip().lower()


def seed_retailers(
    db: Session,
    seed_file: Path = _DEFAULT_SEED_PATH,
) -> dict[str, int]:
    """UPSERT retailers + aliases from the JSON seed file.

    Returns stats dict with keys ``inserted``, ``updated``, ``aliases_added``.
    ``inserted`` counts rows that did not exist before the call, ``updated``
    counts existing rows whose payload changed, ``aliases_added`` counts new
    (retailer_id, alias) pairs.
    """
    data = json.loads(Path(seed_file).read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError(f"seed file {seed_file} must contain a JSON array")

    inserted = 0
    updated = 0
    aliases_added = 0

    # ── Phase 1 : upsert retailer rows without parent_id ──────────────────────
    for entry in data:
        slug = entry["slug"]
        canonical_name = entry["canonical_name"]
        color_hex = entry.get("color_hex")
        logo_url = entry.get("logo_url")
        website = entry.get("website")
        country_code = entry.get("country_code", "FR")

        row = db.execute(
            text(
                """
                INSERT INTO retailers (
                    canonical_name, slug, color_hex, logo_url, website, country_code
                ) VALUES (
                    :canonical_name, :slug, :color_hex, :logo_url, :website, :country_code
                )
                ON CONFLICT (slug) DO UPDATE SET
                    canonical_name = EXCLUDED.canonical_name,
                    color_hex      = EXCLUDED.color_hex,
                    logo_url       = EXCLUDED.logo_url,
                    website        = EXCLUDED.website,
                    country_code   = EXCLUDED.country_code
                RETURNING (xmax = 0) AS is_insert
                """
            ),
            {
                "canonical_name": canonical_name,
                "slug": slug,
                "color_hex": color_hex,
                "logo_url": logo_url,
                "website": website,
                "country_code": country_code,
            },
        ).first()
        if row is not None:
            if row.is_insert:
                inserted += 1
            else:
                updated += 1

    # ── Phase 2 : resolve parent_slug → parent_id ─────────────────────────────
    for entry in data:
        parent_slug: str | None = entry.get("parent_slug")
        if not parent_slug:
            continue
        db.execute(
            text(
                """
                UPDATE retailers AS child
                SET parent_id = parent.id
                FROM retailers AS parent
                WHERE child.slug = :child_slug
                  AND parent.slug = :parent_slug
                  AND (child.parent_id IS DISTINCT FROM parent.id)
                """
            ),
            {"child_slug": entry["slug"], "parent_slug": parent_slug},
        )

    # ── Phase 3 : upsert aliases (ON CONFLICT DO NOTHING) ─────────────────────
    for entry in data:
        slug = entry["slug"]
        retailer_id = db.execute(
            text("SELECT id FROM retailers WHERE slug = :slug"),
            {"slug": slug},
        ).scalar()
        if retailer_id is None:
            # Should never happen if phase 1 succeeded, but fail-safe.
            continue
        for raw_alias in entry.get("aliases", []):
            alias = _normalize_alias(raw_alias)
            if not alias:
                continue
            result = db.execute(
                text(
                    """
                    INSERT INTO retailer_aliases (retailer_id, alias, source)
                    VALUES (:retailer_id, :alias, 'manual')
                    ON CONFLICT (retailer_id, alias) DO NOTHING
                    """
                ),
                {"retailer_id": retailer_id, "alias": alias},
            )
            # rowcount = 1 when actually inserted, 0 on conflict.
            if result.rowcount == 1:
                aliases_added += 1

    stats = {
        "inserted": inserted,
        "updated": updated,
        "aliases_added": aliases_added,
    }
    _log.info("seed_retailers stats: %s", stats)
    return stats
