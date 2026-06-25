"""Phase 4 — Persister.

Écrit en DB le résultat des 4 phases. Première et SEULE phase qui a un
side-effect DB explicite — les phases 1-3 sont pure-fonctionnelles via
callbacks injectés.

Steps (1 transaction unique — caller commits) :

  1. UPSERT ``parsed_tickets`` via ON CONFLICT (parsed_jsonb_hash).
     Idempotence : un re-run sur la même image renvoie le même
     ``parsed_jsonb_hash`` (cf. ARCH § Cardinal state) ; le DO UPDATE
     no-op permet le RETURNING dans tous les cas.

  2. UPSERT ``receipts`` (un par RawTicket, identifié par ``raw.receipt_id``).
     Set ``parsed_ticket_id`` ; si ``store_status='matched'`` set
     ``store_id`` ; ``store_status`` mappé v3→DB enum.

  3. INSERT ``scans[]`` — un par :class:`ItemMatch`. Mapping direct
     v3 → DB enum (matched / unresolved / rejected ; barcode / knowledge /
     consensus_match). ``parsed_ticket_id``, ``match_confidence`` et
     ``store_id`` (si store matché) propagés.

  4. INSERT ``store_candidates`` si ``store_status='suggested'``.
     ``occurrence_count`` part à 1 — la dédup est gérée hors-pipeline
     (cf. ARCH_store_validation).

  5. INSERT ``pipeline_audit_log`` final ``persist_completed``.

Anti-patterns interdits (cf. ARCH § Anti-patterns) :

- ❌ Drop silencieux d'un :class:`ItemMatch` : chaque ItemMatch produit
  exactement un scan. ``len(matched.item_matches) == n_scans_inserted``.
- ❌ Suppression / rollback partiel : si un INSERT échoue on lève
  :class:`PersistError` et le caller (orchestrator) rollback la
  transaction entière.
- ❌ Mutation d'un audit log existant — la table est append-only
  (trigger ``trg_pipeline_audit_log_no_update`` enforced en migration).

Cf. ``ARCH_receipt_pipeline.md`` § Phase 4 Persister + § Traçabilité.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from datetime import date as date_cls
from typing import Any
from uuid import UUID, uuid4

from ratis_core.products import claim_first_discovery
from ratis_core.settings import load_settings
from repositories.name_resolution_writes import record_resolution
from repositories.scan_repository import handle_barcode_rescan
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from worker.pipeline.fingerprint import (
    FingerprintComponents,
    compute_fp_global,
    compute_fp_user,
    validate_mandatory_signals,
)
from worker.pipeline.fingerprint_extract import (
    extract_components_from_pipeline_output,
)
from worker.pipeline.fraud_lookup import (
    check_cross_user_duplicate,
    check_device_pattern,
    fuzzy_match_intra_user,
)
from worker.pipeline.types import (
    ItemMatch,
    MatchedTicket,
    ParsedReceiptBarcode,
    ParsedTicket,
    RawTicket,
)

logger = logging.getLogger(__name__)


# ── Enum mappings v3 → DB ─────────────────────────────────────────────────


# v3 ItemMatch.status maps 1:1 to scans.status superset (cf. migration
# bloc 2 — `ck_scans_status_check_v3`). Explicit dict so future drift is
# surfaced as a KeyError, never silently coerced.
_SCAN_STATUS_MAP: dict[str, str] = {
    "matched": "matched",
    "unresolved": "unresolved",
    "rejected": "rejected",
}

_MATCH_METHOD_MAP: dict[str, str] = {
    "barcode": "barcode",
    "knowledge": "knowledge",
    "consensus_match": "consensus_match",
}

# v3 store_status maps to receipts/scans.store_status enum :
#   v3 'matched'    → 'confirmed'    (store_id set, trusted)
#   v3 'suggested'  → 'pending'      (StoreCandidate row created for review)
#   v3 'unresolved' → 'unknown'      (no store_id, awaiting reconciliation)
_STORE_STATUS_MAP: dict[str, str] = {
    "matched": "confirmed",
    "suggested": "pending",
    "unresolved": "unknown",
}


class PersistError(Exception):
    """Raised on any persistence failure — caller must rollback.

    Per ARCH § Anti-patterns, no silent drop : a partial write would
    leave a Receipt with mismatched parsed_ticket / scan rows. The
    orchestrator translates this into a transaction rollback so DB
    state stays coherent.
    """


def persist_pipeline_result(
    *,
    raw: RawTicket,
    parsed: ParsedTicket,
    matched: MatchedTicket,
    db: Session,
    user_id: UUID | None,
    log_level: str = "normal",
) -> dict[str, Any]:
    """Persist the full pipeline outcome. Idempotent via
    ``parsed_jsonb_hash``.

    Args:
        raw: Phase 1 output (image hash + OCR engine version).
        parsed: Phase 2 output (cardinal state — JSONB persisted as-is).
        matched: Phase 3 output (item matches + store match).
        db: SQLAlchemy session — caller commits.
        user_id: owner of the receipt / scans. ``None`` is allowed
            (anonymous reprocessing, batch jobs) — scans are then
            inserted with NULL user_id (FK is SET NULL on delete).
        log_level: filters the audit events (verbose < normal < production).
            Defaults to ``"normal"``.

    Returns:
        ``{"parsed_ticket_id": UUID, "receipt_id": UUID,
        "scan_ids": list[UUID], "store_candidate_id": UUID | None,
        "audit_event_count": int}``

    Raises:
        PersistError: on any DB anomaly (counts mismatch, INSERT
            failure, contract violation). Caller rolls back.
    """
    if parsed.parsed_jsonb_hash is None:
        raise PersistError(
            "ParsedTicket.parsed_jsonb_hash must be set before persistence "
            "(call .with_jsonb_hash() in Phase 2 before invoking persist)."
        )
    if len(matched.item_matches) != len(parsed.items):
        raise PersistError(
            f"item count mismatch : parsed={len(parsed.items)} vs "
            f"matched={len(matched.item_matches)} — invariant violated "
            "(no drop allowed between phases 2 and 3)."
        )

    audit_event_count = 0

    def _audit(
        event: str,
        *,
        level: str = "normal",
        payload: dict | None = None,
        parsed_ticket_id: UUID | None = None,
        scan_id: UUID | None = None,
    ) -> None:
        nonlocal audit_event_count
        if not _level_allows(log_level, level):
            return
        try:
            db.execute(
                text(
                    "INSERT INTO pipeline_audit_log "
                    "(parsed_ticket_id, scan_id, phase, level, event, payload) "
                    "VALUES (:pt, :scan, 'persist', :lvl, :event, "
                    "CAST(:payload AS jsonb))"
                ),
                {
                    "pt": parsed_ticket_id,
                    "scan": scan_id,
                    "lvl": level,
                    "event": event,
                    "payload": json.dumps(payload or {}),
                },
            )
            audit_event_count += 1
        except Exception:
            # Audit log is best-effort by ARCH discipline — do NOT block
            # the persist on an audit failure (e.g. trigger bug, schema
            # drift). Log a warning so the issue surfaces in metrics.
            logger.warning(
                "pipeline_audit_log insert failed (event=%s) — best-effort skip",
                event,
                exc_info=True,
            )

    # 0. Anti-doublon barcode rescan ───────────────────────────────────────
    # If the same physical receipt was already uploaded (its raw barcode
    # is set on a prior ``receipts`` row), supersede the prior receipt's
    # active scans and free the unique-index slot before our INSERT.
    barcode_raw = parsed.footer.barcode.raw if parsed.footer.barcode and parsed.footer.barcode.raw else None
    if barcode_raw:
        # KP-41 — two concurrent uploads of the same physical receipt both
        # reach ``handle_barcode_rescan`` ; with no prior receipt row the
        # ``with_for_update`` lock has nothing to grab, so both INSERTs race
        # and one violates ``uq_receipts_receipt_barcode`` → PersistError →
        # poisoned transaction → scan lost. Serialise the whole barcode
        # critical section on a transaction-scoped advisory lock keyed on
        # the barcode : the second transaction blocks here until the first
        # commits, then sees the prior receipt and rescans cleanly.
        db.execute(
            text("SELECT pg_advisory_xact_lock(hashtext(:b))"),
            {"b": barcode_raw},
        )
        handle_barcode_rescan(db=db, barcode=barcode_raw, new_receipt_id=raw.receipt_id)

    # 0b. Anti-fraud PR3 — compute fingerprint components + mandatory rule
    # check. When the ticket lacks the mandatory signals for a stable
    # fingerprint (no date, OR no brand/address pair) we short-circuit
    # the pipeline : INSERT a skeleton receipt + a single sentinel rejected
    # scan, and return early. Per ARCH § Pipeline POST /scan/receipt
    # — étape 4 (décisions actées 2026-05-11). The skeleton receipt has
    # fingerprint columns NULL so the UNIQUE partial index ``idx_receipts_fp_user``
    # (which gates on ``parse_fingerprint_user IS NOT NULL``) does not
    # fire, and ``parsed_ticket_id=NULL`` so we don't pollute the
    # parsed_tickets cardinal store with an audit-only payload.
    #
    # When ``receipt_barcode`` is set (DA-18 path) the fingerprint
    # mechanism is bypassed entirely — the barcode is a stronger signal
    # than the OCR-derived fingerprint, so we skip the hard-rule check
    # and let the existing barcode UNIQUE index drive dedup. This mirrors
    # the partial-index WHERE clauses (``WHERE receipt_barcode IS NULL``).
    components: FingerprintComponents | None = None
    fuzzy_canonical_id: UUID | None = None
    fuzzy_signal_payload: dict | None = None
    if barcode_raw is None:
        components = extract_components_from_pipeline_output(parsed=parsed, matched=matched)
        valid, mandatory_reason = validate_mandatory_signals(components)
        if not valid:
            sentinel_scan_id = _insert_mandatory_signals_reject(
                db=db,
                raw=raw,
                user_id=user_id,
                reason=mandatory_reason or "unknown",
            )
            _audit(
                "receipt_rejected_missing_mandatory_signals",
                payload={
                    "receipt_id": str(raw.receipt_id),
                    "reason": mandatory_reason,
                    "scan_id": str(sentinel_scan_id),
                },
            )
            return {
                "parsed_ticket_id": None,
                "receipt_id": raw.receipt_id,
                "scan_ids": [sentinel_scan_id],
                "store_candidate_id": None,
                "audit_event_count": audit_event_count,
            }

        # ── 0c. Anti-fraud PR4 — pre-INSERT cheap rejects ────────────────
        # The age + daily-cap-hard checks short-circuit the rest of the
        # pipeline with a sentinel rejected scan (similar to the
        # mandatory-signals path above). These are *mechanical* rejects
        # — they do NOT INSERT a ``fraud_suspicion`` row (no user intent
        # to defraud is implied by "ticket too old" or "burst-uploading").
        af_cfg = _load_anti_fraud_cfg()
        age_reject_reason = _check_ticket_age_reject(parsed)
        if age_reject_reason is not None:
            sentinel_scan_id = _insert_anti_fraud_reject(
                db=db,
                raw=raw,
                user_id=user_id,
                reason=age_reject_reason,
            )
            _audit(
                "receipt_rejected_anti_fraud",
                payload={
                    "receipt_id": str(raw.receipt_id),
                    "reason": age_reject_reason,
                    "scan_id": str(sentinel_scan_id),
                },
            )
            return {
                "parsed_ticket_id": None,
                "receipt_id": raw.receipt_id,
                "scan_ids": [sentinel_scan_id],
                "store_candidate_id": None,
                "audit_event_count": audit_event_count,
            }

        daily_count = _count_user_receipts_last_24h(db, user_id)
        cap_hard = int(af_cfg.get("receipts_max_per_day_per_user", 14))
        if user_id is not None and daily_count >= cap_hard:
            sentinel_scan_id = _insert_anti_fraud_reject(
                db=db,
                raw=raw,
                user_id=user_id,
                reason="daily_cap_exceeded",
            )
            _audit(
                "receipt_rejected_anti_fraud",
                payload={
                    "receipt_id": str(raw.receipt_id),
                    "reason": "daily_cap_exceeded",
                    "current_count": daily_count,
                    "cap": cap_hard,
                    "scan_id": str(sentinel_scan_id),
                },
            )
            return {
                "parsed_ticket_id": None,
                "receipt_id": raw.receipt_id,
                "scan_ids": [sentinel_scan_id],
                "store_candidate_id": None,
                "audit_event_count": audit_event_count,
            }

        # ── 0d. Anti-fraud PR4 — fuzzy intra-user fallback (étape 8) ─────
        # The strict UNIQUE INDEX ``idx_receipts_fp_user`` (PR3) catches
        # exact-fingerprint rescans inside ``_upsert_receipt``. The fuzzy
        # fallback runs BEFORE the INSERT attempt so the canonical receipt
        # absorbs the new scans without an aborted INSERT polluting the
        # transaction. When fuzzy matches, we skip the INSERT entirely
        # and use the canonical receipt id for the rest of the pipeline.
        if user_id is not None:
            fuzzy = fuzzy_match_intra_user(
                db,
                components=components,
                user_id=user_id,
                window_hours=int(af_cfg.get("fp_window_hours", 48)),
                threshold=int(af_cfg.get("fp_fuzzy_match_threshold", 8)),
            )
            if fuzzy is not None:
                fuzzy_canonical_id = _consolidate_via_fuzzy_match(
                    db=db,
                    canonical_id=fuzzy.existing_receipt_id,
                    new_receipt_id=raw.receipt_id,
                )
                fuzzy_signal_payload = {
                    "canonical_id": str(fuzzy.existing_receipt_id),
                    "exact_matches": fuzzy.exact_matches,
                    "lev_tolerance_used": fuzzy.lev_tolerance_used,
                }

    # 1. UPSERT receipts FIRST (parsed_tickets has FK → receipts) ─────────
    # The FK ``parsed_tickets.receipt_id → receipts(id) ON DELETE CASCADE``
    # is enforced at INSERT time, so the parent row must exist before the
    # child. We initialise the receipt with parsed_ticket_id=NULL ; step 2
    # below upserts the parsed ticket and step 3 sets the FK back.
    #
    # Anti-fraud PR3 : compute fp_user / fp_global / time_precision and
    # pass them to ``_upsert_receipt`` for the new fingerprint columns.
    # When ``receipt_barcode`` is NULL and a rescan-by-fingerprint
    # collision occurs (UNIQUE partial ``idx_receipts_fp_user`` fires),
    # ``_upsert_receipt`` redirects the INSERT to an UPDATE on the
    # pre-existing receipt and returns its id ; scans then attach to that
    # canonical receipt rather than the would-be new one.
    db_store_status = _STORE_STATUS_MAP[matched.store_status]
    store_id = matched.store_match_id if matched.store_status == "matched" else None

    fp_user: str | None = None
    fp_global: str | None = None
    if barcode_raw is None and components is not None:
        fp_user = compute_fp_user(components, str(user_id) if user_id else "")
        fp_global = compute_fp_global(components)

    if fuzzy_canonical_id is not None:
        # Fuzzy intra-user match folded the upload into a canonical
        # receipt above. Skip the INSERT entirely — the canonical row
        # already carries the parsed signal ; we just attach the scans
        # to it via ``receipt_id = fuzzy_canonical_id``.
        receipt_id = fuzzy_canonical_id
    else:
        receipt_id = _upsert_receipt(
            db=db,
            raw_receipt_id=raw.receipt_id,
            user_id=user_id,
            parsed=parsed,
            parsed_ticket_id=None,  # set in step 3 after parsed_ticket exists
            store_id=store_id,
            store_status=db_store_status,
            fingerprint_components=components,
            fp_user=fp_user,
            fp_global=fp_global,
        )

    # 2. UPSERT parsed_tickets ─────────────────────────────────────────────
    # When a fingerprint-rescan consolidated into a canonical receipt
    # (cf. anti-fraud PR3), ``receipt_id`` differs from ``raw.receipt_id``
    # — we pass the canonical id so the parsed_ticket FK lands on the
    # right row (the would-be new id was never INSERTed).
    parsed_ticket_id = _upsert_parsed_ticket(parsed, raw, db, receipt_id=receipt_id)

    # 3. Backfill receipts.parsed_ticket_id ────────────────────────────────
    db.execute(
        text("UPDATE receipts SET parsed_ticket_id = :pt, updated_at = now() WHERE id = :rid"),
        {"pt": parsed_ticket_id, "rid": receipt_id},
    )

    _audit(
        "parsed_ticket_persisted",
        parsed_ticket_id=parsed_ticket_id,
        payload={
            "parsed_ticket_id": str(parsed_ticket_id),
            "parsed_jsonb_hash": parsed.parsed_jsonb_hash,
            "image_hash": raw.image_hash,
        },
    )
    if barcode_raw:
        bc = parsed.footer.barcode
        _audit(
            "barcode_persisted",
            parsed_ticket_id=parsed_ticket_id,
            payload={
                "retailer_key": bc.retailer_key if bc else None,
                "store_code": bc.store_code if bc else None,
                "has_date": bool(bc and bc.date is not None),
                "has_time": bool(bc and bc.time is not None),
            },
        )

    # ── 0e. Anti-fraud PR4 — post-INSERT cross-user check (étape 7) ─────
    # Only runs on the no-barcode path AND when no fuzzy/UNIQUE-collision
    # consolidated into a pre-existing row (a fuzzy or exact intra-user
    # match means the new upload is already absorbed — no fresh cross-
    # user signal to compute). The check uses the current receipt's
    # ``fp_global`` and ``time_precision`` to decide between strict
    # reject and flag-only review.
    if barcode_raw is None and fp_global is not None and fuzzy_canonical_id is None and user_id is not None:
        time_precision_self = components.time_precision if components is not None else None
        cross_verdict = check_cross_user_duplicate(
            db,
            fp_global=fp_global,
            time_precision_self=time_precision_self,
            scanned_at=raw.captured_at or datetime.now(tz=None).astimezone(),
            current_user_id=user_id,
            window_hours=int(af_cfg.get("fp_window_hours", 48)),
        )
        if cross_verdict.kind == "second_strict":
            # Strict cross-user duplicate → REJECT the scan + INSERT
            # ``fp_global_strict`` fraud_suspicion. The receipt row stays
            # in DB for audit trail (admin queue surfaces both sides via
            # ``evidence_receipt_ids``) but no item-level scans are
            # produced — a single sentinel rejected scan replaces them.
            _insert_fraud_suspicion(
                db=db,
                receipt_id=receipt_id,
                evidence_receipt_ids=[cross_verdict.matched_receipt_id],
                signal="fp_global_strict",
            )
            sentinel_scan_id = uuid4()
            db.execute(
                text(
                    "INSERT INTO scans "
                    "(id, user_id, store_id, store_status, product_ean, "
                    " scanned_name, price, quantity, scan_type, "
                    " receipt_id, status, match_method, match_confidence, "
                    " rejected_reason, scanned_at, status_updated_at, "
                    " parsed_ticket_id) "
                    "VALUES (:id, :user, NULL, 'unknown', NULL, '', "
                    "        0, 1, 'receipt', :receipt, 'rejected', NULL, "
                    "        NULL, :reason, now(), now(), :pt)"
                ),
                {
                    "id": sentinel_scan_id,
                    "user": user_id,
                    "receipt": receipt_id,
                    "reason": "duplicate_cross_user_strict",
                    "pt": parsed_ticket_id,
                },
            )
            _audit(
                "receipt_rejected_cross_user_strict",
                parsed_ticket_id=parsed_ticket_id,
                payload={
                    "receipt_id": str(receipt_id),
                    "matched_receipt_id": str(cross_verdict.matched_receipt_id),
                    "matched_user_id": (str(cross_verdict.matched_user_id) if cross_verdict.matched_user_id else None),
                    "scan_id": str(sentinel_scan_id),
                },
            )
            return {
                "parsed_ticket_id": parsed_ticket_id,
                "receipt_id": receipt_id,
                "scan_ids": [sentinel_scan_id],
                "store_candidate_id": None,
                "audit_event_count": audit_event_count,
            }
        if cross_verdict.kind == "minute":
            # Flag-only — the scan continues through the normal flow but
            # the admin queue sees a ``fp_global_minute`` row.
            _insert_fraud_suspicion(
                db=db,
                receipt_id=receipt_id,
                evidence_receipt_ids=[cross_verdict.matched_receipt_id],
                signal="fp_global_minute",
            )
            _audit(
                "fraud_suspicion_inserted_fp_global_minute",
                parsed_ticket_id=parsed_ticket_id,
                payload={
                    "receipt_id": str(receipt_id),
                    "matched_receipt_id": str(cross_verdict.matched_receipt_id),
                },
            )

    # ── 0f. Anti-fraud PR4 — device-shared pattern (étape 11) ───────────
    # The check is best-effort : ``device_fingerprint`` is populated by
    # an upstream upload context which is not yet wired in V3 (PR5
    # concern), so this lookup is typically a no-op today. When the
    # column is populated and > N distinct users share it, we INSERT a
    # ``device_shared`` fraud_suspicion. **No trust_score mutation** is
    # performed here — ``trust_score`` is batch-recomputed nightly from
    # ``product_name_resolutions`` per ARCH_anti_fraud.md ; an in-band
    # penalty would conflict with that ownership. The admin queue row
    # is the durable signal ; PR5 (or a dedicated ARCH update) will
    # decide whether to add a direct penalty path.
    if barcode_raw is None and user_id is not None:
        device_fp = db.execute(
            text("SELECT device_fingerprint FROM receipts WHERE id = :id"),
            {"id": receipt_id},
        ).scalar()
        device_verdict = check_device_pattern(
            db,
            device_fingerprint=device_fp,
            current_user_id=user_id,
            window_days=int(af_cfg.get("device_fp_window_days", 30)),
            distinct_users_threshold=int(af_cfg.get("device_fp_distinct_users_threshold", 3)),
        )
        if device_verdict.kind == "shared":
            _insert_fraud_suspicion(
                db=db,
                receipt_id=receipt_id,
                evidence_receipt_ids=[],
                signal="device_shared",
            )
            _audit(
                "fraud_suspicion_inserted_device_shared",
                parsed_ticket_id=parsed_ticket_id,
                payload={
                    "receipt_id": str(receipt_id),
                    "distinct_user_count": device_verdict.distinct_user_count,
                    # TODO(PR5+): apply trust_score penalty via a
                    # dedicated helper if/when product decides on
                    # an in-band path (cf ARCH_anti_fraud.md §
                    # "Définition du trust_score" — batch ownership).
                    "trust_penalty_deferred": True,
                },
            )

    # ── 0g. Anti-fraud PR4 — daily soft cap (étape 9 soft warn) ─────────
    # Re-uses the count from the hard-cap check. Fires when
    # ``soft_warn ≤ count < cap_hard`` — non-blocking, the admin queue
    # surfaces the pattern for review. The count at this point is the
    # pre-INSERT count ; we are about to add 1 receipt so the boundary
    # to evaluate is the post-INSERT count : ``count + 1``.
    if barcode_raw is None and fuzzy_canonical_id is None and user_id is not None:
        soft_warn = int(af_cfg.get("receipts_soft_warn_per_day", 7))
        post_count = daily_count + 1  # current upload counts toward the day
        if soft_warn <= post_count < cap_hard:
            _insert_fraud_suspicion(
                db=db,
                receipt_id=receipt_id,
                evidence_receipt_ids=[],
                signal="daily_soft_burst",
            )
            _audit(
                "fraud_suspicion_inserted_daily_soft_burst",
                parsed_ticket_id=parsed_ticket_id,
                payload={
                    "receipt_id": str(receipt_id),
                    "current_count": post_count,
                    "soft_warn_threshold": soft_warn,
                },
            )

    # Emit the fuzzy-consolidation audit AFTER cross-user/device/cap so
    # the timeline reads naturally (fuzzy fold → policy checks all
    # already done by the time the operator looks).
    if fuzzy_signal_payload is not None:
        _audit(
            "receipt_consolidated_fuzzy_intra_user",
            parsed_ticket_id=parsed_ticket_id,
            payload={
                **fuzzy_signal_payload,
                "new_receipt_id": str(raw.receipt_id),
            },
        )

    # 3. INSERT scans[] (1 per ItemMatch) ──────────────────────────────────
    parsed_items_by_id = {it.id: it for it in parsed.items}
    scan_ids: list[UUID] = []
    for item_match in matched.item_matches:
        parsed_item = parsed_items_by_id.get(item_match.parsed_item_id)
        if parsed_item is None:
            raise PersistError(
                f"ItemMatch references parsed_item_id={item_match.parsed_item_id} "
                "which does not exist in the ParsedTicket — invariant violated."
            )
        scan_id = _insert_scan(
            db=db,
            user_id=user_id,
            store_id=store_id,
            store_status=db_store_status,
            receipt_id=receipt_id,
            parsed_ticket_id=parsed_ticket_id,
            parsed_item=parsed_item,
            item_match=item_match,
        )
        scan_ids.append(scan_id)
        _audit(
            "scan_persisted",
            level="verbose",
            parsed_ticket_id=parsed_ticket_id,
            scan_id=scan_id,
            payload={
                "scan_id": str(scan_id),
                "status": item_match.status,
                "match_method": item_match.match_method,
                "rejected_reason": item_match.rejected_reason,
            },
        )

    if len(scan_ids) != len(matched.item_matches):
        raise PersistError(
            f"scan insert count mismatch : expected {len(matched.item_matches)}, "
            f"got {len(scan_ids)} — invariant violated."
        )

    # 4. INSERT store_candidates if store_status='suggested' ───────────────
    store_candidate_id: UUID | None = None
    if matched.store_status == "suggested":
        store_candidate_id = _insert_store_candidate(
            db=db,
            parsed=parsed,
            receipt_id=receipt_id,
        )
        _audit(
            "store_candidate_persisted",
            parsed_ticket_id=parsed_ticket_id,
            payload={
                "store_candidate_id": str(store_candidate_id),
                "store_status": matched.store_status,
            },
        )

    # 5. Final audit event ─────────────────────────────────────────────────
    _audit(
        "persist_completed",
        parsed_ticket_id=parsed_ticket_id,
        payload={
            "receipt_id": str(receipt_id),
            "parsed_ticket_id": str(parsed_ticket_id),
            "scan_count": len(scan_ids),
            "store_status": matched.store_status,
            "matched_count": sum(1 for m in matched.item_matches if m.status == "matched"),
        },
    )

    return {
        "parsed_ticket_id": parsed_ticket_id,
        "receipt_id": receipt_id,
        "scan_ids": scan_ids,
        "store_candidate_id": store_candidate_id,
        "audit_event_count": audit_event_count,
    }


# ── Private helpers ───────────────────────────────────────────────────────


_LEVEL_RANK: dict[str, int] = {"production": 0, "normal": 1, "verbose": 2}


def _level_allows(current: str, event_level: str) -> bool:
    """Return True if an event of ``event_level`` should be emitted given
    ``current`` log_level. Stricter levels filter more events :

      ``production`` keeps only ``production``-tagged events
      ``normal``     keeps ``production`` + ``normal``
      ``verbose``    keeps everything
    """
    return _LEVEL_RANK.get(event_level, 1) <= _LEVEL_RANK.get(current, 1)


def _upsert_parsed_ticket(
    parsed: ParsedTicket,
    raw: RawTicket,
    db: Session,
    *,
    receipt_id: UUID | None = None,
) -> UUID:
    """INSERT parsed_tickets ON CONFLICT (parsed_jsonb_hash) DO NOTHING.

    On hit (same image, same parse) we re-SELECT the existing row's id —
    idempotent re-run guarantee.

    ``receipt_id`` defaults to ``raw.receipt_id`` for the clean path ;
    callers pass an explicit value when a fingerprint-rescan
    consolidation redirected the receipt INSERT to a pre-existing
    canonical row (cf. :func:`_consolidate_rescan_into_existing`).
    """
    parsed_jsonb_text = json.dumps(parsed.model_dump(mode="json"), sort_keys=True)
    target_receipt_id = receipt_id if receipt_id is not None else raw.receipt_id
    try:
        row = db.execute(
            text(
                "INSERT INTO parsed_tickets "
                "(id, receipt_id, parsed_jsonb, parsed_jsonb_hash, "
                " raw_ticket_image_hash, ocr_engine_version, captured_at) "
                "VALUES (:id, :receipt_id, CAST(:jsonb AS jsonb), :hash, "
                "        :image_hash, :engine, :captured_at) "
                "ON CONFLICT (parsed_jsonb_hash) DO NOTHING "
                "RETURNING id"
            ),
            {
                "id": parsed.id,
                "receipt_id": target_receipt_id,
                "jsonb": parsed_jsonb_text,
                "hash": parsed.parsed_jsonb_hash,
                "image_hash": raw.image_hash,
                "engine": raw.ocr_engine_version,
                "captured_at": raw.captured_at,
            },
        ).first()
        if row is not None:
            return row.id
        # ON CONFLICT DO NOTHING returned no row — the row pre-existed.
        # Re-fetch by hash (UNIQUE) to recover its id.
        existing = db.execute(
            text("SELECT id FROM parsed_tickets WHERE parsed_jsonb_hash = :hash"),
            {"hash": parsed.parsed_jsonb_hash},
        ).first()
        if existing is None:
            raise PersistError(
                f"parsed_tickets row vanished between INSERT ON CONFLICT and "
                f"recovery SELECT (hash={parsed.parsed_jsonb_hash!r})"
            )
        return existing.id
    except PersistError:
        raise
    except Exception as exc:
        raise PersistError(f"parsed_tickets upsert failed: {exc}") from exc


def _resolve_purchased_at(
    barcode: ParsedReceiptBarcode | None,
    ocr_date: datetime | None,
) -> tuple[date_cls | None, datetime | None]:
    """Return ``(purchased_at_date, purchased_at_with_time)``.

    Priority for ``purchased_at`` (date column) :

    1. ``barcode.date`` — most authoritative (encoded by the till at
       transaction time).
    2. ``ocr_date.date()`` — derived by Phase 2 from the receipt body.
    3. ``None`` — caller substitutes :data:`SENTINEL_DATE` 1970-01-01.

    For ``purchased_at_with_time`` we combine the chosen date with
    ``barcode.time`` when both are present (most useful for receipts_semantic_dedup_key
    UNIQUE index). OCR-only path doesn't yield a precise time so we
    leave ``purchased_at_with_time = NULL`` rather than fabricate one.
    """
    chosen_date: date_cls | None = None
    purchased_with_time: datetime | None = None

    if barcode is not None and barcode.date is not None:
        chosen_date = barcode.date
        if barcode.time is not None:
            # Naive datetime — the column type is ``timestamp(0) without
            # time zone`` per schema (wall-clock local at the till).
            purchased_with_time = datetime.combine(barcode.date, barcode.time)
    elif ocr_date is not None:
        chosen_date = ocr_date.date()

    return chosen_date, purchased_with_time


def _build_barcode_fields_jsonb(
    barcode: ParsedReceiptBarcode | None,
) -> str | None:
    """Serialize the decoded barcode fields as canonical JSON for jsonb storage.

    Excludes ``raw`` (lives in its own ``receipts.receipt_barcode`` column —
    do not duplicate). Returns ``None`` when ``barcode`` itself is None
    so the column stays NULL.
    """
    if barcode is None:
        return None
    payload = barcode.model_dump(mode="json", exclude={"raw"})
    return json.dumps(payload, sort_keys=True)


def _upsert_receipt(
    *,
    db: Session,
    raw_receipt_id: UUID,
    user_id: UUID | None,
    parsed: ParsedTicket,
    parsed_ticket_id: UUID | None,
    store_id: UUID | None,
    store_status: str,
    fingerprint_components: FingerprintComponents | None = None,
    fp_user: str | None = None,
    fp_global: str | None = None,
) -> UUID:
    """INSERT ON CONFLICT (id) DO UPDATE — idempotent receipt creation.

    Re-running the pipeline on the same ``raw.receipt_id`` updates the
    receipt's parsed_ticket_id / store_id / store_status / barcode fields
    without duplicating the row. ``purchased_at`` is resolved via
    :func:`_resolve_purchased_at` (barcode date > OCR date > SENTINEL).

    Anti-fraud PR3 — when ``fp_user`` / ``fp_global`` are provided
    (only on the no-barcode path — DA-18 already gates by barcode UNIQUE),
    they are written to the new ``parse_fingerprint_user`` /
    ``parse_fingerprint_global`` columns. The 10 components are also
    persisted in ``fingerprint_components_jsonb`` for forensic replay.

    Collision on the UNIQUE partial ``idx_receipts_fp_user`` (a same-user
    rescan with no ticket barcode) triggers the **rescan consolidation
    flow** : we ROLLBACK the savepoint, look up the canonical
    pre-existing receipt by ``parse_fingerprint_user``, append
    ``raw_receipt_id`` to its ``consolidated_from_ids`` array, refresh
    its denormalised fields (total / store_id / store_status), and
    return the canonical receipt id so the caller's scans attach to
    that row. This mirrors DA-18's barcode rescan pattern without a
    barcode (cf. ARCH § étape 6).
    """
    barcode = parsed.footer.barcode if parsed.footer else None
    barcode_raw = barcode.raw if (barcode and barcode.raw) else None
    barcode_fields_json = _build_barcode_fields_jsonb(barcode)
    chosen_date, purchased_with_time = _resolve_purchased_at(barcode, parsed.purchased_at)
    sentinel_date = "1970-01-01"
    total_amount = parsed.footer.total_cents
    components_json: str | None = None
    time_precision: str | None = None
    if fingerprint_components is not None:
        # Persist only when at least one component is non-null — avoid
        # writing an empty JSON object that would surface as misleading
        # forensic data in the admin queue.
        comp_dict = {k: v for k, v in fingerprint_components.__dict__.items() if v is not None}
        if comp_dict:
            components_json = json.dumps(comp_dict, sort_keys=True)
        time_precision = fingerprint_components.time_precision

    params = {
        "id": raw_receipt_id,
        "user": user_id,
        "store": store_id,
        "purchased": chosen_date,
        "purchased_with_time": purchased_with_time,
        "sentinel": sentinel_date,
        "total": total_amount,
        "store_status": store_status,
        "pt": parsed_ticket_id,
        "receipt_barcode": barcode_raw,
        "barcode_fields": barcode_fields_json,
        "fp_user": fp_user,
        "fp_global": fp_global,
        "components_jsonb": components_json,
        "time_precision": time_precision,
    }
    sql = text(
        "INSERT INTO receipts "
        "(id, user_id, store_id, purchased_at, purchased_at_with_time, "
        " total_amount, store_status, parsed_ticket_id, "
        " receipt_barcode, barcode_fields, "
        " parse_fingerprint_user, parse_fingerprint_global, "
        " fingerprint_components_jsonb, time_precision) "
        "VALUES (:id, :user, :store, "
        "        COALESCE(CAST(:purchased AS date), CAST(:sentinel AS date)), "
        "        CAST(:purchased_with_time AS timestamp(0)), "
        "        :total, :store_status, :pt, "
        "        :receipt_barcode, "
        "        CASE WHEN CAST(:barcode_fields AS text) IS NOT NULL "
        "             THEN CAST(:barcode_fields AS jsonb) ELSE NULL END, "
        "        :fp_user, :fp_global, "
        "        CASE WHEN CAST(:components_jsonb AS text) IS NOT NULL "
        "             THEN CAST(:components_jsonb AS jsonb) ELSE NULL END, "
        "        :time_precision) "
        "ON CONFLICT (id) DO UPDATE SET "
        "  parsed_ticket_id = EXCLUDED.parsed_ticket_id, "
        "  store_id = EXCLUDED.store_id, "
        "  store_status = EXCLUDED.store_status, "
        "  total_amount = EXCLUDED.total_amount, "
        "  receipt_barcode = EXCLUDED.receipt_barcode, "
        "  barcode_fields = EXCLUDED.barcode_fields, "
        "  purchased_at = EXCLUDED.purchased_at, "
        "  purchased_at_with_time = EXCLUDED.purchased_at_with_time, "
        "  parse_fingerprint_user = EXCLUDED.parse_fingerprint_user, "
        "  parse_fingerprint_global = EXCLUDED.parse_fingerprint_global, "
        "  fingerprint_components_jsonb = EXCLUDED.fingerprint_components_jsonb, "
        "  time_precision = EXCLUDED.time_precision, "
        "  updated_at = now()"
    )

    # If we have a fingerprint, wrap the INSERT in a savepoint so a
    # UNIQUE-violation on ``idx_receipts_fp_user`` (rescan-by-fingerprint)
    # can be caught and converted into the rescan consolidation path
    # without aborting the outer transaction. Without the savepoint, the
    # whole pipeline transaction would be poisoned and the orchestrator
    # would rollback — losing the rescan signal entirely.
    if fp_user is not None:
        try:
            with db.begin_nested():
                db.execute(sql, params)
        except IntegrityError as exc:
            if "idx_receipts_fp_user" in str(exc.orig):
                return _consolidate_rescan_into_existing(
                    db=db,
                    fp_user=fp_user,
                    new_receipt_id=raw_receipt_id,
                    user_id=user_id,
                    store_id=store_id,
                    store_status=store_status,
                    total_amount=total_amount,
                    chosen_date=chosen_date,
                    purchased_with_time=purchased_with_time,
                    fp_global=fp_global,
                    components_json=components_json,
                    time_precision=time_precision,
                )
            raise PersistError(f"receipts upsert failed: {exc}") from exc
        except Exception as exc:
            raise PersistError(f"receipts upsert failed: {exc}") from exc
    else:
        try:
            db.execute(sql, params)
        except Exception as exc:
            raise PersistError(f"receipts upsert failed: {exc}") from exc
    return raw_receipt_id


def _consolidate_rescan_into_existing(
    *,
    db: Session,
    fp_user: str,
    new_receipt_id: UUID,
    user_id: UUID | None,
    store_id: UUID | None,
    store_status: str,
    total_amount: int | None,
    chosen_date: date_cls | None,
    purchased_with_time: datetime | None,
    fp_global: str | None,
    components_json: str | None,
    time_precision: str | None,
) -> UUID:
    """Resolve a UNIQUE-collision on ``idx_receipts_fp_user`` by folding
    the new upload into the canonical pre-existing receipt.

    Steps :

    1. SELECT the canonical receipt by ``parse_fingerprint_user`` (the
       partial index guarantees at most one row when
       ``receipt_barcode IS NULL``).
    2. UPDATE its ``consolidated_from_ids`` array — append the
       new ``raw_receipt_id``. The array starts NULL and is upgraded to
       ``ARRAY[new_id]`` on first append, then ``array_append``-ed on
       subsequent rescans.
    3. Refresh denormalised header fields (total / store / dates /
       global fp / components / time_precision) — the latest upload
       likely has cleaner OCR or a confirmed store, so prefer the new
       data over the stale one.
    4. Return the canonical receipt id so the caller's scans attach to
       it rather than the would-be new row.

    Cf. ARCH § étape 6 — "UPDATE le receipt cible (au lieu d'INSERT new),
    Append le nouveau scan dans consolidated_from_ids[]".
    """
    row = db.execute(
        text("SELECT id FROM receipts WHERE parse_fingerprint_user = :fp   AND receipt_barcode IS NULL LIMIT 1"),
        {"fp": fp_user},
    ).first()
    if row is None:
        # Shouldn't happen — the UNIQUE collision says exactly one row
        # holds that fingerprint. If we lose the race (concurrent
        # delete), surface a PersistError rather than masking the
        # anomaly silently.
        raise PersistError(
            f"rescan consolidation lookup failed : fp_user={fp_user!r} "
            "matched UNIQUE index but no row visible — concurrent delete?"
        )
    canonical_id: UUID = row.id
    try:
        db.execute(
            text(
                "UPDATE receipts SET "
                "  consolidated_from_ids = COALESCE(consolidated_from_ids, "
                "                                   ARRAY[]::uuid[]) "
                "                          || ARRAY[CAST(:new_id AS uuid)], "
                "  user_id = COALESCE(:user, user_id), "
                "  store_id = COALESCE(:store, store_id), "
                "  store_status = :store_status, "
                "  total_amount = COALESCE(:total, total_amount), "
                "  purchased_at = COALESCE(CAST(:purchased AS date), purchased_at), "
                "  purchased_at_with_time = COALESCE("
                "      CAST(:purchased_with_time AS timestamp(0)), "
                "      purchased_at_with_time), "
                "  parse_fingerprint_global = COALESCE(:fp_global, "
                "                                      parse_fingerprint_global), "
                "  fingerprint_components_jsonb = COALESCE("
                "      CASE WHEN CAST(:components_jsonb AS text) IS NOT NULL "
                "           THEN CAST(:components_jsonb AS jsonb) "
                "           ELSE NULL END, "
                "      fingerprint_components_jsonb), "
                "  time_precision = COALESCE(:time_precision, time_precision), "
                "  updated_at = now() "
                "WHERE id = :canonical_id"
            ),
            {
                "new_id": new_receipt_id,
                "user": user_id,
                "store": store_id,
                "store_status": store_status,
                "total": total_amount,
                "purchased": chosen_date,
                "purchased_with_time": purchased_with_time,
                "fp_global": fp_global,
                "components_jsonb": components_json,
                "time_precision": time_precision,
                "canonical_id": canonical_id,
            },
        )
    except Exception as exc:
        raise PersistError(f"rescan consolidation UPDATE failed (canonical={canonical_id}): {exc}") from exc
    return canonical_id


def _load_anti_fraud_cfg() -> dict[str, Any]:
    """Return the ``pipeline.anti_fraud`` section from settings, or {}.

    Wrapped here so the helper is mockable in tests and a single
    log-and-degrade path covers a malformed settings file. Anti-fraud
    is best-effort by design (cf ARCH § "Réconciliation tickets V1" —
    fail-safe contract on the V3 hot path) ; an empty dict makes every
    threshold lookup fall through to its caller-side default and
    effectively disables the checks rather than crashing the pipeline.
    """
    try:
        s = load_settings()
        return s.get("pipeline", {}).get("anti_fraud", {}) or {}
    except Exception:
        logger.warning(
            "anti-fraud PR4: load_settings failed — degrading to empty cfg",
            exc_info=True,
        )
        return {}


def _check_ticket_age_reject(parsed: ParsedTicket) -> str | None:
    """Return ``"receipt_too_old"`` when the ticket's date is older than
    ``consensus.ticket_max_age_days`` (re-uses the existing 7-day cap —
    cf R19 / R28 : ne pas dupliquer un settings key existant).

    Returns ``None`` when the date is in window (or when ``purchased_at``
    is missing — the mandatory-signals check upstream already enforces
    a non-null date for the no-barcode path, so this branch is defensive).

    The settings key lives under ``consensus`` for historical reasons :
    the same 7-day boundary is the price-consensus cutoff (cf
    ``scan_repository.upsert_price_consensus``). PR4 reuses it as the
    pipeline-level reject cap per ARCH § étape 10.
    """
    if parsed.purchased_at is None:
        return None
    try:
        s = load_settings()
        max_age_days = int(s["consensus"]["ticket_max_age_days"])
    except Exception:
        logger.warning(
            "anti-fraud PR4: ticket_max_age_days unavailable — skipping age reject",
            exc_info=True,
        )
        return None

    now = datetime.now(UTC).date()
    ticket_date = parsed.purchased_at.date() if hasattr(parsed.purchased_at, "date") else parsed.purchased_at
    age_days = (now - ticket_date).days
    if age_days > max_age_days:
        return "receipt_too_old"
    return None


def _count_user_receipts_last_24h(db: Session, user_id: UUID | None) -> int:
    """Count receipts owned by ``user_id`` created in the last 24h.

    Used for both the hard daily cap (REJECT when count ≥ cap) and the
    soft burst flag (INSERT fraud_suspicion when soft ≤ count+1 < hard).
    Returns 0 when ``user_id`` is None (anonymous reprocess / batch —
    no per-user accounting applies).
    """
    if user_id is None:
        return 0
    row = db.execute(
        text("SELECT COUNT(*) AS n FROM receipts WHERE user_id = :uid   AND created_at > now() - interval '24 hours'"),
        {"uid": user_id},
    ).first()
    return int(row.n) if row is not None else 0


def _insert_anti_fraud_reject(
    *,
    db: Session,
    raw: RawTicket,
    user_id: UUID | None,
    reason: str,
) -> UUID:
    """Persist a skeleton receipt + sentinel rejected scan for an
    anti-fraud mechanical reject (age, daily cap). Mirrors
    :func:`_insert_mandatory_signals_reject` shape — the receipt is
    minimal (no parsed_ticket_id, no fingerprints) so it doesn't
    pollute the dedup pool. The scan carries the precise
    ``rejected_reason`` for the user-history endpoint.

    No ``fraud_suspicions`` row is INSERTed here — age / cap rejections
    are *mechanical* (no user intent to defraud implied) and don't
    belong in the admin queue. The receipt + rejected scan is the audit
    trail.
    """
    sentinel_date = "1970-01-01"
    try:
        db.execute(
            text(
                "INSERT INTO receipts "
                "(id, user_id, purchased_at, store_status, total_amount) "
                "VALUES (:id, :user, CAST(:sentinel AS date), 'unknown', NULL) "
                "ON CONFLICT (id) DO NOTHING"
            ),
            {
                "id": raw.receipt_id,
                "user": user_id,
                "sentinel": sentinel_date,
            },
        )
    except Exception as exc:
        raise PersistError(f"anti-fraud reject skeleton receipt INSERT failed: {exc}") from exc
    scan_id = uuid4()
    try:
        db.execute(
            text(
                "INSERT INTO scans "
                "(id, user_id, store_id, store_status, product_ean, scanned_name, "
                " price, quantity, scan_type, receipt_id, status, match_method, "
                " match_confidence, rejected_reason, scanned_at, "
                " status_updated_at, parsed_ticket_id) "
                "VALUES (:id, :user, NULL, 'unknown', NULL, '', "
                "        0, 1, 'receipt', :receipt, 'rejected', NULL, "
                "        NULL, :reason, now(), now(), NULL)"
            ),
            {
                "id": scan_id,
                "user": user_id,
                "receipt": raw.receipt_id,
                "reason": reason,
            },
        )
    except Exception as exc:
        raise PersistError(f"anti-fraud reject sentinel scan INSERT failed: {exc}") from exc
    return scan_id


def _consolidate_via_fuzzy_match(
    *,
    db: Session,
    canonical_id: UUID,
    new_receipt_id: UUID,
) -> UUID:
    """Fold a fuzzy-matched new upload into a canonical receipt.

    Unlike :func:`_consolidate_rescan_into_existing` (which is triggered
    by a UNIQUE-INDEX collision on ``parse_fingerprint_user`` and thus
    has the new fp/total/date handy), the fuzzy match path is taken
    BEFORE the INSERT — we know the canonical id (returned by
    :func:`fuzzy_match_intra_user`) but we deliberately do NOT refresh
    the canonical's denormalised fields. Rationale : the fuzzy match
    was triggered precisely because the new upload's data differs by
    a single OCR digit-swap ; overwriting the canonical with the
    drifted value would be the wrong direction. We append the new id
    to ``consolidated_from_ids[]`` and stop there.

    Returns the canonical id (mirrors the sibling helper for symmetry).
    """
    try:
        db.execute(
            text(
                "UPDATE receipts SET "
                "  consolidated_from_ids = COALESCE(consolidated_from_ids, "
                "                                   ARRAY[]::uuid[]) "
                "                          || ARRAY[CAST(:new_id AS uuid)], "
                "  updated_at = now() "
                "WHERE id = :canonical_id"
            ),
            {"new_id": new_receipt_id, "canonical_id": canonical_id},
        )
    except Exception as exc:
        raise PersistError(f"fuzzy consolidation UPDATE failed (canonical={canonical_id}): {exc}") from exc
    return canonical_id


def _insert_fraud_suspicion(
    *,
    db: Session,
    receipt_id: UUID,
    evidence_receipt_ids: list[UUID | None],
    signal: str,
) -> UUID:
    """INSERT one ``fraud_suspicions`` row with ``resolution_status='pending'``.

    ``evidence_receipt_ids`` is a possibly-empty list — for the
    ``device_shared`` and ``daily_soft_burst`` signals there is no
    single peer receipt to point at, just a pattern, so we pass ``[]``.
    For ``fp_global_strict`` / ``fp_global_minute`` / ``phash`` we pass
    the matched peer id. ``None`` entries are filtered out defensively
    (caller paranoia).

    The PG ``ARRAY[]::uuid[]`` cast is required when the list is empty
    so psycopg doesn't infer an ambiguous type.
    """
    filtered = [str(rid) for rid in evidence_receipt_ids if rid is not None]
    suspicion_id = uuid4()
    if filtered:
        # Build an explicit literal array — parameter binding for an
        # empty list is awkward and the cast keeps the SQL readable.
        db.execute(
            text(
                "INSERT INTO fraud_suspicions "
                "(id, receipt_id, evidence_receipt_ids, detection_signal) "
                "VALUES (:id, :rid, "
                "        CAST(:ev AS uuid[]), "
                "        :sig)"
            ),
            {
                "id": suspicion_id,
                "rid": receipt_id,
                "ev": "{" + ",".join(filtered) + "}",
                "sig": signal,
            },
        )
    else:
        db.execute(
            text(
                "INSERT INTO fraud_suspicions "
                "(id, receipt_id, evidence_receipt_ids, detection_signal) "
                "VALUES (:id, :rid, ARRAY[]::uuid[], :sig)"
            ),
            {"id": suspicion_id, "rid": receipt_id, "sig": signal},
        )
    return suspicion_id


def _insert_mandatory_signals_reject(
    *,
    db: Session,
    raw: RawTicket,
    user_id: UUID | None,
    reason: str,
) -> UUID:
    """Persist a skeleton receipt + sentinel rejected scan for a ticket
    that fails the hard-rule check (cf ARCH étape 4).

    The receipt is intentionally **minimal** — no parsed_ticket_id, no
    fingerprints, no store/dates beyond the SENTINEL. Its sole purpose
    is to satisfy the ``scans.receipt_id NOT NULL`` FK so the rejected
    scan can be inserted (the user must see a "rejected" row in their
    scan history so they can re-scan). We deliberately do NOT write
    ``parse_fingerprint_user`` — the partial index requires
    ``parse_fingerprint_user IS NOT NULL`` so the receipt stays out of
    the dedup pool. This also means a subsequent successful scan of the
    same ticket is not blocked by this skeleton row.

    Returns the sentinel scan's id (caller surfaces it in the result
    dict for downstream tracing).
    """
    sentinel_date = "1970-01-01"
    try:
        db.execute(
            text(
                "INSERT INTO receipts "
                "(id, user_id, purchased_at, store_status, total_amount) "
                "VALUES (:id, :user, CAST(:sentinel AS date), 'unknown', NULL) "
                "ON CONFLICT (id) DO NOTHING"
            ),
            {
                "id": raw.receipt_id,
                "user": user_id,
                "sentinel": sentinel_date,
            },
        )
    except Exception as exc:
        raise PersistError(f"mandatory-signals reject skeleton receipt INSERT failed: {exc}") from exc
    scan_id = uuid4()
    try:
        db.execute(
            text(
                "INSERT INTO scans "
                "(id, user_id, store_id, store_status, product_ean, scanned_name, "
                " price, quantity, scan_type, receipt_id, status, match_method, "
                " match_confidence, rejected_reason, scanned_at, "
                " status_updated_at, parsed_ticket_id) "
                "VALUES (:id, :user, NULL, 'unknown', NULL, '', "
                "        0, 1, 'receipt', :receipt, 'rejected', NULL, "
                "        NULL, :reason, now(), now(), NULL)"
            ),
            {
                "id": scan_id,
                "user": user_id,
                "receipt": raw.receipt_id,
                "reason": f"missing_mandatory_signals_for_dedup:{reason}",
            },
        )
    except Exception as exc:
        raise PersistError(f"mandatory-signals reject sentinel scan INSERT failed: {exc}") from exc
    return scan_id


def _insert_scan(
    *,
    db: Session,
    user_id: UUID | None,
    store_id: UUID | None,
    store_status: str,
    receipt_id: UUID,
    parsed_ticket_id: UUID,
    parsed_item: Any,
    item_match: ItemMatch,
) -> UUID:
    """INSERT one ``scans`` row from a :class:`ItemMatch`.

    Status / match_method enums are mapped through :data:`_SCAN_STATUS_MAP`
    / :data:`_MATCH_METHOD_MAP` (KeyError on unknown — fail loud per
    ARCH § Anti-patterns).

    The CHECK constraint ``ck_scans_store_status_consistency`` requires
    ``(store_status='unknown' AND store_id IS NULL) OR (store_status<>'unknown'
    AND store_id IS NOT NULL)``. When the pipeline yields
    ``store_status='pending'`` (suggested) without a matched store, we
    must downgrade to ``'unknown'`` for the scan row. Receipts use the
    same enum but accept NULL store_id at any status.
    """
    db_status = _SCAN_STATUS_MAP[item_match.status]
    db_match_method = _MATCH_METHOD_MAP[item_match.match_method] if item_match.match_method else None
    # Scans CHECK : store_status='unknown' iff store_id IS NULL.
    if store_id is None:
        scan_store_status = "unknown"
    else:
        scan_store_status = store_status

    scan_id = uuid4()
    try:
        db.execute(
            text(
                "INSERT INTO scans "
                "(id, user_id, store_id, store_status, product_ean, scanned_name, "
                " price, quantity, scan_type, receipt_id, status, match_method, "
                " match_confidence, rejected_reason, scanned_at, "
                " status_updated_at, parsed_ticket_id) "
                "VALUES (:id, :user, :store, :store_status, :ean, :name, "
                "        :price, :qty, 'receipt', :receipt, :status, :method, "
                "        :confidence, :reason, now(), now(), :pt)"
            ),
            {
                "id": scan_id,
                "user": user_id,
                "store": store_id,
                "store_status": scan_store_status,
                "ean": item_match.product_ean,
                "name": parsed_item.normalized_label,
                "price": parsed_item.total_cents,
                "qty": parsed_item.quantity,
                "receipt": receipt_id,
                "status": db_status,
                "method": db_match_method,
                "confidence": item_match.match_confidence,
                "reason": item_match.rejected_reason,
                "pt": parsed_ticket_id,
            },
        )
    except Exception as exc:
        raise PersistError(
            f"scans INSERT failed (parsed_item_id={parsed_item.id}, status={item_match.status}): {exc}"
        ) from exc
    # V1.1 first-discovery attribution (KP-75) — fired only on the
    # ``matched`` terminal state with both EAN and user_id known. Helper
    # is idempotent + filters banned/deleted users itself.
    if item_match.status == "matched" and item_match.product_ean and user_id is not None:
        claim_first_discovery(db, item_match.product_ean, user_id)

    # F-PA-3 — NRC ledger record_resolution (mirrors V2 ``barcode_service``).
    # The user physically scanning the barcode is the strongest signal that
    # feeds the cross-retailer consensus state machine. Skips for
    # ``consensus_match`` (would loop : the EAN already came from the
    # ledger), ``knowledge`` (not accepted by ``pnr_match_method_check``
    # until the curated knowledge ledger lands), unresolved/rejected
    # items (no EAN), and rows without a confirmed store (the ledger is
    # keyed on store_id + label).
    _record_nrc_ledger(
        db=db,
        scan_id=scan_id,
        store_id=store_id,
        user_id=user_id,
        parsed_item=parsed_item,
        item_match=item_match,
    )
    return scan_id


# Match methods produced by pipeline that are SAFE to write into the
# NRC ledger. ``consensus_match`` is intentionally excluded : the EAN
# already came from the ledger, so re-recording would feed a row's own
# vote back into its own consensus computation (cf. ARCH NRC § "Bloc C
# — write hooks" + V2 receipt_task.py:1946-1952 documented refonte
# 2026-05-02). ``knowledge`` is excluded because the V3 knowledge
# stage is a no-op stub today AND the DB CHECK ``pnr_match_method_check``
# does not accept that label yet (the curated knowledge ledger lands
# with the future ``product_knowledge`` table).
_LEDGER_SAFE_METHODS: frozenset[str] = frozenset({"barcode"})


def _record_nrc_ledger(
    *,
    db: Session,
    scan_id: UUID,
    store_id: UUID | None,
    user_id: UUID | None,
    parsed_item: Any,
    item_match: ItemMatch,
) -> None:
    """Append one row to ``product_name_resolutions`` when the V3 match
    is a positive user-physical-scan signal worth recording.

    Skip conditions :

    - ``item_match.status != 'matched'`` — no EAN to record.
    - ``item_match.product_ean is None`` — defensive (the matched
      invariant enforces non-null, but the column is Optional).
    - ``item_match.match_method not in _LEDGER_SAFE_METHODS`` — see
      module-level rationale.
    - ``store_id is None`` — the ledger is per-store ; without a store
      the row has no consensus to feed.
    - ``user_id is None`` — anonymous reprocess / batch path.
    - ``parsed_item.normalized_label`` is empty — defensive (Phase 2
      should always produce a label).

    Side-effects : runs inside the caller's transaction. ``record_resolution``
    is idempotent on ``(scan_id, source_type, normalized_label)`` so a
    Celery retry that re-runs the persist does not duplicate.
    """
    if item_match.status != "matched":
        return
    if item_match.product_ean is None:
        return
    if item_match.match_method not in _LEDGER_SAFE_METHODS:
        return
    # _LEDGER_SAFE_METHODS == frozenset({"barcode"}) — the guard above leaves
    # only "barcode", which is a valid LedgerMethod for record_resolution.
    assert item_match.match_method == "barcode"
    if store_id is None or user_id is None:
        return
    normalized_label = parsed_item.normalized_label
    if not normalized_label:
        return
    record_resolution(
        db,
        scan_id=scan_id,
        store_id=store_id,
        normalized_label=normalized_label,
        product_ean=item_match.product_ean,
        user_id=user_id,
        match_method=item_match.match_method,
        source_type="receipt",
    )


def _insert_store_candidate(
    *,
    db: Session,
    parsed: ParsedTicket,
    receipt_id: UUID,
) -> UUID:
    """INSERT store_candidates from the parsed header — admin-resolved later.

    Best-effort population : every ParsedHeader field that can hint at
    a real store goes in. Per ARCH_store_validation, occurrence_count
    starts at 1 and is bumped by a separate consensus job.
    """
    candidate_id = uuid4()
    raw_header = "\n".join(
        line
        for line in (
            parsed.header.brand,
            parsed.header.address_line,
            " ".join(v for v in (parsed.header.postcode, parsed.header.city) if v) or None,
            parsed.header.phone,
        )
        if line
    )
    try:
        db.execute(
            text(
                "INSERT INTO store_candidates "
                "(id, raw_header, retailer_guess, address_guess, postal_code, "
                " phone, occurrence_count, status, receipt_id) "
                "VALUES (:id, :raw, :retailer, :address, :postal, :phone, "
                "        1, 'pending', :receipt)"
            ),
            {
                "id": candidate_id,
                "raw": raw_header or "",
                "retailer": parsed.header.brand,
                "address": parsed.header.address_line,
                "postal": parsed.header.postcode,
                "phone": parsed.header.phone,
                "receipt": receipt_id,
            },
        )
    except Exception as exc:
        raise PersistError(f"store_candidates INSERT failed: {exc}") from exc
    return candidate_id


__all__ = [
    "PersistError",
    "persist_pipeline_result",
]
