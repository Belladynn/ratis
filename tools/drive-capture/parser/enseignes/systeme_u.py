"""Système U (coursesu.com) drive parser.

coursesu.com runs on Salesforce Commerce Cloud (Demandware): server-rendered
HTML with structured product data in several shapes:

* category / shelf-listing pages render one ``<li data-tc-product-tile="{...}">``
  per product, the attribute holding a full JSON object (``id``, ``name``,
  ``EAN``, ``brand``, ``price``, category breadcrumb, picture);
* the same tile's inner ``product-tile`` div carries the live shelf price in a
  ``data-item-price`` attribute plus a ``unit-info`` per-measure price;
* product-detail pages additionally embed a JSON-LD ``schema.org/Product``
  block (``sku`` = EAN, ``brand``, ``name``) — used as a fallback source.

The selected drive store is exposed once via the inline ``dataLayerNC`` object
(``store_id`` / ``store_name``); we stamp every observation with it.

For the national store directory, ``parse_stores`` reads a **sibling** capture
file ``www.magasins-u.com.ndjson`` in the same session folder. The record
whose URL ends with ``/annuaire-magasin`` carries the full HTML listing of
~1389 stores (Hyper U / Super U / U Express / Utile) grouped by department.
``store_ref`` = the URL slug (last segment after ``/magasin/``). Postal codes
and GPS coordinates are not present in the annuaire.
"""

from __future__ import annotations

import html as _html
import json
import logging
import re
from collections.abc import Iterator
from pathlib import Path

from parser.capture import iter_records
from parser.enseignes import _schemaorg as so
from parser.model import ParsedProduct, ParsedStore
from parser.pricing import to_cents

logger = logging.getLogger(__name__)

ENSEIGNE = "systeme_u"

# Sibling capture file holding the national store directory (annuaire-magasin).
_STORES_FILENAME = "www.magasins-u.com.ndjson"

# URL suffix identifying the national directory page in the sibling capture.
_ANNUAIRE_URL_SUFFIX = "/annuaire-magasin"

# Known store-format prefixes used in store names (longest first for safe matching).
_FORMAT_PREFIXES = ("Hyper U", "Super U", "U Express", "Utile")

# Matches every <a class="u-list-magasin__link" href="..."> element.
_STORE_LINK_RE = re.compile(
    r'<a\s+class="u-list-magasin__link"\s+href="([^"]+)"[^>]*>'
    r'.*?<span\s+class="title">([^<]+)</span>',
    re.DOTALL,
)

# one shelf tile: <li ... data-tc-product-tile="<escaped JSON>" ...> ... </li>
_TILE_SPLIT_RE = re.compile(r"(?=<li [^>]*data-tc-product-tile=)")
_TC_ATTR_RE = re.compile(r'data-tc-product-tile="([^"]*)"')
_ITEM_PRICE_RE = re.compile(r'data-item-price="([\d.]+)"')
_UNIT_INFO_RE = re.compile(r'class="unit-info[^"]*"[^>]*>([^<]+)<')
_HREF_RE = re.compile(r'href="(/p/[^"]+)"')


def _store_from_datalayer(html: str) -> ParsedStore | None:
    """Build a ``ParsedStore`` from a page's inline ``dataLayerNC`` object."""
    data = so.extract_json_assignment(html, "dataLayerNC")
    if not data:
        return None
    ref = data.get("store_id")
    if not ref:
        return None
    name = data.get("store_name")
    return ParsedStore(
        enseigne=ENSEIGNE,
        store_ref=str(ref),
        name=so.unescape(name) if isinstance(name, str) else None,
    )


def _active_store_ref(ndjson_path: str) -> str | None:
    """The selected drive store id, read from pages' ``dataLayerNC``.

    Before a store is chosen the home page carries a non-numeric placeholder
    (``seo-store``); once a drive is selected ``store_id`` is the numeric
    store reference. We keep the last numeric id seen — store selection
    happens part-way through a session.
    """
    ref: str | None = None
    for record in iter_records(ndjson_path):
        html = record.get("response_text")
        if not html or "dataLayerNC" not in html:
            continue
        store = _store_from_datalayer(html)
        if store is not None and store.store_ref.isdigit():
            ref = store.store_ref
    return ref


def _measure_unit(unit_info: str | None) -> str | None:
    """Extract the measure unit (``kg``, ``l``, ...) from a ``19,88 €/kg`` text."""
    if not unit_info:
        return None
    match = re.search(r"/\s*([a-zA-Z]+)", unit_info)
    return match.group(1).lower() if match else None


def _parse_tile(chunk: str, captured_at: str, store_ref: str | None) -> ParsedProduct | None:
    """Build a ``ParsedProduct`` from one ``data-tc-product-tile`` shelf tile.

    The tile JSON is the canonical metadata source; the live shelf price and
    per-measure price are read from the rendered ``data-item-price`` /
    ``unit-info`` markup (the tile JSON ``price`` is the catalogue price and
    usually matches, but the rendered value is store-accurate).
    """
    attr = _TC_ATTR_RE.search(chunk)
    if not attr:
        return None
    try:
        tile = json.loads(_html.unescape(attr.group(1)))
    except json.JSONDecodeError:
        return None
    name = so.unescape(tile.get("name"))
    if not name:
        return None

    rendered_price = _ITEM_PRICE_RE.search(chunk)
    price_cents = to_cents(rendered_price.group(1)) if rendered_price else None
    if price_cents is None:
        price_cents = to_cents(tile.get("price"))

    unit_match = None
    for candidate in _UNIT_INFO_RE.finditer(chunk):
        text = so.unescape(candidate.group(1))
        if text:
            unit_match = text
            break

    href = _HREF_RE.search(chunk)
    discount = tile.get("discount")
    is_promo = bool(discount) and str(discount).strip() not in ("", "0")

    return ParsedProduct(
        enseigne=ENSEIGNE,
        name=name,
        captured_at=captured_at,
        store_ref=store_ref,
        ean=str(tile["EAN"]) if tile.get("EAN") else None,
        brand=so.unescape(tile.get("brand")),
        category=so.unescape(tile.get("product_cat3"))
        or so.unescape(tile.get("product_cat2")),
        price_cents=price_cents,
        price_per_measure_cents=to_cents(unit_match),
        measure_unit=_measure_unit(unit_match),
        is_promo=is_promo,
        product_url="https://www.coursesu.com" + so.unescape(href.group(1))
        if href
        else None,
        image_url=so.unescape(tile.get("product_url_picture")),
        enseigne_product_id=str(tile["id"]) if tile.get("id") else None,
    )


def _parse_ld_product(html: str, url: str, captured_at: str, store_ref: str | None) -> ParsedProduct | None:
    """Build a ``ParsedProduct`` from a detail page's JSON-LD ``Product`` block.

    Used as a fallback for product-detail pages that ship no shelf tile —
    the JSON-LD carries ``sku`` (EAN), ``name`` and ``brand`` but no price.
    """
    for obj in so.iter_ld_json(html):
        if obj.get("@type") != "Product":
            continue
        name = so.unescape(obj.get("name"))
        if not name:
            continue
        brand = obj.get("brand")
        if isinstance(brand, dict):
            brand = brand.get("name")
        image = obj.get("image")
        if isinstance(image, list):
            image = image[0] if image else None
        return ParsedProduct(
            enseigne=ENSEIGNE,
            name=name,
            captured_at=captured_at,
            store_ref=store_ref,
            ean=str(obj["sku"]) if obj.get("sku") else None,
            brand=so.unescape(brand) if isinstance(brand, str) else None,
            product_url=url or (obj.get("@id") if isinstance(obj.get("@id"), str) else None),
            image_url=image if isinstance(image, str) else None,
            enseigne_product_id=str(obj["mpn"]) if obj.get("mpn") else None,
        )
    return None


def parse_products(ndjson_path: str) -> Iterator[ParsedProduct]:
    """Yield every product observation in a Système U capture file.

    Shelf tiles are the primary source; JSON-LD detail pages backfill any
    product not seen as a tile. De-duplicated on
    ``(enseigne_product_id or ean, store_ref, price_cents)``.
    """
    store_ref = _active_store_ref(ndjson_path)
    if store_ref:
        logger.info("systeme_u: store actif = %s", store_ref)

    by_id: dict[str, ParsedProduct] = {}
    ld_fallback: dict[str, ParsedProduct] = {}
    n_tiles = 0
    n_with_ean = 0
    n_ignored_no_name = 0

    for record in iter_records(ndjson_path):
        html = record.get("response_text")
        if not html:
            continue
        captured_at = record.get("captured_at") or ""
        url = record.get("url") or ""

        for chunk in _TILE_SPLIT_RE.split(html)[1:]:
            n_tiles += 1
            product = _parse_tile(chunk, captured_at, store_ref)
            if product is None:
                n_ignored_no_name += 1
                continue
            if product.ean:
                n_with_ean += 1
            key = product.enseigne_product_id or product.ean
            if key and key not in by_id:
                by_id[key] = product

        if "/p/" in url and "application/ld+json" in html:
            ld = _parse_ld_product(html, url, captured_at, store_ref)
            if ld is not None:
                key = ld.enseigne_product_id or ld.ean
                if key:
                    ld_fallback[key] = ld

    # backfill: keep JSON-LD products only if no tile already covered them
    for key, ld in ld_fallback.items():
        by_id.setdefault(key, ld)

    seen: set[tuple] = set()
    n_emitted = 0
    n_ignored_dup = 0
    for product in by_id.values():
        dedup = (
            product.enseigne_product_id or product.ean,
            product.store_ref,
            product.price_cents,
        )
        if dedup in seen:
            n_ignored_dup += 1
            continue
        seen.add(dedup)
        n_emitted += 1
        yield product

    logger.info(
        "systeme_u: %d records produit trouvés (%d avec EAN), "
        "%d ParsedProduct extraits, %d ignorés "
        "(%d sans nom, %d doublons)",
        n_tiles + len(ld_fallback),
        n_with_ean,
        n_emitted,
        n_ignored_no_name + n_ignored_dup,
        n_ignored_no_name,
        n_ignored_dup,
    )


def _city_from_name(name: str) -> str | None:
    """Extract the city from a store name by stripping the known format prefix.

    The annuaire name is ``"<Format> <CITY IN CAPS>"``.  We strip one of the
    known format prefixes (``Hyper U``, ``Super U``, ``U Express``, ``Utile``)
    and title-case the remainder.  Returns ``None`` when no prefix matches so
    we never produce a misleading city value.

    Examples::

        "Hyper U ABBEVILLE "  -> "Abbeville"
        "Super U LAIZ"        -> "Laiz"
        "U Express PARIS 15"  -> "Paris 15"
        "Marché U LYON"       -> None  (unknown format)
    """
    for prefix in _FORMAT_PREFIXES:
        if name.startswith(prefix):
            remainder = name[len(prefix):].strip()
            return remainder.title() if remainder else None
    return None


def _store_from_annuaire_link(href: str, raw_title: str) -> ParsedStore | None:
    """Build a ``ParsedStore`` from one ``u-list-magasin__link`` anchor.

    ``href`` is the full URL (e.g. ``https://www.magasins-u.com/magasin/hyperu-abbeville``);
    the ``store_ref`` is its last path segment (``hyperu-abbeville``).
    ``raw_title`` is the raw text of the ``<span class="title">`` — may have a
    trailing space and HTML entities.
    """
    # Extract slug: last segment after /magasin/
    slug_match = re.search(r"/magasin/([^/?#]+)$", href)
    if not slug_match:
        return None
    store_ref = slug_match.group(1)

    name = _html.unescape(raw_title).strip()
    if not name:
        return None

    city = _city_from_name(name)

    return ParsedStore(
        enseigne=ENSEIGNE,
        store_ref=store_ref,
        name=name,
        city=city,
        postal_code=None,
        lat=None,
        lng=None,
    )


def parse_stores(ndjson_path: str) -> Iterator[ParsedStore]:
    """Yield Système U stores from the national annuaire.

    The store directory lives in a **sibling** capture file
    (``www.magasins-u.com.ndjson``) in the same session folder as
    ``ndjson_path``.  The record whose URL ends with ``/annuaire-magasin``
    contains the full HTML listing (~1389 stores: Hyper U, Super U, U Express,
    Utile) grouped by department.

    ``store_ref`` = URL slug (last segment after ``/magasin/``).
    ``postal_code``, ``lat`` and ``lng`` are always ``None`` — the annuaire
    carries no postal code per store and no GPS coordinates.

    If the sibling file is absent we log a warning and yield nothing rather
    than failing the whole run.
    """
    stores_path = Path(ndjson_path).resolve().parent / _STORES_FILENAME
    if not stores_path.exists():
        logger.warning(
            "systeme_u: fichier annuaire introuvable (%s) — 0 magasin extrait",
            stores_path,
        )
        return

    seen: set[str] = set()
    n_emitted = 0
    found_page = False

    for record in iter_records(str(stores_path)):
        url = record.get("url") or ""
        if not url.endswith(_ANNUAIRE_URL_SUFFIX):
            continue
        html = record.get("response_text") or ""
        if not html:
            continue
        found_page = True

        for match in _STORE_LINK_RE.finditer(html):
            href = match.group(1)
            raw_title = match.group(2)
            store = _store_from_annuaire_link(href, raw_title)
            if store is None or store.store_ref in seen:
                continue
            seen.add(store.store_ref)
            n_emitted += 1
            yield store

        # The annuaire is a single page — stop after the first matching record.
        break

    if not found_page:
        logger.warning(
            "systeme_u: enregistrement /annuaire-magasin absent de %s — 0 magasin extrait",
            stores_path,
        )
        return

    logger.info("systeme_u: %d magasin(s) extraits depuis l'annuaire national", n_emitted)
