"""
Annual gift-card YTD reset batch — run once a year on 1 January.

Resets ``users.gift_card_redeemed_ytd_cents = 0`` so the DAS2 annual fiscal
cap (1199 €/year) is correctly scoped to the current calendar year rather than
acting as a lifetime cap.  Without this reset, the counter set by audit H4
(``gift_card_cap_service.py``) would accumulate indefinitely and deferred
orders would never become eligible in the following year.

References: audit H4, ``ARCH_cab_economy.md`` § "Modèle de réservation H4".

Usage:
  uv run python batch/ratis_batch_annual_reset/reset.py            # normal run
  uv run python batch/ratis_batch_annual_reset/reset.py --dry-run  # log counts, no commit
"""

import argparse
import logging
import os
import sys
from datetime import UTC, datetime

from ratis_core.database import make_engine
from ratis_core.observability import init_sentry
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger("annual_reset")


def _is_january(now: datetime) -> bool:
    """Return True iff *now* falls in January (month == 1)."""
    return now.month == 1


def reset_gift_card_ytd(Session, dry_run: bool) -> int:
    """Reset ``gift_card_redeemed_ytd_cents`` to 0 for all users where it is non-zero.

    Returns the number of rows updated (or that would be updated in dry-run mode).
    """
    with Session() as db:
        if dry_run:
            result = db.execute(text("SELECT COUNT(*) FROM users WHERE gift_card_redeemed_ytd_cents <> 0"))
            count = result.scalar() or 0
            log.info("DRY-RUN — annual reset would affect %d user(s)", count)
            return count

        result = db.execute(
            text("UPDATE users SET gift_card_redeemed_ytd_cents = 0 WHERE gift_card_redeemed_ytd_cents <> 0")
        )
        count = result.rowcount
        db.commit()
        log.info("annual reset: %d user(s) reset to 0", count)
        return count


def main() -> None:
    parser = argparse.ArgumentParser(description="Annual gift-card YTD reset — run 1 Jan 00:00 UTC")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Log affected row counts without committing any changes",
    )
    args = parser.parse_args()

    # Init Sentry first — silent no-op when SENTRY_DSN is absent.
    init_sentry("ratis_batch_annual_reset")

    now = datetime.now(UTC)
    if not _is_january(now):
        log.info("not January (month=%d) — skipping annual reset", now.month)
        return

    if args.dry_run:
        log.info("DRY-RUN mode — no changes will be committed")

    url = os.environ.get("DATABASE_URL")
    if not url:
        log.error("DATABASE_URL environment variable is not set")
        sys.exit(1)

    engine = make_engine(url, pool_pre_ping=True)
    Session = sessionmaker(engine)

    try:
        reset_gift_card_ytd(Session, dry_run=args.dry_run)
    except Exception as exc:
        log.error("FAILED annual_reset: %s", exc, exc_info=True)
        sys.exit(1)

    log.info(
        "Annual reset completed successfully%s.",
        " (dry-run)" if args.dry_run else "",
    )


if __name__ == "__main__":
    main()
