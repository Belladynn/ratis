---
# Identity
type: service-global
service: ratis_rewards
status: production

# Navigation (Obsidian + RAG)
parent: ARCH_RATIS
sub_archs: [ARCH_cab, ARCH_battlepass, ARCH_missions, ARCH_cashback, ARCH_gift_cards, ARCH_gamification, ARCH_mystery_product]
related: [ARCH_CORE, ARCH_AUTH, ARCH_PRODUCT_ANALYSER, ARCH_cab_economy, ARCH_referral, ARCH_BATCH_RECONCILIATION, ARCH_BATCH_LEADERBOARD, ARCH_BATCH_REFERRAL_PAYOUT, ARCH_BATCH_SAVINGS, ARCH_BATCH_MYSTERY_ANNOUNCE]

# Technique
port: 8004
tech: [FastAPI, PostgreSQL, Redis, Runa, Affilae, Awin, CJ]
tables: [user_cab_balance, user_cashback_balance, cabecoin_transactions, cashback_transactions, cashback_withdrawals, user_missions, missions, user_battlepass_progress, user_battlepass_claims, battlepass_seasons, battlepass_milestones, user_xp_balance, gift_card_brands, gift_card_orders, referrals, referral_events, mission_xp_records, mystery_challenges, user_streaks, notification_outbox, app_settings, affiliate_offers, community_challenges, challenge_milestones, user_challenge_claims]
env_vars: [DATABASE_URL, JWT_PUBLIC_KEY_PATH, JWT_AUDIENCE, INTERNAL_API_KEY, ADMIN_API_KEY, CASHBACK_WEBHOOK_SECRET_AFFILAE, CASHBACK_WEBHOOK_SECRET_AWIN, CASHBACK_WEBHOOK_SECRET_CJ, GIFT_CARD_PROVIDER_KEY, AFFILAE_API_KEY, AWIN_API_KEY, CJ_API_KEY, PAYMENT_PROVIDER_KEY, NOTIFIER_URL]

# Business
tags: [rewards, cab, cashback, gift-cards, battlepass, missions, gamification, xp, admin, referral, rgpd, streak, mystery, leaderboard]
business_domain: cashback
rgpd_concern: true

# Freshness
updated: 2026-05-18
---

# ratis_rewards — CAB economy, cashback, gamification, gift cards

> FastAPI service (port 8004) hosting the entire Ratis economy: CAB balance (cabecoin), cashback (Affilae/Awin/CJ), gift cards (Runa post-KYB V1), gamification (battlepass, missions, XP, streaks), mystery challenges, referral, leaderboard.
> @tags: rewards cab cashback gift-cards battlepass missions xp gamification streak mystery leaderboard runa affilae awin cj admin referral
> @status: LIVRÉ V0
> @subs: auto

> [[ARCH_RATIS]] · sub-ARCHs: [[ARCH_cab]], [[ARCH_battlepass]], [[ARCH_missions]], [[ARCH_cashback]], [[ARCH_gift_cards]], [[ARCH_gamification]], [[ARCH_mystery_product]] · related: [[ARCH_CORE]], [[ARCH_AUTH]], [[ARCH_PRODUCT_ANALYSER]], [[ARCH_cab_economy]], [[ARCH_referral]]

## Index

- [One-sentence summary](#one-sentence-summary) · L.48
- [Responsibility](#responsibility) · L.52
- [Exposed endpoints](#exposed-endpoints) · L.61
- [Owned tables](#owned-tables) · L.102
- [Internal dependencies (other ratis services)](#internal-dependencies-other-ratis-services) · L.141
- [External dependencies (third parties)](#external-dependencies-third-parties) · L.149
- [Key architecture decisions](#key-architecture-decisions) · L.155
- [Main flows](#main-flows) · L.223
- [GDPR constraints specific to this service](#gdpr-constraints-specific-to-this-service) · L.274
- [Things to know (vectorised FAQ)](#things-to-know-vectorised-faq) · L.282
- [Sub-ARCHs](#sub-archs) · L.323
- [Glossary](#glossary) · L.333

---

## One-sentence summary

ratis_rewards is the FastAPI service (port 8004) that hosts the entire Ratis economy: virtual currency CAB (cabecoin), monetary cashback, daily/weekly missions, seasonal battle pass, XP/leaderboard, referral, "Feed Jack" streak, mystery product, gift cards (Runa), and administration via `ADMIN_API_KEY`.

## Responsibility

- ratis_rewards exposes `/api/v1/rewards/*` and `/api/v1/gamification/*` user-facing (JWT) + `/api/v1/admin/*` admin (`ADMIN_API_KEY`) + internal webhooks (`INTERNAL_API_KEY`) + partner webhooks (`CASHBACK_WEBHOOK_SECRET_{PROVIDER}`, one secret per provider).
- ratis_rewards manages the dual currency: **CAB** (virtual, non-sellable, earned through gamification) and **cashback** (monetary, from receipt scans at Affilae/Awin/CJ partner stores, withdrawable via Runa gift cards).
- ratis_rewards issues and validates rewards: missions (daily/weekly/monthly), battle pass milestones (CAB/gift_card/skin), referral (signup bonus +150 CAB for referred user, subscription trigger = CAB + XP + gift card for referrer).
- ratis_rewards maintains gamification: XP, level, Burst leaderboard (monthly + all-time), "Feed Jack" streak, community challenges, mystery product.
- ratis_rewards orchestrates gift cards via Runa (V1 post-KYB) with idempotence on `(source_type, source_ref_id)`.
- ratis_rewards has an internal **outbox worker** (30s poll, `FOR UPDATE SKIP LOCKED`) that dispatches notifications queued in `notification_outbox` to [[ARCH_NOTIFIER]] — outbox pattern to guarantee delivery beyond fire-and-forget.

## Exposed endpoints

Full auto-generated inventory in `ENDPOINTS.md` (section `ratis_rewards`, ~45 endpoints). Functional summary by domain:

**CAB — `/api/v1/rewards/cab/*`**
- `GET /balance` — CAB balance + current battle pass progress.

**Cashback — `/api/v1/rewards/cashback/*`**
- `GET /balance` — cashback balance (pending + available).
- `POST /boost/{transaction_id}` — cashback boost: user doubles their commission (50% → 100%) by spending CABs. Cashback mechanic, distinct from the Buffer mission in [[ARCH_gamification]].
- `POST /scan-detected` — internal, called by ratis_product_analyser for a partner receipt scan.
- `POST /webhook/{provider}` — partner webhook (Affilae/Awin/CJ) signalling validation/rejection.
- `POST /withdraw` — withdrawal to gift card (Runa).

**Events — `/api/v1/rewards/events/*`**
- `POST /scan_accepted` — internal, called by ratis_product_analyser → `award_cab` + `check_missions_progress` atomic.

**Gift cards — `/api/v1/rewards/gift-cards/*`**
- `GET /` · `GET /{order_id}`
- `POST /annual` — creates pending order for annual subscription.
- `POST /{order_id}/issue` — triggers Runa issuance.

**Referral — `/api/v1/rewards/referral/*`**
- `GET /code` (lazy-create) · `GET /history`
- `POST /signup-bonus` · `POST /trigger` (see [[ARCH_referral]]).

**Gamification — `/api/v1/gamification/*`**
- Missions: `GET /missions`, `POST /{id}/claim`, `POST /{id}/freeze`, `POST /{id}/buffer`, `POST /{id}/burst-claim`, `GET /leaderboard/burst-monthly`, `GET /leaderboard/burst-alltime`.
- Battlepass: `GET /battlepass`, `POST /battlepass/claim/{milestone_id}`.
- Community challenge: `GET /challenge`, `POST /challenge/milestones/{id}/claim`.
- "Feed Jack" streak: `GET /streak`, `POST /streak/feed`, `POST /streak/purchase-reserve`, `POST /streak/repair`.
- Mystery: `GET /mystery`, `GET /mystery/leaderboard`, `GET /mystery/history`.
- XP: `GET /xp/balance`.

**Admin — `/api/v1/admin/*`** (protected by `ADMIN_API_KEY`)
- Cashback: `GET/POST /affiliate-offers`, `PATCH /cashback/{id}/validate`, `PATCH /cashback/{id}/refuse`.
- Challenges: `GET/POST /challenges`, `PATCH /{id}/activate`, `PATCH /{id}/deactivate`, `POST /{id}/milestones`.
- Mystery: `GET/POST /mystery`, `GET /mystery/draw`, `PATCH/DELETE /mystery/{id}`.
- Referral: `POST /referral/link` (manual datafix).
- Settings: `GET /settings`, `POST /settings/seed`, `GET/PUT /settings/{section}` (`app_settings` management).

## Owned tables

**Currencies**
- **`user_cab_balance`** — materialised CAB balance per user. PK `user_id`. `balance INT NOT NULL DEFAULT 0 CHECK (balance >= 0)`. Atomic UPDATE mandatory (R09 CLAUDE.md).
- **`cabecoin_transactions`** — immutable ledger of all CAB movements. `direction ∈ {credit, debit}`, `amount > 0`, `reason` CHECK closed list (`receipt_scan`, `label_scan`, `barcode_scan`, `mission_reward`, `battlepass_milestone`, `referral`, `cashback_unlock`, `shop_purchase`, `mystery_reward`, `streak_repair`, ...). **Never purged**. `user_id` FK SET NULL.
- **`user_cashback_balance`** — materialised cashback balance. `pending_cents` + `available_cents`. Atomic UPDATE mandatory.
- **`cashback_transactions`** — cashback ledger (INSERT on partner scan detection, UPDATE status on webhook). **Never purged** (legal requirement). `user_id` FK SET NULL.
- **`cashback_withdrawals`** — withdrawal requests (→ Runa gift card). UNIQUE on `(source_type, source_ref_id)` for idempotence. **Never purged** (legal).

**Battlepass**
- **`battlepass_seasons`** — seasons. Partial UNIQUE INDEX `WHERE is_active=TRUE` guarantees only one active season.
- **`battlepass_milestones`** — milestones per season. `cab_required` (absolute threshold), `reward_type ∈ {cab, gift_card, skin}`, `subscriber_only`.
- **`user_battlepass_progress`** — snapshot `cab_earned_season` per user/season. Updated in the same transaction as `award_cab`.
- **`user_battlepass_claims`** — claims. UNIQUE `(user_id, milestone_id)` = double-claim impossible.

**Missions**
- **`missions`** — unified daily/weekly catalogue. UNIQUE `(action_type, frequency, difficulty)`. `action_type ∈ {receipt_scan, label_scan, barcode_scan, price_compared, ...}`. `difficulty ∈ {easy, medium, hard}`.
- **`user_missions`** — instances per user/mission/period. UNIQUE `(user_id, mission_id, period_start)` (daily = UTC date, weekly = UTC Monday). `status ∈ {pending, completed, claimed}`.

**Gamification**
- **`user_xp_balance`** — total XP per user.
- **`mission_xp_records`** — XP records per completed mission (UNIQUE per `user_mission_id`), feeds Burst monthly + all-time leaderboard. *Replaces `stonks_records` (refactored 2026-05-09 — see [[ARCH_gamification]]).*
- **`user_streaks`** — "Feed Jack" streak (consecutive scan days). Columns: `current_streak`, `food_reserves`, `last_fed_at`, `broken_at`.
- **`community_challenges`** + **`challenge_milestones`** + **`user_challenge_claims`** — community challenges with collective milestones.
- **`mystery_challenges`** — mystery challenges with hidden product, announcement window, winner.

**Gift cards**
- **`gift_card_brands`** — brand catalogue (Amazon, Carrefour, etc.). `runa_product_id`.
- **`gift_card_orders`** — issuance orders. UNIQUE `(source_type, source_ref_id)` = idempotence (same event only creates one card). `eligible_at` implements the 30-day anti-churn on referral gift cards.

**Referral**
- **`referrals`** — link X (referrer) → Y (referred). Shared with [[ARCH_AUTH]] (ratis_auth inserts at registration if code provided).
- **`referral_events`** — events (signup, subscription, trigger, reward) for audit.

**Infra / admin**
- **`app_settings`** — runtime settings source of truth (loaded via `ratis_core.settings.load_settings`, fallback `ratis_settings.json`).
- **`affiliate_offers`** — affiliate offer catalogue (Affilae/Awin/CJ) activatable by admin.
- **`notification_outbox`** — notification queue to dispatch (outbox pattern + `FOR UPDATE SKIP LOCKED`). Polled every 30s by the internal worker.

## Internal dependencies (other ratis services)

- [[ARCH_CORE]] — uses `ratis_core.auth.get_current_user`, `ratis_core.deps.verify_internal_key`, `ratis_core.database`, `ratis_core.settings.load_settings`, `ratis_core.startup.require_env`, `ratis_core.rewards_client` (consumed by other services that call ratis_rewards).
- [[ARCH_AUTH]] — ratis_auth calls ratis_rewards at registration (create `user_cab_balance`), at referral (`POST /rewards/referral/signup-bonus`), at subscription (`POST /rewards/referral/trigger`).
- [[ARCH_PRODUCT_ANALYSER]] — ratis_product_analyser calls ratis_rewards fire-and-forget on each accepted scan (`POST /rewards/events/scan_accepted`) and for detected cashback (`POST /rewards/cashback/scan-detected`).
- [[ARCH_NOTIFIER]] — ratis_rewards calls ratis_notifier via `notification_outbox` (outbox worker poll 30s) for notifications: mission completed, battlepass claimable, cashback validated, etc.
- Satellite batches: [[ARCH_BATCH_RECONCILIATION]] (balance audit), [[ARCH_BATCH_LEADERBOARD]] (refresh materialised leaderboard view — will be reused for `mission_xp_records` in V1.x if relevant, to confirm post-Buffer/Burst refactor), [[ARCH_BATCH_REFERRAL_PAYOUT]] (referrer gift card issuance after anti-churn delay), [[ARCH_BATCH_SAVINGS]] (savings aggregate), [[ARCH_BATCH_MYSTERY_ANNOUNCE]] (mystery winner announcement).

## External dependencies (third parties)

- **Runa** (gift card provider) — env var `GIFT_CARD_PROVIDER_KEY`. Post-KYB (V1). If absent, the service logs a warning at boot and issuances are skipped (orders remain `pending`).
- **Affilae, Awin, CJ** — cashback affiliate networks. Env vars `AFFILAE_API_KEY`, `AWIN_API_KEY`, `CJ_API_KEY`. Inbound webhooks signed via a per-provider HMAC secret `CASHBACK_WEBHOOK_SECRET_{AFFILAE,AWIN,CJ}`.
- **`PAYMENT_PROVIDER_KEY`** (optional) — reserved for a future direct payment provider (out of V1 scope, not currently used).

## Key architecture decisions

### DA-01 — Dual authentication: user-facing JWT + inter-service INTERNAL_API_KEY + ADMIN_API_KEY

**Choice**: `/rewards/*` and `/gamification/*` use user JWT (via `ratis_core.auth.get_current_user`). `/rewards/events/*` and `/rewards/cashback/scan-detected` use `INTERNAL_API_KEY` (via `ratis_core.deps.verify_internal_key`). `/admin/*` uses `ADMIN_API_KEY`. `/rewards/cashback/webhook/{provider}` uses a per-provider HMAC secret `CASHBACK_WEBHOOK_SECRET_{PROVIDER}` (partner signature verification — a leaked secret only compromises one provider).
**Rejected alternative**: everything via JWT with scopes.
**Reason**: in ratis_rewards, inter-service calls (e.g. ratis_product_analyser → ratis_rewards) have no user JWT to relay reliably (fire-and-forget), and partner webhooks have their own auth model. Separating auth schemes avoids mixing contexts and hardens the surface: leaking `ADMIN_API_KEY` does not expose users, leaking a user JWT does not expose admin.

### DA-02 — Atomic UPDATE mandatory on balances (R09)

**Choice**: every CAB / cashback debit goes through `UPDATE ... SET balance = balance - :x WHERE user_id = :u AND balance >= :x`. If `rowcount == 0` → `InsufficientBalance`.
**Rejected alternative**: SELECT then UPDATE with application-level lock.
**Reason**: in ratis_rewards, debits (withdraw, mission freeze, cashback boost) are critical race-condition points. A debit that goes to "pending" while another is in progress can produce a negative balance if the lock is application-level. The atomic `UPDATE` + `rowcount` is the only robust DB-level solution, tested by [[ARCH_BATCH_RECONCILIATION]].

### DA-03 — `award_cab` + battle pass progress update in a single transaction

**Choice**: the `award_cab(user_id, amount, reason, db)` function does in the same transaction: UPDATE `user_cab_balance` + INSERT `cabecoin_transactions` + UPSERT `user_battlepass_progress` (if season active).
**Rejected alternative**: three separate operations with retry.
**Reason**: in ratis_rewards, if one of the three fails (e.g. crash between INSERT transaction and UPSERT progress), the system diverges (balance but no progress, or transaction without balance). The single SQL transaction guarantees "all or nothing". `award_cab` is the sole entry point for any CAB credit, never bypassed.

### DA-04 — Double protection on claims (battlepass, mission, challenge)

**Choice**: (1) the service checks status before INSERT (`get_milestone_for_claim`, etc.). (2) The DB has a UNIQUE constraint (`user_id, milestone_id` for BP, `user_id, mission_id, period_start` for missions, etc.) → `IntegrityError` → 409 on concurrent claim.
**Rejected alternative**: application-level lock only.
**Reason**: in ratis_rewards, a client double-tap (two simultaneous claim requests) can pass the application check if both requests read state before inserting. The DB constraint is the last resort that guarantees uniqueness even in a pure race. Cost: slightly more code to catch `IntegrityError`.

### DA-05 — Bulk mission upsert (no N+1)

**Choice**: on each event (scan accepted), `check_missions_progress` performs two grouped `INSERT ... ON CONFLICT DO UPDATE` (one for `daily`, one for `weekly`), not a Python loop.
**Rejected alternative**: per-mission SELECT/UPDATE/INSERT loop.
**Reason**: in ratis_rewards, a user can have 3-6 active missions simultaneously. N+1 would multiply DB roundtrips on a critical path (every scan). The bulk upsert stays at 2 queries regardless of the number of missions.

### DA-06 — BP milestone status computed dynamically (no `status` column)

**Choice**: no `status` column on `battlepass_milestones`. The status is computed at read time from `cab_earned_season` + `user_battlepass_claims`: `locked` / `unlocked` / `claimed`.
**Rejected alternative**: `status` column updated on each `award_cab`.
**Reason**: in ratis_rewards, the column would duplicate information and require an UPDATE on every award (to recompute locked→unlocked). Dynamic computation is O(1) at read time (~a few milestones per season) and always consistent with the source of truth (progress + claims).

### DA-07 — Outbox pattern for notifications to ratis_notifier

**Choice**: when ratis_rewards needs to trigger a notification (mission claimable, cashback validated, etc.), it INSERTs into `notification_outbox` (in the same transaction as the business change). An internal worker (`_run_outbox_worker`, 30s async loop) SELECTs `FOR UPDATE SKIP LOCKED` and dispatches to ratis_notifier.
**Rejected alternative**: direct fire-and-forget call to ratis_notifier.
**Reason**: in ratis_rewards, some notifications are sensitive (cashback validated = the user must be informed). A fire-and-forget that fails (ratis_notifier down) loses the notification. The outbox guarantees durability: as long as the row exists, the worker retries. `FOR UPDATE SKIP LOCKED` allows N replicas without double-dispatch.

### DA-08 — `gift_card_orders` idempotence via `(source_type, source_ref_id)` + 30-day anti-churn

**Choice**: UNIQUE `(source_type, source_ref_id)` on `gift_card_orders` guarantees that the same event (e.g. referral_trigger user X) only issues one card. `eligible_at = now() + 30 days` on referral cards for anti-churn.
**Rejected alternative**: no constraint, application-level idempotence.
**Reason**: in ratis_rewards, partner webhooks (Runa) can retry. DB idempotence is the only guarantee that 2× webhook = 1 card. The 30-day anti-churn blocks the fraud pattern "I create account Y, subscribe, collect the gift card for X (my own other account), then unsubscribe": the delay allows [[ARCH_BATCH_REFERRAL_PAYOUT]] to verify the subscription is still active before actual issuance.

### DA-09 — `HTTPException` tolerated in services (accepted — project pattern)

**Choice**: as an exception to the "HTTPException only in routes" rule (KP-05), ratis_rewards tolerates `HTTPException` raised in services.
**Rejected alternative**: full refactor to business exceptions then handlers in routes.
**Reason**: in ratis_rewards, services are only called by HTTP routes (no CLI/batch reuse). A full refactor would cost a lot for a theoretical gain (testability). Decision recorded in `DECISIONS_ACTED.md` (2026-04-11). Accepted as contained technical debt.

### DA-10 — `/rewards/missions/{id}/claim` returns 200 (not 201)

**Choice**: POST `/claim` returns 200, not 201.
**Rejected alternative**: 201 Created (REST convention).
**Reason**: in ratis_rewards, a claim is not the creation of a user-visible persistent resource (no returned ID to reuse). It is an action that changes state (`status='claimed'`) and pays out a reward. 200 is semantically more appropriate. Decision recorded in `DECISIONS_ACTED.md` (2026-04-11).

### DA-11 — Runtime parameters in `app_settings` DB + fallback `ratis_settings.json`

**Choice**: all thresholds (CAB per scan, expiry windows, confidence thresholds, etc.) are in the `app_settings` table (loaded via `ratis_core.settings.load_settings`) with fallback `ratis_settings.json` at boot if DB is empty. Admin can modify via `PUT /api/v1/admin/settings/{section}`.
**Rejected alternative**: env vars or hardcoded Python.
**Reason**: in ratis_rewards, economic thresholds (CAB per scan, cashback_min_withdrawal, etc.) must be adjustable without redeployment (R19 CLAUDE.md). The DB enables hot-reload, JSON is the initial seed. Values are stored in cents (no float).

## Main flows

### Flow 1 — Award CAB via accepted scan (fire-and-forget from ratis_product_analyser)

1. ratis_product_analyser accepts a scan and calls `POST /api/v1/rewards/events/scan_accepted` with `INTERNAL_API_KEY` and payload `{user_id, scan_type, scan_id}`.
2. ratis_rewards reads `settings.rewards.cab_per_{receipt|label|barcode}_scan`.
3. In a single transaction: `award_cab(user_id, amount, reason='<scan_type>')` (UPDATE balance + INSERT `cabecoin_transactions` + UPSERT `user_battlepass_progress`) + `check_missions_progress(user_id, action_type=scan_type)` (bulk UPSERT `user_missions`).
4. If any missions just transitioned to `completed` → INSERT `notification_outbox` (type `mission_completed`).
5. Commit. Return 200. The outbox worker (30s poll) dispatches the notification to ratis_notifier.

### Flow 2 — Claim battlepass milestone

1. Client calls `POST /api/v1/gamification/battlepass/claim/{milestone_id}`.
2. ratis_rewards loads the milestone + user progress + verifies `cab_earned_season >= cab_required` + absence of existing claim (double protection).
3. If `subscriber_only=true` → verify active subscription via [[ARCH_AUTH]] (direct DB read, no HTTP call). If not subscribed → 403 `subscriber_required`.
4. INSERT `user_battlepass_claims` (if `IntegrityError` → 409 `already_claimed`).
5. According to `reward_type`: `cab` → `award_cab`, `gift_card` → INSERT pending `gift_card_orders`, `skin` → reserved V2.
6. Commit. INSERT `notification_outbox`. Return 200 with the reward.

### Flow 3 — Cashback detection + validation

1. ratis_product_analyser detects a partner receipt scan → calls `POST /api/v1/rewards/cashback/scan-detected` with `INTERNAL_API_KEY`.
2. ratis_rewards INSERTs `cashback_transactions` (`status='pending'`) + UPDATEs `user_cashback_balance.pending_cents`.
3. Later, the partner (Affilae/Awin/CJ) validates the cashback and calls `POST /api/v1/rewards/cashback/webhook/{provider}` signed via the HMAC secret for that provider `CASHBACK_WEBHOOK_SECRET_{PROVIDER}`.
4. ratis_rewards UPDATEs `cashback_transactions.status='validated'` + transfers `pending_cents` → `available_cents`.
5. INSERT `notification_outbox` (type `cashback_available`). Outbox worker dispatches via ratis_notifier.

### Flow 4 — Withdraw cashback (withdrawal via Runa gift card)

1. Client calls `POST /api/v1/rewards/cashback/withdraw` with `{brand_id, amount_cents}`.
2. ratis_rewards verifies `amount_cents >= settings.rewards.cashback_min_withdrawal`.
3. Atomic transaction: `UPDATE user_cashback_balance SET available_cents = available_cents - :x WHERE user_id = :u AND available_cents >= :x` → if `rowcount=0` → `InsufficientBalance`.
4. INSERT `cashback_transactions` (direction=debit), INSERT `cashback_withdrawals`, INSERT `gift_card_orders` (pending, UNIQUE on `(source_type='cashback_withdrawal', source_ref_id)`).
5. Commit. An async process (or manual `POST /gift-cards/{id}/issue`) calls the Runa API.
6. Runa webhook confirms issuance → `gift_card_orders.status='issued'` + `cashback_withdrawals.status='completed'`.

### Flow 5 — Referral trigger (referrer rewarded on referred user subscription)

1. Referred user Y subscribes via ratis_auth → ratis_auth calls `POST /api/v1/rewards/referral/trigger` with `INTERNAL_API_KEY` and `{referred_user_id, subscription_type}`.
2. ratis_rewards loads the `referrals(referrer_id, referred_id=Y)` row.
3. Immediate CAB + XP credit to referrer X via `award_cab(X, settings.rewards.cab_referral_{monthly|annual})`.
4. INSERT `gift_card_orders(source_type='referral_trigger', source_ref_id=referral.id, eligible_at=now()+30days, status='pending')` → idempotent.
5. [[ARCH_BATCH_REFERRAL_PAYOUT]] (cron) will verify at `eligible_at` that Y's subscription is still active before actually issuing the card (anti-churn). See [[ARCH_referral]].

### Flow 6 — Outbox worker (dispatch notifications)

1. The worker (async coroutine in lifespan) loops: `await asyncio.sleep(30)`.
2. `process_outbox_batch(db)` does `SELECT ... FROM notification_outbox WHERE status='pending' ORDER BY created_at LIMIT N FOR UPDATE SKIP LOCKED`.
3. For each row, calls ratis_notifier (`notifier_client.notify_user`) then UPDATEs `status='dispatched'` or `status='failed'` with retry count.
4. Commit. Rows locked by other replicas are ignored (`SKIP LOCKED`) → zero double-dispatch.

## GDPR constraints specific to this service

- **Never purge** `cabecoin_transactions`, `cashback_transactions`, `cashback_withdrawals`, `subscriptions` (legal financial traceability requirement).
- On user GDPR deletion (via ratis_auth `DELETE /account`), historical tables keep `user_id=NULL` (FK SET NULL) to preserve accounting integrity. Balances (`user_cab_balance`, `user_cashback_balance`) are RESTRICT (no DB DELETE, only user soft-delete).
- `leaderboard` and `burst-leaderboard` (monthly + all-time) never display the email: only anonymous `display_name` (e.g. `Ratis_a3f2`).
- Partner webhooks receive opaque tokens: ratis_rewards never sends user information back to them.
- `gift_card_orders` contains the gift card code encrypted (or stored at Runa only) — never in plaintext.

## Things to know (vectorised FAQ)

### Why does ratis_rewards host BOTH currencies (CAB + cashback) in the same service?

In ratis_rewards, CAB and cashback share the same issuance logic (scan → event → credit), the same invariants (atomic UPDATE, immutable ledger, never purge) and the same admin surface (validate/refuse cashback, seed settings). Splitting into two services would duplicate 70% of the code (atomic transactions, audit, withdraw, outbox) with no clear benefit. The two currencies coexist with distinct rules: CAB = virtual non-sellable (red-line `!sell-CAB`), cashback = monetary withdrawable via gift card.

### Why does ratis_rewards have an internal outbox worker instead of Celery?

In ratis_rewards, the outbox pattern with `FOR UPDATE SKIP LOCKED` provides the durability needed without the Celery infrastructure. The worker runs in the same FastAPI process (async coroutine, negligible overhead), shares the DB connection pool, and scales horizontally to N replicas. Adding Celery would be excess infrastructure for a modest notification volume (thousands/day, not millions).

### How does ratis_rewards guarantee atomicity of `award_cab` touching 3 tables?

In ratis_rewards, the `award_cab` function opens a transaction (`with db.begin()`) and executes in order: UPDATE `user_cab_balance`, INSERT `cabecoin_transactions`, UPSERT `user_battlepass_progress`. If one fails, PostgreSQL rolls back the previous ones. No intermediate state is visible to reads (Read Committed isolation). Combined with the rule "never `db.commit()` in services, commit only in the route" (R02 CLAUDE.md), consistency is guaranteed.

### How to test ratis_rewards locally?

1. Start the infrastructure: `docker compose up -d` (Postgres + Redis).
2. Seed settings: `uv run --package ratis_rewards python -c "from ratis_core.settings import load_settings; print(load_settings())"` (the service bootstraps settings into `app_settings` via `POST /admin/settings/seed`).
3. Run tests: `uv run --package ratis_rewards pytest webservices/ratis_rewards/tests/ -v`.
4. Start the service: `uv run --package ratis_rewards uvicorn main:app --port 8004 --reload` from `webservices/ratis_rewards/`.

### What is the difference between ratis_rewards and ratis_auth for referral?

ratis_auth creates the `referrals(referrer_id=X, referred_id=Y)` row at Y's registration if a code was provided, and calls `POST /rewards/referral/signup-bonus` to credit Y with 150 welcome CABs. ratis_rewards hosts all reward logic: signup bonus, trigger at subscription (CAB + XP credit to referrer + INSERT pending gift card), and 30-day anti-churn management via [[ARCH_BATCH_REFERRAL_PAYOUT]]. See [[ARCH_referral]] for the full cross-service flow.

### Why does `reward_type='skin'` exist in the CHECK even though no milestone uses it in V1?

In ratis_rewards, the CHECK (`reward_type IN ('cab', 'gift_card', 'skin')`) is defined in V1 to avoid a breaking migration in V2 when cosmetic skins are introduced (see `PROD_CHECKLIST.md`). Adding a value to an existing CHECK requires `ALTER TABLE ... DROP CONSTRAINT + ADD CONSTRAINT` with risk of conflict. Better to plan ahead now.

### How does `ratis_rewards` prevent double-claim on a mission?

Two protections: (1) the service verifies `status='completed'` before UPDATE→`claimed`, (2) the UNIQUE constraint `(user_id, mission_id, period_start)` on `user_missions` prevents inserting a duplicate for the same period. A client double-tap causes an `IntegrityError` → 409 `mission_already_claimed`. The `award_cab` is in the same transaction → full rollback if claim fails.

### Why does the Burst leaderboard have monthly + all-time?

In ratis_rewards, two timeframes have coexisted since the 2026-05-09 refactor (see [[ARCH_gamification]] § Leaderboard Burst):

- **Monthly** — a purely global leaderboard favours early adopters and discourages newcomers (insurmountable gap). Monthly reset puts everyone back on equal footing → sustained engagement. The materialised view `leaderboard_current` is refreshed by [[ARCH_BATCH_LEADERBOARD]] every N minutes (configurable).
- **All-time** — absolute prestige: the record of the dedicated farmer who reached `n_burst = 18` stays in the hall of fame forever, even if never beaten again. Killer feature for competitive culture. No reset.

Records are persisted in `mission_xp_records` (UNIQUE per `user_mission_id`, in-place UPDATE on re-claim of additional Burst milestones). The table replaces `stonks_records` (pre-prod drop, see [[ARCH_gamification]] § Drop obsolete table).

### Why the 30-day anti-churn on referral gift cards?

In ratis_rewards, without anti-churn, a bad-faith user could create a fake account Y, subscribe with a prepaid card, trigger the referral gift card for X (their own other account), then unsubscribe. The 30 days allow [[ARCH_BATCH_REFERRAL_PAYOUT]] to verify that Y's subscription is still active before actually issuing the card. UX cost: the referrer waits a month for the card, but receives their CABs + XP immediately as feedback.

## Sub-ARCHs

- [[ARCH_cab]] — details of the CAB currency (award_cab, debit_cab, reasons).
- [[ARCH_battlepass]] — seasons, milestones, claims, subscriber_only.
- [[ARCH_missions]] — unified daily/weekly catalogue, bulk upsert, freeze, buffer, burst.
- [[ARCH_cashback]] — scan-detected, partner webhooks, withdraw, Runa.
- [[ARCH_gift_cards]] — brands, orders, idempotence, anti-churn.
- [[ARCH_gamification]] — XP, levels, Burst leaderboard (monthly + all-time), Buffer, Feed Jack streak, community challenges.
- [[ARCH_mystery_product]] — mystery challenges, clues, leaderboard, admin draw.

## Glossary

- **DA-XX**: numbered architecture decision (see dedicated section).
- **CAB / cabecoin**: Ratis internal virtual currency. Non-sellable (red-line `!sell-CAB`). Earned through gamification, spent in shop/missions/cashback unlock.
- **Cashback**: real euros from partner receipt scans (Affilae/Awin/CJ). Withdrawable via Runa gift card.
- **Runa**: gift card provider used in V1 post-KYB. Env var `GIFT_CARD_PROVIDER_KEY`.
- **Buffer**: active extension of a daily mission (`× 2 target`, `+ R cab`, `+ 1 day duration`, free, stackable up to 3). Cumulative multi-claim via double gating. See [[ARCH_gamification]] § Buffer.
- **Burst**: passive unlock of exponential XP milestones after exceeding the target (`xp × 2^n_burst`, 0 CAB, no cap). Anti-Buffer lock on first burst-claim. See [[ARCH_gamification]] § Burst.
- **Burst Leaderboard**: monthly + all-time, records in `mission_xp_records` (UNIQUE per `user_mission_id`). Replaces the former "Stonks leaderboard" (refactored 2026-05-09).
- **Feed Jack**: streak mechanic where the user "feeds" a character (Jack) every day. `user_streaks.food_reserves` purchasable in CAB to protect the streak.
- **Mystery product**: mystery challenge where a product is hidden with progressive clues. First to scan = winner. See [[ARCH_mystery_product]].
- **Outbox pattern**: `notification_outbox` + internal worker `FOR UPDATE SKIP LOCKED` to guarantee notification delivery even if ratis_notifier is temporarily down.
- **Anti-churn 30d**: delay applied to `gift_card_orders.eligible_at` for referral cards, verified by `ratis_batch_referral_payout` before Runa issuance.
- **`INTERNAL_API_KEY`**: shared secret between all ratis services for inter-service calls (via `ratis_core.deps.verify_internal_key`).
- **`ADMIN_API_KEY`**: internal admin secret (Ratis operator), distinct from INTERNAL_API_KEY, protects `/api/v1/admin/*`.
- **`CASHBACK_WEBHOOK_SECRET_{PROVIDER}`**: signing secret for cashback partner webhooks — one distinct secret PER provider (`AFFILAE`, `AWIN`, `CJ`), dynamically derived from the `cashback.webhook_providers` allowlist. A leaked secret only compromises one provider (AUDIT 2026-05-17).
