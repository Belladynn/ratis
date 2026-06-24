"""Concurrent-grant race tests — F-RW-5.

Audit `docs/audits/2026-05-10-deep-audit-rewards.md` (F-RW-5) flagged
that `award_xp` computed `level` from a stale pre-UPDATE balance read
in Python, then wrote it as a constant. Concurrent grants on the same
user therefore left `level` out-of-sync with `balance` :

    A: read balance=100 level=1 → +50 → INSERT level=1 (new=150 → level 1)
    B: read balance=100 level=1 → +50 → UPDATE balance=150+50=200 level=1
       (Python computed level from the stale 100+50=150, missed the real 200)

Persisted state : balance=200, level=1. True level for 200 should be 2.

Fix : `award_xp` now recomputes `level` inline in the SQL UPDATE using
the post-+= balance ; this test proves the persisted state matches the
true level under concurrent grants. Mirrors the two-session pattern
of `test_missions_concurrency.py`.
"""

from __future__ import annotations

import uuid

import pytest
from repositories.xp_repository import _compute_level, award_xp
from sqlalchemy import text
from sqlalchemy.orm import Session

from tests.conftest import TestingSessionLocal, make_user


def _open_session() -> Session:
    return TestingSessionLocal()


def _cleanup_user(user_id: uuid.UUID) -> None:
    cleanup = _open_session()
    try:
        cleanup.execute(
            text("DELETE FROM xp_transactions WHERE user_id = :uid"),
            {"uid": user_id},
        )
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


@pytest.fixture
def fresh_user():
    """Create a user committed to the live DB, cleaned up at teardown."""
    setup = _open_session()
    try:
        user_id = make_user(setup)
    finally:
        setup.close()
    yield user_id
    _cleanup_user(user_id)


# ---------------------------------------------------------------------------
# F-RW-5 — level is computed atomically from post-UPDATE balance
# ---------------------------------------------------------------------------


class TestAwardXpAtomicLevel:
    """The persisted level must always equal `_compute_level(balance)`,
    even under interleaved concurrent grants."""

    def test_concurrent_grants_persist_correct_level(self, fresh_user):
        """Two interleaved 100-XP grants on a fresh user → final balance
        200, persisted level must be 2 (threshold = 300 not crossed, but
        threshold for level 2 is 300, so 200 is level 1).

        Wait — level_base=100 → threshold(2) = 100 * (2^2 - 1) = 300.
        Balance 200 < 300 → level 1. Good, that's the safe simple case.

        Pre-fix : if A reads balance=0 level=0, B reads balance=0
        level=0, both compute new_level=1 from their local 100, and
        the UPDATE writes level=1 — correct by luck. To trigger the
        actual bug we need a level-up boundary.

        See ``test_concurrent_grants_cross_threshold`` below for the
        actual bug-witness — this test serves as a sanity baseline.
        """
        s1 = _open_session()
        s2 = _open_session()
        try:
            award_xp(s1, fresh_user, 100, "receipt_scan", apply_streak_multiplier=False)
            s1.commit()
            award_xp(s2, fresh_user, 100, "label_scan", apply_streak_multiplier=False)
            s2.commit()
        finally:
            s1.close()
            s2.close()

        check = _open_session()
        try:
            row = check.execute(
                text("SELECT balance, level FROM user_xp_balance WHERE user_id = :uid"),
                {"uid": fresh_user},
            ).first()
            assert int(row.balance) == 200
            assert row.level == _compute_level(200, 100)  # = 1
        finally:
            check.close()

    def test_concurrent_grants_cross_threshold(self, fresh_user):
        """Bug-witness for F-RW-5 — true interleaving via threads.

        Setup : user already at balance=100, level=1 (threshold(1)=100).
        Two threads call award_xp(100) concurrently.

        Pre-fix (Python-side level) :
            * Both threads read balance=100 level=1 BEFORE either UPDATE
              fires (the SELECT in `get_xp_balance` is non-locking).
            * Both compute new_balance=200, new_level=1 (threshold(2)=300).
            * First UPDATE writes balance=200, level=1.
            * Second UPDATE — blocked on row lock from the upsert — then
              proceeds with its stale-computed level=1, writing
              balance=300 (atomic +=) BUT level=1 instead of 2.
            Final : balance=300, level=1 → leak.

        Post-fix (SQL-side level) :
            * Both threads still read pre-update state for `old_level`.
            * The UPSERT recomputes `level` from `user_xp_balance.balance
              + :amount` inside the SQL itself, so the second UPDATE sees
              balance=200+100=300 and writes level=2.
            Final : balance=300, level=2.

        We use real threads so both reads happen before either UPDATE.
        A `threading.Barrier(2)` synchronises the read step ; without
        it the test reduces to the sequential case (which already
        works pre-fix).
        """
        import threading

        # Seed user_xp_balance with balance=100 level=1.
        seed = _open_session()
        try:
            seed.execute(
                text("INSERT INTO user_xp_balance (user_id, balance, level, updated_at) VALUES (:uid, 100, 1, now())"),
                {"uid": fresh_user},
            )
            seed.commit()
        finally:
            seed.close()

        barrier = threading.Barrier(2)
        results: list[Exception | None] = [None, None]

        def _grant(idx: int, reason: str) -> None:
            s = _open_session()
            try:
                # 1. Read current state — both threads do this before
                #    either UPDATE fires. We inline a SELECT so the
                #    barrier wait happens AFTER the non-locking read.
                s.execute(
                    text("SELECT balance FROM user_xp_balance WHERE user_id = :uid"),
                    {"uid": fresh_user},
                ).first()
                # 2. Synchronise — both threads here together.
                barrier.wait(timeout=5)
                # 3. Fire the award — pre-fix : level computed from
                #    stale read above. Post-fix : level computed in SQL.
                award_xp(s, fresh_user, 100, reason, apply_streak_multiplier=False)
                s.commit()
            except Exception as exc:
                results[idx] = exc
            finally:
                s.close()

        t1 = threading.Thread(target=_grant, args=(0, "receipt_scan"))
        t2 = threading.Thread(target=_grant, args=(1, "label_scan"))
        t1.start()
        t2.start()
        t1.join(timeout=10)
        t2.join(timeout=10)

        for idx, exc in enumerate(results):
            if exc is not None:
                raise AssertionError(f"thread {idx} raised : {exc!r}") from exc

        check = _open_session()
        try:
            row = check.execute(
                text("SELECT balance, level FROM user_xp_balance WHERE user_id = :uid"),
                {"uid": fresh_user},
            ).first()
            assert int(row.balance) == 300, "balance += is atomic, should always sum"
            # Truth : threshold(2) = 100 * (2^2 - 1) = 300 → level should be 2.
            expected_level = _compute_level(300, 100)
            assert expected_level == 2  # sanity
            assert row.level == 2, (
                f"F-RW-5 regression : level not recomputed from post-UPDATE "
                f"balance. Got level={row.level} balance={row.balance} "
                f"(expected level={expected_level})."
            )
        finally:
            check.close()

    def test_level_matches_compute_level_at_every_boundary(self, fresh_user):
        """Spot-check the SQL formula matches the Python `_compute_level`
        at each threshold boundary up to level 8.

        Run inline in a fresh user, granting just enough XP each step
        to land exactly at the next threshold. The persisted level
        must match `_compute_level(threshold, 100)`.
        """
        s = _open_session()
        try:
            cumulative = 0
            for level_target in range(1, 9):
                threshold = 100 * ((1 << level_target) - 1)
                delta = threshold - cumulative
                if delta <= 0:
                    continue
                award_xp(s, fresh_user, delta, "receipt_scan", apply_streak_multiplier=False)
                cumulative = threshold
                s.commit()

                row = s.execute(
                    text("SELECT balance, level FROM user_xp_balance WHERE user_id = :uid"),
                    {"uid": fresh_user},
                ).first()
                expected = _compute_level(int(row.balance), 100)
                assert row.level == expected, (
                    f"At threshold(level={level_target})={threshold} : SQL level={row.level} != python level={expected}"
                )
        finally:
            s.close()

    def test_large_balance_uses_numeric_precision(self, fresh_user):
        """Sanity : SQL `log(2.0, numeric)` must not float-overflow at
        very large balances (PG numeric is arbitrary precision).

        Grant 2^60 XP at once and verify level matches `_compute_level`.
        2^60 ≈ 1.15e18 — well past int8 range for `balance` if it were
        plain bigint, but the schema uses ``numeric`` for balance so
        this is supported. The level should equal
        ``floor(log2(2^60/100 + 1))`` = 53.
        """
        # Verify schema supports it before stressing.
        check = _open_session()
        try:
            col_type = check.execute(
                text(
                    "SELECT data_type FROM information_schema.columns "
                    "WHERE table_name='user_xp_balance' AND column_name='balance'"
                )
            ).scalar()
        finally:
            check.close()
        if col_type not in ("numeric", "bigint"):
            pytest.skip(f"balance column is {col_type!r} — can't test huge values")

        big = 1 << 60  # 2^60
        s = _open_session()
        try:
            award_xp(s, fresh_user, big, "receipt_scan", apply_streak_multiplier=False)
            s.commit()
            row = s.execute(
                text("SELECT balance, level FROM user_xp_balance WHERE user_id = :uid"),
                {"uid": fresh_user},
            ).first()
            assert int(row.balance) == big
            expected = _compute_level(big, 100)
            assert row.level == expected
        finally:
            s.close()


# ---------------------------------------------------------------------------
# Regression — `level` SQL expression must remain in the repository.
# ---------------------------------------------------------------------------


def test_level_recomputation_present_in_award_xp():
    """Belt + suspenders : assert award_xp's SQL recomputes level.

    Traps a future refactor that would silently revert to the
    Python-side stale-read pattern.
    """
    import inspect

    from repositories import xp_repository

    src = inspect.getsource(xp_repository.award_xp)
    assert "log(" in src, "F-RW-5 regression : level no longer recomputed in SQL"
    assert "user_xp_balance.balance + :amount" in src, "F-RW-5 regression : level not computed from post-UPDATE balance"
