"""ratis_batch_vrac_seed — one-shot seed of generic bulk-produce products.

Inserts ~65 canonical French bulk-produce entries into ``products`` so the
fuzzy matcher has anchors when an OCR'd receipt line is a vrac (no EAN, just
a name like ``POMMES VRAC 1.234kg 3.45€``).

Idempotent : every INSERT uses ``ON CONFLICT (ean) DO NOTHING``. Re-running
the script after the first successful run is a no-op.

Usage :
    uv run python batch/ratis_batch_vrac_seed/vrac_seed.py            # commit
    uv run python batch/ratis_batch_vrac_seed/vrac_seed.py --dry-run  # log only

Exit codes :
    0 — success (whether rows were inserted or already present)
    1 — error (missing DATABASE_URL, DB unreachable, constraint violation)
"""

from __future__ import annotations

import argparse
import logging
import os
import sys

from ratis_core.database import make_engine
from ratis_core.observability import init_sentry
from seed_data import SEED_DATA, VracEntry
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("vrac_seed")

BATCH_NAME = "vrac_seed"


def seed_products(db: Session, entries: list[VracEntry], *, dry_run: bool = False) -> dict[str, int]:
    """Insert each ``entries`` row with ON CONFLICT DO NOTHING.

    Returns ``{"inserted": N, "skipped": M}``. ``skipped`` covers EANs
    that already exist (the conflict path). When ``dry_run`` is True we
    still execute the SELECT-style probe but never write — counts reflect
    what *would* happen.
    """
    inserted = 0
    skipped = 0

    for entry in entries:
        if dry_run:
            existing = db.execute(
                text("SELECT 1 FROM products WHERE ean = :ean"),
                {"ean": entry["ean"]},
            ).scalar()
            if existing:
                skipped += 1
            else:
                inserted += 1
            continue

        result = db.execute(
            text(
                "INSERT INTO products (ean, name, source, unit) "
                "VALUES (:ean, :name, 'internal', :unit) "
                "ON CONFLICT (ean) DO NOTHING"
            ),
            {"ean": entry["ean"], "name": entry["name"], "unit": entry["unit"]},
        )
        if result.rowcount == 1:
            inserted += 1
        else:
            skipped += 1

    return {"inserted": inserted, "skipped": skipped}


def _write_sync_log(db: Session, status: str, rows_affected: int) -> None:
    db.execute(
        text("INSERT INTO batch_sync_log (batch_name, status, rows_affected) VALUES (:name, :status, :rows)"),
        {"name": BATCH_NAME, "status": status, "rows": rows_affected},
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Ratis vrac seed batch (one-shot)")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Log what would be inserted without committing to the database",
    )
    args = parser.parse_args()

    # Init Sentry first — silent no-op when SENTRY_DSN is absent. Any
    # exception raised during the seed run is then captured.
    init_sentry("ratis_batch_vrac_seed")

    if args.dry_run:
        log.info("DRY-RUN mode — no changes will be committed")

    url = os.environ.get("DATABASE_URL")
    if not url:
        log.error("DATABASE_URL environment variable is not set")
        return 1

    engine = make_engine(url, pool_pre_ping=True)
    session_factory = sessionmaker(engine)

    log.info("Seeding %d vrac entries", len(SEED_DATA))

    try:
        with session_factory() as db:
            stats = seed_products(db, SEED_DATA, dry_run=args.dry_run)
            if not args.dry_run:
                _write_sync_log(db, "success", stats["inserted"])
                db.commit()
    except Exception:
        log.exception("vrac seed batch failed")
        try:
            with session_factory() as db:
                _write_sync_log(db, "failed", 0)
                db.commit()
        except Exception:
            log.exception("Failed to write failure sync log")
        return 1

    log.info(
        "vrac seed complete : %d inserted, %d skipped (already present)%s",
        stats["inserted"],
        stats["skipped"],
        " — DRY-RUN" if args.dry_run else "",
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
