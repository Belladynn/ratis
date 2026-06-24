"""Token-level OCR normalization (extracted from legacy ``matcher.py``).

This module owns the ``ocr_knowledge`` token-cleanup pipeline that was
historically embedded inside :mod:`worker.ocr.matcher`. Following the
2026-05-02 refonte (consensus-only), the legacy matcher has been dropped,
but its token-cleanup helpers remain useful — they are the only path that
turns raw OCR text into a canonical "cleaned label" used as the consensus
key (``store_id, normalized_label``).

Public API :

- :func:`normalize_text` — the canonical cleanup. Looks up
  ``ocr_knowledge`` for full-sequence cache, then token-by-token, with
  pg_trgm fuzzy fallback against the lexical dictionary
  (``products.product_name_fr`` / ``products.name``).
- :func:`lookup_knowledge_corrected` — read-only single-row lookup
  used by ``local_prefilter`` for direct cache hits.

All helpers below ``normalize_text`` are private (underscore-prefixed)
because their contract is "subroutines of normalize_text". They are not
re-exported.

Cf. ``ARCH_name_resolution_consensus.md`` § "Token-level cleanup" and
``TRAINING.md`` for the auto-learning closed-loop that feeds
``ocr_knowledge.corrected``.
"""

from __future__ import annotations

import logging
import uuid
from difflib import SequenceMatcher

from ratis_core.settings import load_settings
from sqlalchemy import text
from sqlalchemy.orm import Session

_log = logging.getLogger(__name__)

_CFG = load_settings()["fuzzy"]
_KNOW = load_settings()["knowledge"]


# ── i18n hook for the lexical dictionary used by token cleanup ───────────────


def _dictionary_columns_for_locale(country_code: str = "FR") -> list[str]:
    """Return ordered list of ``products`` column names to use as the
    lexical dictionary for OCR token cleanup.

    Order matters : the UNION ALL is read in declaration order, so the
    most "local" column should come first.

    V1 hardcodes ``country_code='FR'`` (alpha users are all French).
    V2+ extends here for other countries (``IT``, ``ES``, ...).
    """
    if country_code == "FR":
        return ["product_name_fr", "name"]
    return ["name"]  # fallback


def _build_token_fuzzy_sql(country_code: str = "FR") -> str:
    """Build the per-locale fuzzy-token query.

    UNIONs every dictionary column declared by
    :func:`_dictionary_columns_for_locale` and ranks word-similarity
    against the query token. Hardcoded ``country_code='FR'`` for V1 ;
    callers can override via the helper for tests.

    The column names interpolated below come from a hardcoded allowlist
    (:func:`_dictionary_columns_for_locale`) — never from user input —
    so the f-string interpolation is safe. The ``S608`` ruff warning is
    a false-positive in this context.
    """
    cols = _dictionary_columns_for_locale(country_code)
    union_parts = [
        f"SELECT {col} AS dict_word FROM products WHERE {col} IS NOT NULL"  # noqa: S608 — col from hardcoded allowlist
        for col in cols
    ]
    union_sql = " UNION ALL ".join(union_parts)
    return f"""
        SELECT dict_word, word_similarity(:token, dict_word) AS score
        FROM ({union_sql}) words
        WHERE word_similarity(:token, dict_word) > :threshold
        ORDER BY score DESC LIMIT 1
    """  # noqa: S608 — union_sql composed from hardcoded column allowlist


# Compiled once at import time — V1 single-locale.
_TOKEN_FUZZY_SQL = text(_build_token_fuzzy_sql("FR"))


# ── public API ───────────────────────────────────────────────────────────────


def normalize_text(db: Session, scanned_name: str) -> str:
    """Normalize ``scanned_name`` via ``ocr_knowledge`` lookups.

    1. Full sequence lookup: if known (``corrected IS NOT NULL``), return
       the corrected text directly.
    2. Token-by-token: check knowledge, then ILIKE (batched), then fuzzy.
       - Resolved tokens that differ from the original are recorded as
         ``'token'`` corrections.
       - Unresolvable tokens (``len >= 2``) are recorded as pending
         review (``corrected=NULL``).
    3. If any meaningful correction was found, save the full sequence
       mapping.

    Returns the best-effort corrected string (may equal ``scanned_name``
    if nothing changed).
    """
    # Step 1: full sequence cache hit
    corrected_seq = lookup_knowledge_corrected(db, scanned_name)
    if corrected_seq is not None:
        return corrected_seq

    tokens = scanned_name.split()

    # Phase A: knowledge lookup for all tokens (one query each — cheap cache)
    knowledge_results: list[str | None] = [lookup_knowledge_corrected(db, t) for t in tokens]

    # Phase B: batch ILIKE for unknown tokens (1 DB round-trip instead of N)
    unknown_tokens = [t for t, k in zip(tokens, knowledge_results, strict=False) if k is None]
    ilike_names = _batch_ilike_lookup(db, unknown_tokens) if unknown_tokens else {}

    corrected_tokens: list[str] = []
    correction_confidences: list[float] = []

    for token, from_knowledge in zip(tokens, knowledge_results, strict=False):
        if from_knowledge is not None:
            corrected_tokens.append(from_knowledge)
            continue

        resolved, confidence = _resolve_unknown_token(db, token, ilike_names.get(token.upper()))
        if resolved is not None:
            corrected_tokens.append(resolved)
            if resolved.upper() != token.upper():
                _upsert_knowledge_correction(db, token.upper(), resolved, "token", "ocr_arbitrage", confidence)
                correction_confidences.append(confidence)
        else:
            corrected_tokens.append(token)
            if len(token) >= 2:
                _upsert_knowledge_pending(db, token.upper(), "token")

    corrected_name = " ".join(corrected_tokens)

    # Step 3: save sequence mapping when a meaningful correction was found
    if corrected_name.upper() != scanned_name.upper():
        seq_confidence = min(correction_confidences) if correction_confidences else 1.0
        _upsert_knowledge_correction(
            db,
            scanned_name.upper(),
            corrected_name,
            "sequence",
            "ocr_arbitrage",
            seq_confidence,
        )
        _log.debug(
            "normalized %d correction(s): %r → %r",
            len(correction_confidences),
            scanned_name[:40],
            corrected_name[:40],
        )

    return corrected_name


def lookup_knowledge_corrected(db: Session, raw_ocr: str, ocr_type: str = "product_name") -> str | None:
    """Return ``corrected`` text if ``raw_ocr`` is known
    (``corrected IS NOT NULL``), incrementing ``seen_count``.

    ``raw_ocr`` is looked up case-insensitively (stored uppercase).

    ``ocr_type``: one of ``'product_name'``, ``'brand_name'``,
    ``'retailer_header'``, ``'address_token'``. Defaults to
    ``'product_name'`` (product pipeline). Store detection uses
    ``'retailer_header'``.
    """
    row = db.execute(
        text("""
            UPDATE ocr_knowledge
            SET seen_count = seen_count + 1
            WHERE raw_ocr = :raw AND type = :ocr_type AND corrected IS NOT NULL
            RETURNING corrected
        """),
        {"raw": raw_ocr.upper(), "ocr_type": ocr_type},
    ).first()
    return row.corrected if row else None


# ── private helpers ──────────────────────────────────────────────────────────


def _batch_ilike_lookup(db: Session, tokens: list[str]) -> dict[str, str]:
    """Batch ILIKE: find a product name containing each token in a single query.

    Returns ``{TOKEN_UPPER: product_name}`` for tokens that matched.
    Tokens shorter than ``token_min_length`` are skipped (same gate as
    individual lookup).
    """
    min_len: int = _KNOW["token_min_length"]
    eligible = [t for t in tokens if len(t) >= min_len]
    if not eligible:
        return {}

    patterns = [f"%{t}%" for t in eligible]
    rows = db.execute(
        text("SELECT DISTINCT name FROM products WHERE name ILIKE ANY(:patterns)"),
        {"patterns": patterns},
    ).fetchall()

    product_names = [row.name for row in rows]
    result: dict[str, str] = {}
    for token in eligible:
        token_up = token.upper()
        for name in product_names:
            if token_up in name.upper():
                result[token_up] = name
                break
    return result


def _resolve_unknown_token(db: Session, token: str, ilike_name: str | None = None) -> tuple[str | None, float | None]:
    """Attempt to resolve a token not in ``ocr_knowledge``.

    1. ILIKE: use pre-batched result (``ilike_name``) if provided;
       skipped otherwise.
    2. ``pg_trgm`` ``word_similarity`` fallback (requires
       ``len >= token_min_length``).

    Returns ``(best_matching_word, confidence)`` or ``(None, None)``.
    """
    min_len: int = _KNOW["token_min_length"]
    if len(token) < min_len:
        return None, None

    # ILIKE result already fetched in batch — no extra DB round-trip
    if ilike_name is not None:
        best = _best_matching_word(token, ilike_name)
        if best:
            return best, 0.95

    # Fuzzy : pg_trgm word_similarity on the isolated token, against the
    # locale-aware lexical dictionary (FR : product_name_fr ∪ name).
    threshold: float = _KNOW["token_fuzzy_threshold"]
    row = db.execute(
        _TOKEN_FUZZY_SQL,
        {"token": token, "threshold": threshold},
    ).first()
    if row:
        best = _best_matching_word(token, row.dict_word)
        if best:
            return best, float(row.score)

    return None, None


def _levenshtein(s1: str, s2: str) -> int:
    """Pure-Python Levenshtein edit distance.

    Iterative two-row DP, ``O(len(s1) * len(s2))`` time, ``O(min(len))``
    space. Cheap on the short strings we deal with (< 50 chars).
    """
    if len(s1) < len(s2):
        return _levenshtein(s2, s1)
    if not s2:
        return len(s1)
    previous = list(range(len(s2) + 1))
    for i, c1 in enumerate(s1):
        current = [i + 1]
        for j, c2 in enumerate(s2):
            ins = previous[j + 1] + 1
            delete = current[j] + 1
            sub = previous[j] + (c1 != c2)
            current.append(min(ins, delete, sub))
        previous = current
    return previous[-1]


def _word_edit_distance_acceptable(token: str, candidate_word: str, *, max_diff: int = 2) -> bool:
    """Two-stage gate before computing ``SequenceMatcher.ratio()`` :
    quick len-diff reject, then actual Levenshtein.

    - ``len_diff > max_diff`` → impossible to be close → reject (no compute).
    - ``len_diff <= max_diff`` → compute Levenshtein, accept if ``<= max_diff``.

    Examples (with default ``max_diff=2``) :

      MOUSSE vs MOUSSEUSEMENT (len_diff=7)   → reject (avoids false positive)
      HIPRO  vs HIPOPOTAME    (len_diff=5)   → reject
      HIPRO  vs HIPROA        (len_diff=1, lev=1) → accept (legit OCR fix)
      MOUSSE vs MOUSSEUX      (len_diff=2, lev=2) → accept
    """
    if abs(len(token) - len(candidate_word)) > max_diff:
        return False
    return _levenshtein(token.upper(), candidate_word.upper()) <= max_diff


def _best_matching_word(token: str, product_name: str) -> str | None:
    """Find the word in ``product_name`` most similar to ``token``.

    Two-stage gate :

    1. Per-word Levenshtein ``<= fuzzy.max_edit_distance_per_word`` (default 2).
       Quick len-diff reject + actual edit distance. Eliminates pairs like
       MOUSSE/MOUSSEUSEMENT that the SequenceMatcher ratio alone would accept.
    2. ``SequenceMatcher.ratio() >= fuzzy.word_match_min_ratio`` (default 0.7).
       Final tie-breaker among candidates that pass the edit-distance gate.

    Returns ``None`` if no word in ``product_name`` passes both gates.
    """
    upper_token = token.upper()
    max_diff = _CFG.get("max_edit_distance_per_word", 2)
    min_ratio = _CFG.get("word_match_min_ratio", 0.7)
    best_word: str | None = None
    best_score = 0.0
    for word in product_name.split():
        # Stage 1 : hard edit-distance gate before computing the ratio.
        if not _word_edit_distance_acceptable(token, word, max_diff=max_diff):
            continue
        # Stage 2 : SequenceMatcher ratio for ranking among accepted candidates.
        score = SequenceMatcher(None, upper_token, word.upper(), autojunk=False).ratio()
        if score > best_score:
            best_score = score
            best_word = word
    return best_word if best_score >= min_ratio else None


def _upsert_knowledge_correction(
    db: Session,
    raw_ocr_upper: str,
    corrected: str,
    match_type: str,
    source: str,
    confidence: float,
) -> None:
    """Insert a correction into ``ocr_knowledge`` (``raw_ocr`` must be uppercase).

    On conflict :

    - If existing entry has ``corrected=NULL`` (pending): fill in the correction.
    - Otherwise: only increment ``seen_count`` (never overwrite existing
      correction).
    """
    db.execute(
        text("""
            INSERT INTO ocr_knowledge (id, type, raw_ocr, corrected, match_type, source, confidence, seen_count)
            VALUES (:id, 'product_name', :raw_ocr, :corrected, :match_type, :source, :confidence, 1)
            ON CONFLICT (raw_ocr, type) DO UPDATE
                SET seen_count  = ocr_knowledge.seen_count + 1,
                    corrected   = CASE WHEN ocr_knowledge.corrected IS NULL
                                       THEN EXCLUDED.corrected
                                       ELSE ocr_knowledge.corrected END,
                    confidence  = CASE WHEN ocr_knowledge.corrected IS NULL
                                       THEN EXCLUDED.confidence
                                       ELSE ocr_knowledge.confidence END,
                    source      = CASE WHEN ocr_knowledge.corrected IS NULL
                                       THEN EXCLUDED.source
                                       ELSE ocr_knowledge.source END,
                    match_type  = CASE WHEN ocr_knowledge.corrected IS NULL
                                       THEN EXCLUDED.match_type
                                       ELSE ocr_knowledge.match_type END
        """),
        {
            "id": str(uuid.uuid4()),
            "raw_ocr": raw_ocr_upper,
            "corrected": corrected,
            "match_type": match_type,
            "source": source,
            "confidence": confidence,
        },
    )


def _upsert_knowledge_pending(db: Session, raw_ocr_upper: str, match_type: str) -> None:
    """Record an unresolvable token as pending review
    (``corrected=NULL, confidence=NULL``).

    On conflict: only increment ``seen_count``, never overwrite an
    existing correction.
    """
    db.execute(
        text("""
            INSERT INTO ocr_knowledge (id, type, raw_ocr, corrected, match_type, source, confidence, seen_count)
            VALUES (:id, 'product_name', :raw_ocr, NULL, :match_type, 'ocr_arbitrage', NULL, 1)
            ON CONFLICT (raw_ocr, type) DO UPDATE
                SET seen_count = ocr_knowledge.seen_count + 1
        """),
        {
            "id": str(uuid.uuid4()),
            "raw_ocr": raw_ocr_upper,
            "match_type": match_type,
        },
    )


__all__ = [
    "lookup_knowledge_corrected",
    "normalize_text",
]
