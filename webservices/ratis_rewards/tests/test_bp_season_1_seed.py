"""Tests for the Battle Pass Season 1 seed.

These tests guard the data-only migration (and its companion seed helper
in ``ratis_core.seed.bp_season_1``) that inserts :

- 1 row in ``battlepass_seasons`` for Saison 1 (active, 90-day window).
- 30 rows in ``battlepass_milestones`` linked to that season, following
  the two-segment exponential curve agreed with product (mid = 10000 CAB
  / 5€ gift card at palier 15, end = 40000 CAB / 20€ gift card at
  palier 30).

The seed function (``seed_bp_season_1``) is purely SQL-driven so it can
run inside an Alembic migration where mapped models may not be fully
registered. These tests exercise it directly against the ``create_all``
test schema (no Alembic) — same pattern as ``test_missions_catalog_v1``.
"""

from __future__ import annotations

import pytest
from ratis_core.seed.bp_season_1 import (
    BP_SEASON_1_END,
    BP_SEASON_1_START,
    cab_required_for_palier,
    cab_reward_for_palier,
    seed_bp_season_1,
)
from sqlalchemy import text

# ---------------------------------------------------------------------- #
# Pure-Python curve unit tests — no DB required.                         #
# ---------------------------------------------------------------------- #


def test_palier_1_first_cab() -> None:
    """Palier 1 anchors the curve at 50 CAB."""
    assert cab_required_for_palier(1) == 50


def test_palier_15_mid_cab_required() -> None:
    """Palier 15 (mid) anchors the curve at 10 000 CAB."""
    assert cab_required_for_palier(15) == 10000


def test_palier_30_end_cab_required() -> None:
    """Palier 30 (end) anchors the curve at 40 000 CAB."""
    assert cab_required_for_palier(30) == 40000


def test_courbe_monotone_croissante() -> None:
    """For every i in [1, 29], cab_required(i+1) > cab_required(i)."""
    for i in range(1, 30):
        assert cab_required_for_palier(i + 1) > cab_required_for_palier(i), f"curve not monotone at palier {i}"


def test_cab_rewards_waves_pattern() -> None:
    """Waves pattern : two segments with peaks at paliers 8 and 22.

    Acted 2026-05-08 — the original linear curve (peak palier 29 ≈ 2897
    CAB, total ~40 000 CAB redistributed) was over-generous. The product
    owner validated a "waves" pattern with two crests :

    - segment 1 (paliers 1-14)  : crest at palier 8  → 250 CAB
    - segment 2 (paliers 16-29) : crest at palier 22 → 400 CAB (overall max)

    Invariants locked here :
    - segment 2 crest > segment 1 crest (segment 2 plus rentable)
    - all reward values are multiples of 50 (UI / accounting friendliness)
    - total redistributed across the 28 cab-reward paliers ∈ [5000, 5200]
      (~5100 CAB target — vs ~40000 with the old linear curve).
    """
    # Boundary values + crests.
    assert cab_reward_for_palier(1) == 50
    assert cab_reward_for_palier(8) == 250  # peak segment 1
    assert cab_reward_for_palier(14) == 50  # valley pre-mid (palier 15 = gift card)
    assert cab_reward_for_palier(22) == 400  # peak segment 2 (overall max)
    assert cab_reward_for_palier(28) == 100
    assert cab_reward_for_palier(29) == 100

    # Invariant : segment 2 peak strictly higher than segment 1 peak.
    assert cab_reward_for_palier(22) > cab_reward_for_palier(8), (
        "segment 2 peak (palier 22) must outrank segment 1 peak (palier 8)"
    )

    # All cab-reward paliers (1-14, 16-29) must be multiples of 50.
    cab_paliers = list(range(1, 15)) + list(range(16, 30))
    for p in cab_paliers:
        v = cab_reward_for_palier(p)
        assert v % 50 == 0, f"palier {p} reward {v} is not a multiple of 50"

    # Total redistributed across the 28 cab-reward paliers ∈ [5000, 5200].
    total = sum(cab_reward_for_palier(p) for p in cab_paliers)
    assert 5000 <= total <= 5200, f"total CAB redistributed = {total}, expected ∈ [5000, 5200]"


# ---------------------------------------------------------------------- #
# DB integration tests — exercise the seed against a clean DB.           #
# ---------------------------------------------------------------------- #


@pytest.fixture
def seeded_season_1(db):
    """Reset BP tables then apply the Saison 1 seed.

    create_all (used by conftest) does not run Alembic data migrations,
    so each test that asserts seed-level invariants applies the seed
    function directly. We wipe BP tables first to make the test
    independent of leftover rows from earlier tests in the session.
    """
    # Wipe in FK-safe order (claims → progress → milestones → seasons).
    db.execute(text("DELETE FROM user_battlepass_claims"))
    db.execute(text("DELETE FROM user_battlepass_progress"))
    db.execute(text("DELETE FROM battlepass_milestones"))
    db.execute(text("DELETE FROM battlepass_seasons"))
    db.flush()
    seed_bp_season_1(db)
    db.flush()
    return db


def test_saison_1_active(seeded_season_1) -> None:
    """Exactly 1 active battlepass_seasons row, season_number=1."""
    db = seeded_season_1
    rows = db.execute(
        text(
            "SELECT season_number, name, started_at, ends_at, is_active FROM battlepass_seasons WHERE is_active = TRUE"
        )
    ).fetchall()
    assert len(rows) == 1
    row = rows[0]
    assert row.season_number == 1
    assert row.name == "Saison 1"
    assert row.is_active is True
    # Started/ended timestamps match the hardcoded calibration window.
    assert row.started_at.replace(tzinfo=None) == BP_SEASON_1_START.replace(tzinfo=None)
    assert row.ends_at.replace(tzinfo=None) == BP_SEASON_1_END.replace(tzinfo=None)


def test_30_milestones_seeded(seeded_season_1) -> None:
    """30 battlepass_milestones rows linked to Saison 1."""
    db = seeded_season_1
    season_id = db.execute(
        text("SELECT id FROM battlepass_seasons WHERE season_number = 1 AND is_active = TRUE")
    ).scalar_one()
    n = db.execute(
        text("SELECT count(*) FROM battlepass_milestones WHERE season_id = :sid"),
        {"sid": season_id},
    ).scalar()
    assert n == 30
    # Milestone numbers must form the exact set {1, 2, …, 30}.
    nums = (
        db.execute(
            text("SELECT milestone_number FROM battlepass_milestones WHERE season_id = :sid ORDER BY milestone_number"),
            {"sid": season_id},
        )
        .scalars()
        .all()
    )
    assert list(nums) == list(range(1, 31))


def test_palier_15_mid_gift_card(seeded_season_1) -> None:
    """Palier 15 → 10000 CAB required, gift card 500 cents (5€)."""
    db = seeded_season_1
    row = db.execute(
        text(
            "SELECT m.cab_required, m.reward_type, m.reward_value, "
            "       m.subscriber_only "
            "FROM battlepass_milestones m "
            "JOIN battlepass_seasons s ON s.id = m.season_id "
            "WHERE s.season_number = 1 AND m.milestone_number = 15"
        )
    ).one()
    assert row.cab_required == 10000
    assert row.reward_type == "gift_card"
    assert row.reward_value == 500
    assert row.subscriber_only is False


def test_palier_30_end_gift_card(seeded_season_1) -> None:
    """Palier 30 → 40000 CAB required, gift card 2000 cents (20€)."""
    db = seeded_season_1
    row = db.execute(
        text(
            "SELECT m.cab_required, m.reward_type, m.reward_value, "
            "       m.subscriber_only "
            "FROM battlepass_milestones m "
            "JOIN battlepass_seasons s ON s.id = m.season_id "
            "WHERE s.season_number = 1 AND m.milestone_number = 30"
        )
    ).one()
    assert row.cab_required == 40000
    assert row.reward_type == "gift_card"
    assert row.reward_value == 2000
    assert row.subscriber_only is False


def test_intermediate_paliers_are_cab(seeded_season_1) -> None:
    """All non-gift-card milestones (1-14, 16-29) carry reward_type='cab'."""
    db = seeded_season_1
    sample_paliers = [2, 5, 10, 14, 16, 20, 25, 29]
    rows = db.execute(
        text(
            "SELECT m.milestone_number, m.reward_type, m.subscriber_only "
            "FROM battlepass_milestones m "
            "JOIN battlepass_seasons s ON s.id = m.season_id "
            "WHERE s.season_number = 1 "
            "  AND m.milestone_number = ANY(:nums) "
            "ORDER BY m.milestone_number"
        ),
        {"nums": sample_paliers},
    ).all()
    assert len(rows) == len(sample_paliers)
    for row in rows:
        assert row.reward_type == "cab", f"palier {row.milestone_number} must be reward_type='cab'"
        assert row.subscriber_only is False


def test_db_curve_monotone(seeded_season_1) -> None:
    """The persisted cab_required values form a strictly increasing sequence."""
    db = seeded_season_1
    cabs = (
        db.execute(
            text(
                "SELECT m.cab_required FROM battlepass_milestones m "
                "JOIN battlepass_seasons s ON s.id = m.season_id "
                "WHERE s.season_number = 1 "
                "ORDER BY m.milestone_number"
            )
        )
        .scalars()
        .all()
    )
    assert len(cabs) == 30
    for i in range(len(cabs) - 1):
        assert cabs[i + 1] > cabs[i], f"db curve not monotone at palier {i + 1}: {cabs[i]} → {cabs[i + 1]}"


def test_seed_is_idempotent(seeded_season_1) -> None:
    """Re-running the seed leaves exactly 1 season + 30 milestones."""
    db = seeded_season_1
    seed_bp_season_1(db)
    db.flush()
    n_seasons = db.execute(text("SELECT count(*) FROM battlepass_seasons WHERE season_number = 1")).scalar()
    n_milestones = db.execute(
        text(
            "SELECT count(*) FROM battlepass_milestones m "
            "JOIN battlepass_seasons s ON s.id = m.season_id "
            "WHERE s.season_number = 1"
        )
    ).scalar()
    assert n_seasons == 1
    assert n_milestones == 30


def test_seed_deactivates_other_active_season(db) -> None:
    """If another season is already active, the seed flips it to inactive
    before inserting Saison 1 — the partial unique index
    ``uq_one_active_season`` forbids two active rows simultaneously."""
    # Wipe + insert a different active season (number=99 to dodge the
    # season_number unique constraint).
    db.execute(text("DELETE FROM user_battlepass_claims"))
    db.execute(text("DELETE FROM user_battlepass_progress"))
    db.execute(text("DELETE FROM battlepass_milestones"))
    db.execute(text("DELETE FROM battlepass_seasons"))
    db.execute(
        text(
            "INSERT INTO battlepass_seasons "
            "  (id, season_number, name, started_at, ends_at, is_active) "
            "VALUES (gen_random_uuid(), 99, 'Legacy', "
            "        now() - interval '120 days', "
            "        now() - interval '30 days', TRUE)"
        )
    )
    db.flush()
    seed_bp_season_1(db)
    db.flush()
    # Now exactly 2 rows in seasons (legacy + saison 1) but only 1 active.
    n_total = db.execute(text("SELECT count(*) FROM battlepass_seasons")).scalar()
    n_active = db.execute(text("SELECT count(*) FROM battlepass_seasons WHERE is_active = TRUE")).scalar()
    assert n_total == 2
    assert n_active == 1
    # The active row is Saison 1.
    active_num = db.execute(text("SELECT season_number FROM battlepass_seasons WHERE is_active = TRUE")).scalar()
    assert active_num == 1
