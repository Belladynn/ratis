---
type: sub-arch
service: ratis_product_analyser
parent: ARCH_PRODUCT_ANALYSER
related: [ARCH_name_resolution_consensus, ARCH_receipt_pipeline, ARCH_consensus, ARCH_OCR_LLM_BRIDGE]
status: planned
tags: [consensus, name-resolution, cross-retailer, esl, retailer, source-of-truth, pipeline-v3]
business_domain: pricing
rgpd_concern: false
updated: 2026-05-02
---

# Cross-Retailer Consensus + ESL elevated as source-of-truth — ARCH

> Extension of the NRC (Name Resolution Consensus): promotion of `(label) → product_ean` cross-store (not per-store) for V1 scale, and elevation of the ESL (electronic shelf label) as the product-in-store source-of-truth. Plan blocks A-H.
> @tags: consensus name-resolution cross-retailer esl retailer source-of-truth pipeline-v3 product_name_resolutions verified planned-2026-05
> @status: PLANIFIÉ
> @subs: auto

> Sub-ARCH of [[ARCH_name_resolution_consensus]] (NRC). Also read: [[ARCH_receipt_pipeline]] § Phase 3 matcher, [[ARCH_consensus]] (price), [[ARCH_OCR_LLM_BRIDGE]].

> Status: 📋 **Planned — no code yet.** This ARCH formalizes the product decision made during the 2026-05-02 brainstorm (orchestrator + product owner). Implementation in blocks A → H (see plan below).

---

## Genesis

The NRC alpha test (`ARCH_name_resolution_consensus.md`, blocks A-F merged) validated the crowdsourced consensus mechanics `(store_id, normalized_label) → product_ean`. However, **two structural limitations** emerged:

1. **Per-store does not scale in V1.** With 3 distinct users required per `(store, label)`, reaching `verified` requires 3 distinct users to scan the same product in **the same physical store**. For a neighbourhood Intermarché with 50-200 active users in V1, a given product will take weeks, not hours, to be promoted. Yet the promised product experience is "scan your receipt, immediately see green names".

2. **The ESL (electronic shelf label) is under-used.** Today a label scan triggers `worker/label_task.py`, which reads an EAN via pyzbar then OCR fallback. The result feeds `price_consensus` (price) but **never writes to `product_name_resolutions`** (historical gap: the gap was not documented but is visible in the code — `label_task.py` never references `record_resolution`). Yet the ESL is the most reliable source of product-in-store identity that the retailer itself exposes.

**Product decision (validated by user-product-owner on 2026-05-02)** :

> **Name consensus is per retailer (chain), not per store (physical location). ESLs are elevated to source-of-truth status: a user who scans an electronic shelf label feeds a separate ESL ledger, with a reduced quorum threshold (2 distinct users instead of 3). A receipt label that EXACTLY matches a `verified` ESL label is auto-matched (cross-source exact).**

This ARCH establishes the contract for this redesign. The ledger remains append-only; no purge logic is introduced. The store remains persisted for audit purposes but is removed from the consensus aggregation key.

---

## Implementation plan by block

| Block | Description | Dependencies | Status |
|---|---|---|---|
| **A — schema + migration** | ALTER `product_name_resolutions`: ADD `source_type ENUM('receipt','esl')`, ADD `retailer_id UUID FK`. Extend `match_method` CHECK with `'esl'` + `'cross_source_esl_exact'`. Migrate the UNIQUE `(scan_id, normalized_label)` → `(scan_id, source_type, normalized_label)`. Index `(retailer_id, source_type, normalized_label)` + GIN trgm on `normalized_label`. Trigger `fn_sync_pnr_retailer_id` that denorms `retailer_id` from `stores` on INSERT. Update `ratis_settings.json` § `name_resolution_consensus` (`min_distinct_users: 3` unified V1, add `validation_methods_receipt`/`_esl`). | — | ✅ V1 |
| **B — extended read-only repos** | `get_consensus_for_label(retailer_id, source_type, normalized_label)` (signature change). `find_fuzzy_verified_consensus` (new, retailer-wide pg_trgm). `was_ever_verified` adapted. `list_divergent_labels` adapted. Helper `resolve_retailer_id(db, store_id) -> UUID \| None`. | A | ✅ V1 |
| **C — pipeline_v3 matcher cascade refactored** | Redesign of the `pipeline/match.py` cascade: adding the exact consensus stage `(retailer_id, 'receipt', label)`, fuzzy stage `(retailer_id, 'receipt')`. Wire the `_consensus_step` helper on `retailer_id` instead of `store_id`. Resolve `retailer_id` via `resolve_retailer_id(db, store_id)`. TDD tests for each stage. (Stage 7a cross-source = V2.) | B | ✅ V1 |
| **D — ESL → ledger writes (historical gap A)** | Wire `record_resolution` in `worker/label_task.py` after `pyzbar` or `OCR EAN+checksum` match. `source_type='esl'`, `match_method='esl'`, `weight=1`. Idempotent via `ON CONFLICT (scan_id, source_type, normalized_label) DO NOTHING`. TDD tests: ledger row written after label match, skip if no resolvable retailer_id. | A, B | ✅ V1 |
| **E — ESL pipeline V1 (pyzbar + OCR checksum + partial EAN recovery batch only)** | Strengthen `worker/pipeline/label_parser.py`: if pyzbar misses, OCR-scan for pattern `\d{13}` + `validate_ean13_checksum()` (E.1). E.2 batch only: partial EAN recovery via Levenshtein ≤ 2 + name similarity > 0.75 + filter retailer_id, run by batch I (see below). If all MISS → unresolved direct (NO on-the-fly fuzzy fallback in V1). Helper `validate_ean13_checksum(ean: str) -> bool` (pure, in `ratis_core.utils.ean_checksum`). TDD tests checksum + label_parser EAN extraction. | — (parallel to A-D) | ✅ V1 (E.1 + E.2 batch only) |
| **F — admin UI updates** | NRC block D mini-UI updated: NRC queue displays `retailer` columns (instead of `store`) + `source_type` filter (receipt/esl/all). Detail page: timeline + resolutions table broken down by source_type. `services/name_resolution_admin_service.py` adapted (retailer-based signature). JSON endpoints `/api/v1/admin/name-resolutions/*`: query params `retailer_slug` + `source_type`. | A, B | 🔁 V2 |
| **G — frontend mobile ESL burst mode** | `ratis_client/app/(tabs)/scan.tsx`: continuous ESL scan UI (burst mode). Bump `label.batch_max_images` (settings) from 10 → 30. `useLabelBatch` hook adapted for contiguous batching without camera re-mount. Jest tests for the batch flow. | E (V1 ESL pipeline validated and stable) | 🔁 V2 |
| **H — V2 backlog** | Stage 7a cross-source ticket↔ESL exact match. Trust score weight bonus (elite trust>=95% + 100+ scans → weight=2 or 3). User-validated cross-pollination. On-the-fly partial EAN recovery (beyond batch I). See § Out of scope V1 — V2 Backlog for the exhaustive list. | A-E + I stable, alpha data | 🔁 V2 backlog |
| **I — nightly reconciliation batch** | Nightly cron that re-sweeps `unresolved` scans, applies partial EAN recovery (E.2), retroactively resolves when consensus emerges, feeds `ocr_knowledge` automatically. Retroactive CAB + gratitude-driven push notification. | C, D, E | ✅ V1 |

> **Reminder R24**: one block at a time. Do not start block B before A is merged + pg_dump is up to date. Blocks E and G are parallelizable (E does not touch the ledger; G only depends on E being stable).

---

## Index

- [Genesis](#genesis)
- [Implementation plan by block](#implementation-plan-by-block)
- [Principles](#principles)
- [DB Schema](#db-schema)
- [Matcher cascade (post cross-retailer redesign)](#matcher-cascade-post-cross-retailer-redesign)
- [ESL flow — step by step](#esl-flow--step-by-step)
- [Derived state semantics](#derived-state-semantics)
- [Cross-source matching (receipt ↔ ESL)](#cross-source-matching-receipt--esl)
- [Weights and thresholds](#weights-and-thresholds)
- [Data migration](#data-migration)
- [Parameters `ratis_settings.json`](#parameters-ratis_settingsjson)
- [Append-only philosophy](#append-only-philosophy)
- [Out of scope V1](#out-of-scope-v1)
- [Block details](#block-details)
- [Open questions — acted decisions](#open-questions--acted-decisions)
- [Glossary](#glossary)

---

## Principles

Three guiding principles:

1. **Labels are per retailer, prices are per store, EANs are the global pivot.** At Intermarché, a yoghurt is labelled `"YAOURT NATURE 4X125G"` everywhere in France; the price may differ between Intermarché Lyon-7e and Intermarché Marseille-10e. This is the natural division: `(retailer, label)` → EAN, `(store, ean)` → price.

2. **ESLs are a distinct source from the receipt.** An electronic shelf label is *the official product identity as the retailer communicates it to the consumer*. The receipt is *the identity as printed by the till* — often abbreviated to fit in 24 characters. The two signals are not interchangeable; a separate consensus per `source_type` keeps them orthogonal and enables strict cross-source matching (see § Cross-source matching).

3. **`store_id` remains persisted for audit.** The ledger keeps `store_id` on every INSERT to be able to reconstruct "where the user was when they validated". But `store_id` **no longer enters the aggregation key** — it becomes a provenance column, not a computation dimension.

---

## Principles — Gratitude-driven notifications

> Ratis notifies to **give**, never to **demand**. The app respects the user's time.

**Gratitude notification (Ratis)** :
- "🎉 Your scan has been identified — here are 50 CAB"
- "Your receipt from 5 days ago is now matched. Thank you for your contribution."
- "You unlocked a consensus — 200 CAB bonus"

**Guilt notification (forbidden in Ratis)** :
- ❌ "You haven't scanned in 3 days"
- ❌ "Your friends are saving without you"
- ❌ "Come back, we miss you"

Domino effect linked to cross-retailer consensus: a single user who resolves a consensus (admin seed via ESL burst mode or manual_admin) can trigger **dozens of retroactive notifications** for all users who have already scanned that label. Free user engagement, a brand that respects its users = marketing differentiator.

---

## DB Schema

### Table `product_name_resolutions` (extended by block A)

```sql
-- Current state (ARCH_name_resolution_consensus block A):
-- product_name_resolutions (
--   id UUID PK,
--   scan_id UUID FK CASCADE,
--   user_id UUID FK,
--   store_id UUID FK,
--   normalized_label TEXT,
--   product_ean TEXT,
--   match_method TEXT CHECK ('barcode' | 'manual_admin' | 'fuzzy_pending' | 'observed_name'),
--   weight_override INT NULL,         -- anti-fraud V1 NRC
--   resolved_at TIMESTAMPTZ
-- )
-- UNIQUE (scan_id, normalized_label)
-- INDEX (store_id, normalized_label)

-- Block A change-set:
ALTER TABLE product_name_resolutions
  ADD COLUMN source_type TEXT NOT NULL DEFAULT 'receipt'
    CHECK (source_type IN ('receipt', 'esl')),
  ADD COLUMN retailer_id UUID NULL
    REFERENCES retailers(id) ON DELETE RESTRICT;
-- Note: `retailer_id` is nullable to absorb historical rows where the
-- store does not (yet) have a retailer_id (alpha case). In production, the trigger
-- below fills it as soon as `stores.retailer_id` is known. The matcher
-- ignores rows with retailer_id NULL (filter `WHERE retailer_id IS NOT NULL`).

-- Extend the match_method CHECK constraint (additive):
ALTER TABLE product_name_resolutions DROP CONSTRAINT IF EXISTS pnr_match_method_check;
ALTER TABLE product_name_resolutions ADD CONSTRAINT pnr_match_method_check
  CHECK (match_method IN (
    'barcode', 'manual_admin', 'fuzzy_pending', 'observed_name',
    'esl', 'cross_source_esl_exact'
  ));

-- Migrate the UNIQUE: we accept 1 receipt row + 1 esl row per (scan_id, label).
DROP INDEX IF EXISTS idx_pnr_scan_label;
CREATE UNIQUE INDEX idx_pnr_scan_source_label
  ON product_name_resolutions (scan_id, source_type, normalized_label);

-- Consensus aggregation index (hot path of the matcher):
CREATE INDEX idx_pnr_retailer_source_label
  ON product_name_resolutions (retailer_id, source_type, normalized_label)
  WHERE retailer_id IS NOT NULL;

-- GIN trgm index for retailer-wide fuzzy (Q4 V1, avoids latent debt):
CREATE INDEX idx_pnr_norm_label_trgm
  ON product_name_resolutions
  USING GIN (normalized_label gin_trgm_ops)
  WHERE retailer_id IS NOT NULL;

-- Trigger BEFORE INSERT/UPDATE OF store_id: denorm retailer_id from stores.
CREATE OR REPLACE FUNCTION fn_sync_pnr_retailer_id()
RETURNS TRIGGER AS $$
BEGIN
  IF NEW.store_id IS NOT NULL AND NEW.retailer_id IS NULL THEN
    NEW.retailer_id := (SELECT retailer_id FROM stores WHERE id = NEW.store_id);
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_pnr_sync_retailer_id ON product_name_resolutions;
CREATE TRIGGER trg_pnr_sync_retailer_id
  BEFORE INSERT OR UPDATE OF store_id ON product_name_resolutions
  FOR EACH ROW EXECUTE FUNCTION fn_sync_pnr_retailer_id();
```

### Schema decisions

- **`source_type` NOT NULL DEFAULT `'receipt'`**: existing rows (alpha) are by construction receipt resolutions. The DEFAULT avoids an explicit backfill.
- **`retailer_id` nullable**: avoids blocking the migration if a store still has a `retailer_id IS NULL` (e.g. user-suggested store not yet validated). The matcher filters these rows. This is intentional: a scan with no resolvable retailer_id has no business entering the retailer-wide consensus.
- **Trigger `fn_sync_pnr_retailer_id` rather than a batch backfill**: the service always writes `store_id`, never `retailer_id` directly. A BEFORE INSERT/UPDATE trigger guarantees consistency without touching application code. The trigger cost is negligible (one PK SELECT). One-time backfill of legacy data via UPDATE within the migration (see § Data migration).
- **Extended UNIQUE** `(scan_id, source_type, normalized_label)`: a scan can produce 1 `receipt` row (normal case) AND 1 `esl` row (rare, but possible for a label scan downstream that reaffirms a label already observed on receipt by the same user). We never write 2 rows for the same `(scan_id, source_type, label)`.
- **The old index `(store_id, normalized_label)` is kept as-is** (audit / admin queries per store, not dropped). Negligible storage cost, no application hot path via this index post-redesign.
- **GIN trgm for retailer-wide fuzzy (Q4 V1)**: the GIN trgm index is created in block A to avoid latent debt. Negligible cost (~25 MB per retailer × 36 retailers ≈ 900 MB, +1-5ms per INSERT). Benefit: retailer-wide fuzzy remains fast even at 100k+ rows. V1 decision acted to avoid revisiting in 1 year.

### Illustrated trigger flow

```text
Application code:
  INSERT INTO product_name_resolutions
    (scan_id, store_id, source_type, normalized_label, product_ean,
     user_id, match_method, resolved_at)
  VALUES (...)

Trigger fn_sync_pnr_retailer_id (BEFORE):
  IF retailer_id IS NULL AND store_id IS NOT NULL:
    SELECT retailer_id FROM stores WHERE id = NEW.store_id
    → NEW.retailer_id

Stored row:
  (scan_id, store_id, retailer_id, source_type, normalized_label, ...)
```

---

## Matcher cascade (post cross-retailer redesign)

### Prerequisite: retailer detection already resolved

The retailer of a scan is resolved **upstream** by existing components (confirmed by audit):
- **Receipts**: `worker/pipeline/store_detector.py::extract_store_signals` reads the OCR header (lines 1-8) and matches against `retailers` (36 FR seeded) + `retailer_aliases`. → store_id resolved → `stores.retailer_id` known.
- **ESL**: GPS geo-match → store_id resolved → `stores.retailer_id` known (OSM batch populated via brand tag).
- **User-suggested stores not yet validated**: `stores.retailer_id IS NULL`. The matcher filters these rows (see Q5 cascade fallback).

The cross-retailer consensus block **simply consumes `stores.retailer_id`** via JOIN. No new retailer detection to code.

> Implemented by block C in `worker/pipeline/match.py`. Stages 1-3 and 4-5 already exist; block C adds stage 7a and **changes the aggregation key** of consensus stages from `store_id` to `retailer_id`.

### For a receipt scan (`source_type='receipt'`)

```text
ParsedItem (scanned_name + store_id + retailer_id resolved)
    │
    ▼
┌─ 1. BARCODE STRICT ────────────────────────────────────────────┐
│  If parsed_item.barcode present → product_by_ean lookup.       │
│  In practice: MISS expected (receipt OCR does not read         │
│  physical product barcodes — only the receipt barcode          │
│  itself, already handled elsewhere).                           │
└────────────────────────────────────────────────────────────────┘
    │
    ▼
┌─ 2. KNOWLEDGE CURATED ─────────────────────────────────────────┐
│  product_by_knowledge(normalized_label) — validated cache,      │
│  fed by closed-loop (auto-learn). Hit → matched/               │
│  knowledge.                                                    │
└────────────────────────────────────────────────────────────────┘
    │
    ▼
┌─ 3. NORMALIZE ─────────────────────────────────────────────────┐
│  cleaned_label = normalize_text(scanned_name) via              │
│  ocr_knowledge (auto-learned OCR corrections, stopwords).      │
└────────────────────────────────────────────────────────────────┘
    │
    ▼
┌─ 4. CONSENSUS EXACT (RECEIPT) ─────────────────────────────────┐
│  get_consensus_for_label(                                       │
│      retailer_id=parsed.retailer_id,                            │
│      source_type='receipt',                                     │
│      normalized_label=cleaned_label                             │
│  ) → state == VERIFIED ? matched/consensus_match                │
│  → state == PENDING/CONTROVERSE/UNVERIFIED ? continue           │
└────────────────────────────────────────────────────────────────┘
    │
    ▼
┌─ 5. CONSENSUS FUZZY (RECEIPT) ─────────────────────────────────┐
│  find_fuzzy_verified_consensus(                                 │
│      retailer_id=parsed.retailer_id,                            │
│      source_type='receipt',                                     │
│      query_label=cleaned_label,                                 │
│      len_diff_max=2,                                            │
│      similarity_min=0.80                                        │
│  ) → returns matched VERIFIED label OR None                     │
│  Hit → matched/fuzzy_consensus                                  │
└────────────────────────────────────────────────────────────────┘
    │
    ▼
┌─ 6. (Legacy stages products.name + strict fuzzy — UNCHANGED) ──┐
│  (See ARCH_name_resolution_consensus § Matcher cascade.)       │
│  Note block C: these stages remain as a safety net but         │
│  only write `fuzzy_pending` rows (weight 0).                   │
└────────────────────────────────────────────────────────────────┘
    │
    ▼
┌─ 7a. CROSS-SOURCE EXACT (RECEIPT ↔ ESL) ───────────────────────┐
│  get_consensus_for_label(                                       │
│      retailer_id=parsed.retailer_id,                            │
│      source_type='esl',                                         │
│      normalized_label=cleaned_label  ← STRICT EQUALITY          │
│  ) → state == VERIFIED ?                                        │
│  → matched/cross_source_esl_exact (confidence=1.0)              │
│                                                                │
│  Why strict equality?                                           │
│  Receipt and ESL formats differ (24-char abbreviated vs        │
│  full marketing label). But when by chance the receipt label   │
│  IS identical to a verified ESL label, it is an               │
│  ULTRA-reliable signal (probability of a false positive is     │
│  near-zero).                                                   │
└────────────────────────────────────────────────────────────────┘
    │
    ▼
┌─ 8. STOP — UNRESOLVED ─────────────────────────────────────────┐
│  rejected_reason='no_consensus_match'                           │
│  display = raw cleaned_label (user sees what they scanned)     │
│  candidate_eans = top-3 fuzzy fallback (for audit + admin)     │
└────────────────────────────────────────────────────────────────┘
```

### For an ESL scan (`source_type='esl'`)

```text
LabelImage (R2 image + store_id resolved)
    │
    ▼
┌─ 1. PYZBAR ────────────────────────────────────────────────────┐
│  read_ean_barcode(image) → EAN-13 OR EAN-8                     │
│  pyzbar = all-or-nothing (checksum verified by ZBar lib).      │
│  Hit → match equivalent to barcode strict, source_type='esl'.  │
└────────────────────────────────────────────────────────────────┘
    │ MISS
    ▼
┌─ 2. OCR EAN + CHECKSUM (block E) ──────────────────────────────┐
│  OCR text scan for pattern \d{13}.                              │
│  For each 13-digit candidate: validate_ean13_checksum().       │
│  11+ consecutive digits on ESL = near-exclusive EAN signature  │
│  (no price/quantity/qty with 11+ chars on a price label).      │
│  Checksum-valid hit → match equivalent to pyzbar a posteriori. │
└────────────────────────────────────────────────────────────────┘
    │ MISS
    ▼
┌─ 3. UNRESOLVED V1 ─────────────────────────────────────────────┐
│  rejected_reason='ocr_no_ean_in_label'                         │
│  NO fuzzy fallback in V1 (see § Out of scope).                 │
│  V2 backlog: partial EAN recovery via Levenshtein products     │
│  + name similarity > 0.75 + contextual filter.                 │
└────────────────────────────────────────────────────────────────┘
    │ HIT (pyzbar OR OCR+checksum)
    ▼
┌─ 4. WRITE LEDGER (block D) ────────────────────────────────────┐
│  record_resolution(                                             │
│      scan_id, store_id, normalized_label=label_item.scanned_name│
│      (UPPER+TRIM), product_ean=ean, user_id,                    │
│      source_type='esl', match_method='esl'                      │
│  )                                                              │
│  + upsert_price_consensus(scan) (already existing)              │
└────────────────────────────────────────────────────────────────┘
```

---

## ESL flow — step by step

| Step | What happens | Code (post-redesign) | V1/V2 |
|---|---|---|---|
| 1 | User points the camera at the electronic shelf label. Image uploaded to R2 (TTL 48h per GDPR). | `routes/scan.py` POST `/scan-label` | V1 |
| 2 | Worker `process_label` downloads the image, multipass OCR. | `worker/label_task.py` | V1 |
| 3 | `read_ean_barcode(image)` (pyzbar) attempts to read the barcode printed on the ESL. If successful → canonical EAN. | `worker/pipeline/barcode_reader.py` | V1 (existing) |
| 4 | If pyzbar MISS: `parse_label(ocr_result)` extracts OCR digits. For each `\d{13}` match → `validate_ean13_checksum()`. | `worker/pipeline/label_parser.py` (block E strengthens) | V1 |
| 5 | EAN obtained (pyzbar OR OCR+checksum) → product lookup `Product.ean`. | unchanged | V1 |
| 6 | If product found: status='accepted', match_method='barcode_ean' (pyzbar) or 'manual' (OCR+checksum). | unchanged | V1 |
| 7 | **Block D — NEW**: call `record_resolution(... source_type='esl', match_method='esl')`. The trigger `fn_sync_pnr_retailer_id` denorms `retailer_id` from `stores`. | `worker/label_task.py` (block D add) | V1 |
| 8 | `upsert_price_consensus(scan)` (already existing) feeds price_consensus for this store. | unchanged | V1 |
| 9 | If pyzbar AND OCR+checksum MISS: `status='unmatched'`, `rejected_reason='ocr_no_ean_in_label'`, **no** ESL ledger write. | block E | V1 |
| 10 | V2: if MISS, partial EAN recovery — Levenshtein on `products.ean` + similarity on `products.name_normalized` > 0.75 + contextual filter (ESL neighbours in the session's aisle). | not implemented | V2 |

---

## Derived state semantics

Inherits the semantics established by NRC block A § "Derived states" (`ConsensusState`) — **structurally unchanged** but now computed by `(retailer_id, source_type, normalized_label)` instead of `(store_id, normalized_label)`.

| State | Meaning (product vocabulary) | Code trigger |
|---|---|---|
| `verified` | Consensus established, reliable | quorum reached + `top1_pct ≥ convergence_threshold_pct` + `top1/top2 ≥ min_top1_lead_factor` |
| `controverse` | Ambiguous, divergent users, honest divergence (cold-start) | quorum reached but never converged (`was_ever_verified=false`) |
| `unverified` | **Suspect** (alert signal: system bug / fraud / data drift) | quorum reached, `was_ever_verified=true` but now divergent |
| `pending` | Quorum not yet reached | `distinct_validators < min_distinct_users[source_type]` |
| `unresolved` | No ledger entry for `(retailer, source_type, label)` | `get_consensus_for_label` returns `None` |

> **Product philosophy reminder**: `verified` is triggered ONLY via crowdsourced consensus. A fuzzy `top1=0.99` alone NEVER promotes a scan to `verified`. A `cross_source_esl_exact` match (stage 7a) translates to `match_method='cross_source_esl_exact'` at the SCAN level but **does NOT write** a ledger row `match_method='cross_source_esl_exact'` in the receipt consensus — it consumes an existing ESL consensus to decorate a receipt scan. (See § Cross-source matching, sub-section "Bicephalous behaviour of stage 7a".)

---

## Cross-source matching (receipt ↔ ESL)

### Stage 7a — Auto only, strict equality

When the receipt cascade reaches stage 7a (all previous stages have missed), we query the ESL consensus:

```python
esl_match = get_consensus_for_label(
    retailer_id=parsed.retailer_id,
    source_type='esl',
    normalized_label=cleaned_receipt_label  # ← strict equality
)
if esl_match is not None and esl_match.state == ConsensusState.VERIFIED:
    return ItemMatch(
        status='matched',
        product_ean=esl_match.ean,
        match_method='cross_source_esl_exact',
        match_confidence=1.0,
        ...
    )
```

**Cumulative conditions for the cross-source auto-match:**

1. The receipt label is **strictly identical** (post-normalisation `UPPER+TRIM+unaccent`) to a `verified` ESL label at the same retailer.
2. The ESL consensus is in state `VERIFIED` (not pending, not controverse, not unverified).

**Why strict equality?** Receipt (24-char abbreviated) and ESL (full marketing label) formats are normally different — `"YAOURT NAT 4X125"` vs `"YAOURT NATURE BIO 4 X 125G"`. The case where they are strictly identical is rare but ULTRA-reliable: the probability that two distinct EANs produce the same normalised label on a receipt AND on an ESL is near-zero.

### Bicephalous behaviour of stage 7a

When stage 7a hits, the receipt scan is marked:

- `scans.match_method = 'cross_source_esl_exact'`
- `scans.product_ean = esl_match.ean`
- `scans.status = 'matched'`

But **we do NOT write** a `product_name_resolutions` row with `match_method='cross_source_esl_exact'` on the `source_type='receipt'` side. Why? Because that would be double-counting: the user did NOT manually validate the receipt label — they simply had the luck that another user (ESL) had already validated the same label.

Instead, the matcher writes a `match_method='fuzzy_pending'` row (weight 0, does not contribute to receipt consensus) to trace the scan in the ledger. The scan appears `matched` on the UI side but **does not elevate** the receipt label to `verified` status on the receipt ledger side — 3 distinct users are still needed on the receipt side for that.

> **Orchestrator decision (acted)**: this behaviour is deliberately conservative. If cross-source were equivalent to a receipt vote, we would artificially seed the receipt consensus with ESL votes — which would nullify the source separation we are in the process of establishing.

### Explicit drops

- ❌ **7b — fuzzy auto cross-source**: dropped. A receipt label fuzzy-matching a `verified` ESL label (similarity 0.80) is NOT auto-matched. Too many false positives (receipt and ESL formats too different).
- ❌ **Track D — UI ESL→receipt suggestion**: dropped. Cluttered UX; the user has rarely scanned the ESL for the product they are buying at the moment they scan the receipt.
- ❌ **User-validated cross-pollination**: V2 backlog. If user X scans the ESL for product Y, then the receipt with label Z mapping to Y, we could consider "X has implicitly validated Z=Y". V2.

---

## Weights and thresholds

### Weights per actor

| Actor | Weight | Notes |
|---|---|---|
| Normal user (barcode/manual) | 1 | Reset NRC block A. |
| Admin (`manual_admin`) | 5 | Setting `name_resolution_consensus.admin_validation_weight`. |
| Shadow-banned user (anti-fraud V1) | 0 | `weight_override=0` at INSERT (see NRC block A `_shadow_ban_weight_override`). Row kept for audit. |
| **ESL** (`match_method='esl'`) | **1** | Same as a normal user. The reduced quorum (2 vs 3) reflects reliability, not weight. |
| **V2 backlog**: Elite user (trust ≥ 95% + 100+ scans) | 2 or 3 | Out of scope V1, to be validated on alpha data. |

### Quorums per `source_type`

Settings split by source_type (see § Parameters ratis_settings.json):

| `source_type` | `min_distinct_users` | Justification |
|---|---|---|
| `receipt` | 3 | Unchanged from NRC block A. Reflects the reduced reliability of receipt labels (24-char abbreviated, variable OCR). |
| `esl` | **3** | Simplified V1 — uniformity with receipt, more robust against false positives. The threshold per source_type remains configurable (parameter) but V1 sets **3 everywhere** for simplicity. |

### Convergence (unchanged from NRC)

- `convergence_threshold_pct = 80` (top1_pct ≥ 80%)
- `min_top1_lead_factor = 2.0` (top1_weight ≥ 2.0 × top2_weight)

Applies identically to both `source_type` values.

---

## Data migration

At the time of block A, the `product_name_resolutions` ledger is **empty in production** (NRC block A merged end of April, no significant live usage yet at the time of the brainstorm). Therefore:

1. **Migration of existing rows**: trivial (zero rows to migrate in prod).
2. **Backfill `retailer_id` for alpha rows (test)**: `UPDATE product_name_resolutions SET retailer_id = (SELECT retailer_id FROM stores WHERE id = product_name_resolutions.store_id) WHERE retailer_id IS NULL`. To be run inside the block A migration after the trigger is created (the trigger will not fire on already-persisted rows).
3. **No `source_type` backfill**: DEFAULT `'receipt'` covers 100% of existing rows (which are by construction receipt resolutions).
4. **Prod settings**: the `min_distinct_users: 3` key remains unique (Q7 simplified V1 — uniform receipt + esl). Addition of sub-keys `validation_methods_receipt` / `validation_methods_esl` + `fuzzy_consensus_*` (see § Parameters).
5. **Retailers seed**: already present in prod via migration `20260422_0945_retailers_seed` (Carrefour, Auchan, Intermarché, Leclerc, Franprix + variants). **No seed prerequisite** for this ARCH.

> **KP-42 concern**: a backfill `UPDATE` can break rows in prod if the condition matches unanticipated cases. Here the condition is `retailer_id IS NULL AND store_id IS NOT NULL` — strictly additive (we do not overwrite an already non-null retailer_id). Pre-migration audit: `SELECT count(*) FROM product_name_resolutions WHERE retailer_id IS NULL` (expected: number of alpha rows).

---

## Parameters `ratis_settings.json`

### Current state (NRC block A)

```json
"name_resolution_consensus": {
  "min_distinct_users": 3,
  "validation_methods": ["barcode", "manual_admin"],
  "convergence_threshold_pct": 80,
  "min_top1_lead_factor": 2.0,
  "admin_validation_weight": 5
}
```

### Target (post-block A)

```json
"name_resolution_consensus": {
  "min_distinct_users": 3,
  "validation_methods_receipt": ["barcode", "manual_admin", "fuzzy_pending"],
  "validation_methods_esl": ["esl", "manual_admin"],
  "convergence_threshold_pct": 80,
  "min_top1_lead_factor": 2.0,
  "admin_validation_weight": 5,
  "fuzzy_consensus_similarity_min": 0.80,
  "fuzzy_consensus_len_diff_max": 2
}
```

**Parameter decisions:**

- **`min_distinct_users` unified V1**: a single `min_distinct_users: 3` key everywhere (receipt + esl). V1 simplification (Q7 acted). If V2 needs to re-split, we will introduce `_receipt` / `_esl` at that point — the JSON structure remains extensible.
- **`validation_methods_receipt`** extends the list to include `fuzzy_pending`? **No — decision: we keep the current list (`barcode + manual_admin`).** `fuzzy_pending` rows are stored but still do not contribute.
- **`validation_methods_esl: ["esl", "manual_admin"]`**: only ESL and admin votes contribute to the ESL consensus. No fuzzy_pending on the ESL side (the V1 ESL pipeline never writes fuzzy_pending — it is all-or-nothing checksum).
- **`fuzzy_consensus_similarity_min` + `fuzzy_consensus_len_diff_max`**: promoted to the settings level (instead of hardcoded in the matcher), for stage 5 "receipt fuzzy consensus".

### `label` section (ESL settings, block G)

```json
"label": {
  "batch_max_images": 10  // ← bump to 30 in block G (burst mode)
}
```

Out of scope for blocks A-F: stays at 10. Block G bumps to 30 + validates via dogfooding.

---

## Append-only philosophy

`product_name_resolutions` remains an **immutable ledger** (NRC block A § "Append-only"). We do not `UPDATE`, we do not `DELETE` (except GDPR cascade via `scan_id ON DELETE CASCADE`).

**What changes with this ARCH:**

- Block A `ALTER TABLE ADD COLUMN` is compatible with append-only (existing rows acquire the new columns via DEFAULT + trigger; no manual UPDATE on existing `match_method` values).
- Block D: a new ESL scan produces a new INSERT — not an UPDATE of a receipt row triggered by an ESL event.
- Stage 7a (cross-source): the receipt scan is decorated with `match_method='cross_source_esl_exact'` at the `scans` row level. **No retroactive UPDATE** on the receipt ledger (the receipt row is kept as `fuzzy_pending` for the ledger — see § "Bicephalous behaviour of stage 7a").

The states `verified / controverse / unverified` are **derived** at read time, not persisted. Consistent with NRC block A.

---

## Out of scope V1 — V2 Backlog

| V2 | Description |
|---|---|
| **F — Admin UI updates** | Retailer-aware NRC arbitration + source_type filter + endpoints `/by-retailer/{slug}/{source}/{label}` |
| **G — ESL burst mode mobile** | Continuous scan UI + bump `batch_max_images` to 30+ + append-to-session endpoint |
| **7a — Cross-source receipt↔ESL exact auto match** | If receipt label is EXACTLY identical to a VERIFIED ESL label → match with `match_method='cross_source_esl_exact'`. Dropped for V1 (rare in practice, not critical). |
| **On-the-fly partial EAN recovery** | Today V1: only batch I does E.2. On-the-fly = V2 if latency is acceptable. |
| **Trust_score user weight bonus** | Elite user (trust>=95% + 100+ scans) → weight=2-3 to reduce bootstrap threshold |
| **Admin endpoint `PATCH /admin/stores/{id}/assign-retailer`** | Manual curation of user-suggested stores without a retailer. Audit noted this is a minor gap, not blocking. |
| **User-validated cross-pollination** | User who scans ESL then receipt → strengthens both ledgers in one action. |
| **Async batch consensus recompute** | No batch — computed live at read time. If cardinality exceeds 10M rows, V2 introduces a materialised cache. |
| **Drop old index `(store_id, normalized_label)`** | Kept in V1 (audit / admin queries). V2 will decide based on usage. |

Note: the "retailer auto-detection from OCR receipt header" that was considered for V2 is in fact **already implemented** (confirmed by audit via `store_detector.py`). No longer in the backlog.

---

## Block details

### Block A — schema + migration

- **Description**: extends `product_name_resolutions` with `source_type` + `retailer_id`, updates CHECK + UNIQUE, adds denorm trigger, extends settings.
- **Files touched**:
  - `alembic/versions/<new>_cross_retailer_consensus_schema.py` (NEW)
  - `ratis_core/ratis_core/models/name_resolution.py` (MODIFIED — columns + relationship)
  - `ratis_core/ratis_core/config/ratis_settings.json` (MODIFIED — split keys)
  - `ratis_core/tests/test_models_name_resolution.py` (MODIFIED — column assertions)
  - `webservices/ratis_product_analyser/ARCH_cross_retailer_consensus.md` (this file — checkboxes)
- **Dependencies**: none.
- **Required tests**:
  - Alembic upgrade/downgrade idempotence test (`alembic/tests/test_cross_retailer_migration.py`)
  - Trigger `fn_sync_pnr_retailer_id` test (INSERT row without retailer_id with valid store_id → trigger fills it)
  - Trigger on UPDATE OF store_id test (new store_id → retailer_id refresh)
  - CHECK constraint accepts `'esl'` + `'cross_source_esl_exact'`
  - UNIQUE `(scan_id, source_type, normalized_label)` accepts 1 receipt + 1 esl for same `(scan_id, label)`, rejects 2 identical receipts
- **Data migration**: YES (UPDATE backfill retailer_id on existing alpha rows — strictly additive).
- **New settings**: `min_distinct_users_receipt`, `min_distinct_users_esl`, `validation_methods_receipt`, `validation_methods_esl`, `fuzzy_consensus_similarity_min`, `fuzzy_consensus_len_diff_max`. Backward-compat fallback documented.
- **ARCH update**: YES (tick block A checkboxes at the end + cross-link to PR).
- **Risks / gotchas**:
  - The trigger fires BEFORE INSERT — if the application service inserts `retailer_id` manually (bad practice), the trigger does not overwrite it (`IF NEW.retailer_id IS NULL`). Inline doc.
  - Index `idx_pnr_retailer_source_label` is partial (`WHERE retailer_id IS NOT NULL`) — pg_dump must preserve it. See alembic test.
  - The `ON DELETE RESTRICT` on `retailer_id` is intentional: a retailer must not be able to disappear silently (R05 — no prod delete). If a test tries to delete a referenced retailer, explicit FK violation.

### Block B — extended read-only repos

- **Description**: adapts the repository read-only functions for the `(retailer_id, source_type, label)` key.
- **Files touched**:
  - `webservices/ratis_product_analyser/repositories/name_resolution_repository.py` (MODIFIED — signature `get_consensus_for_label`, `was_ever_verified`, `list_divergent_labels`)
  - `webservices/ratis_product_analyser/repositories/retailer_resolution.py` (NEW — helper `resolve_retailer_id(db, store_id) -> UUID | None`)
  - `webservices/ratis_product_analyser/tests/test_name_resolution_repository.py` (MODIFIED — fixtures + retailer-keyed tests)
  - `webservices/ratis_product_analyser/tests/test_retailer_resolution.py` (NEW)
- **Dependencies**: A (schema).
- **Required tests**:
  - `get_consensus_for_label(retailer_id, source_type='receipt', label)` — happy path verified (3+ distinct users cross-stores of the same retailer)
  - Same `source_type='esl'` with reduced quorum (2 distinct users)
  - Test: 3 users in 3 distinct Intermarché stores converge → VERIFIED (before redesign = PENDING per store).
  - `find_fuzzy_verified_consensus(retailer_id, source_type='receipt', query, len_diff_max, similarity_min)` — retailer-wide pg_trgm fuzzy; returns matched label only if state=VERIFIED
  - `was_ever_verified(retailer_id, source_type, label)` — audit log payload keyed on `retailer_id` (not `store_id`)
  - `resolve_retailer_id(db, store_id)`: happy path, store without retailer_id → None, non-existent store → None
- **Data migration**: NO.
- **New settings**: NO (consumed by reading the settings established in block A).
- **ARCH update**: YES (tick block B).
- **Risks / gotchas**:
  - **Breaking repo signature change**: `get_consensus_for_label` changes `store_id` → `retailer_id`. All call sites must migrate in block C (matcher) and block F (admin service). Risk: a forgotten call site = TypeError at runtime. Mitigation: grep `get_consensus_for_label` before merge.
  - `was_ever_verified` reads `pipeline_audit_log` payload — block C will need to emit `consensus_state_changed` events with `retailer_id` instead of `store_id` in the payload. Backward-compat: historical (alpha) payloads use `store_id`. Decision: future payload writes BOTH (`retailer_id` + `store_id`) for traceability, read filters on `retailer_id`. Existing partial index `idx_pal_consensus_state_changed` (NRC block C migration `20260501_1700_nrcC`) remains valid (predicate on `event = 'consensus_state_changed'` only).

### Block C — pipeline_v3 matcher cascade refactored

- **Description**: redesign of the matcher cascade for cross-retailer + addition of stage 7a.
- **Files touched**:
  - `webservices/ratis_product_analyser/worker/pipeline/match.py` (MODIFIED — cascade refactored)
  - `webservices/ratis_product_analyser/worker/pipeline/types.py` (MODIFIED — `match_method` Literal extended)
  - `webservices/ratis_product_analyser/tests/pipeline/test_match.py` (MODIFIED — cascade tests)
  - `webservices/ratis_product_analyser/tests/pipeline/test_match_cross_source.py` (NEW — stage 7a)
- **Dependencies**: A, B.
- **Required tests**:
  - 3 users in 3 distinct Intermarché stores (Lyon, Marseille, Lille) → 4th user in Bordeaux gets `verified` immediately.
  - Stage 7a: receipt label = `verified` ESL label at the retailer → `matched/cross_source_esl_exact`.
  - Stage 7a: receipt label ≈ `verified` ESL label (similarity 0.95 but not strict) → PASS at stage 7a, fall through to unresolved.
  - Stage 7a: `pending` ESL label → PASS at stage 7a (does not auto-match; only `verified` counts).
  - Existing compat: NRC block B tests `test_matcher_cascade.py` still pass after refactor (stages 1-3 unchanged).
- **Data migration**: NO.
- **New settings**: NO.
- **ARCH update**: YES.
- **Risks / gotchas**:
  - **Stage 7a MUST NOT write** a `cross_source_esl_exact` row on the `source_type='receipt'` side (see § Bicephalous behaviour). Risk of double-counting if the instruction is missed. Mitigation: explicit test "stage 7a hit → receipt ledger unchanged".
  - Performance risk: stage 7a calls `get_consensus_for_label` a second time (with `source_type='esl'`) if stages 4 and 5 MISS — one extra DB round-trip per item. Acceptable in V1 (matcher runs in async worker).
  - `retailer_id` can be `None` (user-suggested store, non-existent store). Mitigation: if `retailer_id is None` in the cascade, skip directly to legacy stages (4 and 5 in `pipeline`) with `(store_id, label)` as a temporary fallback. → **Open question #5**: do we want this fallback or do we cut immediately?

### Block D — ESL → ledger writes (historical gap)

- **Description**: calls `record_resolution` in `worker/label_task.py` after a successful ESL match.
- **Files touched**:
  - `webservices/ratis_product_analyser/worker/label_task.py` (MODIFIED — record_resolution call post-match)
  - `webservices/ratis_product_analyser/repositories/name_resolution_writes.py` (MODIFIED — `record_resolution` accepts `source_type`, Literal validation)
  - `webservices/ratis_product_analyser/tests/test_label_task.py` (MODIFIED — ledger row assertions)
  - `webservices/ratis_product_analyser/tests/test_ledger_writes.py` (MODIFIED — tests source_type='esl')
- **Dependencies**: A, B.
- **Required tests**:
  - Label scan pyzbar match → ledger row `(scan_id, source_type='esl', label, ean, match_method='esl', weight=1)`.
  - Label scan OCR EAN+checksum match → identical ledger row (same match_method='esl' — the pyzbar vs OCR distinction is in `scans.match_method`, not in the ledger).
  - Unmatched label scan → NO ledger row.
  - Label scan match but `store_id IS NULL` (user outside radius) → NO ledger row (skip — no resolvable retailer_id).
  - Idempotence: 2 process_label on the same scan → 1 single row (ON CONFLICT DO NOTHING).
- **Data migration**: NO.
- **New settings**: NO.
- **ARCH update**: YES.
- **Risks / gotchas**:
  - `record_resolution` must be adapted to accept `source_type` (signature change). Backward-compat: default `source_type='receipt'` for existing call sites (receipt matcher + barcode_service + admin override).
  - `normalized_label` on the ESL side: we take `UPPER+TRIM(label_item.scanned_name)` — no normalize via ocr_knowledge (ESL labels are typically well-formed, no need for corrected tokens). Decision documented inline.
  - The call to `record_resolution` must be inside the worker transaction (before the final `db.commit()`). Risk: if we commit first and record_resolution is in a second transaction, we lose atomicity. → see test "label scan rollback → no ledger row".

### Block E — ESL pipeline V1 (pyzbar + OCR checksum)

- **Description**: strengthens `parse_label` to extract an OCR EAN-13 with checksum validation.
- **Files touched**:
  - `webservices/ratis_product_analyser/worker/pipeline/label_parser.py` (MODIFIED — extract EAN+checksum)
  - `ratis_core/ratis_core/utils/ean_checksum.py` (NEW — `validate_ean13_checksum`, pure function)
  - `ratis_core/tests/test_ean_checksum.py` (NEW)
  - `webservices/ratis_product_analyser/tests/test_label_parser.py` (MODIFIED — OCR EAN extraction tests)
- **Dependencies**: none (parallelizable with A-D).
- **Required tests**:
  - `validate_ean13_checksum('3017620422003')` → True (Nutella).
  - `validate_ean13_checksum('3017620422004')` → False (invalid checksum).
  - `validate_ean13_checksum('301762042200')` → False (12 digits, not EAN-13).
  - `parse_label` with OCR text containing `"3017620422003"` → LabelItem.product_ean filled.
  - `parse_label` with OCR text containing `"3017620422004"` (bad checksum) → LabelItem.product_ean=None.
  - `parse_label` with 2 EAN candidates of which 1 has a valid checksum → takes the valid one.
- **Data migration**: NO.
- **New settings**: NO.
- **ARCH update**: YES.
- **Risks / gotchas**:
  - EAN-8 (`\d{8}`) keeps the current logic (no checksum V1 — rare in France, V2 if needed).
  - **KP-37** (`re.IGNORECASE` does not fold accents): not applicable here, we match `\d{13}` (digits only). But relevant for label normalizations (UPPER vs accent fold).
  - V2 backlog noted inline: partial EAN recovery via Levenshtein products.

### Block F — admin UI updates

- **Description**: NRC mini-UI + JSON endpoints adapted to the retailer dimension + source_type filter.
- **Files touched**:
  - `webservices/ratis_product_analyser/services/name_resolution_admin_service.py` (MODIFIED — retailer-based signatures + source_type filter)
  - `webservices/ratis_product_analyser/routes/admin/name_resolutions.py` (MODIFIED — query params)
  - `webservices/ratis_product_analyser/admin_ui/templates/name_resolutions_queue.html` (MODIFIED — retailer columns + source_type filter)
  - `webservices/ratis_product_analyser/admin_ui/templates/name_resolution_detail.html` (MODIFIED — timeline broken down by source_type)
  - `webservices/ratis_product_analyser/tests/test_admin_name_resolutions.py` (MODIFIED)
  - `webservices/ratis_product_analyser/tests/test_admin_ui_name_resolutions.py` (MODIFIED)
  - `ENDPOINTS.md` (auto-regenerated — no manual modification)
- **Dependencies**: A, B.
- **Required tests**:
  - `GET /api/v1/admin/name-resolutions/queue?retailer_slug=intermarche&source_type=esl` returns only Intermarché ESL pairs in `unverified|controverse`.
  - `GET /detail/{retailer_slug}/{source_type}/{label}` (refactored URL) — 404 if triplet unknown.
  - `POST /resolve` accepts `retailer_slug` + `source_type` + `label` + `target_ean`. Creates a `manual_admin` row with the correct `source_type`.
  - Compat: old `/queue` URL without `source_type` returns all types (default).
- **Data migration**: NO.
- **New settings**: NO.
- **ARCH update**: YES.
- **Risks / gotchas**:
  - Admin URLs change (`/store_id/label` → `/retailer_slug/source_type/label`). Risk of breaking existing admin bookmarks (alpha). Acceptable — alpha test, not public prod.
  - Dashboard `index.html` counter must aggregate across both source_types (by default). Inline doc.

### Block G — frontend mobile ESL burst mode

- **Description**: continuous ESL scan UI + batch_max bump.
- **Files touched**:
  - `ratis_client/app/(tabs)/scan.tsx` (MODIFIED — burst UI, batch hook)
  - `ratis_client/hooks/useLabelBatch.ts` (NEW — contiguous batch orchestration)
  - `ratis_client/__tests__/useLabelBatch.test.ts` (NEW)
  - `ratis_core/ratis_core/config/ratis_settings.json` (MODIFIED — `label.batch_max_images: 30`)
  - `webservices/ratis_product_analyser/routes/scan.py` (potentially — verify that `batch_max_images=30` does not introduce a timeout on the R2 upload side)
- **Dependencies**: E (V1 ESL pipeline validated and stable).
- **Required tests**:
  - Jest: useLabelBatch uploads 30 images in series, handles intermediate network error (retry).
  - Manual / dogfood: burst scan of 30 ESL in a row, acceptable latency.
- **Data migration**: NO.
- **New settings**: MODIFIED `label.batch_max_images` (10 → 30).
- **ARCH update**: YES.
- **Risks / gotchas**:
  - R2 / Cloudflare payload limit: 30 images × ~1MB = 30MB per session. Verify multipart limit.
  - Hot path pyzbar on the worker side: 30 images = 30 Celery jobs. Stress test queue.

### Block H — V2 backlog

- **Description**: on-the-fly partial EAN recovery + trust score weight bonus + user-validated cross-pollination + stage 7a cross-source. **No V1 PR.**
- **Status**: 📋 backlog. To be spec'd in a dedicated ARCH when we tackle it (post-V1, with alpha data in hand).

### Block I — Nightly reconciliation batch

**Path**: `batch/ratis_batch_scan_reconciliation/main.py` (new batch)

**Frequency**: nightly cron 03:00 UTC (off-peak hours)

**Logic**:
For each scan WHERE `status='unresolved'` AND `scanned_at > NOW() - INTERVAL '30 days'`:

1. **Re-attempt consensus match**: a VERIFIED consensus may have emerged for `(retailer_id, source_type, cleaned_label)` since the scan. Full cascade (exact + retailer-wide fuzzy).
2. **Partial EAN recovery (E.2 batch only)**: if OCR had output 11-12 chars OR 13 chars + failed checksum, lookup `products` with Levenshtein ≤ 2 + name similarity > 0.75 + filter retailer_id. If 1 unique candidate → match.
3. **If match found**:
   - UPDATE scan: `status='matched'`, `product_ean=X`, `match_method='reconciliation'`, `rejected_reason=NULL`
   - INSERT `product_name_resolutions` (feeds ledger + recomputes consensus)
   - upsert `price_consensus` (feeds the price pivot)
   - **Retroactive CAB**: INSERT `cabecoin_transactions` (direction='credit', amount=50 V0, reason='retroactive_match', context={scan_id, matched_at, scanned_at, product_ean})
   - **Gratitude-driven push notification**: "🎉 Your scan has been identified — here are X CAB" via NT service
   - **OCR knowledge auto-feed**: if the resolution involves an OCR correction (e.g. HIPROA → HIPRO via name similarity), INSERT into `ocr_knowledge` with `confidence ≥ 0.85` minimum, source='batch_reconciliation'
4. **Otherwise**: leave as unresolved, will retry tomorrow.

**R2 image TTL for unresolved scans**: 30 days (consistent with reconciliation window, GDPR-friendly). Auto-purge by `ratis_batch_purge` beyond that.

**Required TDD tests**:
- Re-match on emerging consensus
- Partial EAN recovery: 1 candidate → match, 0 candidates → unchanged
- Retroactive CAB credited + transaction logged
- Fire-and-forget push notification (mock)
- ocr_knowledge auto-feed with confidence ≥ 0.85 guard
- Idempotence: re-running the same batch does not double-credit
- Consistent purge TTL

---

## Open questions — acted decisions

1. **`retailer_id` denorm via DB trigger or batch backfill?**
   **Decision: DB trigger acted V1** (denorm worth it to avoid long-term latent debt). BEFORE INSERT/UPDATE OF store_id on `product_name_resolutions`. Advantages: auto-consistency, no possible drift.

2. **Migration of existing prod entries: trivial?**
   **Decision: NOT NULL directly on empty ledger. If rows arrive before block A, simple idempotent backfill.** Alpha status (2026-05-02): empty ledger in prod. The migration backfill `UPDATE ... WHERE retailer_id IS NULL` remains idempotent and strictly additive.

3. **How to handle a store that changes retailer (rebranding)?**
   **Decision: drift tolerated, append-only respected. Historical audit preserved. Admin intervenes on a case-by-case basis (rare).** Historical ledger rows point to the old retailer_id; we do NOT propagate the rebranding to the ledger (consistent with R05 append-only).

4. **GIN trgm index on `normalized_label` for retailer-wide fuzzy?**
   **Decision: GIN trgm index created in V1** (Q1 same reasoning, avoid latent debt). Negligible cost (~25 MB per retailer × 36 retailers ≈ 900 MB, +1-5ms per INSERT). See § DB Schema for the SQL definition.

5. **Cascade fallback `retailer_id IS NULL`?**
   **Decision: skip consensus stages if retailer_id IS NULL → unresolved direct. Batch I will retry when store is validated. Retailer detection already solid at 99% via store_detector.py + OSM (confirmed by audit), no need for Block J.**

6. **Admin endpoints URL scheme: `/retailer_slug/source_type/label` vs query params?**
   **Decision: path-based, consistent with existing NRC endpoints.** `/api/v1/admin/name-resolutions/{retailer_slug}/{source_type}/{label}`.

7. **`min_distinct_users` quorum per source_type?**
   **Decision: `min_distinct_users=3` everywhere (receipt + esl), V1 simplification.** Single settings key; JSON structure remains extensible for a possible V2 split.

---

## Glossary

- **ESL** (Electronic Shelf Label): electronic price label displayed on the shelf in-store. Official source of product-in-store identity. In code: scan of type `label` (`scans.scan_type='label'`).
- **Retailer**: chain (Intermarché, Carrefour, Auchan…). `retailers` model (DA-34), seeded via `retailers_fr.json`. Referenced by `stores.retailer_id`.
- **Store**: unique physical store (one Intermarché Lyon-7e ≠ Intermarché Marseille-10e, but same retailer). `stores` model, FK `retailer_id`.
- **`source_type`**: new column in `product_name_resolutions`. Values: `'receipt'` (label comes from a receipt scan) or `'esl'` (label comes from an electronic shelf label). Separates the two logical ledgers for strict cross-source matching.
- **Cross-source matching**: logic of stage 7a — a receipt `cleaned_label` that EXACTLY matches a `verified` ESL label at the same retailer auto-promotes the receipt scan. Strict equality only (no fuzzy cross-source in V1).
- **Stage 7a**: internal name of the matcher cascade stage for cross-source exact. Position in the cascade: after all receipt stages (exact consensus, fuzzy consensus, products.name strict, strict fuzzy).
- **`normalized_label`**: `UPPER(TRIM(unaccent(scanned_name)))` post ocr_knowledge corrections. Identical for both source_types (but in practice the values rarely converge between `receipt` and `esl` since the formats differ — hence the strict equality of stage 7a).
- **NRC**: Name Resolution Consensus, parent ARCH (ARCH_name_resolution_consensus.md).
- **`match_method='esl'`**: new enum value. A ledger row written by `worker/label_task.py` after a successful ESL match.
- **`match_method='cross_source_esl_exact'`**: new enum value (appears ONLY on `scans.match_method`, never in `product_name_resolutions.match_method` — see § Bicephalous behaviour).
- **Append-only ledger**: table where we never UPDATE/DELETE (except GDPR cascade `scan_id ON DELETE CASCADE`). All votes remain in the history.

---

## Sub-ARCHs and cross-references

- **Parent**: [[ARCH_name_resolution_consensus]] — main ledger, derived states, anti-fraud V1.
- **Sibling**: [[ARCH_consensus]] — price consensus (orthogonal — key `(store_id, ean)`, not affected by this ARCH).
- **Sibling**: [[ARCH_receipt_pipeline]] — Phase 3 matcher (modified by block C).
- **Sibling**: [[ARCH_OCR_LLM_BRIDGE]] — knowledge integration (unchanged).

---

## Implementation checklist (master)

### Block A
- [x] ARCH created (this file)
- [x] Alembic migration created + upgrade/downgrade tests
- [x] SQLAlchemy model `ProductNameResolution` updated (columns + relationship)
- [x] `name_resolution_consensus` settings extended + backward-compat
- [x] Trigger `fn_sync_pnr_retailer_id` created + tested (INSERT + UPDATE OF store_id)
- [x] CHECK constraint match_method extended (`esl`, `cross_source_esl_exact`)
- [x] UNIQUE (`scan_id`, `source_type`, `normalized_label`) active, old index dropped
- [x] Index `(retailer_id, source_type, normalized_label) WHERE retailer_id IS NOT NULL`
- [x] UPDATE backfill retailer_id on alpha rows
- [ ] `pg_dump > db/schema.sql` post-merge
- [ ] CI green

### Block B
- [x] `get_consensus_for_label` signature change (retailer_id + source_type)
- [x] `find_fuzzy_verified_consensus` (NEW — retailer-wide pg_trgm)
- [x] `was_ever_verified` adapted (retailer_id)
- [x] `list_divergent_labels` adapted
- [x] `resolve_retailer_id(db, store_id)` (NEW helper, `repositories/retailer_resolution.py`)
- [x] TDD tests for each function
- [x] Cross-store tests: 3 distinct users in 3 Intermarché stores → VERIFIED
- [ ] CI green
- In-PR decisions:
  - Retailer-keyed canonicals; transitional `*_by_store` shims (`get_consensus_for_label_by_store`, `find_fuzzy_verified_consensus_by_store`, `was_ever_verified_by_store`) bridge the block C/D/F call sites until their migration. When `retailer_id` is resolvable → forward to canonical. Otherwise → fallback store-keyed live computation (legacy alpha data path). Blocks C/D/F remove the wrappers when migrating imports.
  - Settings: addition of helper `_validation_methods_for(settings, source_type)` that reads `validation_methods_receipt` / `validation_methods_esl` (present from block A) with fallback on the unified `validation_methods` key. Allows ESL rows (`match_method='esl'`) to contribute to the ESL consensus without polluting the receipt side.
  - `was_ever_verified` retailer-keyed reads `payload->>'retailer_id'` + `source_type`; the `*_by_store` shim also probes the old `store_id`-keyed payload to preserve the UNVERIFIED semantics of alpha audit rows. Block C will dual-write both keys and the fallback will become dead code.
  - `get_consensus_for_label` accepts a `was_ever_verified_override: bool | None` to allow the by_store shim to inject the legacy-aware result. Avoids a redundant DB round-trip.
  - Tests conftest: addition of a `retailer` fixture (canonical_name="Lidl"/slug="lidl") attached to the `store` fixture, denormed via the trigger `fn_sync_store_retailer_text`. Preserves `store.retailer.lower()=="lidl"` for existing tests that depend on it (test_receipt_task.TestStoreDetection).

### Block C
- [x] Matcher cascade refactored (stages 3 = retailer-keyed exact, 4 = retailer-wide fuzzy). Stage 7a cross-source = V2 backlog (see § Out of scope).
- [ ] Stage 7a: strict equality only, source_type='esl', state=VERIFIED — V2
- [ ] Stage 7a DOES NOT write receipt row match_method='cross_source_esl_exact' — V2
- [x] Block B compat tests pass (test_match.py 36 green, test_orchestrator.py 19 green, test_scans_admin.py replay match 5 green)
- [x] Retailer-keyed tests in `test_match.py` (cross-store same-retailer, retailer_id=None short-circuit, retailer isolation, source_type isolation contract)
- [x] `pipeline/match.py` drops the `*_by_store` shims — `ConsensusExactLookup`/`ConsensusFuzzyLookup` Protocols key on `retailer_id`, new Protocol `RetailerResolver`. Orchestrator wires the retailer-keyed canonicals with `source_type='receipt'` pinned.
- [x] CI green (PR #265)

### Block D
- [x] `record_resolution` accepts `source_type` (additive signature — already in place Block A; `LedgerMethod` Literal extended with `'esl'` in Block D)
- [x] `worker/label_task.py` calls record_resolution post-match (pyzbar OR OCR EAN, after `upsert_price_consensus`, within the same transaction)
- [x] TDD tests: ledger row written after ESL pyzbar/OCR match (`TestEslLedgerWrites` in `test_label_task.py`)
- [x] Tests: skip if store_id IS NULL (no resolvable retailer_id) — guard `if scan.store_id and label_item.scanned_name`. Case `store_id present + retailer_id NULL`: row inserted with `retailer_id=NULL`, ignored by partial consensus indexes (consistent with ARCH § risks)
- [x] Idempotence ON CONFLICT (test `test_idempotent_replay_writes_single_row`)
- [ ] CI green
- In-PR decisions:
  - `normalized_label` ESL = `UPPER+TRIM(label_item.scanned_name)` directly (no `normalize_text` via ocr_knowledge — ESL labels typically well-formed, ARCH § Block D risks).
  - Single canonical `match_method='esl'` for pyzbar AND OCR EAN — the distinction remains on `scans.match_method` (`'barcode_ean'` vs `'manual'`).
  - Call site placed in the `if product_ean:` branch (after `upsert_price_consensus`, before `db.commit()`) — shares the worker transaction, atomic with the scan update.

### Block E
- [x] `validate_ean13_checksum` (pure, ratis_core.utils.ean_checksum)
- [x] `parse_label` extracts EAN+checksum if pyzbar misses
- [x] TDD checksum tests
- [x] TDD label_parser tests for multiple candidates
- [ ] CI green
- In-PR decisions:
  - `ratis_core/ratis_core/utils.py` (module) converted to package `utils/` with `__init__.py` re-exporting `assert_owner` + `strip_str` (backward-compat preserved for ~10 call sites). New sub-module `utils/ean_checksum.py`.
  - EAN-8 keeps the legacy first-match-wins behaviour (no checksum V1) — ARCH § Block E gotcha. EAN-13 strict checksum filter.
  - Multiple distinct valid EAN-13 candidates → `product_ean=None` (strict V1, V2 batch reconciliation Block E.2). Same EAN repeated → deduplicated, accepted as a unique candidate.
  - The pyzbar→OCR fallback ordering remains implemented on the `worker/label_task.py` side (Block D) — not touched here. `parse_label` covers the OCR-side recovery in isolation.
  - 13-digit noise (date concat, store ID) with invalid checksum is now filtered → no longer pollutes `product_ean`. Lateral hardening of the parser.

### Block F
- [ ] Admin service signature retailer-based + source_type filter
- [ ] JSON endpoints adapted
- [ ] Mini-UI templates updated (queue + detail)
- [ ] Admin + UI tests
- [ ] CI green

### Block G
- [ ] `useLabelBatch` hook + Jest tests
- [ ] Continuous scan UI
- [ ] `label.batch_max_images: 30`
- [ ] Backend stress test (Celery queue, R2 multipart)
- [ ] Mobile dogfood smoke test
- [ ] CI green

### Block H — V2 backlog (no V1 checkboxes)
