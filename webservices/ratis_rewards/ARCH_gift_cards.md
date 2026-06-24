---
type: sub-arch
service: ratis_rewards
parent: ARCH_REWARDS
related: [ARCH_battlepass, ARCH_referral, ARCH_BATCH_REFERRAL_PAYOUT]
status: in-progress
tags: [gift-cards, rewards, runa, payout]
updated: 2026-04-24
---

# ratis_rewards — ARCH Gift Cards

> Provisioning gift-cards via Runa (V1 post-KYB) : `gift_card_brands`, `gift_card_orders` with `UNIQUE(source_type,source_ref_id)` idempotent. Entry points from battlepass, shop, referral payout.
> @tags: gift-cards rewards runa payout gift_card_brands gift_card_orders idempotent source-of-truth kyb v1
> @status: EN-COURS
> @subs: auto

> Parent : [[ARCH_REWARDS]] · Relations : [[ARCH_battlepass]], [[ARCH_referral]], [[ARCH_BATCH_REFERRAL_PAYOUT]]

> Status : 🔄 In progress
> Branch : `feature/rewards-gift-cards`

---

## Implementation Checklist

**Base checklist — to keep in every ARCH :**
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

**Custom checklist :**
- [x] Migration — tables `gift_card_brands` + `gift_card_orders`
- [x] `repositories/gift_card_repository.py` — insert_order, get_orders_by_user, get_order, update_order_status
- [x] `services/gift_card_service.py` — issue_gift_card (provider call + update)
- [ ] Battlepass wiring — claim flow `reward_type = 'gift_card'`
- [ ] Annual subscription wiring — Stripe webhook `checkout.session.completed`
- [x] `routes/rewards/gift_cards.py` — GET /rewards/gift-cards, GET /rewards/gift-cards/{id}
- [x] `main.py` — router + GIFT_CARD_PROVIDER_KEY require_env
- [x] `conftest.py` — fixtures gift_card_brands + gift_card_orders
- [x] Tests : test_gift_cards.py (list, detail, issued/pending states)
- [ ] Tests : battlepass gift_card claim wiring
- [ ] Tests : annual subscription webhook wiring

> ⚠️ One item at a time. Do not move to the next until the current one is done.

---

## Index

- [Context](#context) [L.60 - L.75]
- [Tables](#tables) [L.77 - L.128]
- [Issuance flow](#issuance-flow) [L.130 - L.175]
- [Endpoints](#endpoints) [L.177 - L.230]
- [Provider](#provider) [L.232 - L.268]
- [Existing wirings](#existing-wirings) [L.270 - L.295]
- [Parameters](#parameters) [L.297 - L.308]
- [Rules](#rules) [L.310 - L.322]
- [Out of scope V1](#out-of-scope-v1) [L.324 - L.332]

---

## Context

Read before starting :
- `ARCH_battlepass.md` — `reward_type = 'gift_card'` already in the CHECK, wiring to implement
- `ARCH_cashback.md` — fire-and-forget pattern for webhooks
- `webservices/ratis_auth/routes/webhooks.py` — existing Stripe handler

Required dependencies :
- `battlepass_milestones.reward_type IN ('cab', 'gift_card', 'skin')` — ✅ already in DB
- `user_battlepass_claims` — ✅ already implemented
- Stripe webhook `checkout.session.completed` — ✅ already in ratis_auth

Chosen provider : **Runa** (modern API, large catalogue, Amazon.fr available after KYB).
KYB must be initiated independently from development.

---

## Tables

### `gift_card_brands` — created

Catalogue of available brands. Managed by admin (direct DB insert for V1).

```sql
id                UUID PRIMARY KEY DEFAULT gen_random_uuid()
name              TEXT NOT NULL                          -- "Amazon"
provider_brand_id TEXT NOT NULL                          -- Runa brand ID
logo_url          TEXT                                   -- frontend image URL
is_active         BOOLEAN NOT NULL DEFAULT TRUE
created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
```

### `gift_card_orders` — created

One row per gift card issued, regardless of the source.

```sql
id                UUID PRIMARY KEY DEFAULT gen_random_uuid()
user_id           UUID REFERENCES users(id) ON DELETE SET NULL
brand_id          UUID NOT NULL REFERENCES gift_card_brands(id) ON DELETE RESTRICT
denomination      INT NOT NULL                           -- cents : 2000 = 20€
status            TEXT NOT NULL DEFAULT 'pending'
                  CHECK (status IN ('pending', 'issued', 'failed', 'churned'))
                  -- 'churned' added in migration 20260517_1600_gift_card_churned_status (H3 audit fix):
                  -- churn-farming cancellations are now distinct from real Runa issuance failures.
source_type       TEXT NOT NULL
                  CHECK (source_type IN ('annual_subscription', 'battlepass_milestone', 'shop_purchase'))
source_ref_id     TEXT NOT NULL                          -- idempotency : Stripe session_id, milestone_id, etc.
provider_order_id TEXT                                   -- Runa ref (NULL while pending)
code              TEXT                                   -- card code (NULL while pending)
issued_at         TIMESTAMPTZ
failed_at         TIMESTAMPTZ
created_at        TIMESTAMPTZ NOT NULL DEFAULT now()

UNIQUE (source_type, source_ref_id)   -- idempotency : prevents double issuance on webhook retry
```

---

## Issuance flow

### General principle (fire-and-forget)

The route or webhook that triggers issuance **never** blocks on the provider call.
Sequence :

```
1. INSERT gift_card_orders (status='pending', source_ref_id=<idempotency_key>)
   ON CONFLICT (source_type, source_ref_id) DO NOTHING   ← idempotent
2. db.commit()   ← immediate return to caller
3. [BackgroundTasks] → gift_card_service.issue_gift_card(order_id)
4.   → POST Runa /orders call
5.   → UPDATE gift_card_orders SET status='issued', code=..., provider_order_id=...
       or SET status='failed' on provider error
```

### Flow A — Annual subscription

Trigger : Stripe webhook `checkout.session.completed` in `ratis_auth`.

```
Stripe webhook → ratis_auth verifies signature
→ calls ratis_rewards via rewards_client.trigger_annual_gift_card(user_id, session_id)
→ ratis_rewards : INSERT pending + BackgroundTasks.add_task(issue_gift_card, order_id)
→ returns 200 immediately
```

Idempotency key : Stripe `session_id` → `source_ref_id`.
Value : `gift_card_annual_denomination` from `ratis_settings.json` (2000 = 20€).
Brand : `gift_card_annual_brand_id` from `ratis_settings.json`.

### Flow B — Battlepass milestone

Trigger : `POST /rewards/battlepass/claim/{milestone_id}` when `reward_type = 'gift_card'`.

```
existing claim flow :
  → if reward_type = 'gift_card'
  → INSERT gift_card_orders (pending, source_type='battlepass_milestone', source_ref_id=milestone_id)
  → db.commit()
  → BackgroundTasks.add_task(issue_gift_card, order_id)
  → return { "claimed": true, "reward_type": "gift_card", "reward_value": <denomination> }
```

`denomination` = `battlepass_milestones.reward_value` (already in cents).
`brand_id` = `gift_card_battlepass_brand_id` from `ratis_settings.json`.

### Flow C — Shop (out of scope V1)

Tables planned, route not wired. See section [Out of scope V1].

---

## Endpoints

### `GET /api/v1/rewards/gift-cards`

Auth : user JWT

Response :
```json
[
  {
    "id": "uuid",
    "brand": { "name": "Amazon", "logo_url": "https://..." },
    "denomination": 2000,
    "status": "issued",
    "code": "XXXX-XXXX-XXXX",
    "source_type": "annual_subscription",
    "issued_at": "2026-04-13T18:00:00Z"
  },
  {
    "id": "uuid",
    "brand": { "name": "Amazon", "logo_url": "https://..." },
    "denomination": 500,
    "status": "pending",
    "code": null,
    "source_type": "battlepass_milestone",
    "issued_at": null
  }
]
```

`code` : `null` if `status != 'issued'`.

### `GET /api/v1/rewards/gift-cards/{id}`

Auth : user JWT — verifies `assert_owner(gift_card.user_id, current_user.id)`

Same structure as the list item, with `code` visible if `status = 'issued'`.

Error codes : `404 gift_card_not_found`

---

## Provider

### Runa — integration

Base URL : `https://api.runa.io/v1` (sandbox : `https://sandbox-api.runa.io/v1`)

**Issue a card :**
```
POST /orders
Authorization: Bearer <GIFT_CARD_PROVIDER_KEY>
{
  "product_id": "<provider_brand_id>",   ← gift_card_brands.provider_brand_id
  "face_value": 20.00,                   ← denomination / 100 (Runa expects float euros)
  "currency": "EUR",
  "idempotency_key": "<order.id>"
}
```

Success response :
```json
{
  "id": "runa_order_xyz",
  "status": "COMPLETE",
  "redemption_code": "XXXX-XXXX-XXXX"
}
```

**Runa status → internal mapping :**
| Runa status | internal |
|---|---|
| `COMPLETE` | `issued` |
| `PROCESSING` | `pending` (re-poll in 30s) |
| `FAILED` | `failed` |

**Errors to handle :**
- `402 Payment Required` → insufficient Runa account balance → `failed` + admin alert
- `422 Unprocessable Entity` → invalid denomination for this brand → `failed`
- `5xx` → retry x3 with backoff (tenacity) → `failed` if exhausted

Env var : `GIFT_CARD_PROVIDER_KEY` — Runa API key.

---

## Existing wirings

### `ratis_core.rewards_client` — new function

```python
def trigger_annual_gift_card(user_id: UUID, stripe_session_id: str) -> None:
    """Fire-and-forget — called from ratis_auth after annual subscription webhook."""
```

POST to internal `ratis_rewards` : `POST /internal/gift-cards/annual`
Auth : `INTERNAL_API_KEY` (existing pattern).

### `ratis_auth.routes.webhooks` — modification

In `checkout.session.completed`, after annual subscription activation :
```python
if plan == "annual":
    rewards_client.trigger_annual_gift_card(user_id, session.id)
```

### `ratis_rewards.routes.rewards.battlepass` — modification

In `claim_milestone`, replace `# livraison hors V1` :
```python
if milestone.reward_type == "gift_card":
    gift_card_service.enqueue_gift_card(
        db, background_tasks, user_id,
        denomination=milestone.reward_value,
        source_type="battlepass_milestone",
        source_ref_id=str(milestone_id),
    )
```

---

## Parameters

Add to `ratis_settings.json` :

```json
"gift_cards": {
    "annual_subscription_denomination": 2000,
    "annual_subscription_brand_id": "<uuid gift_card_brands>",
    "battlepass_brand_id": "<uuid gift_card_brands>"
}
```

`brand_id` filled in after manual INSERT of the Amazon brand in DB.

---

## Rules

- **Fire-and-forget mandatory** — never block subscription/battlepass on the Runa call
- **Idempotency** — `UNIQUE (source_type, source_ref_id)` + `ON CONFLICT DO NOTHING`
- **Code visible only if `status = 'issued'`** — null otherwise (avoids displaying an invalid code)
- **`assert_owner` mandatory** on `GET /gift-cards/{id}` — see KP-05
- **Retry x3 with tenacity** on the provider call — `failed` if exhausted, never retry silently
- **Never log the code** — the code is monetary value

---

## Out of scope V1

- **User-initiated shop** → see [[ARCH_boutique]] (V1, design 2026-05-08). The infrastructure (tables `gift_card_brands` + `gift_card_orders` + `source_type='shop_purchase'`) is ready here ; the route + caps + UX are specified in `ARCH_boutique.md`.
- **Async polling** : if Runa responds `PROCESSING`, re-poll planned but not implemented in V1 — Runa is considered synchronous for standard denominations.
- **Push notification** : notify the user when their card is ready (ratis_notifier).
- **Brand rotation** : admin UI to enable/disable brands in `gift_card_brands`.
- **Code encryption** in DB (V2 if PCI compliance required).
