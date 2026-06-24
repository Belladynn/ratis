"""Tests for individual achievement trigger handlers.

Each handler has the uniform signature ::

    (db, user_id, target, window_days, extra) -> bool

Cf docs/superpowers/specs/2026-05-09-achievements-v1-design.md § Handlers.
"""

from __future__ import annotations

from datetime import UTC


# ---------------------------------------------------------------------------
# Compute primitives — V1.1 (KP-76 fix foundation)
#
# 5 fundamental SQL building blocks shared by ``_eval_*`` (bool threshold)
# and ``_compute_*`` (live scalar progress).
# ---------------------------------------------------------------------------
class TestPrimitiveCountForUser:
    """``_count_for_user`` — generic ``COUNT(*) WHERE user_id=:uid AND ...``."""

    def test_returns_zero_when_no_rows(self, db, test_user):
        from ratis_core.models.scan import Scan
        from services.achievement_service import _count_for_user

        assert _count_for_user(db, test_user.id, Scan) == 0

    def test_counts_user_rows(self, db, test_user, accepted_scan_factory):
        from ratis_core.models.scan import Scan
        from services.achievement_service import _count_for_user

        for _ in range(3):
            accepted_scan_factory(user_id=test_user.id)
        assert _count_for_user(db, test_user.id, Scan) == 3

    def test_isolates_per_user(self, db, test_user, accepted_scan_factory):
        from ratis_core.models.scan import Scan
        from services.achievement_service import _count_for_user

        other = make_user(db)
        for _ in range(5):
            accepted_scan_factory(user_id=other)
        accepted_scan_factory(user_id=test_user.id)
        assert _count_for_user(db, test_user.id, Scan) == 1

    def test_applies_extra_where(self, db, test_user, scan_factory):
        from ratis_core.models.scan import Scan
        from services.achievement_service import _count_for_user

        scan_factory(user_id=test_user.id, status="accepted")
        scan_factory(user_id=test_user.id, status="accepted")
        scan_factory(user_id=test_user.id, status="rejected")
        assert _count_for_user(db, test_user.id, Scan, Scan.status == "accepted") == 2


class TestPrimitiveSumForUser:
    """``_sum_for_user`` — generic ``SUM(<col>) WHERE user_id=:uid AND ...``."""

    def _credit(self, db, user_id, amount_cents, status="confirmed"):
        from tests.conftest import make_cashback_credit

        make_cashback_credit(db, user_id=user_id, amount=amount_cents, status=status)
        db.commit()

    def test_returns_zero_when_no_rows(self, db, test_user):
        from ratis_core.models.rewards import CashbackTransaction
        from services.achievement_service import _sum_for_user

        assert _sum_for_user(db, test_user.id, CashbackTransaction, CashbackTransaction.amount) == 0

    def test_sums_amounts(self, db, test_user):
        from ratis_core.models.rewards import CashbackTransaction
        from services.achievement_service import _sum_for_user

        self._credit(db, test_user.id, 100)
        self._credit(db, test_user.id, 250)
        assert _sum_for_user(db, test_user.id, CashbackTransaction, CashbackTransaction.amount) == 350

    def test_isolates_per_user(self, db, test_user):
        from ratis_core.models.rewards import CashbackTransaction
        from services.achievement_service import _sum_for_user

        other = make_user(db)
        self._credit(db, other, 9_999)
        self._credit(db, test_user.id, 50)
        assert _sum_for_user(db, test_user.id, CashbackTransaction, CashbackTransaction.amount) == 50

    def test_applies_extra_where(self, db, test_user):
        from ratis_core.models.rewards import CashbackTransaction
        from services.achievement_service import _sum_for_user

        self._credit(db, test_user.id, 100, status="confirmed")
        self._credit(db, test_user.id, 200, status="refused")
        # Filter excludes ``refused`` → only the 100 c is summed.
        total = _sum_for_user(
            db,
            test_user.id,
            CashbackTransaction,
            CashbackTransaction.amount,
            CashbackTransaction.status.in_(("pending", "confirmed")),
        )
        assert total == 100


class TestPrimitiveCountDistinctForUser:
    """``_count_distinct_for_user`` — wraps a caller-built SELECT chain."""

    def test_counts_distinct_store_ids(self, db, test_user, accepted_scan_factory):
        from ratis_core.models.scan import Scan
        from services.achievement_service import _count_distinct_for_user
        from sqlalchemy import select

        # 3 scans across 2 distinct stores.
        store_a = make_store(db)
        store_b = make_store(db)
        accepted_scan_factory(user_id=test_user.id, store_id=store_a)
        accepted_scan_factory(user_id=test_user.id, store_id=store_a)
        accepted_scan_factory(user_id=test_user.id, store_id=store_b)
        base = select(Scan).where(
            Scan.user_id == test_user.id,
            Scan.store_id.isnot(None),
        )
        assert _count_distinct_for_user(db, test_user.id, Scan.store_id, base) == 2


class TestPrimitiveMaxStreakForUser:
    """``_max_streak_for_user`` — reads ``user_streaks.current_streak_days``."""

    def _set_streak(self, db, user_id, days):
        from sqlalchemy import text as _text

        db.execute(
            _text(
                "INSERT INTO user_streaks (user_id, current_streak_days, food_reserves) "
                "VALUES (:uid, :days, 0) "
                "ON CONFLICT (user_id) DO UPDATE SET current_streak_days = :days"
            ),
            {"uid": user_id, "days": days},
        )
        db.commit()

    def test_returns_zero_when_no_row(self, db, test_user):
        from services.achievement_service import _max_streak_for_user

        assert _max_streak_for_user(db, test_user.id) == 0

    def test_returns_value_when_row_exists(self, db, test_user):
        from services.achievement_service import _max_streak_for_user

        self._set_streak(db, test_user.id, 7)
        assert _max_streak_for_user(db, test_user.id) == 7


class TestPrimitiveFirstEventSeen:
    """``_first_event_seen`` — placeholder, always True."""

    def test_returns_true_for_any_event(self, db, test_user):
        from services.achievement_service import _first_event_seen

        assert _first_event_seen(db, test_user.id, "konami_code_entered") is True
        assert _first_event_seen(db, test_user.id, "app_opened_at_3am") is True


# ---------------------------------------------------------------------------
# _eval_scan_count — Task 2.2
# ---------------------------------------------------------------------------
class TestEvalScanCount:
    def test_below_threshold_returns_false(self, db, test_user, accepted_scan_factory):
        from services.achievement_service import _eval_scan_count

        for _ in range(4):
            accepted_scan_factory(user_id=test_user.id)
        assert _eval_scan_count(db, test_user.id, target=5, window_days=None, extra={}) is False

    def test_at_threshold_returns_true(self, db, test_user, accepted_scan_factory):
        from services.achievement_service import _eval_scan_count

        for _ in range(5):
            accepted_scan_factory(user_id=test_user.id)
        assert _eval_scan_count(db, test_user.id, target=5, window_days=None, extra={}) is True

    def test_above_threshold_returns_true(self, db, test_user, accepted_scan_factory):
        from services.achievement_service import _eval_scan_count

        for _ in range(10):
            accepted_scan_factory(user_id=test_user.id)
        assert _eval_scan_count(db, test_user.id, target=5, window_days=None, extra={}) is True

    def test_excludes_pending_and_unmatched(self, db, test_user, scan_factory):
        """Only ``accepted``/``matched`` scans count toward scan_count."""
        from services.achievement_service import _eval_scan_count

        scan_factory(user_id=test_user.id, status="pending")
        scan_factory(user_id=test_user.id, status="unmatched")
        scan_factory(user_id=test_user.id, status="rejected")
        assert _eval_scan_count(db, test_user.id, target=1, window_days=None, extra={}) is False


class TestComputeScanCount:
    """``_compute_scan_count`` returns the *current scalar count* (int)."""

    def test_returns_zero_when_no_scans(self, db, test_user):
        from services.achievement_service import _compute_scan_count

        assert _compute_scan_count(db, test_user.id, None, {}) == 0

    def test_returns_count_of_accepted_scans(self, db, test_user, accepted_scan_factory):
        from services.achievement_service import _compute_scan_count

        for _ in range(7):
            accepted_scan_factory(user_id=test_user.id)
        assert _compute_scan_count(db, test_user.id, None, {}) == 7

    def test_excludes_non_accepted_scans(self, db, test_user, scan_factory):
        from services.achievement_service import _compute_scan_count

        scan_factory(user_id=test_user.id, status="pending")
        scan_factory(user_id=test_user.id, status="rejected")
        scan_factory(user_id=test_user.id, status="accepted")
        assert _compute_scan_count(db, test_user.id, None, {}) == 1

    def test_honors_extra_window_scans_only(self, db, test_user, accepted_scan_factory):
        from datetime import datetime, timedelta

        from services.achievement_service import _compute_scan_count

        # Two scans now ; extra.window_since filters scans before that ts.
        accepted_scan_factory(user_id=test_user.id)
        accepted_scan_factory(user_id=test_user.id)
        future = datetime.now(UTC) + timedelta(days=1)
        # Future window-since → 0 scans satisfy ``scanned_at >= future``.
        assert (
            _compute_scan_count(
                db,
                test_user.id,
                None,
                {"window_scans_only": True, "window_since_iso": future},
            )
            == 0
        )
        # Past window-since → both scans count.
        past = datetime.now(UTC) - timedelta(days=1)
        assert (
            _compute_scan_count(
                db,
                test_user.id,
                None,
                {"window_scans_only": True, "window_since_iso": past},
            )
            == 2
        )


# ---------------------------------------------------------------------------
# Compute helpers for the 8 remaining handlers — V1.1 (KP-76)
# Each ``_compute_*`` wrapper returns the scalar value; the matching
# ``_eval_*`` test class above already covers the bool-threshold path.
# ---------------------------------------------------------------------------
class TestComputeSavingsEurTotal:
    def _credit(self, db, user_id, amount_cents, status="confirmed"):
        from tests.conftest import make_cashback_credit

        make_cashback_credit(db, user_id=user_id, amount=amount_cents, status=status)
        db.commit()

    def test_returns_zero_when_no_credits(self, db, test_user):
        from services.achievement_service import _compute_savings_eur_total

        assert _compute_savings_eur_total(db, test_user.id, None, {}) == 0

    def test_sums_pending_and_confirmed(self, db, test_user):
        from services.achievement_service import _compute_savings_eur_total

        self._credit(db, test_user.id, 100, status="pending")
        self._credit(db, test_user.id, 250, status="confirmed")
        assert _compute_savings_eur_total(db, test_user.id, None, {}) == 350

    def test_excludes_refused(self, db, test_user):
        from services.achievement_service import _compute_savings_eur_total

        self._credit(db, test_user.id, 100, status="confirmed")
        self._credit(db, test_user.id, 999, status="refused")
        assert _compute_savings_eur_total(db, test_user.id, None, {}) == 100


class TestComputeStreakDays:
    def _set_streak(self, db, user_id, days):
        from sqlalchemy import text as _text

        db.execute(
            _text(
                "INSERT INTO user_streaks (user_id, current_streak_days, food_reserves) "
                "VALUES (:uid, :days, 0) "
                "ON CONFLICT (user_id) DO UPDATE SET current_streak_days = :days"
            ),
            {"uid": user_id, "days": days},
        )
        db.commit()

    def test_returns_zero_when_no_row(self, db, test_user):
        from services.achievement_service import _compute_streak_days

        assert _compute_streak_days(db, test_user.id, None, {}) == 0

    def test_returns_current_streak(self, db, test_user):
        from services.achievement_service import _compute_streak_days

        self._set_streak(db, test_user.id, 14)
        assert _compute_streak_days(db, test_user.id, None, {}) == 14


class TestComputeReferralCount:
    def _make_order(self, db, *, user_id, eligible_at):
        import uuid

        from sqlalchemy import text as _text

        bid = make_gift_card_brand(db)
        db.execute(
            _text(
                "INSERT INTO gift_card_orders "
                "  (id, user_id, brand_id, denomination, status, source_type, "
                "   source_ref_id, eligible_at, created_at) "
                "VALUES (:id, :uid, :bid, 2000, 'pending', 'referral_reward', "
                "        :sref, :elig, now())"
            ),
            {
                "id": uuid.uuid4(),
                "uid": user_id,
                "bid": bid,
                "sref": uuid.uuid4().hex,
                "elig": eligible_at,
            },
        )
        db.commit()

    def test_returns_zero(self, db, test_user):
        from services.achievement_service import _compute_referral_count

        assert _compute_referral_count(db, test_user.id, None, {}) == 0

    def test_counts_eligible_orders(self, db, test_user):
        from datetime import datetime

        from services.achievement_service import _compute_referral_count

        self._make_order(db, user_id=test_user.id, eligible_at=datetime.now(UTC))
        self._make_order(db, user_id=test_user.id, eligible_at=datetime.now(UTC))
        # Pending one (eligible_at NULL) — must NOT count.
        self._make_order(db, user_id=test_user.id, eligible_at=None)
        assert _compute_referral_count(db, test_user.id, None, {}) == 2


class TestComputeUniqueBrandsCount:
    def test_returns_zero(self, db, test_user):
        from services.achievement_service import _compute_unique_brands_count

        assert _compute_unique_brands_count(db, test_user.id, None, {}) == 0

    def test_counts_distinct_stores(self, db, test_user, accepted_scan_factory):
        from services.achievement_service import _compute_unique_brands_count

        store_a = make_store(db)
        store_b = make_store(db)
        # 3 scans across 2 distinct stores → expect 2.
        accepted_scan_factory(user_id=test_user.id, store_id=store_a)
        accepted_scan_factory(user_id=test_user.id, store_id=store_a)
        accepted_scan_factory(user_id=test_user.id, store_id=store_b)
        assert _compute_unique_brands_count(db, test_user.id, None, {}) == 2


class TestComputeUniqueCategoriesCount:
    def _make_category(self, db, name):
        import uuid

        from sqlalchemy import text as _text

        cid = uuid.uuid4()
        db.execute(
            _text("INSERT INTO categories (id, name) VALUES (:id, :name)"),
            {"id": cid, "name": f"{name}-{cid.hex[:6]}"},
        )
        db.commit()
        return cid

    def _product_with_category(self, db, *, category_id):
        import uuid

        from sqlalchemy import text as _text

        ean = str(uuid.uuid4().int)[:13]
        db.execute(
            _text(
                "INSERT INTO products (ean, name, source, category_id, "
                "                     created_at, updated_at) "
                "VALUES (:ean, 'p', 'off', :cid, now(), now())"
            ),
            {"ean": ean, "cid": category_id},
        )
        db.commit()
        return ean

    def test_returns_zero(self, db, test_user):
        from services.achievement_service import _compute_unique_categories_count

        assert _compute_unique_categories_count(db, test_user.id, None, {}) == 0

    def test_counts_distinct_categories_via_join(self, db, test_user, accepted_scan_factory):
        from services.achievement_service import _compute_unique_categories_count

        for i in range(3):
            cat = self._make_category(db, f"cat_{i}")
            ean = self._product_with_category(db, category_id=cat)
            accepted_scan_factory(user_id=test_user.id, product_ean=ean)
        assert _compute_unique_categories_count(db, test_user.id, None, {}) == 3


class TestComputeUniqueProductsDiscoveredCount:
    def _attribute(self, db, user_id, count):
        import uuid

        from sqlalchemy import text as _text

        for _ in range(count):
            ean = str(uuid.uuid4().int)[:13]
            db.execute(
                _text(
                    "INSERT INTO products "
                    "    (ean, name, source, first_discovered_by_user_id, "
                    "     created_at, updated_at) "
                    "VALUES (:ean, 'p', 'off', :uid, now(), now())"
                ),
                {"ean": ean, "uid": user_id},
            )
        db.commit()

    def test_returns_zero(self, db, test_user):
        from services.achievement_service import _compute_unique_products_discovered_count

        assert _compute_unique_products_discovered_count(db, test_user.id, None, {}) == 0

    def test_returns_attribution_count(self, db, test_user):
        from services.achievement_service import _compute_unique_products_discovered_count

        self._attribute(db, test_user.id, 6)
        assert _compute_unique_products_discovered_count(db, test_user.id, None, {}) == 6


class TestComputeFirstEvent:
    def test_returns_zero_for_unlocked_path(self, db, test_user):
        """Pre-unlock progress is 0 (handler primitive returns 0 for the
        live progress field — the dispatcher never invokes it once unlocked).
        """
        from services.achievement_service import _compute_first_event

        assert _compute_first_event(db, test_user.id, None, {"event": "konami_code_entered"}) == 0


class TestComputeSavingsEurInWindow:
    def _credit(self, db, user_id, amount_cents, *, days_ago=0, status="confirmed"):
        from tests.conftest import make_cashback_credit

        make_cashback_credit(db, user_id=user_id, amount=amount_cents, status=status, days_ago=days_ago)
        db.commit()

    def test_returns_zero_when_window_none(self, db, test_user):
        from services.achievement_service import _compute_savings_eur_in_window

        self._credit(db, test_user.id, 5_000)
        # window_days=None → 0 (unbounded variant lives in _compute_savings_eur_total).
        assert _compute_savings_eur_in_window(db, test_user.id, None, {}) == 0

    def test_sums_inside_window(self, db, test_user):
        from services.achievement_service import _compute_savings_eur_in_window

        self._credit(db, test_user.id, 1_500, days_ago=0)
        self._credit(db, test_user.id, 999, days_ago=10)  # outside 1-day window.
        assert _compute_savings_eur_in_window(db, test_user.id, 1, {}) == 1_500


# ---------------------------------------------------------------------------
# _eval_savings_eur_total — Task 2.3.a
# ---------------------------------------------------------------------------
class TestEvalSavingsEurTotal:
    """``CashbackTransaction.amount`` is in cents — handler sums all CREDIT
    transactions in status ``pending`` or ``confirmed`` (``refused`` excluded
    explicitly — the data model mutates the row's status in place rather
    than emitting a compensating DEBIT row).
    """

    def _credit(self, db, user_id, amount_cents, status="confirmed"):
        from tests.conftest import make_cashback_credit

        make_cashback_credit(db, user_id=user_id, amount=amount_cents, status=status)
        db.commit()

    def test_below_threshold(self, db, test_user):
        from services.achievement_service import _eval_savings_eur_total

        self._credit(db, test_user.id, 99)
        assert _eval_savings_eur_total(db, test_user.id, target=100, window_days=None, extra={}) is False

    def test_at_threshold(self, db, test_user):
        from services.achievement_service import _eval_savings_eur_total

        self._credit(db, test_user.id, 100)
        assert _eval_savings_eur_total(db, test_user.id, target=100, window_days=None, extra={}) is True

    def test_above_threshold(self, db, test_user):
        from services.achievement_service import _eval_savings_eur_total

        self._credit(db, test_user.id, 250)
        assert _eval_savings_eur_total(db, test_user.id, target=100, window_days=None, extra={}) is True

    def test_ignores_other_users(self, db, test_user):
        """Cashback credited to another user must not count toward this user's total."""
        from services.achievement_service import _eval_savings_eur_total

        other_uid = make_user(db)
        self._credit(db, other_uid, 5_000)
        assert _eval_savings_eur_total(db, test_user.id, target=100, window_days=None, extra={}) is False

    def test_excludes_refused_status(self, db, test_user):
        """Regression — ``refused`` cashback rows must NOT contribute.

        The data model updates ``cashback_transactions.status`` in place when
        a refusal arrives (``cashback_service.resolve_*``) — there is no
        compensating DEBIT row. Without an explicit ``status != 'refused'``
        filter the handler over-counts and unlocks achievements that were
        never actually earned.
        """
        from services.achievement_service import _eval_savings_eur_total

        self._credit(db, test_user.id, 5_000, status="confirmed")  # 50 €
        self._credit(db, test_user.id, 10_000, status="refused")  # 100 € refused
        # Only the 5_000 confirmed cents count → fails a 10_000 c target.
        assert _eval_savings_eur_total(db, test_user.id, target=10_000, window_days=None, extra={}) is False
        # But succeeds at the 5_000 c target — proves we still count the
        # confirmed row (i.e. the filter is not over-aggressive).
        assert _eval_savings_eur_total(db, test_user.id, target=5_000, window_days=None, extra={}) is True

    def test_includes_pending_status(self, db, test_user):
        """``pending`` rows must still count — mirrors FE 'savings to date'."""
        from services.achievement_service import _eval_savings_eur_total

        self._credit(db, test_user.id, 1_000, status="pending")
        assert _eval_savings_eur_total(db, test_user.id, target=1_000, window_days=None, extra={}) is True


# ---------------------------------------------------------------------------
# _eval_streak_days — Task 2.3.b
# ---------------------------------------------------------------------------
class TestEvalStreakDays:
    """Reads ``user_streaks.current_streak_days`` (denorm Feed Jack column)."""

    def _set_streak(self, db, user_id, days):
        from sqlalchemy import text as _text

        db.execute(
            _text(
                "INSERT INTO user_streaks (user_id, current_streak_days, food_reserves) "
                "VALUES (:uid, :days, 0) "
                "ON CONFLICT (user_id) DO UPDATE SET current_streak_days = :days"
            ),
            {"uid": user_id, "days": days},
        )
        db.commit()

    def test_below_threshold(self, db, test_user):
        from services.achievement_service import _eval_streak_days

        self._set_streak(db, test_user.id, 2)
        assert _eval_streak_days(db, test_user.id, target=3, window_days=None, extra={}) is False

    def test_at_threshold(self, db, test_user):
        from services.achievement_service import _eval_streak_days

        self._set_streak(db, test_user.id, 3)
        assert _eval_streak_days(db, test_user.id, target=3, window_days=None, extra={}) is True

    def test_above_threshold(self, db, test_user):
        from services.achievement_service import _eval_streak_days

        self._set_streak(db, test_user.id, 10)
        assert _eval_streak_days(db, test_user.id, target=3, window_days=None, extra={}) is True

    def test_no_streak_row_returns_false(self, db, test_user):
        """User without a user_streaks row → 0 streak → False."""
        from services.achievement_service import _eval_streak_days

        assert _eval_streak_days(db, test_user.id, target=1, window_days=None, extra={}) is False


# ---------------------------------------------------------------------------
# _eval_referral_count — Task 2.3.c
# ---------------------------------------------------------------------------
class TestEvalReferralCount:
    """Counts ``gift_card_orders`` rows where ``source_type='referral_reward'``
    AND ``eligible_at IS NOT NULL`` for the referrer (= user_id field).
    """

    def _make_referral_order(self, db, *, user_id, eligible_at):
        import uuid

        from sqlalchemy import text as _text

        bid = make_gift_card_brand(db)
        order_id = uuid.uuid4()
        db.execute(
            _text(
                "INSERT INTO gift_card_orders "
                "  (id, user_id, brand_id, denomination, status, source_type, "
                "   source_ref_id, eligible_at, created_at) "
                "VALUES (:id, :uid, :bid, 2000, 'pending', 'referral_reward', "
                "        :sref, :elig, now())"
            ),
            {
                "id": order_id,
                "uid": user_id,
                "bid": bid,
                "sref": uuid.uuid4().hex,
                "elig": eligible_at,
            },
        )
        db.commit()
        return order_id

    def test_zero_referrals(self, db, test_user):
        from services.achievement_service import _eval_referral_count

        assert _eval_referral_count(db, test_user.id, target=1, window_days=None, extra={}) is False

    def test_one_eligible_referral_meets_target(self, db, test_user):
        from datetime import datetime

        from services.achievement_service import _eval_referral_count

        self._make_referral_order(db, user_id=test_user.id, eligible_at=datetime.now(UTC))
        assert _eval_referral_count(db, test_user.id, target=1, window_days=None, extra={}) is True

    def test_pending_referral_does_not_count(self, db, test_user):
        """``eligible_at IS NULL`` → still pending the 30-day anti-churn delay."""
        from services.achievement_service import _eval_referral_count

        self._make_referral_order(db, user_id=test_user.id, eligible_at=None)
        assert _eval_referral_count(db, test_user.id, target=1, window_days=None, extra={}) is False


# ---------------------------------------------------------------------------
# _eval_unique_brands_count — Task 2.3.d
# ---------------------------------------------------------------------------
class TestEvalUniqueBrandsCount:
    """Counts distinct ``store_id`` across the user's accepted/matched scans.

    The "brand" here is the *store* brand (Carrefour, Leclerc, ...), not the
    product brand — that maps to "scan dans 5 enseignes différentes".
    """

    def test_below_threshold_one_store(self, db, test_user, accepted_scan_factory):
        from services.achievement_service import _eval_unique_brands_count

        store = make_store(db)
        for _ in range(4):
            accepted_scan_factory(user_id=test_user.id, store_id=store)
        assert _eval_unique_brands_count(db, test_user.id, target=5, window_days=None, extra={}) is False

    def test_at_threshold_distinct_stores(self, db, test_user, accepted_scan_factory):
        from services.achievement_service import _eval_unique_brands_count

        for _ in range(5):
            accepted_scan_factory(user_id=test_user.id)  # new store each time
        assert _eval_unique_brands_count(db, test_user.id, target=5, window_days=None, extra={}) is True


# ---------------------------------------------------------------------------
# _eval_unique_categories_count — Task 2.3.e
# ---------------------------------------------------------------------------
class TestEvalUniqueCategoriesCount:
    """Counts distinct ``products.category_id`` reached by the user's
    accepted/matched scans (joined on ``scans.product_ean`` →
    ``products.ean``).
    """

    def _make_category(self, db, name):
        import uuid

        from sqlalchemy import text as _text

        cid = uuid.uuid4()
        db.execute(
            _text("INSERT INTO categories (id, name) VALUES (:id, :name)"),
            {"id": cid, "name": f"{name}-{cid.hex[:6]}"},
        )
        db.commit()
        return cid

    def _product_with_category(self, db, *, category_id):
        import uuid

        from sqlalchemy import text as _text

        ean = str(uuid.uuid4().int)[:13]
        db.execute(
            _text(
                "INSERT INTO products (ean, name, source, category_id, created_at, updated_at) "
                "VALUES (:ean, 'p', 'off', :cid, now(), now())"
            ),
            {"ean": ean, "cid": category_id},
        )
        db.commit()
        return ean

    def test_below_threshold(self, db, test_user, accepted_scan_factory):
        from services.achievement_service import _eval_unique_categories_count

        cat = self._make_category(db, "cat")
        ean = self._product_with_category(db, category_id=cat)
        accepted_scan_factory(user_id=test_user.id, product_ean=ean)
        assert _eval_unique_categories_count(db, test_user.id, target=2, window_days=None, extra={}) is False

    def test_at_threshold_with_distinct_categories(self, db, test_user, accepted_scan_factory):
        from services.achievement_service import _eval_unique_categories_count

        for i in range(2):
            cat = self._make_category(db, f"cat_{i}")
            ean = self._product_with_category(db, category_id=cat)
            accepted_scan_factory(user_id=test_user.id, product_ean=ean)
        assert _eval_unique_categories_count(db, test_user.id, target=2, window_days=None, extra={}) is True

    def test_ignores_products_without_category(self, db, test_user, accepted_scan_factory):
        from services.achievement_service import _eval_unique_categories_count

        ean = self._product_with_category(db, category_id=None)
        accepted_scan_factory(user_id=test_user.id, product_ean=ean)
        assert _eval_unique_categories_count(db, test_user.id, target=1, window_days=None, extra={}) is False


# ---------------------------------------------------------------------------
# _eval_unique_products_discovered_count — Task 2.3.f (V1.1 active — KP-75)
# ---------------------------------------------------------------------------
class TestEvalUniqueProductsDiscoveredCount:
    """Counts ``products.first_discovered_by_user_id == user_id``.

    Activated in V1.1 via migration ``20260510_2100_pfd`` and the
    ``ratis_core.products.claim_first_discovery`` helper wired into every
    scan-acceptance path. The handler powers the seed achievement
    ``exp_unknown_10`` (Pionnier·e).

    These tests build the discovery state directly via raw INSERT/UPDATE
    on ``products.first_discovered_by_user_id`` — they verify the
    handler's COUNT logic in isolation. End-to-end attribution via the
    scan path is covered by ``test_first_discovery_hooks.py`` in PA.
    """

    def _attribute_products_to_user(self, db, user_id, count):
        """Insert ``count`` products and attribute them to ``user_id``."""
        import uuid

        from sqlalchemy import text as _text

        for _ in range(count):
            ean = str(uuid.uuid4().int)[:13]
            db.execute(
                _text(
                    "INSERT INTO products "
                    "    (ean, name, source, first_discovered_by_user_id, "
                    "     created_at, updated_at) "
                    "VALUES (:ean, 'p', 'off', :uid, now(), now())"
                ),
                {"ean": ean, "uid": user_id},
            )
        db.commit()

    def test_below_threshold_returns_false(self, db, test_user):
        from services.achievement_service import _eval_unique_products_discovered_count

        self._attribute_products_to_user(db, test_user.id, 9)
        assert _eval_unique_products_discovered_count(db, test_user.id, target=10, window_days=None, extra={}) is False

    def test_at_threshold_returns_true(self, db, test_user):
        from services.achievement_service import _eval_unique_products_discovered_count

        self._attribute_products_to_user(db, test_user.id, 10)
        assert _eval_unique_products_discovered_count(db, test_user.id, target=10, window_days=None, extra={}) is True

    def test_above_threshold_returns_true(self, db, test_user):
        from services.achievement_service import _eval_unique_products_discovered_count

        self._attribute_products_to_user(db, test_user.id, 25)
        assert _eval_unique_products_discovered_count(db, test_user.id, target=10, window_days=None, extra={}) is True

    def test_zero_discoveries_returns_false(self, db, test_user):
        from services.achievement_service import _eval_unique_products_discovered_count

        # No products attributed — even at target=1 returns False.
        assert _eval_unique_products_discovered_count(db, test_user.id, target=1, window_days=None, extra={}) is False

    def test_other_users_discoveries_do_not_count(self, db, test_user):
        """Discoveries credited to another user must not bump our count."""
        import uuid

        from services.achievement_service import _eval_unique_products_discovered_count
        from sqlalchemy import text as _text

        # Spawn a second user and attribute 20 products to them.
        from tests.conftest import make_user

        other = make_user(db)
        for _ in range(20):
            ean = str(uuid.uuid4().int)[:13]
            db.execute(
                _text(
                    "INSERT INTO products "
                    "    (ean, name, source, first_discovered_by_user_id, "
                    "     created_at, updated_at) "
                    "VALUES (:ean, 'p', 'off', :uid, now(), now())"
                ),
                {"ean": ean, "uid": other},
            )
        db.commit()

        # test_user has zero attributions of their own — must return False.
        assert _eval_unique_products_discovered_count(db, test_user.id, target=1, window_days=None, extra={}) is False

    def test_unattributed_products_do_not_count(self, db, test_user):
        """OFF-seeded products with NULL discoverer must not skew the count."""
        import uuid

        from services.achievement_service import _eval_unique_products_discovered_count
        from sqlalchemy import text as _text

        # 50 unattributed products in DB (typical OFF dump).
        for _ in range(50):
            ean = str(uuid.uuid4().int)[:13]
            db.execute(
                _text(
                    "INSERT INTO products (ean, name, source, "
                    "                     created_at, updated_at) "
                    "VALUES (:ean, 'p', 'off', now(), now())"
                ),
                {"ean": ean},
            )
        # Plus 3 attributed to test_user.
        self._attribute_products_to_user(db, test_user.id, 3)

        # Target 5 → still False (only 3 attributed to us).
        assert _eval_unique_products_discovered_count(db, test_user.id, target=5, window_days=None, extra={}) is False
        # Target 3 → True (exact threshold).
        assert _eval_unique_products_discovered_count(db, test_user.id, target=3, window_days=None, extra={}) is True


# ---------------------------------------------------------------------------
# _eval_first_event — Task 2.3.g
# ---------------------------------------------------------------------------
class TestEvalFirstEvent:
    """Always True — discrimination by event_type is enforced by the SQL filter
    in ``check_achievements`` (``Achievement.extra_params['event'].astext == event_type``).
    """

    def test_returns_true(self, db, test_user):
        from services.achievement_service import _eval_first_event

        assert (
            _eval_first_event(db, test_user.id, target=1, window_days=None, extra={"event": "konami_code_entered"})
            is True
        )

    def test_returns_true_even_with_empty_extra(self, db, test_user):
        from services.achievement_service import _eval_first_event

        assert _eval_first_event(db, test_user.id, target=1, window_days=None, extra={}) is True


# ---------------------------------------------------------------------------
# _eval_savings_eur_in_window — Task 2.3.h (BATCH-ONLY)
# ---------------------------------------------------------------------------
class TestEvalSavingsEurInWindow:
    """Reserved to the nightly batch — sum ``cashback_transactions.amount``
    credited within the trailing ``window_days`` window.
    """

    def _credit(self, db, user_id, amount_cents, *, days_ago=0, status="confirmed"):
        from tests.conftest import make_cashback_credit

        make_cashback_credit(db, user_id=user_id, amount=amount_cents, status=status, days_ago=days_ago)
        db.commit()

    def test_zero_window_returns_false(self, db, test_user):
        from services.achievement_service import _eval_savings_eur_in_window

        self._credit(db, test_user.id, 5_000)
        assert _eval_savings_eur_in_window(db, test_user.id, target=2_000, window_days=None, extra={}) is False

    def test_inside_window_meets_target(self, db, test_user):
        from services.achievement_service import _eval_savings_eur_in_window

        self._credit(db, test_user.id, 2_000, days_ago=0)
        assert _eval_savings_eur_in_window(db, test_user.id, target=2_000, window_days=1, extra={}) is True

    def test_outside_window_does_not_count(self, db, test_user):
        from services.achievement_service import _eval_savings_eur_in_window

        self._credit(db, test_user.id, 5_000, days_ago=10)
        assert _eval_savings_eur_in_window(db, test_user.id, target=2_000, window_days=1, extra={}) is False

    def test_excludes_refused_status(self, db, test_user):
        """Regression — same status filter as ``_eval_savings_eur_total``.

        The batch handler must exclude ``refused`` rows for the same reason
        the event handler does (in-place status update, no compensating
        DEBIT row).
        """
        from services.achievement_service import _eval_savings_eur_in_window

        self._credit(db, test_user.id, 1_000, days_ago=0, status="confirmed")
        self._credit(db, test_user.id, 5_000, days_ago=0, status="refused")
        # Only the confirmed 1_000 c counts → fails 2_000 c target.
        assert _eval_savings_eur_in_window(db, test_user.id, target=2_000, window_days=1, extra={}) is False
        # But succeeds at 1_000 c — confirms the filter is not over-aggressive.
        assert _eval_savings_eur_in_window(db, test_user.id, target=1_000, window_days=1, extra={}) is True


def make_user(db):
    """Re-export to make tests above readable. Pulled lazily from conftest."""
    from tests.conftest import make_user as _mu

    return _mu(db)


def make_store(db):
    from tests.conftest import make_store as _ms

    return _ms(db)


def make_gift_card_brand(db):
    from tests.conftest import make_gift_card_brand as _mb

    return _mb(db)
