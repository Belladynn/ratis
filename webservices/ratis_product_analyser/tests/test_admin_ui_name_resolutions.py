"""Tests for the NRC arbitration mini admin UI (Bloc D — Page F).

Covers :

- Auth gate (no cookie → 302 to login)
- Queue page render (empty + populated)
- Queue page filters (state, store_id)
- Detail page render (existing label) + 404 path
- POST resolve (form → service → redirect with flash)
- POST reject-challenges (form → service → redirect with flash)
- Dashboard tile counter (number of items to arbitrate)
- Nav link presence on protected pages

Reuses ``raw_client`` (no auth bypass) so the cookie-session dep is
exercised end-to-end. The JSON API tests cover the same service layer
under ``test_admin_name_resolutions.py``.
"""

from __future__ import annotations

import json
import uuid
from datetime import date
from decimal import Decimal

from ratis_core.models.scan import Receipt, Scan
from ratis_core.models.store import Store
from ratis_core.models.user import User
from sqlalchemy import text

# ============================================================================
# Helpers — DB seeders (mirror test_admin_name_resolutions.py)
# ============================================================================


def _make_store(db, *, name: str = "Lidl Test") -> Store:
    s = Store(
        id=uuid.uuid4(),
        name=name,
        retailer="lidl",
        address="1 rue Test",
        city="Paris",
        postal_code="75001",
        lat=Decimal("48.8566"),
        lng=Decimal("2.3522"),
    )
    db.add(s)
    db.flush()
    db.commit()
    return s


def _make_user(db, *, suffix: str = "") -> User:
    uid = uuid.uuid4()
    u = User(
        id=uid,
        email=f"user-{suffix}-{uid.hex[:8]}@ratis.fr",
        account_type="oauth",
        is_deleted=False,
    )
    db.add(u)
    db.flush()
    db.commit()
    return u


def _make_scan(db, *, store_id, user_id, scanned_name: str) -> Scan:
    # CHECK ``receipt_required`` — receipt scans need a sibling Receipt.
    r = Receipt(
        id=uuid.uuid4(),
        store_id=store_id,
        user_id=user_id,
        purchased_at=date.today(),
    )
    db.add(r)
    db.flush()
    s = Scan(
        id=uuid.uuid4(),
        scan_type="receipt",
        status="pending",
        scanned_name=scanned_name,
        store_id=store_id,
        user_id=user_id,
        receipt_id=r.id,
        price=199,
    )
    db.add(s)
    db.flush()
    db.commit()
    return s


def _record_ledger(db, *, scan_id, store_id, label, ean, user_id, method="barcode"):
    db.execute(
        text(
            """
            INSERT INTO product_name_resolutions
                (id, scan_id, store_id, normalized_label, product_ean,
                 user_id, match_method, resolved_at)
            VALUES (:id, :scan, :sid, :label, :ean, :uid, :m, clock_timestamp())
            """
        ),
        {
            "id": str(uuid.uuid4()),
            "scan": str(scan_id),
            "sid": str(store_id),
            "label": label,
            "ean": ean,
            "uid": str(user_id),
            "m": method,
        },
    )
    db.commit()


def _emit_state_event(db, *, store_id, label, to_state, top1_ean):
    payload = {
        "event": "consensus_state_changed",
        "store_id": str(store_id),
        "normalized_label": label,
        "from_state": None,
        "to_state": to_state,
        "top1_ean": top1_ean,
        "distinct_validators": 3,
        "convergence_pct": 100.0,
        "triggered_by_scan_id": None,
        "challengers": None,
    }
    db.execute(
        text(
            """
            INSERT INTO pipeline_audit_log
                (phase, level, event, scan_id, parsed_ticket_id, payload, created_at)
            VALUES ('match', 'normal', 'consensus_state_changed',
                    NULL, NULL, CAST(:p AS jsonb), clock_timestamp())
            """
        ),
        {"p": json.dumps(payload)},
    )
    db.commit()


def _ensure_admin_anchor_product(db, ean: str) -> None:
    """Bug 6 — see docstring in
    ``test_admin_name_resolutions._ensure_admin_anchor_product``.
    Seeds a Product so the synthetic admin-anchor scan path satisfies
    the FK ``scans_product_ean_fkey`` when the test drives the resolve
    or reject_challenges admin endpoint."""
    db.execute(
        text("INSERT INTO products (ean, name, source) VALUES (:e, 'Bug6 anchor', 'off') ON CONFLICT (ean) DO NOTHING"),
        {"e": ean},
    )
    db.commit()


def _seed_controverse(db, store, label, ean_a, ean_b, *, count_a=2, count_b=2):
    users_a = [_make_user(db, suffix=f"ca{i}") for i in range(count_a)]
    users_b = [_make_user(db, suffix=f"cb{i}") for i in range(count_b)]
    for u in users_a:
        s = _make_scan(db, store_id=store.id, user_id=u.id, scanned_name=label)
        _record_ledger(db, scan_id=s.id, store_id=store.id, label=label, ean=ean_a, user_id=u.id)
    for u in users_b:
        s = _make_scan(db, store_id=store.id, user_id=u.id, scanned_name=label)
        _record_ledger(db, scan_id=s.id, store_id=store.id, label=label, ean=ean_b, user_id=u.id)
    return users_a, users_b


def _seed_unverified(db, store, label, prev_ean, new_ean, *, challenger_count=1):
    """3 verified-on-prev + N challengers on new_ean → state=unverified."""
    users_v = [_make_user(db, suffix=f"v{i}") for i in range(3)]
    for u in users_v:
        s = _make_scan(db, store_id=store.id, user_id=u.id, scanned_name=label)
        _record_ledger(db, scan_id=s.id, store_id=store.id, label=label, ean=prev_ean, user_id=u.id)
    _emit_state_event(db, store_id=store.id, label=label, to_state="verified", top1_ean=prev_ean)
    challengers = [_make_user(db, suffix=f"ch{i}") for i in range(challenger_count)]
    for u in challengers:
        s = _make_scan(db, store_id=store.id, user_id=u.id, scanned_name=label)
        _record_ledger(db, scan_id=s.id, store_id=store.id, label=label, ean=new_ean, user_id=u.id)
    return challengers


def _login(raw_client, api_key="test-admin-key-padded-to-32-chars-min", operator="tester"):
    return raw_client.post(
        "/admin/ui/login",
        data={"api_key": api_key, "operator": operator},
        follow_redirects=False,
    )


# ============================================================================
# Auth gate
# ============================================================================
class TestAuth:
    def test_queue_page_redirects_when_no_cookie(self, raw_client):
        r = raw_client.get("/admin/ui/name-resolutions/queue", follow_redirects=False)
        assert r.status_code == 302
        assert r.headers["location"] == "/admin/ui/login"

    def test_detail_page_redirects_when_no_cookie(self, raw_client):
        r = raw_client.get(
            f"/admin/ui/name-resolutions/{uuid.uuid4()}/X",
            follow_redirects=False,
        )
        assert r.status_code == 302
        assert r.headers["location"] == "/admin/ui/login"

    def test_resolve_post_redirects_when_no_cookie(self, raw_client):
        r = raw_client.post(
            "/admin/ui/name-resolutions/resolve",
            data={
                "store_id": str(uuid.uuid4()),
                "normalized_label": "X",
                "target_ean": "1",
            },
            follow_redirects=False,
        )
        assert r.status_code == 302
        assert r.headers["location"] == "/admin/ui/login"


# ============================================================================
# Queue page
# ============================================================================
class TestQueuePage:
    def test_queue_empty_renders_placeholder(self, raw_client):
        _login(raw_client)
        r = raw_client.get("/admin/ui/name-resolutions/queue")
        assert r.status_code == 200
        assert "Aucun cas à arbitrer" in r.text

    def test_queue_renders_controverse_row(self, raw_client, db):
        _login(raw_client)
        store = _make_store(db)
        _seed_controverse(db, store, "MY LABEL", "1111111111111", "2222222222222")
        r = raw_client.get("/admin/ui/name-resolutions/queue")
        assert r.status_code == 200
        assert "MY LABEL" in r.text
        assert "controverse" in r.text

    def test_queue_state_filter_applied(self, raw_client, db):
        _login(raw_client)
        store = _make_store(db)
        _seed_controverse(db, store, "LBL-CTV", "1A", "2A")
        _seed_unverified(db, store, "LBL-UNV", "3A", "4A")
        r = raw_client.get("/admin/ui/name-resolutions/queue?state=controverse")
        assert "LBL-CTV" in r.text
        assert "LBL-UNV" not in r.text

    def test_queue_invalid_state_shown_as_error(self, raw_client):
        _login(raw_client)
        r = raw_client.get("/admin/ui/name-resolutions/queue?state=foo")
        assert r.status_code == 200
        assert "État invalide" in r.text


# ============================================================================
# Detail page
# ============================================================================
class TestDetailPage:
    def test_detail_renders_resolutions(self, raw_client, db):
        _login(raw_client)
        store = _make_store(db)
        _seed_controverse(db, store, "DETAIL-LBL", "1111111111111", "2222222222222")
        r = raw_client.get(f"/admin/ui/name-resolutions/{store.id}/DETAIL-LBL")
        assert r.status_code == 200
        assert "DETAIL-LBL" in r.text
        assert "controverse" in r.text

    def test_detail_404_unknown_label(self, raw_client):
        _login(raw_client)
        r = raw_client.get(f"/admin/ui/name-resolutions/{uuid.uuid4()}/UNKNOWN")
        assert r.status_code == 404
        assert "introuvable" in r.text.lower()


# ============================================================================
# POST handlers
# ============================================================================
class TestResolvePost:
    def test_post_resolve_redirects_to_queue_with_flash(self, raw_client, db):
        _login(raw_client)
        store = _make_store(db)
        _seed_controverse(db, store, "LBL", "1111111111111", "2222222222222", count_a=3, count_b=1)
        _ensure_admin_anchor_product(db, "1111111111111")
        r = raw_client.post(
            "/admin/ui/name-resolutions/resolve",
            data={
                "store_id": str(store.id),
                "normalized_label": "LBL",
                "target_ean": "1111111111111",
            },
            follow_redirects=False,
        )
        assert r.status_code == 303
        assert r.headers["location"].startswith("/admin/ui/name-resolutions/queue")
        assert "Résolu" in r.headers["location"] or "1111111111111" in r.headers["location"]

    def test_post_resolve_redirect_to_detail_when_requested(self, raw_client, db):
        _login(raw_client)
        store = _make_store(db)
        _seed_controverse(db, store, "LBL", "1111111111111", "2222222222222", count_a=3, count_b=1)
        _ensure_admin_anchor_product(db, "1111111111111")
        r = raw_client.post(
            "/admin/ui/name-resolutions/resolve",
            data={
                "store_id": str(store.id),
                "normalized_label": "LBL",
                "target_ean": "1111111111111",
                "redirect_to": "detail",
            },
            follow_redirects=False,
        )
        assert r.status_code == 303
        assert "/name-resolutions/" in r.headers["location"]
        assert "/queue" not in r.headers["location"]


class TestRejectChallengesPost:
    def test_post_reject_challenges_happy_path(self, raw_client, db):
        _login(raw_client)
        store = _make_store(db)
        _seed_unverified(db, store, "LBL", "1111111111111", "2222222222222", challenger_count=1)
        _ensure_admin_anchor_product(db, "1111111111111")
        r = raw_client.post(
            "/admin/ui/name-resolutions/reject-challenges",
            data={
                "store_id": str(store.id),
                "normalized_label": "LBL",
            },
            follow_redirects=False,
        )
        assert r.status_code == 303
        assert "rejet" in r.headers["location"].lower()

    def test_post_reject_challenges_state_mismatch_flash(self, raw_client, db):
        _login(raw_client)
        store = _make_store(db)
        # Controverse, not unverified — should fail with flash
        _seed_controverse(db, store, "LBL", "1111111111111", "2222222222222")
        r = raw_client.post(
            "/admin/ui/name-resolutions/reject-challenges",
            data={
                "store_id": str(store.id),
                "normalized_label": "LBL",
            },
            follow_redirects=False,
        )
        assert r.status_code == 303
        # The flash carries the error
        assert "unverified" in r.headers["location"]


# ============================================================================
# Dashboard counter + nav link
# ============================================================================
class TestDashboardCounter:
    def test_dashboard_renders_zero_when_empty(self, raw_client):
        _login(raw_client)
        r = raw_client.get("/admin/ui/")
        assert r.status_code == 200
        # Counter badge absent when total=0
        assert 'data-testid="nrc-counter"' not in r.text

    def test_dashboard_renders_counter_when_populated(self, raw_client, db):
        _login(raw_client)
        store = _make_store(db)
        _seed_controverse(db, store, "LBL", "1111111111111", "2222222222222")
        r = raw_client.get("/admin/ui/")
        assert r.status_code == 200
        assert 'data-testid="nrc-counter"' in r.text
        assert "1 à traiter" in r.text


class TestNavLink:
    def test_nav_includes_arbitrage_link(self, raw_client):
        _login(raw_client)
        r = raw_client.get("/admin/ui/")
        assert r.status_code == 200
        assert "Arbitrage NRC" in r.text
        assert "/admin/ui/name-resolutions/queue" in r.text
