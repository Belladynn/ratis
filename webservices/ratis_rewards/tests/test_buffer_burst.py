"""Tests TDD pour Buffer + Burst (refonte Stonks).

Spec source-of-truth :
``docs/superpowers/specs/2026-05-09-buffer-burst-design.md``

24 cas TDD répartis en 6 catégories :

* Buffer (5)              — apply_buffer service + cap + lock + status
* Claim Buffer (8)        — multi-claim cumulatif + double gating
* Burst (4)               — déblocage paliers + XP exponentielle + lock
* Buffer ⊕ Burst (2)      — exclusion mutuelle après 1er Burst claim
* Leaderboard (2)         — monthly + all-time
* Migration (3)           — rename, add columns, drop stonks_records

TDD strict : tests rouges D'ABORD, implémentation ensuite.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta

import pytest
from repositories.cab_repository import get_balance
from repositories.xp_repository import get_xp_balance
from sqlalchemy import text

from tests.conftest import make_user

# ---------------------------------------------------------------------------
# Test helpers — dedicated to Buffer/Burst (pattern from test_stonks.py)
# ---------------------------------------------------------------------------


def _make_mission(
    db,
    *,
    action_type: str = "label_scan",
    frequency: str = "daily",
    difficulty: str = "easy",
    target_count: int = 3,
    cab_reward: int = 50,
    is_active: bool = True,
) -> uuid.UUID:
    """Insert a catalogue mission row."""
    mission_id = uuid.uuid4()
    db.execute(
        text(
            "INSERT INTO missions "
            "    (id, action_type, frequency, difficulty, "
            "     target_count, cab_reward, is_active) "
            "VALUES (:id, :action, :freq, :diff, "
            "        :target, :reward, :active)"
        ),
        {
            "id": mission_id,
            "action": action_type,
            "freq": frequency,
            "diff": difficulty,
            "target": target_count,
            "reward": cab_reward,
            "active": is_active,
        },
    )
    db.commit()
    return mission_id


def _make_user_mission(
    db,
    *,
    user_id: uuid.UUID,
    mission_id: uuid.UUID,
    status: str = "pending",
    current_count: int = 0,
    target_count: int = 3,
    cab_reward: int = 50,
    xp_reward: int = 10,
    buffer_count: int = 0,
    burst_count: int = 0,
    burst_locked: bool = False,
    portions_claimed: int = 0,
    period_extended_until=None,
    period_start: date | None = None,
) -> uuid.UUID:
    """Insert a user_missions row with full Buffer + Burst columns."""
    um_id = uuid.uuid4()
    if period_start is None:
        period_start = datetime.now(UTC).date()
    db.execute(
        text(
            "INSERT INTO user_missions "
            "    (id, user_id, mission_id, period_start, current_count, status, "
            "     target_count, cab_reward, xp_reward, buffer_count, burst_count, "
            "     burst_locked, portions_claimed, period_extended_until) "
            "VALUES (:id, :uid, :mid, :period, :count, :status, "
            "        :target, :cab, :xp, :buffer, :burst, "
            "        :locked, :claimed, :extended)"
        ),
        {
            "id": um_id,
            "uid": user_id,
            "mid": mission_id,
            "period": period_start,
            "count": current_count,
            "status": status,
            "target": target_count,
            "cab": cab_reward,
            "xp": xp_reward,
            "buffer": buffer_count,
            "burst": burst_count,
            "locked": burst_locked,
            "claimed": portions_claimed,
            "extended": period_extended_until,
        },
    )
    db.commit()
    return um_id


# ===========================================================================
# Buffer — apply_buffer service (5 tests)
# ===========================================================================


class TestApplyBuffer:
    def test_apply_buffer_increments_count_and_target(self, db):
        """Test 1 — n=0 → n=1, target × 2, cab_reward × 2, period_extended_until SET."""
        uid = make_user(db)
        mission_id = _make_mission(db, target_count=3, cab_reward=50)
        period_start = date(2026, 5, 1)
        um_id = _make_user_mission(
            db,
            user_id=uid,
            mission_id=mission_id,
            target_count=3,
            cab_reward=50,
            xp_reward=10,
            period_start=period_start,
        )

        from services.missions_service import apply_buffer

        apply_buffer(db, uid, um_id)
        db.flush()

        row = db.execute(
            text(
                "SELECT buffer_count, target_count, cab_reward, xp_reward, "
                "       period_extended_until "
                "FROM user_missions WHERE id = :id"
            ),
            {"id": um_id},
        ).first()
        assert row.buffer_count == 1
        assert row.target_count == 6  # 3 × 2
        assert row.cab_reward == 100  # 50 × 2 (linear scaling)
        assert int(row.xp_reward) == 10  # XP unchanged
        assert row.period_extended_until is not None
        # period_start + (n+1) days = period_start + 2 days
        expected = datetime(2026, 5, 3, tzinfo=UTC)
        assert row.period_extended_until == expected

    def test_apply_buffer_cap_reached(self, db):
        """Test 2 — buffer_count == 3 → 409 buffer_cap_reached."""
        uid = make_user(db)
        mission_id = _make_mission(db)
        um_id = _make_user_mission(
            db,
            user_id=uid,
            mission_id=mission_id,
            buffer_count=3,
        )

        from ratis_core.exceptions import Conflict
        from services.missions_service import apply_buffer

        with pytest.raises(Conflict, match="buffer_cap_reached"):
            apply_buffer(db, uid, um_id)

    def test_apply_buffer_weekly_refused(self, db):
        """Test 3 — frequency='weekly' → 400 weekly_not_bufferable."""
        uid = make_user(db)
        mission_id = _make_mission(db, frequency="weekly")
        um_id = _make_user_mission(db, user_id=uid, mission_id=mission_id)

        from ratis_core.exceptions import BadRequest
        from services.missions_service import apply_buffer

        with pytest.raises(BadRequest, match="weekly_not_bufferable"):
            apply_buffer(db, uid, um_id)

    def test_apply_buffer_blocked_after_burst_lock(self, db):
        """Test 4 — burst_locked=true → 409 burst_locked."""
        uid = make_user(db)
        mission_id = _make_mission(db)
        um_id = _make_user_mission(
            db,
            user_id=uid,
            mission_id=mission_id,
            burst_locked=True,
        )

        from ratis_core.exceptions import Conflict
        from services.missions_service import apply_buffer

        with pytest.raises(Conflict, match="burst_locked"):
            apply_buffer(db, uid, um_id)

    def test_apply_buffer_blocked_if_claimed(self, db):
        """Test 5 — status='claimed' → 409 mission_not_pending."""
        uid = make_user(db)
        mission_id = _make_mission(db)
        um_id = _make_user_mission(
            db,
            user_id=uid,
            mission_id=mission_id,
            status="claimed",
        )

        from ratis_core.exceptions import Conflict
        from services.missions_service import apply_buffer

        with pytest.raises(Conflict, match="mission_not_pending"):
            apply_buffer(db, uid, um_id)


# ===========================================================================
# Claim Buffer — multi-claim cumulatif + double gating (8 tests)
# ===========================================================================


class TestClaimBuffer:
    def test_claim_no_buffer_simple(self, db):
        """Test 6 — mission classique (n=0), claim 1R."""
        uid = make_user(db)
        mission_id = _make_mission(db, target_count=3, cab_reward=50)
        um_id = _make_user_mission(
            db,
            user_id=uid,
            mission_id=mission_id,
            current_count=3,
            target_count=3,
            cab_reward=50,
        )

        from services.missions_service import claim_mission

        result = claim_mission(db, uid, um_id)
        db.flush()

        assert result["cab_awarded"] == 50
        assert result["portions_claimed_total"] == 1
        assert get_balance(db, uid) == 50

        row = db.execute(
            text("SELECT status, portions_claimed FROM user_missions WHERE id = :id"),
            {"id": um_id},
        ).first()
        assert row.status == "claimed"
        assert row.portions_claimed == 1

    def test_claim_buffer_n2_full_target_day1_each_day(self, db):
        """Test 7 — n=2, target=12 ESL, fait 12 J1 → claim J1=1R, J2=1R, J3=1R."""
        uid = make_user(db)
        mission_id = _make_mission(db, target_count=3, cab_reward=50)
        # buffer 2× : target=12, cab_reward=150, period=3 jours
        period_start = date(2026, 5, 1)
        period_extended = datetime(2026, 5, 4, tzinfo=UTC)
        um_id = _make_user_mission(
            db,
            user_id=uid,
            mission_id=mission_id,
            current_count=12,
            target_count=12,
            cab_reward=150,
            buffer_count=2,
            period_start=period_start,
            period_extended_until=period_extended,
        )

        from services.missions_service import claim_mission

        # J1 — should be 1 portion = 1R = 50
        with _frozen_now(datetime(2026, 5, 1, 12, tzinfo=UTC)):
            r1 = claim_mission(db, uid, um_id)
        db.flush()
        assert r1["cab_awarded"] == 50
        assert r1["portions_claimed_total"] == 1

        # J2 — second claim, 1 more portion = 50
        with _frozen_now(datetime(2026, 5, 2, 12, tzinfo=UTC)):
            r2 = claim_mission(db, uid, um_id)
        db.flush()
        assert r2["cab_awarded"] == 50
        assert r2["portions_claimed_total"] == 2

        # J3 — third claim, 1 more portion = 50, mission close
        with _frozen_now(datetime(2026, 5, 3, 12, tzinfo=UTC)):
            r3 = claim_mission(db, uid, um_id)
        db.flush()
        assert r3["cab_awarded"] == 50
        assert r3["portions_claimed_total"] == 3
        # CAB total = 150
        assert get_balance(db, uid) == 150
        row = db.execute(
            text("SELECT status FROM user_missions WHERE id = :id"),
            {"id": um_id},
        ).first()
        assert row.status == "claimed"

    def test_claim_buffer_n2_full_target_claim_j3_cumulative(self, db):
        """Test 8 — n=2, fait 12 J1 → claim direct J3 = 3R d'un coup (150)."""
        uid = make_user(db)
        mission_id = _make_mission(db, target_count=3, cab_reward=50)
        period_start = date(2026, 5, 1)
        period_extended = datetime(2026, 5, 4, tzinfo=UTC)
        um_id = _make_user_mission(
            db,
            user_id=uid,
            mission_id=mission_id,
            current_count=12,
            target_count=12,
            cab_reward=150,
            buffer_count=2,
            period_start=period_start,
            period_extended_until=period_extended,
        )

        from services.missions_service import claim_mission

        with _frozen_now(datetime(2026, 5, 3, 12, tzinfo=UTC)):
            result = claim_mission(db, uid, um_id)
        db.flush()

        assert result["cab_awarded"] == 150  # 3R d'un coup
        assert result["portions_claimed_total"] == 3
        assert get_balance(db, uid) == 150

    def test_claim_buffer_n2_partial_target_10_of_12(self, db):
        """Test 9 — n=2, fait 10/12 → claim J3 = 2R seulement (P3 jamais atteint)."""
        uid = make_user(db)
        mission_id = _make_mission(db, target_count=3, cab_reward=50)
        # palier_size = 12/3 = 4 → paliers: 4, 8, 12. Avec 10 ESL → paliers atteints = 2
        period_start = date(2026, 5, 1)
        period_extended = datetime(2026, 5, 4, tzinfo=UTC)
        um_id = _make_user_mission(
            db,
            user_id=uid,
            mission_id=mission_id,
            current_count=10,
            target_count=12,
            cab_reward=150,
            buffer_count=2,
            period_start=period_start,
            period_extended_until=period_extended,
        )

        from services.missions_service import claim_mission

        with _frozen_now(datetime(2026, 5, 3, 12, tzinfo=UTC)):
            result = claim_mission(db, uid, um_id)
        db.flush()

        assert result["cab_awarded"] == 100  # 2R (R=50)
        assert result["portions_claimed_total"] == 2

    def test_claim_buffer_n2_partial_target_4_of_12(self, db):
        """Test 10 — n=2, fait 4/12 → claim J3 = 1R seulement (P1 atteint)."""
        uid = make_user(db)
        mission_id = _make_mission(db, target_count=3, cab_reward=50)
        period_start = date(2026, 5, 1)
        period_extended = datetime(2026, 5, 4, tzinfo=UTC)
        um_id = _make_user_mission(
            db,
            user_id=uid,
            mission_id=mission_id,
            current_count=4,
            target_count=12,
            cab_reward=150,
            buffer_count=2,
            period_start=period_start,
            period_extended_until=period_extended,
        )

        from services.missions_service import claim_mission

        with _frozen_now(datetime(2026, 5, 3, 12, tzinfo=UTC)):
            result = claim_mission(db, uid, um_id)
        db.flush()

        assert result["cab_awarded"] == 50  # 1R
        assert result["portions_claimed_total"] == 1

    def test_claim_buffer_no_progress_returns_402(self, db):
        """Test 11 — n=2, 0 ESL → 402 no_portion_available_now."""
        uid = make_user(db)
        mission_id = _make_mission(db, target_count=3, cab_reward=50)
        period_start = date(2026, 5, 1)
        period_extended = datetime(2026, 5, 4, tzinfo=UTC)
        um_id = _make_user_mission(
            db,
            user_id=uid,
            mission_id=mission_id,
            current_count=0,
            target_count=12,
            cab_reward=150,
            buffer_count=2,
            period_start=period_start,
            period_extended_until=period_extended,
        )

        from ratis_core.exceptions import PaymentRequired
        from services.missions_service import claim_mission

        with _frozen_now(datetime(2026, 5, 3, 12, tzinfo=UTC)):
            with pytest.raises(PaymentRequired, match="no_portion_available_now"):
                claim_mission(db, uid, um_id)

    def test_claim_buffer_already_claimed_returns_409(self, db):
        """Test 12 — toutes portions récoltées → 409 already_claimed."""
        uid = make_user(db)
        mission_id = _make_mission(db, target_count=3, cab_reward=50)
        period_start = date(2026, 5, 1)
        period_extended = datetime(2026, 5, 4, tzinfo=UTC)
        um_id = _make_user_mission(
            db,
            user_id=uid,
            mission_id=mission_id,
            current_count=12,
            target_count=12,
            cab_reward=150,
            buffer_count=2,
            portions_claimed=3,  # tout récolté
            status="claimed",
            period_start=period_start,
            period_extended_until=period_extended,
        )

        from ratis_core.exceptions import Conflict
        from services.missions_service import claim_mission

        with _frozen_now(datetime(2026, 5, 3, 12, tzinfo=UTC)):
            with pytest.raises(Conflict, match="already_claimed"):
                claim_mission(db, uid, um_id)

    def test_claim_buffer_expired_returns_410(self, db):
        """Test 13 — now > period_extended_until → 410 mission_expired."""
        uid = make_user(db)
        mission_id = _make_mission(db, target_count=3, cab_reward=50)
        period_start = date(2026, 5, 1)
        # deadline passée il y a 1 jour
        period_extended = datetime(2026, 5, 4, tzinfo=UTC)
        um_id = _make_user_mission(
            db,
            user_id=uid,
            mission_id=mission_id,
            current_count=12,
            target_count=12,
            cab_reward=150,
            buffer_count=2,
            period_start=period_start,
            period_extended_until=period_extended,
        )

        from ratis_core.exceptions import Gone
        from services.missions_service import claim_mission

        # claim J5 (= deadline était J3) → expired
        with _frozen_now(datetime(2026, 5, 5, 12, tzinfo=UTC)), pytest.raises(Gone, match="mission_expired"):
            claim_mission(db, uid, um_id)


# ===========================================================================
# Burst — déblocage paliers + XP exponentielle (4 tests)
# ===========================================================================


class TestBurst:
    def test_burst_palier_unlocked_after_target_doubled(self, db):
        """Test 14 — fait 24 ESL (= target × 2) sur mission 12 → palier 1 dispo."""
        uid = make_user(db)
        mission_id = _make_mission(db, target_count=12, cab_reward=50)
        um_id = _make_user_mission(
            db,
            user_id=uid,
            mission_id=mission_id,
            current_count=24,  # target × 2
            target_count=12,
            cab_reward=50,
            xp_reward=10,
        )

        from services.burst_service import compute_burst_paliers

        row = db.execute(
            text("SELECT current_count, target_count, burst_count FROM user_missions WHERE id = :id"),
            {"id": um_id},
        ).first()
        # Construct a lightweight namespace for the helper
        paliers = compute_burst_paliers(
            current_count=row.current_count,
            target_count=row.target_count,
            current_burst_count=row.burst_count,
        )
        # 24 / 12 = 2 → log2(2) = 1 → 1 palier débloqué (au-delà de
        # current_burst_count=0)
        assert paliers == 1

    def test_burst_claim_awards_exponential_xp(self, db):
        """Test 15 — claim N=2 paliers → xp = R_xp × (2^N − 1) = 30.

        Spec § Burst : ``xp_reward × 2^(N - 1)`` per palier N.
        Cumulative XP for paliers 1..N = ``xp_reward × (2^N − 1)``.
        Avec R_xp=10, N=2 → 10 × 3 = 30.
        """
        uid = make_user(db)
        mission_id = _make_mission(db, target_count=12, cab_reward=50)
        # Mission xp_reward=10, current_count=48 → 48/12 = 4 → log2(4)=2 paliers
        um_id = _make_user_mission(
            db,
            user_id=uid,
            mission_id=mission_id,
            current_count=48,
            target_count=12,
            cab_reward=50,
            xp_reward=10,
        )

        from services.burst_service import claim_burst

        result = claim_burst(db, uid, um_id)
        db.flush()

        # 2 paliers : palier 1 = 10 × 2^0 = 10, palier 2 = 10 × 2^1 = 20
        # Cumulative = 10 × (2^2 − 1) = 30.
        assert result["xp_awarded"] == 30
        assert result["burst_count_total"] == 2
        xp = get_xp_balance(db, uid)
        assert xp["balance"] == 30

    def test_burst_claim_locks_buffer(self, db):
        """Test 16 — après claim Burst, apply_buffer → 409 burst_locked."""
        uid = make_user(db)
        mission_id = _make_mission(db, target_count=12, cab_reward=50)
        um_id = _make_user_mission(
            db,
            user_id=uid,
            mission_id=mission_id,
            current_count=24,
            target_count=12,
            cab_reward=50,
            xp_reward=10,
        )

        from ratis_core.exceptions import Conflict
        from services.burst_service import claim_burst
        from services.missions_service import apply_buffer

        claim_burst(db, uid, um_id)
        db.commit()

        with pytest.raises(Conflict, match="burst_locked"):
            apply_buffer(db, uid, um_id)

    def test_burst_no_cab_additional(self, db):
        """Test 17 — claim Burst n'ajoute aucune cabecoin_transactions."""
        uid = make_user(db)
        mission_id = _make_mission(db, target_count=12, cab_reward=50)
        um_id = _make_user_mission(
            db,
            user_id=uid,
            mission_id=mission_id,
            current_count=24,
            target_count=12,
            cab_reward=50,
            xp_reward=10,
        )

        from services.burst_service import claim_burst

        claim_burst(db, uid, um_id)
        db.flush()

        count = db.execute(
            text("SELECT COUNT(*) FROM cabecoin_transactions WHERE user_id = :uid"),
            {"uid": uid},
        ).scalar()
        assert count == 0

    def test_compute_burst_paliers_hard_capped(self):
        """F-RW-gamif-8 — palier index is hard-capped to bound XP.

        Without a cap, a pathological ``current_count`` (or a corrupt
        target) makes ``log2(ratio)`` grow unbounded → ``xp_reward × 2^N``
        overflows into an absurd XP grant. The cap keeps the worst case
        finite.
        """
        from services.burst_service import BURST_PALIER_HARD_CAP, compute_burst_paliers

        # ratio = 2^60 → uncapped this would return 60.
        paliers = compute_burst_paliers(
            current_count=12 * (1 << 60),
            target_count=12,
            current_burst_count=0,
        )
        assert paliers == BURST_PALIER_HARD_CAP
        assert BURST_PALIER_HARD_CAP == 30


# ===========================================================================
# Buffer ⊕ Burst — exclusion mutuelle (2 tests)
# ===========================================================================


class TestBufferBurstExclusion:
    def test_buffer_then_burst_compatible_until_first_claim(self, db):
        """Test 18 — apply_buffer puis user dépasse target post-buffer → palier dispo, OK."""
        uid = make_user(db)
        mission_id = _make_mission(db, target_count=3, cab_reward=50)
        um_id = _make_user_mission(
            db,
            user_id=uid,
            mission_id=mission_id,
            target_count=3,
            cab_reward=50,
            xp_reward=10,
        )

        from services.missions_service import apply_buffer

        apply_buffer(db, uid, um_id)
        db.commit()
        # After buffer : target=6. Now user does 12 = 6 × 2 → palier 1 disponible
        db.execute(
            text("UPDATE user_missions SET current_count = 12 WHERE id = :id"),
            {"id": um_id},
        )
        db.commit()

        from services.burst_service import compute_burst_paliers

        row = db.execute(
            text("SELECT current_count, target_count, burst_count FROM user_missions WHERE id = :id"),
            {"id": um_id},
        ).first()
        paliers = compute_burst_paliers(
            current_count=row.current_count,
            target_count=row.target_count,
            current_burst_count=row.burst_count,
        )
        assert paliers >= 1

    def test_burst_first_claim_locks_buffer_permanently(self, db):
        """Test 19 — claim_burst → burst_locked=true, apply_buffer → 409."""
        uid = make_user(db)
        mission_id = _make_mission(db, target_count=12, cab_reward=50)
        um_id = _make_user_mission(
            db,
            user_id=uid,
            mission_id=mission_id,
            current_count=24,
            target_count=12,
            cab_reward=50,
            xp_reward=10,
        )

        from ratis_core.exceptions import Conflict
        from services.burst_service import claim_burst
        from services.missions_service import apply_buffer

        claim_burst(db, uid, um_id)
        db.commit()

        # Verify burst_locked persisted
        row = db.execute(
            text("SELECT burst_locked FROM user_missions WHERE id = :id"),
            {"id": um_id},
        ).first()
        assert row.burst_locked is True

        with pytest.raises(Conflict, match="burst_locked"):
            apply_buffer(db, uid, um_id)


# ===========================================================================
# Leaderboard (2 tests)
# ===========================================================================


class TestLeaderboard:
    def test_leaderboard_monthly_returns_top_50_by_xp(self, db):
        """Test 20 — populate 60 records → returns top 50."""
        from services.leaderboard_service import get_burst_monthly_top

        # Create 60 (user, mission_xp_record) rows in current month
        now = datetime.now(UTC)
        month_str = now.strftime("%Y-%m")
        mission_id = _make_mission(db)

        # Need 60 distinct users and user_missions
        for i in range(60):
            uid = make_user(db)
            um_id = _make_user_mission(
                db,
                user_id=uid,
                mission_id=mission_id,
                current_count=12,
            )
            db.execute(
                text(
                    "INSERT INTO mission_xp_records "
                    "    (id, user_id, mission_id, user_mission_id, "
                    "     xp_earned, burst_count, buffer_count) "
                    "VALUES (:id, :uid, :mid, :umid, :xp, :burst, :buf)"
                ),
                {
                    "id": uuid.uuid4(),
                    "uid": uid,
                    "mid": mission_id,
                    "umid": um_id,
                    "xp": (i + 1) * 100,  # 100, 200, ..., 6000
                    "burst": i % 5,
                    "buf": 0,
                },
            )
        db.commit()

        top = get_burst_monthly_top(db, month_str, limit=50)
        assert len(top) == 50
        # Ordered xp desc — top score = 6000
        assert top[0]["xp_earned"] == 6000
        # 50th = 100 + (60-50) * 100 = 1100 (positions 60→11)
        assert top[49]["xp_earned"] == 1100

    def test_leaderboard_alltime_returns_max_xp_record_per_user(self, db):
        """Test 21 — 1 user a 3 records (XP 100/500/200) → leaderboard montre 500."""
        from services.leaderboard_service import get_burst_alltime_top

        uid = make_user(db)
        mission_id = _make_mission(db)
        # Different period_start per record to bypass the user_missions
        # UNIQUE (user_id, mission_id, period_start) constraint — each
        # leaderboard row needs its own user_mission anchor.
        for idx, xp_val in enumerate((100, 500, 200)):
            ps = date(2026, 5, 1) + timedelta(days=idx)
            um_id = _make_user_mission(
                db,
                user_id=uid,
                mission_id=mission_id,
                current_count=12,
                period_start=ps,
            )
            db.execute(
                text(
                    "INSERT INTO mission_xp_records "
                    "    (id, user_id, mission_id, user_mission_id, "
                    "     xp_earned, burst_count, buffer_count) "
                    "VALUES (:id, :uid, :mid, :umid, :xp, :burst, :buf)"
                ),
                {
                    "id": uuid.uuid4(),
                    "uid": uid,
                    "mid": mission_id,
                    "umid": um_id,
                    "xp": xp_val,
                    "burst": 1,
                    "buf": 0,
                },
            )
        db.commit()

        top = get_burst_alltime_top(db, limit=10)
        # User appears once with the max xp
        user_rows = [r for r in top if r["user_id"] == uid]
        assert len(user_rows) == 1
        assert user_rows[0]["xp_earned"] == 500


# ===========================================================================
# Migration (3 tests) — schema-level checks
# ===========================================================================


class TestMigrationSchema:
    """These run on the application's regular test DB, which is built via
    Base.metadata.create_all() (the conftest setup). The model definitions
    must reflect the post-migration schema for these to pass.
    """

    def test_migration_renames_boost_count_to_buffer_count(self, db):
        """Test 22 — column buffer_count exists and boost_count does NOT."""
        cols = {
            r.column_name
            for r in db.execute(
                text("SELECT column_name FROM information_schema.columns WHERE table_name = 'user_missions'")
            )
        }
        assert "buffer_count" in cols
        assert "boost_count" not in cols

    def test_migration_adds_4_new_columns_with_defaults(self, db):
        """Test 23 — burst_count, period_extended_until, burst_locked, portions_claimed."""
        cols = {
            r.column_name: r.column_default
            for r in db.execute(
                text(
                    "SELECT column_name, column_default "
                    "FROM information_schema.columns "
                    "WHERE table_name = 'user_missions'"
                )
            )
        }
        for c in (
            "burst_count",
            "period_extended_until",
            "burst_locked",
            "portions_claimed",
        ):
            assert c in cols, f"missing column {c}"
        # period_extended_until is nullable (no default required)
        # burst_count default = '0', portions_claimed default = '0',
        # burst_locked default = 'false'
        assert cols["burst_count"].startswith("0")
        assert cols["portions_claimed"].startswith("0")
        assert cols["burst_locked"].lower().startswith("false")

    def test_migration_drops_stonks_records(self, db):
        """Test 24 — table stonks_records absente post-migration."""
        result = db.execute(text("SELECT 1 FROM information_schema.tables WHERE table_name = 'stonks_records'")).first()
        assert result is None
        # Et mission_xp_records existe
        result_new = db.execute(
            text("SELECT 1 FROM information_schema.tables WHERE table_name = 'mission_xp_records'")
        ).first()
        assert result_new is not None


# ---------------------------------------------------------------------------
# Time-freeze helper — utility for date-sensitive Buffer tests
# ---------------------------------------------------------------------------

from contextlib import contextmanager
from unittest.mock import patch


@contextmanager
def _frozen_now(ts: datetime):
    """Freeze ``datetime.now`` inside services.missions_service for a block.

    The service module reads ``datetime.now(timezone.utc)`` directly. Patching
    the bound name in the service module is enough to simulate calendar time
    progression without touching DB clocks.
    """
    import services.missions_service as ms

    class _FakeDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return ts.astimezone(tz) if tz else ts.replace(tzinfo=None)

    with patch.object(ms, "datetime", _FakeDatetime):
        yield
