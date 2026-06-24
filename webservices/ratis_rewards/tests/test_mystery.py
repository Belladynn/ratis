"""
Tests for mystery product feature — TDD.

All repository functions tested directly (no HTTP layer).
Route tests appended at the bottom of this file.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import text

from tests.conftest import make_mystery_challenge, make_price_consensus, make_product, make_scan, make_user

# ---------------------------------------------------------------------------
# Pure function tests
# ---------------------------------------------------------------------------


class TestResolveCabTier:
    def test_rank_1_returns_tier_1_cab(self):
        from repositories.mystery_repository import resolve_cab_tier

        tiers = [
            {"min_rank": 1, "max_rank": 1, "cab": 500},
            {"min_rank": 2, "max_rank": 10, "cab": 100},
            {"min_rank": 11, "max_rank": None, "cab": 10},
        ]
        assert resolve_cab_tier(tiers, 1) == 500

    def test_rank_5_returns_tier_top10(self):
        from repositories.mystery_repository import resolve_cab_tier

        tiers = [
            {"min_rank": 1, "max_rank": 1, "cab": 500},
            {"min_rank": 2, "max_rank": 10, "cab": 100},
            {"min_rank": 11, "max_rank": None, "cab": 10},
        ]
        assert resolve_cab_tier(tiers, 5) == 100

    def test_rank_1001_returns_participant_tier(self):
        from repositories.mystery_repository import resolve_cab_tier

        tiers = [
            {"min_rank": 1, "max_rank": 1, "cab": 500},
            {"min_rank": 2, "max_rank": 10, "cab": 100},
            {"min_rank": 11, "max_rank": None, "cab": 10},
        ]
        assert resolve_cab_tier(tiers, 1001) == 10

    def test_no_matching_tier_returns_0(self):
        from repositories.mystery_repository import resolve_cab_tier

        tiers = [
            {"min_rank": 1, "max_rank": 5, "cab": 100},
        ]
        assert resolve_cab_tier(tiers, 10) == 0


# ---------------------------------------------------------------------------
# draw_random_product
# ---------------------------------------------------------------------------


class TestDrawRandomProduct:
    def test_draws_eligible_product(self, db):
        from repositories.mystery_repository import draw_random_product

        ean = make_product(db)
        make_price_consensus(db, ean=ean)
        result = draw_random_product(db)
        assert result == ean

    def test_excludes_excluded_products(self, db):
        from repositories.mystery_repository import NoEligibleProduct, draw_random_product

        ean = make_product(db)
        make_price_consensus(db, ean=ean)
        # Add to exclusions
        db.execute(
            text(
                "INSERT INTO mystery_challenge_exclusions (product_ean, excluded_until) "
                "VALUES (:ean, now() + interval '30 days')"
            ),
            {"ean": ean},
        )
        db.flush()
        with pytest.raises(NoEligibleProduct):
            draw_random_product(db)

    def test_raises_no_eligible_product_when_empty(self, db):
        from repositories.mystery_repository import NoEligibleProduct, draw_random_product

        with pytest.raises(NoEligibleProduct):
            draw_random_product(db)


# ---------------------------------------------------------------------------
# create_mystery_challenge
# ---------------------------------------------------------------------------


class TestCreateMysteryChallenge:
    def _base_tiers(self):
        return [{"min_rank": 1, "max_rank": None, "cab": 10}]

    def _base_clues(self):
        return [{"reveal_day": 1, "clue_text": "Clue 1"}]

    def test_creates_with_manual_ean(self, db):
        from datetime import datetime

        from repositories.mystery_repository import create_mystery_challenge

        ean = make_product(db)
        starts_at = datetime(2027, 1, 1, tzinfo=UTC)
        challenge_id = create_mystery_challenge(
            db,
            starts_at=starts_at,
            product_ean=ean,
            reward_tiers=self._base_tiers(),
            clues=self._base_clues(),
        )
        assert isinstance(challenge_id, uuid.UUID)
        row = db.execute(
            text("SELECT id, product_ean, status FROM mystery_challenges WHERE id = :id"),
            {"id": challenge_id},
        ).first()
        assert row is not None
        assert str(row.product_ean) == ean
        assert row.status == "scheduled"

    def test_creates_with_auto_draw(self, db):
        from datetime import datetime

        from repositories.mystery_repository import create_mystery_challenge

        ean = make_product(db)
        make_price_consensus(db, ean=ean)
        starts_at = datetime(2027, 2, 1, tzinfo=UTC)
        challenge_id = create_mystery_challenge(
            db,
            starts_at=starts_at,
            product_ean=None,
            reward_tiers=self._base_tiers(),
            clues=self._base_clues(),
        )
        assert isinstance(challenge_id, uuid.UUID)
        row = db.execute(
            text("SELECT product_ean FROM mystery_challenges WHERE id = :id"),
            {"id": challenge_id},
        ).first()
        assert row.product_ean == ean

    def test_raises_overlap_if_conflict(self, db):
        from datetime import datetime

        from repositories.mystery_repository import ChallengeOverlap, create_mystery_challenge

        ean = make_product(db)
        starts_at = datetime(2027, 3, 1, tzinfo=UTC)
        create_mystery_challenge(
            db,
            starts_at=starts_at,
            product_ean=ean,
            reward_tiers=self._base_tiers(),
            clues=self._base_clues(),
        )
        # Second challenge overlapping the same window
        ean2 = make_product(db)
        # starts 3 days into first challenge
        starts_at2 = datetime(2027, 3, 4, tzinfo=UTC)
        with pytest.raises(ChallengeOverlap):
            create_mystery_challenge(
                db,
                starts_at=starts_at2,
                product_ean=ean2,
                reward_tiers=self._base_tiers(),
                clues=self._base_clues(),
            )

    def test_clues_inserted(self, db):
        from datetime import datetime

        from repositories.mystery_repository import create_mystery_challenge

        ean = make_product(db)
        starts_at = datetime(2027, 4, 1, tzinfo=UTC)
        clues = [
            {"reveal_day": 1, "clue_text": "First hint"},
            {"reveal_day": 2, "clue_text": "Second hint"},
        ]
        challenge_id = create_mystery_challenge(
            db,
            starts_at=starts_at,
            product_ean=ean,
            reward_tiers=self._base_tiers(),
            clues=clues,
        )
        count = db.execute(
            text("SELECT COUNT(*) FROM mystery_challenge_clues WHERE challenge_id = :id"),
            {"id": challenge_id},
        ).scalar()
        assert count == 2

    def test_exclusion_updated(self, db):
        from datetime import datetime

        from repositories.mystery_repository import create_mystery_challenge

        ean = make_product(db)
        starts_at = datetime(2027, 5, 1, tzinfo=UTC)
        create_mystery_challenge(
            db,
            starts_at=starts_at,
            product_ean=ean,
            reward_tiers=self._base_tiers(),
            clues=self._base_clues(),
        )
        row = db.execute(
            text("SELECT product_ean FROM mystery_challenge_exclusions WHERE product_ean = :ean"),
            {"ean": ean},
        ).first()
        assert row is not None


# ---------------------------------------------------------------------------
# check_mystery_find
# ---------------------------------------------------------------------------


class TestCheckMysteryFind:
    def test_no_active_challenge_returns_none(self, db):
        from repositories.mystery_repository import check_mystery_find

        user_id = make_user(db)
        ean = make_product(db)
        scan_id = make_scan(db, user_id=user_id, product_ean=ean)
        result = check_mystery_find(db, user_id, scan_id)
        assert result is None

    def test_wrong_ean_returns_none(self, db):
        from repositories.mystery_repository import check_mystery_find

        user_id = make_user(db)
        ean_mystery = make_product(db)
        ean_other = make_product(db)
        make_mystery_challenge(db, product_ean=ean_mystery)
        scan_id = make_scan(db, user_id=user_id, product_ean=ean_other)
        result = check_mystery_find(db, user_id, scan_id)
        assert result is None

    def test_already_found_returns_none(self, db):
        from repositories.mystery_repository import check_mystery_find

        user_id = make_user(db)
        ean = make_product(db)
        make_mystery_challenge(db, product_ean=ean)
        scan_id = make_scan(db, user_id=user_id, product_ean=ean)
        # First find
        result1 = check_mystery_find(db, user_id, scan_id)
        assert result1 is not None
        # Second call — already found
        scan_id2 = make_scan(db, user_id=user_id, product_ean=ean)
        result2 = check_mystery_find(db, user_id, scan_id2)
        assert result2 is None

    def test_first_find_returns_rank_1(self, db):
        from repositories.mystery_repository import check_mystery_find

        user_id = make_user(db)
        ean = make_product(db)
        make_mystery_challenge(db, product_ean=ean)
        scan_id = make_scan(db, user_id=user_id, product_ean=ean)
        result = check_mystery_find(db, user_id, scan_id)
        assert result is not None
        assert result["rank"] == 1

    def test_second_find_returns_rank_2(self, db):
        from repositories.mystery_repository import check_mystery_find

        user_id1 = make_user(db)
        user_id2 = make_user(db)
        ean = make_product(db)
        make_mystery_challenge(db, product_ean=ean)
        scan_id1 = make_scan(db, user_id=user_id1, product_ean=ean)
        scan_id2 = make_scan(db, user_id=user_id2, product_ean=ean)
        r1 = check_mystery_find(db, user_id1, scan_id1)
        r2 = check_mystery_find(db, user_id2, scan_id2)
        assert r1["rank"] == 1
        assert r2["rank"] == 2

    def test_rank_atomique_unique_constraint(self, db):
        """Same user cannot find the same challenge twice."""
        from repositories.mystery_repository import check_mystery_find

        user_id = make_user(db)
        ean = make_product(db)
        make_mystery_challenge(db, product_ean=ean)
        scan_id1 = make_scan(db, user_id=user_id, product_ean=ean)
        scan_id2 = make_scan(db, user_id=user_id, product_ean=ean)
        r1 = check_mystery_find(db, user_id, scan_id1)
        assert r1 is not None
        r2 = check_mystery_find(db, user_id, scan_id2)
        assert r2 is None


# ---------------------------------------------------------------------------
# process_mystery_find (service)
# ---------------------------------------------------------------------------


class TestProcessMysteryFind:
    def test_awards_cab_on_find(self, db):
        from repositories.cab_repository import get_balance
        from services.mystery_service import process_mystery_find

        user_id = make_user(db)
        ean = make_product(db)
        make_mystery_challenge(db, product_ean=ean)
        scan_id = make_scan(db, user_id=user_id, product_ean=ean)
        before = get_balance(db, user_id)
        process_mystery_find(db, user_id, scan_id)
        after = get_balance(db, user_id)
        # rank=1 → cab=500 (from make_mystery_challenge tiers); streak mult may apply
        assert after > before

    def test_no_op_no_active_challenge(self, db):
        from repositories.cab_repository import get_balance
        from services.mystery_service import process_mystery_find

        user_id = make_user(db)
        ean = make_product(db)
        scan_id = make_scan(db, user_id=user_id, product_ean=ean)
        before = get_balance(db, user_id)
        process_mystery_find(db, user_id, scan_id)
        after = get_balance(db, user_id)
        assert after == before

    def test_enqueues_notification(self, db):
        from services.mystery_service import process_mystery_find

        user_id = make_user(db)
        ean = make_product(db)
        make_mystery_challenge(db, product_ean=ean)
        scan_id = make_scan(db, user_id=user_id, product_ean=ean)
        process_mystery_find(db, user_id, scan_id)
        count = db.execute(
            text("SELECT COUNT(*) FROM notification_outbox WHERE user_id = :uid AND type = 'mystery_product_found'"),
            {"uid": user_id},
        ).scalar()
        assert count == 1


# ---------------------------------------------------------------------------
# HTTP route helpers
# ---------------------------------------------------------------------------


def _insert_mystery_find(
    db,
    challenge_id: uuid.UUID,
    user_id: uuid.UUID,
    *,
    rank: int = 1,
    cab_awarded: int = 500,
    announced: bool = False,
    product_ean: str | None = None,
) -> None:
    """Insert a mystery_challenge_finds row directly. Creates a scan row for FK."""
    find_id = uuid.uuid4()
    announced_at = "now()" if announced else "NULL"
    # Create a real scan row to satisfy FK constraint
    scan_id = make_scan(db, user_id=user_id, product_ean=product_ean)
    db.execute(
        text(
            f"INSERT INTO mystery_challenge_finds "
            f"    (id, challenge_id, user_id, scan_id, rank, cab_awarded, found_at, announced_at) "
            f"VALUES (:id, :cid, :uid, :sid, :rank, :cab, now(), {announced_at})"
        ),
        {
            "id": find_id,
            "cid": challenge_id,
            "uid": user_id,
            "sid": scan_id,
            "rank": rank,
            "cab": cab_awarded,
        },
    )
    db.commit()


def _insert_clue(
    db,
    challenge_id: uuid.UUID,
    *,
    reveal_day: int = 1,
    clue_text: str = "A clue",
    revealed: bool = True,
) -> None:
    """Insert a mystery_challenge_clues row directly."""
    revealed_at = "now()" if revealed else "NULL"
    db.execute(
        text(
            f"INSERT INTO mystery_challenge_clues "
            f"    (id, challenge_id, reveal_day, clue_text, revealed_at) "
            f"VALUES (:id, :cid, :day, :text, {revealed_at})"
        ),
        {
            "id": uuid.uuid4(),
            "cid": challenge_id,
            "day": reveal_day,
            "text": clue_text,
        },
    )
    db.commit()


# ---------------------------------------------------------------------------
# GET /api/v1/gamification/mystery
# ---------------------------------------------------------------------------


class TestGetMysteryEndpoint:
    def test_returns_active_challenge(self, user_client, db):
        client, set_user = user_client
        uid = make_user(db)
        set_user(uid)

        ean = make_product(db)
        cid = make_mystery_challenge(db, product_ean=ean, status="active")
        _insert_clue(db, cid, reveal_day=1, clue_text="Hint 1", revealed=True)

        resp = client.get("/api/v1/gamification/mystery")
        assert resp.status_code == 200
        body = resp.json()

        assert body["id"] == str(cid)
        assert body["status"] == "active"
        assert body["starts_at"] is not None
        assert body["ends_at"] is not None
        assert isinstance(body["clues"], list)
        assert isinstance(body["reward_tiers"], list)
        assert "announced_winner" in body
        assert "user_find" in body

    def test_returns_404_when_no_active(self, user_client, db):
        client, set_user = user_client
        uid = make_user(db)
        set_user(uid)

        resp = client.get("/api/v1/gamification/mystery")
        assert resp.status_code == 404
        assert resp.json()["detail"] == "mystery_not_found"

    def test_clues_not_leaked_before_reveal(self, user_client, db):
        """Unrevealed clues must NOT appear in the user-facing endpoint."""
        client, set_user = user_client
        uid = make_user(db)
        set_user(uid)

        ean = make_product(db)
        cid = make_mystery_challenge(db, product_ean=ean, status="active")
        _insert_clue(db, cid, reveal_day=1, clue_text="Revealed", revealed=True)
        _insert_clue(db, cid, reveal_day=2, clue_text="Secret", revealed=False)

        resp = client.get("/api/v1/gamification/mystery")
        assert resp.status_code == 200
        clues = resp.json()["clues"]
        # Only revealed clue should be present
        assert len(clues) == 1
        assert clues[0]["clue_text"] == "Revealed"

    def test_product_ean_not_leaked_when_active(self, user_client, db):
        """product_ean must NOT appear in the response when status is not 'revealed'."""
        client, set_user = user_client
        uid = make_user(db)
        set_user(uid)

        ean = make_product(db)
        make_mystery_challenge(db, product_ean=ean, status="active")

        resp = client.get("/api/v1/gamification/mystery")
        assert resp.status_code == 200
        assert "product_ean" not in resp.json()

    def test_user_find_included_when_found(self, user_client, db):
        """When the user has found the mystery, user_find must be non-null."""
        client, set_user = user_client
        uid = make_user(db)
        set_user(uid)

        ean = make_product(db)
        cid = make_mystery_challenge(db, product_ean=ean, status="active")
        _insert_mystery_find(db, cid, uid, rank=1, cab_awarded=500)

        resp = client.get("/api/v1/gamification/mystery")
        assert resp.status_code == 200
        uf = resp.json()["user_find"]
        assert uf is not None
        assert uf["rank"] == 1
        assert uf["cab_awarded"] == 500


# ---------------------------------------------------------------------------
# GET /api/v1/gamification/mystery/leaderboard
# ---------------------------------------------------------------------------


class TestGetLeaderboard:
    def test_returns_announced_finds_only(self, user_client, db):
        """Only announced finds appear in the leaderboard."""
        client, set_user = user_client
        uid = make_user(db)
        set_user(uid)

        uid2 = make_user(db)
        ean = make_product(db)
        cid = make_mystery_challenge(db, product_ean=ean, status="active")
        _insert_mystery_find(db, cid, uid, rank=1, cab_awarded=500, announced=True)
        _insert_mystery_find(db, cid, uid2, rank=2, cab_awarded=100, announced=False)

        resp = client.get("/api/v1/gamification/mystery/leaderboard")
        assert resp.status_code == 200
        body = resp.json()

        assert body["challenge_id"] == str(cid)
        finds = body["finds"]
        assert len(finds) == 1
        assert finds[0]["rank"] == 1

    def test_user_rank_is_null_if_not_found(self, user_client, db):
        """user_rank is null when the authenticated user has not found the mystery."""
        client, set_user = user_client
        uid = make_user(db)
        set_user(uid)

        ean = make_product(db)
        make_mystery_challenge(db, product_ean=ean, status="active")

        resp = client.get("/api/v1/gamification/mystery/leaderboard")
        assert resp.status_code == 200
        assert resp.json()["user_rank"] is None


# ---------------------------------------------------------------------------
# Admin mystery routes
# ---------------------------------------------------------------------------


class TestAdminMystery:
    def test_create_mystery_with_manual_ean(self, admin_client, db):
        ean = make_product(db)
        payload = {
            "starts_at": "2027-06-01T00:00:00Z",
            "product_ean": ean,
            "reward_tiers": [{"min_rank": 1, "max_rank": None, "cab": 10}],
            "clues": [{"reveal_day": 1, "clue_text": "Clue 1"}],
        }
        resp = admin_client.post("/api/v1/admin/mystery", json=payload)
        assert resp.status_code == 200
        body = resp.json()
        assert "id" in body
        # verify challenge was persisted
        row = db.execute(
            text("SELECT product_ean FROM mystery_challenges WHERE id = :id"),
            {"id": uuid.UUID(body["id"])},
        ).first()
        assert row is not None
        assert row.product_ean == ean

    def test_create_mystery_auto_draw(self, admin_client, db):
        ean = make_product(db)
        make_price_consensus(db, ean=ean)
        payload = {
            "starts_at": "2027-07-01T00:00:00Z",
            "reward_tiers": [{"min_rank": 1, "max_rank": None, "cab": 10}],
            "clues": [{"reveal_day": 1, "clue_text": "Clue 1"}],
        }
        resp = admin_client.post("/api/v1/admin/mystery", json=payload)
        assert resp.status_code == 200
        body = resp.json()
        assert "id" in body

    def test_create_returns_409_on_overlap(self, admin_client, db):
        ean = make_product(db)
        make_mystery_challenge(db, product_ean=ean, status="scheduled")
        # Try to create another that overlaps
        ean2 = make_product(db)
        payload = {
            "starts_at": datetime.now(UTC).isoformat(),
            "product_ean": ean2,
            "reward_tiers": [{"min_rank": 1, "max_rank": None, "cab": 10}],
            "clues": [{"reveal_day": 1, "clue_text": "Clue"}],
        }
        resp = admin_client.post("/api/v1/admin/mystery", json=payload)
        assert resp.status_code == 409
        assert resp.json()["detail"] == "challenge_overlap"

    def test_draw_returns_eligible_product(self, admin_client, db):
        ean = make_product(db, name="Mystery Product")
        make_price_consensus(db, ean=ean)
        db.commit()

        resp = admin_client.get("/api/v1/admin/mystery/draw")
        assert resp.status_code == 200
        body = resp.json()
        assert body["ean"] == ean
        assert body["name"] == "Mystery Product"

    def test_patch_updates_scheduled_challenge(self, admin_client, db):
        ean = make_product(db)
        cid = make_mystery_challenge(db, product_ean=ean, status="scheduled")
        ean2 = make_product(db)
        payload = {"product_ean": ean2}

        resp = admin_client.patch(f"/api/v1/admin/mystery/{cid}", json=payload)
        assert resp.status_code == 200
        assert resp.json()["id"] == str(cid)

        row = db.execute(
            text("SELECT product_ean FROM mystery_challenges WHERE id = :id"),
            {"id": cid},
        ).first()
        assert row.product_ean == ean2

    def test_patch_returns_409_if_active(self, admin_client, db):
        ean = make_product(db)
        cid = make_mystery_challenge(db, product_ean=ean, status="active")
        payload = {"product_ean": make_product(db)}

        resp = admin_client.patch(f"/api/v1/admin/mystery/{cid}", json=payload)
        assert resp.status_code == 409
        assert resp.json()["detail"] == "challenge_not_modifiable"

    def test_delete_scheduled_challenge(self, admin_client, db):
        ean = make_product(db)
        cid = make_mystery_challenge(db, product_ean=ean, status="scheduled")

        resp = admin_client.delete(f"/api/v1/admin/mystery/{cid}")
        assert resp.status_code == 200
        assert resp.json()["deleted"] is True

        row = db.execute(
            text("SELECT id FROM mystery_challenges WHERE id = :id"),
            {"id": cid},
        ).first()
        assert row is None

    def test_requires_admin_key(self, raw_client, db):
        resp = raw_client.get("/api/v1/admin/mystery")
        assert resp.status_code == 403
