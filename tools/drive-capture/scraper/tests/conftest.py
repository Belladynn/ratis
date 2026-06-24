"""Shared fixtures for the scraper test suite.

All fixture data is extracted inline from real capture files — no network
calls, no file I/O at test-run time.
"""

from __future__ import annotations

import html as _html
import json

import pytest

# ---------------------------------------------------------------------------
# ITM fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def itm_stores_json():
    """Minimal ITM pdvs/zone response with 1 DRIVE store and 1 non-DRIVE store.

    Extracted from captures/20260521_185338/www.intermarche.com.ndjson line 5.
    """
    return {
        "resultats": [
            {
                "entityCode": "07879",
                "tradeNameLabel": "INTERMARCHE",
                "modelLabel": "EXPRESS",
                "ecommerce": {
                    "typeLivraisonOuvert": ["A_DOMICILE", "DRIVE"]
                },
                "addresses": [
                    {
                        "latitude": "48.893354",
                        "longitude": "2.252723",
                        "townLabel": "Courbevoie",
                        "postCode": "92400",
                    }
                ],
            },
            {
                "entityCode": "07576",
                "tradeNameLabel": "INTERMARCHE",
                "modelLabel": "EXPRESS",
                "ecommerce": {
                    "typeLivraisonOuvert": ["A_DOMICILE", "DRIVE_PIETON"]
                },
                "addresses": [
                    {
                        "latitude": "48.912719",
                        "longitude": "2.259196",
                        "townLabel": "Bois Colombes",
                        "postCode": "92270",
                    }
                ],
            },
        ]
    }


def _build_itm_rayon_html() -> str:
    """Build a minimal ITM rayon HTML with a __next_f push block containing 2 real products.

    Extracted from captures/20260521_185338/www.intermarche.com.ndjson line 7.
    """
    # The RSC chunk must contain "path":"/rayons/..." followed within ~400 chars by "products":[...]
    products = [
        {
            "id": "188156",
            "url": "/produit/banane-bio/3250393139833",
            "informations": {
                "title": "Banane BIO",
                "packaging": "Le lot de 5 fruits",
                "brand": "Le Choix du Primeur",
                "allImages": [
                    {
                        "src": "https://cdn.intermarche.com/fr/Content/images/boitmal/produit/zoom/19518268.jpg",
                        "alt": "Banane BIO",
                    }
                ],
            },
            "prices": {
                "productPrice": {
                    "currency": "€",
                    "value": 2.49,
                    "integer": "2",
                    "decimal": "49",
                    "concatenated": "2,49€",
                }
            },
            "hasReduction": False,
        },
        {
            "id": "114941",
            "url": "/produit/jeunes-pousses-bio/3250392605070",
            "informations": {
                "title": "Jeunes pousses bio mélange de salades",
                "packaging": "le sachet de 100 g",
                "brand": "Saint Eloi, une marque Intermarché",
                "allImages": [
                    {
                        "src": "https://assets-big.cdn-mousquetaires.com/medias/domain11815/media137555/11266572.jpg",
                        "alt": "Jeunes pousses",
                    }
                ],
            },
            "prices": {
                "productPrice": {
                    "currency": "€",
                    "value": 2.09,
                    "integer": "2",
                    "decimal": "09",
                    "concatenated": "2,09€",
                }
            },
            "hasReduction": False,
        },
    ]
    # Build the RSC chunk: contains path marker + products array
    chunk = (
        '"analytics":[],"isPromo":false,"type":"CATEGORY"},'
        '"path":"/rayons/fruits-et-legumes/fruits-et-legumes-bio/7575",'
        '"products":' + json.dumps(products, ensure_ascii=False) + "}"
    )
    # Encode the chunk as a JSON string (inner value of the push array)
    inner = json.dumps(chunk)
    return f"<script>self.__next_f.push([1, {inner}])</script>"


@pytest.fixture
def itm_rayon_html():
    """Minimal ITM rayon HTML containing RSC push block with 2 real products."""
    return _build_itm_rayon_html()


# ---------------------------------------------------------------------------
# Carrefour fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def carrefour_stores_json():
    """Minimal Carrefour /api/eligibility/drive response with 2 stores.

    Extracted from captures/20260516_101440/www.carrefour.fr.ndjson line 17.
    """
    return {
        "data": [
            {
                "id": "7850",
                "name": "Market Courbevoie",
                "ref": "1323",
                "banner": "MARKET",
                "distance": "0.30",
            },
            {
                "id": "7851",
                "name": "Carrefour Express Levallois",
                "ref": "1324",
                "banner": "EXPRESS",
                "distance": "1.20",
            },
        ],
        "links": [],
        "meta": {"sideEligibilities": ["clcv", "express_delivery"]},
    }


# ---------------------------------------------------------------------------
# Système U fixtures
# ---------------------------------------------------------------------------

def _build_su_stores_html() -> str:
    """Minimal SU StoreLocator HTML with 2 DRIVE store <li> elements.

    Extracted from captures/20260521_185338/www.coursesu.com.ndjson line 64.
    """
    # Both stores have drive-option class
    store1 = (
        '<li id="20066" class="store-container" role="option"'
        ' data-city-zipcode="92400" data-city-name="Courbevoie"'
        ' data-store-id="20066" data-store-position="1.0" data-wrapper-store-details>'
        '<div class="store-title-container">'
        '<h3 class="store-name su-font-mulish" data-cs-mask data-store-name>U Express - Courbevoie </h3>'
        "</div>"
        '<div class="store-delivery-option drive-option mode-RETRAIT" data-delivery-mode="RETRAIT"></div>'
        "</li>"
    )
    store2 = (
        '<li id="20057" class="store-container" role="option"'
        ' data-city-zipcode="92800" data-city-name="Puteaux"'
        ' data-store-id="20057" data-store-position="2.0" data-wrapper-store-details>'
        '<div class="store-title-container">'
        '<h3 class="store-name su-font-mulish" data-cs-mask data-store-name>Super U - Puteaux </h3>'
        "</div>"
        '<div class="store-delivery-option drive-option mode-RETRAIT" data-delivery-mode="RETRAIT"></div>'
        "</li>"
    )
    return f'<ul id="search-result-listbox">\n{store1}\n{store2}\n</ul>'


@pytest.fixture
def su_stores_html():
    """Minimal Système U StoreLocator HTML with 2 DRIVE stores."""
    return _build_su_stores_html()


def _build_su_rayon_html() -> str:
    """Minimal SU rayon HTML with 2 product tiles.

    Extracted from captures/20260521_185338/www.coursesu.com.ndjson line 24.
    data-tc-product-tile attributes use HTML-escaped JSON.
    """
    tile1 = {
        "id": "2618631",
        "name": "Pâte à tartiner NUTELLA, 1kg",
        "EAN": "3017620425035",
        "brand": "NUTELLA",
        "price": "8.42",
        "product_cat1": "Epicerie sucrée",
        "product_cat2": "Petit déjeuner",
        "product_url_picture": "https://static.coursesu.com/images/3017620425035.png",
    }
    tile2 = {
        "id": "1843907",
        "name": "Eau gazeuse minérale BADOIT VERTE - 6x1l",
        "EAN": "3068320114460",
        "brand": "BADOIT",
        "price": "4.07",
        "product_cat1": "Boissons sans alcool",
        "product_cat2": "Eaux",
        "product_url_picture": "https://static.coursesu.com/images/3068320114460.png",
    }

    def li(tile: dict, href: str) -> str:
        encoded = _html.escape(json.dumps(tile, ensure_ascii=False), quote=True)
        return f'<li data-tc-product-tile="{encoded}"><a href="{href}">link</a></li>'

    return (
        li(tile1, "/p/pate-a-tartiner-nutella-1kg/2618631.html")
        + "\n"
        + li(tile2, "/p/eau-badoit-verte-6x1l/1843907.html")
    )


@pytest.fixture
def su_rayon_html():
    """Minimal Système U rayon HTML with 2 product tiles."""
    return _build_su_rayon_html()


# ---------------------------------------------------------------------------
# Leclerc fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def leclerc_mappoint_json():
    """Minimal Leclerc MapPoint list with 3 real stores.

    Extracted from captures/20260521_185338/api-recherchemagasins.leclercdrive.fr.ndjson line 1.
    """
    return [
        {
            "noPL": "010101",
            "noPR": "010101",
            "name": "Beynost",
            "postalCode": "01700",
            "latitude": 45.821674,
            "longitude": 4.990454,
        },
        {
            "noPL": "010111",
            "noPR": "010111",
            "name": "Rillieux-la-Pape / Caluire-et-Cuire",
            "postalCode": "69140",
            "latitude": 45.810667,
            "longitude": 4.878144,
        },
        {
            "noPL": "010201",
            "noPR": "010201",
            "name": "Oulins / Anet",
            "postalCode": "28260",
            "latitude": 48.8687522,
            "longitude": 1.4751452,
        },
    ]


def _build_leclerc_rayon_html() -> str:
    """Minimal Leclerc rayon HTML with initOptions(pnlElementProduit, {…}) containing 2 products.

    Extracted from captures/20260521_185338/fd6-courses.leclercdrive.fr.ndjson line 2.
    Products have no EAN at rayon level — only internal_id and fiche URL.
    """
    elements = [
        {
            "objElement": {
                "sType": "Produit",
                "iIdProduit": 149039,
                "sLibelleLigne1": "Chipolatas ",
                "sLibelleLigne2": "L&#39;Atelier Boucherie  X8 - 520g",
                "nrPVUnitaireTTC": 6.83,
                "sUrlVignetteProduit": "https://fd6-photos.leclercdrive.fr/image.ashx?id=2992666",
                "sUrlPageProduit": "https://fd6-courses.leclercdrive.fr/magasin-077801-077801-Acheres/fiche-produits-149039-Chipolatas-.aspx",
                "sPrixPromo": "0,00 €",
            }
        },
        {
            "objElement": {
                "sType": "Produit",
                "iIdProduit": 215307,
                "sLibelleLigne1": "Saucisses de Strasbourg",
                "sLibelleLigne2": "X8 - 320g",
                "nrPVUnitaireTTC": 3.15,
                "sUrlVignetteProduit": "https://fd6-photos.leclercdrive.fr/image.ashx?id=3812345",
                "sUrlPageProduit": "https://fd6-courses.leclercdrive.fr/magasin-077801-077801-Acheres/fiche-produits-215307-Saucisses-.aspx",
                "sPrixPromo": "0,00 €",
            }
        },
    ]
    data = {"objContenu": {"lstElements": elements}}
    return (
        "<html><body><script>"
        "initOptions('ctl00_ctl00_mainMutiUnivers_main_ctl05_pnlElementProduit', "
        + json.dumps(data, ensure_ascii=False)
        + ")</script></body></html>"
    )


@pytest.fixture
def leclerc_rayon_html():
    """Minimal Leclerc rayon HTML with 2 products in initOptions payload."""
    return _build_leclerc_rayon_html()


# ---------------------------------------------------------------------------
# Monoprix fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def monoprix_stores_json():
    """Minimal Monoprix delivery-addresses response with 2 stores.

    Extracted from captures/20260521_185338/courses.monoprix.fr.ndjson lines 21 + 34.
    """
    return {
        "deliveryAddresses": [
            {
                "deliveryDestinationId": "9861bdd0-63ae-4788-b561-8d0322cb2a71",
                "name": "75004",
                "coordinates": {"latitude": 48.852966, "longitude": 2.3499022},
                "resolvedRegionId": "235c7f87-0a09-4b04-be29-80e27ef55216",
                "postalCode": "75004",
            },
            {
                "deliveryDestinationId": "7fdd768e-65d1-431d-a490-3c8edda03505",
                "name": "92400",
                "coordinates": {"latitude": 48.90087, "longitude": 2.2548869},
                "resolvedRegionId": "235c7f87-0a09-4b04-be29-80e27ef55216",
                "postalCode": "92400",
            },
        ]
    }


@pytest.fixture
def monoprix_rayon_json():
    """Minimal Monoprix /api/webproductpagews/v6/products response with 2 products.

    Extracted from captures/20260521_185338/courses.monoprix.fr.ndjson line 54.
    NOTE: Monoprix has NO EAN at any level.  Price is a dict {"amount": "3.80", "currency": "EUR"}.
    """
    return {
        "products": [
            {
                "retailerProductId": "MPX_6813928",
                "name": "Antikal Original Spray Élimine Jusqu'à 100 % Du Calcaire 700ml",
                "brand": "Antikal",
                "price": {"amount": "3.80", "currency": "EUR"},
                "images": [
                    {
                        "src": "https://courses.monoprix.fr/images-v3/0c44253f/a1dbfa52/500x500.jpg",
                        "description": "Antikal 700ml",
                    }
                ],
            },
            {
                "retailerProductId": "MPX_4042014",
                "name": "Lu Napolitain L'Original Gâteaux au Chocolat 180g",
                "brand": "NAPOLITAIN",
                "price": {"amount": "2.09", "currency": "EUR"},
                "images": [
                    {
                        "src": "https://courses.monoprix.fr/images-v3/0c44253f/3ad8afdb/500x500.jpg",
                        "description": "Napolitain 180g",
                    }
                ],
            },
        ]
    }
