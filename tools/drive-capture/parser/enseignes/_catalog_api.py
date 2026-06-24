"""Shared parser for the Casino-group ``catalog-api`` backend.

Franprix and several Casino-group banners (Casino, Petit Casino, ...) run
the *same* commerce backend. Promotion catalogues come from the JSON REST
endpoint::

    /catalog-api/rest/api/promotion/by_department?...&storeId=<id>

whose body is ``{"items": [{"department": {...}, "promotions": [...]}],
"totalItems": N}``. A single-promotion variant is served by::

    /catalog-api/rest/api/promotion/by_promotion_id/<id>?storeId=<id>

which returns a bare promotion object (no ``items`` envelope).

Each promotion carries a ``product`` sub-object with a shared schema
(``title``, ``brand.name``, ``ean``, ``capacity``, ``batchPrice``,
``department.{title,slug}``, ``originCountry``, ...) plus promotion fields
(``discount`` percent, ``discountPrice``, ``discountFid``). The store the
promotion belongs to is the ``storeId`` URL query parameter — it is not
embedded in the body.

Drive stores come from ``/api/store`` whose body is
``{"stores": [{"storeId", "lat", "lng", ...}], ...}``.

``franprix.py`` and ``casino.py`` are thin wrappers binding an enseigne
name and a capture-host filter to the generic logic here.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from urllib.parse import parse_qs, urlparse

from parser.capture import iter_records
from parser.model import ParsedProduct, ParsedStore
from parser.pricing import to_cents

logger = logging.getLogger(__name__)

_BY_DEPARTMENT = "/catalog-api/rest/api/promotion/by_department"
_BY_PROMOTION_ID = "/catalog-api/rest/api/promotion/by_promotion_id"
_STORE_ENDPOINT = "/api/store"


# --------------------------------------------------------------------------
# product extraction
# --------------------------------------------------------------------------
def _store_id_from_url(url: str | None) -> str | None:
    """Pull the ``storeId`` query parameter out of a capture record URL."""
    if not url:
        return None
    values = parse_qs(urlparse(url).query).get("storeId")
    return values[0] if values else None


def _iter_promotions(body: object) -> Iterator[dict]:
    """Yield every promotion dict from a ``catalog-api`` response body.

    Handles both response shapes: the ``by_department`` envelope
    (``{"items": [{"promotions": [...]}]}``) and the bare single
    ``by_promotion_id`` promotion object.
    """
    if not isinstance(body, dict):
        return
    items = body.get("items")
    if isinstance(items, list):
        for item in items:
            if not isinstance(item, dict):
                continue
            promotions = item.get("promotions")
            if isinstance(promotions, list):
                for promo in promotions:
                    if isinstance(promo, dict) and isinstance(promo.get("product"), dict):
                        yield promo
        return
    # bare promotion object (by_promotion_id)
    if isinstance(body.get("product"), dict) and "promotionId" in body:
        yield body


def _brand_name(brand: object) -> str | None:
    """Brand label — the ``catalog-api`` brand is ``{"name": ..., ...}``."""
    if isinstance(brand, dict):
        name = brand.get("name")
        return name if isinstance(name, str) and name else None
    if isinstance(brand, str) and brand:
        return brand
    return None


def _department_title(department: object) -> str | None:
    """Rayon label from the product ``department`` sub-object."""
    if isinstance(department, dict):
        title = department.get("title")
        if isinstance(title, str) and title:
            return title
    return None


def _first_image(medias: object) -> str | None:
    """First product image URL from the ``medias.productImages`` list."""
    if not isinstance(medias, dict):
        return None
    images = medias.get("productImages")
    if isinstance(images, list) and images and isinstance(images[0], str):
        return images[0]
    return None


def _discount_pct(raw: object) -> int | None:
    """Parse the promotion's stated ``discount`` percentage to an int.

    The ``catalog-api`` ships the *authoritative* discount percent on the
    promotion itself (``"45"`` -> 45). It is not recomputed from prices —
    the retailer's reference price for the percentage can differ from the
    shelf ``batchPrice``.
    """
    if isinstance(raw, bool):
        return None
    if isinstance(raw, (int, float)):
        return round(raw)
    if isinstance(raw, str):
        cents = to_cents(raw)  # reuses the numeric-string parser
        return cents // 100 if cents is not None else None
    return None


def _product_from_promotion(
    enseigne: str, promo: dict, store_ref: str | None, captured_at: str
) -> ParsedProduct | None:
    """Build one ``ParsedProduct`` from a ``catalog-api`` promotion.

    The base (struck-through) price is ``product.batchPrice`` /
    ``product.priceBase``; the promo price is the promotion's
    ``discountPrice``. Casino's ``by_department`` frequently omits all
    prices — ``price_cents`` is then ``None`` and the gap is logged by the
    caller.

    ``promo_pct`` is taken from the promotion's stated ``discount`` field
    (authoritative), not recomputed from prices: the retailer's reference
    for the percentage can differ from the shelf base price.
    """
    product = promo.get("product")
    if not isinstance(product, dict):
        return None
    name = product.get("title")
    if not isinstance(name, str) or not name:
        return None

    ean = product.get("ean") or None
    base_cents = to_cents(product.get("batchPrice")) or to_cents(product.get("priceBase"))
    promo_cents = to_cents(promo.get("discountPrice"))
    pct = _discount_pct(promo.get("discount"))

    is_promo = pct is not None or promo_cents is not None or bool(promo.get("promotionId"))

    per_measure = to_cents(product.get("priceMeasureUnit"))
    measure = product.get("measureUnit")
    measure_unit = measure.lower() if isinstance(measure, str) and measure else None

    return ParsedProduct(
        enseigne=enseigne,
        name=name,
        captured_at=captured_at,
        store_ref=store_ref,
        ean=ean,
        brand=_brand_name(product.get("brand")),
        quantity=product.get("capacity") or product.get("shortDescription") or None,
        category=_department_title(product.get("department")),
        price_cents=base_cents,
        price_per_measure_cents=per_measure,
        measure_unit=measure_unit,
        promo_price_cents=promo_cents,
        promo_pct=pct,
        is_promo=is_promo,
        product_url=None,
        image_url=_first_image(product.get("medias")),
        available=None,
        enseigne_product_id=product.get("retailerId") or None,
    )


def parse_products(ndjson_path: str, *, enseigne: str) -> Iterator[ParsedProduct]:
    """Yield every product observation in a Casino-group capture file.

    De-duplicated on ``(ean, store_ref, price_cents)`` so a product seen on
    several captured pages is counted once. ``store_ref`` comes from the
    ``storeId`` URL query parameter of each ``promotion`` endpoint hit.
    """
    seen: set[tuple] = set()
    n_promotions = 0
    n_with_ean = 0
    n_emitted = 0
    n_no_price = 0
    n_ignored_no_name = 0
    n_ignored_dup = 0
    for record in iter_records(ndjson_path):
        url = record.get("url") or ""
        if _BY_DEPARTMENT not in url and _BY_PROMOTION_ID not in url:
            continue
        store_ref = _store_id_from_url(url)
        captured_at = record.get("captured_at") or ""
        for promo in _iter_promotions(record.get("response_json")):
            n_promotions += 1
            product = _product_from_promotion(enseigne, promo, store_ref, captured_at)
            if product is None:
                n_ignored_no_name += 1
                continue
            key = (product.ean, product.store_ref, product.price_cents)
            if key in seen:
                n_ignored_dup += 1
                continue
            seen.add(key)
            n_emitted += 1
            if product.ean:
                n_with_ean += 1
            if product.price_cents is None:
                n_no_price += 1
            yield product
    logger.info(
        "%s: %d promotion(s) trouvées (%d avec EAN), %d ParsedProduct extraits, %d ignorés (%d sans nom, %d doublons)",
        enseigne,
        n_promotions,
        n_with_ean,
        n_emitted,
        n_ignored_no_name + n_ignored_dup,
        n_ignored_no_name,
        n_ignored_dup,
    )
    if n_no_price:
        logger.info(
            "%s: %d observation(s) sans prix — l'endpoint by_department de "
            "cette enseigne n'expose pas toujours batchPrice/priceBase",
            enseigne,
            n_no_price,
        )


# --------------------------------------------------------------------------
# store extraction
# --------------------------------------------------------------------------
def _store_from_entry(enseigne: str, entry: object) -> ParsedStore | None:
    """Build a ``ParsedStore`` from one ``/api/store`` ``stores[]`` entry."""
    if not isinstance(entry, dict):
        return None
    ref = entry.get("storeId")
    if ref in (None, ""):
        return None
    lat = entry.get("lat")
    lng = entry.get("lng")
    return ParsedStore(
        enseigne=enseigne,
        store_ref=str(ref),
        name=entry.get("name") or None,
        city=entry.get("city") or None,
        postal_code=entry.get("postalCode") or entry.get("zipCode") or None,
        lat=lat if isinstance(lat, (int, float)) else None,
        lng=lng if isinstance(lng, (int, float)) else None,
    )


def parse_stores(ndjson_path: str, *, enseigne: str) -> Iterator[ParsedStore]:
    """Yield drive stores from a Casino-group ``/api/store`` capture.

    De-duplicated on ``store_ref``.
    """
    seen: set[str] = set()
    n_emitted = 0
    for record in iter_records(ndjson_path):
        url = record.get("url") or ""
        if urlparse(url).path != _STORE_ENDPOINT:
            continue
        body = record.get("response_json")
        if not isinstance(body, dict):
            continue
        entries = body.get("stores")
        if not isinstance(entries, list):
            continue
        for entry in entries:
            store = _store_from_entry(enseigne, entry)
            if store is None or store.store_ref in seen:
                continue
            seen.add(store.store_ref)
            n_emitted += 1
            yield store
    logger.info("%s: %d magasin(s) drive extraits", enseigne, n_emitted)
