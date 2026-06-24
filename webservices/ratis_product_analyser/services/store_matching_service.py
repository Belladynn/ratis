"""
Local store detection (DA-35).

Replaces the runtime Overpass / Nominatim calls by a local DB lookup against
the ``retailers`` + ``retailer_aliases`` + ``stores`` tables seeded by
``batch_osm_sync`` (DA-34).

Public API
----------
- ``match_retailer_from_header(db, header_text, *, min_similarity=0.75)``
  → Optional[RetailerMatch]
- ``match_store_from_address(db, retailer_id, postal_code, city_hint=None)``
  → Optional[StoreMatch]
- ``cache_retailer_header_resolution(db, raw_header, retailer_match)``
  → upserts into ``ocr_knowledge`` (type='retailer_header', entity_id=uuid).
- ``extract_postal_code(text)`` → first 5-digit word-bounded group, else None.
- ``extract_city_hint(text, postal_code)`` → first token after postal_code, else None.

Matching strategy (header):
    1. Normalize: lowercase + strip accents + trim.
    2. Exact alias hit → confidence 1.0 (fast path, indexed).
    3. pg_trgm similarity() fuzzy fallback → top-1 above threshold.

Matching strategy (store):
    1. retailer_id + postal_code unique match → confidence 1.0.
    2. retailer_id + postal_code multiple + city_hint → disambiguate (1.0).
    3. retailer_id + postal_code multiple + no city_hint → is_ambiguous, 0.5.
    4. retailer_id + city_hint (no postal_code) → 0.3 (loose).
    5. Nothing → None.
"""

from __future__ import annotations

import re
import unicodedata
import uuid
from dataclasses import dataclass

from ratis_core.settings import load_settings
from sqlalchemy import text
from sqlalchemy.orm import Session

_CFG = load_settings()["store_matching"]
_DEFAULT_MIN_SIMILARITY: float = float(_CFG.get("retailer_header_min_similarity", 0.75))


_POSTAL_RE = re.compile(r"\b(\d{5})\b")


@dataclass(frozen=True)
class RetailerMatch:
    retailer_id: uuid.UUID
    canonical_name: str
    confidence: float  # 0..1
    matched_alias: str


@dataclass(frozen=True)
class StoreMatch:
    store_id: uuid.UUID
    confidence: float  # 0..1
    is_ambiguous: bool


# ---------------------------------------------------------------------------
# Text helpers
# ---------------------------------------------------------------------------


def _strip_accents(value: str) -> str:
    nfd = unicodedata.normalize("NFD", value)
    return "".join(c for c in nfd if unicodedata.category(c) != "Mn")


def _normalize(value: str) -> str:
    """Lowercase, strip accents, collapse whitespace."""
    return " ".join(_strip_accents(value).lower().split())


def extract_postal_code(value: str) -> str | None:
    """Return the first 5-digit word-bounded group, or None."""
    if not value:
        return None
    m = _POSTAL_RE.search(value)
    return m.group(1) if m else None


def extract_city_hint(value: str, postal_code: str | None) -> str | None:
    """
    Return the token directly following ``postal_code`` in ``value``, else None.

    - If ``postal_code`` is None, or is not found in ``value``, or nothing follows
      it: returns None.
    - Case is preserved: callers normalize themselves (the city ILIKE in store
      matching absorbs case variation).
    """
    if not postal_code or not value:
        return None
    # Anchor on the exact postal_code (it is just 5 digits — safe literal).
    # We want the first alphabetic token directly after it.
    after = value.split(postal_code, 1)
    if len(after) != 2:
        return None
    tail = after[1].strip()
    if not tail:
        return None
    # Next whitespace-separated token — but strip leading punctuation/dashes.
    tokens = tail.split()
    if not tokens:
        return None
    first = tokens[0].strip(" -,;:.")
    return first or None


# ---------------------------------------------------------------------------
# Retailer header match
# ---------------------------------------------------------------------------


_EXACT_ALIAS_SQL = text(
    """
    SELECT r.id, r.canonical_name, a.alias
    FROM retailer_aliases a
    JOIN retailers r ON r.id = a.retailer_id
    WHERE a.alias = :alias
    LIMIT 1
    """
)


_FUZZY_ALIAS_SQL = text(
    """
    SELECT r.id, r.canonical_name, a.alias, similarity(a.alias, :alias) AS sim
    FROM retailer_aliases a
    JOIN retailers r ON r.id = a.retailer_id
    WHERE similarity(a.alias, :alias) >= :min_sim
    ORDER BY sim DESC, r.parent_id NULLS FIRST, r.canonical_name ASC
    LIMIT 1
    """
)


def match_retailer_from_header(
    db: Session,
    header_text: str,
    *,
    min_similarity: float | None = None,
) -> RetailerMatch | None:
    """
    Look up a retailer by matching an OCR header line against ``retailer_aliases``.

    Strategy:
      1. Normalize (lowercase, strip accents, collapse whitespace).
      2. Exact alias match → confidence 1.0.
      3. Fallback: pg_trgm similarity() above threshold → top-1 by score, tied by
         preferring parent rows (``parent_id IS NULL``) then canonical_name.
    Returns ``None`` when the header is empty or no alias meets the threshold.
    """
    if not header_text or not header_text.strip():
        return None

    normalized = _normalize(header_text)
    if not normalized:
        return None

    threshold = float(min_similarity) if min_similarity is not None else _DEFAULT_MIN_SIMILARITY

    # Exact alias path — O(1) on the unique index.
    row = db.execute(_EXACT_ALIAS_SQL, {"alias": normalized}).first()
    if row is not None:
        return RetailerMatch(
            retailer_id=row[0],
            canonical_name=row[1],
            confidence=1.0,
            matched_alias=row[2],
        )

    # Fuzzy fallback — pg_trgm similarity on the trigram GIN index.
    row = db.execute(_FUZZY_ALIAS_SQL, {"alias": normalized, "min_sim": threshold}).first()
    if row is None:
        return None
    return RetailerMatch(
        retailer_id=row[0],
        canonical_name=row[1],
        confidence=float(row[3]),
        matched_alias=row[2],
    )


# ---------------------------------------------------------------------------
# Store match
# ---------------------------------------------------------------------------


_STORE_BY_POSTAL_SQL = text(
    """
    SELECT id
    FROM stores
    WHERE retailer_id = :retailer_id
      AND is_disabled = false
      AND postal_code = :postal_code
    ORDER BY id ASC
    """
)


_STORE_BY_POSTAL_AND_CITY_SQL = text(
    """
    SELECT id
    FROM stores
    WHERE retailer_id = :retailer_id
      AND is_disabled = false
      AND postal_code = :postal_code
      AND city ILIKE :city
    ORDER BY id ASC
    LIMIT 2
    """
)


_STORE_BY_CITY_ONLY_SQL = text(
    """
    SELECT id
    FROM stores
    WHERE retailer_id = :retailer_id
      AND is_disabled = false
      AND city ILIKE :city
    ORDER BY id ASC
    LIMIT 2
    """
)


def match_store_from_address(
    db: Session,
    retailer_id: uuid.UUID,
    postal_code: str | None,
    city_hint: str | None = None,
) -> StoreMatch | None:
    """
    Find a store for a given retailer + postal_code (+ optional city_hint).

    See module docstring for the decision table.
    """
    if postal_code:
        rows = db.execute(
            _STORE_BY_POSTAL_SQL,
            {"retailer_id": str(retailer_id), "postal_code": postal_code},
        ).all()
        if len(rows) == 1:
            return StoreMatch(store_id=rows[0][0], confidence=1.0, is_ambiguous=False)
        if len(rows) > 1:
            if city_hint:
                hint = f"%{city_hint.strip()}%"
                narrowed = db.execute(
                    _STORE_BY_POSTAL_AND_CITY_SQL,
                    {
                        "retailer_id": str(retailer_id),
                        "postal_code": postal_code,
                        "city": hint,
                    },
                ).all()
                if len(narrowed) == 1:
                    return StoreMatch(
                        store_id=narrowed[0][0],
                        confidence=1.0,
                        is_ambiguous=False,
                    )
                if len(narrowed) > 1:
                    return StoreMatch(
                        store_id=narrowed[0][0],
                        confidence=0.5,
                        is_ambiguous=True,
                    )
            # Multiple matches, no (useful) hint → ambiguous.
            return StoreMatch(store_id=rows[0][0], confidence=0.5, is_ambiguous=True)

    # No postal_code (or postal_code miss) — try city-only as a weak fallback.
    if city_hint:
        hint = f"%{city_hint.strip()}%"
        rows = db.execute(
            _STORE_BY_CITY_ONLY_SQL,
            {"retailer_id": str(retailer_id), "city": hint},
        ).all()
        if len(rows) == 1:
            return StoreMatch(store_id=rows[0][0], confidence=0.3, is_ambiguous=False)
        if len(rows) > 1:
            return StoreMatch(store_id=rows[0][0], confidence=0.3, is_ambiguous=True)

    return None


# ---------------------------------------------------------------------------
# ocr_knowledge cache
# ---------------------------------------------------------------------------


_UPSERT_OCR_KNOWLEDGE_SQL = text(
    """
    INSERT INTO ocr_knowledge (
        id, type, raw_ocr, corrected, match_type, source, confidence,
        seen_count, entity_id, created_at
    ) VALUES (
        gen_random_uuid(), 'retailer_header', :raw_ocr, :corrected,
        :match_type, 'ocr_arbitrage', :confidence, 1, :entity_id, now()
    )
    ON CONFLICT (raw_ocr, type) DO UPDATE SET
        seen_count = ocr_knowledge.seen_count + 1,
        corrected  = EXCLUDED.corrected,
        entity_id  = EXCLUDED.entity_id,
        confidence = EXCLUDED.confidence,
        match_type = EXCLUDED.match_type
    """
)


def cache_retailer_header_resolution(
    db: Session,
    raw_header: str,
    retailer_match: RetailerMatch,
) -> None:
    """
    UPSERT a row into ``ocr_knowledge`` so a future OCR pass of the same
    raw_header can short-circuit to the resolved retailer.

    - ``type='retailer_header'`` (DA-34 polymorphic entity column).
    - ``match_type='sequence'`` when confidence == 1.0 (exact alias) else
      ``'ngram'`` (pg_trgm similarity).
    - Increments ``seen_count`` on conflict.
    """
    match_type = "sequence" if retailer_match.confidence >= 1.0 else "ngram"
    db.execute(
        _UPSERT_OCR_KNOWLEDGE_SQL,
        {
            "raw_ocr": raw_header,
            "corrected": retailer_match.canonical_name,
            "match_type": match_type,
            "confidence": retailer_match.confidence,
            "entity_id": str(retailer_match.retailer_id),
        },
    )
