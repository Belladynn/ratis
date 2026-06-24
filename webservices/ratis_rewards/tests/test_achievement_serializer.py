"""Tests for ``services/achievement_serializer.py``.

Cf docs/superpowers/specs/2026-05-09-achievements-v1-design.md § 5
"Limited-time + j_y_etais" + § 7 "Endpoints API".

The serializer enforces the following display rules :

1. Limited-time window CLOSED + user not unlocked → return ``None``
   (the achievement is no longer obtainable, do not show it).
2. ``is_hidden=True`` + user not unlocked → return ``None``
   (the achievement should not appear in the catalog at all until unlocked).
3. ``is_secret=True`` + user not unlocked → return masked dict
   (label="???", icon="❓", description="Mystère...", code=None).
4. Limited-time window CLOSED + user UNLOCKED → return full dict with
   ``category='j_y_etais'`` (the 8th computed-only category, applied at
   serialization time — the catalog itself never stores this value).
5. Otherwise → return full dict with the catalog's category.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

# ---------------------------------------------------------------------------
# Fixtures helpers
# ---------------------------------------------------------------------------


def _now() -> datetime:
    return datetime(2026, 5, 9, 12, 0, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Rule 1 — limited-time window closed + not unlocked → None
# ---------------------------------------------------------------------------


def test_limited_time_closed_not_unlocked_returns_none(db, achievement_factory):
    """Window is over, user never unlocked → hide entirely."""
    from services.achievement_serializer import serialize_achievement_for_user

    now = _now()
    ach = achievement_factory(
        code="halloween_2024",
        category="seasonal",
        available_from=now - timedelta(days=200),
        available_until=now - timedelta(days=100),
    )
    assert serialize_achievement_for_user(ach, ua=None, now=now) is None


# ---------------------------------------------------------------------------
# Rule 2 — hidden + not unlocked → None
# ---------------------------------------------------------------------------


def test_hidden_not_unlocked_returns_none(db, achievement_factory):
    """is_hidden=True hides from catalog until user unlocks it."""
    from services.achievement_serializer import serialize_achievement_for_user

    ach = achievement_factory(code="hidden_one", is_hidden=True)
    assert serialize_achievement_for_user(ach, ua=None, now=_now()) is None


# ---------------------------------------------------------------------------
# Rule 3 — secret + not unlocked → masked dict
# ---------------------------------------------------------------------------


def test_secret_not_unlocked_returns_masked_dict(db, achievement_factory):
    """is_secret=True returns a masked dict with placeholders."""
    from services.achievement_serializer import serialize_achievement_for_user

    ach = achievement_factory(
        code="konami",
        label="Code Konami",
        description="Real description",
        icon="game",
        rarity="diamond",
        category="secret",
        cab_reward=500,
        is_secret=True,
    )
    out = serialize_achievement_for_user(ach, ua=None, now=_now())
    assert out is not None
    assert out["id"] == str(ach.id)
    assert out["code"] is None
    assert out["label"] == "???"
    assert out["icon"] == "❓"  # ❓
    assert out["description"] == "Mystère..."  # Mystère...
    assert out["rarity"] == "diamond"
    assert out["category"] == "secret"
    assert out["cab_reward"] is None
    assert out["target_value"] is None
    assert out["progress"] is None
    assert out["unlocked"] is False
    assert out["unlocked_at"] is None
    assert out["window_open"] is True


# ---------------------------------------------------------------------------
# Rule 4 — limited-time closed + UNLOCKED → category override = j_y_etais
# ---------------------------------------------------------------------------


def test_limited_time_closed_unlocked_overrides_category_to_jyetais(db, test_user, achievement_factory):
    """Window is over but the user did unlock it → display in 'J'y étais'."""
    from ratis_core.models.achievement import UserAchievement
    from services.achievement_serializer import serialize_achievement_for_user

    now = _now()
    ach = achievement_factory(
        code="winter_2024",
        category="seasonal",
        cab_reward=100,
        available_from=now - timedelta(days=200),
        available_until=now - timedelta(days=100),
    )
    ua = UserAchievement(
        user_id=test_user.id,
        achievement_id=ach.id,
        cab_granted=ach.cab_reward,
        unlocked_at=now - timedelta(days=150),
    )
    db.add(ua)
    db.commit()

    out = serialize_achievement_for_user(ach, ua=ua, now=now)
    assert out is not None
    assert out["category"] == "j_y_etais"
    assert out["unlocked"] is True
    assert out["window_open"] is False
    assert out["code"] == "winter_2024"
    assert out["cab_reward"] == 100


# ---------------------------------------------------------------------------
# Rule 5 — normal achievement, not unlocked → full dict
# ---------------------------------------------------------------------------


def test_unlocked_returns_full_dict_with_unlocked_at(db, test_user, achievement_factory):
    """Vanilla achievement with user unlock → full dict with ISO timestamp."""
    from ratis_core.models.achievement import UserAchievement
    from services.achievement_serializer import serialize_achievement_for_user

    now = _now()
    ach = achievement_factory(
        code="ser_full_one",
        label="Premier scan",
        description="Scanner ton tout premier ticket",
        icon="x",
        rarity="terracotta",
        category="volume",
        cab_reward=20,
        target_value=1,
    )
    unlocked_at = now - timedelta(hours=3)
    ua = UserAchievement(
        user_id=test_user.id,
        achievement_id=ach.id,
        cab_granted=20,
        unlocked_at=unlocked_at,
    )
    db.add(ua)
    db.commit()

    out = serialize_achievement_for_user(ach, ua=ua, now=now)
    assert out is not None
    assert out["id"] == str(ach.id)
    assert out["code"] == "ser_full_one"
    assert out["label"] == "Premier scan"
    assert out["description"] == "Scanner ton tout premier ticket"
    assert out["icon"] == "x"
    assert out["rarity"] == "terracotta"
    assert out["category"] == "volume"
    assert out["cab_reward"] == 20
    assert out["target_value"] == 1.0
    # V1.1 — unlocked → progress = target_value (full bar). No DB needed.
    assert out["progress"] == 1.0
    assert out["unlocked"] is True
    assert out["unlocked_at"] == unlocked_at.isoformat()
    assert out["window_open"] is True


def test_not_unlocked_inside_window_returns_full_dict(db, achievement_factory):
    """Window is currently open, user not unlocked yet → full dict, unlocked=False.

    No ``db``/``user_id`` passed → progress falls back to None (legacy V1
    behaviour preserved when serializer is called from a unit test without
    DB context).
    """
    from services.achievement_serializer import serialize_achievement_for_user

    now = _now()
    ach = achievement_factory(
        code="seasonal_open",
        category="seasonal",
        cab_reward=50,
        target_value=5,
        available_from=now - timedelta(days=10),
        available_until=now + timedelta(days=10),
    )
    out = serialize_achievement_for_user(ach, ua=None, now=now)
    assert out is not None
    assert out["unlocked"] is False
    assert out["unlocked_at"] is None
    assert out["window_open"] is True
    assert out["category"] == "seasonal"  # NOT j_y_etais (window still open)
    assert out["cab_reward"] == 50
    assert out["target_value"] == 5.0
    # No db/user_id passed → progress=None (V1 fallback preserved).
    assert out["progress"] is None


# ---------------------------------------------------------------------------
# V1.1 — progress field via compute_progress (KP-76)
# ---------------------------------------------------------------------------


def test_progress_populated_when_db_and_user_id_provided(db, test_user, achievement_factory, accepted_scan_factory):
    """Caller supplies db + user_id → serializer fetches live scan_count progress."""
    from services.achievement_serializer import serialize_achievement_for_user

    ach = achievement_factory(
        code="ser_progress_live",
        category="volume",
        trigger_type="scan_count",
        target_value=10,
        cab_reward=20,
    )
    # 4 accepted scans → progress = 4 (under target=10).
    for _ in range(4):
        accepted_scan_factory(user_id=test_user.id)

    out = serialize_achievement_for_user(ach, ua=None, now=_now(), db=db, user_id=test_user.id)
    assert out is not None
    assert out["unlocked"] is False
    assert out["progress"] == 4
    assert out["target_value"] == 10.0


def test_progress_none_when_db_missing(db, test_user, achievement_factory, accepted_scan_factory):
    """Legacy code path (no db arg) keeps returning ``progress: null``."""
    from services.achievement_serializer import serialize_achievement_for_user

    ach = achievement_factory(
        code="ser_no_db",
        trigger_type="scan_count",
        target_value=10,
        cab_reward=20,
    )
    accepted_scan_factory(user_id=test_user.id)
    out = serialize_achievement_for_user(ach, ua=None, now=_now())
    assert out is not None
    assert out["progress"] is None


def test_progress_capped_at_target(db, test_user, achievement_factory, accepted_scan_factory):
    """User exceeded target but not yet unlocked (e.g. dispatcher race) → cap."""
    from services.achievement_serializer import serialize_achievement_for_user

    ach = achievement_factory(
        code="ser_progress_cap",
        trigger_type="scan_count",
        target_value=3,
        cab_reward=20,
    )
    # 9 actual scans → cap at 3.
    for _ in range(9):
        accepted_scan_factory(user_id=test_user.id)
    out = serialize_achievement_for_user(ach, ua=None, now=_now(), db=db, user_id=test_user.id)
    assert out is not None
    assert out["progress"] == 3
