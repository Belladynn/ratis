"""Casino-group drive parser — www.mescoursesdeproximite.com.

The Casino group (Le Petit Casino, Spar, Vival, Casino Shop, Casino Hypermarché,
Géant Casino…) runs its click-and-collect / drive service on
``www.mescoursesdeproximite.com``. The site is server-side rendered and
embeds structured data as schema.org JSON-LD.

**Product pages** (``/produit/<slug>/<store_code>/<product_id>``) carry **two**
``<script type="application/ld+json">`` blocks:

* ``@type: Product`` — name, gtin13 (EAN), brand, category, weight, offers
  (price, availability, seller); the ``@id`` encodes ``/<store_code>/<id>#product``.
* ``@type: LocalBusiness`` — store name and full postal address + geo coords;
  the ``@id`` encodes ``/<store_code>#store``.

**Category / rayon pages** (``/famille/…``) carry only a ``LocalBusiness``
block (no product listings in ld+json — the shelf tiles are plain HTML and
carry no structured EAN/price data). These pages are therefore skipped for
product extraction. This is a known limitation: only explicit product-detail
pages contribute observations.

**Store pages** (``/courses-en-ligne/<slug>/<store_code>``) carry a
``LocalBusiness`` block as well — address + geo without a product context.

``store_ref`` is extracted from the ``@id`` field of the ``LocalBusiness``
block (pattern ``.../C1507#store`` → ``C1507``); the Product ``@id`` (pattern
``.../C1507/174353#product``) is used as the canonical source for both
``store_ref`` and ``enseigne_product_id``. The ``sku`` field (``C1507174353``)
is a concatenation of store code + product id but its split point is
ambiguous without knowing the store code length — the ``@id`` is unambiguous
and therefore preferred.

``quantity`` is intentionally left ``None``: the weight is in
``weight.value`` (kg) but the human-readable quantity (e.g. ``2x130g``) is
embedded in the product name, not in a separate structured field. Parsing
the name to reconstruct a quantity string would be fragile and is out of
scope. The weight field (a ``QuantitativeValue`` in kg) could be mapped to a
``quantity`` string if a future pass requires it.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterator

from parser.capture import iter_records
from parser.enseignes import _schemaorg as so
from parser.model import ParsedProduct, ParsedStore
from parser.pricing import to_cents

logger = logging.getLogger(__name__)

ENSEIGNE = "casino"
HOST = "www.mescoursesdeproximite.com"

# /…/C1507/174353#product  →  store_ref=C1507, product_id=174353
_PRODUCT_ID_RE = re.compile(r"/([A-Z]\d+)/(\d+)#product$")
# /…/C1507#store           →  store_ref=C1507
_STORE_REF_RE = re.compile(r"/([A-Z]\d+)#store$")


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def _store_ref_from_product_id(at_id: str | None) -> str | None:
    """Store code from a Product ``@id`` (e.g. ``.../C1507/174353#product``)."""
    if not at_id:
        return None
    m = _PRODUCT_ID_RE.search(at_id)
    return m.group(1) if m else None


def _product_id_from_at_id(at_id: str | None) -> str | None:
    """Product id from a Product ``@id`` (e.g. ``174353``)."""
    if not at_id:
        return None
    m = _PRODUCT_ID_RE.search(at_id)
    return m.group(2) if m else None


def _store_ref_from_lb_id(at_id: str | None) -> str | None:
    """Store code from a LocalBusiness ``@id`` (e.g. ``.../C1507#store``)."""
    if not at_id:
        return None
    m = _STORE_REF_RE.search(at_id)
    return m.group(1) if m else None


# --------------------------------------------------------------------------
# product extraction (schema.org Product ld+json from /produit/ pages)
# --------------------------------------------------------------------------

def _parse_product(obj: dict, captured_at: str) -> ParsedProduct | None:
    """Build a ``ParsedProduct`` from a schema.org ``Product`` ld+json object.

    Returns ``None`` when the minimum required fields (name or EAN) are
    missing.
    """
    name = so.unescape(obj.get("name"))
    if not name:
        return None

    at_id = obj.get("@id") if isinstance(obj.get("@id"), str) else None
    store_ref = _store_ref_from_product_id(at_id)
    enseigne_product_id = _product_id_from_at_id(at_id)

    ean = obj.get("gtin13") or None
    if isinstance(ean, str) and not ean:
        ean = None

    brand_raw = obj.get("brand")
    brand: str | None = None
    if isinstance(brand_raw, dict):
        brand = so.unescape(brand_raw.get("name"))
    elif isinstance(brand_raw, str):
        brand = so.unescape(brand_raw)

    category = so.unescape(obj.get("category")) if isinstance(obj.get("category"), str) else None

    image_raw = obj.get("image")
    image_url: str | None = None
    if isinstance(image_raw, list):
        image_url = image_raw[0] if image_raw and isinstance(image_raw[0], str) else None
    elif isinstance(image_raw, str):
        image_url = image_raw or None

    offers = obj.get("offers")
    price_cents: int | None = None
    available: bool | None = None
    product_url: str | None = None

    if isinstance(offers, dict):
        product_url = offers.get("url") if isinstance(offers.get("url"), str) else None
        price_cents = to_cents(offers.get("price"))

        availability = offers.get("availability")
        if isinstance(availability, dict):
            avail_id = availability.get("@id") or ""
            available = avail_id.endswith("InStock")
        elif isinstance(availability, str):
            available = "InStock" in availability

    return ParsedProduct(
        enseigne=ENSEIGNE,
        name=name,
        captured_at=captured_at,
        store_ref=store_ref,
        ean=ean,
        brand=brand,
        quantity=None,  # see module docstring — fragile to parse from name
        category=category,
        price_cents=price_cents,
        product_url=product_url,
        image_url=image_url,
        available=available,
        enseigne_product_id=enseigne_product_id,
    )


def parse_products(ndjson_path: str) -> Iterator[ParsedProduct]:
    """Yield every product observation in a mescoursesdeproximite.com capture.

    Only pages whose URL path starts with ``/produit/`` are parsed — the
    ``@type: Product`` ld+json block is exclusively present on product-detail
    pages. Category / rayon pages (``/famille/``) expose only a
    ``LocalBusiness`` block and are skipped. De-duplicated on
    ``(enseigne_product_id, store_ref, price_cents)``.
    """
    seen: set[tuple] = set()
    n_records = 0
    n_with_ean = 0
    n_emitted = 0
    n_ignored_no_name = 0
    n_ignored_dup = 0

    for record in iter_records(ndjson_path):
        if record.get("host") != HOST:
            continue
        url = record.get("url") or ""
        html = record.get("response_text")
        if not html:
            continue
        # only product-detail pages carry the Product block
        if "/produit/" not in url:
            continue

        for obj in so.iter_ld_json(html):
            if obj.get("@type") != "Product":
                continue
            n_records += 1
            product = _parse_product(obj, record.get("captured_at") or "")
            if product is None:
                n_ignored_no_name += 1
                continue
            key = (product.enseigne_product_id, product.store_ref, product.price_cents)
            if key in seen:
                n_ignored_dup += 1
                continue
            seen.add(key)
            n_emitted += 1
            if product.ean:
                n_with_ean += 1
            yield product

    logger.info(
        "casino: %d record(s) produit trouvés (%d avec EAN), "
        "%d ParsedProduct extraits, %d ignorés "
        "(%d sans nom, %d doublons)",
        n_records,
        n_with_ean,
        n_emitted,
        n_ignored_no_name + n_ignored_dup,
        n_ignored_no_name,
        n_ignored_dup,
    )


# --------------------------------------------------------------------------
# store extraction (schema.org LocalBusiness ld+json — any page)
# --------------------------------------------------------------------------

def _parse_store(obj: dict) -> ParsedStore | None:
    """Build a ``ParsedStore`` from a schema.org ``LocalBusiness`` ld+json object.

    Returns ``None`` when no store code can be extracted from the ``@id``.
    """
    at_id = obj.get("@id") if isinstance(obj.get("@id"), str) else None
    store_ref = _store_ref_from_lb_id(at_id)
    if not store_ref:
        return None

    name = so.unescape(obj.get("name"))

    address = obj.get("address") or {}
    city: str | None = None
    postal_code: str | None = None
    if isinstance(address, dict):
        city = so.unescape(address.get("addressLocality"))
        postal_code = address.get("postalCode") or None
        if isinstance(postal_code, str) and not postal_code:
            postal_code = None

    geo = obj.get("geo") or {}
    lat: float | None = None
    lng: float | None = None
    if isinstance(geo, dict):
        lat_raw = geo.get("latitude")
        lng_raw = geo.get("longitude")
        lat = float(lat_raw) if isinstance(lat_raw, (int, float)) else None
        lng = float(lng_raw) if isinstance(lng_raw, (int, float)) else None

    return ParsedStore(
        enseigne=ENSEIGNE,
        store_ref=store_ref,
        name=name,
        city=city,
        postal_code=postal_code,
        lat=lat,
        lng=lng,
    )


def parse_stores(ndjson_path: str) -> Iterator[ParsedStore]:
    """Yield drive stores from a mescoursesdeproximite.com capture.

    Every HTML page (product detail, category listing, store landing page)
    embeds a ``@type: LocalBusiness`` ld+json block for the active store.
    We emit each unique store code once — de-duplicated on ``store_ref``.
    """
    seen: set[str] = set()
    n_emitted = 0

    for record in iter_records(ndjson_path):
        if record.get("host") != HOST:
            continue
        html = record.get("response_text")
        if not html:
            continue

        for obj in so.iter_ld_json(html):
            if obj.get("@type") != "LocalBusiness":
                continue
            store = _parse_store(obj)
            if store is None or store.store_ref in seen:
                continue
            seen.add(store.store_ref)
            n_emitted += 1
            yield store

    logger.info("casino: %d magasin(s) drive extraits", n_emitted)
