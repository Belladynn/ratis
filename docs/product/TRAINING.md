# Ratis — OCR Model Training Strategy V2

> Out of scope for V1. This document describes the strategy for training a proprietary OCR model specialized for French store receipts, built from data collected in production.

---

## Principle

In V1, PaddleOCR does the work. Each processed scan is a training example for the V2 model. The goal is a specialized, lightweight model that outperforms PaddleOCR on our specific use case.

---

## `product_knowledge` — OCR learning table

Central table that accumulates OCR corrections. Also serves as a queue for manual corrections.

```sql
product_knowledge (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    raw_ocr     TEXT NOT NULL,       -- toujours en UPPERCASE
    corrected   TEXT,                -- NULL = en attente de correction manuelle
    match_type  TEXT NOT NULL CHECK (match_type IN (
                    'sequence',  -- "POT NUTELL4 40OG" séquence complète
                    'ngram',     -- "NUTELL4 400G" sous-séquence
                    'token'      -- "NUTELL4" token isolé
                )),
    source      TEXT NOT NULL CHECK (source IN (
                    'ocr_arbitrage',   -- déduit automatiquement entre passes
                    'user_correction', -- validé par l'utilisateur via scan barcode
                    'manual'           -- pré-alimenté équipe Ratis
                )),
    confidence  FLOAT CHECK (confidence >= 0 AND confidence <= 1),  -- NULL si pas encore corrigé
    seen_count  INT NOT NULL DEFAULT 1,  -- fréquence → priorité de correction manuelle
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (raw_ocr)
)
```

**`corrected = NULL`** → token discarded (too short for fuzzy) or unresolved, awaiting manual correction.

**Manual queue:**
```sql
SELECT raw_ocr, seen_count
FROM product_knowledge
WHERE corrected IS NULL
ORDER BY seen_count DESC
LIMIT 50
```
The 50 most frequent tokens without a correction — fill regularly, immediate impact.

---

## OCR correction pipeline

All comparisons are done in **UPPERCASE** before matching.

```
scanned_name = "POT NUTELL4 40OG"
    ↓
1. Lookup séquence complète product_knowledge
   "POT NUTELL4 40OG" → pas trouvé

    ↓
2. Lookup token par token product_knowledge
   "POT"    → corrected=NULL (trop court, écarté) → garder tel quel
   "NUTELL4" → pas trouvé → étape 3
   "40OG"   → corrected="400G" ✅

    ↓
3. Pour tokens non trouvés avec len >= 6 → LIKE dans products.name
   "NUTELL4" → LIKE '%NUTELL4%' → pas trouvé → étape 4

    ↓
4. Pour tokens toujours non trouvés avec len >= 6 → fuzzy pg_trgm
   "NUTELL4" → fuzzy → "NUTELLA" (score 0.88) ✅

    ↓
5. Reconstruction string corrigée
   "POT" + "NUTELLA" + "400G" = "POT NUTELLA 400G"

    ↓
6. Save dans product_knowledge
   raw_ocr    = "POT NUTELL4 40OG"
   corrected  = "POT NUTELLA 400G"
   match_type = 'sequence'
   source     = 'ocr_arbitrage'
   confidence = min(confidences des tokens) = 0.88

    ↓
7. Pipeline matching classique sur la string corrigée
   product_observed_names → exact store-specific
       ↓
   products.name exact
       ↓
   fuzzy pg_trgm (toujours nécessaire — "POT NUTELLA 400G" ≠ "Nutella 400g")
```

**Important note:** `product_knowledge` normalizes OCR text; it does not remove the final fuzzy step. It improves the quality of the fuzzy input.

---

## Fuzzy token thresholds

| Token length | Action |
|---|---|
| < 6 characters | No fuzzy → `product_knowledge` with `corrected=NULL` + `seen_count++` |
| >= 6 characters | Fuzzy allowed with strict threshold (0.85) |

```
"POT"     → 3 chars → écarté
"LAIT"    → 4 chars → écarté
"BEURRE"  → 6 chars → fuzzy autorisé
"NUTELLA" → 7 chars → très fiable
```

**Why 6:** pg_trgm generates too few trigrams on short words — "POT" matches "HARRY POTTER" at 0.67, above the 0.65 threshold. False positives guaranteed.

**Sequence confidence = minimum of its component tokens:**
```
"POT"     source='manual'      → confidence 1.0
"NUTELL4" source='ocr_arbitrage' → confidence 0.88
"40OG"    source='ocr_arbitrage' → confidence 0.92
→ confidence séquence = min(1.0, 0.88, 0.92) = 0.88
```

---

## In-memory cache

Frequent corrections are loaded into memory at Celery worker startup:

```python
_PK_CACHE = {
    row.raw_ocr: row.corrected
    for row in db.query(ProductKnowledge)
    .filter(
        ProductKnowledge.seen_count >= cache_min_seen_count,
        ProductKnowledge.corrected.isnot(None)
    )
    .all()
}
```

**What gets cached quickly:**
- `"4OOG"` → `"400G"`, `"1OOML"` → `"100ML"` — O/0 confusion
- `"NUT3LLA"` → `"NUTELLA"`, `"HARRY'5"` → `"HARRY'S"` — frequent brands
- Full sequences from the most-scanned receipts

---

## Natural pipeline evolution

```
Phase 1 — startup:
product_observed_names → MISS
product_knowledge      → MISS (table vide)
pg_trgm fuzzy          → MATCH → alimente product_knowledge

Phase 2 — quelques semaines :
product_observed_names → MISS
product_knowledge      → MATCH ✅ (cache mémoire)
pg_trgm fuzzy          → fallback rare

Phase 3 — maturité :
product_observed_names → MATCH ✅ 80% des cas
product_knowledge      → MATCH ✅ 15% des cas (cache)
pg_trgm fuzzy          → 5% — vrais nouveaux produits uniquement
```

---

## V2 training dataset

### Collection

For each `accepted` or `failed` scan:
- The 3 preprocessed images (corrected, clahe, binarized) — 48h in R2
- The OCR result from each pass + confidence scores
- The final arbitrated result
- Ground truth when available (via `products.name` + `product_knowledge`)

### Retry pairs — hard cases annotated for free

```
Photo 1 → 3 passes divergentes → "image illisible, réessayez"
    ↓
Photo 2 (même ticket) → parsing réussi → vérité terrain connue
    ↓
Dataset : (photo_1, vérité_terrain_photo_2) = exemple gold sur cas difficile
```

`retry_of_receipt_id UUID` on `receipts` — links the retry photo to the original.
Photo 1 kept in R2 until photo 2 is processed.

### Ground truth construction

```python
def build_ground_truth(raw_ocr: str, matched_product: Product) -> str:
    tokens = raw_ocr.upper().split()
    corrected = []
    for token in tokens:
        pk = get_product_knowledge(token)
        if pk and pk.confidence >= 0.85:
            corrected.append(pk.corrected)
            continue
        if len(token) >= 6:
            best = fuzzy_match_token(token, matched_product.name.upper())
            if best.score >= 85:
                corrected.append(best.value)
                continue
        corrected.append(token)  # garder tel quel
    return " ".join(corrected)
```

**Fundamental rule:**
- `"NUT3LLA"` → OCR error → correct ✅
- `"NUT 400G"` → receipt abbreviation → keep as-is ✅

### The 3 passes in training

The V2 model is trained on all 3 preprocessed versions — same conditions as production:

```python
training_example = {
    "pass_corrected":  preprocess_corrected(image),
    "pass_clahe":      preprocess_clahe(image),
    "pass_binarized":  preprocess_binarized(image),
    "ground_truth":    build_ground_truth(raw_ocr, product)
}
```

---

## V2 model architecture

**Option A — Fine-tuned PaddleOCR**
- Lightweight: 10–50 MB after fine-tuning
- Fast: 100–300 ms per image
- Excellent on dense structured text
- Apache 2.0 ✅

**Option B — Fine-tuned Pixtral 12B**
- Understands document structure (columns, total, items)
- Potentially eliminates `_spatial_sort` and `parse_receipt`
- Slower: 2–5 s per image
- Apache 2.0 ✅

**V2 target — both in cascade:**
- Fine-tuned PaddleOCR for 80% of receipts (fast)
- Fine-tuned Pixtral for the difficult 20% (reliable)
- Async absorbs latency — the user feels no difference

---

## Manual pre-seeding before launch

Generate via agent the typical OCR variants of the 500 most frequent brands in the database:
- O → 0 : `"4OOG"`, `"1OOML"`
- E → 3 : `"NUT3LLA"`, `"DANN3TTE"`
- S → 5 : `"HARRY'5"`
- A → 4 : `"NUTELL4"`
- I → 1 : `"BR1DEL"`

Source = `'manual'`, confidence = 1.0.

---

## `ratis_settings.json` parameters

```json
"product_knowledge": {
    "cache_min_seen_count": 10,
    "correction_min_confidence": 0.85,
    "min_token_length_for_fuzzy": 6,
    "token_fuzzy_threshold": 0.85
}
```

---

## Estimated volume

```
500 users × 2.5 tickets/semaine × 52 semaines = 65 000 tickets/an
Avec 20% de taux de validation correcte = 13 000 tickets annotés en 1 an
```

More than sufficient for quality fine-tuning after 6 months of real usage.
