"""
Mystery product service — orchestration layer.

Thin service: delegates to repositories, re-exports domain exceptions.
"""

from __future__ import annotations

import uuid

from repositories.cab_repository import award_cab
from repositories.mystery_repository import (
    ChallengeNotModifiable,
    ChallengeOverlap,
    NoEligibleProduct,
    check_mystery_find,
)
from repositories.notification_repository import enqueue_notification
from sqlalchemy.orm import Session

# Re-export exceptions for callers (routes) that import from service layer.
__all__ = [
    "ChallengeNotModifiable",
    "ChallengeOverlap",
    "NoEligibleProduct",
    "process_mystery_find",
]


def process_mystery_find(
    db: Session,
    user_id: uuid.UUID,
    scan_id: uuid.UUID,
) -> None:
    """
    Called from events_service.handle_action.

    If the scan product matches an active mystery challenge:
    - Records the find with atomic rank
    - Awards CAB (if > 0)
    - Enqueues a mystery_product_found notification

    No-op if:
    - No active challenge exists
    - Scanned EAN doesn't match the challenge product
    - User already found this challenge
    """
    result = check_mystery_find(db, user_id, scan_id)
    if result is None:
        return

    rank = result["rank"]
    cab = result["cab_awarded"]

    if cab > 0:
        award_cab(
            db,
            user_id,
            cab,
            "mystery_product",
            reference_id=scan_id,
            reference_type="scan",
        )

    enqueue_notification(db, user_id, "mystery_product_found", {"rank": rank, "cab": cab})
