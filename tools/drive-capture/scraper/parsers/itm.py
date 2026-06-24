"""ITM (Intermarché) scraper-side parsers — www.intermarche.com.

Three parse phases:

* ``parse_stores`` — JSON from GET /api/service/pdvs/v4/pdvs/zone?lat=…&lon=…
  Returns stores where "DRIVE" is in typeLivraisonOuvert.

* ``parse_rayon`` — HTML/RSC from GET /rayons/{path}
  Reconstructs Next.js RSC stream, extracts the products[] array.
  Enqueues fiche_jobs for the per-product JSON endpoint.

* ``parse_fiche`` — JSON from GET /api/service/produits/v3/pdvs/{ref}/produits/{EAN}
  Returns a single enriched ProductResult.
"""

from __future__ import annotations

import json
import logging
import re

from scraper.parsers._models import ParsedResult, ProductResult, StoreResult, to_cents

logger = logging.getLogger(__name__)

# Regex to extract RSC push chunks: self.__next_f.push([n, "<chunk>"])
_RSC_PUSH_RE = re.compile(r"self\.__next_f\.push\(\[(.*?)\]\)", re.DOTALL)

# EAN-13: exactly 13 digits
_EAN13_RE = re.compile(r"^\d{13}$")

# next page href in rendered HTML
_NEXT_PAGE_RE = re.compile(r'href="(/rayons/[^"]+\?page=(\d+))"')


def _empty() -> ParsedResult:
    return ParsedResult()


def parse_stores(response_json: dict) -> ParsedResult:
    """Parse ITM store list from /api/service/pdvs/v4/pdvs/zone response.

    Filters to DRIVE stores only.
    """
    if not response_json:
        logger.debug("itm.parse_stores: empty response")
        return _empty()

    stores: list[StoreResult] = []
    resultats = response_json.get("resultats") or []
    for store in resultats:
        try:
            ecommerce = store.get("ecommerce") or {}
            types_ouverts = ecommerce.get("typeLivraisonOuvert") or []
            if "DRIVE" not in types_ouverts:
                continue

            addresses = store.get("addresses") or []
            addr = addresses[0] if addresses else {}

            lat: float | None = None
            lng: float | None = None
            try:
                lat = float(addr.get("latitude") or 0) or None
                lng = float(addr.get("longitude") or 0) or None
            except (TypeError, ValueError):
                pass

            trade = (store.get("tradeNameLabel") or "").strip()
            model = (store.get("modelLabel") or "").strip()
            name = f"{trade} {model}".strip() or None

            stores.append(StoreResult(
                store_id=str(store.get("entityCode", "")),
                name=name,
                city=addr.get("townLabel") or None,
                postal_code=str(addr.get("postCode") or "") or None,
                lat=lat,
                lng=lng,
            ))
        except Exception as exc:
            logger.warning("itm.parse_stores: skipping store — %s", exc)

    logger.debug("itm.parse_stores: %d DRIVE stores extracted", len(stores))
    return ParsedResult(stores=stores)


def _reconstruct_rsc(html: str) -> str:
    """Concatenate RSC text chunks from self.__next_f.push calls."""
    chunks: list[str] = []
    for match in _RSC_PUSH_RE.finditer(html):
        body = match.group(1)
        try:
            arr = json.loads("[" + body + "]")
        except json.JSONDecodeError:
            continue
        if len(arr) >= 2 and isinstance(arr[1], str):
            chunks.append(arr[1])
    return "".join(chunks)


def _extract_products_array(rsc_text: str) -> list:
    """Find the products[] array inside the RSC text near a /rayons/ path marker."""
    # Look for "path":"/rayons/ within 400 chars before "products":[
    search_start = 0
    while True:
        rayon_pos = rsc_text.find('"path":"/rayons/', search_start)
        if rayon_pos == -1:
            break

        # Look for "products":[ within 400 chars after the path marker
        window_end = rayon_pos + 400
        products_pos = rsc_text.find('"products":[', rayon_pos, window_end)
        if products_pos == -1:
            search_start = rayon_pos + 1
            continue

        # Find start of array
        arr_start = rsc_text.index('[', products_pos + len('"products":'))
        # Balanced brace scan to find the end
        depth = 0
        i = arr_start
        while i < len(rsc_text):
            c = rsc_text[i]
            if c == '[':
                depth += 1
            elif c == ']':
                depth -= 1
                if depth == 0:
                    arr_text = rsc_text[arr_start:i + 1]
                    try:
                        return json.loads(arr_text)
                    except json.JSONDecodeError as exc:
                        logger.warning("itm._extract_products_array: JSON decode error — %s", exc)
                        return []
            i += 1
        search_start = rayon_pos + 1

    return []


def _parse_product_obj(obj: dict) -> ProductResult | None:
    """Build a ProductResult from one ITM RSC product object."""
    try:
        info = obj.get("informations") or {}
        name = (info.get("title") or "").strip()
        if not name or name.startswith("$"):
            return None

        url = obj.get("url") or ""
        # EAN = last path segment if 13 digits
        ean: str | None = None
        segments = url.rstrip("/").split("/")
        if segments:
            last = segments[-1]
            if _EAN13_RE.match(last):
                ean = last

        prices = obj.get("prices") or {}
        product_price = prices.get("productPrice") or {}
        price_cents = to_cents(product_price.get("value"))

        is_promo = bool(obj.get("hasReduction"))
        promo_price_cents: int | None = None
        if is_promo:
            crossed_obj = prices.get("crossedOutPrice") or {}
            crossed_val = crossed_obj.get("value")
            crossed_cents = to_cents(crossed_val)
            if crossed_cents is not None and price_cents is not None and crossed_cents > price_cents:
                promo_price_cents = price_cents

        product_url: str | None = None
        if url.startswith("/"):
            product_url = "https://www.intermarche.com" + url

        all_images = info.get("allImages") or []
        image_url: str | None = None
        if all_images and isinstance(all_images[0], dict):
            image_url = all_images[0].get("src") or None
        elif all_images and isinstance(all_images[0], str):
            image_url = all_images[0] or None

        internal_id = str(obj["id"]) if obj.get("id") is not None else None

        return ProductResult(
            name=name,
            ean=ean,
            internal_id=internal_id,
            brand=info.get("brand") or None,
            quantity=info.get("packaging") or None,
            price_cents=price_cents,
            promo_price_cents=promo_price_cents,
            is_promo=is_promo,
            image_url=image_url,
            product_url=product_url,
        )
    except Exception as exc:
        logger.warning("itm._parse_product_obj: skipping product — %s", exc)
        return None


def parse_rayon(response_text: str) -> ParsedResult:
    """Parse ITM category listing page (Next.js RSC stream).

    Reconstructs the RSC chunks, finds the products[] array, and extracts
    ProductResults.  Pagination: looks for ?page=N href in the raw HTML.
    """
    if not response_text:
        logger.debug("itm.parse_rayon: empty response")
        return _empty()

    rsc_text = _reconstruct_rsc(response_text)
    if not rsc_text:
        logger.debug("itm.parse_rayon: no RSC chunks found")
        return _empty()

    raw_products = _extract_products_array(rsc_text)
    products: list[ProductResult] = []
    for obj in raw_products:
        if not isinstance(obj, dict):
            continue
        p = _parse_product_obj(obj)
        if p is not None:
            products.append(p)

    # Pagination: look for ?page=N href in the raw HTML
    next_url: str | None = None
    if products:
        next_matches = _NEXT_PAGE_RE.findall(response_text)
        if next_matches:
            # Take the highest page number href
            best = max(next_matches, key=lambda m: int(m[1]))
            next_url = "https://www.intermarche.com" + best[0]

    logger.debug("itm.parse_rayon: %d products extracted", len(products))
    return ParsedResult(products=products, next_url=next_url)


def parse_fiche(response_json: dict, store_id: str) -> ParsedResult:
    """Parse ITM product-detail JSON from /api/service/produits/v3/…

    Extracts a single enriched ProductResult.
    """
    if not response_json:
        logger.debug("itm.parse_fiche: empty response")
        return _empty()

    try:
        categories = response_json.get("categories") or {}
        global_cat = categories.get("global") or {}
        prix_cat = categories.get("prix") or {}

        ean = (
            str(global_cat.get("ean") or global_cat.get("productEan13") or "")
            or None
        )
        name = str(global_cat.get("libelle") or "").strip()
        if not name:
            logger.warning("itm.parse_fiche: product has no libelle")
            return _empty()

        images = global_cat.get("images") or []
        image_url: str | None = images[0] if images and isinstance(images[0], str) else None

        product = ProductResult(
            name=name,
            ean=ean,
            brand=global_cat.get("marque") or None,
            quantity=global_cat.get("conditionnement") or None,
            price_cents=to_cents(prix_cat.get("prix")),
            category=global_cat.get("description") or None,
            image_url=image_url,
        )
        return ParsedResult(products=[product])
    except Exception as exc:
        logger.warning("itm.parse_fiche: failed — %s", exc)
        return _empty()
