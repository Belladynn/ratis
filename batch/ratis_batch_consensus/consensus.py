"""
Consensus batch — run daily via cron / GitHub Actions scheduled workflow.

Recalculates trust_score for all active price_consensus rows.
Pipeline per consensus (in order):
  1. Skip if still frozen; unfreeze if frozen_until has elapsed
  2. Retrieve the last window_size scans
  3. Compute base temporal weights
  4. Apply Pattern A — isolated outlier neutralization (weight → 0)
  5. Apply Pattern B — emerging price detection (old concordant weights reduced)
  6. Recompute trust_score and detect price basculement
  7. Apply temporal decay if consensus has been inactive longer than decay_grace_days
  8. Persist changes (trust_score, computed_at, frozen_until, price on basculement)

All parameters come from ratis_core/config/ratis_settings.json — nothing hardcoded.

Usage:
  uv run python batch/ratis_batch_consensus/consensus.py            # normal run
  uv run python batch/ratis_batch_consensus/consensus.py --dry-run  # log counts, no commit
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from ratis_core.database import make_engine
from ratis_core.models.price import PriceConsensus, PriceConsensusHistory, PriceConsensusScans
from ratis_core.models.scan import Scan
from ratis_core.observability import init_sentry
from ratis_core.settings import load_settings
from sqlalchemy import select, text
from sqlalchemy.orm import Session, sessionmaker
from store_validation_phase import REQUIRED_KEYS as _SV_REQUIRED_KEYS
from store_validation_phase import run_store_validation_phase

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger("consensus")

BATCH_NAME = "consensus"


# ── Data structures ────────────────────────────────────────────────────────────


@dataclass(slots=True)
class _WeightedScan:
    price: Decimal
    weight: Decimal  # mutable — modified by pattern detection


# ── Pure functions ─────────────────────────────────────────────────────────────


def _base_weight(now: datetime, scanned_at: datetime, cfg: dict) -> Decimal:
    """Temporal weight: decreases linearly with age, floored at scan_weight_floor."""
    age_days = (now - scanned_at).total_seconds() / 86400
    raw = max(cfg["scan_weight_floor"], 1.0 - age_days * cfg["scan_weight_decay_per_day"])
    return Decimal(str(round(raw, 10)))


def _apply_pattern_a(items: list[_WeightedScan], consensus_price: Decimal) -> None:
    """
    Isolated outlier neutralization.

    A scan is isolated when:
    - its price differs from consensus_price
    - the 2 immediately more-recent scans (indices i-2, i-1) are concordant
    - the 2 immediately less-recent scans (indices i+1, i+2) are concordant

    Items must be ordered DESC (index 0 = most recent). Edge scans (index < 2 or
    index > len-3) cannot have 2 neighbors on both sides and are never neutralized.
    """
    n = len(items)
    for i in range(2, n - 2):
        if items[i].price == consensus_price:
            continue
        if (
            items[i - 1].price == consensus_price
            and items[i - 2].price == consensus_price
            and items[i + 1].price == consensus_price
            and items[i + 2].price == consensus_price
        ):
            log.debug("Pattern A: scan at index %d (price=%s) neutralized", i, items[i].price)
            items[i].weight = Decimal("0")


def _apply_pattern_b(items: list[_WeightedScan], consensus_price: Decimal, cfg: dict) -> None:
    """
    Emerging price detection.

    When the N most recent scans (N = emerging_consecutive_threshold) are all at the
    same divergent price, reduce all older concordant scans to
    min(current_weight, emerging_old_weight).

    Items must be ordered DESC (index 0 = most recent).
    Does nothing when the window has fewer items than the threshold.
    Does not increase weights — only reduces them.
    """
    threshold: int = cfg["emerging_consecutive_threshold"]
    old_weight = Decimal(str(cfg["emerging_old_weight"]))

    if len(items) < threshold:
        return

    head = items[:threshold]
    emerging_price = head[0].price
    if emerging_price == consensus_price:
        return
    if not all(s.price == emerging_price for s in head):
        return

    log.debug(
        "Pattern B: emerging price %s detected in last %d scans",
        emerging_price,
        threshold,
    )
    for item in items[threshold:]:
        if item.price == consensus_price:
            item.weight = min(item.weight, old_weight)


def _compute_scores(
    items: list[_WeightedScan],
    consensus_price: Decimal,
) -> tuple[Decimal, Decimal | None, Decimal]:
    """
    Return (trust_score, dominant_price, dominant_score) as percentages (0-100).

    trust_score    = concordant_weight / total_weight × 100
    dominant_price = price with the highest weighted score across all prices
    dominant_score = its score as a percentage

    Returns (0, None, 0) on an empty or fully-zeroed window.
    """
    score_total = sum(item.weight for item in items)
    if score_total == 0:
        return Decimal("0.00"), None, Decimal("0.00")

    price_scores: dict[Decimal, Decimal] = {}
    for item in items:
        price_scores[item.price] = price_scores.get(item.price, Decimal("0")) + item.weight

    score_consensus = price_scores.get(consensus_price, Decimal("0"))
    trust_score = (score_consensus / score_total * 100).quantize(Decimal("0.01"))

    dominant_price = max(price_scores, key=lambda p: price_scores[p])
    dominant_score = (price_scores[dominant_price] / score_total * 100).quantize(Decimal("0.01"))

    return trust_score, dominant_price, dominant_score


def _apply_decay(trust_score: Decimal, days_inactive: int, cfg: dict) -> Decimal:
    """
    Reduce trust_score for consensus rows that have received no new scans for
    longer than decay_grace_days.

    decay_amount = (days_inactive - decay_grace_days) × decay_rate_pct
    result       = max(decay_floor, trust_score - decay_amount)
    """
    grace: int = cfg["decay_grace_days"]
    if days_inactive < grace:
        return trust_score
    excess_days = days_inactive - grace
    decay_amount = Decimal(str(excess_days * cfg["decay_rate_pct"]))
    floor = Decimal(str(cfg["decay_floor"]))
    return max(floor, trust_score - decay_amount)


# ── DB helpers ─────────────────────────────────────────────────────────────────


def _get_window(db: Session, consensus_id: uuid.UUID, window_size: int) -> list[tuple[Decimal, datetime]]:
    """Return the (price, scanned_at) pairs for the most recent window_size scans."""
    rows = db.execute(
        select(Scan.price, Scan.scanned_at)
        .join(PriceConsensusScans, PriceConsensusScans.scan_id == Scan.id)
        .where(PriceConsensusScans.consensus_id == consensus_id)
        .order_by(Scan.scanned_at.desc(), Scan.id.desc())
        .limit(window_size)
    ).all()
    return [(row.price, row.scanned_at) for row in rows]


# ── Main per-consensus pipeline ────────────────────────────────────────────────


def process_consensus(
    db: Session,
    consensus: PriceConsensus,
    cfg: dict,
    now: datetime,
    dry_run: bool,
) -> str:
    """
    Full recalculation pipeline for a single price_consensus row.

    Returns:
        "frozen"      — consensus is still frozen, skipped entirely
        "basculement" — price switched to a new dominant price
        "updated"     — trust_score recalculated, no price switch
    """
    # 1. Freeze check
    if consensus.frozen_until is not None:
        if consensus.frozen_until > now:
            log.debug("Consensus %s frozen until %s — skipping", consensus.id, consensus.frozen_until)
            return "frozen"
        log.info("Consensus %s: freeze expired — unfreezing", consensus.id)
        if not dry_run:
            consensus.frozen_until = None

    # 2. Window
    raw_window = _get_window(db, consensus.id, cfg["window_size"])
    if not raw_window:
        log.warning("Consensus %s: empty window — nothing to recalculate", consensus.id)
        consensus.computed_at = now
        if not dry_run:
            db.flush()
        return "updated"

    # 3. Base weights
    items = [
        _WeightedScan(
            price=price,
            weight=_base_weight(now, scanned_at, cfg),
        )
        for price, scanned_at in raw_window
    ]

    # 4. Pattern A — isolated outlier neutralization
    _apply_pattern_a(items, consensus.price)

    # 5. Pattern B — emerging price detection
    _apply_pattern_b(items, consensus.price, cfg)

    # 6. Scores + basculement
    trust_score, dominant_price, dominant_score = _compute_scores(items, consensus.price)

    result = "updated"
    if dominant_price is not None and dominant_price != consensus.price and dominant_score > trust_score:
        log.info(
            "Consensus %s: basculement %s → %s (%.1f%% > %.1f%%)",
            consensus.id,
            consensus.price,
            dominant_price,
            float(dominant_score),
            float(trust_score),
        )
        if not dry_run:
            db.add(
                PriceConsensusHistory(
                    id=uuid.uuid4(),
                    consensus_id=consensus.id,
                    store_id=consensus.store_id,
                    product_ean=consensus.product_ean,
                    price=consensus.price,
                    trust_score=trust_score,
                    first_seen_at=consensus.first_seen_at,
                    last_seen_at=now,
                    frozen_until=None,
                )
            )
            consensus.price = dominant_price
            consensus.first_seen_at = now
            # Bump last_seen_at to satisfy the PG ``seen_order`` CHECK
            # (first_seen_at <= last_seen_at). A basculement starts a new
            # consensus *now*, so the freshness clock resets — semantically
            # identical to the scan-driven writer in
            # ``ratis_product_analyser/repositories/scan_repository.py``.
            consensus.last_seen_at = now
        trust_score = dominant_score  # used for decay calculation regardless of dry_run
        result = "basculement"

    # 7. Decay
    if result == "basculement":
        # A basculement resets the freshness clock to ``now``. In a non-dry
        # run ``consensus.last_seen_at`` is bumped above; in dry-run it is
        # left stale, so derive days_inactive from the semantics, not the
        # (possibly un-bumped) attribute — otherwise dry-run reports a
        # decayed score for a row that just switched price.
        days_inactive = 0
    else:
        days_inactive = (now - consensus.last_seen_at).days
    trust_score = _apply_decay(trust_score, days_inactive, cfg)

    if days_inactive > cfg["decay_grace_days"]:
        log.info(
            "Consensus %s: decay applied (%d days inactive) → %.1f%%",
            consensus.id,
            days_inactive,
            float(trust_score),
        )

    # 8. Persist
    consensus.trust_score = trust_score
    consensus.computed_at = now
    if not dry_run:
        db.flush()

    return result


# ── Batch runner ───────────────────────────────────────────────────────────────


def _process_chunk(
    session_factory,
    ids_chunk: list[uuid.UUID],
    cfg: dict,
    now: datetime,
    dry_run: bool,
) -> tuple[dict[str, int], list[str]]:
    """Process a chunk of consensus IDs in the calling thread. Thread-safe."""
    errors: list[str] = []
    stats: dict[str, int] = {"updated": 0, "basculement": 0, "frozen": 0}
    for consensus_id in ids_chunk:
        with session_factory() as db:
            try:
                consensus = db.get(PriceConsensus, consensus_id)
                if consensus is None:
                    continue
                outcome = process_consensus(db, consensus, cfg, now, dry_run)
                stats[outcome] += 1
                if not dry_run:
                    db.commit()
            except Exception:
                log.exception("Failed to process consensus %s", consensus_id)
                db.rollback()
                errors.append(str(consensus_id))
    return stats, errors


def run_batch(session_factory, cfg: dict, now: datetime, dry_run: bool) -> tuple[list[str], int]:
    """
    Iterate all price_consensus rows and run the full pipeline on each.

    Rows are split into chunks and processed in parallel via ThreadPoolExecutor.
    Each consensus is processed in its own transaction — an error on one row does
    not affect the others.

    Returns (failed_consensus_ids, rows_processed).
    """
    chunk_size: int = cfg["batch_chunk_size"]
    max_workers: int = cfg["batch_max_workers"]

    with session_factory() as db:
        ids: list[uuid.UUID] = list(db.scalars(select(PriceConsensus.id)))

    total = len(ids)
    log.info(
        "Processing %d consensus rows%s (chunk_size=%d, workers=%d)",
        total,
        " (dry-run)" if dry_run else "",
        chunk_size,
        max_workers,
    )

    chunks = [ids[i : i + chunk_size] for i in range(0, total, chunk_size)]
    all_errors: list[str] = []
    stats: dict[str, int] = {"updated": 0, "basculement": 0, "frozen": 0}

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [executor.submit(_process_chunk, session_factory, chunk, cfg, now, dry_run) for chunk in chunks]
        for future in as_completed(futures):
            try:
                chunk_stats, chunk_errors = future.result()
            except Exception:
                log.exception("Unexpected error in worker thread")
                continue
            for k, v in chunk_stats.items():
                stats[k] += v
            all_errors.extend(chunk_errors)

    rows_processed = stats["updated"] + stats["basculement"]
    log.info(
        "Batch complete: %d updated, %d basculements, %d frozen skipped, %d errors",
        stats["updated"],
        stats["basculement"],
        stats["frozen"],
        len(all_errors),
    )
    return all_errors, rows_processed


def _write_sync_log(session_factory, status: str, rows_affected: int, dry_run: bool) -> None:
    if dry_run:
        return
    with session_factory() as db:
        db.execute(
            text("INSERT INTO batch_sync_log (batch_name, status, rows_affected) VALUES (:name, :status, :rows)"),
            {"name": BATCH_NAME, "status": status, "rows": rows_affected},
        )
        db.commit()


# ── Entry point ────────────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(description="Ratis daily consensus batch")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Log what would be changed without committing to the database",
    )
    args = parser.parse_args()

    # Init Sentry first — silent no-op when SENTRY_DSN is absent. Any
    # exception raised during DB setup or run_batch is then captured.
    init_sentry("ratis_batch_consensus")

    if args.dry_run:
        log.info("DRY-RUN mode — no changes will be committed")

    url = os.environ.get("DATABASE_URL")
    if not url:
        log.error("DATABASE_URL environment variable is not set")
        sys.exit(1)

    try:
        settings = load_settings()
        cfg = settings["consensus"]
    except FileNotFoundError:
        log.error(
            "Settings unavailable: app_settings table empty/unreachable and ratis_settings.json not found — aborting"
        )
        sys.exit(1)
    except KeyError:
        log.error("Settings missing 'consensus' section — check app_settings table or ratis_settings.json — aborting")
        sys.exit(1)

    _REQUIRED_KEYS = {
        "window_size",
        "scan_weight_floor",
        "scan_weight_decay_per_day",
        "emerging_consecutive_threshold",
        "emerging_old_weight",
        "decay_grace_days",
        "decay_rate_pct",
        "decay_floor",
        "freeze_threshold_scans",
        "freeze_duration_hours",
        "batch_chunk_size",
        "batch_max_workers",
    }
    missing = _REQUIRED_KEYS - cfg.keys()
    if missing:
        log.error("Missing keys in consensus config: %s — aborting", ", ".join(sorted(missing)))
        sys.exit(1)

    # Phase 3 (store_validation) settings — fail-fast as for Phase 1+2.
    sv_cfg = settings.get("store_validation")
    if sv_cfg is None:
        log.error(
            "Settings missing 'store_validation' section — check app_settings table or ratis_settings.json — aborting"
        )
        sys.exit(1)
    sv_missing = _SV_REQUIRED_KEYS - sv_cfg.keys()
    if sv_missing:
        log.error(
            "Missing keys in store_validation config: %s — aborting",
            ", ".join(sorted(sv_missing)),
        )
        sys.exit(1)

    engine = make_engine(url, pool_pre_ping=True)
    session_factory = sessionmaker(engine)
    now = datetime.now(UTC)

    errors, rows_processed = run_batch(session_factory, cfg, now, dry_run=args.dry_run)

    # Phase 3 — store validation. Runs after Phase 1+2; its failures are
    # tracked separately in sv_stats but DO NOT rollback Phase 1+2 (those
    # rows are already committed per-chunk).
    sv_stats: dict[str, int] = {
        "flipped_confirmed": 0,
        "flipped_suspicious": 0,
        "retroactive_cashback_calls": 0,
        "errors": 0,
    }
    if args.dry_run:
        log.info("Phase 3 (store_validation) skipped — dry-run mode")
    else:
        try:
            sv_stats = run_store_validation_phase(session_factory, settings)
        except Exception:
            log.exception("Phase 3 (store_validation) crashed — Phase 1+2 unaffected")
            sv_stats["errors"] += 1
    log.info(
        "Phase 3 stats: %d confirmed flips, %d suspicious flips, %d retroactive cashback calls, %d errors",
        sv_stats["flipped_confirmed"],
        sv_stats["flipped_suspicious"],
        sv_stats["retroactive_cashback_calls"],
        sv_stats["errors"],
    )

    status = "failed" if errors or sv_stats["errors"] else "success"
    try:
        _write_sync_log(session_factory, status, rows_processed, args.dry_run)
    except Exception:
        log.exception("Failed to write sync log")

    if errors:
        log.error("Batch completed with errors on %d consensus rows", len(errors))
        sys.exit(1)

    log.info("Consensus batch completed successfully%s.", " (dry-run)" if args.dry_run else "")


if __name__ == "__main__":
    main()
