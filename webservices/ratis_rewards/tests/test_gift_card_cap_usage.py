"""TDD — GET /api/v1/rewards/gift-cards/cap-usage.

Authoritative server-side computation of the user's gift-card cap usage
(annual + daily + weekly windows). Replaces the client-side aggregation
in ``ratis_client/hooks/use-gift-cards.ts:computeUsageStats`` so the
mobile UI stays consistent across devices and pagination doesn't break
the cap math.

Cf F-11 in the V1.1 usage-stats sprint.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import text

from tests.conftest import make_gift_card_brand, make_user


def _set_redeemed_ytd(db, user_id, cents):
    db.execute(
        text("UPDATE users SET gift_card_redeemed_ytd_cents = :c WHERE id = :uid"),
        {"c": cents, "uid": user_id},
    )
    db.commit()


def _insert_shop_order(
    db,
    *,
    user_id,
    brand_id,
    denomination_cents,
    minutes_ago=10,
    status="issued",
):
    """Insert a shop_purchase order ``minutes_ago`` minutes in the past."""
    order_id = uuid.uuid4()
    db.execute(
        text(
            "INSERT INTO gift_card_orders "
            "  (id, user_id, brand_id, denomination, status, "
            "   source_type, source_ref_id, created_at) "
            "VALUES (:id, :uid, :bid, :denom, :status, "
            "        'shop_purchase', :sref, "
            "        now() - make_interval(mins => :mins))"
        ),
        {
            "id": order_id,
            "uid": user_id,
            "bid": brand_id,
            "denom": denomination_cents,
            "status": status,
            "sref": f"sref_{uuid.uuid4().hex[:12]}",
            "mins": minutes_ago,
        },
    )
    db.commit()
    return order_id


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------


def test_requires_auth(raw_client):
    """No JWT → 401 (handled by get_current_user dep)."""
    resp = raw_client.get("/api/v1/rewards/gift-cards/cap-usage")
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Annual cap
# ---------------------------------------------------------------------------


def test_zero_usage_user(user_client, db):
    """Fresh user → zero ytd, full remaining, warning not reached."""
    client_inst, bypass = user_client
    user_id = make_user(db)
    bypass(user_id)

    resp = client_inst.get("/api/v1/rewards/gift-cards/cap-usage")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ytd_cents"] == 0
    # ratis_settings.json seed values.
    assert data["annual_warning_threshold_cents"] == 30500
    assert data["annual_hard_cap_cents"] == 119900
    assert data["remaining_cents"] == 119900
    assert data["warning_threshold_reached"] is False
    assert data["year"] == datetime.now(tz=UTC).year
    # Daily/weekly windows are part of the same response.
    assert data["daily_cents"] == 0
    assert data["weekly_cents"] == 0
    assert data["daily_cap_cents"] == 10000
    assert data["weekly_cap_cents"] == 30000


def test_25_percent_usage(user_client, db):
    """Quarter-filled cap → mid-range remaining, warning still under threshold."""
    client_inst, bypass = user_client
    user_id = make_user(db)
    bypass(user_id)
    _set_redeemed_ytd(db, user_id, 29975)  # 1 cent below the 30500 threshold

    resp = client_inst.get("/api/v1/rewards/gift-cards/cap-usage")
    data = resp.json()
    assert data["ytd_cents"] == 29975
    assert data["remaining_cents"] == 119900 - 29975
    assert data["warning_threshold_reached"] is False


def test_warning_threshold_reached(user_client, db):
    """At exactly the threshold → ``warning_threshold_reached`` flips True."""
    client_inst, bypass = user_client
    user_id = make_user(db)
    bypass(user_id)
    _set_redeemed_ytd(db, user_id, 30500)

    resp = client_inst.get("/api/v1/rewards/gift-cards/cap-usage")
    data = resp.json()
    assert data["ytd_cents"] == 30500
    assert data["warning_threshold_reached"] is True
    assert data["remaining_cents"] == 119900 - 30500


def test_full_cap_usage(user_client, db):
    """100 % cap → remaining=0, warning still True."""
    client_inst, bypass = user_client
    user_id = make_user(db)
    bypass(user_id)
    _set_redeemed_ytd(db, user_id, 119900)

    resp = client_inst.get("/api/v1/rewards/gift-cards/cap-usage")
    data = resp.json()
    assert data["ytd_cents"] == 119900
    assert data["remaining_cents"] == 0
    assert data["warning_threshold_reached"] is True


def test_remaining_clamped_at_zero_when_over(user_client, db):
    """Defensive : if a manual UPDATE pushed ytd above the cap, remaining=0."""
    client_inst, bypass = user_client
    user_id = make_user(db)
    bypass(user_id)
    _set_redeemed_ytd(db, user_id, 200000)  # over the 119900 cap

    resp = client_inst.get("/api/v1/rewards/gift-cards/cap-usage")
    data = resp.json()
    assert data["remaining_cents"] == 0


# ---------------------------------------------------------------------------
# Daily / weekly windows (Europe/Paris cutoff — cf ARCH_boutique.md)
# ---------------------------------------------------------------------------


def test_daily_window_excludes_failed_orders(user_client, db):
    """Failed shop_purchase orders never consume the cap."""
    client_inst, bypass = user_client
    user_id = make_user(db)
    bypass(user_id)
    brand_id = make_gift_card_brand(db, name="Brand A")

    _insert_shop_order(
        db,
        user_id=user_id,
        brand_id=brand_id,
        denomination_cents=2000,
        status="issued",
        minutes_ago=15,
    )
    _insert_shop_order(
        db,
        user_id=user_id,
        brand_id=brand_id,
        denomination_cents=5000,
        status="failed",
        minutes_ago=20,
    )

    resp = client_inst.get("/api/v1/rewards/gift-cards/cap-usage")
    data = resp.json()
    assert data["daily_cents"] == 2000
    assert data["weekly_cents"] == 2000


def test_daily_excludes_other_users(user_client, db):
    """Two users, two orders — each user only sees their own daily total."""
    client_inst, bypass = user_client
    me = make_user(db)
    other = make_user(db)
    brand_id = make_gift_card_brand(db, name="Brand B")

    _insert_shop_order(db, user_id=me, brand_id=brand_id, denomination_cents=1000)
    _insert_shop_order(db, user_id=other, brand_id=brand_id, denomination_cents=5000)

    bypass(me)
    resp = client_inst.get("/api/v1/rewards/gift-cards/cap-usage")
    assert resp.json()["daily_cents"] == 1000

    bypass(other)
    resp = client_inst.get("/api/v1/rewards/gift-cards/cap-usage")
    assert resp.json()["daily_cents"] == 5000


def test_excludes_non_shop_purchase_source_types(user_client, db):
    """``annual_subscription`` / ``referral`` orders bypass the boutique caps."""
    client_inst, bypass = user_client
    user_id = make_user(db)
    bypass(user_id)
    brand_id = make_gift_card_brand(db, name="Brand C")

    # Direct insert — annual_subscription source_type bypasses the daily cap.
    db.execute(
        text(
            "INSERT INTO gift_card_orders "
            "  (id, user_id, brand_id, denomination, status, "
            "   source_type, source_ref_id, created_at) "
            "VALUES (:id, :uid, :bid, 5000, 'issued', "
            "        'annual_subscription', :sref, now())"
        ),
        {
            "id": uuid.uuid4(),
            "uid": user_id,
            "bid": brand_id,
            "sref": f"ann_{uuid.uuid4().hex[:12]}",
        },
    )
    db.commit()

    resp = client_inst.get("/api/v1/rewards/gift-cards/cap-usage")
    data = resp.json()
    assert data["daily_cents"] == 0
    assert data["weekly_cents"] == 0
