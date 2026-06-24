---
type: sub-arch
service: ratis_product_analyser
parent: ARCH_PRODUCT_ANALYSER
related: [ARCH_BATCH_OFF_SYNC]
status: production
tags: [barcode, ean, scan, product-analyser, off]
updated: 2026-04-24
---

# ratis_product_analyser — EAN Scan (barcode)

> Barcode (EAN) scan pipeline: pyzbar decoding, `products` lookup, OFF fallback if missing, returns product sheet + favourites.
> @tags: barcode ean scan pyzbar product-analyser off lookup
> @status: LIVRÉ V0
> @subs: auto

> Parent: [[ARCH_PRODUCT_ANALYSER]] · Relations: [[ARCH_BATCH_OFF_SYNC]]

> Status: ✅ Implemented.
> Branch: `main`

---

## Implementation Checklist

**Base checklist — to keep in every ARCH:**
- [x] Alembic migration created and verified
- [x] SQLAlchemy models updated
- [x] Repository — CRUD functions
- [x] Service — business logic + edge cases
- [x] Route — endpoint + error codes
- [x] Tests written (TDD — before the code)
- [x] `conftest.py` updated if new `require_env()`
- [x] `ratis_settings.json` updated if new parameters
- [x] `pg_dump > db/schema.sql` after migration
- [x] `ruff check --fix` clean
- [x] CI pipeline green

**Custom checklist — CC plans its items before coding:**
- [x] Case 2 only — unmatched scan linking
- [x] Determines match_method (fuzzy_confirmed / manual)
- [x] upsert_price_consensus after resolution

> ⚠️ One item at a time. Do not move to the next without finishing the previous one.

---

## Index

- [Endpoint](#endpoint) [L.35 - L.39]
- [Case 2 — Unmatched scan linking (receipt only)](#case-2--unmatched-scan-linking-receipt-only) [L.41 - L.90]
- [Gamification "Trésor découvert"](#gamification-trésor-découvert) [L.92 - L.94]
- [Critical rules](#critical-rules) [L.96 - L.104]
- [Implementation notes](#implementation-notes) [L.106 - L.112]

---

## Endpoint

```
POST /api/v1/scan/barcode
```

`scan_id` required — the endpoint is exclusively Case 2 (unmatched scan linking).

For the product sheet + nearby prices, use `GET /api/v1/product/{ean}`.

---

## Case 2 — Unmatched scan linking (receipt only)

The user browses their **purchase history** and sees an unrecognised line (`status='unmatched'`). They scan the barcode of the actual product to create the link.

**Eligibility:** `scan_type='receipt'` AND `status='unmatched'` only.
`electronic_label` scans have their own pipeline (price_challenges) — they do not go through here.

**Body:**
```json
{
  "ean": "3017620422003",
  "scan_id": "uuid"
}
```

**Pipeline:**
1. Lookup `products` by EAN
2. Fetch the scan — verify `scan_type='receipt'` + `status='unmatched'` + ownership
3. Determine `match_method` via `_determine_match_method` (fuzzy_confirmed / manual)
4. `resolve_scan`: updates `product_ean`, `status='accepted'`, `user_verified_at=now()`
5. `upsert_price_consensus` for this `(scan.store_id, ean)`

**Response:**
```json
{
  "product": { "ean", "name", "brand", "photo_url" },
  "resolved_scan": {
    "scan_id": "uuid",
    "scanned_name": "NUT 400g",
    "product_ean": "3017620422003",
    "match_method": "fuzzy_confirmed",
    "user_verified": true,
    "globally_verified": false
  }
}
```

### Barcode — verification status

| `user_verified` | `globally_verified` | Frontend icon | Meaning |
|---|---|---|---|
| false | false | 🟡 yellow | Uncertain fuzzy match → "Scan to confirm + points" |
| true | false | ⚪ neutral | This user confirmed via barcode |
| any | true | 🟢 green | Community-confirmed (trust_score ≥ 80%) |

- `user_verified = true`: the current scan has just been resolved (always true in Case 2)
- `globally_verified`: `price_consensus.trust_score >= consensus.globally_verified_threshold` (default 80%)

### Trust score for Case 2 scans

Scans resolved via barcode receive a reduced weight in the consensus calculation.
Parameter: `consensus.barcode_resolve_weight_factor` (default 0.5).

**TODO**: applying the factor in `ratis_core.consensus.compute_trust_score` is planned for when `user_trust_score` becomes available on the user profile. Currently Case 2 scans carry the same weight as normal OCR scans.

---

## Gamification "Trésor découvert"

Triggered if `storage_type IS NULL` or `'unmatched'` — **not implemented in V1**.

---

## Critical rules

- No OFF call in real time — local `products` table only
- `nearby_prices` centred on `user_lat/user_lng` (actual position) — not on `store_id`
- `nearby_prices` excludes the current store (`store_id`) to avoid doubling it with `local_price`
- `nearby_prices` uses `user_preferences.search_radius_km` — never hardcoded
- `scan_id` required — Case 2 endpoint only
- `scan_type='receipt'` + `status='unmatched'` required
- Free scan → `GET /api/v1/product/{ean}`

---

## Implementation notes

- **Haversine + psycopg**: `CAST(:param AS uuid)` required — `:param::uuid` is invalid (psycopg interprets `::` as the start of a second parameter). The `exclude_store_id` clause is built conditionally in Python to avoid passing NULL to a UUID-typed parameter.
- **Case 2 resolution**: `upsert_price_consensus` is called on the existing scan (the price was already in the receipt scan) — no new scan is created.
- **`user_verified_at`**: set by `resolve_scan` at the time of barcode resolution. Allows the frontend to distinguish scans confirmed by the current user from auto-matched scans.
