"""
ratis_batch_osm_sync / osm_bulk_import — PBF-based bulk store import (DA-36).

Primary V1 path for peupler stores depuis OpenStreetMap. Le fichier PBF est
téléchargé depuis Geofabrik (`france-latest.osm.pbf`, ~4-5 GB) et streamé via
``osmium.SimpleHandler`` pour rester memory-efficient (peak RAM ~200 MB).

Pipeline :

1. Si ``--update`` : lance ``pyosmium-up-to-date`` pour appliquer les diffs
   Geofabrik depuis le dernier run (mise à jour in-place du PBF). Skip avec
   un warning si l'outil n'est pas installé.
2. Stream le PBF : pour chaque ``node`` / ``way`` avec ``tags["shop"]`` dans
   la whitelist de la config, normalise + upsert dans ``stores``
   (ON CONFLICT osm_id DO UPDATE).
3. Commit par ``batch_chunk_size`` rows (défaut 1000) — crash-safe.
4. Si ``--disable-missing`` : marque ``is_disabled=true`` toutes les stores
   OSM absentes du PBF actuel (détection fermetures).

Note (PR7 — 2026-05-31) : l'upsert délègue désormais à
``batch_shared.store_consolidation.apply_upsert`` (find_match + apply_upsert).
La logique ``--disable-missing`` reste spécifique à OSM (UPDATE en masse)
et n'est PAS dans le shared helper.

Usage :

    uv run python batch/ratis_batch_osm_sync/osm_bulk_import.py \\
        --pbf batch/ratis_batch_osm_sync/data/france-latest.osm.pbf \\
        [--update] [--dry-run] [--disable-missing]

Env vars requises : DATABASE_URL
"""

from __future__ import annotations

import argparse
import logging
import os
import shutil
import subprocess  # nosec B404 — pyosmium-up-to-date binary, path vetted via shutil.which
import sys
import time
from pathlib import Path

import osmium
from batch_shared.store_consolidation import apply_upsert
from normalize import normalize_pbf_tags, osm_composite_key_collision, osm_dict_to_candidate, upsert_city
from ratis_core.database import make_engine
from ratis_core.observability import init_sentry
from ratis_core.settings import load_settings
from ratis_core.startup import require_env
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

_log = logging.getLogger(__name__)

# Default path — file lives inside the batch dir, ignored by git (.gitignore).
DEFAULT_PBF_PATH = Path(__file__).parent / "data" / "france-latest.osm.pbf"


# ---------------------------------------------------------------------------
# PBF streaming
# ---------------------------------------------------------------------------


class _ShopHandler(osmium.SimpleHandler):
    """Streams shop nodes + ways out of a PBF, yielding normalized store dicts.

    The handler batches elements in-memory per ``chunk_size`` and calls
    ``flush_cb(batch)`` for each full chunk so the caller can commit it.
    """

    def __init__(
        self,
        shop_types: set[str],
        country_code: str,
        chunk_size: int,
        flush_cb,
        skip_null_island: bool = True,
    ) -> None:
        super().__init__()
        self._shop_types = shop_types
        self._country_code = country_code
        self._chunk_size = chunk_size
        self._flush_cb = flush_cb
        self._skip_null_island = skip_null_island
        self._batch: list[dict] = []
        self.seen_osm_ids: set[int] = set()
        self.skipped_non_shop = 0
        self.skipped_invalid = 0
        # Progress tracking — logged every N elements encountered (shops only,
        # since the fast-path returns early at the C level for non-shops).
        self._elements_seen = 0
        self._progress_every = 500
        self._start_time = time.monotonic()

    # -- internal helpers --------------------------------------------------

    def _maybe_log_progress(self) -> None:
        self._elements_seen += 1
        if self._elements_seen % self._progress_every == 0:
            elapsed = time.monotonic() - self._start_time
            rate = self._elements_seen / max(elapsed, 0.001)
            _log.info(
                "progress: %d shop elements seen, %d kept, %.0f/s elapsed %.1fs",
                self._elements_seen,
                len(self.seen_osm_ids),
                rate,
                elapsed,
            )

    def _is_interesting(self, tags: dict) -> bool:
        shop = tags.get("shop")
        return bool(shop) and shop in self._shop_types

    def _emit(self, osm_id: int, lat: float, lon: float, tags: dict, *, kind: str) -> None:
        """Emit a normalized store row, or log a structured skip with reason.

        Skip semantics — every drop path emits a single INFO line with
        ``osm_id`` + reason so prod ops can quantify import gaps. The shared
        contract for ``stores`` requires only ``name`` + valid coords ; all
        other tags are best-effort (city/postcode/address may be NULL).
        """
        if self._skip_null_island and lat == 0.0 and lon == 0.0:
            self.skipped_invalid += 1
            _log.info("osm_skip kind=%s osm_id=%s reason=null_island_coords", kind, osm_id)
            return
        if not tags.get("name"):
            self.skipped_invalid += 1
            _log.info("osm_skip kind=%s osm_id=%s reason=missing_name", kind, osm_id)
            return
        store = normalize_pbf_tags(osm_id, lat, lon, tags, self._country_code)
        if store is None:
            self.skipped_invalid += 1
            _log.info(
                "osm_skip kind=%s osm_id=%s reason=normalize_returned_none",
                kind,
                osm_id,
            )
            return
        self.seen_osm_ids.add(osm_id)
        self._batch.append(store)
        if len(self._batch) >= self._chunk_size:
            self._flush_cb(self._batch)
            self._batch = []

    # -- osmium callbacks --------------------------------------------------

    def node(self, n) -> None:
        # FAST PATH: check the 'shop' tag at C level before materializing
        # any Python dict. OSM has ~600M+ nodes in France; 99.9% are untagged
        # geometry. A full dict comprehension per node would be catastrophic.
        shop = n.tags.get("shop")
        if shop is None:
            return
        if shop not in self._shop_types:
            self.skipped_non_shop += 1
            _log.info(
                "osm_skip kind=node osm_id=%s reason=shop_type_not_in_whitelist value=%s",
                n.id,
                shop,
            )
            return
        try:
            lat = n.location.lat
            lon = n.location.lon
        except osmium.InvalidLocationError:
            self.skipped_invalid += 1
            _log.info("osm_skip kind=node osm_id=%s reason=invalid_location", n.id)
            return
        # Only now materialize the full tags dict (we know this is a shop of interest)
        tags = {t.k: t.v for t in n.tags}
        self._emit(n.id, lat, lon, tags, kind="node")
        self._maybe_log_progress()

    def way(self, w) -> None:
        # Same fast path as node(): tag check at C level first.
        shop = w.tags.get("shop")
        if shop is None:
            return
        if shop not in self._shop_types:
            self.skipped_non_shop += 1
            _log.info(
                "osm_skip kind=way osm_id=%s reason=shop_type_not_in_whitelist value=%s",
                w.id,
                shop,
            )
            return
        # Resolve geometry centroid from constituent node locations.
        # ``apply_file(..., locations=True)`` populates each NodeRef's
        # ``.location`` attribute. Some nodes may be missing if the way
        # references nodes outside the PBF — those raise InvalidLocationError
        # (or yield invalid locations). We accept partial geometry and
        # average over valid points.
        lat_sum = 0.0
        lon_sum = 0.0
        valid = 0
        for nref in w.nodes:
            try:
                loc = nref.location
                if not loc.valid():
                    continue
                lat_sum += loc.lat
                lon_sum += loc.lon
                valid += 1
            except osmium.InvalidLocationError:
                continue
        if valid == 0:
            self.skipped_invalid += 1
            _log.info(
                "osm_skip kind=way osm_id=%s reason=no_resolvable_node_locations",
                w.id,
            )
            self._maybe_log_progress()
            return
        lat = lat_sum / valid
        lon = lon_sum / valid
        tags = {t.k: t.v for t in w.tags}
        self._emit(w.id, lat, lon, tags, kind="way")
        self._maybe_log_progress()

    def flush_remainder(self) -> None:
        if self._batch:
            self._flush_cb(self._batch)
            self._batch = []


# ---------------------------------------------------------------------------
# pyosmium-up-to-date wrapper
# ---------------------------------------------------------------------------


def update_pbf(pbf_path: Path) -> None:
    """Run ``pyosmium-up-to-date`` on ``pbf_path`` to apply Geofabrik diffs.

    Skips silently (logs a warning) if the binary is not on PATH — some
    self-hosted runners may not have it yet. The caller should still be able
    to run a stale-but-present PBF in that case.
    """
    tool = shutil.which("pyosmium-up-to-date") or shutil.which("pyosmium-up-to-date.exe")
    if tool is None:
        _log.warning(
            "pyosmium-up-to-date not found on PATH — skipping diff refresh. "
            "Install via `pip install osmium` or `apt-get install pyosmium-tools`."
        )
        return

    _log.info("Running pyosmium-up-to-date on %s", pbf_path)
    cmd = [tool, str(pbf_path)]
    result = subprocess.run(cmd, check=False, capture_output=True, text=True)  # nosec B603
    if result.returncode != 0:
        _log.error(
            "pyosmium-up-to-date failed (rc=%d): %s",
            result.returncode,
            result.stderr.strip(),
        )
        raise RuntimeError(f"pyosmium-up-to-date exited with code {result.returncode}")
    _log.info("pyosmium-up-to-date finished: %s", result.stdout.strip() or "ok")


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def run_bulk_import(
    session_factory,
    cfg: dict,
    pbf_path: Path,
    *,
    dry_run: bool = False,
    disable_missing: bool = False,
) -> dict:
    """Stream ``pbf_path`` and upsert stores. Returns stats dict.

    Stats keys :
        inserted          — number of shop rows normalized + (conditionally) upserted
        skipped_non_shop  — tagged OSM elements that don't match ``shop_types``
        skipped_invalid   — shop elements with missing/invalid geometry or name
        cities_upserted   — city rows touched
        chunks_committed  — number of chunk commits (0 in dry-run)
        disabled_missing  — stores flagged is_disabled=true (disable_missing only)

    Since PR7, upsert delegates to batch_shared.store_consolidation.apply_upsert
    (find_match + apply_upsert) instead of the local normalize.upsert_store.
    The --disable-missing bulk UPDATE is OSM-specific and stays here.
    """
    pbf_path = Path(pbf_path)
    if not pbf_path.exists():
        raise FileNotFoundError(f"PBF not found: {pbf_path}")

    shop_types = set(cfg["shop_types"])
    country_code = cfg.get("country_code", "FR")
    chunk_size = int(cfg.get("batch_chunk_size", 1000))
    dedup_radius_m = int(cfg.get("dedup_radius_m", 50))
    fuzzy_threshold = float(cfg.get("fuzzy_threshold", 0.85))

    inserted = cities_upserted = chunks_committed = 0

    def _flush(batch: list[dict]) -> None:
        nonlocal inserted, cities_upserted, chunks_committed
        if not batch:
            return
        if dry_run:
            inserted += len(batch)
            return
        with session_factory() as db:
            for store in batch:
                candidate = osm_dict_to_candidate(store, db)
                if osm_composite_key_collision(db, candidate):
                    _log.warning(
                        "osm_bulk_import: unique_store collision — osm_id=%s shares "
                        "(address=%r, postal_code=%r) with an existing OSM row; "
                        "skipping",
                        candidate.osm_id,
                        candidate.address,
                        candidate.postal_code,
                    )
                    continue
                apply_upsert(
                    db,
                    candidate,
                    conflict_log=lambda msg: _log.warning("conflict: %s", msg),
                    fuzzy_radius_m=dedup_radius_m,
                    fuzzy_threshold=fuzzy_threshold,
                )
                if store["postal_code"] and store["city"]:
                    upsert_city(db, store["postal_code"], store["city"], country_code)
                    cities_upserted += 1
            db.commit()
        inserted += len(batch)
        chunks_committed += 1
        _log.info("Committed chunk of %d stores (total=%d)", len(batch), inserted)

    handler = _ShopHandler(
        shop_types=shop_types,
        country_code=country_code,
        chunk_size=chunk_size,
        flush_cb=_flush,
    )

    _log.info("Streaming PBF %s (%.1f MB)", pbf_path, pbf_path.stat().st_size / 1e6)
    # ``locations=True`` populates a node-location index so way callbacks can
    # resolve their constituent NodeRef locations and we can compute a
    # geometry centroid (DA-36 follow-up : ways were silently skipped before,
    # missing ~5-15% of stores like the Intermarché Express Courbevoie case).
    handler.apply_file(str(pbf_path), locations=True)
    handler.flush_remainder()

    disabled_missing = 0
    if disable_missing and not dry_run and handler.seen_osm_ids:
        disabled_missing = _disable_missing_stores(session_factory, handler.seen_osm_ids)

    stats = {
        "inserted": inserted,
        "skipped_non_shop": handler.skipped_non_shop,
        "skipped_invalid": handler.skipped_invalid,
        "cities_upserted": cities_upserted,
        "chunks_committed": chunks_committed,
        "disabled_missing": disabled_missing,
        "seen_osm_ids": len(handler.seen_osm_ids),
    }
    _log.info("Bulk import complete: %s", stats)
    return stats


def _disable_missing_stores(session_factory, seen_osm_ids: set[int]) -> int:
    """Mark OSM-sourced stores absent from the current PBF as is_disabled=true.

    Only touches rows where ``osm_id IS NOT NULL`` and ``NOT is_disabled`` to
    stay idempotent. ``disabled_at = NOW()`` is set in the same UPDATE.

    This bulk UPDATE is OSM-specific — the shared helper (apply_upsert) does
    not handle bulk disable operations.  It stays here intentionally.
    """
    with session_factory() as db:
        # Use a parametrized ANY(array) for large IN sets — Postgres handles
        # this efficiently and avoids the 65k bind-param limit.
        result = db.execute(
            text(
                """
                UPDATE stores
                SET is_disabled = true,
                    disabled_at = NOW()
                WHERE osm_id IS NOT NULL
                  AND NOT is_disabled
                  AND osm_id <> ALL(:seen_ids)
                """
            ),
            {"seen_ids": list(seen_osm_ids)},
        )
        db.commit()
        return result.rowcount or 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(description="Bulk import stores from an OSM PBF")
    parser.add_argument(
        "--pbf",
        type=Path,
        default=DEFAULT_PBF_PATH,
        help=f"Path to the .osm.pbf file (default: {DEFAULT_PBF_PATH})",
    )
    parser.add_argument(
        "--update",
        action="store_true",
        help="Run pyosmium-up-to-date on the PBF before the import",
    )
    parser.add_argument("--dry-run", action="store_true", help="No DB writes")
    parser.add_argument(
        "--disable-missing",
        action="store_true",
        help="Mark OSM-sourced stores absent from the PBF as is_disabled",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        stream=sys.stdout,
    )

    # Init Sentry first — silent no-op when SENTRY_DSN is absent. Any
    # exception raised during PBF parse / DB writes is then captured.
    init_sentry("ratis_batch_osm_sync")

    require_env("DATABASE_URL")
    database_url = os.environ["DATABASE_URL"]
    cfg = load_settings()["osm_sync"]

    if args.update and not args.dry_run:
        update_pbf(args.pbf)

    engine = make_engine(database_url)
    factory = sessionmaker(engine)
    try:
        run_bulk_import(
            factory,
            cfg,
            args.pbf,
            dry_run=args.dry_run,
            disable_missing=args.disable_missing,
        )
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
