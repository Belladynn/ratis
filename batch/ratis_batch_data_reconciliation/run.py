"""ratis_batch_data_reconciliation — entry point.

Orchestrates the 4 jobs sequentially with try/except per-job — if one
job raises, the others still run. Each job is given its own DB session
(scope = the job's runtime) so a transaction failure stays isolated.

Phase 1 ships :
  Job 1 ``ean_recovery``      — Bloc I NRC retry-match
  Job 2 ``store_mdd_vote``    — STUB (Phase 2)
  Job 3 ``price_disambiguate``— STUB (Phase 2)
  Job 4 ``retro_cab``         — retroactive CAB credit + notif

Usage :
  uv run python batch/ratis_batch_data_reconciliation/run.py            # normal
  uv run python batch/ratis_batch_data_reconciliation/run.py --dry-run  # no writes

Env vars : DATABASE_URL · NOTIFIER_URL (Job 4) · INTERNAL_API_KEY (Job 4).
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env.local")

from data_reconciliation.ean_recovery import reconcile_ean_recovery
from data_reconciliation.price_disambiguate import reconcile_price_disambiguate
from data_reconciliation.retro_cab import reconcile_retro_cab
from data_reconciliation.store_mdd_vote import reconcile_store_mdd_vote
from ratis_core.database import make_engine
from ratis_core.observability import init_sentry
from sqlalchemy.orm import sessionmaker

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
log = logging.getLogger("data_reconciliation")


# Ordered list of (job_name, job_fn). Order matters : retro_cab reads the
# scans newly resolved by the upstream jobs.
JOBS = [
    ("ean_recovery", reconcile_ean_recovery),
    ("store_mdd_vote", reconcile_store_mdd_vote),
    ("price_disambiguate", reconcile_price_disambiguate),
    ("retro_cab", reconcile_retro_cab),
]


def main(dry_run: bool = False) -> dict:
    """Run all 4 jobs and return the aggregated stats dict.

    Returns the dict so tests can assert on the orchestration outcome
    without parsing logs. CLI ignores the return value (the structured
    log line carries the same payload).
    """
    # Init Sentry first — silent no-op when SENTRY_DSN is absent. Any
    # exception raised during the reconciliation jobs is then captured.
    init_sentry("ratis_batch_data_reconciliation")

    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        log.error("DATABASE_URL not set — aborting")
        sys.exit(1)

    engine = make_engine(db_url)
    Session = sessionmaker(bind=engine)

    mode = "DRY-RUN" if dry_run else "LIVE"
    log.info("=== ratis_batch_data_reconciliation START [%s] ===", mode)

    overall_stats: dict[str, dict] = {}
    for job_name, job_fn in JOBS:
        try:
            with Session() as db:
                job_stats = job_fn(db, dry_run=dry_run)
            overall_stats[job_name] = job_stats
            log.info(
                "job_complete %s",
                json.dumps({"job": job_name, **job_stats}, default=str),
            )
        except Exception as exc:
            log.error(
                "job_failed %s",
                json.dumps({"job": job_name, "error": str(exc)}),
                exc_info=True,
            )
            overall_stats[job_name] = {"error": str(exc)}

    log.info(
        "=== ratis_batch_data_reconciliation END [%s] %s ===",
        mode,
        json.dumps({"event": "batch_complete", "stats": overall_stats}, default=str),
    )

    return overall_stats


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="ratis_batch_data_reconciliation")
    parser.add_argument("--dry-run", action="store_true", help="Detect only, no DB writes, no notif")
    args = parser.parse_args()
    main(dry_run=args.dry_run)
