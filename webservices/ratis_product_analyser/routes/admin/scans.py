"""Admin scan-level endpoints — ARCH_admin_endpoints.md PR3.

Three endpoints :

- ``GET  /api/v1/admin/receipts/{receipt_id}`` — read-only 360 view
  (receipt + parsed_ticket + scans + audit log + store info). Used by
  the operator to investigate "what happened on this ticket".
  Requires only ``ADMIN_API_KEY``.

- ``PATCH /api/v1/admin/scans/{scan_id}`` — manual override on one
  scan. Accepts ``product_ean`` / ``status`` / ``match_method`` /
  ``store_id`` / ``rejected_reason``. Defaults ``match_method`` to
  ``'manual_admin'`` when the operator transitions to ``'matched'``.
  Validates v3 invariants in Python before the UPDATE so the operator
  gets a clean 400 (instead of a 500 from a CHECK constraint trip).
  Requires ``ADMIN_API_KEY`` + ``X-Admin-Operator`` header.

- ``POST /api/v1/admin/scans/{scan_id}/replay-match`` — re-run Phase 3
  on a single scan with the current DB knowledge. Used after the admin
  curates ``products`` / ``ocr_knowledge`` so the unresolved scan can
  pick up the new lookups without re-uploading the receipt. Synchronous
  (one item, lookups are fast). Requires ``ADMIN_API_KEY`` +
  ``X-Admin-Operator`` header.

Auth pattern : ``ADMIN_API_KEY`` on every endpoint, plus
``X-Admin-Operator`` header on mutations. No 2FA TOTP — these
endpoints touch pipeline state only, never CAB/cashback. Audit trail
is a ``pipeline_audit_log`` row at ``phase='manual'``.

KP-08 — the ``'manual_admin'`` match_method value is gated by CHECK
``ck_scans_match_method_v3``. Migration 20260430_1700_paadmin adds it
in lockstep with the SQLAlchemy ``Scan`` model. Same migration adds
``'manual'`` to ``ck_pipeline_audit_log_phase``.
"""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import Sequence
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field
from ratis_core.database import get_db
from ratis_core.deps import verify_admin_key
from ratis_core.products import claim_first_discovery
from sqlalchemy import text
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# Allowed enum values — mirrored from ck_scans_* / ck_pipeline_audit_log_*.
# Kept here for fast-fail Python validation BEFORE the SQL UPDATE so the
# operator gets a 400 with a meaningful detail instead of a 500 from a
# CHECK trip. Source of truth remains the DB CHECK constraint (KP-08).
# ---------------------------------------------------------------------------
_ALLOWED_STATUSES = {"matched", "unresolved", "rejected", "pending"}
_ALLOWED_MATCH_METHODS = {
    "barcode",
    "knowledge",
    "consensus_match",
    "fuzzy_strict",  # legacy v2 — kept until data migration drops it
    "manual_admin",
    "observed_name",
    "fuzzy",
    "fuzzy_confirmed",
    "manual",
    "barcode_ean",
}


# ===========================================================================
# Helpers
# ===========================================================================
def _require_operator(x_admin_operator: str | None) -> str:
    """Validate the X-Admin-Operator header is present and non-empty.

    Returns the operator handle. Raises 400 ``operator_required`` when the
    header is absent / empty. Honor-system identifier — no crypto check,
    only logged for human traceability per ARCH_admin_endpoints § Auth.
    """
    if not x_admin_operator or not x_admin_operator.strip():
        raise HTTPException(status_code=400, detail="operator_required")
    return x_admin_operator.strip()


def _audit(
    db: Session,
    *,
    scan_id: uuid.UUID | None,
    parsed_ticket_id: uuid.UUID | None,
    event: str,
    payload: dict[str, Any],
) -> None:
    """INSERT a ``pipeline_audit_log`` row at ``phase='manual'``.

    Mirrors the orchestrator's best-effort audit logger but always logs
    at ``level='normal'`` (admin operations are never debug-grade noise).
    Failures are logged at WARNING and swallowed so a broken audit never
    masks the operator's actual mutation outcome.
    """
    try:
        db.execute(
            text(
                "INSERT INTO pipeline_audit_log "
                "(phase, level, event, scan_id, parsed_ticket_id, payload) "
                "VALUES ('manual', 'normal', :event, :scan_id, :pt_id, "
                "        CAST(:payload AS jsonb))"
            ),
            {
                "event": event,
                "scan_id": scan_id,
                "pt_id": parsed_ticket_id,
                "payload": json.dumps(payload),
            },
        )
    except Exception:
        logger.warning(
            "pipeline_audit_log insert failed (phase=manual event=%s) — best-effort skip",
            event,
            exc_info=True,
        )


# ===========================================================================
# GET /admin/receipts/{receipt_id}  — 360 view
# ===========================================================================
@router.get(
    "/admin/receipts/{receipt_id}",
    dependencies=[Depends(verify_admin_key)],
)
def get_receipt_360(
    receipt_id: uuid.UUID,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Aggregate receipt + parsed_ticket + scans + audit + store.

    Read-only. Returns 404 ``receipt_not_found`` when the receipt id
    does not exist. The endpoint never blocks on a missing parsed_ticket
    or audit row : those fields are returned as ``null`` / empty list so
    the operator can still see partial state on legacy / failed receipts.
    """
    receipt = db.execute(
        text(
            "SELECT id, user_id, store_id, purchased_at, total_amount, "
            "       total_lines_detected, parsed_ticket_id, store_status, "
            "       receipt_barcode, image_r2_key, image_uploaded_at, "
            "       image_deleted_at, created_at, updated_at "
            "FROM receipts WHERE id = :rid"
        ),
        {"rid": str(receipt_id)},
    ).first()

    if receipt is None:
        raise HTTPException(status_code=404, detail="receipt_not_found")

    store: dict[str, Any] | None = None
    if receipt.store_id is not None:
        store_row = db.execute(
            text(
                "SELECT id, name, retailer, address, city, postal_code, "
                "       lat, lng, source, is_disabled "
                "FROM stores WHERE id = :sid"
            ),
            {"sid": str(receipt.store_id)},
        ).first()
        if store_row is not None:
            store = {
                "id": str(store_row.id),
                "name": store_row.name,
                "retailer": store_row.retailer,
                "address": store_row.address,
                "city": store_row.city,
                "postal_code": store_row.postal_code,
                "lat": float(store_row.lat) if store_row.lat is not None else None,
                "lng": float(store_row.lng) if store_row.lng is not None else None,
                "source": store_row.source,
                "is_disabled": bool(store_row.is_disabled),
            }

    parsed_ticket: dict[str, Any] | None = None
    if receipt.parsed_ticket_id is not None:
        pt_row = db.execute(
            text(
                "SELECT id, receipt_id, parsed_jsonb_hash, "
                "       raw_ticket_image_hash, ocr_engine_version, "
                "       captured_at, created_at, parsed_jsonb "
                "FROM parsed_tickets WHERE id = :pt_id"
            ),
            {"pt_id": str(receipt.parsed_ticket_id)},
        ).first()
        if pt_row is not None:
            parsed_ticket = {
                "id": str(pt_row.id),
                "receipt_id": str(pt_row.receipt_id) if pt_row.receipt_id else None,
                "parsed_jsonb_hash": pt_row.parsed_jsonb_hash,
                "raw_ticket_image_hash": pt_row.raw_ticket_image_hash,
                "ocr_engine_version": pt_row.ocr_engine_version,
                "captured_at": pt_row.captured_at.isoformat() if pt_row.captured_at else None,
                "created_at": pt_row.created_at.isoformat() if pt_row.created_at else None,
                "parsed_jsonb": pt_row.parsed_jsonb,
            }

    scan_rows = db.execute(
        text(
            "SELECT id, user_id, store_id, product_ean, scanned_name, "
            "       price, quantity, tva_amount, scan_type, status, "
            "       match_method, match_confidence, rejected_reason, "
            "       scanned_at, status_updated_at, parsed_ticket_id, "
            "       store_status "
            "FROM scans WHERE receipt_id = :rid ORDER BY scanned_at, id"
        ),
        {"rid": str(receipt_id)},
    ).fetchall()
    scans = [
        {
            "id": str(s.id),
            "user_id": str(s.user_id) if s.user_id else None,
            "store_id": str(s.store_id) if s.store_id else None,
            "product_ean": s.product_ean,
            "scanned_name": s.scanned_name,
            "price": s.price,
            "quantity": float(s.quantity) if s.quantity is not None else None,
            "tva_amount": s.tva_amount,
            "scan_type": s.scan_type,
            "status": s.status,
            "match_method": s.match_method,
            "match_confidence": s.match_confidence,
            "rejected_reason": s.rejected_reason,
            "scanned_at": s.scanned_at.isoformat() if s.scanned_at else None,
            "status_updated_at": (s.status_updated_at.isoformat() if s.status_updated_at else None),
            "parsed_ticket_id": (str(s.parsed_ticket_id) if s.parsed_ticket_id else None),
            "store_status": s.store_status,
        }
        for s in scan_rows
    ]

    # Audit events scoped to this receipt — by parsed_ticket_id when set,
    # OR by scan_id of any scan attached to this receipt. Both filters are
    # ORed so admin events that target a scan but no parsed_ticket are
    # surfaced too.
    scan_ids = [s["id"] for s in scans]
    audit_rows: Sequence[Any] = []
    if receipt.parsed_ticket_id is not None or scan_ids:
        audit_rows = db.execute(
            text(
                "SELECT id, parsed_ticket_id, scan_id, phase, level, event, "
                "       payload, created_at "
                "FROM pipeline_audit_log "
                "WHERE (parsed_ticket_id = :pt_id) "
                "   OR (scan_id = ANY(CAST(:scan_ids AS uuid[]))) "
                "ORDER BY created_at, id"
            ),
            {
                "pt_id": (str(receipt.parsed_ticket_id) if receipt.parsed_ticket_id else None),
                "scan_ids": scan_ids,
            },
        ).fetchall()
    audit_log = [
        {
            "id": str(a.id),
            "parsed_ticket_id": (str(a.parsed_ticket_id) if a.parsed_ticket_id else None),
            "scan_id": str(a.scan_id) if a.scan_id else None,
            "phase": a.phase,
            "level": a.level,
            "event": a.event,
            "payload": a.payload,
            "created_at": a.created_at.isoformat() if a.created_at else None,
        }
        for a in audit_rows
    ]

    return {
        "receipt": {
            "id": str(receipt.id),
            "user_id": str(receipt.user_id) if receipt.user_id else None,
            "store_id": str(receipt.store_id) if receipt.store_id else None,
            "purchased_at": (receipt.purchased_at.isoformat() if receipt.purchased_at else None),
            "total_amount": receipt.total_amount,
            "total_lines_detected": receipt.total_lines_detected,
            "parsed_ticket_id": (str(receipt.parsed_ticket_id) if receipt.parsed_ticket_id else None),
            "store_status": receipt.store_status,
            "receipt_barcode": receipt.receipt_barcode,
            "image_r2_key": receipt.image_r2_key,
            "image_uploaded_at": (receipt.image_uploaded_at.isoformat() if receipt.image_uploaded_at else None),
            "image_deleted_at": (receipt.image_deleted_at.isoformat() if receipt.image_deleted_at else None),
            "created_at": receipt.created_at.isoformat() if receipt.created_at else None,
            "updated_at": receipt.updated_at.isoformat() if receipt.updated_at else None,
        },
        "store": store,
        "parsed_ticket": parsed_ticket,
        "scans": scans,
        "audit_log": audit_log,
    }


# ===========================================================================
# PATCH /admin/scans/{scan_id}  — manual override
# ===========================================================================
class ScanOverrideRequest(BaseModel):
    """Manual override payload — every field optional, only the ones set
    are applied. Validation is exclusively post-Pydantic in
    :func:`_validate_override_invariants` so the operator-facing error
    detail uses ``snake_code`` strings instead of Pydantic's tree.
    """

    product_ean: str | None = Field(default=None)
    status: str | None = Field(default=None)
    match_method: str | None = Field(default=None)
    store_id: uuid.UUID | None = Field(default=None)
    rejected_reason: str | None = Field(default=None)

    model_config = {"extra": "forbid"}


def _validate_override_invariants(
    *,
    new_status: str | None,
    new_ean: str | None,
    new_method: str | None,
    new_reason: str | None,
    new_store_id: uuid.UUID | None,
    current: dict[str, Any],
) -> tuple[str | None, str | None, str | None, str | None, uuid.UUID | None]:
    """Apply override semantics + validate v3 invariants pre-UPDATE.

    Defaulting rule : when the operator sets ``status='matched'`` and
    omits ``match_method``, default it to ``'manual_admin'`` (the
    canonical override marker). The operator still has the option to
    pass an explicit value (e.g. correcting a bad ``fuzzy_strict``
    annotation post-hoc).

    Returns the merged tuple ``(status, ean, method, reason, store_id)``
    that will be applied via the SQL UPDATE.
    """
    merged_status = new_status if new_status is not None else current["status"]
    merged_ean = new_ean if new_ean is not None else current["product_ean"]
    merged_method = new_method if new_method is not None else current["match_method"]
    merged_reason = new_reason if new_reason is not None else current["rejected_reason"]
    merged_store_id = new_store_id if new_store_id is not None else current["store_id"]

    # Default match_method when the operator transitions to 'matched'
    # without specifying one. ``manual_admin`` is the canonical override
    # marker (added in migration 20260430_1700_paadmin).
    if new_status == "matched" and new_method is None and merged_method is None:
        merged_method = "manual_admin"

    if merged_status is not None and merged_status not in _ALLOWED_STATUSES:
        raise HTTPException(status_code=400, detail="invalid_status")
    if merged_method is not None and merged_method not in _ALLOWED_MATCH_METHODS:
        raise HTTPException(status_code=400, detail="invalid_match_method")

    if merged_status == "matched" and (merged_ean is None or merged_method is None):
        raise HTTPException(status_code=400, detail="matched_requires_ean_and_method")
    if merged_status in ("unresolved", "rejected") and merged_reason is None:
        raise HTTPException(status_code=400, detail="non_matched_requires_reason")

    return merged_status, merged_ean, merged_method, merged_reason, merged_store_id


@router.patch(
    "/admin/scans/{scan_id}",
    dependencies=[Depends(verify_admin_key)],
)
def patch_scan_override(
    scan_id: uuid.UUID,
    body: ScanOverrideRequest,
    x_admin_operator: str | None = Header(default=None, alias="X-Admin-Operator"),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Force-apply an admin correction on one scan.

    Defaults ``match_method`` to ``'manual_admin'`` when the operator
    transitions ``status='matched'`` without specifying one. Logs an
    ``admin_scan_override`` event in ``pipeline_audit_log`` with the
    before/after diff and the operator handle.

    Errors :
        - 400 ``operator_required`` — missing X-Admin-Operator header
        - 400 ``invalid_status`` / ``invalid_match_method`` — value not in enum
        - 400 ``matched_requires_ean_and_method`` — v3 invariant
        - 400 ``non_matched_requires_reason`` — v3 invariant
        - 403 ``forbidden`` — wrong / missing ADMIN_API_KEY
        - 404 ``scan_not_found``
    """
    operator = _require_operator(x_admin_operator)

    current = db.execute(
        text(
            "SELECT id, status, match_method, product_ean, rejected_reason, "
            "       store_id, store_status, parsed_ticket_id, "
            "       user_id, scanned_name "
            "FROM scans WHERE id = :sid"
        ),
        {"sid": str(scan_id)},
    ).first()
    if current is None:
        raise HTTPException(status_code=404, detail="scan_not_found")

    current_dict = {
        "status": current.status,
        "match_method": current.match_method,
        "product_ean": current.product_ean,
        "rejected_reason": current.rejected_reason,
        "store_id": current.store_id,
    }

    fields_set = body.model_fields_set
    new_status = body.status if "status" in fields_set else None
    new_ean = body.product_ean if "product_ean" in fields_set else None
    new_method = body.match_method if "match_method" in fields_set else None
    new_reason = body.rejected_reason if "rejected_reason" in fields_set else None
    new_store_id = body.store_id if "store_id" in fields_set else None

    (
        merged_status,
        merged_ean,
        merged_method,
        merged_reason,
        merged_store_id,
    ) = _validate_override_invariants(
        new_status=new_status,
        new_ean=new_ean,
        new_method=new_method,
        new_reason=new_reason,
        new_store_id=new_store_id,
        current=current_dict,
    )

    db.execute(
        text(
            "UPDATE scans SET "
            "  status = :status, "
            "  match_method = :method, "
            "  product_ean = :ean, "
            "  rejected_reason = :reason, "
            "  store_id = COALESCE(:store_id, store_id), "
            "  status_updated_at = now() "
            "WHERE id = :sid"
        ),
        {
            "sid": str(scan_id),
            "status": merged_status,
            "method": merged_method,
            "ean": merged_ean,
            "reason": merged_reason,
            "store_id": (str(merged_store_id) if merged_store_id is not None else None),
        },
    )

    diff: dict[str, Any] = {}
    if new_status is not None and current.status != merged_status:
        diff["status"] = {"from": current.status, "to": merged_status}
    if new_method is not None and current.match_method != merged_method:
        diff["match_method"] = {
            "from": current.match_method,
            "to": merged_method,
        }
    if new_status == "matched" and new_method is None and merged_method == "manual_admin":
        # Surface the implicit default so the audit row makes the
        # behaviour obvious (no silent inference).
        diff.setdefault("match_method", {"from": current.match_method, "to": merged_method})
    if new_ean is not None and current.product_ean != merged_ean:
        diff["product_ean"] = {"from": current.product_ean, "to": merged_ean}
    if new_reason is not None and current.rejected_reason != merged_reason:
        diff["rejected_reason"] = {
            "from": current.rejected_reason,
            "to": merged_reason,
        }
    if new_store_id is not None and current.store_id != merged_store_id:
        diff["store_id"] = {
            "from": str(current.store_id) if current.store_id else None,
            "to": str(merged_store_id) if merged_store_id else None,
        }

    # NRC bloc C — when the operator forces ``manual_admin`` on a matched
    # scan, also append to the name-resolution ledger so the consensus
    # computation (weight 5) reflects the override. The operator handle
    # is recorded in the pipeline_audit_log row below ; the ledger row
    # uses ``scan.user_id`` (the receipt owner) for traceability since
    # the ledger schema requires a real user FK. Fast-fail
    # preconditions : merged_method must be ``manual_admin``, the scan
    # must have a store + scanned_name, and a product_ean must be set.
    if (
        merged_method == "manual_admin"
        and merged_ean is not None
        and merged_store_id is not None
        and current.user_id is not None
        and current.scanned_name
    ):
        # Local import — keeps the route module light and avoids
        # dragging the worker namespace into FastAPI startup when
        # ADMIN_API_KEY is unset.
        from repositories.name_resolution_writes import record_resolution
        from worker.ocr.normalize import normalize_text

        normalized_label = normalize_text(db, current.scanned_name)
        record_resolution(
            db,
            scan_id=scan_id,
            store_id=merged_store_id,
            normalized_label=normalized_label,
            product_ean=merged_ean,
            user_id=current.user_id,
            match_method="manual_admin",
            source_type="receipt",
        )

    # V1.1 first-discovery attribution (KP-75) — admin override path.
    # When an operator forcibly transitions a scan to ``matched`` with an
    # EAN, that user becomes the first discoverer if no one else already
    # is. Helper is idempotent + filters banned/deleted users itself.
    if merged_status == "matched" and merged_ean is not None and current.user_id is not None:
        claim_first_discovery(db, merged_ean, current.user_id)

    _audit(
        db,
        scan_id=scan_id,
        parsed_ticket_id=current.parsed_ticket_id,
        event="admin_scan_override",
        payload={
            "operator": operator,
            "diff": diff,
        },
    )

    db.commit()
    return {
        "scan_id": str(scan_id),
        "status": merged_status,
        "match_method": merged_method,
        "product_ean": merged_ean,
        "rejected_reason": merged_reason,
        "store_id": str(merged_store_id) if merged_store_id else None,
        "diff": diff,
    }


# ===========================================================================
# POST /admin/scans/{scan_id}/replay-match  — re-run Phase 3 on one scan
# ===========================================================================
@router.post(
    "/admin/scans/{scan_id}/replay-match",
    dependencies=[Depends(verify_admin_key)],
)
def post_replay_match(
    scan_id: uuid.UUID,
    x_admin_operator: str | None = Header(default=None, alias="X-Admin-Operator"),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Re-run Phase 3 on one scan with the current DB knowledge.

    Resolves the scan's parsed_item from ``parsed_tickets.parsed_jsonb``
    (matching on normalized_label + total_cents — the same fields
    persisted by Phase 4), reconstructs a Pydantic ``ParsedItem``, and
    invokes :func:`worker.pipeline.match._match_one_item` with
    DB-wired lookups. The new outcome UPDATEs the scan row.

    Synchronous — one item, lookups are fast (<200ms p99 in alpha).

    Errors :
        - 400 ``operator_required``
        - 403 ``forbidden`` — wrong / missing ADMIN_API_KEY
        - 404 ``scan_not_found``
        - 409 ``no_parsed_ticket`` — scan has no parsed_ticket_id (legacy v2 row)
        - 409 ``parsed_item_not_found`` — scan's normalized_label + price
          do not match any item in the persisted parsed_jsonb (data drift)
    """
    # Local import — keeps the route module light and avoids dragging the
    # worker namespace into FastAPI startup when ADMIN_API_KEY is unset.
    from worker.pipeline import match as match_mod
    from worker.pipeline.orchestrator import (
        _make_consensus_exact,
        _make_consensus_fuzzy,
        _make_product_by_ean,
        _make_product_by_knowledge,
        _make_retailer_resolver,
    )
    from worker.pipeline.types import ParsedItem

    operator = _require_operator(x_admin_operator)

    scan = db.execute(
        text("SELECT id, parsed_ticket_id, scanned_name, price, quantity, store_id FROM scans WHERE id = :sid"),
        {"sid": str(scan_id)},
    ).first()
    if scan is None:
        raise HTTPException(status_code=404, detail="scan_not_found")
    if scan.parsed_ticket_id is None:
        raise HTTPException(status_code=409, detail="no_parsed_ticket")

    pt = db.execute(
        text("SELECT parsed_jsonb FROM parsed_tickets WHERE id = :pt_id"),
        {"pt_id": str(scan.parsed_ticket_id)},
    ).first()
    if pt is None or not pt.parsed_jsonb:
        raise HTTPException(status_code=409, detail="parsed_ticket_missing")

    # Find the parsed_item whose normalized_label + total_cents match
    # what was persisted to scans. Phase 4 writes
    #   scans.scanned_name = parsed_item.normalized_label
    #   scans.price        = parsed_item.total_cents
    # so a single (label, price) lookup is unambiguous in practice.
    items_data = pt.parsed_jsonb.get("items", [])
    matching_item: dict[str, Any] | None = None
    for it in items_data:
        if it.get("normalized_label") == scan.scanned_name and it.get("total_cents") == scan.price:
            matching_item = it
            break
    if matching_item is None:
        raise HTTPException(status_code=409, detail="parsed_item_not_found")

    parsed_item = ParsedItem.model_validate(matching_item)

    # Bloc C : resolve retailer_id from the scan's store so the
    # consensus stages can run retailer-keyed. ``None`` is fine —
    # the cascade short-circuits to ``no_retailer_for_consensus``.
    retailer_resolver = _make_retailer_resolver(db)
    retailer_id = retailer_resolver(scan.store_id) if scan.store_id is not None else None

    item_match = match_mod._match_one_item(
        parsed_item,
        store_id=scan.store_id,
        retailer_id=retailer_id,
        product_by_ean=_make_product_by_ean(db),
        product_by_knowledge=_make_product_by_knowledge(db),
        consensus_exact=_make_consensus_exact(db),
        consensus_fuzzy=_make_consensus_fuzzy(db),
        audit_logger=lambda **kwargs: None,  # internal Phase-3 events stay quiet
        log_level="normal",
    )

    # Map ItemMatch.status / match_method back to the DB enum (same
    # convention as persist._SCAN_STATUS_MAP / _MATCH_METHOD_MAP).
    db_status = item_match.status  # 'matched' / 'unresolved' / 'rejected' all valid
    db_method = item_match.match_method  # 'barcode' / 'knowledge' / 'consensus_match' / None

    db.execute(
        text(
            "UPDATE scans SET "
            "  status = :status, "
            "  match_method = :method, "
            "  product_ean = :ean, "
            "  match_confidence = :confidence, "
            "  rejected_reason = :reason, "
            "  status_updated_at = now() "
            "WHERE id = :sid"
        ),
        {
            "sid": str(scan_id),
            "status": db_status,
            "method": db_method,
            "ean": item_match.product_ean,
            "confidence": item_match.match_confidence,
            "reason": item_match.rejected_reason,
        },
    )

    _audit(
        db,
        scan_id=scan_id,
        parsed_ticket_id=scan.parsed_ticket_id,
        event="admin_replay_match",
        payload={
            "operator": operator,
            "outcome": {
                "status": db_status,
                "match_method": db_method,
                "product_ean": item_match.product_ean,
                "match_confidence": item_match.match_confidence,
                "rejected_reason": item_match.rejected_reason,
            },
        },
    )

    db.commit()
    return {
        "scan_id": str(scan_id),
        "status": db_status,
        "match_method": db_method,
        "product_ean": item_match.product_ean,
        "match_confidence": item_match.match_confidence,
        "rejected_reason": item_match.rejected_reason,
    }
