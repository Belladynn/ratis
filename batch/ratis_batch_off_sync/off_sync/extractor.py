"""Product extraction from raw OFF JSON.

To add a new stored field:
  1. Add the field to the returned dict below.
  2. Add the column to repository._SYNC_COLS (with its PG type).
  3. Add the field name to API_FIELDS.
  4. Create an Alembic migration for the new column.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

from ratis_core.knowledge import classify, load_knowledge

if TYPE_CHECKING:
    from off_sync.sources import Source

_EAN_RE = re.compile(r"^\d{8,13}$")
_QTY_RE = re.compile(r"^\s*(\d+(?:[.,]\d+)?)\s*([a-zA-Zµ]+\.?)\s*$")

# Photo CDN whitelist — Open*Facts projects share the same URL pattern but
# host on their own subdomain (images/static/world . open<x>facts . org).
# Sourced from off_sync.sources.SOURCES — kept as a frozenset literal here
# to avoid an import cycle (sources.py → no other module of this package).
_ALLOWED_PHOTO_HOSTS: frozenset[str] = frozenset(
    {
        # OFF
        "images.openfoodfacts.org",
        "static.openfoodfacts.org",
        "world.openfoodfacts.org",
        # OBP
        "images.openbeautyfacts.org",
        "static.openbeautyfacts.org",
        "world.openbeautyfacts.org",
        # OPF
        "images.openproductsfacts.org",
        "static.openproductsfacts.org",
        "world.openproductsfacts.org",
        # OPFF
        "images.openpetfoodfacts.org",
        "static.openpetfoodfacts.org",
        "world.openpetfoodfacts.org",
    }
)

# Field length caps to limit oversized payloads from a compromised OFF API.
_MAX_NAME = 500
_MAX_GENERIC_NAME = 500
_MAX_BRANDS = 200
_MAX_QTY_RAW = 100
_MAX_URL = 2048
_MAX_PRODUCT_QUANTITY = 100_000  # any unit — above this is implausible
_MAX_TAG_ITEM = 100
# "6 x 125 g", "6 × 125 g", "6X125g" — case-insensitive 'x'
_MULTI_QTY_RE = re.compile(
    r"^\s*(\d+)\s*[x×X]\s*(\d+(?:[.,]\d+)?)\s*([a-zA-Zµ]+\.?)\s*$",
    re.IGNORECASE,
)

# Fields requested from the OFF Search API.
# generic_name_fr / generic_name : richer descriptions ("Yaourt à boire fraise")
# used by ratis_core.products.pick_display_name when product_name_fr is poor.
API_FIELDS = (
    "code,product_name,product_name_fr,generic_name,generic_name_fr,"
    "image_front_url,image_front_small_url,"
    "quantity,product_quantity,product_quantity_unit,packagings,"
    "brands,categories_tags,labels_tags,allergens_tags,ingredients_tags,"
    "origins_tags,"
    "conservation_conditions"
)


# ---------------------------------------------------------------------------
# Storage-type rules — loaded once at module startup from ratis_core.
# Tests inject their own dict via the `rules` kwarg to avoid file I/O.
# ---------------------------------------------------------------------------
def _load_storage_rules() -> dict[str, list[str]]:
    data = load_knowledge()
    if "storage_type" not in data:
        raise KeyError("Missing 'storage_type' key in product_knowledge.json")
    return data["storage_type"]


_STORAGE_RULES_DATA: dict[str, list[str]] = _load_storage_rules()


def _derive_storage_type(
    categories_tags: list[str] | None,
    labels_tags: list[str] | None,
    conservation_conditions: str | None,
    *,
    rules: dict[str, list[str]] | None = None,
) -> str | None:
    """Derive storage_type from OFF fields using ratis_core.knowledge.classify.

    Returns:
        'frozen'    — surgelé
        'fresh'     — frais / réfrigéré
        'ambient'   — catégorie alimentaire connue, non réfrigérée
        'unmatched' — champs présents mais aucun pattern reconnu, pas de catégorie
        None        — aucun champ disponible
    """
    if rules is None:
        rules = _STORAGE_RULES_DATA

    cats = categories_tags or []
    labs = labels_tags or []
    cons = conservation_conditions or ""

    if not cats and not labs and not cons:
        return None

    tags = [t.lower() for t in cats] + [t.lower() for t in labs]
    matched = classify(rules, tags, cons.lower())
    if matched is not None:
        return matched

    return "ambient" if cats else "unmatched"


# ---------------------------------------------------------------------------
# Quantity helpers
# ---------------------------------------------------------------------------


def _parse_quantity(s: str | None) -> tuple[float, str] | None:
    """Parse a simple quantity string like '500 g' or '1.5 kg'."""
    if not s:
        return None
    m = _QTY_RE.match(s.strip())
    if not m:
        return None
    try:
        return float(m.group(1).replace(",", ".")), m.group(2).rstrip(".")
    except ValueError:
        return None


def _parse_quantity_extended(s: str | None) -> tuple[float, str] | None:
    """Parse simple ('500 g') or multi-pack ('6 x 125 g') quantity strings."""
    if not s:
        return None
    s = s.strip()

    result = _parse_quantity(s)
    if result:
        return result

    m = _MULTI_QTY_RE.match(s)
    if m:
        try:
            n = float(m.group(1))
            qty = float(m.group(2).replace(",", "."))
            unit = m.group(3).rstrip(".")
            return n * qty, unit
        except ValueError:
            pass

    return None


def extract_net_weight(product: dict[str, Any]) -> tuple[float, str] | None:
    """Compute net weight from an OFF product dict.

    Priority:
      1. product_quantity (numeric OFF field) + product_quantity_unit
      2. packagings[i].quantity_per_unit_value × number_of_units — first valid component
      3. Parse quantity_raw or quantity string (handles 'N x V unit' patterns)

    Works with both raw OFF API dicts and already-extracted product dicts.
    Returns (value, unit) or None if no usable data is available.
    """
    # P1: numeric fields provided directly by OFF
    qty = product.get("product_quantity")
    unit = product.get("product_quantity_unit")
    if qty is not None and unit:
        try:
            return float(qty), str(unit)
        except (ValueError, TypeError):
            pass

    # P2: packagings array — take the first component with valid data
    for pkg in product.get("packagings") or []:
        value = pkg.get("quantity_per_unit_value")
        n_units = pkg.get("number_of_units")
        pkg_unit = pkg.get("quantity_per_unit_unit") or "g"
        if value is not None and n_units:
            try:
                return float(value) * float(n_units), str(pkg_unit)
            except (ValueError, TypeError):
                continue

    # P3: raw string — handles "500 g", "6 x 125 g", etc.
    raw_str = (product.get("quantity_raw") or product.get("quantity") or "").strip() or None
    if raw_str:
        result = _parse_quantity_extended(raw_str)
        if result:
            return result

    return None


# ---------------------------------------------------------------------------
# Security helpers
# ---------------------------------------------------------------------------


def _sanitize_tags(tags: list | None) -> list[str]:
    """Keep only string items within the length cap."""
    if not tags:
        return []
    return [t for t in tags if isinstance(t, str) and len(t) <= _MAX_TAG_ITEM]


def _sanitize_text(value: Any, max_len: int) -> str | None:
    """Strip + length-cap a free-text OFF field. Returns None for empty/oversized."""
    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    if not cleaned or len(cleaned) > max_len:
        return None
    return cleaned


def _safe_url(url: str | None) -> str | None:
    """Return the URL only if it points to a known OFF CDN host, else None."""
    if not url:
        return None
    if len(url) > _MAX_URL:
        return None
    try:
        host = urlparse(url).hostname or ""
    except ValueError:
        return None
    return url if host in _ALLOWED_PHOTO_HOSTS else None


# ---------------------------------------------------------------------------
# Product extraction
# ---------------------------------------------------------------------------


def extract_product(
    raw: dict[str, Any],
    *,
    source: "Source | None" = None,
) -> dict[str, Any] | None:
    """Validate and extract a product from a raw Open*Facts item.

    Returns a dict ready for upsert, or None if the item is invalid.
    Works for both API responses and JSONL dump lines.

    Args:
        raw: a single product dict as emitted by the API or JSONL dump.
        source: the active Source. Drives whether ``storage_type`` is
            classified (``Source.classify_storage`` flag — True for OFF,
            False for OBP/OPF/OPFF, which are non-food catalogues). When
            ``None`` (default, back-compat for ad-hoc callers and the OFF
            test corpus), the classifier runs as before.
    """
    ean = raw.get("code") or ""
    if not _EAN_RE.match(ean):
        return None
    name = (raw.get("product_name_fr") or raw.get("product_name") or "").strip()
    if not name:
        return None
    if len(name) > _MAX_NAME:
        return None

    qty_raw = (raw.get("quantity") or "").strip() or None
    if qty_raw and len(qty_raw) > _MAX_QTY_RAW:
        qty_raw = None
    net_weight = extract_net_weight(raw)
    product_quantity = net_weight[0] if net_weight else None
    product_quantity_unit = net_weight[1] if net_weight else None
    if product_quantity is not None and product_quantity > _MAX_PRODUCT_QUANTITY:
        product_quantity = None
        product_quantity_unit = None

    brands = (raw.get("brands") or "").strip() or None
    if brands and len(brands) > _MAX_BRANDS:
        brands = None

    categories_tags = _sanitize_tags(raw.get("categories_tags"))

    # Multi-field enrichment for ratis_core.products.pick_display_name.
    # Each is independently optional — missing / oversized values become NULL
    # and the helper falls back to the next preference. Note : the existing
    # ``name`` column already holds the best-of-FR/EN (product_name_fr >
    # product_name) — no need to also persist the international ``product_name``
    # separately.
    product_name_fr = _sanitize_text(raw.get("product_name_fr"), _MAX_NAME)
    generic_name_fr = _sanitize_text(raw.get("generic_name_fr") or raw.get("generic_name"), _MAX_GENERIC_NAME)

    # Storage classification — food-only by design. For non-food catalogues
    # (OBP cosmetics, OPF generic, OPFF pet food) the rules in
    # product_knowledge.json have no meaningful match — we persist NULL and
    # the FE / scan pipeline simply skips the storage-type badge.
    # Back-compat : source=None keeps the legacy behaviour (used by direct
    # callers and pre-multi-source tests).
    if source is None or source.classify_storage:
        storage_type = _derive_storage_type(
            categories_tags or None,
            raw.get("labels_tags"),
            raw.get("conservation_conditions"),
        )
    else:
        storage_type = None

    return {
        "ean": ean,
        "name": name,
        "photo_url": _safe_url(raw.get("image_front_url")),
        "photo_url_small": _safe_url(raw.get("image_front_small_url")),
        "brands": brands,
        "product_quantity": product_quantity,
        "product_quantity_unit": product_quantity_unit,
        "quantity_raw": qty_raw,
        "storage_type": storage_type,
        "allergens_tags": _sanitize_tags(raw.get("allergens_tags")),
        "ingredients_tags": _sanitize_tags(raw.get("ingredients_tags")),
        "categories_tags": categories_tags,
        "labels_tags": _sanitize_tags(raw.get("labels_tags")),
        # Phase C-2 — origins_tags drives the ``attribute:french`` mission
        # qualifier in the PA dual-emit (cf services.product_attributes.
        # is_french_product). Sanitised through the same length cap as the
        # other tag arrays. NULL/empty in OFF rows is preserved verbatim ;
        # the consumer treats missing data as "non-french signal absent",
        # which is the safe default (no false positive).
        "origins_tags": _sanitize_tags(raw.get("origins_tags")),
        # OFF multi-field enrichment (PR feat/off-sync-multi-fields-display-name).
        # Stored verbatim so ratis_core.products.pick_display_name can compose
        # the best display label downstream.
        "product_name_fr": product_name_fr,
        "generic_name_fr": generic_name_fr,
        # ``brands_text`` mirrors the existing ``brands`` field — kept distinct
        # so the column naming aligns with ``quantity_text`` and stays
        # forward-compatible with a possible cleanup of ``brands`` (legacy).
        "brands_text": brands,
        "quantity_text": qty_raw,
    }


def is_france_product(raw: dict[str, Any]) -> bool:
    """Return True if the product is sold in France.

    Used for dump mode only — the API filters via countries_tags param.
    """
    tags = raw.get("countries_tags") or []
    return "en:france" in tags
