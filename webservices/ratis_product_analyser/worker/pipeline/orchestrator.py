"""Orchestrator — composes the 4 pipeline phases end-to-end.

Wires the pure-functional phases (extract / comprehend / match) to
real DB-backed lookups and an LLM client, then commits via the persist
phase. The phases themselves don't know about the DB ; everything is
funneled through Protocol callbacks (cf. comprehend / match modules).

Cf. ``ARCH_receipt_pipeline.md`` § Plan de migration et § Anti-patterns.

Production wiring :

    run_pipeline(image_bytes, db=db, user_id=u, captured_at=now)

In tests, inject :

    run_pipeline(image_bytes, db=db, ..., llm_client=StubLLMClient({...}))

Anti-patterns interdits (cf. ARCH § Anti-patterns) :

- ❌ Catching ``ComprehendError`` / ``MatchError`` / ``PersistError`` to
  produce a half-state DB row. The orchestrator lets them propagate ;
  the caller (Celery task / route) decides whether to retry or mark a
  receipt as rejected.
- ❌ ``parsing_issues``-on-item → silent ``status='rejected'`` mapping.
  The ARCH original said ``parsing_status='partial' → rejected`` but
  ``types.py`` settled on a ``parsing_issues: tuple[str, ...]`` field
  whose semantics is "non-fatal warnings". Until product decides
  otherwise, the orchestrator surfaces an audit event for items that
  carry parsing_issues but does NOT pre-emptively reject them — the
  Phase 3 cascade is the canonical decision-maker.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session

from worker.pipeline import comprehend, extract, match, persist
from worker.pipeline.llm_clients import AnthropicLLMClient

if TYPE_CHECKING:
    from worker.pipeline.types import ParsedReceiptBarcode

logger = logging.getLogger(__name__)


def run_pipeline(
    image_bytes: bytes,
    *,
    db: Session,
    user_id: UUID | None = None,
    captured_at: datetime | None = None,
    receipt_id: UUID | None = None,
    log_level: str = "normal",
    llm_client: comprehend.LLMClient | None = None,
    debug: bool = False,
) -> dict[str, Any]:
    """Run the 4 phases end-to-end. Returns the persist result dict.

    Args:
        image_bytes: source image (JPEG/PNG bytes).
        db: SQLAlchemy session — caller commits.
        user_id: receipt owner. ``None`` is allowed (anonymous reprocess).
        captured_at: when the photo was taken / uploaded. Defaults to
            ``datetime.now(UTC)`` so the route handler can omit it for
            now-ish uploads ; tests / batch reprocess pin it explicitly.
        receipt_id: pre-allocated receipt id (Phase 1 generates one if
            absent). Pinned in production by the upload route so the
            response can return the id before the worker finishes.
        log_level: ``"verbose"``/``"normal"``/``"production"``. Forced
            to ``"verbose"`` when ``debug=True``.
        llm_client: injectable LLM. Defaults to
            :class:`AnthropicLLMClient` reading ``LLM_API_KEY`` from
            env (consistent with legacy bridge — cf.
            ``worker/pipeline/llm_filter.make_default_llm_filter``).
        debug: convenience flag — sets ``log_level='verbose'`` so audit
            events are emitted in full. Used by the contract test.

    Returns:
        ``{"parsed_ticket_id": UUID, "receipt_id": UUID,
        "scan_ids": list[UUID], "raw_receipt_text": str, ...}`` — see
        :func:`persist.persist_pipeline_result` for the persist-layer
        keys ; ``raw_receipt_text`` (added Phase C-4) is the flattened
        OCR text post-extract used by downstream promo detection.
        Callers may pass it to ``_award_scan_rewards`` to fire the
        ``promo_found`` action_type.

    Raises:
        ExtractError / ComprehendError / MatchError / PersistError —
        let propagate. The orchestrator NEVER swallows pipeline errors.
    """
    if debug:
        log_level = "verbose"
    if captured_at is None:
        captured_at = datetime.now(UTC)
    if llm_client is None:
        llm_client = AnthropicLLMClient()

    audit_logger = _make_db_audit_logger(db, log_level=log_level)

    # ── Phase 1 — Extract ────────────────────────────────────────────────
    raw = extract.extract_raw_ticket(
        image_bytes,
        captured_at=captured_at,
        receipt_id=receipt_id,
        audit_logger=audit_logger,
        log_level=log_level,
    )

    # ── Phase 2 — Comprehend ─────────────────────────────────────────────
    parsed = comprehend.comprehend_ticket(
        raw,
        llm_client=llm_client,
        ocr_knowledge_loader=_make_ocr_knowledge_loader(db),
        product_knowledge_loader=_make_product_knowledge_loader(db),
        barcode_parser=_make_barcode_parser(db),
        audit_logger=audit_logger,
        log_level=log_level,
    )

    # ── Phase 3 prep — surface parsing_issues without silent reject ──────
    _emit_parsing_issues_events(parsed, audit_logger=audit_logger)

    # ── Phase 3 — Match ──────────────────────────────────────────────────
    matched = match.match_ticket(
        parsed,
        product_by_ean=_make_product_by_ean(db),
        product_by_knowledge=_make_product_by_knowledge(db),
        consensus_exact=_make_consensus_exact(db),
        consensus_fuzzy=_make_consensus_fuzzy(db),
        retailer_resolver=_make_retailer_resolver(db),
        store_lookup=_make_store_lookup(db),
        store_by_code=_make_store_by_code(db),
        audit_logger=audit_logger,
        log_level=log_level,
    )

    # ── Phase 4 — Persist ────────────────────────────────────────────────
    result = persist.persist_pipeline_result(
        raw=raw,
        parsed=parsed,
        matched=matched,
        db=db,
        user_id=user_id,
        log_level=log_level,
    )

    audit_logger(
        phase="persist",
        level="normal",
        event="pipeline_completed",
        payload={
            "receipt_id": str(raw.receipt_id),
            "parsed_ticket_id": str(result["parsed_ticket_id"]),
            "scan_count": len(result["scan_ids"]),
            "store_status": matched.store_status,
        },
    )

    # Phase C-4 — surface the raw OCR text (post-extract, newline-
    # joined from RawBlock.text) so downstream reward emission can run
    # promo-signal regex detection. We do NOT persist this anywhere ;
    # the raw text is in-memory only, scoped to this call. Storing it
    # would need a migration + RGPD review (the text can carry
    # store name / receipt date / accidental PII — out of scope here).
    result["raw_receipt_text"] = _build_raw_receipt_text(raw)

    return result


def _build_raw_receipt_text(raw) -> str:
    """Flatten ``RawTicket.blocks`` into a single multi-line string.

    Used by Phase C-4 promo detection only. Order follows the raw
    block sequence from the OCR engine ; we do NOT re-do the spatial
    line assembly (``comprehend._assemble_lines``) since regex matching
    is layout-agnostic — keyword and negative-price patterns trigger
    on substring presence regardless of which OCR line they sat on.
    """
    return "\n".join(blk.text for blk in raw.blocks)


# ── DB-backed audit logger ────────────────────────────────────────────────


_LEVEL_RANK: dict[str, int] = {"production": 0, "normal": 1, "verbose": 2}


def _make_db_audit_logger(db: Session, *, log_level: str):
    """Build an :class:`extract.AuditLogger` callable that writes events
    to ``pipeline_audit_log`` filtered by current log_level.

    Audit failures are best-effort : per ARCH § Traçabilité, the audit
    log is forensic-grade but never gates the pipeline — a write
    failure (e.g. a trigger bug) is logged at WARNING and swallowed.
    """
    current_rank = _LEVEL_RANK.get(log_level, 1)

    def audit(
        *,
        phase: str,
        level: str,
        event: str,
        payload: dict | None = None,
    ) -> None:
        if _LEVEL_RANK.get(level, 1) > current_rank:
            return
        try:
            db.execute(
                text(
                    "INSERT INTO pipeline_audit_log "
                    "(phase, level, event, payload) "
                    "VALUES (:phase, :level, :event, CAST(:payload AS jsonb))"
                ),
                {
                    "phase": phase,
                    "level": level,
                    "event": event,
                    "payload": json.dumps(payload or {}),
                },
            )
        except Exception:
            logger.warning(
                "pipeline_audit_log insert failed (phase=%s event=%s) — best-effort skip",
                phase,
                event,
                exc_info=True,
            )

    return audit


# ── Lookup factories — DB wiring ──────────────────────────────────────────


def _make_ocr_knowledge_loader(db: Session):
    """Returns :class:`comprehend.OcrKnowledgeLoader`.

    Reads ``ocr_knowledge.corrected`` for a given raw OCR text. Returns
    ``None`` when no corrected mapping exists (the comprehend layer
    then keeps the raw text). Cf. ARCH § Knowledge tables.

    Filters on ``type='product_name'`` since ``ocr_knowledge`` is
    polymorphic (also stores retailer_header / brand_name / dismissal
    rows). Comprehend Phase 2 only consumes product-name corrections.
    """

    def lookup(raw_ocr: str) -> str | None:
        row = db.execute(
            text("SELECT corrected FROM ocr_knowledge WHERE raw_ocr = :raw AND type = 'product_name' LIMIT 1"),
            {"raw": raw_ocr},
        ).first()
        if row is None:
            return None
        return row.corrected if row.corrected else None

    return lookup


def _make_product_knowledge_loader(db: Session):
    """Returns a no-op :class:`comprehend.ProductKnowledgeLoader`.

    The legacy ``product_knowledge`` table was renamed to
    ``ocr_knowledge`` (migration 20260415_2300) and reshaped — it no
    longer maps a normalized label to a product EAN. ARCH
    ``§ Knowledge tables`` describes a future ``product_knowledge``
    table indexed by ``(store_id, normalized_label)`` ; until that
    table lands (post-bloc-7), this loader returns ``None`` so
    Phase 2 keeps :attr:`ParsedItem.barcode` empty and Phase 3
    cascades to fuzzy matching. Behaviour is intentional and explicit
    rather than silently swallowed — DP entry candidate.
    """

    def lookup(normalized_label: str) -> str | None:
        return None

    return lookup


def _make_product_by_ean(db: Session):
    """Returns :class:`match.ProductByEanLookup`."""

    def lookup(ean: str) -> dict | None:
        row = db.execute(
            text("SELECT ean, name FROM products WHERE ean = :ean LIMIT 1"),
            {"ean": ean},
        ).first()
        if row is None:
            return None
        return {"ean": row.ean, "label": row.name}

    return lookup


def _make_product_by_knowledge(db: Session):
    """Returns a no-op :class:`match.ProductByKnowledgeLookup`.

    Same caveat as :func:`_make_product_knowledge_loader` — the
    ``(store_id, normalized_label) → product_ean`` table specified by
    ARCH § Knowledge tables does NOT exist yet. Until it lands, this
    lookup always returns ``None`` so the Phase 3 cascade falls
    through to fuzzy matching. Documented behaviour, not a silent
    drop.
    """

    def lookup(normalized_label: str) -> dict | None:
        return None

    return lookup


def _make_retailer_resolver(db: Session):
    """Returns :class:`match.RetailerResolver`.

    Wraps :func:`repositories.retailer_resolution.resolve_retailer_id`
    (Bloc B helper) so the matcher can convert the resolved
    ``store_id`` to the cross-retailer consensus key. Returns ``None``
    when the store has no ``retailer_id`` (user-suggested pending
    admin validation, or detached) — the matcher's cascade then
    short-circuits the consensus stages with
    ``rejected_reason='no_retailer_for_consensus'``.
    """
    from repositories.retailer_resolution import resolve_retailer_id

    def resolve(store_id: UUID) -> UUID | None:
        return resolve_retailer_id(db, store_id)

    return resolve


def _make_consensus_exact(db: Session):
    """Returns :class:`match.ConsensusExactLookup`, retailer-keyed.

    Bloc C : wraps the canonical retailer-keyed
    :func:`repositories.name_resolution_repository.get_consensus_for_label`
    with ``source_type='receipt'`` pinned (the matcher cascade for
    ticket scans only consults the receipt ledger ; cross-source ESL
    matching is a separate stage in V2 backlog). Serializes the
    :class:`ConsensusResult` to the dict contract expected by the
    matcher : ``{"ean": str, "state": str}``.
    """
    from repositories.name_resolution_repository import get_consensus_for_label

    def lookup(retailer_id: UUID, normalized_label: str) -> dict | None:
        result = get_consensus_for_label(
            db,
            retailer_id=retailer_id,
            source_type="receipt",
            normalized_label=normalized_label,
        )
        if result is None:
            return None
        return {"ean": result.ean, "state": result.state.name}

    return lookup


def _make_consensus_fuzzy(db: Session):
    """Returns :class:`match.ConsensusFuzzyLookup`, retailer-keyed.

    Bloc C : wraps the canonical retailer-keyed
    :func:`repositories.name_resolution_repository.find_fuzzy_verified_consensus`
    with ``source_type='receipt'`` pinned. Serializes the matched
    neighbour to ``{"ean": str, "label": str, "similarity": float}``.

    Note : the ``similarity`` key is filled with the consensus
    ``top1_pct`` (a proxy — pg_trgm similarity is not exposed by the
    repository today). The matcher only uses this for
    :attr:`Candidate.score` ; persistence does not depend on it.
    """
    from repositories.name_resolution_repository import find_fuzzy_verified_consensus

    def lookup(retailer_id: UUID, cleaned_label: str) -> dict | None:
        result = find_fuzzy_verified_consensus(
            db,
            retailer_id=retailer_id,
            source_type="receipt",
            cleaned_label=cleaned_label,
        )
        if result is None:
            return None
        return {
            "ean": result.ean,
            "label": cleaned_label,
            "similarity": float(result.top1_pct) / 100.0,
        }

    return lookup


def _make_barcode_parser(db: Session):
    """Returns :class:`comprehend.BarcodeParser` bound to ``db``.

    Wraps :func:`worker.pipeline.barcode.parse_receipt_barcode` so
    Phase 2 stays oblivious to SQLAlchemy. The wrapper itself is stateless ;
    the DB hits happen on every call (per-retailer format lookup), so
    ``retailer_receipt_formats`` cache concerns live in the wrapper, not
    here.
    """
    from worker.pipeline.barcode import parse_receipt_barcode

    def parser(raw: str, retailer: str | None) -> ParsedReceiptBarcode:
        return parse_receipt_barcode(raw, retailer, db)

    return parser


def _make_store_by_code(db: Session):
    """Returns :class:`match.StoreByCodeLookup`.

    Direct SQL lookup on ``stores(retailer, store_code)`` — backed by the
    composite partial index ``ix_stores_retailer_store_code`` (PR-A). The
    ``retailer`` parameter is ALREADY normalized at the call site (the
    Phase-2 ParsedReceiptBarcode.retailer_key is the canonical key emitted
    by the barcode wrapper). Defensive re-normalization keeps the loader
    robust against future callers passing a raw brand string.

    Filters on ``is_disabled = false`` so soft-deleted stores never match.
    """
    from worker.ocr.store_detector import _normalize_retailer_key

    def lookup(retailer: str, store_code: str) -> dict | None:
        retailer_key = _normalize_retailer_key(retailer)
        row = db.execute(
            text(
                "SELECT id, name, address FROM stores "
                "WHERE retailer = :ret AND store_code = :code "
                "  AND is_disabled = false "
                "LIMIT 1"
            ),
            {"ret": retailer_key, "code": store_code},
        ).first()
        if row is None:
            return None
        return {
            "id": row.id,
            "name": row.name,
            "address": row.address,
            "score": 1.0,
        }

    return lookup


def _make_store_lookup(db: Session):
    """Returns :class:`match.StoreLookup`.

    Combines pg_trgm on ``stores.name_normalized`` + optional postcode
    exact filter. Returns top-N rows sorted by combined score (brand
    similarity weighted ; postcode match adds a bonus).

    Phase 3 owns threshold logic ; this loader just produces ranked
    candidates.
    """

    def lookup(
        *,
        brand: str | None,
        address_line: str | None,
        postcode: str | None,
        city: str | None,
        top_n: int = 3,
    ) -> list[dict]:
        # No brand → no fuzzy match possible (postcode alone is too weak).
        if not brand:
            return []
        # Compose : score on name_normalized, optional postcode exact bonus.
        # CAST(:postcode AS text) is required : psycopg cannot infer the
        # parameter type when the same placeholder appears in both an
        # IS NOT NULL test and an equality test (PG raises
        # AmbiguousParameter). Explicit cast keeps the prepared statement
        # well-typed.
        sql = (
            "SELECT id, name, address, "
            "       (word_similarity(name_normalized, :brand) "
            "        + CASE WHEN CAST(:postcode AS text) IS NOT NULL "
            "                  AND postal_code = CAST(:postcode AS text) "
            "               THEN 0.20 ELSE 0.0 END) AS score "
            "FROM stores "
            "WHERE is_disabled = false "
            "  AND word_similarity(name_normalized, :brand) >= 0.30 "
            "ORDER BY score DESC "
            "LIMIT :top_n"
        )
        rows = db.execute(
            text(sql),
            {
                "brand": brand,
                "postcode": postcode,
                "top_n": top_n,
            },
        ).all()
        results: list[dict] = []
        for r in rows:
            score = float(r.score)
            if score > 1.0:
                score = 1.0
            results.append(
                {
                    "id": r.id,
                    "name": r.name,
                    "address": r.address,
                    "score": score,
                }
            )
        return results

    return lookup


# ── Pre-Phase-3 audit hook ────────────────────────────────────────────────


def _emit_parsing_issues_events(parsed, *, audit_logger) -> None:
    """Emit a per-item audit event when ``parsing_issues`` is non-empty.

    The ARCH original spoke of mapping ``parsing_status='partial'`` to
    ``status='rejected'`` ; the v3 type contract uses
    :attr:`ParsedItem.parsing_issues` (tuple of short labels) instead.
    Until product confirms the strict mapping, items with parsing_issues
    flow through the Phase 3 cascade as usual — the rejected_reason
    written at persist time will already reflect the cascade outcome
    (``no_fuzzy_candidate`` / ``barcode_unknown_in_db`` / ...). The
    parsing_issues are visible via ``parsed_tickets.parsed_jsonb``
    (replayable) and surfaced here in the audit log so the data
    pipeline can quantify the split.
    """
    for item in parsed.items:
        if item.parsing_issues:
            audit_logger(
                phase="match",
                level="normal",
                event="item_has_parsing_issues",
                payload={
                    "parsed_item_id": str(item.id),
                    "parsing_issues": list(item.parsing_issues),
                    "raw_label": item.raw_label,
                },
            )


__all__ = ["run_pipeline"]
