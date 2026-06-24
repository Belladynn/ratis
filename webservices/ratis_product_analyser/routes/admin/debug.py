"""Admin routes for ratis_product_analyser (PR #126, extended in PR #132).

Endpoints :

    GET /api/v1/admin/scans/{scan_id}/debug
        Legacy lookup — by scan_id. Falls back to the receipt's debug row
        when no debug row is keyed off the scan directly (we only persist
        one debug row per receipt-task run).

    GET /api/v1/admin/receipts/{receipt_id}/debug
        New in PR #132. Anchored on receipt_id, which is always available
        (even when the pipeline produced zero scans because store
        detection failed). Returns the same payload shape as the scan_id
        endpoint, with ``scan_id`` set to NULL when no scan was created.

Both endpoints return per-pass presigned URLs for the OCR-processed
images (``processed_images``) when scan_debug rows have the new
``processed_images_r2_keys`` JSONB populated. For legacy rows written
before PR #132 we fall back to the single ``processed_image_r2_key``
column under the synthetic key ``"corrected"``.

Auth : ADMIN_API_KEY via Authorization: Bearer <key>. Mounted only when
ADMIN_API_KEY is set at startup (see main.py lifespan) — when missing,
the routes are absent (404), not exposed without auth.
"""

from __future__ import annotations

import os
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from ratis_core.database import get_db
from ratis_core.deps import verify_admin_key
from sqlalchemy import text
from sqlalchemy.orm import Session
from storage import get_s3_client

router = APIRouter()


_PRESIGN_TTL_SECONDS = 15 * 60


def _get_s3_client():
    """Return the shared R2-configured S3 client (storage.get_s3_client).

    Indirection kept so tests can monkeypatch ``routes.admin._get_s3_client``
    without leaking into the worker upload path. Production code MUST
    rely on ``storage.get_s3_client`` so the virtual-hosted addressing
    style (required by R2 for presigned URLs to return 200 instead of
    401) stays consistent across all call sites.
    """
    return get_s3_client()


def _presign_get(key: str, *, s3_client=None) -> str | None:
    """Generate a presigned GET URL for an R2 object.

    Returns None if presigning fails (object missing / R2 unreachable / etc.)
    so the endpoint can degrade gracefully rather than 500."""
    client = s3_client or _get_s3_client()
    bucket = os.environ["R2_BUCKET_NAME"]
    try:
        return client.generate_presigned_url(
            "get_object",
            Params={"Bucket": bucket, "Key": key},
            ExpiresIn=_PRESIGN_TTL_SECONDS,
        )
    except Exception:
        return None


def _build_processed_images(
    debug_row,
    *,
    s3_client,
) -> tuple[dict[str, str | None], str | None]:
    """Return ``(processed_images, fallback_url)``.

    ``processed_images`` is a dict mapping each OCR pass name
    ("corrected" / "clahe" / "binarized" / "inverted") to either a
    presigned URL or None (pass ran but presign / object failed).

    Reads the new JSONB ``processed_images_r2_keys`` column when present
    ; falls back to the legacy single ``processed_image_r2_key`` column
    under the "corrected" key for back-compat with rows written before
    PR #132. ``fallback_url`` mirrors the legacy ``processed_image_url``
    field for back-compat with old admin clients.
    """
    keys_map: dict[str, str | None] = {}
    raw_keys = getattr(debug_row, "processed_images_r2_keys", None)
    if raw_keys:
        # JSONB → already a dict via psycopg adapter. Defensive cast.
        if isinstance(raw_keys, dict):
            keys_map = raw_keys
    elif getattr(debug_row, "processed_image_r2_key", None):
        # Legacy row — one image only, treat as the corrected pass.
        keys_map = {"corrected": debug_row.processed_image_r2_key}

    processed_images: dict[str, str | None] = {}
    fallback_url: str | None = None
    for pass_name, key in keys_map.items():
        if not key:
            processed_images[pass_name] = None
            continue
        url = _presign_get(key, s3_client=s3_client)
        processed_images[pass_name] = url
        if fallback_url is None and url is not None:
            fallback_url = url
    return processed_images, fallback_url


def _build_payload(
    db: Session,
    *,
    debug_row,
    receipt_id: uuid.UUID | None,
    scan_id: uuid.UUID | None,
    scan_status: str | None,
    scanned_at,
    receipt_image_r2_key: str | None,
    receipt_image_deleted_at,
) -> dict[str, Any]:
    """Shared payload builder for both lookup endpoints.

    Centralizes presigning, status flagging, and sibling-scan listing so
    the two routes stay byte-identical except for their lookup keys.
    """
    s3_client = _get_s3_client()

    # Raw uploaded image (the receipt photo) — same logic as PR #126.
    raw_image_url: str | None = None
    raw_image_status: str | None = None
    if receipt_image_r2_key and receipt_image_deleted_at is None:
        raw_image_url = _presign_get(receipt_image_r2_key, s3_client=s3_client)
        if raw_image_url is None:
            raw_image_status = "presign_failed"
    elif receipt_image_deleted_at is not None:
        raw_image_status = "purged"
    else:
        raw_image_status = "not_stored"

    # Per-pass processed images (PR #132).
    processed_images, fallback_processed_url = _build_processed_images(debug_row, s3_client=s3_client)

    processed_image_url: str | None = fallback_processed_url
    processed_image_status: str | None = None
    if not processed_images:
        processed_image_status = "not_stored"
    elif fallback_processed_url is None:
        # Map exists but every presign failed.
        processed_image_status = "presign_failed_or_purged"

    scan_items: list[dict[str, Any]] = []
    if receipt_id is not None:
        items = db.execute(
            text("""
                SELECT id, scanned_name, price, quantity, tva_amount,
                       product_ean, status, match_method, rejected_reason
                FROM scans
                WHERE receipt_id = :receipt_id
                ORDER BY scanned_at
            """),
            {"receipt_id": str(receipt_id)},
        ).fetchall()
        scan_items = [
            {
                "id": str(it.id),
                "scanned_name": it.scanned_name,
                "price": it.price,
                "quantity": float(it.quantity) if it.quantity is not None else None,
                "tva_amount": it.tva_amount,
                "product_ean": it.product_ean,
                "status": it.status,
                "match_method": it.match_method,
                "rejected_reason": it.rejected_reason,
            }
            for it in items
        ]

    return {
        "scan_id": str(scan_id) if scan_id is not None else None,
        "receipt_id": str(receipt_id) if receipt_id is not None else None,
        "scanned_at": scanned_at.isoformat() if scanned_at else None,
        "scan_status": scan_status,
        "raw_image_url": raw_image_url,
        "raw_image_status": raw_image_status,
        # Legacy single-image fields — kept for back-compat with old
        # admin clients. New clients should read ``processed_images``
        # (per-pass) instead.
        "processed_image_url": processed_image_url,
        "processed_image_status": processed_image_status,
        # PR #132 — per-pass URLs. Keys are a subset of
        # {"corrected", "clahe", "binarized", "inverted"}.
        "processed_images": processed_images,
        "rich_blocks": debug_row.rich_blocks,
        "llm_output": debug_row.llm_output,
        # Phase 2e (ARCH OCR↔LLM Bridge v2) :
        # - ``final_receipt_data`` : what was actually used for the
        #   scan (= LLM-derived in most cases, legacy fallback
        #   otherwise). Was previously misnamed
        #   ``legacy_receipt_data``.
        # - ``legacy_parser_output`` : the parallel ``parse_receipt()``
        #   run result, kept for true side-by-side comparison in the
        #   debug viewer.
        "final_receipt_data": debug_row.final_receipt_data,
        "legacy_parser_output": debug_row.legacy_parser_output,
        # Back-compat alias for old admin clients : route the new
        # ``final_receipt_data`` to the old key. Will be removed once
        # all consumers migrate to the new field.
        "legacy_receipt_data": debug_row.final_receipt_data,
        "ocr_passes_summary": debug_row.ocr_passes_summary,
        "scan_items": scan_items,
    }


@router.get(
    "/admin/scans/{scan_id}/debug",
    dependencies=[Depends(verify_admin_key)],
)
def get_scan_debug(
    scan_id: uuid.UUID,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Return debug payload for a scan (alpha instrumentation).

    Lookup priority :
      1. scan_debug row keyed off this scan_id (PR #132 row schema, but
         scan_id may match for any receipt that did produce scans).
      2. scan_debug row keyed off the same receipt_id as this scan.

    Errors :
        404 — scan_id not found in scans
        404 + detail="no_debug_data_available" — scan exists but no
              scan_debug row was found.
    """
    row = db.execute(
        text("""
            SELECT
                s.id          AS scan_id,
                s.scanned_at  AS scanned_at,
                s.status      AS scan_status,
                s.receipt_id  AS receipt_id,
                r.image_r2_key      AS receipt_image_r2_key,
                r.image_deleted_at  AS receipt_image_deleted_at
            FROM scans s
            LEFT JOIN receipts r ON r.id = s.receipt_id
            WHERE s.id = :scan_id
        """),
        {"scan_id": str(scan_id)},
    ).first()

    if row is None:
        raise HTTPException(status_code=404, detail="scan_not_found")

    # Prefer a row keyed off this exact scan_id ; otherwise fall back to
    # any row attached to the same receipt (we only persist one per
    # receipt-task run). ORDER BY created_at DESC so re-runs surface the
    # latest attempt.
    debug = db.execute(
        text("SELECT * FROM scan_debug WHERE scan_id = :scan_id ORDER BY created_at DESC LIMIT 1"),
        {"scan_id": str(scan_id)},
    ).first()

    if debug is None and row.receipt_id is not None:
        debug = db.execute(
            text("""
                SELECT *
                FROM scan_debug
                WHERE receipt_id = :receipt_id
                ORDER BY created_at DESC
                LIMIT 1
            """),
            {"receipt_id": str(row.receipt_id)},
        ).first()

    if debug is None:
        raise HTTPException(status_code=404, detail="no_debug_data_available")

    return _build_payload(
        db,
        debug_row=debug,
        receipt_id=row.receipt_id,
        scan_id=row.scan_id,
        scan_status=row.scan_status,
        scanned_at=row.scanned_at,
        receipt_image_r2_key=row.receipt_image_r2_key,
        receipt_image_deleted_at=row.receipt_image_deleted_at,
    )


@router.get(
    "/admin/receipts/{receipt_id}/debug",
    dependencies=[Depends(verify_admin_key)],
)
def get_receipt_debug(
    receipt_id: uuid.UUID,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Return debug payload for a receipt (PR #132).

    Anchored on receipt_id so failure paths that produced zero scans
    (e.g. store detection failed → items in receipts.pending_items) are
    still inspectable.

    Errors :
        404 — receipt_id not found
        404 + detail="no_debug_data_available" — receipt exists but no
              scan_debug row was found (flag was off when processed).
    """
    receipt_row = db.execute(
        text("""
            SELECT id, image_r2_key, image_deleted_at
            FROM receipts
            WHERE id = :receipt_id
        """),
        {"receipt_id": str(receipt_id)},
    ).first()

    if receipt_row is None:
        raise HTTPException(status_code=404, detail="receipt_not_found")

    debug = db.execute(
        text("""
            SELECT *
            FROM scan_debug
            WHERE receipt_id = :receipt_id
            ORDER BY created_at DESC
            LIMIT 1
        """),
        {"receipt_id": str(receipt_id)},
    ).first()

    if debug is None:
        raise HTTPException(status_code=404, detail="no_debug_data_available")

    # If a scan was attached to this debug row, surface its status +
    # scanned_at so the admin payload mirrors the scan-anchored shape.
    scan_status: str | None = None
    scanned_at = None
    debug_scan_id = debug.scan_id
    if debug_scan_id is not None:
        scan_meta = db.execute(
            text("SELECT status, scanned_at FROM scans WHERE id = :scan_id"),
            {"scan_id": str(debug_scan_id)},
        ).first()
        if scan_meta is not None:
            scan_status = scan_meta.status
            scanned_at = scan_meta.scanned_at

    return _build_payload(
        db,
        debug_row=debug,
        receipt_id=receipt_row.id,
        scan_id=debug_scan_id,
        scan_status=scan_status,
        scanned_at=scanned_at,
        receipt_image_r2_key=receipt_row.image_r2_key,
        receipt_image_deleted_at=receipt_row.image_deleted_at,
    )
