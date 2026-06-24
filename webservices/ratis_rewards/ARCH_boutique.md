---
type: sub-arch
service: ratis_rewards
parent: ARCH_REWARDS
related: [ARCH_gift_cards, ARCH_cab, ARCH_cab_economy, ARCH_battlepass]
status: planned
tags: [boutique, shop, gift-cards, sink, rotation, runa]
updated: 2026-05-08
---

# ratis_rewards — ARCH Boutique V1

> User-initiated shop that closes the earn→spend loop in V1: the user spends their CAB on Runa gift cards (5 brands Season 1, denominations 5/10/20/50€), subject to fiscal/daily/weekly caps. Phase 1 backend shipped, frontend in progress.
> @tags: boutique shop gift-cards sink rotation runa cab spend caps fiscal-cap weekly-cap saison-1 v1
> @status: EN-COURS
> @subs: auto

> Parent : [[ARCH_REWARDS]] · Relations : [[ARCH_gift_cards]], [[ARCH_cab]], [[ARCH_cab_economy]], [[ARCH_battlepass]]

> Status: 🚧 Phase 1 backend shipped — branch `feat/boutique-v1-backend` 2026-05-08, frontend in parallel
> Design reference: `docs/superpowers/specs/2026-05-08-boutique-v1-design.md`

User-initiated shop that closes the earn→spend loop in V1: the user spends their CAB on Runa gift cards (5 brands Season 1, denominations 5/10/20/50€), subject to fiscal/daily/weekly caps.

---

## Implementation Checklist

**Base checklist — to keep in every ARCH:**
- [x] Alembic migration created and verified — `20260508_2200_boutique_v1.py`
- [x] SQLAlchemy models updated — `User.gift_card_redeemed_ytd_cents`, `GiftCardBrand` UNIQUE(name), `_CAB_REASONS` += 'gift_card_purchase'
- [x] Repository — CRUD functions (`boutique_repository.py`)
- [x] Service — business logic + edge cases (`boutique_service.py`)
- [x] Route — endpoint + error codes (`POST /rewards/gift-cards/order` + `GET /rewards/gift-cards/catalog`)
- [x] Tests written (TDD — before the code) — `test_boutique_v1.py` 15 tests
- [ ] `conftest.py` updated if new `require_env()` — N/A (no new env var)
- [x] `ratis_settings.json` updated if new parameters — section `boutique.*`
- [x] `pg_dump > db/schema.sql` after migration
- [x] `ruff check --fix` clean
- [ ] CI pipeline green — pending PR opening

**Custom checklist:**
- [x] Migration ALTER CHECK constraint `cabecoin_transactions.reason` to add `'gift_card_purchase'`
- [x] Migration seed `gift_card_brands` — 5 brands Season 1 (Amazon, Carrefour, Decathlon, Sephora, Spotify) — Runa placeholders, to be substituted by ops
- [x] Sync `_CAB_REASONS` Python frozenset (3 locations: `models/gamification.py` + `repositories/cab_repository.py` + migration enum — cf KP-08)
- [x] Pydantic schemas: `OrderRequest`, `OrderResponse` (`routes/rewards/gift_cards.py`, route-local — no separate `schemas/boutique.py` file)
- [x] Repository `boutique_repository.py` — `count_redeemed_today_cents()`, `count_redeemed_this_week_cents()`, `get_active_brands()`, `get_brand_if_active()`, `find_recent_duplicate_order()`, `insert_order()`, `increment_user_ytd_cents()`, `get_user_ytd_cents()`
- [x] Service `boutique_service.py` — `create_order()` with cap enforcement + atomic CAB debit + `get_catalog()`
- [x] Route `POST /api/v1/rewards/gift-cards/order` + `GET /api/v1/rewards/gift-cards/catalog` — validation + service call + BackgroundTasks Runa
- [x] TDD tests — 15 cases (14 ARCH + GET /catalog)
- [x] `ratis_settings.json` — section `boutique.*` (ratio, caps, denominations, dedup window)
- [x] Frontend `services/rewards-client.ts` — `orderGiftCard(brand_id, denomination_cents)` + `getCatalog()` (PR #329)
- [x] Frontend `hooks/use-shop-catalog.ts` + `hooks/use-shop-order.ts` + `hooks/use-gift-cards.ts` (computeUsageStats client-side MVP) (PR #329)
- [x] Frontend screens `app/shop/index.tsx` (catalog carousel) + `app/shop/[brand_id].tsx` + confirmation modal (PR #329)
- [x] Frontend update `app/(tabs)/profil.tsx` — un-grey "Shop" entry, link to `/shop` (PR #329)
- [x] i18n: add `shop.*` keys to `locales/fr.json` (PR #329)
- [x] Jest tests hooks + front components (25 tests · 5 suites — PR #329)
- [ ] Runa ops validation — phase 3 (substitute `placeholder-runa-*` provider_brand_id)

**Audit H4 checklist (2026-05-18) — unified fiscal cap across all flows:**
- [x] Migration `20260518_1000_gc_cap_resv` — column `gift_card_orders.cap_reserved_cents INT NOT NULL DEFAULT 0`
- [x] Service `gift_card_cap_service.py` — `reserve_gift_card_cap` / `release_gift_card_cap` / `CapDecision` (advisory lock `gift_card_cap:{user_id}`)
- [x] `reserve` wired in `issue_gift_card` for all 4 flows (boutique hard-block, 3 others → deferred `eligible_at`)
- [x] `release` wired in `_mark_failed` + batch C3 `reconcile_deferred_gift_card_orders`
- [x] Boutique: YTD increment moved from `create_order` → `reserve`; fast-check (fast-fail) kept in route
- [x] Unified advisory lock key `gift_card_cap:{user_id}` (boutique aligned with the other flows)

> ⚠️ One item at a time. Do not move to the next without completing the previous.

---

## Index

- [Context](#context)
- [Acted decisions](#acted-decisions)
- [V1 scope](#v1-scope)
- [Pricing](#pricing)
- [Season 1 catalog](#season-1-catalog)
- [Anti-fraud / anti-bug caps](#anti-fraud--anti-bug-caps)
- [Tables](#tables)
- [Endpoints](#endpoints)
- [Internal logic](#internal-logic)
- [UX flow](#ux-flow)
- [Inter-services](#inter-services)
- [Parameters](#parameters)
- [Rules](#rules)
- [Required TDD tests](#required-tdd-tests)
- [Out of scope V1](#out-of-scope-v1)
- [Glossary](#glossary)
- [Links](#links)

---

## Context

Product brainstorm 2026-05-08, closing the gamif cycle (missions Phase A/B merged + BP Season 1 seeded). The shop = a real CAB sink that closes the earn→spend loop. The infrastructure existed at 80% (tables `gift_card_brands` + `gift_card_orders`, Runa provider integrated, list/detail endpoints); [[ARCH_gift_cards]] explicitly mentioned "Boutique: route not wired — out of original V1 scope". This ARCH re-scopes that perimeter within **V1**.

Overall long-term roadmap:
- **V1** (now) — gift cards only
- **V1.x** — gamif utilities (mission freeze + food reserve Jack + XP boost) integrated into shop UX
- **V1.5 / V2** — UI skins (custom Cabé avatars, themes, profile frames)
- **V2** — Ratis Fridge / Ratis Recipe credits (internal sinks)

Read before starting:
- [[ARCH_gift_cards]] — gift cards infrastructure (tables, Runa, existing endpoints)
- [[ARCH_cab_economy]] § Plafond annuel — fiscal cap source-of-truth (1199€/yr DAS2)
- [[ARCH_cab]] — `award_cab` / atomic CAB debit pattern
- `webservices/ratis_rewards/services/gift_card_service.py` — `enqueue_gift_card()` to reuse

---

## Acted decisions

| # | Decision | Rationale |
|---|---|---|
| 1 | V1 scope = gift cards only | KISS, closes earn→spend loop, infrastructure ready |
| 2 | Pricing `1€ = 5 000 CAB` fixed linear ratio | Simple, readable, tunable later via admin UI |
| 3 | No subscriber gating on items | Sub multiplier ×2 on earns already acts as a filter — avoid double penalty |
| 4 | Season 1 catalog = 5 brands (Amazon, Carrefour, Decathlon, Sephora, Spotify) | 5 distinct categories, anti-duplicate (not Fnac+Amazon, not Carrefour+Auchan) |
| 5 | V1 denominations = 5 / 10 / 20 / 50€ | Drop 100€ in V1 (anti-fraud, re-introduced in V1.x) |
| 6 | Per-card cap = 50€ | Damage control for isolated bug |
| 7 | Daily cap = 100€/day | Anti-burst (= 2× 50€ card or combos) |
| 8 | Weekly cap = 300€/week | Additional safeguard |
| 9 | Annual cap = 1199€/yr (existing DAS2) | Fiscal compliance (already decided [[ARCH_cab_economy]]) |
| 10 | UX = brand carousel → denominations screen → confirmation modal | Cleaner than a flat list of 20 items |
| 11 | Seasonal brand rotation | Category anti-duplicate + scarcity + marketing buzz |

---

## V1 scope

### Included

- Endpoint `POST /api/v1/rewards/gift-cards/order` (the user-initiated shop)
- Catalog of 5 brands seeded in `gift_card_brands` for Season 1
- Cap enforcement (per card / daily / weekly / annual)
- "Shop" mobile screen UX (brand carousel + denominations + confirmation)
- Exhaustive TDD tests (cap reached / insufficient_balance / brand inactive / Runa fail / idempotence)

### Out of scope V1 (= V1.x or later)

See section [Out of scope V1](#out-of-scope-v1) below.

---

## Pricing

Fixed linear ratio:

```
1€ = 5 000 CAB
```

That is:

| Denomination | CAB required |
|---:|---:|
| 5€ | 25 000 |
| 10€ | 50 000 |
| 20€ | 100 000 |
| 50€ | 250 000 |

No volume discount in V1, no sub-only pricing. Tunable via admin UI later (cf [[ARCH_admin_settings]]).

> ⚠️ **[[ARCH_cab_economy]] consistency**: this boutique ratio `5000` replaces the historical ratio `ratio_cab_to_eur_cents=200` mentioned in cab_economy PR2 (= never implemented, indicative value). The boutique establishes a 25× more restrictive ratio.

---

## Season 1 catalog

| Brand | Category | Runa provider | Logo |
|---|---|---|---|
| Amazon.fr | General e-commerce | ✅ post-KYB | logo Amazon |
| Carrefour | Food | to confirm Runa | logo Carrefour |
| Decathlon | Sport / outdoor | to confirm Runa | logo Decathlon |
| Sephora | Beauty / cosmetics | to confirm Runa | logo Sephora |
| Spotify | Streaming / digital | to confirm Runa | logo Spotify |

> ⚠️ **Runa availability to validate at seed time** — not all brands are onboarded. The implementation phase must first check the Runa catalog and substitute if a brand is missing (Season 2+ alternatives: Fnac, Auchan, Apple App Store, Netflix, Zalando).

### Planned rotation pool (Season 2-4 vision)

| Season | General | Food | Sport | Beauty | Digital | Bonus |
|---|---|---|---|---|---|---|
| **Season 1** | Amazon | Carrefour | Decathlon | Sephora | Spotify | — |
| Season 2 | Fnac | Auchan | (skip) | (skip) | Apple App Store | + 1 surprise |
| Season 3 | (rotate) | Leclerc | (skip) | Marionnaud | Netflix | + 1 surprise |
| Season 4 | (rotate) | (rotate) | Decathlon return | (rotate) | (rotate) | + holiday theme |

**Golden rule**: no brand returns 2 consecutive seasons. Pool ≥ 12 brands total to sustain 4 seasons without repetition.

---

## Anti-fraud / anti-bug caps

> **Source of truth for the legal framework**: [[ARCH_cab_economy]] § Plafond annuel (DAS2 + BNC). This section describes the boutique-side implementation only.

### Per-card cap

Max **50€/card** V1 (drop 100€ card). Enforced via Pydantic body request validation.

### Daily cap

Max **100€/day** total (all brands combined).

Calculation: `SUM(denomination) FROM gift_card_orders WHERE user_id=:u AND source_type='shop_purchase' AND created_at >= date_trunc('day', NOW() AT TIME ZONE 'Europe/Paris')`.

### Weekly cap

Max **300€/week** total.

Calculation: `SUM(denomination) FROM gift_card_orders WHERE user_id=:u AND source_type='shop_purchase' AND created_at >= date_trunc('week', NOW() AT TIME ZONE 'Europe/Paris')`.

### Annual cap

Max **1199€/yr** total (existing — cf [[ARCH_cab_economy]] § Plafond annuel).

Calculation: `users.gift_card_redeemed_ytd_cents` (denormalized column updated atomically on each succeeded redeem).

> **Audit H4 (2026-05-18)** — the annual cap is now enforced via `reserve_gift_card_cap` (centralized service `gift_card_cap_service.py`, advisory lock `gift_card_cap:{user_id}`), called in `issue_gift_card` **before** the Runa call. For the boutique (`shop_purchase`), exceeding the cap remains a **hard block** (409 `annual_gift_card_cap_reached` + CAB refund). The boutique keeps a **fast check** at order creation time (`create_order`) to fail-fast without waiting for the issuance lock. The definitive (atomic) check happens on the `reserve` side. See [[ARCH_cab_economy]] § Modèle de réservation H4.

---

## Tables

### Existing (reused, no migration)

- `gift_card_brands` — columns: `id`, `name`, `provider_brand_id`, `logo_url`, `is_active`, `created_at`. Rotation uses `is_active`. Cf [[ARCH_gift_cards]] § Tables.
- `gift_card_orders` — `source_type='shop_purchase'` already allowed in the CHECK constraint. `source_ref_id` = id of the `cabecoin_transactions` debit (idempotence via UNIQUE `(source_type, source_ref_id)`).
- `users.gift_card_redeemed_ytd_cents` — already decided in [[ARCH_cab_economy]] PR2 (denormalized column + annual reset batch January 1st).
- `cabecoin_transactions` — debit with `reason='gift_card_purchase'` (to add to the Python frozenset + ORM model + migration enum CHECK constraint, cf KP-08).

### Required migration

1 minor Alembic migration:
- `ALTER TABLE cabecoin_transactions DROP CONSTRAINT IF EXISTS ...; ADD CONSTRAINT ... CHECK reason IN (..., 'gift_card_purchase')`.
- Sync `_CAB_REASONS` Python frozenset (KP-08 — 3 locations: `models/gamification.py` + `repositories/cab_repository.py` + migration enum).
- Seed `gift_card_brands` with the 5 Season 1 brands (real Runa provider_brand_id to insert post-ops validation).

---

## Endpoints

### Existing (not modified — cf [[ARCH_gift_cards]])

- `GET /api/v1/rewards/gift-cards` — user list
- `GET /api/v1/rewards/gift-cards/{id}` — user detail
- `POST /api/v1/rewards/gift-cards/annual` — creation for annual subscription

### New

#### `POST /api/v1/rewards/gift-cards/order`

Auth: user JWT (`Depends(get_bearer_token)`).

Body:
```json
{
  "brand_id": "uuid",
  "denomination": 2000
}
```

`denomination` in cents — ∈ {500, 1000, 2000, 5000}.

Success response (201):
```json
{
  "order_id": "uuid",
  "brand": "Amazon.fr",
  "denomination_cents": 2000,
  "cab_cost": 100000,
  "new_cab_balance": 32500,
  "status": "pending",
  "estimated_arrival": "in a few seconds"
}
```

Error codes:

| Code | Detail | Trigger |
|---|---|---|
| 400 | `invalid_denomination` | denomination not in {500, 1000, 2000, 5000} |
| 400 | `invalid_brand_id` | brand not in the active catalog |
| 402 | `insufficient_cab_balance` | user CAB balance < cab_cost |
| 404 | `brand_not_available` | `is_active=false` (= rotation) |
| 409 | `daily_redeem_cap_reached` | daily sum + denomination > 100€ |
| 409 | `weekly_redeem_cap_reached` | weekly sum + denomination > 300€ |
| 409 | `annual_gift_card_cap_reached` | ytd_cents + denomination > 119900 |
| 409 | `duplicate_order_recent` | exact retry (same brand+denomination within 1 min per user) — anti-double-tap |

Idempotence: if exact retry (same brand+denomination within 1 minute per user), reject with 409 `duplicate_order_recent`.

---

## Internal logic

### Enforcement order in `POST /rewards/gift-cards/order`

1. Validate body (denomination ∈ {5, 10, 20, 50} EUR).
2. `cab_balance >= cab_cost`? Otherwise → 402 `insufficient_cab_balance`.
3. Daily SUM → if `+ denomination > 100€` → 409 `daily_redeem_cap_reached`.
4. Weekly SUM → if `+ denomination > 300€` → 409 `weekly_redeem_cap_reached`.
5. Fast check `users.gift_card_redeemed_ytd_cents + denomination > 119900` → 409 `annual_gift_card_cap_reached` (fast-fail without lock — the definitive atomic check happens at step 7 via `reserve`).
6. Brand active? Otherwise → 404 `brand_not_available`.
7. All OK → atomic CAB debit + INSERT `gift_card_orders` (status=pending, source_type=shop_purchase, source_ref_id=cabecoin_transactions.id) → background task `issue_gift_card` → `reserve_gift_card_cap(user_id, denomination, 'shop_purchase')` (advisory lock `gift_card_cap:{user_id}`, increments `ytd_cents`, sets `cap_reserved_cents`) → Runa call.
8. On Runa success → UPDATE order status=issued, code=runa_redemption_code.

> **Audit H4** — the `UPDATE users.gift_card_redeemed_ytd_cents += denomination` was moved from step 7 (synchronous creation) to `reserve_gift_card_cap` (issuance, in the background task). The boutique keeps the fast check (step 5) to fail-fast at the route level, but the definitive and atomic increment is under the unified advisory lock `gift_card_cap:{user_id}` — consistent with the 3 other flows.

### Atomic CAB debit (R09)

```python
r = db.execute(
    text("UPDATE user_cab_balance SET balance=balance-:x WHERE user_id=:u AND balance>=:x"),
    {"x": cab_cost, "u": user_id},
)
if r.rowcount == 0:
    raise InsufficientCabBalance()
```

Insert `cabecoin_transactions` (direction=debit, reason=gift_card_purchase) + INSERT `gift_card_orders` in the same transaction. `db.commit()` in route before `BackgroundTasks.add_task(...)` (Runa). **(H4)** The `users.gift_card_redeemed_ytd_cents` counter is no longer incremented here — the authoritative increment happens at issuance via `reserve_gift_card_cap` (cf [[ARCH_cab_economy]] § Modèle de réservation H4).

### Concurrent orders race condition

2 simultaneous POSTs at the daily cap must result in: 1 success + 1 clean fail (409 `daily_redeem_cap_reached`). Pattern: SELECT FOR UPDATE on `users.gift_card_redeemed_ytd_cents` + daily SUM recalculation in the same transaction. Cf KP-41 for concurrent INSERT pattern.

For the annual cap (YTD), serialization is ensured by the advisory lock `gift_card_cap:{user_id}` in `reserve_gift_card_cap` — unified for all flows (boutique + 3 others). No double-counting possible between a boutique order and a referral order issued simultaneously.

---

## UX flow

### Shop screen (mobile, accessible from Profile)

```
┌─────────────────────────┐
│ Boutique                │ ← header
│ Solde : 47 500 CAB      │ ← user balance
│                         │
│ Cette saison :          │
│                         │
│ ┌──────────┬──────────┐ │
│ │ Amazon   │ Carrefour│ │ ← carrousel logos
│ │ from 25k │ from 25k │ │   2 colonnes
│ └──────────┴──────────┘ │
│ ┌──────────┬──────────┐ │
│ │ Decathlon│ Sephora  │ │
│ │ from 25k │ from 25k │ │
│ └──────────┴──────────┘ │
│ ┌──────────┐            │
│ │ Spotify  │            │
│ │ from 25k │            │
│ └──────────┘            │
│                         │
│ [Mes cartes cadeaux] →  │ ← link to GET /rewards/gift-cards
└─────────────────────────┘
```

Tap on brand → denominations screen.

### Denominations screen

```
┌─────────────────────────┐
│ ← Amazon.fr             │
│                         │
│ Carte 5€   25 000 CAB ✓ │ ← ✓ if sufficient balance
│ Carte 10€  50 000 CAB ✓ │
│ Carte 20€  100 000 CAB ✓│
│ Carte 50€  250 000 CAB ✗│ ← ✗ greyed out if insufficient balance
│                         │
│ Tu as fait 0€/100€ aujd │ ← cap usage display
│ Tu as fait 50€/300€ sem │
└─────────────────────────┘
```

Tap on available denomination → confirmation modal.

### Confirmation modal

```
┌─────────────────────────┐
│ Confirmer l'achat ?     │
│                         │
│ Carte cadeau Amazon 20€ │
│ Coût : 100 000 CAB      │
│                         │
│ Solde après achat :     │
│ 47 500 - 100 000 = ...  │
│                         │
│ [Confirmer] [Annuler]   │
└─────────────────────────┘
```

On confirmation → POST endpoint → success → "Ta carte arrive d'ici quelques secondes" → polling on `GET /rewards/gift-cards` until `status=issued`.

---

## Inter-services

### Runa provider (existing — cf [[ARCH_gift_cards]] § Provider)

Reuse of the existing `gift_card_service.enqueue_gift_card()`. Fire-and-forget pattern:

```
1. Route INSERT gift_card_orders (status='pending', source_type='shop_purchase', source_ref_id=<cabecoin_transactions.id>)
2. db.commit()  ← immediate return to caller (201)
3. BackgroundTasks.add_task(issue_gift_card, order_id)
4.   → POST Runa /orders
5.   → UPDATE gift_card_orders SET status='issued', code=..., provider_order_id=...
       ou SET status='failed' si erreur provider
```

V1 considers Runa synchronous (no `PROCESSING` polling → re-poll). Out of scope V1 → V2.

### No other external service

No `ratis_notifier` call, no outbound webhook, no batch dependency. The shop lives in `ratis_rewards` end-to-end.

---

## Parameters

Add to `ratis_settings.json`:

```json
"boutique": {
    "ratio_cab_per_eur": 5000,
    "cap_per_card_cents": 5000,
    "cap_daily_cents": 10000,
    "cap_weekly_cents": 30000,
    "allowed_denominations_cents": [500, 1000, 2000, 5000],
    "duplicate_order_window_seconds": 60
}
```

All tunable via future admin UI (cf [[ARCH_admin_settings]]). The admin will be able to:
- Bump caps when the system is battle-tested
- Re-introduce the 100€ card
- Rotate active brands at each new BP season
- Tune the CAB→€ ratio globally

---

## Rules

- **Fixed ratio** `1€ = 5000 CAB` V1, parameter values checked at service startup (`require_settings("boutique.ratio_cab_per_eur")`)
- **Caps accumulated in `Europe/Paris`** — not UTC (the user sees their daily cap roll over at Paris midnight)
- **Atomic CAB debit** — conditional UPDATE `WHERE balance >= cab_cost`, rowcount==0 → 402 (R09)
- **Anti-double-tap idempotence** — 60s exact match window (brand + denomination + user) → 409
- **No subscriber gating** — the shop is universal (sub multiplier ×2 on earns already acts as a filter)
- **Fire-and-forget Runa** — never block the route on the provider call (reuses [[ARCH_gift_cards]] pattern)
- **Code visible only if `status='issued'`** — null otherwise
- **`assert_owner` mandatory** on every GET `/gift-cards/{id}` (existing)
- **Never log the code** — monetary value, mask in Sentry
- **Migration `cabecoin_transactions.reason`** — KP-08: sync 3 locations (Python frozenset + ORM model + DB CHECK)

---

## Required TDD tests

File: `webservices/ratis_rewards/tests/test_boutique_v1.py`

1. `test_order_success_full_flow` — sufficient balance, active brand, under cap → 201 + INSERT order + UPDATE balance + Runa called via background_task
2. `test_order_insufficient_balance` → 402
3. `test_order_invalid_denomination` (e.g. 30€) → 400
4. `test_order_invalid_brand` (non-existent uuid) → 400
5. `test_order_inactive_brand` (`is_active=false`) → 404
6. `test_daily_cap_reached` (already 80€ today, requesting 30€) → 409
7. `test_weekly_cap_reached` (already 280€ this week, requesting 30€) → 409
8. `test_annual_cap_reached` (already 1180€ this year, requesting 30€) → 409
9. `test_balance_debit_atomic` — the CAB transaction and the INSERT order are in the same DB transaction (rollback if one step fails)
10. `test_idempotency_duplicate_order_recent` — 2nd identical POST within 1 minute → 409
11. `test_brand_seed_count` — 5 brands with `is_active=true` after migration
12. `test_cap_calculation_timezone_aware` — Europe/Paris for daily/weekly cutoff
13. `test_runa_failure_marks_order_failed` — mock Runa 5xx → status=failed (no crash)
14. `test_concurrent_orders_race_condition` — 2 simultaneous POSTs at daily cap → 1 success + 1 clean fail

---

## Out of scope V1

### V1.x — gamif utilities (reorganize into shop UX)

- **Mission freeze** (existing — `POST /rewards/missions/{id}/freeze`, cf [[ARCH_gamification]] § Mission freeze) → standalone today, to integrate into boutique carousel in V1.x
- **Food reserve Jack** (food mission freeze, cf [[ARCH_gamification]]) → same
- **XP boost** → same
- **100€ card** re-introduced when system is battle-tested

### V1.5 / V2 — UI skins

- **UI skins** (custom Cabé avatars, themes, profile frames) → table to create, battlepass branch `reward_type='skin'` already in the existing CHECK ([[ARCH_battlepass]])

### V2

- **Ratis Fridge / Recipe credits** — internal sinks, depends on the brainstorm of these features
- **Async Runa polling** (`PROCESSING` → re-poll in 30s) — V1 considers Runa synchronous
- **Push notification** when card is ready → V2 (via [[ARCH_REWARDS]] and `ratis_notifier`)
- **Code encryption** in database (PCI) → V2 if compliance requires it

### Product backlog (separate)

- **Admin UI** season rotation + param tuning → [[ARCH_admin_settings]] (planned separately)

---

## Glossary

- **Boutique**: user screen where they spend their CAB on gift cards
- **Carousel**: 2-column grid layout on mobile
- **Fiscal cap DAS2**: 1199€/yr beyond = Ratis paperwork (annual tax declaration)
- **Per-card cap**: max 50€/card V1
- **Season 1 catalog**: 5 brands selected for the 1st BP season
- **Seasonal rotation**: changing `is_active=true` on brands at each new BP season (= 3 months)
- **`source_type='shop_purchase'`**: enum in `gift_card_orders` to distinguish these orders from others (annual, battlepass, referral)
- **CAB sink**: user action that consumes CAB (the opposite of earn)

---

## Links

- [[ARCH_gift_cards]] — parent infrastructure ARCH (tables, Runa, existing endpoints)
- [[ARCH_cab_economy]] § Plafond annuel — fiscal cap source-of-truth
- [[ARCH_battlepass]] — BP seasons (aligned rotation)
- [[ARCH_admin_settings]] — boutique cap tuning UI (future)
- `docs/superpowers/specs/2026-05-08-boutique-v1-design.md` — full design doc (372 lines)
- `docs/superpowers/specs/2026-05-08-gamif-calibration.xlsx` — CAB→€ ratio calibration and profiles
