"""Open*Facts sync CLI entry point — multi-source pluggable batch.

Supports the four Open*Facts projects via the `--source` flag (default `off`).
The active project is resolved through `off_sync.sources.get_source(name)`,
which provides API base URL, User-Agent, photo CDN whitelist, and the
`batch_sync_log` cursor name (per-source resume in delta mode).

Usage:
    # Default = OFF (back-compat with all existing scheduled invocations)
    uv run python batch/ratis_batch_off_sync/off_sync/main.py --mode delta
    uv run python batch/ratis_batch_off_sync/off_sync/main.py --mode weekly
    uv run python batch/ratis_batch_off_sync/off_sync/main.py --mode monthly

    # Other Open*Facts sources (PR2/PR3/PR4 will wire the prod cron entries)
    uv run python batch/ratis_batch_off_sync/off_sync/main.py --mode delta --source obp
    uv run python batch/ratis_batch_off_sync/off_sync/main.py --mode delta --source opf
    uv run python batch/ratis_batch_off_sync/off_sync/main.py --mode delta --source opff

    # Explicit date range (API)
    uv run python batch/ratis_batch_off_sync/off_sync/main.py --since 2026-01-01 --until 2026-03-31

    # Full dump — on-demand only, never scheduled
    uv run python batch/ratis_batch_off_sync/off_sync/main.py --mode full --dump /data/off.jsonl.gz

    # Options
    uv run python batch/ratis_batch_off_sync/off_sync/main.py --mode delta --workers 8 --dry-run
"""

from __future__ import annotations

import argparse
import asyncio
import dataclasses
import logging
import os
import sys
from datetime import UTC, date, datetime, timedelta

from ratis_core.database import make_engine
from ratis_core.observability import init_sentry
from ratis_core.settings import load_settings
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

from off_sync.sources import SOURCES, get_source

log = logging.getLogger(__name__)

_MODE_DAYS: dict[str, int] = {
    "delta": 1,
    "weekly": 7,
    "monthly": 30,
}

# Overlap applied to delta mode when resuming from batch_sync_log.
# Ensures products modified just before the previous run's cutoff are not missed.
DELTA_OVERLAP_SECONDS: int = 5 * 60  # 5 minutes


def _to_ts(d: date) -> int:
    return int(datetime(d.year, d.month, d.day, tzinfo=UTC).timestamp())


def _get_last_success_ts(engine, batch_name: str) -> int | None:
    """Return Unix timestamp of the last successful run for `batch_name`, or None."""
    Session = sessionmaker(engine)
    with Session() as db:
        row = db.execute(
            text(
                "SELECT last_run_at FROM batch_sync_log "
                "WHERE batch_name = :name AND status = 'success' "
                "ORDER BY last_run_at DESC LIMIT 1"
            ),
            {"name": batch_name},
        ).fetchone()
        return int(row.last_run_at.timestamp()) if row else None


def _write_sync_log(engine, batch_name: str, status: str, rows_affected: int | None) -> None:
    """Insert a batch_sync_log row for this run."""
    Session = sessionmaker(engine)
    with Session() as db:
        db.execute(
            text("INSERT INTO batch_sync_log (batch_name, status, rows_affected) VALUES (:name, :status, :rows)"),
            {"name": batch_name, "status": status, "rows": rows_affected},
        )
        db.commit()


def _compute_range(
    args: argparse.Namespace,
    last_success_ts: int | None = None,
) -> tuple[int, int | None]:
    """Return (since_ts, until_ts) from parsed args.

    For --mode delta: uses last_success_ts from batch_sync_log when available,
    shifted back by DELTA_OVERLAP_SECONDS to avoid missing entries at the boundary.
    Falls back to 1 day ago (midnight UTC) on the first run (no log entry yet).
    """
    if args.mode in _MODE_DAYS:
        if args.mode == "delta" and last_success_ts is not None:
            return last_success_ts - DELTA_OVERLAP_SECONDS, None
        since = datetime.now(UTC).date() - timedelta(days=_MODE_DAYS[args.mode])
        return _to_ts(since), None

    # --since / --until explicit
    since_ts = _to_ts(args.since)
    until_ts = _to_ts(args.until) if args.until else None
    return since_ts, until_ts


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Open*Facts multi-source sync — delta (API) or full dump (JSONL)")
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--mode",
        choices=["delta", "weekly", "monthly", "full"],
        help="Preset mode. 'full' requires --dump.",
    )
    group.add_argument(
        "--since",
        type=date.fromisoformat,
        metavar="YYYY-MM-DD",
        help="Explicit start date (API modes only).",
    )
    parser.add_argument(
        "--until",
        type=date.fromisoformat,
        metavar="YYYY-MM-DD",
        help="Explicit end date (API modes only, optional).",
    )
    parser.add_argument(
        "--dump",
        metavar="PATH",
        help="Path to JSONL(.gz) dump file. Required for --mode full.",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=4,
        metavar="N",
        help="Parallel workers (HTTP coroutines or processes). Must be >= 1. Default: 4.",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=3600,
        metavar="SECONDS",
        help="Max run duration in seconds (API and dump modes). Default: 3600.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Fetch and validate without writing to DB.",
    )
    parser.add_argument(
        "--force-resync",
        action="store_true",
        help=(
            "Ignore last_modified_t cutoff and re-fetch every product "
            "(France-only). Use sparingly — typically after a schema change "
            "that adds new persisted fields. API modes only."
        ),
    )
    parser.add_argument(
        "--source",
        choices=sorted(SOURCES),
        default="off",
        help="Catalogue source to ingest. Default: off (back-compat).",
    )
    args = parser.parse_args(argv)
    _validate(args, parser)
    return args


def _validate(args: argparse.Namespace, parser: argparse.ArgumentParser) -> None:
    if args.workers < 1:
        parser.error("--workers must be >= 1")
    if args.timeout < 1:
        parser.error("--timeout must be >= 1")
    if args.mode == "full" and not args.dump:
        parser.error("--dump is required for --mode full")
    if args.dump and args.mode != "full":
        parser.error("--dump is only valid with --mode full")
    if args.until and not args.since:
        parser.error("--until requires --since")
    if not args.mode and not args.since:
        parser.error("one of --mode or --since is required")
    # --force-resync makes sense only for the API path (re-fetch every product).
    # Combining with --mode full would be redundant (full dump already re-reads
    # every record). Refuse the combo to avoid confusion.
    if args.force_resync and args.mode == "full":
        parser.error("--force-resync is incompatible with --mode full (full already re-reads everything)")


def main(argv: list[str] | None = None) -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    # Init Sentry first — silent no-op when SENTRY_DSN is absent. Any
    # exception raised during DB/HTTP work is then captured.
    init_sentry("ratis_batch_off_sync")

    args = _parse_args(argv)  # validation runs inside _parse_args
    src = get_source(args.source)

    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        log.error("DATABASE_URL not set")
        sys.exit(1)

    try:
        cfg = load_settings()["off_sync"]
    except FileNotFoundError:
        log.error(
            "Settings unavailable: app_settings table empty/unreachable and ratis_settings.json not found — aborting"
        )
        sys.exit(1)
    except KeyError:
        log.error("Settings missing 'off_sync' section — check app_settings table or ratis_settings.json — aborting")
        sys.exit(1)

    # Per-source override : settings can override the registry URL (ops escape
    # hatch for staging or vendor URL changes), but the registry holds the
    # production default — no settings change needed to ship.
    source_cfg = cfg.get("sources", {}).get(src.name, {})
    api_base_url = source_cfg.get("api_base_url", src.api_base_url)
    src_effective = dataclasses.replace(src, api_base_url=api_base_url)

    engine = make_engine(db_url, pool_pre_ping=True)
    status = "failed"
    rows_affected: int | None = None
    try:
        if args.mode == "full":
            from off_sync.dump import run_dump

            stats = run_dump(
                args.dump,
                db_url,
                args.workers,
                args.dry_run,
                args.timeout,
                source=src_effective,
            )
        else:
            from off_sync.api import run_api

            last_success_ts = _get_last_success_ts(engine, src.batch_name) if args.mode == "delta" else None
            since_ts, until_ts = _compute_range(args, last_success_ts)
            # --force-resync : drop the lower bound entirely so the Search
            # API returns every France product regardless of last_modified_t.
            # since_ts=0 is honoured by the API (Unix epoch) — equivalent to
            # "no cutoff" in practice.
            if args.force_resync:
                log.warning("%s API: --force-resync enabled, ignoring last_modified_t cutoff", src.name)
                since_ts = 0
                until_ts = None

            async def _run_with_timeout():
                return await asyncio.wait_for(
                    run_api(
                        db_url,
                        since_ts,
                        until_ts,
                        args.workers,
                        args.dry_run,
                        source=src_effective,
                    ),
                    timeout=args.timeout,
                )

            stats = asyncio.run(_run_with_timeout())

        rows_affected = stats.inserted + stats.updated
        status = "success"
    except TimeoutError:
        log.error("%s sync timed out after %d second(s) — consider increasing --timeout", src.name, args.timeout)
    except Exception as exc:
        log.error("%s sync failed: %s", src.name, exc, exc_info=True)
    finally:
        if not args.dry_run:
            try:
                _write_sync_log(engine, src.batch_name, status, rows_affected)
            except Exception as log_exc:
                log.error("Failed to write sync log: %s", log_exc)
        engine.dispose()

    if status == "failed":
        sys.exit(1)


if __name__ == "__main__":
    main()
