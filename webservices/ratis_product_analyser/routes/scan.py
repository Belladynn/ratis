from __future__ import annotations

import uuid
from typing import Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import JSONResponse
from limiter import limiter
from pydantic import BaseModel, Field
from ratis_core.auth import get_http_current_user
from ratis_core.database import get_db
from ratis_core.deps import get_bearer_token
from repositories.scan_repository import check_photo_hash_receipt, check_photo_hash_scan
from services.barcode_service import scan_barcode
from services.label_service import get_label_session_status, submit_label, submit_label_batch
from services.rescan_service import (
    RescanCapExceeded,
    rescan_receipt,
)
from services.scan_history_service import list_scan_history
from services.scan_service import (
    get_label_group,
    get_receipt_status,
    submit_receipt,
)
from services.store_confirmation_service import confirm_store_from_ocr
from sqlalchemy.orm import Session

router = APIRouter()


class BarcodeScanRequest(BaseModel):
    ean: str = Field(..., min_length=8, max_length=13, pattern=r"^\d+$")
    scan_id: uuid.UUID


@router.post("/receipt", status_code=202)
@limiter.limit("3/minute")
def post_scan_receipt(
    request: Request,
    image: UploadFile = File(...),
    idempotency_key: uuid.UUID | None = Form(None),
    token: str = Depends(get_bearer_token),
    db: Session = Depends(get_db),
):
    """Upload a receipt photo for OCR processing.

    ``idempotency_key`` is an optional client-generated UUID, stable across
    upload retries. When a client replays its upload queue (e.g. killed
    after the POST succeeded server-side but before recording success),
    sending the same key returns the original ``receipt_id`` with 202
    instead of creating a duplicate receipt. See ``services/scan_service``.
    """
    user = get_http_current_user(db, token)
    receipt_id = submit_receipt(db, image=image, user_id=user.id, idempotency_key=idempotency_key)
    return JSONResponse(status_code=202, content={"receipt_id": str(receipt_id)})


@router.get("/receipt/{receipt_id}")
def get_scan_receipt(
    receipt_id: uuid.UUID,
    token: str = Depends(get_bearer_token),
    db: Session = Depends(get_db),
):
    user = get_http_current_user(db, token)
    return get_receipt_status(db, receipt_id, user_id=user.id)


@router.post("/receipt/{receipt_id}/rescan", status_code=202)
@limiter.limit("3/minute")
def post_scan_receipt_rescan(
    request: Request,
    receipt_id: uuid.UUID,
    token: str = Depends(get_bearer_token),
    db: Session = Depends(get_db),
):
    """Re-trigger the OCR pipeline on an existing receipt.

    Anti-fraud PR5 — user-facing rescue path when the first OCR pass
    produced a bad parse (wrong store, missing items, OCR garbage).
    Strict guards (see ``services/rescan_service.py``) :

    - 404 ``receipt_not_found`` — receipt absent or not owned
    - 409 ``receipt_already_accepted`` — validated, use admin path
    - 410 ``receipt_image_expired`` — R2 48h window elapsed
    - 429 ``rescan_cap_exceeded`` — ``rescan_max_attempts`` (3) reached
    - 503 ``queue_unavailable`` — Celery dispatch failed

    The endpoint is rate-limited to 3/min/user (slowapi) on top of
    the per-receipt cap so a misbehaving client can't burn through
    the cap of one receipt in <1s. Returns 202 with the new
    ``rescan_attempts`` counter so the client UI can disable the
    button when the cap is reached.

    See ARCH_receipt_pipeline.md § "Implem sprint suggéré" PR5.
    """
    user = get_http_current_user(db, token)
    try:
        result = rescan_receipt(db, receipt_id=receipt_id, user_id=user.id)
    except RescanCapExceeded as exc:
        # No dedicated 429 domain exception in ratis_core.exceptions
        # yet — see rescan_service.RescanCapExceeded docstring. Inline
        # HTTPException is the canonical route-only path (R12).
        raise HTTPException(
            status_code=429,
            detail="rescan_cap_exceeded",
            headers={
                "X-Rescan-Attempts": str(exc.attempts),
                "X-Rescan-Cap": str(exc.cap),
            },
        )
    db.commit()  # MANDATORY — increment must persist for the cap to bite
    return JSONResponse(status_code=202, content=result)


@router.post("/receipt/{receipt_id}/confirm-store", status_code=200)
@limiter.limit("3/minute")
def post_confirm_store(
    request: Request,
    receipt_id: uuid.UUID,
    token: str = Depends(get_bearer_token),
    db: Session = Depends(get_db),
):
    """User confirms the OCR-detected store for a receipt that couldn't be
    auto-matched. Creates a ``user_suggested`` store with ``validation_status='pending'``
    and links the receipt. Cashback stays gated until consensus accumulates.

    See ARCH_store_validation.md § Endpoint confirm-store.
    """
    user = get_http_current_user(db, token)
    return confirm_store_from_ocr(db, receipt_id=receipt_id, user_id=user.id)


@router.post("/label", status_code=202)
@limiter.limit("3/minute")
def post_scan_label(
    request: Request,
    store_id: uuid.UUID = Form(...),
    image: UploadFile = File(...),
    hint: Literal["label", "receipt"] = Form("label"),
    token: str = Depends(get_bearer_token),
    db: Session = Depends(get_db),
):
    user = get_http_current_user(db, token)
    scan_id = submit_label(db, store_id=store_id, image=image, user_id=user.id, hint=hint)
    return JSONResponse(status_code=202, content={"scan_id": str(scan_id)})


@router.post("/label/batch", status_code=202)
@limiter.limit("3/minute")
def post_scan_label_batch(
    request: Request,
    user_lat: float = Form(...),
    user_lng: float = Form(...),
    images: list[UploadFile] = File(...),
    hint: Literal["label", "receipt"] = Form("label"),
    token: str = Depends(get_bearer_token),
    db: Session = Depends(get_db),
):
    user = get_http_current_user(db, token)
    result = submit_label_batch(
        db,
        user_lat=user_lat,
        user_lng=user_lng,
        images=images,
        user_id=user.id,
        hint=hint,
    )
    return JSONResponse(
        status_code=202,
        content={
            "session_id": str(result["session_id"]),
            "scan_ids": [str(s) for s in result["scan_ids"]],
            "store_status": result["store_status"],
        },
    )


@router.get("/label/session/{session_id}")
def get_label_session(
    session_id: uuid.UUID,
    token: str = Depends(get_bearer_token),
    db: Session = Depends(get_db),
):
    user = get_http_current_user(db, token)
    return get_label_session_status(db, session_id, user_id=user.id)


@router.get("/check-hash")
@limiter.limit("20/minute")
def get_check_hash(
    request: Request,
    hash: str = Query(..., min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$"),
    token: str = Depends(get_bearer_token),
    db: Session = Depends(get_db),
):
    """Client-side duplicate check — returns {duplicate: bool} without uploading.

    Checks both receipts and label scans. A True result means the client can skip
    the upload entirely (network optimisation). This does NOT replace server-side
    deduplication, which is enforced unconditionally on every upload.
    """
    get_http_current_user(db, token)  # auth required — prevent hash enumeration
    duplicate = check_photo_hash_receipt(db, hash) or check_photo_hash_scan(db, hash)
    return {"duplicate": duplicate}


@router.post("/barcode")
@limiter.limit("10/minute")
def post_scan_barcode(
    request: Request,
    body: BarcodeScanRequest,
    token: str = Depends(get_bearer_token),
    db: Session = Depends(get_db),
):
    user = get_http_current_user(db, token)
    return scan_barcode(db, ean=body.ean, user_id=user.id, scan_id=body.scan_id)


@router.get("/history")
def get_scan_history(
    limit: int = Query(20, ge=1, le=50),
    cursor: str | None = Query(None, min_length=1, max_length=256),
    token: str = Depends(get_bearer_token),
    db: Session = Depends(get_db),
):
    """Return the authenticated user's scan history — unified entries
    (receipts + label groups), newest first, cursor pagination.

    See ``ratis_client/ARCH_scan_history.md`` § Endpoints backend.
    """
    user = get_http_current_user(db, token)
    return list_scan_history(db, user_id=user.id, limit=limit, cursor=cursor)


@router.get("/label-group")
def get_scan_label_group(
    store_id: uuid.UUID = Query(...),
    date: str = Query(..., pattern=r"^\d{4}-\d{2}-\d{2}$"),
    token: str = Depends(get_bearer_token),
    db: Session = Depends(get_db),
):
    """Return accepted electronic_label scans for the group (store, date).

    Unmatched and rejected scans are never included — see ARCH_scan_history.
    Raises 404 ``group_not_found`` if no accepted scan matches.
    """
    from datetime import date as _date

    try:
        day = _date.fromisoformat(date)
    except ValueError:
        from fastapi import HTTPException

        raise HTTPException(status_code=422, detail="invalid_date")
    user = get_http_current_user(db, token)
    return get_label_group(db, user_id=user.id, store_id=store_id, day=day)
