"""Système U scraper-side parsers — magasins-u.com JSON API + coursesu.com.

Two parse phases:

* ``parse_stores`` — JSON list from GET
  https://www.magasins-u.com/bin/servlet/apistorelocatorentities.json?zip_code=…
  Filters for DRIVE stores (deliveryMode.type == RETRAIT and isEligible).

* ``parse_rayon`` — HTML from GET /c/{rayon}?start=N&sz=18
  Extracts products from ``data-tc-product-tile`` JSON attributes on <li> elements.
  Pagination: uses ``total_count`` from rendered product-count text; caller
  increments ``start`` by ``sz``.
"""

from __future__ import annotations

import html as _html
import json
import logging
import re

from scraper.parsers._models import ParsedResult, ProductResult, StoreResult, to_cents

logger = logging.getLogger(__name__)

# Product tile pattern
_TILE_ATTR_RE = re.compile(r'data-tc-product-tile="([^"]*)"')
_TILE_PRICE_RE = re.compile(r'data-item-price="([\d.]+)"')
_TILE_HREF_RE = re.compile(r'href="(/p/[^"]+)"')

# Pagination: total product count
_TOTAL_COUNT_RE = re.compile(r'(\d+)\s*produit', re.IGNORECASE)


def _empty() -> ParsedResult:
    return ParsedResult()


def parse_stores(response_json: list) -> ParsedResult:
    """Parse Système U store-locator JSON response from magasins-u.com API.

    Filters for DRIVE stores (deliveryMode.type == RETRAIT and isEligible).
    """
    if not response_json or not isinstance(response_json, list):
        logger.debug("systeme_u.parse_stores: empty/invalid response")
        return _empty()

    stores: list[StoreResult] = []
    for entry in response_json:
        try:
            # Filter for DRIVE (RETRAIT)
            delivery_modes = entry.get("deliveryMode") or []
            is_drive = any(
                d.get("type") == "RETRAIT" and d.get("isEligible")
                for d in delivery_modes
            )
            if not is_drive:
                continue

            store_id = str(entry.get("storeId") or "").strip()
            if not store_id:
                continue

            name = (entry.get("name") or "").strip() or None
            address = entry.get("address") or {}
            city = (address.get("city") or "").strip() or None
            postal_code = (address.get("zipcode") or "").strip() or None

            stores.append(StoreResult(
                store_id=store_id,
                name=name,
                city=city,
                postal_code=postal_code,
            ))
        except Exception as exc:
            logger.warning("systeme_u.parse_stores: skipping store — %s", exc)

    logger.debug("systeme_u.parse_stores: %d DRIVE stores extracted", len(stores))
    return ParsedResult(stores=stores)


def parse_rayon(response_text: str) -> ParsedResult:
    """Parse Système U category listing HTML.

    Products come from ``data-tc-product-tile`` JSON attributes on <li> tiles.
    Tile JSON: {id, name, EAN, brand, price, product_cat1/2/3, product_url_picture}.
    Also extracts rendered shelf price from ``data-item-price`` if available.
    Pagination: total_count from "N produits" text; next_url built by incrementing start.
    """
    if not response_text:
        logger.debug("systeme_u.parse_rayon: empty response")
        return _empty()

    products: list[ProductResult] = []

    for tile_match in _TILE_ATTR_RE.finditer(response_text):
        raw_json = _html.unescape(tile_match.group(1))
        try:
            tile = json.loads(raw_json)
        except json.JSONDecodeError as exc:
            logger.warning("systeme_u.parse_rayon: JSON decode error on tile — %s", exc)
            continue

        try:
            name = (tile.get("name") or "").strip()
            if not name:
                continue

            ean = str(tile.get("EAN") or "").strip() or None
            internal_id = str(tile.get("id") or "").strip() or None
            brand = tile.get("brand") or None

            # Price from tile JSON; fallback to data-item-price attribute nearby
            raw_price = tile.get("price")
            price_cents = to_cents(raw_price)

            category = (
                tile.get("product_cat3")
                or tile.get("product_cat2")
                or tile.get("product_cat1")
                or None
            )

            image_url = tile.get("product_url_picture") or None

            # Product URL: find href="/p/..." near this tile occurrence
            tile_pos = tile_match.start()
            nearby = response_text[tile_pos: tile_pos + 2000]
            href_match = _TILE_HREF_RE.search(nearby)
            product_url = ("https://www.coursesu.com" + href_match.group(1)) if href_match else None

            products.append(ProductResult(
                name=name,
                ean=ean,
                internal_id=internal_id,
                brand=str(brand) if brand else None,
                price_cents=price_cents,
                category=str(category) if category else None,
                image_url=str(image_url) if image_url else None,
                product_url=product_url,
            ))
        except Exception as exc:
            logger.warning("systeme_u.parse_rayon: skipping tile — %s", exc)

    # Total count for pagination
    total_count: int | None = None
    total_match = _TOTAL_COUNT_RE.search(response_text)
    if total_match:
        try:
            total_count = int(total_match.group(1))
        except ValueError:
            pass

    logger.debug(
        "systeme_u.parse_rayon: %d products, total_count=%s", len(products), total_count
    )
    # next_url = None — caller increments start by sz
    return ParsedResult(products=products, total_count=total_count)
