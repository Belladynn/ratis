"""Extract :class:`FingerprintComponents` from a finalized V3 pipeline output.

Bridges :class:`ParsedTicket` (Phase 2) and :class:`MatchedTicket` (Phase 3)
to the 10 canonical components consumed by :mod:`fingerprint`.

Mapping (cf ARCH § "Composants du fingerprint (10 — [acted 2026-05-11])") :

    1. store_id              ← ``MatchedTicket.store_match_id`` when
                               ``store_status == "matched"`` (UUID hex
                               string), else ``None``. This is the
                               business "priority" signal — when present
                               it identifies the merchant uniquely.
    2. address_normalized    ← ``ParsedHeader.address_line`` upper-cased.
                               Falls back through ``postcode`` / ``city``
                               so partial headers still hash usefully.
    3. brand_normalized      ← ``ParsedHeader.brand`` upper-cased.
    4. iso_date              ← ``ParsedTicket.purchased_at.date().isoformat()``.
                               ``None`` when Phase 2 failed to extract a date.
    5. iso_time              ← ``footer.barcode.time`` rendered as
                               ``HH:MM:SS`` (preferred — encoded by the
                               till) ; ``None`` when the barcode wasn't
                               decoded with a time field. OCR-only flow
                               does NOT yield a precise time (cf.
                               ``persist._resolve_purchased_at``), so we
                               leave it ``None`` rather than fabricate.
    6. time_precision        ← ``"second"`` when ``iso_time`` was set,
                               ``None`` otherwise. ``"minute"`` is
                               reserved for the OCR-only path which V3
                               does not produce yet — it stays a future
                               extension point for the comprehend layer.
    7. total_ttc_cents       ← ``ParsedFooter.total_cents``.
    8. item_count_declared   ← ``ParsedFooter.item_count_declared``.
    9. payment_method        ← normalized enum from the raw OCR string
                               (``"cb"`` / ``"cash"`` / ``"check"`` /
                               ``"other"``). ``None`` if absent.
    10. tva_total_cents      ← ``sum(line.tax_cents for line in
                               footer.vat_breakdown)`` or ``None`` when
                               no VAT breakdown was parsed.

The output is deterministic on the inputs : same parsed+matched →
same components → same fingerprint hash.
"""

from __future__ import annotations

from worker.pipeline.fingerprint import FingerprintComponents
from worker.pipeline.types import MatchedTicket, ParsedTicket

# Payment method normalisation — fold the raw OCR string ("CB", "Espèces",
# "TICKET RESTO", "CARTE BANCAIRE" …) into a small closed enum so the
# fingerprint isn't sensitive to printer variations.
#
# The enum is documented in ARCH § Composants. Anything unrecognised is
# folded to ``"other"`` (rather than ``None``) — the presence/absence of
# a payment line is itself a signal worth keeping, even when we don't
# know which method. This matches the cross-user fraud model : two
# receipts both lacking a payment line is more suspicious than one
# missing and one ``"cb"``.
_PAYMENT_METHOD_TOKENS: dict[str, str] = {
    # CB / Carte bancaire variants
    "cb": "cb",
    "carte": "cb",
    "carte bancaire": "cb",
    "credit": "cb",
    "carte bleue": "cb",
    "bleue": "cb",
    # Cash variants (FR + EN)
    "especes": "cash",
    "espèces": "cash",
    "cash": "cash",
    "liquide": "cash",
    # Check variants
    "cheque": "check",
    "chèque": "check",
    "check": "check",
}


def _normalize_payment_method(raw: str | None) -> str | None:
    """Map the raw OCR payment string to the canonical enum.

    Returns ``None`` when the input is ``None`` / blank, ``"other"`` when
    the input is non-empty but doesn't match any known token (e.g.
    "TICKET RESTO", "AMEX"), or one of {"cb", "cash", "check"} when it
    matches.
    """
    if raw is None:
        return None
    s = raw.strip().lower()
    if not s:
        return None
    if s in _PAYMENT_METHOD_TOKENS:
        return _PAYMENT_METHOD_TOKENS[s]
    # Substring scan — handles "CARTE BANCAIRE", "PAIEMENT CB", "PAR CHEQUE".
    for token, normalized in _PAYMENT_METHOD_TOKENS.items():
        if token in s:
            return normalized
    return "other"


def _normalize_brand_or_address(value: str | None) -> str | None:
    """Upper-case and strip a header field for fingerprint stability.

    Returns ``None`` on blank/None input. Does NOT fold accents — the OCR
    layer already operates on accent-folded text upstream, and a stray
    accent on one run vs. another would be a Phase 2 bug worth surfacing
    via fingerprint divergence rather than swallowing here.
    """
    if value is None:
        return None
    s = value.strip().upper()
    return s or None


def _build_address_normalized(parsed: ParsedTicket) -> str | None:
    """Compose a stable address string from header components.

    Preference order :

    1. ``header.address_line`` (single-line street) — most stable for
       chain stores where the brand is also printed (Intermarché format).
    2. Concatenation of ``(address_line, postcode, city)`` when
       ``address_line`` is missing but the postcode+city pair is set.
    3. ``None`` otherwise.

    The fingerprint hash is canonical-string based, so we deliberately
    pick a single representation here rather than letting two different
    paths through the data yield two different hashes.
    """
    header = parsed.header
    line = _normalize_brand_or_address(header.address_line)
    if line:
        return line
    parts = [
        _normalize_brand_or_address(header.postcode),
        _normalize_brand_or_address(header.city),
    ]
    joined = " ".join(p for p in parts if p)
    return joined or None


def _build_iso_time(parsed: ParsedTicket) -> tuple[str | None, str | None]:
    """Return ``(iso_time, time_precision)`` derived from the parsed footer.

    Priority :

    1. ``footer.barcode.time`` — encoded by the till at transaction time
       (most authoritative). Always returns ``HH:MM:SS`` rendering →
       precision ``"second"``.
    2. Otherwise return ``(None, None)`` — V3 OCR pipeline does not yet
       parse a stand-alone time from the receipt body (see
       ``_resolve_purchased_at`` in persist.py). The ``"minute"``
       precision branch is reserved for future comprehend work.
    """
    footer = parsed.footer
    if footer is None or footer.barcode is None or footer.barcode.time is None:
        return None, None
    t = footer.barcode.time
    # ``time`` objects are always HH:MM:SS shaped — even when the barcode
    # only encoded HHMM (the parser zero-fills seconds). Today we surface
    # "second" precision uniformly because barcode times are emitted by
    # the till at transaction-second granularity (cf. ``ParsedReceiptBarcode``
    # parser). If a future format only carries HHMM we'll set precision
    # to "minute" here.
    return t.strftime("%H:%M:%S"), "second"


def _build_tva_total_cents(parsed: ParsedTicket) -> int | None:
    """Sum the VAT amounts from the parsed footer breakdown.

    Returns ``None`` when the breakdown is empty (receipt didn't print a
    VAT table, or comprehend failed to parse one). ``0`` is reserved for
    a parsed-but-zero-VAT receipt — distinguishable from ``None`` in the
    canonical string (cf. ``test_int_zero_distinct_from_null``).
    """
    footer = parsed.footer
    if footer is None or not footer.vat_breakdown:
        return None
    return sum(line.tax_cents for line in footer.vat_breakdown)


def extract_components_from_pipeline_output(*, parsed: ParsedTicket, matched: MatchedTicket) -> FingerprintComponents:
    """Project ``ParsedTicket`` + ``MatchedTicket`` onto the 10 components.

    Pure function — no DB, no IO. The output is fed straight into
    :func:`worker.pipeline.fingerprint.compute_fp_user` /
    :func:`compute_fp_global` for receipt persistence.

    See module docstring for the field-by-field mapping rationale.
    """
    store_id_str: str | None = None
    if matched.store_status == "matched" and matched.store_match_id is not None:
        store_id_str = str(matched.store_match_id)

    address_normalized = _build_address_normalized(parsed)
    brand_normalized = _normalize_brand_or_address(parsed.header.brand)

    iso_date = parsed.purchased_at.date().isoformat() if parsed.purchased_at is not None else None

    iso_time, time_precision = _build_iso_time(parsed)

    footer = parsed.footer
    total_ttc_cents = footer.total_cents if footer is not None else None
    item_count_declared = footer.item_count_declared if footer is not None else None
    payment_method = _normalize_payment_method(footer.payment_method if footer is not None else None)
    tva_total_cents = _build_tva_total_cents(parsed)

    return FingerprintComponents(
        store_id=store_id_str,
        address_normalized=address_normalized,
        brand_normalized=brand_normalized,
        iso_date=iso_date,
        iso_time=iso_time,
        time_precision=time_precision,
        total_ttc_cents=total_ttc_cents,
        item_count_declared=item_count_declared,
        payment_method=payment_method,
        tva_total_cents=tva_total_cents,
    )


__all__ = ["extract_components_from_pipeline_output"]
