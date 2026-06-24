"""Celery task : admin-triggered replay of Phase 3 + Phase 4 on a
persisted ParsedTicket — ARCH_admin_endpoints PR4.

Use case : the admin curates ``products`` / ``ocr_knowledge`` /
``stores`` after a receipt failed to match well. Instead of asking the
user to re-upload the photo, we re-run the cascade (Phase 3 Match) and
the persistence (Phase 4) on the **stored** :class:`ParsedTicket`.
This is the Cardinal-state replay pattern documented in
``ARCH_receipt_pipeline.md`` § Reproductibilité.

Why ASYNC (vs the SYNC scan-level replay-match in PR3) :
- a parsed ticket can carry tens of items, each running a fuzzy
  lookup ; keeping it on the request thread would block under load.
- the admin polls task status via ``GET /admin/tasks/{task_id}/status``
  and surfaces progress in the mini UI (PR12+).

Idempotence guarantees come from the persist layer :
- ``parsed_tickets.parsed_jsonb_hash`` is UNIQUE → the upsert is a
  ``DO NOTHING`` on the second run, the existing row id is recovered.
- ``handle_barcode_rescan`` supersedes any prior receipts pointing to
  the same physical barcode before our INSERT.
- ``receipts.id`` upsert (ON CONFLICT id DO UPDATE) refreshes
  parsed_ticket_id / store_id / store_status without duplicating the
  row.

Scans are re-INSERTed with fresh ids each time. The persist layer's
caller-controlled barcode rescan path neutralises prior scans pointing
to the same physical receipt — but for receipts WITHOUT a physical
barcode, repeated replays ADD rows. This is documented as a known
caveat (the alternative — diff-then-update — would entangle Phase 4
with mutation logic that the ARCH explicitly bans). The audit log
makes the duplication observable.

Returns a stats dict :
    {
        "parsed_ticket_id": <uuid>,
        "receipt_id": <uuid>,
        "scan_ids": [<uuid>, ...],
        "store_status": <str>,
    }
"""

from __future__ import annotations

import json
import logging
from typing import Any
from uuid import UUID

from celery_app import celery_app
from sqlalchemy import text

logger = logging.getLogger(__name__)


@celery_app.task(
    name="worker.pipeline_replay_task.replay_parsed_ticket",
    # Override the global task_ignore_result=True so the admin polling
    # endpoint can recover the return value via AsyncResult.result.
    ignore_result=False,
)
def replay_parsed_ticket(
    *,
    parsed_ticket_id: str,
    admin_operator: str,
    log_level: str = "verbose",
) -> dict[str, Any]:
    """Re-run Phase 3 + 4 on a persisted ParsedTicket.

    Both arguments are mandatory keyword-only — Celery serialises them
    as JSON kwargs over the broker, so the task signature stays explicit.

    Steps :

    1. Load the ``parsed_tickets`` row + its ``parsed_jsonb`` from DB.
    2. Reconstruct a Pydantic ``ParsedTicket`` (and a synthetic
       ``RawTicket`` carrying just the metadata Phase 4 needs).
    3. Re-run Phase 3 (``match_ticket``) wired to fresh DB lookups.
    4. Re-run Phase 4 (``persist_pipeline_result``) — idempotent.
    5. Emit ``phase='manual', event='admin_replay'`` audit log row.
    6. Return a stats dict the admin polls.
    """
    # Local imports — avoid circular at module-load time and keep the
    # admin route path light. ``receipt_task`` is imported as a module
    # (not via ``from``) so monkeypatching its ``_get_session_factory``
    # attribute in tests is honored, mirroring the barcode_reparse_task
    # convention.
    from worker import receipt_task as _rt
    from worker.pipeline import match as match_mod
    from worker.pipeline import persist as persist_mod
    from worker.pipeline.orchestrator import (
        _make_consensus_exact,
        _make_consensus_fuzzy,
        _make_product_by_ean,
        _make_product_by_knowledge,
        _make_retailer_resolver,
        _make_store_by_code,
        _make_store_lookup,
    )
    from worker.pipeline.types import ParsedTicket, RawTicket

    pt_uuid = UUID(parsed_ticket_id)

    session_cm = _rt._get_session_factory()()
    with session_cm as db:
        pt_row = db.execute(
            text(
                "SELECT id, receipt_id, parsed_jsonb, parsed_jsonb_hash, "
                "       raw_ticket_image_hash, ocr_engine_version, captured_at "
                "FROM parsed_tickets WHERE id = :pt_id"
            ),
            {"pt_id": str(pt_uuid)},
        ).first()
        if pt_row is None:
            raise ValueError(f"parsed_ticket_not_found: {pt_uuid}")

        # Reconstruct a Pydantic ParsedTicket from the stored JSONB. The
        # JSONB is the canonical model_dump(mode='json') so model_validate
        # round-trips cleanly. The persisted parsed_jsonb_hash is preserved
        # so the persist layer's idempotent upsert hits the existing row.
        parsed = ParsedTicket.model_validate(pt_row.parsed_jsonb)
        # The hash on the persisted row is authoritative — use it
        # directly rather than recomputing (avoids float-noise drift if
        # any field's serialisation changes).
        parsed = parsed.model_copy(update={"parsed_jsonb_hash": pt_row.parsed_jsonb_hash})

        # Rebuild a synthetic RawTicket with empty blocks/barcodes — the
        # persist layer only reads ``raw.receipt_id``, ``raw.image_hash``,
        # ``raw.ocr_engine_version`` and ``raw.captured_at``. blocks /
        # barcodes are Phase 1 artefacts that we no longer have on disk
        # (and that Phase 4 doesn't need).
        synthetic_raw = RawTicket(
            receipt_id=pt_row.receipt_id or parsed.receipt_id,
            blocks=(),
            barcodes=(),
            image_hash=pt_row.raw_ticket_image_hash,
            ocr_engine_version=pt_row.ocr_engine_version,
            captured_at=pt_row.captured_at,
        )

        # Phase 3 — Match ─────────────────────────────────────────────────
        matched = match_mod.match_ticket(
            parsed,
            product_by_ean=_make_product_by_ean(db),
            product_by_knowledge=_make_product_by_knowledge(db),
            consensus_exact=_make_consensus_exact(db),
            consensus_fuzzy=_make_consensus_fuzzy(db),
            retailer_resolver=_make_retailer_resolver(db),
            store_lookup=_make_store_lookup(db),
            store_by_code=_make_store_by_code(db),
            log_level=log_level,
        )

        # Phase 4 — Persist ───────────────────────────────────────────────
        # Resolve user_id from the existing receipt (the replay must
        # preserve ownership ; we never strip it). When the receipt has
        # no user (anonymous reprocess), keep it None.
        user_id: UUID | None = None
        if synthetic_raw.receipt_id is not None:
            r = db.execute(
                text("SELECT user_id FROM receipts WHERE id = :rid"),
                {"rid": str(synthetic_raw.receipt_id)},
            ).first()
            if r is not None and r.user_id is not None:
                user_id = r.user_id

        result = persist_mod.persist_pipeline_result(
            raw=synthetic_raw,
            parsed=parsed,
            matched=matched,
            db=db,
            user_id=user_id,
            log_level=log_level,
        )

        # Audit row : phase='manual', event='admin_replay'. Best-effort
        # so an audit insert failure never masks the persist outcome.
        audit_payload = {
            "parsed_ticket_id": str(result["parsed_ticket_id"]),
            "receipt_id": str(result["receipt_id"]),
            "scan_ids": [str(s) for s in result["scan_ids"]],
            "store_status": matched.store_status,
            "admin_operator": admin_operator,
            "log_level": log_level,
        }
        try:
            db.execute(
                text(
                    "INSERT INTO pipeline_audit_log "
                    "(phase, level, event, parsed_ticket_id, payload) "
                    "VALUES ('manual', 'normal', 'admin_replay', :pt_id, "
                    "        CAST(:payload AS jsonb))"
                ),
                {
                    "pt_id": str(result["parsed_ticket_id"]),
                    "payload": json.dumps(audit_payload, sort_keys=True),
                },
            )
        except Exception:
            logger.warning(
                "pipeline_audit_log insert failed for parsed_ticket %s — best-effort skip",
                pt_uuid,
                exc_info=True,
            )

        db.commit()

        return {
            "parsed_ticket_id": str(result["parsed_ticket_id"]),
            "receipt_id": str(result["receipt_id"]),
            "scan_ids": [str(s) for s in result["scan_ids"]],
            "store_status": matched.store_status,
        }
