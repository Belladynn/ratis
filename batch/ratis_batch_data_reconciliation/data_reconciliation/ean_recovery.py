"""Job 1 — ean_recovery (Bloc I NRC).

Re-balaye les scans ``status='unresolved'`` récents et retry le match via
le ledger ``product_name_resolutions`` retailer-keyed (consensus
crowdsourcé qui a possiblement bougé depuis la dernière run du worker).

Cascade par scan :

1. Resolve ``retailer_id`` depuis ``stores`` — skip si NULL (user-suggested
   pending ou store détaché : dehors du chemin consensus).
2. Stage 3 — exact lookup retailer-keyed sur
   ``product_name_resolutions(retailer_id, source_type='receipt',
   normalized_label)``. État VERIFIED requis.
3. Stage 4 — fuzzy retailer-wide via pg_trgm
   (``idx_pnr_norm_label_trgm``), gates ``similarity ≥ fuzzy_similarity_min``
   et ``abs(len_diff) ≤ fuzzy_levenshtein_max``. Toujours VERIFIED only.
4. Si match → UPDATE scan + INSERT PNR row idempotent + auto-feed
   ``ocr_knowledge`` si confidence ≥ ``ocr_knowledge_auto_feed_confidence_min``.

⚠️ SYNC OBLIGATOIRE : la cascade doit rester cohérente avec
``webservices/ratis_product_analyser/repositories/name_resolution_repository.py``
(``get_consensus_for_label`` + ``find_fuzzy_verified_consensus``). Toute
évolution du seuil de convergence ou du calcul de poids doit être
répercutée ici. Voir aussi ARCH_cross_retailer_consensus.md § "Cascade
matcher".

Stage E.2 — partial EAN recovery via Levenshtein on EAN strings — *not*
implemented in V1 ; the alpha will tell us whether the OCR confuses
enough digits to justify it. TODO in this file with a pointer to the
ARCH section.
"""

from __future__ import annotations

import logging
import time
import uuid

from ratis_core.settings import load_settings
from sqlalchemy import text
from sqlalchemy.orm import Session

log = logging.getLogger(__name__)

# Validation methods that contribute to the receipt-source consensus.
# Mirrors ``settings.name_resolution_consensus.validation_methods_receipt``
# fallback chain. Hardcoded subset here keeps the SQL simple ; the
# repositories' code path takes the same default.
_RECEIPT_VALIDATION_METHODS_DEFAULT = ("barcode", "manual_admin")


def _receipt_validation_methods(nrc_settings: dict) -> list[str]:
    """Receipt-source contributing methods, mirroring the NRC repository."""
    if "validation_methods_receipt" in nrc_settings:
        return list(nrc_settings["validation_methods_receipt"])
    if "validation_methods" in nrc_settings:
        return list(nrc_settings["validation_methods"])
    return list(_RECEIPT_VALIDATION_METHODS_DEFAULT)


def _resolve_retailer_id(db: Session, store_id: uuid.UUID) -> uuid.UUID | None:
    """Mirror of ``repositories.retailer_resolution.resolve_retailer_id``."""
    row = db.execute(
        text("SELECT retailer_id FROM stores WHERE id = :store_id"),
        {"store_id": str(store_id)},
    ).first()
    if row is None or row.retailer_id is None:
        return None
    rid = row.retailer_id
    if isinstance(rid, uuid.UUID):
        return rid
    return uuid.UUID(str(rid))


def _verified_consensus_exact(
    db: Session,
    *,
    retailer_id: uuid.UUID,
    normalized_label: str,
    methods: list[str],
    nrc_settings: dict,
) -> dict | None:
    """Stage 3 — retailer-keyed exact match, return VERIFIED winner only.

    Returns a dict ``{"ean", "top1_pct", "distinct_validators"}`` when the
    triple satisfies the convergence + lead-factor gates, else ``None``.
    """
    if not methods:
        return None

    admin_weight = int(nrc_settings.get("admin_validation_weight", 5))
    convergence_pct = float(nrc_settings.get("convergence_threshold_pct", 80))
    lead_factor = float(nrc_settings.get("min_top1_lead_factor", 2.0))
    min_users = int(nrc_settings.get("min_distinct_users", 3))

    # Per-EAN aggregation, identical to repositories.name_resolution_repository.
    # ``weight_override`` (anti-fraud V1) overrides method-derived weight.
    rows = db.execute(
        text(
            """
            SELECT product_ean,
                   SUM(COALESCE(
                       weight_override,
                       CASE WHEN match_method = 'manual_admin' THEN :admin_weight
                            WHEN match_method = ANY(:methods)   THEN 1
                            ELSE 0 END
                   ))::int AS weight,
                   COUNT(DISTINCT CASE
                       WHEN COALESCE(
                           weight_override,
                           CASE WHEN match_method = 'manual_admin' THEN :admin_weight
                                WHEN match_method = ANY(:methods)   THEN 1
                                ELSE 0 END
                       ) = 0 THEN NULL
                       ELSE user_id END
                   ) AS distinct_users
            FROM product_name_resolutions
            WHERE retailer_id = :retailer_id
              AND source_type = 'receipt'
              AND normalized_label = :label
              AND match_method = ANY(:methods)
            GROUP BY product_ean
            ORDER BY weight DESC, product_ean ASC
            """
        ),
        {
            "retailer_id": str(retailer_id),
            "label": normalized_label,
            "methods": list(methods),
            "admin_weight": admin_weight,
        },
    ).fetchall()

    if not rows:
        return None

    top1 = rows[0]
    top1_weight = int(top1.weight or 0)
    top2_weight = int(rows[1].weight) if len(rows) > 1 and rows[1].weight else 0
    total_weight = sum(int(r.weight or 0) for r in rows)
    if total_weight == 0:
        return None

    distinct_validators = int(top1.distinct_users or 0)
    if distinct_validators < min_users:
        return None

    top1_pct = (top1_weight / total_weight) * 100.0
    pct_ok = top1_pct >= convergence_pct
    lead_ok = (top2_weight == 0) or (top1_weight >= lead_factor * top2_weight)
    if not (pct_ok and lead_ok):
        return None

    return {
        "ean": str(top1.product_ean),
        "top1_pct": top1_pct,
        "distinct_validators": distinct_validators,
    }


def _verified_consensus_fuzzy(
    db: Session,
    *,
    retailer_id: uuid.UUID,
    cleaned_label: str,
    methods: list[str],
    nrc_settings: dict,
    sim_min: float,
    len_diff_max: int,
) -> dict | None:
    """Stage 4 — retailer-wide pg_trgm fuzzy fallback, VERIFIED winners only.

    Mirrors ``repositories.name_resolution_repository.find_fuzzy_verified_consensus``.
    """
    rows = db.execute(
        text(
            """
            SELECT DISTINCT pnr.normalized_label,
                   similarity(pnr.normalized_label, :cleaned_label) AS sim
            FROM product_name_resolutions pnr
            WHERE pnr.retailer_id = :retailer_id
              AND pnr.source_type = 'receipt'
              AND pnr.normalized_label != :cleaned_label
              AND ABS(LENGTH(pnr.normalized_label) - LENGTH(:cleaned_label))
                  <= :max_len_diff
              AND similarity(pnr.normalized_label, :cleaned_label)
                  >= :min_similarity
            ORDER BY sim DESC
            LIMIT 5
            """
        ),
        {
            "retailer_id": str(retailer_id),
            "cleaned_label": cleaned_label,
            "max_len_diff": len_diff_max,
            "min_similarity": sim_min,
        },
    ).fetchall()

    for row in rows:
        verified = _verified_consensus_exact(
            db,
            retailer_id=retailer_id,
            normalized_label=row.normalized_label,
            methods=methods,
            nrc_settings=nrc_settings,
        )
        if verified is not None:
            return verified
    return None


def _record_resolution(
    db: Session,
    *,
    scan_id: uuid.UUID,
    store_id: uuid.UUID,
    user_id: uuid.UUID,
    normalized_label: str,
    product_ean: str,
) -> None:
    """Insert a ledger row for the recovered match, idempotent.

    Mirrors ``repositories.name_resolution_writes.record_resolution`` for
    the receipt path with ``match_method='observed_name'`` (the value
    ``consensus_match`` is on ``scans.match_method`` only — the ledger
    CHECK ``pnr_match_method_check`` accepts ``observed_name`` for
    machine-derived recoveries). The trigger ``fn_sync_pnr_retailer_id``
    will denorm ``retailer_id`` from the store automatically.
    """
    db.execute(
        text(
            """
            INSERT INTO product_name_resolutions
                (id, scan_id, store_id, normalized_label,
                 product_ean, user_id, match_method, source_type,
                 resolved_at)
            VALUES
                (:id, :scan_id, :store_id, :label,
                 :ean, :user_id, 'observed_name', 'receipt',
                 clock_timestamp())
            ON CONFLICT (scan_id, source_type, normalized_label) DO NOTHING
            """
        ),
        {
            "id": str(uuid.uuid4()),
            "scan_id": str(scan_id),
            "store_id": str(store_id),
            "label": normalized_label,
            "ean": product_ean,
            "user_id": str(user_id),
        },
    )


def _upsert_ocr_knowledge_auto_feed(
    db: Session,
    *,
    raw_ocr: str,
    corrected: str,
    confidence: float,
) -> None:
    """Auto-feed the OCR knowledge dictionary with a machine-confidence row.

    Idempotent on ``UNIQUE (raw_ocr, type)``. Identical text on raw_ocr
    and corrected at this stage — the recovery resolved the OCR text to
    a known EAN, so the OCR text itself is its own canonical form for
    the dictionary's purpose. Future LLM rephrasing pass may rewrite
    ``corrected`` later (out of Phase 1 scope).
    """
    db.execute(
        text(
            """
            INSERT INTO ocr_knowledge
                (id, type, raw_ocr, corrected, match_type, source, confidence, seen_count)
            VALUES
                (gen_random_uuid(), 'product_name', :raw, :corrected,
                 'sequence', 'ocr_arbitrage', :confidence, 1)
            ON CONFLICT (raw_ocr, type) DO UPDATE
            SET seen_count = ocr_knowledge.seen_count + 1
            """
        ),
        {"raw": raw_ocr, "corrected": corrected, "confidence": confidence},
    )


def reconcile_ean_recovery(db: Session, *, dry_run: bool = False) -> dict:
    """Re-scan unresolved receipt scans and retry-match via fresh consensus.

    Returns a dict carrying canonical counters :
    ``count_processed``, ``count_resolved``, ``count_skipped``,
    ``count_errors``, ``duration_ms``.

    The function commits per-scan when a match is applied (so a partial
    failure doesn't roll back successful recoveries). On dry_run, no
    writes occur and ``count_resolved`` reports what *would* have been
    matched.
    """
    start = time.monotonic()

    settings = load_settings()
    job_settings = settings["data_reconciliation"]["ean_recovery"]
    nrc_settings = settings["name_resolution_consensus"]

    lookback_days = int(job_settings["lookback_days"])
    sim_min = float(job_settings["fuzzy_similarity_min"])
    len_diff_max = int(job_settings["fuzzy_levenshtein_max"])
    auto_feed_min = float(job_settings["ocr_knowledge_auto_feed_confidence_min"])
    methods = _receipt_validation_methods(nrc_settings)

    # Pull unresolved receipt scans within the lookback window. The
    # CHECK ``ck_scans_non_matched_requires_reason`` guarantees a non-NULL
    # rejected_reason — we don't use it for the cascade, the matcher's
    # decision is independent of why it was unresolved last time.
    candidates = db.execute(
        text(
            """
            SELECT id AS scan_id, user_id, store_id, scanned_name
            FROM scans
            WHERE status = 'unresolved'
              AND scan_type = 'receipt'
              AND scanned_name IS NOT NULL
              AND store_id IS NOT NULL
              AND user_id IS NOT NULL
              AND scanned_at > now() - make_interval(days => :lookback_days)
            ORDER BY scanned_at ASC
            """
        ),
        {"lookback_days": lookback_days},
    ).fetchall()

    stats = {
        "count_processed": 0,
        "count_resolved": 0,
        "count_skipped": 0,
        "count_errors": 0,
    }

    for row in candidates:
        stats["count_processed"] += 1
        scan_id: uuid.UUID = row.scan_id
        user_id: uuid.UUID = row.user_id
        store_id: uuid.UUID = row.store_id
        normalized_label: str = row.scanned_name

        try:
            retailer_id = _resolve_retailer_id(db, store_id)
            if retailer_id is None:
                stats["count_skipped"] += 1
                continue

            match = _verified_consensus_exact(
                db,
                retailer_id=retailer_id,
                normalized_label=normalized_label,
                methods=methods,
                nrc_settings=nrc_settings,
            )
            if match is None:
                match = _verified_consensus_fuzzy(
                    db,
                    retailer_id=retailer_id,
                    cleaned_label=normalized_label,
                    methods=methods,
                    nrc_settings=nrc_settings,
                    sim_min=sim_min,
                    len_diff_max=len_diff_max,
                )

            # TODO: Phase 1+ — Stage E.2 partial EAN recovery via
            # Levenshtein on the EAN string itself (cf
            # ARCH_cross_retailer_consensus.md § Stage E.2). Postponed
            # pending alpha telemetry on whether digit-confusion OCR is
            # a real failure mode. Wire here when needed.

            if match is None:
                stats["count_skipped"] += 1
                continue

            if dry_run:
                # In dry_run we still count as "would resolve" so the run.py
                # log surfaces the recovery rate.
                stats["count_resolved"] += 1
                continue

            # Apply the recovery — atomic per-scan transaction.
            db.execute(
                text(
                    """
                    UPDATE scans
                    SET status = 'matched',
                        product_ean = :ean,
                        match_method = 'consensus_match',
                        rejected_reason = NULL,
                        status_updated_at = now()
                    WHERE id = :scan_id
                      AND status = 'unresolved'
                    """
                ),
                {"ean": match["ean"], "scan_id": str(scan_id)},
            )
            _record_resolution(
                db,
                scan_id=scan_id,
                store_id=store_id,
                user_id=user_id,
                normalized_label=normalized_label,
                product_ean=match["ean"],
            )

            # Auto-feed OCR knowledge if the consensus crossed the gate.
            # The convergence pct on the consensus is a reasonable proxy
            # for the auto-feed confidence (range 0–100 in our settings,
            # 0–1 here for the ocr_knowledge.confidence column).
            consensus_confidence = float(match["top1_pct"]) / 100.0
            if consensus_confidence >= auto_feed_min:
                _upsert_ocr_knowledge_auto_feed(
                    db,
                    raw_ocr=normalized_label,
                    corrected=normalized_label,
                    confidence=consensus_confidence,
                )

            db.commit()
            stats["count_resolved"] += 1
            log.info(
                "ean_recovery_resolved scan=%s ean=%s top1_pct=%.2f validators=%d",
                scan_id,
                match["ean"],
                match["top1_pct"],
                match["distinct_validators"],
            )
        except Exception as exc:
            db.rollback()
            stats["count_errors"] += 1
            log.warning(
                "ean_recovery_failed scan=%s error=%s",
                scan_id,
                exc,
                exc_info=True,
            )

    stats["duration_ms"] = int((time.monotonic() - start) * 1000)
    return stats


__all__ = ["reconcile_ean_recovery"]
