"""Tests for the PA admin observability endpoints (ARCH_admin_endpoints PR4).

Covers :

- ``GET  /api/v1/admin/pipeline/audit-log`` — paginated lineage debug
- ``GET  /api/v1/admin/parsed-tickets/{parsed_ticket_id}`` — full state
- ``GET  /api/v1/admin/parsed-tickets`` — browse + derived status filter
- ``POST /api/v1/admin/parsed-tickets/{parsed_ticket_id}/replay``
- ``GET  /api/v1/admin/tasks/{task_id}/status``
- The Celery task ``replay_parsed_ticket`` (direct calls, no HTTP)

Uses the service-level conftest at ``tests/conftest.py`` for DB +
TestClient + admin auth bypass fixtures.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any
from urllib.parse import quote

import pytest
from ratis_core.models.pipeline import ParsedTicket as ParsedTicketModel
from ratis_core.models.scan import Receipt, Scan
from sqlalchemy import text

# ── helpers ──────────────────────────────────────────────────────────────────


def _make_receipt(db, store, *, parsed_ticket_id: uuid.UUID | None = None) -> Receipt:
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
    scanned_at: datetime | None = None,
) -> Scan:
    kwargs: dict[str, Any] = {
        "id": uuid.uuid4(),
        "user_id": user.id,
        "store_id": store.id,
        "scanned_name": scanned_name,
        "price": price,
        "quantity": Decimal("1"),
        "scan_type": "receipt",
        "receipt_id": receipt.id,
        "status": status,
        "match_method": match_method,
        "product_ean": product_ean,
        "rejected_reason": rejected_reason if status in ("unresolved", "rejected") else None,
        "parsed_ticket_id": parsed_ticket_id,
    }
    if scanned_at is not None:
        # The unique constraint on (user_id, store_id, product_ean, scanned_at)
        # collides when several scans of the same product land on the same
        # millisecond ; tests pass an explicit timestamp to disambiguate.
        kwargs["scanned_at"] = scanned_at
    s = Scan(**kwargs)
    db.add(s)
    db.flush()
    db.commit()
    return s


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


def _make_full_parsed_jsonb(
    *,
    receipt_id: uuid.UUID,
    items: list[dict[str, Any]],
) -> dict[str, Any]:
    """Produce a ``parsed_jsonb`` dict that round-trips through
    ``ParsedTicket.model_validate`` — used by the replay task tests
    where the JSONB must be a faithful Pydantic dump.

    The fixture pre-fills the **mandatory signals** required by the
    anti-fraud PR3 fingerprint rule (``header.brand`` + a date in
    ``purchased_at``). A real parsed_ticket on disk would never have
    landed without these — the POST /scan/receipt path rejects them
    before persistence — so the fixture mirrors a realistic post-PR3
    on-disk shape.

    PR4 adds an age-cap reject (``consensus.ticket_max_age_days = 7``)
    so the ``purchased_at`` value is anchored to "now - 1 day" — a
    hardcoded date would silently fail the replay path once the wall
    clock walks past the 7-day window.
    """
    _recent = (datetime.now(UTC) - timedelta(days=1)).isoformat()
    return {
        "id": str(uuid.uuid4()),
        "receipt_id": str(receipt_id),
        "items": items,
        "header": {
            "brand": "INTERMARCHE",
            "address_line": None,
            "postcode": None,
            "city": None,
            "phone": None,
            "siret": None,
            "raw_lines": [],
            "source_block_ids": [],
        },
        "footer": {
            "total_cents": sum(i["total_cents"] for i in items) or None,
            "vat_breakdown": [],
            "payment_method": None,
            "item_count_declared": None,
            "barcode": None,
            "source_block_ids": [],
        },
        "purchased_at": _recent,
        "raw_ticket_image_hash": "test-image-hash-abc",
        "parsed_jsonb_hash": None,
    }


def _make_parsed_ticket(
    db,
    *,
    receipt_id: uuid.UUID | None,
    items: list[dict[str, Any]] | None = None,
    parsed_jsonb_full: dict[str, Any] | None = None,
    captured_at: datetime | None = None,
) -> ParsedTicketModel:
    """Persist a parsed_tickets row.

    When ``parsed_jsonb_full`` is provided it is used as-is (preserves
    the full Pydantic shape required by the replay task). Otherwise
    a minimal ``{"items": [...]}`` dict is stored — sufficient for the
    GET endpoints.
    """
    pt_id = uuid.uuid4()
    if parsed_jsonb_full is None:
        parsed_jsonb_full = {"items": items or []}
    pt = ParsedTicketModel(
        id=pt_id,
        receipt_id=receipt_id,
        parsed_jsonb=parsed_jsonb_full,
        parsed_jsonb_hash=f"test-hash-{pt_id}",
        raw_ticket_image_hash=f"test-img-{pt_id}",
        ocr_engine_version="test-3.0.0",
        captured_at=captured_at or datetime(2026, 5, 1, 12, 0, tzinfo=UTC),
    )
    db.add(pt)
    db.flush()
    db.commit()
    return pt


def _insert_audit(
    db,
    *,
    phase: str = "match",
    level: str = "normal",
    event: str = "match_completed",
    parsed_ticket_id: uuid.UUID | None = None,
    scan_id: uuid.UUID | None = None,
    payload: dict[str, Any] | None = None,
    created_at: datetime | None = None,
) -> uuid.UUID:
    """Insert one ``pipeline_audit_log`` row — returns the inserted id."""
    audit_id = uuid.uuid4()
    if created_at is None:
        db.execute(
            text(
                "INSERT INTO pipeline_audit_log "
                "(id, phase, level, event, parsed_ticket_id, scan_id, payload) "
                "VALUES (:id, :phase, :level, :event, :pt, :scan, "
                "        CAST(:payload AS jsonb))"
            ),
            {
                "id": str(audit_id),
                "phase": phase,
                "level": level,
                "event": event,
                "pt": str(parsed_ticket_id) if parsed_ticket_id else None,
                "scan": str(scan_id) if scan_id else None,
                "payload": json.dumps(payload or {}),
            },
        )
    else:
        db.execute(
            text(
                "INSERT INTO pipeline_audit_log "
                "(id, phase, level, event, parsed_ticket_id, scan_id, payload, "
                " created_at) "
                "VALUES (:id, :phase, :level, :event, :pt, :scan, "
                "        CAST(:payload AS jsonb), :created_at)"
            ),
            {
                "id": str(audit_id),
                "phase": phase,
                "level": level,
                "event": event,
                "pt": str(parsed_ticket_id) if parsed_ticket_id else None,
                "scan": str(scan_id) if scan_id else None,
                "payload": json.dumps(payload or {}),
                "created_at": created_at,
            },
        )
    db.commit()
    return audit_id


# ============================================================================
# GET /admin/pipeline/audit-log
# ============================================================================
class TestAuditLog:
    def test_audit_log_filters_by_receipt_id(self, admin_client, db, store):
        pt = _make_parsed_ticket(db, receipt_id=None, items=[])
        receipt = _make_receipt(db, store, parsed_ticket_id=pt.id)
        # Sync FK on parsed_ticket — the receipt row arrives after the PT
        db.execute(
            text("UPDATE parsed_tickets SET receipt_id = :r WHERE id = :p"),
            {"r": str(receipt.id), "p": str(pt.id)},
        )
        db.commit()
        _insert_audit(db, parsed_ticket_id=pt.id, event="match_completed")
        # Unrelated audit (different parsed ticket) — must be filtered out.
        other_pt = _make_parsed_ticket(db, receipt_id=None, items=[])
        _insert_audit(db, parsed_ticket_id=other_pt.id, event="match_completed")

        r = admin_client.get(f"/api/v1/admin/pipeline/audit-log?receipt_id={receipt.id}")
        assert r.status_code == 200, r.text
        body = r.json()
        assert len(body) == 1
        assert body[0]["parsed_ticket_id"] == str(pt.id)

    def test_audit_log_filters_by_parsed_ticket_id(self, admin_client, db):
        pt1 = _make_parsed_ticket(db, receipt_id=None, items=[])
        pt2 = _make_parsed_ticket(db, receipt_id=None, items=[])
        _insert_audit(db, parsed_ticket_id=pt1.id, event="extract_completed")
        _insert_audit(db, parsed_ticket_id=pt2.id, event="extract_completed")

        r = admin_client.get(f"/api/v1/admin/pipeline/audit-log?parsed_ticket_id={pt1.id}")
        assert r.status_code == 200
        body = r.json()
        assert len(body) == 1
        assert body[0]["parsed_ticket_id"] == str(pt1.id)

    def test_audit_log_filters_by_phase(self, admin_client, db):
        pt = _make_parsed_ticket(db, receipt_id=None, items=[])
        _insert_audit(db, parsed_ticket_id=pt.id, phase="extract", event="x")
        _insert_audit(db, parsed_ticket_id=pt.id, phase="match", event="y")
        _insert_audit(db, parsed_ticket_id=pt.id, phase="manual", event="z")

        r = admin_client.get("/api/v1/admin/pipeline/audit-log?phase=manual")
        assert r.status_code == 200
        body = r.json()
        # All returned rows must be 'manual'
        assert all(row["phase"] == "manual" for row in body)
        assert any(row["event"] == "z" for row in body)

    def test_audit_log_filters_by_level(self, admin_client, db):
        pt = _make_parsed_ticket(db, receipt_id=None, items=[])
        _insert_audit(db, parsed_ticket_id=pt.id, level="verbose", event="v_evt")
        _insert_audit(db, parsed_ticket_id=pt.id, level="normal", event="n_evt")
        _insert_audit(db, parsed_ticket_id=pt.id, level="production", event="p_evt")

        r = admin_client.get("/api/v1/admin/pipeline/audit-log?level=verbose")
        assert r.status_code == 200
        body = r.json()
        assert all(row["level"] == "verbose" for row in body)

    def test_audit_log_filters_by_since(self, admin_client, db):
        pt = _make_parsed_ticket(db, receipt_id=None, items=[])
        old = datetime(2025, 1, 1, tzinfo=UTC)
        recent = datetime(2026, 5, 1, tzinfo=UTC)
        _insert_audit(db, parsed_ticket_id=pt.id, event="old_evt", created_at=old)
        _insert_audit(db, parsed_ticket_id=pt.id, event="recent_evt", created_at=recent)

        cutoff = datetime(2026, 1, 1, tzinfo=UTC)
        # URL-encode the timezone-aware ISO string : raw '+' in a query string
        # decodes to a space, which breaks datetime parsing (422).
        r = admin_client.get(f"/api/v1/admin/pipeline/audit-log?since={quote(cutoff.isoformat())}")
        assert r.status_code == 200
        events = [row["event"] for row in r.json()]
        assert "recent_evt" in events
        assert "old_evt" not in events

    def test_audit_log_orders_desc(self, admin_client, db):
        pt = _make_parsed_ticket(db, receipt_id=None, items=[])
        early = datetime(2026, 1, 1, tzinfo=UTC)
        later = early + timedelta(hours=1)
        _insert_audit(db, parsed_ticket_id=pt.id, event="first", created_at=early)
        _insert_audit(db, parsed_ticket_id=pt.id, event="second", created_at=later)

        r = admin_client.get(f"/api/v1/admin/pipeline/audit-log?parsed_ticket_id={pt.id}")
        assert r.status_code == 200
        events = [row["event"] for row in r.json()]
        # DESC : 'second' should come before 'first'
        assert events.index("second") < events.index("first")

    def test_audit_log_limit_caps_at_500(self, admin_client):
        r = admin_client.get("/api/v1/admin/pipeline/audit-log?limit=1000")
        # FastAPI Query(le=500) returns 422 (validation error) for > 500.
        assert r.status_code == 422

    def test_audit_log_invalid_phase_400(self, admin_client):
        r = admin_client.get("/api/v1/admin/pipeline/audit-log?phase=lol")
        assert r.status_code == 400
        assert r.json()["detail"] == "invalid_phase"

    def test_audit_log_unauth_without_admin_key_403(self, raw_client):
        r = raw_client.get("/api/v1/admin/pipeline/audit-log")
        assert r.status_code == 403


# ============================================================================
# GET /admin/parsed-tickets/{parsed_ticket_id}
# ============================================================================
class TestGetParsedTicketDetail:
    def test_get_parsed_ticket_returns_full_state(self, admin_client, db, store, user):
        items = [_make_parsed_item_dict(label="NUTELLA", total_cents=250)]
        pt = _make_parsed_ticket(db, receipt_id=None, items=items)
        receipt = _make_receipt(db, store, parsed_ticket_id=pt.id)
        db.execute(
            text("UPDATE parsed_tickets SET receipt_id = :r WHERE id = :p"),
            {"r": str(receipt.id), "p": str(pt.id)},
        )
        db.commit()
        s1 = _make_scan(db, user=user, store=store, receipt=receipt, parsed_ticket_id=pt.id)
        s2 = _make_scan(
            db,
            user=user,
            store=store,
            receipt=receipt,
            parsed_ticket_id=pt.id,
            scanned_name="POMME",
            price=120,
        )
        _insert_audit(db, parsed_ticket_id=pt.id, event="match_completed")
        _insert_audit(db, scan_id=s1.id, event="scan_persisted")
        _insert_audit(db, scan_id=s2.id, event="scan_persisted")

        r = admin_client.get(f"/api/v1/admin/parsed-tickets/{pt.id}")
        assert r.status_code == 200
        body = r.json()
        assert body["parsed_ticket"]["id"] == str(pt.id)
        assert len(body["scans"]) == 2
        events = [a["event"] for a in body["audit_log"]]
        assert events.count("scan_persisted") == 2
        assert "match_completed" in events

    def test_get_parsed_ticket_404_when_not_found(self, admin_client):
        r = admin_client.get(f"/api/v1/admin/parsed-tickets/{uuid.uuid4()}")
        assert r.status_code == 404
        assert r.json()["detail"] == "parsed_ticket_not_found"


# ============================================================================
# GET /admin/parsed-tickets — browse / derived status
# ============================================================================
class TestListParsedTickets:
    def _seed_ticket_with_scans(
        self,
        db,
        *,
        store,
        user,
        scan_statuses: list[str],
    ) -> ParsedTicketModel:
        """Persist a parsed_ticket + linked receipt + N scans of given statuses.

        For ``matched`` scans we link a product via ``product_ean`` ; the
        scans table has a FK to ``products.ean`` so the product row must
        exist first. We upsert a single fixture-grade product per call
        to keep the helper self-contained (the class-level test does not
        receive the ``product`` fixture).
        """
        from ratis_core.models.product import Product

        if any(st == "matched" for st in scan_statuses):
            ean = "3017620422003"
            existing = db.execute(text("SELECT 1 FROM products WHERE ean = :e"), {"e": ean}).first()
            if existing is None:
                db.add(Product(ean=ean, name="Nutella 400g", source="off"))
                db.flush()
                db.commit()

        items = [_make_parsed_item_dict() for _ in scan_statuses]
        pt = _make_parsed_ticket(db, receipt_id=None, items=items)
        receipt = _make_receipt(db, store, parsed_ticket_id=pt.id)
        db.execute(
            text("UPDATE parsed_tickets SET receipt_id = :r WHERE id = :p"),
            {"r": str(receipt.id), "p": str(pt.id)},
        )
        db.commit()
        # Force a distinct scanned_at per scan to avoid colliding on the
        # UNIQUE (user_id, store_id, product_ean, scanned_at) index when
        # several matched scans share the same product. ``datetime.now``
        # gives us microsecond resolution so successive seeds within one
        # test (multiple parsed tickets back-to-back) stay disjoint.
        base = datetime.now(UTC)
        for offset, st in enumerate(scan_statuses):
            _make_scan(
                db,
                user=user,
                store=store,
                receipt=receipt,
                parsed_ticket_id=pt.id,
                status=st,
                match_method="fuzzy_strict" if st == "matched" else None,
                product_ean="3017620422003" if st == "matched" else None,
                rejected_reason=None if st == "matched" else "no_fuzzy_candidate",
                scanned_at=base + timedelta(seconds=offset),
            )
        return pt

    def test_list_filters_by_status_matched(self, admin_client, db, store, user):
        matched_pt = self._seed_ticket_with_scans(db, store=store, user=user, scan_statuses=["matched", "matched"])
        unresolved_pt = self._seed_ticket_with_scans(db, store=store, user=user, scan_statuses=["unresolved"])

        r = admin_client.get("/api/v1/admin/parsed-tickets?status=matched")
        assert r.status_code == 200
        ids = [item["id"] for item in r.json()["items"]]
        assert str(matched_pt.id) in ids
        assert str(unresolved_pt.id) not in ids

    def test_list_filters_by_status_unresolved(self, admin_client, db, store, user):
        matched_pt = self._seed_ticket_with_scans(db, store=store, user=user, scan_statuses=["matched"])
        unresolved_pt = self._seed_ticket_with_scans(
            db, store=store, user=user, scan_statuses=["unresolved", "unresolved"]
        )

        r = admin_client.get("/api/v1/admin/parsed-tickets?status=unresolved")
        assert r.status_code == 200
        ids = [item["id"] for item in r.json()["items"]]
        assert str(unresolved_pt.id) in ids
        assert str(matched_pt.id) not in ids

    def test_list_filters_status_mixed(self, admin_client, db, store, user):
        mixed_pt = self._seed_ticket_with_scans(db, store=store, user=user, scan_statuses=["matched", "unresolved"])
        all_matched_pt = self._seed_ticket_with_scans(db, store=store, user=user, scan_statuses=["matched"])

        r = admin_client.get("/api/v1/admin/parsed-tickets?status=mixed")
        assert r.status_code == 200
        ids = [item["id"] for item in r.json()["items"]]
        assert str(mixed_pt.id) in ids
        assert str(all_matched_pt.id) not in ids

    def test_list_paginated(self, admin_client, db, store, user):
        for _ in range(3):
            self._seed_ticket_with_scans(db, store=store, user=user, scan_statuses=["matched"])

        r = admin_client.get("/api/v1/admin/parsed-tickets?limit=2&offset=0")
        assert r.status_code == 200
        body = r.json()
        assert len(body["items"]) == 2
        assert body["limit"] == 2
        assert body["offset"] == 0

        r2 = admin_client.get("/api/v1/admin/parsed-tickets?limit=2&offset=2")
        assert r2.status_code == 200
        # Combined first + second page should not duplicate ids.
        ids1 = {item["id"] for item in body["items"]}
        ids2 = {item["id"] for item in r2.json()["items"]}
        assert ids1.isdisjoint(ids2)

    def test_list_orders_desc(self, admin_client, db, store, user):
        first = self._seed_ticket_with_scans(db, store=store, user=user, scan_statuses=["matched"])
        second = self._seed_ticket_with_scans(db, store=store, user=user, scan_statuses=["matched"])
        # Force a deterministic created_at gap : on fast CI both inserts
        # can land on the same NOW() tick, after which ORDER BY pt_id DESC
        # makes the random UUIDs decide — flaky. We pin both timestamps.
        early = datetime(2026, 5, 1, 10, 0, tzinfo=UTC)
        later = datetime(2026, 5, 1, 11, 0, tzinfo=UTC)
        db.execute(
            text("UPDATE parsed_tickets SET created_at = :ts WHERE id = :i"),
            {"ts": early, "i": str(first.id)},
        )
        db.execute(
            text("UPDATE parsed_tickets SET created_at = :ts WHERE id = :i"),
            {"ts": later, "i": str(second.id)},
        )
        db.commit()
        r = admin_client.get("/api/v1/admin/parsed-tickets")
        assert r.status_code == 200
        ids = [item["id"] for item in r.json()["items"]]
        # ``second`` (later created_at) must come strictly before ``first``.
        assert ids.index(str(second.id)) < ids.index(str(first.id))


# ============================================================================
# POST /admin/parsed-tickets/{parsed_ticket_id}/replay
# ============================================================================
class TestReplay:
    def test_replay_dispatches_celery_task(self, admin_client, db, monkeypatch):
        pt = _make_parsed_ticket(db, receipt_id=None, items=[])
        captured: dict[str, Any] = {}

        class _FakeAsyncResult:
            id = "fake-replay-task-id"

        def fake_dispatch(*, parsed_ticket_id, admin_operator, log_level):
            captured["parsed_ticket_id"] = parsed_ticket_id
            captured["admin_operator"] = admin_operator
            captured["log_level"] = log_level
            return _FakeAsyncResult()

        from routes.admin import observability as obs_mod

        monkeypatch.setattr(obs_mod, "_dispatch_replay_task", fake_dispatch)

        r = admin_client.post(
            f"/api/v1/admin/parsed-tickets/{pt.id}/replay",
            headers={"X-Admin-Operator": "guillaume"},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["task_id"] == "fake-replay-task-id"
        assert body["log_level"] == "verbose"
        assert captured["parsed_ticket_id"] == pt.id
        assert captured["admin_operator"] == "guillaume"
        assert captured["log_level"] == "verbose"

    def test_replay_404_when_not_found(self, admin_client, monkeypatch):
        # Stub dispatch so a bug here would surface as a 200 instead of 404.
        from routes.admin import observability as obs_mod

        monkeypatch.setattr(
            obs_mod,
            "_dispatch_replay_task",
            lambda **kw: pytest.fail("dispatch must not be called on 404"),
        )

        r = admin_client.post(
            f"/api/v1/admin/parsed-tickets/{uuid.uuid4()}/replay",
            headers={"X-Admin-Operator": "guillaume"},
        )
        assert r.status_code == 404
        assert r.json()["detail"] == "parsed_ticket_not_found"

    def test_replay_requires_admin_operator_header(self, admin_client, db):
        pt = _make_parsed_ticket(db, receipt_id=None, items=[])
        r = admin_client.post(f"/api/v1/admin/parsed-tickets/{pt.id}/replay")
        assert r.status_code == 400
        assert r.json()["detail"] == "operator_required"

    def test_replay_invalid_log_level_400(self, admin_client, db, monkeypatch):
        pt = _make_parsed_ticket(db, receipt_id=None, items=[])
        from routes.admin import observability as obs_mod

        monkeypatch.setattr(
            obs_mod,
            "_dispatch_replay_task",
            lambda **kw: pytest.fail("dispatch must not be called on 400"),
        )

        r = admin_client.post(
            f"/api/v1/admin/parsed-tickets/{pt.id}/replay?log_level=lol",
            headers={"X-Admin-Operator": "guillaume"},
        )
        assert r.status_code == 400
        assert r.json()["detail"] == "invalid_log_level"

    def test_replay_unauth_without_admin_key_403(self, raw_client):
        r = raw_client.post(
            f"/api/v1/admin/parsed-tickets/{uuid.uuid4()}/replay",
            headers={"X-Admin-Operator": "guillaume"},
        )
        assert r.status_code == 403


# ============================================================================
# GET /admin/tasks/{task_id}/status
# ============================================================================
class TestTaskStatus:
    def _patch_async_result(self, monkeypatch, *, state: str, value: Any = None):
        """Stub _get_async_result to return a fake AsyncResult."""

        class _FakeAR:
            def __init__(self, st, val):
                self.state = st
                self._val = val

            @property
            def result(self):
                return self._val

        from routes.admin import observability as obs_mod

        monkeypatch.setattr(
            obs_mod,
            "_get_async_result",
            lambda task_id: _FakeAR(state, value),
        )

    def test_task_status_returns_pending_for_unknown_id(self, admin_client, monkeypatch):
        # Celery's default behaviour for an unknown id is state=PENDING.
        self._patch_async_result(monkeypatch, state="PENDING")

        r = admin_client.get(f"/api/v1/admin/tasks/{uuid.uuid4()}/status")
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "pending"
        assert body["result"] is None
        assert body["error"] is None

    def test_task_status_returns_started(self, admin_client, monkeypatch):
        self._patch_async_result(monkeypatch, state="STARTED")
        r = admin_client.get(f"/api/v1/admin/tasks/{uuid.uuid4()}/status")
        assert r.status_code == 200
        assert r.json()["status"] == "started"

    def test_task_status_returns_success_for_completed(self, admin_client, monkeypatch):
        payload = {"parsed_ticket_id": "abc", "scan_ids": ["s1", "s2"]}
        self._patch_async_result(monkeypatch, state="SUCCESS", value=payload)

        r = admin_client.get(f"/api/v1/admin/tasks/{uuid.uuid4()}/status")
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "success"
        assert body["result"] == payload
        assert body["error"] is None

    def test_task_status_returns_failure_with_error(self, admin_client, monkeypatch):
        self._patch_async_result(monkeypatch, state="FAILURE", value=ValueError("boom"))
        r = admin_client.get(f"/api/v1/admin/tasks/{uuid.uuid4()}/status")
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "failure"
        assert body["error"] is not None
        assert "boom" in body["error"]

    def test_task_status_unauth_without_admin_key_403(self, raw_client):
        r = raw_client.get(f"/api/v1/admin/tasks/{uuid.uuid4()}/status")
        assert r.status_code == 403


# ============================================================================
# Celery task replay_parsed_ticket (direct call, no HTTP)
# ============================================================================
class TestReplayTask:
    """Exercise the task body directly with the test DB engine.

    Mirrors the pattern used in ``test_admin_barcode.py::TestReparseTask``.
    """

    def _patch_session(self, monkeypatch, db):
        """Force the task's internal session to share the test connection."""
        from worker import receipt_task as worker_mod

        class _SessionCtx:
            def __enter__(self_inner):
                return db

            def __exit__(self_inner, *exc):
                return False

        def _factory():
            return _SessionCtx

        monkeypatch.setattr(worker_mod, "_get_session_factory", lambda: _factory())

    def _seed_replayable_pt(self, db, *, store, user, ean: str):
        """Persist a parsed_ticket + receipt + matching item dict suitable
        for replay via the barcode cascade (Phase 3 hits the products
        fixture by EAN, no fuzzy needed)."""
        item_dict = _make_parsed_item_dict(label="NUTELLA", total_cents=250, barcode=ean)
        # Build a faithful Pydantic ``ParsedTicket`` shape so the
        # ``model_validate`` call inside the task succeeds.
        receipt = _make_receipt(db, store)
        full_jsonb = _make_full_parsed_jsonb(receipt_id=receipt.id, items=[item_dict])
        pt = _make_parsed_ticket(db, receipt_id=receipt.id, parsed_jsonb_full=full_jsonb)
        db.execute(
            text("UPDATE receipts SET parsed_ticket_id = :p, user_id = :u WHERE id = :r"),
            {"p": str(pt.id), "u": str(user.id), "r": str(receipt.id)},
        )
        db.commit()
        return pt, receipt

    def _run_task(
        self,
        parsed_ticket_id: uuid.UUID,
        admin_operator: str = "tester",
        log_level: str = "verbose",
    ) -> dict:
        from worker.pipeline_replay_task import replay_parsed_ticket

        return replay_parsed_ticket.run(
            parsed_ticket_id=str(parsed_ticket_id),
            admin_operator=admin_operator,
            log_level=log_level,
        )

    def test_replay_task_re_runs_phase3_and_4(self, db, store, user, product, monkeypatch):
        pt, receipt = self._seed_replayable_pt(db, store=store, user=user, ean=product.ean)
        self._patch_session(monkeypatch, db)
        result = self._run_task(pt.id)

        assert result["parsed_ticket_id"] == str(pt.id)
        assert result["receipt_id"] == str(receipt.id)
        assert len(result["scan_ids"]) == 1

        # The scan must be persisted with status=matched/match_method=barcode.
        row = db.execute(
            text("SELECT status, match_method, product_ean FROM scans WHERE id = :sid"),
            {"sid": result["scan_ids"][0]},
        ).first()
        assert row.status == "matched"
        assert row.match_method == "barcode"
        assert row.product_ean == product.ean

    def test_replay_task_idempotent(self, db, store, user, product, monkeypatch):
        pt, _ = self._seed_replayable_pt(db, store=store, user=user, ean=product.ean)
        self._patch_session(monkeypatch, db)

        first = self._run_task(pt.id)
        # The parsed_jsonb_hash is UNIQUE so the second run must hit the
        # ON CONFLICT DO NOTHING path and return the SAME parsed_ticket id.
        second = self._run_task(pt.id)
        assert first["parsed_ticket_id"] == second["parsed_ticket_id"]
        assert first["receipt_id"] == second["receipt_id"]

    def test_replay_task_emits_audit_log_admin_replay(self, db, store, user, product, monkeypatch):
        pt, _ = self._seed_replayable_pt(db, store=store, user=user, ean=product.ean)
        self._patch_session(monkeypatch, db)
        self._run_task(pt.id, admin_operator="alice", log_level="verbose")

        rows = db.execute(
            text(
                "SELECT phase, level, event, payload FROM pipeline_audit_log "
                "WHERE event = 'admin_replay' "
                "  AND parsed_ticket_id = :pt "
                "ORDER BY created_at, id"
            ),
            {"pt": str(pt.id)},
        ).fetchall()
        assert len(rows) >= 1
        evt = rows[-1]
        assert evt.phase == "manual"
        assert evt.event == "admin_replay"
        payload = evt.payload
        assert payload["admin_operator"] == "alice"
        assert payload["log_level"] == "verbose"
        assert "scan_ids" in payload


# Pytest collection sanity
def test_module_collects():
    assert True
