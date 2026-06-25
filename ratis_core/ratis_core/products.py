"""Product helpers shared across services (PA, batch, RW…).

The OFF (OpenFoodFacts) sync stores several name-related fields on the
``products`` table — historically a single ``name`` was persisted but recent
work added ``product_name_fr``, ``generic_name_fr``, ``brands_text`` and
``quantity_text`` for richer display.

``pick_display_name`` picks the best human-readable label for a product row
from the available fields, applying a documented preference order. The helper
accepts both a plain dict (e.g. SQL row mapping) and an ORM ``Product``
instance — the FE-facing serializer (``scan_repository.get_receipt_items``)
uses the dict form, while admin/debug code can pass the ORM model directly.

``claim_first_discovery`` atomically attributes a product to its first
ever scanner (V1.1 — KP-75 / achievement ``exp_unknown_10`` Pionnier·e).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from uuid import UUID

from sqlalchemy import text

from ratis_core.database import affected_rows

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

# Minimum length for a candidate to be considered usable. A 1–3 char string is
# almost never a meaningful display label ("?", "OK", "Bio") and would only
# add noise — fall through to the next candidate instead.
_MIN_LEN = 4


def _candidate(value: Any) -> str | None:
    """Return ``value`` stripped if it's a usable display candidate, else None.

    A candidate must be a non-None string of length ≥ ``_MIN_LEN`` after
    stripping whitespace. Empty strings and non-strings yield ``None``.
    """
    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    if len(cleaned) < _MIN_LEN:
        return None
    return cleaned


def _get(product: Any, key: str) -> Any:
    """Read ``key`` from a dict mapping or an ORM model. Returns ``None`` if missing."""
    if isinstance(product, dict):
        return product.get(key)
    return getattr(product, key, None)


def pick_display_name(product: Any) -> str:
    """Pick the best display name for a product row.

    Order of preference (first usable candidate wins):
      1. ``product_name_fr``  — French commercial name from OFF
      2. ``product_name``     — international fallback (some FR products only set this)
      3. ``generic_name_fr``  — generic French description (e.g. "Yaourt à boire fraise")
      4. ``brands_text`` + ``quantity_text`` joined ("Hipro 4 x 250 g") if both present
      5. ``name``             — raw OFF best-of (the historical single field)

    A candidate is "usable" when it is a non-empty string of at least
    ``_MIN_LEN`` characters after stripping. The raw ``name`` is the ultimate
    fallback even when shorter than ``_MIN_LEN`` so we never return an empty
    string for a row whose legacy ``name`` is the only field populated.

    Args:
        product: a dict-like row mapping (e.g. from SQLAlchemy ``.mappings()``)
            or an ORM ``Product`` instance — both are read via attribute /
            key lookup.

    Returns:
        A non-empty string when the product carries any name field, else the
        raw ``name`` (which is NOT NULL on the table — guaranteed by schema).
    """
    for key in ("product_name_fr", "product_name", "generic_name_fr"):
        cand = _candidate(_get(product, key))
        if cand:
            return cand

    # Composite fallback : "<brands_text> <quantity_text>" — useful when the
    # OFF row has no name but does have brands and quantity (rare but real).
    brands = _candidate(_get(product, "brands_text"))
    qty = _candidate(_get(product, "quantity_text"))
    if brands and qty:
        return f"{brands} {qty}"

    # Final fallback — return the raw name even if shorter than _MIN_LEN, to
    # guarantee a non-empty string for a row whose only populated field is
    # ``name``. Schema enforces ``name <> ''`` (CHECK name_not_empty), so the
    # empty-string branch below is defensive only.
    raw_name = _get(product, "name")
    if isinstance(raw_name, str) and raw_name.strip():
        return raw_name.strip()
    return ""


# ---------------------------------------------------------------------------
# First-discovery attribution (V1.1 — KP-75 / achievement exp_unknown_10)
# ---------------------------------------------------------------------------

# Single SQL statement — atomic CAS-style update :
#   * skip if the product row does not exist (no-op)
#   * skip if the row already has a discoverer (don't overwrite)
#   * skip if the user is shadow-banned or deleted (mirror the achievement
#     dispatcher's anti-ban guard so banned users can't steal credit)
# Returns 1 row updated when the claim succeeds, 0 otherwise.
_CLAIM_FIRST_DISCOVERY_SQL = text(
    """
    UPDATE products
    SET first_discovered_by_user_id = :user_id
    WHERE ean = :ean
      AND first_discovered_by_user_id IS NULL
      AND EXISTS (
          SELECT 1 FROM users u
          WHERE u.id = :user_id
            AND u.is_deleted = false
            AND u.is_shadow_banned = false
      )
    """
)


def claim_first_discovery(db: "Session", product_ean: str | None, user_id: UUID | None) -> bool:
    """Attribute the EAN's first-discovery slot to ``user_id`` if still free.

    Idempotent CAS — the UPDATE only fires when the column is NULL, so the
    second caller for the same EAN simply no-ops. Banned / deleted users
    are silently skipped (the EXISTS subquery is the gate).

    Returns ``True`` when the row was newly attributed, ``False`` otherwise
    (already attributed, missing product, missing user, banned, deleted,
    or NULL inputs). Does NOT commit — the caller owns the transaction
    (the scan-acceptance path commits its own work).

    Used by every scan-acceptance code path (label_task, receipt_task,
    pipeline persist, barcode rescue, admin manual match) so the
    achievement handler ``_eval_unique_products_discovered_count`` has
    something to count against. Cf KP-75 / DP-achievements-v1-followups
    item 1 / migration ``20260510_2100_pfd``.
    """
    # Defensive : NULL inputs are normal in scan paths where the resolution
    # cascade may end with status=unmatched (no product_ean) or where the
    # scan was anonymous (no user_id). Skip silently.
    if not product_ean or user_id is None:
        return False
    result = db.execute(
        _CLAIM_FIRST_DISCOVERY_SQL,
        {"ean": product_ean, "user_id": user_id},
    )
    return affected_rows(result) > 0
