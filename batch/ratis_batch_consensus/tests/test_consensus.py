"""
Tests for batch/ratis_batch_consensus/consensus.py

Unit tests cover pure functions (no DB required).
Integration tests use the SA 2.0 SAVEPOINT fixtures from conftest.py.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from consensus import (
    _apply_decay,
    _apply_pattern_a,
    _apply_pattern_b,
    _compute_scores,
    _WeightedScan,
    process_consensus,
)
from ratis_core.models.price import PriceConsensusHistory

# ── Shared constants ───────────────────────────────────────────────────────────

PRICE = Decimal("3.50")
OTHER = Decimal("3.60")
# DB constants (centimes) — used in integration tests that round-trip through the INTEGER price column
PRICE_DB = 350
OTHER_DB = 360
NOW = datetime(2026, 4, 8, 12, 0, 0, tzinfo=UTC)

CFG = {
    "window_size": 20,
    "scan_weight_floor": 0.30,
    "scan_weight_decay_per_day": 0.10,
    "freeze_threshold_scans": 3,
    "freeze_duration_hours": 24,
    "decay_grace_days": 5,
    "decay_rate_pct": 10,
    "decay_floor": 30,
    "emerging_consecutive_threshold": 4,
    "emerging_old_weight": 0.15,
    "price_quarantine_pct": 30,
    "ticket_max_age_days": 7,
    "min_scans_to_create": 2,
    "min_distinct_users": 2,
    "globally_verified_threshold": 80,
    "barcode_resolve_weight_factor": 0.5,
}


def _item(price: float | str, weight: str = "1.0") -> _WeightedScan:
    return _WeightedScan(
        price=Decimal(str(price)),
        weight=Decimal(weight),
    )


# ══════════════════════════════════════════════════════════════════════════════
# Pattern A — isolated outlier neutralization
# ══════════════════════════════════════════════════════════════════════════════


class TestPatternA:
    def test_isolated_outlier_neutralized(self):
        """Scan surrounded by 2 concordant on each side → weight zeroed."""
        items = [_item(3.50), _item(3.50), _item(3.60, "0.8"), _item(3.50), _item(3.50)]
        _apply_pattern_a(items, PRICE)
        assert items[2].weight == Decimal("0")

    def test_surrounding_scans_unchanged(self):
        items = [_item(3.50), _item(3.50), _item(3.60, "0.8"), _item(3.50), _item(3.50)]
        _apply_pattern_a(items, PRICE)
        assert items[0].weight == Decimal("1.0")
        assert items[4].weight == Decimal("1.0")

    def test_only_one_concordant_each_side_not_neutralized(self):
        """Only 1 concordant neighbor on each side — not enough."""
        items = [_item(3.50), _item(3.60, "0.9"), _item(3.50)]
        _apply_pattern_a(items, PRICE)
        assert items[1].weight == Decimal("0.9")

    def test_edge_index_0_not_neutralized(self):
        """Outlier at index 0 has no 2 more-recent concordant neighbors."""
        items = [_item(3.60), _item(3.50), _item(3.50), _item(3.50), _item(3.50)]
        _apply_pattern_a(items, PRICE)
        assert items[0].weight == Decimal("1.0")

    def test_edge_index_1_not_neutralized(self):
        """Outlier at index 1 has only 1 more-recent concordant neighbor."""
        items = [_item(3.50), _item(3.60, "0.9"), _item(3.50), _item(3.50), _item(3.50)]
        _apply_pattern_a(items, PRICE)
        assert items[1].weight == Decimal("0.9")

    def test_edge_index_n_minus_2_not_neutralized(self):
        """Outlier at n-2 has only 1 less-recent concordant neighbor."""
        items = [_item(3.50), _item(3.50), _item(3.50), _item(3.60, "0.9"), _item(3.50)]
        _apply_pattern_a(items, PRICE)
        assert items[3].weight == Decimal("0.9")

    def test_edge_index_last_not_neutralized(self):
        """Outlier at last index has no less-recent neighbors."""
        items = [_item(3.50), _item(3.50), _item(3.50), _item(3.50), _item(3.60)]
        _apply_pattern_a(items, PRICE)
        assert items[4].weight == Decimal("1.0")

    def test_concordant_scan_never_neutralized(self):
        items = [_item(3.50)] * 5
        _apply_pattern_a(items, PRICE)
        for item in items:
            assert item.weight == Decimal("1.0")

    def test_outlier_with_non_concordant_neighbors_not_neutralized(self):
        """Neighbors exist but are not concordant — pattern A not triggered."""
        items = [_item(3.50), _item(3.60), _item(3.70, "0.8"), _item(3.60), _item(3.50)]
        _apply_pattern_a(items, PRICE)
        assert items[2].weight == Decimal("0.8")

    def test_multiple_isolated_outliers(self):
        """Two independent isolated outliers are both neutralized."""
        items = [
            _item(3.50),
            _item(3.50),
            _item(3.60, "0.8"),
            _item(3.50),
            _item(3.50),
            _item(3.50),
            _item(3.50),
            _item(3.70, "0.7"),
            _item(3.50),
            _item(3.50),
        ]
        _apply_pattern_a(items, PRICE)
        assert items[2].weight == Decimal("0")
        assert items[7].weight == Decimal("0")


# ══════════════════════════════════════════════════════════════════════════════
# Pattern B — emerging price detection
# ══════════════════════════════════════════════════════════════════════════════


class TestPatternB:
    def test_emerging_price_reduces_old_concordant(self):
        """4 consecutive scans at new price → old concordant weights reduced to 0.15."""
        items = [
            _item(3.60, "1.0"),
            _item(3.60, "0.9"),
            _item(3.60, "0.8"),
            _item(3.60, "0.7"),
            _item(3.50, "0.5"),
            _item(3.50, "0.4"),
        ]
        _apply_pattern_b(items, PRICE, CFG)
        assert items[4].weight == Decimal("0.15")
        assert items[5].weight == Decimal("0.15")

    def test_emerging_scans_themselves_unchanged(self):
        items = [
            _item(3.60, "1.0"),
            _item(3.60, "0.9"),
            _item(3.60, "0.8"),
            _item(3.60, "0.7"),
            _item(3.50, "0.5"),
        ]
        _apply_pattern_b(items, PRICE, CFG)
        assert items[0].weight == Decimal("1.0")
        assert items[3].weight == Decimal("0.7")

    def test_fewer_than_threshold_not_triggered(self):
        """3 consecutive when threshold is 4 → no effect."""
        items = [
            _item(3.60, "1.0"),
            _item(3.60, "0.9"),
            _item(3.60, "0.8"),
            _item(3.50, "0.7"),
        ]
        _apply_pattern_b(items, PRICE, CFG)
        assert items[3].weight == Decimal("0.7")

    def test_mixed_head_not_triggered(self):
        """First N scans not all at the same price → pattern B not triggered."""
        items = [
            _item(3.60, "1.0"),
            _item(3.50, "0.9"),
            _item(3.60, "0.8"),
            _item(3.60, "0.7"),
            _item(3.50, "0.5"),
        ]
        _apply_pattern_b(items, PRICE, CFG)
        assert items[4].weight == Decimal("0.5")

    def test_emerging_equals_consensus_not_triggered(self):
        """All recent scans at consensus price → not an emerging divergent price."""
        items = [_item(3.50)] * 5
        _apply_pattern_b(items, PRICE, CFG)
        for item in items:
            assert item.weight == Decimal("1.0")

    def test_does_not_increase_weight_below_old_weight(self):
        """Old concordant already below emerging_old_weight → weight not increased."""
        items = [
            _item(3.60, "1.0"),
            _item(3.60, "0.9"),
            _item(3.60, "0.8"),
            _item(3.60, "0.7"),
            _item(3.50, "0.10"),
        ]
        _apply_pattern_b(items, PRICE, CFG)
        assert items[4].weight == Decimal("0.10")  # min(0.10, 0.15) stays at 0.10

    def test_third_price_not_reduced(self):
        """Old scans at a third price (neither consensus nor emerging) → unchanged."""
        items = [
            _item(3.60, "1.0"),
            _item(3.60, "0.9"),
            _item(3.60, "0.8"),
            _item(3.60, "0.7"),
            _item(3.80, "0.5"),
        ]
        _apply_pattern_b(items, PRICE, CFG)
        assert items[4].weight == Decimal("0.5")

    def test_window_smaller_than_threshold_skipped(self):
        """Window has fewer items than threshold → no operation."""
        items = [_item(3.60, "1.0"), _item(3.60, "0.9"), _item(3.50, "0.8")]
        _apply_pattern_b(items, PRICE, CFG)
        # No crash, no change
        assert items[2].weight == Decimal("0.8")


# ══════════════════════════════════════════════════════════════════════════════
# Score computation
# ══════════════════════════════════════════════════════════════════════════════


class TestComputeScores:
    def test_all_concordant(self):
        items = [_item(3.50, "1.0"), _item(3.50, "0.9"), _item(3.50, "0.8")]
        trust, dominant, _ = _compute_scores(items, PRICE)
        assert trust == Decimal("100.00")
        assert dominant == PRICE

    def test_all_divergent(self):
        items = [_item(3.60, "1.0"), _item(3.60, "0.9")]
        trust, dominant, _ = _compute_scores(items, PRICE)
        assert trust == Decimal("0.00")
        assert dominant == Decimal("3.60")

    def test_mixed_scores(self):
        items = [_item(3.50, "0.7"), _item(3.60, "0.3")]
        trust, dominant, dominant_score = _compute_scores(items, PRICE)
        assert trust == Decimal("70.00")
        assert dominant == PRICE
        assert dominant_score == Decimal("70.00")

    def test_empty_window(self):
        trust, dominant, dominant_score = _compute_scores([], PRICE)
        assert trust == Decimal("0.00")
        assert dominant is None
        assert dominant_score == Decimal("0.00")

    def test_zeroed_weight_excluded_from_total(self):
        """Weight=0 scans (Pattern A) contribute 0 to both numerator and denominator."""
        items = [_item(3.50, "1.0"), _item(3.60, "0"), _item(3.50, "0.8")]
        trust, _, _ = _compute_scores(items, PRICE)
        assert trust == Decimal("100.00")

    def test_basculement_signal(self):
        """dominant_score > trust_score when new price outweighs old."""
        items = [
            _item(3.60, "1.0"),
            _item(3.60, "0.9"),
            _item(3.60, "0.8"),
            _item(3.50, "0.3"),
            _item(3.50, "0.3"),
        ]
        trust, dominant, dominant_score = _compute_scores(items, PRICE)
        assert dominant == Decimal("3.60")
        assert dominant_score > trust


# ══════════════════════════════════════════════════════════════════════════════
# Decay
# ══════════════════════════════════════════════════════════════════════════════


class TestApplyDecay:
    def test_decay_applied_after_grace(self):
        # 8 days inactive, grace=5 → 3 excess × 10 = 30 decay
        assert _apply_decay(Decimal("85"), 8, CFG) == Decimal("55")

    def test_exactly_at_grace_no_decay(self):
        assert _apply_decay(Decimal("85"), 5, CFG) == Decimal("85")

    def test_below_grace_no_decay(self):
        assert _apply_decay(Decimal("85"), 3, CFG) == Decimal("85")

    def test_one_day_excess(self):
        assert _apply_decay(Decimal("85"), 6, CFG) == Decimal("75")

    def test_decay_capped_at_floor(self):
        # 20 days inactive → 15 excess × 10 = 150 decay → floor=30
        assert _apply_decay(Decimal("85"), 20, CFG) == Decimal("30")

    def test_already_at_floor_stays(self):
        assert _apply_decay(Decimal("30"), 10, CFG) == Decimal("30")

    def test_zero_days_inactive_no_decay(self):
        assert _apply_decay(Decimal("75"), 0, CFG) == Decimal("75")


# ══════════════════════════════════════════════════════════════════════════════
# process_consensus — integration tests (DB required)
# ══════════════════════════════════════════════════════════════════════════════


class TestProcessConsensus:
    def test_updates_trust_score(self, db, make_consensus, add_scan):
        consensus = make_consensus(PRICE, Decimal("50.00"), NOW - timedelta(days=1))
        # 5 concordant scans, recent
        for d in range(5):
            add_scan(consensus, PRICE, NOW - timedelta(days=d))
        db.expire(consensus)

        result = process_consensus(db, consensus, CFG, NOW, dry_run=False)

        assert result == "updated"
        db.expire(consensus)
        assert consensus.trust_score == Decimal("100.00")

    def test_computed_at_updated(self, db, make_consensus, add_scan):
        consensus = make_consensus(PRICE, Decimal("50.00"), NOW - timedelta(days=1))
        add_scan(consensus, PRICE, NOW - timedelta(days=1))
        process_consensus(db, consensus, CFG, NOW, dry_run=False)
        db.expire(consensus)
        # computed_at should be very close to NOW (stripped of tz by DB)
        assert consensus.computed_at is not None

    def test_frozen_consensus_skipped(self, db, make_consensus, add_scan):
        frozen_until = NOW + timedelta(hours=12)
        consensus = make_consensus(PRICE, Decimal("80.00"), NOW - timedelta(days=1), frozen_until=frozen_until)
        add_scan(consensus, OTHER, NOW - timedelta(hours=1))  # divergent — would change score

        result = process_consensus(db, consensus, CFG, NOW, dry_run=False)

        assert result == "frozen"
        db.expire(consensus)
        assert consensus.trust_score == Decimal("80.00")  # unchanged
        assert consensus.frozen_until is not None

    def test_expired_freeze_cleared(self, db, make_consensus, add_scan):
        frozen_until = NOW - timedelta(hours=1)  # already past
        consensus = make_consensus(PRICE, Decimal("80.00"), NOW - timedelta(days=1), frozen_until=frozen_until)
        add_scan(consensus, PRICE, NOW - timedelta(hours=2))

        result = process_consensus(db, consensus, CFG, NOW, dry_run=False)

        assert result != "frozen"
        db.expire(consensus)
        assert consensus.frozen_until is None

    def test_basculement_inserts_history(self, db, make_consensus, add_scan):
        consensus = make_consensus(PRICE_DB, Decimal("80.00"), NOW - timedelta(days=1))
        # 4 concordant old scans at PRICE_DB (with low weight)
        for d in [20, 18, 15, 12]:
            add_scan(consensus, PRICE_DB, NOW - timedelta(days=d))
        # 4 recent scans at OTHER_DB (high weight) — Pattern B will reduce old concordant
        for d in [3, 2, 1, 0]:
            add_scan(consensus, OTHER_DB, NOW - timedelta(days=d))

        result = process_consensus(db, consensus, CFG, NOW, dry_run=False)

        assert result == "basculement"
        db.expire(consensus)
        assert consensus.price == OTHER_DB
        history_count = db.query(PriceConsensusHistory).filter_by(consensus_id=consensus.id).count()
        assert history_count == 1

    def test_basculement_archives_old_price(self, db, make_consensus, add_scan):
        consensus = make_consensus(PRICE_DB, Decimal("80.00"), NOW - timedelta(days=1))
        for d in [20, 18, 15, 12]:
            add_scan(consensus, PRICE_DB, NOW - timedelta(days=d))
        for d in [3, 2, 1, 0]:
            add_scan(consensus, OTHER_DB, NOW - timedelta(days=d))
        process_consensus(db, consensus, CFG, NOW, dry_run=False)

        history = db.query(PriceConsensusHistory).filter_by(consensus_id=consensus.id).one()
        assert history.price == PRICE_DB

    def test_basculement_respects_seen_order_invariant(self, db, make_consensus, add_scan):
        """Bug 3 regression — basculement must bump ``last_seen_at`` so that
        the PG ``seen_order`` CHECK (first_seen_at <= last_seen_at) holds.

        Before the fix, basculement set ``first_seen_at = now`` while leaving
        ``last_seen_at`` at its pre-basculement value (older than ``now``),
        violating the invariant and crashing the batch on flush.
        """
        consensus = make_consensus(PRICE_DB, Decimal("80.00"), NOW - timedelta(days=1))
        for d in [20, 18, 15, 12]:
            add_scan(consensus, PRICE_DB, NOW - timedelta(days=d))
        for d in [3, 2, 1, 0]:
            add_scan(consensus, OTHER_DB, NOW - timedelta(days=d))

        result = process_consensus(db, consensus, CFG, NOW, dry_run=False)

        assert result == "basculement"
        db.expire(consensus)
        assert consensus.first_seen_at <= consensus.last_seen_at
        assert consensus.first_seen_at == NOW
        assert consensus.last_seen_at == NOW

    def test_basculement_with_recent_last_seen_at_keeps_invariant(self, db, make_consensus, add_scan):
        """Edge case — ``last_seen_at`` was very recent (e.g. equal to NOW)
        before basculement. Bumping it to ``now`` is still safe: the
        invariant holds either way, and the canonical writer in
        ``scan_repository.py`` always sets ``last_seen_at = now`` on touch.
        """
        # Pre-basculement: consensus already shows last_seen_at == NOW.
        consensus = make_consensus(PRICE_DB, Decimal("80.00"), NOW)
        for d in [20, 18, 15, 12]:
            add_scan(consensus, PRICE_DB, NOW - timedelta(days=d))
        for d in [3, 2, 1, 0]:
            add_scan(consensus, OTHER_DB, NOW - timedelta(days=d))

        result = process_consensus(db, consensus, CFG, NOW, dry_run=False)

        assert result == "basculement"
        db.expire(consensus)
        # Invariant maintained, freshness clock now anchored on the new price.
        assert consensus.first_seen_at <= consensus.last_seen_at
        assert consensus.last_seen_at == NOW

    def test_decay_applied_on_inactive_consensus(self, db, make_consensus, add_scan):
        last_seen = NOW - timedelta(days=8)  # 3 days past grace_days=5
        # Use the int-cents constant on both sides : the price columns are
        # INTEGER and a stray Decimal coercion can land 3.50€ as ``3`` on one
        # row and ``4`` on another, triggering a spurious basculement that
        # would bypass the decay path (Pattern A secondary cleanup).
        consensus = make_consensus(PRICE_DB, Decimal("85.00"), last_seen)
        # One old concordant scan (weight ~0.3 floor, very old)
        add_scan(consensus, PRICE_DB, last_seen)

        process_consensus(db, consensus, CFG, NOW, dry_run=False)

        db.expire(consensus)
        # Window trust_score should be 100% (only concordant scan) but then
        # decay(100, 8 days inactive, grace=5) = max(30, 100 - 3*10) = 70
        assert consensus.trust_score == Decimal("70.00")

    def test_dry_run_does_not_update_db(self, db, make_consensus, add_scan):
        consensus = make_consensus(PRICE, Decimal("50.00"), NOW - timedelta(days=1))
        add_scan(consensus, PRICE, NOW - timedelta(days=1))
        original_score = consensus.trust_score

        process_consensus(db, consensus, CFG, NOW, dry_run=True)

        # In dry_run, flush is skipped — the in-memory object may differ from DB
        # We verify by rolling back and re-reading
        db.expire(consensus)
        # Since dry_run skips flush, the DB row has not changed
        reloaded = db.get(type(consensus), consensus.id)
        assert reloaded.trust_score == original_score

    def test_empty_window_returns_updated(self, db, make_consensus):
        """Consensus with no linked scans is a no-op."""
        consensus = make_consensus(PRICE, Decimal("50.00"), NOW - timedelta(days=1))
        result = process_consensus(db, consensus, CFG, NOW, dry_run=False)
        assert result == "updated"
        db.expire(consensus)
        assert consensus.trust_score == Decimal("50.00")

    def test_dry_run_basculement_no_history(self, db, make_consensus, add_scan):
        """dry_run=True with basculement must not insert PriceConsensusHistory."""
        consensus = make_consensus(PRICE_DB, Decimal("80.00"), NOW - timedelta(days=1))
        for d in [20, 18, 15, 12]:
            add_scan(consensus, PRICE_DB, NOW - timedelta(days=d))
        for d in [3, 2, 1, 0]:
            add_scan(consensus, OTHER_DB, NOW - timedelta(days=d))

        result = process_consensus(db, consensus, CFG, NOW, dry_run=True)

        assert result == "basculement"
        history_count = db.query(PriceConsensusHistory).filter_by(consensus_id=consensus.id).count()
        assert history_count == 0
        # consensus.price must also be unchanged in DB
        db.expire(consensus)
        assert consensus.price == PRICE_DB

    def test_dry_run_basculement_does_not_decay_on_stale_last_seen_at(self, db, make_consensus, add_scan):
        """A basculement resets the freshness clock. In dry-run ``last_seen_at``
        is not bumped on the object, so naive ``(now - last_seen_at).days``
        would wrongly decay (down to the floor) the score of a row that just
        switched price.

        With the fix, days_inactive is 0 on a basculement regardless of
        dry_run, so the dry-run score stays at the dominant_score — well
        above the decay_floor of 30.
        """
        # last_seen_at well past the decay grace window (5 days) — without the
        # fix, dry-run would compute days_inactive=30 and floor the score.
        stale = NOW - timedelta(days=30)
        consensus = make_consensus(PRICE_DB, Decimal("80.00"), stale)
        for d in [20, 18, 15, 12]:
            add_scan(consensus, PRICE_DB, NOW - timedelta(days=d))
        for d in [3, 2, 1, 0]:
            add_scan(consensus, OTHER_DB, NOW - timedelta(days=d))

        result = process_consensus(db, consensus, CFG, NOW, dry_run=True)

        assert result == "basculement"
        # No spurious decay: a basculement just reset the freshness clock,
        # so the score must not be dragged down to the decay_floor.
        assert consensus.trust_score > Decimal(str(CFG["decay_floor"]))
