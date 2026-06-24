"""Celery task : retroactive barcode re-parse for one retailer.

Triggered by ``POST /api/v1/admin/barcode/reparse`` after the admin
inserts a new row in ``retailer_receipt_formats``. The task scans
``receipts`` for rows where the raw barcode is stored
(``receipt_barcode IS NOT NULL``) but the parsed JSON is still empty
(``barcode_fields IS NULL``) AND the (normalized) store retailer matches
the requested ``retailer_key``. For each row :

1. invoke :func:`worker.pipeline.barcode.parse_receipt_barcode`,
2. UPDATE ``receipts.barcode_fields`` if the parser produced a useful
   structured payload (any of ``store_code`` / ``tx_id`` / ``date`` set),
3. INSERT a ``pipeline_audit_log`` row with ``phase='manual'`` and
   ``event='barcode_reparsed'`` carrying the operator handle + the
   parsed fields for traceability.

Idempotent — re-running on the same retailer is safe because the WHERE
filters out rows whose ``barcode_fields`` was already populated by the
previous run (or by the live pipeline since).

Returns a stats dict ``{processed, parsed_ok, parse_failed}`` so the
admin (or a downstream notifier) can surface progress.
"""

from __future__ import annotations

import json
import logging

from celery_app import celery_app
from sqlalchemy import text

logger = logging.getLogger(__name__)


@celery_app.task(name="worker.barcode_reparse_task.reparse_barcode_for_retailer")
def reparse_barcode_for_retailer(
    *,
    retailer_key: str,
    admin_operator: str,
) -> dict[str, int]:
    """Re-parse all backlog receipts for ``retailer_key``.

    Both arguments are mandatory keyword-only — Celery serialises them
    as JSON kwargs over the broker, so the task signature stays explicit.
    """
    # Local imports : avoid circular at module-load time and keep the
    # admin-route module light. ``receipt_task`` is imported as a module
    # (not via ``from``) so that monkeypatching its
    # ``_get_session_factory`` attribute in tests is honored — a
    # ``from worker.receipt_task import _get_session_factory`` would
    # bind the original at import time and miss the patch.
    from worker import receipt_task as _rt
    from worker.pipeline.barcode import parse_receipt_barcode

    stats: dict[str, int] = {
        "processed": 0,
        "parsed_ok": 0,
        "parse_failed": 0,
    }

    session_cm = _rt._get_session_factory()()
    with session_cm as db:
        # Mirror the admin endpoint's normalization SQL — must stay in
        # sync with :data:`routes.admin.barcode._NORMALIZE_RETAILER_SQL`.
        rows = (
            db.execute(
                text(
                    "SELECT r.id AS id, r.receipt_barcode AS receipt_barcode, "
                    "       s.retailer AS retailer "
                    "FROM receipts r "
                    "LEFT JOIN stores s ON r.store_id = s.id "
                    "WHERE r.receipt_barcode IS NOT NULL "
                    "  AND r.barcode_fields IS NULL "
                    "  AND REPLACE(LOWER(unaccent(s.retailer)), ' ', '_') = :k"
                ),
                {"k": retailer_key},
            )
            .mappings()
            .all()
        )

        for row in rows:
            stats["processed"] += 1
            receipt_id = row["id"]
            raw = row["receipt_barcode"]
            retailer = row["retailer"]
            try:
                parsed = parse_receipt_barcode(raw, retailer, db)
            except Exception:
                # must not abort the whole batch
                logger.warning(
                    "parse_receipt_barcode raised for receipt %s — skipping",
                    receipt_id,
                    exc_info=True,
                )
                stats["parse_failed"] += 1
                continue

            # "Useful" parse = at least one canonical structured field
            # extracted. ``raw`` alone is not enough — the column would
            # just duplicate ``receipt_barcode`` content with no upgrade.
            useful = bool(parsed.store_code or parsed.tx_id or parsed.date)
            if not useful:
                stats["parse_failed"] += 1
                continue

            payload = parsed.model_dump(mode="json", exclude={"raw"})
            payload_json = json.dumps(payload, sort_keys=True)

            db.execute(
                text("UPDATE receipts SET barcode_fields =   CAST(:payload AS jsonb) WHERE id = :rid"),
                {"payload": payload_json, "rid": str(receipt_id)},
            )

            audit_payload = {
                "receipt_id": str(receipt_id),
                "retailer_key": retailer_key,
                "admin_operator": admin_operator,
                "parsed_fields": payload,
            }
            try:
                db.execute(
                    text(
                        "INSERT INTO pipeline_audit_log "
                        "(phase, level, event, payload) "
                        "VALUES ('manual', 'normal', 'barcode_reparsed', "
                        "        CAST(:p AS jsonb))"
                    ),
                    {"p": json.dumps(audit_payload, sort_keys=True)},
                )
            except Exception:
                # mask the UPDATE outcome
                logger.warning(
                    "pipeline_audit_log insert failed for receipt %s — best-effort skip",
                    receipt_id,
                    exc_info=True,
                )

            stats["parsed_ok"] += 1

        db.commit()

    return stats
