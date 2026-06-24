"""Concurrent-claim race tests — F-RW-2 / F-RW-4 / F-RW-10.

Audit `docs/audits/2026-05-10-deep-audit-rewards.md` flagged three race
conditions in the missions claim path :

* F-RW-2  — Burst claim : `get_user_mission_for_burst` lacked FOR UPDATE
            → double XP credit on concurrent claim.
* F-RW-4  — Multi-claim : `get_user_mission_for_claim` lacked FOR UPDATE
            → double CAB credit on concurrent partial claims.
* F-RW-10 — Buffer apply : `get_user_mission_for_buffer` lacked FOR UPDATE
            → buffer_count could exceed n_max=3.

Each test exercises the real two-connection race :

1. Two independent DB sessions are opened on the live engine (the
   savepoint-based `db` fixture cannot model row locks — both savepoints
   share one connection, so a SELECT FOR UPDATE on session A does not
   block session B's identical SELECT).
2. Session A reads the row with FOR UPDATE and holds the lock by NOT
   committing yet.
3. Session B issues the same FOR UPDATE with a 500 ms `lock_timeout`. We
   assert it raises `LockNotAvailable` — proof the lock is honoured.
4. Session A commits ; session B retried fresh succeeds, observes the
   new state, and behaves correctly (= rejects already-claimed,
   already-bursted, or buffer-cap-reached).

We avoid threads to keep the test deterministic on CI : both sessions
run synchronously on the same Python thread, the lock probe relies on
`SET LOCAL lock_timeout = '500ms'` so blocking is observable without
sleep loops.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, OperationalError
from sqlalchemy.orm import Session

from tests.conftest import TestingSessionLocal, make_user

# ---------------------------------------------------------------------------
# Two-connection harness
# ---------------------------------------------------------------------------


def _open_session() -> Session:
    """Return a fresh DB session with its own connection.

    The autouse `db` fixture uses a savepoint pattern that shares a
    single connection — useless for row-lock tests. We open a real
    independent session here ; the caller MUST clean up via
    `_cleanup_user_mission` to keep the DB pristine between tests.
    """
    return TestingSessionLocal()


def _cleanup_user_mission(um_id: uuid.UUID) -> None:
    """Hard-delete user_mission + related rows after a concurrency test."""
    cleanup = TestingSessionLocal()
    try:
        cleanup.execute(
            text("DELETE FROM xp_transactions WHERE reference_id = :id"),
            {"id": um_id},
        )
        cleanup.execute(
            text("DELETE FROM cabecoin_transactions WHERE reference_id = :id"),
            {"id": um_id},
        )
        cleanup.execute(
            text("DELETE FROM mission_xp_records WHERE user_mission_id = :id"),
            {"id": um_id},
        )
        cleanup.execute(
            text("DELETE FROM user_missions WHERE id = :id"),
            {"id": um_id},
        )
        cleanup.commit()
    finally:
        cleanup.close()


def _cleanup_user(user_id: uuid.UUID) -> None:
    """Hard-delete a test user + its balance rows."""
    cleanup = TestingSessionLocal()
    try:
        cleanup.execute(
            text("DELETE FROM user_xp_balance WHERE user_id = :uid"),
            {"uid": user_id},
        )
        cleanup.execute(
            text("DELETE FROM user_cab_balance WHERE user_id = :uid"),
            {"uid": user_id},
        )
        cleanup.execute(
            text("DELETE FROM user_cashback_balance WHERE user_id = :uid"),
            {"uid": user_id},
        )
        cleanup.execute(
            text("DELETE FROM users WHERE id = :uid"),
            {"uid": user_id},
        )
        cleanup.commit()
    finally:
        cleanup.close()


def _cleanup_mission(mission_id: uuid.UUID) -> None:
    cleanup = TestingSessionLocal()
    try:
        cleanup.execute(
            text("DELETE FROM missions WHERE id = :id"),
            {"id": mission_id},
        )
        cleanup.commit()
    finally:
        cleanup.close()


def _insert_mission(
    session: Session,
    *,
    frequency: str = "daily",
    target_count: int = 3,
    cab_reward: int = 50,
) -> uuid.UUID:
    mission_id = uuid.uuid4()
    session.execute(
        text(
            "INSERT INTO missions "
            "    (id, action_type, frequency, difficulty, "
            "     target_count, cab_reward, is_active) "
            "VALUES (:id, 'label_scan', :freq, 'easy', "
            "        :target, :cab, true)"
        ),
        {
            "id": mission_id,
            "freq": frequency,
            "target": target_count,
            "cab": cab_reward,
        },
    )
    session.commit()
    return mission_id


def _insert_user_mission(
    session: Session,
    *,
    user_id: uuid.UUID,
    mission_id: uuid.UUID,
    current_count: int = 0,
    target_count: int = 3,
    cab_reward: int = 50,
    xp_reward: int = 10,
    buffer_count: int = 0,
    burst_count: int = 0,
    burst_locked: bool = False,
    portions_claimed: int = 0,
    period_start: date | None = None,
    status: str = "pending",
) -> uuid.UUID:
    um_id = uuid.uuid4()
    if period_start is None:
        period_start = datetime.now(UTC).date()
    session.execute(
        text(
            "INSERT INTO user_missions "
            "    (id, user_id, mission_id, period_start, current_count, status, "
            "     target_count, cab_reward, xp_reward, buffer_count, burst_count, "
            "     burst_locked, portions_claimed) "
            "VALUES (:id, :uid, :mid, :period, :count, :status, "
            "        :target, :cab, :xp, :buffer, :burst, "
            "        :locked, :claimed)"
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
        },
    )
    session.commit()
    return um_id


@pytest.fixture
def two_sessions():
    """Open two independent DB sessions on the live engine.

    Each has its own connection — required to observe real PG row locks.
    """
    s1 = _open_session()
    s2 = _open_session()
    try:
        yield s1, s2
    finally:
        s1.close()
        s2.close()


def _setup_concurrency_world(
    *,
    mission_kwargs: dict | None = None,
    um_kwargs: dict | None = None,
) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    """Insert a user + mission + user_mission visible to both sessions.

    Returns ``(user_id, mission_id, user_mission_id)``. The caller must
    pass these to ``_cleanup_world`` at the end of the test.

    Inserted with its own session+commit so both ``two_sessions``
    sessions see the rows.
    """
    setup = _open_session()
    try:
        user_id = make_user(setup)
        mission_id = _insert_mission(setup, **(mission_kwargs or {}))
        um_id = _insert_user_mission(
            setup,
            user_id=user_id,
            mission_id=mission_id,
            **(um_kwargs or {}),
        )
    finally:
        setup.close()
    return user_id, mission_id, um_id


def _cleanup_world(user_id, mission_id, um_id) -> None:
    _cleanup_user_mission(um_id)
    _cleanup_mission(mission_id)
    _cleanup_user(user_id)


# ---------------------------------------------------------------------------
# F-RW-2 — get_user_mission_for_burst FOR UPDATE
# ---------------------------------------------------------------------------


class TestBurstClaimRace:
    """Two concurrent `claim_burst` on the same user_mission must serialize."""

    def test_for_update_blocks_second_select(self, two_sessions):
        """Session A reads with FOR UPDATE → session B blocks until A commits."""
        from repositories.missions_repository import get_user_mission_for_burst

        s1, s2 = two_sessions
        user_id, mission_id, um_id = _setup_concurrency_world(
            mission_kwargs={"target_count": 3, "cab_reward": 50},
            um_kwargs={
                "current_count": 6,  # = target × 2 → palier 1 unlocked
                "target_count": 3,
                "xp_reward": 10,
            },
        )
        try:
            # Session A acquires the row lock.
            row_a = get_user_mission_for_burst(s1, um_id, user_id)
            assert row_a is not None
            assert row_a.burst_count == 0

            # Session B sets a 200 ms lock timeout and tries to lock too —
            # must raise LockNotAvailable / OperationalError because A
            # still holds the lock.
            s2.execute(text("SET LOCAL lock_timeout = '200ms'"))
            with pytest.raises((OperationalError, DBAPIError)):
                get_user_mission_for_burst(s2, um_id, user_id)
            # The failed statement aborts s2's transaction — rollback it.
            s2.rollback()

            # A commits → release lock. B can now lock.
            s1.commit()
            row_b = get_user_mission_for_burst(s2, um_id, user_id)
            assert row_b is not None
            s2.commit()
        finally:
            _cleanup_world(user_id, mission_id, um_id)

    def test_serialised_claims_no_double_xp(self, two_sessions):
        """End-to-end : two `claim_burst` serialise → second sees updated state.

        Session A holds the lock + computes + UPDATEs burst_count=1, then
        commits. Session B then retries fresh : it sees burst_count=1
        and raises `no_burst_palier_unlocked` (current_count=6 only
        unlocks palier 1) — proves the second writer cannot
        double-credit XP.
        """
        from ratis_core.exceptions import PaymentRequired
        from services.burst_service import claim_burst

        s1, s2 = two_sessions
        user_id, mission_id, um_id = _setup_concurrency_world(
            mission_kwargs={"target_count": 3, "cab_reward": 50},
            um_kwargs={
                "current_count": 6,
                "target_count": 3,
                "xp_reward": 10,
                "burst_count": 0,
            },
        )
        try:
            # A claims the first palier → expects xp = 10 * (2^1 - 2^0) = 10.
            result_a = claim_burst(s1, user_id, um_id)
            s1.commit()
            assert result_a["xp_awarded"] == 10
            assert result_a["burst_count_total"] == 1

            # B retries with a fresh session : burst_count is now 1, no new
            # palier reachable from current_count=6 → must raise.
            with pytest.raises(PaymentRequired, match="no_burst_palier_unlocked"):
                claim_burst(s2, user_id, um_id)
            s2.rollback()

            # Sanity : total XP awarded = 10, not 20.
            check = _open_session()
            try:
                total_xp = check.execute(
                    text("SELECT balance FROM user_xp_balance WHERE user_id = :uid"),
                    {"uid": user_id},
                ).scalar()
                assert int(total_xp) == 10
            finally:
                check.close()
        finally:
            _cleanup_world(user_id, mission_id, um_id)


# ---------------------------------------------------------------------------
# F-RW-4 — get_user_mission_for_claim FOR UPDATE
# ---------------------------------------------------------------------------


class TestMissionClaimRace:
    """Two concurrent `claim_mission` must serialize on the user_mission row."""

    def test_for_update_blocks_second_select(self, two_sessions):
        from repositories.missions_repository import get_user_mission_for_claim

        s1, s2 = two_sessions
        user_id, mission_id, um_id = _setup_concurrency_world(
            mission_kwargs={"target_count": 3, "cab_reward": 50},
            um_kwargs={
                "current_count": 3,
                "target_count": 3,
                "cab_reward": 50,
            },
        )
        try:
            row_a = get_user_mission_for_claim(s1, um_id, user_id)
            assert row_a is not None

            s2.execute(text("SET LOCAL lock_timeout = '200ms'"))
            with pytest.raises((OperationalError, DBAPIError)):
                get_user_mission_for_claim(s2, um_id, user_id)
            s2.rollback()

            s1.commit()
            row_b = get_user_mission_for_claim(s2, um_id, user_id)
            assert row_b is not None
            s2.commit()
        finally:
            _cleanup_world(user_id, mission_id, um_id)

    def test_serialised_claims_no_double_cab(self, two_sessions):
        """A claims all 1 portion → B sees portions_claimed=1 → rejects.

        With n=0 (no buffer), there is exactly 1 portion. A serialised
        race must result in A getting 50 CAB and B getting `already_claimed`.
        Pre-fix : B would also see portions_claimed=0 and double-credit.
        """
        from ratis_core.exceptions import Conflict
        from repositories.cab_repository import get_balance
        from services.missions_service import claim_mission

        s1, s2 = two_sessions
        user_id, mission_id, um_id = _setup_concurrency_world(
            mission_kwargs={"target_count": 3, "cab_reward": 50},
            um_kwargs={
                "current_count": 3,
                "target_count": 3,
                "cab_reward": 50,
                "xp_reward": 10,
            },
        )
        try:
            result_a = claim_mission(s1, user_id, um_id)
            s1.commit()
            assert result_a["cab_awarded"] == 50

            with pytest.raises(Conflict, match="already_claimed"):
                claim_mission(s2, user_id, um_id)
            s2.rollback()

            check = _open_session()
            try:
                assert get_balance(check, user_id) == 50
            finally:
                check.close()
        finally:
            _cleanup_world(user_id, mission_id, um_id)


# ---------------------------------------------------------------------------
# F-RW-10 — get_user_mission_for_buffer FOR UPDATE
# ---------------------------------------------------------------------------


class TestApplyBufferRace:
    """Two concurrent `apply_buffer` must serialize ; buffer_count never
    exceeds n_max=3."""

    def test_for_update_blocks_second_select(self, two_sessions):
        from repositories.missions_repository import get_user_mission_for_buffer

        s1, s2 = two_sessions
        user_id, mission_id, um_id = _setup_concurrency_world(
            um_kwargs={"buffer_count": 0},
        )
        try:
            row_a = get_user_mission_for_buffer(s1, um_id, user_id)
            assert row_a is not None

            s2.execute(text("SET LOCAL lock_timeout = '200ms'"))
            with pytest.raises((OperationalError, DBAPIError)):
                get_user_mission_for_buffer(s2, um_id, user_id)
            s2.rollback()

            s1.commit()
            row_b = get_user_mission_for_buffer(s2, um_id, user_id)
            assert row_b is not None
            s2.commit()
        finally:
            _cleanup_world(user_id, mission_id, um_id)

    def test_serialised_buffer_respects_cap(self, two_sessions):
        """User at buffer_count=2 (1 below cap=3) → two concurrent buffer
        attempts : one succeeds → 3, second must raise `buffer_cap_reached`.

        Pre-fix : both would read buffer_count=2, both pass the cap check,
        both increment → end state buffer_count=3 (lucky : the UPDATE
        is SET not INCR) but with two CAB-reward + period-extension
        re-applications.

        With FOR UPDATE : B sees buffer_count=3 after A commits and
        correctly rejects.
        """
        from ratis_core.exceptions import Conflict
        from services.missions_service import apply_buffer

        s1, s2 = two_sessions
        user_id, mission_id, um_id = _setup_concurrency_world(
            mission_kwargs={"target_count": 3, "cab_reward": 50},
            um_kwargs={"buffer_count": 2, "target_count": 12, "cab_reward": 150},
        )
        try:
            apply_buffer(s1, user_id, um_id)
            s1.commit()

            with pytest.raises(Conflict, match="buffer_cap_reached"):
                apply_buffer(s2, user_id, um_id)
            s2.rollback()

            check = _open_session()
            try:
                row = check.execute(
                    text("SELECT buffer_count FROM user_missions WHERE id = :id"),
                    {"id": um_id},
                ).first()
                assert row.buffer_count == 3  # cap respected
            finally:
                check.close()
        finally:
            _cleanup_world(user_id, mission_id, um_id)


# ---------------------------------------------------------------------------
# Follow-up to PR #390 — get_user_mission_for_freeze FOR UPDATE
# ---------------------------------------------------------------------------


class TestFreezeMissionRace:
    """Two concurrent `freeze_mission` on the same user_mission must serialize.

    Same race shape as F-RW-2 / F-RW-4 / F-RW-10 :

    * ``get_user_mission_for_freeze`` reads ``frozen_until`` + ``freeze_count``
    * ``freeze_mission`` validates state, debits CAB, then ``apply_freeze`` UPDATEs.
    * Two parallel requests both read ``frozen_until=NULL`` /
      ``freeze_count=0`` → both pass the checks → both debit CAB → user
      double-charged for a single freeze.
    """

    def test_for_update_blocks_second_select(self, two_sessions):
        """Session A reads with FOR UPDATE → session B blocks until A commits."""
        from repositories.missions_repository import get_user_mission_for_freeze

        s1, s2 = two_sessions
        user_id, mission_id, um_id = _setup_concurrency_world(
            um_kwargs={"current_count": 0},
        )
        try:
            row_a = get_user_mission_for_freeze(s1, um_id, user_id)
            assert row_a is not None
            assert row_a.frozen_until is None
            assert row_a.freeze_count == 0

            s2.execute(text("SET LOCAL lock_timeout = '200ms'"))
            with pytest.raises((OperationalError, DBAPIError)):
                get_user_mission_for_freeze(s2, um_id, user_id)
            s2.rollback()

            s1.commit()
            row_b = get_user_mission_for_freeze(s2, um_id, user_id)
            assert row_b is not None
            s2.commit()
        finally:
            _cleanup_world(user_id, mission_id, um_id)

    def test_serialised_freeze_no_double_debit(self, two_sessions):
        """A freezes → B sees ``freeze_count=1`` → rejects with ``freeze_limit_reached``.

        Pre-fix : both sessions read ``frozen_until=NULL`` /
        ``freeze_count=0`` → both pass validation → both debit 100 CAB
        → user ends up with balance 0 (started 200), freeze_count=2,
        but only one effective freeze (apply_freeze SETs frozen_until to
        a deterministic value, not increment).

        Post-fix : B blocks on the row lock until A commits, then sees
        ``frozen_until`` set → raises ``mission_already_frozen`` (the
        first guard in ``freeze_mission`` — fires before the freeze_count
        check) → no debit. Final balance = 100 (200 - 1 × 100),
        freeze_count = 1.
        """
        from ratis_core.exceptions import Conflict
        from repositories.cab_repository import get_balance
        from services.missions_service import freeze_mission

        s1, s2 = two_sessions
        user_id, mission_id, um_id = _setup_concurrency_world(
            um_kwargs={"current_count": 0},
        )
        # Credit enough CAB to afford TWO freezes — so the second debit
        # would succeed pre-fix (proving the bug needs the lock to be
        # the gate, not balance shortage).
        setup = _open_session()
        try:
            setup.execute(
                text("UPDATE user_cab_balance SET balance = 200 WHERE user_id = :uid"),
                {"uid": user_id},
            )
            setup.commit()
        finally:
            setup.close()

        try:
            result_a = freeze_mission(s1, user_id, um_id, freeze_cost=100)
            s1.commit()
            assert result_a["cost_paid_cab"] == 100

            with pytest.raises(Conflict, match="mission_already_frozen"):
                freeze_mission(s2, user_id, um_id, freeze_cost=100)
            s2.rollback()

            check = _open_session()
            try:
                # Exactly one debit happened (200 - 100 = 100).
                assert get_balance(check, user_id) == 100
                # freeze_count = 1, not 2.
                row = check.execute(
                    text("SELECT freeze_count FROM user_missions WHERE id = :id"),
                    {"id": um_id},
                ).first()
                assert row.freeze_count == 1
            finally:
                check.close()
        finally:
            _cleanup_world(user_id, mission_id, um_id)


# ---------------------------------------------------------------------------
# SQL-surface regression — the lock clauses must remain present.
# ---------------------------------------------------------------------------


def test_for_update_clauses_present_in_repository():
    """Belt + suspenders : assert the SQL text still carries FOR UPDATE.

    A future refactor could drop the clause silently — this test traps
    that regression at unit-test speed (no DB needed beyond import).
    """
    import inspect

    from repositories import missions_repository

    src = inspect.getsource(missions_repository.get_user_mission_for_burst)
    assert "FOR UPDATE" in src, "F-RW-2 regression : burst SELECT missing FOR UPDATE"

    src = inspect.getsource(missions_repository.get_user_mission_for_claim)
    assert "FOR UPDATE" in src, "F-RW-4 regression : claim SELECT missing FOR UPDATE"

    src = inspect.getsource(missions_repository.get_user_mission_for_buffer)
    assert "FOR UPDATE" in src, "F-RW-10 regression : buffer SELECT missing FOR UPDATE"

    src = inspect.getsource(missions_repository.get_user_mission_for_freeze)
    assert "FOR UPDATE" in src, "Freeze regression : freeze SELECT missing FOR UPDATE (follow-up to PR #390)"
