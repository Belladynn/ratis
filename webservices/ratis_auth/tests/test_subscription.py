"""TDD — endpoints abonnement (POST/GET/DELETE /account/subscription + webhook Stripe)."""

from decimal import Decimal
from unittest.mock import MagicMock, patch

from _auth_helpers import oauth_signup
from ratis_core.models.rewards import DiscountCampaign, StripeWebhookEvent, Subscription

# ─────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────


def _register_and_token(client, email="sub_user@example.com", password="pass12345"):
    """Mint a user + access_token via OAuth. ``password`` kept for call-site
    compatibility but ignored — Ratis is OAuth-only."""
    return oauth_signup(client, email)["access_token"]


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


def _fake_stripe_session(session_id="cs_test_123", url="https://checkout.stripe.com/pay/cs_test_123"):
    session = MagicMock()
    session.id = session_id
    session.url = url
    return session


def _stripe_event(event_type, session_id, metadata=None, amount_total=1199, currency="eur", event_id=None):
    """Build a minimal Stripe Event object for webhook tests.

    ``event_id`` defaults to ``"evt_" + session_id`` to produce a
    unique, stable Stripe event ID for the idempotency dedup table.
    """
    obj = {
        "id": session_id,
        "object": "checkout.session",
        "amount_total": amount_total,
        "currency": currency,
        "payment_intent": "pi_test_123",
        "metadata": metadata or {},
    }
    event = MagicMock()
    event.id = event_id if event_id is not None else f"evt_{session_id}"
    event.type = event_type
    event.data = MagicMock()
    event.data.object = MagicMock(**obj)
    event.data.object.__getitem__ = lambda self, k: obj[k]
    event.data.object.get = lambda k, d=None: obj.get(k, d)
    return event


# ─────────────────────────────────────────────
# POST /account/subscription
# ─────────────────────────────────────────────


def test_subscribe_monthly_creates_pending(client, db):
    token = _register_and_token(client, "sub_monthly@example.com")
    fake_session = _fake_stripe_session()

    with patch("services.subscription_service.stripe") as mock_stripe:
        mock_stripe.checkout.Session.create.return_value = fake_session
        resp = client.post(
            "/api/v1/account/subscription",
            json={"plan": "monthly"},
            headers=_auth(token),
        )

    assert resp.status_code == 201
    data = resp.json()
    assert "checkout_url" in data

    sub = db.query(Subscription).filter(Subscription.stripe_session_id == "cs_test_123").first()
    assert sub is not None
    assert sub.status == "pending"
    assert sub.plan == "monthly"


def test_subscribe_annual_creates_pending(client, db):
    token = _register_and_token(client, "sub_annual@example.com")
    fake_session = _fake_stripe_session("cs_annual_456", "https://checkout.stripe.com/pay/cs_annual_456")

    with patch("services.subscription_service.stripe") as mock_stripe:
        mock_stripe.checkout.Session.create.return_value = fake_session
        resp = client.post(
            "/api/v1/account/subscription",
            json={"plan": "annual"},
            headers=_auth(token),
        )

    assert resp.status_code == 201


def test_subscribe_attaches_metadata_at_session_create(client, db):
    """Metadata (subscription_id) is passed directly to Session.create — no separate
    Session.modify call, so the session never exists without its metadata."""
    token = _register_and_token(client, "sub_meta@example.com")
    fake_session = _fake_stripe_session("cs_meta_001")

    with patch("services.subscription_service.stripe") as mock_stripe:
        mock_stripe.checkout.Session.create.return_value = fake_session
        resp = client.post(
            "/api/v1/account/subscription",
            json={"plan": "monthly"},
            headers=_auth(token),
        )

    assert resp.status_code == 201
    sub = db.query(Subscription).filter(Subscription.stripe_session_id == "cs_meta_001").first()
    assert sub is not None

    # Session.create was called with metadata carrying subscription_id
    create_kwargs = mock_stripe.checkout.Session.create.call_args.kwargs
    assert create_kwargs["metadata"] == {"subscription_id": str(sub.id)}
    # The gratuitous Session.modify call is gone — no failure window
    mock_stripe.checkout.Session.modify.assert_not_called()


def test_subscribe_invalid_plan(client):
    token = _register_and_token(client, "sub_badplan@example.com")
    resp = client.post(
        "/api/v1/account/subscription",
        json={"plan": "weekly"},
        headers=_auth(token),
    )
    assert resp.status_code == 422


def test_subscribe_unauthenticated(client):
    resp = client.post("/api/v1/account/subscription", json={"plan": "monthly"})
    assert resp.status_code == 401


def test_subscribe_already_active_rejected(client, db):
    """Un utilisateur avec un abonnement actif ne peut pas en créer un nouveau."""
    token = _register_and_token(client, "sub_already@example.com")
    fake_session = _fake_stripe_session("cs_already_001")

    with patch("services.subscription_service.stripe") as mock_stripe:
        mock_stripe.checkout.Session.create.return_value = fake_session
        client.post(
            "/api/v1/account/subscription",
            json={"plan": "monthly"},
            headers=_auth(token),
        )

    # Activate the subscription manually
    sub = db.query(Subscription).filter(Subscription.stripe_session_id == "cs_already_001").first()
    sub.status = "active"
    sub.payment_ref = "pi_test_already"
    db.flush()
    db.commit()  # commit the manual activation before calling the endpoint

    # Second attempt should fail
    with patch("services.subscription_service.stripe") as mock_stripe:
        mock_stripe.checkout.Session.create.return_value = _fake_stripe_session("cs_already_002")
        resp = client.post(
            "/api/v1/account/subscription",
            json={"plan": "annual"},
            headers=_auth(token),
        )
    assert resp.status_code == 400
    assert resp.json()["detail"] == "already_subscribed"


def test_subscribe_invalid_discount_code(client, db):
    token = _register_and_token(client, "sub_badcode@example.com")
    with patch("services.subscription_service.stripe"):
        resp = client.post(
            "/api/v1/account/subscription",
            json={"plan": "monthly", "discount_campaign_code": "INVALID99"},
            headers=_auth(token),
        )
    assert resp.status_code == 400
    assert resp.json()["detail"] == "discount_code_invalid"


def test_subscribe_exhausted_discount_code(client, db):
    campaign = DiscountCampaign(
        code="EXHAUSTED",
        label="Test",
        type="percentage",
        value=Decimal("10"),
        max_uses=5,
        uses_count=5,
        is_public=True,
    )
    db.add(campaign)
    db.flush()

    token = _register_and_token(client, "sub_exhausted@example.com")
    with patch("services.subscription_service.stripe"):
        resp = client.post(
            "/api/v1/account/subscription",
            json={"plan": "monthly", "discount_campaign_code": "EXHAUSTED"},
            headers=_auth(token),
        )
    assert resp.status_code == 400
    assert resp.json()["detail"] == "discount_code_exhausted"


def test_subscribe_expired_discount_code(client, db):
    import datetime as dt

    campaign = DiscountCampaign(
        code="EXPIRED10",
        label="Test expired",
        type="percentage",
        value=Decimal("10"),
        valid_until=dt.datetime(2020, 1, 1, tzinfo=dt.UTC),
        is_public=True,
    )
    db.add(campaign)
    db.flush()

    token = _register_and_token(client, "sub_expired@example.com")
    with patch("services.subscription_service.stripe"):
        resp = client.post(
            "/api/v1/account/subscription",
            json={"plan": "monthly", "discount_campaign_code": "EXPIRED10"},
            headers=_auth(token),
        )
    assert resp.status_code == 400
    assert resp.json()["detail"] == "discount_code_expired"


def test_subscribe_private_code_rejected(client, db):
    campaign = DiscountCampaign(
        code="PRIVATE01",
        label="Private",
        type="fixed",
        value=Decimal("2"),
        is_public=False,
    )
    db.add(campaign)
    db.flush()

    token = _register_and_token(client, "sub_private@example.com")
    with patch("services.subscription_service.stripe"):
        resp = client.post(
            "/api/v1/account/subscription",
            json={"plan": "monthly", "discount_campaign_code": "PRIVATE01"},
            headers=_auth(token),
        )
    assert resp.status_code == 400
    assert resp.json()["detail"] == "discount_code_invalid"


def test_subscribe_with_valid_discount_code(client, db):
    campaign = DiscountCampaign(
        code="PROMO20",
        label="20% off",
        type="percentage",
        value=Decimal("20"),
        is_public=True,
    )
    db.add(campaign)
    db.flush()

    token = _register_and_token(client, "sub_promo@example.com")
    fake_session = _fake_stripe_session("cs_promo_789")

    with patch("services.subscription_service.stripe") as mock_stripe:
        mock_stripe.checkout.Session.create.return_value = fake_session
        resp = client.post(
            "/api/v1/account/subscription",
            json={"plan": "monthly", "discount_campaign_code": "PROMO20"},
            headers=_auth(token),
        )

    assert resp.status_code == 201
    sub = db.query(Subscription).filter(Subscription.stripe_session_id == "cs_promo_789").first()
    assert sub is not None
    assert sub.discount_campaign_code == "PROMO20"
    assert sub.discount_amount is not None


# ─────────────────────────────────────────────
# GET /account/subscription
# ─────────────────────────────────────────────


def test_get_subscription_none_returns_404(client):
    token = _register_and_token(client, "sub_get_none@example.com")
    resp = client.get("/api/v1/account/subscription", headers=_auth(token))
    assert resp.status_code == 404
    assert resp.json()["detail"] == "no_active_subscription"


def test_get_subscription_unauthenticated(client):
    resp = client.get("/api/v1/account/subscription")
    assert resp.status_code == 401


# ─────────────────────────────────────────────
# DELETE /account/subscription
# ─────────────────────────────────────────────


def test_cancel_subscription_no_active(client):
    token = _register_and_token(client, "sub_cancel_none@example.com")
    resp = client.delete("/api/v1/account/subscription", headers=_auth(token))
    assert resp.status_code == 404
    assert resp.json()["detail"] == "no_active_subscription"


def test_cancel_subscription_unauthenticated(client):
    resp = client.delete("/api/v1/account/subscription")
    assert resp.status_code == 401


# ─────────────────────────────────────────────
# POST /webhooks/stripe
# ─────────────────────────────────────────────


def test_webhook_checkout_completed_activates_subscription(client, db):
    token = _register_and_token(client, "sub_webhook@example.com")
    fake_session = _fake_stripe_session("cs_webhook_001")

    with patch("services.subscription_service.stripe") as mock_stripe:
        mock_stripe.checkout.Session.create.return_value = fake_session
        client.post(
            "/api/v1/account/subscription",
            json={"plan": "monthly"},
            headers=_auth(token),
        )

    sub = db.query(Subscription).filter(Subscription.stripe_session_id == "cs_webhook_001").first()
    assert sub.status == "pending"

    event = _stripe_event(
        "checkout.session.completed",
        "cs_webhook_001",
        metadata={"subscription_id": str(sub.id)},
        amount_total=1199,
        currency="eur",
    )

    with (
        patch("routes.webhooks.stripe") as mock_stripe,
        patch("routes.webhooks.trigger_referral_reward"),
    ):
        mock_stripe.Webhook.construct_event.return_value = event
        resp = client.post(
            "/webhooks/stripe",
            content=b"{}",
            headers={"stripe-signature": "t=1,v1=sig"},
        )

    assert resp.status_code == 200
    db.expire(sub)
    assert sub.status == "active"
    assert sub.paid_with == "stripe"
    assert sub.payment_ref == "pi_test_123"


def test_webhook_checkout_completed_underpayment_flags_sentry(client, db):
    """amount_total below the expected plan price logs a warning + Sentry event."""
    token = _register_and_token(client, "sub_underpay@example.com")
    fake_session = _fake_stripe_session("cs_underpay_001")

    with patch("services.subscription_service.stripe") as mock_stripe:
        mock_stripe.checkout.Session.create.return_value = fake_session
        client.post(
            "/api/v1/account/subscription",
            json={"plan": "monthly"},
            headers=_auth(token),
        )

    sub = db.query(Subscription).filter(Subscription.stripe_session_id == "cs_underpay_001").first()
    expected_cents = int((sub.price * 100).to_integral_value())

    event = _stripe_event(
        "checkout.session.completed",
        "cs_underpay_001",
        metadata={"subscription_id": str(sub.id)},
        amount_total=expected_cents - 500,  # underpaid by 5 EUR
        currency="eur",
    )

    with (
        patch("routes.webhooks.stripe") as mock_stripe,
        patch("routes.webhooks.trigger_referral_reward"),
        patch("services.subscription_service.sentry_sdk.capture_message") as mock_capture,
    ):
        mock_stripe.Webhook.construct_event.return_value = event
        resp = client.post(
            "/webhooks/stripe",
            content=b"{}",
            headers={"stripe-signature": "t=1,v1=sig"},
        )

    assert resp.status_code == 200
    # Underpayment must not silently activate without a flagged Sentry event.
    assert mock_capture.called
    db.expire(sub)
    assert sub.status == "active"  # activation not blocked — only flagged


def test_webhook_checkout_completed_correct_amount_no_sentry(client, db):
    """A correctly-paid checkout must NOT emit a Sentry mismatch event."""
    token = _register_and_token(client, "sub_correct@example.com")
    fake_session = _fake_stripe_session("cs_correct_001")

    with patch("services.subscription_service.stripe") as mock_stripe:
        mock_stripe.checkout.Session.create.return_value = fake_session
        client.post(
            "/api/v1/account/subscription",
            json={"plan": "monthly"},
            headers=_auth(token),
        )

    sub = db.query(Subscription).filter(Subscription.stripe_session_id == "cs_correct_001").first()
    expected_cents = int((sub.price * 100).to_integral_value())

    event = _stripe_event(
        "checkout.session.completed",
        "cs_correct_001",
        metadata={"subscription_id": str(sub.id)},
        amount_total=expected_cents,
        currency="eur",
    )

    with (
        patch("routes.webhooks.stripe") as mock_stripe,
        patch("routes.webhooks.trigger_referral_reward"),
        patch("services.subscription_service.sentry_sdk.capture_message") as mock_capture,
    ):
        mock_stripe.Webhook.construct_event.return_value = event
        resp = client.post(
            "/webhooks/stripe",
            content=b"{}",
            headers={"stripe-signature": "t=1,v1=sig"},
        )

    assert resp.status_code == 200
    assert not mock_capture.called


def test_webhook_checkout_expired_cancels_pending(client, db):
    """checkout.session.expired (session_obj.id = cs_...) cancels the pending subscription."""
    token = _register_and_token(client, "sub_wh_fail@example.com")
    fake_session = _fake_stripe_session("cs_fail_002")

    with patch("services.subscription_service.stripe") as mock_stripe:
        mock_stripe.checkout.Session.create.return_value = fake_session
        client.post(
            "/api/v1/account/subscription",
            json={"plan": "monthly"},
            headers=_auth(token),
        )

    sub = db.query(Subscription).filter(Subscription.stripe_session_id == "cs_fail_002").first()

    event = MagicMock()
    event.id = "evt_cs_fail_002"
    event.type = "checkout.session.expired"
    event.data.object.get = lambda k, d=None: "cs_fail_002" if k == "id" else d
    event.data.object.__getitem__ = lambda self, k: "cs_fail_002"

    with patch("routes.webhooks.stripe") as mock_stripe:
        mock_stripe.Webhook.construct_event.return_value = event
        resp = client.post(
            "/webhooks/stripe",
            content=b"{}",
            headers={"stripe-signature": "t=1,v1=sig"},
        )

    assert resp.status_code == 200
    db.expire(sub)
    assert sub.status == "cancelled"


def test_webhook_checkout_completed_annual_triggers_gift_card(client, db):
    """Annual subscription → trigger_annual_gift_card is enqueued as background task."""
    token = _register_and_token(client, "sub_annual_wh@example.com")
    fake_session = _fake_stripe_session("cs_annual_wh_001")

    with patch("services.subscription_service.stripe") as mock_stripe:
        mock_stripe.checkout.Session.create.return_value = fake_session
        client.post(
            "/api/v1/account/subscription",
            json={"plan": "annual"},
            headers=_auth(token),
        )

    sub = db.query(Subscription).filter(Subscription.stripe_session_id == "cs_annual_wh_001").first()
    assert sub is not None

    event = _stripe_event(
        "checkout.session.completed",
        "cs_annual_wh_001",
        metadata={"subscription_id": str(sub.id)},
        amount_total=11900,
        currency="eur",
    )

    with (
        patch("routes.webhooks.stripe") as mock_stripe,
        patch("routes.webhooks.trigger_referral_reward"),
        patch("routes.webhooks.trigger_annual_gift_card") as mock_gift_card,
    ):
        mock_stripe.Webhook.construct_event.return_value = event
        resp = client.post(
            "/webhooks/stripe",
            content=b"{}",
            headers={"stripe-signature": "t=1,v1=sig"},
        )

    assert resp.status_code == 200
    db.expire(sub)
    assert sub.status == "active"
    mock_gift_card.assert_called_once_with(sub.user_id, "cs_annual_wh_001")


def test_webhook_checkout_completed_monthly_no_gift_card(client, db):
    """Monthly subscription → trigger_annual_gift_card is NOT called."""
    token = _register_and_token(client, "sub_monthly_wh@example.com")
    fake_session = _fake_stripe_session("cs_monthly_wh_001")

    with patch("services.subscription_service.stripe") as mock_stripe:
        mock_stripe.checkout.Session.create.return_value = fake_session
        client.post(
            "/api/v1/account/subscription",
            json={"plan": "monthly"},
            headers=_auth(token),
        )

    sub = db.query(Subscription).filter(Subscription.stripe_session_id == "cs_monthly_wh_001").first()

    event = _stripe_event(
        "checkout.session.completed",
        "cs_monthly_wh_001",
        metadata={"subscription_id": str(sub.id)},
        amount_total=1199,
        currency="eur",
    )

    with (
        patch("routes.webhooks.stripe") as mock_stripe,
        patch("routes.webhooks.trigger_referral_reward"),
        patch("routes.webhooks.trigger_annual_gift_card") as mock_gift_card,
    ):
        mock_stripe.Webhook.construct_event.return_value = event
        resp = client.post(
            "/webhooks/stripe",
            content=b"{}",
            headers={"stripe-signature": "t=1,v1=sig"},
        )

    assert resp.status_code == 200
    mock_gift_card.assert_not_called()


def test_webhook_downstream_reward_failure_is_captured(client, db):
    """A failure while wiring up a downstream reward (annual gift card) must
    NOT be silently swallowed — it is logged with exc_info AND reported to
    Sentry, while the webhook still returns 200 so Stripe does not
    retry-storm."""
    token = _register_and_token(client, "sub_reward_fail@example.com")
    fake_session = _fake_stripe_session("cs_reward_fail_001")

    with patch("services.subscription_service.stripe") as mock_stripe:
        mock_stripe.checkout.Session.create.return_value = fake_session
        client.post(
            "/api/v1/account/subscription",
            json={"plan": "annual"},
            headers=_auth(token),
        )

    sub = db.query(Subscription).filter(Subscription.stripe_session_id == "cs_reward_fail_001").first()

    event = _stripe_event(
        "checkout.session.completed",
        "cs_reward_fail_001",
        metadata={"subscription_id": str(sub.id)},
        amount_total=11900,
        currency="eur",
    )

    # Simulate the gift-card reward step failing — BackgroundTasks.add_task
    # raising stands in for any failure inside the reward-wiring try block.
    from fastapi import BackgroundTasks

    with (
        patch("routes.webhooks.stripe") as mock_stripe,
        patch("routes.webhooks.trigger_referral_reward"),
        patch("routes.webhooks.trigger_annual_gift_card"),
        patch.object(
            BackgroundTasks,
            "add_task",
            side_effect=RuntimeError("gift card provider down"),
        ),
        patch("routes.webhooks.sentry_sdk.capture_exception") as mock_capture,
        patch("routes.webhooks.log.error") as mock_log_error,
    ):
        mock_stripe.Webhook.construct_event.return_value = event
        resp = client.post(
            "/webhooks/stripe",
            content=b"{}",
            headers={"stripe-signature": "t=1,v1=sig"},
        )

    # Still 200 — avoid Stripe retry storms
    assert resp.status_code == 200
    # The downstream failure left a trace: Sentry + error log with exc_info
    assert mock_capture.called
    assert mock_log_error.called
    assert mock_log_error.call_args.kwargs.get("exc_info") is True


def test_webhook_unknown_event_ignored(client):
    event = MagicMock()
    event.id = "evt_unknown_001"
    event.type = "some.unknown.event"

    with patch("routes.webhooks.stripe") as mock_stripe:
        mock_stripe.Webhook.construct_event.return_value = event
        resp = client.post(
            "/webhooks/stripe",
            content=b"{}",
            headers={"stripe-signature": "t=1,v1=sig"},
        )

    assert resp.status_code == 200


def test_webhook_invalid_signature(client):
    from stripe._error import SignatureVerificationError as StripeSignatureError

    with patch(
        "routes.webhooks.stripe.Webhook.construct_event", side_effect=StripeSignatureError("bad sig", "t=1,v1=bad")
    ):
        resp = client.post(
            "/webhooks/stripe",
            content=b"{}",
            headers={"stripe-signature": "t=1,v1=bad"},
        )

    assert resp.status_code == 400
    assert resp.json()["detail"] == "invalid_stripe_signature"


# ─────────────────────────────────────────────
# Stripe webhook idempotency (audit C2)
# ─────────────────────────────────────────────


def test_claim_stripe_event_returns_true_then_false(db):
    """First claim returns True; second claim with same event_id returns False."""
    from services.subscription_service import claim_stripe_event

    result1 = claim_stripe_event(db, "evt_test_idempotency", "checkout.session.completed")
    result2 = claim_stripe_event(db, "evt_test_idempotency", "checkout.session.completed")

    assert result1 is True
    assert result2 is False

    # Exactly one row in stripe_webhook_events
    rows = db.query(StripeWebhookEvent).filter(StripeWebhookEvent.event_id == "evt_test_idempotency").all()
    assert len(rows) == 1


def test_stripe_webhook_duplicate_event_is_idempotent(client, db):
    """Second POST of the same event_id does NOT re-enqueue trigger_annual_gift_card."""
    token = _register_and_token(client, "sub_dedup_annual@example.com")
    fake_session = _fake_stripe_session("cs_dedup_annual_001")

    with patch("services.subscription_service.stripe") as mock_stripe:
        mock_stripe.checkout.Session.create.return_value = fake_session
        client.post(
            "/api/v1/account/subscription",
            json={"plan": "annual"},
            headers=_auth(token),
        )

    sub = db.query(Subscription).filter_by(stripe_session_id="cs_dedup_annual_001").first()
    assert sub is not None

    event = _stripe_event(
        "checkout.session.completed",
        "cs_dedup_annual_001",
        metadata={"subscription_id": str(sub.id)},
        amount_total=11900,
        currency="eur",
    )
    # Give the event a stable ID so both calls share the same dedup key
    event.id = "evt_dedup_fixed_001"

    with (
        patch("routes.webhooks.stripe") as mock_stripe,
        patch("routes.webhooks.trigger_referral_reward"),
        patch("routes.webhooks.trigger_annual_gift_card") as mock_gift_card,
    ):
        mock_stripe.Webhook.construct_event.return_value = event

        resp1 = client.post(
            "/webhooks/stripe",
            content=b"{}",
            headers={"stripe-signature": "t=1,v1=sig"},
        )
        resp2 = client.post(
            "/webhooks/stripe",
            content=b"{}",
            headers={"stripe-signature": "t=1,v1=sig"},
        )

    assert resp1.status_code == 200
    assert resp2.status_code == 200
    # Annual gift card must have been enqueued exactly once — NOT twice
    assert mock_gift_card.call_count == 1
