# ARCH seed test data — local dev seeded DB

> Deterministic seed test data for the local dev DB: 6 personas, 14 stores, 25-26 products, 500 receipts / 3 567 scans, 274 price_consensus, 5 subscriptions, 8 gift_card_orders, 5 withdrawals, 10 product_knowledge. Waves 1-5 shipped 2026-05-11, Step 4 gamification BLOCKED.
> @tags: seed test-data ratis_seed personas stores products receipts consensus subscriptions gift-cards withdrawals shipped-v0 waves dev-db prng make-seed-rebuild
> @status: LIVRÉ V0
> @subs: auto

> **Status** : ✅ **Shipped V0** (Waves 1-5 complete, 2026-05-11).
> Step 4 (gamification) BLOCKED on prereq gamif templates — out of V0.
> Steps 1 / 2 / 2-bis / 3 / 5 / 6 / 7 / 8 / 9 ✅ ; Step 4 ⏳ blocked ; Step 10 ⏳ spec deferred (V1).

> **See also** — operator runbook : [`scripts/seed/README.md`](../../scripts/seed/README.md) (first-time setup, switching modes, troubleshooting).

> **Owner** : Guillaume (PO + orchestrator)
> **Reference brainstorm** : conversation Claude orchestrator du 2026-05-08.

### Implem progress

- **Wave 1** ✅ (2026-05-11) — Infra + scaffold : `ratis_seed` bootstrap, `.env.dev/seed.example`, Makefile targets, `scripts/seed/` skeleton with DA-5 safety guards.
- **Wave 2** ✅ (2026-05-11) — Foundation data : 6 personas (`users.py`) + 14 stores (`stores.py`) + 25 food products (`products.py`) + barcode HTML generator (`barcodes.py` → `docs/seed/barcodes.html`) + `provider='dev'` CHECK widening (migration `20260511_2000_seed_provider_dev_widen`) + e2e test (`test_seed_e2e.py`). `make seed-rebuild` now produces real DB rows (6+14+25+3 audit + 6+6 balances) ; `make seed-barcodes` regenerates the 26-barcode HTML (25 valid + 1 synthetic invalid EAN). Decision Wave 2 : Option A — `provider='dev'` at DB level via migration (cleaner DB semantics, no impact on prod code paths, 3-layer defense via ENVIRONMENT + URL pattern + email sentinel).
- **Wave 3** ✅ (2026-05-11) — Scans content (Step 3) : 47 bob + 312 charlie + 13 diane + 128 eve receipts, total 500 receipts / 3 567 scans (277 bob / 3 093 charlie / 57 diane / 140 eve) / 274 price_consensus rows (3 181 links) / 3 102 cabecoin_transactions credits (NONE for eve — shadow ban silent skip respected). 10 narrative scenarios materialised (OCR borderline / unmatched / rejected / pending fresh / 30+ items / battlepass tier-up / referral first / eve duplicate / eve geo outlier / eve EAN mismatch). Approach C : 4-persona PRNG seeded at distinct offsets (`PRNG_BASE_SEED=42 + persona offset`) + 10 hardcoded scenarios with deterministic UUIDs `11111111-…` / `22222222-…`. Idempotency via `_already_seeded()` short-circuit. **Side tables deferred** : `product_knowledge` (Wave 5) ; `price_consensus_history` (only relevant on price changes, not load-bearing for demo) ; anti-fraud fingerprint columns on `receipts` left NULL (auto-populated by pipeline_v3 hot-path, NOT pre-seeded). Decision Wave 3 : per-persona PRNG seed offsets are mandatory — a single shared seed value caused cross-persona UUID collisions on the `uq_cabtx_scan_credit` partial UNIQUE index (surfaced via the E2E test, fixed before merge).
- **Wave 4** ✅ (2026-05-11) — Monetization (Step 5) : 5 subscriptions (4 charlie + 1 alice trial) + 8 gift_card_orders for charlie (5 `referral_reward` ← 3 issued/eligible + 2 pending/cooldown anti-churn 30d per KP-07-bis ; 3 `shop_purchase` cashback redemptions staggered -6mo / -3mo / -1mo) + 5 cashback_withdrawals (3 charlie processed/pending/failed + 2 diane processed-pre-DELETE/abandoned-post-DELETE) + 4 paired `WITHDRAWAL` cashback_transactions. Migration `20260511_2200_cashback_abandoned` ships the `'abandoned'` widening on `cashback_withdrawals.status` CHECK (decision 2026-05-08, ARCH § Cashback abandonment) + ORM mirror + Pattern A schema-sync guard re-passes. **Schema CHECK mapping** : brief's `referral_payout` ↔ schema `referral_reward` ; brief's `cashback_redemption` ↔ schema `shop_purchase` ; subscription "trialing" ↔ `status='pending'` + `payment_ref=NULL` (admitted by `payment_ref_coherence` for non-active/expired statuses). **Out of scope (R33)** : `account_deletion_absorption` cashback_transactions row for Diane's abandoned withdrawal — requires widening `cashback_transactions.type` CHECK + service-side flow in `account_service.delete_account` + UX modal (tracked in `PROD_CHECKLIST.md § RGPD Cashback handling at account deletion`).
- **Wave 5** ✅ (2026-05-11) — Polish (Steps 8 + 9 + deferred product_knowledge) : `make seed-wipe` target (TRUNCATE seeded tables CASCADE + re-run `main.py`, DA-5 guarded mirrored in `wipe.py`) ; 10 `product_knowledge` samples (5 confirmed `ocr_arbitrage`/`user_correction` + 5 unconfirmed manual-queue) seeded via new `scripts/seed/product_knowledge.py` ; `scripts/seed/README.md` operator runbook (first-time setup / reseed / barcode workflow / persona reference / troubleshooting / safety guards). Step 4 (gamification) explicitly noted BLOCKED on prereq gamif templates. `account_deletion_absorption` Diane absorption flow remains out-of-scope (tracked in `PROD_CHECKLIST.md § RGPD Cashback handling at account deletion`). **Sprint complete** : 5 waves shipped 2026-05-11, ~3.5K scans + 500 receipts + 274 consensus + 8 gift_cards + 5 subscriptions + 5 withdrawals + 10 product_knowledge samples + 14 stores + 26 products + 6 personas in `ratis_seed`. `make seed-rebuild` end-to-end runs in <60s on the M4 Pro Mac mini dev-host. **Wave 4-6 backend follow-ups** : Step 4 gamification (blocked), `account_deletion_absorption` flow (tracked in PROD_CHECKLIST), Tiers 5-7 (referral codes / shopping lists / notifications / favorites / etc.) seeded at-need per ARCH line 365.
- **(Step 4 BLOCKED on prereq gamif templates — see Roadmap row.)**

---

## Index

- [Vision](#vision)
- [Components](#components)
- [Topology — 2 DBs strategy](#topology--2-dbs-strategy)
- [Key architecture decisions](#key-architecture-decisions)
- [Personas (test users)](#personas-test-users)
- [Step 1 — Stores curated](#step-1--stores-curated)
- [Roadmap of steps](#roadmap-of-steps)
- [V0 scope — tiers to seed](#v0-scope--tiers-to-seed)
- [Out of scope](#out-of-scope)
- [FAQ](#faq)
- [Glossary](#glossary)

---

## Vision

The seed test data allows an operator (Guillaume today, future devs tomorrow) to **log in as a pre-configured persona in the dev mobile app** and see a realistic populated state (dashboard, scans, missions, cashback, etc.) without having to manually create a user and scan receipts by hand to reach the desired state.

Covered use cases:

- **Mobile dev** : dev-bypass login `__DEV__` only, picker among 5 personas depending on the UI state to test (empty, populated, premium, RGPD, admin)
- **Backend integration tests** : JWT signed locally with a known-state sub UUID, hit endpoints without recreating the user each time
- **Demo / product showcase** : screenshot a "live" dashboard for a PO / investor / co-founder
- **Visual regression** : cover all screen states (empty / loading / populated / edge) via personas

V0 does NOT cover multi-user flows (referral between real users via real OAuth, etc.) — these remain to be tested manually with real OAuth signup.

---

## Components

| Component | Location | Role |
|---|---|---|
| `ratis_dev` (existing DB) | Local PG Mac mini | Clean dev DB. Stores via `batch_osm_sync`, products via `batch_off_sync`/`batch_vrac_seed`. No test users. For direct dev work (manual scan test, bug debug). |
| `ratis_seed` (new DB) | Local PG Mac mini | Seed DB. Same structure (alembic upgrade head applied) + 14 hardcoded stores + ~30 hardcoded products + 5 test users + their scans/missions/etc. |
| `scripts/seed/` (to create) | Repo root | Simplified Python factories (Pattern A). 1 file per domain (`users.py`, `stores.py`, `scans.py`, etc.) + `main.py` that orchestrates. |
| `Makefile` (to extend) | Repo root | Targets `seed-db-init`, `seed-rebuild`, `dev-up`, `seed-up` |
| `.env.dev` / `.env.seed` (to create) | Repo root | Different DATABASE_URLs — switch between the 2 modes |

---

## Topology — 2 DBs strategy

```
                 PG cluster local (Mac mini, port 5432)
                ┌─────────────────────────────────────┐
                │                                     │
                │  ratis_dev   ◄── DATABASE_URL       │
                │  ─────────                          │
                │  · stores via OSM batch sync         │
                │  · products via OFF batch sync       │
                │  · zero test users                   │
                │  · what dev experiments touch        │
                │                                     │
                │  ratis_seed  ◄── DATABASE_URL       │
                │  ──────────                          │
                │  · same schema (alembic head)        │
                │  · 14 stores hardcoded (OSM-derived) │
                │  · ~30 products hardcoded            │
                │  · 5 personas + their scans/etc.     │
                │  · re-runnable via make seed-rebuild │
                │                                     │
                └─────────────────────────────────────┘
```

Mode switch: `cp .env.dev .env.local` (dev work) vs `cp .env.seed .env.local` (seed/demo mode). FastAPI services read `.env.local` automatically.

---

## Key architecture decisions

### DA-1 — 2 separate DBs (`ratis_dev` clean + `ratis_seed` seeded)

**Rationale** :
- Dev work (manually scanning a REAL receipt in debug) does not pollute the seeded state used for demos/screenshots
- Re-seed is re-runnable without destroying ongoing dev work
- Demo screenshots are deterministic (same UUIDs, same balances, same scans every session)

**Rejected alternative** : 1 single DB with idempotent seed targeting `dev_*@ratis.app` (initial Option A). Rejected because mixing dev+seed in the same DB causes surprises (a forgotten scan from the day before appears in the demo, etc.).

**Cost** : 1 more DB to maintain + alembic to apply × 2. Minor.

### DA-2 — Pattern A : simplified Python factories (not factory_boy)

**Rationale** :
- 1 file per domain `scripts/seed/{users,stores,scans,...}.py`
- Each persona = function `def make_dev_bob() -> User: return User(...)`
- Refactoring-safe (SQLAlchemy models are the source of truth)
- No DSL or additional framework — just idiomatic Python

**Rejected alternatives** :
- factory_boy : overkill for 5 users + ~30 items, heavy framework
- YAML/JSON declarative : no FK typing, silent divergence possible when schema evolves
- Raw SQL `INSERT` : hard to maintain on schema changes, no conditional logic

### DA-3 — Hardcoded subset stores+products (not reusing OSM/OFF batches)

**Rationale** :
- Seed re-run in <60s (alembic + Python INSERTs) vs 30min-2h for OSM+OFF batches
- Determinism : test users' scans reference KNOWN store_id/product_id. OSM IDs change with every batch run.
- Maintenance : 14 stores + 30 hardcoded products = 3 readable Python files. Decoupled from OSM upstream.

**Rejected alternative** : pull OSM + OFF into `ratis_seed` then overlay test users. Prohibitive waiting cost + unwanted variability.

### DA-3-bis — Seed = food only, DO NOT seed hygiene/household/beauty

**Rationale** : OFF (OpenFoodFacts) which feeds the products table in prod **covers food items only**. Today Ratis has no official source for non-food products (shampoo, toothpaste, laundry detergent, etc.). If we seed them in `ratis_seed`, we **risk forgetting** that prod doesn't have them → guaranteed "product not found" bug in prod for any user scanning non-food items.

**Decision** : the seed covers **only OFF-compatible food categories** :
- ✅ Fresh food (milk, cheese, meat, fruit/vegetables)
- ✅ Dry grocery (pasta, rice, coffee, sugar, flour)
- ✅ Bakery (bread, pastries)
- ✅ Beverages (water, juice, soda, wine)
- ✅ Generic bulk (already aligned with `batch_vrac_seed`)
- ❌ Hygiene / household / beauty / pet / non-food : **skipped V0**

**Prod tracking** : the non-food source limitation is tracked in **`PROD_CHECKLIST.md` § Qualité données produits (OFF)** with options to investigate (OBP / OPFF / OPF / GS1 / cascade lookup / user crowdsourcing). To address before public-facing marketing "all your everyday purchases".

**Cost** : seed honestly reflects the real state of the product database → representative tests, no surprises in prod.

### DA-4 — Email pattern `dev_*@ratis.app` as sentinel

**Rationale** :
- Easy detection : grep prod DB for `email LIKE 'dev_%@ratis.app'` → 0 results expected in prod
- Pre-commit lint / CI can check : if a seed function ever emits a user without the `dev_` prefix, fail loud
- Clear anti-fraud edge case : "user creating an account with an email matching the pattern" → blocked

### DA-5 — Safety guard : raise if `ENVIRONMENT == 'production'`

The `scripts/seed/main.py` checks at boot :
```python
if os.environ.get("ENVIRONMENT") == "production":
    raise RuntimeError("Seed scripts NEVER run in production.")
```
Plus : `DATABASE_URL` must contain `_seed` or `_dev` substring, otherwise abort.

Double protection against the worst-case scenario: accidentally seeding in prod.

---

## Personas (test users)

**6 personas**, each corresponding to a set of UI states + flows to test. Each persona carries a `trust_score` aligned with [`ARCH_anti_fraud.md`](ARCH_anti_fraud.md) (ratio `agreed/total × 100`, grace period `total < 100`, silent shadow ban at `< 65` once the grace period has passed).

| Persona | trust_score | total contribs | shadow_banned | Role |
|---|---|---|---|---|
| dev_alice (new empty) | 50 (neutral default) | 0 | false | Onboarding / empty states |
| dev_bob (active daily) | 88 | ~55 | false (grace period) | Mainstream daily user |
| dev_charlie (premium power) | 95 | ~285 | false | Power user + Premium subscriber |
| dev_diane (RGPD deleted) | (was 80, anonymized) | (preserved) | n/a | Soft-delete / RGPD compliance |
| dev_admin (service account) | 100 | 0 | false (exempt) | Admin endpoints + audit |
| **dev_eve (shadow-banned)** | **32** | 140 | **true** | Anti-fraud + shadow ban behaviors |

### 🟢 dev_alice — the new registrant

- **Email** : `dev_alice@ratis.app`
- **Provider** : `dev`
- **Registered** : 2 minutes ago
- **State** : email verified ✅, 0 scans, 0 missions, 0 streak, CAB=0, cashback=0€, no subscription, no referral
- **Used to test** : onboarding screens, empty states, tutorial first-scan, "Welcome" UX

### 🔵 dev_bob — the active daily user

- **Email** : `dev_bob@ratis.app`
- **Provider** : `dev`
- **Registered** : 4 months ago
- **State** : 47 receipts + 23 e-labels + 3 manual (mix accepted/pending/unmatched), 12 completed missions + 2 in progress, 14-day active streak, battlepass tier 8, CAB 3,250, cashback 18.40€ (never withdrawn), free tier, 1 referral code, 0 conversions
- **Used to test** : populated normal dashboard, mission cards, streak indicator, scan history mix of states, CAB economy + non-empty cashback display, shopping list + optimized route

### 🟣 dev_charlie — the premium power user

- **Email** : `dev_charlie@ratis.app`
- **Provider** : `dev`
- **Registered** : 1 year ago
- **State** : 312 scans, all missions completed across 6 seasons, 187d streak (personal record), battlepass tier 30 max + 4 past max seasons, CAB 47,500, cashback 8.20€ (just withdrew 50€ as gift card last week), **active monthly Premium subscription** (Stripe-backed, renewal in 12d), 8 referrals (8 conversions), 5 gift_card_orders eligible_at past + 3 still pending 30d anti-churn
- **Used to test** : paywall behaviors (already paid → unlocked), already-active cashback withdrawal flow, top-tier referral leaderboard, subscription management screen, battlepass max tier UI (visual reward unlock), demo / product pitch screenshot

### 🟡 dev_diane — the RGPD edge case

- **Email** : `dev_diane@ratis.app` (anonymized post-DELETE → `deleted_<uuid>@ratis.app`)
- **Provider** : `dev`
- **Registered** : 8 months ago, ran `DELETE /account` 2 months ago
- **State** : `is_deleted = true`, `deleted_at = -2mo`, email anonymized, pseudo blanked, receipts still in DB but user_id points to anonymized entry (legal preservation), cashback_transactions PRESERVED (legal NEVER PURGE), subscription history preserved, CAB balance forced to 0 + historical transactions kept
- **Used to test** : "Account deleted" UI states, RGPD compliance (DELETE flow + post-deletion data access), legal data retention, anti-fraud edge case (re-creating account with same email → blocked via email pattern)

### 🔴 dev_admin — the ops account

- **Email** : `dev_admin@ratis.app`
- **Provider** : `dev`
- **Registered** : 2 years ago (service account, not a human)
- **State** : 0 personal scans, no gamification (admin badges, hidden from public), no subscription, **entry in `admin_users`** with full admin role, ~30 `admin_audit_logs` actions (settings changes, user lookups, manual store validation, manual gift_card grant, etc.) over the last 30 days
- **Used to test** : `/admin/*` endpoints accessible with `ADMIN_API_KEY` + JWT admin user, admin UI (FastAPI + HTMX per `ARCH_admin_endpoints.md`), audit log viewing, settings runtime override (`ARCH_admin_settings.md`), manual store validation flow, manual gift card grant flow

### 🟠 dev_eve — the shadow-banned nuisance

- **Email** : `dev_eve@ratis.app`
- **Provider** : `dev`
- **Registered** : 6 months ago (long enough to be past the grace period `total >= 100`)
- **trust_score** : **32** (= 45 agreed / 140 total contribs)
- **is_shadow_banned** : **true** (since ~3 months post-switch)
- **State** :
  - 140 scans submitted over 6 months (high volume, suspicious in itself)
  - CAB balance = 800 (accumulated BEFORE shadow ban — frozen post-switch)
  - cashback balance = 0€ (silent earning skip post-shadow)
  - 0 completed missions (silent skip — should have had ~30)
  - 0 streak (silent skip)
  - 0 referrals
- **Suspicious patterns seeded in her scans** (drives the agreed/total ratio to 32%) :
  - ~80 honest scans (ratio calibration)
  - ~25 product_ean mismatch consensus (vote against top1_ean)
  - ~10 duplicate receipts (same store + total + 1h time window)
  - ~8 receipts from stores >50km from average user_lat (geo outlier)
  - ~5 implausible receipts total >€400 (refund fraud attempts)
  - ~12 manual entries with suspicious patterns
- **Used to test** :
  - `batch_trust_score` nightly recompute → confirms 32% + classifies shadow_banned
  - Shadow ban silent effects : `weight_override=0` on contribs, silent skip CAB/XP/mission/battlepass in `cab_service.handle_scan_accepted`, **no user notification**, **no UI feedback** (eve sees her scans accepted as normal — all sanctions are silent)
  - Admin queue flagging eve in `admin_audit_logs`
  - CAB economy : balance frozen post-shadow (no growth despite scanning)
  - Anti-fraud edge cases : duplicate detection, geolocation outliers, implausible totals
  - NEVER PURGE invariant : her historical cashback_transactions remain preserved (legal)
  - Cf [`ARCH_anti_fraud.md`](ARCH_anti_fraud.md) for the complete mechanism

---

## Step 1 — Stores curated

14 stores total (12 OSM-derived + 2 hardcoded edge cases).

### Ring 2km — daily shopping (8 stores)

| # | Brand | Name | Distance | Lat | Lon | Format |
|---|---|---|---|---|---|---|
| 1 | Monoprix | Monoprix | 0.12km | 48.89146 | 2.25487 | premium |
| 2 | Franprix | Franprix | 0.24km | 48.89371 | 2.25817 | convenience |
| 3 | Carrefour Market | Carrefour Market | 0.39km | 48.89443 | 2.25266 | medium |
| 4 | Carrefour Express | Carrefour Express | 0.59km | 48.88776 | 2.26139 | convenience |
| 5 | Naturalia | Naturalia | 0.65km | 48.89555 | 2.24928 | bio specialty |
| 6 | G20 | G20 | 0.96km | 48.89805 | 2.24707 | small chain |
| 7 | Carrefour City | Carrefour City | 1.01km | 48.88585 | 2.24597 | convenience |
| 8 | Aldi | Aldi | 1.67km | 48.88251 | 2.23856 | hard-discount |

### Ring 2-5km — driving (3 stores)

| # | Brand | Distance | Format |
|---|---|---|---|
| 9 | Auchan Supermarché | 2.07km | big chain |
| 10 | Intermarché | 1.96km | medium chain |
| 11 | Le Petit Casino | 2.09km | small format Casino group |

### Ring 10-15km — out-of-perimeter (1 store)

| # | Brand | Distance | Why |
|---|---|---|---|
| 12 | Carrefour City | 10.02km | Test "store recommendation outside perimeter" — should NOT appear in dev_bob nearby_stores |

### Edge cases — non-OSM (2 stores)

| # | State | Notes |
|---|---|---|
| 13 | user_suggested pending validation | `lat=0, lng=0, source='user_suggested', validation_status=pending` ; tested via dev_admin manual validation |
| 14 | disabled (soft-delete) | `is_disabled=true, disabled_at=now-30d` ; tested for soft-delete UI |

### SIRET resolution — TODO

For each store in the 2km/5km/10km rings, the real SIRET can be retrieved via :
- https://annuaire-entreprises.data.gouv.fr (search by address) or
- Sirene v3 API : `https://api.insee.fr/entreprises/sirene/V3/siret?q=adresseEtablissement="<address>"`

V0 acceptable : seed with `siret=null` initially. Lazy enrichment as needed (dev_bob scans Monoprix → SIRET resolved → update). The schema accepts NULL on this field.

V1.5 : the OSM → SIREN pivot will be the opportunity to resolve all SIRETs upfront.

### Operator reference coordinates

`USER_LAT = 48.891923, USER_LON = 2.256298` (Levallois-Perret, Hauts-de-Seine, 92).

Test users (dev_bob, dev_charlie) will have `user_lat / user_lng = NULL` for PII (CLAUDE.md). The user perimeter "near Guillaume's home" is configurable at runtime on the frontend side.

---

## Roadmap of steps

| Step | Status | Description |
|---|---|---|
| **1. Stores curated** | ✅ DONE (seeded Wave 2 — 14 rows) | 14 stores selected, lat/lon, brand, format. Seeded via `scripts/seed/stores.py` (12 OSM + 1 user_suggested + 1 soft-deleted). SIRET resolution still pending V1.5. |
| **2. Products hardcoded** | ✅ DONE (Wave 2 — 25 rows) | 25 food-only products (5 × Fresh food + 5 × Dry grocery + 5 × Bakery + 5 × Beverages + 5 × Generic bulk) with real OFF EANs where possible. Seeded via `scripts/seed/products.py`. Cf DA-3-bis (non-food deliberately skipped). |
| **2-bis. Barcode generator** | ✅ DONE (Wave 2) | `make seed-barcodes` → `scripts/seed/barcodes.py` generates `docs/seed/barcodes.html` with 26 EAN-13 barcodes (25 valid + 1 synthetic invalid `9999999999999` for rejection testing). Tool : `python-barcode` (dependency group `seed`). Workflow : open on 2nd screen, phone scans the screen from the dev mobile app. |
| **3. Personas data — scans + receipts** | ✅ DONE (Wave 3 — 500 receipts / 3 567 scans) | 47 receipts bob + 312 charlie + 13 diane + 128 eve. Counts per persona (scan rows) : bob 277 (47 receipts × 3-8 items + 23 e-labels + 3 manual) / charlie 3 093 (309 bulk × 5-15 items + 32-line scenario + 2 single-line) / diane 57 (13 × ~4 items, all accepted, preserved post-DELETE) / eve 140 exact (80 honest + 25 mismatch + 10 duplicate + 8 geo outlier + 5 implausible + 12 manual). Approach **C** : PRNG seed `42 + persona offset` + 10 hardcoded scenarios with deterministic UUIDs. Side tables : `price_consensus` 274 rows / 3 181 links + `cabecoin_transactions` 3 102 credits (NONE for eve — shadow ban silent skip) + `product_knowledge` 10 samples (5 confirmed + 5 unconfirmed, shipped Wave 5). **Deferred** : `price_consensus_history` (price-change only, not seed-relevant). |
| **4. Personas data — gamification** | ⏳ TODO (state frozen, implem blocked on prereq) | Sub-task 4-A : seed ~10 mission templates + 5 battlepass seasons (4 past + 1 current) × 30 tiers + 53 community_challenges (52 past weekly + 1 current) × milestones. Sub-task 4-B : persona state matrix (charlie = day-1 backer, all completed × 5 seasons + 187d streak + 48/52 mystery completed ; bob = 12 missions + tier 8 + 14d streak + 12/16 mystery ; eve = ALL at 0 despite 140 scans for shadow ban silent skip invariant test). **Blocked on prod prereq** : gamif templates to define in `ratis_core/gamif_templates.py` (cf PROD_CHECKLIST.md). Achievements / badges = future feature, not in seed V0. |
| **5. Personas data — monetization** | ✅ DONE (Wave 4 — 5 subs / 8 gift cards / 5 withdrawals) | Subscriptions : 5 states (4 charlie : 1 active monthly current + 1 expired annual past + 1 cancelled monthly past + 1 expired monthly past ; 1 alice trial in-flight = `status='pending'` since schema has no separate `'trialing'`). Gift cards : 8 charlie (5 `referral_reward` ← 3 issued/eligible + 2 pending/cooldown anti-churn 30d KP-07-bis ; 3 `shop_purchase` cashback redemptions staggered -6mo / -3mo / -1mo). Withdrawals : 3 charlie (processed -7d / pending -1d / failed RIB) + 2 diane (1 processed pre-DELETE preserved NEVER PURGE + 1 **abandoned** post-DELETE). Migration `20260511_2200_cashback_abandoned` widens `cashback_withdrawals.status` CHECK to admit `'abandoned'` ; ORM mirror + Pattern A schema-sync re-passes. Charlie's `gift_card_redeemed_ytd_cents` denorm bumped to 8500 (85€ = 20+15+50 from cashback redemptions). **Decision Wave 4** : ship the `abandoned` migration here (Pattern A trodden path) but defer the `account_deletion_absorption` cashback_transaction row for Diane to a follow-up (requires widening `cashback_transactions.type` CHECK + service flow in `account_service.delete_account` + UX modal — full scope tracked in `PROD_CHECKLIST.md § RGPD Cashback handling at account deletion`). Diane's `abandoned` withdrawal row in seed faithfully reflects post-migration shape ; the absorption tx lands when the service flow ships. |
| **6. Infra — 2 DBs setup** | ✅ DONE (Wave 1, 2026-05-11) | `make seed-db-init` (DROP+CREATE+alembic) + `.env.dev/seed.example` + `make dev-up`/`seed-up` switch targets. Smoke test : alembic head applied cleanly on fresh `ratis_seed`. |
| **7. Seed scripts (Python factories)** | ✅ Skeleton DONE (Wave 1, 2026-05-11) | `scripts/seed/{main,users,stores,products,scans,monetization,_engine}.py` + DA-5 safety guards (`ENVIRONMENT=production` OR no `_seed`/`_dev` in URL → `RuntimeError`). Domain placeholders awaiting Wave 2+. |
| **8. Idempotency + reset** | ✅ DONE (Wave 5) | `make seed-wipe` ships TRUNCATE-and-reseed (CASCADE on the 17 seeded tables, DA-5 guards mirrored in `wipe.py`). `make seed-rebuild` remains the full DROP+CREATE+alembic path. Both are idempotent — re-runs are observably no-ops. See `scripts/seed/README.md § Common workflows`. |
| **9. Operator documentation** | ✅ DONE (Wave 5) | `scripts/seed/README.md` — purpose / first-time setup / target reference / personas table / barcode workflow / "adding to the seed" howto / safety guards / troubleshooting. Linked from the top of this ARCH. |
| **10. Spec doc + impl plan** | ⏳ Deferred (V1) | spec doc + writing-plans skill output — not blocking V0 shipping ; revisit when a feature spec template lands in the broader codebase. |

---

## V0 scope — tiers to seed

V0 = **Tiers 1+2+3+4** (Foundation + Core flow + Gamification + Monetization).

Tiers 5/6/7 (Social / Ops / Edge cases) added in V1 when a specific use case requires it.

| Tier | Effort | Without → consequence |
|---|---|---|
| 1. Foundation (users + stores) | 30 min | App does not work at all |
| 2. Core flow (scans + prices + CAB + cashback) | 1.5h | Empty UI, no happy path demo |
| 3. Gamification (missions + battlepass + streaks + mystery) | 1h | Gamif screens empty — product differentiator hidden |
| 4. Monetization (subscriptions + gift cards + withdrawals) | 45 min | Monetization screens not testable |
| ~~5. Social~~ (skipped V0) | 30 min | Referral / lists empty — add in V1 when touching this code |
| ~~6. Ops~~ (skipped V0) | 45 min | Admin dashboard empty — post-launch concern |
| ~~7. Edge cases~~ (skipped V0) | 30 min | Edge states not tested — add case by case |

Total V0 effort : ~4h cumulative dev.

---

## Out of scope

- Real Google/Apple OAuth for test users (the dev-bypass `__DEV__` only is sufficient)
- Multi-tenant (Ratis is single-tenant today)
- Performance load testing (~100K users / 1M scans) — needs separate stress test seed strategy
- Sentry events / Loki logs / n8n incidents for test users (the ITOps pipeline already consumes real prod events from Hetzner)
- OSM → SIREN pivot (separate brainstorm, cf PROD_CHECKLIST.md § Qualité données stores ligne 261)
- Migration to Hetzner staging env with real OAuth (V1+ when staging is deployed)

## Future enrichment — Tiers 5/6/7 (rolling)

Deliberately not detailed in V0 (covering Tiers 1-4 already takes ~5h dev). These categories will be seeded **at the time we touch the relevant code** — iterative rather than upfront design :

| Category | When to seed | Estimated effort |
|---|---|---|
| **Referral codes** detailed (entries `referral_codes` + `referrals` per persona) | When working on the V1 referral flow | 30 min |
| **Shopping lists + items + routes** per persona | When touching `/lists/*` screens | 30 min |
| **Notifications history** (Expo push) per persona | When working on the notification system | 30 min |
| **product_favorites** per persona | When touching favorites screen | 15 min |
| **user_badges** linked to completed missions | When badges system is implemented | 15 min |
| **price_alerts** per persona | When touching alerts UI | 15 min |
| **user_store_preferences** | When touching preferences UI | 15 min |
| **product_tracking** (tracking products for price changes) | When implementing the price change notification | 15 min |

Discipline : each seed addition for these categories = **update to this ARCH** + commit of the seed script + re-run `make seed-rebuild`. Always Pattern A (simplified Python factories).

## Cashback abandonment — refined implementation (2026-05-08)

Recheck `webservices/ratis_auth/services/account_service.py:146` : the current `delete_account` function **preserves** `cashback_withdrawals + cashback_transactions + user_cashback_balance` (legal retention 5-10 years, FK RESTRICT) without transitioning `pending → abandoned`. Our decision acted on 2026-05-08 therefore introduces a delta vs the current code.

**Status (2026-05-11, Wave 4)** : step 1 (schema migration) **shipped** via `20260511_2200_cashback_abandoned` (Pattern A — PG CHECK widened, ORM `CashbackWithdrawal.status_check` mirrored, `db/schema.sql` updated, `test_schema_sync` re-passes). The `ratis_seed` seed loads Diane with `status='abandoned'` directly to exercise the admin queue UI. Steps 2 (service flow) + 3 (UX modal) remain to be shipped next.

**Implementation approach (suggested)** :

1. ✅ Schema migration : `'abandoned'` added to CHECK `status_check` on `cashback_withdrawals.status` (PR Wave 4, 2026-05-11).
2. ⏳ At `delete_account` time (or async batch hook) :
   - INSERT new `cashback_transactions` row of type **`account_deletion_absorption`** (debit of the residual cashback amount) — preserves the NEVER PURGE invariant and traces the absorbed amount. **Prereq** : widening of `cashback_transactions.type` CHECK to admit the new type (current CHECK = `('CREDIT', 'BOOST', 'WITHDRAWAL')`).
   - UPDATE pending cashback_withdrawals.status = `abandoned`
   - Recompute `user_cashback_balance` → reset to 0 (consistent with the absorption transaction)
   - INSERT `admin_audit_logs` row : `action='account_deletion_absorption'`, payload contains the anonymized user_id + absorbed amount
3. ⏳ Before DELETE : explicit UX modal with pending amount to forfeit.

→ Approach consistent with NEVER PURGE (immutable transactions) + balance integrity (recompute) + audit trail (admin_audit_logs entry).

**Tracked in** : `PROD_CHECKLIST.md § RGPD Cashback handling at account deletion`.

---

## FAQ

### Why 2 DBs and not a single one with idempotent seed?

See [DA-1](#da-1--2-separate-dbs-ratis_dev-clean--ratis_seed-seeded). TL;DR : to avoid mixing dev work with seed state, especially for deterministic demo screenshots.

### Why hardcode stores and not reuse `batch_osm_sync`?

See [DA-3](#da-3--hardcoded-subset-storesproducts-not-reusing-osmoff-batches). TL;DR : determinism + speed. 14 hardcoded stores re-seedable in <60s vs 30min for full France OSM batch.

### Does the seed run in prod?

**No**, never. See [DA-5](#da-5--safety-guard--raise-if-environment--production). Double protection : ENVIRONMENT check + DATABASE_URL pattern check + email sentinel `dev_*`.

### How do I seed my local DB?

Once Step 6 (infra) + Step 7 (scripts) are done:

```bash
make seed-db-init      # createdb ratis_seed + alembic migrations
make seed-rebuild      # drop + recreate + run scripts/seed/main.py
cp .env.seed .env.local  # switch to seed mode
docker compose up      # services pickup DATABASE_URL=ratis_seed
```

### How do I switch back to clean dev mode?

```bash
cp .env.dev .env.local
docker compose restart
```

### What if I want to enrich ratis_dev with real OSM stores?

```bash
DATABASE_URL=...ratis_dev python -m batch.ratis_batch_osm_sync
```

That's your choice what you put in ratis_dev — the seed scripts do not touch that DB.

### What happens when the schema evolves?

Alembic migrations apply normally to both DBs:

```bash
DATABASE_URL=...ratis_dev alembic upgrade head
DATABASE_URL=...ratis_seed alembic upgrade head
```

If the migration impacts seeded tables, update `scripts/seed/` in parallel (test : re-run `make seed-rebuild` post-migration).

### Why 14 stores and not 5 or 50?

14 = sweet spot between :
- Covering ~7 distinct chains for chain variety in UI tests
- Covering 4 distance rings (0-2km, 2-5km, 5-10km, 10-15km) for perimeter testing
- Including 2 edge states (user_suggested, disabled) for admin flows
- Remains small enough for : fast seed re-run, human-readable list, easy maintenance

50 stores would add variety but little additional testable value. 5 would miss edge cases.

---

## Glossary

| Term | Definition |
|---|---|
| **Seed** | Process of inserting deterministic initial data into a DB for dev/test/demo purposes |
| **Persona** | Predefined user profile with a fixed set of characteristics (e.g.: dev_bob = active daily user) |
| **Pattern A** | Seed convention via simplified Python factories (not factory_boy) — see [DA-2](#da-2--pattern-a--simplified-python-factories-not-factory_boy) |
| **`dev_*@ratis.app` sentinel** | Email convention to identify seeded users vs real users — see [DA-4](#da-4--email-pattern-dev_ratisapp-as-sentinel) |
| **`ratis_dev`** | Local clean DB used for direct dev work |
| **`ratis_seed`** | Local seeded DB used for demos / UI testing / visual regression |
| **Tier** | Category of seed data ordered by FK priority + demo value (Tier 1 = users + stores, etc.) |
| **Ring** | Category of stores by distance from the operator coordinates (`USER_LAT/LON`) |
| **Step** | Incremental step in the seed implementation roadmap (Step 1 = stores, Step 2 = products, etc.) |
