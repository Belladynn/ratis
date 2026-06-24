"""Carrefour scraper-side parsers — www.carrefour.fr.

Two parse phases:

* ``parse_stores`` — JSON from GET /api/eligibility/drive?latitude=…&longitude=…
  Paginated via links.next or meta.totalPages.  GPS not included in response.

* ``parse_rayon`` — HTML from GET /r/{slug}?page=N (Nuxt SSR).
  Products are SSR-rendered; EAN in ``data-testId`` attribute of <article>.
  Pagination: page_size=30; if count==30 assume more pages, increment page.
  ``total_count`` is unavailable from SSR (Vue state starts empty).
"""

from __future__ import annotations

import logging
import re

from scraper.parsers._models import ParsedResult, ProductResult, StoreResult, to_cents

logger = logging.getLogger(__name__)

# article with EAN in data-testId
_ARTICLE_RE = re.compile(
    r'<article[^>]*data-testId="(\d{8,15})"[^>]*>(.*?)</article>',
    re.DOTALL,
)
_H2H3_RE = re.compile(r'<h[23][^>]*>\s*(.*?)\s*</h[23]>', re.DOTALL)
_PRICE_RE = re.compile(r'(\d+)[,.](\d{2})\s*(?:<[^>]*>)?\s*€')
_PKG_RE = re.compile(r'class="[^"]*packaging[^"]*">\s*(.*?)\s*<', re.DOTALL)
_IMG_RE = re.compile(r'<img[^>]*src="(https://[^"]+carrefour[^"]+\.(?:jpg|jpeg|png|webp))"', re.IGNORECASE)
_URL_RE = re.compile(r'href="(/p/[^"]+)"')
_PAGE_SIZE = 30


def _empty() -> ParsedResult:
    return ParsedResult()


def parse_stores(response_json: dict) -> ParsedResult:
    """Parse Carrefour drive store list from /geoloc response.

    New endpoint (migrated from /api/eligibility/drive):
    - Response: {"data": {"stores": [...], "filters": {...}}, "meta": {...}}
    - GPS coordinates available in store.address.geoCoordinates
    - Pagination: if len(stores) == limit (50) → increment page
    """
    if not response_json:
        logger.debug("carrefour.parse_stores: empty response")
        return _empty()

    stores: list[StoreResult] = []
    # New /geoloc format: data is a dict with a "stores" key
    data_field = response_json.get("data") or {}
    if isinstance(data_field, dict):
        raw_stores = data_field.get("stores") or []
    else:
        # Fallback: old format returned data as a list directly
        raw_stores = data_field

    for store in raw_stores:
        if not isinstance(store, dict):
            continue
        try:
            store_id = str(store.get("ref") or store.get("id") or "").strip()
            if not store_id:
                continue

            # GPS from address.geoCoordinates (new endpoint only)
            address = store.get("address") or {}
            geo = address.get("geoCoordinates") or {}
            lat: float | None = None
            lng: float | None = None
            try:
                if geo.get("latitude") is not None:
                    lat = float(geo["latitude"])
                if geo.get("longitude") is not None:
                    lng = float(geo["longitude"])
            except (TypeError, ValueError):
                pass

            stores.append(StoreResult(
                store_id=store_id,
                name=store.get("name") or None,
                city=address.get("city") or None,
                postal_code=str(address.get("postalCode") or "").strip() or None,
                lat=lat,
                lng=lng,
            ))
        except Exception as exc:
            logger.warning("carrefour.parse_stores: skipping store — %s", exc)

    # Pagination: /geoloc returns up to `limit` (50) stores per page.
    # Signal next page when we got a full page — runner increments `page` param.
    next_url: str | None = None
    _LIMIT = 50
    if len(raw_stores) >= _LIMIT:
        next_url = "?page=next"  # sentinel handled by _resolve_next_url in runner

    logger.debug("carrefour.parse_stores: %d stores extracted", len(stores))
    return ParsedResult(stores=stores, next_url=next_url)


def parse_rayon(response_text: str) -> ParsedResult:
    """Parse Carrefour product listing from /r/{slug}?page=N (Nuxt SSR HTML).

    Products are server-rendered into <article data-testId="{EAN}"> elements.
    EAN is available at rayon level; no fiche_jobs needed.
    Pagination: page_size is 30; caller should increment page while
    len(products) == 30, since totalPage is unavailable in the SSR state.
    """
    if not response_text:
        logger.debug("carrefour.parse_rayon: empty response")
        return _empty()

    products: list[ProductResult] = []
    for ean_candidate, content in _ARTICLE_RE.findall(response_text):
        try:
            # EAN validation: 13 digits (may also be 8 for EAN-8, but Carrefour uses 13)
            ean = ean_candidate if len(ean_candidate) == 13 else None
            internal_id = ean_candidate if not ean else None

            # Name from first h2/h3 inside the article
            name_m = _H2H3_RE.search(content)
            if not name_m:
                continue
            name = re.sub(r'<[^>]+>', '', name_m.group(1)).strip()
            if not name:
                continue

            # Price: "X,XX €" or "X.XX €"
            price_m = _PRICE_RE.search(content)
            price_cents = to_cents(f"{price_m.group(1)}.{price_m.group(2)}") if price_m else None

            # Packaging
            pkg_m = _PKG_RE.search(content)
            quantity = pkg_m.group(1).strip() if pkg_m else None

            # Image
            img_m = _IMG_RE.search(content)
            image_url = img_m.group(1) if img_m else None

            # Product URL
            url_m = _URL_RE.search(content)
            product_url = f"https://www.carrefour.fr{url_m.group(1)}" if url_m else None

            products.append(ProductResult(
                name=name,
                ean=ean,
                internal_id=internal_id,
                quantity=quantity,
                price_cents=price_cents,
                image_url=image_url,
                product_url=product_url,
            ))
        except Exception as exc:
            logger.warning("carrefour.parse_rayon: skipping product — %s", exc)

    # Pagination: if we got a full page, signal there may be more
    next_url: str | None = None
    if len(products) == _PAGE_SIZE:
        # Sentinel: runner increments page number using current URL
        next_url = "?page=next"

    logger.debug("carrefour.parse_rayon: %d products extracted", len(products))
    return ParsedResult(products=products, next_url=next_url)
