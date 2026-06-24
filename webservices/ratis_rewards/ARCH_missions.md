---
type: sub-arch
service: ratis_rewards
parent: ARCH_REWARDS
related: [ARCH_cab, ARCH_battlepass, ARCH_gamification]
status: production
tags: [missions, rewards, daily, weekly, quests, buffer, burst]
updated: 2026-05-11
---

# ratis_rewards — ARCH Missions

> Daily/weekly missions: `missions` (catalogue) + `user_missions` (progress), CAB + XP rewards, battlepass and gamification integration. Buffer/Burst on XP.
> @tags: missions rewards daily weekly quests buffer burst user_missions xp cab catalog completion
> @status: LIVRÉ V0
> @subs: auto

> Parent: [[ARCH_REWARDS]] · Relations: [[ARCH_cab]], [[ARCH_battlepass]], [[ARCH_gamification]]

> Status: ✅ Implemented — daily/weekly missions
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
- [x] Lazy mission generation (1 easy + 1 medium + 1 hard per period)
- [x] No repetition of action_type per period constraint
- [x] check_missions_progress in the same transaction as award_cab
- [x] period_start computed server-side (never passed by client)
- [x] UNIQUE (action_type, frequency, difficulty) on missions catalogue

> ⚠️ One item at a time. Do not move to the next without finishing the current one.

---

## Index

- [Context](#context) [L.43 - L.47]
- [Parent reference](#parent-reference)
- [Endpoints](#endpoints) [L.61 - L.125]
- [Tables](#tables) [L.127 - L.152]
- [POST /rewards/events/scan_accepted](#post-rewardseventscan_accepted) [L.154 - L.163]
- [check_missions_progress](#check_missions_progress) [L.165 - L.177]
- [ratis_settings.json parameters](#ratis_settingsjson-parameters) [L.179 - L.185]
- [Rules](#rules) [L.187 - L.195]

---

## Context

Read `ratis_core` and `ARCH_cab.md` before starting. `award_cab` must exist. Strict TDD. Branch `feature/rewards-missions`.

---

## Parent Reference

This ARCH is a sub-domain of `webservices/ratis_rewards/ARCH_REWARDS.md` (global rewards service). For cross-cutting rules (CAB award, gamification flow), refer to the parent ARCH.

Owned tables: `missions` (unified catalogue), `user_missions` (per-user instances).

Endpoints: `POST /rewards/events/scan_accepted`, `GET /rewards/missions`, `POST /rewards/missions/{id}/claim` (cf. `ENDPOINTS.md`). Configuration: `ratis_core/config/ratis_settings.json` § missions.

---

## Endpoints

```
GET  /rewards/missions
POST /rewards/missions/{id}/claim
```

### `GET /rewards/missions`

If the user has no missions for the current period → generate them on-the-fly from the active `missions` catalogue.

```json
{
  "daily": {
    "date": "2026-04-10",
    "missions": [
      { "id": "uuid", "action_type": "receipt_scan", "difficulty": "easy", "target_count": 1, "current_count": 1, "cab_reward": 50, "status": "completed" },
      { "id": "uuid", "action_type": "price_compared", "difficulty": "hard", "target_count": 5, "current_count": 2, "cab_reward": 150, "status": "pending" }
    ]
  },
  "weekly": {
    "week_start": "2026-04-07",
    "missions": [
      { "id": "uuid", "action_type": "receipt_scan", "difficulty": "medium", "target_count": 5, "current_count": 3, "cab_reward": 200, "status": "pending" }
    ]
  }
}
```

**Mission generation:**
```python
def generate_missions_for_user(user_id, period_start, frequency):
    active_missions = get_active_missions(frequency)
    selected = []
    used_action_types = set()

    for difficulty in ['easy', 'medium', 'hard']:
        candidates = [
            m for m in active_missions
            if m.difficulty == difficulty
            and m.action_type not in used_action_types
        ]
        if candidates:
            mission = random.choice(candidates)
            selected.append(mission)
            used_action_types.add(mission.action_type)

    for mission in selected:
        insert_user_mission(user_id, mission.id, period_start)
```

1 easy + 1 medium + 1 hard per period. If a difficulty level cannot be covered without repeating an `action_type` → skip it, no forced repetition.

### `POST /rewards/missions/{id}/claim`

⚠️ **Rework 2026-05-09** — the logic has evolved to support **cumulative multi-claim** + **double gating** introduced by Buffer (see `docs/superpowers/specs/2026-05-09-buffer-burst-design.md` + [[ARCH_gamification]] § Buffer for full details).

Pseudo-code (summary):

```
1. Verify the mission belongs to the logged-in user
2. Verify mission_not_pending if status='claimed' (all portions collected)
3. Verify mission_expired if now > period_extended_until (or period_start + 1d if not buffered)
4. n = buffer_count
   R = cab_reward / (n + 1)                                   ← original R
   palier_size = target_count / (n + 1)
5. paliers_atteints     = min(current_count // palier_size, n + 1)
   jours_écoulés        = min((now.date() - period_start).days + 1, n + 1)
   portions_disponibles = min(paliers_atteints, jours_écoulés)  ← double gating
   portions_à_claim     = portions_disponibles - portions_claimed
6. If portions_à_claim <= 0 → 402 no_portion_available_now
7. cab_à_award = portions_à_claim × R
8. award_cab(user_id, cab_à_award, 'mission_reward', reference_id=user_mission.id)
9. UPDATE user_missions SET portions_claimed = portions_disponibles
   If portions_claimed == n + 1 → status='claimed'
10. db.commit()
```

Degenerate case (classic non-buffered mission, `n=0`): `palier_size = target_count`, `portions_max = 1` → behaviour equivalent to the old all-or-nothing claim. No regression for missions without Buffer.

Error codes: `mission_not_found` (404), `no_portion_available_now` (402), `already_claimed` (409), `mission_expired` (410).

Returns:
```json
{
  "cab_awarded": 2,
  "portions_claimed_total": 2,
  "portions_remaining": 1,
  "mission_status": "pending"
}
```

---

## Tables

**`missions`** — unified daily/weekly catalogue
```sql
id           UUID PRIMARY KEY DEFAULT gen_random_uuid()
action_type  TEXT NOT NULL CHECK (action_type IN (
                 'receipt_scan', 'label_scan', 'barcode_scan', 'price_compared'
             ))
frequency    TEXT NOT NULL CHECK (frequency IN ('daily', 'weekly'))
difficulty   TEXT NOT NULL CHECK (difficulty IN ('easy', 'medium', 'hard'))
target_count INT NOT NULL
cab_reward   INT NOT NULL
is_active    BOOLEAN NOT NULL DEFAULT TRUE
UNIQUE (action_type, frequency, difficulty)
```

**`user_missions`** — per-user per-period instances
```sql
id                    UUID PRIMARY KEY DEFAULT gen_random_uuid()
user_id               UUID REFERENCES users(id) ON DELETE SET NULL
mission_id            UUID NOT NULL REFERENCES missions(id) ON DELETE RESTRICT
period_start          DATE NOT NULL
current_count         INT NOT NULL DEFAULT 0
status                TEXT NOT NULL CHECK (status IN ('pending', 'completed', 'claimed')) DEFAULT 'pending'

-- Rework 2026-05-09 — Buffer + Burst
buffer_count          INT NOT NULL DEFAULT 0       -- renamed from boost_count, 0-3 daily, 0 weekly (non-bufferable)
burst_count           INT NOT NULL DEFAULT 0       -- number of Burst tiers reached (no cap)
period_extended_until TIMESTAMPTZ NULL             -- extended deadline (NULL if not buffered)
burst_locked          BOOLEAN NOT NULL DEFAULT FALSE  -- true on 1st burst-claim → blocks Buffer
portions_claimed      INT NOT NULL DEFAULT 0       -- number of Buffer portions already collected (0 to n+1)

UNIQUE (user_id, mission_id, period_start)
```

`period_start`:
- `daily` → today's UTC date
- `weekly` → Monday of the UTC week: `date - timedelta(days=date.weekday())`

---

## `POST /rewards/events/scan_accepted`

Internal endpoint called by `ratis_product_analyser` via `ratis_core.rewards_client` (fire-and-forget). Executes `award_cab` + `check_missions_progress` in the same SQL transaction.

```json
{ "user_id": "uuid", "scan_type": "receipt" }
```

`scan_type` → `action_type`: `receipt` → `receipt_scan`, `electronic_label` → `label_scan`, `manual` → `barcode_scan`.

---

## `check_missions_progress(user_id, action_type, db)`

Called from `POST /rewards/events/scan_accepted`, in the same transaction as `award_cab`.

```
1. Fetch the user's active missions for the current day (daily) and current week (weekly)
2. For each mission whose action_type matches:
   → UPDATE user_missions SET current_count = current_count + 1
   → If current_count >= target_count → SET status = 'completed'
3. If the row does not exist yet → create it (first action of the period)
```

---

## `ratis_settings.json` Parameters

```json
"missions": {
    "daily_count_per_difficulty": 1,
    "weekly_count_per_difficulty": 1
}
```

---

## Rules

- `UNIQUE (action_type, frequency, difficulty)` — never two identical missions
- Lazy generation on the first request of the period
- `award_cab` + `check_missions_progress` in the same transaction — via `POST /rewards/events/scan_accepted`
- UPDATE status + `award_cab` in the same transaction on claim
- `period_start` always computed server-side — never passed by the client
- `frequency` extensible — add `'monthly'` to the CHECK without refactoring

---

## Phase A & B Evolutions (2026-05-08)

> **Phase A** = PR #324 merged — catalogue extension + qualifier + distinct tracking.
> **Phase B** = PR #325 merged — `trigger_action` refactor + generic `/events/action` endpoint.
> This section describes **incremental** changes without rewriting the original ARCH. The missions system remains conceptually the same (catalogue + lazy gen + claim), but the scope of `action_type` and the emission API have evolved.

### New columns (Alembic migration)

```sql
ALTER TABLE missions ADD COLUMN qualifier TEXT NULL;
ALTER TABLE user_missions ADD COLUMN tracked_values JSONB NULL;
```

- `missions.qualifier`: optional, prefixed for readability — supported formats:
  - `attribute:organic` · `attribute:french` (product label)
  - `category:<slug>` (category from classification_rules.json)
  - `store:<uuid>` (store-specific mission — future)
- `user_missions.tracked_values`: JSONB, used for `scan_distinct` (counting distinct EANs or store_ids) — example: `{"distinct_eans": ["3270190..."]}` to avoid double-counting.

### Action types — rename + extension

The CHECK on `missions.action_type` is extended:

| Action type | Origin | Note |
|---|---|---|
| `receipt_scan` | V1 original | unchanged |
| `label_scan` | V1 original | unchanged |
| `product_identification` | **renamed** from `barcode_scan` | EAN scan or product identified post-OCR |
| `price_compared` | V1 original | unchanged |
| `fill_product_field` | **new Phase A** | user completes a missing OFF/internal product field (product suggestion) |
| `scan_distinct` | **new Phase A** | counting of distinct scans (`tracked_values` required), can be qualified by store/category |
| `promo_found` | **new Phase A** | user signals a detected promo (rare in V1, tracked for V2) |

### Lazy generation extended to `(action_type, qualifier)`

The old constraint "no repetition of `action_type` per period" becomes "no repetition of `(action_type, qualifier)` per period". This allows 2 active `scan_distinct` missions in the same week if one has `qualifier='category:fruits'` and the other has `qualifier='store:<uuid>'`.

The `generate_missions_for_user` code must therefore now use `used_keys = set()` with `(m.action_type, m.qualifier)` instead of `m.action_type`.

### Seeded catalogue — 41 Phase A & B templates

The seed `ratis_core/seed/missions_v1.py` loads 41 templates in total (3 difficulties × {daily, weekly} × 7 action_types, partial). See the file for the full grid.

⚠️ **qualifier `attribute:*` wiring status**:
- ✅ **Phase C-1 (2026-05-11)** — `attribute:organic` wiring on the PA side connected. 3 `product_identification + attribute:organic` templates re-activated via `20260511_2300_phase_c1_reflip_organic.py`. See "Phase C-1 Evolutions" section below.
- ⏳ 6 remaining templates disabled: 3 `*+attribute:french` (Phase C-2 needs origins enrichment) + 3 `fill_product_field+attribute:organic` (Phase C-5 needs contribute endpoint).

### Refactor — generic `POST /rewards/events/action` endpoint

The old `POST /rewards/events/scan_accepted` is **replaced** by:

```
POST /rewards/events/action
{
  "user_id": "uuid",
  "action_type": "receipt_scan",  // or any other valid action_type
  "qualifier": "category:fruits",  // optional
  "reference_type": "scan",        // optional — for idempotency + audit
  "reference_id": "uuid",          // optional
  "metadata": { ... }              // action-specific payload (e.g. distinct_ean for scan_distinct)
}
```

On the `ratis_core.rewards_client` side, the old `trigger_scan_accepted` becomes `trigger_action(action_type, qualifier, ...)` (more generic). The call-sites in `ratis_product_analyser` are updated (Phase B). The internal service: (1) fetches the active `user_missions` matching `(action_type, qualifier)` for the current period, (2) updates `current_count` + `tracked_values` if applicable, (3) awards CAB if the mission becomes `completed`. All in the same SQL transaction.

### New `reward_events` table (Phase B)

Audit log + idempotency layer for the `/events/action` endpoint:

```sql
reward_events (
  id UUID PK,
  user_id UUID,
  action_type TEXT NOT NULL,
  qualifier TEXT NULL,
  reference_type TEXT NULL,
  reference_id TEXT NULL,
  payload JSONB NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (user_id, reference_type, reference_id)  -- idempotency
)
```

Provides an immutable trace of every received event (for debugging + replay if a bug is detected). UNIQUE(user_id, reference_type, reference_id) ensures a caller retry does not double-count.

### Update — Endpoints section

- `POST /rewards/events/scan_accepted` → **deprecated** (replaced by `/events/action`) — kept temporarily for compatibility while all call-sites migrate, to be removed in Phase C.
- `POST /rewards/events/action` → **new**, generic action-typed endpoint.

### Update — Rules section

- Lazy generation constraint = no repetition of `(action_type, qualifier)` per period (instead of `action_type` alone)
- `reward_events` UNIQUE(user_id, reference_type, reference_id) → caller-side idempotency
- `qualifier` mandatory prefix (`attribute:` / `category:` / `store:`) for future admin catalogue readability

---

## Phase C-1 Evolutions — organic qualifier enrichment (2026-05-11)

> **Status**: ✅ shipped (PR to merge). Branch `feat/missions-phase-c1-organic-enrich`.
> Migration: `20260511_2300_phase_c1_reflip_organic.py`.

Phase B had flipped the 9 `attribute:*` templates to `is_active=true`; on 2026-05-08 the migration `20260509_0100_disqual` re-disabled them while the PA worker was not emitting qualifiers. C-1 connects the upstream wiring for `attribute:organic` and re-activates **3** of the 9 templates.

### Dual-emit pattern (decision)

The challenge: for a scan of an OFF-tagged organic product, we want to **simultaneously** advance (a) missions without a qualifier (e.g. `product_identification + None`) **and** (b) organic-qualified missions. Three options were considered:

1. ❌ **Single-emit conditional qualifier** — a single `trigger_action(qualifier='attribute:organic' if organic else None, ...)`. Breaks the progression of the 6 None-qualifier missions as soon as an organic scan arrives.
2. ❌ **Server-side fan-out** — let the rewards service derive the organic qualifier from context. Increases coupling between RW and the product schema (RW does not know `labels_tags`).
3. ✅ **Dual-emit on the caller side** — 2 distinct events from the PA, suffixed idempotency_keys. Preserves None-qualifier missions, keeps RW idiomatic (one event = one qualifier), and each emit remains atomic.

### Implementation

Canonical site: `webservices/ratis_product_analyser/services/reconciliation_service.py::_default_reward_trigger`.

```python
def _default_reward_trigger(user_id, scan_id, scan_type, *, labels_tags=None):
    action_type = _SCAN_TYPE_TO_ACTION_TYPE.get(scan_type, "product_identification")
    base_context = {"scan_id": str(scan_id), "source": "reconciliation"}

    # (1) Vanilla emit — always.
    trigger_action(user_id, action_type, idempotency_key=str(scan_id), context=base_context)

    # (2) Organic emit — only when the product is OFF-tagged organic.
    if is_organic_product(labels_tags):
        trigger_action(
            user_id, action_type, qualifier="attribute:organic",
            idempotency_key=f"{scan_id}:organic",
            context={**base_context, "attribute": "organic"},
        )
```

Pure helper: `services/product_attributes.py::is_organic_product(labels_tags)`. Matches `en:organic`, `fr:bio`, `en:eu-organic`, `fr:agriculture-biologique` (case-insensitive, exact match).

### Idempotency

The 2 events share the same `scan_id` but carry different `idempotency_key` values (vanilla = `<scan_id>`; organic = `<scan_id>:organic`). The `reward_events UNIQUE(user_id, reference_type, reference_id)` treats the 2 events as independent — no violation, no double-credit.

### Periodicity — out of scope C-1

| Phase | Scope | Status |
|---|---|---|
| C-1 | `product_identification + attribute:organic` (3 missions) re-flipped | ✅ shippé |
| C-2 | `attribute:french` — column + backfill batch + dual-emit wired, **flip deferred** post-backfill | ✅ shippé (PR Phase C-2) |
| C-3 | `scan_distinct` emit (8 missions: 6 category + 2 store) | ✅ shippé |
| C-4 | `promo_found` emit (regex layer) | ✅ shippé |
| C-5 | `fill_product_field` backend endpoint + CL UI + emit (re-flips 3 `fill_product_field.organic` missions) | ✅ shippé (backend) |

### Emit sites — other call-sites

`receipt_task.py::_award_scan_rewards` emits `receipt_scan` per receipt (idempotency_key=`receipt_id`). No organic qualifier here — the V1 catalogue has no `receipt_scan + attribute:*` mission. Site **unchanged**. If in the future we want to advance a `receipt_scan + attribute:organic` mission (e.g. "scan 1 receipt with ≥1 organic product"), it would require computing the attribute at the receipt level (any-line organic) — not required in V1.

---

## Phase C-2 Evolutions — attribute:french origins_tags + backfill batch (2026-05-11)

> **Status**: ✅ shipped (PR `feat(missions): Phase C-2 — attribute:french origins_tags + backfill batch + dual-emit`). Migration: `20260511_2400_phase_c2_origins_tags.py`. Batch: [[ARCH_BATCH_ORIGINS_BACKFILL]].
> **Heaviest wave of the sprint**: adds an ARRAY[TEXT] column to products, a new one-shot batch, and extends the PA dual-emit to cover the French attribute — mirror of the C-1 organic pattern.

C-2 delivers all upstream wiring for the `french` attribute, but **does not re-flip the 3 `product_identification + attribute:french` missions**: this flip is a manual SQL one-row executed after backfill confirmation, only when production coverage reaches ≥80% (cf [PROD_CHECKLIST.md § Missions Phase C-2](../../../docs/ops/PROD_CHECKLIST.md)).

### Added components

| Layer | Artifact | Role |
|---|---|---|
| Schema | `products.origins_tags ARRAY[TEXT]` nullable | Stores the OFF array of origin signals |
| ORM | `Product.origins_tags` mapped | Mirror Pattern A |
| off_sync extractor | `extract_product(raw)["origins_tags"]` | Forward path persistence (every nightly run) |
| off_sync repository | `_SYNC_COLS += ("origins_tags", "text[]")` | Auto-generated EXCLUDED clause |
| PA helper | `is_french_product(origins_tags)` | Mirror of `is_organic_product` |
| Batch | `ratis_batch_origins_backfill` (one-shot) | Backfill of historical rows that are `IS NULL` |
| PA dual-emit | `_default_reward_trigger` gains a 3rd emit | `qualifier='attribute:french'` |

### Key decisions

#### Why a separate batch rather than a single off_sync `--force-resync`

`off_sync --force-resync` would re-fetch **all** fields (~20 columns) for the ~1M France products from OFF. This is huge, costly in bandwidth, and potentially overwrites already up-to-date data (storage_type, multi-field display name...).

`ratis_batch_origins_backfill`:
- only requests `fields=origins_tags` from OFF → payload ~50× smaller
- UPDATE touches ONLY the targeted column (no rewrite of the rest)
- idempotent by construction (IS NULL filter), therefore resumable
- runtime independent of the nightly cron — the operator launches and monitors it without interfering with other batches

#### The mission flip is deferred outside the PR

Re-flipping the 3 `product_identification + attribute:french` missions (daily/easy + weekly/easy + weekly/medium) **in this PR** would show users "No French product scanned this week" when the column is NULL everywhere (pre-backfill) — UX bug. The flip is a SQL one-row executed after backfill confirmation:

```sql
UPDATE missions
   SET is_active = TRUE
 WHERE qualifier = 'attribute:french'
   AND action_type = 'product_identification'
   AND is_active = FALSE;
-- Expect 3 rows updated.
```

#### Qualifier shape — exact match, no hierarchy

OFF `origins_tags` can contain various shapes:
- `["en:france"]` — the dominant case (measured 2026-05-11 on the live API)
- `["en:france", "en:european-union"]` — common
- `["en:france", "fr:saint-martin-de-gurson"]` — fine French commune origin
- `["en:france", "en:non-european-union"]` — product made in France with non-EU ingredients

V1 decision: **strict** match on `{"en:france", "fr:france", "en:made-in-france"}` (case-insensitive). No hierarchy parsing (`en:european-union` does NOT trigger the french emit). Rationale: missions target made-in-France products, not "anything European".

### Idempotency

3 `product_identification` events share the same `scan_id` but carry distinct `idempotency_key` values:
- vanilla = `<scan_id>`
- organic = `<scan_id>:organic`
- french  = `<scan_id>:french`

Reward_events UNIQUE(user_id, reference_type, reference_id) coexists with the 3 rows without collision.

### Extended emit matrix — up to 5 trigger_action per scan

| # | action_type | qualifier | idempotency_key | Gating |
|---|---|---|---|---|
| 1 | `<scan_type-mapped>` | `None` | `<scan_id>` | always |
| 2 | `<scan_type-mapped>` | `attribute:organic` | `<scan_id>:organic` | `is_organic_product(labels_tags)` |
| 3 | `<scan_type-mapped>` | `attribute:french`  | `<scan_id>:french`  | `is_french_product(origins_tags)` (Phase C-2) |
| 4 | `scan_distinct` | `category:<tag>` | `<scan_id>:distinct:category:<tag>` | `categories_tags` non-empty |
| 5 | `scan_distinct` | `store:<uuid>` | `<scan_id>:distinct:store:<uuid>` | `scan_store_id is not None` |

The 3rd emit (french) silently accumulates reward_events as long as the corresponding missions are `is_active=false` — when the operator flips them live, the `apply_action_event_to_user_missions` runtime cannot backfill historical events (missions are scoped by `period_start`), but from the T+flip instant onwards events arrive into active missions normally.

### Out of scope C-2

- Re-flip of the 3 `fill_product_field + attribute:french` missions — depends on the CL "Product Origin" form (post-C-5 backend, not yet delivered).
- `attribute:organic` historical backfill: `labels_tags` was already populated in the original schema → C-1 was able to activate its 3 missions immediately, no backfill needed. C-2 pays the price of arriving after the fact.

---

## Phase C-3 Evolutions — scan_distinct emit (2026-05-11)

> **Status**: ✅ shipped (PR to merge). Branch `feat/missions-phase-c3-scan-distinct`.
> **No migration** — the 8 `scan_distinct` templates are already `is_active=true` since `miss_pb` (Phase B). C-3 only delivers the upstream emission wiring on the PA side.

### Affected catalogue

8 V1 templates (`MISSION_TEMPLATES_V1` Mission 8 + Mission 9):

| action_type | qualifier | frequency | difficulty | target |
|---|---|---|---|---|
| `scan_distinct` | `category` | daily | easy | 2 |
| `scan_distinct` | `category` | daily | medium | 3 |
| `scan_distinct` | `category` | daily | hard | 5 |
| `scan_distinct` | `category` | weekly | easy | 5 |
| `scan_distinct` | `category` | weekly | medium | 8 |
| `scan_distinct` | `category` | weekly | hard | 12 |
| `scan_distinct` | `store` | weekly | easy | 2 |
| `scan_distinct` | `store` | weekly | medium | 3 |

The template qualifier is the **type-tag** (`category` / `store`); the emitted event carries the full `<tag>:<value>` qualifier. The runtime (`missions_repository.apply_action_event_to_user_missions` branch B):

1. `partition(":")` extracts `tag = "category"` (resp. `"store"`).
2. Match `m.qualifier = tag` selects the family templates.
3. The full qualifier (`category:en:dairies`, `store:<uuid>`) is appended deduplicated to `user_missions.tracked_values` JSONB.
4. `current_count = jsonb_array_length(tracked_values)`.

### Qualifier shape contract

| Source | Emitted qualifier | Tracked value (post `partition(":")`) |
|---|---|---|
| `products.categories_tags[0]` | `category:<off-tag>` | `<off-tag>` (e.g. `en:dairies`) |
| `scans.store_id` post-reconciliation | `store:<uuid>` | `<uuid>` |

`partition(":")` only splits on the **first** colon — OFF tags (`en:dairies`, `fr:produits-laitiers`) retain their language prefix in the tracked value, without ambiguity.

### Choice: `categories_tags[0]` (most-specific) vs `[-1]` (broadest)

V1 decision: use **`categories_tags[0]`** (the most specific tag). Rationale:

- **Tangible UX signal**: "you scanned an apple" speaks more to the user than "you scanned a plant product".
- **1:1 mapping without heuristics**: no sorting by hierarchy depth; each OFF product has its most specific canonical tag in `[0]`.
- **Catalogue accommodation**: a single `scan_distinct + category + target=5/week` template naturally fills with a diversity of real products (5 apples ≠ 5 dairies). With the broadest, the mission would risk being too easy (all fruits → 1 single tracked value).
- **Possible C-3.1 evolution**: if PO feedback reveals that targets are too high, we could emit **two** qualifiers per scan (most-specific AND broadest) — the JSONB dedup would absorb this cleanly. Backwards-compatible.

### Implementation

Pure helper (side-effect-free): `webservices/ratis_product_analyser/services/product_attributes.py::derive_scan_distinct_qualifiers(*, categories_tags, store_id)` → returns 0 to 2 strings.

Emission: `webservices/ratis_product_analyser/services/reconciliation_service.py::_default_reward_trigger`. For each qualifier returned by the helper, an independent `trigger_action` is emitted with:

- `action_type="scan_distinct"`
- `qualifier=q`
- `idempotency_key=f"{scan_id}:distinct:{q}"`
- `context.tracked_value=q.split(":",1)[1]` (persisted in `reward_events.payload`)

### Emit matrix — up to 4 trigger_action per scan (Phase C-3, extended to 5 by C-2)

For a reconciled scan on a product with an OFF category + organic label + resolved store:

| # | action_type | qualifier | idempotency_key | Gating |
|---|---|---|---|---|
| 1 | `<scan_type-mapped>` | `None` | `<scan_id>` | always |
| 2 | `<scan_type-mapped>` | `attribute:organic` | `<scan_id>:organic` | `is_organic_product(labels_tags)` |
| 3 | `scan_distinct` | `category:<tag>` | `<scan_id>:distinct:category:<tag>` | `categories_tags` non-empty |
| 4 | `scan_distinct` | `store:<uuid>` | `<scan_id>:distinct:store:<uuid>` | `scan_store_id is not None` |

The 4 idempotency_keys are unique → `reward_events UNIQUE(user_id, reference_type, reference_id)` survives; a Celery retry deduplicates cleanly on the server side.

**Phase C-2 extends this matrix** with a 5th emit `attribute:french` (`<scan_id>:french`, gating `is_french_product(origins_tags)`) — see Phase C-2 section above.

### Special gating: unmatched scans

A scan without `product_ean` (rare in reconciliation) fires **only** the vanilla event. Design decision: the 8 `scan_distinct` missions (category + store) are intentionally tied to a **resolved product** — without it, "scan 5 distinct categories" carries no user meaning. If user research shows that store diversity alone warrants progression, revisit by widening the store-only gating to unmatched scans (a 1-line change in `reconciliation_service.py`).

### Out of scope C-3

- **C-2** `attribute:french` — requires `products.origins_tags` enrichment + OFF re-sync.
- **C-4** `promo_found` — regex layer on OCR receipt lines.
- **C-5** `fill_product_field` endpoint + CL UI.

---

## Phase C-4 Evolutions — promo_found emit via regex layer (2026-05-11)

> **Status**: ✅ shipped (PR to merge). Branch `feat/missions-phase-c4-promo-found`.
> **No migration** — the 4 `promo_found` templates are already `is_active=true` since `miss_pb` (Phase B). C-4 only delivers the upstream emission wiring on the PA side.

### Unlocked catalogue

4 V1 templates (`MISSION_TEMPLATES_V1`, action_type=`promo_found`):

| action_type | qualifier | frequency | difficulty | target |
|---|---|---|---|---|
| `promo_found` | None | daily | easy | 1 |
| `promo_found` | None | weekly | easy | 1 |
| `promo_found` | None | weekly | medium | 2 |
| `promo_found` | None | weekly | hard | 3 |

Before C-4 these 4 missions were **ghost**: no call-site emitted `trigger_action("promo_found", ...)`. They existed in the catalogue but could not progress.

### Decision: regex layer vs LLM prompt rework

**Chosen approach**: bolt-on regex layer, post-comprehend, on the raw OCR text.

**Rejected alternative**: extending the V3 prompt (`comprehend.py:_PROMPT_TEMPLATE`) to capture promos in the `parsed_jsonb` schema.

Rationale:
- **Contract preservation**: a prompt rework would break the V3 contract tests on `ParsedTicket` (immutable schema, sha256 hash). Ripple-effect on LLM → comprehend → match → persist → tests.
- **Acceptance decoupling**: the promo signal is **observational** (mission driver), not **load-bearing** (does not influence the receipt acceptance decision). Coupling it to the LLM decision would create a critical fragility point on the cashback payment flow for a marginal UX gain.
- **Runtime tunability**: patterns live in `ratis_settings.json` — an admin can add an Auchan/Lidl pattern without redeployment.
- **Rollback escape hatch**: the `pipeline.promo_detection.enable=false` flag disables the detector entirely without a code revert.

### The 7 default patterns

Defined in `ratis_settings.json § pipeline.promo_detection.patterns`, mirrored also in `worker/pipeline_v3/promo_detector.py::DEFAULT_PROMO_PATTERNS` (fail-safe if settings are inaccessible):

| Pattern (regex) | Target |
|---|---|
| `\bpromo\b` | "PROMO ..." or "Promo:..." |
| `\bremise\b` | "Remise 10%" or "Remise appliquée" |
| `\br[ée]duction\b` | "Reduction" / "Réduction" (accent-permissive) |
| `-\s?\d+(?:[,.]\d+)?\s?[€%]` | "-10€" / "- 5,50€" / "-20%" |
| `\boffre\b` | "Offre fidélité" / "Offre du jour" |
| `\bsoldes?\b` | "Solde 30%" / "Soldes" |
| `\b[ée]conomies?\b\s*:?\s*\d+` | "Economie 2,50€" — Carrefour shape |

All compiled with `re.IGNORECASE`. Initial coverage: Carrefour, Monoprix, Franprix. Auchan / Lidl variants out of scope for V1 (to be tuned after real data).

### Distinctness rule (dedup-by-pattern)

**The same pattern matched on multiple lines counts as 1 signal**. A receipt with `PROMO 10%` repeated 3× emits **one** promo signal (the `\bpromo\b` pattern fired). This rule is encoded in `detect_promos`: we use `compiled.search()` (one call per pattern), not `findall()`.

**Different** patterns matching independently count **separately**: a receipt with `PROMO` AND `-2,50€` emits 2 signals (two distinct patterns fired).

Rationale: the "find 1 promo / day" mission is about the **presence** of promo behaviour, not multiplicity. If we counted `findall`, a Carrefour receipt with 8 "PROMO" lines would trigger 8 progressions in 1 scan — not the intention.

### Emission site

Bolt-on in `worker/receipt_task.py::_award_scan_rewards` (shared V2/V3 helper — cf audit F-PA-1). After the existing `trigger_action("receipt_scan", ...)`:

```python
def _emit_promo_found_if_any(receipt, raw_receipt_text: str) -> None:
    matches = detect_promos(raw_receipt_text, patterns=..., enable=...)
    if matches:
        trigger_action(
            receipt.user_id,
            "promo_found",
            quantity=len(matches),
            idempotency_key=f"{receipt.id}:promo",
            context={
                "receipt_id": str(receipt.id),
                "patterns_matched": [m.pattern for m in matches],
            },
        )
```

Idempotency_key `<receipt_id>:promo` → a Celery retry hitting the same tail deduplicates via `reward_events UNIQUE(user_id, reference_type, reference_id)`.

### Raw text plumbing

**The raw OCR text is not persisted anywhere in V3** (`parsed_tickets.parsed_jsonb` contains the structured output; not the raw text). Decision: to avoid a migration + RGPD review (store name, purchase date, accidental PII), we keep the text in memory:

- **V3**: `run_pipeline_v3` returns `raw_receipt_text` (newline-joined `RawBlock.text`) in its result dict. `receipt_task.py` passes it to `_award_scan_rewards(receipt, raw_receipt_text=...)`.
- **V2**: `receipt_task.py` joins `pipeline_result.ocr_result` (list of `(text, conf)` tuples) with `\n` before calling `_award_scan_rewards`.

The `raw_receipt_text` kwarg is **optional** (default `None`) — pre-C4 callers do not break, the detector is opt-in.

### Out of scope C-4

- **Per-item promo**: counting is at receipt level, not per item. No `category:*` qualifier on `promo_found`.
- **Retailer-specific Auchan/Lidl patterns**: to be added after real empirical data (Sentry breadcrumbs on `patterns_matched` enable observation).
- **Receipts in English**: V0/V1 fr-only (cf CLAUDE.md § i18n).
- **Frontend exposure**: no UI display of matched pattern details — only in the `reward_events` audit payload. On the UX side the user simply sees the "Promo found" mission progress.

### Post-merge monitoring

- Sentry breadcrumb on `_emit_promo_found_if_any` exceptions (best-effort, logged at WARNING).
- Metric to monitor: `reward_events.payload->>'patterns_matched'` distribution — if one pattern dominates at 99% it is too permissive; if another fires 0%, remove it.
- Known likely false positive: `\bpromo\b` matches `"promotion"` (intentional — promotion = promo) but would also match `"promouvoir"` if it appeared on a receipt (unlikely but documented).

---

## Phase C-5 Evolutions — fill_product_field backend endpoint (2026-05-11)

> **Status**: ✅ backend shipped (PR to merge). Branch `feat/missions-phase-c5-fill-product-field`.
> **Migration**: `20260511_2100_c5pc` creates `product_contributions` (audit + status enum).
> **No mission flip migration** — the 6 `fill_product_field + qualifier=None` templates are already `is_active=true` since `miss_pb` (Phase B). C-5 only delivers the `trigger_action` emission site.

### Unlocked catalogue

6 V1 templates (`MISSION_TEMPLATES_V1`):

| action_type | qualifier | frequency | difficulty | target |
|---|---|---|---|---|
| `fill_product_field` | None | daily | easy | 2 |
| `fill_product_field` | None | daily | medium | 4 |
| `fill_product_field` | None | daily | hard | 6 |
| `fill_product_field` | None | weekly | easy | 10 |
| `fill_product_field` | None | weekly | medium | 12 |
| `fill_product_field` | None | weekly | hard | 15 |

These 6 templates were ghost in practice before C-5 (no route fired `trigger_action("fill_product_field", ...)`).

### Endpoint contract

```
POST /api/v1/product/{ean}/contribute
Authorization: Bearer <jwt>
Content-Type: application/json

{
  "field": "brands" | "categories_tags" | "labels_tags" | "name",
  "value": "<str>" (scalar fields) | ["<tag>", ...] (array fields)
}
```

Responses:

| Code | Case |
|---|---|
| `201` | INSERT created. `body.status` = `applied` (UPDATE products) or `pending_review` (admin queue). |
| `200` | 24h idempotency window absorbed the call — same row returned, no new credit. |
| `401` | JWT missing or invalid. |
| `404` | Unknown EAN (`product_not_found`). |
| `422` | Validation: wrong type, length, tag shape, etc. (`contribution_*`). |

Response body:

```json
{
  "id": "<uuid>",
  "status": "applied" | "pending_review",
  "field": "<field>",
  "applied": true | false,
  "idempotent": false (or true for 200)
}
```

### Apply-vs-queue decision logic

The endpoint inspects the target field on `products`:

- **NULL or empty field** (empty string or empty list) → `status='applied'`: direct UPDATE of `products.<field>` + INSERT `product_contributions` + **trigger_action fires** (`action_type='fill_product_field'`, `qualifier=None`).
- **Non-empty field** → `status='pending_review'`: INSERT `product_contributions` only, products UNCHANGED, **no trigger** (admin will vet the override before any credit to prevent bad faith).

Rationale: we reward filling the unknown (data hole filled), not modifying existing data. The eventual mission credit on `pending_review` will come from the future admin worker (out of scope C-5).

### Validation

| Field | Accepted type | Constraints |
|---|---|---|
| `brands`, `name` | `str` | non-empty after strip, ≤ 200 chars, no control chars (`\\x00-\\x1F\\x7F`) |
| `categories_tags`, `labels_tags` | `list[str]` | 1..30 entries, each ≤ 100 chars, regex `^[a-z]{2}:[a-z0-9-]+$` (OFF shape) |

Any violation → 422 with `detail='contribution_<code>'` (`value_type`, `value_too_long`, `value_invalid_tag`, `value_invalid_chars`, `value_empty`, `value_too_many_entries`).

### Idempotency window

Lookup: `SELECT id FROM product_contributions WHERE user_id=:u AND product_ean=:e AND field=:f AND created_at > now() - interval '24 hours'`. If a row matches, the endpoint returns 200 with that row.id, **without** a new INSERT and **without** a new `trigger_action`. The replayed `value` is ignored (correction or change requires a new admin path).

Why 24h? Absorbs mobile double-taps + allows a genuine correction the next day if the user realizes they made a mistake. The server-side `reward_events.idempotency_key` (`contribution:<contribution_id>`) remains the last-resort forensics anchor.

### Trigger emit shape

```python
trigger_action(
    user_id,
    "fill_product_field",
    quantity=1,
    qualifier=None,
    idempotency_key=f"contribution:{contribution_id}",
    context={
        "contribution_id": "<uuid>",
        "product_ean": "<ean>",
        "field": "<field>",
        "source": "user_contribution",
    },
)
```

Only `qualifier=None` fires in V0 — the 3 `fill_product_field + attribute:organic` templates remain disabled pending follow-up (cf "Out of scope C-5").

### Audit log

Every `applied` or `pending_review` call writes a `pipeline_audit_log` (`phase='manual'`, `event='product_contribution'`, payload = `{contribution_id, user_id, product_ean, field, status}`). Best-effort: an INSERT audit log failure logs at WARN but does not abort the contribution (the `product_contributions` row remains the forensics source of truth).

### Table `product_contributions`

```sql
CREATE TABLE product_contributions (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID REFERENCES users(id) ON DELETE SET NULL,
  product_ean TEXT NOT NULL,
  field TEXT NOT NULL,            -- whitelist via CHECK
  value_text TEXT,                -- scalar payload
  value_array TEXT[],             -- array payload
  status TEXT NOT NULL DEFAULT 'applied',  -- enum CHECK
  rejected_reason TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  reviewed_at TIMESTAMPTZ,
  reviewed_by_admin_id UUID,
  CHECK (field IN ('brands', 'categories_tags', 'labels_tags', 'name')),
  CHECK (
    (field IN ('brands','name') AND value_text IS NOT NULL AND value_array IS NULL)
    OR
    (field IN ('categories_tags','labels_tags') AND value_array IS NOT NULL AND value_text IS NULL)
  ),
  CHECK (status IN ('applied', 'rejected', 'pending_review'))
);
CREATE INDEX idx_product_contributions_user_ean ON product_contributions(user_id, product_ean);
CREATE INDEX idx_product_contributions_status_created
  ON product_contributions(status, created_at) WHERE status = 'pending_review';
```

ORM mirror `ratis_core.models.product_contributions.ProductContribution` — Pattern A guard (`test_schema_sync`) passes.

### Out of scope C-5

- **CL mobile UI** "Complete this product" — separate OTA. The endpoint is live but missions remain ghost in practice for V0 deploy as long as the client does not call the route.
- **3× `fill_product_field + attribute:organic`** missions remain disabled. They require a qualifier-enriched emission to detect that a fill enriches the `labels_tags` field with an organic tag — a more subtle follow-up wave.
- **Admin endpoints** for `status='pending_review'` rows (approve / reject / merge). Follows in a separate PR. The partial index `idx_product_contributions_status_created` is already in place to support them.

---

## Buffer + Burst Evolutions (2026-05-09)

> **Status**: design validated, implementation pending (PR upcoming, branch `feat/buffer-burst-v1`).
> **Authoritative spec**: `docs/superpowers/specs/2026-05-09-buffer-burst-design.md` (559 lines).
> **Parent gamif ARCH**: see [[ARCH_gamification]] § Buffer + § Burst + § Leaderboard Burst for the complete mechanics, error codes, formulas, and concrete examples.

The old **Stonks** mechanic (`boost_count × 1.1^n CAB`) is replaced by two distinct mechanics serving two opposite user profiles:

- **Buffer** (= ex-Stonks renamed) — **active** extension of a daily mission: `target × 2^n`, `cab × (n+1)`, duration `+n days`. Cap `n_max = 3 daily`. Weekly **non-bufferable**. Free.
- **Burst** — **passive** unlocking of exponential XP tiers after exceeding the objective (`xp × 2^n_burst`, 0 CAB, no cap). Anti-Buffer lock on 1st burst-claim.

### DB changes (Alembic migration)

```sql
-- Rename existing column
ALTER TABLE user_missions RENAME COLUMN boost_count TO buffer_count;

-- New columns
ALTER TABLE user_missions
  ADD COLUMN burst_count INT NOT NULL DEFAULT 0,
  ADD COLUMN period_extended_until TIMESTAMPTZ NULL,
  ADD COLUMN burst_locked BOOLEAN NOT NULL DEFAULT FALSE,
  ADD COLUMN portions_claimed INT NOT NULL DEFAULT 0;

-- Drop obsolete table (pre-prod, non-critical loss)
DROP TABLE stonks_records;

-- New table for Burst monthly + all-time leaderboard
-- (cf docs/superpowers/specs/2026-05-09-buffer-burst-design.md § Data model for the complete definition)
CREATE TABLE mission_xp_records (...);
```

### Affected endpoints

- `POST /api/v1/gamification/missions/{id}/buffer` — **renamed** from `/boost`. Applies 1 Buffer (free, 0 CAB).
- `POST /api/v1/gamification/missions/{id}/claim` — **modified**: cumulative multi-claim + double gating `min(paliers_atteints, jours_écoulés) − portions_claimed`. Degenerate case (`buffer_count=0`) remains compatible with the old all-or-nothing behaviour.
- `POST /api/v1/gamification/missions/{id}/burst-claim` — **new**: claims 1 or more Burst tiers (XP only). Sets `burst_locked = TRUE` on 1st claim.
- `GET /api/v1/gamification/leaderboard/burst-monthly` — **new**.
- `GET /api/v1/gamification/leaderboard/burst-alltime` — **new**.

### Anti-Buffer lock (Buffer ⊕ Burst exclusion)

On the 1st `burst-claim` on a mission, `burst_locked = TRUE` is set irreversibly. Any subsequent attempt to `apply_buffer` on this mission returns `409 burst_locked`. A tactical choice for the user: defer (Buffer) or challenge yourself (Burst), not both.

### Double gating distribution

For a buffered mission with `n`, the total reward `R × (n+1)` is spread over the `n+1` day window, conditioned by progress tiers. On each claim, the API pays out `min(paliers_atteints, jours_écoulés) − portions_claimed` portions. Allows daily multi-claim OR a single claim at the end of the window, without friction.

### No new CAB reason

We reuse `mission_reward` (existing) for Buffer claims. No `cabecoin_transactions.reason` CHECK migration. No KP-08 sync (3 locations).

### Implementation phases

1. Alembic migration (rename + 4 cols + drop + create table)
2. ORM models (`UserMission` cols, new `MissionXpRecord`)
3. Services: `apply_buffer()`, `claim_mission()` reworked, new `claim_burst()`, `burst_service`, `leaderboard_service`
4. Routes (3 modified, 4 new)
5. Mini-cron `notify_buffer_deadlines` every 10 min
6. TDD tests (24 cases — see spec § Required TDD Tests)
7. Frontend: hooks + Buffer/Burst UI + leaderboard screen
8. i18n locales/fr.json

ETA: ~4-5 cumulative SA days (backend + frontend + notif).
