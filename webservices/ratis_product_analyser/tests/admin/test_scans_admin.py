"""Tests for the PA admin scan-level endpoints (ARCH_admin_endpoints PR3).

Covers :

- ``GET  /api/v1/admin/receipts/{receipt_id}`` — 360 view
- ``PATCH /api/v1/admin/scans/{scan_id}`` — manual override
- ``POST /api/v1/admin/scans/{scan_id}/replay-match`` — re-run Phase 3

Uses the service-level conftest at ``tests/conftest.py`` for DB,
TestClient and admin auth bypass fixtures.
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from typing import Any

from ratis_core.models.pipeline import ParsedTicket as ParsedTicketModel
from ratis_core.models.scan import Receipt, Scan
from sqlalchemy import text

# ── helpers ───────────────────────────────────────────────────────────────────


def _make_receipt(
    db,
    store,
    *,
    parsed_ticket_id: uuid.UUID | None = None,
) -> Receipt:
    r = Receipt(
        id=uuid.uuid4(),
        store_id=store.id,
        purchased_at=date.today(),
        image_r2_key="fake-receipt-key.jpg",
        parsed_ticket_id=parsed_ticket_id,
    )
    db.add(r)
    db.flush()
    db.commit()
    return r


def _make_scan(
    db,
    *,
    user,
    store,
    receipt,
    parsed_ticket_id: uuid.UUID | None = None,
    status: str = "unresolved",
    match_method: str | None = None,
    product_ean: str | None = None,
    rejected_reason: str | None = "no_fuzzy_candidate",
    scanned_name: str = "NUTELLA",
    price: int = 250,
) -> Scan:
    s = Scan(
        id=uuid.uuid4(),
        user_id=user.id,
        store_id=store.id,
        scanned_name=scanned_name,
        price=price,
        quantity=Decimal("1"),
        scan_type="receipt",
        receipt_id=receipt.id,
        status=status,
        match_method=match_method,
        product_ean=product_ean,
        rejected_reason=rejected_reason if status in ("unresolved", "rejected") else None,
        parsed_ticket_id=parsed_ticket_id,
    )
    db.add(s)
    db.flush()
    db.commit()
    return s


def _make_parsed_ticket(
    db,
    *,
    receipt_id: uuid.UUID | None,
    items: list[dict[str, Any]],
) -> ParsedTicketModel:
    """Persist a minimal parsed_tickets row with items in JSONB."""
    pt_id = uuid.uuid4()
    pt = ParsedTicketModel(
        id=pt_id,
        receipt_id=receipt_id,
        parsed_jsonb={"items": items},
        parsed_jsonb_hash=f"test-hash-{pt_id}",
        raw_ticket_image_hash=f"test-img-{pt_id}",
        ocr_engine_version="test-3.0.0",
        captured_at=date.today(),
    )
    db.add(pt)
    db.flush()
    db.commit()
    return pt


def _make_parsed_item_dict(
    *,
    label: str = "NUTELLA",
    total_cents: int = 250,
    barcode: str | None = None,
) -> dict[str, Any]:
    """Build a ParsedItem-shaped JSONB dict (matches Pydantic ParsedItem)."""
    return {
        "id": str(uuid.uuid4()),
        "raw_label": label,
        "normalized_label": label,
        "quantity": 1,
        "unit_price_cents": None,
        "total_cents": total_cents,
        "barcode": barcode,
        "source_block_ids": [],
        "parsing_issues": [],
    }


def _audit_events(db, *, scan_id: uuid.UUID) -> list[dict[str, Any]]:
    rows = db.execute(
        text(
            "SELECT phase, level, event, payload FROM pipeline_audit_log WHERE scan_id = :sid ORDER BY created_at, id"
        ),
        {"sid": str(scan_id)},
    ).fetchall()
    return [
        {
            "phase": r.phase,
            "level": r.level,
            "event": r.event,
            "payload": r.payload,
        }
        for r in rows
    ]


# ============================================================================
# GET /admin/receipts/{receipt_id} — 360 view
# ============================================================================
class TestGetReceipt360:
    def test_returns_full_state(self, admin_client, db, store, user):
        """Seed receipt + scans + parsed_ticket + audit → endpoint surfaces all."""
        items = [_make_parsed_item_dict(label="NUTELLA", total_cents=250)]
        pt = _make_parsed_ticket(db, receipt_id=None, items=items)
        receipt = _make_receipt(db, store, parsed_ticket_id=pt.id)
        # Sync parsed_ticket.receipt_id post-hoc (the FK is SET NULL, so the
        # ordering doesn't matter as long as the link is set somewhere).
        db.execute(
            text("UPDATE parsed_tickets SET receipt_id = :r WHERE id = :p"),
            {"r": str(receipt.id), "p": str(pt.id)},
        )
        db.commit()
        scan = _make_scan(
            db,
            user=user,
            store=store,
            receipt=receipt,
            parsed_ticket_id=pt.id,
            status="unresolved",
        )

        # Seed an audit event so the 360 view has something to show.
        db.execute(
            text(
                "INSERT INTO pipeline_audit_log "
                "(phase, level, event, parsed_ticket_id, scan_id, payload) "
                "VALUES ('match', 'normal', 'match_completed', :pt, :sid, "
                "        CAST('{\"matched_count\": 0}' AS jsonb))"
            ),
            {"pt": str(pt.id), "sid": str(scan.id)},
        )
        db.commit()

        r = admin_client.get(f"/api/v1/admin/receipts/{receipt.id}")
        assert r.status_code == 200
        body = r.json()

        assert body["receipt"]["id"] == str(receipt.id)
        assert body["store"]["id"] == str(store.id)
        assert body["parsed_ticket"]["id"] == str(pt.id)
        assert body["parsed_ticket"]["parsed_jsonb"]["items"][0]["normalized_label"] == "NUTELLA"
        assert len(body["scans"]) == 1
        assert body["scans"][0]["id"] == str(scan.id)
        assert body["scans"][0]["status"] == "unresolved"
        assert any(e["event"] == "match_completed" for e in body["audit_log"])

    def test_404_when_receipt_not_found(self, admin_client):
        r = admin_client.get(f"/api/v1/admin/receipts/{uuid.uuid4()}")
        assert r.status_code == 404
        assert r.json()["detail"] == "receipt_not_found"

    def test_returns_partial_state_when_no_parsed_ticket(self, admin_client, db, store, user):
        """Legacy receipts without a parsed_ticket should still be inspectable."""
        receipt = _make_receipt(db, store)
        _make_scan(db, user=user, store=store, receipt=receipt, status="accepted")

        r = admin_client.get(f"/api/v1/admin/receipts/{receipt.id}")
        assert r.status_code == 200
        body = r.json()
        assert body["parsed_ticket"] is None
        assert len(body["scans"]) == 1


# ============================================================================
# PATCH /admin/scans/{scan_id} — manual override
# ============================================================================
class TestPatchScanOverride:
    def test_requires_x_admin_operator_header(self, admin_client, db, store, user):
        receipt = _make_receipt(db, store)
        scan = _make_scan(db, user=user, store=store, receipt=receipt)

        r = admin_client.patch(
            f"/api/v1/admin/scans/{scan.id}",
            json={"product_ean": "3017620422003", "status": "matched"},
        )
        assert r.status_code == 400
        assert r.json()["detail"] == "operator_required"

    def test_status_matched_requires_ean_and_method(self, admin_client, db, store, user):
        """Override → matched without EAN must 400 (CHECK ck_scans_matched_requires_ean_method)."""
        receipt = _make_receipt(db, store)
        scan = _make_scan(db, user=user, store=store, receipt=receipt)

        r = admin_client.patch(
            f"/api/v1/admin/scans/{scan.id}",
            json={"status": "matched"},
            headers={"X-Admin-Operator": "guillaume"},
        )
        assert r.status_code == 400
        assert r.json()["detail"] == "matched_requires_ean_and_method"

    def test_status_unresolved_requires_reason(self, admin_client, db, store, user, product):
        """Override → unresolved without rejected_reason must 400."""
        receipt = _make_receipt(db, store)
        scan = _make_scan(
            db,
            user=user,
            store=store,
            receipt=receipt,
            status="matched",
            match_method="fuzzy_strict",
            product_ean=product.ean,
            rejected_reason=None,
        )

        r = admin_client.patch(
            f"/api/v1/admin/scans/{scan.id}",
            json={"status": "unresolved"},
            headers={"X-Admin-Operator": "guillaume"},
        )
        assert r.status_code == 400
        assert r.json()["detail"] == "non_matched_requires_reason"

    def test_override_logs_audit_with_operator(self, admin_client, db, store, user, product):
        receipt = _make_receipt(db, store)
        scan = _make_scan(db, user=user, store=store, receipt=receipt)

        r = admin_client.patch(
            f"/api/v1/admin/scans/{scan.id}",
            json={"status": "matched", "product_ean": product.ean},
            headers={"X-Admin-Operator": "guillaume"},
        )
        assert r.status_code == 200, r.json()
        body = r.json()
        assert body["status"] == "matched"
        assert body["match_method"] == "manual_admin"
        assert body["product_ean"] == product.ean

        events = _audit_events(db, scan_id=scan.id)
        override = [e for e in events if e["event"] == "admin_scan_override"]
        assert len(override) == 1
        evt = override[0]
        assert evt["phase"] == "manual"
        assert evt["payload"]["operator"] == "guillaume"
        assert "diff" in evt["payload"]
        diff = evt["payload"]["diff"]
        assert diff["status"] == {"from": "unresolved", "to": "matched"}
        assert diff["match_method"]["to"] == "manual_admin"
        assert diff["product_ean"]["to"] == product.ean

        # Verify the row was actually committed.
        row = db.execute(
            text("SELECT status, match_method, product_ean FROM scans WHERE id = :sid"),
            {"sid": str(scan.id)},
        ).first()
        assert row.status == "matched"
        assert row.match_method == "manual_admin"
        assert row.product_ean == product.ean

    def test_invalid_status_rejected(self, admin_client, db, store, user):
        receipt = _make_receipt(db, store)
        scan = _make_scan(db, user=user, store=store, receipt=receipt)

        r = admin_client.patch(
            f"/api/v1/admin/scans/{scan.id}",
            json={"status": "frobnicated"},
            headers={"X-Admin-Operator": "guillaume"},
        )
        assert r.status_code == 400
        assert r.json()["detail"] == "invalid_status"

    def test_404_when_scan_not_found(self, admin_client):
        r = admin_client.patch(
            f"/api/v1/admin/scans/{uuid.uuid4()}",
            json={"status": "rejected", "rejected_reason": "manual_dismiss"},
            headers={"X-Admin-Operator": "guillaume"},
        )
        assert r.status_code == 404
        assert r.json()["detail"] == "scan_not_found"


# ============================================================================
# POST /admin/scans/{scan_id}/replay-match — re-run Phase 3
# ============================================================================
class TestReplayMatch:
    def test_requires_x_admin_operator_header(self, admin_client, db, store, user):
        receipt = _make_receipt(db, store)
        scan = _make_scan(db, user=user, store=store, receipt=receipt)

        r = admin_client.post(f"/api/v1/admin/scans/{scan.id}/replay-match")
        assert r.status_code == 400
        assert r.json()["detail"] == "operator_required"

    def test_404_when_scan_not_found(self, admin_client):
        r = admin_client.post(
            f"/api/v1/admin/scans/{uuid.uuid4()}/replay-match",
            headers={"X-Admin-Operator": "guillaume"},
        )
        assert r.status_code == 404
        assert r.json()["detail"] == "scan_not_found"

    def test_409_when_scan_has_no_parsed_ticket(self, admin_client, db, store, user):
        receipt = _make_receipt(db, store)
        scan = _make_scan(db, user=user, store=store, receipt=receipt)
        # parsed_ticket_id intentionally NULL — simulates a legacy v2 scan.

        r = admin_client.post(
            f"/api/v1/admin/scans/{scan.id}/replay-match",
            headers={"X-Admin-Operator": "guillaume"},
        )
        assert r.status_code == 409
        assert r.json()["detail"] == "no_parsed_ticket"

    def test_re_runs_phase3_via_barcode(self, admin_client, db, store, user, product):
        """Seed an unresolved scan whose ParsedItem has a barcode that now
        resolves in ``products`` → replay must transition it to 'matched'
        with ``match_method='barcode'``."""
        item_dict = _make_parsed_item_dict(
            label="NUTELLA",
            total_cents=250,
            barcode=product.ean,  # the fixture-product is now registered
        )
        pt = _make_parsed_ticket(db, receipt_id=None, items=[item_dict])
        receipt = _make_receipt(db, store, parsed_ticket_id=pt.id)
        db.execute(
            text("UPDATE parsed_tickets SET receipt_id = :r WHERE id = :p"),
            {"r": str(receipt.id), "p": str(pt.id)},
        )
        db.commit()
        scan = _make_scan(
            db,
            user=user,
            store=store,
            receipt=receipt,
            parsed_ticket_id=pt.id,
            status="unresolved",
            scanned_name="NUTELLA",
            price=250,
        )

        r = admin_client.post(
            f"/api/v1/admin/scans/{scan.id}/replay-match",
            headers={"X-Admin-Operator": "guillaume"},
        )
        assert r.status_code == 200, r.json()
        body = r.json()
        assert body["status"] == "matched"
        assert body["match_method"] == "barcode"
        assert body["product_ean"] == product.ean

        # Verify persistence.
        row = db.execute(
            text("SELECT status, match_method, product_ean FROM scans WHERE id = :sid"),
            {"sid": str(scan.id)},
        ).first()
        assert row.status == "matched"
        assert row.match_method == "barcode"
        assert row.product_ean == product.ean

    def test_logs_audit(self, admin_client, db, store, user, product):
        item_dict = _make_parsed_item_dict(label="NUTELLA", total_cents=250, barcode=product.ean)
        pt = _make_parsed_ticket(db, receipt_id=None, items=[item_dict])
        receipt = _make_receipt(db, store, parsed_ticket_id=pt.id)
        db.execute(
            text("UPDATE parsed_tickets SET receipt_id = :r WHERE id = :p"),
            {"r": str(receipt.id), "p": str(pt.id)},
        )
        db.commit()
        scan = _make_scan(
            db,
            user=user,
            store=store,
            receipt=receipt,
            parsed_ticket_id=pt.id,
            scanned_name="NUTELLA",
            price=250,
        )

        r = admin_client.post(
            f"/api/v1/admin/scans/{scan.id}/replay-match",
            headers={"X-Admin-Operator": "guillaume"},
        )
        assert r.status_code == 200

        events = _audit_events(db, scan_id=scan.id)
        replay = [e for e in events if e["event"] == "admin_replay_match"]
        assert len(replay) == 1
        evt = replay[0]
        assert evt["phase"] == "manual"
        assert evt["payload"]["operator"] == "guillaume"
        assert evt["payload"]["outcome"]["status"] == "matched"
        assert evt["payload"]["outcome"]["match_method"] == "barcode"


# ============================================================================
# Auth — ADMIN_API_KEY missing must yield 403 across the board
# ============================================================================
class TestAdminAuth:
    def test_get_receipt_requires_admin_key(self, raw_client):
        r = raw_client.get(f"/api/v1/admin/receipts/{uuid.uuid4()}")
        assert r.status_code == 403
        assert r.json()["detail"] == "forbidden"

    def test_patch_scan_requires_admin_key(self, raw_client):
        r = raw_client.patch(
            f"/api/v1/admin/scans/{uuid.uuid4()}",
            json={"status": "matched"},
            headers={"X-Admin-Operator": "guillaume"},
        )
        assert r.status_code == 403
        assert r.json()["detail"] == "forbidden"

    def test_replay_match_requires_admin_key(self, raw_client):
        r = raw_client.post(
            f"/api/v1/admin/scans/{uuid.uuid4()}/replay-match",
            headers={"X-Admin-Operator": "guillaume"},
        )
        assert r.status_code == 403
        assert r.json()["detail"] == "forbidden"

    def test_get_receipt_with_correct_key(self, raw_client):
        """Sanity check : a 401 isn't being returned in the auth path."""
        r = raw_client.get(
            f"/api/v1/admin/receipts/{uuid.uuid4()}",
            headers={"Authorization": "Bearer test-admin-key-padded-to-32-chars-min"},
        )
        assert r.status_code == 404  # passes auth, fails lookup
        assert r.json()["detail"] == "receipt_not_found"


# Pytest collection sanity
def test_module_collects():
    assert True
