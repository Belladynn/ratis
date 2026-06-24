---
type: sub-arch
service: ratis_rewards
parent: ARCH_REWARDS
related: [ARCH_BATCH_RECONCILIATION]
status: in-progress
tags: [cashback, rewards, payout, webhook, reconciliation]
updated: 2026-04-24
---

# ratis_rewards — ARCH Cashback

> Cashback affiliation (Affilae/Awin/CJ): click detection, conversion webhook, payout via withdrawals. Reconciliation to come via a dedicated batch. `cashback_transactions` + `cashback_withdrawals` never purgeable (legal).
> @tags: cashback rewards payout webhook reconciliation affilae awin cj affiliate cashback_transactions cashback_withdrawals legal never-purge
> @status: EN-COURS
> @subs: auto

> Parent : [[ARCH_REWARDS]] · Relations : [[ARCH_BATCH_RECONCILIATION]]

> Design decided in this ARCH (consolidated). Strict TDD. See also `ARCH_REWARDS.md` (parent) and `ARCH_admin_endpoints.md` (admin withdrawals workflow + 2FA TOTP).

> Original design doc archived: `docs/superpowers/specs/_archive/2026-04/cashback-design-original.md` (consolidated into this ARCH).

> Status: 🔄 In progress — detection/boost/webhook/withdrawal implemented, batch reconciliation pending
> Branch: `feature/rewards-gift-card-cashback`

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

**Custom checklist — CC plans items before coding:**
- [x] `repositories/cashback_repository.py` — 13 functions (get_active_offer, insert_cashback_credit, credit_cashback_balance, etc.)
- [x] `services/cashback_service.py` — detect_cashback, boost_cashback, resolve_cashback
- [x] `routes/rewards/cashback.py` — GET balance, POST scan-detected, POST boost
- [x] `routes/rewards/cashback_webhook.py` — POST webhook/{provider}
- [x] `routes/admin/cashback.py` — validate/refuse + affiliate-offers
- [x] `main.py` — routers + ADMIN_API_KEY + CASHBACK_WEBHOOK_SECRET_{PROVIDER}
- [x] `ratis_core.deps` — verify_admin_key
- [x] `conftest.py` — cashback fixtures
- [x] Tests: test_cashback_detection.py (11), test_cashback_boost.py (7), test_cashback_resolve.py (8)
- [x] `ratis_core.rewards_client` — trigger_cashback_scan
- [x] `ratis_product_analyser.worker.receipt_task` — wiring post-commit
- [x] Migration cashback_boost_refund
- [x] Migration — table cashback_withdrawals
- [x] `repositories/cashback_repository.py` — debit_cashback_balance, insert_cashback_withdrawal, get_pending_withdrawals
- [x] `services/cashback_service.py` — withdraw_cashback
- [x] `routes/rewards/cashback_withdraw.py` — POST /rewards/cashback/withdraw
- [x] Tests: test_cashback_withdraw.py
- [ ] `ratis_batch_reconciliation` — implementation

> ⚠️ One item at a time. Do not move to the next without completing the current one.

---

## Index

- [Concept](#concept) [L.60 - L.67]
- [Endpoints](#endpoints) [L.69 - L.133]
- [Tables](#tables) [L.135 - L.191]
- [Service logic](#service-logic) [L.193 - L.228]
- [Parameters ratis_settings.json](#parameters-ratis_settingsjson) [L.230 - L.241]
- [Inter-service](#inter-service) [L.243 - L.254]
- [Rules](#rules) [L.256 - L.284]
- [To implement](#to-implement) [L.286 - L.309]

---

## Concept

Two parallel and complementary systems:
- **CAB** — virtual currency, earned via scans
- **Cashback €** — real reimbursement, tied to a product + a brand (`affiliate_offers`)

Interaction: the user **spends CAB to double their cashback** — which incentivizes
scanning more to earn CAB back.

---

## Endpoints

```
GET  /rewards/cashback/balance
POST /rewards/cashback/scan-detected          (internal key)
POST /rewards/cashback/boost/{transaction_id} (JWT)
POST /rewards/cashback/withdraw               (JWT)
POST /rewards/cashback/webhook/{provider}     (webhook secret)
PATCH /admin/cashback/{id}/validate           (admin key)
PATCH /admin/cashback/{id}/refuse             (admin key)
POST  /admin/affiliate-offers                 (admin key)
```

### GET /rewards/cashback/balance
```json
{
  "cashback_balance": "3.50",
  "pending": [
    {
      "id": "uuid",
      "amount": "0.80",
      "product_ean": "3017620422003",
      "status": "pending",
      "boost_available_until": "2026-04-13T10:00:00Z",
      "boost_cost_cab": 96
    }
  ]
}
```
`boost_available_until` = `created_at + cashback_boost_window_hours` (parameter).
`boost_cost_cab` = `full_amount_in_centimes × cashback_boost_cab_rate`.
Absent if the window has expired or if the CREDIT is already boosted (`boost_applied = true`).

### POST /rewards/cashback/scan-detected
Called by `ratis_product_analyser` via `ratis_core.rewards_client`.
Payload:
```json
{
  "user_id": "uuid",
  "scan_id": "uuid",
  "receipt_lines": [
    {"ean": "3017620422003", "price": 2.50},
    {"ean": "7613035898530", "price": 1.10}
  ]
}
```
Actions (all in one transaction):
1. Lookup active `affiliate_offers` by EAN
2. For each offer found: compute `base = cashback_rate × price`
3. Create `cashback_transactions` type=`CREDIT`, `status=pending`,
   `distributed_at=now()` if subscriber else NULL
4. If subscriber: `UPDATE user_cashback_balance SET balance = balance + base`
5. Idempotency: skip if `(scan_id, product_ean)` already present in DB

### POST /rewards/cashback/boost/{transaction_id}
Conditions:
- `status = pending` or `confirmed`
- `boost_applied = false`
- `created_at + boost_window > now()`
- `user_cab_balance >= boost_cost_cab`

Actions (all in one transaction):
1. `debit_cab(user_id, boost_cost_cab, "BOOST_CASHBACK")`
2. Compute `delta = base_amount` (full = base × 2, so delta = base)
3. Create `cashback_transactions` type=`BOOST`, `amount=delta`,
   `parent_transaction_id=CREDIT.id`, `affiliate_offer_id` + `product_ean` copied from CREDIT
4. `UPDATE user_cashback_balance SET balance = balance + delta`
5. `UPDATE cashback_transactions SET boost_applied = true WHERE id = CREDIT.id`

Errors: `404 transaction_not_found`, `409 already_boosted`,
`409 boost_window_expired`, `422 insufficient_cab_balance`.

### POST /rewards/cashback/withdraw
Payload: `{"amount": "10.00"}`

Conditions:
- `amount >= cashback_min_withdrawal` (10.00€)
- `user_cashback_balance.balance >= amount`

**Atomicity (CLAUDE.md pattern) — all in one transaction:**
1. `debit_cashback_balance(db, user_id, amount)` — atomic UPDATE `balance = balance - x WHERE balance >= x`; rowcount = 0 → `422 insufficient_balance`
2. INSERT `cashback_transactions` type=`WITHDRAWAL`, amount, **`status='confirmed'`**, `distributed_at=now()` RETURNING id
   → immediate accounting write: the debit is a done deal
3. INSERT `cashback_withdrawals` (`cashback_tx_id`, amount, `status='pending'`) RETURNING withdrawal_id
4. COMMIT
5. Payment provider call → `payment_provider_ref`
6. UPDATE `cashback_withdrawals` SET `payment_provider_ref=ref`, `status='completed'`, `completed_at=now()`
7. COMMIT

**Post-commit error handling:**
- Step 5 fails (provider unavailable): `cashback_withdrawals.status='pending'` without ref → `ratis_batch_reconciliation` detects and retries the provider call
- Step 7 fails (DB down after successful provider call): `status='pending'` without ref in DB but ref known on the provider side → same batch detection, idempotent if the provider returns the same ref on retry

**In case of definitive transfer failure** (`cashback_withdrawals.status='failed'`, `failure_reason` set):
Never modify the confirmed WITHDRAWAL — offset it with a compensating transaction:
1. INSERT `cashback_transactions` type=`CREDIT`, amount, `status='confirmed'`, `distributed_at=now()` (refund)
2. `credit_cashback_balance(db, user_id, amount)`
3. UPDATE `cashback_withdrawals` SET `status='failed'`, `completed_at=now()`
4. COMMIT

The history remains readable: confirmed WITHDRAWAL + compensating CREDIT = full traceability without retroactive mutation.

Errors: `422 insufficient_balance`, `422 below_minimum`.

### Webhook + Admin validate/refuse
Share the same resolution logic:
- `status → confirmed`: if `distributed_at IS NULL` → credit now
- `status → refused`:
  - If `distributed_at IS NOT NULL` → Ratis loss (no user debit)
  - Look for linked BOOST via `parent_transaction_id` → rollback CAB
    (`award_cab(user_id, boost_cost_cab, "BOOST_CASHBACK_REFUND")`)

### Webhook auth — HMAC + provider allowlist (F-RW-6, 2026-05-11)

The webhook `POST /rewards/cashback/webhook/{provider}` does NOT use a static Bearer
token (audit vulnerability RW F-RW-6: a token leak opened an
unlimited replay window). Current scheme — modeled on Stripe:

```
X-Cashback-Signature: t=<unix_ts>,v1=<hex_hmac_sha256>
hmac_sha256(key=CASHBACK_WEBHOOK_SECRET_<PROVIDER>, msg=f"{ts}.{raw_body}")
```

The handler:

1. **Provider allowlist** — `ratis_settings.json § cashback.webhook_providers`
   (`["affilae","awin","cj"]` V1). Any other `provider` path → 401
   `unknown_provider`. Fail-fast **before** reading the body — an attacker
   testing random provider names never reaches the signature
   verification code.
2. **Timestamp window** — `cashback.webhook_timestamp_tolerance_seconds`
   (300 s by default). `abs(now - ts) > tolerance` → 401 `signature_expired`.
   Bounds the value of a token leak to 5 min.
3. **Per-provider secret** (AUDIT 2026-05-17) — each provider has its own
   secret `CASHBACK_WEBHOOK_SECRET_{PROVIDER}` (provider name in
   uppercase, derived dynamically from the config-driven allowlist). The
   handler verifies the signature ONLY against the secret of the provider
   identified by the path param. A leaked secret only allows forging
   webhooks for THAT provider, not the entire affiliate network.
   The old single shared secret `CASHBACK_WEBHOOK_SECRET` is removed.
4. **24 h overlap rotation** — verification first tries
   `CASHBACK_WEBHOOK_SECRET_{PROVIDER}` (current) then, if non-empty,
   `CASHBACK_WEBHOOK_SECRET_{PROVIDER}_PREV` (previous). A match on PREV
   logs a `warning` — rotation observability. Rotation itself is
   per provider. Procedure (per provider): (a) generate new
   secret, (b) `{PROVIDER}_PREV=<old>` + `{PROVIDER}=<new>` deployed
   together, (c) communicate the new secret to the partner, (d) wait
   ~24 h for the partner to rotate, (e) remove `{PROVIDER}_PREV`.
5. **`hmac.compare_digest`** for timing-safe comparison — no leaky short-circuit.

Error codes (401 — lack of discrimination prevents an attacker from
distinguishing "wrong secret" from "stale ts", limiting information leakage):

- `unknown_provider`: provider not in allowlist
- `missing_signature`: `X-Cashback-Signature` header absent
- `invalid_signature`: malformed header, wrong sig, or secret(s) not configured
- `signature_expired`: `|now - ts| > tolerance`

**Sample HMAC signing client** (to share with partners for
rotation):

```python
import hmac, time
from hashlib import sha256

def sign_cashback_webhook(secret: str, body_bytes: bytes) -> tuple[int, str]:
    ts = int(time.time())
    msg = f"{ts}.".encode("ascii") + body_bytes
    sig = hmac.new(secret.encode(), msg, sha256).hexdigest()
    return ts, sig

# Usage côté partenaire :
ts, sig = sign_cashback_webhook(SECRET, payload_bytes)
headers = {"X-Cashback-Signature": f"t={ts},v1={sig}"}
```

Out-of-V1: `pipeline_audit_log` of body hash + IP + provider on each call
(F-RW-6 recommendation c, not shipped in V1 to limit the fix scope).

---

## Tables

**`affiliate_offers`**
```sql
id              UUID PK
provider        TEXT NOT NULL CHECK (IN 'affilae','awin','cj','direct')
external_id     TEXT NOT NULL
product_ean     TEXT NOT NULL FK products(ean) RESTRICT
brand_id        UUID NOT NULL FK brands(id) RESTRICT
cashback_rate   NUMERIC(5,4) NOT NULL CHECK > 0
valid_from      TIMESTAMPTZ NOT NULL
valid_until     TIMESTAMPTZ  -- NULL = no expiry
UNIQUE (provider, external_id)
```

**`cashback_transactions`** (columns added by migration `c4d5e6f7a8b9`)
```sql
id                    UUID PK
user_id               UUID FK users CASCADE
type                  TEXT CHECK IN ('CREDIT','BOOST','WITHDRAWAL','SUBSCRIPTION_PAYMENT')
amount                NUMERIC(10,2) CHECK > 0
status                TEXT CHECK IN ('pending','confirmed','refused')  DEFAULT 'pending'
product_ean           TEXT FK products RESTRICT  -- required for CREDIT/BOOST
affiliate_offer_id    UUID FK affiliate_offers SET NULL  -- required for CREDIT/BOOST
boost_applied         BOOL DEFAULT false  -- true if this CREDIT has already been boosted
distributed_at        TIMESTAMPTZ  -- NULL = not yet credited to balance
scan_id               UUID FK scans SET NULL
parent_transaction_id UUID FK cashback_transactions SET NULL  -- BOOST → parent CREDIT
created_at            TIMESTAMPTZ DEFAULT now()
```

**`user_cashback_balance`**
```sql
user_id    UUID PK FK users CASCADE
balance    NUMERIC(10,2) DEFAULT 0 CHECK >= 0
updated_at TIMESTAMPTZ
```
Created at registration — **ratis_auth fix required** (see below).

**`cashback_withdrawals`**
```sql
id                      UUID PK
user_id                 UUID NOT NULL FK users RESTRICT
cashback_transaction_id UUID FK cashback_transactions(id) RESTRICT  -- NULL possible (set at INSERT)
amount                  NUMERIC(10,2) NOT NULL CHECK > 0
status                  TEXT NOT NULL DEFAULT 'pending' CHECK IN ('pending','processed','failed')
payment_provider_ref    TEXT        -- NULL until provider call succeeds
provider_initiated_at   TIMESTAMPTZ -- NULL while payment_provider_ref is NULL (coherence constraint)
last_reconciled_at      TIMESTAMPTZ -- updated by ratis_batch_reconciliation
requested_at            TIMESTAMPTZ NOT NULL DEFAULT now()
processed_at            TIMESTAMPTZ -- NULL until resolved (requires processed_at IS NOT NULL if status='processed')
failure_reason          TEXT        -- NULL unless status='failed'
updated_at              TIMESTAMPTZ NOT NULL DEFAULT now()
```
Note: CHECK constraints in the initial schema — `processed_at IS NOT NULL` if `status='processed'`, `failure_reason IS NOT NULL` if `status='failed'`.

---

## Service logic

### `detect_cashback(db, user_id, scan_id, receipt_lines, is_subscriber)`
```python
for line in receipt_lines:
    offer = get_active_offer_by_ean(db, line.ean)
    if offer is None:
        continue
    if has_cashback_for_scan(db, scan_id, line.ean):
        continue  # idempotence
    base = round(offer.cashback_rate * line.price, 2)
    distributed_at = now() if is_subscriber else None
    insert_cashback_credit(db, user_id, offer, base, scan_id, distributed_at)
    if is_subscriber:
        credit_cashback_balance(db, user_id, base)
```

### `resolve_cashback(db, transaction_id, resolution)`  (`confirmed` | `refused`)
```python
tx = get_cashback_tx(db, transaction_id)
if resolution == "confirmed":
    tx.status = "confirmed"
    if tx.distributed_at is None:
        credit_cashback_balance(db, tx.user_id, tx.amount)
        tx.distributed_at = now()
elif resolution == "refused":
    tx.status = "refused"
    # Do not claw back from user if already distributed — Ratis loss
    boost = get_boost_child(db, transaction_id)
    if boost:
        boost.status = "refused"
        refund_cab(db, tx.user_id, boost_cost(boost.amount))
```

---

## Parameters `ratis_settings.json`

```json
"rewards": {
  "cashback_boost_multiplier": 2,
  "cashback_boost_cab_rate": 1.2,
  "cashback_boost_window_hours": 12,
  "cashback_min_withdrawal": 10.00,
  "cashback_pending_expiry_days": 90
}
```

---

## Inter-service

### ratis_core.rewards_client
Add `trigger_cashback_scan(user_id, scan_id, receipt_lines)` — called from
`ratis_product_analyser` only for scans of type `receipt`.
Same pattern as `trigger_scan_accepted` (HTTP + internal key, fire-and-forget).

### ratis_auth — registration fix
`_create_cashback_balance` missing from `auth_service.py`.
Add at lines 182, 243, 287 (same points as `_create_cab_balance`).

---

## Rules

- `credit_cashback_balance`: atomic UPDATE `balance = balance + :x` (never ORM)
- `debit_cashback_balance`: atomic UPDATE `balance = balance - :x WHERE balance >= :x` — rowcount = 0 → `InsufficientBalance`
- Never purge `cashback_transactions` or `cashback_withdrawals`
- Never modify a confirmed `cashback_transaction` — in case of cancellation, insert a compensating CREDIT
- WITHDRAWAL always inserted with `status='confirmed'` — it is `cashback_withdrawals.status` that tracks the operational state of the transfer
- `parent_transaction_id` has two semantics depending on `type`: BOOST → parent affiliate CREDIT; compensating CREDIT → failed WITHDRAWAL. No `reason` column — `type` + `parent_transaction_id` carry the information.

**Tracing query — find all withdrawal refunds:**
```sql
SELECT c.id          AS refund_tx_id,
       c.user_id,
       c.amount,
       c.created_at  AS refunded_at,
       w.id          AS withdrawal_tx_id,
       cw.payment_provider_ref,
       cw.initiated_at
FROM cashback_transactions c
JOIN cashback_transactions w  ON w.id = c.parent_transaction_id AND w.type = 'WITHDRAWAL'
JOIN cashback_withdrawals  cw ON cw.cashback_tx_id = w.id
WHERE c.type = 'CREDIT'
  AND c.affiliate_offer_id IS NULL
ORDER BY c.created_at DESC;
```
- Loss on refusal after advance: absorbed by Ratis, deduced from `(status=refused AND distributed_at IS NOT NULL)` — no additional field
- Boost only if window not expired AND `boost_applied = false`
- If `user_cashback_balance` missing (registration predating the fix): UPSERT safety net in the repo

---

## To implement

- [x] `repositories/cashback_repository.py` — `get_active_offer_by_ean`, `insert_cashback_credit`, `credit_cashback_balance`, `get_boost_child`, `refund_cab_for_boost`
- [x] `services/cashback_service.py` — `detect_cashback`, `boost_cashback`, `resolve_cashback`
- [x] `routes/rewards/cashback.py` — GET balance, POST scan-detected, POST boost
- [x] `routes/rewards/cashback_webhook.py` — POST webhook/{provider}
- [x] `routes/admin/cashback.py` — PATCH validate/refuse, POST/GET affiliate-offers
- [x] `main.py` — new routers mounted + `ADMIN_API_KEY` + `CASHBACK_WEBHOOK_SECRET_{PROVIDER}` (one per provider)
- [x] `ratis_core.deps` — `verify_admin_key`
- [x] `ratis_core.models.gamification` — `cashback_boost_refund` in `_CAB_REASONS`
- [x] `ratis_auth.auth_service` — `_create_cashback_balance` ✓ (done in previous session)
- [x] `conftest.py` — `make_affiliate_offer`, `make_brand`, `make_product`, `make_store`, `make_scan`
- [x] Tests: `test_cashback_detection.py` (11), `test_cashback_boost.py` (7), `test_cashback_resolve.py` (8) — 26 tests, 97 total ✅
- [x] `ratis_core.rewards_client` — `trigger_cashback_scan` (step 5)
- [x] `ratis_product_analyser.worker.receipt_task` — `notify_scan_accepted` + `trigger_cashback_scan` wired post-commit
- [x] Migration `d5e6f7a8b9c0` — `cashback_boost_refund` added to `reason_check`
- [ ] Migration — table `cashback_withdrawals`
- [ ] `repositories/cashback_repository.py` — `debit_cashback_balance`, `insert_cashback_withdrawal`, `get_pending_withdrawals`
- [ ] `services/cashback_service.py` — `withdraw_cashback`
- [ ] `routes/rewards/cashback_withdraw.py` — POST /rewards/cashback/withdraw
- [ ] Tests: `test_cashback_withdraw.py`
- [ ] `ratis_batch_reconciliation` — implementation (step 6b)
