"""Cross-user pHash lookup — anti-fraud PR2 (phase 0 of pipeline).

Given a candidate pHash hex string computed by
:func:`worker.pipeline.phash.compute_phash`, find a cross-user
receipt with a near-identical image hash inside a configurable time
window. A hit means the same image (within JPEG / crop / brightness
tolerance) was already submitted by a different user — which the V1
policy auto-rejects as ``image_duplicate``.

Hamming distance is computed in PG via bit-level XOR on a 64-bit
representation : ``('x' || hex)::bit(64)`` casts a 16-char hex string
to a bit-string of fixed width, ``#`` is the bitwise-XOR operator on
bit-strings, and ``bit_count`` (PG14+) returns the number of set bits.
This keeps the heavy lifting inside the DB and lets the partial index
``idx_receipts_image_phash`` short-circuit non-pHash receipts.

Fail-safe contract
------------------

* The receipt_task hot-path **must not** crash on a lookup bug — this
  helper catches anything broader than ``ProgrammingError`` (which
  would indicate a schema drift the caller should know about during
  tests). On a runtime DB error we log + return ``None`` so OCR
  continues. The caller is expected to wrap the call in its own
  ``try / except`` block too (defense in depth).

Cf. ``ARCH_receipt_pipeline.md`` § "Réconciliation tickets — V1" step 2.
"""

from __future__ import annotations

import logging
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


# Type alias — (peer_receipt_id, details_dict). ``details_dict`` keys
# are documented inline below and used as-is in
# ``fraud_suspicions.detection_details_jsonb``.
PhashMatch = tuple[UUID, dict]


def lookup_phash_cross_user(
    db: Session,
    *,
    user_id: UUID | None,
    candidate_phash_hex: str,
    max_hamming_distance: int,
    window_days: int,
) -> PhashMatch | None:
    """Find a cross-user receipt with pHash within ``max_hamming_distance``.

    Args:
        db: SQLAlchemy session — read-only query, no commit needed.
        user_id: the *current* user uploading. Excluded from the lookup
            so re-uploads by the same user fall through to the regular
            intra-user dedup (handled in PR3 via ``fp_user``). ``None``
            (anonymous reprocess) means "no exclusion" — all receipts
            with a populated ``user_id`` are considered.
        candidate_phash_hex: the 16-char lowercase hex produced by
            :func:`worker.pipeline.phash.compute_phash`.
        max_hamming_distance: threshold ; matches above this are not
            returned. Configured via
            ``pipeline.anti_fraud.phash_hamming_threshold`` (default 8).
        window_days: lookback window on ``receipts.created_at``. Configured
            via ``pipeline.anti_fraud.phash_window_days`` (default 30).

    Returns:
        ``(peer_receipt_id, details_dict)`` of the *closest* match
        within the window, or ``None`` if no match within threshold.
        ``details_dict`` carries::

            {
                "peer_user_id": "<uuid-str>" | None,
                "peer_receipt_id": "<uuid-str>",
                "hamming_distance": <int>,
            }

        Ties on distance are broken by the most recent receipt (DESC
        on ``created_at``) so the admin reviewer sees the freshest
        offender first.

    Notes:
        * Receipts with ``user_id IS NULL`` are skipped — they
          represent legacy / anonymized rows for which no cross-user
          fraud signal is meaningful.
        * The lookup uses ``receipts.created_at`` (not
          ``purchased_at``) so the window is anchored on submission
          time, matching the ARCH semantics ("a re-upload within 30
          days of the original submission").
    """
    if not candidate_phash_hex or len(candidate_phash_hex) != 16:
        logger.warning(
            "lookup_phash_cross_user: invalid candidate_phash_hex=%r — skipping",
            candidate_phash_hex,
        )
        return None
    if max_hamming_distance < 0:
        logger.warning(
            "lookup_phash_cross_user: negative max_hamming_distance=%d — skipping",
            max_hamming_distance,
        )
        return None
    if window_days <= 0:
        logger.warning(
            "lookup_phash_cross_user: non-positive window_days=%d — skipping",
            window_days,
        )
        return None

    # Hamming distance via bit(64) XOR. ``('x' || :candidate)::bit(64)``
    # turns the 16-char hex into a fixed-width bit string ; ``#`` is the
    # bitwise XOR ; ``bit_count`` (PG14+) returns the number of set bits.
    # We pre-compute the candidate cast once via a CTE to keep the
    # WHERE predicate sargable on ``idx_receipts_image_phash``.
    sql = text(
        """
        WITH cand AS (
            SELECT ('x' || :candidate)::bit(64) AS bits
        )
        SELECT id, user_id,
               bit_count(
                 ('x' || image_phash)::bit(64) # (SELECT bits FROM cand)
               ) AS hamming
          FROM receipts
         WHERE image_phash IS NOT NULL
           AND (:user_id IS NULL OR user_id <> :user_id)
           AND user_id IS NOT NULL
           AND created_at > now() - make_interval(days => :window_days)
           AND bit_count(
                 ('x' || image_phash)::bit(64) # (SELECT bits FROM cand)
               ) <= :threshold
         ORDER BY hamming ASC, created_at DESC
         LIMIT 1
        """
    )

    try:
        row = db.execute(
            sql,
            {
                "candidate": candidate_phash_hex,
                "user_id": user_id,
                "window_days": window_days,
                "threshold": max_hamming_distance,
            },
        ).first()
    except Exception as exc:
        # Any DB hiccup (timeout, transient connection, schema drift)
        # must not block OCR. We log so Sentry / log aggregation can
        # raise a breadcrumb, then return None.
        logger.warning(
            "lookup_phash_cross_user failed (candidate=%s, user=%s): %s",
            candidate_phash_hex,
            user_id,
            exc,
            exc_info=True,
        )
        return None

    if row is None:
        return None

    return (
        row.id,
        {
            "peer_user_id": str(row.user_id) if row.user_id else None,
            "peer_receipt_id": str(row.id),
            "hamming_distance": int(row.hamming),
        },
    )
