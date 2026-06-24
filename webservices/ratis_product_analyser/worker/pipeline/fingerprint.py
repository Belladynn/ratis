"""Receipt fingerprint compute — anti-fraud PR3.

Pure-functional helpers for the dual-fingerprint mechanism described in
``ARCH_receipt_pipeline.md`` § "Réconciliation tickets — V1 (dual
fingerprint + pHash + admin queue)", decisions acted 2026-05-11.

Two fingerprints over the same 10 canonical components :

- ``fp_user`` = ``sha256(user_id + "|" + canonical_components)`` — strict
  intra-user dedup key (UNIQUE partial index ``idx_receipts_fp_user``
  triggers on collision so the worker can UPDATE the existing receipt,
  same pattern as DA-18 without a barcode).
- ``fp_global`` = ``sha256(canonical_components)`` — cross-user lookup
  for fraud detection (NON unique index ; collisions are the signal).

The 10 components (cf ARCH décision 2026-05-11) :

    1. store_id          (priority — UUID stable > OCR-fragile address)
    2. address_normalized
    3. brand_normalized
    4. iso_date          (YYYY-MM-DD)
    5. iso_time          (HH:MM or HH:MM:SS)
    6. time_precision    ("second" / "minute")
    7. total_ttc_cents
    8. item_count_declared
    9. payment_method
    10. tva_total_cents  (PR ajout 2026-05-11 — very discriminant)

A NULL component is rendered as the empty string in the canonical
representation. ``store_id`` does NOT short-circuit ``address_normalized``
in the hash input — both are concatenated, so an upstream resolver
upgrading a receipt from "no store" to "store_id confirmed" does NOT
silently break dedup. The "priority" mentioned in the ARCH refers to
business logic (use ``store_id`` when present), not the canonical input
to the hash function.

Hard rule (cf ARCH § Pipeline POST /scan/receipt — étape 4) :
``header.date`` AND (``header.brand`` OR ``header.address_full``) MUST
be non-null, otherwise the fingerprint is unreliable (digit-swap on a
single component is recoverable, but missing ``date`` would alias
receipts across days). :func:`validate_mandatory_signals` enforces this.
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from typing import Literal

TimePrecision = Literal["second", "minute"]


@dataclass(frozen=True)
class FingerprintComponents:
    """The 10 components that feed the dual-fingerprint hash.

    All fields are :class:`Optional` because OCR may legitimately fail
    to extract some of them ; the hard-rule check
    (:func:`validate_mandatory_signals`) rejects receipts where the
    *mandatory* subset is missing, but the dataclass itself stays
    permissive so callers can pass partial data through audit /
    forensic paths.

    Per ARCH § "Composants du fingerprint (10 — [acted 2026-05-11])" :

    - ``store_id`` is UUID hex when Phase 3 confirmed a store, else
      ``None``. It is the **priority** signal — when present it
      identifies the merchant uniquely without OCR variation.
    - ``address_normalized`` / ``brand_normalized`` are the
      uppercase, accents-folded forms of the OCR header fields.
    - ``iso_date`` is ``YYYY-MM-DD``.
    - ``iso_time`` is ``HH:MM`` or ``HH:MM:SS`` depending on what the
      ticket printed ; :attr:`time_precision` records which.
    - ``time_precision`` is ``"second"`` when ``iso_time`` carries
      seconds, ``"minute"`` otherwise. ``None`` when ``iso_time`` itself
      is ``None``.
    - ``total_ttc_cents`` / ``tva_total_cents`` are integer cents.
    - ``payment_method`` is the normalized enum (``"cb"`` / ``"cash"`` /
      ``"check"`` / ``"other"``), not the raw OCR string.
    """

    store_id: str | None
    address_normalized: str | None
    brand_normalized: str | None
    iso_date: str | None
    iso_time: str | None
    time_precision: TimePrecision | None
    total_ttc_cents: int | None
    item_count_declared: int | None
    payment_method: str | None
    tva_total_cents: int | None


def canonical_string(components: FingerprintComponents) -> str:
    """Return the deterministic canonical concat used as hash input.

    NULL components → empty string. Fields are separated by ``"|"`` so
    that ("a", None, "b") and (None, "a|b", None) cannot collide. The
    ordering is fixed and documented above ; do NOT reorder — existing
    fingerprints persisted by an earlier run would silently mismatch.
    """
    parts: list[str] = [
        components.store_id or "",
        components.address_normalized or "",
        components.brand_normalized or "",
        components.iso_date or "",
        components.iso_time or "",
        components.time_precision or "",
        str(components.total_ttc_cents) if components.total_ttc_cents is not None else "",
        str(components.item_count_declared) if components.item_count_declared is not None else "",
        components.payment_method or "",
        str(components.tva_total_cents) if components.tva_total_cents is not None else "",
    ]
    return "|".join(parts)


def compute_fp_user(components: FingerprintComponents, user_id: str) -> str:
    """Return the hex sha256 fingerprint that includes ``user_id``.

    Used as the intra-user dedup key (``receipts.parse_fingerprint_user``)
    with a UNIQUE partial index — a collision triggers the rescan
    UPDATE flow (DA-18 fallback, cf ARCH § étape 6).
    """
    payload = f"{user_id}|{canonical_string(components)}"
    return sha256(payload.encode("utf-8")).hexdigest()


def compute_fp_global(components: FingerprintComponents) -> str:
    """Return the hex sha256 fingerprint that excludes ``user_id``.

    Used for the cross-user lookup (``receipts.parse_fingerprint_global``)
    — collisions are the fraud-suspicion signal, NOT a hard constraint
    (NON-unique index). The cross-user policy table lives in
    ARCH § "Politique cross-user".
    """
    return sha256(canonical_string(components).encode("utf-8")).hexdigest()


def validate_mandatory_signals(
    components: FingerprintComponents,
) -> tuple[bool, str | None]:
    """Hard rule check (cf ARCH décision 2026-05-11, étape 4).

    Returns ``(True, None)`` when ``iso_date`` is non-null AND at least
    one of (``brand_normalized``, ``address_normalized``) is non-null.

    Otherwise returns ``(False, reason)`` where ``reason`` is a short
    snake_case label suitable for ``scans.rejected_reason`` (prefixed by
    the caller with ``"missing_mandatory_signals_for_dedup:"``).

    Returned reasons :

    - ``"missing_date"`` — no ``iso_date`` (the most authoritative
      anti-collision signal, can't be recovered from elsewhere).
    - ``"missing_brand_and_address"`` — both ``brand_normalized`` and
      ``address_normalized`` are ``None`` (the merchant identity is
      unknown, fingerprint would alias unrelated receipts).
    """
    if not components.iso_date:
        return False, "missing_date"
    if not components.brand_normalized and not components.address_normalized:
        return False, "missing_brand_and_address"
    return True, None


__all__ = [
    "FingerprintComponents",
    "TimePrecision",
    "canonical_string",
    "compute_fp_global",
    "compute_fp_user",
    "validate_mandatory_signals",
]
