---
type: cross-cutting
parent: ARCH_RATIS
related: [ARCH_REWARDS, ARCH_AUTH, ARCH_gift_cards, ARCH_BATCH_REFERRAL_PAYOUT, ARCH_CLIENT]
status: production
tags: [referral, parrainage, rewards, gift-cards, anti-churn]
updated: 2026-04-24
---

# ratis_rewards — ARCH Referral V1

> Referral programme V1: referrer shares their code, referred user signs up + receives a signup bonus, referrer payout via gift card upon subscription to a paid plan after 30-day anti-churn delay (`eligible_at`). Backend + frontend + batch payout shipped.
> @tags: referral parrainage rewards gift-cards anti-churn eligible_at filleul parrain code souscription payout batch v1
> @status: LIVRÉ V0
> @subs: auto

> Parent : [[ARCH_RATIS]] · Relations : [[ARCH_REWARDS]], [[ARCH_AUTH]], [[ARCH_gift_cards]], [[ARCH_BATCH_REFERRAL_PAYOUT]], [[ARCH_CLIENT]]

> Status: ✅ Implemented — backend (ratis_rewards) + frontend (ratis_client) + batch payout
> Branch: `main`

User-side referral programme: X (referrer) retrieves their code,
shares it. Y (referred user) signs up with the code and receives a bonus. If Y
subscribes to a paid plan, X is rewarded.

The backend plumbing already exists partially (DB models, internal webhook
route, `handle_referral_reward`). V1 adds **the 2 missing user-facing
endpoints** + **the frontend screen**.

---

## Implementation Checklist

**Base checklist:**
- [x] Alembic migration — add `eligible_at TIMESTAMPTZ NULL` on `gift_card_orders` (for the referral anti-churn delay)
- [x] SQLAlchemy models — `GiftCardOrder.eligible_at`
- [x] Repository — extend `referral_repository` with `get_history_for_user(user_id)`, `get_or_create_code_for_user(user_id)`, `link_user_manually(referred_user_id, code)` (for admin)
- [x] Service — new `referral_service` with `get_or_create_code(user_id)` + `get_user_history(user_id)` + `link_manually_and_reward(referred_user_id, code)`
- [x] User-facing routes: `GET /rewards/referral/code`, `GET /rewards/referral/history`
- [x] Admin route: `POST /admin/referral/link` (auth `ADMIN_API_KEY`)
- [x] Tests written (TDD — before the code)
- [x] `conftest.py` — no new `require_env()`
- [x] `ratis_settings.json` — `cab.earn.referral_signup_bonus: 150` + `referral.gift_card_amount_cents: 500` + `referral.eligibility_delay_days: 30`
- [x] `pg_dump > db/schema.sql` after migration
- [x] `ruff check --fix` clean
- [x] CI pipeline green

**Custom checklist:**
- [x] Add signup bonus for Y (150 CAB) via hook on `POST /auth/register` if `referral_code` is present and valid
- [x] Extend existing `POST /rewards/referral/trigger`: (a) award CAB to X (the referrer) and not to Y, (b) force `apply_streak_multiplier=False` + `subscription_multiplier=1`, (c) also insert a `gift_card_orders` row with `source_type='referral_reward'` + `eligible_at = NOW() + 30 days`
- [x] New batch `batch/ratis_batch_referral_payout/` — daily pickup of `gift_card_orders` with `source_type='referral_reward'` + `status='pending'` + `eligible_at <= NOW()` + verify Y subscription status before triggering `issue_gift_card()`
- [x] GH Actions workflow `batch_referral_payout.yml` (daily cron)
- [x] Admin endpoint `POST /admin/referral/link` documented in `PROD_CHECKLIST.md` with support procedure
- [x] Frontend screen `app/referral.tsx` (Expo Router, outside tabs)
- [x] Hook `useReferralCode` + `useReferralHistory` (React Query)
- [x] Wiring in `profil.tsx` — replace `onPress={() => {}}` on the "Parrainage" menu item with `router.push('/referral')`
- [x] Share button uses native React Native `Share.share()` (iOS + Android)
- [x] i18n FR — keys `profil.referral.*` (show pending gift card "arriving in N days")
- [x] Screen tests `/referral` (TDD)
- [x] Full flow test: Y signs up with code → +150 CAB Y immediate + Y subscribes Stripe → webhook trigger → +500/750 CAB X immediate + `gift_card_orders` pending eligible_at=+30d → batch after 30d with Y still subscribed → 5€ gift card issued

> ⚠️ One item at a time. Do not move to the next without finishing the current one.

---

## Index

- [Context](#context) [L.50 - L.90]
- [Actioned decisions](#acted-decisions) [L.92 - L.150]
- [User journey](#user-journey) [L.152 - L.200]
- [Tables](#tables) [L.202 - L.225]
- [Endpoints](#endpoints) [L.227 - L.295]
- [Internal logic](#internal-logic) [L.297 - L.360]
- [Frontend](#frontend) [L.362 - L.410]
- [Inter-services](#inter-services) [L.412 - L.430]
- [Parameters](#parameters) [L.432 - L.445]
- [Rules](#rules) [L.447 - L.465]
- [Out of scope](#out-of-scope) [L.467 - L.490]

---

## Context

Read before starting:
- `CLAUDE.md`
- `ARCH_cab_economy.md` — CAB grid + flat rules on exceptional bonuses
- `DECISIONS_ACTED.md` — decisions on referral (to be added)

Required dependencies (already exist):
- Table `referral_codes` (`id`, `user_id` unique, `code` unique uppercase, `type` user/influencer)
- Table `referral_uses` (`id`, `referral_id`, `referred_user_id` unique, `plan` monthly/annual, `rewarded_at`)
- Repository `referral_repository` (`get_by_code`, `create_for_user`, `create_use`)
- `UserCreate.referral_code` already accepts a code at signup
- Internal route `POST /rewards/referral/trigger` (webhook-only via INTERNAL_API_KEY) called by Stripe when Y subscribes
- `handle_referral_reward()` + `handle_referral_xp()` in `ratis_rewards` — trigger CAB/XP award

**What is missing (V1 scope)**:
- No public route for X to retrieve their own code
- No public route for X to view their referral history
- No signup bonus for Y when registering with a code
- No frontend screen

---

## Acted decisions

These decisions were made during a brainstorming session (2026-04-22) and are locked for V1. Reviewers must refer to this document (following sections) to understand why certain features are NOT implemented.

### No public `POST /claim` — replaced by admin support procedure

**Permanently rejected on the public API side**. Economic reasons:

1. **Acquisition inconsistency** — A user Y who signed up without a code has shown they did not need X's incentive to register. Retroactively rewarding X (automatically) = paying for an acquisition that happened anyway.

2. **Farming attack surface** — "sign up, then enter my code, we split the rewards" becomes trivial if public. Incompatible with the product red line `"Never sell CAB"`.

3. **Pollutes acquisition metrics** — an "existing user who claims a code" is not a genuine acquisition.

#### Replacement flow: admin datafix via customer support

Instead of a public `POST /claim` endpoint, we implement an **internal procedure** accessible only to customer support. Covered use case: the user forgot to enter their code at signup and contacts support.

**Process**:
1. User Y contacts support → provides their email (or user_id) + the referrer's code
2. Support verifies the user's identity (via helpdesk, email, etc.) — human in the loop
3. Support uses an internal endpoint: `POST /admin/referral/link`
   - Auth: `ADMIN_API_KEY` (not user JWT)
   - Body: `{ referred_user_id, code }`
   - Effect: identical to a signup with code
     - Creates `referral_uses` (link Y → X)
     - Awards signup bonus to Y (+150 CAB) if not already done
     - Marks Y as eligible for X's reward on next subscribe
   - Idempotent: if already linked, return 200 without re-awarding
4. If Y is already subscribed → immediately trigger X's reward (CAB + enqueue gift card with anti-churn)

**Branding benefit**: *"generous customer service"* — the user who genuinely forgot gets their referral reinstated, but the vector remains human (not farmable).

**Audit**: every use of the admin endpoint must be logged with `admin_operator_id` for traceability.

### Gift cards for referral X — REINTRODUCED with 30-day anti-churn

**Scope change from the first ARCH iterations**: we introduce gift cards specifically for X's referral reward.

**Reward X per subscribed referred user**:

| Case | Immediate CAB | Gift card (after 30d) |
|---|---|---|
| Y subscribes **monthly** | **500 CAB** | **5€** |
| Y subscribes **annual** | **750 CAB** | **5€** |

**Gift card amount = 5€ flat**, regardless of Y's plan. Consistent with the rationale:
- An annual referral is already very profitable for Ratis (79€ revenue - 20€ subscription incentive Y = 59€ net — easily covers 5€ reward X)
- A monthly referral is also profitable after ~2 months of retention (7.99€ × 2 = 15.98€ - 5€ reward = +10.98€)

**30-day anti-churn**: delay before sending the gift card, to protect against fake referred users who subscribe then cancel.
- Immediate CAB → X sees their balance go up as soon as subscription happens (dopamine effect)
- Deferred gift card → X only receives their card if Y is still subscribed on day 30

#### Gift card infrastructure — uses existing system

Reference: `webservices/ratis_rewards/ARCH_gift_cards.md` already covers:
- Provider: **Runa** (Amazon.fr available after KYB)
- Table `gift_card_orders` with idempotent pattern (`source_type` + `source_ref_id`)
- Flow: INSERT pending → BackgroundTasks → `issue_gift_card(order_id)` → Runa API → UPDATE status

**Extension for referral**:
- New `source_type = 'referral_reward'`
- `source_ref_id = referral_use.id` (idempotency key)
- Add column `eligible_at TIMESTAMPTZ` on `gift_card_orders` (NULL = immediately eligible, value = batch will wait until that date)
- Or a dedicated `referral_gift_card_queue` table (cleaner but +1 table)

**To decide when coding**: extend `gift_card_orders` vs dedicated table. Recommendation: extension for consistency with the rest (`gift_card_orders` already centralises battlepass + annual subscription).

#### Anti-churn batch — new or existing?

New batch `ratis_batch_referral_payout/` (hourly or daily):
1. For each `gift_card_orders` WHERE `source_type='referral_reward'` AND `status='pending'` AND `eligible_at <= NOW()`:
   a. Verify that Y (`referral_use.referred_user_id`) is **still subscribed** — query on `users.subscription_status` or equivalent
   b. If yes: UPDATE `status='eligible'` → trigger `issue_gift_card(order_id)` (existing pattern from ARCH_gift_cards)
   c. If Y has churned: UPDATE `status='churned'`, gift card NEVER issued (X's reward cancelled)
2. Log stats (N orders processed, N delivered, N churned)

**Frequency**: daily is sufficient (the 30-day delay has ±1 day precision — nobody will count the hours).

**On CAB**: immediate, not affected by anti-churn. If Y churns, CAB stays with the referrer (marginal cost ≈ 0 for Ratis, not worth clawing back).

### Reward Y — signup bonus only (unchanged)

- Y receives **+150 CAB at signup** only if a valid code is passed in `UserCreate.referral_code`
- No additional bonus from the referral system if Y subscribes

### Yearly subscription gift card Y — out of scope (separate mechanism)

The user specified: *"Y 20€ if they take the yearly (but unrelated to referral, it's pure incentive)"*.

The 20€ yearly bonus to Y is a **pure incentive mechanic**, independent of referral. Covered by **Flow A** in `ARCH_gift_cards.md` (triggered via Stripe webhook `checkout.session.completed` on an annual subscription).

**Out of scope for this ARCH** — we do not duplicate the logic here. If Y took the annual plan AND via a referral code, both mechanics apply independently (Y receives 20€ as an annual subscriber + 150 CAB as a referred user; X receives 750 CAB + 5€).

### CAB values for X unchanged

- Monthly: **500 CAB** (existing value `rewards.cab_referral_monthly`)
- Annual: **750 CAB** (existing value `rewards.cab_referral_annual`)
- Flat, not multiplied (cf ARCH_cab_economy — exceptional bonus rules)

These amounts may be revised upward in V1.1+ based on data — never downward (product rule).

---

## User journey

### Flow X → Y (referral)

1. **X opens the app** → navigates to Profile → taps "Parrainage"
2. **X arrives at `/referral`** — the screen shows:
   - Their personal code (lazily created if it doesn't exist)
   - "Copy" button → native clipboard
   - "Share" button → `Share.share()` iOS/Android with pre-filled message
   - "Your referrals" section: list of referred users (display_name + status)
   - Aggregated stats: total referred users signed up, total subscribed, total CAB earned
3. **X shares their code** via SMS, WhatsApp, email, etc.
4. **Y receives the code**, downloads the app
5. **Y signs up** via OAuth or email → signup screen pre-fills the `referral_code` if deep-linked, otherwise manual field
6. **Backend on Y's signup**:
   - Code validation
   - If valid: create a `referral_uses` (link Y → X), +150 CAB to Y
7. **Y uses the app normally**, accumulates CAB, etc.
8. **If Y subscribes to a paid plan**:
   - Stripe webhook → `POST /rewards/referral/trigger` (internal)
   - Service sets `referral_uses.rewarded_at = NOW()`, `referral_uses.plan = monthly/annual`
   - +500 (monthly) or +750 (annual) CAB to X immediately
   - Notification to X ("Your referred user Alice subscribed — +500 CAB!")

### Flow X consults their history

1. X opens `/referral`
2. "Your referrals" section shows the list:
   - Display name of each referred user (not the email — privacy)
   - Status: "signed up" (signup only), "monthly subscriber" (rewarded), "annual subscriber" (rewarded)
   - Timestamp "referred on X"
   - CAB earned for this referral
3. Aggregated stats at the top: `{total_uses: 12, rewarded_uses: 4, total_cab_earned: 2500}`

---

## Tables

### No schema changes in V1

The `referral_codes` and `referral_uses` tables already exist with all necessary columns.

**Reference** (for reviewers):

```sql
-- referral_codes
id            UUID PRIMARY KEY
user_id       UUID NULL UNIQUE REFERENCES users(id)    -- NULL = influencer code
code          TEXT NOT NULL UNIQUE                     -- uppercase, 6-12 chars
type          TEXT NOT NULL CHECK (type IN ('user', 'influencer'))
created_at    TIMESTAMPTZ NOT NULL DEFAULT now()

-- referral_uses
id                  UUID PRIMARY KEY
referral_id         UUID NOT NULL REFERENCES referral_codes(id)
referred_user_id    UUID NULL UNIQUE REFERENCES users(id)     -- unique = 1 referred user / user lifetime
plan                TEXT NULL CHECK (plan IN ('monthly', 'annual'))
rewarded_at         TIMESTAMPTZ NULL                           -- NULL = Y not yet subscribed
created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
```

The unique constraint on `referred_user_id` guarantees that a user can only be "referred" by a single referrer for life — protection against multi-claim.

---

## Endpoints

### `GET /rewards/referral/code`

**Usage**: X retrieves their own referral code. Creates it if absent (lazy creation).

**Auth**: JWT user (via `get_http_current_user`)

**Response 200**:
```json
{
  "code": "ALICE42",
  "created_at": "2026-04-22T14:30:00+00:00"
}
```

**Error codes**:
- `401 unauthorized` — no valid JWT
- `503 upstream_error` — if DB creation fails

**No request payload** (user derived from JWT).

---

### `GET /rewards/referral/history`

**Usage**: X views the list of their referred users + aggregated stats.

**Auth**: JWT user

**Response 200**:
```json
{
  "code": "ALICE42",
  "stats": {
    "total_uses": 12,
    "rewarded_uses": 4,
    "total_cab_earned": 2500
  },
  "uses": [
    {
      "referred_user_display_name": "Bob",
      "plan": "annual",
      "status": "rewarded",
      "rewarded_at": "2026-04-10T09:00:00+00:00",
      "created_at": "2026-04-01T12:00:00+00:00"
    },
    {
      "referred_user_display_name": null,
      "plan": null,
      "status": "pending",
      "rewarded_at": null,
      "created_at": "2026-04-15T18:30:00+00:00"
    }
  ]
}
```

**Privacy / RGPD**:
- `referred_user_display_name` is the **only** personal information exposed. If the referred user has no display_name, return `null` (not the email).
- `status` derived: `'pending'` if `rewarded_at IS NULL`, `'rewarded'` otherwise.
- `total_cab_earned` calculated from `referral_monthly` / `referral_annual` according to `plan`.

**Error codes**:
- `401 unauthorized`
- `404 no_code` — if the user has not yet generated a code (in practice the UI calls `/code` first which creates it lazily, so this is rare)

---

### `POST /rewards/referral/trigger` (EXISTING, to be extended)

**Usage**: Stripe webhook after `customer.subscription.created` for a user who has a pending `referral_uses`.

**Auth**: `INTERNAL_API_KEY` (not user JWT)

**Request** (unchanged):
```json
{
  "referred_user_id": "uuid",
  "plan": "monthly" | "annual"
}
```

**Response 200**:
```json
{
  "detail": "referral_rewarded",
  "cab_awarded": 500
}
```

**Logic** (significant V1 modifications):
1. Retrieve `referral_uses` by `referred_user_id` (unique)
2. If already `rewarded_at` — return `already_rewarded`
3. Set `rewarded_at = NOW()`, `plan = monthly/annual`
4. **Award CAB to X** (the referrer — `referral.user_id`, NOT `referred_user_id`):
   ```python
   award_cab(
     db,
     user_id=referral.user_id,  # = X (referrer)
     amount=cab_referral_monthly or cab_referral_annual,  # 500 or 750
     reason='referral_reward',
     reference_id=referral_use.id,
     reference_type='referral_use',
     apply_streak_multiplier=False,     # FLAT
     coverage_bonus=0.0,                 # FLAT
     subscription_multiplier=1.0,        # FLAT
   )
   ```
5. **Enqueue gift card for X** (5€ flat, delivered after 30d if Y still subscribed):
   ```python
   INSERT INTO gift_card_orders (
     user_id=referral.user_id,             # recipient = X
     source_type='referral_reward',
     source_ref_id=referral_use.id,         # idempotency
     brand_id=settings.referral.gift_card_brand_id,
     denomination=500,                       # 5€ in cents, flat
     status='pending',
     eligible_at=NOW() + INTERVAL '30 days', # anti-churn delay
   )
   ON CONFLICT (source_type, source_ref_id) DO NOTHING
   ```
6. Commit (no call to Runa here — the daily batch will do it after 30d)

### 🚨 Bug fixed vs existing behaviour

**BEFORE this PR**: `handle_referral_reward` awarded CAB to `referred_user_id` (= Y, the referred user) instead of `referral.user_id` (= X, the referrer). This meant Y was receiving the subscription bonus instead of X.

**AFTER**: the behaviour is corrected in line with referral programme semantics. Y receives only the signup bonus (150 CAB) at registration time, via the signup hook — NOT at the time of their subscription.

**Migration impact**: no existing users affected if the flow was never exercised (to verify — query `cabecoin_transactions WHERE reason='referral' GROUP BY user_id`). If a few users received incorrect CAB, leave them as-is (rule "never reduce").

---

### `POST /admin/referral/link` — NEW

**Usage**: admin/support procedure to link a referred user Y to a referrer X **after signup** (user Y forgot to enter the code at signup, contacts support).

**Auth**: `ADMIN_API_KEY` via header `X-Admin-Api-Key`. Not user JWT.

**Request**:
```json
{
  "referred_user_id": "uuid",
  "code": "ALICE42",
  "admin_operator_id": "alice-support@ratis.app"
}
```

**Response 200**:
```json
{
  "detail": "link_created",
  "referral_use_id": "uuid",
  "signup_bonus_awarded": 150,
  "subscription_reward_triggered": false
}
```

If Y is already subscribed at the time of the request, X's reward is triggered immediately:
```json
{
  "detail": "link_created_and_rewarded",
  "referral_use_id": "uuid",
  "signup_bonus_awarded": 150,
  "subscription_reward_triggered": true,
  "cab_awarded_to_referrer": 500
}
```

**Logic**:
1. Validate `code` → retrieve `referral_codes` row (404 if invalid)
2. Verify there is no existing `referral_uses` for `referred_user_id` (409 if already linked)
3. Verify that `referred_user_id` exists (404 otherwise)
4. Verify `referral.user_id != referred_user_id` (400 `self_parrainage` otherwise)
5. Create `referral_uses` (link Y → X)
6. Award signup bonus to Y (+150 CAB) immediately
7. If Y is already subscribed (check `users.subscription_status == 'active'`): trigger subscription reward flow (CAB to X + enqueue gift card with eligible_at=+30d)
8. Log the operation with `admin_operator_id` in an audit log (new table `admin_audit_log`? or structured log file?)
9. Commit

**Error codes**:
- `401 unauthorized` — ADMIN_API_KEY invalid
- `400 invalid_code` — non-existent code or self-referral
- `404 user_not_found` — referred_user_id does not exist
- `409 already_linked` — Y is already someone's referred user

**PROD doc**: add an entry in `PROD_CHECKLIST.md` explaining the support procedure:
> *"To link a user to a referral post-signup: user contacts support → support retrieves user_id + referrer code → POST /admin/referral/link with ADMIN_API_KEY."*

---

## Internal logic

### `referral_service.get_or_create_code(db, user_id) → ReferralCodeResponse`

```
1. Retrieve referral_codes WHERE user_id = X (LIMIT 1)
2. If found: return (code, created_at)
3. Otherwise:
   a. Generate a unique code: f"{slug_from_user}_{random_4_chars}".upper()[:12]
      If user.display_name exists: base = display_name[:5].upper()
      Otherwise: base = user.email[:5].upper() (then strip non-alphanum)
      Fallback if still unavailable: 8 chars random uppercase
   b. Uniqueness test against referral_codes.code (retry up to 3 times on collision)
   c. INSERT referral_codes
   d. Commit
   e. Return
```

**Edge case**: code collision. Retry with longer random string.

### `referral_service.get_user_history(db, user_id) → ReferralHistoryResponse`

```
1. Retrieve referral_codes WHERE user_id = X → code, referral_id
2. If no code: HTTPException 404 no_code
3. Retrieve referral_uses WHERE referral_id = referral_id
4. For each use:
   a. JOIN users on referred_user_id to retrieve display_name
   b. If users.is_deleted OR display_name IS NULL: return None
5. Calculate stats:
   total_uses = len(uses)
   rewarded_uses = count(u WHERE u.rewarded_at IS NOT NULL)
   total_cab_earned = sum(cab_referral_monthly OR cab_referral_annual for each rewarded use)
6. Return (code, stats, uses[])
```

**Privacy**: NEVER expose the referred user's email or user_id. display_name only.

### Y signup hook (new)

In `ratis_auth/routes/auth.py` on `POST /auth/register` (or equivalent OAuth signup):

```
1. After user creation:
   if input.referral_code is not None:
      referral = referral_repository.get_by_code(db, input.referral_code)
      if referral and referral.user_id != new_user.id:     # no self-referral
          # Create the link
          referral_use = referral_repository.create_use(
              db,
              referral_id=referral.id,
              referred_user_id=new_user.id,
          )
          # Award signup bonus to Y (immediate)
          award_cab(
              db,
              user_id=new_user.id,
              amount=150,
              reason='referral_signup_bonus',
              reference_id=referral_use.id,
              reference_type='referral_use',
              apply_streak_multiplier=False,
              coverage_bonus=0.0,
              subscription_multiplier=1.0,
          )
      # Otherwise: silent fail (invalid code = no bonus, no 400 error either to avoid leaking "exists/does not exist")
2. Commit
```

**Why silent fail**:
- An invalid code must not block signup
- A 400 "referral_code invalid" response leaks which codes exist (enumeration risk)

---

## Frontend

### New file `app/referral.tsx` (Expo Router, outside tabs)

Contents:
- **Header** with back button (`router.back()`)
- **"Your code" section**:
  - Card with the code displayed large (fetched via `useReferralCode`)
  - "Copy" button → `Clipboard.setStringAsync(code)` + toast
  - "Share" button → `Share.share({ message: t('profil.referral.share_message', { code, url }) })`
- **"Stats" section**:
  - 3 tiles: "Friends signed up", "Subscribers", "CAB earned" (from `useReferralHistory`)
- **"History" section**:
  - Scrollable list of `uses`, each item shows: display_name (or "New referral" if null), plan, status (coloured badge), date

### React Query hooks to create

```typescript
// hooks/use-referral-code.ts
export function useReferralCode() {
  return useQuery({
    queryKey: ['referral-code'],
    queryFn: () => rewardsClient.get<ReferralCodeResponse>('/referral/code'),
    staleTime: 60 * 60_000,  // 1h — the code almost never changes
  });
}

// hooks/use-referral-history.ts
export function useReferralHistory() {
  return useQuery({
    queryKey: ['referral-history'],
    queryFn: () => rewardsClient.get<ReferralHistoryResponse>('/referral/history'),
    staleTime: 5 * 60_000,
  });
}
```

### Wiring in `profil.tsx`

Replace:

```tsx
<ProfilMenuRow
  icon="👥" iconColor="coral"
  title={t('profil.items.referral')} subtitle={t('profil.items.referral_sub')}
  onPress={() => {}}
/>
```

With:

```tsx
<ProfilMenuRow
  icon="👥" iconColor="coral"
  title={t('profil.items.referral')} subtitle={t('profil.items.referral_sub')}
  onPress={() => router.push('/referral')}
/>
```

### i18n FR keys to add

```json
"profil": {
  "referral": {
    "title": "Parrainage",
    "back": "Retour",
    "section_code": "Ton code de parrainage",
    "section_stats": "Tes statistiques",
    "section_history": "Tes parrainages",
    "copy_button": "Copier",
    "copied": "✅ Code copié",
    "share_button": "Partager",
    "share_message": "Rejoins-moi sur Ratis avec mon code {{code}} et on gagne chacun des CAB ! {{url}}",
    "stats_signups": "Inscrits",
    "stats_subscribers": "Abonnés",
    "stats_cab_earned": "CAB gagnés",
    "history_empty": "Aucun parrainage pour l'instant. Partage ton code avec tes amis !",
    "status_pending": "Inscrit",
    "status_rewarded_monthly": "Abonné mensuel",
    "status_rewarded_annual": "Abonné annuel",
    "unnamed_user": "Nouveau filleul"
  }
},
```

---

## Inter-services

| Direction | Service | Function | Trigger |
|---|---|---|---|
| ← inbound | `ratis_auth` | `POST /auth/register` with `referral_code` | Y signs up with code |
| → outbound | `ratis_rewards` | `award_cab(signup_bonus)` | After `referral_uses` created |
| ← inbound (webhook) | Stripe | `customer.subscription.created` | Y subscribes |
| → outbound (webhook) | `ratis_rewards` | `POST /referral/trigger` internal | After Stripe webhook |
| → outbound | `ratis_rewards` | `award_cab(referral_reward)` | In trigger handler |
| → outbound | `ratis_notifier` | `notify_user()` (X) | After reward (bonus, optional V1) |

---

## Parameters

In `ratis_settings.json`:

```json
"cab": {
  "earn": {
    "referral_signup_bonus": 150,       // NEW — Y bonus at signup
    "referral_monthly": 500,             // EXISTING (ex cab_referral_monthly)
    "referral_annual": 750               // EXISTING (ex cab_referral_annual)
  }
},
"referral": {
  "gift_card_amount_cents": 500,         // 5€ flat for X regardless of Y's plan
  "gift_card_brand_id": "<uuid>",        // default Runa brand for referral (to set after KYB)
  "eligibility_delay_days": 30           // anti-churn — gift card issued if Y still subscribed after X days
}
```

---

## Rules

- **No self-referral**: `referral.user_id != new_user.id` checked at signup hook and in admin `/link`.
- **1 referred user per user for life**: guaranteed by unique constraint on `referral_uses.referred_user_id`.
- **Invalid code at signup = silent fail** (no 400, no existence leak via enumeration).
- **Invalid code via admin `/link` = explicit 400** (support gets precise feedback, not an attacker).
- **Referred user's display name only** in history (no email, no user_id). If `display_name IS NULL` or user soft-deleted, return `null` (UI renders it as "New referral").
- **X reward flat**: `apply_streak_multiplier=False`, `coverage_bonus=0`, `subscription_multiplier=1` (cf ARCH_cab_economy).
- **Y signup bonus flat**: same, flat, no multiplier.
- **Immediate CAB, deferred gift card**: X receives CAB at Stripe webhook. 5€ gift card only after 30d if Y still subscribed (daily batch).
- **Yearly subscription bonus Y (20€ gift card)**: separate mechanic, covered by `ARCH_gift_cards.md` Flow A — out of scope for this ARCH.
- **Admin audit**: every `POST /admin/referral/link` logged with `admin_operator_id` for traceability.

---

## Out of scope

### V1 strict (pushed to V2+)

- **`POST /rewards/referral/claim` public** — permanently rejected (cf actioned decisions, replaced by admin `/link`)
- **Gift cards in the V1 shop** — no gift card catalogue for CAB spending. Only referral gift cards (5€ X) and yearly subscription bonus (20€ Y) exist in V1.
- **Push notifications to X** when Y subscribes + when the gift card is delivered after 30d — optional V1, can wait for V1.1
- **Deep linking** `ratis.app/?ref=CODE` to auto-fill the code on the OAuth side — nice-to-have V1.1

### Not planned at all

- Cascading bonuses (X earns a bonus if their referred user Y in turn refers Z)
- Referrer tiers (ambassadors, influencers) — the `'influencer'` type exists in DB but is not handled in V1
- Data export by X (referral list as PDF, etc.)

---

## Note on DECISIONS_ACTED

The following decisions are to be added to `DECISIONS_ACTED.md` after this ARCH is validated:

- **DA-xx**: Referral V1 — no public `POST /claim` (economic inconsistency, farming protection). Replaced by `POST /admin/referral/link` via customer support.
- **DA-xx**: Referral V1 — X reward = **500 CAB (monthly) / 750 CAB (annual)** immediate at Stripe webhook + **5€ flat gift card** delivered after 30 days of Y retention (anti-churn).
- **DA-xx**: Referral V1 — Y signup bonus = **150 CAB** flat, at signup with valid code only.
- **DA-xx**: Referral V1 — gift card provider **Runa** (confirmed in `ARCH_gift_cards.md`), `gift_card_orders` pattern extended with `eligible_at` column.
- **DA-xx**: Existing bug fix — `handle_referral_reward` was awarding CAB to `referred_user_id` (Y) instead of `referral.user_id` (X). V1 corrects the direction.
