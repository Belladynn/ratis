---
type: cross-cutting
parent: ARCH_RATIS
related: [ARCH_PRODUCT_ANALYSER, ARCH_store_resolution, ARCH_BATCH_OSM_SYNC]
status: production
tags: [ocr, store-detection, receipt, fingerprint, product-analyser]
updated: 2026-04-24
---

# OCR Store Detection — ARCH

> Store detection from receipt OCR: `store_detector.py` (fingerprint + match), `OcrKnowledge`, `City`, `StoreFingerprint`, `StoreCandidate`. Internal pipeline (no public endpoint). **Note 2026-05-15**: store proximity search now via PostGIS `ratis_core.geo`.
> @tags: ocr store-detection receipt fingerprint product-analyser store_detector ocr_knowledge city store_fingerprint store_candidate ratis_core-geo
> @status: LIVRÉ V0
> @subs: auto

> **Note (2026-05-15)**: Store proximity search now goes through PostGIS + `ratis_core.geo`. See `ARCH_geo.md`.

> Parent: [[ARCH_RATIS]] · Relations: [[ARCH_PRODUCT_ANALYSER]], [[ARCH_store_resolution]], [[ARCH_BATCH_OSM_SYNC]]

> Status: implemented V1 — PR #32 merged on `feature/ocr-store-detection`
> Branch: `feature/ocr-store-detection`

---

## Implementation Checklist

**Base checklist — to keep in every ARCH:**
- [x] Alembic migration created and verified — 4 migrations (OSM fields, cities/fingerprints/candidates, ocr_knowledge rename, receipts nullable)
- [x] SQLAlchemy models updated — OcrKnowledge, City, StoreFingerprint, StoreCandidate, Store, Receipt
- [ ] Repository — CRUD functions — N/A: internal pipeline, no dedicated repository
- [x] Service — business logic + edge cases — `store_detector.py` (7 functions, 25 tests)
- [ ] Route — endpoint + error codes — N/A: internal pipeline (no public endpoint)
- [x] Tests written (TDD — before code) — 25 store_detector tests + 3 receipt_task integration
- [ ] `conftest.py` updated if new `require_env()` — N/A: no new require_env
- [x] `ratis_settings.json` updated — `store_matching` section added
- [ ] `pg_dump > db/schema.sql` after migration — ⚠️ TODO (F-3)
- [x] `ruff check --fix` clean
- [x] CI pipeline green — after detect-secrets false positive allowlist

**Custom checklist:**
- [x] `ratis_batch_osm_sync` implemented (stores source of truth)
- [x] `stores` enriched (phone, siret, osm_id, store_code, opening_hours)
- [x] `store_fingerprints` table created
- [x] `product_knowledge` renamed to `ocr_knowledge` with `type` column
- [x] Numeric normalizer (OCR_DIGIT_FIXES) implemented
- [x] Receipt header extraction (brand, postal code, address, phone, barcode prefix)
- [x] Fingerprint lookup → candidate intersection → scoring pipeline
- [x] Confidence thresholds in `ratis_settings.json`
- [x] `cities` table created and loaded (La Poste open data)
- [x] "unknown store" case handled (store_candidate)
- [ ] 3-tier pipeline (auto/confirm/unknown) — threshold_confirm implemented in detection, UX confirmation V2
- [ ] Receipt barcode reading (pyzbar) — receipt_barcode, barcode_fields JSONB, store_status, per-brand format parsing
- [ ] Migration: receipt_barcode TEXT + barcode_fields JSONB + store_status on receipts
- [ ] Module barcode_reader.py: read_receipt_barcode() + parse_receipt_barcode()
- [ ] Config barcode_formats in ratis_settings.json (Monoprix, Intermarché)
- [ ] Integration pipeline receipt_task.py: barcode → store_code → scoring
- [ ] Rescan logic: same barcode = re-process existing receipt, not rejection
- [ ] Tests barcode_reader + pipeline integration
- [x] Product EAN reading on electronic_label (pyzbar) — `read_ean_barcode` + label_task.py integration

> ⚠️ One item at a time. Do not move to the next until the previous is done.

---

## Index

- [Context](#context) [L.55 - L.80]
- [Sub-projects and order](#sub-projects-and-order) [L.82 - L.90]
- [Tables](#tables) [L.92 - L.230]
- [OSM Sync](#osm-sync) [L.232 - L.275]
- [OCR header extraction](#ocr-header-extraction) [L.277 - L.340]
- [Matching pipeline](#matching-pipeline) [L.342 - L.415]
- [Parameters](#parameters) [L.417 - L.430]
- [Rules](#rules) [L.432 - L.445]
- [Receipt barcode — V1](#receipt-barcode--v1) [L.447+]
- [Out of scope](#out-of-scope)

---

## Context

Read before starting:
- `CLAUDE.md`
- `KNOWN_PROBLEMS_INDEX.md`
- `DECISIONS_ACTED.md`
- `webservices/ratis_product_analyser/ARCH.md`

Today, `store_id` is **passed by the client** when scanning a receipt. The OCR pipeline completely ignores the receipt header (store name, address, phone). This ARCH describes how to automatically detect the store from the receipt.

**What a receipt contains (Monoprix as reference):**
```
MONOPRIX COURBEVOIE 10          ← brand + city + store number
12 RUE DE L'ABREUVOIR
92400 COURBEVOIE
Tél: 0149970970

24/06/2025 08:41  2341 31 9975 8125
234103109975250624084122        ← barcode: store_code(4) + register(3) + transaction(5) + date + time
```

**OSM provides for each store:** name, brand, address, lat/lng, phone, ref:FR:SIRET, opening_hours.

Required dependencies before starting:
- `ratis_batch_osm_sync` implemented and stores table populated
- PG indexes on `stores.phone` and `stores.siret`

---

## Sub-projects and order

**1. `ratis_batch_osm_sync`** — Prerequisite. Populates the `stores` table from OSM.
**2. OCR store detection** — Header extraction + matching pipeline (this ARCH).

Both can be coded independently but the logical order is 1 → 2.

---

## Tables

### `stores` — modified

Columns to add:

```sql
phone           TEXT                          -- ex: "0149970970" (normalized, no spaces)
siret           CHAR(14)                      -- ex: "55208329700374" (ref:FR:SIRET OSM)
osm_id          BIGINT                        -- OSM node/way id for incremental updates
store_code      TEXT                          -- brand's internal code in barcode (ex: "2341")
opening_hours   TEXT                          -- OSM format: "Mo-Sa 08:00-21:00; Su 09:00-20:00"
```

### `receipts` — modified

`store_id` changes from `NOT NULL` to `NULLABLE` to allow scans without a known store:

```sql
ALTER TABLE receipts ALTER COLUMN store_id DROP NOT NULL;
```

Added columns (barcode V1):

```sql
receipt_barcode    TEXT          -- raw barcode (pyzbar), NULL if unreadable
barcode_fields     JSONB         -- fields parsed according to brand format, NULL if format unknown
store_status       TEXT NOT NULL DEFAULT 'confirmed'
  CHECK (store_status IN ('confirmed', 'pending', 'unknown'))
  -- confirmed : auto-match (score ≥ threshold_auto) or store_id provided by the client
  -- pending   : soft-match (40-79), tentative store_id, cashback blocked
  -- unknown   : no match, store_id=NULL, cashback blocked
```

**Barcode deduplication:**
```sql
CREATE UNIQUE INDEX uq_receipts_brand_barcode
  ON receipts(receipt_barcode)
  WHERE receipt_barcode IS NOT NULL;
```

> Note: the existing `receipts_semantic_dedup_key` index remains as a safety net
> when the barcode is not readable. The real dedup target is `(brand, receipt_barcode)` —
> for now we simplify with a unique on `receipt_barcode` alone since two different brands
> will not have the same barcode in practice (distinct store_code prefix).

**Rescan:** same barcode = same physical receipt → re-process existing receipt (UPDATE),
no new INSERT. Prevents double-credit but allows OCR correction.

Indexes to create:
```sql
CREATE UNIQUE INDEX uq_stores_phone  ON stores(phone)     WHERE phone IS NOT NULL;
CREATE UNIQUE INDEX uq_stores_siret  ON stores(siret)     WHERE siret IS NOT NULL;
CREATE UNIQUE INDEX uq_stores_osm_id ON stores(osm_id)    WHERE osm_id IS NOT NULL;
CREATE INDEX        ix_stores_brand  ON stores(brand);
CREATE INDEX        ix_stores_postal ON stores(postal_code);
```

---

### `store_fingerprints` — created

Self-learning system: every confirmed signal is recorded as a fingerprint.
Analogous to `observed_names` for products.

```sql
id              UUID PRIMARY KEY DEFAULT gen_random_uuid()
store_id        UUID NOT NULL REFERENCES stores(id) ON DELETE CASCADE
signal_type     TEXT NOT NULL
  -- CHECK signal_type IN (
  --   'phone',          -- "0149970970"
  --   'store_code',     -- "MONOPRIX:2341"
  --   'barcode_prefix', -- "23410310" (store_code + register, first 8 digits)
  --   'brand_postal',   -- "MONOPRIX:92400"
  --   'brand_postal_num'-- "MONOPRIX:92400:10" (with store number)
  -- )
signal_value    TEXT NOT NULL
confirmed_count INTEGER NOT NULL DEFAULT 1
created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()

UNIQUE (signal_type, signal_value)
```

---

### `ocr_knowledge` — renamed from `product_knowledge`

Generalization: `product_knowledge` becomes `ocr_knowledge` with a `type` column.

```sql
-- Table rename + type column added
ALTER TABLE product_knowledge RENAME TO ocr_knowledge;
ALTER TABLE ocr_knowledge ADD COLUMN type TEXT NOT NULL DEFAULT 'product_name';
ALTER TABLE ocr_knowledge ADD CONSTRAINT ck_ocr_knowledge_type
  CHECK (type IN ('product_name', 'brand_name', 'store_header', 'address_token'));
```

Examples of new entries:
| type | raw | corrected |
|---|---|---|
| `store_header` | `MNPRIX` | `MONOPRIX` |
| `brand_name` | `CRFOUR MKT` | `CARREFOUR MARKET` |
| `address_token` | `R DE LABR` | `RUE DE L ABREUVOIR` |
| `product_name` | `SKYR BRE` | `SKYR A BOIRE` | ← existing |

The `ratis_core/knowledge.py` module adapts with a `type=` parameter for filtering.

---

### `store_candidates` — created

Unrecognized stores surfaced by the pipeline, awaiting validation.

```sql
id              UUID PRIMARY KEY DEFAULT gen_random_uuid()
raw_header      TEXT NOT NULL           -- raw text extracted from the OCR header
brand_guess     TEXT                    -- best brand hypothesis
address_guess   TEXT
postal_code     TEXT
phone           TEXT
occurrence_count INTEGER NOT NULL DEFAULT 1
status          TEXT NOT NULL DEFAULT 'pending'
  -- CHECK status IN ('pending', 'matched', 'ignored')
matched_store_id UUID REFERENCES stores(id) ON DELETE SET NULL
created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
```

---

### `cities` — created

Postal code → canonical city name reference table.
Used as a cross-validation pivot: the OCR postal code is reliable (strict 5 digits), the OCR city name is noisy. We ignore the OCR city rendering and derive it from this table.

```sql
postal_code     TEXT NOT NULL
city_name       TEXT NOT NULL           -- canonical name (ex: "COURBEVOIE")
department      TEXT                    -- ex: "92"
country_code    TEXT NOT NULL DEFAULT 'FR'

PRIMARY KEY (postal_code, city_name)    -- one postal code can cover multiple municipalities
```

Index:
```sql
CREATE INDEX ix_cities_postal ON cities(postal_code);
```

**Data source:**
- France: La Poste open data (~36,000 municipalities, ~50,000 rows with multiple postal codes) — `https://datanova.laposte.fr`
- Initial load: one-shot SQL script or Alembic migration with `COPY`
- Incremental update: `ratis_batch_osm_sync` populates in passing via `addr:postcode` + `addr:city` OSM (upsert on `(postal_code, city_name)`)

**Usage in the pipeline:**
```python
# After header extraction: postal_code="92400", city_raw="C0URBE\/OIIE"
canonical = db.query(City).filter(
    City.postal_code == postal_code,
    City.country_code == country_code,
).first()
# → city_name="COURBEVOIE" (reliable, OCR rendering ignored)
# → signal brand_postal ("MONOPRIX:92400") confirmed by valid postal code
```

A postal code not found in `cities` → unreliable postal signal, do not score.

---

## OSM Sync

### `ratis_batch_osm_sync` — implementation

Overpass query for all food retailers in France:

```
[out:json][timeout:120];
area["ISO3166-1"="FR"][admin_level=2] -> .fr;
(
  node["shop"~"supermarket|convenience|bakery|butcher|greengrocer"]["name"](area.fr);
  way["shop"~"supermarket|convenience|bakery|butcher|greengrocer"]["name"](area.fr);
);
out body;
```

OSM tags mapped to `stores`:

| OSM Tag | stores column |
|---|---|
| `name` | `name` |
| `brand` | `brand` |
| `addr:housenumber` + `addr:street` | `address` |
| `addr:city` | `city` |
| `addr:postcode` | `postal_code` |
| `phone` / `contact:phone` | `phone` (normalized) |
| `ref:FR:SIRET` | `siret` |
| `opening_hours` | `opening_hours` |
| node/way id | `osm_id` |
| lat/lon | `lat` / `lng` |

Upsert on `osm_id` — incremental updates possible.
Phone normalization: apply OCR_DIGIT_FIXES + strip non-digit → format `0XXXXXXXXX`.

**Populating `cities` in passing:** for each OSM node processed, if `addr:postcode` and `addr:city` are present, upsert into `cities(postal_code, city_name, department, country_code)`. `department` = first 2 digits of postal code (except overseas territories). Marginal cost, no additional query.

---

## OCR Header Extraction

### Universal numeric normalization — `normalize_numeric(text)`

Hardcoded substitution map — known and predictable OCR letter/digit confusions:

```python
OCR_DIGIT_FIXES = {
    'O': '0', 'o': '0',   # O → zero
    'I': '1', 'l': '1',   # I/l → one
    'Z': '2',              # Z → two
    'A': '4',              # A → four
    'S': '5',              # S → five
    'G': '6', 'g': '6',   # G → six
    'B': '8',              # B → eight
}

def normalize_numeric(text: str) -> str:
    """Strip separators then correct OCR letter/digit confusions."""
    cleaned = re.sub(r'[\s.\-/()]', '', text)
    return ''.join(OCR_DIGIT_FIXES.get(c, c) for c in cleaned)
```

Applied **before** any matching on: store_code, barcode, any purely numeric field.

---

### Phone normalization — `normalize_phone(text, country_code="FR")`

Phone deserves a dedicated function: formats vary by country and spaces between pairs are common (e.g. Intermarché: `01 49 97 09 70`).

**V1 — France only. The `country_code` parameter is set now for future internationalization (UK, DE, ES... formats differ). Do not implement multi-country in V1.**

```python
def normalize_phone(text: str, country_code: str = "FR") -> str | None:
    # 1. Strip separators (spaces, dashes, dots, parentheses)
    stripped = re.sub(r'[\s\-\./()]', '', text)

    # 2. Normalize international prefix
    #    +33XXXXXXXXX → 0XXXXXXXXX  (France)
    #    ⚠️ To adapt per country_code in V2 (+44 → 0 UK, etc.)
    if country_code == "FR":
        stripped = re.sub(r'^\+33', '0', stripped)
        stripped = re.sub(r'^0033', '0', stripped)

    # 3. Apply OCR_DIGIT_FIXES
    digits = ''.join(OCR_DIGIT_FIXES.get(c, c) for c in stripped)

    # 4. Validate format (V1 = France: 0[1-9] + 8 digits)
    #    ⚠️ Validation pattern must be parameterized by country_code in V2
    if country_code == "FR" and re.fullmatch(r'0[1-9]\d{8}', digits):
        return digits
    return None
```

Examples:

| Raw OCR | After strip | After fix | Valid FR |
|---|---|---|---|
| `0149970970` | same | same | ✅ |
| `01 49 97 09 70` | `0149970970` | same | ✅ |
| `O1 A9 97 O9 7O` | `O1A997O97O` | `0149970970` | ✅ |
| `+33 1 49 97 09 70` | `+33149970970` → `0149970970` | same | ✅ |
| `0033 1 49 97 09 70` | `00331497...` → `0149970970` | same | ✅ |

**⚠️ Internationalization note (PROD_CHECKLIST):** address formats and postal codes are also country-dependent (FR: 5 digits, UK: alphanumeric, DE: 5 different digits, etc.). In V1, the header extraction regexes are hardcoded for France. Plan a `country_code` parameter on `extract_store_header()` from the design stage to avoid a full rewrite in V2.

---

### Extraction from the receipt header

The header is typically the **first 5–8 lines** of the receipt (before the first product line).

**Sequential rule-based classifier — each line is classified in priority order:**

| Priority | Type | Detection | Example |
|---|---|---|---|
| 1 | Phone (labeled) | `[Tt][e3é][l1][éeè]?[:.]\s*(.+)` | `Tél: 01 49 97 09 70` |
| 2 | Postal code + city | `\b(\d{5})\b\s+(\w.+)` | `92400 COURBEVOIE` |
| 3 | Address | digit(s) + `\b(RUE\|BD\|AV\|ALL\|PL\|IMP\|RES)\b` | `12 RUE DE L'ABREUVOIR` |
| 4 | Phone (unlabeled) | OCR-tolerant 10 numeric chars pattern | `O1A997O970` |
| 5 | Brand / retailer | uppercase ≥ 4 chars, no price, no address | `MONOPRIX COURBEVOIE 10` |
| 6 | Store code | first 4 digits of barcode (line ≥ 20 digits) | `2341` from `234103...` |

**⚠️ V1 France:** address patterns (RUE, BD, AV...) and postal code format `\d{5}` are hardcoded for FR. Parameterize by `country_code` in V2.

After classification, each signal goes through its dedicated normalization function:
- `phone` → `normalize_phone(value, country_code="FR")`
- `store_code`, `barcode` → `normalize_numeric(value)`
- `brand`, `address` → cleanup via `ocr_knowledge` (type `store_header`, `brand_name`, `address_token`)

The OCR header goes through 3 passes (same arbitration as the receipt body).
Signals are extracted on each pass, then consolidated by majority vote.

---

## Matching Pipeline

### Overview

```
extract_store_signals(ocr_header)
    → {phone, store_code, brand, postal_code, address}
    → normalize_phone() on phone, normalize_numeric() on store_code and barcode

lookup_fingerprints(signals)                          ← O(1), priority
    → store_id if exact match                         → DONE, confidence=HIGH

candidate_intersection(signals)                       ← if no fingerprint
    brand → set_A (index stores.brand)
    postal_code → set_B (index stores.postal_code)
    phone → set_C (direct lookup)
    address_fuzzy → set_D (pg_trgm)
    → intersection + scoring

score_result(intersection)
    → score ≥ THRESHOLD_AUTO  : auto-match
    → score ≥ THRESHOLD_CONFIRM : store_candidate (awaiting confirmation)
    → score < THRESHOLD_CONFIRM : store_candidate (unknown)

on_match_confirmed(store_id, signals)                ← after each validated match
    → INSERT/UPDATE store_fingerprints for all extracted signals
```

### Signal scoring

| Signal | Points |
|---|---|
| phone exact (after normalize_numeric) | 80 |
| store_code exact (`brand:code`) | 70 |
| brand + postal_code | 50 |
| address fuzzy (pg_trgm word_similarity ≥ 0.7) | 40 |
| brand alone | 20 |
| barcode_prefix (8 digits) | 60 if known in fingerprint |

Thresholds in `ratis_settings.json`:
```json
"store_matching": {
    "threshold_auto":    80,
    "threshold_confirm": 40
}
```

### Integration into the receipt pipeline

Today: client sends mandatory `store_id`.
After this feature: `store_id` becomes **optional**. If absent:

```
POST /scan/receipt  (without store_id)
→ worker process_receipt()
→ extract_store_signals(header)
→ match_store(signals)
    → auto-match  : store_id injected, pipeline continues normally
    → confirm     : receipt set to "store_pending" status, client notification
    → unknown     : receipt set to "store_unknown" status, INSERT store_candidates
```

---

## Parameters

Add to `ratis_settings.json`:

```json
"store_matching": {
    "threshold_auto": 80,
    "threshold_confirm": 40,
    "header_lines": 8,
    "fuzzy_address_min_similarity": 0.70,
    "barcode_min_digits": 20
},
"barcode_formats": {
    "intermarche": { "length": 24, "fields": [...] },
    "monoprix":    { "length": 24, "fields": [...] }
},
"osm_sync": {
    "shop_types": ["supermarket", "convenience", "bakery", "butcher", "greengrocer"],
    "country_code": "FR",
    "overpass_timeout": 120
}
```

---

## Rules

- `normalize_phone()` for phone numbers, `normalize_numeric()` for store_code and barcode — never match on raw OCR
- `store_fingerprints` is the source of truth for fast matches — always consult before fuzzy
- A fingerprint is only recorded after **confirmation** of the match (auto or user)
- `store_id` provided by the client → skip detection, but still record fingerprints extracted from the header if confidence is sufficient (passive learning)
- Never create a store directly from an unconfirmed detection — go through `store_candidates`
- `store_candidates.occurrence_count` increments if the same unknown store is encountered again (same brand + postal code) — priority signal for admin review
- `ocr_knowledge` type `store_header`/`brand_name` fed the same way as `product_name`: unknown tokens → manual review

---

## Receipt Barcode — V1

> **Status:** design finalized, not implemented. Depends on `pyzbar`.
> **Priority:** primary store detection method. OCR header is the fallback.

### Problem addressed

The barcode printed on a receipt contains structured data encoded
by the retailer: store_code, register number, date/time, transaction number.
This data is **more reliable than OCR** (no noise, fixed format), but
requires reading the barcode as a barcode (pyzbar) rather than doing
line-by-line OCR on it.

**Benefits:**
1. **Transactional deduplication** — unique raw barcode per physical receipt
2. **store_code directly extracted** → +70 points in scoring (strong signal)
3. **No OCR noise** on date/time/transaction fields
4. **Safe rescan** — same barcode = same receipt → re-process, not duplicate

### Schema

```sql
-- On receipts (migration)
receipt_barcode    TEXT          -- raw barcode (pyzbar), NULL if unreadable
barcode_fields     JSONB         -- fields parsed according to brand format, NULL if format unknown
store_status       TEXT NOT NULL DEFAULT 'confirmed'
  CHECK (store_status IN ('confirmed', 'pending', 'unknown'))

-- Dedup index
CREATE UNIQUE INDEX uq_receipts_brand_barcode
  ON receipts(receipt_barcode) WHERE receipt_barcode IS NOT NULL;
```

### Known formats per brand

Retailers do not encode the same fields in the same order, but all
encode the same fundamental information. The format must be reverse-engineered per brand.

Configuration in `ratis_settings.json` → section `barcode_formats`:

```json
"barcode_formats": {
  "intermarche": {
    "length": 24,
    "fields": [
      {"name": "date",       "start": 0,  "end": 8,  "format": "YYYYMMDD"},
      {"name": "time",       "start": 8,  "end": 12, "format": "HHMM"},
      {"name": "tx_id",      "start": 12, "end": 16},
      {"name": "caisse",     "start": 16, "end": 19},
      {"name": "store_code", "start": 19, "end": 24}
    ]
  },
  "monoprix": {
    "length": 24,
    "fields": [
      {"name": "store_code", "start": 0,  "end": 4},
      {"name": "caisse",     "start": 4,  "end": 7},
      {"name": "tx_id",      "start": 7,  "end": 12},
      {"name": "date",       "start": 12, "end": 18, "format": "YYMMDD"},
      {"name": "time",       "start": 18, "end": 24, "format": "HHMMSS"}
    ]
  }
}
```

#### Intermarché — `202603270904002200207879`

```
20260327 → date        (8) : YYYYMMDD (27/03/2026)
0904     → time        (4) : HHMM (09:04)
0022     → tx_id       (4) : transaction number
002      → caisse      (3) : register number
07879    → store_code  (5) : store number
```

**barcode_fields stored:**
```json
{"date": "20260327", "time": "0904", "tx_id": "0022", "caisse": "002", "store_code": "07879"}
```

#### Monoprix — `234100109106250407120518`

```
2341   → store_code   (4) : store identifier in the Monoprix IS
001    → caisse       (3) : register number
09106  → tx_id        (5) : transaction number
250407 → date         (6) : YYMMDD (07/04/2025)
120518 → time         (6) : HHMMSS (12:05:18)
```

**barcode_fields stored:**
```json
{"store_code": "2341", "caisse": "001", "tx_id": "09106", "date": "250407", "time": "120518"}
```

> **Empirical approach:** each brand has its own encoding. Collect ~10 receipts
> per brand and verify the consistency of date/time fields with the OCR header.
> When the format is unknown, we still store the raw barcode (dedup + future learning).

### Barcode reading — `barcode_reader.py`

```python
from pyzbar import pyzbar  # type: ignore[import-untyped]

def read_receipt_barcode(image: np.ndarray) -> str | None:
    """Reads the first barcode ≥ barcode_min_digits in the image.
    Filters product EAN13 (13 digits) and small QR codes.
    Returns raw data or None."""
    min_digits = _CFG.get("barcode_min_digits", 20)
    codes = pyzbar.decode(image)
    for code in codes:
        data = code.data.decode("utf-8", errors="ignore")
        if len(data) >= min_digits and data.isdigit():
            return data
    return None

def parse_receipt_barcode(raw: str, brand: str | None) -> dict | None:
    """Parses the barcode according to the known brand format in barcode_formats config.
    Returns None if brand unknown or format not defined → graceful degradation."""
    if brand is None:
        return None
    brand_key = brand.lower().replace("é", "e").replace(" ", "_")
    fmt = _BARCODE_FORMATS.get(brand_key)
    if fmt is None or len(raw) != fmt["length"]:
        return None
    return {f["name"]: raw[f["start"]:f["end"]] for f in fmt["fields"]}
```

### Rescan logic

**Same barcode = same physical receipt → re-process, not rejection.**

```
POST /scan/receipt (image containing an already known barcode)
→ read_receipt_barcode(image) → "234100109106250407120518"
→ SELECT id FROM receipts WHERE receipt_barcode = ? → existing receipt_id
→ re-process existing receipt (UPDATE, not INSERT)
→ linked scans recalculated, previous cashback revoked if store_id changes
→ new cashback if conditions met
```

The raw barcode serves as a natural key to find the existing receipt.
If the barcode is new → normal INSERT.
If unreadable → fallback on existing `receipts_semantic_dedup_key`.

### store_status

| Value | Condition | store_id | Cashback |
|---|---|---|---|
| `confirmed` | auto-match (score ≥ 80) or store_id provided by the client | NOT NULL | ✅ |
| `pending` | soft-match (score 40-79), tentative store_id | NOT NULL | ❌ blocked |
| `unknown` | no match found | NULL | ❌ blocked |

**Transition `pending` → `confirmed`:** the user confirms/corrects the store
via the app (notification "needs your attention"). Triggers fingerprint recording
and cashback unblocking.

**Transition `unknown` → `confirmed`:** the user indicates the store manually
via the app. Same trigger.

### Pipeline integration (receipt_task.py)

```
1. Download image
2. read_receipt_barcode(image)              ← NEW: before OCR
   → raw_barcode (or None)
3. Look up existing receipt by barcode      ← RESCAN check
   → if found: re-process mode (UPDATE)
   → otherwise: INSERT new receipt
4. OCR multi-pass
5. Extract brand from OCR header
6. parse_receipt_barcode(raw, brand)        ← NEW: field extraction
   → barcode_fields JSONB + store_code signal
7. detect_store(ocr_lines, db, store_code_from_barcode)
   → store_code from barcode = +70 point signal in scoring
8. Persist receipt_barcode, barcode_fields, store_status
9. Normal continuation (items, cashback guard, etc.)
```

**Graceful degradation:** if pyzbar fails or format unknown,
the pipeline continues with existing OCR signals. Never blocking.
The raw barcode is stored even if the format is not parsed (dedup + future learning).

### Product EAN reading on electronic_label — V1 (second step)

An `electronic_label` scan is a photo of an ESL on a shelf. These labels
**always display an EAN13 barcode** below the price. Pyzbar can read this
code directly from the photo → the product `ean` is obtained without going through
name OCR.

- **Complete bypass of product OCR** — more reliable, 0 ambiguity
- **No fuzzy matching** — exact EAN → direct lookup in `products`
- **Maximum confidence score** — primary source, no interpretation

**Implementation:** in `electronic_label_task.py`, before text OCR,
attempt `pyzbar.decode(image)` and filter EAN13 (13 digits). If found,
short-circuit the product matching OCR pipeline.

```python
def try_read_ean_from_image(image: np.ndarray) -> str | None:
    """Attempts to read an EAN13 from a label photo. None if absent."""
    for code in pyzbar.decode(image):
        data = code.data.decode("utf-8", errors="ignore")
        if len(data) == 13 and data.isdigit():
            return data
    return None
```

---

## Out of Scope

- User confirmation on the app side (UX to define in a client ARCH — red dot notification)
- Admin review of `store_candidates` (admin interface to define)
- Matching by SIRET from the receipt (unreliable — cf. brainstorm, the SIRET visible on the receipt is a CB terminal ID, not the legal SIRET)
- Geolocalization via photo EXIF (V2)
- Opening hours as a validation signal (V2, requires complete OSM sync)
- ESL EAN reading (pyzbar) — V1 second step (see Product EAN reading section above)
