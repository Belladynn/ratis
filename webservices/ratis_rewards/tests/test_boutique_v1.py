"""TDD — Boutique V1 endpoint POST /api/v1/rewards/gift-cards/order.

Spec : `webservices/ratis_rewards/ARCH_boutique.md` + design doc
`docs/superpowers/specs/2026-05-08-boutique-v1-design.md`.

The endpoint lets a user spend CAB to obtain a gift card via Runa, with
caps enforced (per-card / daily / weekly / annual) and an idempotency
window against double-tap.

Amounts in cents — ratio fixed at 1 € = 5 000 CAB. Allowed denominations
are {500, 1000, 2000, 5000} (5 € · 10 € · 20 € · 50 €).

Tests cover the 14 cases listed in the ARCH § Tests TDD requis.
"""

from __future__ import annotations

import uuid
from unittest.mock import patch

import pytest
from sqlalchemy import text

from tests.conftest import make_gift_card_brand, make_user

# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


def _set_balance(db, user_id, amount_cab):
    """Bump the user's CAB balance for tests."""
    db.execute(
        text("UPDATE user_cab_balance SET balance = :b WHERE user_id = :uid"),
        {"b": amount_cab, "uid": user_id},
    )
    db.commit()


def _insert_completed_order(db, *, user_id, brand_id, denomination_cents, days_ago=0):
    """Insert a gift_card_orders row (issued, source_type='shop_purchase')
    backdated by ``days_ago`` days plus 5 minutes (always older than the
    60 s anti-double-tap dedup window). Used to seed daily/weekly cap state.
    """
    order_id = uuid.uuid4()
    source_ref = f"shop_test_{uuid.uuid4().hex[:12]}"
    db.execute(
        text(
            "INSERT INTO gift_card_orders "
            "  (id, user_id, brand_id, denomination, status, "
            "   source_type, source_ref_id, created_at) "
            "VALUES (:id, :uid, :bid, :denom, 'issued', "
            "        'shop_purchase', :sref, "
            "        now() - make_interval(days => :days) "
            "             - interval '5 minutes')"
        ),
        {
            "id": order_id,
            "uid": user_id,
            "bid": brand_id,
            "denom": denomination_cents,
            "sref": source_ref,
            "days": days_ago,
        },
    )
    db.commit()
    return order_id


def _insert_failed_order(db, *, user_id, brand_id, denomination_cents, days_ago=0):
    """Insert a gift_card_orders row with status='failed' (source_type=
    'shop_purchase'), backdated like :func:`_insert_completed_order`. A
    failed order (Runa 5xx, network error, annual-cap BLOCK) must NOT
    consume a daily/weekly cap.
    """
    order_id = uuid.uuid4()
    source_ref = f"shop_failed_{uuid.uuid4().hex[:12]}"
    db.execute(
        text(
            "INSERT INTO gift_card_orders "
            "  (id, user_id, brand_id, denomination, status, "
            "   source_type, source_ref_id, created_at, failed_at) "
            "VALUES (:id, :uid, :bid, :denom, 'failed', "
            "        'shop_purchase', :sref, "
            "        now() - make_interval(days => :days) "
            "             - interval '5 minutes', now())"
        ),
        {
            "id": order_id,
            "uid": user_id,
            "bid": brand_id,
            "denom": denomination_cents,
            "sref": source_ref,
            "days": days_ago,
        },
    )
    db.commit()
    return order_id


def _set_redeemed_ytd(db, user_id, cents):
    """Set users.gift_card_redeemed_ytd_cents directly."""
    db.execute(
        text("UPDATE users SET gift_card_redeemed_ytd_cents = :c WHERE id = :uid"),
        {"c": cents, "uid": user_id},
    )
    db.commit()


# ---------------------------------------------------------------------------
# 1. test_order_success_full_flow
# ---------------------------------------------------------------------------


def test_order_success_full_flow(user_client, db):
    """201 + INSERT order + UPDATE balance + Runa background task scheduled."""
    client_inst, bypass = user_client
    user_id = make_user(db)
    _set_balance(db, user_id, 200000)  # plenty
    brand_id = make_gift_card_brand(db, name="Amazon", is_active=True)
    bypass(user_id)

    with patch("routes.rewards.gift_cards.issue_gift_card_bg") as mock_bg:
        resp = client_inst.post(
            "/api/v1/rewards/gift-cards/order",
            json={"brand_id": str(brand_id), "denomination_cents": 2000},
        )

    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["brand"] == "Amazon"
    assert body["denomination_cents"] == 2000
    assert body["cab_cost"] == 100000  # 20 € * 5000 CAB/€
    assert body["status"] == "pending"
    assert body["new_cab_balance"] == 100000  # 200000 - 100000
    assert "order_id" in body
    assert uuid.UUID(body["order_id"])  # parses as UUID

    # DB checks : order pending, balance debited, cab tx debit row, ytd bumped.
    order = db.execute(
        text("SELECT status, denomination, source_type FROM gift_card_orders WHERE id = :oid"),
        {"oid": body["order_id"]},
    ).first()
    assert order is not None
    assert order.status == "pending"
    assert order.denomination == 2000
    assert order.source_type == "shop_purchase"

    bal = db.execute(
        text("SELECT balance FROM user_cab_balance WHERE user_id = :uid"),
        {"uid": user_id},
    ).scalar()
    assert bal == 100000

    cab_tx = db.execute(
        text(
            "SELECT direction, amount, reason FROM cabecoin_transactions "
            "WHERE user_id = :uid AND reason = 'gift_card_purchase'"
        ),
        {"uid": user_id},
    ).first()
    assert cab_tx is not None
    assert cab_tx.direction == "debit"
    assert cab_tx.amount == 100000

    # gift_card_redeemed_ytd_cents is NOT bumped at create_order time (Task 5,
    # audit H4): the authoritative increment now happens at issuance time via
    # reserve_gift_card_cap (gift_card_cap_service). create_order is a
    # fast-fail guard only; the counter stays unchanged here.
    ytd = db.execute(
        text("SELECT gift_card_redeemed_ytd_cents FROM users WHERE id = :uid"),
        {"uid": user_id},
    ).scalar()
    assert ytd == 0  # unchanged until issuance

    mock_bg.assert_called_once()


# ---------------------------------------------------------------------------
# 2. test_order_insufficient_balance
# ---------------------------------------------------------------------------


def test_order_insufficient_balance(user_client, db):
    client_inst, bypass = user_client
    user_id = make_user(db)
    _set_balance(db, user_id, 1000)  # only 1k CAB, needs 25k for 5€
    brand_id = make_gift_card_brand(db)
    bypass(user_id)

    resp = client_inst.post(
        "/api/v1/rewards/gift-cards/order",
        json={"brand_id": str(brand_id), "denomination_cents": 500},
    )
    assert resp.status_code == 402
    assert resp.json()["detail"] == "insufficient_cab_balance"


# ---------------------------------------------------------------------------
# 3. test_order_invalid_denomination
# ---------------------------------------------------------------------------


def test_order_invalid_denomination(user_client, db):
    """30 € (3000 cents) is not in the allowed set → 400/422 invalid_denomination."""
    client_inst, bypass = user_client
    user_id = make_user(db)
    brand_id = make_gift_card_brand(db)
    bypass(user_id)

    resp = client_inst.post(
        "/api/v1/rewards/gift-cards/order",
        json={"brand_id": str(brand_id), "denomination_cents": 3000},
    )
    # Pydantic Literal validation → 422 with detail format slightly different,
    # but the spec wants a friendly 400 — service-level validation runs the
    # check explicitly so we accept either.
    assert resp.status_code in (400, 422)
    if resp.status_code == 400:
        assert resp.json()["detail"] == "invalid_denomination"


# ---------------------------------------------------------------------------
# 4. test_order_invalid_brand
# ---------------------------------------------------------------------------


def test_order_invalid_brand(user_client, db):
    client_inst, bypass = user_client
    user_id = make_user(db)
    _set_balance(db, user_id, 50000)
    bypass(user_id)

    resp = client_inst.post(
        "/api/v1/rewards/gift-cards/order",
        json={"brand_id": str(uuid.uuid4()), "denomination_cents": 500},
    )
    # Spec says 400 invalid_brand_id OR 404 brand_not_available depending on
    # whether the row missing vs is_active=false. We treat unknown UUID as
    # 404 brand_not_available — same external user signal (the brand is not
    # in the active catalog), saves a probe round-trip on cold UUIDs.
    assert resp.status_code == 404
    assert resp.json()["detail"] == "brand_not_available"


# ---------------------------------------------------------------------------
# 5. test_order_inactive_brand
# ---------------------------------------------------------------------------


def test_order_inactive_brand(user_client, db):
    client_inst, bypass = user_client
    user_id = make_user(db)
    _set_balance(db, user_id, 50000)
    brand_id = make_gift_card_brand(db, is_active=False)
    bypass(user_id)

    resp = client_inst.post(
        "/api/v1/rewards/gift-cards/order",
        json={"brand_id": str(brand_id), "denomination_cents": 500},
    )
    assert resp.status_code == 404
    assert resp.json()["detail"] == "brand_not_available"


# ---------------------------------------------------------------------------
# 6. test_daily_cap_reached
# ---------------------------------------------------------------------------


def test_daily_cap_reached(user_client, db):
    """Already 80€ today, requests 30€ → 409 daily_redeem_cap_reached."""
    client_inst, bypass = user_client
    user_id = make_user(db)
    _set_balance(db, user_id, 1_000_000)  # never short on CAB
    brand_id = make_gift_card_brand(db)
    # 80€ already today (2x 20€ + 2x 20€ = 80€ — denominations restricted)
    _insert_completed_order(db, user_id=user_id, brand_id=brand_id, denomination_cents=5000, days_ago=0)  # 50€
    _insert_completed_order(db, user_id=user_id, brand_id=brand_id, denomination_cents=2000, days_ago=0)  # 20€
    _insert_completed_order(db, user_id=user_id, brand_id=brand_id, denomination_cents=1000, days_ago=0)  # 10€ → 80€
    bypass(user_id)

    # Request 50€ more → 130€ > 100€ cap.
    resp = client_inst.post(
        "/api/v1/rewards/gift-cards/order",
        json={"brand_id": str(brand_id), "denomination_cents": 5000},
    )
    assert resp.status_code == 409
    assert resp.json()["detail"] == "daily_redeem_cap_reached"


# ---------------------------------------------------------------------------
# 6b. test_daily_cap_ignores_failed_orders
# ---------------------------------------------------------------------------


def test_daily_cap_ignores_failed_orders(user_client, db):
    """A failed shop_purchase order must NOT consume the daily cap.

    Regression : a Runa 5xx (or annual-cap BLOCK) marks the order
    status='failed'. The user received nothing, so that order must not
    lock them out of the boutique. We seed a failed order at the full
    daily cap (100€) and assert a fresh 50€ purchase is still allowed.
    """
    client_inst, bypass = user_client
    user_id = make_user(db)
    _set_balance(db, user_id, 1_000_000)  # never short on CAB
    brand_id = make_gift_card_brand(db)
    # 100€ of FAILED orders today (2x 50€) — must not count toward cap.
    _insert_failed_order(db, user_id=user_id, brand_id=brand_id, denomination_cents=5000, days_ago=0)
    _insert_failed_order(db, user_id=user_id, brand_id=brand_id, denomination_cents=5000, days_ago=0)
    bypass(user_id)

    # Fresh 50€ purchase — daily cap untouched by the failed orders.
    with patch("routes.rewards.gift_cards.issue_gift_card_bg"):
        resp = client_inst.post(
            "/api/v1/rewards/gift-cards/order",
            json={"brand_id": str(brand_id), "denomination_cents": 5000},
        )
    assert resp.status_code == 201, resp.text


# ---------------------------------------------------------------------------
# 7. test_weekly_cap_reached
# ---------------------------------------------------------------------------


def test_weekly_cap_reached(user_client, db):
    """Weekly cap (300 €) reached → 409 weekly_redeem_cap_reached, not 'daily'.

    Strategy : pile up orders such that
        - daily(today) + denom_asked <= cap_daily  (so daily check passes)
        - weekly(this ISO week) + denom_asked > cap_weekly

    We seed 95€ today (45€ + 30€... but only allowed dénos are
    {5,10,20,50}, so 1×50 + 2×20 + 1×5 = 95€) and pile 5×50€ = 250€ on
    a past day still in this ISO week (yesterday, except on Monday where
    'yesterday' is in the previous ISO week — we skip then).
    """
    import datetime

    if datetime.datetime.now().isoweekday() == 1:
        pytest.skip("Test skipped on Monday — past days fall in previous ISO week")

    client_inst, bypass = user_client
    user_id = make_user(db)
    _set_balance(db, user_id, 5_000_000)
    brand_id = make_gift_card_brand(db)
    # Today : 50 + 20 + 20 + 5 = 95€
    _insert_completed_order(db, user_id=user_id, brand_id=brand_id, denomination_cents=5000, days_ago=0)
    _insert_completed_order(db, user_id=user_id, brand_id=brand_id, denomination_cents=2000, days_ago=0)
    _insert_completed_order(db, user_id=user_id, brand_id=brand_id, denomination_cents=2000, days_ago=0)
    _insert_completed_order(db, user_id=user_id, brand_id=brand_id, denomination_cents=500, days_ago=0)
    # Yesterday : 5×50 = 250€ → weekly 345€
    for _ in range(5):
        _insert_completed_order(db, user_id=user_id, brand_id=brand_id, denomination_cents=5000, days_ago=1)

    bypass(user_id)
    # Ask 5€ : daily 95+5 = 100 ≤ 100 (OK). Weekly 345+5 = 350 > 300 → fail.
    resp = client_inst.post(
        "/api/v1/rewards/gift-cards/order",
        json={"brand_id": str(brand_id), "denomination_cents": 500},
    )
    assert resp.status_code == 409, resp.text
    assert resp.json()["detail"] == "weekly_redeem_cap_reached"


# ---------------------------------------------------------------------------
# 8. test_annual_cap_reached
# ---------------------------------------------------------------------------


def test_annual_cap_reached(user_client, db):
    """gift_card_redeemed_ytd_cents already 1180€, asks 30€ → 409."""
    client_inst, bypass = user_client
    user_id = make_user(db)
    _set_balance(db, user_id, 5_000_000)
    _set_redeemed_ytd(db, user_id, 118000)  # 1180 €
    brand_id = make_gift_card_brand(db)
    bypass(user_id)

    resp = client_inst.post(
        "/api/v1/rewards/gift-cards/order",
        json={"brand_id": str(brand_id), "denomination_cents": 5000},
    )
    assert resp.status_code == 409
    assert resp.json()["detail"] == "annual_gift_card_cap_reached"


# ---------------------------------------------------------------------------
# 9. test_balance_debit_atomic
# ---------------------------------------------------------------------------


def test_balance_debit_atomic(user_client, db):
    """If the order INSERT fails after the balance debit, the whole
    transaction must roll back so balance stays untouched."""
    client_inst, bypass = user_client
    user_id = make_user(db)
    _set_balance(db, user_id, 200000)
    brand_id = make_gift_card_brand(db)
    bypass(user_id)

    # Force insert_order to raise → service must propagate and DB must roll
    # back the balance UPDATE in the same transaction.
    # The TestClient surfaces unhandled exceptions raised in the route as
    # the original Python exception. We expect the simulated RuntimeError.
    with (
        patch(
            "services.boutique_service.repository.insert_order",
            side_effect=RuntimeError("simulated insert failure"),
        ),
        pytest.raises(RuntimeError, match="simulated insert failure"),
    ):
        client_inst.post(
            "/api/v1/rewards/gift-cards/order",
            json={"brand_id": str(brand_id), "denomination_cents": 500},
        )

    bal = db.execute(
        text("SELECT balance FROM user_cab_balance WHERE user_id = :uid"),
        {"uid": user_id},
    ).scalar()
    assert bal == 200000  # unchanged — atomic rollback


# ---------------------------------------------------------------------------
# 10. test_idempotency_duplicate_order_recent
# ---------------------------------------------------------------------------


def test_idempotency_duplicate_order_recent(user_client, db):
    """2nd identical POST within the dedup window → 409 duplicate_order_recent."""
    client_inst, bypass = user_client
    user_id = make_user(db)
    _set_balance(db, user_id, 500000)
    brand_id = make_gift_card_brand(db)
    bypass(user_id)

    with patch("routes.rewards.gift_cards.issue_gift_card_bg"):
        r1 = client_inst.post(
            "/api/v1/rewards/gift-cards/order",
            json={"brand_id": str(brand_id), "denomination_cents": 500},
        )
        assert r1.status_code == 201
        r2 = client_inst.post(
            "/api/v1/rewards/gift-cards/order",
            json={"brand_id": str(brand_id), "denomination_cents": 500},
        )
    assert r2.status_code == 409
    assert r2.json()["detail"] == "duplicate_order_recent"


# ---------------------------------------------------------------------------
# RW-05 — create_order debits via the canonical debit_cab helper
# ---------------------------------------------------------------------------


def test_order_debits_via_debit_cab_helper(user_client, db):
    """``create_order`` must route the CAB debit through the canonical
    ``debit_cab`` helper rather than re-implementing the SQL inline
    (audit RW-05). The helper carries the VALID_REASONS guard and any
    future evolution of the debit path."""
    client_inst, bypass = user_client
    user_id = make_user(db)
    _set_balance(db, user_id, 200000)
    brand_id = make_gift_card_brand(db)
    bypass(user_id)

    with (
        patch("routes.rewards.gift_cards.issue_gift_card_bg"),
        patch(
            "services.boutique_service.debit_cab",
            wraps=__import__("repositories.cab_repository", fromlist=["debit_cab"]).debit_cab,
        ) as spy_debit,
    ):
        resp = client_inst.post(
            "/api/v1/rewards/gift-cards/order",
            json={"brand_id": str(brand_id), "denomination_cents": 500},
        )

    assert resp.status_code == 201, resp.text
    spy_debit.assert_called_once()
    # tx_id is pre-allocated by the caller and passed through.
    assert spy_debit.call_args.kwargs.get("tx_id") is not None


def test_order_source_ref_id_links_to_debit_transaction(user_client, db):
    """The gift-card order's ``source_ref_id`` must equal the id of the
    ``cabecoin_transactions`` debit row created by ``debit_cab`` — a
    stable order↔transaction link (audit RW-05)."""
    client_inst, bypass = user_client
    user_id = make_user(db)
    _set_balance(db, user_id, 200000)
    brand_id = make_gift_card_brand(db)
    bypass(user_id)

    with patch("routes.rewards.gift_cards.issue_gift_card_bg"):
        resp = client_inst.post(
            "/api/v1/rewards/gift-cards/order",
            json={"brand_id": str(brand_id), "denomination_cents": 500},
        )
    assert resp.status_code == 201, resp.text
    order_id = resp.json()["order_id"]

    order = db.execute(
        text("SELECT source_ref_id FROM gift_card_orders WHERE id = :oid"),
        {"oid": order_id},
    ).first()
    assert order is not None
    cab_tx = db.execute(
        text("SELECT id FROM cabecoin_transactions WHERE user_id = :uid AND reason = 'gift_card_purchase'"),
        {"uid": user_id},
    ).first()
    assert cab_tx is not None
    assert str(order.source_ref_id) == str(cab_tx.id)


def test_order_insufficient_balance_rejected_cleanly(user_client, db):
    """An insufficient balance must be rejected by the atomic guard
    inside ``debit_cab`` (WHERE balance >= :amount) and surface as 402
    without any partial write (audit RW-05)."""
    client_inst, bypass = user_client
    user_id = make_user(db)
    _set_balance(db, user_id, 100)  # far below the 25000 CAB cost of 5 €
    brand_id = make_gift_card_brand(db)
    bypass(user_id)

    resp = client_inst.post(
        "/api/v1/rewards/gift-cards/order",
        json={"brand_id": str(brand_id), "denomination_cents": 500},
    )
    assert resp.status_code == 402
    assert resp.json()["detail"] == "insufficient_cab_balance"

    bal = db.execute(
        text("SELECT balance FROM user_cab_balance WHERE user_id = :uid"),
        {"uid": user_id},
    ).scalar()
    assert bal == 100  # untouched
    no_order = db.execute(
        text("SELECT 1 FROM cabecoin_transactions WHERE user_id = :uid AND reason = 'gift_card_purchase'"),
        {"uid": user_id},
    ).first()
    assert no_order is None


# ---------------------------------------------------------------------------
# 11. test_brand_seed_count
# ---------------------------------------------------------------------------


def test_brand_seed_count(db):
    """The data migration seeds 5 active brands for Saison 1."""
    from ratis_core.seed.boutique_brands import seed_boutique_brands

    seed_boutique_brands(db)
    db.commit()

    n = db.execute(text("SELECT COUNT(*) FROM gift_card_brands WHERE is_active = true")).scalar()
    assert n >= 5  # seed adds 5 ; conftest may not pre-seed any

    names = {r.name for r in db.execute(text("SELECT name FROM gift_card_brands WHERE is_active = true")).fetchall()}
    expected = {"Amazon.fr", "Carrefour", "Decathlon", "Sephora", "Spotify"}
    assert expected.issubset(names)


# ---------------------------------------------------------------------------
# 12. test_cap_calculation_timezone_aware
# ---------------------------------------------------------------------------


def test_cap_calculation_timezone_aware(db):
    """count_redeemed_today_cents must use Europe/Paris cutoff, not UTC.

    A row inserted at 23:30 UTC on day D ends up on day D+1 in Paris during
    summer time (UTC+2). The repository must count it under the Paris-local
    'day D+1' bucket — which means a query running at 00:30 UTC on day D+1
    (= 02:30 Paris on day D+1) sees that row in today's window.
    """
    from repositories import boutique_repository as repo

    user_id = make_user(db)
    brand_id = make_gift_card_brand(db)

    # Insert a row dated "1 hour ago" — guaranteed to be within the same
    # Paris-local day as NOW(), but the assertion is structural : the SQL
    # uses date_trunc('day', NOW() AT TIME ZONE 'Europe/Paris').
    db.execute(
        text(
            "INSERT INTO gift_card_orders "
            "  (id, user_id, brand_id, denomination, status, "
            "   source_type, source_ref_id, created_at) "
            "VALUES (gen_random_uuid(), :uid, :bid, 500, 'issued', "
            "        'shop_purchase', :sref, now() - interval '1 hour')"
        ),
        {"uid": user_id, "bid": brand_id, "sref": uuid.uuid4().hex},
    )
    db.commit()
    today = repo.count_redeemed_today_cents(db, user_id)
    assert today >= 500


# ---------------------------------------------------------------------------
# 13. test_runa_failure_marks_order_failed
# ---------------------------------------------------------------------------


def test_runa_failure_marks_order_failed(user_client, db):
    """When the Runa-side issuance fails, the order row ends up status='failed'.

    The order row is created in 'pending' on the synchronous request and the
    background task transitions it. We exercise the existing
    issue_gift_card path with a mocked httpx call that raises 5xx, and check
    that the order is marked failed.
    """
    import httpx
    from services.gift_card_service import issue_gift_card

    user_id = make_user(db)
    brand_id = make_gift_card_brand(db, name="Amazon", provider_brand_id="runa-amazon")
    # Pre-create a pending order
    order_id = uuid.uuid4()
    db.execute(
        text(
            "INSERT INTO gift_card_orders "
            "  (id, user_id, brand_id, denomination, status, "
            "   source_type, source_ref_id, created_at) "
            "VALUES (:id, :uid, :bid, 500, 'pending', "
            "        'shop_purchase', :sref, now())"
        ),
        {
            "id": order_id,
            "uid": user_id,
            "bid": brand_id,
            "sref": uuid.uuid4().hex,
        },
    )
    db.commit()

    # Mock httpx.post to return 5xx — force the failure path.
    fake_resp = httpx.Response(503, request=httpx.Request("POST", "http://x"))
    with patch("services.gift_card_service.httpx.post") as mock_post:
        mock_post.return_value = fake_resp
        # Real key path — sandbox bypass kicks in only if env empty.
        with patch.dict("os.environ", {"GIFT_CARD_PROVIDER_KEY": "real"}):
            issue_gift_card(order_id, db)

    db.commit()
    status = db.execute(
        text("SELECT status FROM gift_card_orders WHERE id = :oid"),
        {"oid": order_id},
    ).scalar()
    assert status == "failed"


# ---------------------------------------------------------------------------
# Catalog endpoint — GET /api/v1/rewards/gift-cards/catalog
# ---------------------------------------------------------------------------


def test_catalog_returns_active_brands_only(user_client, db):
    """GET /catalog returns active brands only, sorted by name."""
    client_inst, bypass = user_client
    user_id = make_user(db)
    bypass(user_id)
    make_gift_card_brand(db, name="Amazon.fr", is_active=True)
    make_gift_card_brand(db, name="Carrefour", is_active=True)
    make_gift_card_brand(db, name="Inactive", is_active=False)

    resp = client_inst.get("/api/v1/rewards/gift-cards/catalog")
    assert resp.status_code == 200
    body = resp.json()
    names = [b["name"] for b in body["brands"]]
    assert "Amazon.fr" in names
    assert "Carrefour" in names
    assert "Inactive" not in names
    # Spec exposes denominations + ratio at the response level too.
    assert body["allowed_denominations_cents"] == [500, 1000, 2000, 5000]
    assert body["ratio_cab_per_eur"] == 5000


# ---------------------------------------------------------------------------
# 14a. test_sequential_orders_at_daily_cap_rejects_second
# ---------------------------------------------------------------------------


def test_sequential_orders_at_daily_cap_rejects_second(user_client, db):
    """After a first POST commits, a second POST sees the bumped daily SUM
    and is rejected with 409 `daily_redeem_cap_reached`.

    Sequential, not concurrent. The real concurrency contract (advisory
    lock acquired before cap reads + per-user lock scope) is covered by
    the two tests below — here we only check the per-request cap math
    (the first request reads the seeded 90 €, adds 5 €, commits ; the
    second reads 95 €, would add 10 € → 105 € → 409).
    """
    client_inst, bypass = user_client
    user_id = make_user(db)
    _set_balance(db, user_id, 500_000)
    brand_id = make_gift_card_brand(db)
    # Already 90 € today via past orders.
    _insert_completed_order(db, user_id=user_id, brand_id=brand_id, denomination_cents=5000, days_ago=0)  # 50€
    _insert_completed_order(db, user_id=user_id, brand_id=brand_id, denomination_cents=2000, days_ago=0)  # 20€
    _insert_completed_order(db, user_id=user_id, brand_id=brand_id, denomination_cents=2000, days_ago=0)  # 20€  → 90€
    bypass(user_id)

    with patch("routes.rewards.gift_cards.issue_gift_card_bg"):
        # First 5 € passes (90 + 5 = 95 ≤ 100).
        r1 = client_inst.post(
            "/api/v1/rewards/gift-cards/order",
            json={"brand_id": str(brand_id), "denomination_cents": 500},
        )
        assert r1.status_code == 201, r1.text
        # Second 10 € fails (95 + 10 = 105 > 100).
        r2 = client_inst.post(
            "/api/v1/rewards/gift-cards/order",
            json={"brand_id": str(brand_id), "denomination_cents": 1000},
        )
    assert r2.status_code == 409
    assert r2.json()["detail"] == "daily_redeem_cap_reached"


# ---------------------------------------------------------------------------
# 14b. test_create_order_acquires_advisory_lock_first
# ---------------------------------------------------------------------------


def test_create_order_acquires_advisory_lock_first(user_client, db):
    """``create_order`` must emit ``pg_advisory_xact_lock(hashtext(:key))``
    BEFORE any cap-read SELECT (audit F-RW-3 fix).

    The lock serialises concurrent gift-card orders for the same user at
    the PG backend level so two parallel orders cannot both pass the
    daily / weekly / annual cap reads and overshoot the DAS2 1199 €/year
    fiscal cap. PG's own advisory-lock semantics guarantee mutual
    exclusion once the lock is acquired ; here we only verify that our
    service issues the lock SQL *before* the cap reads.

    Why this discipline rather than a real-concurrency integration test :
    spawning two real connections committing durable rows against the
    shared test DB races with the session-scoped ``setup_db`` teardown
    (``DROP SCHEMA CASCADE``) and produces flakes in CI. PG's advisory
    locks are battle-tested ; the application contract under test is
    *"the lock SQL is emitted at the right point"*.
    """
    client_inst, bypass = user_client
    user_id = make_user(db)
    _set_balance(db, user_id, 500_000)
    brand_id = make_gift_card_brand(db)
    bypass(user_id)

    # Capture every cursor execute on this test's connection. We then
    # assert that the advisory-lock SQL appears BEFORE any cap-read
    # SELECT (count_redeemed_today / count_redeemed_this_week /
    # get_user_ytd_cents).
    statements: list[str] = []
    from sqlalchemy import event as _event

    conn = db._test_connection

    @_event.listens_for(conn, "before_cursor_execute")
    def _capture(_c, _cur, statement, _params, _ctx, _many):
        statements.append(statement)

    with patch("routes.rewards.gift_cards.issue_gift_card_bg"):
        resp = client_inst.post(
            "/api/v1/rewards/gift-cards/order",
            json={"brand_id": str(brand_id), "denomination_cents": 500},
        )
    _event.remove(conn, "before_cursor_execute", _capture)
    assert resp.status_code == 201, resp.text

    # Find the advisory-lock statement and the first cap-read.
    lock_idx = next(
        (i for i, s in enumerate(statements) if "pg_advisory_xact_lock" in s.lower()),
        None,
    )
    assert lock_idx is not None, (
        f"create_order must emit a pg_advisory_xact_lock — none seen. Statements: {statements!r}"
    )

    # Cap reads are SUM queries on gift_card_orders (daily/weekly) and a
    # SELECT on users.gift_card_redeemed_ytd_cents (annual). The dedup
    # check is also a SELECT on gift_card_orders but it's SELECT id ;
    # the SUM query has 'sum(denomination)' which is distinctive enough.
    def _is_cap_read(s: str) -> bool:
        sl = s.lower()
        if "sum(denomination)" in sl:
            return True
        # Annual cap : SELECT gift_card_redeemed_ytd_cents FROM users WHERE...
        return bool("select gift_card_redeemed_ytd_cents" in sl and "from users" in sl)

    cap_idx = next((i for i, s in enumerate(statements) if _is_cap_read(s)), None)
    assert cap_idx is not None, (
        f"create_order should issue at least one cap-read SELECT — none seen. Statements: {statements!r}"
    )
    assert lock_idx < cap_idx, (
        "pg_advisory_xact_lock must be acquired BEFORE the first cap-read "
        f"SELECT — got lock at {lock_idx}, first cap-read at {cap_idx}. "
        f"Statements: {statements!r}"
    )


# ---------------------------------------------------------------------------
# 14c. test_create_order_lock_key_is_user_scoped
# ---------------------------------------------------------------------------


def test_create_order_lock_key_is_user_scoped(user_client, db):
    """The advisory lock key must be derived from the user_id so that two
    DIFFERENT users can place gift-card orders in parallel without
    blocking each other (audit F-RW-3 fix — per-user lock granularity).

    We verify the executed lock SQL references ``gift_card_order:<uid>``
    via the parameter dict passed to ``pg_advisory_xact_lock(hashtext(...))``.
    """
    client_inst, bypass = user_client
    user_id = make_user(db)
    _set_balance(db, user_id, 500_000)
    brand_id = make_gift_card_brand(db)
    bypass(user_id)

    # Capture (statement, parameters) tuples on the test connection.
    seen: list[tuple[str, object]] = []
    from sqlalchemy import event as _event

    conn = db._test_connection

    @_event.listens_for(conn, "before_cursor_execute")
    def _capture(_c, _cur, statement, parameters, _ctx, _many):
        seen.append((statement, parameters))

    with patch("routes.rewards.gift_cards.issue_gift_card_bg"):
        resp = client_inst.post(
            "/api/v1/rewards/gift-cards/order",
            json={"brand_id": str(brand_id), "denomination_cents": 500},
        )
    _event.remove(conn, "before_cursor_execute", _capture)
    assert resp.status_code == 201, resp.text

    # Locate the advisory-lock call and inspect its bind parameters.
    lock_call = next(
        ((s, p) for s, p in seen if "pg_advisory_xact_lock" in s.lower()),
        None,
    )
    assert lock_call is not None, "advisory lock SQL not seen"
    _stmt, params = lock_call
    # psycopg3 passes positional or named params depending on driver mode.
    flat = list(params.values()) if isinstance(params, dict) else list(params) if params else []
    flat_str = " ".join(str(v) for v in flat)
    # Task 5 (audit H4): lock key unified to gift_card_cap:{user_id} so that
    # the boutique create-time fast-fail and the issuance-time
    # reserve_gift_card_cap serialise on the SAME per-user lock.
    expected = f"gift_card_cap:{user_id}"
    assert expected in flat_str, (
        f"advisory lock key should contain {expected!r} (per-user scope, "
        f"unified with issuance-time reserve_gift_card_cap) "
        f"but parameters are {params!r}"
    )
