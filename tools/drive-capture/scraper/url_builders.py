"""Pure URL / parameter builders for each enseigne × phase.

No HTTP calls are made here — each function just returns a URL string or a
dict of request parameters.  The HTTP layer (method, headers, session) is the
responsibility of the caller.

Catalog files (``rayons_catalog.json``, ``geo_reference_cities.json``) live at
``tools/drive-capture/`` — two levels above this file's package directory.
They are loaded lazily (on first call) so that importing this module never
fails even if the catalogs are not yet present on disk.
"""

from __future__ import annotations

import json
import logging
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlencode

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

_DATA_DIR = Path(__file__).parent.parent  # tools/drive-capture/


@lru_cache(maxsize=1)
def _rayons_catalog() -> dict:
    """Load and cache rayons_catalog.json."""
    path = _DATA_DIR / "rayons_catalog.json"
    with path.open(encoding="utf-8") as fh:
        return json.load(fh)


@lru_cache(maxsize=1)
def _geo_cities() -> list[dict]:
    """Load and cache geo_reference_cities.json."""
    path = _DATA_DIR / "geo_reference_cities.json"
    with path.open(encoding="utf-8") as fh:
        return json.load(fh)


# ---------------------------------------------------------------------------
# ITM (Intermarché)
# ---------------------------------------------------------------------------

_ITM_BASE = "https://www.intermarche.com"


def itm_store_locator_url(lat: float, lon: float, postal_code: str) -> str:
    """Return the ITM zone-search URL for a given location.

    Filter ``resultats`` on ``"DRIVE" in typeLivraisonOuvert`` after fetching.
    """
    params = urlencode(
        {
            "lat": lat,
            "lon": lon,
            "min": 10,
            "postalCode": postal_code,
            "r": 10000,
        }
    )
    return f"{_ITM_BASE}/api/service/pdvs/v4/pdvs/zone?{params}"


def itm_rayon_url(path: str, page: int = 1) -> str:
    """Return the ITM rayon browse URL.

    ``path`` may be an absolute path (starting with ``/``) — e.g.
    ``"/boutique/2214"`` for top-level categories — or a legacy relative
    path ``"mon-marche-frais/mon-primeur/fruits/15068"``.
    """
    if path.startswith("/"):
        base = f"{_ITM_BASE}{path}"
    else:
        base = f"{_ITM_BASE}/rayons/{path}"
    if page > 1:
        return f"{base}?page={page}"
    return base


def itm_rayon_urls_for_store() -> list[str]:
    """Return page-1 rayon URLs for all ITM rayons in the catalog."""
    rayons: list[dict] = _rayons_catalog().get("intermarche", {}).get("rayons", [])
    return [itm_rayon_url(r["path"]) for r in rayons]


# ---------------------------------------------------------------------------
# Carrefour
# ---------------------------------------------------------------------------

_CARREFOUR_BASE = "https://www.carrefour.fr"


def carrefour_store_locator_url(
    lat: float, lng: float, postal_code: str, city: str = "", page: int = 1
) -> str:
    """Return the Carrefour drive store-locator URL (/geoloc endpoint).

    The old /api/eligibility/drive endpoint was deprecated; the new one is /geoloc.
    ``modes[]=picking`` returns click-and-collect/drive stores.
    Pagination: increment page while len(stores) == limit (50).
    """
    params = urlencode(
        {
            "lat": lat,
            "lng": lng,
            "page": page,
            "limit": 50,
            "postal_code": postal_code,
            "city": city,
            "modes[]": "picking",
            "array_postal_codes[]": postal_code,
            "checkAvailability": "true",
        }
    )
    return f"{_CARREFOUR_BASE}/geoloc?{params}"


def carrefour_rayon_url(slug: str, page: int = 1) -> str:
    """Return the Carrefour category URL for *slug*.

    The endpoint ``/r/{slug}?page=N`` returns JSON:API format
    ``{"data": [...], "links": {...}, "meta": {...}}``.
    EAN is available directly in ``attributes.ean``.
    """
    base = f"{_CARREFOUR_BASE}/r/{slug}"
    if page > 1:
        return f"{base}?page={page}"
    return base


def carrefour_rayon_urls() -> list[str]:
    """Return page-1 rayon URLs for all Carrefour categories in the catalog."""
    categories: list[dict] = _rayons_catalog().get("carrefour", {}).get("rayons", [])
    return [carrefour_rayon_url(c["slug"]) for c in categories]


# ---------------------------------------------------------------------------
# Auchan
# ---------------------------------------------------------------------------

_AUCHAN_BASE = "https://www.auchan.fr"


def auchan_store_locator_url() -> str:
    """Return the Auchan drive store-listing URL (HTML SSR, no geo params)."""
    return f"{_AUCHAN_BASE}/nos-magasins?types=DRIVE"


def auchan_rayon_url(slug: str, category_id: str, page: int = 1) -> str:
    """Return the Auchan rayon HTML URL.

    ``slug`` is the path portion before ``/ca-`` in the catalog ``url`` field.
    ``category_id`` comes from ``rayons_catalog["auchan"][i]["category_id"]``.
    """
    base = f"{_AUCHAN_BASE}/{slug}/ca-{category_id}"
    if page > 1:
        return f"{base}?page={page}"
    return base


def auchan_rayon_url_from_catalog_entry(entry: dict, page: int = 1) -> str:
    """Build an Auchan rayon URL from a single rayons_catalog entry dict.

    The catalog ``url`` field looks like
    ``"/boissons-fraiches-bio-et-de-marque/ca-12345"`` — we split on ``/ca-``
    to extract the slug and category_id.
    """
    raw_url: str = entry["url"]
    # Strip a leading slash if present
    raw_url = raw_url.lstrip("/")
    if "/ca-" in raw_url:
        slug_part, cat_part = raw_url.rsplit("/ca-", 1)
    else:
        # Fallback: treat the whole thing as slug, use catalog category_id
        slug_part = raw_url
        cat_part = entry.get("category_id", "")
    return auchan_rayon_url(slug_part, cat_part, page)


def auchan_fiche_url(product_slug: str, product_id: str) -> str:
    """Return the Auchan product detail HTML URL."""
    return f"{_AUCHAN_BASE}/{product_slug}/pr-C{product_id}"


def auchan_rayon_urls() -> list[str]:
    """Return page-1 rayon URLs for all Auchan rayons in the catalog."""
    rayons: list[dict] = _rayons_catalog().get("auchan", {}).get("rayons", [])
    return [auchan_rayon_url_from_catalog_entry(r) for r in rayons]


def auchan_search_infinite_url(category_id: str, page: int) -> str:
    """Return the Auchan AJAX pagination URL for a category page > 1.

    category_id examples: "n0201", "n0601", "b202209050930"
    """
    params = urlencode({
        "categoryId": category_id,
        "page": page,
        "x-cms-page-template": "PRODUCT_LIST_PAGE_TEMPLATE",
        "x-cms-page-type": "CATEGORY",
        "x-cms-ua-device": "MOBILEFIRST",
        "x-cms-category": category_id,
    })
    return f"{_AUCHAN_BASE}/search-infinite?{params}"


# ---------------------------------------------------------------------------
# Système U
# ---------------------------------------------------------------------------

_SYSTEME_U_BASE = "https://www.coursesu.com"
_SYSTEME_U_LOCATOR_BASE = "https://www.magasins-u.com"


def systeme_u_store_locator_url(lat: float, lng: float, zip_code: str, city: str) -> str:
    """Return the Système U store-locator JSON API URL."""
    params = urlencode({
        "zip_code": zip_code,
        "city": city,
        "latitude": lat,
        "longitude": lng,
        "trust_coordinates": "true",
    })
    return f"{_SYSTEME_U_LOCATOR_BASE}/bin/servlet/apistorelocatorentities.json?{params}"


def systeme_u_rayon_url(rayon_url: str) -> str:
    """Return the Système U rayon URL.

    The full URL is already stored in
    ``rayons_catalog["systeme_u"]["rayons"][i]["url"]``.
    """
    return rayon_url


def systeme_u_rayon_urls() -> list[str]:
    """Return all Système U rayon URLs from the catalog."""
    rayons: list[dict] = _rayons_catalog().get("systeme_u", {}).get("rayons", [])
    return [r["url"] for r in rayons]


# ---------------------------------------------------------------------------
# Leclerc
# ---------------------------------------------------------------------------

_LECLERC_LOCATOR_BASE = "https://api-recherchemagasins.leclercdrive.fr"
_LECLERC_FICHE_BASE = "https://dp.leclercdrive.fr"


def leclerc_store_locator_url(
    lat: float, lng: float, postal_code: str
) -> str:
    """Return the Leclerc nearby-stores URL."""
    params = urlencode(
        {
            "latitude": lat,
            "longitude": lng,
            "postalCode": postal_code,
        }
    )
    return (
        f"{_LECLERC_LOCATOR_BASE}/API_RechercheMagasins/api/v1/MapPoint/nearby?{params}"
    )


def leclerc_infomagasin_url(store_ref: str) -> str:
    """Return the Leclerc store-info URL for a given store reference."""
    return (
        f"{_LECLERC_LOCATOR_BASE}/API_RechercheMagasins/api/v1"
        f"/pointretrait/infomagasin/drive/pointlivraison/{store_ref}"
    )


def leclerc_rayon_url(
    silo: str,
    store_ref: str,
    city: str,
    rayon_id: str,
    slug: str,
    page: int = 1,
) -> str:
    """Return the Leclerc rayon browse URL.

    Args:
        silo: numSilo from the store-locator response (e.g. ``"123"``).
        store_ref: noPL or noPR value from the store-locator.
        city: city slug used in the drive URL path.
        rayon_id: rayon identifier from rayons_catalog.
        slug: rayon slug from rayons_catalog.
        page: page number (1-indexed).
    """
    base = (
        f"https://fd{silo}-courses.leclercdrive.fr"
        f"/magasin-{store_ref}-{store_ref}-{city}"
        f"/rayon-{rayon_id}-{slug}.aspx"
    )
    if page > 1:
        return f"{base}?page={page}"
    return base


def leclerc_fiche_payload(product_id: str, store_ref: str) -> dict:
    """Return the JSON body for the Leclerc product-detail POST."""
    return {"IdProduit": product_id, "IdPDV": store_ref}


def leclerc_fiche_url() -> str:
    """Return the Leclerc product-detail POST endpoint URL."""
    return f"{_LECLERC_FICHE_BASE}/ficheproduit/FicheProduitJson.ashx"


# ---------------------------------------------------------------------------
# Monoprix
# ---------------------------------------------------------------------------

_MONOPRIX_BASE = "https://courses.monoprix.fr"


def monoprix_stores_url() -> str:
    """Return the Monoprix store-listing URL (no geo filtering needed)."""
    return (
        f"{_MONOPRIX_BASE}/api/ecomdeliverydestinations/v4/delivery-addresses"
        "?deliveryMethod=CUSTOMER_COLLECTION"
    )


def monoprix_categories_url() -> str:
    """Return the Monoprix category tree URL.

    Requires a session with the correct ``regionId`` cookie/header set.
    """
    return (
        f"{_MONOPRIX_BASE}/api/webproductpagews/v1/categories"
        "?decoration=false&categoryDepth=4"
    )


def monoprix_products_url(category_id: str) -> str:
    """Return the Monoprix first-batch product-page URL for a category UUID.

    Uses maxProductsToDecorate=300 (= maxPageSize) so every product in the page
    is fully decorated — avoids extra bulk-detail round-trips.
    Top-level category IDs return all products from their sub-tree.
    """
    params = urlencode(
        {
            "categoryId": category_id,
            "includeAdditionalPageInfo": "true",
            "maxPageSize": 300,
            "maxProductsToDecorate": 300,
            "tag": ["web", "category-item"],
        },
        doseq=True,
    )
    return f"{_MONOPRIX_BASE}/api/webproductpagews/v6/product-pages?{params}"


def monoprix_products_next_url(page_token: str) -> str:
    """Return the Monoprix paginated product URL for *page_token*."""
    params = urlencode(
        {
            "pageToken": page_token,
            "includeAdditionalPageInfo": "false",
            "maxPageSize": 300,
            "maxProductsToDecorate": 300,
            "tag": ["web", "category-item"],
        },
        doseq=True,
    )
    return f"{_MONOPRIX_BASE}/api/webproductpagews/v6/product-pages?{params}"


def monoprix_products_details_url() -> str:
    """Return the Monoprix bulk-product-detail PUT endpoint URL."""
    return f"{_MONOPRIX_BASE}/api/webproductpagews/v6/products"


def monoprix_products_details_payload(product_ids: list[str]) -> dict:
    """Return the JSON body for the Monoprix bulk product-detail PUT."""
    return {"productIds": product_ids}


def monoprix_fiche_url(mpx_id: str) -> str:
    """Return the Monoprix single-product detail URL."""
    return (
        f"{_MONOPRIX_BASE}/api/webproductpagews/v5/products/bop"
        f"?retailerProductId={mpx_id}"
    )
