"""
Tests for missions bloc: GET /gamification/missions, POST /rewards/missions/{id}/claim."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta

from repositories.cab_repository import get_balance
from sqlalchemy import text

from tests.conftest import make_mission, make_user

TODAY = datetime.now(UTC).date()
WEEK_START = TODAY - timedelta(days=TODAY.weekday())


def _insert_user_mission(
    db,
    user_id: uuid.UUID,
    mission_id: uuid.UUID,
    period_start: date,
    *,
    current_count: int = 0,
    status: str = "pending",
) -> uuid.UUID:
    """Insert a user_mission row, copying target_count and cab_reward from the catalogue.

    Updated to copy cab_reward / target_count from missions — claim_mission now reads
    from user_missions.cab_reward (per-user value, supports post-boost overrides).
    """
    um_id = uuid.uuid4()
    db.execute(
        text(
            "INSERT INTO user_missions "
            "    (id, user_id, mission_id, period_start, current_count, status, "
            "     target_count, cab_reward, xp_reward) "
            "SELECT :id, :uid, :mid, :period, :count, :status, "
            "       m.target_count, m.cab_reward, 0 "
            "FROM missions m WHERE m.id = :mid"
        ),
        {
            "id": um_id,
            "uid": user_id,
            "mid": mission_id,
            "period": period_start,
            "count": current_count,
            "status": status,
        },
    )
    db.flush()
    return um_id


# ===========================================================================
# GET /gamification/missions# ===========================================================================


class TestGetMissions:
    def test_returns_empty_sections_when_no_catalogue(self, db, user_client):
        client, set_user = user_client
        uid = make_user(db)
        set_user(uid)
        resp = client.get("/api/v1/gamification/missions")
        assert resp.status_code == 200
        data = resp.json()
        assert data["daily"]["missions"] == []
        assert data["weekly"]["missions"] == []

    def test_lazy_generate_daily_missions_from_catalogue(self, db, user_client):
        client, set_user = user_client
        uid = make_user(db)
        set_user(uid)
        make_mission(db, action_type="receipt_scan", frequency="daily", difficulty="easy")
        make_mission(db, action_type="label_scan", frequency="daily", difficulty="medium")
        make_mission(db, action_type="barcode_scan", frequency="daily", difficulty="hard")
        resp = client.get("/api/v1/gamification/missions")
        assert resp.status_code == 200
        assert len(resp.json()["daily"]["missions"]) == 3

    def test_lazy_generate_weekly_missions_from_catalogue(self, db, user_client):
        client, set_user = user_client
        uid = make_user(db)
        set_user(uid)
        make_mission(db, action_type="receipt_scan", frequency="weekly", difficulty="easy")
        resp = client.get("/api/v1/gamification/missions")
        assert resp.status_code == 200
        assert len(resp.json()["weekly"]["missions"]) == 1

    def test_daily_date_matches_today(self, db, user_client):
        client, set_user = user_client
        uid = make_user(db)
        set_user(uid)
        make_mission(db, action_type="receipt_scan", frequency="daily", difficulty="easy")
        resp = client.get("/api/v1/gamification/missions")
        assert resp.json()["daily"]["date"] == TODAY.isoformat()

    def test_weekly_week_start_is_monday(self, db, user_client):
        client, set_user = user_client
        uid = make_user(db)
        set_user(uid)
        make_mission(db, action_type="receipt_scan", frequency="weekly", difficulty="easy")
        resp = client.get("/api/v1/gamification/missions")
        assert resp.json()["weekly"]["week_start"] == WEEK_START.isoformat()

    def test_no_duplicate_action_type_across_difficulties(self, db, user_client):
        client, set_user = user_client
        uid = make_user(db)
        set_user(uid)
        # easy and medium share the same action_type — medium must be skipped
        make_mission(db, action_type="receipt_scan", frequency="daily", difficulty="easy")
        make_mission(db, action_type="receipt_scan", frequency="daily", difficulty="medium")
        make_mission(db, action_type="barcode_scan", frequency="daily", difficulty="hard")
        resp = client.get("/api/v1/gamification/missions")
        missions = resp.json()["daily"]["missions"]
        action_types = [m["action_type"] for m in missions]
        assert len(action_types) == len(set(action_types))
        assert len(missions) == 2  # easy + hard; medium skipped

    def test_existing_progress_returned_without_regenerating(self, db, user_client):
        client, set_user = user_client
        uid = make_user(db)
        set_user(uid)
        mid = make_mission(
            db,
            action_type="receipt_scan",
            frequency="daily",
            difficulty="easy",
            target_count=3,
            cab_reward=50,
        )
        _insert_user_mission(db, uid, mid, TODAY, current_count=2, status="pending")
        resp = client.get("/api/v1/gamification/missions")
        missions = resp.json()["daily"]["missions"]
        assert len(missions) == 1
        assert missions[0]["current_count"] == 2
        assert missions[0]["status"] == "pending"

    def test_mission_response_fields(self, db, user_client):
        client, set_user = user_client
        uid = make_user(db)
        set_user(uid)
        make_mission(
            db,
            action_type="receipt_scan",
            frequency="daily",
            difficulty="easy",
            target_count=2,
            cab_reward=75,
        )
        resp = client.get("/api/v1/gamification/missions")
        m = resp.json()["daily"]["missions"][0]
        assert "id" in m
        assert m["action_type"] == "receipt_scan"
        assert m["difficulty"] == "easy"
        assert m["target_count"] == 2
        assert m["current_count"] == 0
        assert m["cab_reward"] == 75
        assert m["status"] == "pending"
        assert "xp_reward" in m

    def test_get_missions_includes_buffer_burst_fields(self, db, user_client):
        """Each mission row must expose the 7 Buffer + Burst fields with
        correct defaults so the FE (PR #343) can render the UI.

        Catalogue fields :
            - frequency  ('daily' | 'weekly')
            - is_boostable (bool)

        Per-user state (defaults from schema) :
            - buffer_count (int, default 0)
            - burst_count (int, default 0)
            - burst_locked (bool, default False)
            - period_extended_until (timestamptz | None)
            - portions_claimed (int, default 0)
        """
        client, set_user = user_client
        uid = make_user(db)
        set_user(uid)
        make_mission(
            db,
            action_type="receipt_scan",
            frequency="daily",
            difficulty="easy",
            target_count=3,
            cab_reward=50,
        )
        make_mission(
            db,
            action_type="label_scan",
            frequency="weekly",
            difficulty="easy",
            target_count=5,
            cab_reward=120,
        )
        resp = client.get("/api/v1/gamification/missions")
        assert resp.status_code == 200
        data = resp.json()

        for section in ("daily", "weekly"):
            assert len(data[section]["missions"]) == 1
            m = data[section]["missions"][0]
            # Catalogue fields
            assert m["frequency"] == ("daily" if section == "daily" else "weekly")
            assert m["is_boostable"] is True  # schema default
            # Per-user state defaults
            assert m["buffer_count"] == 0
            assert m["burst_count"] == 0
            assert m["burst_locked"] is False
            assert m["period_extended_until"] is None
            assert m["portions_claimed"] == 0

    def test_get_missions_includes_buffer_state_after_apply_buffer(self, db, user_client):
        """After apply_buffer on a daily mission, GET /missions must surface
        the updated state : buffer_count=1, target doubled, period_extended_until SET.
        """
        from services.missions_service import apply_buffer

        client, set_user = user_client
        uid = make_user(db)
        set_user(uid)
        mid = make_mission(
            db,
            action_type="receipt_scan",
            frequency="daily",
            difficulty="easy",
            target_count=3,
            cab_reward=50,
        )
        um_id = _insert_user_mission(db, uid, mid, TODAY, current_count=0)
        apply_buffer(db, uid, um_id)
        db.commit()

        resp = client.get("/api/v1/gamification/missions")
        assert resp.status_code == 200
        m = resp.json()["daily"]["missions"][0]
        assert m["buffer_count"] == 1
        assert m["target_count"] == 6  # doubled from 3
        assert m["period_extended_until"] is not None
        # Should serialise as ISO 8601 string
        assert isinstance(m["period_extended_until"], str)
        assert m["burst_locked"] is False

    def test_get_missions_includes_burst_state_after_burst_claim(self, db, user_client):
        """After a Burst claim (current_count >= target × 2), GET /missions
        must reflect burst_count > 0 AND burst_locked=True (lock irreversible).
        """
        from services.burst_service import claim_burst

        client, set_user = user_client
        uid = make_user(db)
        set_user(uid)
        mid = make_mission(
            db,
            action_type="receipt_scan",
            frequency="daily",
            difficulty="easy",
            target_count=3,
            cab_reward=50,
        )
        # current_count = target × 2 → palier 1 unlocked
        um_id = _insert_user_mission(
            db,
            uid,
            mid,
            TODAY,
            current_count=6,
            status="completed",
        )
        # _insert_user_mission seeds xp_reward=0; bump to a non-zero value so
        # the claim is meaningful (palier 1 awards xp_reward × 2^1 - 2^0 = xp).
        db.execute(
            text("UPDATE user_missions SET xp_reward = 10 WHERE id = :id"),
            {"id": um_id},
        )
        db.flush()
        claim_burst(db, uid, um_id)
        db.commit()

        resp = client.get("/api/v1/gamification/missions")
        assert resp.status_code == 200
        m = resp.json()["daily"]["missions"][0]
        assert m["burst_count"] >= 1
        assert m["burst_locked"] is True


# ===========================================================================
# POST /gamification/missions/{id}/claim# ===========================================================================


class TestClaimMission:
    """Tests for claim mission endpoint. Updated 2026-05-09 to match the
    Buffer + Burst refonte API contract :

    - response keys ``claimed``/``cab_reward``/``new_cab_balance`` →
      ``cab_awarded``/``portions_claimed_total``/``mission_status``/
      ``new_cab_balance``
    - 409 ``mission_already_claimed`` → 409 ``already_claimed``
    - 409 ``mission_not_completed`` → 402 ``no_portion_available_now``
      (= insufficient progress)

    Spec : ``docs/superpowers/specs/2026-05-09-buffer-burst-design.md``.
    """

    def test_claim_completed_mission(self, db, user_client):
        client, set_user = user_client
        uid = make_user(db)
        set_user(uid)
        mid = make_mission(
            db,
            action_type="receipt_scan",
            frequency="daily",
            difficulty="easy",
            target_count=1,
            cab_reward=50,
        )
        um_id = _insert_user_mission(db, uid, mid, TODAY, current_count=1, status="completed")
        resp = client.post(f"/api/v1/gamification/missions/{um_id}/claim")
        assert resp.status_code == 200
        data = resp.json()
        assert data["cab_awarded"] == 50
        assert data["portions_claimed_total"] == 1
        assert data["mission_status"] == "claimed"
        assert data["new_cab_balance"] == 50

    def test_claim_sets_status_to_claimed(self, db, user_client):
        client, set_user = user_client
        uid = make_user(db)
        set_user(uid)
        mid = make_mission(
            db,
            action_type="receipt_scan",
            frequency="daily",
            difficulty="easy",
            target_count=1,
            cab_reward=50,
        )
        um_id = _insert_user_mission(db, uid, mid, TODAY, current_count=1, status="completed")
        client.post(f"/api/v1/gamification/missions/{um_id}/claim")
        row = db.execute(
            text("SELECT status FROM user_missions WHERE id = :id"),
            {"id": um_id},
        ).first()
        assert row.status == "claimed"

    def test_claim_awards_cab(self, db, user_client):
        client, set_user = user_client
        uid = make_user(db)
        set_user(uid)
        mid = make_mission(
            db,
            action_type="receipt_scan",
            frequency="daily",
            difficulty="easy",
            target_count=1,
            cab_reward=80,
        )
        um_id = _insert_user_mission(db, uid, mid, TODAY, current_count=1, status="completed")
        client.post(f"/api/v1/gamification/missions/{um_id}/claim")
        assert get_balance(db, uid) == 80

    def test_double_claim_returns_409(self, db, user_client):
        client, set_user = user_client
        uid = make_user(db)
        set_user(uid)
        mid = make_mission(
            db,
            action_type="receipt_scan",
            frequency="daily",
            difficulty="easy",
            target_count=1,
            cab_reward=50,
        )
        um_id = _insert_user_mission(db, uid, mid, TODAY, current_count=1, status="claimed")
        resp = client.post(f"/api/v1/gamification/missions/{um_id}/claim")
        assert resp.status_code == 409
        # Detail rename : 'mission_already_claimed' → 'already_claimed'
        # (Buffer/Burst refonte 2026-05-09).
        assert resp.json()["detail"] == "already_claimed"

    def test_claim_pending_returns_402(self, db, user_client):
        """Renamed from test_claim_pending_returns_409.

        Pre-refonte : pending+incomplete returned 409 mission_not_completed.
        Post-refonte (multi-claim cumulatif + double gating) : the same
        scenario returns 402 ``no_portion_available_now`` because no
        portion is currently claimable. Same UX for the user (= can't
        claim yet), different status code reflecting the new mechanic.
        """
        client, set_user = user_client
        uid = make_user(db)
        set_user(uid)
        mid = make_mission(
            db,
            action_type="receipt_scan",
            frequency="daily",
            difficulty="easy",
            target_count=3,
            cab_reward=50,
        )
        um_id = _insert_user_mission(db, uid, mid, TODAY, current_count=1, status="pending")
        resp = client.post(f"/api/v1/gamification/missions/{um_id}/claim")
        assert resp.status_code == 402
        assert resp.json()["detail"] == "no_portion_available_now"

    def test_claim_other_user_mission_returns_404(self, db, user_client):
        client, set_user = user_client
        uid = make_user(db)
        other_uid = make_user(db)
        set_user(uid)
        mid = make_mission(
            db,
            action_type="receipt_scan",
            frequency="daily",
            difficulty="easy",
            target_count=1,
            cab_reward=50,
        )
        um_id = _insert_user_mission(db, other_uid, mid, TODAY, current_count=1, status="completed")
        resp = client.post(f"/api/v1/gamification/missions/{um_id}/claim")
        assert resp.status_code == 404
        assert resp.json()["detail"] == "mission_not_found"

    def test_claim_unknown_id_returns_404(self, db, user_client):
        client, set_user = user_client
        uid = make_user(db)
        set_user(uid)
        resp = client.post(f"/api/v1/gamification/missions/{uuid.uuid4()}/claim")
        assert resp.status_code == 404
        assert resp.json()["detail"] == "mission_not_found"


# ===========================================================================
# RW-06 — XP of buffered missions is awarded at the FIRST claim
# ===========================================================================


def _xp_balance(db, user_id: uuid.UUID) -> int:
    row = db.execute(
        text("SELECT balance FROM user_xp_balance WHERE user_id = :uid"),
        {"uid": user_id},
    ).first()
    return int(row.balance) if row else 0


def _seed_buffered_mission(
    db,
    user_id: uuid.UUID,
    mission_id: uuid.UUID,
    period_start: date,
    *,
    buffer_count: int,
    target_count: int,
    cab_reward: int,
    xp_reward: int,
    current_count: int,
) -> uuid.UUID:
    """Insert a user_mission row directly with an explicit buffered state.

    Unlike ``_insert_user_mission`` this lets the test set buffer_count,
    target_count, cab_reward, xp_reward and current_count freely so a
    partially-completed buffered mission can be exercised. For a buffered
    mission (``buffer_count > 0``) ``period_extended_until`` is set to
    ``period_start + (buffer_count + 1) days`` — mirrors ``apply_buffer``
    — so the claim deadline gate uses the extended window.
    """
    um_id = uuid.uuid4()
    extended = (
        datetime.combine(period_start, datetime.min.time(), tzinfo=UTC) + timedelta(days=buffer_count + 1)
        if buffer_count > 0
        else None
    )
    db.execute(
        text(
            "INSERT INTO user_missions "
            "    (id, user_id, mission_id, period_start, current_count, "
            "     status, target_count, cab_reward, xp_reward, buffer_count, "
            "     period_extended_until) "
            "VALUES (:id, :uid, :mid, :period, :count, 'pending', "
            "        :target, :cab, :xp, :buf, :ext)"
        ),
        {
            "id": um_id,
            "uid": user_id,
            "mid": mission_id,
            "period": period_start,
            "count": current_count,
            "target": target_count,
            "cab": cab_reward,
            "xp": xp_reward,
            "buf": buffer_count,
            "ext": extended,
        },
    )
    db.flush()
    return um_id


class TestBufferedMissionXp:
    """RW-06 : XP is flat and credited once, at the FIRST claim — so a
    buffered mission claimed partially still receives its XP even if the
    period expires before the final portion."""

    def test_first_partial_claim_of_buffered_mission_awards_xp(self, db, user_client):
        """A buffered mission (n=2) claimed partially (1 portion of 3)
        must credit the flat XP at this first claim."""
        client, set_user = user_client
        uid = make_user(db)
        set_user(uid)
        mid = make_mission(
            db,
            action_type="receipt_scan",
            frequency="daily",
            difficulty="easy",
            target_count=2,
            cab_reward=30,
        )
        # n=2 → target 6, cab 90 (3×30), palier_size 2, xp flat 100.
        # current_count=2 → 1 palier reached. period_start 2 days ago →
        # days_elapsed gate = 3, so 1 portion claimable.
        um_id = _seed_buffered_mission(
            db,
            uid,
            mid,
            TODAY - timedelta(days=2),
            buffer_count=2,
            target_count=6,
            cab_reward=90,
            xp_reward=100,
            current_count=2,
        )
        db.commit()

        resp = client.post(f"/api/v1/gamification/missions/{um_id}/claim")
        assert resp.status_code == 200, resp.text
        assert resp.json()["portions_claimed_total"] == 1  # partial
        assert _xp_balance(db, uid) == 100  # flat XP credited at 1st claim

    def test_xp_not_double_credited_on_subsequent_claims(self, db, user_client):
        """A 2nd and 3rd claim of the same buffered mission must NOT
        re-credit XP — flat XP is awarded exactly once."""
        client, set_user = user_client
        uid = make_user(db)
        set_user(uid)
        mid = make_mission(
            db,
            action_type="receipt_scan",
            frequency="daily",
            difficulty="easy",
            target_count=2,
            cab_reward=30,
        )
        # n=2 → target 6, palier_size 2. current_count=6 → 3 paliers.
        # period_start 2 days ago → window = n+1 = 3 days → still open,
        # days_elapsed gate = 3 → all 3 portions claimable at once.
        um_id = _seed_buffered_mission(
            db,
            uid,
            mid,
            TODAY - timedelta(days=2),
            buffer_count=2,
            target_count=6,
            cab_reward=90,
            xp_reward=100,
            current_count=6,
        )
        db.commit()

        # First claim — takes all 3 portions at once (gating allows it).
        r1 = client.post(f"/api/v1/gamification/missions/{um_id}/claim")
        assert r1.status_code == 200, r1.text
        assert _xp_balance(db, uid) == 100

        # Second claim — nothing left, must 409 and NOT touch XP.
        r2 = client.post(f"/api/v1/gamification/missions/{um_id}/claim")
        assert r2.status_code == 409
        assert _xp_balance(db, uid) == 100  # no double-credit

    def test_xp_awarded_once_across_two_partial_claims(self, db, user_client):
        """Two partial claims on distinct days — XP credited on the first
        only, never re-credited on the second."""
        client, set_user = user_client
        uid = make_user(db)
        set_user(uid)
        mid = make_mission(
            db,
            action_type="receipt_scan",
            frequency="daily",
            difficulty="easy",
            target_count=2,
            cab_reward=30,
        )
        # n=1 → target 4, palier_size 2, max 2 portions. current_count=2
        # → 1 palier. period_start yesterday → days_elapsed gate = 2.
        # → portion 1 claimable now.
        um_id = _seed_buffered_mission(
            db,
            uid,
            mid,
            TODAY - timedelta(days=1),
            buffer_count=1,
            target_count=4,
            cab_reward=60,
            xp_reward=50,
            current_count=2,
        )
        db.commit()

        r1 = client.post(f"/api/v1/gamification/missions/{um_id}/claim")
        assert r1.status_code == 200, r1.text
        assert r1.json()["portions_claimed_total"] == 1
        assert _xp_balance(db, uid) == 50

        # Progress to full target → 2nd palier reachable.
        db.execute(
            text("UPDATE user_missions SET current_count = 4 WHERE id = :id"),
            {"id": um_id},
        )
        db.commit()

        r2 = client.post(f"/api/v1/gamification/missions/{um_id}/claim")
        assert r2.status_code == 200, r2.text
        assert r2.json()["portions_claimed_total"] == 2  # final portion
        assert _xp_balance(db, uid) == 50  # XP not re-credited

    def test_non_buffered_mission_still_awards_xp(self, db, user_client):
        """The degenerate case (n=0) must keep awarding XP at its single
        all-or-nothing claim."""
        client, set_user = user_client
        uid = make_user(db)
        set_user(uid)
        mid = make_mission(
            db,
            action_type="receipt_scan",
            frequency="daily",
            difficulty="easy",
            target_count=3,
            cab_reward=40,
        )
        um_id = _seed_buffered_mission(
            db,
            uid,
            mid,
            TODAY,
            buffer_count=0,
            target_count=3,
            cab_reward=40,
            xp_reward=25,
            current_count=3,
        )
        db.commit()

        resp = client.post(f"/api/v1/gamification/missions/{um_id}/claim")
        assert resp.status_code == 200, resp.text
        assert resp.json()["mission_status"] == "claimed"
        assert _xp_balance(db, uid) == 25
