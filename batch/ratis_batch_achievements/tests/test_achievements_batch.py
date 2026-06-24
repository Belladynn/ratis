"""Tests for ratis_batch_achievements.

Three behavioural assertions :

1. Windowed trigger (``savings_eur_in_window``) — only the batch path can
   evaluate this trigger ; the event-driven dispatcher excludes it. Asserts
   the batch unlocks the achievement AND credits the catalog CAB amount.
2. Anti-shadow-ban defence-in-depth — even though the catch-all ``_unlock``
   path goes through ``check_achievements``-style filters in production, the
   batch enumerates users itself and MUST exclude shadow-banned ones at the
   SELECT layer. No unlock row should exist for a banned user.
3. Idempotence — two consecutive runs over the same data produce one row,
   not two. Relies on the UNIQUE(user_id, achievement_id) +
   ``ON CONFLICT DO NOTHING`` in ``_unlock``.
"""

from __future__ import annotations


def test_batch_unlocks_savings_eur_in_window(
    db_session,
    test_user,
    achievement_factory,
    cashback_transaction_factory,
):
    from ratis_core.models.achievement import UserAchievement

    from batch.ratis_batch_achievements.achievements_batch import run_batch

    achievement_factory(
        code="b_window",
        trigger_type="savings_eur_in_window",
        target_value=2000,
        window_days=1,
        cab_reward=150,
    )
    cashback_transaction_factory(user_id=test_user.id, amount=2000)

    result = run_batch(db_session)

    assert result.success is True
    assert result.rows_affected >= 1
    ua = db_session.query(UserAchievement).filter_by(user_id=test_user.id).first()
    assert ua is not None
    assert ua.cab_granted == 150


def test_batch_skips_shadow_banned_user(
    db_session,
    shadow_banned_user,
    achievement_factory,
    accepted_scan_factory,
):
    from ratis_core.models.achievement import UserAchievement

    from batch.ratis_batch_achievements.achievements_batch import run_batch

    achievement_factory(
        code="b_skip_ban",
        trigger_type="scan_count",
        target_value=1,
        cab_reward=20,
    )
    accepted_scan_factory(user_id=shadow_banned_user.id)

    run_batch(db_session)

    count = db_session.query(UserAchievement).filter_by(user_id=shadow_banned_user.id).count()
    assert count == 0


def test_batch_idempotent_double_run(
    db_session,
    test_user,
    achievement_factory,
    cashback_transaction_factory,
):
    from ratis_core.models.achievement import UserAchievement

    from batch.ratis_batch_achievements.achievements_batch import run_batch

    achievement_factory(
        code="b_idem",
        trigger_type="savings_eur_in_window",
        target_value=100,
        window_days=1,
        cab_reward=20,
    )
    cashback_transaction_factory(user_id=test_user.id, amount=100)

    r1 = run_batch(db_session)
    r2 = run_batch(db_session)

    count = db_session.query(UserAchievement).filter_by(user_id=test_user.id).count()
    assert count == 1
    assert r1.rows_affected >= 1
    assert r2.rows_affected == 0
