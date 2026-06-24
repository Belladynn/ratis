"""Tests for upsert_price_consensus — ARCH_consensus logic.

Covers:
- Min creation gate (2 concordant scans, 2 distinct users)
- Ticket age check (> 7 days → skip)
- Price quarantine (±30% outside consensus → skip)
- Trust score calculation (weighted ratio, window=20)
- Freeze trigger (3 concordant in last 24h → frozen_until set)
- Frozen consensus: scan recorded but trust_score not recalculated
- Price basculement: dominant price switches → old archived in history, new takes over
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from itertools import count as _count

_SCAN_OFFSET = _count(0)  # microsecond offset for unique scanned_at

from ratis_core.models.price import PriceConsensus, PriceConsensusScans
from ratis_core.models.scan import Receipt, Scan
from ratis_core.models.user import User
from sqlalchemy import select

# ============================================================
# Helpers
# ============================================================


def _make_user(db) -> User:
    uid = uuid.uuid4()
    u = User(
        id=uid,
        email=f"{uid.hex[:8]}@ratis.fr",
        account_type="oauth",
        is_deleted=False,
    )
    db.add(u)
    db.flush()
    return u


def _make_receipt(db, store, user, purchased_at: date | None = None) -> Receipt:
    r = Receipt(
        id=uuid.uuid4(),
        store_id=store.id,
        user_id=user.id,
        purchased_at=purchased_at or date.today(),
        image_r2_key=f"{uuid.uuid4()}.jpg",
    )
    db.add(r)
    db.flush()
    return r


def _make_scan(
    db,
    store,
    user,
    product,
    price: str = "3.50",
    receipt=None,
    status: str = "accepted",
    created_at: datetime | None = None,
) -> Scan:
    from sqlalchemy import text

    # CHECKs ``receipt_required`` + ``manual_no_scanned_name`` :
    #   - receipt-typed scans must have receipt_id (caller passes a Receipt)
    #   - manual-typed scans must have scanned_name=NULL + product_ean NOT NULL
    is_receipt = receipt is not None
    s = Scan(
        id=uuid.uuid4(),
        store_id=store.id,
        user_id=user.id,
        receipt_id=receipt.id if is_receipt else None,
        scan_type="receipt" if is_receipt else "manual",
        status=status,
        scanned_name="Nutella 400g" if is_receipt else None,
        price=round(Decimal(str(price)) * 100),
        quantity=1.0,
        product_ean=product.ean,
    )
    db.add(s)
    db.flush()

    # Always set a unique scanned_at to avoid the (user_id, store_id, product_ean, scanned_at) constraint
    ts = created_at if created_at is not None else datetime.now(UTC)
    ts = ts + timedelta(microseconds=next(_SCAN_OFFSET))
    db.execute(
        text("UPDATE scans SET scanned_at = :ts WHERE id = :id"),
        {"ts": ts, "id": s.id},
    )
    db.expire(s)

    return s


def _consensus_for(db, store, product) -> PriceConsensus | None:
    return db.scalar(
        select(PriceConsensus).where(
            PriceConsensus.store_id == store.id,
            PriceConsensus.product_ean == product.ean,
        )
    )


def _linked_scan_count(db, consensus_id) -> int:
    return (
        db.scalar(select(PriceConsensusScans).where(PriceConsensusScans.consensus_id == consensus_id)) is not None
        and db.execute(select(PriceConsensusScans.id).where(PriceConsensusScans.consensus_id == consensus_id))
        .fetchall()
        .__len__()
    )


# ============================================================
# Min creation gate
# ============================================================


class TestMinCreationGate:
    def test_single_scan_does_not_create_consensus(self, db, store, user, product):
        from repositories.scan_repository import upsert_price_consensus

        receipt = _make_receipt(db, store, user)
        scan = _make_scan(db, store, user, product, receipt=receipt)

        upsert_price_consensus(db, scan)

        assert _consensus_for(db, store, product) is None

    def test_two_scans_same_user_does_not_create_consensus(self, db, store, user, product):
        from repositories.scan_repository import upsert_price_consensus

        receipt1 = _make_receipt(db, store, user)
        receipt2 = _make_receipt(db, store, user)
        scan1 = _make_scan(db, store, user, product, receipt=receipt1)
        scan2 = _make_scan(db, store, user, product, receipt=receipt2)

        upsert_price_consensus(db, scan1)
        upsert_price_consensus(db, scan2)

        assert _consensus_for(db, store, product) is None

    def test_two_scans_distinct_users_creates_consensus(self, db, store, user, product):
        from repositories.scan_repository import upsert_price_consensus

        user2 = _make_user(db)
        receipt1 = _make_receipt(db, store, user)
        receipt2 = _make_receipt(db, store, user2)
        scan1 = _make_scan(db, store, user, product, receipt=receipt1)
        scan2 = _make_scan(db, store, user2, product, receipt=receipt2)

        upsert_price_consensus(db, scan1)
        upsert_price_consensus(db, scan2)

        consensus = _consensus_for(db, store, product)
        assert consensus is not None
        assert consensus.price == 350

    def test_both_scans_linked_when_consensus_created(self, db, store, user, product):
        from repositories.scan_repository import upsert_price_consensus

        user2 = _make_user(db)
        receipt1 = _make_receipt(db, store, user)
        receipt2 = _make_receipt(db, store, user2)
        scan1 = _make_scan(db, store, user, product, receipt=receipt1)
        scan2 = _make_scan(db, store, user2, product, receipt=receipt2)

        upsert_price_consensus(db, scan1)
        upsert_price_consensus(db, scan2)

        consensus = _consensus_for(db, store, product)
        rows = (
            db.execute(select(PriceConsensusScans.scan_id).where(PriceConsensusScans.consensus_id == consensus.id))
            .scalars()
            .all()
        )
        assert set(rows) == {scan1.id, scan2.id}

    def test_non_accepted_scan_ignored(self, db, store, user, product):
        from repositories.scan_repository import upsert_price_consensus

        user2 = _make_user(db)
        receipt = _make_receipt(db, store, user)
        receipt2 = _make_receipt(db, store, user2)
        scan1 = _make_scan(db, store, user, product, receipt=receipt, status="unmatched")
        scan2 = _make_scan(db, store, user2, product, receipt=receipt2, status="accepted")

        upsert_price_consensus(db, scan1)
        upsert_price_consensus(db, scan2)

        assert _consensus_for(db, store, product) is None


# ============================================================
# Ticket age check
# ============================================================


class TestTicketAgeCheck:
    def test_old_receipt_scan_skipped(self, db, store, user, product):
        from repositories.scan_repository import upsert_price_consensus

        user2 = _make_user(db)
        old_date = date.today() - timedelta(days=8)  # > 7 days
        receipt1 = _make_receipt(db, store, user, purchased_at=old_date)
        receipt2 = _make_receipt(db, store, user2, purchased_at=old_date)
        scan1 = _make_scan(db, store, user, product, receipt=receipt1)
        scan2 = _make_scan(db, store, user2, product, receipt=receipt2)

        upsert_price_consensus(db, scan1)
        upsert_price_consensus(db, scan2)

        assert _consensus_for(db, store, product) is None

    def test_7_day_receipt_scan_accepted(self, db, store, user, product):
        from repositories.scan_repository import upsert_price_consensus

        user2 = _make_user(db)
        today = date.today()
        receipt1 = _make_receipt(db, store, user, purchased_at=today - timedelta(days=7))
        receipt2 = _make_receipt(db, store, user2, purchased_at=today - timedelta(days=7))
        scan1 = _make_scan(db, store, user, product, receipt=receipt1)
        scan2 = _make_scan(db, store, user2, product, receipt=receipt2)

        upsert_price_consensus(db, scan1)
        upsert_price_consensus(db, scan2)

        # day 7 is still within the 7-day limit (strict >)
        assert _consensus_for(db, store, product) is not None

    def test_manual_scan_not_age_checked(self, db, store, user, product):
        """Manual scans (no receipt) bypass the ticket age check."""
        from repositories.scan_repository import upsert_price_consensus

        user2 = _make_user(db)
        # no receipt → manual scan
        scan1 = _make_scan(db, store, user, product)
        scan2 = _make_scan(db, store, user2, product)

        upsert_price_consensus(db, scan1)
        upsert_price_consensus(db, scan2)

        assert _consensus_for(db, store, product) is not None


# ============================================================
# Price quarantine
# ============================================================


class TestPriceQuarantine:
    def _setup_consensus(self, db, store, user, product, price="3.50") -> PriceConsensus:
        """Helper to bootstrap a consensus with 2 distinct users."""
        from repositories.scan_repository import upsert_price_consensus

        user2 = _make_user(db)
        scan1 = _make_scan(db, store, user, product, price=price)
        scan2 = _make_scan(db, store, user2, product, price=price)
        upsert_price_consensus(db, scan1)
        upsert_price_consensus(db, scan2)
        return _consensus_for(db, store, product)

    def test_price_within_30pct_accepted(self, db, store, user, product):
        from repositories.scan_repository import upsert_price_consensus

        consensus = self._setup_consensus(db, store, user, product, price="3.50")
        assert consensus is not None

        user3 = _make_user(db)
        # 3.50 * 1.29 = 4.515 → within 30%
        scan3 = _make_scan(db, store, user3, product, price="4.51")
        upsert_price_consensus(db, scan3)

        rows = (
            db.execute(select(PriceConsensusScans.scan_id).where(PriceConsensusScans.consensus_id == consensus.id))
            .scalars()
            .all()
        )
        assert scan3.id in rows

    def test_price_above_30pct_quarantined(self, db, store, user, product):
        from repositories.scan_repository import upsert_price_consensus

        consensus = self._setup_consensus(db, store, user, product, price="3.50")

        user3 = _make_user(db)
        # 3.50 * 1.31 = 4.585 → 31% above → quarantine
        scan3 = _make_scan(db, store, user3, product, price="4.59")
        upsert_price_consensus(db, scan3)

        rows = (
            db.execute(select(PriceConsensusScans.scan_id).where(PriceConsensusScans.consensus_id == consensus.id))
            .scalars()
            .all()
        )
        assert scan3.id not in rows

    def test_price_below_30pct_quarantined(self, db, store, user, product):
        from repositories.scan_repository import upsert_price_consensus

        consensus = self._setup_consensus(db, store, user, product, price="3.50")

        user3 = _make_user(db)
        # 3.50 * 0.69 = 2.415 → 31% below → quarantine
        scan3 = _make_scan(db, store, user3, product, price="2.41")
        upsert_price_consensus(db, scan3)

        rows = (
            db.execute(select(PriceConsensusScans.scan_id).where(PriceConsensusScans.consensus_id == consensus.id))
            .scalars()
            .all()
        )
        assert scan3.id not in rows


# ============================================================
# Trust score
# ============================================================


class TestTrustScore:
    def test_all_concordant_score_is_100(self, db, store, user, product):
        from repositories.scan_repository import upsert_price_consensus

        user2 = _make_user(db)
        scan1 = _make_scan(db, store, user, product)
        scan2 = _make_scan(db, store, user2, product)
        upsert_price_consensus(db, scan1)
        upsert_price_consensus(db, scan2)

        consensus = _consensus_for(db, store, product)
        assert consensus.trust_score == Decimal("100.00")

    def test_one_discordant_scan_lowers_score(self, db, store, user, product):
        from repositories.scan_repository import upsert_price_consensus

        # Build consensus: 2 scans at 3.50
        user2 = _make_user(db)
        scan1 = _make_scan(db, store, user, product, price="3.50")
        scan2 = _make_scan(db, store, user2, product, price="3.50")
        upsert_price_consensus(db, scan1)
        upsert_price_consensus(db, scan2)

        # Third scan at different price
        user3 = _make_user(db)
        scan3 = _make_scan(db, store, user3, product, price="3.60")
        upsert_price_consensus(db, scan3)

        consensus = _consensus_for(db, store, product)
        # score < 100 because one scan disagrees
        assert consensus.trust_score < Decimal("100.00")
        assert consensus.trust_score > Decimal("0.00")

    def test_trust_score_decreases_with_age(self, db, store, user, product):
        """Older concordant scans carry less weight → score differs from fresh scans."""
        from repositories.scan_repository import upsert_price_consensus

        now = datetime.now(UTC)
        user2 = _make_user(db)

        # scan1 at J-5 (weight 0.50), scan2 at J-0 (weight 1.00), both concordant
        scan1 = _make_scan(db, store, user, product, price="3.50", created_at=now - timedelta(days=5))
        scan2 = _make_scan(db, store, user2, product, price="3.50", created_at=now)
        upsert_price_consensus(db, scan1)
        upsert_price_consensus(db, scan2)

        # All concordant → 100%
        consensus = _consensus_for(db, store, product)
        assert consensus.trust_score == Decimal("100.00")

        # Add a discordant scan at J-5 → score should be affected by weights
        user3 = _make_user(db)
        scan3 = _make_scan(db, store, user3, product, price="3.60", created_at=now - timedelta(days=5))
        upsert_price_consensus(db, scan3)

        # Recalculate: window has scan1(0.50, ✓), scan2(1.00, ✓), scan3(0.50, ✗)
        # concordants = 1.50, total = 2.00 → 75%
        consensus = db.merge(consensus)
        db.refresh(consensus)
        assert consensus.trust_score == Decimal("75.00")


# ============================================================
# Freeze logic
# ============================================================


class TestFreezeLogic:
    def _bootstrap(self, db, store, user, product, price="3.50"):
        """Create a 2-user consensus."""
        from repositories.scan_repository import upsert_price_consensus

        user2 = _make_user(db)
        scan1 = _make_scan(db, store, user, product, price=price)
        scan2 = _make_scan(db, store, user2, product, price=price)
        upsert_price_consensus(db, scan1)
        upsert_price_consensus(db, scan2)
        return _consensus_for(db, store, product)

    def test_freeze_triggered_after_3_concordant_in_24h(self, db, store, user, product):
        from repositories.scan_repository import upsert_price_consensus

        consensus = self._bootstrap(db, store, user, product)
        assert consensus.frozen_until is None  # 2 scans, not yet frozen

        user3 = _make_user(db)
        scan3 = _make_scan(db, store, user3, product)
        upsert_price_consensus(db, scan3)

        db.refresh(consensus)
        assert consensus.frozen_until is not None
        # frozen_until is about 24h from now
        now = datetime.now(UTC)
        delta = consensus.frozen_until - now
        assert timedelta(hours=23) < delta < timedelta(hours=25)

    def test_frozen_consensus_not_recalculated(self, db, store, user, product):
        from repositories.scan_repository import upsert_price_consensus

        consensus = self._bootstrap(db, store, user, product)

        # Freeze manually
        consensus.frozen_until = datetime.now(UTC) + timedelta(hours=12)
        db.flush()
        initial_score = consensus.trust_score

        # Scan with different price — should be recorded but trust_score unchanged
        user3 = _make_user(db)
        scan3 = _make_scan(db, store, user3, product, price="3.60")
        upsert_price_consensus(db, scan3)

        db.refresh(consensus)
        assert consensus.trust_score == initial_score

        # But the scan IS recorded
        rows = (
            db.execute(select(PriceConsensusScans.scan_id).where(PriceConsensusScans.consensus_id == consensus.id))
            .scalars()
            .all()
        )
        assert scan3.id in rows

    def test_expired_freeze_allows_recalculation(self, db, store, user, product):
        from repositories.scan_repository import upsert_price_consensus

        consensus = self._bootstrap(db, store, user, product)
        # Set frozen_until in the past
        consensus.frozen_until = datetime.now(UTC) - timedelta(hours=1)
        db.flush()

        user3 = _make_user(db)
        scan3 = _make_scan(db, store, user3, product, price="3.60")
        upsert_price_consensus(db, scan3)

        db.refresh(consensus)
        # trust_score should have been recalculated (one discordant scan → < 100%)
        assert consensus.trust_score < Decimal("100.00")


# ============================================================
# Price basculement
# ============================================================


class TestPriceBasculement:
    """When a new price accumulates more weight than the current one, the consensus
    switches: old state archived in price_consensus_history, new price takes over."""

    def _bootstrap(self, db, store, user, product, price="3.50", age_days=5):
        """Create a 2-user consensus with scans at a given age (for weight control)."""
        from repositories.scan_repository import upsert_price_consensus

        now = datetime.now(UTC)
        user2 = _make_user(db)
        scan1 = _make_scan(db, store, user, product, price=price, created_at=now - timedelta(days=age_days))
        scan2 = _make_scan(db, store, user2, product, price=price, created_at=now - timedelta(days=age_days))
        upsert_price_consensus(db, scan1)
        upsert_price_consensus(db, scan2)
        return _consensus_for(db, store, product)

    def test_basculement_switches_consensus_price(self, db, store, user, product):
        from repositories.scan_repository import upsert_price_consensus

        # Old consensus: 2 scans at J-5 (weight 0.50 each), price=3.50
        # score_3.50 = 1.00
        consensus = self._bootstrap(db, store, user, product, price="3.50", age_days=5)
        assert consensus is not None

        # 3 fresh scans at J-0 (weight 1.00 each), price=3.60
        # score_3.60 = 3.00 → 3.60 dominates
        now = datetime.now(UTC)
        for _ in range(3):
            u = _make_user(db)
            s = _make_scan(db, store, u, product, price="3.60", created_at=now)
            upsert_price_consensus(db, s)

        db.refresh(consensus)
        assert consensus.price == 360

    def test_basculement_archives_old_consensus_to_history(self, db, store, user, product):
        from ratis_core.models.price import PriceConsensusHistory
        from repositories.scan_repository import upsert_price_consensus

        consensus = self._bootstrap(db, store, user, product, price="3.50", age_days=5)
        original_first_seen_at = consensus.first_seen_at

        now = datetime.now(UTC)
        for _ in range(3):
            u = _make_user(db)
            s = _make_scan(db, store, u, product, price="3.60", created_at=now)
            upsert_price_consensus(db, s)

        history = db.scalar(select(PriceConsensusHistory).where(PriceConsensusHistory.consensus_id == consensus.id))
        assert history is not None
        assert history.price == 350
        # History preserves the old first_seen_at
        assert history.first_seen_at == original_first_seen_at

    def test_basculement_dates_are_contiguous(self, db, store, user, product):
        """history.last_seen_at == new consensus.first_seen_at (no gap, no overlap)."""
        from ratis_core.models.price import PriceConsensusHistory
        from repositories.scan_repository import upsert_price_consensus

        consensus = self._bootstrap(db, store, user, product, price="3.50", age_days=5)

        now = datetime.now(UTC)
        for _ in range(3):
            u = _make_user(db)
            s = _make_scan(db, store, u, product, price="3.60", created_at=now)
            upsert_price_consensus(db, s)

        history = db.scalar(select(PriceConsensusHistory).where(PriceConsensusHistory.consensus_id == consensus.id))
        db.refresh(consensus)

        assert history.last_seen_at == consensus.first_seen_at

    def test_basculement_resets_frozen_until(self, db, store, user, product):
        from repositories.scan_repository import upsert_price_consensus

        consensus = self._bootstrap(db, store, user, product, price="3.50", age_days=5)
        consensus.frozen_until = datetime.now(UTC) - timedelta(hours=1)
        db.flush()

        # 2 scans at J-0 (weight 1.00 each): score_3.60=2.00 vs score_3.50=1.00 → basculement
        now = datetime.now(UTC)
        for _ in range(2):
            u = _make_user(db)
            s = _make_scan(db, store, u, product, price="3.60", created_at=now)
            upsert_price_consensus(db, s)

        db.refresh(consensus)
        assert consensus.frozen_until is None

    def test_no_basculement_when_old_price_still_dominant(self, db, store, user, product):
        from ratis_core.models.price import PriceConsensusHistory
        from repositories.scan_repository import upsert_price_consensus

        # Old consensus: 3 fresh scans at J-0, price=3.50 (weight 1.00 each)
        # score_3.50 = 3.00 → dominant even after one 3.60 scan
        now = datetime.now(UTC)
        users = [_make_user(db) for _ in range(3)]
        scans = [_make_scan(db, store, u, product, price="3.50", created_at=now) for u in users]
        for s in scans:
            upsert_price_consensus(db, s)

        consensus = _consensus_for(db, store, product)
        assert consensus is not None

        u_new = _make_user(db)
        s_new = _make_scan(db, store, u_new, product, price="3.60", created_at=now)
        upsert_price_consensus(db, s_new)

        db.refresh(consensus)
        assert consensus.price == 350  # no switch

        history_count = (
            db.execute(select(PriceConsensusHistory.id).where(PriceConsensusHistory.consensus_id == consensus.id))
            .scalars()
            .all()
        )
        assert len(history_count) == 0
