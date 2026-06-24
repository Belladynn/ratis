---
type: sub-arch
service: ratis_rewards
parent: ARCH_REWARDS
related: [ARCH_cab, ARCH_missions, ARCH_feed_jack, ARCH_BATCH_LEADERBOARD]
status: production
tags: [gamification, xp, streak, feed-jack, buffer, burst, achievements, tirelires, prestige]
updated: 2026-05-09
---

# ratis_rewards + ratis_product_analyser — ARCH Gamification

> Cross-service gamification layer (rewards + product_analyser): photo hash, XP, mission freeze, Feed Jack streak, achievements, tirelires, prestige. Buffer + Burst pending.
> @tags: gamification xp streak feed-jack buffer burst achievements tirelires prestige photo-hash mission-freeze
> @status: LIVRÉ V0
> @subs: auto

> Parent: [[ARCH_REWARDS]] · Relations: [[ARCH_cab]], [[ARCH_missions]], [[ARCH_feed_jack]], [[ARCH_BATCH_LEADERBOARD]]

> Status: ✅ Implemented — photo hash, XP, mission freeze, Feed Jack streak. ⏳ Planned — Buffer + Burst (PR pending, design `docs/superpowers/specs/2026-05-09-buffer-burst-design.md`)
> Branch: `main`

---

## Implementation Checklist

**Base checklist:**
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

**Specific checklist:**
- [x] Photo hash — migration: `receipts.photo_hash` + partial UNIQUE index; `scans.photo_hash` partial index `scan_type='electronic_label'`
- [x] Photo hash — SQLAlchemy models updated (`Receipt`, `Scan`)
- [x] Photo hash — server-side verification + R2 upload post-INSERT in `ratis_product_analyser` (hash → DB → R2)
- [x] Photo hash — rate limiting 3/min on POST receipt + label
- [x] Photo hash — `ratis_batch_purge`: release hashes blocked in `pending > 1h`
- [x] Photo hash — endpoint `GET /scan/check-hash` (client-side check, network optimization, wired to frontend in V2)
- [x] XP — table `user_xp_balance` + events on all actions
- [x] XP — amounts in `ratis_settings.json["xp"]`
- [x] Buffer — `buffer_count` (renamed from `boost_count`) + `portions_claimed` + `period_extended_until` on `user_missions`
- [x] Burst — `burst_count` + `burst_locked` on `user_missions`
- [x] Burst Leaderboard — table `mission_xp_records` (monthly + all-time)
- [ ] V1 implementation (PR pending — design validated 2026-05-09, see `docs/superpowers/specs/2026-05-09-buffer-burst-design.md`)
- [x] Mission freeze — endpoint + `frozen_until` on `user_missions`
- [x] `is_boostable` on `missions` catalogue — flag kept. Buffer gating = (a) `frequency='daily'` AND (b) `is_boostable=true`. Allows marking a daily mission as non-bufferable explicitly (e.g.: `receipt_scan` anti-push-buy)
- [x] Feed Jack — daily streak endpoint + award_xp `feed_jack`

> ⚠️ One item at a time. Do not move to the next until the previous is complete.

---

## Index

- [Context](#context) [L.55 - L.70]
- [Photo hash — scan deduplication](#photo-hash--scan-deduplication) [L.72 - L.130]
- [XP System](#xp-system) [L.132 - L.200]
- [Buffer — active mission extension](#buffer--active-mission-extension)
- [Burst — passive exponential XP](#burst--passive-exponential-xp)
- [Burst Leaderboard](#burst-leaderboard)
- [Mission freeze](#mission-freeze) [L.342 - L.375]
- [Inter-services](#inter-services) [L.377 - L.395]
- [Parameters](#parameters) [L.397 - L.430]
- [Rules](#rules) [L.432 - L.445]
- [Feed Jack — daily streak](#feed-jack--daily-streak) [L.550 - L.640]
- [Community Challenge](#community-challenge) [to complete]
- [Out of scope](#out-of-scope) [L.447 - L.460]

---

## Context

Read before starting:
- `CLAUDE.md`
- `KNOWN_PROBLEMS_INDEX.md`
- `DECISIONS_ACTED.md`
- `webservices/ratis_rewards/ARCH.md`
- `webservices/ratis_product_analyser/ARCH.md`

Required dependencies:
- Table `missions` and `user_missions` (existing)
- Table `scans` and `electronic_label_scans` (existing)
- `ratis_batch_purge` (existing — to extend)
- `award_cab` in `repositories/cab_repository.py`

---

## Photo hash — scan deduplication

### Problem

A user may submit the same photo multiple times. Two distinct cases:
- **Client-side**: the app has already sent this photo (e.g.: double tap, network retry). Goal: save bandwidth — do not re-upload if we already know it is a duplicate.
- **Server-side**: a malicious actor bypasses the client and spams the API directly with the same image to farm buffered missions. The server is the final security line.

Both checks are independent and complementary.

### Client-side check (network optimization)

The client computes the SHA-256 hash of the photo **before** uploading and first calls:

```
GET /api/v1/scans/check-hash?hash=<sha256hex>
```

Response:
```json
{ "duplicate": true }   → the app does not upload, shows "photo already sent" message
{ "duplicate": false }  → the app proceeds with the normal upload
```

Advantage: zero bytes transferred for a duplicate detected client-side. No business logic — just a SELECT.

**Note**: this check is not a security measure — a malicious client can ignore it. It does not replace the server-side check.

### Server-side check (anti-spam security)

The server recomputes the SHA-256 on the received bytes (independently of the client) and attempts an atomic INSERT:

```
INSERT scan (photo_hash=:hash, ...)
ON CONFLICT (photo_hash) DO NOTHING RETURNING id
→ No row returned → 409 duplicate_photo (silently rejected)
```

This check cannot be bypassed — even if the client lies about the hash or bypasses `check-hash`.

### Table changes

**`receipts`** — column added (receipt photo = 1 receipt, N scans per product)

```sql
photo_hash  CHAR(64)  -- SHA-256 hex, NULL until submitted
```

```sql
CREATE UNIQUE INDEX receipts_photo_hash_unique
ON receipts(photo_hash)
WHERE photo_hash IS NOT NULL;
```

> Why `receipts` and not `scans` for receipts? A receipt generates N `scans` (one per OCR product line). Putting the hash on `scans` would cause a UNIQUE constraint violation from the 2nd product on the same receipt. The photo is on `receipts.image_r2_key`, so the hash must live there too.

**`scans`** — column added, covers only `scan_type = 'electronic_label'`

```sql
photo_hash  CHAR(64)  -- SHA-256 hex, NULL if not applicable (e.g.: manual barcode_scan)
```

```sql
CREATE UNIQUE INDEX scans_photo_hash_unique
ON scans(photo_hash)
WHERE photo_hash IS NOT NULL AND scan_type = 'electronic_label';
```

Note: there is no separate `electronic_label_scans` table — all types go through `scans`.

### Full upload flow (ratis_product_analyser)

```
[CLIENT]
1. Local SHA-256 computation
2. GET /scans/check-hash?hash=... → if duplicate: stop (network savings)
3. Upload photo + hash

[SERVER]
4. SHA-256 recomputation on received bytes (independent check)
5. INSERT scan (photo_hash=:hash, status='pending', ...)
   ON CONFLICT (photo_hash) DO NOTHING RETURNING id
   → No row returned → 409 duplicate_photo  ← rejection BEFORE R2 upload
6. Upload to R2  ← only if hash is accepted in DB
7. OCR worker:
   accepted  → photo_hash kept (permanent dedup)
   rejected  → SET photo_hash = NULL (hash released, legitimate retry possible)
   error     → SET photo_hash = NULL
```

> ⚠️ **The R2 upload must happen AFTER the DB INSERT (step 5).** A malicious actor spamming the same image must never trigger an upload to storage. Without this safeguard, each duplicate attempt would consume R2 bandwidth and storage costs.

### Rate limiting

`slowapi` — 3 requests/minute per IP on:
- `POST /api/v1/scan/receipt`
- `POST /api/v1/scan/label`
- `POST /api/v1/scan/label/batch`

Consistent with the `CLAUDE.md` pattern (already applied on `/auth/login` and `/auth/register`).

**Race condition**: `ON CONFLICT DO NOTHING` is atomic — two simultaneous requests with the same hash, only one passes.

**Hash too early**: if the worker crashes before processing the scan, the scan remains `pending` with the hash locked. `ratis_batch_purge` releases blocked hashes (see below).

### ratis_batch_purge extension

Add to the existing batch purge:

```sql
-- Release hashes from blocked pending scans (OCR crash)
UPDATE scans
SET photo_hash = NULL
WHERE status = 'pending'
  AND photo_hash IS NOT NULL
  AND created_at < NOW() - INTERVAL '1 hour';
```

### Error codes

| Code | HTTP | Condition |
|---|---|---|
| `duplicate_photo` | 409 | Hash already present server-side |

---

## XP System

### Principle

XP is a **pure prestige** currency — it does not convert to CABs or monetary rewards. It feeds only leaderboards and badges. Amounts can become astronomical (e.g.: `10 × 2^200` via Burst) — no cap.

### Table

**`user_xp_balance`** — created

```sql
user_id     UUID PRIMARY KEY REFERENCES users(id) ON DELETE RESTRICT
balance     NUMERIC NOT NULL DEFAULT 0  -- NUMERIC without precision: supports 2^200+
level       INT NOT NULL DEFAULT 0      -- updated at each award_xp
updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
```

**Level calculation:**

Threshold to reach level `n`: `X × (2^n − 1)` cumulative XP, with `X = xp_settings["level_base"]`.

Computed in pure integer arithmetic in `award_xp` — never in float (XP can exceed `2^200`).

```python
def _compute_level(balance: int, level_base: int) -> int:
    """Returns the level corresponding to the given XP balance."""
    if balance <= 0 or level_base <= 0:
        return 0
    level = 0
    threshold = level_base  # X × 2^0 = X
    while balance >= threshold:
        level += 1
        threshold += level_base << level  # X × 2^level (integer addition)
    return level
```

The level is recomputed and persisted at each `award_xp`. Never computed on-the-fly on the DB side.

**`xp_transactions`** — created

```sql
id          UUID PRIMARY KEY DEFAULT gen_random_uuid()
user_id     UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT
amount      NUMERIC NOT NULL  -- always positive (no XP debit)
reason      TEXT NOT NULL CHECK (reason IN (
              'receipt_scan', 'label_scan', 'barcode_scan',
              'price_compared', 'mission_completed', 'battlepass_milestone',
              'referral', 'feed_jack', 'burst_completion'
            ))
reference_id    UUID
reference_type  TEXT
created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
```

### Base XP amounts

Defined in `ratis_settings.json["xp"]`:

| Action | Base XP |
|---|---|
| `receipt_scan` accepted | 10 |
| `label_scan` accepted | 8 |
| `barcode_scan` accepted | 5 |
| `price_compared` | 3 |
| `mission_completed` | 10 |
| `battlepass_milestone` | 20 |
| `referral` | 50 |
| `feed_jack` (daily streak — Jack skin) | 5 |
| `burst_completion` | `xp_base × 2^burst_count` |

### Function `award_xp(db, user_id, amount, reason, reference_id, reference_type)`

Same pattern as `award_cab` — UPSERT on `user_xp_balance`, INSERT into `xp_transactions`.

```
1. INSERT xp_transactions (user_id, amount, reason, reference_id, reference_type)
2. INSERT user_xp_balance (user_id, balance=amount, level=_compute_level(amount, level_base))
   ON CONFLICT (user_id) DO UPDATE
     SET balance    = balance + :amount,
         level      = _compute_level(balance + :amount, level_base),
         updated_at = now()
```

`level_base` read from `ratis_settings["xp"]["level_base"]`.

Never XP debit — `amount` always positive.

---

## Buffer — active mission extension

> ⚠️ Overhaul 2026-05-09 — the former **Stonks** mechanic (`boost_count × 1.1^n CAB`) has been replaced by **Buffer + Burst**, two distinct mechanics serving two opposite player profiles. The word "Stonks" disappears from the product + code vocabulary. Authoritative spec: `docs/superpowers/specs/2026-05-09-buffer-burst-design.md`.

### Principle

**Buffer** is an **active** mechanic: the user buys themselves **margin** (window extension + additional linear CAB) by doubling the objective. Use case: "I won't have time to finish within the normal window". Buffer is **free** (no CAB cost) and stackable up to `n_max = 3` daily.

```
Buffer n:
  target_count          × 2^n
  cab_reward            × (n+1)         (linear — anti-inflation)
  xp_reward             unchanged       (the farmer goes to Burst for XP)
  period_extended_until = period_start + (n+1) days
  Buffer cost           = 0 CAB
```

Weekly missions = **non-bufferable** (the 7-day window is already sufficient).

### `user_missions` table change

```sql
buffer_count          INT NOT NULL DEFAULT 0       -- renamed from boost_count, 0-3 daily
period_extended_until TIMESTAMPTZ NULL             -- NULL if not buffered
portions_claimed      INT NOT NULL DEFAULT 0       -- number of Buffer portions already claimed (0 to n+1)
```

### Endpoint

#### `POST /api/v1/gamification/missions/{user_mission_id}/buffer` (renamed from `/boost`)

Auth: JWT

Response 200:
```json
{
  "buffer_count": 2,
  "target_count": 12,
  "cab_reward": 3,
  "period_extended_until": "2026-05-12T00:00:00Z"
}
```

Error codes:

| Code | HTTP | Condition |
|---|---|---|
| `weekly_not_bufferable` | 400 | Mission `frequency = 'weekly'` |
| `buffer_cap_reached` | 409 | `buffer_count >= 3` |
| `burst_locked` | 409 | `burst_locked = true` (1st Burst already claimed) |
| `mission_not_pending` | 409 | Status ≠ `pending` |
| `mission_not_found` | 404 | |

### `apply_buffer(user_mission_id, db)` logic (summary)

```
1. Check conditions: frequency='daily', buffer_count<3, burst_locked=false, status='pending'
2. R = cab_reward / (buffer_count + 1)              -- retrieve original R
3. UPDATE user_missions SET
     buffer_count          = buffer_count + 1,
     target_count          = target_count * 2,
     cab_reward            = R * (buffer_count + 1),
     period_extended_until = period_start + (buffer_count + 1) days
4. db.commit()
```

### Distribution — double gating + cumulative multi-claim

For a buffered mission `n`:
- **Window**: `n + 1` days from `period_start`
- **Total reward**: `R_original × (n + 1)` CAB
- **1R unlocked per calendar day**, conditioned on progress milestones reached
- **Progress milestone**: `target_count / (n+1)` actions → unlocks 1 additional R
- **Available portions calculation**:
  ```
  milestones_reached   = min(current_count // milestone_size, n+1)
  days_elapsed         = min((now.date() - period_start).days + 1, n+1)
  portions_available   = min(milestones_reached, days_elapsed)         ← double gating
  portions_to_claim    = portions_available − portions_claimed
  cab_to_award         = portions_to_claim × R_original
  ```
- **Cumulative multi-claim**: user can claim each day OR all at once at the deadline, at their convenience
- **Mission close**: `portions_claimed == n+1` or `now > period_extended_until`

#### Examples (Buffer mission `n=2`, target_original=3 ESL → post-buffer = 12 ESL, 3-day window, R=1, total 3R)

| User behaviour | At claim time | Receives |
|---|---|---|
| Does 12 ESL D1, claims D1 | min(3, 1) = 1 portion | 1R |
| Does 12 ESL D1, claims D3 without claiming before | min(3, 3) = 3 cumulative portions | 3R at once |
| Does 10/12 ESL over 3 days, claims D3 | milestones=2, days=3 → min=2 | 2R only (milestone 3 never reached) |
| Does 4/12 ESL D1, claims D3 | milestones=1, days=3 → min=1 | 1R only |
| Does 0 ESL | milestones=0 | 0 (deadline → all lost) |

### Claim endpoint (modified)

#### `POST /api/v1/gamification/missions/{user_mission_id}/claim`

Auth: JWT

Response 200:
```json
{
  "cab_awarded": 2,
  "portions_claimed_total": 2,
  "portions_remaining": 1,
  "mission_status": "pending"
}
```

Error codes:

| Code | HTTP | Condition |
|---|---|---|
| `no_portion_available_now` | 402 | Insufficient milestones or days |
| `already_claimed` | 409 | `portions_claimed == buffer_count + 1` |
| `mission_expired` | 410 | `now > period_extended_until` |

### Notification

Single push at D-deadline (`period_extended_until - 1h`):
> "Your Buffer mission expires in 1h, come claim your CAB."

Internal mini-cron `notify_buffer_deadlines` every 10 min scans `user_missions WHERE status='pending' AND period_extended_until BETWEEN NOW() AND NOW() + INTERVAL '1 hour'`. No daily spam (Feed Jack already does that).

### Soft cap

`n_max = 3` daily (= max duration 4 days, target = 8× original). Beyond that → `409 buffer_cap_reached`. UX: greyed-out "Buffer" button.

---

## Burst — passive exponential XP

### Principle

**Burst** is a **passive** mechanic: if the user exceeds the objective of an active mission, **additional Burst milestones unlock automatically** with exponential XP but **0 additional CAB**. Use case: "I'm on a roll, I'm going to crush the score on 1 mission for the leaderboard".

```
Burst milestone N:
  trigger        : current_count >= target_count × 2^N    (auto-unlocked)
  additional_xp  : xp_reward × 2^(N - 1)                  (xp_total = xp_initial × 2^N for milestone N)
  cab            : 0 (intentional — Burst serves the leaderboard, not the economy)
  cap            : none (no cap — a hardcore farmer can reach n_burst = 18+)
  duration       : unchanged (Burst does not affect the window)
```

### `user_missions` table change

```sql
burst_count   INT NOT NULL DEFAULT 0      -- number of Burst milestones reached
burst_locked  BOOLEAN NOT NULL DEFAULT FALSE  -- true at 1st burst-claim → blocks Buffer
```

### Endpoint

#### `POST /api/v1/gamification/missions/{user_mission_id}/burst-claim`

Auth: JWT

Effect: claims 1 or more unlocked Burst milestones (XP only, 0 CAB). Sets `burst_locked = true` at the first claim on this mission.

Response 200:
```json
{
  "xp_awarded": 320,
  "burst_count_total": 3,
  "burst_locked": true,
  "leaderboard_record_updated": true
}
```

Error codes:

| Code | HTTP | Condition |
|---|---|---|
| `no_burst_palier_unlocked` | 402 | `current_count < target_count × 2` |
| `mission_not_found` | 404 | |

### `claim_burst(user_mission_id, db)` logic (summary)

```
1. Calculate burst_milestones = max(0, int(log2(current_count / target_count)))
2. If burst_milestones <= burst_count → 402 no_burst_palier_unlocked
3. xp_to_award = sum(xp_reward × 2^k for k in range(burst_count + 1, burst_milestones + 1))
4. award_xp(user_id, xp_to_award, reason='burst_completion')
5. UPDATE user_missions SET burst_count = burst_milestones, burst_locked = TRUE
6. UPSERT mission_xp_records (= leaderboard)
7. db.commit()
```

### Anti-Buffer lock (Buffer ⊕ Burst exclusion)

At the **first** `burst-claim` on a mission, `burst_locked = TRUE` is set irreversibly. The API now refuses any attempt to `apply_buffer` on this mission (`409 burst_locked`).

Before the 1st burst-claim, the user can still choose to Buffer (= the unlocked Burst milestones are "lost", target increases, Burst mechanic restarts). **Tactical choice**: defer (Buffer) or challenge yourself (Burst), not both.

### UI

A new progress bar appears silently above the current bar. At each milestone reached, "Burst Milestone N — XP × 2^N claimable" appears. User can claim each milestone individually or in aggregate.

---

## Burst Leaderboard

### Table

**`mission_xp_records`** — created (replaces `stonks_records`)

```sql
id              UUID PRIMARY KEY DEFAULT gen_random_uuid()
user_id         UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE
mission_id      UUID NOT NULL REFERENCES missions(id) ON DELETE RESTRICT
user_mission_id UUID NOT NULL UNIQUE REFERENCES user_missions(id) ON DELETE RESTRICT
xp_earned       NUMERIC NOT NULL
burst_count     INT NOT NULL
buffer_count    INT NOT NULL DEFAULT 0
recorded_at     TIMESTAMPTZ NOT NULL DEFAULT now()

CREATE INDEX ix_mxr_user_month ON mission_xp_records (user_id, date_trunc('month', recorded_at));
CREATE INDEX ix_mxr_xp_alltime ON mission_xp_records (xp_earned DESC);
```

`UNIQUE (user_mission_id)`: 1 record per completed mission. Re-claiming additional Burst milestones = in-place UPDATE (no new INSERT).

### Endpoints

#### `GET /api/v1/gamification/leaderboard/burst-monthly`

Auth: JWT user

Response:
```json
{
  "month": "2026-05",
  "top_50": [
    {"user_id": "uuid", "username": "alice", "max_xp": "65536", "burst_count": 16, "mission_name": "Scanner X étiquettes (hard)"}
  ],
  "your_rank": 23,
  "your_max_xp": "4096"
}
```

#### `GET /api/v1/gamification/leaderboard/burst-alltime`

Same but over the full history.

**Note**: `xp_earned` / `max_xp` returned as `string` — too large for a standard JSON `number` (float64 overflow).

### Drop obsolete table

```sql
DROP TABLE stonks_records;
```

Pre-prod, non-critical loss. The "monthly Stonks" leaderboard is replaced by `mission_xp_records` filtered on `date_trunc('month', recorded_at)`.

### Why monthly + all-time?

- **Monthly**: continuous engagement, reset each month → new players are never left behind by early-adopters.
- **All-time**: absolute prestige — the record of the hardcore farmer who reached `n_burst = 18` stays in the hall of fame forever, even if never beaten again. Killer feature for competitive culture.

### Contest mode (future)

Parameter in `ratis_settings.json["burst_contest"]`:
```json
{
  "active": false,
  "prize_description": "",
  "ends_at": null
}
```

When `active=true`: the app displays the leaderboard prominently, contest banner. No business logic changes.

---

## Mission freeze

### Principle

Spend CABs to freeze an active mission and defer it to the next period.

### `user_missions` table change

```sql
frozen_until    TIMESTAMPTZ  -- NULL if not frozen
freeze_count    INT NOT NULL DEFAULT 0  -- max 1 per period
```

### Endpoint

#### `POST /api/v1/rewards/missions/{mission_id}/freeze`

Auth: JWT

Response:
```json
{
  "frozen_until": "2026-05-01T00:00:00Z",
  "cost_paid_cab": 100
}
```

Error codes:

| Code | HTTP | Condition |
|---|---|---|
| `mission_not_found` | 404 | |
| `mission_already_frozen` | 409 | `frozen_until IS NOT NULL` |
| `freeze_limit_reached` | 409 | `freeze_count >= 1` for this period |
| `insufficient_cab_balance` | 422 | |

### `freeze_mission(db, user_id, mission_id)` logic

```
1. Check status = 'active', frozen_until IS NULL, freeze_count < 1
2. cost = ratis_settings["gamification"]["freeze_cost_cab"]
3. debit_cab(db, user_id, cost, reason='mission_freeze')
4. UPDATE user_missions SET
     frozen_until = date_trunc('month', now()) + INTERVAL '1 month',
     freeze_count = freeze_count + 1
```

---

## Inter-services

| Direction | Service | Trigger |
|---|---|---|
| `ratis_product_analyser` → hash | SHA-256 computation + INSERT scan | On each photo upload |
| `ratis_product_analyser` → XP | `award_xp` via `ratis_core.rewards_client` | Scan `accepted` |
| `ratis_rewards` → XP | `award_xp` local | Mission claim, battlepass, referral |
| `ratis_batch_purge` | Release pending hashes > 1h | Existing purge cycle |

---

## Parameters

Add to `ratis_settings.json`:

```json
"xp": {
  "level_base": 100,
  "receipt_scan": 10,
  "label_scan": 8,
  "barcode_scan": 5,
  "price_compared": 3,
  "mission_completed": 10,
  "battlepass_milestone": 20,
  "referral": 50,
  "feed_jack": 5
},
"gamification": {
  "freeze_cost_cab": 100,
  "burst_contest": {
    "active": false,
    "prize_description": "",
    "ends_at": null
  }
},
"buffer": {
  "n_max_daily": 3,
  "weekly_allowed": false,
  "notif_lead_time_hours": 1
},
"burst": {
  "cap_n_max": null,
  "leaderboard_top_size": 50
}
```

> ℹ️ The `buffer` / `burst` sections are introduced by the 2026-05-09 overhaul. Tunable via admin UI (future) — see spec `docs/superpowers/specs/2026-05-09-buffer-burst-design.md` § Tunable params.

---

## Rules

- `xp_reward` and `xp_earned` stored as `NUMERIC` without precision — supports arbitrarily large values (`2^200+`)
- `xp_earned` returned as `string` in JSON responses — never as `number` (float64 overflow)
- `award_xp`: `amount` always positive, never XP debit
- Photo hash computed on raw bytes before R2 upload — never after
- Hash released by the worker on `rejected/error`, by `ratis_batch_purge` on `pending > 1h`
- `mission_xp_records`: 1 row per `user_mission_id` (UNIQUE), in-place UPDATE on re-claiming additional Burst milestones — feeds monthly + all-time leaderboards
- Buffer is free (0 CAB), Burst gives 0 additional CAB — Buffer/Burst are mutually exclusive at the 1st burst-claim (irreversible anti-Buffer lock)
- Buffer not possible on `frequency='weekly'` (the 7-day window is already sufficient)

---

## Feed Jack — daily streak

> ⚠️ **REWORK REQUIRED — decision 2026-04-20**
> The current trigger (tap on Jack = app opening) is insufficient for retention.
> **New rule:** Jack is fed only when the user completes **at least one mission of the day**.
> Rationale: missions define "what to do today" → Jack materialises "did I play today?".
> Same model as Duolingo (the lesson counts, not launching the app).
> Impact: `POST /streak/feed` must be called by the `claim_mission` flow (no longer exposed directly to the client as a tap). To revisit during frontend dashboard implementation.

### Principle

- The user completes at least one mission of the day → Jack is fed automatically (UI defined in `ratis_client/ARCH_feed_jack.md`)
- Each consecutive day → +5% on **all** CAB and XP earnings of the day
- Multiplier: `LEAST(current_streak_days × 0.05, 1.0)` → capped at +100% after 20 days
- Break: one day without feed → streak resets to 0, multiplier = 0
- Protection: **food reserves** purchased in CABs → freeze the streak for 1 day
- **Auto-freeze**: if missed days ≤ available reserves → consume N reserves silently, streak continues. See DA-09.
- **Manual repair**: only if `food_reserves = 0` AND exactly 1 day missed → `needs_repair: true` on reconnection. Cost: `food_reserve_cost_cab` CABs debited directly via `POST /streak/repair`. See DA-09, DA-10.
- **Broken streak**: gap ≥ 2 days without coverage → streak = 0, no repair.

### Table `user_streaks` — created

| Column | Type | Constraint |
|---|---|---|
| `user_id` | UUID | PK, FK `users.id` ON DELETE CASCADE |
| `current_streak_days` | INTEGER | NOT NULL DEFAULT 0 |
| `last_fed_at` | DATE | NULL (NULL = never fed) |
| `food_reserves` | INTEGER | NOT NULL DEFAULT 0 CHECK (food_reserves >= 0) |
| `timezone` | TEXT | NOT NULL DEFAULT 'Europe/Paris' |

> `timezone` = IANA timezone string (e.g.: `Europe/Paris`, `America/New_York`). Sent by the client on the first feed or when it changes. Used for all `gap_days` computations server-side. Validated at entry via `zoneinfo.available_timezones()`. See DA-11.
>
> The multiplier is not stored — it is derived: `LEAST(current_streak_days * 0.05, 1.0)`. No computed column: the read is done in Python at each `award_xp` / `award_cab`.

### Endpoints

#### `POST /api/v1/rewards/streak/feed`

Auth required. **Idempotent**: calling multiple times the same day → returns the current state without re-awarding XP.

Body (optional):
```json
{ "timezone": "Europe/Paris" }
```
If provided → updates `user_streaks.timezone`. If absent → uses the stored timezone. Validated as an IANA string.

`feed_jack(db, user_id, timezone=None)` logic:

1. `UPSERT user_streaks` (create if absent)
2. If `last_fed_at = today` → return state without change
3. If `last_fed_at = yesterday` → streak continues: `current_streak_days += 1`
4. Otherwise (`gap_days = (today - last_fed_at).days - 1 > 0`) → missed days detected:
   - If `gap_days <= food_reserves` → **auto-freeze**: `food_reserves -= gap_days`, streak += 1 (continues)
   - If `gap_days == 1` AND `food_reserves == 0` → **repair proposed**: do not modify streak, return `needs_repair: true`. The user then triggers `POST /streak/repair` (cost = `food_reserve_cost_cab` CABs, see DA-10).
   - Otherwise (`gap_days > food_reserves` OR `gap_days >= 2` without coverage) → **broken streak**: `current_streak_days = 1`, `food_reserves = max(0, food_reserves - gap_days)` (or 0)
5. `last_fed_at = today`
6. `award_xp(db, user_id, cfg["xp"]["xp_per_feed_jack"], "feed_jack", reference_id=user_id, reference_type="user")`
   - ⚠️ The streak multiplier applies here too (consistency)
7. Return `StreakState`

```json
// Response
{
  "streak_days": 7,
  "multiplier": 0.35,
  "food_reserves": 2,
  "xp_earned": 7,
  "already_fed_today": false
}
```

Error codes: `200 OK` (nominal), `401` (not authenticated).

#### `POST /api/v1/rewards/streak/purchase-reserve`

Purchases N food reserves by debiting CABs.

```json
// Body
{ "quantity": 1 }
```

Logic:
1. Check `food_reserves + quantity <= cfg["gamification"]["feed_jack"]["max_food_reserves"]`
2. Debit `quantity × cfg["gamification"]["feed_jack"]["food_reserve_cost_cab"]` CABs (atomic UPDATE)
3. `user_streaks.food_reserves += quantity`

Error codes: `402 insufficient_cabs`, `422 max_reserves_reached`.

#### `POST /api/v1/rewards/streak/repair`

Emergency repair, only available if `needs_repair: true` (gap = 1 day, 0 reserves).

Logic:
1. Check `needs_repair` (gap == 1 AND food_reserves == 0) — otherwise `422 repair_not_available`
2. Debit `food_reserve_cost_cab` CABs (atomic UPDATE, error if insufficient balance → `402 insufficient_cabs`)
3. `current_streak_days += 1`, `last_fed_at = today`
4. Award XP `feed_jack` (the repair counts as a feed)
5. Return updated `StreakState`

#### `GET /api/v1/rewards/streak`

Returns the current streak state. No side-effect.

### Multiplier application in `award_xp` and `award_cab`

At each `award_xp(db, user_id, amount, reason, ...)` and `award_cab(db, user_id, amount, reason, ...)` call:

```python
streak = db.get(UserStreak, user_id)
multiplier = min((streak.current_streak_days if streak else 0) * 0.05, 1.0)
final_amount = round(amount * (1 + multiplier))
```

> The `db.get` is free if the object is already in session (identity map). No additional SELECT in the nominal case.

### Feed Jack Checklist

- [x] Migration: table `user_streaks`
- [x] SQLAlchemy model `UserStreak`
- [x] `feed_jack(db, user_id)` in `streak_repository.py`
- [x] Multiplier in `award_xp` and `award_cab`
- [x] Endpoint `POST /streak/feed` + `POST /streak/purchase-reserve` + `GET /streak` + `POST /streak/repair`
- [x] Full TDD tests (26 tests)
- [x] Catch-up decision enacted — **manual on reconnection** (DA-09)
- [ ] `ratis_settings.json` — prod values validated (see `PROD_CHECKLIST.md`)

---

## Community Challenge

> Status: **design in progress** — pending decisions listed below.

### Principle

The whole community contributes together toward a shared objective. When milestones are reached, rewards are unlocked for everyone. The big final reward includes a temporary multiplier.

The mechanism creates collective engagement and organic word-of-mouth ("we're at 80%, tell your friends to scan").

### Enacted decisions

| Decision | Value |
|---|---|
| Progression scope | All users combined (community-wide) |
| Duration | Variable: 1 week (small challenge) or 1 month (big challenge) — defined per challenge |
| Triggering action | Configurable: `receipt_scan`, `label_scan`, `feed_jack`, `referral`, etc. — with optional filter |
| Reward structure | Progressive milestones (claim required) + big final reward + configurable multiplier |
| Reward types | CABs, XP, `skin` (generic type covering badge / profile skin / mascot / banner / etc.), temporary multiplier |
| Multiplier scope | Configurable per challenge: `'cab'` / `'xp'` / `'both'` — in `reward_value.applies_to` |
| Simultaneous challenges | Only one active at a time — partial unique constraint in DB |
| Milestone claim | Manual (user must claim) — no attribution to absent users |
| Challenge end | `ends_at` = close of progression. `grace_period_days` (configurable) = claim window post-end. After grace: claims expired. |
| Objective not reached | Already-unlocked milestones remain claimable until the end of the grace period |
| Skins / badges / cosmetics | New system — to be designed separately (see PROD_CHECKLIST) |

### Challenge lifecycle

```
[is_active=TRUE]
     │
     ├── now() < ends_at          → ACTIVE    (progression + claims)
     ├── ends_at ≤ now() < ends_at + grace_period_days  → FROZEN (progression stopped, claims ok)
     └── now() ≥ ends_at + grace_period_days            → EXPIRED (claims closed)
```

Status is **computed in Python** from `ends_at` and `grace_period_days` — no `status` column in DB.

### Data model

**`community_challenges`**

```sql
id                  UUID PRIMARY KEY
title               TEXT NOT NULL
description         TEXT
action_type         TEXT NOT NULL  -- 'receipt_scan' | 'label_scan' | 'feed_jack' | 'referral' | ...
action_filter       JSONB          -- NULL = any action of the type; e.g.: {"category": "toys"}
objective           INT NOT NULL   -- target number of community actions
starts_at           TIMESTAMPTZ NOT NULL
ends_at             TIMESTAMPTZ NOT NULL
grace_period_days   INT NOT NULL DEFAULT 3   -- claim days post-end
is_active           BOOLEAN NOT NULL DEFAULT FALSE
created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
```

```sql
-- Only one active challenge at a time
CREATE UNIQUE INDEX community_challenges_one_active
ON community_challenges (is_active)
WHERE is_active = TRUE;
```

**`community_challenge_milestones`**

```sql
id              UUID PRIMARY KEY
challenge_id    UUID NOT NULL REFERENCES community_challenges(id) ON DELETE CASCADE
threshold       INT NOT NULL     -- milestone in number of community actions
reward_type     TEXT NOT NULL    -- 'cab' | 'xp' | 'skin' | 'multiplier'
                                 -- 'skin' = generic type: badge, profile skin, mascot, banner…
                                 --          resolved by the cosmetics system (to design) via item_id
reward_value    JSONB NOT NULL
-- reward_value examples:
--   CABs          : {"amount": 500}
--   XP            : {"amount": 200}
--   Multiplier    : {"multiplier": 0.5, "duration_hours": 48, "applies_to": "both"}
--   Skin          : {"item_id": "skin_winter_2026"}   ← covers badge, profile skin, mascot, banner, etc.
label           TEXT             -- "Milestone 1 — 500 scans!"
sort_order      INT NOT NULL
```

**`community_challenge_progress`**

```sql
challenge_id    UUID PRIMARY KEY REFERENCES community_challenges(id) ON DELETE CASCADE
current_count   INT NOT NULL DEFAULT 0
last_updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
```

> A single row per challenge. Incremented atomically:
> `UPDATE community_challenge_progress SET current_count = current_count + 1 WHERE challenge_id = :cid`

**`community_challenge_claims`**

```sql
id              UUID PRIMARY KEY DEFAULT gen_random_uuid()
challenge_id    UUID NOT NULL REFERENCES community_challenges(id)
milestone_id    UUID NOT NULL REFERENCES community_challenge_milestones(id)
user_id         UUID NOT NULL REFERENCES users(id)
claimed_at      TIMESTAMPTZ NOT NULL DEFAULT now()
UNIQUE (milestone_id, user_id)
```

**`community_multipliers`** *(created on multiplier milestone claim)*

```sql
id              UUID PRIMARY KEY DEFAULT gen_random_uuid()
challenge_id    UUID NOT NULL REFERENCES community_challenges(id)
user_id         UUID NOT NULL REFERENCES users(id)
multiplier      NUMERIC NOT NULL        -- e.g.: 0.5 = +50%
applies_to      TEXT NOT NULL           -- 'cab' | 'xp' | 'both'
active_from     TIMESTAMPTZ NOT NULL
active_until    TIMESTAMPTZ NOT NULL
UNIQUE (challenge_id, user_id)          -- one multiplier per challenge per user
```

> `award_cab` and `award_xp` check `community_multipliers` (same pattern as `get_streak_multiplier`) and apply the active multiplier if `active_from <= now() < active_until`.

### Integration into existing flows

Call to `increment_challenge_progress(db, action_type, context)` added in existing routes, after the main flow commit, only if an active challenge matches the `action_type`.

```python
# Example in routes/rewards/events.py (scan_accepted)
with db_transaction(db):
    award_xp(db, user_id, amount, "receipt_scan")
    award_cab(db, user_id, amount, "receipt_scan")
    maybe_increment_challenge(db, action_type="receipt_scan")
```

`maybe_increment_challenge`: checks if an active challenge exists for `action_type` (+ optional filter), increments `current_count` if yes, no-op otherwise. Minimal cost — 1 SELECT + 1 conditional UPDATE.

> **`action_filter`**: if the challenge has `action_filter = {"category": "toys"}`, the context passed to `maybe_increment_challenge` must include the category of the scanned product. Each `action_type` defines which context fields are available for the filter.

### Community Challenge Checklist

- [x] TDD tests `test_challenge.py` (24 tests)
- [x] Migration: `community_challenges`, `community_challenge_milestones`, `community_challenge_progress`, `community_challenge_claims`, `community_multipliers` — revision `i2j3k4l5m6n7`
- [x] SQLAlchemy models (`CommunityChallenge`, `CommunityChallengeMilestone`, `CommunityChallengeProgress`, `CommunityChallengeClaim`, `CommunityMultiplier`)
- [x] `challenge_repository.py`: `get_active_challenge_with_state`, `maybe_increment_challenge`, `get_active_community_multiplier`, `claim_milestone`, `_apply_reward`
- [x] Community multiplier in `award_cab` and `award_xp` (lazy import to avoid circular)
- [x] `maybe_increment_challenge` hooked in: `handle_scan_accepted` (via `cab_service`), `streak.py` (feed_jack), `referral.py`
- [x] Endpoint `GET /gamification/challenge` — current state + milestones + progression
- [x] Endpoint `POST /gamification/challenge/milestones/{milestone_id}/claim`
- [x] Status logic (ACTIVE / FROZEN / EXPIRED) in `get_active_challenge_with_state`
- [x] `ratis_settings.json` — no global parameters (all per-challenge in DB)

### Deferred V1.x — CommunityChallenge scaffolding

> Audit status: **complete scaffolding, dormant feature** — no challenge created/activated in prod, no client screen (`ratis_client/` does not reference `challenge`). User decision 2026-05-10 (code health audit PR #364, item F-4): **keep scaffolding, defer user-facing surface to V1.x**.

**What is shipped (pre-positioned, do not touch)**:

- 5 SQLAlchemy models in `ratis_core/ratis_core/models/gamification.py`:
  `CommunityChallenge`, `CommunityChallengeMilestone`, `CommunityChallengeProgress`, `CommunityChallengeClaim`, `CommunityMultiplier`
- 5 tables shipped via migration `alembic/versions/20260414_1600_i2j3k4l5m6n7_community_challenges.py`
- CHECK constraint `cabecoin_transactions.reference_type` extends the enum to accept `'community_challenge_milestone'` (never inserted to date)
- Repository `webservices/ratis_rewards/repositories/challenge_repository.py` (594 lines) — `get_active_challenge_with_state`, `maybe_increment_challenge`, `claim_milestone`, etc.
- User routes `webservices/ratis_rewards/routes/gamification/challenge.py`: `GET /gamification/challenge` + `POST /gamification/challenge/milestones/{id}/claim`
- Admin routes `webservices/ratis_rewards/routes/admin/challenges.py`: list/create/activate/deactivate/add-milestone
- Hooks `maybe_increment_challenge` in `events_service.handle_scan_accepted`, `routes/gamification/streak.py` (feed_jack), `services/referral_service.py` (no-op as long as no challenge `is_active=TRUE` — conditional SELECT cost)
- TDD tests `test_challenge.py` (24 tests) + `test_admin_challenges.py` (green in CI)

**Scaffolding justification (no drop)**:

- Product concept enacted (see enacted decisions section above + `PRODUCT.md` § Gamification)
- Fully TDD-validated infrastructure → zero marginal cost to keep it
- Drop = loss of work + future re-implementation costs 5-10× more than keeping it
- `maybe_increment_challenge` hooks are silent no-ops as long as no active challenge → zero runtime impact

**To implement in V1.x (when user-facing priority arrives)**:

- Front: screens + React Query hooks (`use-challenge.ts`, `use-claim-milestone.ts`)
- Front: i18n `challenge.*` in `CL/locales/fr.json`
- Cosmetics system (`reward_type='skin'` depends on a badge/skin/mascot system to design separately, see PROD_CHECKLIST)
- Admin back-office: UI to create/activate a challenge (admin route exists, UI is missing)
- First launch challenge (product content: title/description/objective/milestones)

> ⚠️ **While this section is marked "Deferred V1.x"**: do not re-test or re-touch the infra. If a bug surfaces during audit/maintenance, fix in-place and re-mark as deferred. Do not uncheck `[ ]` the checklist items above — they are correctly `[x]` (= shipped) — dormancy is on product usage, not on code.

---

## Design — Donut ROI (client dashboard)

> Purely frontend logic — computed from `total_savings` returned by the API. No dedicated endpoint.

### Ring mechanic (decision 2026-04-20)

- **Unit**: 1 ring = €7.99 saved (= 1 subscription reimbursed)
- **Fixed denominator**: `total_lifetime_savings / 7.99` — never decreasing, never impacted by renewals
- **Display**: "You have reimbursed **1.6 subscriptions**" (not a %)
- **Fossils**: completed rings → remain visible, ultra-thin (1.5 px stroke), golden and increasingly dark toward the outside
- **Prestige**: at the 10th ring (€79.90 cumulative)
  - All 10 fossils shatter simultaneously (shockwave + shards + flash)
  - New Prestige ring: iridescent (CSS hue-rotate), cracked (SVG feTurbulence + feDisplacementMap), scaled (multiple layers), rotating mobile reflection
  - Badge **★I**, **★II**, **★III**… in the center of the donut
- **Infinite**: at the 10th prestige (100 rings = €799 saved)
  - The circular gauge transforms into an **∞ symbol** (Bernoulli lemniscate, SVG path + stroke-dashoffset)
  - Each full loop of the ∞ = €79.90 additional (10 virtual rings)
  - Prestige visuals preserved (iridescent, cracked, scaled) on the lemniscate

### Break interaction

- Ring at 100% → golden pulse + "👆 Break the limit!"
- Tap → cracks + shards + flash + shockwave → fossil forms + new ring spawns (spring animation)
- Prestige → × 10 simultaneous shattering, more dramatic (12 rays, double wave)

### Feed Jack ↔ Missions (decision 2026-04-20)

- Jack is fed only when the user completes ≥ 1 mission of the day
- `POST /streak/feed` triggered from `claim_mission` — no longer exposed directly to the client as a tap
- Jack UI = passive indicator (satiety = "did I play today?")

---

## Achievements V1 (implemented ✅)

> **Status**: ✅ Implemented — backend + frontend shipped. See the full design in `docs/superpowers/specs/2026-05-09-achievements-v1-design.md` and the plan in `docs/superpowers/plans/2026-05-09-achievements-v1.md`.

### CAB grid by rarity (enacted 2026-05-08)

| Rarity | CAB reward |
|---|---|
| Terracotta | 20 |
| Bronze | 30 |
| Copper | 40 |
| Silver | 50 |
| Gold | 100 |
| Emerald | 150 |
| Sapphire | 250 |
| Ruby | 500 |
| Crystal | 750 |
| Diamond | 1200 |

10 rarity tiers, approximately geometric curve on the low end (×1.5 per tier) then exponential on the high end. The Diamond tier (1200 CAB) is calibrated to stay below the DAS2 fiscal cap if a user unlocks all Diamonds in a row (= very long-term).

### Existing sources (frontend)

- **56 ideas** in `ratis_client/Ratis_handoff/lib/ratis-achievements-data.jsx` — complete product backlog (design reference only, no longer consulted at runtime)
- **23 seed entries** in `alembic/versions/20260510_1030_seed_achievements_v1.py` — current catalogue
- **V1 hardcoded mock** in `ratis_client/components/profil/achievements-data.ts` → @deprecated, kept as storybook fallback; live screens consume `useAchievements()` since PR #360

### V1 implementation (8 PRs, merged 2026-05-09)

- [x] **PR1 — Schema**: table `achievements` (catalogue) + `user_achievements` (instances) + 3 ENUMs + 23 seed + KP-08 sync (`achievement_unlock` reason)
- [x] **PR2 — Service**: `achievement_service.check_achievements(user_id, event_type, payload)` dispatcher + 9 trigger handlers + `_unlock` (transactional INSERT + grant CAB)
- [x] **PR3 — Notifications**: `notify_achievement_unlocked` with gradient by rarity (toast/modal/push/bespoke)
- [x] **PR4 — Hooks**: `check_achievements()` wired in 5 services (`cab_service.handle_scan_accepted`, `cashback_service.detect_cashback`, `streak_service`, `referral_service.handle_subscription_referral`, `events_service`)
- [x] **PR5 — User API**: 3 endpoints (`GET /rewards/achievements`, `GET /rewards/achievements/{id}`, `POST /rewards/achievements/secret-event` rate-limited 10/h) + serializer (secret/hidden/limited-time + `j_y_etais` override)
- [x] **PR6 — Admin API**: 5 endpoints (CRUD catalogue + manual grant) + audit log + immutability after unlock
- [x] **PR7 — Batch nightly**: `ratis_batch_achievements` (cron 3:15 UTC) — `savings_eur_in_window` windows + general safety net
- [x] **PR8 — Frontend**: `useAchievements()` + `triggerSecretEvent()` + extract toast (`<AchievementUnlockToast />`) + new modal (`<AchievementCelebrationModal />` for emerald+) + bespoke registry (`r_365` + `sec_konami` Diamonds) + un-grey profile section + 3am hook in `app/_layout.tsx` + `useKonamiCode` (gesture wiring V1.1)

### Next — V1.1 backlog

- Wire `useKonamiCode` to a global gesture overlay (swipes ↑↑↓↓ + A/B tap-zones) — PR8 ships the hook + the contract, gesture detection to wire
- Polish of the 2 bespokes (Lottie for `r_365`, CRT scanline for `sec_konami`)
- Non-null `progress` field in the serializer (V1 returns `null` — the X/Y bar awaits this signal)
- Real-time push on FE side (to wire with `expo-notifications` + the overlay: `dispatchAchievementUnlocked(payload)` is already the canonical entrypoint)
- **`unique_products_discovered_count` (achievement `exp_unknown_10`)** — `webservices/ratis_rewards/services/achievement_service.py:236`. Requires either a `products.first_discovered_by_user_id` column or an analytical materialized view. Achievement seeded but only unlocks via batch path until one of the two solutions is shipped.

---

## Tirelires & Prestiges (planned)

> **Status**: 📋 Planned — 2 economic mechanics **enacted 2026-05-08** but not yet implemented. Detailed design to be done in a future workstream (post-Boutique V1).

### Break the tirelire

- **Trigger**: every **€8** of cumulative savings (= 1× the monthly subscription price)
- **Reward**: **100 CAB** per tirelire broken
- **Source field**: on update of `users.savings_eur` (or similar column to design — can be a derivative of the cumulative `cashback_transactions.amount` net + price comparisons won)
- **Logic**: counter `tirelires_cassees = floor(savings_eur / 8)`. On each floor increment → award 100 CAB + INSERT `cabecoin_transactions(reason='tirelire_milestone')`

### Prestige

- **Trigger**: every **€80** of cumulative savings (= 10× the monthly subscription price)
- **Reward**: **300 CAB** per prestige
- **Logic**: counter `prestiges = floor(savings_eur / 80)`. On each floor increment → award 300 CAB + INSERT `cabecoin_transactions(reason='prestige_milestone')` + dramatic UI animation (see § Design — Donut ROI § Break interaction, "Prestige → × 10 simultaneous shattering")

### Future plan

1. Define the source-of-truth for `users.savings_eur` (denormalized column? nightly batch? Postgres trigger?)
2. Migration: add columns `tirelires_cassees_count INT NOT NULL DEFAULT 0` + `prestiges_count INT NOT NULL DEFAULT 0` on `users`
3. Sync `_CAB_REASONS` (KP-08 — 3 places) to add `'tirelire_milestone'` and `'prestige_milestone'`
4. Service: `economy_service.check_savings_milestones(user_id)` called in the fire-and-forget pattern after each mutation of CAB earns or `cashback_transactions`
5. UI: integrate into the client dashboard donut ROI (existing — see § Design — Donut ROI), add visible counters to profile

---

## Out of scope

- Burst leaderboard titles and badges — UI/frontend, no specific backend logic
- CAB sink mini-games — V2, see `FUTUR.md`
- Temporary premium access in exchange for CABs — V1 but separate ARCH
- Mystery product, Leagues, Community Challenge, Friends outings — see `FUTUR.md` § Gamification
