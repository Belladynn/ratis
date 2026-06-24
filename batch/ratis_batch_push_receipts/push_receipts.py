"""Expo push-receipt polling batch — run periodically (e.g. hourly).

Expo's push API returns a *ticket* per accepted push ; the final delivery
outcome is only known by later calling Expo's *receipts* endpoint with the
ticket IDs. ``ratis_notifier`` persists each ticket in ``push_receipt_tickets``
(one row per (send, token)). This batch :

  1. Reads not-yet-checked ticket rows (``checked_at IS NULL``).
  2. POSTs the ticket IDs to Expo ``getReceipts`` in chunks.
  3. For every receipt with a ``DeviceNotRegistered`` error, deletes the
     matching ``user_push_tokens`` row — the token is permanently dead.
  4. Marks every polled ticket row ``checked_at = now()`` so it is not
     re-polled (an Expo receipt is only retained ~24h upstream).

Idempotent : a re-run skips already-checked rows. A receipt that is still
``status='ok'`` or not yet available is marked checked anyway — Expo does
not retain receipts long enough to make retrying worthwhile, and a missing
receipt is not an error.

Usage :
  uv run python batch/ratis_batch_push_receipts/push_receipts.py
  uv run python batch/ratis_batch_push_receipts/push_receipts.py --dry-run

Env vars : DATABASE_URL.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env.local")

import httpx
from ratis_core.database import make_engine
from ratis_core.observability import init_sentry
from ratis_core.settings import load_settings
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
log = logging.getLogger("push_receipts")

BATCH_NAME = "push_receipts"

# Expo caps a single getReceipts request at 1000 ticket IDs.
EXPO_RECEIPTS_MAX_IDS = 1000

# Receipt error codes that mean the push token is permanently dead and must
# be removed. ``DeviceNotRegistered`` is the canonical one ; the others are
# Expo-documented terminal token errors.
DEAD_TOKEN_ERRORS = frozenset({"DeviceNotRegistered"})


def _chunks(seq: list, size: int):
    """Yield successive ``size``-long chunks of ``seq``."""
    for i in range(0, len(seq), size):
        yield seq[i : i + size]


def fetch_receipts(receipts_url: str, ticket_ids: list[str]) -> dict[str, dict]:
    """POST ticket IDs to Expo getReceipts ; return ``{ticket_id: receipt}``.

    Expo returns receipts only for tickets it still retains — missing IDs
    are simply absent from the response and treated as "no outcome".
    """
    out: dict[str, dict] = {}
    for chunk in _chunks(ticket_ids, EXPO_RECEIPTS_MAX_IDS):
        response = httpx.post(receipts_url, json={"ids": chunk}, timeout=30)
        response.raise_for_status()
        data = response.json().get("data", {})
        if isinstance(data, dict):
            out.update(data)
    return out


def poll_receipts(session_factory, *, dry_run: bool) -> dict[str, int]:
    """Poll Expo receipts for all unchecked tickets ; clean up dead tokens.

    Returns a counters dict for the sync log.
    """
    settings = load_settings()["notifier"]
    receipts_url: str = os.environ.get("EXPO_RECEIPTS_URL") or settings["expo_receipts_url"]
    batch_size = int(settings.get("push_receipt_batch_size", EXPO_RECEIPTS_MAX_IDS))

    counters = {
        "tickets_polled": 0,
        "receipts_received": 0,
        "dead_tokens_removed": 0,
    }

    with session_factory() as db:
        rows = db.execute(
            text(
                "SELECT id, expo_ticket_id, push_token "
                "FROM push_receipt_tickets "
                "WHERE checked_at IS NULL "
                "ORDER BY created_at "
                "LIMIT :lim"
            ),
            {"lim": batch_size},
        ).fetchall()

    if not rows:
        log.info("No unchecked push tickets — nothing to poll")
        return counters

    counters["tickets_polled"] = len(rows)
    # ticket_id may repeat in theory only via data corruption ; map by id.
    ticket_ids = [r.expo_ticket_id for r in rows]
    log.info("Polling Expo for %d push receipt(s)", len(ticket_ids))

    receipts = fetch_receipts(receipts_url, ticket_ids)
    counters["receipts_received"] = len(receipts)

    with session_factory() as db:
        for row in rows:
            receipt = receipts.get(row.expo_ticket_id)
            if receipt and receipt.get("status") == "error":
                error_code = receipt.get("details", {}).get("error", "")
                if error_code in DEAD_TOKEN_ERRORS:
                    counters["dead_tokens_removed"] += _delete_dead_token(db, row.push_token, dry_run=dry_run)
            if not dry_run:
                db.execute(
                    text("UPDATE push_receipt_tickets SET checked_at = now() WHERE id = :tid"),
                    {"tid": row.id},
                )
        if not dry_run:
            db.commit()

    return counters


def _delete_dead_token(db: Session, push_token: str, *, dry_run: bool) -> int:
    """Delete the user_push_tokens row for a dead token. Returns 1 if a row
    was (or would be, in dry-run) deleted, else 0."""
    if dry_run:
        exists = db.execute(
            text("SELECT 1 FROM user_push_tokens WHERE token = :tok"),
            {"tok": push_token},
        ).first()
        if exists:
            log.info("DRY-RUN would delete dead token %s...", push_token[:24])
            return 1
        return 0
    result = db.execute(
        text("DELETE FROM user_push_tokens WHERE token = :tok"),
        {"tok": push_token},
    )
    if result.rowcount:
        log.info("Deleted dead push token %s... (DeviceNotRegistered)", push_token[:24])
    return result.rowcount


def _write_sync_log(session_factory, status: str, rows_affected: int, dry_run: bool) -> None:
    if dry_run:
        return
    with session_factory() as db:
        db.execute(
            text("INSERT INTO batch_sync_log (batch_name, status, rows_affected) VALUES (:name, :status, :rows)"),
            {"name": BATCH_NAME, "status": status, "rows": rows_affected},
        )
        db.commit()


def main() -> None:
    parser = argparse.ArgumentParser(description="Ratis push-receipt polling batch")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Poll Expo and report ; delete nothing, mark nothing checked.",
    )
    args = parser.parse_args()

    # Init Sentry first — silent no-op when SENTRY_DSN is absent.
    init_sentry("ratis_batch_push_receipts")

    if args.dry_run:
        log.info("DRY-RUN mode — no changes will be committed")

    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        log.error("DATABASE_URL environment variable is not set")
        sys.exit(1)

    engine = make_engine(db_url, pool_pre_ping=True)
    session_factory = sessionmaker(engine)

    try:
        counters = poll_receipts(session_factory, dry_run=args.dry_run)
    except Exception:
        log.exception("push_receipts batch failed")
        try:
            _write_sync_log(session_factory, "failed", 0, args.dry_run)
        except Exception:
            log.exception("Failed to write sync log")
        sys.exit(1)

    log.info(
        "Batch complete: polled=%d receipts=%d dead_tokens_removed=%d",
        counters["tickets_polled"],
        counters["receipts_received"],
        counters["dead_tokens_removed"],
    )

    try:
        _write_sync_log(session_factory, "success", counters["dead_tokens_removed"], args.dry_run)
    except Exception:
        log.exception("Failed to write sync log")


if __name__ == "__main__":
    main()
