"""ratis_batch_savings — nightly snapshot recompute for total savings.

For every user with at least one accepted receipt-type scan, recompute
``lifetime_savings_cents`` and UPSERT into ``user_savings_snapshot``.

The hot path /account/stats then returns ``snapshot.lifetime + live_delta``,
where ``live_delta`` only covers scans accepted since ``last_computed_at``.

Reuses ``ratis_core.savings.compute_savings_for_user`` so the formula stays
in one place and cannot diverge between online and offline code.

Usage :
    uv run python batch/ratis_batch_savings/savings_batch.py            # normal
    uv run python batch/ratis_batch_savings/savings_batch.py --dry-run  # count only
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import UTC, datetime

from ratis_core.database import make_engine
from ratis_core.observability import init_sentry
from ratis_core.savings import compute_savings_for_user
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("savings_batch")

BATCH_NAME = "savings_snapshot"

# Users are recomputed in chunks committed independently — a crash at 90%
# keeps the first 90% of progress. The ON CONFLICT DO UPDATE upsert makes
# every chunk idempotent, so a re-run safely resumes.
DEFAULT_CHUNK_SIZE = 500


def recompute_all_user_snapshots(db: Session, *, dry_run: bool = False, chunk_size: int = DEFAULT_CHUNK_SIZE) -> int:
    """Recompute and upsert lifetime_savings_cents for every eligible user.

    Commits every ``chunk_size`` users so a mid-run crash keeps the chunks
    already processed (each chunk is idempotent via ON CONFLICT DO UPDATE).

    Returns the number of users processed.
    """
    user_ids = (
        db.execute(
            text(
                "SELECT DISTINCT user_id FROM scans "
                "WHERE status = 'accepted' "
                "  AND scan_type = 'receipt' "
                "  AND user_id IS NOT NULL"
            )
        )
        .scalars()
        .all()
    )

    log.info("savings_batch: %d user(s) to process", len(user_ids))

    now = datetime.now(UTC)
    count = 0
    for i in range(0, len(user_ids), chunk_size):
        chunk = user_ids[i : i + chunk_size]
        for uid in chunk:
            lifetime = compute_savings_for_user(db, uid, since=None)
            if dry_run:
                log.info("user %s → lifetime_savings_cents=%d (dry-run)", uid, lifetime)
                continue
            db.execute(
                text(
                    "INSERT INTO user_savings_snapshot "
                    "(user_id, lifetime_savings_cents, last_computed_at, updated_at) "
                    "VALUES (:uid, :v, :now, :now) "
                    "ON CONFLICT (user_id) DO UPDATE "
                    "  SET lifetime_savings_cents = EXCLUDED.lifetime_savings_cents, "
                    "      last_computed_at = EXCLUDED.last_computed_at, "
                    "      updated_at = EXCLUDED.updated_at"
                ),
                {"uid": str(uid), "v": lifetime, "now": now},
            )
            count += 1

        if not dry_run:
            db.commit()
            log.info(
                "savings_batch: chunk committed — %d/%d user(s) processed",
                count,
                len(user_ids),
            )
    return count


def _write_sync_log(Session, status: str, dry_run: bool) -> None:
    if dry_run:
        return
    with Session() as db:
        db.execute(
            text("INSERT INTO batch_sync_log (batch_name, status) VALUES (:name, :status)"),
            {"name": BATCH_NAME, "status": status},
        )
        db.commit()


def main() -> None:
    parser = argparse.ArgumentParser(description="Ratis savings snapshot batch")
    parser.add_argument("--dry-run", action="store_true", help="Log counts, do not commit")
    args = parser.parse_args()

    # Init Sentry first — silent no-op when SENTRY_DSN is absent. Any
    # exception raised during DB setup or run_batch is then captured.
    init_sentry("ratis_batch_savings")

    url = os.environ.get("DATABASE_URL")
    if not url:
        log.error("DATABASE_URL environment variable is not set")
        sys.exit(1)

    engine = make_engine(url, pool_pre_ping=True)
    Session = sessionmaker(engine)

    try:
        with Session() as db:
            count = recompute_all_user_snapshots(db, dry_run=args.dry_run)
        log.info("savings_batch: processed %d user(s)%s", count, " (dry-run)" if args.dry_run else "")
        _write_sync_log(Session, "success", args.dry_run)
    except Exception as exc:
        log.error("savings_batch FAILED: %s", exc, exc_info=True)
        try:
            _write_sync_log(Session, "failed", args.dry_run)
        except Exception:
            log.exception("Failed to write sync log")
        sys.exit(1)


if __name__ == "__main__":
    main()
