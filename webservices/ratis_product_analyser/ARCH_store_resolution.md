---
type: sub-arch
service: ratis_product_analyser
parent: ARCH_PRODUCT_ANALYSER
related: [ARCH_ocr_store_detection, ARCH_BATCH_OSM_SYNC]
status: production
tags: [store-resolution, osm, overpass, matcher, product-analyser]
updated: 2026-04-24
---

# ratis_product_analyser — ARCH store resolution (cold start)

> Store resolution from a scan: local `retailer_aliases` + `stores` matcher (DA-35, replaces real-time Overpass). The `identify-store` endpoint (free-text input) is deprecated 2026-04-29 — replaced by `confirm-store` (PR-B) for anti-abuse reasons. The internal pipeline remains in place.
> @tags: store-resolution osm overpass matcher product-analyser retailer_aliases store_candidates cold-start identify-store deprecated-partiel ratis_core-geo
> @status: LIVRÉ V0
> @subs: auto

> **Note (2026-05-15)** : proximity store search now goes through PostGIS + `ratis_core.geo`. See `ARCH_geo.md`.

> Parent : [[ARCH_PRODUCT_ANALYSER]] · Relations : [[ARCH_ocr_store_detection]], [[ARCH_BATCH_OSM_SYNC]]

> Status : ✅ Implemented V1 — local matcher (DA-35), process_pending_items
> Branch : `main`
>
> ⚠️ **DEPRECATED 2026-04-29 (partial)** — the `POST /receipt/{id}/identify-store` endpoint
> has been removed (orphan, never wired on the client side). Replaced by `confirm-store`
> (PR-B, see future `ARCH_user_suggested_stores.md`). Free-text input by
> the user is abandoned for anti-abuse reasons. The rest of the pipeline
> (worker resolution, `process_pending_items`, `StoreCandidate`) remains in place.

> **DA-35 update (2026-04-22):** `worker/pipeline/osm_resolver.py` was
> removed and replaced by `services/store_matching_service.py` (local lookup
> on `retailer_aliases` + `stores`). The sections below that describe
> the real-time Overpass call reflect the old design; the
> `_try_osm_resolve` / `_resolve_store_osm` interface is preserved (name + signature)
> but now delegates to the local matcher, without any network call.

---

## Implementation checklist

**Base checklist:**
- [x] Alembic migration created and verified
- [x] SQLAlchemy models updated
- [x] Repository — CRUD functions
- [x] Service — business logic + edge cases
- [x] Route — endpoint + error codes
- [x] Tests written (TDD — before the code)
- [x] `conftest.py` updated if new `require_env()` (no new require_env needed)
- [x] `ratis_settings.json` updated if new parameters
- [x] `pg_dump > db/schema.sql` after migration
- [x] `ruff check --fix` clean
- [x] CI pipeline green

**Custom checklist:**
- [x] Migration: `receipts.pending_items JSONB`, `stores.source TEXT`
- [x] `osm_resolver.py` — real-time Overpass resolution
- [x] `receipt_task.py` — Option A (store items) + Option C (real-time OSM)
- [x] Repository — `process_pending_items()`
- [~] Route `POST /receipts/{receipt_id}/identify-store` — Option B (user suggestion) — **DEPRECATED 2026-04-29, endpoint removed (orphan), will be replaced by `confirm-store`**
- [x] User suggestion confidence scoring
- [x] `GET /receipts/{receipt_id}` — expose `store_status` + `pending_items_count`
- [x] `PROD_CHECKLIST.md` — `OSM_OVERPASS_URL` required on `ratis_product_analyser`

> ⚠️ One item at a time. Do not move to the next without finishing the current one.

---

## Index

- [Context](#context) [L.47 - L.67]
- [Tables](#tables) [L.69 - L.109]
- [Worker pipeline — Option A + C](#worker-pipeline--option-a--c) [L.111 - L.174]
- [osm_resolver.py](#osm_resolverpy) [L.176 - L.237]
- [Endpoint identify-store — Option B](#endpoint-identify-store--option-b) [L.239 - L.320]
- [process_pending_items](#process_pending_items) [L.322 - L.365]
- [Parameters](#parameters) [L.367 - L.381]
- [Rules](#rules) [L.383 - L.394]
- [Out of scope](#out-of-scope) [L.396 - L.403]

---

## Context

Read before starting:
- `CLAUDE.md`
- `KNOWN_PROBLEMS_INDEX.md`
- `DECISIONS_ACTED.md`
- `webservices/ratis_product_analyser/ARCH.md`

**Problem**: when `store_status = 'unknown'`, all OCR items are currently ignored.
Paul scans his receipt from the local bakery → 0 products recorded, 0 prices contributed.

**Solution in 3 cumulative options:**

| Option | When | What |
|---|---|---|
| **A** | Always | Store OCR items even without a store → `receipts.pending_items` |
| **C** | Worker, if sufficient signals | Look up the store on OSM in real time before storing as pending |
| **B** | App, if store still unknown | Ask the user → cross-validate vs OCR → resolve |

**Required dependencies:**
- `store_candidates` — existing table, populated by `record_candidate()` on each `store_status='unknown'`
- `OSM_OVERPASS_URL` — env var already required by `ratis_batch_osm_sync`, to be propagated to `ratis_product_analyser`

---

## Tables

### `receipts` — modified

Addition of two columns:

```sql
pending_items        JSONB       NULL  -- OCR items awaiting store resolution, NULL after resolution
user_store_hint      TEXT        NULL  -- raw user suggestion (audit trail)
```

`pending_items` format:
```json
[
  {"scanned_name": "PAIN COMPLET", "price": 180, "quantity": 1.0},
  {"scanned_name": "CROISSANT X2", "price": 240, "quantity": 2.0}
]
```

`price` in cents (CLAUDE.md convention). `quantity` float.

### `stores` — modified

Addition of one column:

```sql
source  TEXT  NOT NULL  DEFAULT 'osm'
        CHECK (source IN ('osm', 'admin', 'user_suggested'))
```

Migration: existing stores (`osm_id IS NOT NULL`) keep the DEFAULT `'osm'`. Stores created by the real-time resolver also have `source='osm'`. Stores created by a user suggestion not validated by OSM have `source='user_suggested'`.

Cashback impact: store-specific cashback remains conditional on `receipt.store_status = 'confirmed'`, which is only reached via OSM or admin — independent of `stores.source`. The `source` column is used solely for auditing and admin purposes.

---

## Worker pipeline — Option A + C

Modifications in `receipt_task.py`, block `store_status = 'unknown'`.

### Before (current)

```python
if receipt.store_id is None:
    logger.info("Receipt %s: no store — skipping item processing", receipt_id)
    finalize_receipt(...)
```

### After

```python
if receipt.store_id is None:
    # Option C — real-time OSM attempt
    if ocr_lines and signals:
        resolved_store_id = _try_osm_resolve(db, signals, pipeline_result, receipt_id)
        if resolved_store_id is not None:
            receipt.store_id = resolved_store_id
            receipt.store_status = "confirmed"
            record_fingerprints(db, resolved_store_id, signals)
            db.flush()

    if receipt.store_id is None:
        # Option A — store items for later resolution
        receipt.store_status = "unknown"
        if receipt_data and receipt_data.items:
            receipt.pending_items = [
                {
                    "scanned_name": item.scanned_name,
                    "price": int(round(Decimal(str(item.price)) * 100)),
                    "quantity": float(item.quantity),
                }
                for item in receipt_data.items
            ]
        finalize_receipt(db, receipt, total_amount=None, total_lines_detected=total_lines_detected)
        db.commit()
        return  # no scans, no cashback
```

### `_try_osm_resolve`

Local function in `receipt_task.py`, fire-and-forget wrapper:

```python
def _try_osm_resolve(
    db,
    signals: dict,
    pipeline_result: OcrPipelineResult,
    receipt_id: uuid.UUID,
) -> Optional[uuid.UUID]:
    """Attempts a real-time OSM resolution. Returns store_id or None."""
    try:
        from worker.pipeline.osm_resolver import resolve_store_realtime
        overpass_url = os.environ.get("OSM_OVERPASS_URL")
        if not overpass_url:
            return None
        return resolve_store_realtime(
            db=db,
            signals=signals,
            pass_results=pipeline_result.pass_results,
            overpass_url=overpass_url,
        )
    except Exception:
        logger.warning("Real-time OSM resolution failed for receipt %s", receipt_id)
        return None
```

`OSM_OVERPASS_URL` absent (local dev without internet) → silent skip. Never block the pipeline.

---

## osm_resolver.py

New module: `worker/pipeline/osm_resolver.py`

### `resolve_store_realtime(db, signals, pass_results, overpass_url) → Optional[uuid.UUID]`

```
1. Verify that signals contains at minimum brand + postal_code
   → if not: return None (not enough information)

2. If pass_results provided (3 OCR passes): compute inter-pass agreement
   on the postal_code extracted from each pass
   → agreement < 0.80 on at least 2 passes: return None (address too noisy)

3. Build targeted Overpass query:
   node["shop"]["name"~"{brand}",i]["addr:postcode"="{postal_code}"];
   timeout = osm_resolver_timeout (ratis_settings)

4. POST Overpass → parse JSON response
   → timeout or HTTP error: return None

5. For each returned element:
   a. Normalize via _normalize_osm_element (same logic as osm_sync)
   b. If signals["phone"] present: compare normalized phone → exact match → score 100
   c. Otherwise: fuzzy match name vs signals["brand"] → score = similarity * 80

6. Take the best score if > 60 → proceed
   → otherwise: return None

7. Upsert store in DB (same SQL ON CONFLICT (osm_id) as osm_sync._upsert_store)
   source = 'osm'
   db.flush()

8. Return store_id
```

**OSM normalization**: duplicate `_normalize_osm_element` from `osm_sync.py` into this module.
Refactoring to `ratis_core` is out of scope for V1.

**Cross-pass validation**:

```python
def _postal_agreement(pass_results: list[OcrResult]) -> bool:
    """True if at least 2 passes extract the same postal code (5 digits)."""
    postals = []
    for result in pass_results:
        for text, _ in result:
            m = re.search(r"\b(\d{5})\b", text)
            if m:
                postals.append(m.group(1))
                break
    if len(postals) < 2:
        return False
    return postals.count(postals[0]) >= 2
```

If `pass_results` absent or empty → skip validation → attempt OSM anyway (graceful degradation).

---

## Endpoint identify-store — Option B

> ⚠️ **DEPRECATED 2026-04-29** — endpoint removed (orphan, never wired on the client side).
> Replaced by `confirm-store` (PR-B, see future `ARCH_user_suggested_stores.md`).
> Section kept for historical reference of the scoring/logic but no longer reflects
> the production code. See also `DECISIONS_ACTED.md` entry from 2026-04-29.

### `POST /api/v1/receipts/{receipt_id}/identify-store`

Auth: JWT (`get_current_user`)
Ownership: `assert_owner(receipt.user_id, current_user.id)` — see KP-05.

**Request:**
```json
{ "brand": "Lidl" }
```

`brand`: free-form string, 1–100 characters.

**Response:**
```json
{
  "store_status": "confirmed" | "pending" | "rejected",
  "store_id": "uuid | null",
  "message": "store_resolved" | "store_pending_review" | "suggestion_rejected"
}
```

**Logic:**

```
1. Load the receipt (404 if not found)
2. assert_owner(receipt.user_id, current_user.id)
3. If receipt.store_status != 'unknown' → 409 receipt_already_resolved

4. Load the store_candidate linked to this receipt
   (via brand_guess / postal_code / phone stored in store_candidates)
   → If no candidate → 422 no_candidate_found

5. Calculate user suggestion confidence score:
   score = 0
   if _normalize_brand_key(brand) == _normalize_brand_key(candidate.brand_guess):
       score += 30
   if candidate.phone and matches_enseigne_phone(brand, candidate.phone):
       score += 40
   if candidate.postal_code and stores_for_brand_in_postal(db, brand, candidate.postal_code):
       score += 20
   if _normalize_brand_key(brand) in raw_header_tokens(candidate.raw_header):
       score += 10

6. If score < threshold_suggest_min (default 30) → store_status = "rejected"
   → 200 { "store_status": "rejected", ... }

7. Attempt OSM resolution with brand + postal_code from the candidate:
   osm_store_id = resolve_store_realtime(db, {brand, postal_code, phone}, overpass_url)

8. If OSM finds a store:
   receipt.store_id = osm_store_id
   receipt.store_status = "confirmed"
   receipt.user_store_hint = brand
   process_pending_items(db, receipt)
   db.commit()
   → 200 { "store_status": "confirmed", "store_id": osm_store_id }

9. If OSM fails and score >= threshold_suggest_confirm (default 40):
   Create minimal store: name=brand, brand=brand, postal_code=candidate.postal_code,
                          lat/lng=0.0 (placeholder — admin will need to complete),
                          source='user_suggested'
   receipt.store_id = new_store_id
   receipt.store_status = "pending"   ← cashback blocked
   receipt.user_store_hint = brand
   process_pending_items(db, receipt)
   db.commit()
   → 200 { "store_status": "pending", "store_id": new_store_id }

10. If OSM fails and score < threshold_suggest_confirm:
    → 200 { "store_status": "rejected", ... }
```

**Error codes:**
- `404 receipt_not_found`
- `403 not_owner`
- `409 receipt_already_resolved`
- `422 no_candidate_found`

---

## process_pending_items

Function in `scan_repository.py`:

### `process_pending_items(db, receipt) → int`

Processes items stored in `receipt.pending_items` and creates the corresponding scans.
Returns the number of scans created.

```
1. If receipt.pending_items is None or empty → return 0
2. If receipt.store_id is None → raise ValueError (precondition)
3. For each item in pending_items:
   a. match_product(db, item["scanned_name"], receipt.store_id)
   b. create_scan(db, receipt=receipt, scanned_name=..., price=item["price"],
                  quantity=item["quantity"], tva_amount=None,
                  product_ean=product_ean, match_method=match_method)
   c. If product_ean and store_status == 'confirmed': upsert_price_consensus(db, scan)
4. receipt.pending_items = None   ← cleanup
5. return count
```

**Cashback**: `process_pending_items` does not itself trigger cashback.
The caller (`identify-store` endpoint or `receipt_task`) calls
`trigger_cashback_scan` after commit, if `store_status == 'confirmed'`.

---

## `GET /api/v1/scan/receipt/{receipt_id}` — response enrichment

Add to the existing response:
```json
{
  "store_status": "confirmed" | "pending" | "unknown",
  "pending_items_count": 12
}
```

`pending_items_count` = `len(receipt.pending_items)` if not NULL, otherwise 0.
Allows the app to know whether to display the store identification screen.

---

## Parameters

Add in `ratis_settings.json` → section `store_matching`:

```json
"store_matching": {
    "threshold_auto": 80,
    "threshold_confirm": 40,
    "header_lines": 8,
    "fuzzy_address_min_similarity": 0.70,
    "barcode_min_digits": 20,
    "osm_resolver_timeout": 30,
    "osm_resolver_postal_agreement_threshold": 0.80,
    "threshold_suggest_min": 30,
    "threshold_suggest_confirm": 40
}
```

---

## Rules

- **Never block the pipeline for an unknown store** — `_try_osm_resolve` is always wrapped in a try/except.
- **`cab_per_receipt_scan` always granted** even if `store_status = 'unknown'` — only store-specific cashback is blocked.
- **`user_suggested` stores never used in `ratis_list_optimiser` routes** — filter `source != 'user_suggested'` in route queries.
- **`pending_items` cleaned up after `process_pending_items`** — never leave `pending_items` non-NULL on a receipt with `store_id IS NOT NULL`.
- **Stores created with `lat=0/lng=0` are incomplete** — a `user_suggested` store without coordinates must be flagged to the admin (PROD_CHECKLIST).
- Never call `process_pending_items` if `receipt.store_id is None` — assertion on entry.
- **`OSM_OVERPASS_URL` absent**: silent skip of all OSM resolution (no error, no WARNING log).

---

## Out of scope

- Community validation (N users identify the same store → auto-accept) — V2.
- Admin endpoint to resolve `store_candidates` manually — V2 (PROD_CHECKLIST).
- Adding lat/lng during scan (app sends GPS) — V2, requires changing the client API contract.
- `ratis_list_optimiser` — filter `source != 'user_suggested'` — to be implemented in `ratis_list_optimiser` when it is developed.
- Push notifications "Your store has been identified" — V2.
