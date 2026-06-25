"""
OCR store detection pipeline.

Extracts store signals from receipt header lines, then resolves them
to a known store via:
  1. Fingerprint lookup (O(1) — learned from previous confirmed matches)
  2. Candidate intersection + scoring (retailer × postal × phone × fuzzy address)

Entry point: detect_store(db, ocr_lines, country_code="FR") → Optional[StoreMatch]

See ARCH_ocr_store_detection.md for the full design.
"""

from __future__ import annotations

import logging
import re
import unicodedata
import uuid
from dataclasses import dataclass

from ratis_core.normalize import normalize_numeric, normalize_phone
from ratis_core.settings import load_settings
from sqlalchemy import text
from sqlalchemy.orm import Session

_log = logging.getLogger(__name__)
_CFG = load_settings()["store_matching"]


@dataclass(frozen=True)
class StoreMatch:
    """Result of a store detection attempt."""

    store_id: uuid.UUID
    score: int  # 0–100; 100 = fingerprint hit (no real score)
    auto: bool  # True = score >= threshold_auto (or fingerprint) → cashback OK
    # False = soft-match → cashback blocked pending user confirmation (V2)


# Scoring table (see ARCH — Pipeline de matching)
#
# 2026-04-27 — phone is now a *retailer* signal, not a store-id signal.
# Multiple stores share a corporate phone number (franchise standard), so
# matching a phone alone can never identify a specific store. We use phone
# only to infer the candidate's retailer; the score is therefore close to a
# plain retailer-only match (20). See refactor/phone-as-retailer-signal.
_SIGNAL_SCORES: dict[str, int] = {
    "phone": 30,  # was 80 — phone identifies a retailer, not a store
    "store_code": 70,
    "retailer_postal": 50,
    "address_fuzzy": 40,
    "retailer": 20,
}

# ── OCR-tolerant phone prefix ─────────────────────────────────────────────────
#
# Receipt printers + thermal paper occasionally garble the "TEL" / "TÉL"
# prefix. Variants observed in alpha 2026-04-29 :
#   TEL 0147459270   (canonical, space-only — strict regex required ":" or ".")
#   TE 0147459270    (drop "L")
#   EL 0147459270    (T→E confusion + drop "L")
#   TEL0147459270    (drop separator after binarization)
#   EL0147459270     (combo — the real Intermarché bug)
#   T0147459270      (drop "EL")
#
# Design : country-agnostic regex extracts a *digit-shaped* token (8-15 digits
# after stripping separators), optionally preceded by a phone-label-shaped
# letter cluster. Per-country format validation is delegated to
# `normalize_phone` downstream — no hardcoded FR `0\d{9}` here so the
# extractor stays intl-friendly.
#
# E.164 caps at 15 digits ; the floor of 8 lets us accept short national
# numbers without colliding with postal codes (5 digits) or short product
# codes. Long internal codes (16+ digits, e.g. receipt barcodes) are
# rejected so they fall through to the dedicated barcode pass.
_PHONE_PREFIX_OCR_RE = re.compile(
    r"(?:\b(?:T[EÉ3]?L?|EL)[.:]?\s*)?(\+?\d[\d\s.\-/+()]{7,})",
    re.IGNORECASE,
)


def _extract_phone_ocr_tolerant(line: str) -> str | None:
    """Extract a phone-like digit sequence from a single header line.

    Tolerates OCR errors on the "TEL" prefix (T/TE/EL/TEL/TÉL with optional
    separator). Returns the **digits-only** string (8 to 15 chars) so the
    caller can pass it to `normalize_phone(country_code=...)` for
    per-country validation. Returns None when no plausible phone token is
    found.

    Country-agnostic by design — extends to non-FR phones automatically
    once `normalize_phone` gains support for other countries.
    """
    if not line:
        return None
    match = _PHONE_PREFIX_OCR_RE.search(line)
    if not match:
        return None
    digits = re.sub(r"\D", "", match.group(1))
    if 8 <= len(digits) <= 15:  # E.164 universal range
        return digits
    return None


# ── Header classifier ─────────────────────────────────────────────────────────

# Address keywords by country (DP-06 — multi-country support)
_ADDRESS_KEYWORDS_BY_COUNTRY: dict[str, str] = {
    "FR": r"\b(RUE|BD|BOULEVARD|AV|AVENUE|ALL|ALLEE|PL|PLACE|IMP|IMPASSE|RES|RESIDENCE|CHE|CHEMIN)\b",
    "BE": r"\b(RUE|STRAAT|LAAN|AVENUE|BOULEVARD|PLAATS|PLEIN)\b",
    "CH": r"\b(STRASSE|STR|GASSE|ALLEE|RUE|VIA|AVENUE)\b",
}


def _get_address_re(country_code: str) -> re.Pattern:
    """Return the compiled address regex for the given country, falling back to FR."""
    keywords = _ADDRESS_KEYWORDS_BY_COUNTRY.get(country_code, _ADDRESS_KEYWORDS_BY_COUNTRY["FR"])
    return re.compile(r"^\d+\s+" + keywords, re.IGNORECASE)


def _normalize_retailer_key(retailer: str) -> str:
    """Normalize retailer name to match retailer_receipt_formats keys.

    Same logic as barcode_reader._normalize_retailer_key — lowercase, strip accents,
    replace spaces with underscores.
    """
    nfd = unicodedata.normalize("NFD", retailer.lower())
    without_accents = "".join(c for c in nfd if unicodedata.category(c) != "Mn")
    return without_accents.replace(" ", "_")


def load_known_retailers(db: "Session") -> frozenset[str]:
    """Load all distinct retailer identifiers from active stores.

    Returns raw values (e.g. 'lidl', 'monoprix') — normalisation done in
    extract_store_signals so OCR variants with accents are handled correctly.
    """
    rows = db.execute(
        text("SELECT DISTINCT retailer FROM stores WHERE retailer IS NOT NULL AND NOT is_disabled")
    ).fetchall()
    return frozenset(row[0] for row in rows if row[0])


def extract_store_signals(
    lines: list[str],
    country_code: str = "FR",
    barcode_formats: dict[str, dict] | None = None,
    known_retailers: "frozenset[str] | None" = None,
) -> dict[str, str | None]:
    """
    Classify header lines and extract store signals (two-pass for barcode).

    Pass 1 (loop): collect phone, postal_code, address, retailer, and raw barcode.
    Pass 2 (post-loop): if retailer known + barcode_formats provided → extract store_code
                        from the retailer-specific slice.  No fallback if retailer unknown.

    Returns a dict with keys (present only when a value was extracted):
      phone, postal_code, address, retailer, store_code
    """
    signals: dict[str, str | None] = {}
    header_lines = lines[: _CFG.get("header_lines", 8)]
    _raw_barcode: str | None = None
    address_re = _get_address_re(country_code)

    # Pre-scan: if known_retailers provided, find the first validated retailer across the
    # entire header window before the main loop.  This prevents a noise uppercase
    # line (e.g. "EN CAISSE") from pre-empting the real enseigne.
    normalized_known: frozenset[str] = (
        frozenset(_normalize_retailer_key(b) for b in known_retailers) if known_retailers else frozenset()
    )
    if normalized_known:
        for line in header_lines:
            stripped = line.strip()
            if stripped and _normalize_retailer_key(stripped) in normalized_known:
                signals["retailer"] = stripped
                break

    for line in header_lines:
        stripped = line.strip()
        if not stripped:
            continue

        # Priority 1: labelled phone  (Tél: / Tel: / Tel. / T: )
        m = re.search(r"[Tt][e3é][l1][éeè]?[:.]\s*(.+)", stripped)
        if m and "phone" not in signals:
            phone = normalize_phone(m.group(1).strip(), country_code=country_code)
            if phone:
                signals["phone"] = phone
            continue

        # Priority 1.5: OCR-tolerant labelled phone (TE / EL / TEL no-sep / T...)
        # Catches alpha 2026-04-29 variants like "EL0147459270" that the strict
        # regex above misses but that still encode a real phone. Country-agnostic :
        # we extract digits and let `normalize_phone` validate per country.
        if "phone" not in signals:
            phone_digits = _extract_phone_ocr_tolerant(stripped)
            if phone_digits is not None:
                phone = normalize_phone(phone_digits, country_code=country_code)
                if phone:
                    signals["phone"] = phone
                    continue

        # Priority 2: barcode line (configurable min digits) — store raw for pass 2
        _min_digits = _CFG.get("barcode_min_digits", 20)
        normalized = normalize_numeric(stripped)
        if re.fullmatch(rf"\d{{{_min_digits},}}", normalized) and _raw_barcode is None:
            _raw_barcode = normalized
            continue

        # Priority 3: postal_code + city  (5 digits + text)
        m = re.match(r"(\d{5})\s+(.+)", stripped)
        if m and "postal_code" not in signals:
            signals["postal_code"] = m.group(1)
            # city stored for candidate recording only; not used as matching signal
            signals["_city_raw"] = m.group(2).strip()
            continue

        # Priority 4: address  (digit(s) + address keyword)
        if address_re.search(stripped):
            if "address" not in signals:
                signals["address"] = stripped.upper()
            continue

        # Priority 5: unlabelled phone (10-digit pattern, OCR-tolerant)
        if "phone" not in signals:
            phone = normalize_phone(stripped, country_code=country_code)
            if phone:
                signals["phone"] = phone
                continue

        # Priority 6: retailer / enseigne  (uppercase ≥ 4 chars, no price-like pattern)
        if (
            "retailer" not in signals
            and len(stripped) >= 4
            and re.match(r"^[A-ZÀÂÇÉÈÊËÎÏÔÙÛÜ0-9 \-'&]+$", stripped)
            and not re.search(r"\d+[.,]\d{2}", stripped)  # no price (12.50)
        ):
            signals["retailer"] = stripped
            continue

    # Pass 2: retailer-aware store_code extraction — no fallback
    if _raw_barcode and "retailer" in signals and barcode_formats:
        # Use only the first word of the retailer, consistent with fingerprint lookup.
        # ``signals`` values are only ever assigned non-None strings (the dict type
        # is ``str | None`` to model "key may be absent", never a stored None).
        retailer_value = signals["retailer"]
        assert retailer_value is not None
        retailer_first_word = retailer_value.split()[0]
        retailer_key = _normalize_retailer_key(retailer_first_word)
        fmt = barcode_formats.get(retailer_key)
        if fmt:
            for field in fmt.get("fields", []):
                if field["name"] == "store_code":
                    s, e = field["start"], field["end"]
                    if len(_raw_barcode) >= e:
                        signals["store_code"] = _raw_barcode[s:e]
                    break

    return {k: v for k, v in signals.items() if not k.startswith("_") and v is not None}


# ── Fingerprint lookup ────────────────────────────────────────────────────────


def lookup_fingerprints(db: Session, signals: dict) -> uuid.UUID | None:
    """
    Check store_fingerprints for any signal match.
    Returns store_id on first hit (highest-priority match), None otherwise.

    Priority: store_code > retailer_postal > retailer_postal_num

    Note (2026-04-27) : phone is intentionally NOT a fingerprint signal
    anymore — phone identifies a retailer (corporate standard, franchise),
    not a single store. Using a phone fingerprint as a store id risks
    cross-city false matches (a Marseille ticket routed to a Lille store
    via the shared retailer standard). Legacy phone fingerprint rows in
    store_fingerprints are ignored on read and a post-deploy DELETE
    cleans them up. See refactor/phone-as-retailer-signal.
    """
    candidates_to_check: list[tuple[str, str]] = []

    if store_code := signals.get("store_code"):
        # Try retailer-qualified store_code first
        if retailer := signals.get("retailer"):
            retailer_key = retailer.split()[0].upper()  # first word = enseigne
            candidates_to_check.append(("store_code", f"{retailer_key}:{store_code}"))
        candidates_to_check.append(("store_code", store_code))

    if retailer := signals.get("retailer"):
        retailer_key = retailer.split()[0].upper()
        if postal := signals.get("postal_code"):
            candidates_to_check.append(("retailer_postal", f"{retailer_key}:{postal}"))

    if not candidates_to_check:
        return None

    for signal_type, signal_value in candidates_to_check:
        row = db.execute(
            text("""
                SELECT store_id FROM store_fingerprints
                WHERE signal_type = :stype AND signal_value = :sval
                LIMIT 1
            """),
            {"stype": signal_type, "sval": signal_value},
        ).first()
        if row:
            _log.debug("Fingerprint hit: %s=%s → %s", signal_type, signal_value, row.store_id)
            return uuid.UUID(str(row.store_id))

    return None


# ── Candidate intersection + scoring ─────────────────────────────────────────


def _candidate_intersection(db: Session, signals: dict) -> list[dict]:
    """
    Build candidate stores by intersecting sets from multiple signals.
    Each candidate dict has: {store_id, score}.
    """
    scores: dict[str, int] = {}

    # Phone match — phone is a *retailer* signal, not a store-id signal
    # (2026-04-27). A corporate standard phone is shared by every franchise
    # of an enseigne. We only credit phone score to candidates whose retailer
    # is unambiguously inferred from the phone number; a phone shared across
    # several retailers carries no signal at all (Marseille / Lille example).
    if phone := signals.get("phone"):
        retailer_ids = db.execute(
            text(
                "SELECT DISTINCT retailer_id FROM stores "
                "WHERE phone = :phone AND retailer_id IS NOT NULL "
                "AND NOT is_disabled "
                "LIMIT 2"
            ),
            {"phone": phone},
        ).fetchall()
        if len(retailer_ids) == 1:
            inferred_retailer_id = retailer_ids[0].retailer_id
            rows = db.execute(
                text("SELECT id FROM stores WHERE retailer_id = :rid AND NOT is_disabled"),
                {"rid": str(inferred_retailer_id)},
            ).fetchall()
            for row in rows:
                sid = str(row.id)
                scores[sid] = scores.get(sid, 0) + _SIGNAL_SCORES["phone"]

    # Store code match (DP-05)
    if store_code := signals.get("store_code"):
        rows = db.execute(
            text("SELECT id FROM stores WHERE store_code = :code AND NOT is_disabled"),
            {"code": store_code},
        ).fetchall()
        for row in rows:
            sid = str(row.id)
            scores[sid] = scores.get(sid, 0) + _SIGNAL_SCORES["store_code"]

    # Retailer match (index lookup)
    if retailer := signals.get("retailer"):
        retailer_key = retailer.split()[0].upper()
        rows = db.execute(
            text("SELECT id FROM stores WHERE UPPER(retailer) = :retailer AND NOT is_disabled"),
            {"retailer": retailer_key},
        ).fetchall()
        for row in rows:
            sid = str(row.id)
            scores[sid] = scores.get(sid, 0) + _SIGNAL_SCORES["retailer"]

    # Postal + retailer combined bonus
    if (retailer := signals.get("retailer")) and (postal := signals.get("postal_code")):
        retailer_key = retailer.split()[0].upper()
        rows = db.execute(
            text("""
                SELECT id FROM stores
                WHERE UPPER(retailer) = :retailer AND postal_code = :postal AND NOT is_disabled
            """),
            {"retailer": retailer_key, "postal": postal},
        ).fetchall()
        for row in rows:
            sid = str(row.id)
            # retailer_postal supersedes the retailer-only score: normalize to exactly 50
            retailer_already = _SIGNAL_SCORES["retailer"] if sid in scores else 0
            scores[sid] = scores.get(sid, 0) - retailer_already + _SIGNAL_SCORES["retailer_postal"]

    # Address fuzzy (pg_trgm word_similarity)
    if address := signals.get("address"):
        min_sim = _CFG.get("fuzzy_address_min_similarity", 0.70)
        rows = db.execute(
            text("""
                SELECT id, word_similarity(address, :addr) AS sim
                FROM stores
                WHERE address IS NOT NULL
                  AND word_similarity(address, :addr) >= :min_sim
                  AND NOT is_disabled
                ORDER BY sim DESC
                LIMIT 5
            """),
            {"addr": address, "min_sim": min_sim},
        ).fetchall()
        for row in rows:
            sid = str(row.id)
            scores[sid] = scores.get(sid, 0) + _SIGNAL_SCORES["address_fuzzy"]

    return [{"store_id": uuid.UUID(k), "score": v} for k, v in scores.items()]


def score_signals(signals: dict) -> int:
    """
    Return the maximum signal score reachable for the given signals dict.

    Mirrors the upper bound of the scoring used by _candidate_intersection.
    Note (2026-04-27) : phone now scores 30 (retailer-signal weight) — see
    refactor/phone-as-retailer-signal for context.
    """
    score = 0
    if signals.get("phone"):
        score += _SIGNAL_SCORES["phone"]
    if signals.get("store_code"):
        score += _SIGNAL_SCORES["store_code"]
    if signals.get("retailer") and signals.get("postal_code"):
        score += _SIGNAL_SCORES["retailer_postal"]
    elif signals.get("retailer"):
        score += _SIGNAL_SCORES["retailer"]
    if signals.get("address"):
        score += _SIGNAL_SCORES["address_fuzzy"]
    return score


def _load_barcode_formats(db: Session) -> dict[str, dict]:
    """Load retailer receipt barcode formats from DB. Delegates to barcode_reader to avoid duplication."""
    from worker.ocr.barcode_reader import load_barcode_formats

    return load_barcode_formats(db)


def mark_candidate_matched(
    db: Session,
    signals: dict,
    store_id: uuid.UUID,
) -> None:
    """
    Mark a pending StoreCandidate as matched after store resolution.

    Matches on (retailer_guess, postal_code) — same keys used by record_candidate.
    No-op if no matching pending candidate exists (graceful degradation).
    """
    retailer = signals.get("retailer")
    postal = signals.get("postal_code")
    if not retailer or not postal:
        return
    db.execute(
        text("""
            UPDATE store_candidates
            SET status = 'matched', matched_store_id = :store_id
            WHERE retailer_guess = :retailer AND postal_code = :postal AND status = 'pending'
        """),
        {"store_id": str(store_id), "retailer": retailer, "postal": postal},
    )


def detect_store(
    db: Session,
    ocr_lines: list[str],
    country_code: str = "FR",
    barcode_store_code: str | None = None,
    known_retailers: "frozenset[str] | None" = None,
) -> StoreMatch | None:
    """
    Full store detection pipeline.

    1. Extract signals from header lines (retailer-aware two-pass barcode extraction)
    2. Fingerprint lookup (O(1))
    3. Candidate intersection + scoring
    4. Apply thresholds:
       - fingerprint hit           → StoreMatch(auto=True, score=100)
       - score >= threshold_auto   → StoreMatch(auto=True)
       - score >= threshold_confirm → StoreMatch(auto=False) — soft-match
       - score <  threshold_confirm → None (caller records store_candidate)
    """
    barcode_formats = _load_barcode_formats(db)
    signals = extract_store_signals(
        ocr_lines,
        country_code=country_code,
        barcode_formats=barcode_formats,
        known_retailers=known_retailers,
    )

    # Barcode-derived store_code is more reliable than OCR — override.
    if barcode_store_code:
        signals["store_code"] = barcode_store_code

    if not signals:
        return None

    # Fast path: fingerprint
    store_id = lookup_fingerprints(db, signals)
    if store_id is not None:
        return StoreMatch(store_id=store_id, score=100, auto=True)

    # Slow path: intersection + scoring
    candidates = _candidate_intersection(db, signals)
    if not candidates:
        return None

    best = max(candidates, key=lambda c: c["score"])
    threshold_auto: int = _CFG.get("threshold_auto", 80)
    threshold_confirm: int = _CFG.get("threshold_confirm", 40)

    if best["score"] >= threshold_auto:
        _log.info(
            "Store auto-matched: store_id=%s score=%d signals=%s",
            best["store_id"],
            best["score"],
            list(signals.keys()),
        )
        return StoreMatch(store_id=best["store_id"], score=best["score"], auto=True)

    if best["score"] >= threshold_confirm:
        _log.info(
            "Store soft-match: store_id=%s score=%d (below threshold_auto=%d) — cashback blocked",
            best["store_id"],
            best["score"],
            threshold_auto,
        )
        return StoreMatch(store_id=best["store_id"], score=best["score"], auto=False)

    _log.debug("Best candidate score %d < threshold_confirm %d", best["score"], threshold_confirm)
    return None


def record_candidate(
    db: Session,
    signals: dict,
    header_text: str,
    receipt_id: "uuid.UUID | None" = None,
) -> None:
    """
    Insert or increment a StoreCandidate for an unrecognized store.
    Matches on (retailer_guess, postal_code) to merge duplicates.
    receipt_id is recorded on first INSERT only — audit trail, not updated on increments.
    """
    retailer = signals.get("retailer")
    postal = signals.get("postal_code")
    phone = signals.get("phone")
    address = signals.get("address")

    # Try to increment existing candidate (same retailer + CP) — receipt_id intentionally not updated
    if retailer and postal:
        updated = db.execute(
            text("""
                UPDATE store_candidates
                SET occurrence_count = occurrence_count + 1
                WHERE retailer_guess = :retailer AND postal_code = :postal AND status = 'pending'
                RETURNING id
            """),
            {"retailer": retailer, "postal": postal},
        ).first()
        if updated:
            return

    db.execute(
        text("""
            INSERT INTO store_candidates
              (id, raw_header, retailer_guess, address_guess, postal_code, phone, occurrence_count, status, receipt_id)
            VALUES
              (gen_random_uuid(), :raw_header, :retailer, :address, :postal, :phone, 1, 'pending', :receipt_id)
        """),
        {
            "raw_header": header_text,
            "retailer": retailer,
            "address": address,
            "postal": postal,
            "phone": phone,
            "receipt_id": str(receipt_id) if receipt_id else None,
        },
    )


def record_fingerprints(
    db: Session,
    store_id: uuid.UUID,
    signals: dict,
) -> None:
    """
    Passive learning: upsert fingerprints for a confirmed (store_id, signals) pair.

    Called when:
    - The client provided store_id directly (receipt pipeline, passive path)
    - A store was auto-matched above threshold_auto

    On conflict (signal_type, signal_value): increment confirmed_count only.
    Also backfills stores.store_code when NULL (DP-05).

    Note (2026-04-27) : phone is intentionally NOT recorded as a fingerprint
    anymore. A phone is shared by every franchise of an enseigne (corporate
    standard) so a 1:1 phone→store mapping is unsafe. See
    refactor/phone-as-retailer-signal.
    """
    fingerprints_to_record: list[tuple[str, str]] = []

    if store_code := signals.get("store_code"):
        if retailer := signals.get("retailer"):
            retailer_key = retailer.split()[0].upper()
            fingerprints_to_record.append(("store_code", f"{retailer_key}:{store_code}"))
        # also record bare store_code (mirrors lookup fallback)
        fingerprints_to_record.append(("store_code", store_code))
        # Backfill stores.store_code when NULL (DP-05)
        db.execute(
            text("UPDATE stores SET store_code = :code WHERE id = :sid AND store_code IS NULL"),
            {"code": store_code, "sid": str(store_id)},
        )

    if retailer := signals.get("retailer"):
        retailer_key = retailer.split()[0].upper()
        if postal := signals.get("postal_code"):
            fingerprints_to_record.append(("retailer_postal", f"{retailer_key}:{postal}"))

    for signal_type, signal_value in fingerprints_to_record:
        db.execute(
            text("""
                INSERT INTO store_fingerprints
                  (id, store_id, signal_type, signal_value, confirmed_count)
                VALUES
                  (gen_random_uuid(), :store_id, :stype, :sval, 1)
                ON CONFLICT (signal_type, signal_value) DO UPDATE
                  SET confirmed_count = store_fingerprints.confirmed_count + 1
            """),
            {"store_id": str(store_id), "stype": signal_type, "sval": signal_value},
        )
