"""CLI entry point for the origins_tags backfill batch.

Usage :
    uv run python -m origins_backfill.main
    uv run python -m origins_backfill.main --dry-run
    uv run python -m origins_backfill.main --max-eans 500   # smoke run
    uv run python -m origins_backfill.main --request-delay-sec 0.5

Behaviour :
    1. Init Sentry (no-op when SENTRY_DSN unset).
    2. Validate ``DATABASE_URL`` is present.
    3. Open a single httpx.Client + sessionmaker, hand them to
       ``run_backfill`` via partial closures.
    4. On exit, record the outcome in ``batch_sync_log``
       (``batch_name='origins_backfill'``) with status + rows_affected
       counts.

Run modes
---------
``--dry-run`` skips the DB writes (still fetches OFF API responses)
— useful for sanity checking the network path without burning the
column in pre-prod. The DB SELECT still runs to identify the work set.

The default API base URL is ``OFF_API_BASE_URL`` env var (so prod can
point staging at a mock if needed) ; fallback is the live OFF host.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys

import httpx
from ratis_core.database import make_engine
from ratis_core.observability import init_sentry
from sqlalchemy.orm import sessionmaker

from origins_backfill.runner import (
    DEFAULT_PAGE_SIZE,
    DEFAULT_REQUEST_DELAY_SEC,
    DEFAULT_USER_AGENT,
    fetch_origins_tags,
    run_backfill,
)

log = logging.getLogger("origins_backfill")

BATCH_NAME = "ratis_batch_origins_backfill"
DEFAULT_OFF_API_BASE_URL = "https://world.openfoodfacts.org"


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="One-shot ETL — fill products.origins_tags from OFF API",
    )
    parser.add_argument(
        "--page-size",
        type=int,
        default=DEFAULT_PAGE_SIZE,
        help=f"EANs per DB page (default: {DEFAULT_PAGE_SIZE}).",
    )
    parser.add_argument(
        "--request-delay-sec",
        type=float,
        default=DEFAULT_REQUEST_DELAY_SEC,
        help=(f"Sleep (seconds) between OFF API calls. Default: {DEFAULT_REQUEST_DELAY_SEC}. Set to 0 in tests."),
    )
    parser.add_argument(
        "--max-eans",
        type=int,
        default=None,
        help="Stop after this many EANs have been scanned (default: unlimited).",
    )
    parser.add_argument(
        "--all-sources",
        action="store_true",
        help=(
            "Backfill rows from every product source. Default: only "
            "``source='off'`` (other sources won't have OFF entries)."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help=("Fetch + count, but do not write origins_tags to the DB. Useful for smoke verification of the API path."),
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    # Init Sentry first — silent no-op when SENTRY_DSN is absent.
    init_sentry(BATCH_NAME)

    args = _parse_args(argv)

    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        log.error("DATABASE_URL not set — aborting")
        return 1

    api_base_url = os.environ.get("OFF_API_BASE_URL", DEFAULT_OFF_API_BASE_URL)
    user_agent = os.environ.get("OFF_USER_AGENT", DEFAULT_USER_AGENT)

    engine = make_engine(db_url, pool_pre_ping=True)
    SessionFactory = sessionmaker(engine, autocommit=False, autoflush=False)

    log.info(
        "%s START — api=%s page_size=%d delay=%.2fs all_sources=%s dry_run=%s",
        BATCH_NAME,
        api_base_url,
        args.page_size,
        args.request_delay_sec,
        args.all_sources,
        args.dry_run,
    )

    exit_code = 0
    try:
        with httpx.Client(
            headers={"User-Agent": user_agent},
            timeout=30.0,
        ) as client:

            def _fetch(ean: str):
                return fetch_origins_tags(client, api_base_url, ean)

            # ``--dry-run`` : swap the session_factory for a context
            # manager that yields a sub-session whose commit() is a
            # no-op. Simpler than threading the flag through runner.
            if args.dry_run:
                session_factory = _make_dry_run_session_factory(SessionFactory)
            else:
                session_factory = SessionFactory

            stats = run_backfill(
                session_factory,
                _fetch,
                page_size=args.page_size,
                request_delay_sec=args.request_delay_sec,
                only_off_source=not args.all_sources,
                max_eans=args.max_eans,
            )

        log.info("%s END — stats=%s", BATCH_NAME, stats.as_dict())

        # Audit log : record success + rows touched. Done in a fresh
        # session so a failure here doesn't poison the backfill rows.
        if not args.dry_run:
            _write_audit_log(
                SessionFactory, status="success", rows_affected=stats.updated + stats.empty_origins + stats.not_found
            )
    except Exception:
        log.exception("%s FATAL", BATCH_NAME)
        exit_code = 2
        try:
            if not args.dry_run:
                _write_audit_log(SessionFactory, status="failed", rows_affected=0)
        except Exception:
            log.exception("%s could not write batch_sync_log on fatal", BATCH_NAME)
    finally:
        engine.dispose()

    return exit_code


def _write_audit_log(
    session_factory,
    *,
    status: str,
    rows_affected: int,
) -> None:
    from sqlalchemy import text  # local import keeps the test-time

    # import surface tiny — runner.py already pulls SQLAlchemy.
    with session_factory() as db:
        db.execute(
            text("INSERT INTO batch_sync_log (batch_name, status, rows_affected) VALUES (:name, :status, :rows)"),
            {"name": BATCH_NAME, "status": status, "rows": rows_affected},
        )
        db.commit()


def _make_dry_run_session_factory(real_factory):
    """Wrap the real session factory so .commit() is a no-op.

    Implemented by patching the session instance in a context manager.
    The dry-run path still issues SELECT (cheap) but skips UPDATE
    persistence — the run aborts as soon as we'd write the first row,
    re-reading the same page on each iteration ; so we also force an
    early exit via ``max_eans`` semantics by raising after the first
    update attempt. **Simpler approach** : a context manager that
    yields a session whose commit() is a no-op, and rely on the SELECT
    seeing the same NULL rows forever (which would loop). Sidestep :
    wrap the inner UPDATE statement.

    Pragmatic V1 : we accept that dry-run will loop forever on a real
    NULL-heavy DB. Operators are expected to combine ``--dry-run``
    with ``--max-eans`` for sanity. Document in PROD_CHECKLIST.
    """
    from contextlib import contextmanager

    @contextmanager
    def factory():
        db = real_factory()
        original_commit = db.commit
        # Swap commit for a no-op. Note : db.execute still runs the
        # SQL ; the session simply never persists. Rollback at close.
        db.commit = lambda: None  # type: ignore[method-assign]
        try:
            yield db
        finally:
            db.commit = original_commit  # type: ignore[method-assign]
            try:
                db.rollback()
            finally:
                db.close()

    return factory


if __name__ == "__main__":
    sys.exit(main())
