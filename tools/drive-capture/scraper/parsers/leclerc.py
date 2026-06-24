"""Leclerc scraper-side parsers — fdN-courses.leclercdrive.fr + api-recherchemagasins.leclercdrive.fr.

Four parse phases:

* ``parse_stores`` — JSON list from GET /API_RechercheMagasins/api/v1/MapPoint
  Full national list (~1009 stores).  numSilo NOT available here.

* ``parse_nearby_stores`` — JSON list from
  GET /API_RechercheMagasins/api/v1/MapPoint/nearby?latitude=…&longitude=…
  Same fields + numSilo → fdN cluster identifier stored in extra.silo.

* ``parse_rayon`` — HTML from GET fdN-courses.leclercdrive.fr/magasin-{ref}-{city}/rayon-{id}-{slug}.aspx
  Extracts JSON from initOptions(…pnlElementProduit…, {JSON}) inline call.
  Builds fiche_jobs for each product detail page (needed to obtain EAN).

* ``parse_fiche`` — HTML from GET .../fiche-produits-{id}-{slug}.aspx
  Extracts JSON from initOptions(…pnlFicheProduit…, {JSON}) inline call.
  Returns a single ProductResult with EAN.
"""

from __future__ import annotations

import html as _html
import json
import logging
import re

from scraper.parsers._models import ParsedResult, ProductResult, StoreResult, to_cents

logger = logging.getLogger(__name__)

# ---- initOptions extraction -------------------------------------------------
_INIT_OPTIONS_RE = re.compile(
    r"initOptions\s*\(\s*['\"]([^'\"]*)['\"][^,]*,\s*(\{)",
    re.DOTALL,
)

# ---- pagination -------------------------------------------------------------
_PAGE_LINK_RE = re.compile(r'href="([^"]*\.aspx\?(?:[^"]*&)?page=(\d+)[^"]*)"')
_TOTAL_RE = re.compile(r'(\d+)\s*(?:produit|article|résultat)', re.IGNORECASE)


def _empty() -> ParsedResult:
    return ParsedResult()


def _balanced_brace_extract(text: str, start: int) -> str | None:
    """Extract a balanced {...} JSON object starting at ``start`` in ``text``."""
    depth = 0
    i = start
    while i < len(text):
        c = text[i]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
        i += 1
    return None


def _extract_init_options(html: str, panel_hint: str) -> dict | None:
    """Extract the JSON argument from an initOptions(…, {…}) call.

    Searches for an initOptions call whose first argument contains ``panel_hint``
    (e.g. 'pnlElementProduit' or 'pnlFicheProduit'), then extracts the JSON
    object using a balanced-brace scan.
    """
    for m in _INIT_OPTIONS_RE.finditer(html):
        if panel_hint.lower() not in m.group(1).lower():
            continue
        obj_start = m.start(2)
        raw = _balanced_brace_extract(html, obj_start)
        if raw is None:
            continue
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            logger.warning("leclerc._extract_init_options: JSON error for %s — %s", panel_hint, exc)
    return None


def _parse_store_entry(entry: dict) -> StoreResult | None:
    """Build a StoreResult from one MapPoint entry."""
    try:
        store_id = str(entry.get("noPL") or entry.get("noPR") or "")
        if not store_id:
            return None

        lat: float | None = None
        lng: float | None = None
        try:
            lat = float(entry["latitude"]) if entry.get("latitude") is not None else None
            lng = float(entry["longitude"]) if entry.get("longitude") is not None else None
        except (TypeError, ValueError):
            pass

        postal_code: str | None = None
        pc = entry.get("postalCode")
        if pc is not None:
            postal_code = str(pc).zfill(5)

        extra: dict = {}
        if entry.get("numSilo") is not None:
            extra["silo"] = str(entry["numSilo"])

        return StoreResult(
            store_id=store_id,
            name=entry.get("name") or None,
            postal_code=postal_code,
            lat=lat,
            lng=lng,
            extra=extra,
        )
    except Exception as exc:
        logger.warning("leclerc._parse_store_entry: skipping entry — %s", exc)
        return None


def parse_stores(response_json: list) -> ParsedResult:
    """Parse full Leclerc store list from /API_RechercheMagasins/api/v1/MapPoint.

    Returns all stores without silo info (numSilo not available at this endpoint).
    """
    if not response_json:
        logger.debug("leclerc.parse_stores: empty response")
        return _empty()

    stores: list[StoreResult] = []
    for entry in response_json:
        if not isinstance(entry, dict):
            continue
        s = _parse_store_entry(entry)
        if s is not None:
            stores.append(s)

    logger.debug("leclerc.parse_stores: %d stores extracted", len(stores))
    return ParsedResult(stores=stores)


def parse_nearby_stores(response_json: list) -> ParsedResult:
    """Parse Leclerc nearby stores from /API_RechercheMagasins/api/v1/MapPoint/nearby.

    Same as parse_stores but includes numSilo → extra.silo (fdN cluster).
    """
    if not response_json:
        logger.debug("leclerc.parse_nearby_stores: empty response")
        return _empty()

    stores: list[StoreResult] = []
    for entry in response_json:
        if not isinstance(entry, dict):
            continue
        s = _parse_store_entry(entry)
        if s is not None:
            stores.append(s)

    logger.debug("leclerc.parse_nearby_stores: %d stores with silo info extracted", len(stores))
    return ParsedResult(stores=stores)


_SILO_RE = re.compile(r'fd(\d+)-courses')
_CITY_RE = re.compile(r'/magasin-\w+-\w+-([^/.]+)\.aspx')


def parse_infomagasin(response_json: dict) -> ParsedResult:
    """Parse Leclerc infomagasin response to extract numSilo from sUrlSiteCourses.

    Endpoint: /API_RechercheMagasins/api/v1/pointretrait/infomagasin/drive/pointlivraison/{noPL}
    Returns a StoreResult with extra={"silo": "6", "city": "acheres"}.
    """
    if not response_json:
        logger.debug("leclerc.parse_infomagasin: empty response")
        return _empty()

    entries = response_json.get("sReponse") or []
    if not entries:
        logger.debug("leclerc.parse_infomagasin: no sReponse entries")
        return _empty()

    entry = entries[0]
    try:
        store_ref = str(entry.get("sNoPR") or "").strip()
        if not store_ref:
            return _empty()

        site_url = str(entry.get("sUrlSiteCourses") or "")

        silo: str | None = None
        silo_m = _SILO_RE.search(site_url)
        if silo_m:
            silo = silo_m.group(1)

        city: str | None = None
        city_m = _CITY_RE.search(site_url)
        if city_m:
            city = city_m.group(1)

        extra: dict = {}
        if silo:
            extra["silo"] = silo
        if city:
            extra["city"] = city
        # Also store the base URL for direct rayon URL construction
        if site_url:
            extra["site_url"] = site_url

        name = (entry.get("sNomPR") or "").strip() or None
        lat = entry.get("rLatitude")
        lng = entry.get("rLongitude")

        store = StoreResult(
            store_id=store_ref,
            name=name,
            lat=float(lat) if lat is not None else None,
            lng=float(lng) if lng is not None else None,
            extra=extra,
        )
        logger.debug(
            "leclerc.parse_infomagasin: store %s silo=%s city=%s",
            store_ref, silo, city,
        )
        return ParsedResult(stores=[store])
    except Exception as exc:
        logger.warning("leclerc.parse_infomagasin: failed — %s", exc)
        return _empty()


def _parse_element_obj(obj: dict) -> ProductResult | None:
    """Build a ProductResult from one pnlElementProduit product object."""
    try:
        if obj.get("sType") != "Produit":
            return None

        name_raw = obj.get("sLibelleLigne1") or ""
        name = _html.unescape(name_raw).strip()
        if not name:
            return None

        internal_id = str(obj["iIdProduit"]) if obj.get("iIdProduit") is not None else None

        quantity_raw = obj.get("sLibelleLigne2")
        quantity = _html.unescape(quantity_raw).strip() if quantity_raw else None

        price_cents = to_cents(obj.get("nrPVUnitaireTTC"))

        promo_price_cents: int | None = None
        is_promo = False
        promo_raw = obj.get("sPrixPromo")
        promo_cents = to_cents(promo_raw)
        if promo_cents and promo_cents > 0:
            promo_price_cents = promo_cents
            is_promo = True
        if (
            obj.get("eIncitationLot")
            or obj.get("fEstAntiGaspi")
            or obj.get("fEstBRIIActifEtDisponible")
        ):
            is_promo = True

        image_url = obj.get("sUrlVignetteProduit") or None
        product_url = obj.get("sUrlPageProduit") or None

        return ProductResult(
            name=name,
            ean=None,  # not available at rayon level
            internal_id=internal_id,
            quantity=quantity or None,
            price_cents=price_cents,
            promo_price_cents=promo_price_cents,
            is_promo=is_promo,
            image_url=str(image_url) if image_url else None,
            product_url=str(product_url) if product_url else None,
        )
    except Exception as exc:
        logger.warning("leclerc._parse_element_obj: skipping product — %s", exc)
        return None


def _iter_elements(node: dict):
    """Recursively yield all objElement dicts from lstElements/lstEnfants trees."""
    for item in node.get("lstElements") or []:
        obj = item.get("objElement") or {}
        if obj:
            yield obj
        children = {"lstElements": item.get("lstEnfants") or []}
        yield from _iter_elements(children)


def parse_rayon(response_text: str) -> ParsedResult:
    """Parse Leclerc category page HTML.

    Extracts JSON from initOptions(…pnlElementProduit…, {JSON}) and iterates
    over all product elements.  Builds fiche_jobs for each product detail URL
    (needed to retrieve EAN).
    Pagination: looks for ?page=N in href links.
    """
    if not response_text:
        logger.debug("leclerc.parse_rayon: empty response")
        return _empty()

    data = _extract_init_options(response_text, "pnlElementProduit")
    if data is None:
        logger.debug("leclerc.parse_rayon: initOptions(pnlElementProduit) not found")
        return _empty()

    products: list[ProductResult] = []
    fiche_jobs: list[dict] = []

    root = data.get("objContenu") or data
    for obj in _iter_elements(root):
        p = _parse_element_obj(obj)
        if p is not None:
            products.append(p)
            if p.product_url:
                fiche_jobs.append({
                    "url": p.product_url,
                    "method": "GET",
                    "product_id": p.internal_id,
                })

    # Pagination
    next_url: str | None = None
    if products:
        page_matches = _PAGE_LINK_RE.findall(response_text)
        if page_matches:
            best = max(page_matches, key=lambda m: int(m[1]))
            next_url = best[0]

    # Total count
    total_count: int | None = None
    total_match = _TOTAL_RE.search(response_text)
    if total_match:
        try:
            total_count = int(total_match.group(1))
        except ValueError:
            pass

    logger.debug(
        "leclerc.parse_rayon: %d products, %d fiche_jobs, next_url=%s",
        len(products),
        len(fiche_jobs),
        next_url,
    )
    return ParsedResult(
        products=products,
        fiche_jobs=fiche_jobs,
        next_url=next_url,
        total_count=total_count,
    )


def parse_fiche(response_text: str) -> ParsedResult:
    """Parse Leclerc product-detail HTML.

    Extracts JSON from initOptions(…pnlFicheProduit…, {JSON}).
    Returns a single ProductResult with EAN.
    """
    if not response_text:
        logger.debug("leclerc.parse_fiche: empty response")
        return _empty()

    data = _extract_init_options(response_text, "pnlFicheProduit")
    if data is None:
        logger.debug("leclerc.parse_fiche: initOptions(pnlFicheProduit) not found")
        return _empty()

    try:
        obj = data.get("objProduit") or data
        name_raw = obj.get("sLibelleProduit") or ""
        name = _html.unescape(name_raw).strip()
        if not name:
            logger.warning("leclerc.parse_fiche: product has no libelle")
            return _empty()

        ean = str(obj.get("sCodeEAN") or "").strip() or None
        internal_id = str(obj["iIdProduit"]) if obj.get("iIdProduit") is not None else None
        price_cents = to_cents(obj.get("nrPVUnitaireTTC"))
        brand = str(obj.get("sMarque") or "").strip() or None
        image_url = str(obj.get("sUrlVignetteProduit") or "").strip() or None

        product = ProductResult(
            name=name,
            ean=ean,
            internal_id=internal_id,
            brand=brand,
            price_cents=price_cents,
            image_url=image_url,
        )
        return ParsedResult(products=[product])
    except Exception as exc:
        logger.warning("leclerc.parse_fiche: failed — %s", exc)
        return _empty()
