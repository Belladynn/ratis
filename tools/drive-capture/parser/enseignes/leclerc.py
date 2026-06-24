"""Leclerc Drive parser.

Leclerc Drive serves classic ASP.NET ``.aspx`` pages. The product data is
**not** a clean JSON API: each interactive widget is initialised by an
inline ``...widget.initOptions('<panel-id>', {<json>})`` call embedded in
the HTML. The JSON argument is well-formed JSON — we balance-scan its
braces (string-aware) to slice it out and ``json.loads`` it.

Two page kinds in a capture matter:

* **rayon pages** (``.../rayon-<id>-<slug>.aspx``) carry the shelf listing
  under the ``pnlElementProduit`` panel: ``objContenu.lstElements`` is a
  list of ``{objElement:{...}, lstEnfants:[...]}`` entries. Each
  ``objElement`` with ``sType == "Produit"`` is one product with its price
  (``nrPVUnitaireTTC``), packaging, store ref (``sNoPointLivraison``) — but
  **no EAN**, only an internal ``iIdProduit``.
* **fiche-produit pages** (``.../fiche-produits-<id>-<slug>.aspx``) carry
  the product detail under the ``pnlFicheProduit`` panel: ``objProduit``
  exposes both ``iIdProduit`` and the real ``sCodeEAN`` barcode.

So ``parse_products`` does a two-pass join:

1. read every fiche page → build an ``iIdProduit -> sCodeEAN`` map;
2. read every rayon page → emit one ``ParsedProduct`` per shelf product,
   filling ``ean`` from the map (``None`` + a log line when no fiche for
   that product was captured).

Stores come from a **sibling** capture file in the same session folder:
``api-recherchemagasins.leclercdrive.fr.ndjson`` — the ``MapPoint``
endpoint returns the full 1012-store referential as a flat JSON list.
"""

from __future__ import annotations

import html
import json
import logging
import re
from collections.abc import Iterator
from pathlib import Path

from parser.capture import iter_records
from parser.model import ParsedProduct, ParsedStore
from parser.pricing import promo_pct, to_cents

logger = logging.getLogger(__name__)

ENSEIGNE = "leclerc"

# Sibling capture file holding the store referential (MapPoint endpoint).
_STORES_FILENAME = "api-recherchemagasins.leclercdrive.fr.ndjson"

# initOptions('<panel-id>', {...}) — the panel id is matched by suffix so we
# tolerate the long ASP.NET ``ctl00_ctl00_...`` control-tree prefixes.
_RAYON_PANEL = "pnlElementProduit"
_FICHE_PANEL = "pnlFicheProduit"


# --------------------------------------------------------------------------
# embedded-JSON extraction
# --------------------------------------------------------------------------
def _initoptions_blob(html_text: str, panel_suffix: str) -> dict | None:
    """Slice the JSON argument of ``initOptions('...<panel_suffix>', {...})``.

    The assignment is followed by arbitrary script content, so we
    balance-scan the braces (string-aware) to find the exact end of the
    object. Returns ``None`` when the panel is absent or the JSON is
    unparsable.
    """
    if not html_text:
        return None
    marker = re.search(
        r"initOptions\('[^']*" + re.escape(panel_suffix) + r"',",
        html_text,
    )
    if not marker:
        return None

    start = html_text.find("{", marker.end())
    if start < 0:
        return None

    depth = 0
    in_str = False
    escaped = False
    for i in range(start, len(html_text)):
        ch = html_text[i]
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
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(html_text[start : i + 1])
                except json.JSONDecodeError:
                    return None
    return None


def _clean(value: object) -> str | None:
    """Decode HTML entities and strip whitespace from a Leclerc label."""
    if not isinstance(value, str):
        return None
    text = html.unescape(value).strip()
    # collapse internal runs of whitespace introduced by entity decoding
    text = re.sub(r"\s+", " ", text)
    return text or None


# --------------------------------------------------------------------------
# page classification
# --------------------------------------------------------------------------
def _is_rayon_page(url: str) -> bool:
    return "/rayon-" in url and url.lower().endswith(".aspx")


def _is_fiche_page(url: str) -> bool:
    return "/fiche-produits-" in url and url.lower().endswith(".aspx")


def _rayon_label(html_text: str) -> str | None:
    """Rayon name = first segment of the page ``<title>``.

    ``<title>Mon boucher | Achères | E.Leclerc DRIVE</title>`` -> ``"Mon
    boucher"``.
    """
    match = re.search(r"<title>([^<]*)</title>", html_text, re.IGNORECASE)
    if not match:
        return None
    return _clean(match.group(1).split("|")[0])


# --------------------------------------------------------------------------
# EAN map (fiche-produit pages)
# --------------------------------------------------------------------------
def _ean_map(ndjson_path: str) -> dict[int, str]:
    """Build ``{iIdProduit: sCodeEAN}`` from every captured fiche page."""
    mapping: dict[int, str] = {}
    n_pages = 0
    for record in iter_records(ndjson_path):
        url = record.get("url") or ""
        if not _is_fiche_page(url):
            continue
        blob = _initoptions_blob(record.get("response_text"), _FICHE_PANEL)
        if not blob:
            continue
        produit = blob.get("objProduit")
        if not isinstance(produit, dict):
            continue
        pid = produit.get("iIdProduit")
        ean = produit.get("sCodeEAN")
        if isinstance(pid, int) and isinstance(ean, str) and ean.strip():
            mapping[pid] = ean.strip()
            n_pages += 1
    logger.info(
        "leclerc: %d fiche-produit(s) lues -> %d EAN référencés",
        n_pages,
        len(mapping),
    )
    return mapping


# --------------------------------------------------------------------------
# product extraction (rayon pages)
# --------------------------------------------------------------------------
def _is_promo(obj_element: dict) -> bool:
    """Whether the shelf product carries a real price-cut promotion.

    A promo is signalled by a non-zero ``sPrixPromo`` or one of the
    promotion flags Leclerc sets on the element (lot incitation, anti-gaspi
    discount, BRII or TEL stickers).
    """
    promo_cents = to_cents(obj_element.get("sPrixPromo"))
    if promo_cents:
        return True
    if obj_element.get("eIncitationLot"):
        return True
    if obj_element.get("fEstAntiGaspi"):
        return True
    if obj_element.get("fEstBRIIActifEtDisponible"):
        return True
    return bool(obj_element.get("fEstVisiblePictoPromoTEL"))


def _product_from_element(
    obj_element: dict,
    rayon: str | None,
    ean_map: dict[int, str],
    captured_at: str,
) -> ParsedProduct | None:
    """Build a ``ParsedProduct`` from one rayon ``objElement``."""
    name = _clean(obj_element.get("sLibelleLigne1"))
    if not name:
        return None

    pid = obj_element.get("iIdProduit")
    price_cents = to_cents(obj_element.get("nrPVUnitaireTTC"))
    promo_cents = to_cents(obj_element.get("sPrixPromo"))
    # a "0,00 €" sPrixPromo means "no promo" — not a real zero-price offer
    if promo_cents == 0:
        promo_cents = None

    is_promo = _is_promo(obj_element)
    pct = promo_pct(price_cents, promo_cents) if promo_cents else None

    measure_unit = obj_element.get("sUniteMesureTotale")
    # eDisponibilite == 0 means available; non-zero codes mean out of stock
    dispo = obj_element.get("eDisponibilite")

    return ParsedProduct(
        enseigne=ENSEIGNE,
        name=name,
        captured_at=captured_at,
        store_ref=str(obj_element["sNoPointLivraison"]) if obj_element.get("sNoPointLivraison") else None,
        ean=ean_map.get(pid) if isinstance(pid, int) else None,
        brand=None,  # Leclerc shelf data carries no separate brand field
        quantity=_clean(obj_element.get("sLibelleLigne2")),
        category=rayon,
        price_cents=price_cents,
        price_per_measure_cents=to_cents(obj_element.get("nrPVParUniteDeMesureTTC")),
        measure_unit=measure_unit.lower() if isinstance(measure_unit, str) else None,
        promo_price_cents=promo_cents,
        promo_pct=pct,
        is_promo=is_promo,
        product_url=_clean(obj_element.get("sUrlPageProduit")),
        image_url=_clean(obj_element.get("sUrlVignetteProduit")),
        available=(dispo == 0) if isinstance(dispo, int) else None,
        enseigne_product_id=str(pid) if pid is not None else None,
    )


def _iter_rayon_elements(blob: dict) -> Iterator[dict]:
    """Yield every product ``objElement`` from a rayon panel JSON blob."""
    contenu = blob.get("objContenu")
    if not isinstance(contenu, dict):
        return
    elements = contenu.get("lstElements")
    if not isinstance(elements, list):
        return
    for entry in elements:
        if not isinstance(entry, dict):
            continue
        obj_element = entry.get("objElement")
        if isinstance(obj_element, dict) and obj_element.get("sType") == "Produit":
            yield obj_element


def parse_products(ndjson_path: str) -> Iterator[ParsedProduct]:
    """Yield every product observation in a Leclerc capture file.

    Two-pass: fiche pages first (EAN map), then rayon pages (shelf prices
    joined to that map). De-duplicated on ``(enseigne_product_id,
    store_ref, price_cents)`` so a product seen on several captured pages
    yields one observation per store.
    """
    ean_map = _ean_map(ndjson_path)

    seen: set[tuple] = set()
    n_rayon_pages = 0
    n_products = 0
    n_with_ean = 0
    n_without_ean = 0
    n_dup = 0
    n_no_name = 0

    for record in iter_records(ndjson_path):
        url = record.get("url") or ""
        if not _is_rayon_page(url):
            continue
        blob = _initoptions_blob(record.get("response_text"), _RAYON_PANEL)
        if not blob:
            continue
        n_rayon_pages += 1
        rayon = _rayon_label(record.get("response_text") or "")
        captured_at = record.get("captured_at") or ""

        for obj_element in _iter_rayon_elements(blob):
            product = _product_from_element(obj_element, rayon, ean_map, captured_at)
            if product is None:
                n_no_name += 1
                continue
            key = (product.enseigne_product_id, product.store_ref, product.price_cents)
            if key in seen:
                n_dup += 1
                continue
            seen.add(key)
            if product.ean:
                n_with_ean += 1
            else:
                n_without_ean += 1
                logger.debug(
                    "leclerc: produit %s (%s) sans fiche capturée -> ean=None",
                    product.enseigne_product_id,
                    product.name,
                )
            n_products += 1
            yield product

    logger.info(
        "leclerc: %d page(s) rayon -> %d produit(s) "
        "(%d avec EAN joint, %d sans EAN), %d doublon(s), %d sans nom ignoré(s)",
        n_rayon_pages,
        n_products,
        n_with_ean,
        n_without_ean,
        n_dup,
        n_no_name,
    )


# --------------------------------------------------------------------------
# store extraction (sibling MapPoint capture)
# --------------------------------------------------------------------------
def _store_from_mappoint(entry: dict) -> ParsedStore | None:
    """Build a ``ParsedStore`` from one ``MapPoint`` referential entry."""
    if not isinstance(entry, dict):
        return None
    ref = entry.get("noPL")
    if not ref:
        return None
    lat = entry.get("latitude")
    lng = entry.get("longitude")
    return ParsedStore(
        enseigne=ENSEIGNE,
        store_ref=str(ref),
        name=_clean(entry.get("name")),
        city=_clean(entry.get("name")),  # MapPoint name == the drive's city
        postal_code=str(entry["postalCode"]).strip() if entry.get("postalCode") else None,
        lat=float(lat) if isinstance(lat, (int, float)) else None,
        lng=float(lng) if isinstance(lng, (int, float)) else None,
    )


def parse_stores(ndjson_path: str) -> Iterator[ParsedStore]:
    """Yield Leclerc drive stores.

    The store referential lives in a **sibling** capture file
    (``api-recherchemagasins.leclercdrive.fr.ndjson``) in the same session
    folder as ``ndjson_path``. If that file is absent we log a warning and
    yield nothing rather than failing the whole run.
    """
    stores_path = Path(ndjson_path).resolve().parent / _STORES_FILENAME
    if not stores_path.exists():
        logger.warning(
            "leclerc: fichier magasins introuvable (%s) — 0 magasin extrait",
            stores_path,
        )
        return

    seen: set[str] = set()
    n_emitted = 0
    for record in iter_records(str(stores_path)):
        url = record.get("url") or ""
        if "/MapPoint" not in url:
            continue
        body = record.get("response_json")
        if not isinstance(body, list):
            continue
        for entry in body:
            store = _store_from_mappoint(entry)
            if store is None or store.store_ref in seen:
                continue
            seen.add(store.store_ref)
            n_emitted += 1
            yield store
    logger.info("leclerc: %d magasin(s) drive extraits", n_emitted)
