"""Tests for the PA admin barcode endpoints (PR-C v3 barcode port).

Covers :

- ``GET  /api/v1/admin/barcode/unknown-retailers`` — list retailers with
  raw barcodes but no parse config.
- ``POST /api/v1/admin/barcode/reparse`` — async re-parse of receipts
  whose ``barcode_fields`` is NULL after a format was added.
- The Celery task ``reparse_barcode_for_retailer`` itself (direct calls,
  no HTTP).

Uses the service-level conftest at ``tests/conftest.py`` for DB +
TestClient + admin auth bypass fixtures.
"""

from __future__ import annotations

import json
import uuid
from datetime import date
from typing import Any

from ratis_core.models.scan import Receipt
from ratis_core.models.store import Store
from sqlalchemy import text

# ── helpers ──────────────────────────────────────────────────────────────────


def _make_store(
    db,
    *,
    retailer: str | None,
    name: str | None = None,
) -> Store:
    s = Store(
        id=uuid.uuid4(),
        name=name or (retailer or "unnamed"),
        retailer=retailer,
        address="1 rue Test",
        city="Paris",
        postal_code="75001",
        lat=48.8566,
        lng=2.3522,
    )
    db.add(s)
    db.flush()
    db.commit()
    return s


def _make_receipt(
    db,
    *,
    store: Store | None,
    receipt_barcode: str | None = "1234567890123456",
    barcode_fields: dict | None = None,
    purchased_at: date | None = None,
) -> Receipt:
    r = Receipt(
        id=uuid.uuid4(),
        store_id=store.id if store is not None else None,
        purchased_at=purchased_at or date.today(),
        image_r2_key="fake-key.jpg",
        receipt_barcode=receipt_barcode,
        barcode_fields=barcode_fields,
    )
    db.add(r)
    db.flush()
    db.commit()
    return r


def _seed_intermarche_format(db) -> None:
    """Insert a minimal retailer_receipt_formats row for 'intermarche'."""
    db.execute(
        text(
            "INSERT INTO retailer_receipt_formats "
            "(retailer_key, length, fields) "
            "VALUES (:k, :len, CAST(:fields AS jsonb)) "
            "ON CONFLICT (retailer_key) DO NOTHING"
        ),
        {
            "k": "intermarche",
            "len": 18,
            "fields": json.dumps(
                [
                    {"name": "store_code", "start": 0, "end": 4},
                    {"name": "tx_id", "start": 4, "end": 10},
                    {"name": "date", "start": 10, "end": 16},
                ]
            ),
        },
    )
    db.commit()


# ============================================================================
# GET /admin/barcode/unknown-retailers
# ============================================================================
class TestListUnknownRetailers:
    def test_lists_retailers_with_raw_but_no_format(self, admin_client, db):
        """Carrefour has receipts with raw barcodes but no format config."""
        carrefour = _make_store(db, retailer="Carrefour")
        for _ in range(5):
            _make_receipt(
                db,
                store=carrefour,
                receipt_barcode=f"BC{uuid.uuid4().hex[:14]}",
                barcode_fields=None,
            )

        r = admin_client.get("/api/v1/admin/barcode/unknown-retailers")
        assert r.status_code == 200
        body = r.json()
        rows = [row for row in body if row["retailer"] == "Carrefour"]
        assert len(rows) == 1
        assert rows[0]["ticket_count"] == 5

    def test_lists_unresolved_store_as_None(self, admin_client, db):
        """Receipts with store_id=NULL bucket as retailer=None."""
        for _ in range(3):
            _make_receipt(
                db,
                store=None,
                receipt_barcode=f"BC{uuid.uuid4().hex[:14]}",
                barcode_fields=None,
            )

        r = admin_client.get("/api/v1/admin/barcode/unknown-retailers")
        assert r.status_code == 200
        body = r.json()
        rows = [row for row in body if row["retailer"] is None]
        assert len(rows) == 1
        assert rows[0]["ticket_count"] == 3

    def test_excludes_retailers_with_format_config(self, admin_client, db):
        """Intermarché has a format row → excluded from the list."""
        _seed_intermarche_format(db)
        inter = _make_store(db, retailer="Intermarché")
        # Even with a raw barcode but no parsed fields, Intermarché should
        # not appear — its format exists so the worker can reparse on the
        # next pass without admin intervention.
        _make_receipt(
            db,
            store=inter,
            receipt_barcode="BC_INTER_RAW",
            barcode_fields=None,
        )

        r = admin_client.get("/api/v1/admin/barcode/unknown-retailers")
        assert r.status_code == 200
        body = r.json()
        retailers = [row["retailer"] for row in body]
        assert "Intermarché" not in retailers

    def test_excludes_receipts_with_barcode_fields_set(self, admin_client, db):
        """Receipts whose barcode_fields is already populated are excluded."""
        s = _make_store(db, retailer="UnknownBrand")
        _make_receipt(
            db,
            store=s,
            receipt_barcode="BC_PARSED_OK",
            barcode_fields={"store_code": "0001", "tx_id": "000123"},
        )

        r = admin_client.get("/api/v1/admin/barcode/unknown-retailers")
        assert r.status_code == 200
        body = r.json()
        retailers = [row["retailer"] for row in body]
        assert "UnknownBrand" not in retailers

    def test_excludes_receipts_with_no_raw_barcode(self, admin_client, db):
        """Receipts with no receipt_barcode at all should never surface."""
        s = _make_store(db, retailer="NoBarcodeBrand")
        _make_receipt(
            db,
            store=s,
            receipt_barcode=None,
            barcode_fields=None,
        )
        r = admin_client.get("/api/v1/admin/barcode/unknown-retailers")
        assert r.status_code == 200
        retailers = [row["retailer"] for row in r.json()]
        assert "NoBarcodeBrand" not in retailers

    def test_orders_by_count_desc(self, admin_client, db):
        few = _make_store(db, retailer="FewBrand")
        many = _make_store(db, retailer="ManyBrand")
        for _ in range(2):
            _make_receipt(db, store=few, receipt_barcode=f"BC{uuid.uuid4().hex[:14]}")
        for _ in range(7):
            _make_receipt(db, store=many, receipt_barcode=f"BC{uuid.uuid4().hex[:14]}")

        r = admin_client.get("/api/v1/admin/barcode/unknown-retailers")
        assert r.status_code == 200
        body = r.json()
        # Find both
        retailers = [row["retailer"] for row in body if row["retailer"] in ("FewBrand", "ManyBrand")]
        assert retailers == ["ManyBrand", "FewBrand"]

    def test_limit_param(self, admin_client, db):
        for n in range(4):
            s = _make_store(db, retailer=f"Brand{n}")
            _make_receipt(db, store=s, receipt_barcode=f"BC{uuid.uuid4().hex[:14]}")

        r = admin_client.get("/api/v1/admin/barcode/unknown-retailers?limit=2")
        assert r.status_code == 200
        body = r.json()
        assert len(body) <= 2

    def test_unauth_without_admin_key_403(self, raw_client):
        r = raw_client.get("/api/v1/admin/barcode/unknown-retailers")
        assert r.status_code == 403
        assert r.json()["detail"] == "forbidden"


# ============================================================================
# POST /admin/barcode/reparse
# ============================================================================
class TestPostReparse:
    def test_reparse_requires_admin_operator_header(self, admin_client, db):
        _seed_intermarche_format(db)
        r = admin_client.post(
            "/api/v1/admin/barcode/reparse",
            json={"retailer_key": "intermarche"},
        )
        assert r.status_code == 400
        assert r.json()["detail"] == "operator_required"

    def test_reparse_404_when_format_not_configured(self, admin_client, db):
        r = admin_client.post(
            "/api/v1/admin/barcode/reparse",
            json={"retailer_key": "carrefour"},
            headers={"X-Admin-Operator": "guillaume"},
        )
        assert r.status_code == 404
        assert r.json()["detail"] == "format_not_configured"

    def test_reparse_returns_task_id_and_estimated_count(self, admin_client, db, monkeypatch):
        _seed_intermarche_format(db)
        inter = _make_store(db, retailer="Intermarché")
        for _ in range(3):
            _make_receipt(
                db,
                store=inter,
                receipt_barcode=f"BC{uuid.uuid4().hex[:14]}",
                barcode_fields=None,
            )

        # Stub the Celery .delay() — we test the endpoint contract here, the
        # task body itself is exercised by TestReparseTask below.
        captured: dict[str, Any] = {}

        class _FakeAsyncResult:
            id = "fake-task-id-xyz"

        def fake_delay(*, retailer_key: str, admin_operator: str):
            captured["retailer_key"] = retailer_key
            captured["admin_operator"] = admin_operator
            return _FakeAsyncResult()

        from routes.admin import barcode as barcode_mod

        monkeypatch.setattr(barcode_mod, "_dispatch_reparse_task", fake_delay)

        r = admin_client.post(
            "/api/v1/admin/barcode/reparse",
            json={"retailer_key": "intermarche"},
            headers={"X-Admin-Operator": "guillaume"},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["task_id"] == "fake-task-id-xyz"
        assert body["retailer_key"] == "intermarche"
        assert body["estimated_count"] == 3
        assert captured == {
            "retailer_key": "intermarche",
            "admin_operator": "guillaume",
        }

    def test_reparse_estimated_count_only_unparsed(self, admin_client, db, monkeypatch):
        """Already-parsed receipts must not inflate estimated_count."""
        _seed_intermarche_format(db)
        inter = _make_store(db, retailer="Intermarché")
        # 2 unparsed
        for _ in range(2):
            _make_receipt(
                db,
                store=inter,
                receipt_barcode=f"BC{uuid.uuid4().hex[:14]}",
                barcode_fields=None,
            )
        # 1 already parsed → must be excluded
        _make_receipt(
            db,
            store=inter,
            receipt_barcode=f"BC{uuid.uuid4().hex[:14]}",
            barcode_fields={"store_code": "0001"},
        )

        from routes.admin import barcode as barcode_mod

        class _FakeAsyncResult:
            id = "task-x"

        monkeypatch.setattr(
            barcode_mod,
            "_dispatch_reparse_task",
            lambda **kw: _FakeAsyncResult(),
        )

        r = admin_client.post(
            "/api/v1/admin/barcode/reparse",
            json={"retailer_key": "intermarche"},
            headers={"X-Admin-Operator": "guillaume"},
        )
        assert r.status_code == 200
        assert r.json()["estimated_count"] == 2

    def test_reparse_unauth_without_admin_key_403(self, raw_client):
        r = raw_client.post(
            "/api/v1/admin/barcode/reparse",
            json={"retailer_key": "intermarche"},
            headers={"X-Admin-Operator": "guillaume"},
        )
        assert r.status_code == 403
        assert r.json()["detail"] == "forbidden"


# ============================================================================
# Celery task reparse_barcode_for_retailer (direct call, no HTTP)
# ============================================================================
class TestReparseTask:
    """Exercise the task body directly with the test DB engine.

    The task creates its own Session via ``_get_session_factory`` ; tests
    monkeypatch the factory to return the test DB session so the inserts
    are visible to ``db`` and rolled back at teardown.
    """

    def _patch_session(self, monkeypatch, db):
        """Force the task's internal session to share the test connection."""
        from worker import receipt_task as worker_mod

        class _SessionCtx:
            def __enter__(self_inner):
                return db

            def __exit__(self_inner, *exc):
                return False

        # The task uses _get_session_factory()() — patch the factory's
        # callable result to yield a context-manager that returns ``db``.
        def _factory():
            return _SessionCtx

        monkeypatch.setattr(worker_mod, "_get_session_factory", lambda: _factory())

    def _run_task(self, retailer_key: str, admin_operator: str = "tester") -> dict:
        from worker.barcode_reparse_task import reparse_barcode_for_retailer

        # Call the Celery task synchronously (the .run() bypasses the
        # broker ; equivalent to calling the function directly).
        return reparse_barcode_for_retailer.run(
            retailer_key=retailer_key,
            admin_operator=admin_operator,
        )

    def test_task_updates_barcode_fields_when_parse_succeeds(self, db, monkeypatch):
        _seed_intermarche_format(db)
        inter = _make_store(db, retailer="Intermarché")
        # 18-char barcode matches the seeded format length.
        # store_code=0042, tx_id=000123, date=240501 (2024-05-01).
        receipt = _make_receipt(
            db,
            store=inter,
            receipt_barcode="0042000123240501XX",
            barcode_fields=None,
        )
        self._patch_session(monkeypatch, db)
        stats = self._run_task("intermarche")

        assert stats["processed"] == 1
        assert stats["parsed_ok"] == 1
        assert stats["parse_failed"] == 0

        row = db.execute(
            text("SELECT barcode_fields FROM receipts WHERE id = :rid"),
            {"rid": str(receipt.id)},
        ).first()
        assert row.barcode_fields is not None
        # store_code must round-trip through parse + persist.
        assert row.barcode_fields.get("store_code") == "0042"

    def test_task_skips_when_parse_returns_only_raw(self, db, monkeypatch):
        """A receipt whose raw barcode does not match the format length
        yields a ParsedReceiptBarcode with no useful fields → parse_failed
        bucket, barcode_fields stays NULL."""
        _seed_intermarche_format(db)
        inter = _make_store(db, retailer="Intermarché")
        receipt = _make_receipt(
            db,
            store=inter,
            receipt_barcode="TOO_SHORT",  # length mismatch
            barcode_fields=None,
        )
        self._patch_session(monkeypatch, db)
        stats = self._run_task("intermarche")

        assert stats["processed"] == 1
        assert stats["parsed_ok"] == 0
        assert stats["parse_failed"] == 1

        row = db.execute(
            text("SELECT barcode_fields FROM receipts WHERE id = :rid"),
            {"rid": str(receipt.id)},
        ).first()
        assert row.barcode_fields is None

    def test_task_writes_audit_log_per_processed(self, db, monkeypatch):
        _seed_intermarche_format(db)
        inter = _make_store(db, retailer="Intermarché")
        _make_receipt(
            db,
            store=inter,
            receipt_barcode="0042000123240501XX",
            barcode_fields=None,
        )
        self._patch_session(monkeypatch, db)
        self._run_task("intermarche", admin_operator="alice")

        rows = db.execute(
            text(
                "SELECT phase, level, event, payload FROM pipeline_audit_log "
                "WHERE event = 'barcode_reparsed' ORDER BY created_at, id"
            )
        ).fetchall()
        assert len(rows) == 1
        assert rows[0].phase == "manual"
        assert rows[0].event == "barcode_reparsed"
        payload = rows[0].payload
        assert payload["admin_operator"] == "alice"
        assert payload["retailer_key"] == "intermarche"
        assert "receipt_id" in payload
        assert "parsed_fields" in payload

    def test_task_idempotent_skips_already_parsed(self, db, monkeypatch):
        _seed_intermarche_format(db)
        inter = _make_store(db, retailer="Intermarché")
        _make_receipt(
            db,
            store=inter,
            receipt_barcode="0042000123240501XX",
            barcode_fields=None,
        )
        self._patch_session(monkeypatch, db)

        first = self._run_task("intermarche")
        assert first["parsed_ok"] == 1

        # Second run — barcode_fields is now set, no rows match the WHERE.
        second = self._run_task("intermarche")
        assert second["processed"] == 0
        assert second["parsed_ok"] == 0
        assert second["parse_failed"] == 0

    def test_task_returns_stats_dict(self, db, monkeypatch):
        _seed_intermarche_format(db)
        inter = _make_store(db, retailer="Intermarché")
        # 1 will parse OK, 1 won't (length mismatch).
        _make_receipt(
            db,
            store=inter,
            receipt_barcode="0042000123240501XX",
            barcode_fields=None,
        )
        _make_receipt(
            db,
            store=inter,
            receipt_barcode="SHORT",
            barcode_fields=None,
        )
        self._patch_session(monkeypatch, db)
        stats = self._run_task("intermarche")

        assert set(stats.keys()) == {"processed", "parsed_ok", "parse_failed"}
        assert stats["processed"] == 2
        assert stats["parsed_ok"] == 1
        assert stats["parse_failed"] == 1


# Pytest collection sanity
def test_module_collects():
    assert True
