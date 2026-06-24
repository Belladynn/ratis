---
type: sub-arch
service: ratis_rewards
parent: ARCH_REWARDS
related: [ARCH_BATCH_MYSTERY_ANNOUNCE, ARCH_NOTIFIER]
status: planned
tags: [mystery-product, rewards, gamification, notification]
updated: 2026-04-24
---

# ratis_rewards — ARCH mystery product

> "Mystery product" challenges: a hidden product to discover through progressive clues over the period. `mystery_challenges` + `mystery_challenge_clues` + `mystery_challenge_finds`. CAB reward for the first finder + participation bonus. Planned.
> @tags: mystery-product rewards gamification notification mystery_challenges clues finds challenge cab planned
> @status: PLANIFIÉ
> @subs: auto

> Parent: [[ARCH_REWARDS]] · Related: [[ARCH_BATCH_MYSTERY_ANNOUNCE]], [[ARCH_NOTIFIER]]

> Status: actioned
> Branch: `feature/mystery-product`

---

## Implementation checklist

**Base checklist:**
- [ ] Alembic migration created and verified
- [ ] SQLAlchemy models updated
- [ ] Repository — CRUD functions
- [ ] Service — business logic + edge cases
- [ ] User-facing routes
- [ ] Admin routes
- [ ] Batch `ratis_batch_mystery_announce`
- [ ] Integration `handle_scan_accepted` → `check_mystery_find`
- [ ] Tests written (TDD — before the code)
- [ ] `conftest.py` updated if new `require_env()`
- [ ] `ratis_settings.json` updated
- [ ] `pg_dump > db/schema.sql` after migration
- [ ] `ruff check --fix` clean
- [ ] CI pipeline green

**Specific checklist:**
- [ ] Uniqueness (challenge_id, user_id) on `mystery_finds` — DB constraint + race condition test
- [ ] Atomic rank via CTE (INSERT … SELECT COUNT + 1 FOR UPDATE)
- [ ] Exclusion of the last 5 EANs from random draw
- [ ] Product filtering: consensus < 3 months mandatory
- [ ] Batch announce: idempotent (safe to replay)
- [ ] Admin can choose the product manually OR draw at random
- [ ] Session log + DECISIONS_ACTED before each block

> ⚠️ One item at a time. Do not move to the next without completing the previous.

---

## Index

- [Context](#context) [L.55 - L.70]
- [Tables](#tables) [L.72 - L.160]
- [Endpoints](#endpoints) [L.162 - L.290]
- [Internal logic](#internal-logic) [L.292 - L.390]
- [Batch](#batch) [L.392 - L.430]
- [Inter-services](#inter-services) [L.432 - L.445]
- [Parameters](#parameters) [L.447 - L.470]
- [Rules](#rules) [L.472 - L.485]
- [Out of scope](#out-of-scope) [L.487 - L.495]

---

## Context

Read before starting:
- `CLAUDE.md`
- `KNOWN_PROBLEMS_INDEX.md`
- `DECISIONS_ACTED.md`

Required dependencies:
- Table `products` with EAN + `product_knowledge` (photo)
- Table `price_consensus` with `updated_at` (filter < 3 months)
- `handle_scan_accepted` in `services/cab_service.py`
- `enqueue_notification` in `repositories/notification_repository.py`
- `ratis_batch_mystery_announce/` — new batch to create

---

## Tables

### `mystery_challenges` — created

```sql
id                  UUID PRIMARY KEY DEFAULT gen_random_uuid()
product_ean         TEXT NOT NULL REFERENCES products(ean) ON DELETE RESTRICT
starts_at           TIMESTAMPTZ NOT NULL
ends_at             TIMESTAMPTZ NOT NULL  -- always starts_at + 7 days
status              TEXT NOT NULL DEFAULT 'scheduled'
                    CHECK (status IN ('scheduled','active','frozen','revealed'))
-- Reward tiers (JSONB to avoid a separate table with little value)
-- Format: [{"min_rank":1,"max_rank":1,"cab":500},{"min_rank":2,"max_rank":10,"cab":200},...]
reward_tiers        JSONB NOT NULL DEFAULT '[]'
created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
```

Index: `UNIQUE WHERE status = 'active'` (partial — only one active at a time, same pattern KP-23).

### `mystery_challenge_clues` — created

```sql
id              UUID PRIMARY KEY DEFAULT gen_random_uuid()
challenge_id    UUID NOT NULL REFERENCES mystery_challenges(id) ON DELETE CASCADE
reveal_day      INT NOT NULL CHECK (reveal_day BETWEEN 1 AND 3)
clue_text       TEXT NOT NULL
revealed_at     TIMESTAMPTZ  -- NULL until reveal_day is reached, set by the batch
created_at      TIMESTAMPTZ NOT NULL DEFAULT now()

UNIQUE (challenge_id, reveal_day)
```

### `mystery_challenge_finds` — created

```sql
id              UUID PRIMARY KEY DEFAULT gen_random_uuid()
challenge_id    UUID NOT NULL REFERENCES mystery_challenges(id) ON DELETE RESTRICT
user_id         UUID NOT NULL REFERENCES users(id) ON DELETE SET NULL
scan_id         UUID NOT NULL REFERENCES scans(id) ON DELETE RESTRICT
rank            INT NOT NULL                  -- final rank (1 = first)
cab_awarded     INT NOT NULL                  -- amount awarded according to tier
found_at        TIMESTAMPTZ NOT NULL          -- actual timestamp of the accepted scan
announced_at    TIMESTAMPTZ                   -- NULL until the midnight batch

UNIQUE (challenge_id, user_id)               -- one find per user per challenge
```

### `mystery_challenge_exclusions` — created

```sql
product_ean     TEXT NOT NULL REFERENCES products(ean) ON DELETE CASCADE
excluded_until  TIMESTAMPTZ NOT NULL          -- the last 5 draws
PRIMARY KEY (product_ean)
```

Note: managed automatically at draw time — INSERT of the new EAN, DELETE of the oldest if > 5.

---

## Endpoints

### `GET /api/v1/gamification/mystery`

Auth: JWT

Response (active challenge):
```json
{
  "id": "uuid",
  "status": "active",
  "starts_at": "2026-04-21T00:00:00Z",
  "ends_at": "2026-04-27T23:59:59Z",
  "clues": [
    { "reveal_day": 1, "clue_text": "C'est un produit laitier", "revealed": true },
    { "reveal_day": 2, "clue_text": "Il coûte moins de 2€",    "revealed": true },
    { "reveal_day": 3, "clue_text": "Il est jaune",             "revealed": false }
  ],
  "announced_winner": {
    "username": "alice42",
    "found_at_day": 1
  },
  "reward_tiers": [
    { "label": "1er",       "min_rank": 1,    "max_rank": 1,    "cab": 500 },
    { "label": "Top 10",    "min_rank": 2,    "max_rank": 10,   "cab": 200 },
    { "label": "Top 100",   "min_rank": 11,   "max_rank": 100,  "cab": 100 },
    { "label": "Top 1000",  "min_rank": 101,  "max_rank": 1000, "cab": 50  },
    { "label": "Participant","min_rank": 1001, "max_rank": null, "cab": 10  }
  ],
  "user_find": {
    "rank": 3,
    "cab_awarded": 200,
    "found_at": "2026-04-22T14:32:00Z"
  }
}
```

`user_find` is `null` if the user has not yet found the product.
`announced_winner` is `null` if no one has been announced yet (batch has not yet run).
Clues not yet revealed: `clue_text` is absent from the response (do not leak).

Error codes: `404 mystery_not_found` if no active or frozen challenge exists.

---

### `GET /api/v1/gamification/mystery/leaderboard`

Auth: JWT

Returns the leaderboard for the active or frozen challenge: all finds where `announced_at IS NOT NULL`, ordered by rank. Called only when the user taps on the challenge (lazy load).

Response:
```json
{
  "challenge_id": "uuid",
  "status": "active",
  "finds": [
    { "rank": 1,  "username": "alice42", "found_at_day": 1, "cab_awarded": 500 },
    { "rank": 2,  "username": "bob99",   "found_at_day": 2, "cab_awarded": 200 },
    { "rank": 3,  "username": "carol",   "found_at_day": 2, "cab_awarded": 200 }
  ],
  "user_rank": 3
}
```

`user_rank`: rank of the authenticated user, `null` if they have not found the product.
Only finds with `announced_at IS NOT NULL` are returned (no leak before midnight).
If `status = 'revealed'`: all finds are visible + the product is revealed (include `product_name` and `product_image_url`).

---

### `GET /api/v1/gamification/mystery/history`

Auth: JWT

Returns the last 10 `revealed` challenges with product + winner. Optional pagination.

---

### `POST /api/v1/admin/mystery`

Auth: ADMIN_API_KEY

Creates a challenge. `product_ean` can be provided manually or omitted (automatic draw).

Request:
```json
{
  "starts_at": "2026-04-21T00:00:00Z",
  "product_ean": null,
  "category_filter": "dairy",
  "reward_tiers": [
    { "min_rank": 1,   "max_rank": 1,    "cab": 500 },
    { "min_rank": 2,   "max_rank": 10,   "cab": 200 },
    { "min_rank": 11,  "max_rank": 100,  "cab": 100 },
    { "min_rank": 101, "max_rank": 1000, "cab": 50  },
    { "min_rank": 1001,"max_rank": null, "cab": 10  }
  ],
  "clues": [
    { "reveal_day": 1, "clue_text": "C'est un produit laitier" },
    { "reveal_day": 2, "clue_text": "Il coûte moins de 2€" },
    { "reveal_day": 3, "clue_text": "Il est jaune" }
  ]
}
```

If `product_ean = null` → automatic draw (consensus < 3 months, outside exclusions, optional category filter).
`ends_at` = `starts_at + 7 days` (computed server-side).

Error codes:
- `409 challenge_overlap` — `starts_at` overlaps an existing challenge
- `422 no_eligible_product` — no eligible product for the draw
- `404 product_not_found` — `product_ean` provided but not found

---

### `GET /api/v1/admin/mystery`

Auth: ADMIN_API_KEY

Lists all challenges (scheduled, active, frozen, revealed) with status, EAN, number of finds.

---

### `GET /api/v1/admin/mystery/draw`

Auth: ADMIN_API_KEY

Draws a random product (without creating the challenge). Allows the admin to preview the result before confirming.

Optional query params: `category` (category filter).

Response:
```json
{
  "ean": "3017620425400",
  "name": "Nutella 400g",
  "category": "spreads",
  "last_consensus_at": "2026-02-10T00:00:00Z"
}
```

---

### `PATCH /api/v1/admin/mystery/{id}`

Auth: ADMIN_API_KEY

Modifies a `scheduled` challenge only (not active/frozen/revealed).
Editable fields: `starts_at`, `product_ean`, `reward_tiers`, `clues`.

Error codes: `409 challenge_not_modifiable` if status ≠ `scheduled`.

---

### `DELETE /api/v1/admin/mystery/{id}`

Auth: ADMIN_API_KEY

Deletes a `scheduled` challenge only.

---

## Internal logic

### `check_mystery_find(db, user_id, scan_id, ean)`

Called from `handle_scan_accepted` after EAN matching.

```
1. SELECT mystery_challenges WHERE status = 'active' AND product_ean = :ean → if absent, return (no-op)
2. SELECT 1 FROM mystery_challenge_finds WHERE challenge_id = :cid AND user_id = :uid → if present, return (already found)
3. INSERT INTO mystery_challenge_finds with atomic rank:
     WITH ranked AS (
       SELECT COUNT(*) + 1 AS rank
       FROM mystery_challenge_finds
       WHERE challenge_id = :cid
       FOR UPDATE
     )
     INSERT INTO mystery_challenge_finds
         (challenge_id, user_id, scan_id, rank, cab_awarded, found_at)
     SELECT :cid, :uid, :scan_id, ranked.rank,
            resolve_cab_tier(:cid, ranked.rank), now()
     FROM ranked
4. award_cab(db, user_id, cab_awarded, 'mystery_product', reference_id=scan_id)
5. enqueue_notification(db, user_id, 'mystery_product_found', {'rank': rank, 'cab': cab_awarded})
```

`resolve_cab_tier`: Python function that reads the `reward_tiers` JSONB from the challenge and returns the amount based on rank.

---

### `draw_random_product(db, category_filter=None)`

```
1. SELECT ean FROM price_consensus
     WHERE updated_at > now() - interval '3 months'
     AND ean NOT IN (SELECT product_ean FROM mystery_challenge_exclusions)
     [AND category = :category_filter if provided]
   ORDER BY random()
   LIMIT 1
2. If no result → raise NoEligibleProduct
3. Return the EAN
```

---

### `create_mystery_challenge(db, starts_at, product_ean, reward_tiers, clues)`

```
1. Check no overlap: SELECT 1 FROM mystery_challenges
     WHERE status IN ('scheduled','active','frozen')
     AND tsrange(starts_at, ends_at) && tsrange(:starts, :ends)
   → ChallengeOverlap if conflict
2. If product_ean = None → draw_random_product
3. INSERT mystery_challenges
4. INSERT mystery_challenge_clues (1 to 3 rows)
5. UPDATE mystery_challenge_exclusions: INSERT new EAN, DELETE if > 5
6. Return the id
```

---

## Batch

### `ratis_batch_mystery_announce`

Runs at 0:00 UTC every day (cron `0 0 * * *`).

**Step 1 — Reveal today's clues**
```
For the active challenge:
  reveal_day = (today - challenge.starts_at).days + 1  (D1 = day 1)
  UPDATE mystery_challenge_clues
    SET revealed_at = now()
  WHERE challenge_id = :cid AND reveal_day <= :current_day AND revealed_at IS NULL
```

**Step 2 — Announce yesterday's finds**
```
UPDATE mystery_challenge_finds
  SET announced_at = now()
WHERE challenge_id = :cid
  AND announced_at IS NULL
  AND found_at < date_trunc('day', now())  -- scans before midnight
→ For each newly announced find:
  enqueue_notification (via outbox) → 'mystery_winner_announced'
```

**Step 3 — Activate the next scheduled challenge if necessary**
```
If no active challenge:
  SELECT * FROM mystery_challenges WHERE status = 'scheduled' ORDER BY starts_at LIMIT 1
  → If starts_at <= now(): UPDATE status = 'active'
```

**Step 4 — Freeze + final reveal on Sunday (D7)**
```
If active challenge AND now() >= ends_at:
  UPDATE mystery_challenges SET status = 'frozen'
  [Sunday 0:00 = ends_at] → UPDATE status = 'revealed'
  enqueue_notification broadcast → 'mystery_product_revealed' (name + photo)
```

The batch is **idempotent** — replaying it twice on the same day duplicates nothing (WHERE revealed_at IS NULL, WHERE announced_at IS NULL, etc.).

---

## Inter-services

| Direction | Service | Function | Trigger |
|---|---|---|---|
| ← incoming | `ratis_product_analyser` | `trigger_scan_accepted(user_id, scan_id, scan_type)` | Scan accepted (EAN resolved) |
| → outgoing | `ratis_notifier` | `enqueue_notification` | Find recorded / midnight announcement / final reveal |

**Note:** `handle_scan_accepted` receives `scan_id` — `check_mystery_find` performs a `SELECT scans.product_ean WHERE id = :scan_id` to retrieve the EAN. No need to modify the `trigger_scan_accepted` contract.

---

## Parameters

Add in `ratis_settings.json`:

```json
"mystery_product": {
    "duration_days": 7,
    "max_clues": 3,
    "exclusion_window": 5,
    "consensus_max_age_days": 90,
    "default_reward_tiers": [
        { "min_rank": 1,    "max_rank": 1,    "cab": 500 },
        { "min_rank": 2,    "max_rank": 10,   "cab": 200 },
        { "min_rank": 11,   "max_rank": 100,  "cab": 100 },
        { "min_rank": 101,  "max_rank": 1000, "cab": 50  },
        { "min_rank": 1001, "max_rank": null, "cab": 10  }
    ]
}
```

The `reward_tiers` in `ratis_settings.json` are the default values pre-filled in the admin UI — the admin can override them per challenge.

---

## Rules

- **Only one active challenge at a time** — partial UNIQUE index on `status = 'active'` (KP-23: declared in both the model AND the migration).
- **Only one find per user per challenge** — UNIQUE (challenge_id, user_id) on `mystery_finds`.
- **Atomic rank** — `FOR UPDATE` on the COUNT before INSERT to avoid race conditions.
- **Announcement at midnight UTC** — `announced_at` NULL until the batch; the GET route never returns an unannounced find to the general public (except to the user themselves for their own find).
- **Mystery product not leaked** — `product_ean` / `name` never appear in the GET response before `status = 'revealed'`. Clues not yet revealed (`revealed_at IS NULL`) are not included in the response.
- **Immediate CAB attribution** — in the same transaction as the INSERT mystery_find, via `award_cab`.
- **Idempotent batch** — all batch UPDATEs are conditioned on `IS NULL` to be replayable without side effects.
- Never raw SQL outside repositories.

---

## Out of scope

- Broadcast notification to all users (final reveal) — for now `enqueue_notification` is one-to-one. Broadcast is out of V1; a `PROD_CHECKLIST` entry will be added.
- Skins / visual badges linked to the mystery product.
- Multiple simultaneous challenges.
- Configurable duration per challenge (fixed at 7 days for V1).
- B2B analytics on the most searched products.
