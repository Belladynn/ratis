"""Monoprix scraper-side parsers — courses.monoprix.fr.

Three parse phases:

* ``parse_stores`` — JSON from GET /api/ecomdeliverydestinations/v4/delivery-addresses
  ?deliveryMethod=CUSTOMER_COLLECTION
  Returns pickup points with GPS coordinates and resolvedRegionId.

* ``parse_categories`` — JSON from GET /api/webproductpagews/v1/categories
  ?decoration=false&categoryDepth=4
  Returns flat list of leaf categories for queuing rayon fetches.

* ``parse_rayon`` — JSON from GET /api/webproductpagews/v6/product-pages
  ?categoryId={id}&maxPageSize=300 (or ?pageToken={token}&maxPageSize=300)
  Paginated via nextPageToken.  NOTE: Monoprix has NO EAN at any level.
"""

from __future__ import annotations

import logging

from scraper.parsers._models import ParsedResult, ProductResult, StoreResult, to_cents

logger = logging.getLogger(__name__)


def _empty() -> ParsedResult:
    return ParsedResult()


def parse_stores(response_json: dict) -> ParsedResult:
    """Parse Monoprix drive/click-and-collect store list.

    Source: GET /api/ecomdeliverydestinations/v4/delivery-addresses
    ?deliveryMethod=CUSTOMER_COLLECTION
    """
    if not response_json:
        logger.debug("monoprix.parse_stores: empty response")
        return _empty()

    stores: list[StoreResult] = []
    # API may return a bare list or a dict with a "deliveryAddresses" key
    if isinstance(response_json, list):
        delivery_addresses = response_json
    else:
        delivery_addresses = response_json.get("deliveryAddresses") or []
    for store in delivery_addresses:
        try:
            store_id = str(store.get("deliveryDestinationId") or "")
            if not store_id:
                continue

            coords = store.get("coordinates") or {}
            lat: float | None = None
            lng: float | None = None
            try:
                raw_lat = coords.get("latitude")
                raw_lng = coords.get("longitude")
                if raw_lat is not None:
                    lat = float(raw_lat) or None
                if raw_lng is not None:
                    lng = float(raw_lng) or None
            except (TypeError, ValueError):
                pass

            region_id = store.get("resolvedRegionId")
            extra: dict = {}
            if region_id is not None:
                extra["region_id"] = region_id

            stores.append(StoreResult(
                store_id=store_id,
                name=store.get("name") or None,
                city=store.get("city") or None,
                postal_code=str(store.get("postalCode") or "").strip() or None,
                lat=lat,
                lng=lng,
                extra=extra,
            ))
        except Exception as exc:
            logger.warning("monoprix.parse_stores: skipping store — %s", exc)

    logger.debug("monoprix.parse_stores: %d stores extracted", len(stores))
    return ParsedResult(stores=stores)


def _collect_leaf_categories(node: dict, depth: int = 0) -> list[dict]:
    """Recursively collect leaf categories (no children or depth >= 3)."""
    # Monoprix API uses "childCategories"; older/generic APIs use "children"/"subcategories"
    children = (
        node.get("childCategories")
        or node.get("children")
        or node.get("subcategories")
        or []
    )
    if not children or depth >= 3:
        cat_id = node.get("categoryId") or node.get("id")
        name = node.get("name") or node.get("label")
        if cat_id and name:
            return [{"id": str(cat_id), "name": str(name)}]
        return []
    results = []
    for child in children:
        if isinstance(child, dict):
            results.extend(_collect_leaf_categories(child, depth + 1))
    return results


def parse_categories(response_json: dict, region_id: str) -> list[dict]:
    """Parse Monoprix category tree into a flat list of leaf categories.

    Source: GET /api/webproductpagews/v1/categories?decoration=false&categoryDepth=4

    Returns list of ``{id, name, region_id}`` for leaf nodes (depth >= 3 or no children).
    The caller uses these to build rayon fetch URLs.
    """
    if not response_json:
        logger.debug("monoprix.parse_categories: empty response")
        return []

    # The response may be a dict with a root key, or a list of top-level categories
    leaves: list[dict] = []
    if isinstance(response_json, list):
        top_level = response_json
    else:
        # Try common wrapper keys
        top_level = (
            response_json.get("categories")
            or response_json.get("data")
            or response_json.get("items")
            or []
        )
        if isinstance(top_level, dict):
            top_level = [top_level]

    for node in top_level:
        if not isinstance(node, dict):
            continue
        leaves.extend(_collect_leaf_categories(node))

    # Inject region_id into each leaf
    for leaf in leaves:
        leaf["region_id"] = region_id

    logger.debug("monoprix.parse_categories: %d leaf categories found", len(leaves))
    return leaves


def parse_rayon(response_json: dict) -> ParsedResult:
    """Parse Monoprix product page response.

    Source: GET /api/webproductpagews/v6/product-pages
    ?categoryId={id}&maxPageSize=300 (first page)
    or ?pageToken={token}&maxPageSize=300 (subsequent pages)

    NOTE: Monoprix has NO EAN at any level — ean is always None.
    Pagination via nextPageToken.
    """
    if not response_json:
        logger.debug("monoprix.parse_rayon: empty response")
        return _empty()

    products: list[ProductResult] = []

    # Product list: v6 API wraps products in productGroups[].decoratedProducts[]
    raw_products: list = []
    product_groups = response_json.get("productGroups")
    if product_groups:
        for group in product_groups:
            if isinstance(group, dict):
                raw_products.extend(group.get("decoratedProducts") or [])
    else:
        # Fallback for older / alternative response shapes
        raw_products = (
            response_json.get("productPages")
            or response_json.get("products")
            or response_json.get("data")
            or []
        )

    for product in raw_products:
        if not isinstance(product, dict):
            continue
        try:
            name = (
                product.get("name")
                or product.get("title")
                or ""
            ).strip()
            if not name:
                continue

            internal_id = str(product.get("retailerProductId") or "").strip() or None

            # Price: check multiple paths.
            # The real API returns {"amount": "3.80", "currency": "EUR"} — extract
            # the nested "amount" string before passing to to_cents.
            raw_price = (
                product.get("price")
                or (product.get("pricing") or {}).get("price")
                or (product.get("offer") or {}).get("price")
            )
            if isinstance(raw_price, dict):
                raw_price = raw_price.get("amount") or raw_price.get("value")
            price_cents = to_cents(raw_price)

            brand = product.get("brand") or None

            # Images: first URL
            images = product.get("images") or []
            image_url: str | None = None
            if images:
                first = images[0]
                if isinstance(first, str):
                    image_url = first or None
                elif isinstance(first, dict):
                    image_url = first.get("url") or first.get("src") or None

            is_promo = bool(product.get("isPromo") or product.get("promotion"))

            # Nutriscore → category field as hint
            nutriscore = product.get("nutriscore") or product.get("nutriScore")
            category: str | None = None
            if nutriscore:
                category = f"nutriscore:{nutriscore}"

            products.append(ProductResult(
                name=name,
                ean=None,  # Monoprix: no EAN at any level
                internal_id=internal_id,
                brand=str(brand) if brand else None,
                price_cents=price_cents,
                is_promo=is_promo,
                category=category,
                image_url=str(image_url) if image_url else None,
            ))
        except Exception as exc:
            logger.warning("monoprix.parse_rayon: skipping product — %s", exc)

    # Pagination via nextPageToken (v6: inside "metadata" dict)
    next_url: str | None = None
    metadata = response_json.get("metadata") or {}
    next_token = (
        metadata.get("nextPageToken")
        or response_json.get("nextPageToken")
        or response_json.get("next_page_token")
        or response_json.get("cursor")
    )
    if next_token:
        # The caller constructs the full URL; we return the token as next_url signal
        # Using a sentinel format that the runner can parse
        next_url = f"?pageToken={next_token}"

    # Total count
    total_count: int | None = None
    raw_total = (
        response_json.get("totalCount")
        or response_json.get("total_count")
        or response_json.get("total")
        or (response_json.get("meta") or {}).get("total")
    )
    if raw_total is not None:
        try:
            total_count = int(raw_total)
        except (TypeError, ValueError):
            pass

    logger.debug(
        "monoprix.parse_rayon: %d products, next_token=%s, total=%s",
        len(products),
        next_token,
        total_count,
    )
    return ParsedResult(products=products, next_url=next_url, total_count=total_count)
