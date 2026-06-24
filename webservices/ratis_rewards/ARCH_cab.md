---
type: sub-arch
service: ratis_rewards
parent: ARCH_REWARDS
related: [ARCH_battlepass, ARCH_missions, ARCH_cab_economy, ARCH_gamification]
status: production
tags: [cab, cabecoin, rewards, balance, currency]
updated: 2026-04-24
---

# ratis_rewards — ARCH CAB

> Cabecoin (CAB) — Ratis internal currency. Materialized balance `user_cab_balance`, source-of-truth via `cabecoin_transactions` (`direction credit/debit`, `reference_type` CHECK). Atomic update mandatory, never-sell-CAB red-line.
> @tags: cab cabecoin rewards balance currency user_cab_balance cabecoin_transactions credit debit atomic rewards_client never-sell
> @status: LIVRÉ V0
> @subs: auto

> Parent : [[ARCH_REWARDS]] · Relations : [[ARCH_battlepass]], [[ARCH_missions]], [[ARCH_cab_economy]], [[ARCH_gamification]]

> Status : ✅ Implemented — cabecoins (CAB) + rewards_client
> Branch : `main`

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
- [x] award_cab (UPDATE balance + INSERT transaction + UPDATE progress — atomic)
- [x] debit_cab (atomic UPDATE with rowcount check)
- [x] ratis_core.rewards_client created (trigger_scan_accepted)
- [x] user_cab_balance created at registration in ratis_auth
- [x] Never purge cabecoin_transactions

> ⚠️ One item at a time. Do not move to the next without finishing the previous.

---

## Index

- [Context](#context) [L.43 - L.47]
- [Parent reference](#parent-reference)
- [Endpoint](#endpoint) [L.61 - L.76]
- [Internal logic](#internal-logic) [L.78 - L.109]
- [Tables](#tables) [L.111 - L.136]
- [ratis_core.rewards_client](#ratis_corerewards_client) [L.138 - L.148]
- [ratis_settings.json parameters](#ratis_settingsjson-parameters) [L.150 - L.158]
- [Rules](#rules) [L.160 - L.167]

---

## Context

Read `ratis_core` before starting. Strict TDD. Branch `feature/rewards-cab`.

---

## Parent Reference

This ARCH is a sub-domain of `webservices/ratis_rewards/ARCH_REWARDS.md` (global rewards service). For cross-cutting rules (anti-double-spend, materialized atomic balance, `int-cents` mantra), refer to the parent ARCH.

Owner tables: `user_cab_balance` (materialized), `cabecoin_transactions` (immutable, `reference_type` enum with `'admin'` added in PR #205).

See also: `ARCH_cab_economy.md` (attribution rules, multipliers), `ARCH_admin_endpoints.md` (CAB adjustment + 2FA TOTP).

---

## Endpoint

```
GET /rewards/cab/balance
```
```json
{
  "cab_balance": 1240,
  "battlepass": {
    "season_number": 1,
    "season_name": "Saison 1",
    "ends_at": "2026-06-30T00:00:00Z",
    "cab_earned_season": 340,
    "next_milestone_delta": 160
  }
}
```
`next_milestone_delta` = `cab_required next milestone - cab_earned_season`. If all milestones are `claimed` → `0`.

---

## Internal Logic

### `award_cab(user_id, amount, reason, db)`
UPDATE + INSERT + UPDATE progress in the same SQL transaction. Skip `user_battlepass_progress` if no active season:
```python
with db.begin():
    db.execute("UPDATE user_cab_balance SET balance = balance + :x WHERE user_id = :uid", ...)
    db.execute("INSERT INTO cabecoin_transactions (user_id, direction, amount, reason) VALUES (:uid, 'credit', :x, :reason)", ...)
    active_season = db.execute("SELECT id FROM battlepass_seasons WHERE is_active = TRUE LIMIT 1").first()
    if active_season:
        db.execute("""
            INSERT INTO user_battlepass_progress (user_id, season_id, cab_earned_season)
            VALUES (:uid, :sid, :x)
            ON CONFLICT (user_id, season_id) DO UPDATE
            SET cab_earned_season = user_battlepass_progress.cab_earned_season + :x
        """, ...)
```

### `debit_cab(user_id, amount, reason, db)`
```python
with db.begin():
    rows = db.execute("UPDATE user_cab_balance SET balance = balance - :x WHERE user_id = :uid AND balance >= :x", ...)
    if rows.rowcount == 0:
        raise InsufficientBalance()
    db.execute("INSERT INTO cabecoin_transactions ...", ...)
```

---

## Tables

**`user_cab_balance`**
```sql
user_id    UUID PRIMARY KEY REFERENCES users(id) ON DELETE RESTRICT
balance    INT NOT NULL DEFAULT 0 CHECK (balance >= 0)
updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
-- PostgreSQL trigger ON UPDATE for updated_at
```
Created at registration with `balance = 0` in the same transaction as account creation.

**`cabecoin_transactions`**
```sql
id         UUID PRIMARY KEY DEFAULT gen_random_uuid()
user_id    UUID REFERENCES users(id) ON DELETE SET NULL
direction  TEXT NOT NULL CHECK (direction IN ('credit', 'debit'))
amount     INT NOT NULL CHECK (amount > 0)
reason     TEXT NOT NULL CHECK (reason IN (
               'receipt_scan', 'label_scan', 'barcode_scan',
               'mission_reward',    -- daily or weekly, indifferently
               'battlepass_milestone', 'referral',
               'cashback_unlock', 'shop_purchase'
           ))
created_at TIMESTAMPTZ NOT NULL DEFAULT now()
```
Never purge.

---

## ratis_core.rewards_client

Create `ratis_core/rewards_client.py` — same pattern as `notifier_client.py`.
Env variable: `REWARDS_BASE_URL` → `.env.example` + `CLAUDE.md`.

Exposed endpoint: `POST /rewards/events/scan_accepted` — fire-and-forget, award_cab + check_missions_progress in a single transaction.

```python
async def notify_scan_accepted(user_id: UUID, scan_type: str) -> None: ...
```

---

## `ratis_settings.json` Parameters

```json
"rewards": {
    "cab_per_receipt_scan": 50,
    "cab_per_label_scan": 20,
    "cab_per_barcode_scan": 10
}
```

---

## Rules

- `award_cab` and `debit_cab` always in a single SQL transaction
- `award_cab` — silently skip `user_battlepass_progress` if no active season
- `debit_cab`: `rowcount == 0` → `InsufficientBalance`
- Never purge `cabecoin_transactions`
- RESTRICT on `user_cab_balance.user_id` — soft delete only
- `user_cab_balance` created by `ratis_auth` at registration, in the same transaction as account creation
