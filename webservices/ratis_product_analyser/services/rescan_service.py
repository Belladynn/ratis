"""Receipt rescan service — anti-fraud PR5.

Backs ``POST /api/v1/scan/receipt/{receipt_id}/rescan`` (cf
``routes/scan.py`` + ``ARCH_receipt_pipeline.md`` § "Implem sprint
suggéré" PR5).

Behaviour (cf brief 2026-05-11) :

1. Ownership : 404 ``receipt_not_found`` if the receipt does not exist
   or does not belong to the caller (do not leak existence).
2. Already accepted : 409 ``receipt_already_accepted`` if any linked
   scan has ``status='accepted'`` or ``status='matched'`` — we never
   re-OCR a validated receipt.
3. Image expired : 410 ``receipt_image_expired`` if
   ``image_deleted_at`` is set (the R2 48h window elapsed and the
   batch purger nulled the key).
4. Cap : 429 ``rescan_cap_exceeded`` when ``rescan_attempts`` already
   matches the configured ``pipeline.anti_fraud.rescan_max_attempts``.
5. Increment atomically via ``UPDATE ... SET rescan_attempts =
   rescan_attempts + 1 WHERE id=:rid AND rescan_attempts < :cap`` and
   re-check the affected row count : if 0, the cap was crossed by a
   concurrent rescan racing this one, surface the same 429 — no
   silent over-increment, no double-enqueue.
6. Enqueue ``worker.receipt_task.process_receipt`` (the same dispatch
   the initial upload uses). Phase 0 pHash + Phase 5 fingerprint run
   in the pipeline ; the consolidation helper
   ``_consolidate_rescan_into_existing`` collapses a successful re-OCR
   back into the canonical receipt.

The service raises domain exceptions (``NotFound`` / ``Conflict`` /
``Gone`` / ``ServiceUnavailable``) handled by ``main.py``. The route
translates the cap-exceeded case to a direct ``HTTPException(429)``
because Ratis services do not have a dedicated ``TooManyRequests``
domain exception yet — see ``__init__`` of
:exc:`RescanCapExceeded` below.

Atomicity contract
------------------
The ``UPDATE ... WHERE rescan_attempts < :cap`` is a single
PostgreSQL statement — row-level locking guarantees that two
concurrent rescans cannot both observe a pre-update value below the
cap and both succeed. The route reads ``rescan_attempts`` BEFORE the
UPDATE only to decide ``early`` 429 (no enqueue side-effect on a
guaranteed-fail path) ; the canonical enforcement is the
``rowcount = 0`` branch after the UPDATE.
"""

from __future__ import annotations

import logging
import uuid
from typing import TYPE_CHECKING

from ratis_core.exceptions import Conflict, Gone, NotFound, ServiceUnavailable
from ratis_core.settings import load_settings
from sqlalchemy import text
from sqlalchemy.orm import Session

if TYPE_CHECKING:
    from ratis_core.models.scan import Receipt

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Public exceptions — translate to HTTP codes in the route
# ---------------------------------------------------------------------------
class RescanCapExceeded(Exception):
    """The user has used all allowed rescan attempts for this receipt.

    Maps to HTTP 429 ``rescan_cap_exceeded`` at the route layer. We do
    not piggy-back on slowapi's rate-limit handler (that path is wired
    for ``RateLimitExceeded`` from the limiter middleware and returns
    a fixed ``rate_limit_exceeded`` detail string) — this is a
    domain-level cap, not a rate-limit, and the operator wants to
    distinguish the two in logs.
    """

    def __init__(self, *, attempts: int, cap: int) -> None:
        self.attempts = attempts
        self.cap = cap
        super().__init__(f"rescan_cap_exceeded (attempts={attempts}, cap={cap})")


# ---------------------------------------------------------------------------
# Indirection — tests stub ``_enqueue_rescan`` to inspect the dispatch
# without spinning up a Celery worker.
# ---------------------------------------------------------------------------
def _enqueue_rescan(receipt_id: uuid.UUID) -> None:
    """Dispatch the same Celery task the initial upload uses.

    Late import — keeps Celery out of the FastAPI import graph in
    tests that monkeypatch this attribute.
    """
    from tasks import enqueue_ocr_job

    enqueue_ocr_job(receipt_id)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _rescan_max_attempts() -> int:
    """Read the cap from ``ratis_settings.json`` (fail-fast on missing key)."""
    settings = load_settings()
    return int(settings["pipeline"]["anti_fraud"]["rescan_max_attempts"])


def _receipt_already_accepted(db: Session, receipt_id: uuid.UUID) -> bool:
    """Return True if any scan linked to this receipt is matched/accepted.

    Pipeline_v3 emits ``status='matched'`` ; legacy V2 emits
    ``status='accepted'``. We treat both as a terminal acceptance — no
    rescan path can promote a receipt past validated.
    """
    row = db.execute(
        text("SELECT 1 FROM scans WHERE receipt_id = :rid   AND status IN ('matched', 'accepted') LIMIT 1"),
        {"rid": str(receipt_id)},
    ).first()
    return row is not None


# ---------------------------------------------------------------------------
# Public entrypoint
# ---------------------------------------------------------------------------
def rescan_receipt(
    db: Session,
    *,
    receipt_id: uuid.UUID,
    user_id: uuid.UUID,
) -> dict:
    """Re-trigger the OCR pipeline on an existing receipt.

    Returns a dict shaped for the 202 response :
        ``{"receipt_id": <uuid>, "rescan_attempts": <int>, "status": "queued"}``

    Raises (translated by the route / ``main.py`` handlers) :
        - :exc:`NotFound` — receipt absent or not owned by ``user_id``
        - :exc:`Conflict` — receipt is already matched/accepted
        - :exc:`Gone` — R2 image was purged after the 48h window
        - :exc:`RescanCapExceeded` — cap reached (429)
        - :exc:`ServiceUnavailable` — Celery enqueue failed
    """
    from ratis_core.models.scan import Receipt

    # 1. Ownership — single query, no leakage on the not-owned path.
    receipt: Receipt | None = db.get(Receipt, receipt_id)
    if receipt is None or receipt.user_id != user_id:
        raise NotFound("receipt_not_found")

    # 2. Already accepted — never re-OCR a validated receipt (R33 :
    # we refuse the workaround "let the user rescan and overwrite"
    # because admin override is the proper path for an accepted receipt
    # that turned out wrong).
    if _receipt_already_accepted(db, receipt_id):
        raise Conflict("receipt_already_accepted")

    # 3. Image expired — the R2 batch purger nulls ``image_r2_key`` and
    # sets ``image_deleted_at`` 48h after upload. Without the source
    # bytes, the phase 1 OCR step has nothing to chew on.
    if receipt.image_deleted_at is not None or not receipt.image_r2_key:
        raise Gone("receipt_image_expired")

    # 4. Cap — soft pre-check so the 429 is the first signal the
    # client sees (no Celery dispatch overhead). The race-safe
    # enforcement happens at step 5 below.
    cap = _rescan_max_attempts()
    if receipt.rescan_attempts >= cap:
        raise RescanCapExceeded(attempts=receipt.rescan_attempts, cap=cap)

    # 5. Atomic increment — UPDATE ... WHERE attempts < :cap. If
    # another request raced us past the cap, ``rowcount`` is 0 and
    # we surface the same 429 (no double-enqueue, no over-increment).
    update_result = db.execute(
        text(
            "UPDATE receipts "
            "SET rescan_attempts = rescan_attempts + 1, "
            "    updated_at = now() "
            "WHERE id = :rid AND rescan_attempts < :cap "
            "RETURNING rescan_attempts"
        ),
        {"rid": str(receipt_id), "cap": cap},
    )
    row = update_result.first()
    if row is None:
        # Two paths converge here :
        #   - The row vanished (anonymized between the get() above and
        #     this UPDATE) — extremely unlikely but RGPD-possible.
        #   - The cap was reached by a concurrent rescan in the gap
        #     between our pre-check and the UPDATE.
        # Both surface as 429 from the user's POV — the receipt's
        # current attempts is whatever the DB now holds, which we
        # re-read for the error payload. We refuse to fabricate
        # values (R33 — never hardcode to make a test pass).
        current = db.execute(
            text("SELECT rescan_attempts FROM receipts WHERE id = :rid"),
            {"rid": str(receipt_id)},
        ).scalar()
        raise RescanCapExceeded(attempts=current or cap, cap=cap)
    new_attempts = int(row[0])

    # 6. Enqueue — fail-loud if Celery is unreachable. The DB commit is
    # owned by the route (route-level boundary, R02).
    try:
        _enqueue_rescan(receipt_id)
    except Exception as exc:
        logger.exception(
            "Celery enqueue failed for rescan of receipt %s — rolling back attempt counter",
            receipt_id,
        )
        # The counter increment is reverted by the route's transaction
        # rollback path. We raise ``ServiceUnavailable`` so the route
        # rolls back rather than committing a phantom attempt.
        raise ServiceUnavailable("queue_unavailable") from exc

    return {
        "receipt_id": str(receipt_id),
        "rescan_attempts": new_attempts,
        "status": "queued",
    }
