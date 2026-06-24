"""
TDD tests for Défi communautaire (community challenges).

Covers:
  - GET  /api/v1/gamification/challenge
  - POST /api/v1/gamification/challenge/milestones/{milestone_id}/claim
  - maybe_increment_challenge — called via existing event/action endpoints
  - get_active_community_multiplier — applied inside award_cab / award_xp

Challenge status logic (computed in Python):
  - ACTIVE:  is_active=True  AND now() < ends_at
  - FROZEN:  is_active=True  AND ends_at <= now() < ends_at + grace_period_days
  - EXPIRED: now() >= ends_at + grace_period_days  OR is_active=False
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text

from tests.conftest import make_user

# ---------------------------------------------------------------------------
# SQL helpers
# ---------------------------------------------------------------------------


def _make_challenge(
    db,
    *,
    action_type: str = "receipt_scan",
    objective: int = 1000,
    is_active: bool = True,
    ends_at: datetime | None = None,
    grace_period_days: int = 3,
    title: str = "Défi test",
    description: str = "Description test",
    action_filter: dict | None = None,
) -> uuid.UUID:
    """Insert a community_challenges row and its companion progress row."""
    challenge_id = uuid.uuid4()
    if ends_at is None:
        ends_at = datetime.now(UTC) + timedelta(days=7)
    db.execute(
        text(
            "INSERT INTO community_challenges "
            "    (id, title, description, action_type, action_filter, objective, "
            "     starts_at, ends_at, grace_period_days, is_active) "
            "VALUES (:id, :title, :desc, :action, :filter, :obj, "
            "        now() - interval '1 day', :ends_at, :grace, :active)"
        ),
        {
            "id": challenge_id,
            "title": title,
            "desc": description,
            "action": action_type,
            "filter": json.dumps(action_filter) if action_filter else None,
            "obj": objective,
            "ends_at": ends_at,
            "grace": grace_period_days,
            "active": is_active,
        },
    )
    # Ensure progress row exists (upsert)
    db.execute(
        text(
            "INSERT INTO community_challenge_progress (challenge_id, current_count) "
            "VALUES (:cid, 0) "
            "ON CONFLICT (challenge_id) DO NOTHING"
        ),
        {"cid": challenge_id},
    )
    db.commit()
    return challenge_id


def _make_milestone(
    db,
    challenge_id: uuid.UUID,
    *,
    threshold: int = 100,
    reward_type: str = "cab",
    reward_value: dict | None = None,
    label: str = "Palier 1",
    sort_order: int = 1,
) -> uuid.UUID:
    """Insert a community_challenge_milestones row."""
    milestone_id = uuid.uuid4()
    if reward_value is None:
        if reward_type == "cab":
            reward_value = {"amount": 500}
        elif reward_type == "xp":
            reward_value = {"amount": 200}
        elif reward_type == "multiplier":
            reward_value = {"multiplier": 0.5, "duration_hours": 48, "applies_to": "both"}
        else:
            reward_value = {}
    db.execute(
        text(
            "INSERT INTO community_challenge_milestones "
            "    (id, challenge_id, threshold, reward_type, reward_value, label, sort_order) "
            "VALUES (:id, :cid, :threshold, :rtype, :rval, :label, :sort)"
        ),
        {
            "id": milestone_id,
            "cid": challenge_id,
            "threshold": threshold,
            "rtype": reward_type,
            "rval": json.dumps(reward_value),
            "label": label,
            "sort": sort_order,
        },
    )
    db.commit()
    return milestone_id


def _set_progress(db, challenge_id: uuid.UUID, count: int) -> None:
    """Set community_challenge_progress.current_count directly."""
    db.execute(
        text(
            "UPDATE community_challenge_progress "
            "SET current_count = :count, last_updated_at = now() "
            "WHERE challenge_id = :cid"
        ),
        {"count": count, "cid": challenge_id},
    )
    db.commit()


def _insert_claim(
    db,
    challenge_id: uuid.UUID,
    milestone_id: uuid.UUID,
    user_id: uuid.UUID,
) -> uuid.UUID:
    """Directly insert a claim row (for pre-condition setup)."""
    claim_id = uuid.uuid4()
    db.execute(
        text(
            "INSERT INTO community_challenge_claims "
            "    (id, challenge_id, milestone_id, user_id) "
            "VALUES (:id, :cid, :mid, :uid)"
        ),
        {"id": claim_id, "cid": challenge_id, "mid": milestone_id, "uid": user_id},
    )
    db.commit()
    return claim_id


def _insert_multiplier(
    db,
    challenge_id: uuid.UUID,
    user_id: uuid.UUID,
    *,
    multiplier: float = 0.5,
    applies_to: str = "both",
    active_from: datetime | None = None,
    active_until: datetime | None = None,
) -> uuid.UUID:
    """Insert a community_multipliers row directly."""
    multiplier_id = uuid.uuid4()
    if active_from is None:
        active_from = datetime.now(UTC) - timedelta(hours=1)
    if active_until is None:
        active_until = datetime.now(UTC) + timedelta(hours=47)
    db.execute(
        text(
            "INSERT INTO community_multipliers "
            "    (id, challenge_id, user_id, multiplier, applies_to, "
            "     active_from, active_until) "
            "VALUES (:id, :cid, :uid, :mult, :applies, :from, :until)"
        ),
        {
            "id": multiplier_id,
            "cid": challenge_id,
            "uid": user_id,
            "mult": multiplier,
            "applies": applies_to,
            "from": active_from,
            "until": active_until,
        },
    )
    db.commit()
    return multiplier_id


# ---------------------------------------------------------------------------
# GET /api/v1/gamification/challenge
# ---------------------------------------------------------------------------


class TestGetChallenge:
    def test_no_active_challenge_returns_404(self, user_client, db):
        client, set_user = user_client
        uid = make_user(db)
        set_user(uid)

        resp = client.get("/api/v1/gamification/challenge")
        assert resp.status_code == 404
        assert resp.json()["detail"] == "challenge_not_found"

    def test_active_challenge_returns_full_state(self, user_client, db):
        client, set_user = user_client
        uid = make_user(db)
        set_user(uid)

        challenge_id = _make_challenge(db, title="Grand défi", objective=500)
        _set_progress(db, challenge_id, 120)

        resp = client.get("/api/v1/gamification/challenge")
        assert resp.status_code == 200
        body = resp.json()

        assert body["id"] == str(challenge_id)
        assert body["title"] == "Grand défi"
        assert body["objective"] == 500
        assert body["current_count"] == 120
        assert body["status"] == "active"
        assert body["ends_at"] is not None
        assert body["claims_until"] is not None
        assert isinstance(body["milestones"], list)

    def test_milestones_show_unlocked_and_locked(self, user_client, db):
        client, set_user = user_client
        uid = make_user(db)
        set_user(uid)

        challenge_id = _make_challenge(db, objective=500)
        _set_progress(db, challenge_id, 150)
        m1 = _make_milestone(db, challenge_id, threshold=100, label="Palier 1", sort_order=1)
        m2 = _make_milestone(db, challenge_id, threshold=300, label="Palier 2", sort_order=2)

        resp = client.get("/api/v1/gamification/challenge")
        assert resp.status_code == 200
        milestones = {m["id"]: m for m in resp.json()["milestones"]}

        assert milestones[str(m1)]["unlocked"] is True
        assert milestones[str(m1)]["claimed"] is False
        assert milestones[str(m2)]["unlocked"] is False
        assert milestones[str(m2)]["claimed"] is False

    def test_milestones_show_claimed_by_user(self, user_client, db):
        client, set_user = user_client
        uid = make_user(db)
        set_user(uid)

        challenge_id = _make_challenge(db, objective=500)
        _set_progress(db, challenge_id, 200)
        m1 = _make_milestone(db, challenge_id, threshold=100, label="Palier 1", sort_order=1)
        _insert_claim(db, challenge_id, m1, uid)

        resp = client.get("/api/v1/gamification/challenge")
        assert resp.status_code == 200
        milestone = resp.json()["milestones"][0]
        assert milestone["id"] == str(m1)
        assert milestone["claimed"] is True

    def test_frozen_challenge_has_frozen_status(self, user_client, db):
        """Challenge where ends_at is in the past but within grace period → FROZEN."""
        client, set_user = user_client
        uid = make_user(db)
        set_user(uid)

        ends_at = datetime.now(UTC) - timedelta(hours=12)  # ended 12h ago
        _make_challenge(
            db,
            ends_at=ends_at,
            grace_period_days=3,  # grace extends 3 days from ends_at → still claimable
        )

        resp = client.get("/api/v1/gamification/challenge")
        assert resp.status_code == 200
        assert resp.json()["status"] == "frozen"

    def test_expired_challenge_returns_404(self, user_client, db):
        """Challenge past ends_at + grace_period_days is no longer visible."""
        client, set_user = user_client
        uid = make_user(db)
        set_user(uid)

        ends_at = datetime.now(UTC) - timedelta(days=5)
        _make_challenge(
            db,
            ends_at=ends_at,
            grace_period_days=3,  # ends_at + 3d = 2 days ago → expired
        )

        resp = client.get("/api/v1/gamification/challenge")
        assert resp.status_code == 404
        assert resp.json()["detail"] == "challenge_not_found"


# ---------------------------------------------------------------------------
# POST /api/v1/gamification/challenge/milestones/{milestone_id}/claim
# ---------------------------------------------------------------------------


class TestClaimMilestone:
    def test_claim_cab_milestone_credits_cab(self, user_client, db):
        client, set_user = user_client
        uid = make_user(db)
        set_user(uid)

        challenge_id = _make_challenge(db, objective=500)
        _set_progress(db, challenge_id, 200)
        m1 = _make_milestone(
            db,
            challenge_id,
            threshold=100,
            reward_type="cab",
            reward_value={"amount": 500},
            sort_order=1,
        )

        resp = client.post(f"/api/v1/gamification/challenge/milestones/{m1}/claim")
        assert resp.status_code == 200
        body = resp.json()
        assert body["milestone_id"] == str(m1)
        assert body["reward_type"] == "cab"
        assert body["reward_value"] == {"amount": 500}

        # Balance credited
        balance = db.execute(
            text("SELECT balance FROM user_cab_balance WHERE user_id = :uid"),
            {"uid": uid},
        ).scalar()
        assert balance == 500

        # Claim row inserted
        claim = db.execute(
            text("SELECT id FROM community_challenge_claims WHERE milestone_id = :mid AND user_id = :uid"),
            {"mid": m1, "uid": uid},
        ).first()
        assert claim is not None

    def test_claim_xp_milestone_credits_xp(self, user_client, db):
        client, set_user = user_client
        uid = make_user(db)
        set_user(uid)

        challenge_id = _make_challenge(db, objective=500)
        _set_progress(db, challenge_id, 200)
        m1 = _make_milestone(
            db,
            challenge_id,
            threshold=100,
            reward_type="xp",
            reward_value={"amount": 200},
            sort_order=1,
        )

        resp = client.post(f"/api/v1/gamification/challenge/milestones/{m1}/claim")
        assert resp.status_code == 200
        body = resp.json()
        assert body["reward_type"] == "xp"
        assert body["reward_value"] == {"amount": 200}

        xp_row = db.execute(
            text("SELECT amount FROM xp_transactions WHERE user_id = :uid AND reason = 'challenge_milestone'"),
            {"uid": uid},
        ).first()
        assert xp_row is not None
        assert xp_row.amount == 200

    def test_claim_multiplier_milestone_creates_multiplier(self, user_client, db):
        client, set_user = user_client
        uid = make_user(db)
        set_user(uid)

        challenge_id = _make_challenge(db, objective=500)
        _set_progress(db, challenge_id, 300)
        reward_value = {"multiplier": 0.5, "duration_hours": 48, "applies_to": "both"}
        m1 = _make_milestone(
            db,
            challenge_id,
            threshold=200,
            reward_type="multiplier",
            reward_value=reward_value,
            sort_order=1,
        )

        resp = client.post(f"/api/v1/gamification/challenge/milestones/{m1}/claim")
        assert resp.status_code == 200
        assert resp.json()["reward_type"] == "multiplier"

        # community_multipliers row inserted
        mult_row = db.execute(
            text(
                "SELECT multiplier, applies_to, active_until "
                "FROM community_multipliers "
                "WHERE challenge_id = :cid AND user_id = :uid"
            ),
            {"cid": challenge_id, "uid": uid},
        ).first()
        assert mult_row is not None
        assert float(mult_row.multiplier) == pytest.approx(0.5)
        assert mult_row.applies_to == "both"

    def test_cannot_claim_locked_milestone(self, user_client, db):
        """Threshold not reached → 409 milestone_locked."""
        client, set_user = user_client
        uid = make_user(db)
        set_user(uid)

        challenge_id = _make_challenge(db, objective=500)
        _set_progress(db, challenge_id, 50)  # below threshold of 100
        m1 = _make_milestone(db, challenge_id, threshold=100, sort_order=1)

        resp = client.post(f"/api/v1/gamification/challenge/milestones/{m1}/claim")
        assert resp.status_code == 409
        assert resp.json()["detail"] == "milestone_locked"

    def test_cannot_claim_twice(self, user_client, db):
        """Already claimed → 409 milestone_already_claimed."""
        client, set_user = user_client
        uid = make_user(db)
        set_user(uid)

        challenge_id = _make_challenge(db, objective=500)
        _set_progress(db, challenge_id, 200)
        m1 = _make_milestone(db, challenge_id, threshold=100, sort_order=1)
        _insert_claim(db, challenge_id, m1, uid)

        resp = client.post(f"/api/v1/gamification/challenge/milestones/{m1}/claim")
        assert resp.status_code == 409
        assert resp.json()["detail"] == "milestone_already_claimed"

    def test_cannot_claim_expired_challenge(self, user_client, db):
        """Past grace period → 409 challenge_expired."""
        client, set_user = user_client
        uid = make_user(db)
        set_user(uid)

        ends_at = datetime.now(UTC) - timedelta(days=5)
        challenge_id = _make_challenge(
            db,
            ends_at=ends_at,
            grace_period_days=3,
            is_active=True,
        )
        _set_progress(db, challenge_id, 200)
        m1 = _make_milestone(db, challenge_id, threshold=100, sort_order=1)

        resp = client.post(f"/api/v1/gamification/challenge/milestones/{m1}/claim")
        assert resp.status_code == 409
        assert resp.json()["detail"] == "challenge_expired"

    def test_claim_milestone_not_found(self, user_client, db):
        """Milestone belonging to no active challenge → 404 milestone_not_found."""
        client, set_user = user_client
        uid = make_user(db)
        set_user(uid)

        _make_challenge(db)  # active challenge exists but milestone is unrelated
        fake_milestone_id = uuid.uuid4()

        resp = client.post(f"/api/v1/gamification/challenge/milestones/{fake_milestone_id}/claim")
        assert resp.status_code == 404
        assert resp.json()["detail"] == "milestone_not_found"


# ---------------------------------------------------------------------------
# Community multiplier applied inside award_cab / award_xp
# ---------------------------------------------------------------------------


class TestCommunityMultiplier:
    def test_multiplier_applied_to_award_cab(self, db):
        """Active community multiplier (applies_to='cab') boosts award_cab."""
        from repositories.cab_repository import award_cab

        uid = make_user(db)
        challenge_id = _make_challenge(db)
        _insert_multiplier(db, challenge_id, uid, multiplier=0.5, applies_to="cab")

        award_cab(db, uid, 100, "receipt_scan")
        db.commit()

        balance = db.execute(
            text("SELECT balance FROM user_cab_balance WHERE user_id = :uid"),
            {"uid": uid},
        ).scalar()
        # 100 * (1 + 0.5) = 150
        assert balance == 150

    def test_multiplier_applied_to_award_xp(self, db):
        """Active community multiplier (applies_to='xp') boosts award_xp."""
        from repositories.xp_repository import award_xp

        uid = make_user(db)
        challenge_id = _make_challenge(db)
        _insert_multiplier(db, challenge_id, uid, multiplier=1.0, applies_to="xp")

        award_xp(db, uid, 10, "receipt_scan")
        db.commit()

        xp_balance = db.execute(
            text("SELECT balance FROM user_xp_balance WHERE user_id = :uid"),
            {"uid": uid},
        ).scalar()
        # 10 * (1 + 1.0) = 20
        assert int(xp_balance) == 20

    def test_multiplier_not_applied_when_expired(self, db):
        """Expired multiplier (active_until in past) has no effect."""
        from repositories.cab_repository import award_cab

        uid = make_user(db)
        challenge_id = _make_challenge(db)
        _insert_multiplier(
            db,
            challenge_id,
            uid,
            multiplier=0.5,
            applies_to="cab",
            active_until=datetime.now(UTC) - timedelta(hours=1),
        )

        award_cab(db, uid, 100, "receipt_scan")
        db.commit()

        balance = db.execute(
            text("SELECT balance FROM user_cab_balance WHERE user_id = :uid"),
            {"uid": uid},
        ).scalar()
        # No multiplier — base amount unchanged
        assert balance == 100

    def test_multiplier_applies_to_cab_only(self, db):
        """applies_to='cab' multiplier boosts CAB but leaves XP unaffected."""
        from repositories.cab_repository import award_cab
        from repositories.xp_repository import award_xp

        uid = make_user(db)
        challenge_id = _make_challenge(db)
        _insert_multiplier(db, challenge_id, uid, multiplier=0.5, applies_to="cab")

        award_cab(db, uid, 100, "receipt_scan")
        award_xp(db, uid, 10, "receipt_scan")
        db.commit()

        cab_balance = db.execute(
            text("SELECT balance FROM user_cab_balance WHERE user_id = :uid"),
            {"uid": uid},
        ).scalar()
        xp_balance = db.execute(
            text("SELECT balance FROM user_xp_balance WHERE user_id = :uid"),
            {"uid": uid},
        ).scalar()

        assert cab_balance == 150  # boosted
        assert int(xp_balance) == 10  # not boosted

    def test_multiplier_applies_to_xp_only(self, db):
        """applies_to='xp' multiplier boosts XP but leaves CAB unaffected."""
        from repositories.cab_repository import award_cab
        from repositories.xp_repository import award_xp

        uid = make_user(db)
        challenge_id = _make_challenge(db)
        _insert_multiplier(db, challenge_id, uid, multiplier=1.0, applies_to="xp")

        award_cab(db, uid, 100, "receipt_scan")
        award_xp(db, uid, 10, "receipt_scan")
        db.commit()

        cab_balance = db.execute(
            text("SELECT balance FROM user_cab_balance WHERE user_id = :uid"),
            {"uid": uid},
        ).scalar()
        xp_balance = db.execute(
            text("SELECT balance FROM user_xp_balance WHERE user_id = :uid"),
            {"uid": uid},
        ).scalar()

        assert cab_balance == 100  # not boosted
        assert int(xp_balance) == 20  # boosted: 10 * (1 + 1.0)


# ---------------------------------------------------------------------------
# maybe_increment_challenge — e2e via action endpoints
# ---------------------------------------------------------------------------


class TestMaybeIncrementChallenge:
    def test_scan_accepted_increments_challenge(self, client, db):
        """POST /rewards/events/action increments an active receipt_scan challenge."""
        uid = make_user(db)
        challenge_id = _make_challenge(db, action_type="receipt_scan")
        _set_progress(db, challenge_id, 0)

        resp = client.post(
            "/api/v1/rewards/events/action",
            json={
                "user_id": str(uid),
                "action_type": "receipt_scan",
                "idempotency_key": str(uuid.uuid4()),
            },
        )
        assert resp.status_code == 200

        count = db.execute(
            text("SELECT current_count FROM community_challenge_progress WHERE challenge_id = :cid"),
            {"cid": challenge_id},
        ).scalar()
        assert count == 1

    def test_feed_jack_increments_challenge(self, user_client, db):
        """POST /gamification/streak/feed increments an active feed_jack challenge."""
        client, set_user = user_client
        uid = make_user(db)
        set_user(uid)

        challenge_id = _make_challenge(db, action_type="feed_jack")
        _set_progress(db, challenge_id, 0)

        resp = client.post("/api/v1/gamification/streak/feed", json={})
        assert resp.status_code == 200

        count = db.execute(
            text("SELECT current_count FROM community_challenge_progress WHERE challenge_id = :cid"),
            {"cid": challenge_id},
        ).scalar()
        assert count == 1

    def test_referral_increments_challenge_for_referrer(self, client, db):
        """POST /rewards/referral/trigger increments the referral challenge
        for X (the referrer), not Y (the referred). The challenge action is
        "successful referral" — that's X's action, not Y's subscription."""
        referrer_id = make_user(db)
        referred_id = make_user(db)
        # Seed the X→Y link (otherwise the trigger is a silent no-op)
        ref_id = uuid.uuid4()
        db.execute(
            text(
                "INSERT INTO referral_codes (id, user_id, code, type, created_at) "
                "VALUES (:id, :uid, 'REFCODE', 'user', now())"
            ),
            {"id": ref_id, "uid": referrer_id},
        )
        db.execute(
            text(
                "INSERT INTO referral_uses (id, referral_id, referred_user_id, created_at) "
                "VALUES (:id, :rid, :ruid, now())"
            ),
            {"id": uuid.uuid4(), "rid": ref_id, "ruid": referred_id},
        )
        db.flush()

        challenge_id = _make_challenge(db, action_type="referral")
        _set_progress(db, challenge_id, 0)

        resp = client.post(
            "/api/v1/rewards/referral/trigger",
            json={
                "referred_user_id": str(referred_id),
                "plan": "monthly",
            },
        )
        assert resp.status_code == 200

        count = db.execute(
            text("SELECT current_count FROM community_challenge_progress WHERE challenge_id = :cid"),
            {"cid": challenge_id},
        ).scalar()
        assert count == 1

    def test_no_increment_when_no_active_challenge(self, client, db):
        """trigger_action when no challenge exists → no progress row created."""
        uid = make_user(db)

        resp = client.post(
            "/api/v1/rewards/events/action",
            json={
                "user_id": str(uid),
                "action_type": "receipt_scan",
                "idempotency_key": str(uuid.uuid4()),
            },
        )
        assert resp.status_code == 200

        count = db.execute(
            text("SELECT COUNT(*) FROM community_challenge_progress"),
        ).scalar()
        assert count == 0

    def test_no_increment_when_frozen(self, client, db):
        """trigger_action with a FROZEN challenge (in grace period) does NOT increment."""
        uid = make_user(db)
        ends_at = datetime.now(UTC) - timedelta(hours=12)
        challenge_id = _make_challenge(
            db,
            action_type="receipt_scan",
            ends_at=ends_at,
            grace_period_days=3,
        )
        _set_progress(db, challenge_id, 10)

        resp = client.post(
            "/api/v1/rewards/events/action",
            json={
                "user_id": str(uid),
                "action_type": "receipt_scan",
                "idempotency_key": str(uuid.uuid4()),
            },
        )
        assert resp.status_code == 200

        count = db.execute(
            text("SELECT current_count FROM community_challenge_progress WHERE challenge_id = :cid"),
            {"cid": challenge_id},
        ).scalar()
        assert count == 10  # unchanged

    def test_no_increment_when_context_missing_and_filter_set(self, db):
        """Challenge with action_filter but no context passed → no increment (conservative)."""
        from repositories.challenge_repository import maybe_increment_challenge

        uid = make_user(db)
        challenge_id = _make_challenge(db, action_type="label_scan", action_filter={"category": "toys"})
        _set_progress(db, challenge_id, 0)

        maybe_increment_challenge(db, uid, "label_scan", context=None)
        db.commit()

        count = db.execute(
            text("SELECT current_count FROM community_challenge_progress WHERE challenge_id = :cid"),
            {"cid": challenge_id},
        ).scalar()
        assert count == 0

    def test_increment_when_context_matches_filter(self, db):
        """Challenge with action_filter and matching context → increments."""
        from repositories.challenge_repository import maybe_increment_challenge

        uid = make_user(db)
        challenge_id = _make_challenge(db, action_type="label_scan", action_filter={"category": "toys"})
        _set_progress(db, challenge_id, 0)

        maybe_increment_challenge(db, uid, "label_scan", context={"category": "toys", "brand": "lego"})
        db.commit()

        count = db.execute(
            text("SELECT current_count FROM community_challenge_progress WHERE challenge_id = :cid"),
            {"cid": challenge_id},
        ).scalar()
        assert count == 1

    def test_no_increment_when_context_does_not_match_filter(self, db):
        """Challenge with action_filter and non-matching context → no increment."""
        from repositories.challenge_repository import maybe_increment_challenge

        uid = make_user(db)
        challenge_id = _make_challenge(db, action_type="label_scan", action_filter={"category": "toys"})
        _set_progress(db, challenge_id, 0)

        maybe_increment_challenge(db, uid, "label_scan", context={"category": "food"})
        db.commit()

        count = db.execute(
            text("SELECT current_count FROM community_challenge_progress WHERE challenge_id = :cid"),
            {"cid": challenge_id},
        ).scalar()
        assert count == 0

    def test_no_filter_always_increments_regardless_of_context(self, db):
        """Challenge without filter increments whether context is passed or not."""
        from repositories.challenge_repository import maybe_increment_challenge

        uid = make_user(db)
        challenge_id = _make_challenge(db, action_type="receipt_scan", action_filter=None)
        _set_progress(db, challenge_id, 0)

        maybe_increment_challenge(db, uid, "receipt_scan", context={"category": "food"})
        db.commit()

        count = db.execute(
            text("SELECT current_count FROM community_challenge_progress WHERE challenge_id = :cid"),
            {"cid": challenge_id},
        ).scalar()
        assert count == 1

    def test_no_increment_action_type_mismatch(self, client, db):
        """Action type mismatch → challenge progress unaffected."""
        uid = make_user(db)
        challenge_id = _make_challenge(db, action_type="label_scan")
        _set_progress(db, challenge_id, 5)

        # Fire a receipt_scan event — challenge is label_scan only
        resp = client.post(
            "/api/v1/rewards/events/action",
            json={
                "user_id": str(uid),
                "action_type": "receipt_scan",
                "idempotency_key": str(uuid.uuid4()),
            },
        )
        assert resp.status_code == 200

        count = db.execute(
            text("SELECT current_count FROM community_challenge_progress WHERE challenge_id = :cid"),
            {"cid": challenge_id},
        ).scalar()
        assert count == 5  # unchanged
