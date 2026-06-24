"""Tests for the alpha debug instrumentation (PR #126).

Covers :
  - GET /api/v1/admin/scans/<id>/debug — admin endpoint
  - migration round-trip is exercised by `test_migration_scan_debug.py`
    invoking alembic.upgrade/downgrade ; here we only test the runtime."""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from unittest.mock import MagicMock

from ratis_core.models.scan import Receipt, Scan
from sqlalchemy import text

# ── helpers ───────────────────────────────────────────────────────────────────


def _make_receipt(db, store) -> Receipt:
    r = Receipt(
        id=uuid.uuid4(),
        store_id=store.id,
        purchased_at=date.today(),
        image_r2_key="fake-receipt-key.jpg",
    )
    db.add(r)
    db.flush()
    db.commit()
    return r


def _seed_debug_row(
    db,
    *,
    receipt_id: uuid.UUID,
    scan_id: uuid.UUID | None = None,
    rich_blocks: list | None = None,
    llm_output: dict | None = None,
    legacy_receipt_data: dict | None = None,
    legacy_parser_output: dict | None = None,
    ocr_passes_summary: dict | None = None,
    processed_image_r2_key: str | None = "debug/sample.processed.jpg",
    processed_images_r2_keys: dict | None = None,
) -> uuid.UUID:
    """Seed one scan_debug row (PR #132 + Phase 2e schema).

    The ``legacy_receipt_data`` kwarg is preserved as an alias for the
    renamed ``final_receipt_data`` column (Phase 2e — ARCH OCR↔LLM
    Bridge v2). New code MAY also pass ``legacy_parser_output`` for the
    parallel parse_receipt result.

    Anchored on receipt_id ; scan_id is optional. Returns the row's id.
    """
    import json

    new_id = uuid.uuid4()
    db.execute(
        text(
            """
            INSERT INTO scan_debug (
                id, receipt_id, scan_id, rich_blocks, llm_output,
                final_receipt_data, legacy_parser_output,
                ocr_passes_summary,
                processed_images_r2_keys, processed_image_r2_key,
                purge_after
            ) VALUES (
                :id,
                :receipt_id,
                :scan_id,
                CAST(:rich_blocks AS jsonb),
                CAST(:llm_output AS jsonb),
                CAST(:final_receipt_data AS jsonb),
                CAST(:legacy_parser_output AS jsonb),
                CAST(:ocr_passes_summary AS jsonb),
                CAST(:processed_images_r2_keys AS jsonb),
                :processed_image_r2_key,
                now() + interval '48 hours'
            )
            """
        ),
        {
            "id": str(new_id),
            "receipt_id": str(receipt_id),
            "scan_id": str(scan_id) if scan_id is not None else None,
            "rich_blocks": json.dumps(rich_blocks if rich_blocks is not None else []),
            "llm_output": json.dumps(llm_output) if llm_output is not None else None,
            "final_receipt_data": (json.dumps(legacy_receipt_data) if legacy_receipt_data is not None else None),
            "legacy_parser_output": (json.dumps(legacy_parser_output) if legacy_parser_output is not None else None),
            "ocr_passes_summary": (json.dumps(ocr_passes_summary) if ocr_passes_summary is not None else None),
            "processed_images_r2_keys": (
                json.dumps(processed_images_r2_keys) if processed_images_r2_keys is not None else None
            ),
            "processed_image_r2_key": processed_image_r2_key,
        },
    )
    db.commit()
    return new_id


# ── admin endpoint tests ─────────────────────────────────────────────────────


class TestAdminDebugEndpoint:
    def test_returns_404_when_scan_not_found(self, admin_client):
        unknown = uuid.uuid4()
        r = admin_client.get(f"/api/v1/admin/scans/{unknown}/debug")
        assert r.status_code == 404
        assert r.json()["detail"] == "scan_not_found"

    def test_returns_404_with_explicit_detail_when_no_debug_row(self, admin_client, db, store, user):
        """Scan exists (e.g. flag was off when processed) but no scan_debug row."""
        receipt = _make_receipt(db, store)
        scan = Scan(
            id=uuid.uuid4(),
            user_id=user.id,
            store_id=store.id,
            scanned_name="ITEM",
            price=100,
            quantity=Decimal("1"),
            scan_type="receipt",
            receipt_id=receipt.id,
            status="accepted",
        )
        db.add(scan)
        db.commit()

        r = admin_client.get(f"/api/v1/admin/scans/{scan.id}/debug")
        assert r.status_code == 404
        assert r.json()["detail"] == "no_debug_data_available"

    def test_returns_full_payload_happy_path(self, admin_client, db, store, user, monkeypatch):
        """Scan with scan_debug row → full JSON returned with presigned URLs."""
        # Stub the S3 client used by the admin route to a deterministic mock.
        fake_s3 = MagicMock()
        fake_s3.generate_presigned_url.return_value = "https://r2.example/presigned/abc"
        monkeypatch.setattr("routes.admin.debug._get_s3_client", lambda: fake_s3)

        receipt = _make_receipt(db, store)
        scan = Scan(
            id=uuid.uuid4(),
            user_id=user.id,
            store_id=store.id,
            scanned_name="NUTELLA",
            price=250,
            quantity=Decimal("1"),
            scan_type="receipt",
            receipt_id=receipt.id,
            status="accepted",
        )
        db.add(scan)
        db.commit()
        _seed_debug_row(
            db,
            receipt_id=receipt.id,
            scan_id=scan.id,
            rich_blocks=[{"text": "NUTELLA", "x": 100, "y": 50, "w": 80, "h": 20, "confidence": 0.97}],
            legacy_receipt_data={"items": [{"scanned_name": "NUTELLA", "price": "2.50"}]},
            ocr_passes_summary={"corrected": {"n_blocks": 4, "time_ms": 120}},
            processed_images_r2_keys={
                "corrected": f"debug/{receipt.id}.corrected.jpg",
                "clahe": f"debug/{receipt.id}.clahe.jpg",
                "binarized": f"debug/{receipt.id}.binarized.jpg",
            },
            processed_image_r2_key=f"debug/{receipt.id}.corrected.jpg",
        )

        r = admin_client.get(f"/api/v1/admin/scans/{scan.id}/debug")
        assert r.status_code == 200
        data = r.json()

        assert data["scan_id"] == str(scan.id)
        assert data["scan_status"] == "accepted"
        assert data["raw_image_url"] == "https://r2.example/presigned/abc"
        # PR #132 — per-pass URLs. The legacy single field is also kept.
        assert data["processed_image_url"] == "https://r2.example/presigned/abc"
        assert set(data["processed_images"].keys()) == {"corrected", "clahe", "binarized"}
        for pass_name in ("corrected", "clahe", "binarized"):
            assert data["processed_images"][pass_name] == "https://r2.example/presigned/abc"
        assert data["rich_blocks"] == [{"text": "NUTELLA", "x": 100, "y": 50, "w": 80, "h": 20, "confidence": 0.97}]
        assert data["legacy_receipt_data"]["items"][0]["scanned_name"] == "NUTELLA"
        assert data["ocr_passes_summary"] == {"corrected": {"n_blocks": 4, "time_ms": 120}}
        assert data["llm_output"] is None
        assert isinstance(data["scan_items"], list)
        assert len(data["scan_items"]) == 1
        assert data["scan_items"][0]["scanned_name"] == "NUTELLA"
        assert data["scan_items"][0]["price"] == 250

    def test_processed_image_status_when_not_stored(self, admin_client, db, store, user, monkeypatch):
        """If processed_image_r2_key is NULL, processed_image_url is null with status."""
        fake_s3 = MagicMock()
        fake_s3.generate_presigned_url.return_value = "https://r2.example/presigned/raw"
        monkeypatch.setattr("routes.admin.debug._get_s3_client", lambda: fake_s3)

        receipt = _make_receipt(db, store)
        scan = Scan(
            id=uuid.uuid4(),
            user_id=user.id,
            store_id=store.id,
            scanned_name="ITEM",
            price=100,
            quantity=Decimal("1"),
            scan_type="receipt",
            receipt_id=receipt.id,
            status="accepted",
        )
        db.add(scan)
        db.commit()
        _seed_debug_row(
            db,
            receipt_id=receipt.id,
            scan_id=scan.id,
            processed_image_r2_key=None,
            processed_images_r2_keys=None,
        )

        r = admin_client.get(f"/api/v1/admin/scans/{scan.id}/debug")
        assert r.status_code == 200
        data = r.json()
        assert data["processed_image_url"] is None
        assert data["processed_image_status"] == "not_stored"
        assert data["processed_images"] == {}

    def test_requires_admin_key(self, raw_client, db, store, user):
        """Without Authorization: Bearer <ADMIN_API_KEY> → 403."""
        receipt = _make_receipt(db, store)
        scan = Scan(
            id=uuid.uuid4(),
            user_id=user.id,
            store_id=store.id,
            scanned_name="ITEM",
            price=100,
            quantity=Decimal("1"),
            scan_type="receipt",
            receipt_id=receipt.id,
            status="accepted",
        )
        db.add(scan)
        db.commit()

        r = raw_client.get(f"/api/v1/admin/scans/{scan.id}/debug")
        assert r.status_code in (401, 403)

    def test_accepts_admin_key_via_bearer(self, raw_client, db, store, user, monkeypatch):
        """Authorization: Bearer test-admin-key-padded-to-32-chars-min → 200 (or 404 no_debug)."""
        fake_s3 = MagicMock()
        fake_s3.generate_presigned_url.return_value = "https://r2.example/presigned/x"
        monkeypatch.setattr("routes.admin.debug._get_s3_client", lambda: fake_s3)

        receipt = _make_receipt(db, store)
        scan = Scan(
            id=uuid.uuid4(),
            user_id=user.id,
            store_id=store.id,
            scanned_name="ITEM",
            price=100,
            quantity=Decimal("1"),
            scan_type="receipt",
            receipt_id=receipt.id,
            status="accepted",
        )
        db.add(scan)
        db.commit()
        _seed_debug_row(db, receipt_id=receipt.id, scan_id=scan.id)

        r = raw_client.get(
            f"/api/v1/admin/scans/{scan.id}/debug",
            headers={"Authorization": "Bearer test-admin-key-padded-to-32-chars-min"},
        )
        assert r.status_code == 200


class TestAdminDebugV2Fields:
    """ARCH OCR↔LLM Bridge Phase 2e — admin debug exposes BOTH the
    final receipt data (= what was used to create the scan) and the
    parallel legacy parser output for side-by-side comparison."""

    def test_returns_final_receipt_data_and_legacy_parser_output(self, admin_client, db, store, user, monkeypatch):
        from unittest.mock import MagicMock

        fake_s3 = MagicMock()
        fake_s3.generate_presigned_url.return_value = "https://r2.example/presigned/x"
        monkeypatch.setattr("routes.admin.debug._get_s3_client", lambda: fake_s3)

        receipt = _make_receipt(db, store)
        scan = Scan(
            id=uuid.uuid4(),
            user_id=user.id,
            store_id=store.id,
            scanned_name="X",
            price=199,
            quantity=Decimal("1"),
            scan_type="receipt",
            receipt_id=receipt.id,
            status="accepted",
        )
        db.add(scan)
        db.commit()
        _seed_debug_row(
            db,
            receipt_id=receipt.id,
            scan_id=scan.id,
            legacy_receipt_data={"items": [{"scanned_name": "FINAL"}]},
            legacy_parser_output={"items": [{"scanned_name": "LEGACY"}]},
        )

        r = admin_client.get(f"/api/v1/admin/scans/{scan.id}/debug")
        assert r.status_code == 200
        data = r.json()
        # New fields exposed.
        assert data["final_receipt_data"]["items"][0]["scanned_name"] == "FINAL"
        assert data["legacy_parser_output"]["items"][0]["scanned_name"] == "LEGACY"
        # Back-compat alias still serves the old key.
        assert data["legacy_receipt_data"]["items"][0]["scanned_name"] == "FINAL"


# ── PR #132 — receipt_id endpoint ────────────────────────────────────────────


class TestAdminReceiptDebugEndpoint:
    def test_returns_404_when_receipt_not_found(self, admin_client):
        unknown = uuid.uuid4()
        r = admin_client.get(f"/api/v1/admin/receipts/{unknown}/debug")
        assert r.status_code == 404
        assert r.json()["detail"] == "receipt_not_found"

    def test_returns_404_when_receipt_has_no_debug_row(self, admin_client, db, store):
        receipt = _make_receipt(db, store)
        r = admin_client.get(f"/api/v1/admin/receipts/{receipt.id}/debug")
        assert r.status_code == 404
        assert r.json()["detail"] == "no_debug_data_available"

    def test_returns_full_payload_with_per_pass_images(self, admin_client, db, store, monkeypatch):
        fake_s3 = MagicMock()
        fake_s3.generate_presigned_url.side_effect = lambda op, Params, ExpiresIn: (
            f"https://r2.example/presigned/{Params['Key']}"
        )
        monkeypatch.setattr("routes.admin.debug._get_s3_client", lambda: fake_s3)

        receipt = _make_receipt(db, store)
        # No scan attached — exercise the store-fail path payload shape.
        _seed_debug_row(
            db,
            receipt_id=receipt.id,
            scan_id=None,
            processed_images_r2_keys={
                "corrected": f"debug/{receipt.id}.corrected.jpg",
                "clahe": f"debug/{receipt.id}.clahe.jpg",
                "binarized": f"debug/{receipt.id}.binarized.jpg",
                "inverted": f"debug/{receipt.id}.inverted.jpg",
            },
            processed_image_r2_key=f"debug/{receipt.id}.corrected.jpg",
        )

        r = admin_client.get(f"/api/v1/admin/receipts/{receipt.id}/debug")
        assert r.status_code == 200
        data = r.json()

        assert data["receipt_id"] == str(receipt.id)
        assert data["scan_id"] is None
        assert data["scan_status"] is None  # no scan attached
        assert set(data["processed_images"].keys()) == {"corrected", "clahe", "binarized", "inverted"}
        for pass_name, expected_key in {
            "corrected": f"debug/{receipt.id}.corrected.jpg",
            "clahe": f"debug/{receipt.id}.clahe.jpg",
            "binarized": f"debug/{receipt.id}.binarized.jpg",
            "inverted": f"debug/{receipt.id}.inverted.jpg",
        }.items():
            assert data["processed_images"][pass_name] == f"https://r2.example/presigned/{expected_key}"

    def test_legacy_row_back_compat(self, admin_client, db, store, user, monkeypatch):
        """A row written before PR #132 (single processed_image_r2_key,
        no JSONB map) must still be readable. Endpoint surfaces the
        single image under the synthetic 'corrected' pass name."""
        fake_s3 = MagicMock()
        fake_s3.generate_presigned_url.return_value = "https://r2.example/legacy"
        monkeypatch.setattr("routes.admin.debug._get_s3_client", lambda: fake_s3)

        receipt = _make_receipt(db, store)
        scan = Scan(
            id=uuid.uuid4(),
            user_id=user.id,
            store_id=store.id,
            scanned_name="ITEM",
            price=100,
            quantity=Decimal("1"),
            scan_type="receipt",
            receipt_id=receipt.id,
            status="accepted",
        )
        db.add(scan)
        db.commit()
        # Legacy shape : processed_images_r2_keys=None, only legacy column set.
        _seed_debug_row(
            db,
            receipt_id=receipt.id,
            scan_id=scan.id,
            processed_image_r2_key=f"debug/{scan.id}.processed.jpg",
            processed_images_r2_keys=None,
        )

        r = admin_client.get(f"/api/v1/admin/receipts/{receipt.id}/debug")
        assert r.status_code == 200
        data = r.json()
        assert data["processed_images"] == {"corrected": "https://r2.example/legacy"}
        # Legacy single field still set for old clients.
        assert data["processed_image_url"] == "https://r2.example/legacy"

    def test_requires_admin_key(self, raw_client, db, store):
        receipt = _make_receipt(db, store)
        r = raw_client.get(f"/api/v1/admin/receipts/{receipt.id}/debug")
        assert r.status_code in (401, 403)


# ── R2 client virtual-hosted addressing (fix/r2-presigned-url-virtual-hosted) ─


class TestR2ClientAddressingStyle:
    """The R2-targeted boto3 client MUST be built with virtual-hosted
    addressing. Cloudflare R2 returns 401 Unauthorized on path-style
    presigned URLs (the boto3 default), even though it accepts path-style
    on direct S3 API calls. Using virtual-hosted everywhere keeps a
    single, consistent code path. See storage.py docstring.
    """

    def test_storage_client_uses_virtual_hosted_style(self, monkeypatch):
        """``storage.get_s3_client`` must pass ``Config(s3={'addressing_style': 'virtual'})``."""
        from unittest.mock import patch

        import storage

        with patch.object(storage.boto3, "client") as mock_client:
            storage.get_s3_client()

        assert mock_client.call_count == 1
        _args, kwargs = mock_client.call_args
        config = kwargs.get("config")
        assert config is not None, "boto3.client must be called with a Config instance"
        # botocore stores s3 options under config.s3 (a dict) ; addressing_style
        # is the canonical knob.
        s3_opts = getattr(config, "s3", None) or {}
        assert s3_opts.get("addressing_style") == "virtual", (
            f"R2 client must use virtual-hosted addressing, got {s3_opts!r}"
        )

    def test_storage_client_uses_sigv4(self, monkeypatch):
        """``storage.get_s3_client`` must pass ``Config(signature_version='s3v4')``.

        Cloudflare R2 only accepts SigV4 for presigned URLs. Without an explicit
        version, boto3 picks a region-dependent default that can produce SigV2
        URLs (`?AWSAccessKeyId=...&Signature=...`), which R2 rejects with 401
        even when addressing_style is correct.

        Lesson 2026-04-27 — `scan_debug_viewer.py` got 401 on virtual-hosted
        URLs until this flag was set explicitly.
        """
        from unittest.mock import patch

        import storage

        with patch.object(storage.boto3, "client") as mock_client:
            storage.get_s3_client()

        assert mock_client.call_count == 1
        _args, kwargs = mock_client.call_args
        config = kwargs.get("config")
        assert config is not None, "boto3.client must be called with a Config instance"
        # botocore stores signature_version as a top-level Config attribute.
        assert getattr(config, "signature_version", None) == "s3v4", (
            f"R2 client must use SigV4 for presigned URLs, got {getattr(config, 'signature_version', None)!r}"
        )

    def test_admin_route_indirection_calls_storage_helper(self, monkeypatch):
        """``routes.admin.debug._get_s3_client`` must defer to ``storage.get_s3_client``
        so the addressing-style fix is enforced at a single call site."""
        from unittest.mock import MagicMock

        import storage
        from routes.admin import debug as admin_mod

        sentinel = MagicMock(name="r2_client")
        monkeypatch.setattr(storage, "get_s3_client", lambda: sentinel)
        # Re-import path : routes.admin.debug imported get_s3_client at module
        # load, so we patch the imported name there too.
        monkeypatch.setattr(admin_mod, "get_s3_client", lambda: sentinel)

        client = admin_mod._get_s3_client()
        assert client is sentinel

    def test_worker_helpers_delegate_to_storage(self, monkeypatch):
        """Both ``worker.receipt_task._get_s3_client`` and
        ``worker.label_task._get_s3_client`` MUST go through
        ``storage.get_s3_client`` so the virtual-hosted addressing
        style applies to upload/download paths too. Otherwise a future
        feature that presigns from the worker would silently 401 in prod.
        """
        from unittest.mock import MagicMock

        import storage
        from worker import label_task, receipt_task

        sentinel = MagicMock(name="r2_client_shared")
        # 2026-04-27 — receipt_task / label_task now bind `get_s3_client` at
        # module-load time (`from storage import get_s3_client`) because the
        # previous lazy import broke at runtime in Celery workers (cf. KP-NN).
        # The test must therefore patch the LOCAL binding in each module on
        # top of patching `storage` itself — otherwise the workers keep
        # using the original reference resolved at import time.
        monkeypatch.setattr(storage, "get_s3_client", lambda: sentinel)
        monkeypatch.setattr(receipt_task, "get_s3_client", lambda: sentinel)
        monkeypatch.setattr(label_task, "get_s3_client", lambda: sentinel)

        assert receipt_task._get_s3_client() is sentinel
        assert label_task._get_s3_client() is sentinel
