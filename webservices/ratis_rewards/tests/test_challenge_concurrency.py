"""Concurrent challenge-claim race test — F-RW-gamif-3.

Audit finding : ``claim_milestone`` checks "not already claimed" with a
plain SELECT, then INSERTs into ``community_challenge_claims``. The
UNIQUE constraint ``uq_challenge_claims_milestone_user`` correctly stops
a concurrent double-claim from double-crediting CAB — but the raw
``IntegrityError`` it raises bubbles up as a 500 instead of the intended
409 ``milestone_already_claimed``.

The race window : two requests both run the SELECT-based check and both
see "not claimed" (each in its own READ COMMITTED snapshot, neither
seeing the other's uncommitted INSERT). Both then INSERT — one commit
wins, the loser hits the UNIQUE violation.

This is reproduced deterministically with two threads synchronised on a
barrier so both pass the SELECT check before either INSERTs.
"""

from __future__ import annotations

import json
import threading
import uuid

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from tests.conftest import TestingSessionLocal, make_user


def _open_session() -> Session:
    """Fresh DB session with its own connection."""
    return TestingSessionLocal()


def _setup_claimable_world() -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    """Insert user + active challenge (progress past threshold) + milestone.

    Returns ``(user_id, challenge_id, milestone_id)``.
    """
    setup = _open_session()
    try:
        user_id = make_user(setup)
        challenge_id = uuid.uuid4()
        setup.execute(
            text(
                "INSERT INTO community_challenges "
                "    (id, title, description, action_type, action_filter, "
                "     objective, starts_at, ends_at, grace_period_days, is_active) "
                "VALUES (:id, 'Race défi', 'desc', 'receipt_scan', NULL, "
                "        500, now() - interval '1 day', "
                "        now() + interval '7 days', 3, TRUE)"
            ),
            {"id": challenge_id},
        )
        setup.execute(
            text("INSERT INTO community_challenge_progress     (challenge_id, current_count) VALUES (:cid, 300)"),
            {"cid": challenge_id},
        )
        milestone_id = uuid.uuid4()
        setup.execute(
            text(
                "INSERT INTO community_challenge_milestones "
                "    (id, challenge_id, threshold, reward_type, reward_value, "
                "     label, sort_order) "
                "VALUES (:id, :cid, 100, 'cab', CAST(:rv AS jsonb), 'P1', 1)"
            ),
            {"id": milestone_id, "cid": challenge_id, "rv": json.dumps({"amount": 500})},
        )
        setup.commit()
    finally:
        setup.close()
    return user_id, challenge_id, milestone_id


def _cleanup_world(user_id, challenge_id) -> None:
    cleanup = _open_session()
    try:
        cleanup.execute(
            text("DELETE FROM community_challenge_claims WHERE challenge_id = :cid"),
            {"cid": challenge_id},
        )
        cleanup.execute(
            text("DELETE FROM community_challenge_milestones WHERE challenge_id = :cid"),
            {"cid": challenge_id},
        )
        cleanup.execute(
            text("DELETE FROM community_challenge_progress WHERE challenge_id = :cid"),
            {"cid": challenge_id},
        )
        cleanup.execute(
            text("DELETE FROM community_challenges WHERE id = :cid"),
            {"cid": challenge_id},
        )
        cleanup.execute(
            text("DELETE FROM cabecoin_transactions WHERE user_id = :uid"),
            {"uid": user_id},
        )
        cleanup.execute(
            text("DELETE FROM user_cab_balance WHERE user_id = :uid"),
            {"uid": user_id},
        )
        cleanup.execute(text("DELETE FROM users WHERE id = :uid"), {"uid": user_id})
        cleanup.commit()
    finally:
        cleanup.close()


class TestChallengeClaimRace:
    """Concurrent double-claim → loser raises MilestoneAlreadyClaimed (409),
    never a bare IntegrityError (500)."""

    def test_concurrent_double_claim_loser_gets_409_not_500(self):
        """Two threads claim the same milestone simultaneously.

        Both pass the SELECT check (barrier-synchronised), both INSERT.
        One wins ; the loser must raise the domain
        ``MilestoneAlreadyClaimed`` (→ 409) — a leaked ``IntegrityError``
        (→ 500) fails the test.
        """
        from repositories.challenge_repository import claim_milestone
        from repositories.exceptions import MilestoneAlreadyClaimed

        user_id, challenge_id, milestone_id = _setup_claimable_world()

        barrier = threading.Barrier(2)
        results: list[object] = [None, None]

        def _worker(idx: int) -> None:
            session = _open_session()
            try:
                # Force both sessions to take their snapshot and pass the
                # SELECT-based not-claimed check at (about) the same time.
                row = session.execute(
                    text("SELECT id FROM community_challenge_claims WHERE milestone_id = :mid AND user_id = :uid"),
                    {"mid": milestone_id, "uid": user_id},
                ).first()
                assert row is None  # both see "not claimed"
                barrier.wait(timeout=5)
                try:
                    claim_milestone(session, user_id, milestone_id)
                    session.commit()
                    results[idx] = "ok"
                except MilestoneAlreadyClaimed:
                    session.rollback()
                    results[idx] = "already_claimed"
                except IntegrityError as exc:
                    # The bug under test : a raw IntegrityError leaking
                    # out instead of MilestoneAlreadyClaimed. Captured so
                    # the main thread can assert on it.
                    session.rollback()
                    results[idx] = exc
            finally:
                session.close()

        try:
            t0 = threading.Thread(target=_worker, args=(0,))
            t1 = threading.Thread(target=_worker, args=(1,))
            t0.start()
            t1.start()
            t0.join(timeout=15)
            t1.join(timeout=15)

            for r in results:
                assert not isinstance(r, IntegrityError), (
                    "claim_milestone leaked a raw IntegrityError (→ 500) "
                    f"instead of MilestoneAlreadyClaimed (→ 409): {r!r}"
                )
            # Exactly one winner, one already-claimed loser.
            assert sorted(results) == ["already_claimed", "ok"]

            # Exactly one claim row persisted → CAB credited once.
            check = _open_session()
            try:
                count = check.execute(
                    text(
                        "SELECT COUNT(*) FROM community_challenge_claims WHERE milestone_id = :mid AND user_id = :uid"
                    ),
                    {"mid": milestone_id, "uid": user_id},
                ).scalar()
                assert count == 1
            finally:
                check.close()
        finally:
            _cleanup_world(user_id, challenge_id)
