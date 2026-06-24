"""Tests for the NRC bloc D admin endpoints.

Covers all five JSON endpoints under ``/api/v1/admin/name-resolutions/*`` :

- ``GET /queue`` — pagination, state filter, ordering, top_eans aggregate
- ``GET /unmatched`` — fuzzy candidates aggregation, grouping
- ``GET /{store_id}/{label}`` — detail (resolutions + audit timeline)
- ``POST /resolve`` — manual_admin write + state transition
- ``POST /reject-challenges`` — re-promotion + special audit payload
- ``POST /{store_id}/{label}/escalate`` — flag-only audit

Auth gate behaviour is exercised on each endpoint family (one test per
family is enough — the same dep is wired everywhere).

Fixtures use the same DB-layer scaffolding as
:mod:`tests.test_ledger_writes` : direct INSERTs into
``product_name_resolutions`` and ``pipeline_audit_log`` to seed
deterministic state without spinning up the matcher cascade.
"""

from __future__ import annotations

import json
import uuid
from datetime import date
from decimal import Decimal
from typing import Any

import pytest
from ratis_core.models.product import Product
from ratis_core.models.scan import Receipt, Scan
from ratis_core.models.store import Store
from ratis_core.models.user import User
from sqlalchemy import text

# ============================================================================
# Helpers — DB seeders
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
    """Create a User with a unique email — emails carry a uuid4 component
    so callers don't have to coordinate ``suffix`` values across helpers."""
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


def _make_product(db, *, ean: str, name: str) -> Product:
    p = Product(ean=ean, name=name, source="off")
    db.add(p)
    db.flush()
    db.commit()
    return p


def _make_scan(db, *, store_id, user_id, scanned_name: str, candidate_eans=None) -> Scan:
    """Create a Scan for tests.

    The ``candidate_eans`` keyword is preserved for back-compat with
    existing test bodies but is now a no-op (the column was dropped in
    the matcher consensus-only refonte 2026-05-02).
    """
    del candidate_eans  # column dropped — kept in signature for callers
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


def _record_ledger(
    db,
    *,
    scan_id,
    store_id,
    label: str,
    ean: str,
    user_id,
    method: str = "barcode",
) -> None:
    """Direct INSERT — bypasses ``record_resolution`` so tests can seed
    state without re-triggering the audit emission. The Bloc C test
    suite exercises the writer ; here we want explicit control over
    state for queue / detail assertions.
    """
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


def _emit_state_event(
    db,
    *,
    store_id,
    label: str,
    to_state: str,
    top1_ean: str,
    from_state: str | None = None,
) -> None:
    """Direct INSERT into ``pipeline_audit_log`` to set up an UNVERIFIED
    case (= one verified event in history).
    """
    payload = {
        "event": "consensus_state_changed",
        "store_id": str(store_id),
        "normalized_label": label,
        "from_state": from_state,
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


def _seed_verified(db, store, label, ean, user_count=3):
    users = [_make_user(db, suffix=f"v{i}") for i in range(user_count)]
    scans = [_make_scan(db, store_id=store.id, user_id=u.id, scanned_name=label) for u in users]
    for s, u in zip(scans, users, strict=False):
        _record_ledger(
            db,
            scan_id=s.id,
            store_id=store.id,
            label=label,
            ean=ean,
            user_id=u.id,
            method="barcode",
        )
    _emit_state_event(db, store_id=store.id, label=label, to_state="verified", top1_ean=ean)
    return users, scans


def _ensure_admin_anchor_product(db, ean: str) -> None:
    """Bug 6 — seed a Product row for ``ean`` so the synthetic admin-
    anchor scan path (``_create_admin_anchor_scan``) satisfies the FK
    ``scans_product_ean_fkey``. The fallback path is triggered when
    every existing scan for ``(store, label)`` already has a ledger
    row — which is the case for every test that drives the resolve /
    reject_challenges endpoints after ``_seed_controverse`` /
    ``_seed_verified`` (each seeded scan immediately gets a sibling
    ``product_name_resolutions`` row via ``_record_ledger``).

    Call this helper from any test that POSTs to
    ``/api/v1/admin/name-resolutions/resolve`` or
    ``/api/v1/admin/name-resolutions/reject-challenges``, passing the
    ``target_ean`` (resolve) or the previously-verified EAN
    (reject_challenges) so the FK resolves.
    """
    db.execute(
        text("INSERT INTO products (ean, name, source) VALUES (:e, 'Bug6 anchor', 'off') ON CONFLICT (ean) DO NOTHING"),
        {"e": ean},
    )
    db.commit()


def _seed_controverse(db, store, label, ean_a, ean_b, *, count_a=2, count_b=2):
    """Quorum reached but lead-factor / pct fails — pure cold-start
    divergence, no verified history.

    Defaults : 2-2 split — meets quorum (4 users ≥ 3) but fails both
    convergence checks (50%, lead 1.0×). A single admin override
    weight=5 on EAN-A → (2+5)/(2+5+2) = 77.8% which still fails the
    80% threshold ; bump count_a/count_b for tests that need to flip
    state by admin action.
    """
    users_a = [_make_user(db, suffix=f"ca{i}") for i in range(count_a)]
    users_b = [_make_user(db, suffix=f"cb{i}") for i in range(count_b)]
    for u in users_a:
        s = _make_scan(db, store_id=store.id, user_id=u.id, scanned_name=label)
        _record_ledger(
            db,
            scan_id=s.id,
            store_id=store.id,
            label=label,
            ean=ean_a,
            user_id=u.id,
        )
    for u in users_b:
        s = _make_scan(db, store_id=store.id, user_id=u.id, scanned_name=label)
        _record_ledger(
            db,
            scan_id=s.id,
            store_id=store.id,
            label=label,
            ean=ean_b,
            user_id=u.id,
        )
    return users_a, users_b


def _seed_unverified(db, store, label, prev_ean, new_ean, *, challenger_count=3):
    """Was VERIFIED on prev_ean ; then ``challenger_count`` users vote for
    new_ean → state falls to UNVERIFIED.
    """
    _seed_verified(db, store, label, prev_ean)
    challenger_users = [_make_user(db, suffix=f"ch{i}") for i in range(challenger_count)]
    for u in challenger_users:
        s = _make_scan(db, store_id=store.id, user_id=u.id, scanned_name=label)
        _record_ledger(db, scan_id=s.id, store_id=store.id, label=label, ean=new_ean, user_id=u.id)
    return challenger_users


# ============================================================================
# Auth gate
# ============================================================================
class TestAuthGate:
    def test_queue_requires_admin_key(self, raw_client):
        r = raw_client.get("/api/v1/admin/name-resolutions/queue")
        assert r.status_code == 403

    def test_unmatched_requires_admin_key(self, raw_client):
        r = raw_client.get("/api/v1/admin/name-resolutions/unmatched")
        assert r.status_code == 403

    def test_resolve_requires_admin_key(self, raw_client):
        r = raw_client.post(
            "/api/v1/admin/name-resolutions/resolve",
            json={
                "store_id": str(uuid.uuid4()),
                "normalized_label": "X",
                "target_ean": "1",
            },
        )
        assert r.status_code == 403

    def test_resolve_requires_operator_header(self, admin_client):
        r = admin_client.post(
            "/api/v1/admin/name-resolutions/resolve",
            json={
                "store_id": str(uuid.uuid4()),
                "normalized_label": "X",
                "target_ean": "1",
            },
        )
        assert r.status_code == 400
        assert r.json()["detail"] == "operator_required"

    def test_reject_challenges_requires_operator_header(self, admin_client):
        r = admin_client.post(
            "/api/v1/admin/name-resolutions/reject-challenges",
            json={"store_id": str(uuid.uuid4()), "normalized_label": "X"},
        )
        assert r.status_code == 400
        assert r.json()["detail"] == "operator_required"


# ============================================================================
# GET /queue
# ============================================================================
class TestQueue:
    def test_queue_empty_when_no_data(self, admin_client):
        r = admin_client.get("/api/v1/admin/name-resolutions/queue")
        assert r.status_code == 200
        body = r.json()
        assert body["items"] == []
        assert body["total"] == 0
        assert body["limit"] == 50
        assert body["offset"] == 0

    def test_queue_lists_controverse_pair(self, admin_client, db):
        store = _make_store(db)
        _seed_controverse(db, store, "ICE TEA 33CL", "1111111111111", "2222222222222")
        r = admin_client.get("/api/v1/admin/name-resolutions/queue")
        assert r.status_code == 200
        body = r.json()
        assert body["total"] == 1
        item = body["items"][0]
        assert item["current_state"] == "controverse"
        assert item["normalized_label"] == "ICE TEA 33CL"
        # Default 2-2 split → 50/50
        eans = sorted([(t["ean"], t["weighted_count"]) for t in item["top_eans"]])
        assert eans == [("1111111111111", 2), ("2222222222222", 2)]
        assert item["challenger_count"] == 0
        assert item["previously_verified_ean"] is None

    def test_queue_lists_unverified_with_challenger_count(self, admin_client, db):
        store = _make_store(db)
        challengers = _seed_unverified(db, store, "HIPRO A BRE", "1111111111111", "2222222222222")
        r = admin_client.get("/api/v1/admin/name-resolutions/queue")
        assert r.status_code == 200
        body = r.json()
        # Filter to our label since the verified-only labels could survive
        items = [i for i in body["items"] if i["normalized_label"] == "HIPRO A BRE"]
        assert len(items) == 1
        item = items[0]
        assert item["current_state"] == "unverified"
        assert item["previously_verified_ean"] == "1111111111111"
        assert item["challenger_count"] == len(challengers)

    def test_queue_filter_state_controverse_excludes_unverified(self, admin_client, db):
        store = _make_store(db)
        _seed_controverse(db, store, "LABEL-CTV", "1111111111111", "2222222222222")
        _seed_unverified(db, store, "LABEL-UNV", "3333", "4444")
        r = admin_client.get("/api/v1/admin/name-resolutions/queue?state=controverse")
        assert r.status_code == 200
        labels = [i["normalized_label"] for i in r.json()["items"]]
        assert "LABEL-CTV" in labels
        assert "LABEL-UNV" not in labels

    def test_queue_filter_state_unverified_excludes_controverse(self, admin_client, db):
        store = _make_store(db)
        _seed_controverse(db, store, "LABEL-CTV", "1111111111111", "2222222222222")
        _seed_unverified(db, store, "LABEL-UNV", "3333", "4444")
        r = admin_client.get("/api/v1/admin/name-resolutions/queue?state=unverified")
        assert r.status_code == 200
        labels = [i["normalized_label"] for i in r.json()["items"]]
        assert "LABEL-UNV" in labels
        assert "LABEL-CTV" not in labels

    def test_queue_invalid_state_returns_400(self, admin_client):
        r = admin_client.get("/api/v1/admin/name-resolutions/queue?state=foo")
        assert r.status_code == 400
        assert r.json()["detail"] == "invalid_state"

    def test_queue_orders_unverified_first(self, admin_client, db):
        store = _make_store(db)
        _seed_controverse(db, store, "LABEL-CTV", "1111111111111", "2222222222222")
        _seed_unverified(db, store, "LABEL-UNV", "3333", "4444")
        r = admin_client.get("/api/v1/admin/name-resolutions/queue")
        body = r.json()
        # First item must be UNVERIFIED
        assert body["items"][0]["current_state"] == "unverified"

    def test_queue_top_eans_pct_calculation(self, admin_client, db):
        """3 users on EAN-A, 1 user on EAN-B → 75% / 25%."""
        store = _make_store(db)
        users_a = [_make_user(db, suffix=f"a{i}") for i in range(3)]
        user_b = _make_user(db, suffix="b0")
        for u in users_a:
            s = _make_scan(db, store_id=store.id, user_id=u.id, scanned_name="X")
            _record_ledger(db, scan_id=s.id, store_id=store.id, label="X", ean="A", user_id=u.id)
        s = _make_scan(db, store_id=store.id, user_id=user_b.id, scanned_name="X")
        _record_ledger(db, scan_id=s.id, store_id=store.id, label="X", ean="B", user_id=user_b.id)
        r = admin_client.get("/api/v1/admin/name-resolutions/queue")
        body = r.json()
        assert body["total"] == 1
        item = body["items"][0]
        # 75% / 25% — but lead is only 3.0× → above lead_factor=2.0 + pct
        # 75% < 80% → still controverse
        assert item["current_state"] == "controverse"
        eans = {t["ean"]: t for t in item["top_eans"]}
        assert eans["A"]["pct"] == 75.0
        assert eans["B"]["pct"] == 25.0

    def test_queue_pagination(self, admin_client, db):
        store = _make_store(db)
        for i in range(3):
            _seed_controverse(db, store, f"LBL-{i}", f"{i}A", f"{i}B")
        r = admin_client.get("/api/v1/admin/name-resolutions/queue?limit=2&offset=0")
        body = r.json()
        assert body["total"] == 3
        assert len(body["items"]) == 2

    def test_queue_filter_by_store_id(self, admin_client, db):
        store_a = _make_store(db, name="A")
        store_b = _make_store(db, name="B")
        _seed_controverse(db, store_a, "LBL", "1A", "2A")
        _seed_controverse(db, store_b, "LBL", "1B", "2B")
        r = admin_client.get(f"/api/v1/admin/name-resolutions/queue?store_id={store_a.id}")
        body = r.json()
        store_ids = {i["store_id"] for i in body["items"]}
        assert store_ids == {str(store_a.id)}

    def test_queue_top_eans_includes_product_name(self, admin_client, db):
        store = _make_store(db)
        _make_product(db, ean="1111111111111", name="Eau Cristaline")
        _seed_controverse(db, store, "EAU 1L", "1111111111111", "2222222222222")
        r = admin_client.get("/api/v1/admin/name-resolutions/queue")
        item = r.json()["items"][0]
        names = {t["ean"]: t["product_name"] for t in item["top_eans"]}
        assert names["1111111111111"] == "Eau Cristaline"
        assert names["2222222222222"] is None  # not in products


# ============================================================================
# GET /unmatched
# ============================================================================
class TestUnmatched:
    """Post matcher consensus-only refonte (2026-05-02), the unmatched
    queue surfaces every scan without a consensus ledger row — the
    legacy filter on ``scans.candidate_eans IS NOT NULL`` is gone, and
    ``top_candidates`` is now always an empty list (kept for response-
    shape back-compat with the admin UI).
    """

    def test_unmatched_lists_scans_without_consensus(self, admin_client, db):
        store = _make_store(db)
        u = _make_user(db, suffix="u")
        _make_scan(
            db,
            store_id=store.id,
            user_id=u.id,
            scanned_name="GHOST LABEL",
        )
        r = admin_client.get("/api/v1/admin/name-resolutions/unmatched")
        assert r.status_code == 200
        body = r.json()
        assert body["total"] == 1
        item = body["items"][0]
        assert item["normalized_label"] == "GHOST LABEL"
        assert item["scan_count"] == 1
        # ``top_candidates`` is preserved as an empty list now.
        assert item["top_candidates"] == []

    def test_unmatched_excludes_resolved_labels(self, admin_client, db):
        store = _make_store(db)
        u = _make_user(db, suffix="u2")
        # Resolved : has a ledger row
        s = _make_scan(
            db,
            store_id=store.id,
            user_id=u.id,
            scanned_name="LBL-RESOLVED",
        )
        _record_ledger(
            db,
            scan_id=s.id,
            store_id=store.id,
            label="LBL-RESOLVED",
            ean="1111111111111",
            user_id=u.id,
        )
        # Unmatched : no ledger row
        _make_scan(
            db,
            store_id=store.id,
            user_id=u.id,
            scanned_name="LBL-UNMATCHED",
        )
        r = admin_client.get("/api/v1/admin/name-resolutions/unmatched")
        labels = {i["normalized_label"] for i in r.json()["items"]}
        assert labels == {"LBL-UNMATCHED"}

    def test_unmatched_aggregates_scan_count(self, admin_client, db):
        """Multiple scans for the same ``(store, label)`` aggregate into
        one queue item with ``scan_count`` == n.
        """
        store = _make_store(db)
        u1 = _make_user(db, suffix="agg1")
        u2 = _make_user(db, suffix="agg2")
        _make_scan(db, store_id=store.id, user_id=u1.id, scanned_name="LBL")
        _make_scan(db, store_id=store.id, user_id=u2.id, scanned_name="LBL")
        r = admin_client.get("/api/v1/admin/name-resolutions/unmatched")
        item = r.json()["items"][0]
        assert item["scan_count"] == 2
        # No more candidate aggregation post-refonte.
        assert item["top_candidates"] == []


# ============================================================================
# GET /{store_id}/{label}
# ============================================================================
class TestDetail:
    def test_detail_404_when_label_unknown(self, admin_client):
        r = admin_client.get(f"/api/v1/admin/name-resolutions/{uuid.uuid4()}/UNKNOWN")
        assert r.status_code == 404
        assert r.json()["detail"] == "label_not_found"

    def test_detail_returns_resolutions_and_events(self, admin_client, db):
        store = _make_store(db)
        challengers = _seed_unverified(db, store, "MY LABEL", "1111111111111", "2222222222222")
        r = admin_client.get(f"/api/v1/admin/name-resolutions/{store.id}/MY LABEL")
        assert r.status_code == 200
        body = r.json()
        assert body["current_state"] == "unverified"
        assert body["previously_verified_ean"] == "1111111111111"
        # 3 verified-on-prev + 3 challengers = 6 ledger rows
        assert len(body["resolutions"]) == 6
        # 1 audit event seeded
        assert len(body["events"]) >= 1
        # Challenger flag is set on the post-verified rows
        challenger_user_ids = {str(u.id) for u in challengers}
        marked_challengers = {r["user_id"] for r in body["resolutions"] if r["is_challenger"]}
        assert marked_challengers == challenger_user_ids


# ============================================================================
# POST /resolve
# ============================================================================
class TestResolve:
    def test_resolve_creates_manual_admin_row_and_promotes(self, admin_client, db):
        # 3-1 split → controverse (75% < 80%). After admin push (weight 5)
        # on EAN-A : (3+5)/(3+5+1) = 88.9% → verified.
        store = _make_store(db)
        _seed_controverse(db, store, "LBL", "1111111111111", "2222222222222", count_a=3, count_b=1)
        _ensure_admin_anchor_product(db, "1111111111111")
        r = admin_client.post(
            "/api/v1/admin/name-resolutions/resolve",
            json={
                "store_id": str(store.id),
                "normalized_label": "LBL",
                "target_ean": "1111111111111",
                "operator_note": "checked photo",
            },
            headers={"X-Admin-Operator": "guillaume"},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["target_ean"] == "1111111111111"
        assert body["to_state"] == "verified"
        # Ledger now has a manual_admin row
        n = db.execute(
            text(
                "SELECT COUNT(*) AS n FROM product_name_resolutions "
                "WHERE store_id = :sid AND normalized_label = :label "
                "  AND match_method = 'manual_admin'"
            ),
            {"sid": str(store.id), "label": "LBL"},
        ).scalar_one()
        assert n == 1
        # Bug 6 regression — the synthetic admin-anchor scan path is
        # reached here (every existing seeded scan already has a ledger
        # row), so a fresh ``scan_type='manual'`` row must exist with
        # the CHECK-respecting shape : ``scanned_name IS NULL`` +
        # ``product_ean = target_ean``. Pre-fix the prod code path
        # inserted ``scanned_name=normalized_label`` + NULL EAN which
        # silently violated ``manual_no_scanned_name`` in real PG.
        anchor_row = db.execute(
            text(
                "SELECT scanned_name, product_ean FROM scans "
                "WHERE store_id = :sid AND scan_type = 'manual' "
                "  AND scanned_name IS NULL "
                "ORDER BY scanned_at DESC LIMIT 1"
            ),
            {"sid": str(store.id)},
        ).first()
        assert anchor_row is not None, "synthetic admin-anchor scan missing"
        assert anchor_row.scanned_name is None
        assert anchor_row.product_ean == "1111111111111"

    def test_resolve_404_when_no_anchor_scan(self, admin_client):
        r = admin_client.post(
            "/api/v1/admin/name-resolutions/resolve",
            json={
                "store_id": str(uuid.uuid4()),
                "normalized_label": "GHOST",
                "target_ean": "1",
            },
            headers={"X-Admin-Operator": "guillaume"},
        )
        assert r.status_code == 404
        assert r.json()["detail"] == "label_not_found"

    def test_resolve_idempotent_state_stays_verified(self, admin_client, db):
        store = _make_store(db)
        _seed_controverse(db, store, "LBL", "1111111111111", "2222222222222", count_a=3, count_b=1)
        _ensure_admin_anchor_product(db, "1111111111111")
        for _ in range(2):
            r = admin_client.post(
                "/api/v1/admin/name-resolutions/resolve",
                json={
                    "store_id": str(store.id),
                    "normalized_label": "LBL",
                    "target_ean": "1111111111111",
                },
                headers={"X-Admin-Operator": "guillaume"},
            )
            assert r.status_code == 200
        # Second call still verified (the ON CONFLICT skip prevents row
        # explosion ; the state is stable).
        assert r.json()["to_state"] == "verified"

    def test_resolve_records_admin_action_event(self, admin_client, db):
        store = _make_store(db)
        _seed_controverse(db, store, "LBL", "1111111111111", "2222222222222")
        _ensure_admin_anchor_product(db, "1111111111111")
        admin_client.post(
            "/api/v1/admin/name-resolutions/resolve",
            json={
                "store_id": str(store.id),
                "normalized_label": "LBL",
                "target_ean": "1111111111111",
                "operator_note": "manual override",
            },
            headers={"X-Admin-Operator": "guillaume"},
        )
        rows = db.execute(
            text(
                """
                SELECT payload FROM pipeline_audit_log
                WHERE event = 'admin_name_resolution_resolve'
                """
            )
        ).fetchall()
        assert len(rows) == 1
        payload = rows[0].payload
        assert payload["operator"] == "guillaume"
        assert payload["operator_note"] == "manual override"
        assert payload["target_ean"] == "1111111111111"

    def test_resolve_rejects_long_operator_note(self, admin_client, db):
        store = _make_store(db)
        _seed_controverse(db, store, "LBL", "1111111111111", "2222222222222")
        r = admin_client.post(
            "/api/v1/admin/name-resolutions/resolve",
            json={
                "store_id": str(store.id),
                "normalized_label": "LBL",
                "target_ean": "1111111111111",
                "operator_note": "x" * 301,
            },
            headers={"X-Admin-Operator": "guillaume"},
        )
        assert r.status_code == 400
        assert r.json()["detail"] == "operator_note_too_long"


# ============================================================================
# POST /reject-challenges
# ============================================================================
class TestRejectChallenges:
    def test_reject_challenges_requires_unverified_state(self, admin_client, db):
        store = _make_store(db)
        _seed_controverse(db, store, "LBL", "1111111111111", "2222222222222")
        r = admin_client.post(
            "/api/v1/admin/name-resolutions/reject-challenges",
            json={"store_id": str(store.id), "normalized_label": "LBL"},
            headers={"X-Admin-Operator": "g"},
        )
        assert r.status_code == 422
        assert r.json()["detail"] == "state_mismatch"

    def test_reject_challenges_re_promotes_previous_ean(self, admin_client, db):
        # Verified-on-1111 (3 users) + 1 challenger on 2222 → unverified.
        # Admin push 1111 (weight 5) → (3+5)/(3+5+1)=88.9% → verified.
        store = _make_store(db)
        _seed_unverified(db, store, "LBL", prev_ean="1111111111111", new_ean="2222222222222", challenger_count=1)
        _ensure_admin_anchor_product(db, "1111111111111")
        r = admin_client.post(
            "/api/v1/admin/name-resolutions/reject-challenges",
            json={
                "store_id": str(store.id),
                "normalized_label": "LBL",
                "operator_note": "false bag",
            },
            headers={"X-Admin-Operator": "g"},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["previously_verified_ean"] == "1111111111111"
        assert body["to_state"] == "verified"
        assert len(body["rejected_user_ids"]) == 1

    def test_reject_challenges_emits_special_audit_payload(self, admin_client, db):
        store = _make_store(db)
        _seed_unverified(db, store, "LBL", prev_ean="1111111111111", new_ean="2222222222222", challenger_count=1)
        _ensure_admin_anchor_product(db, "1111111111111")
        admin_client.post(
            "/api/v1/admin/name-resolutions/reject-challenges",
            json={
                "store_id": str(store.id),
                "normalized_label": "LBL",
                "operator_note": "false bag",
            },
            headers={"X-Admin-Operator": "g"},
        )
        rows = db.execute(
            text(
                """
                SELECT payload FROM pipeline_audit_log
                WHERE event = 'consensus_state_changed'
                  AND payload->>'normalized_label' = 'LBL'
                  AND payload->>'action' = 'challenges_rejected'
                """
            )
        ).fetchall()
        assert len(rows) == 1
        payload = rows[0].payload
        assert payload["action"] == "challenges_rejected"
        assert payload["operator_note"] == "false bag"
        assert payload["operator"] == "g"
        assert isinstance(payload["rejected_user_ids"], list)
        assert len(payload["rejected_user_ids"]) == 1


# ============================================================================
# POST /escalate
# ============================================================================
class TestEscalate:
    def test_escalate_emits_audit_event(self, admin_client, db):
        store = _make_store(db)
        _seed_controverse(db, store, "LBL", "1111111111111", "2222222222222")
        r = admin_client.post(
            f"/api/v1/admin/name-resolutions/{store.id}/LBL/escalate",
            json={"operator_note": "look here"},
            headers={"X-Admin-Operator": "g"},
        )
        assert r.status_code == 200
        rows = db.execute(
            text("SELECT payload FROM pipeline_audit_log WHERE event = 'admin_name_resolution_escalate'")
        ).fetchall()
        assert len(rows) == 1
        assert rows[0].payload["operator"] == "g"
        assert rows[0].payload["operator_note"] == "look here"

    def test_escalate_404_unknown_label(self, admin_client):
        r = admin_client.post(
            f"/api/v1/admin/name-resolutions/{uuid.uuid4()}/UNKNOWN/escalate",
            json={},
            headers={"X-Admin-Operator": "g"},
        )
        assert r.status_code == 404


# ============================================================================
# Helper present mostly to silence lint when imports look unused
# ============================================================================
def _silence_unused() -> Any:
    return Decimal, pytest
