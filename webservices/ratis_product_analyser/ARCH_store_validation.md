---
type: sub-arch
service: ratis_product_analyser
parent: ARCH_PRODUCT_ANALYSER
related: [ARCH_store_resolution, ARCH_consensus, ARCH_BATCH_CONSENSUS, ARCH_cashback, ARCH_scan_history]
status: planned
tags: [store-validation, user-suggested, consensus, anti-abuse, cashback-retroactive, product-analyser]
business_domain: pricing
rgpd_concern: true
updated: 2026-04-29
---

# ratis_product_analyser — ARCH store validation (user-suggested → confirmed via consensus)

> Workflow `user_suggested → confirmed` for stores suggested via OCR: `stores.validation_status`, `stores.suggested_by_user_id`, flip via consensus batch phase 3. Retroactive cashback for pre-confirmation scans. **Planned** 2026-04-29.
> @tags: store-validation user-suggested consensus anti-abuse cashback-retroactive product-analyser pr-b validation_status suggested_by_user_id confirm-store
> @status: PLANIFIÉ
> @subs: auto

> **Note (2026-05-15)**: store proximity search now goes through PostGIS + `ratis_core.geo`. See `ARCH_geo.md`.

> Parent: [[ARCH_PRODUCT_ANALYSER]] · Relations: [[ARCH_store_resolution]], [[ARCH_consensus]], [[ARCH_BATCH_CONSENSUS]], [[ARCH_cashback]], [[ARCH_scan_history]]

> Status: 📋 Planned (PR-B in design 2026-04-29). Pre-requisite: removal of `identify-store` (chore PR pre-PR-B).
> Branch: `feat/store-validation-user-suggested`

> **Chronological pre-requisite**: this ARCH assumes the dead endpoint `POST /scan/receipt/{id}/identify-store` (Option B from [[ARCH_store_resolution]]) has been removed. That UX (free-text user input) contradicts the anti-abuse stance adopted on 2026-04-29.

---

## Implementation Checklist

**Base checklist**:
- [x] Alembic migration created and verified (`stores.validation_status`, `stores.suggested_by_user_id`) — Phase 1
- [x] SQLAlchemy models updated (`ratis_core/models/store.py` + new `StoreValidationHistory`) — Phase 1
- [x] Repository — consensus count functions + status flip (Phase 2 — batch consensus phase 3)
- [x] Service — `confirm_store_from_ocr()` (`services/store_confirmation_service.py`) — Phase 1
- [x] Route — `POST /scan/receipt/{id}/confirm-store` — Phase 1
- [x] TDD tests (before code) — Phase 1 (test_confirm_store.py + test_cashback_gating_validation.py + test_cashback_retroactive.py)
- [ ] `conftest.py` updated if new `require_env()` — no new env var
- [x] `ratis_settings.json` extended (`store_validation.*`) — Phase 1
- [ ] `pg_dump > db/schema.sql` after migration (Phase 2 or pre-PR)
- [x] `ruff check --fix` clean — Phase 1
- [ ] CI pipeline green — verify after push

**Custom checklist**:
- [x] Migration: `stores.validation_status` (`pending`/`confirmed`/`suspicious`) + partial pending index + `stores.suggested_by_user_id` UUID NULL — Phase 1
- [x] Backfill: `UPDATE stores SET validation_status='pending' WHERE source='user_suggested'` — Phase 1 (caveat P-2 — OK alpha)
- [x] Modify `GET /scan/receipt/{id}` to expose `store_candidate_info` when status is `unknown`/`pending` — Phase 1
- [x] New endpoint `POST /scan/receipt/{id}/confirm-store` (TDD) — Phase 1
- [x] Cashback gating: add check `store.validation_status='confirmed'` (in addition to `receipt.store_status='confirmed'`) — Phase 1 (at the trigger point in `worker/receipt_task.py`, where `trigger_cashback_scan` is called)
- [x] Internal endpoint `POST /rewards/cashback/process-retroactive` (INTERNAL_API_KEY) — Phase 1
- [x] Phase 3 added to `ratis_batch_consensus` (`store_validation_phase.py`) + TDD tests (10 tests) + `consensus.py main()` orchestrated — Phase 2A
- [x] `ARCH_BATCH_CONSENSUS.md` extended — Phase 3 documented (Flow 3) — Phase 2A (tables "Tables read/written" already lists `store_validation_history`)
- [x] New table `store_validation_history` (transition audit) — Phase 1 (column named `meta` rather than `metadata` — avoid clash with `Base.metadata` SA-2.0)
- [x] Frontend: hook `useScanConfirmStore` + `<StoreConfirmationModal />` + red pencil wire — Phase 2B (`feat/store-validation-fe`)
- [x] Frontend: "Awaiting validation" badge on pending receipts — Phase 2B (`feat/store-validation-fe`)
- [ ] Terms of Service: "Store Validation" section — Phase 3
- [ ] `PROD_CHECKLIST.md` — no new env var required

> ⚠️ One item at a time. Do not move to the next one before completing the previous one.

---

## Index

- [Context & Problem](#context--problem)
- [States & lifecycle](#states--lifecycle)
- [DB Schema](#db-schema)
- [Endpoint `confirm-store`](#endpoint-confirm-store)
- [Phase 3 of `ratis_batch_consensus` (store validation)](#phase-3-of-ratis_batch_consensus-store-validation)
- [Retroactive cashback](#retroactive-cashback)
- [Frontend](#frontend)
- [Anti-abuse — defensive layers](#anti-abuse--defensive-layers)
- [`ratis_settings.json` parameters](#ratis_settingsjson-parameters)
- [Terms of Service](#terms-of-service)
- [Architecture decisions](#architecture-decisions)
- [Known pitfalls](#known-pitfalls)
- [V2+ — future extensions](#v2--future-extensions)

---

## Context & Problem

**Symptom observed in alpha**: a user scans a valid receipt. The OCR pipeline correctly parses the content (store header, items, prices, total), but no store in the `stores` table matches the extracted signals (brand_guess + phone + postal_code). The receipt remains with `store_status='unknown'` — the user does not receive their cashback and has no way to act.

**Root cause**: the `stores` table is not exhaustive. Current sources = OSM (daily sync via `ratis_batch_osm_sync`) + OFF + manual admin addition. Neighborhood grocery stores, markets, independents, and new stores are often missing. The ratio of "unknown stores" increases with user volume.

**Why this is a real problem**:
1. **User**: valid receipt, penalized for our DB gap → frustration → alpha churn
2. **Business**: cashback promise unfulfilled → loss of trust
3. **Growth**: the more users we have, the more unknown stores we discover
4. **Crowdfunding**: the pitch "the app improves with usage" relies precisely on this mechanism

**Why we can't just let users freely create a store**:
1. **Direct abuse**: fake receipt + fake ghost store → fake cashback (direct monetary loss)
2. **DB pollution**: duplicates, typos, fictitious stores → degraded consensus
3. **Legal**: cashback = monetary transaction → audit trail required

**Adopted solution**: the user **confirms** what the OCR has parsed (1 click, no free-text input) → creation of a `user_suggested` store with `validation_status='pending'`. Cashback frozen. The store automatically flips to `confirmed` when a cross-user price consensus is reached on that store (≥20 distinct products with `trust_score >= 80`). Cashback is then credited **retroactively** on all pending receipts for that store.

---

## States & lifecycle

### Store states (`stores.validation_status`)

| State | Entry trigger | Cashback eligible | Visible to user |
|---|---|---|---|
| `confirmed` | OSM-ingested OR consensus≥20 OR admin-validated | ✅ | ✅ everywhere |
| `pending` | User confirms via `confirm-store` (fresh user_suggested creation) | ❌ frozen (retroactive on flip) | ✅ with "unvalidated" badge |
| `suspicious` | `pending` for ≥6 months AND distinct EAN consensus <30 | ❌ never | ❌ hidden from user searches |

### Transition diagram

```
                                                           ┌────────────────┐
[OSM sync] ──────────────────────────────────────────────► │                │
                                                           │   confirmed    │ ◄──┐
                                                           │                │    │
                                                           └────────────────┘    │
                                                                  ▲              │
                                                                  │              │
                                                          [≥20 EAN distinct]     │
                                                          [trust_score ≥80]      │
                                                                  │              │
[user click stylo rouge]                                          │     [admin   │
       │                                                          │      manual  │
       ▼                                                          │      override│
  POST /scan/receipt/{id}/confirm-store                           │              │
       │                                                          │              │
       ▼                                                  ┌────────────────┐    │
                                                          │                │ ───┘
                                                  ┌─────► │    pending     │
                                                          │                │ ───┐
                                                          └────────────────┘    │
                                                                                 │
                                                                       [≥6mo +    │
                                                                        <30 EAN] │
                                                                                 ▼
                                                                        ┌────────────────┐
                                                                        │   suspicious   │
                                                                        │   (V2-3:       │
                                                                        │   merchant     │
                                                                        │   call)        │
                                                                        └────────────────┘
```

### Coexistence migration with `receipts.store_status`

`receipts.store_status` (ENUM `unknown`/`pending`/`confirmed`) remains unchanged — it describes the state **of the receipt** with respect to store resolution. The new `stores.validation_status` describes the state **of the store itself**.

**Consistency**:
- `receipt.store_status='unknown'` ⟹ `receipt.store_id IS NULL`
- `receipt.store_status='pending'` ⟹ `receipt.store_id` points to a store with `validation_status IN ('pending', 'suspicious')`
- `receipt.store_status='confirmed'` ⟹ `receipt.store_id` points to a store with `validation_status='confirmed'`

The **cashback gating** checks BOTH (defense in depth): `receipt.store_status='confirmed'` AND `store.validation_status='confirmed'`.

---

## DB Schema

### Alembic Migration (to create)

```python
"""store_validation_status

Revision ID: <auto>
Revises: <previous>
Create Date: 2026-04-29
"""
from alembic import op
import sqlalchemy as sa

def upgrade():
    # 1. New column with default 'confirmed' (safe for existing OSM rows)
    op.add_column(
        'stores',
        sa.Column(
            'validation_status',
            sa.String(),
            nullable=False,
            server_default='confirmed',
        ),
    )
    op.create_check_constraint(
        'stores_validation_status_check',
        'stores',
        "validation_status IN ('pending', 'confirmed', 'suspicious')",
    )

    # 2. Backfill: all existing user_suggested rows switch to pending
    op.execute(
        "UPDATE stores SET validation_status='pending' WHERE source='user_suggested'"
    )

    # 3. Audit trail: who suggested
    op.add_column(
        'stores',
        sa.Column(
            'suggested_by_user_id',
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey('users.id', ondelete='SET NULL'),
            nullable=True,
        ),
    )

    # 4. Partial index for the batch (perf: scans only pending rows)
    op.create_index(
        'idx_stores_validation_pending',
        'stores',
        ['validation_status'],
        postgresql_where=sa.text("validation_status = 'pending'"),
    )

    # 5. Audit table (transitions)
    op.create_table(
        'store_validation_history',
        sa.Column('id', sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column('store_id', sa.dialects.postgresql.UUID(as_uuid=True), sa.ForeignKey('stores.id', ondelete='CASCADE'), nullable=False),
        sa.Column('from_status', sa.String(), nullable=True),  # NULL for creation
        sa.Column('to_status', sa.String(), nullable=False),
        sa.Column('reason', sa.String(), nullable=False),  # 'user_confirmed' | 'consensus_threshold_reached' | 'suspicious_timeout' | 'admin_override'
        sa.Column('triggered_by', sa.String(), nullable=False),  # 'user:<uuid>' | 'batch:<name>' | 'admin:<uuid>'
        sa.Column('metadata', sa.dialects.postgresql.JSONB, nullable=True),  # ex: {distinct_eans_count: 20}
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index('idx_store_validation_history_store_id', 'store_validation_history', ['store_id'])

def downgrade():
    op.drop_index('idx_store_validation_history_store_id', table_name='store_validation_history')
    op.drop_table('store_validation_history')
    op.drop_index('idx_stores_validation_pending', table_name='stores')
    op.drop_column('stores', 'suggested_by_user_id')
    op.drop_constraint('stores_validation_status_check', 'stores', type_='check')
    op.drop_column('stores', 'validation_status')
```

### SQLAlchemy Model (`ratis_core/models/store.py`)

```python
class Store(Base):
    # ... existing columns
    validation_status: Mapped[str] = mapped_column(String, nullable=False, server_default='confirmed')
    suggested_by_user_id: Mapped[Optional[UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey('users.id', ondelete='SET NULL'), nullable=True)
```

### Audit model

```python
class StoreValidationHistory(Base):
    __tablename__ = 'store_validation_history'
    id: Mapped[UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    store_id: Mapped[UUID] = mapped_column(UUID(as_uuid=True), ForeignKey('stores.id', ondelete='CASCADE'), nullable=False)
    from_status: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    to_status: Mapped[str] = mapped_column(String, nullable=False)
    reason: Mapped[str] = mapped_column(String, nullable=False)
    triggered_by: Mapped[str] = mapped_column(String, nullable=False)
    metadata: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
```

---

## Endpoint `confirm-store`

### Modification `GET /scan/receipt/{id}` — expose `store_candidate_info`

When `receipt.store_status IN ('unknown', 'pending')`, add to the response payload:

```json
{
  "receipt_id": "...",
  "store_status": "unknown",
  "store_candidate_info": {
    "brand_guess": "Lidl",
    "address": "12 RUE DE LA PAIX",
    "postal_code": "75002",
    "city": "PARIS",
    "phone": "0142345678"
  },
  ...
}
```

Data source: `pending_store_candidates` table (already populated by the OCR pipeline for unresolved receipts). If the table is absent (legacy old design), fall back to `receipts.user_store_hint` or `pending_items` JSONB. The implementation SA will audit the canonical source at dev time.

If `store_candidate_info` is insufficient (missing brand_guess OR address) → do not expose the field → frontend hides the confirm option → user must re-scan.

### `POST /api/v1/scan/receipt/{receipt_id}/confirm-store`

**Auth**: JWT (`get_current_user`)
**Ownership**: `assert_owner(receipt.user_id, current_user.id)` — see KP-05
**Rate limit**: 3/minute (consistent with `/scan/receipt`)

**Request**: no body. Everything is read from the receipt + its store_candidate.

**Response 200**:
```json
{
  "store_status": "pending",
  "store_id": "uuid-of-new-user-suggested-store",
  "validation_status": "pending",
  "message": "store_pending_validation"
}
```

**Logic**:
```
1. Load the receipt (404 if absent)
2. assert_owner(receipt.user_id, current_user.id)
3. If receipt.store_status != 'unknown' → 409 receipt_already_resolved
4. Load the linked store_candidate (by receipt_id or via brand_guess + postal_code)
   → If no candidate OR candidate without brand_guess+postal → 422 insufficient_ocr_data
5. Verify that the candidate data is sufficient:
   brand_guess non-empty AND (postal_code non-empty OR address non-empty)
   → Otherwise → 422 insufficient_ocr_data
6. Create a new Store:
   - source = 'user_suggested'
   - validation_status = 'pending'
   - suggested_by_user_id = current_user.id
   - name = candidate.brand_guess
   - brand = candidate.brand_guess (may be normalized)
   - postal_code = candidate.postal_code
   - city = candidate.city
   - address = candidate.address
   - phone = candidate.phone
   - lat = 0.0, lng = 0.0  (placeholder — admin/V2 batch geocoding)
7. INSERT store_validation_history:
   - store_id = new_store.id
   - from_status = NULL
   - to_status = 'pending'
   - reason = 'user_confirmed'
   - triggered_by = f'user:{current_user.id}'
   - metadata = {receipt_id, candidate_id}
8. Update receipt:
   - receipt.store_id = new_store.id
   - receipt.store_status = 'pending'
9. process_pending_items(db, receipt) — promotion of pending items → consensus contributions start
10. db.commit()
11. DO NOT trigger cashback (frozen until validation)
12. Return 200 { store_status: "pending", store_id, validation_status, message }
```

**Error codes**:
- `404 receipt_not_found` — receipt not found
- `403 forbidden` — ownership mismatch
- `409 receipt_already_resolved` — receipt already in pending/confirmed
- `422 insufficient_ocr_data` — OCR candidate too sparse to confirm
- `422 candidate_not_found` — no store_candidate associated with the receipt

---

## Phase 3 of `ratis_batch_consensus` (store validation)

> **Decision DA-7**: the store validation logic is implemented as **Phase 3 of `ratis_batch_consensus`** (see [[ARCH_BATCH_CONSENSUS]]) rather than a separate new batch. Reason: semantically both are consensus at different granularities (price_consensus = (store, EAN), store_validation = aggregate per store). Folding guarantees temporal ordering (Phase 3 reads fresh `trust_score` values from Phase 1) and avoids infrastructure duplication (1 cron, 1 workflow). See § Architecture decisions.

### Schedule
Inherits from `ratis_batch_consensus`: daily cron **02:00 UTC** (`.github/workflows/batch_consensus.yml`, line `cron: '0 2 * * *'`). No new GitHub workflow to create.

### Position in the batch

`batch/ratis_batch_consensus/main.py` now orchestrates 3 phases in sequence, **separate transactions** (Phase 3 failure does NOT rollback Phase 1/2):

| Phase | Module | Role |
|---|---|---|
| 1 | `recalc_phase.py` (existing) | Recalc `trust_score` per row (Pattern A, Pattern B, decay, unfreeze) |
| 2 | (existing, inside Phase 1) | Freeze management + dominant_price flip |
| 3 | `store_validation_phase.py` (**NEW**) | Aggregate per pending store → flip `validation_status` + retroactive cashback |

### Phase 3 Pseudocode

```python
# batch/ratis_batch_consensus/store_validation_phase.py
def run_store_validation_phase(db: Session, settings: dict) -> dict:
    """Phase 3 — flip pending → confirmed (consensus reached) or pending → suspicious (timeout).
    
    Separate transaction from trust_score recalc. If Phase 3 fails, Phase 1/2 are NOT rolled back.
    """
    sv = settings['store_validation']
    stats = {'flipped_confirmed': 0, 'flipped_suspicious': 0, 'retroactive_cashback_calls': 0}
    
    # Sub-phase 3.1: auto-validation pending → confirmed
    pending_stores = db.query(Store).filter(Store.validation_status == 'pending').all()
    for store in pending_stores:
        distinct_eans = db.execute(text("""
            SELECT COUNT(DISTINCT product_ean)
            FROM price_consensus
            WHERE store_id = :store_id
              AND trust_score >= :min_trust
        """), {
            'store_id': store.id,
            'min_trust': sv['consensus_min_trust_score'],  # 80
        }).scalar()
        
        if distinct_eans >= sv['min_distinct_eans_for_validation']:  # 20
            store.validation_status = 'confirmed'
            db.add(StoreValidationHistory(
                store_id=store.id,
                from_status='pending',
                to_status='confirmed',
                reason='consensus_threshold_reached',
                triggered_by='batch:ratis_batch_consensus:store_validation_phase',
                metadata={'distinct_eans_count': distinct_eans},
            ))
            db.commit()
            stats['flipped_confirmed'] += 1
            
            # Trigger retroactive cashback (fire-and-forget, non-blocking for Phase 3)
            try:
                rewards_client.process_retroactive_cashback(store.id)
                stats['retroactive_cashback_calls'] += 1
            except Exception as e:
                # Log but do not stop the batch (other stores to process)
                log.error("retroactive_cashback failed", store_id=store.id, error=str(e))
    
    # Sub-phase 3.2: auto-suspicious old pending stores
    threshold_date = datetime.now(timezone.utc) - timedelta(days=sv['suspicious_after_months'] * 30)
    old_pending = db.query(Store).filter(
        Store.validation_status == 'pending',
        Store.created_at < threshold_date,
    ).all()
    for store in old_pending:
        distinct_eans = db.execute(text("..."), {...}).scalar()  # same query
        if distinct_eans < sv['suspicious_threshold_eans']:  # 30
            store.validation_status = 'suspicious'
            db.add(StoreValidationHistory(
                store_id=store.id,
                from_status='pending',
                to_status='suspicious',
                reason='suspicious_timeout',
                triggered_by='batch:ratis_batch_consensus:store_validation_phase',
                metadata={
                    'distinct_eans_count': distinct_eans,
                    'age_days': (datetime.now(timezone.utc) - store.created_at).days,
                },
            ))
            db.commit()
            stats['flipped_suspicious'] += 1
    
    return stats
```

### Structure (changes in `batch/ratis_batch_consensus/`)

- ✅ `batch/ratis_batch_consensus/main.py` — extended: import + call to `run_store_validation_phase` after recalc
- 🆕 `batch/ratis_batch_consensus/store_validation_phase.py` — dedicated module (~80 LOC + ~30 LOC tests)
- 🆕 `batch/ratis_batch_consensus/tests/test_store_validation_phase.py` — complete TDD
- 📝 `batch/ratis_batch_consensus/ARCH_BATCH_CONSENSUS.md` — "Phase 3" section added + "Tables read / written" section extended (`stores`, `store_validation_history`)

### Transactional boundary

- **Phase 1+2**: single transaction on `price_consensus` (existing pattern, unchanged)
- **Phase 3**: new transactions, one per store flip (each commit isolated)
- **Retroactive cashback**: HTTP call `POST /rewards/cashback/process-retroactive` fire-and-forget — exception logged but batch continues

### Expected TDD tests (sub-agent Phase 2A)

1. Phase 3: pending store with ≥20 distinct EAN trust_score≥80 → flip confirmed + audit + cashback call
2. Phase 3: pending store with <20 EAN → remains pending
3. Phase 3: pending store ≥6 months + <30 EAN → flip suspicious + audit
4. Phase 3: pending store ≥6 months + ≥30 EAN → remains pending (not suspicious — still active)
5. Phase 3: retroactive cashback exception → batch continues (log error, other stores processed)
6. Phase 3: isolated transaction — Phase 1 commit OK, Phase 3 fail → Phase 1 changes preserved
7. Phase 3: idempotence — rerunning the batch does not flip the same store twice (already confirmed → excluded from SELECT pending)

---

## Retroactive Cashback

### Internal endpoint (Rewards)

`POST /rewards/cashback/process-retroactive`

**Auth**: `INTERNAL_API_KEY` (called by batch only)
**Body**: `{ "store_id": "uuid" }`
**Response 200**: `{ "processed_receipts": <int>, "total_cashback_cents": <int> }`

**Logic**:
```
1. Load all receipts WHERE store_id=X AND store_status='pending'
2. For each receipt:
   a. Recompute the cashback (reuse existing logic from scan_service.trigger_cashback_scan)
   b. Create normal CAB transactions (direction='credit', reference_type='scan')
   c. Update receipt.store_status = 'confirmed'
3. Commit in batch
4. Return stats
```

**Idempotence**: if called twice on the same store, the second call does not re-create CAB (each receipt is switched to `confirmed` on the first pass → the `WHERE store_status='pending'` filter excludes them).

### Cashback gating modification

In `webservices/ratis_rewards/services/cashback*` (to be located at dev time), modify the current check:

```python
# BEFORE
if receipt.store_status != 'confirmed':
    return  # cashback blocked

# AFTER
if receipt.store_status != 'confirmed':
    return  # cashback blocked
store = db.get(Store, receipt.store_id)
if not store or store.validation_status != 'confirmed':
    return  # double check: store must be validated
```

---

## Frontend

### Hook `useScanConfirmStore(receiptId)`

```typescript
// ratis_client/hooks/use-scan-confirm-store.ts
export function useScanConfirmStore(receiptId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => productClient.post(`/scan/receipt/${receiptId}/confirm-store`),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['scan-history'] });
      qc.invalidateQueries({ queryKey: ['receipt-items', receiptId] });
    },
  });
}
```

### Component `<StoreConfirmationModal />`

Props:
```typescript
interface Props {
  visible: boolean;
  candidateInfo: { brand_guess: string; address?: string; postal_code?: string; city?: string; phone?: string };
  onConfirm: () => void;
  onClose: () => void;
  isLoading?: boolean;
  error?: string | null;
}
```

UI:
- Title: *"Magasin inconnu de nos services"*
- Read-only body (no editing):
  ```
  Voici ce que nous avons lu sur votre ticket :
  
  🏪  Lidl
  📍  12 RUE DE LA PAIX, 75002 PARIS
  ☎️  01 42 34 56 78
  
  Ce magasin n'est pas dans notre base. Confirmez-vous
  que ces informations sont correctes ?
  ```
- 2 buttons:
  - **Confirmer** (primary) → calls `onConfirm()` → mutation
  - **Re-scanner** (secondary) → close modal + redirect to `/(tabs)/scan`
- Error states:
  - `insufficient_ocr_data` → message *"Données du ticket insuffisantes — re-scannez"*
  - `receipt_already_resolved` → message *"Ce ticket a déjà été traité"* + auto-close
  - generic → message *"Une erreur est survenue"*

### Wiring `scan-history-receipt-accordion.tsx`

The red pencil (already in place via PR-A #178) now opens `<StoreConfirmationModal />` instead of the `Alert.alert` placeholder. Keep the Alert for confirmed receipts (non-interactive case — the grey pencil is not clickable anyway, to clarify in PR review).

### "Awaiting validation" badge

On receipts with `store.validation_status='pending'`, add a subtitle in the accordion header:
```
Lidl · 27 avr. · 🟡 Magasin en attente de validation
```

i18n: `scan.history.receipt.store_pending_validation` (FR: *"Magasin en attente de validation"*).

### i18n — new keys

```json
{
  "scan": {
    "history": {
      "confirm_store": {
        "modal_title": "Magasin inconnu de nos services",
        "modal_body_intro": "Voici ce que nous avons lu sur votre ticket :",
        "modal_body_question": "Ce magasin n'est pas dans notre base. Confirmez-vous que ces informations sont correctes ?",
        "btn_confirm": "Confirmer",
        "btn_rescan": "Re-scanner",
        "error_insufficient_data": "Données du ticket insuffisantes — re-scannez",
        "error_already_resolved": "Ce ticket a déjà été traité",
        "error_generic": "Une erreur est survenue",
        "success_pending": "Magasin enregistré, en attente de validation"
      },
      "receipt": {
        "store_pending_validation": "Magasin en attente de validation"
      }
    }
  }
}
```

The `edit_store.coming_soon_*` keys (introduced in PR-A) become **obsolete** → to be removed in PR-B.

---

## Anti-abuse — defensive layers

| # | Layer | Description |
|---|---|---|
| 1 | **OCR-driven** | User enters no information — they only confirm what the OCR has parsed. Modifying data is impossible from the app side. |
| 2 | **Minimum OCR data required** | `brand_guess` + (`postal_code` OR `address`) required. Otherwise 422 — user re-scans. |
| 3 | **Consensus threshold of 20 distinct EANs** | To validate, the store must accumulate 20 distinct consensus prices (`trust_score≥80`). Faking this threshold = fabricating 20 coherent fake receipts with real products/prices = disproportionate effort. |
| 4 | **Frozen cashback** | No cashback while `validation_status≠'confirmed'`. The fraudster cannot extract money before the 20 EANs are reached. |
| 5 | **Suspicious at 6 months** | Stores pending for 6 months with <30 EANs → marked suspicious → hidden from user searches. |
| 6 | **Complete audit trail** | `store_validation_history` table tracks all transitions. `stores.suggested_by_user_id` tracks who created it. |
| 7 | **(V2)** User-flag *"this address does not exist"* | Allows accelerating detection of fakes. |
| 8 | **(V2)** LLM handwriting check | On the name/address bbox of the receipt image, detect handwriting (sign of ticket modification). |
| 9 | **(V2-3)** Merchant call at 6 months | Before suspicious, phone the merchant to retrieve their prices → growth hack aligned with incentives. |

---

## `ratis_settings.json` parameters

New section:

```json
{
  "store_validation": {
    "min_distinct_eans_for_validation": 20,
    "consensus_min_trust_score": 80,
    "suspicious_after_months": 6,
    "suspicious_threshold_eans": 30
  }
}
```

**Notes**:
- `consensus_min_trust_score=80` semantically reuses `consensus.globally_verified_threshold=80` but is explicitly exposed in this section for clarity (a future adjustment to one does not necessarily impact the other).
- `suspicious_after_months=6` is calibrated on alpha observation — to be re-evaluated post-V1 based on real volumes.
- `suspicious_threshold_eans=30` is intentionally higher than `min_distinct_eans_for_validation=20`: a store that has accumulated 30+ EANs over 6 months is legitimate even if it hasn't reached the 20 frozen consensus (perhaps its prices change frequently).

---

## Terms of Service

Section **"Store Validation"** to be added to the ToS (to be coordinated with the global ToS document):

> **Store Validation**
>
> Ratis cashback applies exclusively to validated stores. Store validation occurs via one of three mechanisms:
>
> 1. **OpenStreetMap (OSM) integration** — our database is synchronized daily with OSM. Stores referenced there are automatically validated.
> 2. **Administrator curation** — the Ratis team can manually validate a store on request.
> 3. **User consensus** — a store suggested by a user (via the "Confirm an unknown store" feature) is automatically validated when at least **20 distinct products** have been observed at consistent prices by multiple users (price consensus).
>
> Receipts from stores awaiting validation are kept in your history. **The corresponding cashback is credited retroactively** as soon as the store is validated.
>
> Stores pending for more than 6 months without sufficient consensus accumulation are marked as "unverified" and removed from public searches. No retroactive cashback applies to these stores.

---

## Architecture Decisions

### DA-1: Separate `confirm-store` endpoint (not an extension of `identify-store`)

**Choice**: create a new endpoint `POST /scan/receipt/{id}/confirm-store` rather than extending `identify-store` (Option B from [[ARCH_store_resolution]]).
**Reason**: `identify-store` was form-driven (user inputs `{brand}`) — a UX we reject for anti-abuse reasons. Mixing the 2 flows in 1 endpoint = conditional code + mixed logic. Separation = testability + readability.
**Consequence**: `identify-store` is **removed** in a chore PR pre-PR-B (orphan endpoint, never wired client-side).

### DA-2: Separate `stores.validation_status` column from `receipts.store_status`

**Choice**: add `stores.validation_status` rather than reusing/extending `receipts.store_status`.
**Reason**: different semantics — `receipts.store_status` = "has this receipt been resolved against a store", `stores.validation_status` = "is this store reliable". The same store can be referenced by multiple receipts in different states, and its own validation state is independent.
**Consequence**: double-check at cashback gating (both columns must be 'confirmed').

### DA-3: Consensus threshold = `count(DISTINCT product_ean)` ≥ 20 (not scan count)

**Choice**: count **distinct products** with established consensus, not scan contributions (rows `price_consensus_scans`).
**Reason**: 20 distinct products = 20 different EANs = ~3-5 typical scans by an average user. Very hard to fake (requires real products with correct prices). Counting scans would allow a user to scan the same receipt 20 times.
**Consequence**: a store can only be validated if the diversity of products observed there is genuine.

### DA-4: Retroactive cashback via one-shot batch (not async hook)

**Choice**: the `ratis_batch_store_validation` batch calls `process-retroactive` synchronously after each pending→confirmed flip.
**Reason**: V1 — low volumes (alpha + early prod), no need to decouple into async queue. If 1 store flips → ~10 receipts to process → a few seconds.
**Trigger for V2 decoupling**: if a batch action exceeds 60s or requires advanced retry/idempotence → fanout via Celery.

### DA-5: Lat/Lng=(0,0) for fresh user_suggested

**Choice**: we store (0,0) at creation (no auto-geolocation at this stage).
**Reason**:
- No location permission requested at confirm time (consistent no-friction UX)
- Geocoding via OSM Nominatim is out-of-scope for V1 (rate-limited, latency)
- Admin/V2 batch can fill in downstream (`batch_osm_sync` extended)
**Consequence**: the "nearby store" feature on the map will not show fresh user_suggested stores — this is OK (they are not validated and therefore hidden from active searches according to visibility rules).

### DA-6: Single PR-B (not 3 sequential ones)

**Choice**: ship backend + batch + frontend + ToS in a single PR.
**Reason**: all components are interdependent — backend without frontend is useless, batch without backend breaks, frontend without backend = 422 everywhere. Splitting provides no shippable intermediate value.
**Consequence**: larger PR (~25 files) — longer review but coherent.

### DA-7: Store validation = Phase 3 of `ratis_batch_consensus` (not a separate batch)

**Choice**: implement the `pending→confirmed/suspicious` flip logic as **Phase 3** in the existing `ratis_batch_consensus` batch, rather than creating a new `batch/ratis_batch_store_validation/`.
**Reason**:
- **Semantic continuity**: `price_consensus` (Phase 1+2) and `store_validation` (Phase 3) are the same class of problem (consensus + threshold + state transition), just at different granularities — co-locating avoids logic dispersion.
- **Guaranteed temporal ordering**: Phase 3 reads `trust_score` values computed by Phase 1 in the same process → no race or cron to orchestrate.
- **Less infrastructure**: 1 single cron, 1 single YAML workflow, 1 single ARCH (`ARCH_BATCH_CONSENSUS` extended).
- **Tests**: Phase 3 lives in its own module (`store_validation_phase.py`) → testable in isolation just as a separate batch would have been.
**Consequence**: the semantics of "consensus" in `ratis_batch_consensus` broadens (product prices + store validation). To be documented in the batch ARCH. Transactional boundary: Phase 1+2 commit together, Phase 3 commits per store flip independently — a Phase 3 failure does NOT rollback Phase 1+2.

---

## Known Pitfalls

### P-1: `pending_store_candidates` may not exist under this name

The SA audit pre-PR-B must confirm the exact name of the table that stores OCR pre-resolution signals. Possible old names: `store_candidates`, `pending_store_candidates`, or stored directly in `receipts.parsed_store_info` JSONB. The implementation SA **must audit before coding**.

### P-2: Backfill migration in prod

The `UPDATE stores SET validation_status='pending' WHERE source='user_suggested'` impacts existing rows. **Risk**: if some `source='user_suggested'` stores have already been manually validated by an admin, flipping them to pending = breaking their ongoing cashback.

**Mitigation**: pre-migration audit of existing `source='user_suggested'` rows. If some have successful recent cashback transactions → manual exception in the backfill (or `manually_validated_by_admin` flag).

### P-3: Race condition double-confirm

If a user clicks "Confirm" twice quickly in the modal, two simultaneous requests → two user_suggested stores created? **Mitigation**:
- Frontend: disable button during mutation (`isLoading`)
- Backend: transaction + `SELECT FOR UPDATE` on `receipts WHERE id=X AND store_status='unknown'` at the start of the logic
- Idempotence: if receipt.store_status switched to 'pending' between the 2 calls → 409 receipt_already_resolved on the second

### P-4: Retroactive cashback idempotence

If the batch is manually rerun after a partial failure, do not re-credit cashback already paid on receipts already flipped to confirmed.
**Mitigation**: the `WHERE receipt.store_status='pending'` filter in `process-retroactive` natively excludes already-processed receipts.

### P-5: Volatile trust_score

A store may reach 20 distinct EANs trust_score≥80 on Monday, then flip suspicious Tuesday if some scores drop (temporal decay via `batch_consensus`). The batch_validation running at 03:00 UTC captures the state at that moment.
**V1 mitigation**: not a problem, the store remains `confirmed` (a flip→confirmed is definitive unless admin-override). The trust_score check only applies for the **flip pending→confirmed**, not for maintaining confirmed status.

### P-6: OCR variations between scans of the same receipt

If the user has already scanned a receipt with `store_status='unknown'` then re-scans → do we link the 2nd scan to the same `pending_store_candidate` (deduplication by receipt hash)? Or create a 2nd candidate?
**To be clarified at dev time** — see [[ARCH_store_resolution]] § hash dedup logic.

---

## V2+ — future extensions

### V2.1 — User-flag *"this address does not exist"*

On the store detail page with `validation_status='pending'`, secondary button to report it. If N flags accumulated on the same store → admin notification.
- Endpoint: `POST /stores/{id}/flag-not-existing` (auth user)
- Frontend: button in store info modal

### V2.2 — LLM handwriting detection

On user_suggested creation, post-async:
1. Crop `name` + `address` bbox from the receipt image (PaddleOCR provides the bbox)
2. Send to LLM vision: *"Does this image contain handwriting or visible modifications?"*
3. If positive response → flag store for admin review

Cost: ~1 LLM call per fresh user_suggested store. Estimated alpha volume < 100/week → negligible cost. Non-blocking latency (async, not in the user flow).

### V2.3 — Merchant call growth hack

Before the suspicious flip at 6 months:
1. Monthly cron sends email/SMS to the merchant (OCR'd phone):
   *"Your store is listed on Ratis. To stay active and benefit from users, send us your prices within 30 days."*
2. If merchant responds → admin onboarding flow
3. Otherwise → flip suspicious at 6 months as planned

Aligns merchant + Ratis incentives. Dedicated ARCH `ARCH_merchant_onboarding.md` to create.

### V2.4 — Auto geocoding for user_suggested

Extend `batch_osm_sync` to, each run, attempt to resolve the `(name, address, postal_code)` of `user_suggested validation_status='pending'` stores via Nominatim (rate-limited 1/sec). Update `lat/lng` when a match is found.

### V2.5 — Self-service correction of OCR candidate

(Out of V1 scope — strict anti-abuse.) If alpha shows many OCR candidates are almost correct but have 1 typo (e.g. "Linal" instead of "Lidl"), allow a minor edit validated by LLM (verify the correction remains plausible with the source image).

---

## Glossary

- **user_suggested store**: a store with `source='user_suggested'`. Created via `confirm-store`. Not validated until consensus is reached.
- **Consensus validation**: threshold `≥20 distinct products (DISTINCT product_ean) with trust_score >= 80` in `price_consensus` for a given `store_id`.
- **trust_score**: score 0-100 of a `price_consensus` row, calculated by `ratis_batch_consensus` via temporal decay + scan_weight + freeze. See [[ARCH_consensus]].
- **Retroactive cashback**: cashback credit made after the fact on receipts whose store was pending at scan time, after the store flips to confirmed.
- **Suspicious store**: a `pending` store for ≥6 months with distinct EAN consensus <30. Hidden from user searches, `validation_status='suspicious'`. Never validated.

---

> 🔗 **Cross-ARCH references**:
> - [[ARCH_store_resolution]] — initial OCR flow, cold start, automatic identification (does NOT cover user confirmation)
> - [[ARCH_consensus]] — `trust_score` calculation, decay, freeze, parameters
> - [[ARCH_BATCH_CONSENSUS]] — daily batch recalc trust_scores (temporal prerequisite for `batch_store_validation`)
> - [[ARCH_cashback]] — cashback gating, computation, retry. The gating modification described here will be reflected in the update to this ARCH.
> - [[ARCH_scan_history]] — history screen UX, accordion, store pencil (PR-A already merged — modal wiring is PR-B)
