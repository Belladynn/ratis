from __future__ import annotations

import base64
import hashlib
import io
import uuid
from datetime import UTC, date

from fastapi.testclient import TestClient
from PIL import Image
from ratis_core.models.scan import Receipt, Scan
from ratis_core.models.user import User

from tests.conftest import make_token

# Real magic bytes for each accepted type (verified against libmagic).
#
# libmagic asymmetry to be aware of (see Bug 8 / KP-87) :
#   - JPEG / PDF / EXE : the leading magic bytes alone are enough — libmagic
#     identifies the type from `\xff\xd8\xff` / `%PDF` / `MZ` without needing
#     the rest of the file structure to be valid.
#   - PNG : libmagic requires a valid IHDR chunk past the 8-byte signature, so
#     we ship a real minimal 1x1 greyscale PNG.
#   - WebP : RIFF is a generic container (shared with WAV / AVI / ANI / …).
#     Libmagic only emits `image/webp` if there is an actual VP8 / VP8L /
#     VP8X chunk following the `WEBP` FourCC — a bare `RIFF…WEBP` + padding
#     gets reported as `application/octet-stream` and our upload validator
#     rejects it. We therefore generate a real 1x1 WebP via Pillow at module
#     load (cheap, deterministic, no fixture file on disk).
_JPEG_BYTES = b"\xff\xd8\xff\xe0" + b"\x00" * 100
_PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAAAAAA6fptVAAAACklEQVQI12NgAAAAAgAB4iG8MwAAAABJRU5ErkJggg=="
)


def _build_webp_bytes() -> bytes:
    """Generate a minimal 1x1 WebP as actual bytes via Pillow.

    Done at import time and cached in `_WEBP_BYTES` — Pillow's WebP encoder is
    a few hundred microseconds for a 1x1 image, so the cost is negligible vs.
    a hardcoded byte string. The advantage : whatever the runtime libwebp
    version, the bytes it produces are by definition what libmagic recognises
    on the same machine — no risk of « valid on macOS / rejected on Linux »
    drift.
    """
    buf = io.BytesIO()
    Image.new("RGB", (1, 1), color=(0, 0, 0)).save(buf, format="WEBP")
    return buf.getvalue()


_WEBP_BYTES = _build_webp_bytes()
_PDF_BYTES = b"%PDF-1.4\n" + b"\x00" * 100
_EXE_BYTES = b"MZ" + b"\x00" * 100  # Windows PE — should be rejected


def _auth(user) -> dict:
    return {"Authorization": f"Bearer {make_token(user.id)}"}


# ============================================================
# POST /api/v1/scan/receipt
# ============================================================


class TestPostScanReceipt:
    """The frontend no longer sends store_id — the OCR worker resolves the
    store via barcode detection (DA-18). See ``services/scan_service.py``."""

    def _image_payload(self):
        return {"image": ("ticket.jpg", io.BytesIO(_JPEG_BYTES), "image/jpeg")}

    def test_returns_202_and_receipt_id(self, client, user):
        resp = client.post(
            "/api/v1/scan/receipt",
            files=self._image_payload(),
            headers=_auth(user),
        )
        assert resp.status_code == 202
        body = resp.json()
        assert "receipt_id" in body
        uuid.UUID(body["receipt_id"])  # must be valid UUID

    def test_receipt_created_in_db_without_store_id(self, client, user, db):
        resp = client.post(
            "/api/v1/scan/receipt",
            files=self._image_payload(),
            headers=_auth(user),
        )
        receipt_id = uuid.UUID(resp.json()["receipt_id"])
        receipt = db.get(Receipt, receipt_id)
        assert receipt is not None
        assert receipt.store_id is None
        assert receipt.store_status == "unknown"
        assert receipt.user_id == user.id
        assert receipt.image_r2_key == f"{receipt_id}.jpg"
        assert receipt.image_uploaded_at is not None
        assert receipt.image_deleted_at is None

    def test_missing_image_returns_422(self, client, user):
        resp = client.post(
            "/api/v1/scan/receipt",
            headers=_auth(user),
        )
        assert resp.status_code == 422

    def test_no_token_returns_401(self, client):
        resp = client.post(
            "/api/v1/scan/receipt",
            files=self._image_payload(),
        )
        assert resp.status_code == 401


# ============================================================
# GET /api/v1/scan/receipt/{receipt_id}
# ============================================================


class TestGetScanReceipt:
    def _create_receipt(self, db, store, user) -> Receipt:
        r = Receipt(
            id=uuid.uuid4(),
            store_id=store.id,
            user_id=user.id,
            purchased_at=date.today(),
            image_r2_key="somefile.jpg",
        )
        db.add(r)
        db.flush()
        db.commit()
        return r

    def test_pending_when_no_scans(self, client, store, user, db):
        receipt = self._create_receipt(db, store, user)
        resp = client.get(f"/api/v1/scan/receipt/{receipt.id}", headers=_auth(user))
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "pending"
        assert body["matched"] == 0
        assert body["unmatched"] == 0
        assert body["total_amount"] is None

    def test_processing_when_pending_scans(self, client, store, user, db):
        receipt = self._create_receipt(db, store, user)
        _add_scan(db, receipt, store, status="pending")
        resp = client.get(f"/api/v1/scan/receipt/{receipt.id}", headers=_auth(user))
        assert resp.json()["status"] == "processing"

    def test_done_with_matched_and_unmatched(self, client, store, user, db, product):
        receipt = self._create_receipt(db, store, user)
        receipt.total_amount = 1530
        _add_scan(db, receipt, store, status="accepted", product_ean=product.ean)
        _add_scan(db, receipt, store, status="unmatched", product_ean=None)
        db.flush()
        resp = client.get(f"/api/v1/scan/receipt/{receipt.id}", headers=_auth(user))
        body = resp.json()
        assert body["status"] == "done"
        assert body["matched"] == 1
        assert body["unmatched"] == 1
        assert body["total_amount"] == 1530

    def test_rejected_when_all_rejected(self, client, store, user, db):
        receipt = self._create_receipt(db, store, user)
        _add_scan(db, receipt, store, status="rejected")
        resp = client.get(f"/api/v1/scan/receipt/{receipt.id}", headers=_auth(user))
        assert resp.json()["status"] == "rejected"

    def test_failed_when_pipeline_failed(self, client, store, user, db):
        receipt = self._create_receipt(db, store, user)
        _add_scan(db, receipt, store, status="failed")
        resp = client.get(f"/api/v1/scan/receipt/{receipt.id}", headers=_auth(user))
        assert resp.json()["status"] == "failed"

    def test_not_found_returns_404(self, client, user):
        resp = client.get(f"/api/v1/scan/receipt/{uuid.uuid4()}", headers=_auth(user))
        assert resp.status_code == 404
        assert resp.json()["detail"] == "receipt_not_found"

    def test_no_token_returns_401(self, client, store, user, db):
        receipt = self._create_receipt(db, store, user)
        resp = client.get(f"/api/v1/scan/receipt/{receipt.id}")
        assert resp.status_code == 401

    def test_other_user_receipt_returns_403(self, client, store, user, db):
        _other_uid = uuid.uuid4()
        other = User(
            id=_other_uid,
            email="other@ratis.fr",
            account_type="oauth",
            is_deleted=False,
        )
        db.add(other)
        db.flush()
        receipt = self._create_receipt(db, store, other)
        resp = client.get(f"/api/v1/scan/receipt/{receipt.id}", headers=_auth(user))
        assert resp.status_code == 403
        assert resp.json()["detail"] == "forbidden"


# ============================================================
# GET /api/v1/scan/receipt/{receipt_id} — items[] extension
# ============================================================


class TestGetScanReceiptItems:
    """`items: [{scan_id, scanned_name, product_name, product_ean, quantity,
    price_cents, status, match_method}]` is appended to the response."""

    def _create_receipt(self, db, store, user) -> Receipt:
        r = Receipt(
            id=uuid.uuid4(),
            store_id=store.id,
            user_id=user.id,
            purchased_at=date.today(),
            image_r2_key="ticket.jpg",
        )
        db.add(r)
        db.flush()
        db.commit()
        return r

    def _add_scan_full(
        self,
        db,
        receipt,
        store,
        user,
        *,
        status,
        product_ean=None,
        match_method=None,
        scanned_name="LAIT DE DE-ECR",
        price=129,
        quantity=1.0,
        rejected_reason=None,
    ):
        from datetime import datetime as _dt

        # CHECK ck_scans_non_matched_requires_reason : rejected/unresolved
        # rows must carry a reason. Default-fill to a test sentinel.
        if status in ("rejected", "unresolved") and rejected_reason is None:
            rejected_reason = "test_rejected"
        s = Scan(
            id=uuid.uuid4(),
            user_id=user.id,
            store_id=store.id,
            receipt_id=receipt.id,
            scan_type="receipt",
            status=status,
            product_ean=product_ean,
            match_method=match_method,
            rejected_reason=rejected_reason,
            scanned_name=scanned_name,
            price=price,
            quantity=quantity,
        )
        s.scanned_at = _dt.now(UTC)
        db.add(s)
        db.flush()
        db.commit()
        return s

    def test_items_field_present_with_all_keys(self, client, store, user, db, product):
        receipt = self._create_receipt(db, store, user)
        self._add_scan_full(
            db,
            receipt,
            store,
            user,
            status="accepted",
            product_ean=product.ean,
            match_method="barcode_ean",
            scanned_name="LAIT DE DE-ECR",
            price=129,
        )
        resp = client.get(f"/api/v1/scan/receipt/{receipt.id}", headers=_auth(user))
        body = resp.json()
        assert "items" in body
        assert len(body["items"]) == 1
        item = body["items"][0]
        assert set(item.keys()) == {
            "scan_id",
            "scanned_name",
            "product_name",
            "display_name",
            "product_ean",
            "quantity",
            "price_cents",
            "status",
            "match_method",
            "rejected_reason",
            "consensus_state",
        }
        assert item["product_name"] == product.name
        # display_name falls back to product_name when no OFF multi-field
        # is populated on the joined product row (the test fixture only sets `name`).
        assert item["display_name"] == product.name
        assert item["product_ean"] == product.ean
        assert item["price_cents"] == 129
        assert item["status"] == "accepted"
        assert item["match_method"] == "barcode_ean"
        # Legacy v2 'accepted' rows persist with rejected_reason=NULL — the
        # CHECK constraint only enforces NOT NULL for v3 unresolved/rejected.
        assert item["rejected_reason"] is None

    def test_items_display_name_prefers_product_name_fr(self, client, store, user, db):
        """When OFF multi-fields are populated, display_name uses the best one."""
        from ratis_core.models.product import Product

        ean = "7610113013175"
        p = Product(
            ean=ean,
            name="Hipro +",
            source="off",
            product_name_fr="Hipro + protéines fraise",
            generic_name_fr="Yaourt à boire saveur fraise",
            brands_text="Hipro,Danone",
            quantity_text="4 x 250 g",
        )
        db.add(p)
        db.flush()
        db.commit()

        receipt = self._create_receipt(db, store, user)
        self._add_scan_full(
            db,
            receipt,
            store,
            user,
            status="accepted",
            product_ean=ean,
            match_method="barcode_ean",
            scanned_name="HIPRO+",
            price=389,
        )
        resp = client.get(f"/api/v1/scan/receipt/{receipt.id}", headers=_auth(user))
        item = resp.json()["items"][0]
        # product_name remains the raw OFF best-of (backward-compat).
        assert item["product_name"] == "Hipro +"
        # display_name picks product_name_fr per pick_display_name preference.
        assert item["display_name"] == "Hipro + protéines fraise"

    def test_items_display_name_null_when_no_product(self, client, store, user, db):
        """display_name is None when the scan didn't match a product."""
        receipt = self._create_receipt(db, store, user)
        self._add_scan_full(
            db,
            receipt,
            store,
            user,
            status="unmatched",
            product_ean=None,
            match_method=None,
            scanned_name="UNKNOWN ITEM",
            price=150,
        )
        resp = client.get(f"/api/v1/scan/receipt/{receipt.id}", headers=_auth(user))
        item = resp.json()["items"][0]
        assert item["product_name"] is None
        assert item["display_name"] is None

    def test_items_unmatched_has_null_product(self, client, store, user, db):
        receipt = self._create_receipt(db, store, user)
        self._add_scan_full(
            db,
            receipt,
            store,
            user,
            status="unmatched",
            product_ean=None,
            match_method=None,
            scanned_name=None,
            price=150,
        )
        resp = client.get(f"/api/v1/scan/receipt/{receipt.id}", headers=_auth(user))
        items = resp.json()["items"]
        assert len(items) == 1
        assert items[0]["product_name"] is None
        assert items[0]["product_ean"] is None
        assert items[0]["scanned_name"] is None
        assert items[0]["status"] == "unmatched"
        assert items[0]["match_method"] is None

    def test_items_rejected_excluded(self, client, store, user, db, product):
        receipt = self._create_receipt(db, store, user)
        self._add_scan_full(
            db,
            receipt,
            store,
            user,
            status="accepted",
            product_ean=product.ean,
            match_method="fuzzy",
        )
        self._add_scan_full(
            db,
            receipt,
            store,
            user,
            status="rejected",
            product_ean=product.ean,
            match_method="fuzzy",
        )
        resp = client.get(f"/api/v1/scan/receipt/{receipt.id}", headers=_auth(user))
        items = resp.json()["items"]
        assert len(items) == 1
        assert items[0]["status"] == "accepted"

    def test_items_ordered_by_scanned_at_asc(self, client, store, user, db, product):
        from datetime import datetime as _dt
        from datetime import timedelta as _td

        receipt = self._create_receipt(db, store, user)
        base = _dt.now(UTC)
        # Insert OUT of order to confirm ORDER BY works
        s_second = self._add_scan_full(
            db,
            receipt,
            store,
            user,
            status="accepted",
            product_ean=product.ean,
            match_method="fuzzy",
        )
        s_second.scanned_at = base + _td(minutes=5)
        s_first = self._add_scan_full(
            db,
            receipt,
            store,
            user,
            status="accepted",
            product_ean=product.ean,
            match_method="barcode_ean",
        )
        s_first.scanned_at = base
        db.flush()
        db.commit()
        resp = client.get(f"/api/v1/scan/receipt/{receipt.id}", headers=_auth(user))
        items = resp.json()["items"]
        assert [i["scan_id"] for i in items] == [str(s_first.id), str(s_second.id)]

    def test_empty_receipt_has_empty_items(self, client, store, user, db):
        receipt = self._create_receipt(db, store, user)
        resp = client.get(f"/api/v1/scan/receipt/{receipt.id}", headers=_auth(user))
        body = resp.json()
        assert body["items"] == []

    # ── pipeline fields ───────────────────────────────────────────────

    def test_returns_rejected_reason_for_v3_unresolved(self, client, store, user, db):
        """v3 ``unresolved`` rows must surface their rejected_reason so the
        frontend can translate it via formatRejectedReason()."""
        receipt = self._create_receipt(db, store, user)
        self._add_scan_full(
            db,
            receipt,
            store,
            user,
            status="unresolved",
            product_ean=None,
            match_method=None,
            scanned_name="ABCDEF",
            price=199,
            rejected_reason="no_fuzzy_candidate",
        )
        resp = client.get(f"/api/v1/scan/receipt/{receipt.id}", headers=_auth(user))
        items = resp.json()["items"]
        assert len(items) == 1
        assert items[0]["status"] == "unresolved"
        assert items[0]["rejected_reason"] == "no_fuzzy_candidate"

    def test_product_name_is_products_name_not_brand(self, client, store, user, db):
        """Bug 3 (alpha 2026-05-01): the SELECT must return ``products.name``
        (e.g. 'Yaourt à boire saveur fraise'), never the brand
        ('Hipro')."""
        from ratis_core.models.product import Brand, Product

        brand = Brand(id=uuid.uuid4(), name="Hipro", slug="hipro")
        db.add(brand)
        db.flush()
        prod = Product(
            ean="7610113013175",
            name="Yaourt à boire saveur fraise",
            source="off",
            brand_id=brand.id,
            brands="Hipro",
        )
        db.add(prod)
        db.flush()
        db.commit()

        receipt = self._create_receipt(db, store, user)
        self._add_scan_full(
            db,
            receipt,
            store,
            user,
            status="matched",
            product_ean=prod.ean,
            match_method="barcode",
            scanned_name="HIPRO YAO FRAISE",
            price=199,
        )
        resp = client.get(f"/api/v1/scan/receipt/{receipt.id}", headers=_auth(user))
        items = resp.json()["items"]
        assert len(items) == 1
        assert items[0]["product_name"] == "Yaourt à boire saveur fraise"
        assert items[0]["product_name"] != "Hipro"
        # scanned_name surfaced too — frontend prefers product_name when set.
        assert items[0]["scanned_name"] == "HIPRO YAO FRAISE"

    def test_pipeline_matched_counted_in_aggregates(self, client, store, user, db, product):
        """``status='matched'`` (v3) feeds the ``matched`` count alongside
        legacy v2 ``accepted``."""
        receipt = self._create_receipt(db, store, user)
        self._add_scan_full(
            db,
            receipt,
            store,
            user,
            status="matched",
            product_ean=product.ean,
            match_method="barcode",
        )
        resp = client.get(f"/api/v1/scan/receipt/{receipt.id}", headers=_auth(user))
        body = resp.json()
        assert body["matched"] == 1
        assert body["status"] == "done"

    def test_pipeline_unresolved_counted_in_unmatched_aggregate(self, client, store, user, db):
        """``status='unresolved'`` (v3) feeds the ``unmatched`` count
        alongside legacy v2 ``unmatched``."""
        receipt = self._create_receipt(db, store, user)
        self._add_scan_full(
            db,
            receipt,
            store,
            user,
            status="unresolved",
            product_ean=None,
            match_method=None,
            rejected_reason="no_fuzzy_candidate",
        )
        resp = client.get(f"/api/v1/scan/receipt/{receipt.id}", headers=_auth(user))
        body = resp.json()
        assert body["unmatched"] == 1
        assert body["status"] == "done"

    # ── consensus_state surfacing (NRC bloc E) ───────────────────────────

    def test_items_consensus_state_null_when_no_ledger_row(
        self,
        client,
        store,
        user,
        db,
        product,
    ):
        """A scan without any ``product_name_resolutions`` row has no
        consensus context (UNRESOLVED). The endpoint surfaces ``None``
        rather than a string so the frontend renders no badge.
        """
        receipt = self._create_receipt(db, store, user)
        self._add_scan_full(
            db,
            receipt,
            store,
            user,
            status="matched",
            product_ean=product.ean,
            match_method="barcode",
            scanned_name="LAIT 1L",
            price=129,
        )
        resp = client.get(f"/api/v1/scan/receipt/{receipt.id}", headers=_auth(user))
        items = resp.json()["items"]
        assert len(items) == 1
        assert "consensus_state" in items[0]
        assert items[0]["consensus_state"] is None

    def test_items_consensus_state_pending_when_one_validator(
        self,
        client,
        store,
        user,
        db,
        product,
    ):
        """A single ledger row → quorum not reached → state ``PENDING``."""
        from ratis_core.models.name_resolution import ProductNameResolution

        receipt = self._create_receipt(db, store, user)
        scan = self._add_scan_full(
            db,
            receipt,
            store,
            user,
            status="matched",
            product_ean=product.ean,
            match_method="barcode",
            scanned_name="LAIT 1L",
            price=129,
        )
        db.add(
            ProductNameResolution(
                id=uuid.uuid4(),
                scan_id=scan.id,
                store_id=store.id,
                normalized_label="lait 1l",
                product_ean=product.ean,
                user_id=user.id,
                match_method="barcode",
            )
        )
        db.flush()
        db.commit()

        resp = client.get(f"/api/v1/scan/receipt/{receipt.id}", headers=_auth(user))
        items = resp.json()["items"]
        assert items[0]["consensus_state"] == "pending"

    def test_items_consensus_state_verified_when_quorum_and_convergence(
        self,
        client,
        store,
        user,
        db,
        product,
    ):
        """Three distinct validators converging on the same EAN ⇒ VERIFIED.

        The current scan has its own ledger row, plus 2 sibling rows from
        other users for the same ``(store_id, normalized_label)``. All
        three vote ``barcode`` for ``product.ean`` — quorum + 100% top1.
        """
        from ratis_core.models.name_resolution import ProductNameResolution
        from ratis_core.models.scan import Scan as _Scan

        receipt = self._create_receipt(db, store, user)
        scan = self._add_scan_full(
            db,
            receipt,
            store,
            user,
            status="matched",
            product_ean=product.ean,
            match_method="barcode",
            scanned_name="LAIT 1L",
            price=129,
        )
        # Own row.
        db.add(
            ProductNameResolution(
                id=uuid.uuid4(),
                scan_id=scan.id,
                store_id=store.id,
                normalized_label="lait 1l",
                product_ean=product.ean,
                user_id=user.id,
                match_method="barcode",
            )
        )
        # Two sibling validators (distinct users + distinct scans) on the
        # SAME (store, label) pair.
        for tag in ("v1", "v2"):
            _verif_uid = uuid.uuid4()
            other_user = User(
                id=_verif_uid,
                email=f"verif-{tag}@x.fr",
                account_type="oauth",
                is_deleted=False,
            )
            db.add(other_user)
            db.flush()
            sib_scan = _Scan(
                id=uuid.uuid4(),
                user_id=other_user.id,
                store_id=store.id,
                scan_type="electronic_label",
                status="accepted",
                store_status="confirmed",
                product_ean=product.ean,
                scanned_name="LAIT 1L",
                price=129,
                quantity=1,
                match_method="barcode",
            )
            db.add(sib_scan)
            db.flush()
            db.add(
                ProductNameResolution(
                    id=uuid.uuid4(),
                    scan_id=sib_scan.id,
                    store_id=store.id,
                    normalized_label="lait 1l",
                    product_ean=product.ean,
                    user_id=other_user.id,
                    match_method="barcode",
                )
            )
        db.flush()
        db.commit()

        resp = client.get(f"/api/v1/scan/receipt/{receipt.id}", headers=_auth(user))
        items = resp.json()["items"]
        assert items[0]["consensus_state"] == "verified"


# ============================================================
# Helpers
# ============================================================


def _add_scan(db, receipt: Receipt, store, status: str, product_ean: str | None = None):
    s = Scan(
        id=uuid.uuid4(),
        store_id=store.id,
        receipt_id=receipt.id,
        scan_type="receipt",
        status=status,
        # CHECK ck_scans_non_matched_requires_reason : rejected/unresolved
        # rows must carry a reason. Use a generic test sentinel when the
        # test does not care about the specific reason.
        rejected_reason=("test_rejected" if status in ("rejected", "unresolved") else None),
        product_ean=product_ean,
        scanned_name="Nutella 400g",
        price=250,
        quantity=1.0,
    )
    db.add(s)
    db.flush()
    db.commit()
    return s


# ============================================================
# Validation — types et taille
# ============================================================


class TestPostScanReceiptValidation:
    def _post(self, client, user, content: bytes, content_type: str, filename: str):
        return client.post(
            "/api/v1/scan/receipt",
            files={"image": (filename, io.BytesIO(content), content_type)},
            headers=_auth(user),
        )

    def test_png_accepted(self, client, user):
        resp = self._post(client, user, _PNG_BYTES, "image/png", "ticket.png")
        assert resp.status_code == 202

    def test_webp_accepted(self, client, user):
        resp = self._post(client, user, _WEBP_BYTES, "image/webp", "ticket.webp")
        assert resp.status_code == 202

    def test_pdf_accepted(self, client, user):
        resp = self._post(client, user, _PDF_BYTES, "application/pdf", "ticket.pdf")
        assert resp.status_code == 202

    def test_pdf_key_has_pdf_extension(self, client, user, db):
        resp = self._post(client, user, _PDF_BYTES, "application/pdf", "ticket.pdf")
        receipt_id = uuid.UUID(resp.json()["receipt_id"])
        receipt = db.get(Receipt, receipt_id)
        assert receipt.image_r2_key == f"{receipt_id}.pdf"

    def test_jpeg_key_has_jpg_extension(self, client, user, db):
        resp = self._post(client, user, _JPEG_BYTES, "image/jpeg", "ticket.jpg")
        receipt_id = uuid.UUID(resp.json()["receipt_id"])
        receipt = db.get(Receipt, receipt_id)
        assert receipt.image_r2_key == f"{receipt_id}.jpg"

    def test_unsupported_type_returns_422(self, client, user):
        resp = self._post(client, user, _EXE_BYTES, "application/octet-stream", "malware.exe")
        assert resp.status_code == 422
        assert resp.json()["detail"] == "unsupported_file_type"

    def test_text_type_returns_422(self, client, user):
        resp = self._post(client, user, b"hello world", "text/plain", "notes.txt")
        assert resp.status_code == 422
        assert resp.json()["detail"] == "unsupported_file_type"

    def test_file_too_large_returns_422(self, client, user):
        big = b"x" * (10 * 1024 * 1024 + 1)
        resp = self._post(client, user, big, "image/jpeg", "big.jpg")
        assert resp.status_code == 422
        assert resp.json()["detail"] == "file_too_large"

    def test_spoofed_exe_as_jpeg_returns_422(self, client, user):
        """EXE magic bytes declared as image/jpeg → rejected by magic bytes check."""
        resp = self._post(client, user, _EXE_BYTES, "image/jpeg", "ticket.jpg")
        assert resp.status_code == 422
        assert resp.json()["detail"] == "unsupported_file_type"

    def test_spoofed_exe_as_webp_returns_422(self, client, user):
        """EXE magic bytes declared as image/webp → rejected.

        Guard for the WebP fallback in `uploads._looks_like_webp` : a non-RIFF
        payload declared as WebP must still be rejected, otherwise the
        libmagic-bypass we added for libmagic 5.x's poor WebP support (KP-87)
        would become an attack vector. The 12-byte signature check (RIFF+WEBP
        FourCC) catches this.
        """
        resp = self._post(client, user, _EXE_BYTES, "image/webp", "ticket.webp")
        assert resp.status_code == 422
        assert resp.json()["detail"] == "unsupported_file_type"

    def test_spoofed_wav_as_webp_returns_422(self, client, user):
        """RIFF/WAVE bytes declared as image/webp → rejected.

        WAV is also a RIFF container — only the 4-byte FourCC at offset 8
        distinguishes it from WebP. The signature check in `_looks_like_webp`
        verifies *both* `RIFF` at offset 0 *and* `WEBP` at offset 8, so a WAV
        smuggled as WebP must still be rejected.
        """
        wav_bytes = b"RIFF\x20\x00\x00\x00WAVE" + b"\x00" * 100
        resp = self._post(client, user, wav_bytes, "image/webp", "ticket.webp")
        assert resp.status_code == 422
        assert resp.json()["detail"] == "unsupported_file_type"

    def test_queue_failure_cleans_up_r2(self, db, user, monkeypatch):
        """Si Redis est down après upload R2, l'image doit être supprimée (pas d'orphelin)."""
        from fastapi.testclient import TestClient
        from main import app
        from ratis_core.database import get_db

        deleted_keys = []
        monkeypatch.setattr("services.scan_service.upload_receipt_image", lambda *a, **kw: None)
        monkeypatch.setattr("services.scan_service.delete_receipt_image", lambda key, **kw: deleted_keys.append(key))

        def _fail_enqueue(receipt_id):
            raise RuntimeError("Redis is down")

        monkeypatch.setattr("services.scan_service.enqueue_ocr_job", _fail_enqueue)

        def override_db():
            try:
                yield db
            finally:
                pass

        app.dependency_overrides[get_db] = override_db
        try:
            with TestClient(app) as c:
                resp = c.post(
                    "/api/v1/scan/receipt",
                    files={"image": ("ticket.jpg", io.BytesIO(_JPEG_BYTES), "image/jpeg")},
                    headers=_auth(user),
                )
        finally:
            app.dependency_overrides.clear()

        assert resp.status_code == 503
        assert resp.json()["detail"] == "queue_unavailable"
        assert len(deleted_keys) == 1


# ============================================================
# Photo hash — déduplication tickets
# ============================================================


class TestPhotoHashReceipt:
    def _post(self, c, user, content=None):
        return c.post(
            "/api/v1/scan/receipt",
            files={"image": ("t.jpg", io.BytesIO(content or _JPEG_BYTES), "image/jpeg")},
            headers=_auth(user),
        )

    def test_duplicate_receipt_returns_409(self, client, user):
        self._post(client, user)
        resp = self._post(client, user)
        assert resp.status_code == 409
        assert resp.json()["detail"] == "duplicate_photo"

    def test_different_bytes_accepted(self, client, user):
        self._post(client, user, _JPEG_BYTES)
        resp = self._post(client, user, _PNG_BYTES)
        assert resp.status_code == 202

    def test_concurrent_photo_hash_violation_returns_409(self, client, user, monkeypatch):
        """A concurrent upload of the same photo loses the check-first race
        and hits the ``receipts_photo_hash_unique`` index on flush. That
        IntegrityError must surface as 409 ``duplicate_photo``, not 500."""
        from sqlalchemy.exc import IntegrityError

        def _raise_unique(*a, **kw):
            raise IntegrityError(
                "INSERT INTO receipts ...",
                {},
                Exception('duplicate key value violates unique constraint "receipts_photo_hash_unique"'),
            )

        monkeypatch.setattr("services.scan_service.create_receipt", _raise_unique)
        resp = self._post(client, user)
        assert resp.status_code == 409
        assert resp.json()["detail"] == "duplicate_photo"

    def test_photo_hash_stored_on_receipt(self, client, user, db):
        expected = hashlib.sha256(_JPEG_BYTES).hexdigest()
        resp = self._post(client, user)
        receipt = db.get(Receipt, uuid.UUID(resp.json()["receipt_id"]))
        assert receipt.photo_hash == expected

    def test_r2_not_called_on_duplicate(self, db, user, monkeypatch):
        upload_calls = []
        monkeypatch.setattr(
            "services.scan_service.upload_receipt_image",
            lambda *a, **kw: upload_calls.append(1),
        )
        monkeypatch.setattr("services.scan_service.delete_receipt_image", lambda *a, **kw: None)
        monkeypatch.setattr("services.scan_service.enqueue_ocr_job", lambda *a: None)

        from main import app
        from ratis_core.database import get_db

        app.dependency_overrides[get_db] = lambda: (yield db)
        try:
            with TestClient(app) as c:
                c.post(
                    "/api/v1/scan/receipt",
                    files={"image": ("t.jpg", io.BytesIO(_JPEG_BYTES), "image/jpeg")},
                    headers=_auth(user),
                )
                count_after_first = len(upload_calls)
                c.post(
                    "/api/v1/scan/receipt",
                    files={"image": ("t.jpg", io.BytesIO(_JPEG_BYTES), "image/jpeg")},
                    headers=_auth(user),
                )
        finally:
            app.dependency_overrides.clear()

        assert count_after_first == 1
        assert len(upload_calls) == 1


# ============================================================
# POST /api/v1/scan/receipt — idempotency key
# ============================================================


class TestReceiptIdempotencyKey:
    """A client-generated ``idempotency_key`` lets a retried upload (app
    killed after the POST succeeded server-side but before the client
    recorded success) replay safely: the second call returns the SAME
    ``receipt_id`` with 202 instead of creating a duplicate receipt or
    raising 409 ``duplicate_photo`` (which the client treats as a hard
    failure)."""

    def _post(self, c, user, idem_key, content=None):
        data = {"idempotency_key": str(idem_key)} if idem_key else {}
        return c.post(
            "/api/v1/scan/receipt",
            files={"image": ("t.jpg", io.BytesIO(content or _JPEG_BYTES), "image/jpeg")},
            data=data,
            headers=_auth(user),
        )

    def test_same_key_twice_returns_one_receipt(self, client, user, db):
        key = uuid.uuid4()
        first = self._post(client, user, key)
        assert first.status_code == 202
        second = self._post(client, user, key)
        assert second.status_code == 202
        # Second call replays the first — same receipt_id, no new row.
        assert second.json()["receipt_id"] == first.json()["receipt_id"]
        count = db.query(Receipt).filter(Receipt.idempotency_key == key).count()
        assert count == 1

    def test_idempotency_key_stored_on_receipt(self, client, user, db):
        key = uuid.uuid4()
        resp = self._post(client, user, key)
        receipt = db.get(Receipt, uuid.UUID(resp.json()["receipt_id"]))
        assert receipt.idempotency_key == key

    def test_replay_does_not_re_upload_to_r2(self, db, user, monkeypatch):
        upload_calls: list[int] = []
        monkeypatch.setattr(
            "services.scan_service.upload_receipt_image",
            lambda *a, **kw: upload_calls.append(1),
        )
        monkeypatch.setattr("services.scan_service.delete_receipt_image", lambda *a, **kw: None)
        monkeypatch.setattr("services.scan_service.enqueue_ocr_job", lambda *a: None)

        from main import app
        from ratis_core.database import get_db

        app.dependency_overrides[get_db] = lambda: (yield db)
        key = uuid.uuid4()
        try:
            with TestClient(app) as c:
                self._post(c, user, key)
                after_first = len(upload_calls)
                self._post(c, user, key)
        finally:
            app.dependency_overrides.clear()

        assert after_first == 1
        # Replay short-circuits before any R2 upload.
        assert len(upload_calls) == 1

    def test_no_key_still_works(self, client, user):
        """The key is optional — uploads without one keep the legacy path."""
        resp = self._post(client, user, idem_key=None)
        assert resp.status_code == 202
        assert "receipt_id" in resp.json()

    def test_different_keys_create_distinct_receipts(self, client, user):
        """Two uploads with different keys are independent — the photo_hash
        guard still rejects the byte-identical second one with 409."""
        first = self._post(client, user, uuid.uuid4(), _JPEG_BYTES)
        assert first.status_code == 202
        second = self._post(client, user, uuid.uuid4(), _PNG_BYTES)
        assert second.status_code == 202
        assert second.json()["receipt_id"] != first.json()["receipt_id"]


# ============================================================
# GET /api/v1/scan/check-hash
# ============================================================


class TestCheckHashEndpoint:
    _valid_hash = "a" * 64  # 64-char hex string

    def test_unknown_hash_returns_false(self, client, user):
        resp = client.get(
            f"/api/v1/scan/check-hash?hash={self._valid_hash}",
            headers=_auth(user),
        )
        assert resp.status_code == 200
        assert resp.json() == {"duplicate": False}

    def test_known_receipt_hash_returns_true(self, client, user, db):
        client.post(
            "/api/v1/scan/receipt",
            files={"image": ("t.jpg", io.BytesIO(_JPEG_BYTES), "image/jpeg")},
            headers=_auth(user),
        )
        known_hash = hashlib.sha256(_JPEG_BYTES).hexdigest()
        resp = client.get(
            f"/api/v1/scan/check-hash?hash={known_hash}",
            headers=_auth(user),
        )
        assert resp.status_code == 200
        assert resp.json() == {"duplicate": True}

    def test_invalid_hash_length_returns_422(self, client, user):
        resp = client.get(
            "/api/v1/scan/check-hash?hash=tooshort",
            headers=_auth(user),
        )
        assert resp.status_code == 422

    def test_no_token_returns_401(self, client):
        resp = client.get(f"/api/v1/scan/check-hash?hash={self._valid_hash}")
        assert resp.status_code == 401


# ============================================================
# Rate limiting — POST /receipt
# ============================================================


class TestRateLimitReceipt:
    def _post(self, c, user, content=None):
        return c.post(
            "/api/v1/scan/receipt",
            files={"image": ("t.jpg", io.BytesIO(content or _JPEG_BYTES), "image/jpeg")},
            headers=_auth(user),
        )

    def test_fourth_request_returns_429(self, client, user):
        images = [_JPEG_BYTES, _PNG_BYTES, _WEBP_BYTES, _PDF_BYTES]
        for img in images[:3]:
            self._post(client, user, img)
        resp = self._post(client, user, _PDF_BYTES)
        assert resp.status_code == 429
        assert resp.json()["detail"] == "rate_limit_exceeded"


# ============================================================
# Rate limiting — GET /check-hash
# ============================================================


class TestRateLimitCheckHash:
    _valid_hash = "a" * 64

    def test_twentyfirst_request_returns_429(self, client, user):
        url = f"/api/v1/scan/check-hash?hash={self._valid_hash}"
        for _ in range(20):
            resp = client.get(url, headers=_auth(user))
            assert resp.status_code == 200
        resp = client.get(url, headers=_auth(user))
        assert resp.status_code == 429
        assert resp.json()["detail"] == "rate_limit_exceeded"


class TestJwtEdgeCases:
    def test_jwt_missing_sub_returns_401(self, client, user):
        """JWT valide mais sans claim 'sub' → 401, pas 500."""
        from ratis_core.testing import make_test_token

        from tests.conftest import JWT_TEST_PRIVATE_PEM

        bad_token = make_test_token(
            {"type": "access", "aud": "ratis"},
            JWT_TEST_PRIVATE_PEM,
        )
        resp = client.get(
            f"/api/v1/scan/receipt/{uuid.uuid4()}",
            headers={"Authorization": f"Bearer {bad_token}"},
        )
        assert resp.status_code == 401
