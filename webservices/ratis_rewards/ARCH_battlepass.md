---
type: sub-arch
service: ratis_rewards
parent: ARCH_REWARDS
related: [ARCH_cab, ARCH_missions, ARCH_gift_cards]
status: production
tags: [battlepass, rewards, seasons, xp, gamification]
updated: 2026-05-08
---

# ratis_rewards — ARCH Battle Pass

> Seasonal Battle Pass: `battlepass_seasons` + `battlepass_milestones` + `user_battlepass_progress` + `user_battlepass_claims`. XP + CAB + gift-card rewards per milestone. Rolling season.
> @tags: battlepass rewards seasons xp gamification milestones claims progress
> @status: LIVRÉ V0
> @subs: auto

> Parent: [[ARCH_REWARDS]] · Related: [[ARCH_cab]], [[ARCH_missions]], [[ARCH_gift_cards]]

> Status: ✅ Implemented — seasonal battle pass
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
- [x] Milestone status computed dynamically (locked/unlocked/claimed)
- [x] Partial unique index uq_one_active_season (only one active season)
- [x] INSERT user_battlepass_claims + award_cab in the same transaction
- [x] subscriber_only per milestone (no global toggle)

> ⚠️ One item at a time. Do not move to the next before finishing the current one.

---

## Index

- [Context](#context) [L.43 - L.47]
- [Parent reference](#parent-reference)
- [Endpoints](#endpoints) [L.61 - L.107]
- [Tables](#tables) [L.109 - L.158]
- [Rules](#rules) [L.160 - L.167]

---

## Context

Read `ratis_core` and `ARCH_cab.md` before starting. `award_cab` must exist. Strict TDD. Branch `feature/rewards-battlepass`.

---

## Parent Reference

This ARCH is a sub-domain of `webservices/ratis_rewards/ARCH_REWARDS.md` (global rewards service). For cross-cutting rules (CAB economy, anti-double-spend, balance materialized), refer to the parent ARCH.

Tables owned by this sub-domain: `battlepass_seasons`, `battlepass_milestones`, `user_battlepass_progress`, `user_battlepass_claims`.

Endpoints: `GET /rewards/battlepass`, `POST /rewards/battlepass/claim/{milestone_id}` (see `ENDPOINTS.md`).

---

## Endpoints

```
GET  /rewards/battlepass
POST /rewards/battlepass/claim/{milestone_id}
```

### `GET /rewards/battlepass`
```json
{
  "season": { "id": "uuid", "name": "Saison 1", "ends_at": "2026-06-30T00:00:00Z" },
  "cab_earned_season": 340,
  "milestones": [
    {
      "id": "uuid",
      "milestone_number": 1,
      "cab_required": 200,
      "reward_type": "cab",
      "reward_value": 100,
      "subscriber_only": false,
      "status": "claimed"
    },
    {
      "id": "uuid",
      "milestone_number": 2,
      "cab_required": 500,
      "reward_type": "gift_card",
      "reward_value": 500,
      "subscriber_only": true,
      "status": "unlocked"
    },
    {
      "id": "uuid",
      "milestone_number": 3,
      "cab_required": 1000,
      "reward_type": "gift_card",
      "reward_value": 1000,
      "subscriber_only": true,
      "status": "locked"
    }
  ]
}
```

Status computed dynamically — no `status` column in the database:
- `locked` → `cab_earned_season < cab_required`
- `unlocked` → `cab_earned_season >= cab_required` AND no entry in `user_battlepass_claims`
- `claimed` → entry exists in `user_battlepass_claims`

### `POST /rewards/battlepass/claim/{milestone_id}`

```
1. Verify that the milestone belongs to the active season
2. Verify status == unlocked
3. If subscriber_only = true → verify active subscription → 403 subscriber_required otherwise
4. INSERT user_battlepass_claims
5. If reward_type = 'cab' → award_cab(user_id, reward_value, 'battlepass_milestone')  ← same transaction
6. If reward_type = 'gift_card' → delivery out of V1
```

Error codes: `tier_not_found`, `tier_locked`, `tier_already_claimed`, `subscriber_required`.

Returns:
```json
{ "claimed": true, "reward_type": "cab", "reward_value": 100, "new_cab_balance": 1340 }
```

---

## Tables

**`battlepass_seasons`**
```sql
id            UUID PRIMARY KEY DEFAULT gen_random_uuid()
season_number INT NOT NULL UNIQUE
name          TEXT NOT NULL
started_at    TIMESTAMPTZ NOT NULL
ends_at       TIMESTAMPTZ NOT NULL
is_active     BOOLEAN NOT NULL DEFAULT FALSE
-- CREATE UNIQUE INDEX uq_one_active_season ON battlepass_seasons (is_active) WHERE is_active = TRUE
```

**`battlepass_milestones`**
```sql
id              UUID PRIMARY KEY DEFAULT gen_random_uuid()
season_id       UUID NOT NULL REFERENCES battlepass_seasons(id) ON DELETE RESTRICT
milestone_number     INT NOT NULL
cab_required    INT NOT NULL
reward_type     TEXT NOT NULL CHECK (reward_type IN ('cab', 'gift_card', 'skin'))
reward_value    INT NOT NULL
subscriber_only BOOLEAN NOT NULL DEFAULT FALSE
UNIQUE (season_id, milestone_number)
```

**`user_battlepass_progress`**
```sql
id                UUID PRIMARY KEY DEFAULT gen_random_uuid()
user_id           UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT
season_id         UUID NOT NULL REFERENCES battlepass_seasons(id) ON DELETE RESTRICT
cab_earned_season INT NOT NULL DEFAULT 0
updated_at        TIMESTAMPTZ NOT NULL DEFAULT now()
UNIQUE (user_id, season_id)
-- PostgreSQL trigger ON UPDATE for updated_at
```
Updated in the same transaction as `award_cab` via INSERT ON CONFLICT DO UPDATE.

**`user_battlepass_claims`**
```sql
id         UUID PRIMARY KEY DEFAULT gen_random_uuid()
user_id    UUID REFERENCES users(id) ON DELETE SET NULL
milestone_id    UUID NOT NULL REFERENCES battlepass_milestones(id) ON DELETE RESTRICT
claimed_at TIMESTAMPTZ NOT NULL DEFAULT now()
UNIQUE (user_id, milestone_id)
```

---

## Rules

- Milestone status computed dynamically — no `status` column in the database
- INSERT `user_battlepass_claims` + `award_cab` in the same transaction
- Only one active season — partial index `WHERE is_active = TRUE`
- `reward_type = 'skin'` in the CHECK — V2, no milestone uses it in V1
- `subscriber_only` per milestone — no global toggle in `ratis_settings.json`

---

## Season 1 (seeded 2026-05-08)

> **PR #326 merged** — seed `ratis_core/seed/bp_season_1.py` loads the first battle pass season in production.

### Structure

- **30 milestones** in total
- **Mid (milestone 15)** = 10 000 CAB required → `reward_type='gift_card'`, `reward_value=500` (5€)
- **End (milestone 30)** = 40 000 CAB required → `reward_type='gift_card'`, `reward_value=2000` (20€)
- **`cab_required` curve pattern** — 2 exponential segments:
  - Segment 1 (milestones 1→15): ratio ×1.46/milestone
  - Segment 2 (milestones 16→30): ratio ×1.10/milestone
- **Initial `reward_value` pattern** — linear curve (milestone 1 = 20 CAB, milestone 29 = 2897 CAB, total ~40k CAB redistributed across `reward_type='cab'` milestones)

See `ratis_core/seed/bp_season_1.py` for the full 30-row grid (cab_required + reward_type + reward_value per milestone_number).

### ⚠️ reward_value pattern — pending "waves" rework

The initial linear pattern is considered too regular. A rework is planned towards a **"waves" pattern**:
- Segment 1 peaks = 250 CAB, segment 2 peaks = 400 CAB
- Valleys 50-100 CAB
- Total ~5100 CAB redistributed (instead of the original ~40k — recalibration)

A chip was dispatched on 2026-05-08 ("Update BP Saison 1 reward_value (waves FINAL)") to replace the `reward_value` grid once the waves curve is definitively calibrated. No migration needed — just UPDATE the `battlepass_milestones` rows from the Season 1 seed.

### ⚠️ Known arch bug — `award_cab(reason='battlepass_milestone')` auto-feeding

**Symptom**: when a BP milestone of type `reward_type='cab'` is claimed, the code calls `award_cab(user_id, reward_value, reason='battlepass_milestone')` which:
1. INSERT `cabecoin_transactions` (correct)
2. UPDATE `user_cab_balance` (correct)
3. **UPDATE `user_battlepass_progress.cab_earned_season += reward_value`** ← the bug

The 3rd effect increments the BP progress which … potentially unlocks the next milestone automatically. This is an auto-feeding loop (BP CAB rewards count as CAB earns for the BP itself).

**Planned fix** (chip dispatched 2026-05-08 "Fix BP claims auto-feeding cab_earned_season"): add an `apply_to_bp_progress=False` flag in `award_cab()` that the BP claim passes explicitly, to short-circuit step 3 when the caller is itself the BP. Similar pattern to `apply_streak_multiplier` already existing in cab_economy.

See [[ARCH_cab_economy]] § cab_service.award_cab — to be extended.

---
