"""
Tests for GET /gamification/battlepass and POST /rewards/battlepass/claim/{milestone_id}.
"""

from __future__ import annotations

import uuid

from repositories.cab_repository import award_cab
from sqlalchemy import text

from tests.conftest import make_gift_card_brand, make_milestone, make_season, make_subscription, make_user

# ===========================================================================
# GET /gamification/battlepass
# ===========================================================================


class TestGetBattlepass:
    def test_no_active_season(self, user_client, db):
        http, set_user = user_client
        uid = make_user(db)
        set_user(uid)
        resp = http.get("/api/v1/gamification/battlepass")
        assert resp.status_code == 200
        assert resp.json() == {"season": None}

    def test_active_season_no_milestones(self, user_client, db):
        http, set_user = user_client
        uid = make_user(db)
        make_season(db, is_active=True)
        set_user(uid)
        resp = http.get("/api/v1/gamification/battlepass")
        assert resp.status_code == 200
        body = resp.json()
        assert body["season"] is not None
        assert body["milestones"] == []
        assert body["cab_earned_season"] == 0

    def test_all_milestones_locked_when_no_cab(self, user_client, db):
        http, set_user = user_client
        uid = make_user(db)
        season_id = make_season(db, is_active=True)
        make_milestone(db, season_id=season_id, milestone_number=1, cab_required=200)
        make_milestone(db, season_id=season_id, milestone_number=2, cab_required=500)
        set_user(uid)
        resp = http.get("/api/v1/gamification/battlepass")
        assert resp.status_code == 200
        statuses = [m["status"] for m in resp.json()["milestones"]]
        assert statuses == ["locked", "locked"]

    def test_milestone_unlocked_when_cab_sufficient(self, user_client, db):
        http, set_user = user_client
        uid = make_user(db)
        season_id = make_season(db, is_active=True)
        make_milestone(db, season_id=season_id, milestone_number=1, cab_required=200)
        make_milestone(db, season_id=season_id, milestone_number=2, cab_required=500)
        award_cab(db, uid, 300, "receipt_scan")
        db.flush()
        set_user(uid)
        resp = http.get("/api/v1/gamification/battlepass")
        assert resp.status_code == 200
        body = resp.json()
        statuses = [m["status"] for m in body["milestones"]]
        assert statuses == ["unlocked", "locked"]
        assert body["cab_earned_season"] == 300

    def test_milestone_claimed_status(self, user_client, db):
        http, set_user = user_client
        uid = make_user(db)
        season_id = make_season(db, is_active=True)
        milestone_id = make_milestone(db, season_id=season_id, cab_required=200)
        award_cab(db, uid, 300, "receipt_scan")
        db.execute(
            text("INSERT INTO user_battlepass_claims (id, user_id, milestone_id) VALUES (:id, :uid, :mid)"),
            {"id": uuid.uuid4(), "uid": uid, "mid": milestone_id},
        )
        db.flush()
        set_user(uid)
        resp = http.get("/api/v1/gamification/battlepass")
        assert resp.status_code == 200
        assert resp.json()["milestones"][0]["status"] == "claimed"

    def test_milestones_ordered_by_cab_required(self, user_client, db):
        http, set_user = user_client
        uid = make_user(db)
        season_id = make_season(db, is_active=True)
        make_milestone(db, season_id=season_id, milestone_number=2, cab_required=500)
        make_milestone(db, season_id=season_id, milestone_number=1, cab_required=200)
        set_user(uid)
        resp = http.get("/api/v1/gamification/battlepass")
        cabs = [m["cab_required"] for m in resp.json()["milestones"]]
        assert cabs == [200, 500]

    def test_season_fields_present(self, user_client, db):
        http, set_user = user_client
        uid = make_user(db)
        make_season(db, is_active=True, season_number=3)
        set_user(uid)
        resp = http.get("/api/v1/gamification/battlepass")
        season = resp.json()["season"]
        assert "id" in season
        assert season["name"] == "Saison 3"
        assert "ends_at" in season

    def test_inactive_season_milestone_not_found(self, user_client, db):
        http, set_user = user_client
        uid = make_user(db)
        inactive_season_id = make_season(db, is_active=False, season_number=99)
        make_milestone(db, season_id=inactive_season_id, cab_required=100)
        set_user(uid)
        resp = http.get("/api/v1/gamification/battlepass")
        assert resp.status_code == 200
        assert resp.json() == {"season": None}

    def test_requires_auth(self, raw_client, db):
        resp = raw_client.get("/api/v1/gamification/battlepass")
        assert resp.status_code == 401


# ===========================================================================
# POST /gamification/battlepass/claim/{milestone_id}
# ===========================================================================


class TestClaimBattlepassMilestone:
    def test_claim_cab_reward_happy_path(self, user_client, db):
        http, set_user = user_client
        uid = make_user(db)
        season_id = make_season(db, is_active=True)
        milestone_id = make_milestone(db, season_id=season_id, cab_required=200, reward_type="cab", reward_value=100)
        award_cab(db, uid, 200, "receipt_scan")
        db.flush()
        set_user(uid)
        resp = http.post(f"/api/v1/gamification/battlepass/claim/{milestone_id}")
        assert resp.status_code == 200
        body = resp.json()
        assert body["claimed"] is True
        assert body["reward_type"] == "cab"
        assert body["reward_value"] == 100
        assert body["new_cab_balance"] == 300  # 200 earned + 100 reward

    def test_claim_inserts_claim_row(self, user_client, db):
        http, set_user = user_client
        uid = make_user(db)
        season_id = make_season(db, is_active=True)
        milestone_id = make_milestone(db, season_id=season_id, cab_required=100, reward_type="cab", reward_value=50)
        award_cab(db, uid, 100, "receipt_scan")
        db.flush()
        set_user(uid)
        http.post(f"/api/v1/gamification/battlepass/claim/{milestone_id}")
        row = db.execute(
            text("SELECT 1 FROM user_battlepass_claims WHERE user_id = :uid AND milestone_id = :mid"),
            {"uid": uid, "mid": milestone_id},
        ).first()
        assert row is not None

    def test_claim_gift_card_no_cab_credited(self, user_client, db):
        http, set_user = user_client
        uid = make_user(db)
        season_id = make_season(db, is_active=True)
        milestone_id = make_milestone(
            db, season_id=season_id, cab_required=200, reward_type="gift_card", reward_value=500
        )
        award_cab(db, uid, 200, "receipt_scan")
        db.flush()
        set_user(uid)
        resp = http.post(f"/api/v1/gamification/battlepass/claim/{milestone_id}")
        assert resp.status_code == 200
        body = resp.json()
        assert body["reward_type"] == "gift_card"
        assert body["new_cab_balance"] == 200  # unchanged

    def test_claim_gift_card_creates_order(self, user_client, db):
        """Claiming a gift_card milestone creates a pending gift_card_orders row."""
        from main import app

        http, set_user = user_client
        uid = make_user(db)
        season_id = make_season(db, is_active=True)
        milestone_id = make_milestone(
            db, season_id=season_id, cab_required=100, reward_type="gift_card", reward_value=2000
        )
        award_cab(db, uid, 100, "receipt_scan")
        brand_id = make_gift_card_brand(db, name="Amazon")
        db.flush()

        original_brand_id = app.state.cfg["gift_cards"]["battlepass_brand_id"]
        app.state.cfg["gift_cards"]["battlepass_brand_id"] = str(brand_id)
        try:
            set_user(uid)
            resp = http.post(f"/api/v1/gamification/battlepass/claim/{milestone_id}")
            assert resp.status_code == 200
        finally:
            app.state.cfg["gift_cards"]["battlepass_brand_id"] = original_brand_id

        row = db.execute(
            text("SELECT status, source_type, source_ref_id, denomination FROM gift_card_orders WHERE user_id = :uid"),
            {"uid": uid},
        ).first()
        assert row is not None
        assert row.status == "pending"
        assert row.source_type == "battlepass_milestone"
        assert row.source_ref_id == str(milestone_id)
        assert row.denomination == 2000

    def test_claim_locked_returns_403(self, user_client, db):
        http, set_user = user_client
        uid = make_user(db)
        season_id = make_season(db, is_active=True)
        milestone_id = make_milestone(db, season_id=season_id, cab_required=500)
        set_user(uid)
        resp = http.post(f"/api/v1/gamification/battlepass/claim/{milestone_id}")
        assert resp.status_code == 403
        assert resp.json()["detail"] == "milestone_locked"

    def test_claim_already_claimed_returns_409(self, user_client, db):
        http, set_user = user_client
        uid = make_user(db)
        season_id = make_season(db, is_active=True)
        milestone_id = make_milestone(db, season_id=season_id, cab_required=100, reward_type="cab", reward_value=50)
        award_cab(db, uid, 100, "receipt_scan")
        db.execute(
            text("INSERT INTO user_battlepass_claims (id, user_id, milestone_id) VALUES (:id, :uid, :mid)"),
            {"id": uuid.uuid4(), "uid": uid, "mid": milestone_id},
        )
        db.flush()
        set_user(uid)
        resp = http.post(f"/api/v1/gamification/battlepass/claim/{milestone_id}")
        assert resp.status_code == 409
        assert resp.json()["detail"] == "milestone_already_claimed"

    def test_claim_unknown_milestone_returns_404(self, user_client, db):
        http, set_user = user_client
        uid = make_user(db)
        make_season(db, is_active=True)
        set_user(uid)
        resp = http.post(f"/api/v1/gamification/battlepass/claim/{uuid.uuid4()}")
        assert resp.status_code == 404
        assert resp.json()["detail"] == "milestone_not_found"

    def test_claim_subscriber_only_without_subscription_returns_403(self, user_client, db):
        http, set_user = user_client
        uid = make_user(db)
        season_id = make_season(db, is_active=True)
        milestone_id = make_milestone(db, season_id=season_id, cab_required=100, subscriber_only=True)
        award_cab(db, uid, 100, "receipt_scan")
        db.flush()
        set_user(uid)
        resp = http.post(f"/api/v1/gamification/battlepass/claim/{milestone_id}")
        assert resp.status_code == 403
        assert resp.json()["detail"] == "subscriber_required"

    def test_claim_subscriber_only_with_active_subscription_succeeds(self, user_client, db):
        http, set_user = user_client
        uid = make_user(db)
        season_id = make_season(db, is_active=True)
        milestone_id = make_milestone(
            db,
            season_id=season_id,
            cab_required=100,
            reward_type="cab",
            reward_value=50,
            subscriber_only=True,
        )
        award_cab(db, uid, 100, "receipt_scan")
        make_subscription(db, uid)
        db.flush()
        set_user(uid)
        resp = http.post(f"/api/v1/gamification/battlepass/claim/{milestone_id}")
        assert resp.status_code == 200

    def test_claim_concurrent_race_returns_409(self, user_client, db):
        http, set_user = user_client
        uid = make_user(db)
        season_id = make_season(db, is_active=True)
        milestone_id = make_milestone(db, season_id=season_id, cab_required=100, reward_type="cab", reward_value=50)
        award_cab(db, uid, 100, "receipt_scan")
        db.flush()
        set_user(uid)
        resp1 = http.post(f"/api/v1/gamification/battlepass/claim/{milestone_id}")
        assert resp1.status_code == 200
        resp2 = http.post(f"/api/v1/gamification/battlepass/claim/{milestone_id}")
        assert resp2.status_code == 409
        assert resp2.json()["detail"] == "milestone_already_claimed"

    def test_requires_auth(self, raw_client, db):
        resp = raw_client.post(f"/api/v1/gamification/battlepass/claim/{uuid.uuid4()}")
        assert resp.status_code == 401


# ===========================================================================
# Self-feeding loop guard (bug archi acted 2026-05-08)
# ===========================================================================
#
# When a user claims a CAB-reward milestone, the awarded CABs MUST NOT feed
# back into ``user_battlepass_progress.cab_earned_season`` — otherwise claiming
# milestone N could unlock milestone N+1 "for free" (auto-feeding loop).
#
# The fix is a flag ``apply_to_bp_progress`` on ``award_cab`` (default True
# preserves backward compat for every other caller : scan, mission, referral…).
# ``claim_milestone`` is the only caller that passes False.
# ===========================================================================


class TestBpClaimNoSelfFeed:
    def test_bp_claim_cab_does_not_feed_progress(self, user_client, db):
        """Claiming a CAB milestone must NOT increment cab_earned_season."""
        from repositories.cab_repository import get_cab_earned_season

        http, set_user = user_client
        uid = make_user(db)
        season_id = make_season(db, is_active=True)
        milestone_id = make_milestone(
            db,
            season_id=season_id,
            cab_required=200,
            reward_type="cab",
            reward_value=100,
        )
        award_cab(db, uid, 200, "receipt_scan")
        db.flush()
        earned_before = get_cab_earned_season(db, uid, season_id)
        assert earned_before == 200

        set_user(uid)
        resp = http.post(f"/api/v1/gamification/battlepass/claim/{milestone_id}")
        assert resp.status_code == 200

        earned_after = get_cab_earned_season(db, uid, season_id)
        # The 100 CAB reward must NOT have leaked into season progress.
        assert earned_after == 200, (
            f"BP claim self-feed regression: cab_earned_season went {earned_before} → {earned_after} (should stay 200)."
        )

    def test_bp_claim_cab_still_credits_user_balance(self, user_client, db):
        """Claim still credits the user CAB balance + cabecoin_transactions."""
        http, set_user = user_client
        uid = make_user(db)
        season_id = make_season(db, is_active=True)
        milestone_id = make_milestone(
            db,
            season_id=season_id,
            cab_required=200,
            reward_type="cab",
            reward_value=100,
        )
        award_cab(db, uid, 200, "receipt_scan")
        db.flush()

        set_user(uid)
        resp = http.post(f"/api/v1/gamification/battlepass/claim/{milestone_id}")
        assert resp.status_code == 200
        assert resp.json()["new_cab_balance"] == 300  # 200 earned + 100 reward

        tx_row = db.execute(
            text(
                "SELECT amount, direction, reason FROM cabecoin_transactions "
                "WHERE user_id = :uid AND reason = 'battlepass_milestone'"
            ),
            {"uid": uid},
        ).first()
        assert tx_row is not None
        assert tx_row.direction == "credit"
        assert tx_row.amount == 100

    def test_award_cab_default_still_feeds_progress(self, db):
        """Backward compat: default award_cab still increments cab_earned_season."""
        from repositories.cab_repository import get_cab_earned_season

        uid = make_user(db)
        season_id = make_season(db, is_active=True)
        award_cab(db, uid, 150, "receipt_scan")
        db.flush()
        # Default behavior unchanged for non-BP reasons.
        assert get_cab_earned_season(db, uid, season_id) == 150

    def test_award_cab_explicit_apply_false_skips_progress(self, db):
        """Explicit ``apply_to_bp_progress=False`` skips the season progress UPSERT."""
        from repositories.cab_repository import get_cab_earned_season

        uid = make_user(db)
        season_id = make_season(db, is_active=True)
        award_cab(
            db,
            uid,
            250,
            "battlepass_milestone",
            apply_to_bp_progress=False,
        )
        db.flush()
        assert get_cab_earned_season(db, uid, season_id) == 0
