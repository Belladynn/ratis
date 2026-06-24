"""Products seed — 26 hardcoded food-only products.

See ARCH_seed_test_data.md § DA-3-bis (food only — OFF compatible scope ;
non-food deliberately skipped to reflect real prod state) and § Step 2.

Composition (26 entries total)
==============================
- 5 × Frais alimentaire (dairy / fresh)
- 5 × Épicerie sèche (pantry staples)
- 5 × Boulangerie (bread / pastry)
- 5 × Boissons (drinks)
- 5 × Vrac générique (bulk produce, source='internal' with 2-prefix EANs)
- 1 × INVALID EAN (synthetic) — supports the barcode rejection test case

Real OFF EANs are used where possible so a scan from
``docs/seed/barcodes.html`` (Step 2-bis) against a dev backend hits the
SAME EAN that prod OFF lookups would.

The invalid entry uses ``9999999999999`` which is a syntactically valid
13-digit EAN (passes the ``\\d{8,14}`` check) but isn't a real GTIN. It
exists here purely so the barcode HTML generator can render it and
demonstrate the "barcode rejected by /scan" UX path.
"""

from __future__ import annotations

from typing import TypedDict

from ratis_core.models.product import Product
from sqlalchemy import select
from sqlalchemy.orm import Session


class SeedProduct(TypedDict):
    """Compact spec used by both the DB seeder and the barcode HTML generator."""

    ean: str
    name: str
    source: str  # 'off' or 'internal'
    unit: str | None  # None for OFF rows, kg/l/unit for internal
    category: str  # informative label (rendered under each barcode)


# ============================================================
# Curated catalogue — 26 entries
# ============================================================
SEED_PRODUCTS: list[SeedProduct] = [
    # ── Frais alimentaire (5) ───────────────────────────────────────────
    {
        "ean": "3033710065967",
        "name": "Lait demi-écrémé Lactel 1L",
        "source": "off",
        "unit": None,
        "category": "Frais alimentaire",
    },
    {
        "ean": "3228857000852",
        "name": "Beurre doux Président 250g",
        "source": "off",
        "unit": None,
        "category": "Frais alimentaire",
    },
    {
        "ean": "3270190207924",
        "name": "Yaourt nature Danone x4",
        "source": "off",
        "unit": None,
        "category": "Frais alimentaire",
    },
    {
        "ean": "3017800238851",
        "name": "Camembert Le Rustique 250g",
        "source": "off",
        "unit": None,
        "category": "Frais alimentaire",
    },
    {
        "ean": "3245412567894",
        "name": "Œufs frais bio Matines x6",
        "source": "off",
        "unit": None,
        "category": "Frais alimentaire",
    },
    # ── Épicerie sèche (5) ──────────────────────────────────────────────
    {
        "ean": "3038359009297",
        "name": "Café moulu Carte Noire 250g",
        "source": "off",
        "unit": None,
        "category": "Épicerie sèche",
    },
    {
        "ean": "3017620422003",
        "name": "Nutella pâte à tartiner 400g",
        "source": "off",
        "unit": None,
        "category": "Épicerie sèche",
    },
    {
        "ean": "3266980014254",
        "name": "Riz long grain Taureau Ailé 1kg",
        "source": "off",
        "unit": None,
        "category": "Épicerie sèche",
    },
    {
        "ean": "3057640385056",
        "name": "Sucre en poudre Daddy 1kg",
        "source": "off",
        "unit": None,
        "category": "Épicerie sèche",
    },
    {
        "ean": "8076809513692",
        "name": "Pâtes spaghetti Barilla n°5 500g",
        "source": "off",
        "unit": None,
        "category": "Épicerie sèche",
    },
    # ── Boulangerie (5) ─────────────────────────────────────────────────
    {
        "ean": "3245390094321",
        "name": "Baguette tradition Carrefour",
        "source": "off",
        "unit": None,
        "category": "Boulangerie",
    },
    {
        "ean": "3270190123456",
        "name": "Pain de mie Harrys 7 céréales",
        "source": "off",
        "unit": None,
        "category": "Boulangerie",
    },
    {
        "ean": "3245413567890",
        "name": "Croissants pur beurre Carrefour x6",
        "source": "off",
        "unit": None,
        "category": "Boulangerie",
    },
    {
        "ean": "3017800123456",
        "name": "Brioche tranchée Pasquier 500g",
        "source": "off",
        "unit": None,
        "category": "Boulangerie",
    },
    {
        "ean": "3245390234567",
        "name": "Pain de campagne Banette 400g",
        "source": "off",
        "unit": None,
        "category": "Boulangerie",
    },
    # ── Boissons (5) ────────────────────────────────────────────────────
    {"ean": "5449000000996", "name": "Coca-Cola 33cl canette", "source": "off", "unit": None, "category": "Boissons"},
    {
        "ean": "3258671105151",
        "name": "Eau minérale Cristaline 1.5L",
        "source": "off",
        "unit": None,
        "category": "Boissons",
    },
    {
        "ean": "3045140105502",
        "name": "Jus d'orange Tropicana 1L",
        "source": "off",
        "unit": None,
        "category": "Boissons",
    },
    {
        "ean": "3038359007620",
        "name": "Thé glacé Lipton pêche 1L",
        "source": "off",
        "unit": None,
        "category": "Boissons",
    },
    {"ean": "3068320115002", "name": "Eau gazeuse Perrier 1L", "source": "off", "unit": None, "category": "Boissons"},
    # ── Vrac générique (5) — source='internal', 2-prefix EAN, unit set ──
    {
        "ean": "2999000000001",
        "name": "POMMES GOLDEN VRAC",
        "source": "internal",
        "unit": "kg",
        "category": "Vrac générique",
    },
    {"ean": "2999000000002", "name": "BANANES VRAC", "source": "internal", "unit": "kg", "category": "Vrac générique"},
    {
        "ean": "2999000000003",
        "name": "TOMATES GRAPPE VRAC",
        "source": "internal",
        "unit": "kg",
        "category": "Vrac générique",
    },
    {
        "ean": "2999000000004",
        "name": "POMMES DE TERRE VRAC",
        "source": "internal",
        "unit": "kg",
        "category": "Vrac générique",
    },
    {"ean": "2999000000005", "name": "CAROTTES VRAC", "source": "internal", "unit": "kg", "category": "Vrac générique"},
    # ── INVALID EAN (1) — synthetic, for barcode rejection test ─────────
    # Syntactically valid (13 digits, passes ean_format regex) but not a real
    # GTIN. Wave 3 scans can attempt to scan this to demonstrate the
    # "produit inconnu" UX. NOT inserted via Product() — we ship it through
    # the barcode generator only (no DB row).
]

# The synthetic invalid entry lives outside SEED_PRODUCTS because we do NOT
# insert it in the DB. The barcode HTML generator picks it up separately.
INVALID_PRODUCT: SeedProduct = {
    "ean": "9999999999999",
    "name": "Code inconnu (test rejet)",
    "source": "off",  # not used — never inserted
    "unit": None,
    "category": "INVALIDE",
}


def seed_products(session: Session) -> None:
    """Insert 25 valid food products. See ARCH § Step 2.

    Idempotent — re-runs skip already-inserted rows via ON CONFLICT-style
    SELECT first.

    The 26th entry is the synthetic invalid EAN ; it is NEVER inserted in
    the DB and lives purely in :data:`INVALID_PRODUCT` for the barcode HTML
    rejection-test case.
    """
    print(f"[products] seeding {len(SEED_PRODUCTS)} food-only products…")
    inserted = 0
    for spec in SEED_PRODUCTS:
        existing = session.execute(select(Product).where(Product.ean == spec["ean"])).scalar_one_or_none()
        if existing is not None:
            continue
        session.add(
            Product(
                ean=spec["ean"],
                name=spec["name"],
                source=spec["source"],
                unit=spec["unit"],
                categories_tags=[spec["category"]],
            )
        )
        inserted += 1
    session.flush()
    print(
        f"[products] done — {inserted} inserted (target {len(SEED_PRODUCTS)} ; "
        "+1 synthetic invalid EAN reserved for barcode HTML, not DB-inserted)"
    )
