"""Tests for the PA admin knowledge-curation endpoints (ARCH_admin_endpoints PR9).

Covers :

- ``GET   /api/v1/admin/knowledge/ocr-queue`` — paginated read of
  unresolved ``ocr_knowledge`` rows (``corrected IS NULL``).
- ``PATCH /api/v1/admin/knowledge/{ocr_knowledge_id}`` — apply manual
  correction (``corrected="<canonical>"``) or dismissal
  (``corrected=null``).

product_knowledge endpoints (``/admin/knowledge/product-queue`` +
``PATCH /admin/product-knowledge/{id}``) are not in scope here — the
underlying table doesn't exist yet (post-bloc-7 per orchestrator's
documented decision). Tests for those will land alongside the table
migration.

Uses the service-level conftest (``tests/conftest.py``) for DB +
TestClient + admin auth fixtures.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import text

# ── helpers ──────────────────────────────────────────────────────────────────


def _insert_ocr_knowledge(
    db,
    *,
    raw_ocr: str,
    corrected: str | None = None,
    seen_count: int = 1,
    type_: str = "product_name",
    match_type: str = "sequence",
    source: str = "ocr_arbitrage",
    created_at: datetime | None = None,
) -> uuid.UUID:
    """Insert one ``ocr_knowledge`` row — returns the new id.

    Defaults reflect the auto-enrolled state (``corrected=NULL``,
    ``source='ocr_arbitrage'``) so individual tests only need to
    override the field they exercise.
    """
    row_id = uuid.uuid4()
    if created_at is None:
        db.execute(
            text(
                "INSERT INTO ocr_knowledge "
                "(id, raw_ocr, corrected, match_type, source, seen_count, type) "
                "VALUES (:id, :raw, :corr, :mt, :src, :cnt, :type)"
            ),
            {
                "id": str(row_id),
                "raw": raw_ocr,
                "corr": corrected,
                "mt": match_type,
                "src": source,
                "cnt": seen_count,
                "type": type_,
            },
        )
    else:
        db.execute(
            text(
                "INSERT INTO ocr_knowledge "
                "(id, raw_ocr, corrected, match_type, source, seen_count, "
                " type, created_at) "
                "VALUES (:id, :raw, :corr, :mt, :src, :cnt, :type, :ts)"
            ),
            {
                "id": str(row_id),
                "raw": raw_ocr,
                "corr": corrected,
                "mt": match_type,
                "src": source,
                "cnt": seen_count,
                "type": type_,
                "ts": created_at,
            },
        )
    db.commit()
    return row_id


def _fetch_row(db, row_id: uuid.UUID) -> Any:
    """SELECT one ``ocr_knowledge`` row by id, returning the SQLAlchemy Row."""
    return db.execute(
        text("SELECT id, raw_ocr, corrected, source, seen_count, type FROM ocr_knowledge WHERE id = :id"),
        {"id": str(row_id)},
    ).first()


def _fetch_audit_events(db, *, event: str | None = None) -> list[Any]:
    """SELECT recent ``pipeline_audit_log`` rows (optionally filtered on event)."""
    if event is None:
        rows = db.execute(
            text("SELECT phase, level, event, payload FROM pipeline_audit_log ORDER BY created_at DESC, id DESC")
        ).fetchall()
    else:
        rows = db.execute(
            text(
                "SELECT phase, level, event, payload "
                "FROM pipeline_audit_log "
                "WHERE event = :event "
                "ORDER BY created_at DESC, id DESC"
            ),
            {"event": event},
        ).fetchall()
    return list(rows)


_ADMIN_OP_HEADERS = {"X-Admin-Operator": "test-operator"}


# ============================================================================
# GET /admin/knowledge/ocr-queue
# ============================================================================
class TestOcrQueueList:
    def test_list_returns_only_uncorrected(self, admin_client, db):
        """Rows with ``corrected IS NOT NULL`` MUST be filtered out — only the
        manual queue (unresolved fragments) surfaces in the response."""
        unresolved = _insert_ocr_knowledge(db, raw_ocr="NUTELL4")
        _insert_ocr_knowledge(db, raw_ocr="POPP1ER", corrected="POPPIER")

        r = admin_client.get("/api/v1/admin/knowledge/ocr-queue")
        assert r.status_code == 200, r.text
        body = r.json()
        ids = [item["id"] for item in body]
        assert str(unresolved) in ids
        assert all(item["raw_ocr"] != "POPP1ER" for item in body)

    def test_list_orders_by_seen_count_desc(self, admin_client, db):
        """Highest ``seen_count`` first — drives the operator's priority."""
        rare = _insert_ocr_knowledge(db, raw_ocr="RARE_RAW", seen_count=2)
        common = _insert_ocr_knowledge(db, raw_ocr="COMMON_RAW", seen_count=42)
        mid = _insert_ocr_knowledge(db, raw_ocr="MID_RAW", seen_count=10)

        r = admin_client.get("/api/v1/admin/knowledge/ocr-queue")
        assert r.status_code == 200
        ids = [item["id"] for item in r.json()]
        # All three present, ordered common → mid → rare.
        assert ids.index(str(common)) < ids.index(str(mid)) < ids.index(str(rare))

    def test_list_paginated(self, admin_client, db):
        """``limit`` + ``offset`` partition the result set deterministically."""
        # Insert with explicit created_at so the secondary order key is stable.
        base = datetime(2026, 5, 1, 12, 0, tzinfo=UTC)
        ids = [
            _insert_ocr_knowledge(
                db,
                raw_ocr=f"RAW_{i:02d}",
                seen_count=100 - i,  # strictly decreasing → stable primary order
                created_at=base + timedelta(seconds=i),
            )
            for i in range(5)
        ]

        r1 = admin_client.get("/api/v1/admin/knowledge/ocr-queue?limit=2&offset=0")
        r2 = admin_client.get("/api/v1/admin/knowledge/ocr-queue?limit=2&offset=2")
        assert r1.status_code == 200
        assert r2.status_code == 200
        page1 = [item["id"] for item in r1.json()]
        page2 = [item["id"] for item in r2.json()]
        # Exactly 2 items per page, no overlap, in declared order.
        assert len(page1) == 2
        assert len(page2) == 2
        assert set(page1).isdisjoint(set(page2))
        assert page1[0] == str(ids[0])  # highest seen_count first
        assert page2[0] == str(ids[2])

    def test_list_skips_other_types(self, admin_client, db):
        """Curation queue is product_name-scoped — brand_name / retailer_header
        rows belong to other workflows and MUST NOT appear here."""
        product = _insert_ocr_knowledge(db, raw_ocr="PRODUCT_RAW", type_="product_name")
        _insert_ocr_knowledge(db, raw_ocr="BRAND_RAW", type_="brand_name")
        _insert_ocr_knowledge(db, raw_ocr="HEADER_RAW", type_="retailer_header")

        r = admin_client.get("/api/v1/admin/knowledge/ocr-queue")
        assert r.status_code == 200
        ids = [item["id"] for item in r.json()]
        assert str(product) in ids
        # Other categories filtered out.
        raws = [item["raw_ocr"] for item in r.json()]
        assert "BRAND_RAW" not in raws
        assert "HEADER_RAW" not in raws

    def test_list_limit_capped(self, admin_client):
        """``limit > 500`` rejected by FastAPI Query(le=500) — clean 422."""
        r = admin_client.get("/api/v1/admin/knowledge/ocr-queue?limit=1000")
        assert r.status_code == 422


# ============================================================================
# PATCH /admin/knowledge/{ocr_knowledge_id}
# ============================================================================
class TestApplyCorrection:
    def test_apply_correction_updates_row(self, admin_client, db):
        """``corrected="<canonical>"`` writes through ; ``source`` flips to
        ``'manual'`` so the operator's edit overrides the auto-enroll."""
        rid = _insert_ocr_knowledge(db, raw_ocr="NUTELL4", source="ocr_arbitrage")

        r = admin_client.patch(
            f"/api/v1/admin/knowledge/{rid}",
            json={"corrected": "NUTELLA"},
            headers=_ADMIN_OP_HEADERS,
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["corrected"] == "NUTELLA"
        assert body["source"] == "manual"
        assert body["previous_corrected"] is None

        # Verify the DB state matches the response.
        db.expire_all()
        row = _fetch_row(db, rid)
        assert row.corrected == "NUTELLA"
        assert row.source == "manual"

    def test_apply_dismissal_with_null(self, admin_client, db):
        """``corrected=null`` is a valid dismissal — the row stays
        ``corrected IS NULL`` but ``source = 'manual'`` records the
        operator's acknowledgment."""
        rid = _insert_ocr_knowledge(db, raw_ocr="X1Z2_NOISE", source="ocr_arbitrage")

        r = admin_client.patch(
            f"/api/v1/admin/knowledge/{rid}",
            json={"corrected": None},
            headers=_ADMIN_OP_HEADERS,
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["corrected"] is None
        assert body["source"] == "manual"

        db.expire_all()
        row = _fetch_row(db, rid)
        assert row.corrected is None
        assert row.source == "manual"

    def test_apply_correction_emits_audit_event(self, admin_client, db):
        """Mutation logs a ``pipeline_audit_log`` row at ``phase='manual'``
        with the operator handle + the diff."""
        rid = _insert_ocr_knowledge(db, raw_ocr="POPP1ER")

        r = admin_client.patch(
            f"/api/v1/admin/knowledge/{rid}",
            json={"corrected": "POPPIER"},
            headers=_ADMIN_OP_HEADERS,
        )
        assert r.status_code == 200

        events = _fetch_audit_events(db, event="admin_ocr_knowledge_correction")
        assert len(events) == 1
        assert events[0].phase == "manual"
        payload = events[0].payload
        assert payload["operator"] == "test-operator"
        assert payload["ocr_knowledge_id"] == str(rid)
        assert payload["diff"]["corrected"] == {"from": None, "to": "POPPIER"}

    def test_apply_dismissal_emits_distinct_audit_event(self, admin_client, db):
        """A dismissal logs ``admin_ocr_knowledge_dismissal`` (distinct event
        from corrections) so a downstream filter doesn't have to inspect the
        diff payload."""
        rid = _insert_ocr_knowledge(db, raw_ocr="JUNK_RAW")

        r = admin_client.patch(
            f"/api/v1/admin/knowledge/{rid}",
            json={"corrected": None},
            headers=_ADMIN_OP_HEADERS,
        )
        assert r.status_code == 200

        events = _fetch_audit_events(db, event="admin_ocr_knowledge_dismissal")
        assert len(events) == 1
        # No 'correction' event leaked.
        assert _fetch_audit_events(db, event="admin_ocr_knowledge_correction") == []

    def test_404_when_not_found(self, admin_client, db):
        """Targeting a non-existent id returns 404 with the snake-case detail."""
        missing = uuid.uuid4()
        r = admin_client.patch(
            f"/api/v1/admin/knowledge/{missing}",
            json={"corrected": "WHATEVER"},
            headers=_ADMIN_OP_HEADERS,
        )
        assert r.status_code == 404
        assert r.json()["detail"] == "ocr_knowledge_not_found"

    def test_requires_admin_operator(self, admin_client, db):
        """Missing ``X-Admin-Operator`` header → 400 ``operator_required``,
        regardless of the body shape."""
        rid = _insert_ocr_knowledge(db, raw_ocr="ORPHAN_RAW")

        r = admin_client.patch(
            f"/api/v1/admin/knowledge/{rid}",
            json={"corrected": "ORPHAN"},
            # No X-Admin-Operator header.
        )
        assert r.status_code == 400
        assert r.json()["detail"] == "operator_required"

    def test_blank_operator_rejected(self, admin_client, db):
        """Whitespace-only operator handle is no better than missing — 400."""
        rid = _insert_ocr_knowledge(db, raw_ocr="BLANKOP_RAW")
        r = admin_client.patch(
            f"/api/v1/admin/knowledge/{rid}",
            json={"corrected": "BLANKOP"},
            headers={"X-Admin-Operator": "   "},
        )
        assert r.status_code == 400
        assert r.json()["detail"] == "operator_required"

    def test_missing_corrected_field_422(self, admin_client, db):
        """Empty body / missing ``corrected`` key → 422 (Pydantic) so we
        never silently treat an absent field as a dismissal."""
        rid = _insert_ocr_knowledge(db, raw_ocr="NEEDS_FIELD")
        r = admin_client.patch(
            f"/api/v1/admin/knowledge/{rid}",
            json={},  # absent
            headers=_ADMIN_OP_HEADERS,
        )
        assert r.status_code == 422

    def test_extra_fields_forbidden(self, admin_client, db):
        """Unexpected fields in the body → 422 — defense against typos that
        would otherwise be silently dropped."""
        rid = _insert_ocr_knowledge(db, raw_ocr="EXTRAFLD_RAW")
        r = admin_client.patch(
            f"/api/v1/admin/knowledge/{rid}",
            json={"corrected": "OK", "stray": "should-fail"},
            headers=_ADMIN_OP_HEADERS,
        )
        assert r.status_code == 422

    def test_whitespace_only_corrected_treated_as_dismissal(self, admin_client, db):
        """Defense in depth : a UI bug submitting ``"   "`` MUST NOT land a
        whitespace canonical in the DB. Service normalizes to None."""
        rid = _insert_ocr_knowledge(db, raw_ocr="WSPACE_RAW")
        r = admin_client.patch(
            f"/api/v1/admin/knowledge/{rid}",
            json={"corrected": "   "},
            headers=_ADMIN_OP_HEADERS,
        )
        assert r.status_code == 200
        assert r.json()["corrected"] is None
        db.expire_all()
        row = _fetch_row(db, rid)
        assert row.corrected is None


# ============================================================================
# Auth — admin key on every endpoint
# ============================================================================
class TestAuth:
    def test_get_requires_admin_key(self, raw_client):
        """GET without ADMIN_API_KEY bearer → 403 ``forbidden``."""
        r = raw_client.get("/api/v1/admin/knowledge/ocr-queue")
        assert r.status_code == 403

    def test_patch_requires_admin_key(self, raw_client, db):
        """PATCH without ADMIN_API_KEY bearer → 403 ``forbidden`` BEFORE the
        operator-header check fires (admin auth is the outermost gate)."""
        rid = _insert_ocr_knowledge(db, raw_ocr="UNAUTH_RAW")
        r = raw_client.patch(
            f"/api/v1/admin/knowledge/{rid}",
            json={"corrected": "X"},
            headers=_ADMIN_OP_HEADERS,
        )
        assert r.status_code == 403


# ============================================================================
# Service-level direct tests — bypass HTTP for the dataclass / DTO contract
# ============================================================================
class TestServiceDirect:
    """Direct exercises on the service helpers ; complementary to the HTTP
    tests but cheaper to debug when the route layer is fine and only the
    DB query needs scrutiny."""

    def test_list_returns_typed_dataclass(self, db):
        from services.knowledge_admin_service import (
            OcrKnowledgeQueueItem,
            list_ocr_queue,
        )

        rid = _insert_ocr_knowledge(db, raw_ocr="DIRECT_RAW", seen_count=7)
        items = list_ocr_queue(db, limit=10, offset=0)
        match = [i for i in items if i.id == rid]
        assert len(match) == 1
        assert isinstance(match[0], OcrKnowledgeQueueItem)
        assert match[0].seen_count == 7

    def test_apply_raises_typed_exception_on_missing(self, db):
        from services.knowledge_admin_service import (
            OcrKnowledgeNotFound,
            apply_ocr_correction,
        )

        with pytest.raises(OcrKnowledgeNotFound):
            apply_ocr_correction(
                db,
                ocr_knowledge_id=uuid.uuid4(),
                corrected="X",
                operator="op",
            )
