"""Intermarché drive parser.

Intermarché's site is a Next.js App Router app. There is no ``__NEXT_DATA__``
blob; instead the server streams its React Server Components payload as a
sequence of ``self.__next_f.push([n, "<chunk>"])`` calls embedded in the page
HTML (``response_text``). Concatenating the string chunks of every push
reconstructs one big RSC text that contains the fully structured product
objects as plain JSON.

We parse the **RSC payload** rather than scraping the rendered DOM: the RSC
objects ship clean, typed fields (``prices.productPrice.value``,
``informations.packaging``, ``informations.brand`` …) whereas the DOM only
carries display strings that would need fragile text parsing. The RSC objects
are themselves valid JSON sub-trees — Next.js module references such as
``"$undefined"`` or ``"$b:props:..."`` are encoded as ordinary JSON strings,
so a balanced-brace scan + ``json.loads`` extracts them losslessly.

Two product shapes appear, both the same object schema:

* **rayon (category listing) pages** — a ``"products":[...]`` array, sitting
  right after the rayon's ``"path":"/rayons/..."``. This is the authoritative
  listing (the anchor "Viandes et Poissons Bio" rayon = 16 products);
* **product-detail pages** — a single ``"product":{...}`` object. The detail
  page also carries a ``crossMerch`` ("Vous aimerez aussi") block with its own
  ``products`` array — that cross-sell zone is deliberately skipped.

The EAN-13 is taken from the product's ``url`` field
(``/produit/[slug]/[EAN-13]``) — the most reliable source, as required.

``store_ref`` is the Intermarché point-de-vente id (``store_id_itm`` in the
RSC analytics block, e.g. ``"07879"``).

``parse_stores`` yields nothing: no Intermarché store-list endpoint is present
in Phase-1 captures.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Iterator
from urllib.parse import unquote

from parser.capture import iter_records
from parser.model import ParsedProduct, ParsedStore
from parser.pricing import promo_pct, to_cents

logger = logging.getLogger(__name__)

ENSEIGNE = "intermarche"

# ``self.__next_f.push([n, "<string-chunk>"])`` — the RSC stream is delivered
# as a series of these calls. The non-greedy body is JSON-parsed afterwards.
_NEXT_F_RE = re.compile(r"self\.__next_f\.push\(\[(.*?)\]\)", re.DOTALL)

# ``store_id_itm`` lives in the RSC analytics payload; it is the PDV ref.
_STORE_ID_RE = re.compile(r'"store_id_itm":"(\d+)"')

# Breadcrumb crumbs whose label is structural noise, never a real rayon.
_NOISE_CRUMBS = {"voir tout", "accueil"}


# --------------------------------------------------------------------------
# RSC payload reconstruction
# --------------------------------------------------------------------------
def _reconstruct_rsc(html: str | None) -> str:
    """Concatenate every ``__next_f.push`` string chunk into the RSC text.

    Each push is ``[n, "<chunk>"]`` (or shorter bootstrap forms). Only the
    string-payload pushes carry product data; we ignore the rest.
    """
    if not html or "__next_f" not in html:
        return ""
    chunks: list[str] = []
    for body in _NEXT_F_RE.findall(html):
        try:
            arr = json.loads("[" + body + "]")
        except json.JSONDecodeError:
            continue
        if len(arr) >= 2 and isinstance(arr[1], str):
            chunks.append(arr[1])
    return "".join(chunks)


def _balanced_slice(text: str, start: int) -> str | None:
    """Return the balanced ``{...}`` / ``[...]`` substring beginning at
    ``text[start]`` (which must be an opening brace or bracket).

    String-aware so braces inside JSON strings do not break the count.
    Returns ``None`` if the structure never closes.
    """
    if start >= len(text) or text[start] not in "{[":
        return None
    open_ch = text[start]
    close_ch = "}" if open_ch == "{" else "]"
    depth = 0
    in_str = False
    escaped = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == open_ch:
            depth += 1
        elif ch == close_ch:
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return None


def _load_balanced(text: str, marker_end: int) -> object | None:
    """Balance-scan a JSON value that starts at ``text[marker_end - 1]`` and
    ``json.loads`` it. Returns ``None`` on any failure."""
    blob = _balanced_slice(text, marker_end - 1)
    if blob is None:
        return None
    try:
        return json.loads(blob)
    except json.JSONDecodeError:
        return None


# --------------------------------------------------------------------------
# field extraction helpers
# --------------------------------------------------------------------------
def _store_ref(rsc: str) -> str | None:
    """The PDV ref (``store_id_itm``) shared by every product on a page."""
    match = _STORE_ID_RE.search(rsc)
    return match.group(1) if match else None


def _page_category(html: str) -> str | None:
    """Deepest meaningful rayon label, from the JSON-LD ``BreadcrumbList``.

    Both rayon and product-detail pages embed a
    ``{"@type":"BreadcrumbList","itemListElement":[...]}`` script. The last
    non-noise crumb is the product's rayon (``"Voir TOUT"`` is skipped).
    """
    marker = '"@type":"BreadcrumbList","itemListElement":'
    idx = html.find(marker)
    if idx < 0:
        return None
    items = _load_balanced(html, idx + len(marker) + 1)
    if not isinstance(items, list):
        return None
    labels = [
        it["name"].strip()
        for it in items
        if isinstance(it, dict)
        and isinstance(it.get("name"), str)
        and it["name"].strip()
        and it["name"].strip().lower() not in _NOISE_CRUMBS
    ]
    return labels[-1] if labels else None


def _ean_from_url(url: str | None) -> str | None:
    """Extract the EAN-13 trailing segment of a ``/produit/[slug]/[EAN]`` URL."""
    if not isinstance(url, str):
        return None
    segment = url.rstrip("/").rsplit("/", 1)[-1]
    return segment if segment.isdigit() and len(segment) == 13 else None


def _clean(value: object) -> str | None:
    """Return a trimmed string, or ``None`` for empty / Next.js sentinels."""
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text or text.startswith("$"):  # "$undefined", "$b:props:..." refs
        return None
    return text


def _price_value(node: object) -> object | None:
    """Pull the raw numeric ``value`` out of an Intermarché price object."""
    if isinstance(node, dict):
        return node.get("value")
    return None


def _product_from_rsc_object(
    obj: dict, *, store_ref: str | None, category: str | None, captured_at: str
) -> ParsedProduct | None:
    """Build a ``ParsedProduct`` from one RSC product object.

    Returns ``None`` when the object carries no usable name.
    """
    if not isinstance(obj, dict):
        return None
    informations = obj.get("informations")
    informations = informations if isinstance(informations, dict) else {}

    name = _clean(informations.get("title")) or _clean(obj.get("title"))
    if not name:
        return None

    url = _clean(obj.get("url"))
    ean = _ean_from_url(url) or (
        obj.get("ean") if isinstance(obj.get("ean"), str) and len(obj["ean"]) == 13 else None
    )

    prices = obj.get("prices")
    prices = prices if isinstance(prices, dict) else {}
    price_cents = to_cents(_price_value(prices.get("productPrice")))
    per_measure_cents = to_cents(_price_value(prices.get("unitPrice")))

    measure_unit = None
    unit_price = prices.get("unitPrice")
    if isinstance(unit_price, dict):
        currency = unit_price.get("currency")  # e.g. "€/Kg"
        if isinstance(currency, str) and "/" in currency:
            measure_unit = currency.split("/", 1)[1].strip().lower() or None

    crossed = to_cents(_price_value(prices.get("crossedOutPrice")))
    is_promo = bool(obj.get("hasReduction"))
    promo_price_cents = None
    pct = None
    if is_promo and crossed is not None and price_cents is not None and price_cents < crossed:
        promo_price_cents = price_cents
        pct = promo_pct(crossed, price_cents)

    available = obj.get("available")
    product_url = (
        f"https://www.intermarche.com{unquote(url)}"
        if url and url.startswith("/")
        else url
    )

    enseigne_product_id = obj.get("id")
    if enseigne_product_id is not None:
        enseigne_product_id = str(enseigne_product_id)

    return ParsedProduct(
        enseigne=ENSEIGNE,
        name=name,
        captured_at=captured_at,
        store_ref=store_ref,
        ean=ean,
        brand=_clean(informations.get("brand")),
        quantity=_clean(informations.get("packaging")),
        category=category,
        price_cents=price_cents,
        price_per_measure_cents=per_measure_cents,
        measure_unit=measure_unit,
        promo_price_cents=promo_price_cents,
        promo_pct=pct,
        is_promo=is_promo,
        product_url=product_url,
        image_url=_first_image(informations.get("allImages")),
        available=bool(available) if isinstance(available, bool) else None,
        enseigne_product_id=enseigne_product_id,
    )


def _first_image(all_images: object) -> str | None:
    """First product image URL from the ``informations.allImages`` list."""
    if not isinstance(all_images, list):
        return None
    for entry in all_images:
        if isinstance(entry, dict):
            src = _clean(entry.get("src"))
            if src:
                return src
    return None


# --------------------------------------------------------------------------
# RSC product-object discovery
# --------------------------------------------------------------------------
def _iter_rayon_products(rsc: str) -> Iterator[dict]:
    """Yield each product object of a rayon listing's ``"products":[...]``.

    The rayon array is anchored to the page's own ``"path":"/rayons/..."``;
    cross-sell ``products`` arrays nested under ``crossMerch`` are not matched
    because they are not preceded by that rayon-path marker.
    """
    marker = '"path":"/rayons/'
    for path_idx in (m.start() for m in re.finditer(re.escape(marker), rsc)):
        products_marker = '"products":['
        prod_idx = rsc.find(products_marker, path_idx)
        if prod_idx < 0 or prod_idx - path_idx > 400:
            continue  # the products array must sit right after the rayon path
        array = _load_balanced(rsc, prod_idx + len(products_marker))
        if isinstance(array, list):
            for item in array:
                if isinstance(item, dict):
                    yield item
            return  # one rayon listing per page


def _iter_detail_product(rsc: str) -> Iterator[dict]:
    """Yield the single main product object of a product-detail page.

    On a detail page the main product is the value of a top-level
    ``"product":{...}`` key (the surrounding ``$L4c`` component props). The
    ``crossMerch.products`` cross-sell array is intentionally excluded.
    """
    marker = '"product":{"id":"'
    idx = rsc.find(marker)
    if idx < 0:
        return
    # ``_load_balanced`` scans from ``marker_end - 1``; point it at the ``{``.
    obj = _load_balanced(rsc, idx + len('"product":') + 1)
    if isinstance(obj, dict) and obj.get("ean"):
        yield obj


# --------------------------------------------------------------------------
# public interface
# --------------------------------------------------------------------------
def parse_products(ndjson_path: str) -> Iterator[ParsedProduct]:
    """Yield every product observation in an Intermarché capture file.

    De-duplicated on ``(ean, store_ref, price_cents)`` so the same product
    appearing on both its rayon listing and its detail page is counted once.
    """
    seen: set[tuple] = set()
    n_pages = 0
    n_objects = 0
    n_emitted = 0
    n_ignored_no_name = 0
    n_ignored_dup = 0

    for record in iter_records(ndjson_path):
        rsc = _reconstruct_rsc(record.get("response_text"))
        if not rsc:
            continue
        n_pages += 1
        store_ref = _store_ref(rsc)
        category = _page_category(record.get("response_text") or "")
        captured_at = record.get("captured_at") or ""

        objects = list(_iter_rayon_products(rsc)) + list(_iter_detail_product(rsc))
        for obj in objects:
            n_objects += 1
            product = _product_from_rsc_object(
                obj, store_ref=store_ref, category=category, captured_at=captured_at
            )
            if product is None:
                n_ignored_no_name += 1
                continue
            key = (product.ean, product.store_ref, product.price_cents)
            if key in seen:
                n_ignored_dup += 1
                continue
            seen.add(key)
            n_emitted += 1
            yield product

    logger.info(
        "intermarche: %d page(s) RSC, %d objet(s) produit trouvés, "
        "%d ParsedProduct extraits, %d ignorés (%d sans nom, %d doublons)",
        n_pages,
        n_objects,
        n_emitted,
        n_ignored_no_name + n_ignored_dup,
        n_ignored_no_name,
        n_ignored_dup,
    )


def parse_stores(ndjson_path: str) -> Iterator[ParsedStore]:
    """Yield drive stores from an Intermarché capture file.

    Phase-1 Intermarché captures contain no store-list endpoint, so this
    yields nothing. Kept for the standard parser interface.
    """
    logger.info("intermarche: aucune liste magasin dans la capture — 0 magasin")
    return iter(())
