from __future__ import annotations

import io
import logging
import os
import uuid
from contextlib import contextmanager
from typing import TYPE_CHECKING

from celery.exceptions import SoftTimeLimitExceeded
from celery_app import celery_app
from langfuse import observe
from ratis_core.models.store import Store
from ratis_core.rewards_client import trigger_action, trigger_cashback_scan
from ratis_core.settings import load_settings
from repositories.scan_repository import create_scan, get_receipt
from services.reconciliation_service import reconcile_unknown_scans_for_receipt
from sqlalchemy import text
from storage import get_s3_client

if TYPE_CHECKING:
    from sqlalchemy.engine import Engine

logger = logging.getLogger(__name__)


# ── module-level singletons ────────────────────────────────────────────────────

# Celery uses prefork by default — each worker process has its own global state.
# _db_engine is process-local; not safe under --pool=threads/gevent.
_db_engine: "Engine" | None = None
_session_factory = None


def _get_db_engine() -> "Engine":
    global _db_engine
    if _db_engine is None:
        from ratis_core.database import make_engine

        _db_engine = make_engine(os.environ["DATABASE_URL"])
    return _db_engine


def _get_session_factory():
    global _session_factory
    if _session_factory is None:
        from sqlalchemy.orm import sessionmaker

        _session_factory = sessionmaker(bind=_get_db_engine())
    return _session_factory


def _get_s3_client():
    # Delegates to storage.get_s3_client so virtual-hosted addressing + SigV4
    # are used consistently across the service (required by R2 for presigned
    # URLs — see storage.py docstring).
    #
    # 2026-04-27 — was a lazy import for boto3 startup-cost reasons, but Celery
    # workers don't keep `/app/webservices/ratis_product_analyser` in sys.path
    # after bootstrap : Celery adds the dir at startup so module-load imports
    # succeed and get cached in sys.modules, but a *fresh* `from storage import`
    # later raises ModuleNotFoundError. Top-level import (line ~37) caches it.
    return get_s3_client()


def _download_raw(key: str, s3_client=None) -> bytes:
    client = s3_client or _get_s3_client()
    bucket = os.environ["R2_BUCKET_NAME"]
    buf = io.BytesIO()
    client.download_fileobj(bucket, key, buf)
    return buf.getvalue()


def _run_phash_phase_zero(
    *,
    db,
    receipt,
    image_bytes: bytes,
) -> "tuple[str, str | None] | tuple[str, dict]":
    """Anti-fraud PR2 — phase 0 pHash check (V3 only).

    Computes a perceptual hash on the raw image and looks up cross-user
    receipts with a near-identical hash inside the configured window.

    Returns:
        ``("ok", phash_hex)``     — no cross-user match (or pHash compute
                                    skipped) ; proceed to OCR. ``phash_hex``
                                    is ``None`` when compute failed (image
                                    unreadable) so the caller can still
                                    persist the receipt without a hash.
        ``("reject", details)``   — cross-user duplicate detected.
                                    ``details`` is the lookup ``details``
                                    dict ({peer_user_id, peer_receipt_id,
                                    hamming_distance}). Caller MUST mark
                                    the receipt as rejected and skip OCR.

    Fail-safe contract : any exception inside this helper degrades to
    ``("ok", None)`` so a phase 0 bug never blocks a legitimate scan
    (cf ARCH § "Réconciliation tickets V1" — anti-fraud is best-effort
    by design).
    """
    # Local imports so the V2 hot-path never pays the pHash import cost.
    from worker.pipeline.phash import compute_phash
    from worker.pipeline.phash_lookup import lookup_phash_cross_user

    try:
        settings_full = load_settings()
        anti_fraud_cfg = settings_full.get("pipeline", {}).get("anti_fraud", {}) or {}
    except Exception:
        logger.warning(
            "phase0 pHash: load_settings failed — skipping check",
            exc_info=True,
        )
        return ("ok", None)

    if not anti_fraud_cfg.get("enable_phash_check", True):
        logger.debug(
            "phase0 pHash: disabled via settings — skipping for receipt %s",
            receipt.id,
        )
        return ("ok", None)

    try:
        phash_hex = compute_phash(image_bytes)
    except Exception:
        logger.warning(
            "phase0 pHash: compute_phash crashed for receipt %s — continuing OCR",
            receipt.id,
            exc_info=True,
        )
        return ("ok", None)

    if phash_hex is None:
        # Image undecodable / library skipped — no hash to lookup or
        # persist, but OCR may still recover something (or fail loudly
        # later via the regular image-quality path).
        return ("ok", None)

    threshold = int(anti_fraud_cfg.get("phash_hamming_threshold", 8))
    window_days = int(anti_fraud_cfg.get("phash_window_days", 30))

    try:
        match = lookup_phash_cross_user(
            db,
            user_id=receipt.user_id,
            candidate_phash_hex=phash_hex,
            max_hamming_distance=threshold,
            window_days=window_days,
        )
    except Exception:
        logger.warning(
            "phase0 pHash: lookup crashed for receipt %s — continuing OCR",
            receipt.id,
            exc_info=True,
        )
        return ("ok", phash_hex)

    if match is None:
        return ("ok", phash_hex)

    _peer_receipt_id, details = match
    return ("reject", details)


def _apply_phash_rejection(
    *,
    db,
    receipt,
    phash_hex: str,
    details: dict,
) -> None:
    """Persist a phase 0 cross-user duplicate rejection.

    Writes 3 mutations in a single transaction (caller commits) :

      1. ``UPDATE receipts SET image_phash = :ph`` — store the hash so
         future receipts can match against it too (the lookup window
         is symmetric : we both look up and serve future lookups).
      2. ``INSERT scans`` marker row with ``status='rejected'`` and
         ``rejected_reason='image_duplicate'`` — the receipt history
         endpoint surfaces this to the user.
      3. ``INSERT fraud_suspicions`` row with ``detection_signal='phash'``
         and ``evidence_receipt_ids=[peer_receipt_id]`` — admin queue
         picks this up for triage.

    The marker scan uses ``store_status='unknown'`` + ``store_id=NULL``
    (the receipt has no store yet — OCR was skipped) which satisfies
    the ``ck_scans_store_status_consistency`` CHECK.
    """
    peer_receipt_id = details.get("peer_receipt_id")

    db.execute(
        text("UPDATE receipts SET image_phash = :ph WHERE id = :rid"),
        {"ph": phash_hex, "rid": receipt.id},
    )

    # Marker scan — explicit INSERT because the receipt has no store
    # yet (OCR skipped) so create_scan() default 'confirmed' would
    # break the ck_scans_store_status_consistency constraint.
    db.execute(
        text(
            "INSERT INTO scans "
            "(id, user_id, store_id, store_status, product_ean, scanned_name, "
            " price, quantity, scan_type, receipt_id, status, match_method, "
            " rejected_reason, scanned_at, status_updated_at) "
            "VALUES (:id, :uid, NULL, 'unknown', NULL, '', "
            "        0, 1, 'receipt', :rid, 'rejected', NULL, "
            "        'image_duplicate', now(), now())"
        ),
        {
            "id": uuid.uuid4(),
            "uid": receipt.user_id,
            "rid": receipt.id,
        },
    )

    db.execute(
        text(
            "INSERT INTO fraud_suspicions "
            "(receipt_id, evidence_receipt_ids, detection_signal) "
            "VALUES (:rid, ARRAY[CAST(:peer AS uuid)], 'phash')"
        ),
        {"rid": receipt.id, "peer": peer_receipt_id},
    )


def _mark_receipt_failed(receipt_id: uuid.UUID, reason: str, *, _db=None) -> None:
    """Persist a single ``failed`` sentinel scan for a receipt, freeing
    ``photo_hash`` so the user can retry.

    Used for terminal failures that may have left the in-flight session
    poisoned (e.g. a Celery ``SoftTimeLimitExceeded`` raised mid-pipeline).
    A fresh session is opened so the failed status persists even when the
    original transaction is unusable ; tests inject ``_db`` to share the
    savepoint-isolated session. Best-effort : a failure here is logged,
    never re-raised.
    """
    from contextlib import contextmanager

    @contextmanager
    def _session():
        if _db is not None:
            yield _db
        else:
            with _get_session_factory()() as session:
                yield session

    try:
        with _session() as db:
            db.rollback()  # discard any poisoned in-flight transaction
            receipt = get_receipt(db, receipt_id)
            if receipt is None:
                return
            receipt.photo_hash = None
            create_scan(
                db,
                receipt=receipt,
                scanned_name="",
                price=0,
                quantity=1.0,
                tva_amount=None,
                product_ean=None,
                status="failed",
            )
            db.commit()
    except Exception:
        logger.exception(
            "Could not persist failed status for receipt %s (reason=%s)",
            receipt_id,
            reason,
        )


@celery_app.task(name="worker.receipt_task.process_receipt", bind=True, max_retries=3)
@observe(name="ocr_scan", capture_output=False)
def process_receipt(
    self,
    receipt_id_str: str,
    *,
    _s3=None,
    _db=None,
) -> None:
    # @observe (inner — under @celery_app.task) opens one trace per scan
    # ("ocr_scan") ; the downstream AnthropicLLMClient.extract call nests as a
    # *generation* via AnthropicInstrumentor (DA-LO3). No-op when Langfuse keys
    # are absent — the decorator is inert without an initialised client.
    #
    # RGPD : ``capture_output=False`` — the return is always ``None`` so there
    # is nothing useful to record, and we never want trace output to leak PII
    # if the contract ever changes. Trace *input* is ``receipt_id_str`` (a
    # UUID) — no PII (DA-LO3 + ARCH_llm_observability.md § Contraintes RGPD).
    """
    Full pipeline for a receipt — runs the V3 pipeline.

    _s3, _db are injectable for tests — callers should not pass them in production.
    """
    try:
        receipt_id = uuid.UUID(receipt_id_str)
    except ValueError:
        logger.error("Invalid receipt_id: %r", receipt_id_str)
        return

    @contextmanager
    def _session():
        if _db is not None:
            yield _db
        else:
            with _get_session_factory()() as session:
                yield session

    try:
        with _session() as db:
            receipt = get_receipt(db, receipt_id)
            if receipt is None:
                logger.error("Receipt %s not found", receipt_id)
                return

            if not receipt.image_r2_key:
                logger.error("Receipt %s has no image_r2_key", receipt_id)
                return

            try:
                raw = _download_raw(receipt.image_r2_key, s3_client=_s3)
            except Exception as exc:
                logger.exception("Failed to download file for receipt %s", receipt_id)
                if self.request.retries >= self.max_retries:
                    logger.error("Max retries exhausted downloading receipt %s — marking as failed", receipt_id)
                    try:
                        receipt.photo_hash = None
                        create_scan(
                            db,
                            receipt=receipt,
                            scanned_name="",
                            price=0,
                            quantity=1.0,
                            tva_amount=None,
                            product_ean=None,
                            status="failed",
                        )
                        db.commit()
                    except Exception:
                        logger.exception("Could not persist failed status for receipt %s", receipt_id)
                    return
                raise self.retry(exc=exc, countdown=60)

            # ── Phase 0 — pHash pré-OCR (anti-fraud PR2) ──────────────────
            # Cross-user image duplicate check BEFORE OCR (economy of cost :
            # OCR ~3-5s + LLM calls skipped on a hit). Fail-safe : any
            # crash inside _run_phash_phase_zero degrades to ("ok", None)
            # so a phase 0 bug never blocks a legitimate scan.
            _phase0_outcome, _phase0_payload = _run_phash_phase_zero(
                db=db,
                receipt=receipt,
                image_bytes=raw,
            )
            if _phase0_outcome == "reject":
                # _run_phash_phase_zero contract: outcome "reject" always
                # carries the lookup ``details`` dict as the payload.
                assert isinstance(_phase0_payload, dict)
                logger.info(
                    "Receipt %s rejected by phase 0 pHash (cross-user match) — peer=%s d=%s",
                    receipt_id,
                    _phase0_payload.get("peer_receipt_id"),
                    _phase0_payload.get("hamming_distance"),
                )
                # The hash that triggered the rejection is the candidate
                # we just computed ; persist it so future lookups still
                # see the duplicate (symmetric window).
                from worker.pipeline.phash import compute_phash as _cp

                _rejection_phash = _cp(raw) or ""
                try:
                    _apply_phash_rejection(
                        db=db,
                        receipt=receipt,
                        phash_hex=_rejection_phash,
                        details=_phase0_payload,
                    )
                    db.commit()
                except Exception:
                    logger.exception(
                        "Could not persist phase 0 rejection for receipt %s",
                        receipt_id,
                    )
                    db.rollback()
                return

            # Hand-off to pipeline.orchestrator. The v3 path owns its
            # own commit boundary — one transaction per receipt, errors
            # propagate so Celery can retry.
            from worker.pipeline.orchestrator import run_pipeline

            # Read fresh per-task : settings live in app_settings table OR
            # ratis_settings.json fallback so an operator log-level flip
            # takes effect on the next scan without restart.
            try:
                _pipeline_settings = load_settings().get("pipeline")
            except Exception:
                logger.warning(
                    "load_settings failed for pipeline — using defaults",
                    exc_info=True,
                )
                _pipeline_settings = None
            _pipeline_log_level = _pipeline_settings.get("log_level", "normal") if _pipeline_settings else "normal"
            # ``_phase0_payload`` here is the candidate pHash hex (or None
            # if compute failed) — to be persisted alongside the receipt
            # after the orchestrator succeeds. See post-pipeline UPDATE
            # below.
            # The "reject" branch returned above, so the remaining outcome is
            # "ok", whose payload is the candidate pHash hex (str) or None —
            # never the details dict.
            assert not isinstance(_phase0_payload, dict)
            _candidate_phash_hex: "str | None" = _phase0_payload
            reconciliation_outcome = None
            raw_receipt_text: "str | None" = None
            try:
                _pipeline_result = run_pipeline(
                    raw,
                    db=db,
                    user_id=receipt.user_id,
                    captured_at=None,  # let orchestrator default to now(UTC)
                    receipt_id=receipt.id,
                    log_level=_pipeline_log_level,
                )
                # Phase C-4 — orchestrator returns the flattened OCR
                # text in its result dict so the reward emit layer can
                # run the promo-signal regex (cf. _award_scan_rewards).
                raw_receipt_text = _pipeline_result.get("raw_receipt_text")
                # F-PA-5 — Part B label reconciliation. The scan mutations
                # land in the same transaction as the receipt insertions so
                # a commit failure rolls everything back together.
                db.refresh(receipt)
                if receipt.store_id is not None and receipt.store_status == "confirmed" and receipt.user_id is not None:
                    try:
                        reconciliation_outcome = reconcile_unknown_scans_for_receipt(db, receipt)
                    except Exception:
                        logger.exception(
                            "Reconciliation pass raised unexpectedly for receipt %s — continuing",
                            receipt_id,
                        )
                # Phase 0 (anti-fraud PR2) — persist the candidate pHash on
                # the receipt so future cross-user lookups can match this
                # image. Done AFTER persist_pipeline_result has upserted the
                # receipt row. Best-effort : a failure here does not abort
                # the OCR commit.
                if _candidate_phash_hex:
                    try:
                        db.execute(
                            text("UPDATE receipts SET image_phash = :ph WHERE id = :rid AND image_phash IS NULL"),
                            {"ph": _candidate_phash_hex, "rid": receipt.id},
                        )
                    except Exception:
                        logger.warning(
                            "phase0 pHash: UPDATE receipts.image_phash failed for receipt %s — continuing",
                            receipt_id,
                            exc_info=True,
                        )
                db.commit()
            except Exception:
                logger.exception(
                    "pipeline failed for receipt %s — rolling back",
                    receipt_id,
                )
                db.rollback()
                raise
            if reconciliation_outcome is not None:
                logger.info(
                    "Receipt %s: reconciled %d previously-unknown scan(s) for store %s",
                    receipt_id,
                    reconciliation_outcome.reconciled_count,
                    reconciliation_outcome.store_name,
                )
            # F-PA-1 — grant CAB + XP. The helper reads accepted scans
            # straight from the DB (committed) and fires the same
            # trigger_action / trigger_cashback_scan calls with
            # idempotency_key=receipt.id so a Celery retry reaching this
            # tail does not double-credit.
            db.refresh(receipt)
            _award_scan_rewards(db, receipt, raw_receipt_text=raw_receipt_text)
            return
    except SoftTimeLimitExceeded:
        # Celery soft time-limit hit mid-pipeline (cf. celery_app.py
        # task_soft_time_limit). The in-flight session may be poisoned ;
        # mark the receipt failed on a clean session so it does not stay
        # stuck on 'pending' forever (DoS amplifier — the scan is lost).
        logger.error(
            "Receipt %s exceeded the OCR soft time-limit — marking failed",
            receipt_id,
        )
        _mark_receipt_failed(receipt_id, "soft_time_limit_exceeded", _db=_db)
        return


def _award_scan_rewards(
    db,
    receipt,
    *,
    raw_receipt_text: str | None = None,
) -> None:
    """Fire-and-forget : grant CAB + action XP for a successfully-processed
    receipt (audit F-PA-1) so the cross-service contract with
    ``ratis_rewards`` is implemented in exactly one place.

    Guards (defense-in-depth) :

    - ``receipt.user_id`` non-null (anonymous reprocess paths skip).
    - ``receipt.store_status == 'confirmed'`` AND ``receipt.store_id`` set.
    - ``stores.validation_status == 'confirmed'`` (PR-B : soft-match,
      pending and suspicious stores all block cashback).
    - At least one accepted scan with ``product_ean`` set (otherwise
      nothing to cash back).

    Idempotent on ``receipt.id`` — a Celery retry that reaches this tail
    a second time does not double-credit.

    Args:
        raw_receipt_text: Phase C-4 — flattened OCR text used by the
            regex promo-signal detector. ``None`` (default) disables
            the ``promo_found`` emit ; the caller passes the string it
            reads from the orchestrator return dict.
            Disabled also when the settings flag
            ``pipeline.promo_detection.enable`` is false (rollback
            escape hatch).
    """
    if receipt is None or receipt.user_id is None:
        return
    if receipt.store_status != "confirmed" or receipt.store_id is None:
        return
    store_obj = db.get(Store, receipt.store_id)
    if store_obj is None or store_obj.validation_status != "confirmed":
        return

    # ``accepted`` = legacy V2 status name ; ``matched`` = V3 status name.
    # Both represent "scan was successfully attributed to a product".
    rows = db.execute(
        text(
            "SELECT id, product_ean, price FROM scans "
            "WHERE receipt_id = :rid "
            "  AND status IN ('accepted', 'matched') "
            "  AND product_ean IS NOT NULL"
        ),
        {"rid": str(receipt.id)},
    ).all()
    if not rows:
        return

    # Phase B (PR #325) — single receipt event, idempotency keyed on the
    # receipt id so a Celery retry that reaches the same tail does not
    # double-credit.
    trigger_action(
        receipt.user_id,
        "receipt_scan",
        quantity=1,
        idempotency_key=str(receipt.id),
        context={
            "receipt_id": str(receipt.id),
            "scan_id": str(receipt.id),
            "store_id": (str(receipt.store_id) if receipt.store_id else None),
        },
    )
    receipt_lines = [{"ean": r.product_ean, "price": r.price, "scan_id": str(r.id)} for r in rows]
    trigger_cashback_scan(receipt.user_id, receipt_lines)

    # Phase C-4 — promo regex layer. Bolt-on, observational, never
    # raises ; failures here NEVER block receipt acceptance. Idempotent
    # via ``<receipt_id>:promo`` so a Celery retry reaching the same
    # tail does not double-credit the missions ledger.
    if raw_receipt_text:
        _emit_promo_found_if_any(receipt, raw_receipt_text)


def _emit_promo_found_if_any(receipt, raw_receipt_text: str) -> None:
    """Phase C-4 — run the promo-signal regex layer on the raw OCR text
    and, if any signal fires, emit a single
    ``trigger_action("promo_found", quantity=N)`` event.

    Settings live in ``ratis_settings.json § pipeline.promo_detection``
    (R19 — never hardcode). The ``enable`` flag is the rollback escape
    hatch : flipping it to ``false`` disables the entire detector
    without a code revert.

    Best-effort : a failure loading settings or running the regex MUST
    NOT raise — receipt acceptance is on the critical path and must
    not depend on the missions-side observability layer.
    """
    try:
        from worker.pipeline.promo_detector import detect_promos

        _full_settings = load_settings()
        _promo_cfg = _full_settings.get("pipeline", {}).get("promo_detection", {})
        _enable = bool(_promo_cfg.get("enable", False))
        _patterns = _promo_cfg.get("patterns") or None
        matches = detect_promos(
            raw_receipt_text,
            patterns=_patterns,
            enable=_enable,
        )
    except Exception:
        logger.exception(
            "promo_detector failed for receipt %s — skipping promo_found emit",
            receipt.id,
        )
        return

    if not matches:
        return

    try:
        trigger_action(
            receipt.user_id,
            "promo_found",
            quantity=len(matches),
            idempotency_key=f"{receipt.id}:promo",
            context={
                "receipt_id": str(receipt.id),
                "patterns_matched": [m.pattern for m in matches],
            },
        )
    except Exception:
        logger.warning(
            "trigger_action promo_found failed for receipt %s — skipping",
            receipt.id,
            exc_info=True,
        )
