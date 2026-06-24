"""User-driven confirmation of an OCR-detected unknown store.

PR-B Phase 1 — see ARCH_store_validation.md.

Flow:
1. User scans receipt → OCR pipeline records a ``StoreCandidate`` (raw header,
   retailer guess, postal/address) and leaves the receipt at ``store_status='unknown'``.
2. Frontend prompts the user to confirm via a modal showing the candidate info.
3. User taps Confirm → this service creates a ``user_suggested`` store with
   ``validation_status='pending'``, links the receipt, processes pending items,
   and writes an audit row. Cashback is gated until the store flips to
   ``validation_status='confirmed'`` (later, by the consensus batch in Phase 2).

The candidate's ``retailer_guess`` and ``address_guess`` are taken verbatim — the
user does NOT type anything. This is the anti-abuse stance: the user only confirms
what the OCR has parsed, never invents.
"""

from __future__ import annotations

import logging
import uuid
from decimal import Decimal

from ratis_core.exceptions import Conflict, NotFound, UnprocessableEntity
from ratis_core.models.scan import Receipt
from ratis_core.models.store import Store, StoreValidationHistory
from ratis_core.models.store_candidate import StoreCandidate
from ratis_core.utils import assert_owner
from repositories.scan_repository import process_pending_items
from sqlalchemy import select
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


def _candidate_is_sufficient(candidate: StoreCandidate) -> bool:
    """A candidate can drive a user-suggested store iff it has a retailer guess
    AND at least one of (address, postal_code). Anything weaker is rejected to
    avoid creating phantom stores from ambiguous OCR output."""
    if not candidate.retailer_guess or not candidate.retailer_guess.strip():
        return False
    has_address = bool(candidate.address_guess and candidate.address_guess.strip())
    has_postal = bool(candidate.postal_code and candidate.postal_code.strip())
    return has_address or has_postal


def get_candidate_for_receipt(db: Session, receipt_id: uuid.UUID) -> StoreCandidate | None:
    """Return the most-recent candidate attached to this receipt, if any.

    Used by both the GET /scan/receipt endpoint (to surface candidate info on
    unknown/pending receipts) and confirm_store_from_ocr (which additionally
    requires status='pending'). Returning all statuses keeps the GET response
    consistent across the lifecycle: after confirmation the candidate flips to
    'matched' but the receipt is still in pending state and we still want to
    show what was confirmed.
    """
    return db.scalar(
        select(StoreCandidate)
        .where(StoreCandidate.receipt_id == receipt_id)
        .order_by(StoreCandidate.created_at.desc())
        .limit(1)
    )


def get_pending_candidate_for_receipt(db: Session, receipt_id: uuid.UUID) -> StoreCandidate | None:
    """Return the most-recent pending candidate (used by the confirm flow)."""
    return db.scalar(
        select(StoreCandidate)
        .where(StoreCandidate.receipt_id == receipt_id)
        .where(StoreCandidate.status == "pending")
        .order_by(StoreCandidate.created_at.desc())
        .limit(1)
    )


def serialize_candidate_info(candidate: StoreCandidate) -> dict | None:
    """Public-shape projection of a candidate for the GET receipt endpoint.

    Returns None if the candidate is too weak to act on (frontend hides the
    Confirm button when this is None).
    """
    if not _candidate_is_sufficient(candidate):
        return None
    return {
        "brand_guess": candidate.retailer_guess,
        "address": candidate.address_guess,
        "postal_code": candidate.postal_code,
        "city": None,  # store_candidates does not store city — V2 refinement.
        "phone": candidate.phone,
    }


def confirm_store_from_ocr(db: Session, receipt_id: uuid.UUID, user_id: uuid.UUID) -> dict:
    """Create a ``user_suggested`` store from the receipt's OCR candidate.

    Race-safety (P-3): we ``SELECT FOR UPDATE`` the receipt row up-front so
    a concurrent double-tap cannot create two stores.

    Raises:
        NotFound("receipt_not_found") — receipt missing
        (HTTPException 403) — ownership mismatch via assert_owner
        Conflict("receipt_already_resolved") — receipt already pending/confirmed
        UnprocessableEntity("candidate_not_found") — no OCR candidate attached
        UnprocessableEntity("insufficient_ocr_data") — candidate too weak
    """
    # Lock the receipt row to serialise concurrent confirms (P-3).
    receipt = db.scalar(select(Receipt).where(Receipt.id == receipt_id).with_for_update())
    if receipt is None:
        raise NotFound("receipt_not_found")

    assert_owner(receipt, user_id)

    if receipt.store_status != "unknown":
        raise Conflict("receipt_already_resolved")

    candidate = get_pending_candidate_for_receipt(db, receipt_id)
    if candidate is None:
        raise UnprocessableEntity("candidate_not_found")

    if not _candidate_is_sufficient(candidate):
        raise UnprocessableEntity("insufficient_ocr_data")

    retailer = candidate.retailer_guess.strip()
    store = Store(
        name=retailer.title(),
        retailer=retailer.lower(),
        address=candidate.address_guess,
        postal_code=candidate.postal_code,
        phone=candidate.phone,
        # Placeholder coords — admin/V2 batch geocodes later. Lat/lng are NOT
        # NULL on the schema so we use 0 as the sentinel.
        lat=Decimal("0"),
        lng=Decimal("0"),
        is_disabled=False,
        source="user_suggested",
        validation_status="pending",
        suggested_by_user_id=user_id,
    )
    db.add(store)
    db.flush()  # populate store.id

    db.add(
        StoreValidationHistory(
            store_id=store.id,
            from_status=None,
            to_status="pending",
            reason="user_confirmed",
            triggered_by=f"user:{user_id}",
            meta={
                "receipt_id": str(receipt.id),
                "candidate_id": str(candidate.id),
            },
        )
    )

    receipt.store_id = store.id
    receipt.store_status = "pending"
    candidate.matched_store_id = store.id
    candidate.status = "matched"
    db.flush()

    # Promote the receipt's stashed pending_items → real Scan rows now that the
    # store is known. Cashback is intentionally NOT triggered: gated on
    # validation_status='confirmed' (Phase 2 batch).
    process_pending_items(db, receipt)

    db.commit()

    logger.info(
        "Store user_suggested created store_id=%s receipt_id=%s user=%s",
        store.id,
        receipt.id,
        user_id,
    )

    return {
        "store_status": "pending",
        "store_id": str(store.id),
        "validation_status": "pending",
        "message": "store_pending_validation",
    }
