"""Carrefour drive parser (pilot enseigne).

Carrefour's site is a Vue SPA. Structured product data appears in two
shapes within a Phase-1 capture, both using the same JSON:API-style
``{type:"product", attributes:{...}}`` envelope:

* embedded in HTML pages via ``window.__INITIAL_STATE__`` — notably under
  ``vuex.analytics.indexedEntities.product`` on product-detail pages;
* in JSON API responses such as ``/api/recommendations``, under
  ``data[].attributes.products[]``.

We don't care *where* a product object sits — we recursively walk every
captured record (HTML state + JSON body) and pick up every ``product``
envelope, de-duplicating on ``(ean, store_ref)`` so the same product seen
on several pages yields one observation per store.

Stores come from the ``/api/eligibility/drive`` endpoint.

The price for a product lives in
``attributes.offers[ean][offerServiceId].attributes.price`` and the store
that offer belongs to is the last segment of the ``offerServiceId``
(``"7850-150-1323"`` -> store ``ref`` ``"1323"``).
"""

from __future__ import annotations

import logging
from collections.abc import Iterator

from parser.capture import extract_initial_state, iter_records
from parser.model import ParsedProduct, ParsedStore
from parser.pricing import promo_pct, to_cents

logger = logging.getLogger(__name__)

ENSEIGNE = "carrefour"


# --------------------------------------------------------------------------
# product extraction
# --------------------------------------------------------------------------
def _walk_products(node: object) -> Iterator[dict]:
    """Yield every ``{type:"product", attributes:{...}}`` envelope in a tree."""
    if isinstance(node, dict):
        if node.get("type") == "product" and isinstance(node.get("attributes"), dict):
            yield node
        for value in node.values():
            yield from _walk_products(value)
    elif isinstance(node, list):
        for item in node:
            yield from _walk_products(item)


def _store_ref_from_offer_service_id(offer_service_id: str | None) -> str | None:
    """``"7850-150-1323"`` -> ``"1323"`` (the store ``ref``)."""
    if not offer_service_id:
        return None
    parts = offer_service_id.split("-")
    return parts[-1] if parts else None


def _first_image(images: dict | None) -> str | None:
    """First product image URL, with the ``FORMAT`` placeholder resolved."""
    if not isinstance(images, dict):
        return None
    paths = images.get("paths")
    if not isinstance(paths, list) or not paths:
        return None
    url = paths[0]
    fmt = "540x540"
    formats = images.get("formats")
    if isinstance(formats, dict) and formats.get("largest"):
        fmt = formats["largest"]
    return url.replace("p_FORMAT", f"p_{fmt}").replace("FORMAT", fmt)


def _deepest_category(categories: object) -> str | None:
    """Deepest (highest ``level``) category label = the product's rayon."""
    if not isinstance(categories, list) or not categories:
        return None
    valid = [c for c in categories if isinstance(c, dict) and c.get("label")]
    if not valid:
        return None
    deepest = max(valid, key=lambda c: c.get("level") or 0)
    return deepest.get("label")


def _product_url(slug: str | None, ean: str | None) -> str | None:
    if not slug or not ean:
        return None
    return f"https://www.carrefour.fr/p/{slug}-{ean}"


def _promo_from_offer(promotions: object, base_cents: int | None) -> tuple[int | None, int | None, bool]:
    """Return ``(promo_price_cents, promo_pct, is_promo)``.

    A promotion only counts as a real price cut when the offer flags it
    (``isPromo`` or ``isPrixBarre``) — loyalty/club rebates applied at
    checkout (``isLoyalty``) are not shelf-price promotions.
    """
    if not isinstance(promotions, list):
        return None, None, False
    for promo in promotions:
        if not isinstance(promo, dict):
            continue
        if not (promo.get("isPromo") or promo.get("isPrixBarre")):
            continue
        args = promo.get("messageArgs") or {}
        discounted = to_cents(args.get("discountedPrice"))
        initial = to_cents(args.get("initialPrice")) or base_cents
        if discounted is not None and initial is not None and discounted < initial:
            return discounted, promo_pct(initial, discounted), True
        # flagged promo without usable amounts — still a promo
        return None, None, True
    return None, None, False


def _products_from_attributes(attrs: dict, captured_at: str) -> Iterator[ParsedProduct]:
    """Build one ``ParsedProduct`` per (store) offer of a product."""
    ean = attrs.get("ean")
    name = attrs.get("title") or attrs.get("shortTitle")
    if not name:
        return

    base = {
        "enseigne": ENSEIGNE,
        "name": name,
        "captured_at": captured_at,
        "ean": ean,
        "brand": attrs.get("brand"),
        "quantity": attrs.get("packaging") or attrs.get("format"),
        "category": _deepest_category(attrs.get("categories")) or attrs.get("topCategoryName"),
        "product_url": _product_url(attrs.get("slug"), ean),
        "image_url": _first_image(attrs.get("images")),
        "enseigne_product_id": attrs.get("cdbase"),
    }

    offers = attrs.get("offers")
    emitted = False
    if isinstance(offers, dict):
        for by_store in offers.values():
            if not isinstance(by_store, dict):
                continue
            for offer in by_store.values():
                oa = offer.get("attributes") if isinstance(offer, dict) else None
                if not isinstance(oa, dict):
                    continue
                price = oa.get("price") or {}
                price_cents = to_cents(price.get("price"))
                per_measure = to_cents(price.get("perUnit"))
                unit = price.get("unitOfMeasure")
                avail = oa.get("availability") or {}
                store_ref = _store_ref_from_offer_service_id(oa.get("offerServiceId"))
                promo_price, pct, is_promo = _promo_from_offer(oa.get("promotions"), price_cents)
                emitted = True
                yield ParsedProduct(
                    **base,
                    store_ref=store_ref,
                    price_cents=price_cents,
                    price_per_measure_cents=per_measure,
                    measure_unit=unit.lower() if isinstance(unit, str) else None,
                    promo_price_cents=promo_price,
                    promo_pct=pct,
                    is_promo=is_promo,
                    available=bool(avail.get("purchasable")) if avail else None,
                )

    if not emitted:
        # product seen without any offer block — still record name/EAN
        yield ParsedProduct(**base)


def parse_products(ndjson_path: str) -> Iterator[ParsedProduct]:
    """Yield every product observation in a Carrefour capture file.

    De-duplicated on ``(ean, store_ref, price_cents)`` so the same product
    appearing on multiple captured pages is counted once.
    """
    seen: set[tuple] = set()
    n_envelopes = 0
    n_with_ean = 0
    n_emitted = 0
    n_ignored_no_name = 0
    n_ignored_dup = 0
    for record in iter_records(ndjson_path):
        sources = []
        state = extract_initial_state(record.get("response_text"))
        if state is not None:
            sources.append(state)
        if record.get("response_json") is not None:
            sources.append(record["response_json"])
        captured_at = record.get("captured_at") or ""

        for source in sources:
            for envelope in _walk_products(source):
                attrs = envelope["attributes"]
                n_envelopes += 1
                if attrs.get("ean"):
                    n_with_ean += 1
                if not (attrs.get("title") or attrs.get("shortTitle")):
                    n_ignored_no_name += 1
                for product in _products_from_attributes(attrs, captured_at):
                    key = (
                        product.ean,
                        product.store_ref,
                        product.price_cents,
                    )
                    if key in seen:
                        n_ignored_dup += 1
                        continue
                    seen.add(key)
                    n_emitted += 1
                    yield product
    logger.info(
        "carrefour: %d records produit trouvés (%d avec EAN), "
        "%d ParsedProduct extraits, %d ignorés "
        "(%d sans nom, %d doublons)",
        n_envelopes,
        n_with_ean,
        n_emitted,
        n_ignored_no_name + n_ignored_dup,
        n_ignored_no_name,
        n_ignored_dup,
    )


# --------------------------------------------------------------------------
# store extraction
# --------------------------------------------------------------------------
def _store_from_entry(entry: dict) -> ParsedStore | None:
    """Build a ``ParsedStore`` from one ``/api/eligibility/drive`` entry."""
    if not isinstance(entry, dict):
        return None
    ref = entry.get("ref")
    if not ref:
        return None
    address = entry.get("address") or {}
    coords = address.get("geoCoordinates") or {}
    return ParsedStore(
        enseigne=ENSEIGNE,
        store_ref=str(ref),
        name=entry.get("name"),
        city=address.get("city") or None,
        postal_code=address.get("postalCode") or None,
        lat=coords.get("latitude"),
        lng=coords.get("longitude"),
    )


def parse_stores(ndjson_path: str) -> Iterator[ParsedStore]:
    """Yield drive stores from a Carrefour capture file.

    De-duplicated on ``store_ref`` (last write wins via the caller's upsert).
    """
    seen: set[str] = set()
    n_emitted = 0
    for record in iter_records(ndjson_path):
        url = record.get("url") or ""
        if "/api/eligibility/drive" not in url:
            continue
        body = record.get("response_json")
        if not isinstance(body, dict):
            continue
        entries = body.get("data")
        if not isinstance(entries, list):
            continue
        for entry in entries:
            store = _store_from_entry(entry)
            if store is None or store.store_ref in seen:
                continue
            seen.add(store.store_ref)
            n_emitted += 1
            yield store
    logger.info("carrefour: %d magasin(s) drive extraits", n_emitted)
