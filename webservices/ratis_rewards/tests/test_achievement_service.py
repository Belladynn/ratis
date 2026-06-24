"""Tests for ``services/achievement_service.py``.

Cf docs/superpowers/specs/2026-05-09-achievements-v1-design.md § Service & dispatcher.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import text


# ---------------------------------------------------------------------------
# Constants / contract — Task 2.1
# ---------------------------------------------------------------------------
def test_event_type_to_triggers_complete():
    """All 7 event types from the spec must be mapped to at least one trigger."""
    from services.achievement_service import EVENT_TYPE_TO_TRIGGERS

    expected = {
        "scan_accepted",
        "cashback_credited",
        "streak_extended",
        "referral_paid",
        "battlepass_season_participated",
        "konami_code_entered",
        "app_opened_at_3am",
    }
    assert expected <= set(EVENT_TYPE_TO_TRIGGERS.keys())


def test_windowed_trigger_types():
    from services.achievement_service import WINDOWED_TRIGGER_TYPES

    assert "savings_eur_in_window" in WINDOWED_TRIGGER_TYPES


def test_trigger_progress_computers_complete():
    """V1.1 — every TRIGGER_HANDLERS key must have a matching PROGRESS_COMPUTER.

    Without this parity, the serializer would silently return ``progress: null``
    for any newly-added trigger that forgot to register a computer.
    """
    from services.achievement_service import (
        _BATCH_ONLY_HANDLERS,
        TRIGGER_HANDLERS,
        TRIGGER_PROGRESS_COMPUTERS,
    )

    # Every trigger that has a handler must also have a progress computer.
    handler_triggers = set(TRIGGER_HANDLERS) | set(_BATCH_ONLY_HANDLERS)
    missing = handler_triggers - set(TRIGGER_PROGRESS_COMPUTERS)
    assert not missing, f"missing TRIGGER_PROGRESS_COMPUTERS for {missing!r}"


def test_trigger_handlers_complete_for_event_path():
    """All non-windowed triggers used in event path must have a handler."""
    from services.achievement_service import (
        EVENT_TYPE_TO_TRIGGERS,
        TRIGGER_HANDLERS,
        WINDOWED_TRIGGER_TYPES,
    )

    event_triggers = set().union(*EVENT_TYPE_TO_TRIGGERS.values()) - WINDOWED_TRIGGER_TYPES
    for trigger in event_triggers:
        assert trigger in TRIGGER_HANDLERS, f"missing handler for trigger_type={trigger!r}"


# ---------------------------------------------------------------------------
# _unlock — Task 2.4
# ---------------------------------------------------------------------------
class TestUnlock:
    def test_inserts_user_achievement(self, db, test_user, achievement_factory):
        from ratis_core.models.achievement import UserAchievement
        from services.achievement_service import _unlock

        ach = achievement_factory(code="unlock_test", cab_reward=42)
        ok = _unlock(db, test_user.id, ach, trigger_event={"source": "test"})
        assert ok is True
        ua = db.query(UserAchievement).filter_by(user_id=test_user.id, achievement_id=ach.id).one()
        assert ua.cab_granted == 42
        assert ua.trigger_event == {"source": "test"}

    def test_idempotent_double_call(self, db, test_user, achievement_factory):
        from ratis_core.models.achievement import UserAchievement
        from services.achievement_service import _unlock

        ach = achievement_factory(code="unlock_idem", cab_reward=30)
        assert _unlock(db, test_user.id, ach, None) is True
        # Second call : ON CONFLICT DO NOTHING → returns False, no double grant.
        assert _unlock(db, test_user.id, ach, None) is False
        count = db.query(UserAchievement).filter_by(user_id=test_user.id, achievement_id=ach.id).count()
        assert count == 1

    def test_grants_cab_atomically(self, db, test_user, achievement_factory):
        """Verify the CAB transaction row materialises with the catalog amount.

        ``_unlock`` calls ``award_cab(..., apply_streak_multiplier=False)`` on
        purpose — the catalog ``cab_reward`` is the source of truth for the
        rarity-based grille and must NOT be amplified by a streak multiplier
        (cf ``services/achievement_service.py`` § ``_unlock`` rationale).
        """
        from services.achievement_service import _unlock
        from sqlalchemy import text as _text

        ach = achievement_factory(code="unlock_cab", cab_reward=100)
        _unlock(db, test_user.id, ach, None)
        row = db.execute(
            _text(
                "SELECT amount, direction, reason, reference_type, reference_id "
                "FROM cabecoin_transactions WHERE user_id = :uid"
            ),
            {"uid": test_user.id},
        ).one()
        assert row.amount == 100
        assert row.direction == "credit"
        assert row.reason == "achievement_unlock"
        assert row.reference_type == "achievement"
        assert row.reference_id == ach.id

    def test_persists_real_cabecoin_transaction(self, db, test_user, achievement_factory):
        """Regression — ``_unlock`` must persist a CAB tx with
        reason=``'achievement_unlock'`` AND reference_type=``'achievement'``
        WITHOUT raising ``IntegrityError``.

        Guards both the ``cabecoin_transactions_reason_check`` CHECK
        (extended by ``20260510_1100_ach_unlock_rsn``) and the
        ``cabecoin_transactions_reference_type_check`` CHECK (extended by
        ``20260510_1020_ach_cab_ref``). Without those migrations, the first
        call to ``_unlock`` in prod fails — yet PR1 alone shipped Python
        literals only and would have left the DB rejecting the INSERT.
        """
        from ratis_core.models.gamification import CabecoinsTransaction
        from services.achievement_service import _unlock

        ach = achievement_factory(code="unlock_check_constraint", cab_reward=42)
        assert _unlock(db, test_user.id, ach, None) is True
        # If we reach here without IntegrityError, both CHECK constraints
        # accept the (reason, reference_type) pair.
        tx = (
            db.query(CabecoinsTransaction)
            .filter_by(
                user_id=test_user.id,
                reference_id=ach.id,
                reason="achievement_unlock",
                reference_type="achievement",
            )
            .one()
        )
        assert tx.amount == 42
        assert tx.direction == "credit"


# ---------------------------------------------------------------------------
# Commit contract — audit F-RW-1 (PR #387)
# ---------------------------------------------------------------------------
class TestUnlockCommitContract:
    """``_unlock`` MUST NOT commit on its own.

    Pre-F-RW-1 the helper called ``db.commit()`` mid-flow, which silently
    committed every pending write of the caller (CAB awards, mission
    progress, XP, cashback inserts, …). Post-F-RW-1 the helper leaves the
    INSERT + CAB grant pending ; the caller's ``with db_transaction(db):``
    wrapper (or an explicit commit) is the sole commit point.

    These tests are the regression guard.
    """

    def test_unlock_does_not_commit_internally(self, db, test_user, achievement_factory):
        """If we rollback right after ``_unlock``, the row must NOT persist.

        Pre-fix this test would fail : ``_unlock``'s internal ``db.commit()``
        already persisted the row before the test's rollback could undo it.
        Post-fix the savepoint rollback wipes the still-pending INSERT.
        """
        from ratis_core.models.achievement import UserAchievement
        from services.achievement_service import _unlock

        ach = achievement_factory(code="contract_no_commit", cab_reward=42)

        # Open a savepoint, run _unlock, rollback to before. The conftest
        # ``db`` fixture wraps every test in an outer SAVEPOINT already, so
        # a nested savepoint gives us a clean rollback target.
        sp = db.begin_nested()
        ok = _unlock(db, test_user.id, ach, trigger_event={"source": "test"})
        assert ok is True
        sp.rollback()

        # The savepoint rollback dropped the INSERT. If _unlock had
        # committed mid-flow (pre-F-RW-1), the row would survive.
        row = db.query(UserAchievement).filter_by(user_id=test_user.id, achievement_id=ach.id).first()
        assert row is None, (
            "Regression : _unlock committed internally — savepoint rollback "
            "should have unwound the INSERT. Audit F-RW-1."
        )

    def test_caller_commit_persists_unlock_and_prior_writes_atomically(self, db, test_user, achievement_factory):
        """A caller wrapping ``_unlock`` with its own commit persists BOTH
        its prior writes AND the unlock atomically — the transactional
        contract that F-RW-1 was about restoring.

        Models a typical callsite : `caller` flushes some upstream write,
        runs ``_unlock``, then commits once. Both rows must be there.
        """
        from ratis_core.models.achievement import UserAchievement
        from ratis_core.models.gamification import CabecoinsTransaction
        from services.achievement_service import _unlock

        ach = achievement_factory(code="contract_atomic", cab_reward=15)

        # Simulate a caller's prior write : a CabecoinsTransaction debit
        # that should land in the same commit as the unlock's credit.
        prior_ref_id = uuid.uuid4()
        db.execute(
            text(
                "INSERT INTO cabecoin_transactions "
                "(id, user_id, amount, direction, reason, reference_type, reference_id) "
                "VALUES (:id, :uid, 10, 'debit', 'cashback_boost_debit', 'scan', :ref)"
            ),
            {
                "id": uuid.uuid4(),
                "uid": test_user.id,
                "ref": prior_ref_id,
            },
        )

        # Run unlock — leaves the INSERT + credit pending. Then commit
        # once on the caller side.
        assert _unlock(db, test_user.id, ach, None) is True
        db.commit()

        # Both must persist : the prior debit AND the unlock's credit AND
        # the user_achievements row.
        ua = db.query(UserAchievement).filter_by(user_id=test_user.id, achievement_id=ach.id).first()
        assert ua is not None
        assert ua.cab_granted == 15

        debit = (
            db.query(CabecoinsTransaction)
            .filter_by(user_id=test_user.id, direction="debit", reference_id=prior_ref_id)
            .first()
        )
        credit = (
            db.query(CabecoinsTransaction)
            .filter_by(user_id=test_user.id, direction="credit", reference_id=ach.id)
            .first()
        )
        assert debit is not None
        assert credit is not None
        assert credit.amount == 15

    def test_caller_rollback_unwinds_unlock_and_prior_writes(self, db, test_user, achievement_factory):
        """If a caller rolls back AFTER ``_unlock``, both the upstream
        write AND the unlock must disappear — proving the unlock is now
        truly part of the caller's transaction.

        Pre-fix this test would fail : ``_unlock``'s mid-flow commit
        would have persisted both the user_achievements row AND the
        caller's prior debit (since the commit was on the same session,
        all pending writes flushed at once).
        """
        from ratis_core.models.achievement import UserAchievement
        from ratis_core.models.gamification import CabecoinsTransaction
        from services.achievement_service import _unlock

        ach = achievement_factory(code="contract_rollback", cab_reward=22)

        # Wrap caller's logic in a savepoint we will rollback.
        sp = db.begin_nested()
        prior_ref_id = uuid.uuid4()
        db.execute(
            text(
                "INSERT INTO cabecoin_transactions "
                "(id, user_id, amount, direction, reason, reference_type, reference_id) "
                "VALUES (:id, :uid, 5, 'debit', 'cashback_boost_debit', 'scan', :ref)"
            ),
            {
                "id": uuid.uuid4(),
                "uid": test_user.id,
                "ref": prior_ref_id,
            },
        )
        assert _unlock(db, test_user.id, ach, None) is True
        sp.rollback()  # caller decides to roll back the whole flow

        # Neither write must survive.
        ua = db.query(UserAchievement).filter_by(user_id=test_user.id, achievement_id=ach.id).first()
        assert ua is None, (
            "Regression : _unlock committed mid-flow ; the caller's rollback "
            "could not unwind the user_achievements INSERT. Audit F-RW-1."
        )
        debit = (
            db.query(CabecoinsTransaction)
            .filter_by(user_id=test_user.id, direction="debit", reference_id=prior_ref_id)
            .first()
        )
        credit = (
            db.query(CabecoinsTransaction)
            .filter_by(user_id=test_user.id, direction="credit", reference_id=ach.id)
            .first()
        )
        assert debit is None
        assert credit is None


# ---------------------------------------------------------------------------
# check_achievements dispatcher — Task 2.5
# ---------------------------------------------------------------------------
class TestCheckAchievements:
    def test_skips_shadow_banned(self, db, shadow_banned_user, achievement_factory, accepted_scan_factory):
        from services.achievement_service import check_achievements

        achievement_factory(
            code="ck_v_first_sb",
            trigger_type="scan_count",
            target_value=1,
            cab_reward=20,
        )
        accepted_scan_factory(user_id=shadow_banned_user.id)
        result = check_achievements(db, shadow_banned_user.id, "scan_accepted", {})
        assert result == []

    def test_skips_deleted_user(self, db, deleted_user, achievement_factory, accepted_scan_factory):
        from services.achievement_service import check_achievements

        achievement_factory(
            code="ck_v_first_del",
            trigger_type="scan_count",
            target_value=1,
            cab_reward=20,
        )
        accepted_scan_factory(user_id=deleted_user.id)
        assert check_achievements(db, deleted_user.id, "scan_accepted", {}) == []

    def test_unlocks_when_threshold_reached(self, db, test_user, achievement_factory, accepted_scan_factory):
        from services.achievement_service import check_achievements

        ach = achievement_factory(
            code="ck_dispatch_test",
            trigger_type="scan_count",
            target_value=1,
            cab_reward=20,
        )
        accepted_scan_factory(user_id=test_user.id)
        result = check_achievements(db, test_user.id, "scan_accepted", {})
        assert ach.id in result

    def test_excludes_windowed_from_event_path(self, db, test_user, achievement_factory):
        """``savings_eur_in_window`` is batch-only and must NEVER unlock via
        the event path even if its threshold is somehow met."""
        from services.achievement_service import check_achievements

        achievement_factory(
            code="ck_windowed",
            trigger_type="savings_eur_in_window",
            target_value=100,
            window_days=1,
            cab_reward=50,
        )
        result = check_achievements(db, test_user.id, "cashback_credited", {})
        assert result == []

    def test_respects_available_until_window(self, db, test_user, achievement_factory, accepted_scan_factory):
        """Limited-time achievement past its ``available_until`` is skipped.

        The seeded catalog also has ``scan_count`` achievements (``v_first``,
        etc.) that may unlock too — assertion is on the specific test row.
        """
        from services.achievement_service import check_achievements

        past = datetime(2025, 1, 1, tzinfo=UTC)
        ach_past = achievement_factory(
            code="ck_past_event",
            trigger_type="scan_count",
            target_value=1,
            cab_reward=20,
            available_until=past,
        )
        accepted_scan_factory(user_id=test_user.id)
        result = check_achievements(db, test_user.id, "scan_accepted", {})
        assert ach_past.id not in result

    def test_first_event_discrimination(self, db, test_user, achievement_factory):
        """``first_event`` MUST only match the achievement whose
        ``extra_params.event`` equals the dispatched ``event_type``.

        Regression guard for the critical bug : without the SQL filter on
        ``extra_params['event'].astext == event_type``, every ``first_event``
        achievement would unlock on every event of any type.
        """
        from services.achievement_service import check_achievements

        ach_konami = achievement_factory(
            code="ck_konami",
            trigger_type="first_event",
            target_value=1,
            cab_reward=1200,
            rarity="diamond",
            category="secret",
            is_secret=True,
            extra_params={"event": "konami_code_entered"},
        )
        ach_3am = achievement_factory(
            code="ck_3am",
            trigger_type="first_event",
            target_value=1,
            cab_reward=100,
            category="secret",
            is_secret=True,
            extra_params={"event": "app_opened_at_3am"},
        )
        result = check_achievements(db, test_user.id, "konami_code_entered", {})
        assert ach_konami.id in result
        assert ach_3am.id not in result

    def test_handler_exception_continues_with_other_achievements(
        self, db, test_user, achievement_factory, accepted_scan_factory, monkeypatch
    ):
        """One handler crashing must not poison the rest of the evaluation.

        The dispatcher iterates candidates in the order returned by SELECT ;
        the test wraps the ``scan_count`` handler so the FIRST call raises and
        the rest succeed. The first candidate is then absent from the result
        but every subsequent unlock still lands.
        """
        from services import achievement_service

        achievement_factory(
            code="ck_ok",
            trigger_type="scan_count",
            target_value=1,
            cab_reward=20,
        )
        accepted_scan_factory(user_id=test_user.id)

        original = achievement_service.TRIGGER_HANDLERS["scan_count"]
        call_count = {"n": 0}

        def buggy_then_ok(*args, **kwargs):
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise RuntimeError("simulated handler bug")
            return original(*args, **kwargs)

        monkeypatch.setitem(achievement_service.TRIGGER_HANDLERS, "scan_count", buggy_then_ok)

        result = achievement_service.check_achievements(db, test_user.id, "scan_accepted", {})
        # At least one scan_count achievement must have unlocked despite the
        # first handler invocation crashing — either ``ck_ok`` itself or one
        # of the seeded scan_count rows (v_first / v_10 / ...). Either way
        # the dispatcher produced ≥1 unlock, proving the exception was not
        # fatal.
        assert len(result) >= 1
        # AND the dispatcher's own call counter must show at least 2 calls
        # (one crashed, one succeeded) — otherwise the test isn't proving
        # what it claims.
        assert call_count["n"] >= 2


# ---------------------------------------------------------------------------
# compute_progress (V1.1 — KP-76 fix)
# ---------------------------------------------------------------------------
class TestComputeProgress:
    """``compute_progress(db, ach, user_id) -> int|float|None``.

    Returns the live scalar value of the user's progress for ``ach``,
    capped at ``ach.target_value``. Powers the FE X/Y bar in
    ``<AchievementCard />``.
    """

    def test_returns_zero_when_no_progress(self, db, test_user, achievement_factory):
        from services.achievement_service import compute_progress

        ach = achievement_factory(
            code="cp_zero",
            trigger_type="scan_count",
            target_value=10,
            cab_reward=20,
        )
        # No scans yet → progress = 0.
        assert compute_progress(db, ach, test_user.id) == 0

    def test_returns_partial_progress(self, db, test_user, achievement_factory, accepted_scan_factory):
        from services.achievement_service import compute_progress

        ach = achievement_factory(
            code="cp_partial",
            trigger_type="scan_count",
            target_value=10,
            cab_reward=20,
        )
        for _ in range(3):
            accepted_scan_factory(user_id=test_user.id)
        assert compute_progress(db, ach, test_user.id) == 3

    def test_caps_at_target(self, db, test_user, achievement_factory, accepted_scan_factory):
        """Progress is capped at target so the FE never shows ``17/10``."""
        from services.achievement_service import compute_progress

        ach = achievement_factory(
            code="cp_capped",
            trigger_type="scan_count",
            target_value=10,
            cab_reward=20,
        )
        for _ in range(17):
            accepted_scan_factory(user_id=test_user.id)
        # 17 actual scans, target=10 → capped to 10.
        assert compute_progress(db, ach, test_user.id) == 10

    def test_returns_none_for_unknown_trigger(self, db, test_user, achievement_factory):
        """Forward-compat — admin adds new trigger_type without code change.

        We can't rely on ``achievement_factory`` for this (the model uses an
        ENUM constraint). We mock by patching the registry temporarily.
        """
        from services.achievement_service import compute_progress

        ach = achievement_factory(
            code="cp_unknown",
            trigger_type="scan_count",
            target_value=10,
            cab_reward=20,
        )
        # Mutate the in-memory ach to a fake trigger type the dispatcher
        # doesn't know about.
        ach.trigger_type = "future_trigger_not_yet_registered"
        assert compute_progress(db, ach, test_user.id) is None

    def test_returns_none_when_handler_raises(self, db, test_user, achievement_factory, monkeypatch):
        """Defensive fallback — buggy computer must NOT poison the response.

        The serializer must keep returning a dict (with ``progress: null``)
        for the row rather than 500-ing the entire ``GET /achievements``
        endpoint.
        """
        from services import achievement_service

        ach = achievement_factory(
            code="cp_raises",
            trigger_type="scan_count",
            target_value=10,
            cab_reward=20,
        )

        def buggy(*args, **kwargs):
            raise RuntimeError("simulated computer bug")

        monkeypatch.setitem(achievement_service.TRIGGER_PROGRESS_COMPUTERS, "scan_count", buggy)
        assert achievement_service.compute_progress(db, ach, test_user.id) is None

    def test_dispatches_per_trigger_type(self, db, test_user, achievement_factory):
        """Different trigger types route to their respective computers."""
        from services.achievement_service import compute_progress

        # streak_days achievement → routes to _compute_streak_days.
        ach_streak = achievement_factory(
            code="cp_streak",
            trigger_type="streak_days",
            target_value=10,
            cab_reward=20,
        )
        # No streak row → 0.
        assert compute_progress(db, ach_streak, test_user.id) == 0
        # Now seed a streak row with 4 days → progress=4.
        db.execute(
            text(
                "INSERT INTO user_streaks (user_id, current_streak_days, food_reserves) "
                "VALUES (:uid, 4, 0) "
                "ON CONFLICT (user_id) DO UPDATE SET current_streak_days = 4"
            ),
            {"uid": test_user.id},
        )
        db.commit()
        assert compute_progress(db, ach_streak, test_user.id) == 4

    def test_passes_window_days_and_extra_to_computer(self, db, test_user, achievement_factory):
        """``window_days`` + ``extra_params`` from the catalog row are
        forwarded to the computer (e.g. windowed savings, scan-window
        season filter).
        """
        from services.achievement_service import compute_progress

        # savings_eur_in_window with window_days=1 → computer reads it.
        ach = achievement_factory(
            code="cp_in_window",
            trigger_type="savings_eur_in_window",
            target_value=2_000,
            window_days=1,
            cab_reward=50,
        )
        # Credit 1500 c right now → computer should pick it up.
        from tests.conftest import make_cashback_credit

        make_cashback_credit(db, user_id=test_user.id, amount=1500, status="confirmed")
        db.commit()
        # Cap at target=2000 → 1500 (under cap).
        assert compute_progress(db, ach, test_user.id) == 1500
