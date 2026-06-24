"""
ratis_batch_osm_sync — Peuple stores et cities depuis OpenStreetMap Overpass API.

Étapes :
1. Charge la config depuis ratis_settings["osm_sync"]
2. Interroge l'API Overpass pour les commerces alimentaires du pays configuré
3. Pour chaque élément OSM : normalise + upsert dans stores (ON CONFLICT osm_id)
4. Upsert dans cities si addr:postcode + addr:city présents
5. Commit par chunk de batch_chunk_size

Env vars requises : DATABASE_URL, OSM_OVERPASS_URL

Usage : uv run python batch/ratis_batch_osm_sync/osm_sync.py [--dry-run]

Note : depuis DA-36, le chemin principal de peuplement est
``osm_bulk_import.py`` (streaming PBF Geofabrik). Ce module reste fonctionnel
pour les requêtes ciblées (petites zones, tests exploratoires).

Note (PR7 — 2026-05-31) : l'upsert délègue désormais à
``batch_shared.store_consolidation.apply_upsert`` (find_match + apply_upsert).
Voir ARCH_BATCH_OSM_SYNC.md et ARCH_BATCH_SIRENE_SYNC.md pour la stratégie
consolidation multi-source.
"""

import argparse
import logging
import os
import sys
from decimal import Decimal

import httpx
from batch_shared.store_consolidation import apply_upsert
from normalize import (
    build_address,
    normalize_pbf_tags,
    normalize_siret,
    osm_composite_key_collision,
    osm_dict_to_candidate,
    resolve_or_create_retailer,
    slugify,
    upsert_city,
    upsert_store,
)
from ratis_core.database import make_engine
from ratis_core.normalize import normalize_phone
from ratis_core.observability import init_sentry
from ratis_core.settings import load_settings
from ratis_core.startup import require_env
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

_log = logging.getLogger(__name__)

BATCH_NAME = "osm_sync"


# Re-exports — preserve the historical private names used by callers/tests.
_slugify = slugify


# Wrap with alias_source='osm' so callers that omit the kwarg still get the
# correct source tag on auto-created retailer_aliases rows.
def _resolve_or_create_retailer(db, brand_tag):
    return resolve_or_create_retailer(db, brand_tag, alias_source="osm")


_normalize_siret = normalize_siret
_upsert_store = upsert_store
_upsert_city = upsert_city


def _build_overpass_query(cfg: dict) -> str:
    shop_types = "|".join(cfg["shop_types"])
    timeout = cfg["overpass_timeout"]
    country = cfg["country_code"]
    return (
        f"[out:json][timeout:{timeout}];\n"
        f'area["ISO3166-1"="{country}"][admin_level=2] -> .country;\n'
        f"(\n"
        f'  node["shop"~"{shop_types}"]["name"](area.country);\n'
        f'  way["shop"~"{shop_types}"]["name"](area.country);\n'
        f");\n"
        f"out center;\n"
    )


def fetch_osm_elements(overpass_url: str, cfg: dict) -> list[dict]:
    """POST the Overpass QL query and return the list of elements."""
    query = _build_overpass_query(cfg)
    timeout = cfg["overpass_timeout"] + 30
    with httpx.Client(timeout=timeout) as client:
        resp = client.post(overpass_url, data={"data": query})
        resp.raise_for_status()
    return resp.json()["elements"]


def _get_lat_lng(element: dict) -> tuple[Decimal | None, Decimal | None]:
    """Return (lat, lng) as Decimal, or (None, None) if not available."""
    if element["type"] == "node":
        lat = element.get("lat")
        lon = element.get("lon")
    elif element["type"] == "way":
        center = element.get("center", {})
        lat = center.get("lat")
        lon = center.get("lon")
    else:
        return None, None
    if lat is None or lon is None:
        return None, None
    return Decimal(str(lat)), Decimal(str(lon))


def _normalize_osm_element(element: dict, country_code: str) -> dict | None:
    """
    Map an Overpass JSON element to a stores row dict.
    Returns None if name or coords are missing.

    Thin wrapper on top of ``normalize.normalize_pbf_tags`` so both the
    Overpass and PBF paths share the exact same mapping rules.
    """
    tags = element.get("tags", {})
    lat, lng = _get_lat_lng(element)
    lat_f = float(lat) if lat is not None else None
    lng_f = float(lng) if lng is not None else None
    # Overpass handles phone normalization in normalize_pbf_tags already —
    # but the historical _normalize_osm_element kept the exact same phone
    # fallback chain, so delegate.
    _ = normalize_phone  # imported for backwards-compat re-exports
    _ = build_address
    return normalize_pbf_tags(element.get("id"), lat_f, lng_f, tags, country_code)


def run_batch(
    session_factory,
    cfg: dict,
    overpass_url: str,
    dry_run: bool = False,
) -> dict:
    """
    Fetch OSM elements and upsert into stores + cities.
    Returns stats dict: {inserted, skipped, cities_upserted}.
    Commits every batch_chunk_size rows for crash safety.

    Since PR7, upsert delegates to batch_shared.store_consolidation.apply_upsert
    (find_match + apply_upsert) instead of the local normalize.upsert_store.
    """
    country_code = cfg.get("country_code", "FR")
    chunk_size = cfg.get("batch_chunk_size", 500)
    dedup_radius_m = int(cfg.get("dedup_radius_m", 50))
    fuzzy_threshold = float(cfg.get("fuzzy_threshold", 0.85))

    elements = fetch_osm_elements(overpass_url, cfg)
    _log.info("Fetched %d OSM elements", len(elements))

    inserted = skipped = cities_upserted = 0

    for i in range(0, len(elements), chunk_size):
        chunk = elements[i : i + chunk_size]
        with session_factory() as db:
            for element in chunk:
                store_data = _normalize_osm_element(element, country_code)
                if store_data is None:
                    skipped += 1
                    continue

                if not dry_run:
                    candidate = osm_dict_to_candidate(store_data, db)
                    if osm_composite_key_collision(db, candidate):
                        _log.warning(
                            "osm_sync: unique_store collision — osm_id=%s shares "
                            "(retailer=%r, address=%r, postal_code=%r) with an "
                            "existing OSM row; skipping",
                            candidate.osm_id,
                            store_data.get("retailer"),
                            candidate.address,
                            candidate.postal_code,
                        )
                    else:
                        apply_upsert(
                            db,
                            candidate,
                            conflict_log=lambda msg: _log.warning("conflict: %s", msg),
                            fuzzy_radius_m=dedup_radius_m,
                            fuzzy_threshold=fuzzy_threshold,
                        )
                    if store_data["postal_code"] and store_data["city"]:
                        upsert_city(
                            db,
                            store_data["postal_code"],
                            store_data["city"],
                            country_code,
                        )
                        cities_upserted += 1

                inserted += 1

            if not dry_run:
                db.commit()

        _log.info(
            "Chunk %d/%d processed — inserted=%d skipped=%d",
            i // chunk_size + 1,
            -(-len(elements) // chunk_size),
            inserted,
            skipped,
        )

    stats = {"inserted": inserted, "skipped": skipped, "cities_upserted": cities_upserted}
    _log.info("Batch complete: %s", stats)
    return stats


def _write_sync_log(session_factory, status: str, rows_affected: int | None, dry_run: bool) -> None:
    """Record one batch_sync_log row for this run.

    Mirrors the pattern used by off_sync / consensus / purge so that a crash
    mid-run leaves a traceable {status, rows} entry. No-op in dry-run mode.
    """
    if dry_run:
        return
    with session_factory() as db:
        db.execute(
            text("INSERT INTO batch_sync_log (batch_name, status, rows_affected) VALUES (:name, :status, :rows)"),
            {"name": BATCH_NAME, "status": status, "rows": rows_affected},
        )
        db.commit()


def run_and_log(
    session_factory,
    cfg: dict,
    overpass_url: str,
    dry_run: bool = False,
) -> dict:
    """Run the batch and always write a batch_sync_log entry.

    On success → status='success' with rows_affected = inserted count.
    On crash   → status='failed' (best-effort) then the exception is
    re-raised so the process exits non-zero.
    """
    try:
        stats = run_batch(session_factory, cfg, overpass_url, dry_run=dry_run)
    except Exception:
        try:
            _write_sync_log(session_factory, "failed", None, dry_run)
        except Exception:
            _log.exception("Failed to write sync log after batch crash")
        raise
    _write_sync_log(session_factory, "success", stats["inserted"], dry_run)
    return stats


def main() -> None:
    parser = argparse.ArgumentParser(description="Sync stores from OpenStreetMap (Overpass)")
    parser.add_argument("--dry-run", action="store_true", help="No DB writes")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        stream=sys.stdout,
    )

    # Init Sentry first — silent no-op when SENTRY_DSN is absent. Any
    # exception raised during DB/HTTP work is then captured.
    init_sentry("ratis_batch_osm_sync")

    # require_env() validates presence + raises if missing, but returns None —
    # read the values from os.environ after validation (CLAUDE.md convention).
    require_env("DATABASE_URL", "OSM_OVERPASS_URL")
    database_url = os.environ["DATABASE_URL"]
    overpass_url = os.environ["OSM_OVERPASS_URL"]
    cfg = load_settings()["osm_sync"]

    engine = make_engine(database_url)
    factory = sessionmaker(engine)

    try:
        run_and_log(factory, cfg, overpass_url, dry_run=args.dry_run)
    except Exception:
        sys.exit(1)
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
