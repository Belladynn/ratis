"""TDD — Public settings endpoint GET /api/v1/rewards/settings/public.

A non-authenticated read endpoint that returns a **whitelisted** subset of
runtime settings — the values needed by the mobile client to compute
display-time derivations (e.g. jar prestige price, gift-card cap).

The whitelist lives in :mod:`services.public_settings_service` so the
boundary stays explicit (R33 — solution propre) : adding a key requires
amending the whitelist + a test, never a route-level change.

Cf F-10 in the V1.1 usage-stats sprint.
"""

from __future__ import annotations


def test_returns_whitelisted_keys_only(client):
    """Default settings → response contains exactly the whitelisted dotted keys."""
    resp = client.get("/api/v1/rewards/settings/public")
    assert resp.status_code == 200
    data = resp.json()
    expected_keys = {
        "pipeline.jar.monthly_subscription_price_cents",
        "boutique.cap_annual_cents",
        "boutique.cap_per_card_cents",
        "boutique.cap_daily_cents",
        "boutique.cap_weekly_cents",
        "boutique.ratio_cab_per_eur",
        "boutique.allowed_denominations_cents",
        "gift_cards.annual_warning_threshold_cents",
    }
    assert set(data.keys()) == expected_keys


def test_values_match_seeded_settings(client):
    """Each whitelisted key returns the value from app_settings (= ratis_settings.json seed)."""
    resp = client.get("/api/v1/rewards/settings/public")
    data = resp.json()
    # Spot-check against ratis_core/config/ratis_settings.json seed.
    assert data["pipeline.jar.monthly_subscription_price_cents"] == 999
    assert data["boutique.cap_annual_cents"] == 119900
    assert data["boutique.ratio_cab_per_eur"] == 5000
    assert data["gift_cards.annual_warning_threshold_cents"] == 30500
    assert data["boutique.allowed_denominations_cents"] == [500, 1000, 2000, 5000]


def test_no_auth_required(raw_client):
    """Public endpoint — must work without a JWT bearer token."""
    resp = raw_client.get("/api/v1/rewards/settings/public")
    assert resp.status_code == 200


def test_cache_control_header(client):
    """Cache-Control: public, max-age=300 — settings change infrequently."""
    resp = client.get("/api/v1/rewards/settings/public")
    cache_control = resp.headers.get("cache-control", "")
    assert "public" in cache_control
    assert "max-age=300" in cache_control


def test_missing_section_omits_key_silently(client, db):
    """If a whitelisted section is missing entirely from app_settings,
    the corresponding keys are simply absent — no 500.

    This guards against partial seeds in alpha environments. The endpoint
    must never crash because a future-only setting hasn't been deployed.
    """
    from sqlalchemy import text

    db.execute(text("DELETE FROM app_settings WHERE section = 'gift_cards'"))
    db.commit()

    resp = client.get("/api/v1/rewards/settings/public")
    assert resp.status_code == 200
    data = resp.json()
    assert "gift_cards.annual_warning_threshold_cents" not in data
    # Other sections still present.
    assert "boutique.cap_annual_cents" in data
    # Re-seed for downstream tests in the same session.
    from ratis_core.seed_settings import seed_settings

    seed_settings(db)
    db.commit()
