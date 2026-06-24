"""ratis_batch_sirene_sync — SIRENE INSEE -> stores sync (FR primary)."""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

from ratis_core.database import make_engine
from ratis_core.observability import init_sentry
from ratis_core.settings import load_settings
from ratis_core.startup import require_env
from sqlalchemy.orm import sessionmaker

_log = logging.getLogger(__name__)


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    require_env(
        "DATABASE_URL",
        "SIRENE_BULK_URL",
        "GEOPLATEFORME_GEOCODE_URL",
        "SIRENE_BULK_CACHE_DIR",
    )

    parser = argparse.ArgumentParser(description="ratis_batch_sirene_sync — SIRENE INSEE -> stores (FR primary).")
    parser.add_argument("--dry-run", action="store_true", help="Simulate without DB writes")
    parser.add_argument("--full", action="store_true", help="Force re-download bulk SIRENE ZIP")
    parser.add_argument(
        "--geocode-only",
        action="store_true",
        help="Only refresh geocode cache, skip upsert",
    )
    args = parser.parse_args()

    init_sentry("ratis_batch_sirene_sync")
    settings = load_settings()["sirene_sync"]
    engine = make_engine(os.environ["DATABASE_URL"])

    # Import submodules here (after sys.path already set up by entrypoint).
    from sirene_sync.download import ensure_dump
    from sirene_sync.geocode import geocode_candidates
    from sirene_sync.normalize import row_to_candidate
    from sirene_sync.parser import stream_etablissements
    from sirene_sync.upsert import upsert_candidates

    Session = sessionmaker(bind=engine)

    with Session() as db:
        dump_path = ensure_dump(
            Path(os.environ["SIRENE_BULK_CACHE_DIR"]),
            os.environ["SIRENE_BULK_URL"],
            ttl_days=settings["bulk_cache_ttl_days"],
            force=args.full,
        )

        raw_rows = stream_etablissements(
            dump_path,
            ape_whitelist=settings["ape_whitelist"],
            chunk_size=settings["batch_chunk_size"],
            include_closed=True,
        )

        # Generator pipeline: SIRENE row -> CandidateStore (lat=None) -> geocoded CandidateStore.
        normalized = (c for row in raw_rows for c in [row_to_candidate(row, db, settings=settings)] if c is not None)

        if args.geocode_only:
            _log.info("--geocode-only: running geocode pass, skipping upsert")
            # Exhaust the geocoded generator to update sirene_geocode_cache.
            geocoded = geocode_candidates(
                normalized,
                db,
                geocode_url=os.environ["GEOPLATEFORME_GEOCODE_URL"],
                min_score=settings["geocode_min_score"],
                cache_ttl_days=settings["geocode_cache_ttl_days"],
                chunk_size=settings["batch_chunk_size"],
            )
            count = sum(1 for _ in geocoded)
            if not args.dry_run:
                db.commit()
            _log.info("--geocode-only done: processed %d candidates", count)
            return 0

        geocoded = geocode_candidates(
            normalized,
            db,
            geocode_url=os.environ["GEOPLATEFORME_GEOCODE_URL"],
            min_score=settings["geocode_min_score"],
            cache_ttl_days=settings["geocode_cache_ttl_days"],
            chunk_size=settings["batch_chunk_size"],
        )

        try:
            stats = upsert_candidates(
                db,
                geocoded,
                dedup_radius_m=settings["dedup_radius_m"],
                fuzzy_threshold=settings["fuzzy_threshold"],
                dry_run=args.dry_run,
            )
        except ValueError as exc:
            _log.error("SIRENE batch aborted: %s", exc)
            try:
                import sentry_sdk  # type: ignore[import-untyped]

                sentry_sdk.capture_exception(exc)
            except ImportError:
                pass
            return 1

        if not args.dry_run:
            db.commit()

        _log.info(
            "SIRENE sync complete (dry_run=%s): inserted=%d updated=%d merged=%d preserved=%d conflicts=%d total=%d",
            args.dry_run,
            stats.inserted,
            stats.updated,
            stats.merged,
            stats.preserved,
            stats.conflicts,
            stats.total,
        )

        if stats.conflicts > 100:
            try:
                import sentry_sdk  # type: ignore[import-untyped]

                sentry_sdk.capture_message(f"SIRENE: {stats.conflicts} conflicts in run", level="warning")
            except ImportError:
                pass

    return 0


if __name__ == "__main__":
    sys.exit(main())
