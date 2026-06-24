from __future__ import annotations

import hashlib
import logging
import uuid
from decimal import Decimal

from fastapi import UploadFile
from ratis_core.exceptions import Conflict, NotFound, ServiceUnavailable, UnprocessableEntity
from ratis_core.settings import load_settings
from ratis_core.uploads import MIME_EXT, validate_image_upload
from ratis_core.utils import assert_owner
from repositories.barcode_repository import get_search_radius
from repositories.label_repository import (
    create_label_scan,
    create_label_session,
    get_label_session,
    get_label_session_scan_summary,
)
from repositories.scan_repository import (
    check_photo_hash_scan,
    get_active_store,
    get_nearest_store,
)
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from storage import R2UploadError, upload_label_image
from tasks import enqueue_label_job

logger = logging.getLogger(__name__)

# Fail fast at startup — missing keys produce a clear error rather than a cryptic KeyError later
_settings = load_settings()
try:
    _MAX_SIZE: int = _settings["ocr"]["max_file_size_mb"] * 1024 * 1024
    _BATCH_MAX: int = _settings["label"]["batch_max_images"]
except KeyError as exc:
    raise KeyError(
        f"Missing key {exc} in settings — expected: ocr.max_file_size_mb, label.batch_max_images "
        "(check app_settings table or ratis_settings.json)"
    ) from exc


def _upload_and_create_scan(
    db: Session,
    *,
    store_id: uuid.UUID | None,
    user_id: uuid.UUID,
    image: UploadFile,
    label_session_id: uuid.UUID | None = None,
    store_status: str = "confirmed",
    user_lat: Decimal | None = None,
    user_lng: Decimal | None = None,
) -> tuple[uuid.UUID, str]:
    """Validate, hash-check, DB-flush, upload to R2. Returns (scan_id, r2_key).

    When ``store_id`` is None, ``store_status`` must be ``'unknown'`` and
    ``user_lat``/``user_lng`` are expected for Part B reconciliation.
    """
    content, mime = validate_image_upload(image, allow_pdf=False, max_size_bytes=_MAX_SIZE)

    # Hash-first: check before any R2 upload — duplicate rejected with zero network cost
    photo_hash = hashlib.sha256(content).hexdigest()
    if check_photo_hash_scan(db, photo_hash):
        raise Conflict("duplicate_photo")

    scan_id = uuid.uuid4()
    ext = MIME_EXT[mime]
    key = f"label/{scan_id}.{ext}"

    # DB flush before R2 upload — if R2 fails, transaction rolls back, hash is freed.
    # The check-first above is racy : two concurrent uploads of the same photo
    # both pass the SELECT, then one loses the INSERT race on the partial unique
    # index ``scans_photo_hash_unique``. Catch that IntegrityError and surface
    # the same 409 ``duplicate_photo`` as the check-first path (no 500).
    try:
        create_label_scan(
            db,
            scan_id=scan_id,
            store_id=store_id,
            user_id=user_id,
            label_r2_key=key,
            photo_hash=photo_hash,
            label_session_id=label_session_id,
            store_status=store_status,
            user_lat=user_lat,
            user_lng=user_lng,
        )
    except IntegrityError as exc:
        if "scans_photo_hash_unique" in str(exc.orig):
            db.rollback()
            raise Conflict("duplicate_photo") from exc
        raise

    try:
        upload_label_image(content, key, content_type=mime)
    except R2UploadError as exc:
        raise ServiceUnavailable("storage_unavailable") from exc

    return scan_id, key


def submit_label(
    db: Session,
    *,
    store_id: uuid.UUID,
    image: UploadFile,
    user_id: uuid.UUID,
    hint: str = "label",
) -> uuid.UUID:
    """Upload a single label photo and enqueue OCR. Returns scan_id."""
    store = get_active_store(db, store_id)
    if store is None:
        raise NotFound("store_not_found")

    scan_id, _ = _upload_and_create_scan(db, store_id=store_id, user_id=user_id, image=image)

    # Commit before enqueue — if queue is down, scan stays pending (batch_purge cleans overnight)
    # rather than becoming an R2 file without a DB record.
    db.commit()

    try:
        enqueue_label_job(scan_id, hint=hint)
    except Exception as exc:
        raise ServiceUnavailable("queue_unavailable") from exc

    return scan_id


def submit_label_batch(
    db: Session,
    *,
    user_lat: float,
    user_lng: float,
    images: list[UploadFile],
    user_id: uuid.UUID,
    hint: str = "label",
) -> dict:
    """Upload N label photos and enqueue OCR jobs.

    The store is determined from ``(user_lat, user_lng)`` geo-match against
    active stores within the user's ``search_radius_km`` preference.

    Unknown-store path (graceful failure, Part A):
        When no store is matched in radius, the batch is **still persisted**
        with ``store_id=NULL`` on both the ``LabelSession`` and the ``Scan``
        rows, and ``store_status='unknown'`` on the scans. No CAB/XP is
        awarded. The user is invited (by the frontend) to scan a receipt
        from that store — Part B will reconcile the pending scans against
        the receipt's store (geo-match within a radius).

        The PII (user_lat/user_lng) is persisted on each scan for Part B
        reconciliation — RGPD: never logged.

    Returns dict::

        {
          "session_id": UUID,
          "scan_ids":   [UUID, ...],
          "store_status": "confirmed" | "unknown",
        }
    """
    if not images:
        raise UnprocessableEntity("no_images_provided")
    if len(images) > _BATCH_MAX:
        raise UnprocessableEntity("too_many_images")

    radius_km = get_search_radius(db, user_id)
    store_id, _ = get_nearest_store(db, user_lat, user_lng, radius_km)

    if store_id is None:
        store_status = "unknown"
        resolved_store_id: uuid.UUID | None = None
    else:
        store = get_active_store(db, store_id)
        if store is None:
            # Disabled/soft-deleted between get_nearest_store and now — treat as unknown
            store_status = "unknown"
            resolved_store_id = None
        else:
            store_status = "confirmed"
            resolved_store_id = store_id

    # Persist PII (lat/lng) only on unknown-store scans — confirmed scans are
    # already tied to a store, the geo is redundant.
    user_lat_dec = Decimal(str(user_lat)) if store_status == "unknown" else None
    user_lng_dec = Decimal(str(user_lng)) if store_status == "unknown" else None

    label_session = create_label_session(db, user_id=user_id, store_id=resolved_store_id, scan_count=len(images))

    scan_ids: list[uuid.UUID] = []
    for image in images:
        scan_id, _ = _upload_and_create_scan(
            db,
            store_id=resolved_store_id,
            user_id=user_id,
            image=image,
            label_session_id=label_session.id,
            store_status=store_status,
            user_lat=user_lat_dec,
            user_lng=user_lng_dec,
        )
        scan_ids.append(scan_id)

    # Commit session + scans before enqueue — if queue is down, scans stay pending
    # (batch_purge cleans overnight) rather than becoming R2 orphans without DB records.
    db.commit()

    # Only enqueue OCR jobs for confirmed-store scans. Unknown-store scans
    # stay pending until Part B reconciliation — OCR without a store is
    # useless for consensus, and we don't award any CAB anyway.
    if store_status == "confirmed":
        try:
            for scan_id in scan_ids:
                enqueue_label_job(scan_id, hint=hint)
        except Exception as exc:
            raise ServiceUnavailable("queue_unavailable") from exc

    return {
        "session_id": label_session.id,
        "scan_ids": scan_ids,
        "store_status": store_status,
    }


def get_label_session_status(
    db: Session,
    session_id: uuid.UUID,
    user_id: uuid.UUID,
) -> dict:
    session = get_label_session(db, session_id)
    if session is None:
        raise NotFound("label_session_not_found")
    assert_owner(session, user_id)

    summary = get_label_session_scan_summary(db, session_id)

    total = session.scan_count
    processed = sum(summary.values())
    if "pending" in summary or processed < total:
        status = "processing"
    else:
        status = "done"

    return {
        "status": status,
        "products_identified": summary.get("accepted", 0),
    }
