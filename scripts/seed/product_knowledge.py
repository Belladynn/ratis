"""Product knowledge seed — 10 OCR auto-learn samples.

Wave 5 — deferred from Wave 3. See ``ARCH_seed_test_data.md`` § Step 3
(deferred bullet) + ``TRAINING.md`` § ``product_knowledge`` for the
upstream data-flow.

Composition (10 entries) :

- **5 confirmed corrections** : ``corrected IS NOT NULL`` → these would be
  served straight from the cache by the OCR pipeline.

  - 3 × ``source='ocr_arbitrage'`` (auto-derived between OCR passes — the
    most common path in prod).
  - 2 × ``source='user_correction'`` (validated by a persona scanning a
    barcode to anchor the truth — see TRAINING.md § cycle de vie).

- **5 unconfirmed entries** : ``corrected IS NULL`` → land in the manual
  admin curation queue. These exist so the admin "Knowledge curation"
  screen has rows to render in dev.

The raw OCR strings are realistic noisy transcriptions of products that
exist in :mod:`scripts.seed.products`, so the demo dashboards link back
to product rows that actually exist.

Personas as "originators" (brief Wave 5) — encoded *implicitly* :
``ocr_knowledge`` has no user FK (correction dictionary is global, not
per-user). The narrative is :

- ``bob`` raw_ocr noise mirrors his receipt-photo OCR pipeline (lait /
  beurre / yaourt).
- ``charlie`` noise reflects his high-volume bulk scans (coca / café /
  riz / pâtes).
- ``eve`` noise reflects her e-label / manual ambiguity samples (camembert
  / sucre / brioche).

Idempotency : enforced via ``UNIQUE(raw_ocr, type)``. We SELECT for
existence before INSERT to keep the no-op fast path noise-free in logs.
"""

from __future__ import annotations

from typing import TypedDict

from ratis_core.models.product import OcrKnowledge
from sqlalchemy import select
from sqlalchemy.orm import Session


class SeedKnowledgeEntry(TypedDict):
    """Compact spec — mirrors the OcrKnowledge subset we fill in for the seed."""

    raw_ocr: str
    corrected: str | None  # None → unconfirmed → manual admin queue
    type: str  # 'product_name' for all 10 (we don't seed brand/retailer here)
    source: str  # ck_ocr_knowledge_source CHECK
    match_type: str  # ck_ocr_knowledge_match_type CHECK
    confidence: float | None
    seen_count: int
    originator: str  # informative — NOT a DB column, see module docstring


# ============================================================
# 10 curated entries — see module docstring for rationale
# ============================================================
SEED_KNOWLEDGE: list[SeedKnowledgeEntry] = [
    # ── 5 confirmed (corrected IS NOT NULL) ─────────────────────────────
    # 3 × ocr_arbitrage (auto-derived between OCR passes).
    {
        "raw_ocr": "LA1T DEMI ECREME LACTEL 1L",
        "corrected": "Lait demi-écrémé Lactel 1L",
        "type": "product_name",
        "source": "ocr_arbitrage",
        "match_type": "sequence",
        "confidence": 0.92,
        "seen_count": 14,
        "originator": "bob",
    },
    {
        "raw_ocr": "BEURRE D0UX PRESIDENT 250G",
        "corrected": "Beurre doux Président 250g",
        "type": "product_name",
        "source": "ocr_arbitrage",
        "match_type": "sequence",
        "confidence": 0.88,
        "seen_count": 9,
        "originator": "bob",
    },
    {
        "raw_ocr": "COCA C0LA 33CL",
        "corrected": "Coca-Cola 33cl canette",
        "type": "product_name",
        "source": "ocr_arbitrage",
        "match_type": "sequence",
        "confidence": 0.95,
        "seen_count": 42,
        "originator": "charlie",
    },
    # 2 × user_correction (persona scanned the barcode to anchor truth).
    {
        "raw_ocr": "CAFE M0ULU CARTE N0IRE",
        "corrected": "Café moulu Carte Noire 250g",
        "type": "product_name",
        "source": "user_correction",
        "match_type": "sequence",
        "confidence": 1.0,
        "seen_count": 7,
        "originator": "charlie",
    },
    {
        "raw_ocr": "CAMEMB. LE RUSTIQ.",
        "corrected": "Camembert Le Rustique 250g",
        "type": "product_name",
        "source": "user_correction",
        "match_type": "sequence",
        "confidence": 1.0,
        "seen_count": 3,
        "originator": "eve",
    },
    # ── 5 unconfirmed (corrected IS NULL) — admin curation queue ────────
    {
        "raw_ocr": "Y0GHRT NTRE DAN0NE X4",
        "corrected": None,
        "type": "product_name",
        "source": "ocr_arbitrage",
        "match_type": "sequence",
        "confidence": None,
        "seen_count": 2,
        "originator": "bob",
    },
    {
        "raw_ocr": "RIZ LONG GRAIN TAUREAU",
        "corrected": None,
        "type": "product_name",
        "source": "ocr_arbitrage",
        "match_type": "sequence",
        "confidence": None,
        "seen_count": 5,
        "originator": "charlie",
    },
    {
        "raw_ocr": "PATES SPGTI BARILLA N5",
        "corrected": None,
        "type": "product_name",
        "source": "ocr_arbitrage",
        "match_type": "sequence",
        "confidence": None,
        "seen_count": 4,
        "originator": "charlie",
    },
    {
        "raw_ocr": "SUCRE PDR DADDY 1KG",
        "corrected": None,
        "type": "product_name",
        "source": "ocr_arbitrage",
        "match_type": "sequence",
        "confidence": None,
        "seen_count": 1,
        "originator": "eve",
    },
    {
        "raw_ocr": "BRIOCHE TR. PASQUIER 500",
        "corrected": None,
        "type": "product_name",
        "source": "ocr_arbitrage",
        "match_type": "sequence",
        "confidence": None,
        "seen_count": 1,
        "originator": "eve",
    },
]


def _already_seeded(session: Session) -> bool:
    """Idempotency probe : if our first deterministic raw_ocr key exists, skip."""
    first_key = SEED_KNOWLEDGE[0]["raw_ocr"]
    existing = session.execute(select(OcrKnowledge.id).where(OcrKnowledge.raw_ocr == first_key).limit(1)).first()
    return existing is not None


def seed_product_knowledge(session: Session) -> None:
    """Insert 10 OCR auto-learn samples. See ARCH § Step 3 (deferred) + module docstring.

    Idempotent — re-runs short-circuit on the first deterministic key.
    """
    if _already_seeded(session):
        print("[product_knowledge] already seeded — skipping (idempotent)")
        return

    print(f"[product_knowledge] seeding {len(SEED_KNOWLEDGE)} OCR auto-learn samples…")
    for entry in SEED_KNOWLEDGE:
        session.add(
            OcrKnowledge(
                raw_ocr=entry["raw_ocr"],
                corrected=entry["corrected"],
                type=entry["type"],
                source=entry["source"],
                match_type=entry["match_type"],
                confidence=entry["confidence"],
                seen_count=entry["seen_count"],
                # entity_id intentionally NULL — products PK is `ean`, not UUID,
                # so the polymorphic entity_id stays NULL for product_name rows.
                # See OcrKnowledge model docstring.
            )
        )
    session.flush()
    n_confirmed = sum(1 for e in SEED_KNOWLEDGE if e["corrected"] is not None)
    n_unconfirmed = len(SEED_KNOWLEDGE) - n_confirmed
    print(f"[product_knowledge] done — {n_confirmed} confirmed + {n_unconfirmed} unconfirmed (manual queue)")
