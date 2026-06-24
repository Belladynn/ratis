"""
TDD tests for ratis_batch_mystery_announce.

Each step is tested in isolation using the session_factory SAVEPOINT fixture,
ensuring full rollback after each test.
"""

import uuid

from mystery_announce import (
    activate_next,
    announce_finds,
    freeze_and_reveal,
    reveal_clues,
)
from sqlalchemy import text

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_clue_revealed_at(session_factory, clue_id):
    with session_factory() as db:
        row = db.execute(
            text("SELECT revealed_at FROM mystery_challenge_clues WHERE id = :id"),
            {"id": clue_id},
        ).fetchone()
    return row[0] if row else None


def _get_find_announced_at(session_factory, find_id):
    with session_factory() as db:
        row = db.execute(
            text("SELECT announced_at FROM mystery_challenge_finds WHERE id = :id"),
            {"id": find_id},
        ).fetchone()
    return row[0] if row else None


def _get_challenge_status(session_factory, challenge_id):
    with session_factory() as db:
        row = db.execute(
            text("SELECT status FROM mystery_challenges WHERE id = :id"),
            {"id": challenge_id},
        ).fetchone()
    return row[0] if row else None


# ---------------------------------------------------------------------------
# TestRevealClues
# ---------------------------------------------------------------------------


class TestRevealClues:
    def test_reveals_clues_for_current_day(
        self, session_factory, make_product, make_mystery_challenge, make_mystery_clue
    ):
        """Day-1 clue should be revealed when challenge started 1 day ago."""
        ean = make_product()
        # Challenge started 1 day ago → day index = 1
        cid = make_mystery_challenge(ean, status="active", starts_at_offset_days=-1)
        clue_id = make_mystery_clue(cid, reveal_day=1)

        reveal_clues(session_factory, dry_run=False)

        assert _get_clue_revealed_at(session_factory, clue_id) is not None

    def test_does_not_reveal_future_clues(
        self, session_factory, make_product, make_mystery_challenge, make_mystery_clue
    ):
        """Day-2 clue must NOT be revealed on day 1 of the challenge."""
        ean = make_product()
        # Challenge started 0 days ago → day index = 1
        cid = make_mystery_challenge(ean, status="active", starts_at_offset_days=0)
        clue_id = make_mystery_clue(cid, reveal_day=2)

        reveal_clues(session_factory, dry_run=False)

        assert _get_clue_revealed_at(session_factory, clue_id) is None

    def test_idempotent_does_not_double_reveal(
        self, session_factory, make_product, make_mystery_challenge, make_mystery_clue
    ):
        """Running reveal_clues twice must not raise and must keep the original timestamp."""
        ean = make_product()
        cid = make_mystery_challenge(ean, status="active", starts_at_offset_days=-1)
        clue_id = make_mystery_clue(cid, reveal_day=1)

        reveal_clues(session_factory, dry_run=False)
        ts_first = _get_clue_revealed_at(session_factory, clue_id)

        # Second run: rowcount should be 0 (already revealed)
        count = reveal_clues(session_factory, dry_run=False)

        ts_second = _get_clue_revealed_at(session_factory, clue_id)
        assert count == 0
        assert ts_first == ts_second

    def test_dry_run_does_not_commit(self, session_factory, make_product, make_mystery_challenge, make_mystery_clue):
        """dry_run=True must not persist revealed_at."""
        ean = make_product()
        cid = make_mystery_challenge(ean, status="active", starts_at_offset_days=-1)
        clue_id = make_mystery_clue(cid, reveal_day=1)

        reveal_clues(session_factory, dry_run=True)

        assert _get_clue_revealed_at(session_factory, clue_id) is None

    def test_no_active_challenge_is_noop(self, session_factory):
        """With no active challenge the step must return 0 without error."""
        count = reveal_clues(session_factory, dry_run=False)
        assert count == 0


# ---------------------------------------------------------------------------
# TestAnnounceFinds
# ---------------------------------------------------------------------------


class TestAnnounceFinds:
    def test_announces_finds_before_midnight(
        self, session_factory, make_product, make_mystery_challenge, make_mystery_find, make_user
    ):
        """A find found 26 hours ago (before midnight) must be announced."""
        ean = make_product()
        cid = make_mystery_challenge(ean, status="active")
        uid = make_user()
        fid = make_mystery_find(cid, uid, found_at_offset_hours=-26)

        announce_finds(session_factory, dry_run=False)

        assert _get_find_announced_at(session_factory, fid) is not None

    def test_does_not_announce_recent_finds(
        self, session_factory, make_product, make_mystery_challenge, make_mystery_find, make_user
    ):
        """A find from 2 hours ago (still today UTC) must NOT be announced yet."""
        ean = make_product()
        cid = make_mystery_challenge(ean, status="active")
        uid = make_user()
        fid = make_mystery_find(cid, uid, found_at_offset_hours=-2)

        announce_finds(session_factory, dry_run=False)

        assert _get_find_announced_at(session_factory, fid) is None

    def test_idempotent(self, session_factory, make_product, make_mystery_challenge, make_mystery_find, make_user):
        """Running announce_finds twice keeps the original announced_at."""
        ean = make_product()
        cid = make_mystery_challenge(ean, status="active")
        uid = make_user()
        fid = make_mystery_find(cid, uid, found_at_offset_hours=-26)

        announce_finds(session_factory, dry_run=False)
        ts_first = _get_find_announced_at(session_factory, fid)

        count = announce_finds(session_factory, dry_run=False)

        ts_second = _get_find_announced_at(session_factory, fid)
        assert count == 0
        assert ts_first == ts_second

    def test_dry_run_does_not_commit(
        self, session_factory, make_product, make_mystery_challenge, make_mystery_find, make_user
    ):
        """dry_run=True must not persist announced_at."""
        ean = make_product()
        cid = make_mystery_challenge(ean, status="active")
        uid = make_user()
        fid = make_mystery_find(cid, uid, found_at_offset_hours=-26)

        announce_finds(session_factory, dry_run=True)

        assert _get_find_announced_at(session_factory, fid) is None


# ---------------------------------------------------------------------------
# TestActivateNext
# ---------------------------------------------------------------------------


class TestActivateNext:
    def test_activates_scheduled_when_no_active(self, session_factory, make_product, make_mystery_challenge):
        """A scheduled challenge with starts_at <= now() must be activated."""
        ean = make_product()
        cid = make_mystery_challenge(ean, status="scheduled", starts_at_offset_days=-1)

        activate_next(session_factory, dry_run=False)

        assert _get_challenge_status(session_factory, cid) == "active"

    def test_does_not_activate_if_already_active(self, session_factory, make_product, make_mystery_challenge):
        """If an active challenge already exists, the scheduled one must stay scheduled."""
        ean = make_product()
        # Already active
        make_mystery_challenge(ean, status="active", starts_at_offset_days=-2)
        ean2 = make_product()
        cid2 = make_mystery_challenge(ean2, status="scheduled", starts_at_offset_days=-1)

        activate_next(session_factory, dry_run=False)

        assert _get_challenge_status(session_factory, cid2) == "scheduled"

    def test_does_not_activate_future_challenge(self, session_factory, make_product, make_mystery_challenge):
        """A challenge with starts_at in the future must not be activated."""
        ean = make_product()
        cid = make_mystery_challenge(ean, status="scheduled", starts_at_offset_days=1)

        activate_next(session_factory, dry_run=False)

        assert _get_challenge_status(session_factory, cid) == "scheduled"

    def test_dry_run_does_not_commit(self, session_factory, make_product, make_mystery_challenge):
        """dry_run=True must not persist the status change."""
        ean = make_product()
        cid = make_mystery_challenge(ean, status="scheduled", starts_at_offset_days=-1)

        activate_next(session_factory, dry_run=True)

        assert _get_challenge_status(session_factory, cid) == "scheduled"


# ---------------------------------------------------------------------------
# TestFreezeAndReveal
# ---------------------------------------------------------------------------


class TestFreezeAndReveal:
    def test_freezes_active_past_ends_at(self, session_factory, make_product, make_mystery_challenge):
        """An active challenge past ends_at must transition to 'frozen'."""
        ean = make_product()
        # starts_at = -8 days, ends_at = -8 + 6 = -2 days (past)
        cid = make_mystery_challenge(
            ean,
            status="active",
            starts_at_offset_days=-8,
            ends_at_offset_days=-2,
        )

        freeze_and_reveal(session_factory, dry_run=False)

        assert _get_challenge_status(session_factory, cid) == "frozen"

    def test_reveals_frozen_challenge_next_day(self, session_factory, make_product):
        """A frozen challenge whose ends_at was more than 1 day ago must become 'revealed'."""
        ean = make_product()
        cid = uuid.uuid4()
        import json

        tiers = json.dumps([{"min_rank": 1, "max_rank": 1, "cab": 500}])
        with session_factory() as db:
            # Insert already-frozen challenge with ends_at 2 days ago
            db.execute(
                text(
                    "INSERT INTO mystery_challenges "
                    "  (id, product_ean, starts_at, ends_at, status, reward_tiers) "
                    "VALUES (:id, :ean, "
                    "  now() - interval '10 days', "
                    "  now() - interval '2 days', "
                    "  'frozen', CAST(:tiers AS jsonb))"
                ),
                {"id": cid, "ean": ean, "tiers": tiers},
            )
            db.commit()

        freeze_and_reveal(session_factory, dry_run=False)

        assert _get_challenge_status(session_factory, cid) == "revealed"

    def test_does_not_reveal_frozen_same_day(self, session_factory, make_product):
        """A frozen challenge whose ends_at was only 12 hours ago must stay 'frozen'."""
        ean = make_product()
        cid = uuid.uuid4()
        import json

        tiers = json.dumps([{"min_rank": 1, "max_rank": 1, "cab": 500}])
        with session_factory() as db:
            db.execute(
                text(
                    "INSERT INTO mystery_challenges "
                    "  (id, product_ean, starts_at, ends_at, status, reward_tiers) "
                    "VALUES (:id, :ean, "
                    "  now() - interval '10 days', "
                    "  now() - interval '12 hours', "
                    "  'frozen', CAST(:tiers AS jsonb))"
                ),
                {"id": cid, "ean": ean, "tiers": tiers},
            )
            db.commit()

        freeze_and_reveal(session_factory, dry_run=False)

        assert _get_challenge_status(session_factory, cid) == "frozen"

    def test_idempotent(self, session_factory, make_product):
        """Running freeze_and_reveal twice on an already-frozen challenge (12h ago) stays frozen."""
        ean = make_product()
        cid = uuid.uuid4()
        import json

        tiers = json.dumps([{"min_rank": 1, "max_rank": 1, "cab": 500}])
        with session_factory() as db:
            # ends_at = 12 hours ago → frozen but < 1 day → won't become revealed
            db.execute(
                text(
                    "INSERT INTO mystery_challenges "
                    "  (id, product_ean, starts_at, ends_at, status, reward_tiers) "
                    "VALUES (:id, :ean, "
                    "  now() - interval '10 days', "
                    "  now() - interval '12 hours', "
                    "  'frozen', CAST(:tiers AS jsonb))"
                ),
                {"id": cid, "ean": ean, "tiers": tiers},
            )
            db.commit()

        freeze_and_reveal(session_factory, dry_run=False)
        assert _get_challenge_status(session_factory, cid) == "frozen"

        freeze_and_reveal(session_factory, dry_run=False)
        assert _get_challenge_status(session_factory, cid) == "frozen"

    def test_dry_run_does_not_commit(self, session_factory, make_product, make_mystery_challenge):
        """dry_run=True must not persist the status change."""
        ean = make_product()
        cid = make_mystery_challenge(
            ean,
            status="active",
            starts_at_offset_days=-8,
            ends_at_offset_days=-2,
        )

        freeze_and_reveal(session_factory, dry_run=True)

        assert _get_challenge_status(session_factory, cid) == "active"
