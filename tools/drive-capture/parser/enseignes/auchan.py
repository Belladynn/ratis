"""Auchan drive parser.

Auchan's site is classic server-side-rendered HTML. Structured product data
appears as schema.org **microdata** (``itemscope``/``itemprop`` attributes):

* category listing pages (``/.../ca-n0201``, ``/search-infinite?...``) embed
  one ``schema.org/Product`` ``<article>`` per shelf tile, each with a nested
  ``schema.org/Offer`` carrying ``price`` / ``availability``;
* the product-detail page (``/.../pr-C<id>``) repeats the same microdata and
  additionally exposes the **EAN** in a ``Réf / EAN : <ref> / <ean13>`` block.

Tiles do not carry the EAN, so we join tiles and detail pages on the Auchan
product id (the ``pr-C<id>`` URL segment): a detail page enriches its matching
tile with the EAN, per-measure price and rayon breadcrumb.

The basket store is not on the product markup — it is set once via the
``/journey`` API. We read the active ``GROCERY`` context's
``seller.storeReference.id`` and stamp every observation with it.

For stores, two sources are combined:

1. The active ``GROCERY`` context from ``/journey`` (one store, with GPS
   coordinates).
2. The national directory pages ``/nos-magasins?types=<TYPE>`` (6 types:
   HYPER, SUPER, DRIVE, PICKUP_POINT, LOCKERS, PROXY — ~961 stores in total).
   These pages carry no GPS coordinates; ``lat``/``lng`` are left ``None``
   (geocoding from the text address is possible but out of scope here).

   **Double-escaping note**: the HTML in the NDJSON ``response_text`` field
   is HTML-escaped twice (``&amp;#xE9;`` → ``&#xE9;`` → ``é``).  We unescape
   in a loop (``html.unescape`` repeated until stable, max 5 passes) before
   parsing with ``_parse_store_from_block``.

   **Namespace note**: the ``/journey`` context yields a plain numeric
   ``store_ref`` (e.g. ``"452"``), while the annuaire yields ``"s-NNNN"``
   prefixed refs (e.g. ``"s-452"``).  These are distinct identifiers even
   when the numeric part matches — both are kept without collision.  All
   ``parse_stores`` output is de-duplicated on ``store_ref``.
"""

from __future__ import annotations

import html as _html_stdlib
import logging
import re
from collections.abc import Iterator

from parser.capture import iter_records
from parser.enseignes import _schemaorg as so
from parser.model import ParsedProduct, ParsedStore
from parser.pricing import to_cents

logger = logging.getLogger(__name__)

ENSEIGNE = "auchan"

# ``/charal-paves.../pr-C1201319`` -> product id ``C1201319``
_PR_ID_RE = re.compile(r"/pr-(C\d+)\b")
# ``Réf / EAN :</span> ... 22087 / 3181232180801 ...`` — entity-encoded é
_REF_EAN_RE = re.compile(
    r"/\s*EAN\s*:</span>\s*<div[^>]*>(.*?)</div>", re.DOTALL | re.IGNORECASE
)
_EAN_RE = re.compile(r"/\s*(\d{8,14})")

# Annuaire (/nos-magasins) patterns — applied after double-unescape.
# Matches href="/magasins/<type>/<slug>/s-NNNN" inside a place-pos__btn link.
_ANNUAIRE_STORE_REF_RE = re.compile(r'href="[^"]+/(s-\d+)"')
# The store name is inside <span class="place-pos__name">…</span>.
_ANNUAIRE_NAME_RE = re.compile(r'<span class="place-pos__name">(.*?)</span>')
# The address block <div class="place-pos__address"><span>…</span>…</div>.
_ANNUAIRE_ADDR_RE = re.compile(
    r'<div class="place-pos__address">(.*?)</div>', re.DOTALL
)
# Postal code + city: "02500 Hirson" → ("02500", "Hirson")
_CP_CITY_RE = re.compile(r'^(\d{5})\s+(.*)')
# URL marker for national directory pages.
_NOS_MAGASINS_URL = "/nos-magasins?types="


def _pr_id(url: str | None) -> str | None:
    """Auchan product id from a ``/pr-C<id>`` URL or href."""
    if not url:
        return None
    match = _PR_ID_RE.search(url)
    return match.group(1) if match else None


# --------------------------------------------------------------------------
# store extraction (from the /journey API context)
# --------------------------------------------------------------------------
def _grocery_contexts(record: dict) -> Iterator[dict]:
    """Yield every active ``GROCERY`` context object in a ``/journey`` body."""
    body = record.get("response_json")
    if not isinstance(body, dict):
        return
    for entry in body.get("activeContexts") or []:
        if not isinstance(entry, dict) or entry.get("type") != "GROCERY":
            continue
        ctx = entry.get("context")
        if isinstance(ctx, dict):
            yield ctx


def _store_from_context(ctx: dict) -> ParsedStore | None:
    """Build a ``ParsedStore`` from an active GROCERY journey context."""
    seller = ctx.get("seller") or {}
    ref = (seller.get("storeReference") or {}).get("id")
    if not ref:
        return None
    pos = ctx.get("pointOfService") or {}
    address = pos.get("address") or ctx.get("address") or {}
    location = pos.get("location") or {}
    return ParsedStore(
        enseigne=ENSEIGNE,
        store_ref=str(ref),
        name=pos.get("name"),
        city=address.get("city") or None,
        postal_code=address.get("zipcode") or None,
        lat=location.get("latitude"),
        lng=location.get("longitude"),
    )


def _active_store_ref(ndjson_path: str) -> str | None:
    """Last GROCERY store ref seen across the capture's ``/journey`` calls.

    The basket store is chosen part-way through a session, so later journey
    responses are authoritative — last write wins.
    """
    ref: str | None = None
    for record in iter_records(ndjson_path):
        if "/journey" not in (record.get("url") or ""):
            continue
        for ctx in _grocery_contexts(record):
            seller = ctx.get("seller") or {}
            seen = (seller.get("storeReference") or {}).get("id")
            if seen:
                ref = str(seen)
    return ref


def _unescape_loop(text: str, max_passes: int = 5) -> str:
    """Repeatedly apply ``html.unescape`` until the string stabilises.

    The NDJSON ``response_text`` for ``/nos-magasins`` pages is HTML-escaped
    twice (the server embeds already-escaped markup inside another HTML layer).
    Two passes are sufficient in practice, but we loop for safety (max 5).
    """
    prev = ""
    passes = 0
    while text != prev and passes < max_passes:
        prev = text
        text = _html_stdlib.unescape(text)
        passes += 1
    return text


def _parse_store_from_block(block: str) -> ParsedStore | None:
    """Extract a ``ParsedStore`` from one ``place-pos`` HTML block.

    The block must already be fully HTML-unescaped (use ``_unescape_loop``
    before calling).  Returns ``None`` when the ``s-NNNN`` store ref cannot
    be found (block is then unusable as a store identity).

    ``lat``/``lng`` are always ``None`` — the national directory page does not
    carry GPS coordinates.
    """
    ref_match = _ANNUAIRE_STORE_REF_RE.search(block)
    if not ref_match:
        return None
    store_ref = ref_match.group(1)  # e.g. "s-124"

    name_match = _ANNUAIRE_NAME_RE.search(block)
    name: str | None = None
    if name_match:
        name = so.unescape(name_match.group(1))

    postal_code: str | None = None
    city: str | None = None
    addr_match = _ANNUAIRE_ADDR_RE.search(block)
    if addr_match:
        # Address block contains 3 <span> children: [street, (empty), "CP Ville"]
        spans = re.findall(r"<span>(.*?)</span>", addr_match.group(1))
        cp_city_text: str | None = None
        if len(spans) >= 3:
            # 3rd span is "CP Ville"
            cp_city_text = so.unescape(spans[2])
        elif len(spans) == 1:
            # Malformed — try the single span
            cp_city_text = so.unescape(spans[0])
        if cp_city_text:
            m = _CP_CITY_RE.match(cp_city_text)
            if m:
                postal_code = m.group(1)
                city = m.group(2).strip() or None

    return ParsedStore(
        enseigne=ENSEIGNE,
        store_ref=store_ref,
        name=name,
        city=city,
        postal_code=postal_code,
        lat=None,
        lng=None,
    )


def _iter_annuaire_stores(ndjson_path: str) -> Iterator[ParsedStore]:
    """Yield ``ParsedStore`` objects from ``/nos-magasins?types=*`` pages.

    Each type (HYPER, SUPER, DRIVE, PICKUP_POINT, LOCKERS, PROXY) is a
    separate page in the capture but all use the same HTML structure.  Pages
    may appear multiple times in the capture (duplicate URLs); we de-duplicate
    at the page level first (same URL, same content), then at the store-ref
    level via the caller's ``seen`` set.
    """
    seen_urls: set[str] = set()
    n_pages = 0
    n_stores = 0
    for record in iter_records(ndjson_path):
        url = record.get("url") or ""
        if _NOS_MAGASINS_URL not in url:
            continue
        if url in seen_urls:
            continue
        seen_urls.add(url)

        html = record.get("response_text") or ""
        if not html:
            continue

        html = _unescape_loop(html)
        n_pages += 1

        # Split on place-pos__store-wrapper boundaries to isolate each store
        # block; we use the list-item wrapper as the natural separator.
        # Fall back to iterating on all place-pos divs if no wrappers found.
        for block_match in re.finditer(
            r'<div class="place-pos"[^>]*>.*?(?=<div class="place-pos"|</ul>|$)',
            html,
            re.DOTALL,
        ):
            store = _parse_store_from_block(block_match.group(0))
            if store is not None:
                n_stores += 1
                yield store

    logger.info(
        "auchan: annuaire /nos-magasins — %d page(s) lue(s), %d magasin(s) bruts extraits",
        n_pages,
        n_stores,
    )


def parse_stores(ndjson_path: str) -> Iterator[ParsedStore]:
    """Yield drive stores referenced by an Auchan capture.

    Two sources are combined and de-duplicated on ``store_ref``:

    1. The active ``GROCERY`` journey context (``/journey``) — one store with
       GPS coordinates.  Its ``store_ref`` is a plain numeric string (e.g.
       ``"452"``).
    2. The national directory pages (``/nos-magasins?types=*``) — ~961 stores
       across 6 store types (HYPER, SUPER, DRIVE, PICKUP_POINT, LOCKERS,
       PROXY).  Their ``store_ref`` values use the ``"s-NNNN"`` prefix (e.g.
       ``"s-124"``), so they never collide with source 1.

    **GPS limitation**: the national directory does not carry latitude /
    longitude.  Stores sourced from the annuaire have ``lat=None, lng=None``.
    Geocoding from the text address is feasible but out of scope for Phase 2.
    """
    seen: set[str] = set()
    n_emitted = 0

    # Source 1: journey context (one active basket store, with GPS)
    for record in iter_records(ndjson_path):
        if "/journey" not in (record.get("url") or ""):
            continue
        for ctx in _grocery_contexts(record):
            store = _store_from_context(ctx)
            if store is None or store.store_ref in seen:
                continue
            seen.add(store.store_ref)
            n_emitted += 1
            yield store

    # Source 2: national directory (/nos-magasins?types=*)
    for store in _iter_annuaire_stores(ndjson_path):
        if store.store_ref in seen:
            continue
        seen.add(store.store_ref)
        n_emitted += 1
        yield store

    logger.info("auchan: %d magasin(s) au total (journey + annuaire)", n_emitted)


# --------------------------------------------------------------------------
# product extraction (microdata tiles + detail pages)
# --------------------------------------------------------------------------
def _tile_name_and_brand(chunk: str) -> tuple[str | None, str | None]:
    """Name + brand from a listing tile's ``product-thumbnail__description``.

    The block is ``<p itemprop="name description"><strong itemprop="brand">
    BRAND</strong> Product name</p>`` — brand and name share one node.
    """
    block = re.search(
        r'product-thumbnail__description"[^>]*>(.*?)</p>', chunk, re.DOTALL
    )
    if not block:
        return None, None
    inner = block.group(1)
    brand_match = re.search(r"<strong[^>]*>(.*?)</strong>", inner, re.DOTALL)
    brand = so.unescape(brand_match.group(1)) if brand_match else None
    name = so.unescape(re.sub(r"<[^>]+>", " ", inner))
    return name, brand


def _tile_quantity(chunk: str) -> str | None:
    """Joined ``product-attribute`` labels of a tile (e.g. ``2x130g``)."""
    attrs = [
        so.unescape(a)
        for a in re.findall(
            r'class="product-attribute"[^>]*>([^<]+)<', chunk
        )
    ]
    attrs = [a for a in attrs if a]
    return " ".join(attrs) or None


def _offer(chunk: str) -> tuple[int | None, bool | None]:
    """``(price_cents, available)`` from the first nested ``schema.org/Offer``.

    The Offer block is ``<div ... itemtype=".../Offer">...<meta itemprop=
    "price" ...><meta itemprop="availability" ...></div>``. We scope to the
    text from the Offer marker to its closing ``</div></div>``-ish region by
    taking a generous slice — the ``price``/``availability`` ``<meta>`` tags
    always sit right after the marker, well before any sibling Offer.
    """
    marker = re.search(r'itemtype="https?://schema\.org/Offer"', chunk)
    scope = chunk[marker.start() : marker.start() + 600] if marker else chunk
    price = to_cents(so.itemprop_meta(scope, "price"))
    availability = so.itemprop_meta(scope, "availability")
    available = None
    if availability:
        available = "InStock" in availability
    return price, available


def _parse_tile(chunk: str, captured_at: str, store_ref: str | None) -> ParsedProduct | None:
    """Build a ``ParsedProduct`` from one listing-page microdata tile."""
    href = re.search(r'href="(/[^"]*pr-C\d+)"', chunk)
    name, brand = _tile_name_and_brand(chunk)
    if not name:
        return None
    price_cents, available = _offer(chunk)
    image = so.itemprop_meta(chunk, "image")
    product_url = None
    if href:
        product_url = "https://www.auchan.fr" + so.unescape(href.group(1))
    return ParsedProduct(
        enseigne=ENSEIGNE,
        name=name,
        captured_at=captured_at,
        store_ref=store_ref,
        brand=brand,
        quantity=_tile_quantity(chunk),
        price_cents=price_cents,
        product_url=product_url,
        image_url=image,
        available=available,
        enseigne_product_id=_pr_id(href.group(1)) if href else None,
    )


def _detail_breadcrumb_rayon(html: str) -> str | None:
    """Deepest breadcrumb label = the product's rayon.

    The breadcrumb is a ``schema.org/BreadcrumbList`` of ``ListItem``s; the
    last (excluding the product's own name) is the rayon.
    """
    items = so.split_itemscopes(html, "ListItem")
    labels = []
    for item in items:
        label = so.itemprop_meta(item, "name")
        if label:
            labels.append(label)
    if len(labels) <= 1:
        return None
    # first label is "Accueil"; last is usually the product itself
    return labels[-2] if len(labels) >= 2 else None


def _detail_ean(html: str) -> str | None:
    """EAN from the detail page's ``Réf / EAN : <ref> / <ean13>`` block."""
    block = _REF_EAN_RE.search(html)
    if not block:
        return None
    ean = _EAN_RE.search(block.group(1))
    return ean.group(1) if ean else None


def _parse_detail(html: str, url: str, captured_at: str, store_ref: str | None) -> ParsedProduct | None:
    """Build a ``ParsedProduct`` from a product-detail page.

    The detail page carries the EAN, the per-measure price and the rayon
    breadcrumb on top of the listing-tile fields.
    """
    product = so.split_itemscopes(html, "Product")
    chunk = product[0] if product else html
    h1 = re.search(r"<h1>(.*?)</h1>", chunk, re.DOTALL)
    name = so.unescape(h1.group(1)) if h1 else so.itemprop_meta(chunk, "name")
    if not name:
        return None
    brand_match = re.search(r'itemprop="brand"[^>]*content="([^"]*)"', chunk)
    brand = so.unescape(brand_match.group(1)) if brand_match else None
    price_cents, available = _offer(chunk)
    per_measure = None
    measure_unit = None
    smaller = re.search(
        r'product-price--smaller"><span>(.*?)</span>', chunk, re.DOTALL
    )
    if smaller:
        text = so.unescape(smaller.group(1)) or ""
        per_measure = to_cents(text)
        unit = re.search(r"/\s*([a-zA-Z]+)\s*$", text)
        if unit:
            measure_unit = unit.group(1).lower()
    attrs = [
        so.unescape(a)
        for a in re.findall(r'class="product-attribute"[^>]*>([^<]+)<', chunk)
    ]
    attrs = [a for a in attrs if a]
    return ParsedProduct(
        enseigne=ENSEIGNE,
        name=name,
        captured_at=captured_at,
        store_ref=store_ref,
        ean=_detail_ean(html),
        brand=brand,
        quantity=attrs[0] if attrs else None,
        category=_detail_breadcrumb_rayon(html),
        price_cents=price_cents,
        price_per_measure_cents=per_measure,
        measure_unit=measure_unit,
        product_url=url,
        image_url=so.itemprop_meta(chunk, "image"),
        available=available,
        enseigne_product_id=_pr_id(url),
    )


def parse_products(ndjson_path: str) -> Iterator[ParsedProduct]:
    """Yield every product observation in an Auchan capture file.

    Listing tiles and detail pages are both parsed; a detail page enriches
    its matching tile (joined on the ``pr-C<id>`` product id) with the EAN,
    per-measure price and rayon. De-duplicated on
    ``(enseigne_product_id, store_ref, price_cents)``.
    """
    store_ref = _active_store_ref(ndjson_path)
    if store_ref:
        logger.info("auchan: store actif (GROCERY) = %s", store_ref)

    tiles: dict[str, ParsedProduct] = {}
    detail_by_id: dict[str, ParsedProduct] = {}
    no_id: list[ParsedProduct] = []
    n_tiles = 0
    n_with_ean = 0
    n_ignored_no_name = 0

    for record in iter_records(ndjson_path):
        html = record.get("response_text")
        if not html or "schema.org/Product" not in html:
            continue
        captured_at = record.get("captured_at") or ""
        url = record.get("url") or ""

        if "/pr-C" in url:
            detail = _parse_detail(html, url, captured_at, store_ref)
            if detail is None:
                n_ignored_no_name += 1
                continue
            if detail.ean:
                n_with_ean += 1
            if detail.enseigne_product_id:
                detail_by_id[detail.enseigne_product_id] = detail
            else:
                no_id.append(detail)
            continue

        for chunk in so.split_itemscopes(html, "Product"):
            n_tiles += 1
            product = _parse_tile(chunk, captured_at, store_ref)
            if product is None:
                n_ignored_no_name += 1
                continue
            pid = product.enseigne_product_id
            if pid and pid not in tiles:
                tiles[pid] = product
            elif not pid:
                no_id.append(product)

    # merge: a detail page wins (EAN + richer fields), keeping the tile price
    # when the detail page lost its offer block.
    merged: dict[str, ParsedProduct] = dict(tiles)
    for pid, detail in detail_by_id.items():
        tile = merged.get(pid)
        if tile is not None and detail.price_cents is None:
            detail.price_cents = tile.price_cents
        if tile is not None and detail.available is None:
            detail.available = tile.available
        merged[pid] = detail

    seen: set[tuple] = set()
    n_emitted = 0
    n_ignored_dup = 0
    for product in list(merged.values()) + no_id:
        key = (product.enseigne_product_id, product.store_ref, product.price_cents)
        if key in seen:
            n_ignored_dup += 1
            continue
        seen.add(key)
        n_emitted += 1
        yield product

    logger.info(
        "auchan: %d records produit trouvés (%d avec EAN), "
        "%d ParsedProduct extraits, %d ignorés "
        "(%d sans nom, %d doublons)",
        n_tiles + len(detail_by_id) + len(no_id),
        n_with_ean,
        n_emitted,
        n_ignored_no_name + n_ignored_dup,
        n_ignored_no_name,
        n_ignored_dup,
    )
