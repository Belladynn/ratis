---
type: cross-cutting
parent: ARCH_RATIS
related: [ARCH_REWARDS, ARCH_cab, ARCH_PRODUCT_ANALYSER, ARCH_referral, ARCH_consensus, ARCH_boutique]
status: in-progress
tags: [cab, economy, multipliers, coverage-bonus, rewards, gift-cards, fiscal-cap, transparency]
updated: 2026-05-08
---

# ratis_rewards — ARCH CAB Economy

> CAB Economy V1: who emits, at which multipliers (coverage_bonus, subscription, streak), which fiscal/daily/weekly caps, how CAB is spent (sink = boutique gift-cards V1, missions/battlepass). Not the battlepass XP (separate), not gift-cards (outside initial V1).
> @tags: cab economy multipliers coverage-bonus rewards gift-cards fiscal-cap transparency sink subscription-multiplier streak cab_service award_cab
> @status: EN-COURS
> @subs: auto

> Parent : [[ARCH_RATIS]] · Relations : [[ARCH_REWARDS]], [[ARCH_cab]], [[ARCH_PRODUCT_ANALYSER]], [[ARCH_referral]]

> Status: in progress
> Branch: `feature/cab-economy-referral-archs`

This ARCH defines the CAB economy for V1: what emits CAB, in what
quantities, with which multipliers, and how those CAB are
reused (sink). It does NOT cover battlepass XP (separate, see
`ARCH_feed_jack.md`), nor gift cards (outside V1, deferred to V2 to
calibrate on data).

---

## Implementation Checklist

**Base checklist:**
- [ ] Alembic migration created and verified (cols `stores.coverage_bonus`, `stores.coverage_bonus_computed_at`)
- [ ] SQLAlchemy models updated (`Store.coverage_bonus`)
- [ ] `cab_service.award_cab()` — add parameters `coverage_bonus`, `subscription_multiplier`, disable `apply_streak_multiplier` on exceptional bonuses
- [ ] Callers of `award_cab()` updated (referral/signup/prestige/cashback-commission pass `apply_streak_multiplier=False`)
- [ ] New batch `ratis_batch_coverage/` — hourly compute of `coverage_bonus` per store
- [ ] Routes that emit CAB: scan receipt/label, mission completion, streak milestone — add `coverage_bonus` lookup at emission time if store_id applicable
- [ ] Tests written (TDD — before the code)
- [ ] `conftest.py` updated if new `require_env()` (none expected)
- [ ] `ratis_settings.json` updated (section `cab`)
- [ ] `pg_dump > db/schema.sql` after migration
- [ ] `ruff check --fix` clean
- [ ] CI pipeline green

**Custom checklist:**
- [ ] Initial seed `stores.coverage_bonus = 1.0` for all existing stores (migration)
- [ ] GH Actions workflow `batch_coverage.yml` with cron `0 * * * *`
- [ ] Manual script `uv run python batch/ratis_batch_coverage/coverage.py` for local dev
- [ ] Verify that `cabecoin_transactions` traces applied multipliers (debug)
- [ ] Disable ×2 subscription on exceptional bonuses (referral, signup, prestige, cashback-commission)
- [ ] E2E tests: a subscribed user with 10-day streak scanning a receipt in a poorly covered store receives the correct amount of CAB

**PR2 checklist (2026-05-02 extension — data actions + annual cap + observability)**:
- [ ] Alembic migration: `users.gift_card_redeemed_ytd_cents`, `users.gift_card_warning_acknowledged_year`, `users.gift_card_year_reset_at` (+ optional index on `ytd_cents > 0`)
- [ ] `ratis_settings.json`: add blocks `gift_cards.{annual_warning_threshold_cents=30500, annual_hard_cap_cents=119900, ratio_cab_to_eur_cents=200}` and `anti_fraud.{suggestion_rate_limit_per_day=5, suggestion_min_trust_score=75}`
- [ ] `ratis_settings.json`: extend `cab.earn.*` with new PR2 keys (`receipt_complete=500`, `barcode_resolve=5`, `batch_i_reconcile=50`, `suggestion_name_only=20`, `suggestion_name_brand_qty=40`, `suggestion_with_photo=60`, `suggestion_unlocks_off_unknown=100`, `daily_ring_claim=30`, `first_time_store=100`, `mission_weekly=200`)
- [ ] V1 grid override: `cab_per_receipt_scan` (V1) removed in favor of `cab.earn.receipt_complete=500` flat
- [ ] Endpoint `POST /rewards/gift-card/redeem` — cap enforcement: 409 `annual_gift_card_cap_reached` if `ytd + amount > 119900`
- [ ] Endpoint `POST /rewards/gift-card/redeem` — atomic UPDATE `gift_card_redeemed_ytd_cents += amount` after provider success
- [ ] Endpoint `POST /rewards/gift-card/redeem` — `warn_bnc_threshold` flag returned if `ytd ≥ 30500` AND `gift_card_warning_acknowledged_year != current_year`
- [ ] Endpoint `PATCH /account/gift-card-warning-ack` — set `gift_card_warning_acknowledged_year = current_year`
- [ ] Endpoint `GET /api/v1/account/gift-card-stats` — returns `{year, ytd_redeemed_cents, annual_warning_threshold_cents, annual_hard_cap_cents, warn_bnc_displayed}`
- [ ] Endpoint `POST /product/suggestion` — slowapi rate-limit `5/user/day`, gate `trust_score >= 75`, CAB emitted only post-validation
- [ ] CAB emission for `barcode_resolve` (resolving unresolved → validated barcode) — multipliers × jack × sub × coverage
- [ ] CAB emission for `batch_i_reconcile` — emitted in batch I (ARCH_consensus.md): 50 CAB × jack × sub × coverage per retro-reconciled scan, fire-and-forget
- [ ] CAB emission for `daily_ring_claim`, `weekly_mission_completed`, `first_time_store_scan` (3 flat actions, no multiplier)
- [ ] CAB emission for `suggestion_validated` (4 tiers: name, +brand+qty, +photo, +unlocks_off) — flat × jack × sub (no coverage)
- [ ] `first_time_store` tracking: query `cabecoin_transactions WHERE reason='first_time_store' AND user_id=X AND reference_id=store_id LIMIT 1` → if empty, emit + create transaction
- [x] Annual cap reset batch: `batch/ratis_batch_annual_reset/` — runs 1× on January 1 00:00 UTC → `UPDATE users SET gift_card_redeemed_ytd_cents=0 WHERE gift_card_redeemed_ytd_cents <> 0`. Month guard built in (non-January = skip). Workflow: `.github/workflows/batch_annual_reset.yml`.
- [ ] React Query hook `use-gift-card-stats.ts` on CL side
- [ ] Profile section "My gift card earnings 2026" in `app/(tabs)/profil.tsx`
- [ ] BNC warning modal 1× per year in mobile redeem flow
- [ ] Admin UI page `/admin/ui/cab-stats` — distribution metrics + action type breakdown + top users + estimated Ratis cost in €
- [ ] TDD tests for each endpoint (cap reached / cap warning / cap reset / suggestion rate-limit / trust-score gate)
- [ ] Update `PROD_CHECKLIST.md`: add "Admin dashboard /admin/ui/cab-stats" as pre-launch blocker + "Public page /transparency" as V1+
- [ ] Update `ENDPOINTS.md`: regenerate post-PR (minimum 3 new endpoints)

> ⚠️ One item at a time. Do not move to the next without finishing the previous one.

---

## Index

- [Context](#context)
- [Economic philosophy](#economic-philosophy)
- [CAB emission formula](#cab-emission-formula)
- [Earns grid](#earns-grid)
- [Multipliers](#multipliers)
- [Daily diminishing returns](#daily-diminishing-returns)
- [Annual cap and fiscal compliance](#annual-cap-and-fiscal-compliance)
  - [H4 reservation model](#h4-reservation-model)
- [Anti-fraud on product suggestions](#anti-fraud-on-product-suggestions)
- [Profile page — My gift card earnings](#profile-page--my-gift-card-earnings)
- [Admin observability dashboard](#admin-observability-dashboard)
- [Communication & transparency (V1+)](#communication--transparency-v1)
- [Long-term vision Y1-Y5](#long-term-vision-y1-y5)
- [Tables](#tables)
- [Internal logic](#internal-logic)
- [Parameters](#parameters)
- [Rules](#rules)
- [Out of scope](#out-of-scope)

---

## Context

Read before starting:
- `CLAUDE.md` — absolute rules (DB, race condition patterns on balances, atomic UPDATE)
- `KNOWN_PROBLEMS_INDEX.md`
- `DECISIONS_ACTED.md`
- `ARCH_feed_jack.md` — existing streak system (Jack multiplier +5%/day, cap +100%, conditioned on `POST /streak/feed`)
- `batch/ratis_batch_consensus/consensus.py` — existing decay system (5-day grace, 10%/day rate, floor 30)

Required dependencies (already exist in prod):
- `user_cab_balance` (table, `balance` INT ≥ 0)
- `cabecoin_transactions` (table, `direction` credit/debit, `reason` enum, `amount`, `reference_id`, `reference_type`)
- `user_streaks` (table, `current_streak_days`, `last_fed_at`, `food_reserves`, `timezone`)
- `price_consensus` (table, `last_seen_at` base for decay)
- `cab_service.award_cab(db, user_id, amount, reason, reference_id?, reference_type?, apply_streak_multiplier=True, community_multiplier=None)` — central emission point, to be extended (see below)
- `consensus.decay_grace_days` (in `ratis_settings.json`) — reused to define "stale" on the coverage bonus side

**V1 guiding principles**:
- **Pre-production**: free calibration. After launch, the "never reduce prices" rule applies strictly.
- **No catalogue V1**: BP (progresses on `cab_earned_season`) is the main gratification loop. Cashback boost (existing) is the active sink. Digital catalogue + gift cards in V2 based on collected data.
- **Closed-loop economy**: CAB do not convert to euros in V1 (no gift card, no withdrawal). Marginal cost to Ratis ≈ 0.

---

## Economic Philosophy

> **Added PR2 (2026-05-02)** — crowdsourced data actions + product suggestion + annual cap.

Paid crowdsourcing is an **honest redistribution of data value** to
contributors. Without them, Ratis has no FR retail price database — and therefore no
product. CAB emitted for ESL scan, barcode resolution, receipt scan, product
suggestion, and retroactive reconciliation batch (Block I, see `ARCH_consensus.md`) are
**not a marketing expense**: they are the real cost of acquiring the data
that forms Ratis's moat.

The annual cap (1199€/year, see [Annual cap and fiscal compliance](#annual-cap-and-fiscal-compliance))
is **NOT a frustration** but a **legal protection**: below this threshold,
the user maximizes their net gain (zero Ratis paperwork, simple BNC declaration between
305€ and 1199€). Beyond that, accumulation for the following year — no waste, and
Ratis benefits from a payment delay allowing treasury investment in
acquiring new contributors.

**Impact vision (storytelling)**:
- Student: 1200€/year = 4 months of free groceries
- Low-income family: 1200€/year = Christmas gifts without anxiety
- Retiree: 100€/month of food purchasing power

**Long-term vision**: crowdsourcing = bootstrap (Y1-Y3). Eventually, Ratis becomes
the authority on FR retail prices (Y4+) and negotiates directly with retailers (see
Booking/Doctolib/Yuka model). CAB from data actions becomes marginal vs affiliate cashback
+ B2B analytics. See [Long-term vision Y1-Y5](#long-term-vision-y1-y5).

**CAB → € conversion (acted PR2)**:
- Canonical ratio: `10000 CAB = 5€` (gift card) → `2000 CAB = 1€` → `200 CAB = 1 cent`.
- Stored in `ratis_settings.json#gift_cards.ratio_cab_to_eur_cents = 200`.
- No monthly cap — `coverage_bonus` self-regulates (see [Multipliers](#multipliers)):
  a user who blitzes 1 store sees their coverage drop to 0.34× quickly, which
  incentivizes varying stores (consistent with Ratis objective "cover as many stores as possible").

---

## CAB Emission Formula

Every action that emits CAB follows the canonical formula:

```
final_cab = base_cab × (1 + jack_bonus + coverage_bonus) × subscription_multiplier
```

Where:
- `base_cab` — value defined in the [earns grid](#earns-grid) (e.g. 30 for a base receipt)
- `jack_bonus ∈ [0, 1]` — Jack streak bonus, `min(current_streak_days × 0.05, 1.0)` (+5%/day, cap +100% at 20d+), applied only if the user fed Jack today
- `coverage_bonus ∈ [-0.66, 1.0]` — store coverage bonus, read from `stores.coverage_bonus` (scan-related actions only)
- `subscription_multiplier ∈ {1, 2}` — ×2 if the user is an active subscriber, ×1 otherwise

The `jack_bonus + coverage_bonus` multipliers are **summed** into a single multiplier before application (no cascade). The `subscription_multiplier` is separate at the end of the chain.

### Theoretical bounds

- Min: `base × (1 + 0 - 0.66) × 1 = base × 0.34` (free user, streak 0, hyper-covered store)
- Max: `base × (1 + 1 + 1) × 2 = base × 6` (subscriber, streak 20d+, never-scanned store)

### Exceptions — actions that BYPASS the formula

Some emissions are **flat, non-multiplied** — documented in the [rules](#rules):
- Signup bonus Y (with code)
- Referral reward X
- Prestige ★ (if ever implemented)
- Partner cashback (already based on real commission, must not be re-multiplied)

For these actions, call `award_cab(..., apply_streak_multiplier=False, coverage_bonus=0, subscription_multiplier=1)`.

---

## Earns Grid

All values are **base CAB** (before multipliers).

### Daily actions (multipliers active)

| Action | Base CAB | Source table | Multipliable |
|---|---|---|---|
| Receipt scan | `30 + 5 × nb_articles` | `scans` (type=receipt) | ✅ jack + coverage + sub |
| Electronic label scan (ESL) | `5` | `scans` (type=electronic_label) | ✅ jack + coverage + sub |
| Product field filled (crowdsourcing) | `10` | `product_knowledge` | ✅ jack + sub (no coverage, non-store-bound) |
| Daily mission completed | `50` | `user_missions` (cadence=daily) | ✅ jack + sub (no coverage) |
| Weekly mission completed | `250` | `user_missions` (cadence=weekly) | ✅ jack + sub (no coverage) |
| 7-day streak milestone | **OUT OF V1** | N/A | — |
| Break a ROI ring | `500` | `user_ring_claims` or equivalent | ✅ jack + sub (no coverage) |
| Battlepass level up | `50-500` (scaling per level) | `user_battlepass_progress` | ✅ jack + sub (no coverage) |

### Exceptional actions (FLAT, non-multiplied)

| Action | Flat CAB | Source | Trigger |
|---|---|---|---|
| Signup Y with referral code | `150` | Hook on `users.created` if `referral_code` non-null | 1× per lifetime per user |
| Referral X — Y subscribes monthly | `500` | Stripe webhook → `POST /rewards/referral/trigger` | Each monthly-subscribed referral |
| Referral X — Y subscribes annual | `750` | Same as monthly | Each annual-subscribed referral |
| Partner cashback | `~50% × commission_€` | Affilae/Awin/CJ webhook → `affiliate_service` | Automatic on affiliate purchase |
| Prestige ★ | `1500` (if implemented) | Hook on prestige gain | Rare |

### Coverage bonus — which actions is coverage_bonus applicable to?

Coverage only makes sense for actions **tied to a store**:
- ✅ Receipt scan → `coverage_bonus = stores[receipt.store_id].coverage_bonus`
- ✅ Electronic label scan → same
- ✅ Barcode resolution (scan unresolved → barcode found) → store of source scan
- ✅ Batch I — retroactive reconciliation (see `ARCH_consensus.md`) → store of the caught-up scan
- ❌ Daily/weekly missions (not store-bound)
- ❌ Streak, ring claim, BP level up (not store-bound)
- ❌ Product field filled / product suggestion (tied to a product, not a store)
- ❌ Daily ring claim, weekly mission, first-time-store bonus (see PR2 grid below — flat)

### PR2 — Crowdsourced data actions and product suggestions (acted 2026-05-02)

> **PR2 extension** adding critical data actions (barcode resolution,
> product suggestions, retroactive Block I) and three flat bonuses (daily ring,
> weekly mission, first-time-store).

| Action                                          | Base CAB | Multipliers              | Notes                                        |
|-------------------------------------------------|----------|--------------------------|----------------------------------------------|
| 1 ESL scanned + matched (pyzbar OR OCR+checksum) |    5     | × jack × sub × coverage  | (Confirms existing ESL line) — crowdsourced data action |
| 1 barcode resolution (scan unresolved)          |    5     | × jack × sub × coverage  | Critical data action — unlocks consensus    |
| 1 complete receipt scan (regardless of item count) |   500    | × jack × sub × coverage  | **PR2 override**: replaces `30 + 5×nb_articles` (V1) → flat 500. Receipt = 10+ items + prices → valuable |
| 1 validated product suggestion — name           |    20    | × jack × sub             | One-shot per product / per user              |
| 1 validated product suggestion — name + brand + qty |  40    | × jack × sub             | Higher granularity                           |
| 1 validated product suggestion — + quality photo |    60    | × jack × sub             | Packaging photo validated by admin/heuristic |
| 1 suggestion unlocks unknown OFF product        |   100    | × jack × sub             | Premium — product non-existent on OFF side, created via contribution |
| Daily ring claim                                |    30    | flat                     | Daily cap — boosts casual without power users |
| Weekly mission completed                        |   200    | flat                     | **PR2 override**: replaces `250` (V1) → `200`. Weekly cap |
| First time bonus (1st scan of a retailer)       |   100    | flat                     | Absolute cap (~36 distinct FR retailers = max 3600 CAB lifetime) |
| Batch I — retroactive resolution                |    50    | × jack × sub × coverage  | See Block I in `ARCH_consensus.md` — emitted when a past unresolved scan becomes resolved |

**V1 ↔ PR2 notes**:
- `Receipt scan 30 + 5×nb_articles` (V1) is **replaced** by flat `500`. V1 incentivized scanning many small receipts (gaming); PR2 rewards a complete receipt (10+ items + prices) which is the real value.
- `Weekly mission 250` (V1) is **adjusted** to `200` for consistency with the PR2 grid (round, readable).
- `Product field filled 10 CAB` (V1) stays as-is for OCR auto-learn crowdsourcing (see `TRAINING.md`). The 4 "validated product suggestion" lines in PR2 concern the **suggestion endpoint** (proposing a complete product, not just a field).
- All PR2 overrides are in pre-prod — the "never reduce prices" rule does not apply until launch.

### V1.x recalibration (acted 2026-05-08)

Following the boutique V1 brainstorm + fiscal cap alignment, the earns grid is recalibrated for a farmer profile of 100-150€/month max (= ~37k CAB/season of natural earn instead of ~80k with the old grid). The old `cab_per_*` values were ~10× too generous vs the target.

| Action | Base CAB before | Base CAB after V1.x | Multipliable |
|---|---|---|---|
| Receipt scan | 50 | **20** | × jack × cov × sub (cap 3/d) |
| ESL scan | 20 or 5 | **3** | × jack × cov × sub (cap 20/d then ×0.5) |
| product_identification (formerly barcode_scan) | 10 | **1** | × jack × cov × sub |
| fill_product_field | 5 | **5** | × jack × sub (cap 10/d then ×0.5) |
| Daily mission completed | 5/15/30 | **5/15/30** (unchanged) | × jack × sub |
| Weekly mission completed | 50/150/300 | **20/50/100** | × jack × sub |
| Daily ring claim | 30 (PR2 indicative) | **N/A V1** (pattern replaced by piggy banks) | flat |
| Break piggy bank (future V1.x) | — | **100 per 8€ savings** | flat |
| Prestige (future V1.x) | — | **300 per 80€ savings** | flat |

**Source-of-truth**:
- `ratis_core/config/ratis_settings.json` § `rewards.cab_per_*` — admin-editable via `/admin/settings/rewards` (DB-first, JSON fallback).
- `missions.cab_reward` — recalibrated by migration `20260508_2300_recalibrate_cab_earns` (idempotent, conditions `AND cab_reward = X`).
- Canonical seed `ratis_core/seed/missions_v1.py` — used by tests + Alembic phase A/B.

Annual cap **unchanged** (1199€/year = DAS2). The boutique CAB→€ ratio stays `boutique.ratio_cab_per_eur=5000` (= 1€ = 5000 CAB) — see [[ARCH_boutique]] § Pricing. With the new grid, the theoretical max farmer drops from ~16€/month (80k CAB ÷ 5000) to ~7.4€/month (37k CAB ÷ 5000), well under the 100€/month fiscal cap (1199€/year ÷ 12).

Safeguard tests: `test_recalibration_grille_earns_v1x` in `webservices/ratis_rewards/tests/test_missions_catalog_v1.py` validates the post-recal grid (settings + seed + weekly/legacy cohesion).

---

## Multipliers

### Jack streak multiplier

**Existing** in `ARCH_feed_jack.md`. No need to recode, just reference:

```python
# In cab_repository.award_cab() — existing line 87
if apply_streak_multiplier:
    jack_bonus = min(user_streaks.current_streak_days * 0.05, 1.0)
else:
    jack_bonus = 0.0
```

Parameters:
- `ratis_settings.json#gamification.feed_jack.multiplier_per_day` = 0.05
- `ratis_settings.json#gamification.feed_jack.max_multiplier` = 1.0

**Activation condition**: the streak only advances if the user calls `POST /streak/feed` during the day (not based on scan timestamps). If the user doesn't feed Jack today, `current_streak_days` does not increment (may even reset to 0 according to `streak_repository` rules).

### Coverage bonus

**New system**. Pre-computed by hourly batch, stored as a cache on `stores`.

#### Formula (computed in the `ratis_batch_coverage` batch)

```python
# For each store, every hour:
stale_threshold_days = settings.consensus.decay_grace_days  # = 5, reused

total_count = COUNT(price_consensus WHERE store_id = X)

if total_count == 0:
    coverage_bonus = +1.0   # store never scanned → maximum bonus to incentivize first scan

else:
    in_decay_count = COUNT(price_consensus
                           WHERE store_id = X
                             AND last_seen_at < NOW() - INTERVAL ':stale_threshold_days days')
    stale_ratio = in_decay_count / total_count
    coverage_bonus = -0.66 + stale_ratio * 1.66
    # stale_ratio 0.0 → -0.66 (all fresh, no need for more scans)
    # stale_ratio 0.4 → 0.0 (neutral)
    # stale_ratio 1.0 → +1.0 (all stale, maximum need)

# Persist
store.coverage_bonus = coverage_bonus
store.coverage_bonus_computed_at = NOW()
```

#### Read at scan time

```python
# In routes that emit CAB for scans:
store = db.get(Store, scan.store_id)
coverage_bonus = store.coverage_bonus if store.coverage_bonus is not None else 1.0

award_cab(
    db, user_id,
    amount=base_cab,
    reason='scan_receipt',
    reference_id=scan.id,
    reference_type='scan',
    coverage_bonus=coverage_bonus,  # new parameter
    subscription_multiplier=2 if user.is_subscriber else 1,
)
```

#### Edge cases

- **Store with no consensus** (new store) → `coverage_bonus = +1.0` (incentivize scanning)
- **Store with only 1 fresh consensus** → `stale_ratio = 0 → -0.66` (data exists, we want other stores)
- **Store abandoned for 6 months** → `stale_ratio ≈ 1.0 → +1.0` (mass refresh needed)
- **Disabled store (`is_disabled=true`)** → batch skips (no compute, `coverage_bonus` stays at last value but not consulted)

### Subscription multiplier

**New system**. Determines whether the user is an active subscriber at emission time.

```python
def get_subscription_multiplier(user) -> float:
    return 2.0 if user.subscription_status == 'active' else 1.0
```

**Applied only to daily actions** (see grid above). For exceptional actions (signup, referral, cashback, prestige), force `subscription_multiplier=1.0` at the call site.

### Secret multiplier — OUT OF V1

**Acted decision**: no V1 implementation. A future admin interface will allow tuning each parameter atomically. Reference `DECISIONS_ACTED.md` (DA to be created).

---

## Daily Diminishing Returns

Instead of a **hard binary cap** (cap reached → 0 CAB for the rest of the day),
a **×0.5 multiplier** is applied after a daily threshold. The user keeps earning
something but at half rate — we maintain the data incentive without frustration.

### Actions with diminishing returns

Thresholds **identical for free and subscriber** (subscription ×2 applies to the
base amount, not the threshold).

| Action | Base CAB | Daily threshold | Multiplier after threshold |
|---|---|---|---|
| Electronic labels (ESL) | 5 CAB / scan | **20 scans/d** | × 0.5 (= 2.5 CAB/scan) |
| Product field filled | 10 CAB / field | **10 fields/d** | × 0.5 (= 5 CAB/field) |

**Concrete ESL example** (subscriber with ×2 global):
- Scans 1 to 20: `5 × 2 = 10 CAB/scan` → total 200 CAB
- Scans 21+: `5 × 2 × 0.5 = 5 CAB/scan`
- No absolute cap — the user can keep going, but at a diminishing rate

**Implementation**: rolling query on `cabecoin_transactions` to count today's
actions (by `reason` + `user_id` + `created_at > today_utc_start`). At
emission time, compare count to threshold → if exceeded, apply ×0.5 to
`amount` before persist.

No new table needed — existing timestamps on
`cabecoin_transactions` are sufficient.

### Actions capped by dedup (no diminishing — hard stop)

| Action | Rule |
|---|---|
| Receipt | Max 3 validated receipts/day (anti-duplicate via `scans.image_hash`) |
| Daily mission | 1 per day by design |
| Weekly mission | 1 per week by design |
| Streak | N/A (structural condition) |

### Non-capped, non-diminishing actions

- Partner cashback (already controlled by the partner)
- Referral (1 per `referred_user_id` via existing unique constraint)
- Signup bonus (1 lifetime)
- Battlepass level up (structural cap by number of levels)

---

## Annual Cap and Fiscal Compliance

> **Added PR2 (2026-05-02)** — 2-tier legal protection + calendar reset.

> **Note 2026-05-08** — the `ratio_cab_to_eur_cents=200` ratio mentioned in PR2 (below § settings + § dashboard) was **indicative and never implemented**. The boutique V1 (see [[ARCH_boutique]] § Pricing) acts on a more restrictive ratio **`boutique.ratio_cab_per_eur=5000`** (= 1€ = 5000 CAB, i.e. 25× more restrictive than the PR2 value). No endpoint reads `ratio_cab_to_eur_cents` today — no breaking change. The CAB earns grid can be recalibrated later via admin UI (see 2026-05-08 brainstorm, but not yet implemented — separate work stream acted as "via future admin UI"). The annual fiscal cap (1199€/year DAS2) remains source-of-truth here; it is just the CAB↔€ ratio that is now 5000 on the boutique side.

> **H4 audit (2026-05-18)** — the 1199€/year cap now applies to all **4 gift card emission flows**: `shop_purchase` (boutique), `annual_subscription`, `battlepass_milestone`, `referral_reward`. Implementation goes through a **reservation model** centralized in `gift_card_cap_service.py` (`reserve_gift_card_cap` / `release_gift_card_cap`). The `gift_card_orders.cap_reserved_cents` column materializes the reservation. Migration: `20260518_1000_gc_cap_resv`. Details: [§ H4 reservation model](#h4-reservation-model).

### Why an annual cap and why at these thresholds

- **DAS2 threshold = 1200€/year/beneficiary**: above this, Ratis would need to file an annual tax declaration (DAS2 form). Below 1200€/year cumulative, **zero paperwork on Ratis's side**. We therefore choose a hard cap at `1199€ = 119900 cents` to stay strictly below the threshold.
- **BNC individual exemption = 305€/year**: a user who redeems ≥ 305€ of gift cards / year must declare these gains as BNC income in their annual tax return. Ratis does **not block** this (user accountability, not paternalism) but **warns** via a UI warning 1× per calendar year.

### Behavior by tier

| Tier | Annual cumulative amount | Behavior |
|---|---|---|
| **Below 305€/year** | `< 30500 cents` | No action, normal redemption, no warning |
| **305€ to 1199€/year** | `30500 ≤ x < 119900 cents` | Redemption allowed + **UI WARNING** (modal 1× per calendar year): « 💡 Au-delà de 305€/an, tu pourrais devoir déclarer ces gains comme revenus BNC. [En savoir plus] ». User clicks OK 1× → `gift_card_warning_acknowledged_year` flag set to `EXTRACT(YEAR FROM NOW())`. No more warning this year |
| **≥ 1200€/year** | `≥ 119900 cents` | **HARD BLOCK** on backend (409 refusal + `detail="annual_gift_card_cap_reached"`). Gratitude UI message: « 🎉 Tu as atteint le maximum annuel chez Ratis (1200€) ! Tes CAB s'accumulent pour l'année prochaine. » No €-rollover — CAB balance remains on user account, redeemable from the following January 1 |

### Calendar reset

Simple cron, annual batch on January 1 00:00 UTC:

```sql
UPDATE users
   SET gift_card_redeemed_ytd_cents = 0,
       gift_card_warning_acknowledged_year = NULL,
       gift_card_year_reset_at = NOW();
```

Hosted in the `ratis_batch_purge` batch or a new `ratis_batch_annual_reset` depending on deployment arbitration (to be decided at dev time).

### DB schema (to migrate)

```sql
ALTER TABLE users
  ADD COLUMN gift_card_redeemed_ytd_cents INT NOT NULL DEFAULT 0,
  ADD COLUMN gift_card_warning_acknowledged_year INT NULL,
  ADD COLUMN gift_card_year_reset_at TIMESTAMPTZ;

-- Optional: index for admin stats
CREATE INDEX IF NOT EXISTS idx_users_ytd_cents
  ON users (gift_card_redeemed_ytd_cents)
  WHERE gift_card_redeemed_ytd_cents > 0;
```

### Enforcement logic

> **H4 audit** — the logic below (PR2, boutique-only) is **replaced** by the centralized reservation model (see [§ H4 reservation model](#h4-reservation-model) below). It remains here for historical reference.

At each `POST /rewards/gift-card/redeem` (initial boutique V1):

1. Read `users.gift_card_redeemed_ytd_cents` + requested gift card amount (in cents).
2. If `ytd + amount > annual_hard_cap_cents` (= 119900) → 409 `annual_gift_card_cap_reached`.
3. Otherwise: execute the redeem (provider call) → after success:
   - Atomic UPDATE: `UPDATE users SET gift_card_redeemed_ytd_cents = gift_card_redeemed_ytd_cents + :amount WHERE id = :u`.
   - If `ytd + amount ≥ annual_warning_threshold_cents` (= 30500) AND `gift_card_warning_acknowledged_year != EXTRACT(YEAR FROM NOW())` → flag to return on API side (`{"warn_bnc_threshold": true}`) → mobile displays the modal once.
   - When user dismisses the modal → `PATCH /account/gift-card-warning-ack` → set `gift_card_warning_acknowledged_year = EXTRACT(YEAR FROM NOW())`.

### H4 Reservation Model

> **H4 audit (2026-05-18)** — replacement of the increment-on-success model with a reserve-on-issuance model, extended to all flows.

**Problem with the PR2 model**: the cap was only enforced for the boutique (`shop_purchase`). The other 3 flows (`annual_subscription`, `battlepass_milestone`, `referral_reward`) did not decrement `ytd_cents` — a user could reach 1199€ outside the boutique without triggering DAS2.

**Solution**: `gift_card_cap_service.py` — centralized service (advisory lock `gift_card_cap:{user_id}`) with two primitives:

- `reserve_gift_card_cap(db, order_id, *, allow_defer) -> CapDecision` — called **before** the Runa call in `issue_gift_card`, for all flows. Receives the order UUID (denorms `user_id`/`denomination` via SELECT). Increments `users.gift_card_redeemed_ytd_cents` and sets `gift_card_orders.cap_reserved_cents`. Result: a `CapDecision` dataclass with `outcome` (string):
  - `outcome="allow"` — reserved; caller proceeds to issuance.
  - `outcome="defer"` — over-cap, earned reward (`allow_defer=True`): caller sets `eligible_at = CapDecision.deferred_until` (next January 1) and leaves order as `pending`.
  - `outcome="block"` — over-cap, boutique (`allow_defer=False`): caller fails the order, refunds CAB, 409 `annual_gift_card_cap_reached`.
- `release_gift_card_cap(db, order_id)` — called in `_mark_failed` and by the `reconcile_deferred_gift_card_orders` batch (C3) on failed orders. Decrements `ytd_cents` by the order's `cap_reserved_cents` amount. Idempotent (no-op if not reserved).

**Migration**: `20260518_1000_gc_cap_resv` adds `gift_card_orders.cap_reserved_cents INT NOT NULL DEFAULT 0`.

**Behavior per flow**:

| Flow | `allow_defer` | Over-cap → |
|---|---|---|
| Boutique | `False` | `outcome="block"` (409 + CAB refund) |
| Annual subscription | `True` | `outcome="defer"` (`eligible_at` = Jan 1) |
| Battlepass milestone | `True` | `outcome="defer"` |
| Referral reward | `True` | `outcome="defer"` |

**Annual reset**: the `ratis_batch_annual_reset` batch (cron `0 0 1 1 *` — January 1 00:00 UTC) resets `users.gift_card_redeemed_ytd_cents = 0` so the cap is annual and not cumulative. The month guard (`month != 1 → skip`) makes an accidental `workflow_dispatch` harmless.

**Deferred order re-issuance**: `reconcile_deferred_gift_card_orders` batch (integrated in `ratis_batch_reconciliation` job C3) — runs periodically, selects `pending` orders with `eligible_at <= NOW()` and re-issues them via Runa. KP-78 Pattern A applied: `cap_reserved_cents` is declared in the ORM model at the same time as the migration (same commit).

### Points of attention

- **Cumulative anti-fraud**: `gift_card_redeemed_ytd_cents` is cumulative **per user**, not per bank account / IP / device. A user creating multiple accounts to circumvent = separate anti-fraud problem (out of scope of fiscal cap — see trust_score V1).
- **Idempotence**: the existing `gift_card_orders` UNIQUE(source_type, source_ref_id) guarantees a retry does not double-count `ytd_cents`. If the provider fails after the UPDATE, transaction rollback.
- **Audit**: each `gift_card_orders` has `amount_cents` + `cap_reserved_cents` + `created_at` → YTD cumulative can be reconstructed at any time (sanity check vs denormalized column).

---

## Anti-fraud on Product Suggestions

> **Added PR2 (2026-05-02)** — rate-limit + trust_score gating.

Product suggestions (the "1 validated product suggestion — *" lines of the PR2 grid)
emit up to **100 CAB/validated suggestion**. Without rate-limiting, a user could
spam low-quality suggestions hoping a fraction passes admin
validation.

### Safeguards

1. **Suggestion endpoint rate-limit** — slowapi: `5 suggestions / user / day` on `POST /product/suggestion`. 6th refusal → 429 `suggestion_rate_limited`.
2. **trust_score gate** — if `users.trust_score < 75` (warning band, see trust_score V1 anti-fraud), endpoint refuses with 403 `suggestion_locked_low_trust`. The user can no longer submit suggestions until their trust_score returns to ≥ 75.
3. **CAB emitted only after validation** — a submitted suggestion emits **nothing** at POST time. CAB emission only when an admin (or auto-heuristic) marks the suggestion `validated=true`. → no gain from spamming if everything is rejected.
4. **trust_score bonus already in place**: a user with trust_score < 65 (shadow ban) already contributes neither to consensus nor to suggestions. Consistent with the existing tier ban.

### Settings

```json
"anti_fraud": {
  "suggestion_rate_limit_per_day": 5,
  "suggestion_min_trust_score": 75
}
```

(Added to `ratis_settings.json` — see [Parameters](#parameters).)

---

## Profile Page — My Gift Card Earnings

> **Added PR2 (2026-05-02)** — user transparency UX + accountability.

Section visible in `app/(tabs)/profil.tsx` (active V1, under the "My info" block
or equivalent — final placement to be decided in design review).

### Text mockup

```
[Section] My gift card earnings 2026
  Redeemed gift cards: 250€ / 1200€ annual max
  ░░░░░░░░░░ 21%

  ⚠️ Above 305€/year, you may need to declare these gains as BNC income
     in your annual tax return. [See how]
```

### Components

- **Progress bar**: `gift_card_redeemed_ytd_cents / annual_hard_cap_cents` → percentage.
- **BNC warning**: displayed **always** in this section (passive, not a blocking modal) as text + link `[See how]` that opens a static help page (`app/help/bnc-declaration.tsx` to create V1+).
- **Current year in plain text** in the title ("2026", "2027", ...) — auto-computed client-side from `Date()`.
- **Reset state**: just after January 1, the section displays `0€ / 1200€` cleanly (not "loading" — the reset to 0 is done backend-side by the annual batch).

### API Endpoint

`GET /api/v1/account/gift-card-stats` → returns:

```json
{
  "year": 2026,
  "ytd_redeemed_cents": 25000,
  "annual_warning_threshold_cents": 30500,
  "annual_hard_cap_cents": 119900,
  "warn_bnc_displayed": true
}
```

`warn_bnc_displayed = true` as soon as `ytd ≥ warning_threshold`; used to condition
the display of the BNC alert block in the profile. No modal here, just visible text.

React Query hook: `use-gift-card-stats.ts` (to be created on CL side).

### Why no monthly cap displayed

PR2 decision: the monthly cap does not exist (the `coverage_bonus` self-regulates). We
do not display a "recommended pace / month" to avoid creating an
implicit prescription. The user manages their own pace freely; we only inform them of the annual cumulative and
legal thresholds.

---

## Admin Observability Dashboard

> **Added PR2 (2026-05-02)** — pre-launch V1 blocker.

CAB economy observability is **non-negotiable** for steering post-launch
calibration (where the "never reduce prices" rule applies strictly).
Without a dashboard, drifts are discovered by opening the SQL console — too late.

### Admin page `/admin/ui/cab-stats`

Extension of the existing admin UI (`webservices/ratis_rewards/admin_ui/`).
Auth via `ADMIN_API_KEY` like the rest of `/admin/*`.

#### Real-time metrics

- **Total CAB distributed** (since beginning) — aggregated query on `cabecoin_transactions WHERE direction='credit'`.
- **CAB distributed this week / this month** — windowed.
- **CAB by action type** (by `reason`) — breakdown: scan_receipt, scan_label, barcode_resolve, suggestion_validated, mission_daily, mission_weekly, ring_claim, first_time_store, batch_i_reconcile, referral_*, signup_bonus, etc. → stacked bar chart per day over the last 30 days.
- **CAB by profile**: free vs subscriber, active (≥ 1 scan/30d) vs power user (≥ 1 scan/day) — distribution.
- **Top users by CAB earned** — anonymized by default (truncated UUID), unmask possible if admin clicks (audit log).

#### Economic metrics

- **Estimated Ratis cost in €**: `total_cab_distributed × ratio_cab_to_eur_cents / 100` → how much the distribution would theoretically cost if everything were redeemed.
- **vs planned marketing budget**: a `economics.monthly_cab_budget_eur` setting (to be added) → ratio turns red if exceeded.
- **Gift cards redeemed total / this year** — query on `gift_card_orders WHERE status='completed'`.
- **YTD redeemed distribution**: how many users at `< 305€`, `305-1199€`, `≥ 1199€ (cap reached)`.
- **CAB pending redemption** (circulating balance): `SUM(user_cab_balance.balance)`.

#### Alerting (Sentry / cron)

- Alert if **daily distribution > threshold** (configurable, e.g. > 100k CAB/day suddenly) → suspected gaming or grid bug.
- Alert if **national average coverage_bonus** deviates > ±20% in 24h (health signal for the coverage batch).

#### Pre-launch flag

This page is marked **pre-launch V1 blocker** in `PROD_CHECKLIST.md` (to be added
when the dev PR is opened). Without it, we do not launch — observability is
non-negotiable for steering an economy in production.

### Data sources

All metrics are computed from existing tables:
- `cabecoin_transactions` (source-of-truth)
- `user_cab_balance` (materialized)
- `gift_card_orders` (legal, never purged)
- `users` (joins for subscription/profile)
- `scans` + `cabecoin_transactions.reference_id` (debug joins)

No new table needed for V1. If queries become slow (>2s) →
materialize in `cab_stats_daily` (view or snapshot table via nightly batch) in V1+.

---

## Communication & Transparency (V1+)

> **Added PR2 (2026-05-02)** — public transparency page, V1+ scope (post-launch).

### Public page `ratis.app/transparency`

To be created V1+ (post-launch, once 3-6 months of interpretable data are available).

Inspiration: Lemonade Insurance (`lemonade.com/transparency`), a model that
**marked the insurance market** with its radical transparency. Absolute differentiation
in the FR cashback market (all opaque on redistributed volumes,
delays, withdrawal acceptance rates).

#### Planned content

- **Total CAB redistributed** (since beginning) — live counter, ascending number animation.
- **Active users today / this week / this month**.
- **Top contributors** anonymized (`Marie L., Pierre D., ...`) with cumulative earned amount. User opt-in to appear (account setting).
- **Distribution by retailer**: "X% of Carrefour scans, Y% Leclerc, ..." — shows coverage.
- **Stories**: 3-5 short anonymized testimonials ("Marie paid 8 months of organic groceries in 2027 thanks to Ratis").
- **Radical honesty**: also the uncomfortable figures — % rejected scans, average barcode validation delay, pending gift cards, etc.

#### Why V1+ and not V1

- Before launch, not enough data for a credible page (worse than absent).
- Snowball effect expected Y2-Y3 when we can display "1M€ redistributed" — that's when the page becomes a powerful marketing asset.

#### Backlog note

→ Add to `PROD_CHECKLIST.md` § V1+ Communication. No code in the PR2 dev.

### Press storytelling

When the page exists, possibility of press pitches (Que Choisir, 60M Consommateurs,
INSEE, France 3 régionale) on the angle "honest redistribution of data value
to the consumer" — absolute differentiation vs FR cashback competitors. See
[Long-term vision Y1-Y5](#long-term-vision-y1-y5).

---

## Long-term Vision Y1-Y5

> **Added PR2 (2026-05-02)** — long-term economic vision of the CAB system.

Paid crowdsourcing (PR2) is a **bootstrap**: it founds Ratis's FR retail
price DB. As the DB densifies, the marginal utility of a new
scan decreases — this is exactly what `coverage_bonus` models (-0.66× on an
over-covered store). Long-term, CAB from data actions becomes residual and
the economy shifts to affiliate cashback + B2B analytics.

### Phase Y1 — alpha-beta (2026)

- **10-100 power users at 1200€/year** = 12k€-120k€ "marketing budget" paid in data value.
- **DB bootstrapped** by paid crowdsourcing (target: 50k consensus in Y1).
- **Storytelling**: "Marie paid her May rent, Pierre funded his Christmas gifts" — first press material.
- **Economic calibration**: observe users' YTD distributions, adjust the grid (pre-prod only, never reduce post-launch).

### Phase Y2-Y3 — growth (2027-2028)

- **1000+ active users**, dense DB, harder for a user to reach 1200€/year (the `coverage_bonus` drops as stores get covered).
- **Media partnerships**: Que Choisir, 60M Consommateurs, INSEE — based on the transparency page (V1+) which finally provides credible figures.
- **Public stats**: "Ratis = 5M€ redistributed/year to French households" → powerful social angle for organic acquisition.
- **Affiliate cashback** progressively becomes the majority share of emitted CAB (consistent with V1 grid — `cashback_commission_rate=0.5` → 50% of affiliate commission returned as CAB).

### Phase Y4+ — saturation (2029+)

- **Ratis = FR retail price authority** (equivalent of Yuka for prices). DB better covered than retailers' own (network effect).
- **Power shift reversal**: "Dear Carrefour, give us your official prices or you won't be listed / you'll appear in grey". Booking (hotels) / Doctolib (practitioners) / Yuka (manufacturers) model.
- **Direct retailer API**: official real-time price integration via partnership — crowdsourcing becomes residual (not obsolete, just minority vs official feed).
- **Revenue**: B2B analytics (aggregated data sales to manufacturers / panels) + affiliate cashback + premium subscriptions. CAB from data actions becomes a marginal cost.

### Known risks of the trajectory

- **If the Y1 bootstrap fails** (not enough power users at 1200€) → DB too thin, ridiculous transparency page in Y2, no leverage in Y3. Mitigation: admin dashboard monitoring post-launch + recalibration capability in pre-prod.
- **If the State tightens fiscal rules** (e.g. DAS2 threshold lowered) → review 305€/1199€ thresholds with a tax advisor. The code is parameterized (`ratis_settings.json`) so adjustment is a trivial migration.
- **If a competitor copies**: the moat is not the CAB grid, it's the DB. Y3+ crowdsourcing becomes a hard-to-catch-up-with moat (network effect).

---

## Tables

### `stores` — modified

Add 2 columns:

```sql
ALTER TABLE stores
  ADD COLUMN coverage_bonus NUMERIC(4,2) NOT NULL DEFAULT 1.0,
  ADD COLUMN coverage_bonus_computed_at TIMESTAMPTZ;

-- Seed value: 1.0 (max bonus until the batch has run)
-- After hourly batch, values range from -0.66 to +1.0
```

The `NUMERIC(4,2)` constraint bounds possible values. A CHECK `coverage_bonus BETWEEN -0.66 AND 1.0` could be added but is redundant (the batch guarantees bounds).

### `cabecoin_transactions` — already existing

No schema change, but **recommendation** for future traceability:
- Add JSON columns `multipliers_applied`: `{"jack": 0.5, "coverage": 0.3, "subscription": 2.0, "base": 30, "final": 180}`.
- Out of strict V1 scope (can be added later without breaking change), but useful for prod debugging.

### `user_cab_balance` — already existing

No change. Stays on atomic UPDATE pattern `WHERE balance >= X` for debits (see CLAUDE.md race condition on balances).

### `users` — modified (PR2)

Add 3 columns for the annual fiscal cap (see [Annual cap and fiscal compliance](#annual-cap-and-fiscal-compliance)):

```sql
ALTER TABLE users
  ADD COLUMN gift_card_redeemed_ytd_cents INT NOT NULL DEFAULT 0,
  ADD COLUMN gift_card_warning_acknowledged_year INT NULL,
  ADD COLUMN gift_card_year_reset_at TIMESTAMPTZ;
```

Reset on January 1 00:00 UTC via annual cron (dedicated batch).

---

## Internal Logic

### `cab_service.award_cab` — to be extended

V1 signature (existing):

```python
def award_cab(
    db: Session,
    user_id: UUID,
    amount: int,
    reason: str,
    reference_id: UUID | None = None,
    reference_type: str | None = None,
    apply_streak_multiplier: bool = True,
    community_multiplier: Decimal | None = None,
) -> int:  # returns final_amount credited
```

Extended V1 signature:

```python
def award_cab(
    db: Session,
    user_id: UUID,
    amount: int,
    reason: str,
    reference_id: UUID | None = None,
    reference_type: str | None = None,
    apply_streak_multiplier: bool = True,
    coverage_bonus: float = 0.0,                    # new
    subscription_multiplier: float = 1.0,            # new
    community_multiplier: Decimal | None = None,
) -> int:  # returns final_amount credited
    """
    Emits CAB following the formula:
        final = base × (1 + jack + coverage + community) × subscription

    jack: retrieved via user_streaks if apply_streak_multiplier=True, 0 otherwise
    coverage: passed as parameter (default 0 — neutral)
    subscription: passed as parameter (default 1 — free user)
    community: optional, added to the bonus sum

    Persist:
      - INSERT cabecoin_transactions (direction='credit', amount=final, reason, ref_id, ref_type)
      - UPDATE user_cab_balance SET balance = balance + final WHERE user_id = X
      - UPDATE user_battlepass_progress SET cab_earned_season = cab_earned_season + final
        (existing — BP progresses on CAB earned, not on base)
    """
```

### `batch_coverage.run()` — new batch

```
1. For each store (WHERE NOT is_disabled):
    a. Count total_consensus and in_decay_count (SQL aggregate)
    b. Compute coverage_bonus (formula above)
    c. UPDATE stores SET coverage_bonus = X, coverage_bonus_computed_at = NOW()
2. Log stats (N stores, min/max/avg coverage_bonus, duration)
```

**Parallelization**: chunks of 1000 stores, 4 ThreadPoolExecutor workers (inspired by `batch_consensus`). In practice for V1, even single-thread is sufficient (N_stores ≈ 200k max, 5 min per run).

---

## Inter-services

| Direction | Service | Function | Trigger |
|---|---|---|---|
| → outgoing | `ratis_rewards` | `award_cab()` called from | Scan accepted, mission completed, streak, ring claimed, BP level up, referral trigger |
| ← incoming | `ratis_rewards` | `trigger_scan_accepted()` existing | From `ratis_product_analyser` after scan accepted |
| internal batch | `ratis_batch_coverage` | Hourly coverage compute | Hourly cron |

---

## Parameters

Add/confirm in `ratis_settings.json`:

```json
{
  "cab": {
    "earn": {
      "receipt_complete": 500,
      "electronic_label": 5,
      "barcode_resolve": 5,
      "batch_i_reconcile": 50,
      "product_field_filled": 10,
      "suggestion_name_only": 20,
      "suggestion_name_brand_qty": 40,
      "suggestion_with_photo": 60,
      "suggestion_unlocks_off_unknown": 100,
      "daily_ring_claim": 30,
      "mission_weekly": 200,
      "first_time_store": 100,
      "ring_claim": 500,
      "battlepass_level_up_min": 50,
      "battlepass_level_up_max": 500,
      "prestige_star": 1500,
      "referral_signup_bonus": 150,
      "referral_monthly": 500,
      "referral_annual": 750,
      "cashback_commission_rate": 0.5
    },
    "diminishing_returns": {
      "electronic_label": {
        "threshold_per_day": 20,
        "multiplier_after": 0.5
      },
      "product_field_filled": {
        "threshold_per_day": 10,
        "multiplier_after": 0.5
      }
    },
    "hard_caps": {
      "receipt_per_day": 3
    },
    "multipliers": {
      "subscription": 2.0,
      "coverage_floor": -0.66,
      "coverage_ceil": 1.0
    }
  },
  "gift_cards": {
    "annual_warning_threshold_cents": 30500,
    "annual_hard_cap_cents": 119900,
    "ratio_cab_to_eur_cents": 200
  },
  "anti_fraud": {
    "suggestion_rate_limit_per_day": 5,
    "suggestion_min_trust_score": 75
  },
  "consensus": {
    "decay_grace_days": 5  // EXISTING, reused by batch_coverage
  }
}
```

**EXISTING parameters to keep / rename as needed**:
- `rewards.cab_per_receipt_scan` → to **remove** (replaced by `cab.earn.receipt_complete = 500` flat PR2, no more per-item scaling)
- `rewards.cab_referral_monthly`, `rewards.cab_referral_annual` → migrate to `cab.earn.referral_*`
- `gamification.feed_jack.*` → unchanged
- `gift_cards.annual_subscription_denomination` (existing) → unchanged, this is the unit amount of a subscription gift card, unrelated to the annual cap

**NEW PR2 parameters**:
- Block `gift_cards.{annual_warning_threshold_cents, annual_hard_cap_cents, ratio_cab_to_eur_cents}` — fiscal cap and CAB→€ ratio.
- Block `anti_fraud.{suggestion_rate_limit_per_day, suggestion_min_trust_score}` — suggestion endpoint gating.
- Block `cab.earn.*` extended with PR2 data actions (`receipt_complete`, `barcode_resolve`, `batch_i_reconcile`, `suggestion_*`, `daily_ring_claim`, `first_time_store`, `mission_weekly` recalibrated 250→200).

---

## Rules

### Absolute

- **Race condition on balances** — always atomic UPDATE with `WHERE balance >= X` for debits (see `CLAUDE.md`, KP-xx if applicable).
- **`db.commit()` mandatory** in any route that mutates `user_cab_balance` or `cabecoin_transactions`.
- **Never reduce an earn value in production** — "never reduce prices" rule. In pre-prod (before launch), free calibration.
- **Flat multipliers on exceptional bonuses**: signup/referral/prestige/cashback-commission pass `apply_streak_multiplier=False`, `coverage_bonus=0`, `subscription_multiplier=1`. No exceptions.

### Behaviors

- Coverage bonus read at emission time, never recomputed live (always from `stores.coverage_bonus` cache).
- Jack bonus read at emission time via `user_streaks.current_streak_days` (not cached, fast query).
- Subscription check: lookup `users.subscription_status == 'active'` (to be made reliable — see inter-service with `ratis_auth` or Stripe).

### Audit trail

- Each `cabecoin_transactions` has `reason` + `reference_id` + `reference_type` — allows tracing why a user earned CAB.
- For prod debugging, an admin endpoint could expose the breakdown (base, jack, coverage, subscription, final) but **out of scope for V1**.

---

## Out of Scope

### V2 (awaiting data)
- **Digital catalogue** (skins, XP boosts, temporary multipliers, free subscription month, etc.)
- **Global secret multiplier** with temporal ramp-up — replaced by atomic per-parameter admin tuning interface (V2+)
- **Streak milestone 7d = 150 CAB** — the Jack multiplier is sufficient in V1. To reconsider if data shows drop-off before day 7.

### V1+ (post-launch, PR2 ARCH scope)
- **Public page `ratis.app/transparency`** (see [Communication & transparency (V1+)](#communication--transparency-v1)) — created post-3-6 months of interpretable data.
- **CAB roll-over**: currently no €-rollover, just CAB accumulation. If Y2 data shows user frustration from hitting the cap → reconsider.
- **V1 note changed by PR2**: gift cards (5€/10€/20€/50€) **are V1** (post Runa KYB) and no longer V2 — the Ratis identity (see `CLAUDE.md`) includes "gift-cards (V1 post Runa KYB)". The PR2 fiscal cap (305€/1199€) is its legal counterpart.

### Strictly out of V1 (not necessarily V2)
- Debug decomposition on `cabecoin_transactions` (JSON column `multipliers_applied`)
- Denormalized pre-computation of `stale_consensus_count` on `stores` (if the live COUNT becomes a bottleneck)
- Variation of `decay_grace_days` by product type (fast-moving vs slow-moving)
- Prestige tier > ★10 with non-linear scaling bonus
