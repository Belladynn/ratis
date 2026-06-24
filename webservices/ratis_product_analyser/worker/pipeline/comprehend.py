"""Phase 2 — Comprendre.

Transforme :class:`RawTicket` (Phase 1) en :class:`ParsedTicket` Pydantic
frozen via :

  1. spatial assembly (regroupement RawBlocks → lignes lisibles)
  2. LLM call (extraction structurée header / footer / items)
  3. knowledge integration
     - ``ocr_knowledge`` lookup AVANT LLM (corrige les blocks bruts
       par mapping appris ; cf. ARCH § Knowledge tables — read)
     - ``product_knowledge`` lookup APRÈS LLM (résout un EAN sur un
       label normalisé connu ; cf. ARCH § product_knowledge — read)
  4. ParsedTicket build + ``with_jsonb_hash()`` (idempotence —
     même inputs → même hash, cf. ARCH § Traçabilité).

Pure fonctionnel : aucune I/O DB ici. Knowledge lookups + LLM call
passent par des callbacks injectables (``Protocol``). Phase 4 wirera
le tout vers la DB sans toucher à ce module.

Anti-patterns explicitement bannis (cf. ARCH § Anti-patterns) :

- ❌ Drop silencieux d'un item à cause d'un total incohérent — on
  collecte l'incohérence dans :attr:`ParsedItem.parsing_issues` puis
  on garde l'item.
- ❌ Drop silencieux d'un block sans catégorie — la phase reste
  best-effort, mais si le LLM rend du JSON invalide
  on lève :class:`ComprehendError` (le worker aval décidera quoi
  faire).
- ❌ ``int(float * 100)`` — toute conversion d'argent passe par
  :class:`decimal.Decimal` (cf. CLAUDE.md § money).

Légende de design :

- Le cluster_blocks legacy (``worker/pipeline/cluster_blocks.py``)
  fait du clustering **multi-pass** (3 variants OCR → 1 cluster). On
  n'a qu'une seule passe en pipeline : on a donc besoin d'un
  spatial-line-assembly plus simple (group-by-y, sort-by-x). Code
  isolé ici, pas une duplication conceptuelle.
- Le LLM legacy (``worker/pipeline/llm_filter.py``) est un
  denoise+classify per-cluster. Le LLM v3 est un extracteur
  *structured*. Les deux capabilities cohabitent, sans recouvrement.
  Phase 4 pourra retire le legacy si jamais.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from collections.abc import Sequence
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Protocol
from uuid import UUID

from pydantic import ValidationError

from worker.pipeline.extract import AuditLogger, _noop_audit
from worker.pipeline.types import (
    ParsedFooter,
    ParsedHeader,
    ParsedItem,
    ParsedReceiptBarcode,
    ParsedTicket,
    RawBarcode,
    RawBlock,
    RawTicket,
    VatLine,
)

logger = logging.getLogger(__name__)


# ── Tunables ───────────────────────────────────────────────────────────────
# Empirical defaults. Documented constants ; if alpha shows split/merge
# issues we surface them through ``ratis_settings.json`` (R19) — for now
# the spec ships sane defaults so unit tests don't require DB / settings.
DEFAULT_Y_TOLERANCE_PX: float = 8.0
"""Two blocks belong to the same line when ``|y_block - y_line| <= 8``.
Empirically tuned : smaller (±2) misses sub-pixel jitter ; larger
(±50) merges adjacent receipt lines."""


# ── Knowledge loaders (Protocols) ──────────────────────────────────────────


class OcrKnowledgeLoader(Protocol):
    """Lookup ``ocr_knowledge.corrected`` for a raw block text.

    Returns the corrected text if a learned mapping exists, ``None``
    otherwise. Phase 4 wirera vers le repo DB ; les tests passent un
    stub fonction.
    """

    def __call__(self, raw_ocr: str) -> str | None: ...


class ProductKnowledgeLoader(Protocol):
    """Lookup ``product_knowledge`` for a candidate EAN given a normalized label.

    Returns the EAN if a high-confidence learned mapping exists,
    ``None`` otherwise. Cf. ARCH § Knowledge tables —
    ``product_knowledge.source ∈ {user_barcode_scan, fuzzy_high_confidence,
    manual_admin}``.
    """

    def __call__(self, normalized_label: str) -> str | None: ...


def _noop_ocr_knowledge(raw_ocr: str) -> str | None:
    return None


def _noop_product_knowledge(normalized_label: str) -> str | None:
    return None


# ── Receipt barcode parser (Protocol) ──────────────────────────────────────


class BarcodeParser(Protocol):
    """Parse a raw receipt barcode (ticket number, NOT a product EAN) into
    a structured :class:`ParsedReceiptBarcode`.

    Production wiring : ``worker.pipeline.barcode.parse_receipt_barcode``
    bound to the SQLAlchemy session via the orchestrator. Tests pass a
    stub that returns whatever fields are needed for the assertion.

    Contract :

    - Always returns a :class:`ParsedReceiptBarcode` (never ``None``).
    - ``raw`` is preserved on the returned model — even on parse failure.
    - Parsing is best-effort : retailer unknown / format mismatch surface
      via ``retailer_key=None`` and decoded fields ``None`` (no exception).
    """

    def __call__(self, raw: str, retailer: str | None) -> ParsedReceiptBarcode: ...


def _noop_barcode_parser(raw: str, retailer: str | None) -> ParsedReceiptBarcode:
    """Default no-op : preserve raw, no decoded fields. Used in tests
    that don't exercise the barcode wiring path."""
    return ParsedReceiptBarcode(raw=raw)


# Below this raw barcode length we skip the parser entirely. OCR junk
# strings under 10 chars are virtually never real receipt barcodes
# (typical lengths : Intermarché 24 / Monoprix 24 / etc.).
_MIN_BARCODE_RAW_LEN: int = 10


# ── LLM client (Protocol) ──────────────────────────────────────────────────


class LLMClient(Protocol):
    """Encapsulates the LLM call to extract structured data from receipt text.

    Production wiring (Anthropic / Mistral / OpenAI-compatible) lives
    in a separate adapter — this module only sees the Protocol so it
    stays pure-functional.
    """

    def extract(
        self,
        *,
        receipt_text: str,
        barcodes: list[str],
        prompt_template: str,
    ) -> dict[str, Any]:
        """Return a JSON-ish dict shaped as :data:`_PROMPT_TEMPLATE` describes."""
        ...


# ── Errors ─────────────────────────────────────────────────────────────────


class ComprehendError(Exception):
    """Raised when LLM output is malformed or fails Pydantic validation.

    Per ARCH § Anti-patterns, we never swallow such errors — a silent
    drop would leave a Receipt with no parsed ticket and no audit
    trail explaining why. The orchestrator (Phase 4 / Celery task)
    catches and translates into ``status='rejected'`` with
    ``rejected_reason`` populated.
    """


# ── Public API ─────────────────────────────────────────────────────────────


def comprehend_ticket(
    raw: RawTicket,
    *,
    llm_client: LLMClient,
    ocr_knowledge_loader: OcrKnowledgeLoader = _noop_ocr_knowledge,
    product_knowledge_loader: ProductKnowledgeLoader = _noop_product_knowledge,
    barcode_parser: BarcodeParser = _noop_barcode_parser,
    audit_logger: AuditLogger = _noop_audit,
    log_level: str = "normal",
) -> ParsedTicket:
    """Run Phase 2. Return a frozen :class:`ParsedTicket` with ``parsed_jsonb_hash`` set.

    Steps :

    1. ``_assemble_lines(raw.blocks)`` — spatial → ``list[Line]``
    2. ``_apply_ocr_knowledge(...)`` — corrects raw text per line
    3. ``llm_client.extract(...)`` — structured JSON
    4. parse JSON → :class:`ParsedHeader`, :class:`ParsedFooter`,
       :class:`ParsedItem`
    5. ``_apply_product_knowledge(items, loader)`` — may set
       :attr:`ParsedItem.barcode` from a learned mapping
    6. ``ParsedTicket(...).with_jsonb_hash()`` — idempotent hash

    Raises:
        ComprehendError: if the LLM returns malformed JSON or the data
            cannot be coerced into the Pydantic schema. No silent drop.
    """
    audit_logger(
        phase="comprehend",
        level="normal",
        event="comprehend_started",
        payload={
            "receipt_id": str(raw.receipt_id),
            "block_count": len(raw.blocks),
            "barcode_count": len(raw.barcodes),
            "log_level": log_level,
        },
    )

    lines = _assemble_lines(list(raw.blocks), audit_logger=audit_logger, log_level=log_level)
    corrected_lines = _apply_ocr_knowledge(
        lines,
        ocr_knowledge_loader,
        audit_logger=audit_logger,
        log_level=log_level,
    )

    receipt_text = _lines_to_text(corrected_lines)
    barcodes_for_llm = [b.value for b in raw.barcodes]

    try:
        llm_output = llm_client.extract(
            receipt_text=receipt_text,
            barcodes=barcodes_for_llm,
            prompt_template=_PROMPT_TEMPLATE,
        )
    except Exception as exc:
        # Any LLM transport / parse error : surface it as a
        # ComprehendError so the worker aval can decide
        # (retry / mark rejected).
        raise ComprehendError(f"LLM call failed: {exc}") from exc

    if not isinstance(llm_output, dict):
        raise ComprehendError(f"LLM output must be a dict, got {type(llm_output).__name__}")

    audit_logger(
        phase="comprehend",
        level="normal",
        event="llm_extraction_done",
        payload={
            "item_count_raw": _safe_len(llm_output.get("items", [])),
        },
    )

    header = _build_header(llm_output.get("header") or {}, lines)
    footer = _build_footer(
        llm_output.get("footer") or {},
        lines,
        barcode_parser=barcode_parser,
        retailer=header.brand,
        audit_logger=audit_logger,
        log_level=log_level,
    )
    items = _build_items(llm_output.get("items"), lines, raw.barcodes)
    items = _apply_product_knowledge(
        items,
        product_knowledge_loader,
        audit_logger=audit_logger,
        log_level=log_level,
    )

    try:
        ticket = ParsedTicket(
            receipt_id=raw.receipt_id,
            items=tuple(items),
            header=header,
            footer=footer,
            purchased_at=_extract_purchased_at(corrected_lines),
            raw_ticket_image_hash=raw.image_hash,
        ).with_jsonb_hash()
    except ValidationError as exc:
        raise ComprehendError(f"ParsedTicket validation failed: {exc}") from exc

    audit_logger(
        phase="comprehend",
        level="normal",
        event="parsed_ticket_built",
        payload={
            "parsed_ticket_id": str(ticket.id),
            "parsed_jsonb_hash": ticket.parsed_jsonb_hash,
            "item_count": len(ticket.items),
            "store_brand": ticket.header.brand,
        },
    )
    return ticket


# ── Helpers privés ─────────────────────────────────────────────────────────


# A "Line" is a tuple of RawBlocks sharing the same y row, sorted by x.
Line = tuple[RawBlock, ...]


def _assemble_lines(
    blocks: Sequence[RawBlock],
    *,
    audit_logger: AuditLogger,
    log_level: str,
    y_tolerance: float = DEFAULT_Y_TOLERANCE_PX,
) -> list[Line]:
    """Group blocks into spatial lines (y-cluster), sort by x within line.

    Algorithm :

    1. Sort by ``y_center`` ascending.
    2. Greedy : a block joins the current line if ``|y_center - y_anchor|
       <= y_tolerance``. The anchor is the y of the first block of the
       line — we don't update it on attachment so a stream of slightly
       drifting blocks doesn't merge with the next line.
    3. Within each line, sort by ``x_center`` ascending.

    Returns a list of tuples (immutable lines, frozen blocks). Pure :
    does not mutate the input list.
    """
    if not blocks:
        if log_level == "verbose":
            audit_logger(
                phase="comprehend",
                level="verbose",
                event="assemble_lines_empty_input",
                payload=None,
            )
        return []

    by_y = sorted(blocks, key=_block_center_y)
    lines: list[list[RawBlock]] = []
    current: list[RawBlock] = []
    anchor_y: float | None = None

    for blk in by_y:
        cy = _block_center_y(blk)
        if anchor_y is None or abs(cy - anchor_y) > y_tolerance:
            if current:
                lines.append(current)
            current = [blk]
            anchor_y = cy
        else:
            current.append(blk)
    if current:
        lines.append(current)

    # Sort by x within each line.
    sorted_lines: list[Line] = [tuple(sorted(line, key=_block_center_x)) for line in lines]

    if log_level == "verbose":
        audit_logger(
            phase="comprehend",
            level="verbose",
            event="assemble_lines_done",
            payload={
                "block_count": len(blocks),
                "line_count": len(sorted_lines),
            },
        )
    return sorted_lines


def _block_center_y(blk: RawBlock) -> float:
    _x, y, _w, h = blk.bbox
    return float(y) + float(h) / 2.0


def _block_center_x(blk: RawBlock) -> float:
    x, _y, w, _h = blk.bbox
    return float(x) + float(w) / 2.0


def _apply_ocr_knowledge(
    lines: list[Line],
    loader: OcrKnowledgeLoader,
    *,
    audit_logger: AuditLogger,
    log_level: str,
) -> list[Line]:
    """Substitute each block's text by ``ocr_knowledge.corrected`` when a hit exists.

    Pure : returns new ``RawBlock`` instances (frozen) with the
    corrected text. Original ``content_hash`` is preserved — the
    correction is a layer on top of the OCR output, not a re-OCR.
    Lineage stays anchored to the original block id.

    NOTE on content_hash : the hash is computed from raw OCR
    text+bbox+confidence (Phase 1 contract). After correction the text
    field disagrees with the hash. This is intentional : the hash is
    immutable lineage to Phase 1. Downstream (Phase 4) persists the
    correction as ``ocr_knowledge`` row, not as a mutated block.
    """
    out: list[Line] = []
    for line in lines:
        new_line: list[RawBlock] = []
        for blk in line:
            corrected = loader(blk.text)
            if corrected is not None and corrected != blk.text:
                if log_level == "verbose":
                    audit_logger(
                        phase="comprehend",
                        level="verbose",
                        event="ocr_knowledge_hit",
                        payload={
                            "block_id": str(blk.id),
                            "raw_ocr": blk.text,
                            "corrected": corrected,
                        },
                    )
                # Build a copy carrying the corrected text. We keep the
                # original content_hash : the hash is the Phase 1 imprint,
                # never re-derived after correction.
                new_line.append(blk.model_copy(update={"text": corrected}))
            else:
                new_line.append(blk)
        out.append(tuple(new_line))
    return out


def _lines_to_text(lines: list[Line]) -> str:
    """Flatten lines back to a multi-line string for the LLM prompt."""
    return "\n".join(" ".join(blk.text for blk in line) for line in lines)


def _build_header(header_json: dict[str, Any], lines: list[Line]) -> ParsedHeader:
    """Coerce LLM header JSON → :class:`ParsedHeader` with best-effort lineage.

    SIRET validation : the Pydantic model rejects non-14-digit input ;
    we pre-filter so the LLM passing junk doesn't blow up the whole
    phase. Same defensive treatment for postcode (5-digit FR).
    """
    raw_siret = _none_if_blank(header_json.get("siret"))
    siret = raw_siret if raw_siret and re.fullmatch(r"\d{14}", raw_siret) else None

    raw_postcode = _none_if_blank(header_json.get("postcode"))
    postcode = raw_postcode if raw_postcode and re.fullmatch(r"\d{5}", raw_postcode) else None

    candidate_texts = [
        _none_if_blank(header_json.get("brand")),
        _none_if_blank(header_json.get("address_line")),
        postcode,
        _none_if_blank(header_json.get("city")),
        _none_if_blank(header_json.get("phone")),
        siret,
    ]
    source_block_ids = _match_block_ids(candidate_texts, lines)

    try:
        return ParsedHeader(
            brand=_none_if_blank(header_json.get("brand")),
            address_line=_none_if_blank(header_json.get("address_line")),
            postcode=postcode,
            city=_none_if_blank(header_json.get("city")),
            phone=_none_if_blank(header_json.get("phone")),
            siret=siret,
            source_block_ids=tuple(source_block_ids),
        )
    except ValidationError as exc:
        raise ComprehendError(f"ParsedHeader validation failed: {exc}") from exc


def _build_footer(
    footer_json: dict[str, Any],
    lines: list[Line],
    *,
    barcode_parser: BarcodeParser = _noop_barcode_parser,
    retailer: str | None = None,
    audit_logger: AuditLogger = _noop_audit,
    log_level: str = "normal",
) -> ParsedFooter:
    """Coerce LLM footer JSON → :class:`ParsedFooter`. Money via :class:`Decimal`.

    PR-B wiring : when the LLM emits a footer ``barcode_ticket`` raw of
    sufficient length, we delegate decoding to the injected
    :class:`BarcodeParser`. The parser always returns a
    :class:`ParsedReceiptBarcode` (raw-only on miss), so we never lose
    the raw barcode even if no retailer format hits.

    Audit (verbose only) :

    - ``barcode_parsed`` if the parser populated at least one decoded
      field beyond ``raw``.
    - ``barcode_unparsed`` if only ``raw`` came back (retailer unknown
      OR format mismatch).
    """
    total_cents = _coerce_cents(footer_json.get("total_cents"))
    item_count_declared = _coerce_int(footer_json.get("item_count_declared"))
    payment_method = _none_if_blank(footer_json.get("payment_method"))
    # Accept either ``barcode_ticket`` (canonical prompt key) or
    # ``barcode`` (defensive fallback if the LLM normalises the name).
    raw_barcode_field = footer_json.get("barcode_ticket")
    if raw_barcode_field is None:
        raw_barcode_field = footer_json.get("barcode")
    barcode_ticket_raw = _none_if_blank(raw_barcode_field)
    barcode: ParsedReceiptBarcode | None = None
    if barcode_ticket_raw is not None and len(barcode_ticket_raw) >= _MIN_BARCODE_RAW_LEN:
        barcode = barcode_parser(barcode_ticket_raw, retailer)
        if log_level == "verbose":
            has_decoded_fields = any(
                getattr(barcode, name) is not None
                for name in ("retailer_key", "store_code", "caisse", "tx_id", "date", "time")
            )
            audit_logger(
                phase="comprehend",
                level="verbose",
                event="barcode_parsed" if has_decoded_fields else "barcode_unparsed",
                payload={
                    "raw": barcode.raw,
                    "retailer_key": barcode.retailer_key,
                    "store_code": barcode.store_code,
                    "has_date": barcode.date is not None,
                    "has_time": barcode.time is not None,
                },
            )

    vat_breakdown_raw = footer_json.get("vat_breakdown") or []
    if not isinstance(vat_breakdown_raw, list):
        raise ComprehendError(f"footer.vat_breakdown must be a list, got {type(vat_breakdown_raw).__name__}")

    vat_lines: list[VatLine] = []
    for entry in vat_breakdown_raw:
        if not isinstance(entry, dict):
            continue
        try:
            vat_lines.append(
                VatLine(
                    rate_pct=float(entry.get("rate_pct", 0.0)),
                    taxable_cents=_coerce_cents(entry.get("taxable_cents")) or 0,
                    tax_cents=_coerce_cents(entry.get("tax_cents")) or 0,
                    source_block_ids=(),
                )
            )
        except (ValidationError, ValueError, TypeError) as exc:
            raise ComprehendError(f"VatLine invalid: {exc}") from exc

    candidate_texts = [
        payment_method,
        barcode_ticket_raw,
        str(total_cents) if total_cents is not None else None,
    ]
    source_block_ids = _match_block_ids(candidate_texts, lines)

    try:
        return ParsedFooter(
            total_cents=total_cents,
            vat_breakdown=tuple(vat_lines),
            payment_method=payment_method,
            item_count_declared=item_count_declared,
            barcode=barcode,
            source_block_ids=tuple(source_block_ids),
        )
    except ValidationError as exc:
        raise ComprehendError(f"ParsedFooter validation failed: {exc}") from exc


def _build_items(
    items_json: Any,
    lines: list[Line],
    barcodes: tuple[RawBarcode, ...],
) -> list[ParsedItem]:
    """Coerce LLM items list → ``list[ParsedItem]``. Per-item validation strict."""
    if items_json is None:
        return []
    if not isinstance(items_json, list):
        raise ComprehendError(f"items must be a list, got {type(items_json).__name__}")

    items: list[ParsedItem] = []
    barcode_values = {b.value for b in barcodes}

    for raw_item in items_json:
        if not isinstance(raw_item, dict):
            raise ComprehendError(f"each item must be a dict, got {type(raw_item).__name__}")
        raw_label = str(raw_item.get("raw_label", "")).strip()
        if not raw_label:
            raise ComprehendError("item.raw_label is required and must be non-empty")

        normalized_label = _normalize_label(raw_label)
        quantity = _coerce_int(raw_item.get("quantity")) or 1
        if quantity < 1:
            quantity = 1
        unit_price_cents = _coerce_cents(raw_item.get("unit_price_cents"))
        total_cents = _coerce_cents(raw_item.get("total_cents"))
        if total_cents is None:
            raise ComprehendError(f"item.total_cents is required (raw_label={raw_label!r})")

        # Barcode hint : LLM may pass a value ; keep it only if it matches one
        # of the physical barcodes pyzbar saw (defensive against hallucination).
        llm_barcode = _none_if_blank(raw_item.get("barcode"))
        barcode = llm_barcode if llm_barcode and llm_barcode in barcode_values else None

        # Coherence check (non-fatal — collected, not raising).
        parsing_issues: list[str] = []
        if unit_price_cents is not None and quantity * unit_price_cents != total_cents:
            parsing_issues.append("total_unit_qty_mismatch")

        source_block_ids = _match_block_ids([raw_label], lines)

        try:
            items.append(
                ParsedItem(
                    raw_label=raw_label,
                    normalized_label=normalized_label,
                    quantity=quantity,
                    unit_price_cents=unit_price_cents,
                    total_cents=total_cents,
                    barcode=barcode,
                    source_block_ids=tuple(source_block_ids),
                    parsing_issues=tuple(parsing_issues),
                )
            )
        except ValidationError as exc:
            raise ComprehendError(f"ParsedItem validation failed: {exc}") from exc
    return items


def _apply_product_knowledge(
    items: list[ParsedItem],
    loader: ProductKnowledgeLoader,
    *,
    audit_logger: AuditLogger,
    log_level: str,
) -> list[ParsedItem]:
    """For each item without a barcode, lookup ``product_knowledge`` and set it on hit.

    Items already carrying a barcode (from pyzbar / spatial pairing)
    are left untouched — the loader is **not called** for them, which
    saves a round-trip in Phase 4 (loader will hit DB).
    """
    out: list[ParsedItem] = []
    for it in items:
        if it.barcode is not None:
            out.append(it)
            continue
        ean = loader(it.normalized_label)
        if ean is None:
            out.append(it)
            continue
        if log_level == "verbose":
            audit_logger(
                phase="comprehend",
                level="verbose",
                event="product_knowledge_hit",
                payload={
                    "parsed_item_id": str(it.id),
                    "normalized_label": it.normalized_label,
                    "ean": ean,
                },
            )
        out.append(it.model_copy(update={"barcode": ean}))
    return out


# ── Date extraction ────────────────────────────────────────────────────────

# FR receipt dates : 30/04/26, 30/04/2026, 30-04-2026 (most common shapes).
_DATE_RE = re.compile(r"\b(\d{2})[/.\-](\d{2})[/.\-](\d{2}|\d{4})\b")


def _extract_purchased_at(lines: list[Line]) -> datetime | None:
    """Best-effort date extraction. Return ``None`` if no plausible date found.

    Per ``ParsedTicket.purchased_at`` contract : never use a sentinel,
    just return ``None``.
    """
    for line in lines:
        text = " ".join(blk.text for blk in line)
        match = _DATE_RE.search(text)
        if not match:
            continue
        d, m, y = match.groups()
        year = int(y)
        if year < 100:
            year += 2000
        try:
            return datetime(year=year, month=int(m), day=int(d), tzinfo=UTC)
        except ValueError:
            continue
    return None


# ── Coercion helpers ───────────────────────────────────────────────────────


def _none_if_blank(value: Any) -> str | None:
    """Coerce empty string, ``None``, or pure-whitespace into ``None``."""
    if value is None:
        return None
    if not isinstance(value, str):
        value = str(value)
    stripped = value.strip()
    return stripped or None


def _coerce_int(value: Any) -> int | None:
    """Coerce to int, tolerant of strings ; return ``None`` on absence/blank."""
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _coerce_cents(value: Any) -> int | None:
    """Coerce a money value to int-cents using :class:`Decimal`.

    Accepts ints (already cents), int-shaped strings ("1234"), or
    fails open to ``None`` on garbage. Per CLAUDE.md § money :
    NEVER ``int(float * 100)``. We assume the LLM is asked for cents
    directly (per the prompt) ; if it sends euros the integration
    fault gets caught downstream by the parsing_issues checks.
    """
    if value is None or value == "":
        return None
    if isinstance(value, bool):  # bool is a subtype of int — guard against True/False
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    try:
        dec = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None
    if dec < 0:
        return None
    # Round half-even to avoid float noise if the LLM ever returns "12.0".
    return int(dec.to_integral_value(rounding="ROUND_HALF_UP"))


def _normalize_label(raw: str) -> str:
    """UPPERCASE + accents-folded, NFKD strip diacritics."""
    decomposed = unicodedata.normalize("NFKD", raw)
    no_accents = decomposed.encode("ascii", "ignore").decode("ascii")
    return no_accents.upper().strip()


def _match_block_ids(
    candidate_texts: Sequence[str | None],
    lines: list[Line],
) -> list[UUID]:
    """Best-effort lineage : return the ids of blocks whose text contains any candidate.

    Cheap heuristic — exact substring match (case-insensitive). A
    fuzzy matcher would be more accurate but Phase 4 wirera vers
    ``parsed_block_links`` table where lineage is computed
    deterministically. The ARCH says lineage is mandatory but allows
    "best effort" at this layer.
    """
    needles = [t.upper() for t in candidate_texts if t]
    if not needles:
        return []
    seen: set[UUID] = set()
    ids: list[UUID] = []
    for line in lines:
        for blk in line:
            haystack = blk.text.upper()
            for needle in needles:
                if needle and needle in haystack:
                    if blk.id not in seen:
                        seen.add(blk.id)
                        ids.append(blk.id)
                    break
    return ids


def _safe_len(value: Any) -> int:
    try:
        return len(value)
    except TypeError:
        return 0


# ── Prompt template ────────────────────────────────────────────────────────

_PROMPT_TEMPLATE = """\
Voici le contenu d'un ticket de caisse francais. Extrais les informations
structurees au format JSON suivant :
{
  "header": {
    "brand": "<nom enseigne, ex: 'INTERMARCHE'>" ou null,
    "address_line": "<rue + numero>" ou null,
    "postcode": "<code postal 5 chiffres>" ou null,
    "city": "<ville>" ou null,
    "phone": "<numero telephone>" ou null,
    "siret": "<14 chiffres>" ou null
  },
  "footer": {
    "total_cents": <total en centimes, integer> ou null,
    "vat_breakdown": [{"rate_pct": <float>, "taxable_cents": <int>, "tax_cents": <int>}, ...],
    "payment_method": "<CB / ESPECES / TICKET RESTO / ...>" ou null,
    "item_count_declared": <int> ou null,
    "barcode_ticket": "<numero ticket interne>" ou null
  },
  "items": [
    {
      "raw_label": "<texte tel quel sur le ticket>",
      "quantity": <int >= 1>,
      "unit_price_cents": <int ou null>,
      "total_cents": <int>,
      "barcode": "<EAN si present dans la zone du ticket de cet item>" ou null
    }
  ]
}

IMPORTANT :
- Ne fabrique pas de donnees. Si un champ n'est pas lisible, mets null.
- Tous les montants sont en centimes (integer), jamais en euros.
- Inclus uniquement les items reels achetes. Ignore les en-tetes / footers /
  promotions / remises globales.
- Si le label est degueulasse, garde-le tel quel dans raw_label — le pipeline
  normalisera.

Texte du ticket :
{receipt_text}

Codes-barres lus :
{barcodes}
"""


__all__ = [
    "DEFAULT_Y_TOLERANCE_PX",
    "BarcodeParser",
    "ComprehendError",
    "LLMClient",
    "OcrKnowledgeLoader",
    "ProductKnowledgeLoader",
    "comprehend_ticket",
]
