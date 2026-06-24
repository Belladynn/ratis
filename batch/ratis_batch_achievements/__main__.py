"""Achievements batch entrypoint — ``python -m batch.ratis_batch_achievements``.

Opens a session against ``DATABASE_URL``, runs the nightly sweep, records the
outcome in ``batch_sync_log`` (one row per run, success or failed) and exits :

* ``0`` — success
* ``1`` — at least one handler raised (run completed, partial unlocks may
          still have been committed by ``_unlock``)
* ``2`` — fatal exception caught at the entrypoint level (DB unreachable,
          import error, etc.)

Mirrors the run-once pattern used by ``ratis_batch_savings`` /
``ratis_batch_referral_payout``.
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

from ratis_core.database import make_engine
from ratis_core.models.batch_sync_log import BatchSyncLog
from ratis_core.observability import init_sentry
from sqlalchemy.orm import sessionmaker

# achievement_service uses flat-layout imports — make them resolvable when
# this module runs outside the rewards-service container (CI runner, prod
# batch image, dev shell). The same shim lives at the top of
# ``achievements_batch.py`` ; importing the latter brings it along, but we
# duplicate it here so ``python -m batch.ratis_batch_achievements`` works
# even if the achievements_batch import order ever changes.
_REWARDS_DIR = Path(__file__).resolve().parents[2] / "webservices" / "ratis_rewards"
if _REWARDS_DIR.is_dir() and str(_REWARDS_DIR) not in sys.path:
    sys.path.insert(0, str(_REWARDS_DIR))

from batch.ratis_batch_achievements.achievements_batch import run_batch

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger("achievements_batch")

BATCH_NAME = "ratis_batch_achievements"


def main() -> int:
    # Init Sentry first — silent no-op when SENTRY_DSN is absent. Any
    # exception raised during the batch run is then captured.
    init_sentry("ratis_batch_achievements")

    url = os.environ.get("DATABASE_URL")
    if not url:
        log.error("DATABASE_URL environment variable is not set")
        return 2

    engine = make_engine(url, pool_pre_ping=True)
    SessionFactory = sessionmaker(engine, autocommit=False, autoflush=False)

    db = SessionFactory()
    try:
        result = run_batch(db)
        log.info(
            "achievements_batch done : rows_unlocked=%d errors=%d success=%s",
            result.rows_affected,
            result.errors,
            result.success,
        )
        # Write the audit row in a fresh session — ``run_batch`` may have
        # left the working session in a state where the next commit also
        # ships unrelated handler-side writes. A new session is the cheap,
        # boring path.
        with SessionFactory() as audit_db:
            audit_db.add(
                BatchSyncLog(
                    batch_name=BATCH_NAME,
                    status="success" if result.success else "failed",
                    rows_affected=result.rows_affected,
                )
            )
            audit_db.commit()
        return 0 if result.success else 1
    except Exception:
        log.exception("achievements_batch FATAL")
        # Best-effort audit write so an outage shows up in ``batch_sync_log``
        # rather than going silent.
        try:
            with SessionFactory() as audit_db:
                audit_db.add(
                    BatchSyncLog(
                        batch_name=BATCH_NAME,
                        status="failed",
                        rows_affected=0,
                    )
                )
                audit_db.commit()
        except Exception:
            log.exception("Failed to write batch_sync_log on fatal")
        return 2
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
