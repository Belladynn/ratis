from __future__ import annotations

import io
import logging
import os
import uuid
from contextlib import contextmanager
from decimal import Decimal
from typing import TYPE_CHECKING

import cv2
import numpy as np
from celery_app import celery_app
from ratis_core.models.product import Product
from ratis_core.products import claim_first_discovery
from repositories.label_repository import get_label_scan, update_label_scan_result
from repositories.name_resolution_writes import record_resolution
from repositories.scan_repository import upsert_price_consensus
from sqlalchemy import select as sa_select
from storage import get_s3_client

from worker.ocr.arbitrator import arbitrate
from worker.ocr.barcode_reader import read_ean_barcode
from worker.ocr.exceptions import InvalidImageError
from worker.ocr.image_guard import assert_image_dimensions_ok
from worker.ocr.label_parser import parse_label
from worker.ocr.normalize import normalize_text
from worker.ocr.ocr_engine import OcrEngine, PaddleOcrEngine
from worker.ocr.preprocessor import (
    assess_quality,
    pass_binarized,
    pass_clahe,
    pass_corrected,
    pass_inverted,
)
from worker.ocr.type_detector import detect_content_type
from worker.ocr.types import OcrResult

if TYPE_CHECKING:
    from sqlalchemy.engine import Engine

logger = logging.getLogger(__name__)

_ocr_engine: OcrEngine | None = None
_db_engine: "Engine" | None = None
_session_factory = None


def _get_ocr_engine() -> OcrEngine:
    global _ocr_engine
    if _ocr_engine is None:
        _ocr_engine = PaddleOcrEngine()
    return _ocr_engine


def _get_db_engine() -> "Engine":
    global _db_engine
    if _db_engine is None:
        from ratis_core.database import make_engine

        _db_engine = make_engine(os.environ["DATABASE_URL"])
    return _db_engine


def _get_session_factory():
    global _session_factory
    if _session_factory is None:
        from sqlalchemy.orm import sessionmaker

        _session_factory = sessionmaker(bind=_get_db_engine())
    return _session_factory


def _get_s3_client():
    # Delegates to storage.get_s3_client so virtual-hosted addressing + SigV4
    # are used consistently across the service (required by R2 for presigned
    # URLs — see storage.py docstring).
    #
    # 2026-04-27 — was a lazy import for boto3 startup-cost reasons, but Celery
    # workers don't keep `/app/webservices/ratis_product_analyser` in sys.path
    # after bootstrap (see KP-NN). Lazy `from storage import ...` therefore
    # raises ModuleNotFoundError at runtime — every receipt was failing OCR
    # in prod since #137 deployed. Use the module-level import below instead.
    return get_s3_client()


def _download_raw(key: str, s3_client=None) -> bytes:
    client = s3_client or _get_s3_client()
    buf = io.BytesIO()
    client.download_fileobj(os.environ["R2_BUCKET_NAME"], key, buf)
    return buf.getvalue()


def _decode_image(raw: bytes) -> np.ndarray:
    # Decompression-bomb guard — reject oversized rasters before the
    # unbounded cv2.imdecode (the file-size cap does not bound dimensions).
    assert_image_dimensions_ok(raw)
    arr = np.frombuffer(raw, dtype=np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if img is None:
        raise InvalidImageError("corrupted_or_spoofed_image")
    return img


def _run_ocr_pipeline(image: np.ndarray, engine: OcrEngine) -> OcrResult | None:
    """Same 3-pass pipeline as receipt_task — returns None if no convergence."""
    metrics = assess_quality(image)
    if metrics.is_blurry:
        logger.warning("Label image rejected: blur_score=%.1f", metrics.blur_score)
        return None

    corrected = pass_corrected(image)
    # Arbitration : 3 convergence passes — `arbitrate` is fixed-arity
    # (3-pass majority voting). The `inverted` fallback runs below when
    # convergence fails.
    results = [
        engine.recognize(corrected),
        engine.recognize(pass_clahe(corrected)),
        engine.recognize(pass_binarized(corrected)),
    ]
    # Guard: empty results from all passes → no convergence possible
    if all(not r for r in results):
        return None

    winner = arbitrate(*results)
    if winner is not None:
        return winner

    p_inverted = engine.recognize(pass_inverted(corrected))
    if not p_inverted:
        return None
    for candidate in results:
        winner = arbitrate(candidate, p_inverted, p_inverted)
        if winner is not None:
            return winner

    return None


@celery_app.task(name="worker.label_task.process_label", bind=True, max_retries=3)
def process_label(
    self,
    scan_id_str: str,
    hint: str = "label",
    *,
    _engine: OcrEngine | None = None,
    _s3=None,
    _db=None,
) -> None:
    """
    OCR pipeline for a single electronic label scan.

    hint: "label" | "receipt" — used as tiebreaker in type detection.
    _engine, _s3, _db are injectable for tests.
    """
    try:
        scan_id = uuid.UUID(scan_id_str)
    except ValueError:
        logger.error("Invalid scan_id: %r", scan_id_str)
        return

    @contextmanager
    def _session():
        if _db is not None:
            yield _db
        else:
            with _get_session_factory()() as session:
                yield session

    with _session() as db:
        scan = get_label_scan(db, scan_id)
        if scan is None:
            logger.error("Label scan %s not found", scan_id)
            return

        if not scan.label_r2_key:
            logger.error("Label scan %s has no label_r2_key", scan_id)
            return

        try:
            raw = _download_raw(scan.label_r2_key, s3_client=_s3)
        except Exception as exc:
            logger.exception("Failed to download label image for scan %s", scan_id)
            if self.request.retries >= self.max_retries:
                logger.error("Max retries exhausted for label scan %s", scan_id)
                scan.photo_hash = None
                update_label_scan_result(
                    db,
                    scan,
                    scanned_name="",
                    price=0,
                    product_ean=None,
                    match_method=None,
                    status="failed",
                )
                try:
                    db.commit()
                except Exception:
                    logger.exception("Could not persist failed status for label scan %s", scan_id)
                return
            raise self.retry(exc=exc, countdown=60)

        # ── OCR ──────────────────────────────────────────────────────────────
        try:
            image = _decode_image(raw)
        except InvalidImageError as exc:
            logger.warning("Label image invalid for scan %s: %s", scan_id, exc)
            update_label_scan_result(
                db,
                scan,
                scanned_name="",
                price=0,
                product_ean=None,
                match_method=None,
                status="rejected",
                rejected_reason="invalid_image",
            )
            db.commit()
            return

        # ── EAN barcode reading (before OCR) ────────────────────────────────
        ean_from_barcode: str | None = None
        try:
            ean_from_barcode = read_ean_barcode(image)
        except Exception:
            logger.warning("EAN barcode reading failed for label scan %s — continuing", scan_id)

        engine = _engine or _get_ocr_engine()
        ocr_result = _run_ocr_pipeline(image, engine)

        if ocr_result is None:
            update_label_scan_result(
                db,
                scan,
                scanned_name="",
                price=0,
                product_ean=None,
                match_method=None,
                status="rejected",
                rejected_reason="ocr_no_result",
            )
            db.commit()
            return

        # ── type detection ────────────────────────────────────────────────────
        detected = detect_content_type(ocr_result, hint=hint)
        if detected == "receipt":
            logger.info(
                "Label scan %s appears to be a receipt (hint=%s) — best-effort single item extraction",
                scan_id,
                hint,
            )

        # ── parse as label regardless (best-effort, don't punish the user) ───
        label_item = parse_label(ocr_result)

        if label_item is None:
            update_label_scan_result(
                db,
                scan,
                scanned_name="",
                price=0,
                product_ean=None,
                match_method=None,
                status="rejected",
                rejected_reason="ocr_no_result",
            )
            db.commit()
            return

        # ── product matching ──────────────────────────────────────────────────
        # Priority: 1) EAN from pyzbar barcode  2) EAN from OCR text  3) fuzzy name
        product_ean: str | None = None
        match_method: str | None = None

        # 1) pyzbar EAN (most reliable — direct barcode read)
        if ean_from_barcode:
            exists = db.scalar(sa_select(Product.ean).where(Product.ean == ean_from_barcode))
            if exists:
                product_ean = ean_from_barcode
                match_method = "barcode_ean"

        # 2) OCR-extracted EAN (label_parser reads EAN from OCR text)
        if product_ean is None and label_item.product_ean:
            exists = db.scalar(sa_select(Product.ean).where(Product.ean == label_item.product_ean))
            if exists:
                product_ean = label_item.product_ean
                match_method = "manual"

        # 3) Consensus-only resolution (refonte 2026-05-02). The legacy
        # fuzzy product-name matcher is gone ; for label scans we run
        # the OCR cleanup, then probe the per-store ledger for a
        # VERIFIED consensus (exact + fuzzy fallback). When the scan has
        # no store_id the cascade short-circuits to ``unmatched``.
        if product_ean is None and scan.store_id and label_item.scanned_name:
            from repositories.consensus_state import ConsensusState
            from repositories.name_resolution_repository import (
                find_fuzzy_verified_consensus_by_store,
                get_consensus_for_label_by_store,
            )

            # Bloc B (cross-retailer) ships retailer-keyed canonicals ;
            # this path uses the transitional ``*_by_store`` wrappers
            # until Bloc D migrates the label matcher to retailer-keyed.
            cleaned_label = normalize_text(db, label_item.scanned_name)
            consensus = get_consensus_for_label_by_store(db, store_id=scan.store_id, normalized_label=cleaned_label)
            if consensus is not None and consensus.state == ConsensusState.VERIFIED:
                product_ean = consensus.ean
                match_method = "consensus_match"
            else:
                fuzzy = find_fuzzy_verified_consensus_by_store(db, store_id=scan.store_id, cleaned_label=cleaned_label)
                if fuzzy is not None:
                    product_ean = fuzzy.ean
                    match_method = "consensus_match"

        status = "accepted" if product_ean else "unmatched"
        # Flag mismatch only on unmatched — an accepted scan stays clean even if hint was wrong
        if detected == "receipt" and hint == "label":
            if status != "accepted":
                rejected_reason: str | None = "hint_mismatch:likely_receipt"
            else:
                logger.info("Label scan %s: receipt-like image but product found — hint_mismatch not stored", scan_id)
                rejected_reason = None
        else:
            rejected_reason = None

        price_cents = round(Decimal(str(label_item.price)) * 100)
        update_label_scan_result(
            db,
            scan,
            scanned_name=label_item.scanned_name,
            price=price_cents,
            product_ean=product_ean,
            match_method=match_method,
            status=status,
            rejected_reason=rejected_reason,
        )

        if product_ean:
            upsert_price_consensus(db, scan)
            # V1.1 first-discovery attribution (KP-75) — label/ESL scan
            # path. The helper is idempotent + filters banned/deleted users
            # itself, so safe to call unconditionally on every accepted
            # label match.
            if scan.user_id:
                claim_first_discovery(db, product_ean, scan.user_id)
            # ── Bloc D NRC : ESL → ledger write ──────────────────────────
            # Every successful ESL match (pyzbar OR OCR EAN) feeds the
            # cross-retailer consensus ledger as ``source_type='esl'`` /
            # ``match_method='esl'``. The pyzbar-vs-OCR distinction
            # already lives on ``scans.match_method`` (``barcode_ean`` vs
            # ``manual``) — the ledger row carries a single canonical
            # ``'esl'`` method per ARCH § Bloc D.
            #
            # Normalisation : ``UPPER+TRIM(scanned_name)`` directly, no
            # ocr_knowledge correction (ESL labels are clean by design —
            # cf. ARCH risques § Bloc D).
            #
            # Skip when there is no scanned_name (defensive — every
            # accepted scan has one in practice). When ``scan.store_id``
            # is NULL the trigger leaves ``retailer_id`` NULL ; the
            # matcher cascade ignores those rows but the audit row is
            # preserved (consistent with ARCH § "Bloc D — risques").
            #
            # Idempotent on UNIQUE (scan_id, source_type, normalized_label) —
            # Celery task replays never duplicate ledger rows.
            if scan.store_id and label_item.scanned_name:
                esl_normalized_label = label_item.scanned_name.strip().upper()
                if esl_normalized_label:
                    record_resolution(
                        db,
                        scan_id=scan.id,
                        store_id=scan.store_id,
                        normalized_label=esl_normalized_label,
                        product_ean=product_ean,
                        user_id=scan.user_id,
                        match_method="esl",
                        source_type="esl",
                    )

        try:
            db.commit()
        except Exception as exc:
            logger.exception("DB commit failed for label scan %s", scan_id)
            if self.request.retries >= self.max_retries:
                logger.error("Max retries exhausted on commit for label scan %s", scan_id)
                try:
                    db.rollback()
                    scan.photo_hash = None
                    update_label_scan_result(
                        db,
                        scan,
                        scanned_name="",
                        price=0,
                        product_ean=None,
                        match_method=None,
                        status="failed",
                    )
                    db.commit()
                except Exception:
                    logger.exception("Could not persist failed status for label scan %s", scan_id)
                return
            raise self.retry(exc=exc, countdown=30)

        logger.info("Label scan %s processed successfully (status=%s)", scan_id, status)
