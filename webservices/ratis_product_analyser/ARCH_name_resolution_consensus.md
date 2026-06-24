---
type: sub-arch
service: ratis_product_analyser
parent: ARCH_PRODUCT_ANALYSER
related: [ARCH_consensus, ARCH_receipt_pipeline, ARCH_OCR_LLM_BRIDGE]
status: in-progress
tags: [consensus, name-resolution, matcher, crowdsourcing, knowledge, product-analyser]
business_domain: pricing
rgpd_concern: false
updated: 2026-05-02
---

# ratis_product_analyser — Name Resolution Consensus (NRC)

> Crowdsourced consensus `(store_id, normalized_label) → product_ean` that resolves receipt scans via `product_name_resolutions` (VERIFIED). Matcher redesigned to consensus-only on 2026-05-02: `products` becomes a lexical dictionary, no longer a fuzzy matcher. Blocks A-D done, E planned, F in-progress.
> @tags: consensus name-resolution matcher crowdsourcing knowledge product-analyser product_name_resolutions verified consensus-only refonte 2026-05
> @status: EN-COURS
> @subs: auto

> Parent: [[ARCH_PRODUCT_ANALYSER]] · Relations: [[ARCH_consensus]] (price), [[ARCH_receipt_pipeline]] (Phase 3 matcher), [[ARCH_OCR_LLM_BRIDGE]]

> Status: 🚧 Blocks A-D done · consensus-only redesign landed 2026-05-02 · block E planned · block F in-progress.
> Branch: `feat/nrc-A-schema` (block A) · `refactor/matcher-consensus-only` (redesign)

---

## Philosophy (redesign 2026-05-02)

**The matcher NO LONGER attempts to attach an EAN to a scan via the `products` table.** OFF data + supermarket abbreviations are too noisy (e.g. 30+ generic "Hipro" entries) to produce a reliable fuzzy match at the product level.

Consequences:

- `products` becomes a **lexical dictionary**, used exclusively by `_normalize_text` at the **token** level to clean up OCR artifacts (character substitutions, known abbreviations). Never as a full-product matcher.
- The only `scan → EAN` resolution path is a `VERIFIED` row in `product_name_resolutions` (crowdsourced consensus, blocks A-C).
- For unresolved scans, the UI displays the `cleaned_label` (= `scanned_name` post-`_normalize_text`) rather than an incorrect EAN from a false fuzzy match.
- The green "matched" icon remains exclusively consensus-driven.
- The legacy cascade (observed_names → exact products.name → fuzzy + gates) is removed. The matcher becomes (3 steps: normalize → exact consensus → fuzzy consensus fallback):

```python
def match_product_v2(db, *, scanned_name, store_id) -> MatchResult:
    cleaned_label = _normalize_text(db, scanned_name)
    if store_id is None:
        return MatchResult(ean=None, ..., normalized_label=cleaned_label)

    # Step 1 — exact consensus lookup
    consensus = get_consensus_for_label(db, store_id, cleaned_label)
    if consensus is not None and consensus.state == ConsensusState.VERIFIED:
        return MatchResult(ean=consensus.ean, method='consensus_match',
                            confidence='verified', normalized_label=cleaned_label)

    # Step 2 — fuzzy consensus fallback (catches OCR variants pre-knowledge)
    fuzzy = find_fuzzy_verified_consensus(db, store_id=store_id, cleaned_label=cleaned_label)
    if fuzzy is not None:
        return MatchResult(ean=fuzzy.ean, method='consensus_match',
                            confidence='verified', normalized_label=cleaned_label)
                            # ↑ cleaned_label, NOT fuzzy-matched label : ledger writes
                            #   stay anchored on the raw scanned text.

    return MatchResult(ean=None, ..., normalized_label=cleaned_label)
```

The **fuzzy fallback** (step 2, added 2026-05-02 PM via PR fuzzy-consensus-fallback) fixes a limitation: if OCR knowledge has not yet corrected a label (e.g. `HIPROA BRE SAV FRSE` not yet mapped to `HIPRO BRE SAV FRSE`), the `cleaned_label` remains the raw OCR output and the exact lookup misses the consensus seeded on the corrected form. The fuzzy lookup with strict guards catches this case — without risking leaking across genuine product variants (strawberry vs. vanilla = sim ~0.61, below the threshold).

**Guards**:
- `ABS(LENGTH(label) - LENGTH(cleaned_label)) ≤ fuzzy_label_max_len_diff` (default 2)
- `similarity(label, cleaned_label) > fuzzy_label_min_similarity` (default 0.80)
- Only `VERIFIED` consensus rows are returned (PENDING/CONTROVERSE/UNVERIFIED skip)

**Empirical validation** (real pg_trgm, 2026-05-02):
| Comparison | len_diff | similarity | verdict |
|---|---|---|---|
| `HIPROA BRE SAV FRSE` vs `HIPRO BRE SAV FRSE` (OCR variant) | 1 | 0.857 | ✅ accept |
| `HIPRO BRE SAV FRSE` vs `HIPRO A BRE SAV FRSE` (OCR variant, space) | 2 | 0.905 | ✅ accept |
| `HIPRO A BRE SAV VAN` vs `HIPRO A BRE SAV FRSE` (genuine variant) | 1 | 0.640 | ❌ reject |
| `HIPRO BRE SAV VAN` vs `HIPRO BRE SAV FRSE` (genuine variant) | 1 | 0.609 | ❌ reject |

- The `scans.candidate_eans` column (top-3 fuzzy fallback) is dropped — no more producer, no more consumer.
- `MatchResult.candidates` field removed from the Pydantic schema.
- i18n of the lexical dictionary is encapsulated in `_dictionary_columns_for_locale("FR")` which returns `[product_name_fr, name]` — V2+ will add other countries via this function.
- Settings cleaned up: `fuzzy.threshold_*_tokens`, `fuzzy.ambiguity_gap`, `fuzzy.token_overlap_*`, `fuzzy.stopwords_fr`, `fuzzy.min_token_length`, `fuzzy.token_significant_min_length` — all removed. Remaining: `fuzzy.max_edit_distance_per_word` and `fuzzy.word_match_min_ratio` (used at token level only).
- Prod datafix: `db/datafixes/2026-05-02_reset_legacy_fuzzy_strict_scans.sql` — reset scans with `match_method='fuzzy_strict'` to `unresolved` (purge of false OFF matches).

---

## Genesis

The pipeline_v3 matcher (PR #199, ARCH_receipt_pipeline § Phase 3) currently accepts a strict fuzzy match if `top1 ≥ fuzzy_threshold` (0.75) — without crowd validation. This works in most cases but opens the door to a silent false match when OFF data is bad.

**Real case observed (alpha 2026-04)**: OCR text `"HIPRO A BRE SAV VAN"` (4-5 tokens, Intermarché receipt) matches `products.name='Hipro'` (1 token, crappy OFF data) at score `~1.00` because pg_trgm `word_similarity` finds "HIPRO" verbatim in the query. Result: silent false match, the user sees a wrong product name in their scan history.

**Product decision** (validated by user-product-owner):
> **Green icon (matched verified) = crowdsourced consensus only.** Fuzzy-OFF alone is never sufficient to promote a scan to `matched`.

This ARCH establishes the crowd validation contract `(store_id, normalized_label) → product_ean`.

---

## Implementation plan by blocks

| Block | Scope | Status |
|---|---|---|
| **A** — schema + ARCH + read-only repo | Migration `product_name_resolutions` + `scans.candidate_eans` JSONB · SQLAlchemy model · settings `name_resolution_consensus` · read-only repo (`get_consensus_for_label`, `list_divergent_labels`, `list_unmatched_labels`) | ✅ done (PR #230) |
| **B** — matcher cascade | `worker/pipeline/matcher.py` cascade `match_product_v2` consults consensus BEFORE legacy steps · adds GATE B token-overlap (anti-Hipro) · top-3 candidates returned on fallback · compat wrapper `match_product` translates NRC methods to legacy CHECK | ✅ done (PR #233 + #236 ConsensusState refactor) · ✅ redesigned consensus-only (2026-05-02 — drop product-level cascade, products = dictionary, drop candidate_eans + MatchResult.candidates) |
| **C** — write functions + barcode_service | `record_resolution(scan_id, store_id, label, ean, user_id, method)` · hook `barcode_service` (PA `/scan-barcode` → method='barcode') · hook v2 `receipt_task` (`fuzzy_pending` / `observed_name`) · hook `PATCH /admin/scans/{id}` (`manual_admin`) · `was_ever_verified()` + UNVERIFIED detection in `_evaluate` · audit event `consensus_state_changed` + challengers payload · partial index `idx_pal_consensus_state_changed` | ✅ done (PR feat/nrc-C-writes) |
| **D** — admin endpoints + mini UI | `GET /admin/name-resolutions/queue` (state=unverified\|controverse\|all + unverified-first sort) · `GET /admin/name-resolutions/unmatched` · `GET /admin/name-resolutions/{store}/{label}` (detail) · `POST /admin/name-resolutions/resolve` (force EAN, weight 5×) · `POST /admin/name-resolutions/reject-challenges` (re-promote prev EAN, audit `action=challenges_rejected`) · `POST /admin/name-resolutions/{store}/{label}/escalate` (flag-only) · mini UI pages `/admin/ui/name-resolutions/queue` + `/admin/ui/name-resolutions/{store}/{label}` + dashboard counter tile · seed user `RTS-ADMIN0` (provider='internal') | ✅ done |
| **E** — frontend | Scan history iconography (`green` / `orange` / `red`) reflecting `matched verified / pending / unresolved` · admin feedback queue exposed in mini-UI | ⏳ planifié |
| **F** — observed_names + cleanup | Data migration: reclass legacy `fuzzy_strict matched/accepted → pending` · backfill ledger from historic `barcode` scans (UPPER(TRIM(scanned_name)) = `normalized_label`) · `COMMENT ON VIEW product_observed_names` deprecation marker (physical drop = V2 post-beta) · drop deprecated `match_product` wrapper = deferred to a dedicated PR (4 prod call sites to migrate = PR follow-up) | 🚧 in-progress |

## Sub-ARCHs

- [[ARCH_cross_retailer_consensus]] — Cross-retailer consensus + ESL elevated as source-of-truth. Redesign of the aggregation key `(store_id, normalized_label)` → `(retailer_id, source_type, normalized_label)` + addition of the ESL flow (`source_type='esl'`, quorum=2) + matcher stage 7a (cross-source exact ticket↔ESL). Implementation plan A-H, status planned 2026-05-02. See the dedicated ARCH for decision details, target DB schema, and block breakdown.

> Block A produces the **data foundation** — no runtime logic is wired yet. Write-paths are **explicitly out-of-scope** (Block C).

---

## Implementation checklist (block A)

**Base checklist — to keep in every ARCH:**
- [x] ARCH created
- [x] Alembic migration created and verified
- [x] SQLAlchemy models updated
- [x] Repository — read-only functions (write = block C)
- [ ] Service — business logic (block B/C)
- [ ] Route — endpoint (block D)
- [x] Tests written (TDD — before the code) for read functions
- [x] `ratis_settings.json` updated
- [ ] `pg_dump > db/schema.sql` after migration (orchestrator post-merge)
- [x] `ruff check --fix` clean
- [ ] CI pipeline green (check after push)

**Custom checklist — block A items:**
- [x] Table `product_name_resolutions` (append-only ledger)
- [x] Column `scans.candidate_eans JSONB` (top-3 fuzzy fallback)
- [x] CHECK constraint on `match_method ∈ {barcode, manual_admin, fuzzy_pending, observed_name}`
- [x] UNIQUE (scan_id, normalized_label) — anti-duplicate per scan
- [x] Index (store_id, normalized_label) for consensus lookup
- [x] Function `get_consensus_for_label` returns `ConsensusState` enum (refactor: replaces the old booleans `is_verified` / `is_divergent` — see § "Derived states")
- [x] Function `list_divergent_labels` paginated (admin queue input)
- [x] Function `list_unmatched_labels` paginated (admin queue input)
- [x] `admin_validation_weight` weighting applied in consensus logic
- [x] Placeholder file `# TODO Block C` for write-functions (no code written)

> ⚠️ One item at a time. Do not move to the next without finishing the current one.

---

## Index

- [Genesis](#genesis)
- [Implementation plan by blocks](#implementation-plan-by-blocks)
- [Principle](#principle)
- [DB Schema](#db-schema)
- [Promotion `pending` → `verified`](#promotion-pending--verified)
- [Divergence detection](#divergence-detection)
- [Matcher (consensus-only — redesign 2026-05-02)](#matcher-consensus-only--redesign-2026-05-02)
- [Parameters `ratis_settings.json`](#parameters-ratis_settingsjson)
- [Append-only — never delete](#append-only--never-delete)
- [Out of scope](#out-of-scope)
- [Glossary](#glossary)

---

## Principle

A **name resolution** is a tuple `(store_id, normalized_label) → product_ean` validated by a **real** user action:

- `barcode` — the user scanned the physical barcode of the product after seeing the receipt scan with this label
- `manual_admin` — an admin forced the resolution via the back-office
- `fuzzy_pending` — unvalidated fuzzy entry (read-only matcher fallback, does NOT contribute to consensus)
- `observed_name` — historical entry reconstructed from legacy `scans` (block F migration only, NOT generated at runtime)

**Promotion to `verified`**: ≥ N distinct users (default 3) converge on the same EAN, AND the convergence is clear (top1 ≥ 80% of votes AND ≥ 2× top2). Otherwise → remains `pending` (orange); if divergent (e.g. 7/3) → flag `divergent` for the admin queue.

> A name consensus is **per-store**: "HIPRO A BRE SAV VAN" can resolve to EAN-X at Intermarché and EAN-Y at Carrefour if the picking differs. This is a feature, not a bug.

---

## DB Schema

```sql
CREATE TABLE product_name_resolutions (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  scan_id UUID NOT NULL REFERENCES scans(id) ON DELETE CASCADE,
  store_id UUID NOT NULL REFERENCES stores(id),
  normalized_label TEXT NOT NULL,
  product_ean TEXT NOT NULL,
  user_id UUID NOT NULL REFERENCES users(id),
  match_method TEXT NOT NULL,
  resolved_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  CONSTRAINT pnr_match_method_check
    CHECK (match_method IN ('barcode', 'manual_admin', 'fuzzy_pending', 'observed_name'))
);

CREATE INDEX idx_pnr_consensus ON product_name_resolutions (store_id, normalized_label);
CREATE UNIQUE INDEX idx_pnr_scan_label ON product_name_resolutions (scan_id, normalized_label);
CREATE INDEX idx_pnr_user ON product_name_resolutions (user_id);

ALTER TABLE scans ADD COLUMN candidate_eans JSONB;
-- format : [{"ean": "...", "score": 0.78}, ...] top-3 fuzzy fallback candidates
-- NULL if the scan has a strict match OR verified consensus.
-- Populated by block B when the cascade falls through to fuzzy fallback.
```

**Key decisions**:

- `scan_id ON DELETE CASCADE`: if a scan is deleted (GDPR, account-delete anonymises), its validations go too. The ledger maintains referential integrity.
- `store_id ON DELETE RESTRICT` (default): a store referenced by resolutions must not be able to disappear silently. Stores are `is_disabled`, not deleted (R05).
- `UNIQUE (scan_id, normalized_label)`: the same scan can only produce one resolution for a given label — prevents double-validation bugs.
- `(store_id, normalized_label)` indexed for O(log n) consensus lookup (matcher cascade = hot path).
- `(user_id)` indexed for eventual anti-fraud (a user validating too quickly, etc. — out-of-scope V1).
- **Append-only**: no UNIQUE constraint on `(store_id, normalized_label, product_ean)` — multiple validations are accepted even if conflicting (precisely to be able to detect divergences).

---

## Promotion `pending` → `verified`

For a given `(store_id, normalized_label)`:

1. Count votes by `product_ean`, **each vote weighted**:
   - `barcode` → weight 1
   - `manual_admin` → weight `admin_validation_weight` (default 5)
   - `fuzzy_pending`, `observed_name` → weight 0 (do not contribute)
2. Count **distinct users** who have validated (regardless of target EAN) — a user who scans the same barcode 3 times counts as 1.
3. If `distinct_users < min_distinct_users` (default 3) → `state = PENDING`.
4. Otherwise, examine the top1 / top2 EAN by weight:
   - `top1_pct = top1_weight / total_weight × 100`
   - If `top1_pct ≥ convergence_threshold_pct` (default 80) **AND** `top1_weight ≥ min_top1_lead_factor × top2_weight` (default 2.0) → `state = VERIFIED`.
   - Otherwise → `state = CONTROVERSE` (volume threshold reached but not clarity; cold-start divergence). Block C will distinguish `UNVERIFIED` (post-promotion fall) via the audit log.

**Examples** (with defaults: 3 users, 80%, 2.0×):

| Votes (by EAN) | Distinct users | top1_pct | top1/top2 | State |
|---|---|---|---|---|
| EAN-X: 1 user | 1 | 100% | — | `pending` (not enough users) |
| EAN-X: 3 users | 3 | 100% | ∞ | **verified** |
| EAN-X: 8, EAN-Y: 2 (10 users) | 10 | 80% | 4.0× | **verified** (just on the edge) |
| EAN-X: 7, EAN-Y: 3 (10 users) | 10 | 70% | 2.33× | `pending` + `divergent` (not convergent) |
| EAN-X: 5, EAN-Y: 5 (10 users) | 10 | 50% | 1.0× | `pending` + `divergent` (neck and neck) |
| EAN-X: 1 user `manual_admin` (weight 5) + EAN-Y: 2 users `barcode` | 3 | 71% (5/(5+2)) | 2.5× | `pending` (not convergent — admin override alone doesn't settle it when there's a user conflict) |
| EAN-X: 1 user `manual_admin` + EAN-Y: 0 | 1 | 100% | ∞ | **verified** (1 admin = strong signal, but distinct_users=1 → `pending` until 3 distinct users; case settled by block D if admin should be prioritised → pending decision) |

> **Pending block D decision**: should a `manual_admin` alone bypass `min_distinct_users`? Argument for: if an admin takes the time to validate, it's probably correct. Argument against: we want crowd data, not arbitrary admin decisions. To be decided when the admin override endpoint is coded.

---

## Divergence detection

A `(store_id, normalized_label)` is `divergent` when:
- `distinct_users ≥ min_distinct_users`, AND
- `top1_pct < convergence_threshold_pct` OR `top1/top2 < min_top1_lead_factor`

These labels feed the **admin queue** (`list_divergent_labels`) — a human settles it via override (`manual_admin`, weight 5).

> Divergence is **not an error** — it is a legitimate signal. Two very similar EANs (e.g. packaging variants) can legitimately diverge until OFF sources a better label.

### Derived states (`ConsensusState`)

The consensus calculation produces a single `ConsensusState` state (StrEnum, defined in `webservices/ratis_product_analyser/repositories/consensus_state.py`). It replaces the two historical booleans (`is_verified`, `is_divergent`) with a single exhaustive type — type-safety for consumers (matcher, admin queue, frontend), and room for the two additional states required by blocks C/D.

| State | Semantics | Condition | Frontend (block E) |
|---|---|---|---|
| `UNRESOLVED` | No contributing row in the ledger for `(store, label)` | `get_consensus_for_label` returns `None` (zero rows matching `validation_methods`) | grey / no icon |
| `PENDING` | At least 1 contributing vote but quorum not reached | `distinct_validators < min_distinct_users` | orange (waiting) |
| `CONTROVERSE` | Quorum reached but convergence fails, with no promotion history | `distinct_validators ≥ min` AND (`top1_pct < threshold` OR `top1/top2 < lead_factor`) AND `was_ever_verified() = false` | orange + admin queue |
| `UNVERIFIED` | Label was `VERIFIED` at some point in the past then fell into divergence | same convergence failures as `CONTROVERSE` BUT `was_ever_verified() = true` | red + admin alert (fraud / data-quality signal) |
| `VERIFIED` | Promoted via crowd consensus | quorum + `top1_pct ≥ threshold` + `top1/top2 ≥ lead_factor` | green icon |

**Mapping vs old booleans (refactor PR `refactor: ConsensusState enum`)**:
- `is_verified=True` ⇔ `state == VERIFIED`
- `is_divergent=True` ⇔ `state == CONTROVERSE` (cold-start; block C will add the `UNVERIFIED` branch)
- `is_verified=False` AND `is_divergent=False` ⇔ `state == PENDING`
- `get_consensus_for_label` returning `None` ⇔ `state == UNRESOLVED` (mapped by the caller)

**Detection of `UNVERIFIED` (block C — implemented)**: `was_ever_verified(store_id, label) -> bool` queries `pipeline_audit_log` for a `consensus_state_changed` event with `payload->>'to_state' = 'verified'` on the pair. If convergence fails AND the function returns `True`, `_evaluate` emits `UNVERIFIED` instead of `CONTROVERSE`. The query is accelerated by the partial index `idx_pal_consensus_state_changed` (migration `20260501_1700_nrcC`).

> **Why distinguish `CONTROVERSE` vs `UNVERIFIED`?** A cold-start divergence (two similar EANs that OFF couldn't resolve) is a neutral business signal. A post-promotion divergence is an **alert signal**: either fraud (an attacker inflating votes towards a false EAN), or OFF data drift. The admin queue must prioritise `UNVERIFIED` above `CONTROVERSE`.

---

## Matcher (consensus-only — redesign 2026-05-02)

> Implemented in `webservices/ratis_product_analyser/worker/pipeline/matcher.py`. Public entry point: `match_product_v2(db, *, scanned_name, store_id) -> MatchResult`. Return type: `worker/pipeline/match_result.py`. Legacy compat wrapper: `match_product(...) -> Optional[tuple[str, str]]` (to be removed in a follow-up PR).

```
scanned_name + store_id
       │
       ▼
┌─ 1. NORMALIZE (_normalize_text) ────────────────────────┐
│  Token-by-token cleanup via ocr_knowledge cache +       │
│  pg_trgm word_similarity against the lexical            │
│  dictionary of products (product_name_fr ∪ name).       │
│  → cleaned_label (= MatchResult.normalized_label)       │
└─────────────────────────────────────────────────────────┘
       │
       ▼
┌─ 2. CONSENSUS LOOKUP (sole EAN resolution path) ───────┐
│  get_consensus_for_label(store_id, cleaned_label)       │
│  → VERIFIED  → MatchResult(method='consensus_match',    │
│                            confidence='verified',       │
│                            ean=consensus.top1)          │
│  → other / None → MatchResult(ean=None,                 │
│                                normalized_label=...)    │
└─────────────────────────────────────────────────────────┘
```

**No more cascade.** The old steps 3-5 (observed_names, exact products.name, fuzzy with gates) are removed. Only a crowdsourced `VERIFIED` consensus produces an EAN.

**Compat wrapper** (`match_product`): verified consensus → `(ean, 'fuzzy_confirmed')`, otherwise `None`. Maps `consensus_match` → `fuzzy_confirmed` to remain compatible with the CHECK `ck_scans_match_method_v3`. Legacy call sites (4 files: `services/barcode_service.py`, `repositories/scan_repository.py`, `worker/label_task.py`, `worker/pipeline/local_prefilter.py`) continue to work — they degrade gracefully to `None` when no consensus, which is the expected behaviour post-redesign.

**Forbidden anti-pattern**: no product-level fuzzy similarity can promote a scan to `matched/verified` without crowdsourced consensus. The `products` table remains a **lexical dictionary** consulted exclusively at the **token** level by `_normalize_text`.

### Implementation checklist (block B)

- [x] Type `MatchResult` + `FuzzyCandidate` (Pydantic frozen) in `worker/pipeline/match_result.py`
- [x] Helper `_token_overlap_passes(product_name, query)` (gate B anti-Hipro)
- [x] Helper `_significant_tokens(text)` (sig = len ≥ `token_significant_min_length` && not in `stopwords_fr`)
- [x] Helper `_fuzzy_top_n_candidates(db, label, top_n=3)`
- [x] Step `_consensus_step(db, store_id, normalized_label)` — verified/divergent/pending dispatch
- [x] Step `_fuzzy_step(db, normalized_label)` — gates A+B+candidate exposure
- [x] Public cascade `match_product_v2(db, *, scanned_name, store_id) -> MatchResult | None`
- [x] Compat wrapper `match_product(db, scanned_name, store_id, ocr_candidates=None) -> tuple|None` with mapping to legacy CHECK
- [x] Settings `fuzzy.token_overlap_min_ratio` (0.5), `fuzzy.token_significant_min_length` (4), `fuzzy.stopwords_fr` (FR stopwords + units)
- [x] TDD tests `tests/test_matcher_cascade.py` (18 tests: token-overlap unit + cascade integration + compat wrapper)
- [x] Legacy tests `test_matcher.py` + `test_matcher_fuzzy_hardening.py` still green (legacy methods preserved via compat wrapper)
- [x] Lint `ruff check` clean
- [x] CI green (PR #233)

### Implementation checklist (block C)

- [x] Migration `20260501_1700_nrcC` — partial index `idx_pal_consensus_state_changed` on `pipeline_audit_log` (predicate `event = 'consensus_state_changed'`)
- [x] `repositories/name_resolution_writes.py`: `record_resolution` (idempotent via `ON CONFLICT (scan_id, normalized_label) DO NOTHING`), `evaluate_state_transition`, `emit_consensus_state_changed_event`, helper `_collect_challengers`
- [x] `name_resolution_repository.py`: function `was_ever_verified(db, store_id, label)` + `_evaluate` extended to produce `UNVERIFIED` when `quorum + ¬convergence + was_ever_verified=true`
- [x] `worker/pipeline/matcher.py`: new public helper `match_product_with_result` that returns the `MatchResult` (to allow consumers to access `method` / `normalized_label`) — `match_product` remains a compat tuple wrapper
- [x] Hook 1 — `services/barcode_service.py`: after `resolve_scan`, call `record_resolution(... match_method='barcode')` (skip if no `store_id` / `scanned_name`)
- [x] Hook 2 — `worker/receipt_task.py` v2 match phase: call `record_resolution(... match_method=MatchResult.method)` when `MatchResult.method ∈ {'observed_name', 'fuzzy_pending'}`
- [x] Hook 3 — `routes/admin/scans.py` PATCH: call `record_resolution(... match_method='manual_admin')` when the operator forces `merged_method='manual_admin'` + `merged_ean is not None`
- [x] Audit event `consensus_state_changed` (`phase='match'`, `level='normal'`) with payload: `from_state`, `to_state`, `top1_ean`, `distinct_validators`, `convergence_pct`, `triggered_by_scan_id`, `challengers` (populated only when `to_state=unverified`)
- [x] `clock_timestamp()` (vs `now()` default) on ledger + audit log inserts → strict chronological ordering even within the same transaction (deterministic for `_last_persisted_state` and challengers)
- [x] TDD tests `tests/test_ledger_writes.py` (17 tests): record_resolution happy/idempotent/no-event-on-conflict, evaluate transitions (None→PENDING, PENDING→VERIFIED, PENDING→CONTROVERSE, VERIFIED→UNVERIFIED+challengers), was_ever_verified true/false/persists, _evaluate UNVERIFIED vs CONTROVERSE branch, payload schema, 3 call-site integrations
- [x] Block A placeholder test `test_get_consensus_unverified_after_promotion_fall` un-skipped + implemented
- [x] Alembic test `alembic/tests/test_nrc_c_audit_idx_migration.py`: index exists post-upgrade + dropped on downgrade
- [x] Lint `ruff check` clean
- [ ] CI green (check after push)
- [ ] `pg_dump > db/schema.sql` after merge (orchestrator post-merge)

### Implementation checklist (block D)

- [x] Migration `20260501_2000_nrcD` — extends `provider_check` to accept `'internal'` + seed user `RTS-ADMIN0` (`admin@ratis.internal`, `provider='internal'`, stable id `00000000-0000-0000-0000-000000ad0001`)
- [x] `services/name_resolution_admin_service.py`: `list_arbitration_queue`, `list_unmatched_queue`, `get_label_detail`, `resolve_label`, `reject_challenges`, `escalate_label` (5 ops, dataclass `QueueItem` / `UnmatchedItem`, `_get_or_create_admin_user` (idempotent: created via migration in prod, via lazy INSERT in tests)
- [x] `routes/admin/name_resolutions.py`: 5 JSON endpoints under `/api/v1/admin/name-resolutions/*` + auth gate `verify_admin_key` + `X-Admin-Operator` (on mutations)
  - [x] `GET /queue`: pagination + filters `state` (unverified/controverse/all) + `store_id`, sort `unverified-first` then `last_resolution_at desc`
  - [x] `GET /unmatched`: grouped `(store, label)`, aggregates `top_candidates` from `scans.candidate_eans`
  - [x] `GET /{store_id}/{normalized_label}`: detail (resolutions + events timeline + `is_challenger` flag)
  - [x] `POST /resolve`: `record_resolution(... method=manual_admin)` + separate operator audit event (event=`admin_name_resolution_resolve`, payload=`{operator, operator_note, target_ean, anchored_scan_id}`)
  - [x] `POST /reject-challenges`: 422 `state_mismatch` if state ≠ unverified · re-promotion via `record_resolution` on `previously_verified_ean` · audit `consensus_state_changed` with `extra_payload={action: challenges_rejected, rejected_user_ids: [...], operator, operator_note}`
  - [x] `POST /{store_id}/{normalized_label}/escalate`: flag-only, event=`admin_name_resolution_escalate`
- [x] Helper `record_resolution` adapted: if all scans for `(store, label)` already have a ledger row, fallback to a synthetic `scans` row owned by `RTS-ADMIN0` (status=`pending`, scan_type=`manual`, price/quantity=0/1) — otherwise `ON CONFLICT DO NOTHING` silently skips the admin override
- [x] `emit_consensus_state_changed_event` extended with `extra_payload: dict | None` parameter to allow injection of the `challenges_rejected` payload (Block C otherwise unchanged)
- [x] Mini admin UI:
  - [x] `admin_ui/templates/name_resolutions_queue.html`: table with state+store filters, color-coded badges (red=unverified, amber=controverse), inline "Validate top1" + "Reject chal." buttons (visible only if state=unverified)
  - [x] `admin_ui/templates/name_resolution_detail.html`: events timeline + resolutions table with challenger rows highlighted + form action target_ean + form reject-challenges (if state=unverified)
  - [x] `admin_ui/routes.py`: pages `GET /admin/ui/name-resolutions/queue`, `GET /admin/ui/name-resolutions/{store_id}/{label:path}`, `POST /admin/ui/name-resolutions/resolve`, `POST /admin/ui/name-resolutions/reject-challenges` (cookie `get_admin_session` dep, redirect_to=queue|detail)
  - [x] `base.html` nav link "Arbitrage NRC" + `index.html` dashboard tile with counter (call to `list_arbitration_queue` limit=1 for the total)
- [x] TDD tests `tests/test_admin_name_resolutions.py` (31 tests): auth gate (5), `GET /queue` (10 — empty/controverse/unverified/state filters/sort/pagination/store filter/top_eans+pct/product_name lookup), `GET /unmatched` (3 — happy path/exclusion resolved/aggregation), `GET /detail` (2 — happy path challengers flag/404), `POST /resolve` (5 — happy path/404/idempotent/audit event/note length), `POST /reject-challenges` (3 — state gate/happy path/audit payload), `POST /escalate` (2 — happy path/404)
- [x] UI tests `tests/test_admin_ui_name_resolutions.py` (16 tests): auth gate (3), queue page (4), detail page (2), POST resolve (2), POST reject-challenges (2), dashboard counter (2), nav link (1)
- [x] Lint `ruff check` clean
- [ ] CI green (check after push)
- [ ] `pg_dump > db/schema.sql` after merge (orchestrator post-merge)

**Block D decisions:**
- *Convention for user_id on admin actions*: seed `RTS-ADMIN0` rather than NULL. Preserves the NOT NULL FK + allows filtering "admin actions" via simple `WHERE user_id = (SELECT id FROM users WHERE support_id = 'RTS-ADMIN0')` without adding an `is_admin_action` boolean column. `provider='internal'` is a new enum member — the old CHECK (google/apple/email) is extended, not rewritten.
- *Sentinel UUID* `00000000-0000-0000-0000-000000ad0001`: arbitrary but stable choice, documented in the migration. Used in practice via `support_id='RTS-ADMIN0'` lookup (the hardcoded UUID is a migration fallback).
- *Synthetic scan fallback* in `resolve_label` / `reject_challenges`: necessary because of the UNIQUE `(scan_id, normalized_label)` constraint on `product_name_resolutions`. If all existing scans already have a ledger row, a synthetic scan must be created (status=`pending`, scan_type=`manual`, owned by admin) to anchor the admin override. This row has no effect on the pipeline (pending = no cycle), only on the ledger.
- *Separate audit event for resolve* (`admin_name_resolution_resolve` `phase='manual'`) in addition to the automatic `consensus_state_changed`: captures the operator intent (handle + note) even when the state does not change (e.g. admin re-affirms an already verified label).
- *Reject-challenges payload*: the `consensus_state_changed` event carries an `extra_payload` `{action: "challenges_rejected", rejected_user_ids: [...], operator, operator_note}` documenting the operator intent in addition to the state change. Clean solution vs alternatively emitting 2 events (state-change + action) — a single composite event stays coherent with the timeline and is simpler to parse on the UI side.

### Implementation checklist (block F)

- [x] Migration `20260502_1000_nrcF` — UPDATE scans `matched + fuzzy_strict → pending` (DB-state, not user-facing) with idempotent WHERE-clause. Scope narrowed (vs original brief `matched/accepted`) because the DB trigger `fn_check_scan_status_transition` forbids any transition out of `accepted` (load-bearing invariant). See migration docstring § (1).
- [x] Migration `20260502_1000_nrcF` — INSERT-SELECT from barcode scans (`store_id IS NOT NULL` + `scanned_name IS NOT NULL` + `product_ean IS NOT NULL` + `user_id IS NOT NULL`) with `ON CONFLICT (scan_id, normalized_label) DO NOTHING`
- [x] `COMMENT ON VIEW product_observed_names` (deprecation marker — physical drop scheduled V2 post-beta)
- [x] TDD tests `alembic/tests/test_nrcF_migration.py` (8 tests: reclass matched/accepted, negative control non-fuzzy, backfill happy/no-store/non-barcode, deprecation comment, idempotence)
- [x] **Drop deprecated `match_product` wrapper + drop legacy matcher.py + match_result.py + redesign pipeline_v3/match.py** (redesign 2026-05-02 — PR pipeline_v3 consensus-only). The 4 prod call sites have been migrated:
  - `services/barcode_service.py`: `match_method='barcode'` direct (user-scan = verification, drop coherence check)
  - `repositories/scan_repository.py` (`process_pending_items`): exact consensus + fuzzy fallback
  - `worker/label_task.py`: exact consensus + fuzzy fallback (with store_id check)
  - `worker/pipeline/local_prefilter.py`: Stage 1d removed (was dead code with `store_id=None` post-redesign). 1a/1b/1c knowledge curated preserved.
  - **Critical bonus**: redesign of `pipeline_v3/match.py` (the pipeline_v3 matcher active in prod was still doing `fuzzy_strict` — the previous SA had redesigned the wrong file). New cascade: `barcode → knowledge curated → exact consensus → fuzzy consensus → STOP`. `MatchMethod` loses `fuzzy_strict`, gains `consensus_match`. Migration `20260502_1700_consmatch` adds `consensus_match` to the CHECK constraint.
  - **Helpers extracted**: `worker/pipeline/normalize.py` now hosts `normalize_text` + `lookup_knowledge_corrected` + fuzzy helpers (Levenshtein, `_best_matching_word`, …) that were in `matcher.py`. All call sites (admin/scans, barcode_service, scan_repository, label_task, receipt_task, local_prefilter) updated.
  - **`_LEGACY_METHOD_MAP`** removed from receipt_task v2 (with the pipeline_v3 redesign the v2 path now only produces `consensus_match` or None — no mapping needed).
- [x] Drop matcher cascade (redesign 2026-05-02) — drop `_lookup_observed`, `_exact_product_name`, `_fuzzy_match`, `_FUZZY_SQL`, `_fuzzy_top_n_candidates`, `_token_overlap_passes`, `_significant_tokens`; drop `MatchResult.candidates` field; drop column `scans.candidate_eans` (migration `20260502_1500_dropce`); cleanup dead `fuzzy.*` settings; datafix `2026-05-02_reset_legacy_fuzzy_strict_scans.sql`.
- [ ] Physical drop of the `product_observed_names` table/view — **V2 scope** (post-beta). No runtime consumer since the 2026-05-02 redesign, but physical drop deferred to avoid breaking third-party tools that might read the view.
- [ ] CI green (check after push)
- [ ] `pg_dump > db/schema.sql` after merge (orchestrator post-merge)

**Block F decisions:**
- *Reclass scope narrowed to `matched` only (vs brief `matched`+`accepted`)* : the DB trigger `fn_check_scan_status_transition` raises `Forbidden transition: an accepted scan cannot change status` on any move out of `accepted`. This trigger is a load-bearing user-facing invariant (`accepted` scans feed cashback/receipt history). Temporarily disabling it = R33-violating workaround; we take the clean path. In practice pipeline_v3 never directly produces `accepted` for fuzzy_strict — this status is only reached via the user-confirm flow which itself goes through this trigger. The effective scope (`matched` only) therefore covers 100% of the real case.
- *Normalize approach for barcode backfill*: `_normalize_text` (matcher.py) performs runtime `ocr_knowledge` lookups, not portable in pure SQL, and has side-effects (UPSERTs on `ocr_knowledge`) that a migration must never trigger. Choice: `UPPER(TRIM(scanned_name))` as the deterministic `normalized_label`. Trade-off documented: future live writes with OCR correction will have a different `normalized_label` → there will be 2 ledger rows for the same `(scan_id, ...)` but on 2 distinct labels (UNIQUE index `(scan_id, normalized_label)` allows this). Strictly additive, never destructive. Cf migration docstring § (2).
- *View vs table for `product_observed_names`*: this is a **VIEW** materialised at INSERT-time from `scans` (filter `status='accepted'`), not a table. A `DROP TABLE` would have failed. Choice: `COMMENT ON VIEW` deprecation-only, physical drop deferred to V2 when the matcher Step 3 cascade has migrated to ledger reads.
- *Drop of `match_product` wrapper deferred*: 4 active prod call sites (`services/barcode_service.py:130`, `repositories/scan_repository.py:257`, `worker/label_task.py:269`, `worker/pipeline/local_prefilter.py:213`) + 2 test files that patch by path `repositories.scan_repository.match_product`. Clean migration = rethinking the `(ean, method)` tuple contract at each consumer (some use `method` to branch code-paths). Out of scope for a data-migration PR; better served by a dedicated "kill compat wrapper" PR with a line-by-line audit. R33: no workaround, either do it cleanly or flag it → flagged here.
- *Asymmetric downgrade*: the view falls back to `COMMENT NULL` but the fuzzy_strict→pending reclass and the ledger backfill are **not** undone (impossible without a snapshot). Documented in the migration's `downgrade()` docstring.

---

## Parameters `ratis_settings.json`

```json
"name_resolution_consensus": {
    "min_distinct_users": 3,
    "validation_methods": ["barcode", "manual_admin"],
    "convergence_threshold_pct": 80,
    "min_top1_lead_factor": 2.0,
    "admin_validation_weight": 5,
    "fuzzy_label_max_len_diff": 2,         // matcher fuzzy fallback (redesign 2026-05-02)
    "fuzzy_label_min_similarity": 0.80     // matcher fuzzy fallback (redesign 2026-05-02)
},
"fuzzy": {
    "ambiguity_gap": 0.10,                      // block B GATE A
    "token_overlap_min_ratio": 0.5,             // block B GATE B (anti-Hipro)
    "token_significant_min_length": 4,
    "stopwords_fr": ["AVEC", "POUR", "SANS", ...]
}
```

- `min_distinct_users`: threshold of distinct users required to consider promotion (default 3). To be tuned on alpha/real data.
- `validation_methods`: only these methods contribute to the consensus (with their weight). Others (`fuzzy_pending`, `observed_name`) are stored but ignored in the calculation.
- `convergence_threshold_pct`: minimum `top1_pct` for `verified` (anti-neck-and-neck by volume).
- `min_top1_lead_factor`: minimum multiplier top1 vs top2 (anti-neck-and-neck by ratio).
- `admin_validation_weight`: weight of a `manual_admin` vote (default 5). Justification: an admin has checked manually, their vote is worth more than a quick barcode scan where the user may have picked up the wrong product.
- `fuzzy_label_max_len_diff`: maximum length difference accepted by the fuzzy fallback matcher (default 2). Beyond this, the candidate is rejected without computing similarity.
- `fuzzy_label_min_similarity`: minimum pg_trgm `similarity()` threshold for the fuzzy fallback matcher (default 0.80). Chosen to leave a comfortable gap between genuine OCR variants (0.85+) and genuine product variants (0.65-).

---

## Append-only — never delete

`product_name_resolutions` is an **immutable ledger**. No `UPDATE`, no `DELETE` (except via the `scan_id ON DELETE CASCADE` which follows GDPR account-delete anonymisations). Conflicts are kept for audit.

> If block B introduces a false positive (a user incorrectly scanned a barcode), the correction goes through a new `manual_admin` (weight 5) that re-settles it — not through DELETE.

---

## Out of scope

- **Anti-fraud `user_id`**: detection of abusive behaviour (a user validating 100 labels in 5 minutes). V2 — for V1 we trust the crowd.
- **Temporal weighting**: a vote one year old is worth the same as one from today. `price_consensus` has temporal weighting (decay), here not — a product name does not change over time. If a product genuinely changes name at a store, a new convergent crowd will emerge.
- **Frontend**: matched/pending/unresolved iconography → block E.
- **Admin endpoints** → block D.
- **`product_observed_names` migration** → block F.
- **Async batch recalculation**: no dedicated batch. Consensus is computed on the fly in `get_consensus_for_label`. If the table grows (>10M rows) we will add a materialised cache in V2.

### Deferred V1+ — TODOs in code (backref for sprint planning)

- `webservices/ratis_product_analyser/repositories/consensus_state.py:42` — `UNVERIFIED` state never produced today. Block C: detect via `was_ever_verified()` against the audit log (fraud / data-quality signal). Surface in admin queue.
- `webservices/ratis_product_analyser/repositories/name_resolution_writes.py:470` — fraud aggregates by challenger (barcode/manual ratio + `past_challenges_count`) to enrich the admin queue. V1+ after alpha telemetry on challenge volume.

---

## Glossary

- **NRC**: Name Resolution Consensus (this feature).
- **normalized_label**: `parsed_item.normalized_text` post-Phase 2 — uppercase, unaccent, whitespace collapsed. The same string across two scans = candidate match.
- **validation_method**: means by which a user has validated (`barcode`, `manual_admin`, etc.).
- **verified**: promoted via crowd consensus (≥ N users + convergence). Displays green icon in frontend.
- **divergent**: user threshold reached but convergence insufficient. Displays orange + admin queue.
- **append-only ledger**: table where UPDATE/DELETE never happens (except GDPR cascade). All votes remain in history.
- **top-3 candidates** (`scans.candidate_eans`): top-3 EANs returned by fuzzy when no consensus. Used to validate that the user's barcode scan is in the list — otherwise `suspect → admin queue` (block C).
