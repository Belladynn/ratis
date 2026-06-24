"""Map a ``Product`` to a Ratis-canonical FE category.

Wave 12 (PO ticket 2026-05-14) — the Liste tab now groups items by
category under section headers. PO labels :

    frais → boulangerie → epicerie → boissons → vrac → autres

The products table does NOT carry a ``classification`` column (CLAUDE.md
referenced one that never landed). We derive the FE category from two
signals already populated by ``ratis_batch_off_sync`` :

* ``products.storage_type``  — ``frozen`` / ``fresh`` / ``ambient`` /
  ``unmatched`` / NULL  (cf migration / extractor.py).
* ``products.categories_tags`` — OFF tag array
  (e.g. ``['en:beverages', 'en:sodas']``, ``['en:breads']``, …).

Resolution order :

1. If ``categories_tags`` contains a ``boulangerie`` / ``bakery`` /
   ``bread`` signal → ``boulangerie`` (regardless of storage_type — a
   fresh baguette is still bakery, not « frais »).
2. If ``categories_tags`` contains a ``beverages`` / ``drinks`` /
   ``boissons`` signal → ``boissons``.
3. If ``storage_type`` ∈ {``frozen``, ``fresh``} → ``frais`` (covers
   the bulk of fridge/freezer rows : dairy, meat, cheese, frozen
   pizzas, yaourts, etc.).
4. If ``products.source = 'internal'`` AND ``unit`` is set (kg/l/unit)
   → ``vrac`` (loose-weighted SKUs, ``2``-prefixed internal EANs).
5. If ``storage_type = 'ambient'`` OR ``categories_tags`` non-empty
   → ``epicerie`` (default for shelf-stable food).
6. Anything else (no signal at all) → ``autres``.

Categories are returned as the snake-case key (``frais``, ``boulangerie``,
…) — the FE owns the French display label via i18n.
"""

from __future__ import annotations

# Keyword groups matched against the OFF ``categories_tags`` lowercased
# strings. We strip the language prefix (``en:`` / ``fr:`` / ``xx:``)
# before matching so a tag like ``en:breads`` and ``fr:pains`` both hit
# the ``boulangerie`` bucket.
_BAKERY_KEYWORDS = (
    "bread",
    "pain",
    "boulanger",
    "viennoiserie",
    "patisserie",
    "pastr",
    "baguette",
    "brioche",
    "croissant",
)
_BEVERAGE_KEYWORDS = (
    "beverage",
    "boisson",
    "drink",
    "soda",
    "juice",
    "jus",
    "water",
    "eau",
    "tea",
    "the",
    "coffee",
    "cafe",
    "wine",
    "vin",
    "beer",
    "biere",
)


def _strip_lang_prefix(tag: str) -> str:
    """``en:breads`` → ``breads`` · ``fr:pains`` → ``pains``. Idempotent
    when no prefix is present."""
    if ":" in tag:
        return tag.split(":", 1)[1]
    return tag


def _any_keyword(tags: list[str], keywords: tuple[str, ...]) -> bool:
    for raw in tags:
        if not raw:
            continue
        stripped = _strip_lang_prefix(raw.lower())
        for kw in keywords:
            if kw in stripped:
                return True
    return False


def resolve_category(product) -> str | None:
    """Return the Ratis-canonical FE category key for a product.

    ``product`` is a ``ratis_core.models.product.Product`` SQLAlchemy
    instance (relationship-loaded). May be ``None`` (item without a
    resolved product row) — caller handles that branch.
    """
    if product is None:
        return None

    tags: list[str] = list(product.categories_tags or [])
    storage = product.storage_type
    source = product.source
    unit = product.unit

    # 1. Bakery first — bread products are usually ``fresh`` storage but
    #    the user thinks of them as their own section.
    if _any_keyword(tags, _BAKERY_KEYWORDS):
        return "boulangerie"

    # 2. Beverages — same logic ; a chilled bottle of water is « boissons »,
    #    not « frais ».
    if _any_keyword(tags, _BEVERAGE_KEYWORDS):
        return "boissons"

    # 3. Fridge / freezer.
    if storage in ("frozen", "fresh"):
        return "frais"

    # 4. Internal weighted SKUs = vrac (loose-weighted produce, bulk).
    if source == "internal" and unit is not None:
        return "vrac"

    # 5. Ambient or any other tagged row → épicerie (shelf-stable food).
    if storage == "ambient" or tags:
        return "epicerie"

    # 6. No signal at all.
    return "autres"


# Canonical display order — exported for FE alignment but not used by
# the backend itself (the FE owns the order via its own enum). Kept
# here so a future ``GET /lists/categories`` endpoint can mirror it.
CATEGORY_ORDER: tuple[str, ...] = (
    "frais",
    "boulangerie",
    "epicerie",
    "boissons",
    "vrac",
    "autres",
)
