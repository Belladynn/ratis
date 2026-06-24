"""
Mystery announce batch — run daily at 0h00 UTC via cron.

Steps (each in its own transaction, idempotent):
  1. reveal_clues         — reveal clues for the current day
  2. announce_finds       — set announced_at on finds from before midnight
  3. activate_next        — activate next scheduled challenge if no active one
  4. freeze_and_reveal    — freeze active challenge if past ends_at

Usage:
  uv run python batch/ratis_batch_mystery_announce/mystery_announce.py
  uv run python batch/ratis_batch_mystery_announce/mystery_announce.py --dry-run
"""

import argparse
import logging
import os
import sys

from ratis_core.database import make_engine
from ratis_core.observability import init_sentry
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger("mystery_announce")

BATCH_NAME = "mystery_announce"


def _run(db, label: str, sql: str, params: dict | None = None) -> int:
    """Execute a DML statement, log the row count, return it."""
    result = db.execute(text(sql), params or {})
    count = result.rowcount
    log.info("%s: %d row(s) updated", label, count)
    return count


def reveal_clues(Session, dry_run: bool) -> int:
    """
    Reveal clues for the active challenge whose reveal_day has been reached.
    Idempotent: revealed_at IS NULL guard prevents double-reveal.
    """
    with Session() as db:
        count = _run(
            db,
            "reveal_clues",
            """
            UPDATE mystery_challenge_clues
            SET revealed_at = now()
            WHERE challenge_id = (
                SELECT id FROM mystery_challenges WHERE status = 'active' LIMIT 1
            )
            AND reveal_day <= (
                SELECT EXTRACT(DAY FROM (now() - starts_at))::int + 1
                FROM mystery_challenges WHERE status = 'active' LIMIT 1
            )
            AND revealed_at IS NULL
            """,
        )
        if not dry_run:
            db.commit()
    return count


def announce_finds(Session, dry_run: bool) -> int:
    """
    Mark as announced all finds that occurred before midnight UTC today
    and have not yet been announced.
    Idempotent: announced_at IS NULL guard prevents re-announcing.
    Note: push notifications will be added in V2 (see PROD_CHECKLIST).
    """
    with Session() as db:
        count = _run(
            db,
            "announce_finds",
            """
            UPDATE mystery_challenge_finds
            SET announced_at = now()
            WHERE announced_at IS NULL
              AND found_at < date_trunc('day', now() AT TIME ZONE 'UTC')
            """,
        )
        if not dry_run:
            db.commit()
    return count


def activate_next(Session, dry_run: bool) -> int:
    """
    Activate the next scheduled challenge (earliest starts_at <= now()) if no
    challenge is currently active.
    Idempotent: NOT EXISTS guard and the partial unique index on status='active'
    both prevent activating more than one challenge.
    """
    with Session() as db:
        count = _run(
            db,
            "activate_next",
            """
            UPDATE mystery_challenges
            SET status = 'active'
            WHERE id = (
                SELECT id FROM mystery_challenges
                WHERE status = 'scheduled'
                  AND starts_at <= now()
                ORDER BY starts_at
                LIMIT 1
            )
            AND NOT EXISTS (
                SELECT 1 FROM mystery_challenges WHERE status = 'active'
            )
            """,
        )
        if not dry_run:
            db.commit()
    return count


def freeze_and_reveal(Session, dry_run: bool) -> int:
    """
    Transition challenges past their ends_at:
      active  → frozen   (immediately when ends_at <= now())
      frozen  → revealed (one day after ends_at, giving admins time to draw)
    Idempotent: WHERE status IN ('active','frozen') AND ends_at <= now().
    """
    with Session() as db:
        count = _run(
            db,
            "freeze_and_reveal",
            """
            UPDATE mystery_challenges
            SET status = CASE
                WHEN status = 'active'  AND ends_at <= now()
                    THEN 'frozen'
                WHEN status = 'frozen'  AND ends_at <= now() - interval '1 day'
                    THEN 'revealed'
                ELSE status
            END
            WHERE status IN ('active', 'frozen')
              AND ends_at <= now()
            """,
        )
        if not dry_run:
            db.commit()
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


STEPS = [
    ("reveal_clues", reveal_clues),
    ("announce_finds", announce_finds),
    ("activate_next", activate_next),
    ("freeze_and_reveal", freeze_and_reveal),
]


def main() -> None:
    parser = argparse.ArgumentParser(description="Ratis mystery announce batch")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Log affected row counts without committing any changes",
    )
    args = parser.parse_args()

    # Init Sentry first — silent no-op when SENTRY_DSN is absent. Any
    # exception raised during the announce steps is then captured.
    init_sentry("ratis_batch_mystery_announce")

    if args.dry_run:
        log.info("DRY-RUN mode — no changes will be committed")

    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        log.error("DATABASE_URL not set")
        sys.exit(1)

    engine = make_engine(db_url, pool_pre_ping=True)
    Session = sessionmaker(engine)

    errors: list[str] = []
    for name, fn in STEPS:
        try:
            fn(Session, dry_run=args.dry_run)
        except Exception as exc:
            log.error("FAILED %s: %s", name, exc, exc_info=True)
            errors.append(name)

    status = "failed" if errors else "ok"
    try:
        _write_sync_log(Session, status, args.dry_run)
    except Exception as exc:
        log.error("Failed to write sync log: %s", exc)

    if errors:
        log.error("mystery_announce batch failed: %s", ", ".join(errors))
        sys.exit(1)

    log.info("mystery_announce batch completed")


if __name__ == "__main__":
    main()
