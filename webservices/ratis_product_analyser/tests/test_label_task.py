from __future__ import annotations

import uuid
from unittest.mock import MagicMock

import cv2
import numpy as np
from ratis_core.models.scan import Scan
from worker.ocr.types import OcrResult

# ── helpers ───────────────────────────────────────────────────────────────────


def _fake_image() -> np.ndarray:
    img = np.full((200, 400, 3), 240, dtype=np.uint8)
    cv2.putText(img, "LABEL", (10, 50), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 0, 0), 2)
    return img


def _mock_engine(ocr_result: OcrResult) -> MagicMock:
    engine = MagicMock()
    engine.recognize.return_value = ocr_result
    return engine


def _mock_s3(image: np.ndarray) -> MagicMock:
    _, buf = cv2.imencode(".jpg", image)
    image_bytes = buf.tobytes()

    def download_fileobj(bucket, key, fileobj):
        fileobj.write(image_bytes)

    s3 = MagicMock()
    s3.download_fileobj.side_effect = download_fileobj
    return s3


def _make_label_scan(db, store, user) -> Scan:
    scan = Scan(
        id=uuid.uuid4(),
        store_id=store.id,
        user_id=user.id,
        scan_type="electronic_label",
        status="pending",
        price=0,
        quantity=1.0,
        label_r2_key="label/fake-key.jpg",
    )
    db.add(scan)
    db.flush()
    return scan


def _run(db, scan, ocr_result: OcrResult, hint: str = "label"):
    from worker.label_task import process_label

    process_label.apply(
        args=[str(scan.id)],
        kwargs={
            "hint": hint,
            "_engine": _mock_engine(ocr_result),
            "_s3": _mock_s3(_fake_image()),
            "_db": db,
        },
    )
    db.expire_all()


# ── tests ─────────────────────────────────────────────────────────────────────


class TestProcessLabel:
    def test_accepted_when_product_matched_by_ean(self, db, store, user, product):
        """Label with EAN in catalogue → scan accepted with product_ean."""
        ocr: OcrResult = [
            ("Nutella 400g", 0.95),
            ("3,49", 0.92),
            (product.ean, 0.88),
        ]
        scan = _make_label_scan(db, store, user)
        _run(db, scan, ocr)

        assert scan.status == "accepted"
        assert scan.product_ean == product.ean
        assert scan.price == 349
        assert scan.scanned_name == "Nutella 400g"

    def test_unmatched_when_no_product_found(self, db, store, user):
        """Label with unknown product → status unmatched, no product_ean."""
        ocr: OcrResult = [
            ("Produit Inconnu XYZ", 0.90),
            ("5,99", 0.88),
        ]
        scan = _make_label_scan(db, store, user)
        _run(db, scan, ocr)

        assert scan.status == "unmatched"
        assert scan.product_ean is None
        assert scan.price == 599

    def test_rejected_when_ocr_returns_no_price(self, db, store, user):
        """OCR result with no parseable price → rejected."""
        ocr: OcrResult = [
            ("SOME TEXT NO PRICE HERE", 0.80),
        ]
        scan = _make_label_scan(db, store, user)
        _run(db, scan, ocr)

        assert scan.status == "rejected"
        assert scan.rejected_reason == "ocr_no_result"

    def test_rejected_when_no_name_candidate(self, db, store, user):
        """Price only, no product name → rejected."""
        ocr: OcrResult = [
            ("1,99", 0.92),
        ]
        scan = _make_label_scan(db, store, user)
        _run(db, scan, ocr)

        assert scan.status == "rejected"

    def test_receipt_detected_best_effort_extraction(self, db, store, user, product):
        """If OCR looks like a receipt (has TOTAL), we still extract the first item."""
        ocr: OcrResult = [
            (product.ean, 0.85),
            ("Nutella 400g", 0.90),
            ("3,49", 0.92),
            ("TOTAL", 0.95),
            ("3,49", 0.90),
        ]
        scan = _make_label_scan(db, store, user)
        _run(db, scan, ocr, hint="label")

        # Best-effort: we still extracted something
        assert scan.status in ("accepted", "unmatched")
        # rejected_reason only set on non-accepted scans
        if scan.status == "accepted":
            assert scan.rejected_reason is None
        else:
            assert scan.rejected_reason == "hint_mismatch:likely_receipt"
        # EAN block must not have been used as the product name
        assert scan.scanned_name == "Nutella 400g"

    def test_ean_first_block_does_not_become_scanned_name(self, db, store, user, product):
        """EAN-only block appearing before the product name must not pollute scanned_name."""
        ocr: OcrResult = [
            (product.ean, 0.88),  # barcode block — digits only
            ("Nutella 400g", 0.95),
            ("3,49", 0.92),
        ]
        scan = _make_label_scan(db, store, user)
        _run(db, scan, ocr)

        assert scan.scanned_name == "Nutella 400g"
        assert scan.product_ean == product.ean
        assert scan.status == "accepted"

    def test_invalid_scan_id_does_not_raise(self, db):
        """Invalid UUID → logged, no exception."""
        from worker.label_task import process_label

        process_label.apply(args=["not-a-uuid"], kwargs={"_db": db})

    def test_missing_scan_returns_gracefully(self, db):
        """Scan not found in DB → logged, no exception."""
        from worker.label_task import process_label

        process_label.apply(args=[str(uuid.uuid4())], kwargs={"_db": db})


class TestProcessLabelTypeDetection:
    def test_label_hint_no_mismatch_flag_when_truly_label(self, db, store, user):
        """Unambiguous label OCR → no hint_mismatch rejected_reason."""
        ocr: OcrResult = [
            ("Yaourt nature", 0.90),
            ("1,29", 0.92),
        ]
        scan = _make_label_scan(db, store, user)
        _run(db, scan, ocr, hint="label")

        # rejected_reason should NOT contain receipt mismatch
        assert scan.rejected_reason is None or "receipt" not in (scan.rejected_reason or "")

    def test_ean_not_in_catalogue_falls_back_to_fuzzy(self, db, store, user):
        """EAN found in OCR but not in catalogue → fall back to fuzzy match."""
        ocr: OcrResult = [
            ("Some product", 0.90),
            ("2,49", 0.92),
            ("9999999999999", 0.80),  # EAN not in DB
        ]
        scan = _make_label_scan(db, store, user)
        _run(db, scan, ocr)

        # Falls back to unmatched (no fuzzy match for "Some product" either)
        assert scan.status in ("accepted", "unmatched")
        assert scan.product_ean is None or scan.match_method in ("fuzzy", "observed_name", "manual")

    def test_empty_inverted_pass_creates_rejected_scan(self, db, store, user):
        """When pass_inverted returns [], the pipeline must return None (not []),
        so the scan is marked rejected — not silently processed with empty data."""
        from unittest.mock import MagicMock

        from worker.label_task import process_label

        engine = MagicMock()
        # 3 main passes all diverge (not fuzzy-similar) → arbitrate returns
        # None → fallback pass_inverted returns [] → guard must stop the pipeline.
        engine.recognize.side_effect = [
            [("NUTELLA 2,50", 0.90)],  # pass_corrected
            [("JAMBON 3,49", 0.85)],  # pass_clahe — unrelated
            [("LAIT 0,99", 0.80)],  # pass_binarized — unrelated
            [],  # pass_inverted — empty
        ]
        scan = _make_label_scan(db, store, user)
        process_label.apply(
            args=[str(scan.id)],
            kwargs={"_engine": engine, "_s3": _mock_s3(_fake_image()), "_db": db},
        )
        db.expire_all()

        assert scan.status == "rejected"
        assert scan.rejected_reason == "ocr_no_result"


class TestEanBarcodeMatching:
    """pyzbar EAN reading on electronic label — prioritized over OCR EAN and fuzzy."""

    def test_barcode_ean_matches_product_directly(self, db, store, user, product, monkeypatch):
        """EAN read by pyzbar → direct match, OCR still runs for scanned_name/price."""
        monkeypatch.setattr(
            "worker.label_task.read_ean_barcode",
            lambda _img: product.ean,
        )
        ocr: OcrResult = [
            ("Nutella 400g", 0.95),
            ("3,49", 0.92),
        ]
        scan = _make_label_scan(db, store, user)
        _run(db, scan, ocr)

        assert scan.status == "accepted"
        assert scan.product_ean == product.ean
        assert scan.match_method == "barcode_ean"
        assert scan.scanned_name == "Nutella 400g"
        assert scan.price == 349

    def test_barcode_ean_not_in_catalogue_falls_back(self, db, store, user, monkeypatch):
        """EAN from pyzbar not in products table → falls back to OCR EAN / fuzzy."""
        monkeypatch.setattr(
            "worker.label_task.read_ean_barcode",
            lambda _img: "9999999999999",
        )
        ocr: OcrResult = [
            ("Produit Inconnu", 0.90),
            ("2,49", 0.92),
        ]
        scan = _make_label_scan(db, store, user)
        _run(db, scan, ocr)

        # No match for unknown EAN or unknown name → unmatched
        assert scan.status == "unmatched"
        assert scan.product_ean is None

    def test_barcode_ean_prioritized_over_ocr_ean(self, db, store, user, product, monkeypatch):
        """When both pyzbar and OCR find an EAN, pyzbar wins."""
        monkeypatch.setattr(
            "worker.label_task.read_ean_barcode",
            lambda _img: product.ean,
        )
        ocr: OcrResult = [
            ("Nutella 400g", 0.95),
            ("3,49", 0.92),
            ("9999999999999", 0.80),  # OCR EAN — wrong, should be ignored
        ]
        scan = _make_label_scan(db, store, user)
        _run(db, scan, ocr)

        assert scan.product_ean == product.ean
        assert scan.match_method == "barcode_ean"

    def test_no_barcode_falls_back_to_ocr_ean(self, db, store, user, product, monkeypatch):
        """No pyzbar EAN → existing OCR EAN path still works."""
        monkeypatch.setattr(
            "worker.label_task.read_ean_barcode",
            lambda _img: None,
        )
        ocr: OcrResult = [
            ("Nutella 400g", 0.95),
            ("3,49", 0.92),
            (product.ean, 0.80),
        ]
        scan = _make_label_scan(db, store, user)
        _run(db, scan, ocr)

        assert scan.product_ean == product.ean
        assert scan.match_method == "manual"  # existing OCR EAN path

    def test_pyzbar_exception_continues_pipeline(self, db, store, user, product, monkeypatch):
        """pyzbar crash → pipeline continues with OCR matching (fire-and-forget)."""
        monkeypatch.setattr(
            "worker.label_task.read_ean_barcode",
            lambda _img: (_ for _ in ()).throw(RuntimeError("zbar crash")),
        )
        ocr: OcrResult = [
            ("Nutella 400g", 0.95),
            ("3,49", 0.92),
            (product.ean, 0.80),
        ]
        scan = _make_label_scan(db, store, user)
        _run(db, scan, ocr)

        # Pipeline continues despite pyzbar crash — OCR EAN still matched
        assert scan.status == "accepted"
        assert scan.product_ean == product.ean


class TestPhotoHashFailedPath:
    """photo_hash must be cleared when the label task reaches a failed terminal state.

    rejected → keep hash (same photo → same outcome, retry pointless).
    failed   → clear hash (infra failure, user may retry with same photo).
    """

    def _make_scan_with_hash(self, db, store, user) -> Scan:
        from ratis_core.models.scan import Scan as ScanModel

        s = ScanModel(
            id=uuid.uuid4(),
            store_id=store.id,
            user_id=user.id,
            scan_type="electronic_label",
            status="pending",
            price=0,
            quantity=1.0,
            label_r2_key="label/fake-key.jpg",
            photo_hash="b" * 64,
        )
        db.add(s)
        db.flush()
        db.commit()
        return s

    def test_download_failure_clears_photo_hash(self, db, store, user, monkeypatch):
        """MaxRetriesExceeded on download → scan.photo_hash set to NULL."""
        from worker.label_task import process_label

        scan = self._make_scan_with_hash(db, store, user)

        s3_fail = MagicMock()
        s3_fail.download_fileobj.side_effect = OSError("S3 unavailable")

        monkeypatch.setattr(process_label, "max_retries", 0)

        process_label.apply(
            args=[str(scan.id)],
            kwargs={"_s3": s3_fail, "_db": db},
        )
        db.expire_all()

        assert scan.photo_hash is None
        assert scan.status == "failed"

    def test_commit_failure_clears_photo_hash(self, db, store, user, monkeypatch):
        """MaxRetriesExceeded on db.commit → scan.photo_hash set to NULL."""
        from worker.label_task import process_label

        scan = self._make_scan_with_hash(db, store, user)

        commit_calls = {"n": 0}
        original_commit = db.commit

        def failing_commit():
            commit_calls["n"] += 1
            if commit_calls["n"] == 1:
                raise Exception("DB commit error")
            original_commit()

        monkeypatch.setattr(db, "commit", failing_commit)
        monkeypatch.setattr(process_label, "max_retries", 0)

        process_label.apply(
            args=[str(scan.id)],
            kwargs={
                "_engine": _mock_engine([("Nutella 400g", 0.90), ("3,49", 0.88)]),
                "_s3": _mock_s3(_fake_image()),
                "_db": db,
            },
        )
        db.expire_all()

        assert scan.photo_hash is None

    def test_rejected_invalid_image_keeps_photo_hash(self, db, store, user, monkeypatch):
        """InvalidImageError → rejected — photo_hash preserved (retry with same photo futile)."""
        import worker.label_task as task_module
        from worker.label_task import process_label
        from worker.ocr.exceptions import InvalidImageError

        scan = self._make_scan_with_hash(db, store, user)

        def _raise_invalid(_raw):
            raise InvalidImageError("corrupted")

        monkeypatch.setattr(task_module, "_decode_image", _raise_invalid)

        process_label.apply(
            args=[str(scan.id)],
            kwargs={"_s3": _mock_s3(_fake_image()), "_db": db},
        )
        db.expire_all()

        assert scan.photo_hash == "b" * 64
        assert scan.status == "rejected"
        assert scan.rejected_reason == "invalid_image"


# ── Bloc D — ESL → ledger writes ──────────────────────────────────────────────
#
# Bloc D NRC (cross-retailer consensus) wires ``record_resolution`` into
# the label task so that every successful ESL match feeds the
# ``product_name_resolutions`` ledger with ``source_type='esl'`` and
# ``match_method='esl'``. See
# ``ARCH_cross_retailer_consensus.md`` § "Pour un scan ESL" + § Bloc D.
#
# Idempotence is guaranteed by the ``ON CONFLICT (scan_id, source_type,
# normalized_label) DO NOTHING`` clause inside ``record_resolution`` —
# replays of the Celery task on the same scan never duplicate rows.
class TestEslLedgerWrites:
    """ESL ledger writes wired in worker/label_task.py (Bloc D)."""

    def _ledger_rows(self, db, scan_id):
        from ratis_core.models.name_resolution import ProductNameResolution

        return db.query(ProductNameResolution).filter(ProductNameResolution.scan_id == scan_id).all()

    def test_pyzbar_match_writes_pnr_row_with_source_type_esl(
        self,
        db,
        store,
        user,
        product,
        monkeypatch,
    ):
        """pyzbar EAN match → ledger row source_type='esl' / match_method='esl'."""
        monkeypatch.setattr(
            "worker.label_task.read_ean_barcode",
            lambda _img: product.ean,
        )
        ocr: OcrResult = [
            ("Nutella 400g", 0.95),
            ("3,49", 0.92),
        ]
        scan = _make_label_scan(db, store, user)
        _run(db, scan, ocr)

        rows = self._ledger_rows(db, scan.id)
        assert len(rows) == 1
        row = rows[0]
        assert row.source_type == "esl"
        assert row.match_method == "esl"
        assert row.product_ean == product.ean
        # ARCH § Bloc D risques : "UPPER+TRIM(scanned_name)" — no
        # ocr_knowledge normalization for ESL labels (clean format).
        assert row.normalized_label == "NUTELLA 400G"
        assert row.user_id == user.id
        assert row.store_id == store.id

    def test_ocr_ean_match_writes_pnr_row(self, db, store, user, product, monkeypatch):
        """OCR-extracted EAN (pyzbar miss) → ledger row source_type='esl'."""
        # pyzbar misses, OCR carries the EAN.
        monkeypatch.setattr(
            "worker.label_task.read_ean_barcode",
            lambda _img: None,
        )
        ocr: OcrResult = [
            ("Nutella 400g", 0.95),
            ("3,49", 0.92),
            (product.ean, 0.80),
        ]
        scan = _make_label_scan(db, store, user)
        _run(db, scan, ocr)

        rows = self._ledger_rows(db, scan.id)
        assert len(rows) == 1
        row = rows[0]
        assert row.source_type == "esl"
        # match_method='esl' on ledger regardless of pyzbar vs OCR
        # (the pyzbar/OCR distinction lives on scans.match_method).
        assert row.match_method == "esl"
        assert row.product_ean == product.ean

    def test_idempotent_replay_writes_single_row(
        self,
        db,
        store,
        user,
        product,
        monkeypatch,
    ):
        """Running process_label twice on the same scan → 1 ledger row."""
        monkeypatch.setattr(
            "worker.label_task.read_ean_barcode",
            lambda _img: product.ean,
        )
        ocr: OcrResult = [
            ("Nutella 400g", 0.95),
            ("3,49", 0.92),
        ]
        scan = _make_label_scan(db, store, user)
        _run(db, scan, ocr)
        _run(db, scan, ocr)

        rows = self._ledger_rows(db, scan.id)
        assert len(rows) == 1
        assert rows[0].source_type == "esl"

    def test_unmatched_label_writes_no_pnr_row(self, db, store, user, monkeypatch):
        """No EAN found (pyzbar+OCR miss, no consensus) → no ledger row."""
        monkeypatch.setattr(
            "worker.label_task.read_ean_barcode",
            lambda _img: None,
        )
        ocr: OcrResult = [
            ("Produit Inconnu XYZ", 0.90),
            ("5,99", 0.88),
        ]
        scan = _make_label_scan(db, store, user)
        _run(db, scan, ocr)

        assert scan.status == "unmatched"
        rows = self._ledger_rows(db, scan.id)
        assert rows == []

    def test_match_with_store_no_retailer_id_still_writes_row(
        self,
        db,
        store,
        user,
        product,
        monkeypatch,
    ):
        """Store with retailer_id=NULL → row INSERTed with retailer_id=NULL.

        The matcher cascade (Bloc C) excludes such rows via the partial
        index ``WHERE retailer_id IS NOT NULL`` so they don't pollute
        the retailer-wide consensus. We still INSERT for audit traceability
        and so the row turns into a contributing vote retroactively if /
        when the store gets validated and acquires a retailer_id.
        """
        from sqlalchemy import text as sql_text

        # Strip retailer_id from this store to simulate user-suggested
        # store pending admin validation.
        db.execute(
            sql_text("UPDATE stores SET retailer_id = NULL WHERE id = :sid"),
            {"sid": str(store.id)},
        )
        db.flush()
        monkeypatch.setattr(
            "worker.label_task.read_ean_barcode",
            lambda _img: product.ean,
        )
        ocr: OcrResult = [
            ("Nutella 400g", 0.95),
            ("3,49", 0.92),
        ]
        scan = _make_label_scan(db, store, user)
        _run(db, scan, ocr)

        rows = self._ledger_rows(db, scan.id)
        assert len(rows) == 1
        row = rows[0]
        assert row.source_type == "esl"
        assert row.retailer_id is None  # trigger could not denorm

    def test_normalized_label_uppercased_and_trimmed(
        self,
        db,
        store,
        user,
        product,
        monkeypatch,
    ):
        """Mixed-case + leading/trailing whitespace → UPPER+TRIM normalized_label.

        ARCH § Bloc D : ESL labels skip ocr_knowledge normalization and
        use the lighter ``UPPER+TRIM(scanned_name)`` directly — clean
        format from the etiquette so token-level corrections aren't
        needed.
        """
        monkeypatch.setattr(
            "worker.label_task.read_ean_barcode",
            lambda _img: product.ean,
        )
        ocr: OcrResult = [
            ("  Nutella 400g  ", 0.95),
            ("3,49", 0.92),
        ]
        scan = _make_label_scan(db, store, user)
        _run(db, scan, ocr)

        rows = self._ledger_rows(db, scan.id)
        assert len(rows) == 1
        # UPPER + TRIM applied — no internal whitespace squashing
        # (parse_label is responsible for the raw scanned_name shape ;
        # we only normalize for the consensus key).
        assert rows[0].normalized_label == "NUTELLA 400G"
