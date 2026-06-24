"""Pydantic v2 type contracts for the pipeline (receipt OCR rewrite).

Reference : ``webservices/ratis_product_analyser/ARCH_receipt_pipeline.md``
(sections "Les 4 phases" and "Traçabilité").

All models are **frozen** (``ConfigDict(frozen=True)``) — once
constructed they cannot be mutated. Build a new instance with the
desired changes if needed (cf. :meth:`ParsedTicket.with_jsonb_hash`).

Phase 1 (Extract) :
- :class:`RawBlock`, :class:`RawBarcode`, :class:`RawTicket`

Phase 2 (Comprendre) :
- :class:`VatLine`, :class:`ParsedHeader`, :class:`ParsedFooter`,
  :class:`ParsedItem`, :class:`ParsedTicket`

Phase 3 (Matcher) :
- :class:`Candidate` (a single product candidate considered by the
  matching engine)
- :class:`DecisionInputs` (snapshot of the inputs that drove a match
  decision — for reproducibility / audit)
- :class:`ItemMatch` (with cross-field invariants on ``status`` /
  ``match_method`` / ``rejected_reason`` / ``match_confidence``)
- :class:`MatchedTicket` (with cross-field invariants on
  ``store_status`` / ``store_match_id`` / ``store_rejected_reason``)

Phase 4 (Persister) — owned by a later bloc, it consumes
:class:`MatchedTicket` and writes the DB rows.

Hashing helpers
---------------

``content_hash`` fields are sha256-hex digests over the semantic
content of an entity. They are **inputs** to the model — the helpers
below produce the canonical bytes-to-hash for each entity type so
upstream code can compute the hash before constructing the model
instance. The hash is reproducible : same inputs → same hash.

``parsed_jsonb_hash`` is special : it depends on the entire
:class:`ParsedTicket` instance, so it cannot be set at construction
time. It is computed via :meth:`ParsedTicket.with_jsonb_hash`, which
returns a new immutable instance with the field populated. The hash
itself is excluded from its own input (the JSON dump excludes the
field), so calling ``with_jsonb_hash`` on an already-hashed ticket
yields the same hash.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import date as date_cls
from datetime import datetime
from datetime import time as time_cls
from typing import Literal
from uuid import UUID, uuid4

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

# ---------------------------------------------------------------------------
# Hashing helpers (pure, deterministic — no time, no randomness)
# ---------------------------------------------------------------------------


def _sha256_hex(data: bytes) -> str:
    """Return the hexadecimal sha256 digest of ``data`` (UTF-8 bytes)."""
    return hashlib.sha256(data).hexdigest()


def compute_block_hash(text: str, bbox: tuple[float, float, float, float], confidence: float) -> str:
    """Reproducible content hash for a :class:`RawBlock`.

    The hash covers (text, bbox rounded as-is, confidence rounded to
    4 decimals). Confidence is rounded to absorb float noise from
    repeated PaddleOCR runs on the same input ; without rounding,
    ``0.91234567`` and ``0.91234568`` (numerically equivalent for our
    purposes) would yield different hashes and break the regression
    detector.
    """
    payload = f"{text}|{tuple(bbox)!r}|{round(confidence, 4):.4f}"
    return _sha256_hex(payload.encode("utf-8"))


def compute_barcode_hash(value: str, format: str, bbox: tuple[float, float, float, float]) -> str:
    """Reproducible content hash for a :class:`RawBarcode`."""
    payload = f"{value}|{format}|{tuple(bbox)!r}"
    return _sha256_hex(payload.encode("utf-8"))


def compute_image_hash(image_bytes: bytes) -> str:
    """sha256 hex of the raw image bytes — the source-of-truth fingerprint."""
    return _sha256_hex(image_bytes)


def compute_parsed_jsonb_hash(parsed_ticket: "ParsedTicket") -> str:
    """Canonical JSON sha256 of a :class:`ParsedTicket`, excluding the
    ``parsed_jsonb_hash`` field itself.

    Serialization uses ``mode='json'`` (UUIDs → str, datetimes → ISO,
    tuples → lists) and ``sort_keys=True`` to guarantee a stable byte
    representation across Python runs.
    """
    payload = parsed_ticket.model_dump(mode="json", exclude={"parsed_jsonb_hash"})
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return _sha256_hex(canonical.encode("utf-8"))


# ---------------------------------------------------------------------------
# Phase 1 — Extract
# ---------------------------------------------------------------------------


class RawBlock(BaseModel):
    """One OCR text block produced by PaddleOCR.

    Born in Phase 1 with an immutable ``id`` and ``content_hash``. The
    hash is reproducible across runs — used to detect upstream
    regressions (PaddleOCR version change, preprocessing change, ...).
    """

    model_config = ConfigDict(frozen=True)

    id: UUID = Field(default_factory=uuid4)
    text: str
    bbox: tuple[float, float, float, float]
    """(x, y, w, h) — pixel coordinates as returned by the OCR engine."""
    confidence: float = Field(ge=0.0, le=1.0)
    content_hash: str
    """sha256 hex of (text, bbox, round(confidence, 4)). Use
    :func:`compute_block_hash` to compute."""


class RawBarcode(BaseModel):
    """One physical barcode read by pyzbar (or fallback OCR pattern)."""

    model_config = ConfigDict(frozen=True)

    value: str
    format: str
    """e.g. ``"EAN13"``, ``"EAN8"``, ``"CODE128"``, ``"QR"``, ..."""
    bbox: tuple[float, float, float, float]
    content_hash: str
    """sha256 hex of (value, format, bbox). Use
    :func:`compute_barcode_hash` to compute."""


class RawTicket(BaseModel):
    """Output of Phase 1 — purely OCR-level, no business decisions yet.

    ``image_hash`` is the sha256 of the source image bytes — propagates
    as ``raw_ticket_image_hash`` into :class:`ParsedTicket` for end-to-end
    traceability (per ARCH § Traçabilité).

    ``captured_at`` is when the photo was taken / uploaded by the
    client. Provided by the caller (route handler), not inferred here.
    """

    model_config = ConfigDict(frozen=True)

    receipt_id: UUID
    blocks: tuple[RawBlock, ...]
    barcodes: tuple[RawBarcode, ...]
    image_hash: str
    """sha256 hex of the source image bytes. Use :func:`compute_image_hash`."""
    ocr_engine_version: str
    """e.g. ``"paddleocr-2.7.3-fr"``. Bumped on engine upgrade."""
    captured_at: datetime


# ---------------------------------------------------------------------------
# Phase 2 — Comprendre
# ---------------------------------------------------------------------------


_SIRET_RE = re.compile(r"^\d{14}$")


class VatLine(BaseModel):
    """One VAT (TVA) line printed in the receipt footer.

    A receipt typically lists 1–3 VAT brackets (5.5 %, 10 %, 20 %) with
    the taxable base and the tax amount per bracket. All amounts are
    integer cents (cf. CLAUDE.md § money).
    """

    model_config = ConfigDict(frozen=True)

    rate_pct: float
    """VAT rate as a percentage (5.5, 10.0, 20.0 …) — raw OCR value, no
    normalization."""
    taxable_cents: int = Field(ge=0)
    """Taxable base in integer cents."""
    tax_cents: int = Field(ge=0)
    """VAT amount applied in integer cents."""
    source_block_ids: tuple[UUID, ...]
    """The :attr:`RawBlock.id` values that contributed to this VAT line."""


class ParsedHeader(BaseModel):
    """Store / merchant information extracted from the ticket header.

    All fields are :class:`Optional` (best-effort parse) except the
    lineage ``source_block_ids``. A header on a noisy receipt may yield
    every field as ``None`` — that is acceptable, downstream phases
    will handle the missing data explicitly.

    SIRET, when present, must be exactly 14 digits (regex
    ``^\\d{14}$``). No "magic" parsing — the OCR raw value is filtered
    against the strict format ; anything else raises a
    :class:`pydantic.ValidationError` so the upstream caller is forced
    to either validate or pass ``None``.
    """

    model_config = ConfigDict(frozen=True)

    brand: str | None = None
    """Merchant brand (Intermarché, Carrefour, …) — raw header text."""
    address_line: str | None = None
    """Single-line street address as printed on the ticket."""
    postcode: str | None = None
    city: str | None = None
    phone: str | None = None
    """Raw phone number text — no E.164 normalization at this layer."""
    siret: str | None = None
    """SIRET (14 digits) when printed on the ticket — many tickets omit
    it. When non-null, must match ``^\\d{14}$``."""
    source_block_ids: tuple[UUID, ...]
    """The :attr:`RawBlock.id` values that contributed to this header.
    Lineage back to Phase 1."""

    @field_validator("siret")
    @classmethod
    def _validate_siret_shape(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not _SIRET_RE.match(value):
            raise ValueError(
                "ParsedHeader.siret must be exactly 14 digits when non-null (no spaces, no letters, no separators)"
            )
        return value


class ParsedReceiptBarcode(BaseModel):
    """Decoded fields from a receipt's internal ticket-number barcode.

    Born in Phase 2 (v3) — the wrapper around the legacy ``pyzbar`` reader
    + per-retailer format config (``retailer_receipt_formats`` table).

    All fields except ``raw`` are :class:`Optional` because parsing
    degrades gracefully :

    - ``raw`` is always set if a barcode was physically read by pyzbar.
    - ``retailer_key`` is ``None`` when no retailer was detected for the
      receipt OR when no format config exists for that retailer.
    - ``store_code`` / ``caisse`` / ``tx_id`` / ``date`` / ``time`` are
      parsed when a format hits, ``None`` otherwise.
    - ``extra`` is forward-compatible : retailers with non-canonical
      fields (e.g. ``loyalty_id``) surface them here without requiring
      a Pydantic schema bump.

    Date/time interpretation uses **length inference** matching the
    legacy ``_parse_barcode_date`` (see ``worker/receipt_task.py``) :

    - 8-digit date → ``YYYYMMDD`` ; 6-digit → ``YYMMDD`` (century 2000).
    - 4-digit time → ``HHMM`` ; 6-digit → ``HHMMSS``.

    Anything else is silently rejected and the field stays ``None``.
    """

    model_config = ConfigDict(frozen=True)

    raw: str
    """The raw barcode value as decoded by pyzbar (digits only)."""
    retailer_key: str | None = None
    """Normalized retailer key (lowercase, accents stripped, spaces →
    underscores). ``None`` when no retailer could be associated with a
    known format config."""
    store_code: str | None = None
    """Per-retailer store code as printed in the barcode (NOT the
    :class:`Store` UUID). Used by Phase 3 to look up the matching row
    in ``stores`` via ``ix_stores_retailer_store_code``."""
    caisse: str | None = None
    """Cash-register / lane number as encoded in the barcode."""
    tx_id: str | None = None
    """Transaction id as encoded in the barcode (per-retailer format)."""
    date: date_cls | None = None
    """Purchase date parsed from the barcode (length-inferred). Useful
    as a cross-check against the OCR-read date on the receipt body."""
    time: time_cls | None = None
    """Purchase time parsed from the barcode (length-inferred)."""
    extra: dict[str, str] | None = None
    """Forward-compat : any parsed field beyond the canonical 5
    (``store_code`` / ``caisse`` / ``tx_id`` / ``date`` / ``time``).
    ``None`` when no extra fields were present (canonical-only format)."""


class ParsedFooter(BaseModel):
    """Receipt footer (totals, VAT breakdown, payment, item count).

    All amounts are integer cents — never floats. ``None`` means the
    field could not be parsed (never use ``0`` as a sentinel). The
    ``vat_breakdown`` may legitimately be an empty tuple (some
    receipts don't print a VAT table).
    """

    model_config = ConfigDict(frozen=True)

    total_cents: int | None = Field(default=None, ge=0)
    """Total TTC in integer cents. ``None`` if not parsed."""
    vat_breakdown: tuple[VatLine, ...] = ()
    """Tuple of VAT lines (5.5 %, 10 %, 20 % …). Empty tuple is legal."""
    payment_method: str | None = None
    """Raw payment method text (CB, ESPECES, TICKET RESTO, …) — no
    normalization at this layer."""
    item_count_declared: int | None = None
    """"Nombre d'articles vendus" printed at the bottom — useful as a
    cross-check signal for Phase 3."""
    barcode: ParsedReceiptBarcode | None = None
    """Decoded internal ticket-number barcode (NOT a product EAN).
    ``None`` when no barcode was physically read on the receipt."""
    source_block_ids: tuple[UUID, ...]
    """The :attr:`RawBlock.id` values that contributed to this footer."""


class ParsedItem(BaseModel):
    """One item line parsed from the receipt body."""

    model_config = ConfigDict(frozen=True)

    id: UUID = Field(default_factory=uuid4)
    raw_label: str
    """Text exactly as the OCR saw it."""
    normalized_label: str
    """UPPERCASE, accents-folded version (best-effort, set by Phase 2)."""
    quantity: int = Field(default=1, ge=1)
    unit_price_cents: int | None = None
    total_cents: int
    barcode: str | None = None
    """If pyzbar read a barcode and Phase 2 associated it with this item."""
    source_block_ids: tuple[UUID, ...]
    """The :attr:`RawBlock.id` values that contributed to this item."""
    parsing_issues: tuple[str, ...]
    """Short labels for non-fatal issues spotted on this item, e.g.
    ``"qty_inferred_from_total"``. Empty tuple means parsing was clean."""


class ParsedTicket(BaseModel):
    """Cardinal state of Phase 2 — the immutable post-comprehend snapshot.

    ``parsed_jsonb_hash`` is ``None`` at construction and populated via
    :meth:`with_jsonb_hash` once Phase 2 is complete and the ticket is
    ready for persistence. The hash covers every field except itself,
    so re-hashing an already-hashed ticket yields the same hash.

    Per ARCH § "Cardinal state" : if Phase 3/4 evolves or fails, we can
    replay from a persisted :class:`ParsedTicket` JSONB without re-OCR.
    The hash is the integrity check for that JSONB.
    """

    model_config = ConfigDict(frozen=True)

    id: UUID = Field(default_factory=uuid4)
    receipt_id: UUID
    items: tuple[ParsedItem, ...]
    header: ParsedHeader
    footer: ParsedFooter
    purchased_at: datetime | None = None
    """Date extracted from the ticket. ``None`` if Phase 2 could not
    parse it — never use a sentinel here."""
    raw_ticket_image_hash: str
    """Mirrors :attr:`RawTicket.image_hash` — guarantees we can trace
    a parsed ticket back to its source image."""
    parsed_jsonb_hash: str | None = None
    """Set via :meth:`with_jsonb_hash` once Phase 2 is complete."""

    def with_jsonb_hash(self) -> "ParsedTicket":
        """Return a new immutable instance with ``parsed_jsonb_hash`` populated.

        The hash is computed deterministically over every other field
        of this instance ; calling this method twice in a row yields
        the same hash.
        """
        return self.model_copy(update={"parsed_jsonb_hash": compute_parsed_jsonb_hash(self)})


# ---------------------------------------------------------------------------
# Phase 3 — Matcher
# ---------------------------------------------------------------------------


ItemMatchStatus = Literal["matched", "unresolved", "rejected"]
MatchMethod = Literal["barcode", "knowledge", "consensus_match"]
"""Match method written to ``scans.match_method``. Following the
2026-05-02 consensus-only refonte (cf.
``ARCH_name_resolution_consensus.md`` § "Philosophie") the
``fuzzy_strict`` value is no longer emitted by the pipeline ;
product-level fuzzy matching against ``products`` is gone for good.
Verified consensus is the only source of truth for unbarcoded items.
"""
StoreMatchStatus = Literal["matched", "suggested", "unresolved"]
CandidateSource = Literal["barcode", "knowledge", "consensus_match"]


class Candidate(BaseModel):
    """One product candidate considered during Phase 3 matching.

    Stored on :attr:`ItemMatch.top_candidates` for audit / future ML
    training. **Strictly read-only-internal** — never surfaced to the
    end user (cf. ARCH § Anti-patterns : the user never picks from a
    candidate list, only barcode-scans physically).
    """

    model_config = ConfigDict(frozen=True)

    product_ean: str
    product_label: str
    """Canonical product label as stored in the products / OFF table."""
    score: float = Field(ge=0.0, le=1.0)
    """Similarity score in ``[0, 1]`` (fuzzy / knowledge confidence)."""
    source: CandidateSource
    """Which lookup produced this candidate."""


class DecisionInputs(BaseModel):
    """Snapshot of the inputs that drove a Phase 3 match decision.

    Captured per :class:`ItemMatch` so that any past match can be
    replayed and audited later (cf. ARCH § Reproductibilité). If
    ``fuzzy_threshold`` evolves, we can identify which legacy matches
    sit on the boundary and would now flip status — without re-running
    OCR or LLM.
    """

    model_config = ConfigDict(frozen=True)

    normalized_label: str
    """The label used as the matching lookup key (normalized form)."""
    barcode_used: str | None = None
    """Barcode that drove the match, when one was present on the line."""
    knowledge_lookup_hit: bool
    """``True`` if the curated ``ProductKnowledgeLookup`` returned a hit."""
    consensus_state: str | None = None
    """Recorded consensus state at decision time when the cascade
    reached the consensus stage. ``"VERIFIED"`` means a positive match ;
    ``"PENDING"`` / ``"CONTROVERSE"`` / ``"UNVERIFIED"`` mean the cascade
    fell through to ``unresolved`` with that state recorded for audit.
    ``None`` when consensus was never queried (barcode hit, knowledge
    hit, or no store_id available).
    """
    candidates_considered: int = Field(ge=0)
    """Total number of candidates evaluated (pre top-N truncation)."""


class ItemMatch(BaseModel):
    """One parsed item's matching outcome.

    Cross-field invariants (validated in :meth:`_check_invariants`) :

    - ``status == 'matched'`` ⟹ ``product_ean`` and ``match_method``
      both non-null.
    - ``status != 'matched'`` ⟹ ``rejected_reason`` non-null
      (an unresolved or rejected item must always justify itself —
      no silent drops, per ARCH § Anti-patterns).
    - ``match_confidence`` (when provided) must be in ``[0, 1]``.
    - ``len(top_candidates) <= 5`` — top-N is capped to keep the audit
      JSONB compact.

    The inverse — a matched item carrying a ``rejected_reason`` — is
    intentionally allowed for debug/comment purposes.

    ``decision_inputs`` is **mandatory** : every ItemMatch (including
    rejected ones) records the decision inputs for reproducibility.
    """

    model_config = ConfigDict(frozen=True)

    id: UUID = Field(default_factory=uuid4)
    parsed_item_id: UUID
    status: ItemMatchStatus
    product_ean: str | None = None
    match_method: MatchMethod | None = None
    match_confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    rejected_reason: str | None = None
    top_candidates: tuple[Candidate, ...] = ()
    """Top-N candidates considered (max 5). Empty tuple is legal —
    typically when ``status='rejected'`` and nothing was found, or
    when a barcode hit short-circuited the search."""
    decision_inputs: DecisionInputs
    """Snapshot of the inputs that drove this decision. Required."""

    @model_validator(mode="after")
    def _check_invariants(self) -> "ItemMatch":
        if len(self.top_candidates) > 5:
            raise ValueError(
                f"ItemMatch.top_candidates has {len(self.top_candidates)} entries, max allowed is 5 (audit JSONB cap)"
            )
        if self.status == "matched":
            if self.product_ean is None:
                raise ValueError("ItemMatch.status='matched' requires product_ean to be non-null")
            if self.match_method is None:
                raise ValueError("ItemMatch.status='matched' requires match_method to be non-null")
        else:
            if self.rejected_reason is None:
                raise ValueError(
                    f"ItemMatch.status={self.status!r} requires rejected_reason "
                    "to be non-null (no silent drops — every non-matched item "
                    "must justify itself)"
                )
        return self


class MatchedTicket(BaseModel):
    """Phase 3 output : the parsed ticket plus its matching outcomes.

    Cross-field invariants :

    - ``store_status == 'matched'`` ⟹ ``store_match_id`` non-null.
    - ``store_status != 'matched'`` ⟹ ``store_rejected_reason``
      non-null.

    ``store_match_id`` is the :class:`UUID` of the matched store row.
    The ``stores`` table currently uses an integer PK ; the migration
    to UUID lands in bloc 2 (DB migration). The contract is
    UUID-shaped from bloc 1 to keep the type stable across the
    transition.
    """

    model_config = ConfigDict(frozen=True)

    id: UUID = Field(default_factory=uuid4)
    parsed_ticket_id: UUID
    item_matches: tuple[ItemMatch, ...]
    store_match_id: UUID | None = None
    store_status: StoreMatchStatus
    store_rejected_reason: str | None = None

    @model_validator(mode="after")
    def _check_store_invariants(self) -> "MatchedTicket":
        if self.store_status == "matched":
            if self.store_match_id is None:
                raise ValueError("MatchedTicket.store_status='matched' requires store_match_id to be non-null")
        else:
            if self.store_rejected_reason is None:
                raise ValueError(
                    f"MatchedTicket.store_status={self.store_status!r} requires store_rejected_reason to be non-null"
                )
        return self


__all__ = [
    "Candidate",
    "CandidateSource",
    "DecisionInputs",
    "ItemMatch",
    # Phase 3
    "ItemMatchStatus",
    "MatchMethod",
    "MatchedTicket",
    "ParsedFooter",
    "ParsedHeader",
    "ParsedItem",
    "ParsedReceiptBarcode",
    "ParsedTicket",
    "RawBarcode",
    # Phase 1
    "RawBlock",
    "RawTicket",
    "StoreMatchStatus",
    # Phase 2
    "VatLine",
    "compute_barcode_hash",
    # Hashing
    "compute_block_hash",
    "compute_image_hash",
    "compute_parsed_jsonb_hash",
]
