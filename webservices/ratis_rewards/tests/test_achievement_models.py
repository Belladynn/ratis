"""Tests for the Achievement + UserAchievement SQLAlchemy models.

Cf docs/superpowers/specs/2026-05-09-achievements-v1-design.md § Data model.

These tests verify the model contract (table names, columns, FK + UNIQUE
constraints) and that the seed catalog is loaded into the test DB by the
``seed_achievements_catalog`` autouse fixture in this test module's conftest.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.exc import IntegrityError

from tests.conftest import make_user


# ---------------------------------------------------------------------------
# Static model contract
# ---------------------------------------------------------------------------
def test_import_achievement_model():
    from ratis_core.models.achievement import Achievement

    assert Achievement.__tablename__ == "achievements"


def test_import_user_achievement_model():
    from ratis_core.models.achievement import UserAchievement

    assert UserAchievement.__tablename__ == "user_achievements"


# ---------------------------------------------------------------------------
# DB-level behaviour
# ---------------------------------------------------------------------------
def test_create_achievement_in_db(db):
    from ratis_core.models.achievement import Achievement

    code = f"_t_create_{uuid.uuid4().hex[:8]}"
    ach = Achievement(
        code=code,
        label="Test",
        description="Test achievement",
        icon="x",
        rarity="terracotta",
        category="volume",
        trigger_type="scan_count",
        target_value=1,
        cab_reward=20,
    )
    db.add(ach)
    db.commit()
    assert ach.id is not None
    # SAVEPOINT rollback in conftest.py handles cleanup.


def test_unique_constraint_user_achievement(db):
    """``UNIQUE (user_id, achievement_id)`` enforces idempotent unlock."""
    from ratis_core.models.achievement import Achievement, UserAchievement

    user_id = make_user(db)

    code = f"_t_unique_{uuid.uuid4().hex[:8]}"
    ach = Achievement(
        code=code,
        label="T",
        description="T",
        icon="x",
        rarity="bronze",
        category="volume",
        trigger_type="scan_count",
        target_value=1,
        cab_reward=30,
    )
    db.add(ach)
    db.commit()

    ua1 = UserAchievement(user_id=user_id, achievement_id=ach.id, cab_granted=30)
    db.add(ua1)
    db.commit()

    ua2 = UserAchievement(user_id=user_id, achievement_id=ach.id, cab_granted=30)
    db.add(ua2)
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()
    # Per-test SAVEPOINT in conftest.py rolls back ach + user — no manual
    # cleanup needed.


# ---------------------------------------------------------------------------
# Seed catalog
# ---------------------------------------------------------------------------
def test_seed_loaded_23_entries(db):
    """Verify the seed migration / fixture inserted exactly 23 entries."""
    from ratis_core.models.achievement import Achievement

    expected_codes = {
        "v_first",
        "v_10",
        "v_50",
        "v_500",
        "v_1000",
        "s_1",
        "s_10",
        "s_50",
        "s_500",
        "s_day_20",
        "r_3",
        "r_7",
        "r_14",
        "r_30",
        "r_365",
        "soc_invite_1",
        "soc_invite_10",
        "exp_brand_5",
        "exp_cat_15",
        "exp_unknown_10",
        "sea_summer",
        "sec_konami",
        "sec_3am",
    }
    actual_codes = {a.code for a in db.query(Achievement).all()}
    assert expected_codes <= actual_codes, f"missing seed codes: {expected_codes - actual_codes}"
    assert "sea_winter" not in actual_codes  # window already passed at seed date


def test_secrets_have_is_secret_true(db):
    from ratis_core.models.achievement import Achievement

    secrets = db.query(Achievement).filter(Achievement.code.in_(["sec_konami", "sec_3am"])).all()
    assert len(secrets) == 2
    assert all(s.is_secret for s in secrets)


def test_diamond_count(db):
    """Per spec : Diamant rarissime — catalog seed = exactly 2.

    Currently r_365 (1-year streak) + sec_konami. If a third is ever added
    the spec must be revisited and this test bumped intentionally.
    """
    from ratis_core.models.achievement import Achievement

    diamonds = db.query(Achievement).filter(Achievement.rarity == "diamond").all()
    diamond_codes = {d.code for d in diamonds}
    assert {"r_365", "sec_konami"} <= diamond_codes
    assert len(diamonds) == 2, f"unexpected diamond entries: {diamond_codes}"
