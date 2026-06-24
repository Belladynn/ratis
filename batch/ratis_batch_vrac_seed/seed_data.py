"""Canonical bulk-produce seed list — French market, V1.

Schema constraints (db/schema_lite.sql) :
  - ean : ``^\\d{8,14}$``
  - source = 'internal' → ean must start with '2' AND unit IS NOT NULL
  - unit ∈ {'kg', 'l', 'unit'}
  - name not empty

EAN scheme : ``2999000000NNN`` (13 digits, prefix 2 = in-store/internal per
GS1 convention, 999000000 = reserved Ratis-vrac namespace, NNN = sequential
zero-padded id 001-999).

Categories are intentionally NULL : prod ``categories`` rows are unknown at
seed time and cross-referencing them would create environment coupling.
The taxonomy is documented in ``ARCH_BATCH_VRAC_SEED.md`` for future enrich.

All entries use ``unit='kg'`` because vrac OCR lines look like
``POMMES VRAC 1.234kg 3.45€`` — the price is per kg. AVOCATS / ANANAS that
real receipts sometimes price per piece are still seeded with ``kg`` :
matching is name-driven (fuzzy), unit drives consensus normalization.
"""

from __future__ import annotations

from typing import TypedDict


class VracEntry(TypedDict):
    ean: str
    name: str
    unit: str  # always 'kg' for V1 — see module docstring
    category: str  # informative tag — NOT persisted (no category_id mapping)


# Informative high-level taxonomy — stored ONLY in this file for future
# enrichment / migration to a real categories.id mapping. Never persisted
# in the DB by this batch.
CATEGORY_FRUITS = "FRUITS"
CATEGORY_LEGUMES = "LEGUMES"
CATEGORY_EPICERIE = "EPICERIE"

_VRAC_NAMES: list[tuple[str, str]] = [
    # ── FRUITS (25) ────────────────────────────────────────────────────────
    ("POMMES GOLDEN VRAC", CATEGORY_FRUITS),
    ("POMMES GALA VRAC", CATEGORY_FRUITS),
    ("POMMES GRANNY SMITH VRAC", CATEGORY_FRUITS),
    ("POMMES PINK LADY VRAC", CATEGORY_FRUITS),
    ("POIRES CONFERENCE VRAC", CATEGORY_FRUITS),
    ("POIRES WILLIAMS VRAC", CATEGORY_FRUITS),
    ("BANANES VRAC", CATEGORY_FRUITS),
    ("BANANES BIO VRAC", CATEGORY_FRUITS),
    ("ORANGES VRAC", CATEGORY_FRUITS),
    ("CITRONS VRAC", CATEGORY_FRUITS),
    ("MANDARINES VRAC", CATEGORY_FRUITS),
    ("CLEMENTINES VRAC", CATEGORY_FRUITS),
    ("KIWI VRAC", CATEGORY_FRUITS),
    ("RAISINS BLANCS VRAC", CATEGORY_FRUITS),
    ("RAISINS NOIRS VRAC", CATEGORY_FRUITS),
    ("FRAISES VRAC", CATEGORY_FRUITS),
    ("FRAMBOISES VRAC", CATEGORY_FRUITS),
    ("MYRTILLES VRAC", CATEGORY_FRUITS),
    ("PECHES VRAC", CATEGORY_FRUITS),
    ("ABRICOTS VRAC", CATEGORY_FRUITS),
    ("NECTARINES VRAC", CATEGORY_FRUITS),
    ("CERISES VRAC", CATEGORY_FRUITS),
    ("ANANAS VRAC", CATEGORY_FRUITS),
    ("MANGUES VRAC", CATEGORY_FRUITS),
    ("AVOCATS VRAC", CATEGORY_FRUITS),
    # ── LEGUMES (28) ───────────────────────────────────────────────────────
    ("TOMATES GRAPPE VRAC", CATEGORY_LEGUMES),
    ("TOMATES CERISE VRAC", CATEGORY_LEGUMES),
    ("TOMATES COEUR DE BOEUF VRAC", CATEGORY_LEGUMES),
    ("POMMES DE TERRE CHARLOTTE VRAC", CATEGORY_LEGUMES),
    ("POMMES DE TERRE RATTE VRAC", CATEGORY_LEGUMES),
    ("POMMES DE TERRE VRAC", CATEGORY_LEGUMES),
    ("CAROTTES VRAC", CATEGORY_LEGUMES),
    ("COURGETTES VRAC", CATEGORY_LEGUMES),
    ("AUBERGINES VRAC", CATEGORY_LEGUMES),
    ("POIVRONS ROUGES VRAC", CATEGORY_LEGUMES),
    ("POIVRONS VERTS VRAC", CATEGORY_LEGUMES),
    ("POIVRONS JAUNES VRAC", CATEGORY_LEGUMES),
    ("CONCOMBRES VRAC", CATEGORY_LEGUMES),
    ("SALADE LAITUE VRAC", CATEGORY_LEGUMES),
    ("SALADE FRISEE VRAC", CATEGORY_LEGUMES),
    ("EPINARDS VRAC", CATEGORY_LEGUMES),
    ("CHOU FLEUR VRAC", CATEGORY_LEGUMES),
    ("BROCOLI VRAC", CATEGORY_LEGUMES),
    ("CHOU VERT VRAC", CATEGORY_LEGUMES),
    ("RADIS VRAC", CATEGORY_LEGUMES),
    ("NAVETS VRAC", CATEGORY_LEGUMES),
    ("BETTERAVES VRAC", CATEGORY_LEGUMES),
    ("OIGNONS ROUGES VRAC", CATEGORY_LEGUMES),
    ("OIGNONS JAUNES VRAC", CATEGORY_LEGUMES),
    ("AIL VRAC", CATEGORY_LEGUMES),
    ("ECHALOTES VRAC", CATEGORY_LEGUMES),
    ("CHAMPIGNONS DE PARIS VRAC", CATEGORY_LEGUMES),
    ("POIREAUX VRAC", CATEGORY_LEGUMES),
    # ── EPICERIE / VRACS SECS (12) ────────────────────────────────────────
    ("LENTILLES VERTES VRAC", CATEGORY_EPICERIE),
    ("LENTILLES CORAIL VRAC", CATEGORY_EPICERIE),
    ("POIS CHICHES VRAC", CATEGORY_EPICERIE),
    ("HARICOTS BLANCS VRAC", CATEGORY_EPICERIE),
    ("HARICOTS ROUGES VRAC", CATEGORY_EPICERIE),
    ("RIZ BASMATI VRAC", CATEGORY_EPICERIE),
    ("RIZ THAI VRAC", CATEGORY_EPICERIE),
    ("RIZ COMPLET VRAC", CATEGORY_EPICERIE),
    ("QUINOA VRAC", CATEGORY_EPICERIE),
    ("FLOCONS AVOINE VRAC", CATEGORY_EPICERIE),
    ("NOIX DE CAJOU VRAC", CATEGORY_EPICERIE),
    ("AMANDES VRAC", CATEGORY_EPICERIE),
]


def _make_ean(seq: int) -> str:
    """Build 13-digit EAN ``2999000000NNN``. Seq must be in 1..999."""
    if not 1 <= seq <= 999:
        raise ValueError(f"seq out of range 1..999 : {seq}")
    return f"2999000000{seq:03d}"


def build_seed_data() -> list[VracEntry]:
    """Materialize the canonical bulk-produce list with stable EANs.

    Order is the source of truth for EAN allocation : never reorder
    ``_VRAC_NAMES`` once an entry has shipped to prod, only append.
    """
    entries: list[VracEntry] = []
    for idx, (name, category) in enumerate(_VRAC_NAMES, start=1):
        entries.append(
            VracEntry(
                ean=_make_ean(idx),
                name=name,
                unit="kg",
                category=category,
            )
        )
    return entries


SEED_DATA: list[VracEntry] = build_seed_data()
