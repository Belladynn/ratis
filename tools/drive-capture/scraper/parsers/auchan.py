"""Auchan scraper-side parsers — www.auchan.fr.

Two parse phases adapted from the existing capture-replay parser
(``parser/enseignes/auchan.py``).  These parsers receive raw HTTP responses
already fetched by the runner, rather than reading NDJSON replay files.

* ``parse_stores`` — HTML from GET /nos-magasins?types=DRIVE
  Extracts stores via the ``.place-pos`` block pattern.  GPS not included;
  lat/lng = None.  The HTML may be double-escaped — we unescape in a loop.

* ``parse_rayon`` — HTML from GET /{slug}/ca-{category_id}?page=N
  Extracts schema.org microdata ``Product`` tiles.  Builds ``fiche_jobs``
  for detail pages (pr-C<id>) to enrich with EAN + per-measure price.
  Pagination: looks for ``?page=N`` in next-page links.
"""

from __future__ import annotations

import html as _html_stdlib
import logging
import re

from scraper.parsers._models import ParsedResult, ProductResult, StoreResult, to_cents

logger = logging.getLogger(__name__)

ENSEIGNE = "auchan"

# ---- store patterns (from annuaire /nos-magasins) --------------------------
_ANNUAIRE_STORE_REF_RE = re.compile(r'href="[^"]+/(s-\d+)"')
_ANNUAIRE_NAME_RE = re.compile(r'<span class="place-pos__name">(.*?)</span>')
_ANNUAIRE_ADDR_RE = re.compile(
    r'<div class="place-pos__address">(.*?)</div>', re.DOTALL
)
_CP_CITY_RE = re.compile(r'^(\d{5})\s+(.*)')

# ---- fiche patterns (product detail pages) ----------------------------------
_FLIX_EAN_RE = re.compile(r'data-flix-ean="(\d{8,14})"')
_FEATURE_VALUES_RE = re.compile(
    r'Réf\s*/\s*EAN\s*:.*?<div[^>]*class="[^"]*product-description__feature-values[^"]*"[^>]*>(.*?)</div>',
    re.DOTALL | re.IGNORECASE,
)
_EAN_TOKEN_RE = re.compile(r'\b(\d{8,14})\b')

# ---- product patterns (schema.org microdata) --------------------------------
_PRODUCT_ITEMSCOPE_RE = re.compile(
    r'itemtype="https?://schema\.org/Product"', re.IGNORECASE
)
_OFFER_RE = re.compile(r'itemtype="https?://schema\.org/Offer"', re.IGNORECASE)
_ITEMPROP_META_RE = re.compile(
    r'itemprop="([^"]+)"[^>]*(?:content|value)="([^"]*)"', re.IGNORECASE
)
_PR_ID_RE = re.compile(r"/pr-(C\d+)\b")


def _unescape_loop(text: str, max_passes: int = 5) -> str:
    """Repeatedly unescape HTML until stable (handles double-encoding)."""
    prev = ""
    passes = 0
    while text != prev and passes < max_passes:
        prev = text
        text = _html_stdlib.unescape(text)
        passes += 1
    return text


def _itemprop_meta(html: str, prop: str) -> str | None:
    """Extract the first ``content`` / ``value`` for a given itemprop."""
    for m in _ITEMPROP_META_RE.finditer(html):
        if m.group(1).lower() == prop.lower():
            return _html_stdlib.unescape(m.group(2)) or None
    return None


def _split_product_itemscopes(html: str) -> list[str]:
    """Split HTML into per-Product itemscope chunks."""
    positions = [m.start() for m in _PRODUCT_ITEMSCOPE_RE.finditer(html)]
    if not positions:
        return []
    chunks = []
    for i, pos in enumerate(positions):
        # Walk back to the opening < of the tag
        start = html.rfind("<", 0, pos)
        end = positions[i + 1] if i + 1 < len(positions) else len(html)
        # Trim at the opening tag of the next product (generous but safe)
        chunk_start = start if start != -1 else pos
        chunks.append(html[chunk_start:end])
    return chunks


def _parse_store_from_block(block: str) -> StoreResult | None:
    """Extract a StoreResult from one place-pos HTML block."""
    ref_match = _ANNUAIRE_STORE_REF_RE.search(block)
    if not ref_match:
        return None
    store_id = ref_match.group(1)  # e.g. "s-124"

    name: str | None = None
    name_match = _ANNUAIRE_NAME_RE.search(block)
    if name_match:
        name = _html_stdlib.unescape(re.sub(r"<[^>]+>", "", name_match.group(1))).strip() or None

    postal_code: str | None = None
    city: str | None = None
    addr_match = _ANNUAIRE_ADDR_RE.search(block)
    if addr_match:
        spans = re.findall(r"<span>(.*?)</span>", addr_match.group(1))
        cp_city_text: str | None = None
        if len(spans) >= 3:
            cp_city_text = _html_stdlib.unescape(re.sub(r"<[^>]+>", "", spans[2])).strip()
        elif len(spans) == 1:
            cp_city_text = _html_stdlib.unescape(re.sub(r"<[^>]+>", "", spans[0])).strip()
        if cp_city_text:
            m = _CP_CITY_RE.match(cp_city_text)
            if m:
                postal_code = m.group(1)
                city = m.group(2).strip() or None

    return StoreResult(
        store_id=store_id,
        name=name,
        city=city,
        postal_code=postal_code,
    )


def _empty() -> ParsedResult:
    return ParsedResult()


def parse_stores(response_text: str) -> ParsedResult:
    """Parse Auchan store list from /nos-magasins?types=DRIVE HTML.

    Uses the ``.place-pos`` block pattern from the national directory.
    GPS coordinates are not available; lat/lng = None.
    """
    if not response_text:
        logger.debug("auchan.parse_stores: empty response")
        return _empty()

    html = _unescape_loop(response_text)
    stores: list[StoreResult] = []
    for block_match in re.finditer(
        r'<div class="place-pos"[^>]*>.*?(?=<div class="place-pos"|</ul>|$)',
        html,
        re.DOTALL,
    ):
        try:
            store = _parse_store_from_block(block_match.group(0))
            if store is not None:
                stores.append(store)
        except Exception as exc:
            logger.warning("auchan.parse_stores: skipping block — %s", exc)

    logger.debug("auchan.parse_stores: %d stores extracted", len(stores))
    return ParsedResult(stores=stores)


def _parse_tile(chunk: str) -> ProductResult | None:
    """Build a ProductResult from one schema.org/Product listing tile."""
    try:
        # Name: Auchan uses <p itemprop="name description"><strong itemprop="brand">…</strong> NAME</p>
        # The name text follows the brand <strong>; strip inner tags then clean.
        desc_match = re.search(
            r'itemprop="name[^"]*"[^>]*>(.*?)</p>',
            chunk,
            re.DOTALL,
        )
        if desc_match:
            raw_desc = desc_match.group(1)
            # Remove brand tag — text node after it is the product name
            raw_desc = re.sub(r"<strong[^>]*>.*?</strong>", "", raw_desc, flags=re.DOTALL)
            name = _html_stdlib.unescape(re.sub(r"<[^>]+>", " ", raw_desc)).strip()
        else:
            # Fallback: h2/h3 title class or itemprop meta
            h_match = re.search(
                r'<h[23][^>]*class="[^"]*product-thumbnail__title[^"]*"[^>]*>(.*?)</h[23]>',
                chunk,
                re.DOTALL,
            )
            name = (
                _html_stdlib.unescape(re.sub(r"<[^>]+>", " ", h_match.group(1))).strip()
                if h_match
                else (_itemprop_meta(chunk, "name") or "")
            )
        name = re.sub(r"\s+", " ", name).strip()
        if not name:
            return None

        # Brand — from <strong itemprop="brand"> or meta
        brand_match = re.search(r"<strong[^>]*itemprop=['\"]brand['\"][^>]*>(.*?)</strong>", chunk, re.DOTALL)
        if brand_match:
            brand = _html_stdlib.unescape(re.sub(r"<[^>]+>", "", brand_match.group(1))).strip() or None
        else:
            brand = _itemprop_meta(chunk, "brand") or None

        # Price from schema.org/Offer
        offer_match = _OFFER_RE.search(chunk)
        scope = chunk[offer_match.start():offer_match.start() + 600] if offer_match else chunk
        price_cents = to_cents(_itemprop_meta(scope, "price"))

        # Availability
        _itemprop_meta(scope, "availability")
        # (not used in ProductResult but could be added)

        # Image
        image_url = _itemprop_meta(chunk, "image") or None

        # Product URL
        href = re.search(r'href="(/[^"]*pr-C\d+)"', chunk)
        product_url = "https://www.auchan.fr" + _html_stdlib.unescape(href.group(1)) if href else None

        # Internal ID
        internal_id: str | None = None
        if href:
            m = _PR_ID_RE.search(href.group(1))
            if m:
                internal_id = m.group(1)

        # Quantity: product-attribute spans
        attrs = [
            _html_stdlib.unescape(a).strip()
            for a in re.findall(r'class="product-attribute"[^>]*>([^<]+)<', chunk)
        ]
        quantity = " ".join(a for a in attrs if a) or None

        return ProductResult(
            name=name,
            brand=brand,
            quantity=quantity,
            price_cents=price_cents,
            image_url=image_url,
            product_url=product_url,
            internal_id=internal_id,
        )
    except Exception as exc:
        logger.warning("auchan._parse_tile: skipping tile — %s", exc)
        return None


def parse_rayon(response_text: str) -> ParsedResult:
    """Parse Auchan category page HTML (schema.org microdata).

    Extracts ProductResults from listing tiles and builds fiche_jobs for
    detail pages (pr-C<id>) to retrieve EAN + per-measure price.
    Pagination: looks for ?page=N in next-page links.
    """
    if not response_text:
        logger.debug("auchan.parse_rayon: empty response")
        return _empty()

    html = response_text
    if "schema.org/Product" not in html:
        logger.debug("auchan.parse_rayon: no schema.org/Product found")
        return _empty()

    products: list[ProductResult] = []
    fiche_jobs: list[dict] = []

    for chunk in _split_product_itemscopes(html):
        p = _parse_tile(chunk)
        if p is not None:
            products.append(p)
            if p.product_url:
                fiche_jobs.append({
                    "url": p.product_url,
                    "method": "GET",
                    "product_id": p.internal_id,
                })

    # Pagination is handled by _auchan_next_page_url in runner.py via ?page=N.
    # Do NOT detect next_url here — max(page_matches) would pick the last page
    # link (e.g. page 162) instead of page N+1, skipping 160 pages.
    logger.debug(
        "auchan.parse_rayon: %d products, %d fiche_jobs",
        len(products),
        len(fiche_jobs),
    )
    return ParsedResult(products=products, next_url=None, fiche_jobs=fiche_jobs)


def parse_fiche(response_text: str) -> ParsedResult:
    """Parse an Auchan product detail page to extract the EAN.

    Two extraction strategies (tried in order):
    1. ``data-flix-ean="..."`` attribute — present on products with a Flixmedia
       widget.
    2. The "Réf / EAN :" feature row — the value div contains either
       ``{SKU} / {EAN}`` or a bare EAN; we take the last 8-14 digit token.

    Returns a ``ParsedResult`` with a single ``ProductResult(ean=ean)`` when
    the EAN is found, or an empty ``ParsedResult`` otherwise.
    """
    if not response_text:
        logger.debug("auchan.parse_fiche: empty response")
        return _empty()

    # Strategy 1 — Flixmedia attribute
    flix_match = _FLIX_EAN_RE.search(response_text)
    if flix_match:
        ean = flix_match.group(1)
        logger.debug("auchan.parse_fiche: EAN from flix attribute — %s", ean)
        return ParsedResult(ean_updates=[("__job_product_id__", ean)])

    # Strategy 2 — "Réf / EAN :" feature row
    feat_match = _FEATURE_VALUES_RE.search(response_text)
    if feat_match:
        raw_value = _html_stdlib.unescape(re.sub(r"<[^>]+>", " ", feat_match.group(1))).strip()
        # Take the last numeric token of 8-14 digits (handles "SKU / EAN" format)
        tokens = _EAN_TOKEN_RE.findall(raw_value)
        if tokens:
            ean = tokens[-1]
            logger.debug("auchan.parse_fiche: EAN from feature row — %s", ean)
            return ParsedResult(ean_updates=[("__job_product_id__", ean)])

    logger.debug("auchan.parse_fiche: no EAN found")
    return _empty()
