from __future__ import annotations

import hashlib
import logging
import uuid

from fastapi import UploadFile
from ratis_core.exceptions import Conflict, NotFound, ServiceUnavailable
from ratis_core.settings import load_settings
from ratis_core.uploads import MIME_EXT, validate_image_upload
from ratis_core.utils import assert_owner
from repositories.name_resolution_repository import (
    get_consensus_states_for_scans,
)
from repositories.scan_repository import (
    check_photo_hash_receipt,
    create_receipt,
    get_label_group_items,
    get_receipt,
    get_receipt_by_idempotency_key,
    get_receipt_items,
    get_receipt_scan_summary,
)
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from storage import R2UploadError, delete_receipt_image, upload_receipt_image
from tasks import enqueue_ocr_job

logger = logging.getLogger(__name__)

# Fail fast at startup — missing keys produce a clear error rather than a cryptic KeyError later
try:
    _MAX_SIZE: int = load_settings()["ocr"]["max_file_size_mb"] * 1024 * 1024
except KeyError as exc:
    raise KeyError(
        f"Missing key {exc} in settings — expected: ocr.max_file_size_mb "
        "(check app_settings table or ratis_settings.json)"
    ) from exc


def submit_receipt(
    db: Session,
    *,
    image: UploadFile,
    user_id: uuid.UUID,
    idempotency_key: uuid.UUID | None = None,
) -> uuid.UUID:
    """Upload a receipt photo. Store is determined later by the OCR worker
    (barcode detection per DA-18) — the frontend no longer sends store_id.

    ``idempotency_key`` is a client-generated UUID, stable across upload
    retries. A client killed after a successful POST but before recording
    success replays its queue on restart : if a receipt already exists for
    this ``(user_id, idempotency_key)`` we return that receipt's id instead
    of creating a duplicate. ``None`` keeps the legacy non-idempotent path.
    """
    # Idempotent replay short-circuit — checked before validation / R2 so a
    # retried upload costs nothing. The partial unique index
    # ``uq_receipts_user_idempotency_key`` is the race-safe backstop below.
    if idempotency_key is not None:
        existing = get_receipt_by_idempotency_key(db, user_id=user_id, idempotency_key=idempotency_key)
        if existing is not None:
            return existing.id

    content, mime = validate_image_upload(image, allow_pdf=True, max_size_bytes=_MAX_SIZE)

    # Hash-first: check before any R2 upload — duplicate rejected with zero network cost
    photo_hash = hashlib.sha256(content).hexdigest()
    if check_photo_hash_receipt(db, photo_hash):
        raise Conflict("duplicate_photo")

    receipt_id = uuid.uuid4()
    ext = MIME_EXT[mime]
    key = f"{receipt_id}.{ext}"

    # DB flush before R2 upload — if R2 fails, transaction rolls back, hash is freed.
    # The check-first above is racy : two concurrent uploads of the same photo
    # both pass the SELECT, then one loses the INSERT race on the partial unique
    # index ``receipts_photo_hash_unique``. Catch that IntegrityError and surface
    # the same 409 ``duplicate_photo`` as the check-first path (no 500).
    # A concurrent replay of the same idempotency_key loses the race on
    # ``uq_receipts_user_idempotency_key`` instead — re-read and return the
    # winner's receipt so both callers see the same idempotent result.
    try:
        create_receipt(
            db,
            receipt_id=receipt_id,
            user_id=user_id,
            image_r2_key=key,
            photo_hash=photo_hash,
            idempotency_key=idempotency_key,
        )
    except IntegrityError as exc:
        orig = str(exc.orig)
        if "uq_receipts_user_idempotency_key" in orig and idempotency_key is not None:
            db.rollback()
            existing = get_receipt_by_idempotency_key(db, user_id=user_id, idempotency_key=idempotency_key)
            if existing is not None:
                return existing.id
            raise
        if "receipts_photo_hash_unique" in orig:
            db.rollback()
            raise Conflict("duplicate_photo") from exc
        raise

    try:
        upload_receipt_image(content, key, content_type=mime)
    except R2UploadError as exc:
        raise ServiceUnavailable("storage_unavailable") from exc

    try:
        enqueue_ocr_job(receipt_id)
    except Exception as exc:
        delete_receipt_image(key)
        raise ServiceUnavailable("queue_unavailable") from exc
    try:
        db.commit()
    except Exception as exc:
        logger.exception("DB commit failed for receipt %s — cleaning R2 orphan", receipt_id)
        try:
            delete_receipt_image(key)
        except Exception:
            logger.exception("R2 cleanup also failed for key %s — manual cleanup required", key)
        raise ServiceUnavailable("internal_error") from exc
    return receipt_id


def get_receipt_status(db: Session, receipt_id: uuid.UUID, user_id: uuid.UUID) -> dict:
    receipt = get_receipt(db, receipt_id)
    if receipt is None:
        raise NotFound("receipt_not_found")

    assert_owner(receipt, user_id)

    summary = get_receipt_scan_summary(db, receipt_id)

    if not summary:
        # No scans created yet. Distinguish between :
        #   - worker still processing (pending_items NULL → no work done yet)
        #   - worker processed but couldn't match a store (pending_items NOT NULL
        #     → items extracted but stuck on store_status='unknown' awaiting
        #     a future store-confirmation flow — see ARCH_user_suggested_stores)
        # Without this check, the second case stays at 'pending' indefinitely
        # and the UI shows "Analyse en cours" forever (alpha bug 2026-04-28).
        if receipt.pending_items:
            status = "unmatched_store"
        else:
            status = "pending"
    elif "pending" in summary:
        status = "processing"
    # Pipeline_v3 (deployed 2026-04-30) renamed v2 'accepted'→'matched' and
    # 'unmatched'→'unresolved'. Accept both vocabularies so the receipt
    # status endpoint stays correct during the transition window.
    elif "accepted" in summary or "unmatched" in summary or "matched" in summary or "unresolved" in summary:
        status = "done"
    elif "rejected" in summary:
        status = "rejected"
    else:
        status = "failed"

    matched = summary.get("accepted", 0) + summary.get("matched", 0)
    unmatched = summary.get("unmatched", 0) + summary.get("unresolved", 0)
    total = receipt.total_amount

    item_rows = get_receipt_items(db, receipt_id=receipt_id)
    # NRC bloc E : surface the live consensus state per scan so the FE
    # can render a status badge. ``None`` ⇔ no contributing ledger row
    # (UNRESOLVED) ⇒ no badge ; concrete states map to coloured badges
    # (see ratis_client/components/scan/scan-history-item-row.tsx).
    consensus_by_scan = get_consensus_states_for_scans(db, scan_ids=[r["scan_id"] for r in item_rows])
    items = [_serialize_receipt_item(r, consensus_by_scan.get(r["scan_id"])) for r in item_rows]

    payload: dict = {
        "status": status,
        "matched": matched,
        "unmatched": unmatched,
        "total_amount": total,
        "store_status": receipt.store_status,
        "pending_items_count": len(receipt.pending_items) if receipt.pending_items else 0,
        "items": items,
    }

    # PR-B : surface the OCR candidate when the store is still unresolved or
    # pending validation, so the frontend can render the confirmation modal.
    if receipt.store_status in ("unknown", "pending"):
        # Local import : avoids a circular when scan_service is imported by
        # store_confirmation_service-adjacent code at startup.
        from services.store_confirmation_service import (
            get_candidate_for_receipt,
            serialize_candidate_info,
        )

        candidate = get_candidate_for_receipt(db, receipt_id)
        if candidate is not None:
            info = serialize_candidate_info(candidate)
            if info is not None:
                payload["store_candidate_info"] = info

    return payload


def _serialize_receipt_item(row: dict, consensus_state=None) -> dict:
    quantity = row["quantity"]
    # Numeric(10,3) → Decimal. Serialize as float for JSON (quantity is
    # never money; money is int-cents elsewhere).
    return {
        "scan_id": str(row["scan_id"]),
        "scanned_name": row["scanned_name"],
        "product_name": row["product_name"],
        # display_name composed by ratis_core.products.pick_display_name from
        # the OFF multi-field columns. The FE prefers display_name and falls
        # back to product_name for backward compatibility (scan-history-item-row).
        "display_name": row.get("display_name"),
        "product_ean": row["product_ean"],
        "quantity": float(quantity) if quantity is not None else None,
        "price_cents": row["price_cents"],
        "status": row["status"],
        "match_method": row["match_method"],
        # v3 unresolved rows carry a snake_case reason translated by the
        # frontend (formatRejectedReason). Legacy v2 unmatched rows hold NULL.
        "rejected_reason": row.get("rejected_reason"),
        # NRC bloc E : ConsensusState as its StrEnum value (e.g. "verified",
        # "pending", "controverse", "unverified"). ``None`` when the scan
        # has no contributing ledger row (UNRESOLVED) — the FE renders no
        # badge in that case.
        "consensus_state": (consensus_state.value if consensus_state is not None else None),
    }


# ============================================================
# LABEL GROUP — detail view of a (store, date) group
# ============================================================


def get_label_group(
    db: Session,
    *,
    user_id: uuid.UUID,
    store_id: uuid.UUID,
    day,
) -> dict:
    """Return accepted label scans for (user, store, date).

    Raises ``NotFound('group_not_found')`` if zero accepted scans match.
    """
    rows = get_label_group_items(db, user_id=user_id, store_id=store_id, day=day)
    if not rows:
        raise NotFound("group_not_found")
    # NRC bloc E : surface the live consensus state per scan (see
    # ``get_receipt_status`` for the full rationale).
    consensus_by_scan = get_consensus_states_for_scans(db, scan_ids=[r["scan_id"] for r in rows])
    items = [
        {
            "scan_id": str(r["scan_id"]),
            "product_name": r["product_name"],
            "product_ean": r["product_ean"],
            "price_cents": r["price_cents"],
            "match_method": r["match_method"],
            "scanned_at": r["scanned_at"].isoformat() if r["scanned_at"] else None,
            "consensus_state": (consensus_by_scan[r["scan_id"]].value if r["scan_id"] in consensus_by_scan else None),
        }
        for r in rows
    ]
    return {"items": items}
