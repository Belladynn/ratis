"""Repository layer for user-driven product field contributions.

Keeps raw SQL out of the service layer (R03). The service
(``services/product_contribute_service.py``) calls these helpers for
the anti-spam daily-cap accounting.
"""

from __future__ import annotations

import uuid

from sqlalchemy import text
from sqlalchemy.orm import Session


def count_user_contributions_last_24h(
    db: Session,
    *,
    user_id: uuid.UUID,
) -> int:
    """Count contributions created by ``user_id`` in the trailing 24h.

    The window mirrors the idempotency window in
    :func:`services.product_contribute_service._find_recent_contribution`
    (``created_at > now() - interval '24 hours'``) so the cap and the
    de-dup window agree on what "a day" means.
    """
    row = db.execute(
        text(
            "SELECT COUNT(*) AS n FROM product_contributions "
            "WHERE user_id = :uid "
            "  AND created_at > now() - interval '24 hours'"
        ),
        {"uid": user_id},
    ).first()
    return int(row.n) if row is not None else 0
