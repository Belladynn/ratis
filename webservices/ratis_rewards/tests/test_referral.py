"""
Tests for POST /rewards/referral/trigger.

Invariants (cf ARCH_referral.md) :
- X (referrer) is credited, not Y (referred). Pre-fix bug corrected here.
- Reward is flat : no streak / coverage / subscription multipliers apply.
- Idempotent : re-triggering for the same referred_user_id is a no-op.
- Silent no-op if the referred user has no referral link (Stripe webhook
  fired for a regular subscriber).
"""

from __future__ import annotations

import uuid

from repositories.cab_repository import get_balance
from sqlalchemy import text

from tests.conftest import make_gift_card_brand, make_user


def _seed_referral(db, referrer_id: uuid.UUID, referred_id: uuid.UUID, code: str = "TESTCODE") -> uuid.UUID:
    """Create a referral_codes row for X and a referral_uses row linking Y."""
    referral_id = uuid.uuid4()
    use_id = uuid.uuid4()
    db.execute(
        text(
            "INSERT INTO referral_codes (id, user_id, code, type, created_at) VALUES (:id, :uid, :code, 'user', now())"
        ),
        {"id": referral_id, "uid": referrer_id, "code": code.upper()},
    )
    db.execute(
        text(
            "INSERT INTO referral_uses (id, referral_id, referred_user_id, created_at) VALUES (:id, :rid, :ruid, now())"
        ),
        {"id": use_id, "rid": referral_id, "ruid": referred_id},
    )
    db.flush()
    return use_id


class TestReferralTrigger:
    def test_monthly_plan_credits_referrer_not_referred(self, db, client):
        referrer = make_user(db)
        referred = make_user(db)
        _seed_referral(db, referrer, referred)

        resp = client.post(
            "/api/v1/rewards/referral/trigger",
            json={"referred_user_id": str(referred), "plan": "monthly"},
        )
        assert resp.status_code == 200
        assert resp.json() == {"ok": True}
        # X (referrer) is credited, Y (referred) gets nothing from this flow.
        assert get_balance(db, referrer) == 500
        assert get_balance(db, referred) == 0

    def test_annual_plan_credits_referrer_with_750(self, db, client):
        referrer = make_user(db)
        referred = make_user(db)
        _seed_referral(db, referrer, referred)

        resp = client.post(
            "/api/v1/rewards/referral/trigger",
            json={"referred_user_id": str(referred), "plan": "annual"},
        )
        assert resp.status_code == 200
        assert get_balance(db, referrer) == 750
        assert get_balance(db, referred) == 0

    def test_inserts_cab_transaction_with_correct_reference(self, db, client):
        referrer = make_user(db)
        referred = make_user(db)
        use_id = _seed_referral(db, referrer, referred)

        client.post(
            "/api/v1/rewards/referral/trigger",
            json={"referred_user_id": str(referred), "plan": "monthly"},
        )

        row = db.execute(
            text(
                "SELECT direction, amount, reason, reference_id, reference_type "
                "FROM cabecoin_transactions WHERE user_id = :uid"
            ),
            {"uid": referrer},
        ).first()
        assert row is not None
        assert row.direction == "credit"
        assert row.amount == 500
        assert row.reason == "referral"
        assert row.reference_id == use_id
        assert row.reference_type == "referral"

    def test_marks_referral_use_as_rewarded(self, db, client):
        referrer = make_user(db)
        referred = make_user(db)
        _seed_referral(db, referrer, referred)

        client.post(
            "/api/v1/rewards/referral/trigger",
            json={"referred_user_id": str(referred), "plan": "monthly"},
        )

        row = db.execute(
            text("SELECT plan, rewarded_at FROM referral_uses WHERE referred_user_id = :ruid"),
            {"ruid": referred},
        ).first()
        assert row is not None
        assert row.plan == "monthly"
        assert row.rewarded_at is not None

    def test_idempotent_second_trigger_is_noop(self, db, client):
        referrer = make_user(db)
        referred = make_user(db)
        _seed_referral(db, referrer, referred)

        # First trigger — X gets 500
        client.post(
            "/api/v1/rewards/referral/trigger",
            json={"referred_user_id": str(referred), "plan": "monthly"},
        )
        assert get_balance(db, referrer) == 500

        # Second trigger — no-op, balance unchanged
        resp = client.post(
            "/api/v1/rewards/referral/trigger",
            json={"referred_user_id": str(referred), "plan": "monthly"},
        )
        assert resp.status_code == 200
        assert get_balance(db, referrer) == 500  # still 500, not 1000

    def test_no_referral_link_silent_noop(self, db, client):
        """Subscriber without any referral link — route returns 200, no CAB."""
        lone_subscriber = make_user(db)

        resp = client.post(
            "/api/v1/rewards/referral/trigger",
            json={"referred_user_id": str(lone_subscriber), "plan": "monthly"},
        )
        assert resp.status_code == 200
        assert get_balance(db, lone_subscriber) == 0

    def test_reward_is_flat_no_streak_multiplier(self, db, client):
        """Even if X has a streak active, the referral reward is flat 500/750."""
        referrer = make_user(db)
        referred = make_user(db)
        _seed_referral(db, referrer, referred)
        # Give X a 10-day streak (would be +50% multiplier on regular CAB awards)
        db.execute(
            text(
                "INSERT INTO user_streaks "
                "(user_id, current_streak_days, last_fed_at, food_reserves, timezone, updated_at) "
                "VALUES (:uid, 10, now()::date, 0, 'UTC', now())"
            ),
            {"uid": referrer},
        )
        db.flush()

        client.post(
            "/api/v1/rewards/referral/trigger",
            json={"referred_user_id": str(referred), "plan": "monthly"},
        )

        # Exactly 500 — no +50% multiplier applied
        assert get_balance(db, referrer) == 500

    def test_unknown_plan_returns_422(self, db, client):
        # Pydantic validation rejects 'premium' before reaching the handler —
        # no DB interaction needed.
        resp = client.post(
            "/api/v1/rewards/referral/trigger",
            json={"referred_user_id": str(uuid.uuid4()), "plan": "premium"},
        )
        assert resp.status_code == 422

    # ── Gift card enqueue (Bloc C) ────────────────────────────────────────

    def test_enqueues_gift_card_order_with_eligible_at(self, db, client, monkeypatch):
        """After a referral reward, a pending gift_card_orders row is created
        with eligible_at = NOW() + eligibility_delay_days, source_type =
        'referral_reward', source_ref_id = referral_use.id."""
        brand_id = make_gift_card_brand(db, name="Runa Default")
        # Inject the brand id into the app config — done via monkeypatch on the
        # app state (not the on-disk settings file).
        import main

        main.app.state.cfg["referral"]["gift_card_brand_id"] = str(brand_id)

        referrer = make_user(db)
        referred = make_user(db)
        use_id = _seed_referral(db, referrer, referred)

        client.post(
            "/api/v1/rewards/referral/trigger",
            json={"referred_user_id": str(referred), "plan": "monthly"},
        )

        row = db.execute(
            text(
                "SELECT user_id, brand_id, denomination, status, source_type, "
                "       source_ref_id, eligible_at, issued_at "
                "FROM gift_card_orders WHERE source_ref_id = :ref"
            ),
            {"ref": str(use_id)},
        ).first()
        assert row is not None
        assert row.user_id == referrer  # X, not Y
        assert row.brand_id == brand_id
        assert row.denomination == 500  # 5€ flat per settings
        assert row.status == "pending"
        assert row.source_type == "referral_reward"
        assert row.issued_at is None
        # eligible_at ~= now + 30 days (default from settings)
        assert row.eligible_at is not None

    def test_gift_card_skipped_when_no_brand_configured(self, db, client):
        """If gift_card_brand_id is None (bootstrap state), no gift card row
        is inserted but CAB still credited — graceful degradation."""
        import main

        original = main.app.state.cfg["referral"].get("gift_card_brand_id")
        main.app.state.cfg["referral"]["gift_card_brand_id"] = None
        try:
            referrer = make_user(db)
            referred = make_user(db)
            _seed_referral(db, referrer, referred)

            client.post(
                "/api/v1/rewards/referral/trigger",
                json={"referred_user_id": str(referred), "plan": "monthly"},
            )

            assert get_balance(db, referrer) == 500  # CAB still granted
            count = db.execute(
                text("SELECT COUNT(*) FROM gift_card_orders WHERE user_id = :uid"),
                {"uid": referrer},
            ).scalar()
            assert count == 0
        finally:
            main.app.state.cfg["referral"]["gift_card_brand_id"] = original

    def test_gift_card_idempotent_on_retrigger(self, db, client):
        """Second trigger for the same filleul does not create a second gift
        card row (UNIQUE source_type + source_ref_id)."""
        brand_id = make_gift_card_brand(db, name="Runa Default")
        import main

        main.app.state.cfg["referral"]["gift_card_brand_id"] = str(brand_id)

        referrer = make_user(db)
        referred = make_user(db)
        _seed_referral(db, referrer, referred)

        for _ in range(2):
            client.post(
                "/api/v1/rewards/referral/trigger",
                json={"referred_user_id": str(referred), "plan": "monthly"},
            )

        count = db.execute(
            text("SELECT COUNT(*) FROM gift_card_orders WHERE user_id = :uid"),
            {"uid": referrer},
        ).scalar()
        assert count == 1

    def test_missing_auth_returns_403(self, db, raw_client):
        uid = make_user(db)
        resp = raw_client.post(
            "/api/v1/rewards/referral/trigger",
            json={"referred_user_id": str(uid), "plan": "monthly"},
        )
        assert resp.status_code == 403


class TestReferralSignupBonus:
    """POST /rewards/referral/signup-bonus — called by ratis_auth register()."""

    def test_awards_150_cab_to_referred_user(self, db, client):
        referrer = make_user(db)
        referred = make_user(db)
        _seed_referral(db, referrer, referred)

        resp = client.post(
            "/api/v1/rewards/referral/signup-bonus",
            json={"referred_user_id": str(referred)},
        )
        assert resp.status_code == 200
        assert resp.json()["awarded"] is True
        assert get_balance(db, referred) == 150
        # X not affected at this stage (only subscription triggers X reward)
        assert get_balance(db, referrer) == 0

    def test_no_referral_link_silent_noop(self, db, client):
        """If user has no referral_uses row, the endpoint returns 200 with awarded=False."""
        lone_user = make_user(db)

        resp = client.post(
            "/api/v1/rewards/referral/signup-bonus",
            json={"referred_user_id": str(lone_user)},
        )
        assert resp.status_code == 200
        assert resp.json()["awarded"] is False
        assert get_balance(db, lone_user) == 0

    def test_idempotent_second_call_noop(self, db, client):
        referrer = make_user(db)
        referred = make_user(db)
        _seed_referral(db, referrer, referred)

        client.post(
            "/api/v1/rewards/referral/signup-bonus",
            json={"referred_user_id": str(referred)},
        )
        assert get_balance(db, referred) == 150

        # Second call : no-op, balance unchanged
        resp = client.post(
            "/api/v1/rewards/referral/signup-bonus",
            json={"referred_user_id": str(referred)},
        )
        assert resp.json()["awarded"] is False
        assert get_balance(db, referred) == 150

    def test_bonus_is_flat_no_streak_multiplier(self, db, client):
        referrer = make_user(db)
        referred = make_user(db)
        _seed_referral(db, referrer, referred)
        # Give Y a streak (would give +50% on regular awards)
        db.execute(
            text(
                "INSERT INTO user_streaks (user_id, current_streak_days, last_fed_at, "
                "food_reserves, timezone, updated_at) "
                "VALUES (:uid, 10, now()::date, 0, 'UTC', now())"
            ),
            {"uid": referred},
        )
        db.flush()

        client.post(
            "/api/v1/rewards/referral/signup-bonus",
            json={"referred_user_id": str(referred)},
        )
        # Exactly 150, no +50% multiplier
        assert get_balance(db, referred) == 150


# ===========================================================================
# Achievements V1 — hook in referral_service.handle_subscription_referral (PR4)
# ===========================================================================


class TestAchievementHookReferralPaid:
    """`handle_subscription_referral` must fire `check_achievements` with
    event_type='referral_paid' for the REFERRER (X) when a paid plan is
    rewarded for the first time. Idempotent re-trigger (already rewarded)
    must NOT re-fire the hook.
    """

    def _spy(self, monkeypatch):
        from services import achievement_service

        calls: list[dict] = []
        original = achievement_service.check_achievements

        def wrapper(*args, **kwargs):
            calls.append({"args": args, "kwargs": kwargs})
            return original(*args, **kwargs)

        monkeypatch.setattr(achievement_service, "check_achievements", wrapper)
        return calls

    def test_referral_trigger_fires_referral_paid_for_referrer(self, db, client, monkeypatch):
        referrer = make_user(db)
        referred = make_user(db)
        _seed_referral(db, referrer, referred)

        calls = self._spy(monkeypatch)
        resp = client.post(
            "/api/v1/rewards/referral/trigger",
            json={"referred_user_id": str(referred), "plan": "monthly"},
        )
        assert resp.status_code == 200
        ref_calls = [c for c in calls if c["kwargs"].get("event_type") == "referral_paid"]
        assert len(ref_calls) == 1
        # Hook fires for the REFERRER (X), never the referred (Y).
        assert ref_calls[0]["kwargs"].get("user_id") == referrer

    def test_idempotent_replay_does_not_refire(self, db, client, monkeypatch):
        referrer = make_user(db)
        referred = make_user(db)
        _seed_referral(db, referrer, referred)
        # First trigger — primes the rewarded state.
        client.post(
            "/api/v1/rewards/referral/trigger",
            json={"referred_user_id": str(referred), "plan": "monthly"},
        )

        calls = self._spy(monkeypatch)
        resp = client.post(
            "/api/v1/rewards/referral/trigger",
            json={"referred_user_id": str(referred), "plan": "monthly"},
        )
        assert resp.status_code == 200
        ref_calls = [c for c in calls if c["kwargs"].get("event_type") == "referral_paid"]
        assert ref_calls == []
