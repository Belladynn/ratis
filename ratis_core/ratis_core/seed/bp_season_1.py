"""Idempotent seed for the Battle Pass Saison 1 catalogue.

Exposes :

- ``BP_SEASON_1_START`` / ``BP_SEASON_1_END`` — hardcoded calibration
  window (90 days). Hardcoded for reproducibility : a re-run of the
  migration on a fresh DB produces the same row.
- ``cab_required_for_palier(palier)`` — pure-Python implementation of
  the two-segment exponential curve agreed with product. Calling code
  (Alembic migration, tests, admin UI) shares the same source of truth.
- ``cab_reward_for_palier(palier)`` — hardcoded "waves" pattern of the
  in-pass CAB rewards (paliers 15 and 30 ship a gift card instead). The
  pattern has two segments with crests at palier 8 (250 CAB) and palier
  22 (400 CAB — overall max). Total redistributed ≈ 5 100 CAB across the
  28 cab-reward paliers (acted 2026-05-08, replaces the original linear
  curve which redistributed ~40 000 CAB and was over-generous).
- ``seed_bp_season_1(db)`` — UPSERTs the season row + the 30 milestones.

Called from :

- Alembic data migration ``20260508_2000_bp_s1_seed`` — runs the seed
  once on every fresh DB the first time the migration chain is applied.
- Tests — ``test_bp_season_1_seed.py`` exercises the seed against the
  ``create_all`` test schema (no Alembic).

Function is purely SQL-driven (no ORM dependency on
``BattlepassSeason`` / ``BattlepassMilestone``) so it can run inside an
Alembic migration where the mapped models may not be fully registered.

Calibration source : ``docs/superpowers/specs/2026-05-08-gamif-calibration.xlsx``
mid-anchor 10 000 CAB → 5 € gift card (palier 15)
end-anchor 40 000 CAB → 20 € gift card (palier 30)

Two-segment exponential curve :
    paliers  1 → 15  : ratio = (10000 / 50) ** (1 / 14)   ≈ 1.4601
    paliers 15 → 30  : ratio = (40000 / 10000) ** (1 / 15) ≈ 1.0968
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from sqlalchemy import text
from sqlalchemy.orm import Session

_log = logging.getLogger(__name__)


# Hardcoded for reproducibility — calibration window is anchored to the
# brainstorm decision (2026-05-08). A re-run of the migration on a fresh
# DB therefore produces an identical row.
BP_SEASON_1_START: datetime = datetime(2026, 5, 8, 0, 0, 0, tzinfo=UTC)
BP_SEASON_1_END: datetime = datetime(2026, 8, 8, 0, 0, 0, tzinfo=UTC)
BP_SEASON_1_NUMBER: int = 1
BP_SEASON_1_NAME: str = "Saison 1"

# Curve anchors (single source of truth shared with the unit tests).
_FIRST_CAB: int = 50
_MID_PALIER: int = 15
_MID_CAB: int = 10000
_END_PALIER: int = 30
_END_CAB: int = 40000

# Hardcoded "waves" CAB-reward pattern — paliers 15 and 30 ship a gift
# card instead. Two segments with crests at palier 8 (250 CAB) and palier
# 22 (400 CAB — overall max). Total = 5 100 CAB redistributed across the
# 28 cab-reward paliers (segment 1 = 1 900 CAB, segment 2 = 3 200 CAB).
# Acted 2026-05-08 — replaces the original linear curve (≈40 000 CAB)
# deemed over-generous by product. All values are multiples of 50.
_CAB_REWARDS: dict[int, int] = {
    # Segment 1 — paliers 1-14 (sum = 1 900 CAB).
    1: 50,
    2: 50,
    3: 100,
    4: 150,
    5: 150,
    6: 200,
    7: 200,
    8: 250,  # crest segment 1
    9: 200,
    10: 200,
    11: 150,
    12: 100,
    13: 50,
    14: 50,
    # Palier 15 → gift card 5 € (skipped here).
    # Segment 2 — paliers 16-29 (sum = 3 200 CAB).
    16: 100,
    17: 150,
    18: 200,
    19: 250,
    20: 300,
    21: 350,
    22: 400,  # crest segment 2 (overall max)
    23: 350,
    24: 300,
    25: 250,
    26: 200,
    27: 150,
    28: 100,
    29: 100,
    # Palier 30 → gift card 20 € (skipped here).
}

# Gift-card palier rewards (in cents).
_GIFT_CARD_PALIERS: dict[int, int] = {
    15: 500,  # 5 €
    30: 2000,  # 20 €
}


def cab_required_for_palier(palier: int) -> int:
    """Return the cumulative CAB required to unlock ``palier``.

    Two-segment exponential curve anchored at (1, 50), (15, 10 000) and
    (30, 40 000). Each segment has its own ratio so the global shape is
    smooth-ish but the mid- and end-anchors land exactly on the agreed
    values regardless of float drift.
    """
    if palier < 1 or palier > _END_PALIER:
        raise ValueError(f"palier must be in [1, {_END_PALIER}], got {palier}")
    if palier == 1:
        return _FIRST_CAB
    if palier <= _MID_PALIER:
        ratio = (_MID_CAB / _FIRST_CAB) ** (1 / (_MID_PALIER - 1))
        return round(_FIRST_CAB * (ratio ** (palier - 1)))
    ratio = (_END_CAB / _MID_CAB) ** (1 / (_END_PALIER - _MID_PALIER))
    return round(_MID_CAB * (ratio ** (palier - _MID_PALIER)))


def cab_reward_for_palier(palier: int) -> int:
    """Return the CAB reward for the in-pass cab-reward paliers.

    Paliers 15 and 30 ship a gift card instead — calling code must NOT
    use the value returned here for those paliers (they're returned 0
    purely as a sentinel ; ``seed_bp_season_1`` skips them). The other
    28 paliers follow a hardcoded "waves" pattern with crests at palier
    8 (250 CAB, segment 1) and palier 22 (400 CAB, segment 2 — overall
    max). Total redistributed = 5 100 CAB.
    """
    if palier < 1 or palier > _END_PALIER:
        raise ValueError(f"palier must be in [1, {_END_PALIER}], got {palier}")
    if palier in (_MID_PALIER, _END_PALIER):
        return 0
    return _CAB_REWARDS[palier]


def seed_bp_season_1(db: Session) -> tuple[int, int]:
    """Insert (or refresh) the Saison 1 season + 30 milestones.

    Idempotent — the season uses ``ON CONFLICT (season_number) DO UPDATE``
    so a re-run keeps a single row and refreshes the timestamps + name +
    is_active flag. Milestones use ``ON CONFLICT (season_id, milestone_
    number) DO UPDATE`` so re-runs converge to the canonical curve even
    if anchors evolve.

    R33 — the partial unique index ``uq_one_active_season`` forbids two
    active rows simultaneously. If another season currently holds
    ``is_active=true`` (different ``season_number``), the seed flips it
    to false BEFORE upserting Saison 1. We never DELETE a season row
    (historical progress + claims FK-reference it).

    Returns ``(seasons_inserted_or_updated, milestones_inserted_or_updated)``
    — both are 1 + 30 in the canonical case.
    """
    # ------------------------------------------------------------------ #
    # 1. Ensure no other season holds is_active=true — the partial       #
    #    unique index uq_one_active_season would reject the upsert       #
    #    otherwise. We preserve historical rows by flipping their flag,  #
    #    never DELETE.                                                   #
    # ------------------------------------------------------------------ #
    db.execute(
        text("UPDATE battlepass_seasons SET is_active = FALSE WHERE is_active = TRUE AND season_number <> :num"),
        {"num": BP_SEASON_1_NUMBER},
    )

    # ------------------------------------------------------------------ #
    # 2. UPSERT Saison 1.                                                #
    # ------------------------------------------------------------------ #
    db.execute(
        text(
            "INSERT INTO battlepass_seasons "
            "  (id, season_number, name, started_at, ends_at, is_active) "
            "VALUES (gen_random_uuid(), :num, :name, "
            "        :started_at, :ends_at, TRUE) "
            "ON CONFLICT (season_number) DO UPDATE SET "
            "  name = EXCLUDED.name, "
            "  started_at = EXCLUDED.started_at, "
            "  ends_at = EXCLUDED.ends_at, "
            "  is_active = EXCLUDED.is_active"
        ),
        {
            "num": BP_SEASON_1_NUMBER,
            "name": BP_SEASON_1_NAME,
            "started_at": BP_SEASON_1_START,
            "ends_at": BP_SEASON_1_END,
        },
    )

    # Resolve the season id (whether we just inserted or updated).
    season_id = db.execute(
        text("SELECT id FROM battlepass_seasons WHERE season_number = :num"),
        {"num": BP_SEASON_1_NUMBER},
    ).scalar_one()

    # ------------------------------------------------------------------ #
    # 3. UPSERT the 30 milestones.                                       #
    # ------------------------------------------------------------------ #
    milestones_touched = 0
    for palier in range(1, _END_PALIER + 1):
        cab_required = cab_required_for_palier(palier)
        if palier in _GIFT_CARD_PALIERS:
            reward_type = "gift_card"
            reward_value = _GIFT_CARD_PALIERS[palier]
        else:
            reward_type = "cab"
            reward_value = cab_reward_for_palier(palier)
        # Saison 1 ships subscriber_only=false on every palier — the
        # subscriber-only mechanic exists at the schema level but isn't
        # used by the calibration product validated.
        db.execute(
            text(
                "INSERT INTO battlepass_milestones "
                "  (id, season_id, milestone_number, cab_required, "
                "   reward_type, reward_value, subscriber_only) "
                "VALUES (gen_random_uuid(), :sid, :num, :cab, "
                "        :rtype, :rval, FALSE) "
                "ON CONFLICT (season_id, milestone_number) DO UPDATE SET "
                "  cab_required = EXCLUDED.cab_required, "
                "  reward_type = EXCLUDED.reward_type, "
                "  reward_value = EXCLUDED.reward_value, "
                "  subscriber_only = EXCLUDED.subscriber_only"
            ),
            {
                "sid": season_id,
                "num": palier,
                "cab": cab_required,
                "rtype": reward_type,
                "rval": reward_value,
            },
        )
        milestones_touched += 1

    _log.info(
        "seeded BP Saison 1 : 1 season + %d milestones",
        milestones_touched,
    )
    return 1, milestones_touched
