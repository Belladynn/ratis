"""Tests for the V1 missions catalog (phase A — schema + seed only).

These tests guard the migration that adds two columns and re-seeds the
catalogue with 41 templates :

- ``missions.qualifier`` (TEXT NULL) : optional filter on the action
  (e.g. ``organic``, ``french``, ``category``, ``store``). NULL = no filter.
- ``user_missions.tracked_values`` (JSONB NULL) : bag of distinct values
  observed during the period — used by the ``scan_distinct`` action_type
  to count distinct categories / stores. Other action_types leave it NULL.

The unique constraint on the catalogue table is extended to include
``qualifier`` so that two templates can coexist when they target the
same (action_type, frequency, difficulty) tuple but disagree on the
qualifier (e.g. ``barcode_scan/daily/easy/NULL`` vs
``barcode_scan/daily/easy/organic``).

Phase B (service code that will actually award these missions) ships
separately. To avoid surfacing missions the runtime cannot honour, all
templates that depend on phase B logic are seeded with ``is_active=false``.
Only the 14 templates targeting the three pre-existing action_types
(``receipt_scan`` / ``label_scan`` / ``barcode_scan``) with
``qualifier IS NULL`` are active out of the gate.
"""

from __future__ import annotations

import pytest
from ratis_core.seed.missions_v1 import seed_missions_catalog_v1
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError


@pytest.fixture
def seeded_missions(db):
    """Reset the catalogue then apply the V1 seed.

    create_all (used by conftest) does not run Alembic data migrations,
    so each test that asserts seed-level invariants applies the seed
    function directly. We wipe ``missions`` first to make the test
    independent of any leftover rows from earlier tests in the session.

    Returns the db session. No teardown — the conftest savepoint pattern
    rolls every test back to a clean state automatically.
    """
    # Wipe user_missions (FK RESTRICT) then missions to start clean.
    db.execute(text("DELETE FROM user_missions"))
    db.execute(text("DELETE FROM missions"))
    db.flush()
    seed_missions_catalog_v1(db)
    db.flush()
    return db


def test_seed_count(seeded_missions):
    """The catalogue holds exactly 41 templates after seed."""
    db = seeded_missions
    n = db.execute(text("SELECT count(*) FROM missions")).scalar()
    assert n == 41


def test_seed_active_count(seeded_missions):
    """Phase B (PR #325) flipped every template to ``is_active=true``.
    Post-#325 follow-up : the 9 templates with ``qualifier`` in
    ``('attribute:organic', 'attribute:french')`` are deactivated until
    phase C ships PA worker qualifier enrichment (events emitted with
    the ``attribute:`` prefix). Without that upstream signal, the 9
    rows would be visible to users via lazy-gen but their
    ``current_count`` would never increment — broken missions. So the
    catalogue holds 41 rows total but only 32 active."""
    db = seeded_missions
    n_active = db.execute(text("SELECT count(*) FROM missions WHERE is_active = TRUE")).scalar()
    n_total = db.execute(text("SELECT count(*) FROM missions")).scalar()
    assert n_total == 41
    assert n_active == 32, (
        f"expected 32 active templates (41 total - 9 attribute-qualifier deactivated pending phase C), got {n_active}"
    )


def test_qualifier_attribute_templates_inactive(seeded_missions):
    """The 9 templates carrying ``qualifier IN ('attribute:organic',
    'attribute:french')`` MUST be ``is_active=false`` until phase C
    ships PA worker qualifier enrichment.

    Breakdown of the 9 :
      * product_identification + attribute:organic : daily/easy +
        weekly/easy + weekly/medium → 3 rows
      * product_identification + attribute:french  : same 3 frequencies → 3 rows
      * fill_product_field    + attribute:organic : same 3 frequencies → 3 rows
    """
    db = seeded_missions
    rows = db.execute(
        text(
            "SELECT action_type, qualifier, frequency, difficulty, is_active "
            "FROM missions "
            "WHERE qualifier IN ('attribute:organic', 'attribute:french') "
            "ORDER BY action_type, qualifier, frequency, difficulty"
        )
    ).all()
    assert len(rows) == 9, f"expected 9 attribute-qualifier rows, got {len(rows)}"
    for action_type, qualifier, frequency, difficulty, is_active in rows:
        assert is_active is False, (
            f"({action_type}, {qualifier}, {frequency}, {difficulty}) must be "
            "is_active=false pending phase C qualifier enrichment"
        )


def _insert_mission_raw(
    db,
    *,
    action_type: str,
    qualifier: str | None,
    frequency: str = "daily",
    difficulty: str = "easy",
    target_count: int = 1,
    cab_reward: int = 5,
    is_active: bool = True,
) -> None:
    """Insert + flush a mission row via raw SQL.

    Helper used by tests that exercise constraint violations — keeps the
    ``pytest.raises`` block down to a single statement (PT012).
    """
    db.execute(
        text(
            "INSERT INTO missions "
            "  (id, action_type, qualifier, frequency, difficulty, "
            "   target_count, cab_reward, is_active) "
            "VALUES (gen_random_uuid(), :action_type, :qualifier, "
            "        :frequency, :difficulty, "
            "        :target_count, :cab_reward, :is_active)"
        ),
        {
            "action_type": action_type,
            "qualifier": qualifier,
            "frequency": frequency,
            "difficulty": difficulty,
            "target_count": target_count,
            "cab_reward": cab_reward,
            "is_active": is_active,
        },
    )
    db.flush()


def test_unique_qualifier_extension(seeded_missions):
    """The unique constraint must include qualifier — duplicating an
    existing (action_type, qualifier, frequency, difficulty) tuple raises
    IntegrityError. Phase B renamed barcode_scan → product_identification ;
    the catalogue contains (product_identification, NULL, daily, easy) so a
    duplicate insert on that tuple is the canonical conflict."""
    db = seeded_missions
    with pytest.raises(IntegrityError):
        _insert_mission_raw(
            db,
            action_type="product_identification",
            qualifier=None,
            frequency="daily",
            difficulty="easy",
        )


def test_qualifier_nullable(db):
    """The qualifier column accepts NULL (no-filter mission)."""
    db.execute(text("DELETE FROM user_missions"))
    db.execute(text("DELETE FROM missions"))
    db.flush()
    _insert_mission_raw(
        db,
        action_type="receipt_scan",
        qualifier=None,
        frequency="daily",
        difficulty="easy",
    )
    n = db.execute(
        text("SELECT count(*) FROM missions WHERE action_type = 'receipt_scan' AND qualifier IS NULL")
    ).scalar()
    assert n == 1


def test_qualifier_value_organic(seeded_missions):
    """Spot-check a precise template carrying a prefixed qualifier.

    Phase B renamed ``barcode_scan`` to ``product_identification`` and
    prefixed the bare ``organic`` qualifier to ``attribute:organic``.
    The corresponding seed row must be present. ``is_active`` is False
    pending phase C (PA worker qualifier enrichment) — see
    ``test_qualifier_attribute_templates_inactive``."""
    db = seeded_missions
    row = db.execute(
        text(
            "SELECT target_count, cab_reward, is_active FROM missions "
            "WHERE action_type = 'product_identification' "
            "  AND qualifier = 'attribute:organic' "
            "  AND frequency = 'daily' "
            "  AND difficulty = 'easy'"
        )
    ).first()
    assert row is not None, "expected (product_identification, attribute:organic, daily, easy) row"
    target_count, cab_reward, is_active = row
    assert target_count == 1
    assert cab_reward == 5
    assert is_active is False


def test_check_constraint_action_type(db):
    """The CHECK constraint on action_type rejects unknown values."""
    db.execute(text("DELETE FROM user_missions"))
    db.execute(text("DELETE FROM missions"))
    db.flush()
    with pytest.raises(IntegrityError):
        _insert_mission_raw(
            db,
            action_type="unknown_action_type",
            qualifier=None,
            frequency="daily",
            difficulty="easy",
            is_active=False,
        )


def test_recalibration_grille_earns_v1x(seeded_missions):
    """Recalibration V1.x acted 2026-05-08 — verify the canonical grille
    earns is properly seeded.

    Settings ``cab_per_*`` (per-action base, multiplied by quantity) :

    | key                                | value |
    |------------------------------------|-------|
    | cab_per_receipt_scan               |   20  |
    | cab_per_label_scan                 |    3  |
    | cab_per_barcode_scan               |    1  |
    | cab_per_product_identification     |    1  |
    | cab_per_fill_product_field         |    5  |
    | cab_per_scan_distinct              |    0  |
    | cab_per_promo_found                |    5  |

    Mission ``cab_reward`` (when user completes a mission) :
    - daily   : 5 / 15 / 30  (easy / medium / hard) — unchanged
    - weekly  : 20 / 50 / 100 (easy / medium / hard) — recalibrated from
                50 / 150 / 300

    Source-of-truth : ``ratis_core/config/ratis_settings.json`` for the
    per-action base values, the seed catalogue + this migration for the
    mission rewards.
    """
    from ratis_core.settings import load_settings

    db = seeded_missions
    cfg = load_settings()
    rewards = cfg["rewards"]
    assert rewards["cab_per_receipt_scan"] == 20
    assert rewards["cab_per_label_scan"] == 3
    assert rewards["cab_per_barcode_scan"] == 1
    assert rewards["cab_per_product_identification"] == 1
    assert rewards["cab_per_fill_product_field"] == 5
    assert rewards["cab_per_scan_distinct"] == 0
    assert rewards["cab_per_promo_found"] == 5

    # Mission daily rewards : unchanged at 5 / 15 / 30.
    daily_easy = db.execute(
        text("SELECT DISTINCT cab_reward FROM missions WHERE frequency = 'daily' AND difficulty = 'easy'")
    ).scalar()
    assert daily_easy == 5
    daily_medium = db.execute(
        text("SELECT DISTINCT cab_reward FROM missions WHERE frequency = 'daily' AND difficulty = 'medium'")
    ).scalar()
    assert daily_medium == 15
    daily_hard = db.execute(
        text("SELECT DISTINCT cab_reward FROM missions WHERE frequency = 'daily' AND difficulty = 'hard'")
    ).scalar()
    assert daily_hard == 30

    # Mission weekly rewards : recalibrated to 20 / 50 / 100.
    weekly_easy = db.execute(
        text("SELECT DISTINCT cab_reward FROM missions WHERE frequency = 'weekly' AND difficulty = 'easy'")
    ).scalar()
    assert weekly_easy == 20, f"expected weekly/easy cab_reward=20 (V1.x recal), got {weekly_easy}"
    weekly_medium = db.execute(
        text("SELECT DISTINCT cab_reward FROM missions WHERE frequency = 'weekly' AND difficulty = 'medium'")
    ).scalar()
    assert weekly_medium == 50, f"expected weekly/medium cab_reward=50 (V1.x recal), got {weekly_medium}"
    weekly_hard = db.execute(
        text("SELECT DISTINCT cab_reward FROM missions WHERE frequency = 'weekly' AND difficulty = 'hard'")
    ).scalar()
    assert weekly_hard == 100, f"expected weekly/hard cab_reward=100 (V1.x recal), got {weekly_hard}"

    # Cohesion check : no weekly mission carries a legacy-only value.
    # The pre-recal grid was 50/150/300 (easy/medium/hard) ; the post-recal
    # grid is 20/50/100. Weekly/easy on the legacy 50, weekly/medium on the
    # legacy 150, and weekly/hard on the legacy 300 are all forbidden.
    # Note : weekly/medium=50 is the NEW value, so it's allowed — we filter
    # on the (difficulty, cab_reward) pair, not on cab_reward alone.
    n_legacy_weekly = db.execute(
        text(
            "SELECT count(*) FROM missions WHERE frequency = 'weekly' AND ("
            "  (difficulty = 'easy' AND cab_reward = 50) OR "
            "  (difficulty = 'medium' AND cab_reward = 150) OR "
            "  (difficulty = 'hard' AND cab_reward = 300)"
            ")"
        )
    ).scalar()
    assert n_legacy_weekly == 0, (
        f"found {n_legacy_weekly} weekly missions still on legacy values "
        "(easy=50 / medium=150 / hard=300) — recalibration incomplete"
    )


def test_user_missions_tracked_values_nullable(db):
    """user_missions.tracked_values accepts NULL (default for non-distinct
    action_types) AND a JSONB array (for scan_distinct tracking)."""
    import uuid as _uuid
    from datetime import date

    db.execute(text("DELETE FROM user_missions"))
    db.execute(text("DELETE FROM missions"))
    db.flush()

    # Make a user, then a mission, then two user_missions rows :
    # one with tracked_values NULL, one with a JSONB array.
    from tests.conftest import make_mission, make_user

    user_id = make_user(db)
    mission_id = make_mission(db, action_type="barcode_scan")

    # Row 1 : tracked_values left to default (NULL).
    um_null = _uuid.uuid4()
    db.execute(
        text(
            "INSERT INTO user_missions "
            "  (id, user_id, mission_id, period_start, current_count, "
            "   status, target_count) "
            "VALUES (:id, :uid, :mid, :period, 0, 'pending', 1)"
        ),
        {
            "id": um_null,
            "uid": user_id,
            "mid": mission_id,
            "period": date.today(),
        },
    )

    # Row 2 : tracked_values populated with a JSONB array. Different
    # period_start so we don't trip uq_user_mission_period.
    um_jsonb = _uuid.uuid4()
    from datetime import timedelta

    db.execute(
        text(
            "INSERT INTO user_missions "
            "  (id, user_id, mission_id, period_start, current_count, "
            "   status, target_count, tracked_values) "
            "VALUES (:id, :uid, :mid, :period, 2, 'pending', 5, "
            "        CAST(:tv AS jsonb))"
        ),
        {
            "id": um_jsonb,
            "uid": user_id,
            "mid": mission_id,
            "period": date.today() - timedelta(days=1),
            "tv": '["cat_1", "cat_2"]',
        },
    )
    db.flush()

    # Verify both rows exist and the JSONB column is the expected shape.
    row_null = db.execute(
        text("SELECT tracked_values FROM user_missions WHERE id = :id"),
        {"id": um_null},
    ).scalar()
    assert row_null is None

    row_jsonb = db.execute(
        text("SELECT tracked_values FROM user_missions WHERE id = :id"),
        {"id": um_jsonb},
    ).scalar()
    assert row_jsonb == ["cat_1", "cat_2"]
