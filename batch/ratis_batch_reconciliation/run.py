# batch/ratis_batch_reconciliation/run.py
"""
ratis_batch_reconciliation — run daily via cron / GitHub Actions.

Operations (in order):
  1. reconcile_missing_scan_rewards        — CAB manquants sur scans acceptés
  2. check_cab_balance_integrity           — dérives solde CABs (alert only)
  3. reconcile_pending_gift_card_orders    — boutique orders stuck pending > 24h → failed + CAB refund (audit C3)
  4. reconcile_deferred_gift_card_orders   — deferred non-referral orders past eligible_at → re-issue (audit H4)
  5. reconcile_processing_gift_card_orders — non-shop orders stuck on Runa PROCESSING → re-trigger issuance (audit H4)
  6. reconcile_expired_cashbacks           — cashbacks pending > 90j → refused
  7. reconcile_missing_cashback_scans      — receipts sans cashback → CREDIT pending
  8. reconcile_pending_withdrawals         — retraits bloqués > 24h (alert only, V1 stub)
  9. check_cashback_balance_integrity      — dérives solde cashback (alert only)

Usage:
  uv run python batch/ratis_batch_reconciliation/run.py            # normal run
  uv run python batch/ratis_batch_reconciliation/run.py --dry-run  # log only, no writes
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env.local")

from ratis_core.database import make_engine
from ratis_core.observability import init_sentry
from reconciliation.cab import check_cab_balance_integrity, reconcile_missing_scan_rewards
from reconciliation.cashback import (
    check_cashback_balance_integrity,
    reconcile_expired_cashbacks,
    reconcile_missing_cashback_scans,
    reconcile_pending_withdrawals,
)
from reconciliation.deferred_gift_cards import reconcile_deferred_gift_card_orders
from reconciliation.gift_cards import reconcile_pending_gift_card_orders
from reconciliation.processing_gift_cards import reconcile_processing_gift_card_orders
from sqlalchemy.orm import sessionmaker

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
log = logging.getLogger("reconciliation")


def main(dry_run: bool = False) -> None:
    # Init Sentry first — silent no-op when SENTRY_DSN is absent. Any
    # exception raised during reconciliation steps is then captured.
    init_sentry("ratis_batch_reconciliation")

    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        log.error("DATABASE_URL not set — aborting")
        sys.exit(1)

    engine = make_engine(db_url)
    Session = sessionmaker(bind=engine)

    mode = "DRY-RUN" if dry_run else "LIVE"
    log.info("=== ratis_batch_reconciliation START [%s] ===", mode)

    results: dict[str, int | str] = {}

    # --- CAB ---
    with Session() as db:
        try:
            results["missing_scan_rewards"] = reconcile_missing_scan_rewards(db, dry_run)
        except Exception:
            log.error("reconcile_missing_scan_rewards failed", exc_info=True)
            results["missing_scan_rewards"] = "ERROR"

    with Session() as db:
        try:
            drifts = check_cab_balance_integrity(db)
            results["cab_integrity_drifts"] = len(drifts)
        except Exception:
            log.error("check_cab_balance_integrity failed", exc_info=True)
            results["cab_integrity_drifts"] = "ERROR"

    with Session() as db:
        try:
            results["pending_gift_card_orders"] = reconcile_pending_gift_card_orders(db, dry_run)
        except Exception:
            log.error("reconcile_pending_gift_card_orders failed", exc_info=True)
            results["pending_gift_card_orders"] = "ERROR"

    with Session() as db:
        try:
            results["deferred_gift_card_orders"] = reconcile_deferred_gift_card_orders(db, dry_run)
        except Exception:
            log.error("reconcile_deferred_gift_card_orders failed", exc_info=True)
            results["deferred_gift_card_orders"] = "ERROR"

    with Session() as db:
        try:
            results["processing_gift_card_orders"] = reconcile_processing_gift_card_orders(db, dry_run)
        except Exception:
            log.error("reconcile_processing_gift_card_orders failed", exc_info=True)
            results["processing_gift_card_orders"] = "ERROR"

    # --- Cashback ---
    with Session() as db:
        try:
            results["expired_cashbacks"] = reconcile_expired_cashbacks(db, dry_run)
        except Exception:
            log.error("reconcile_expired_cashbacks failed", exc_info=True)
            results["expired_cashbacks"] = "ERROR"

    with Session() as db:
        try:
            results["missing_cashback_scans"] = reconcile_missing_cashback_scans(db, dry_run)
        except Exception:
            log.error("reconcile_missing_cashback_scans failed", exc_info=True)
            results["missing_cashback_scans"] = "ERROR"

    with Session() as db:
        try:
            results["pending_withdrawals"] = reconcile_pending_withdrawals(db, dry_run)
        except Exception:
            log.error("reconcile_pending_withdrawals failed", exc_info=True)
            results["pending_withdrawals"] = "ERROR"

    with Session() as db:
        try:
            drifts = check_cashback_balance_integrity(db)
            results["cashback_integrity_drifts"] = len(drifts)
        except Exception:
            log.error("check_cashback_balance_integrity failed", exc_info=True)
            results["cashback_integrity_drifts"] = "ERROR"

    log.info("=== ratis_batch_reconciliation END [%s] — results: %s ===", mode, results)

    errors = [k for k, v in results.items() if v == "ERROR"]
    if errors:
        log.error("Some operations failed: %s", errors)
        sys.exit(1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="ratis_batch_reconciliation")
    parser.add_argument("--dry-run", action="store_true", help="Detect only, no DB writes")
    args = parser.parse_args()
    main(dry_run=args.dry_run)
