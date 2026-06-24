# ARCH — Admin endpoints (cross-service)

> Cross-service ARCH for `/admin/*` endpoints (PA, AU, RW) + mini FastAPI+HTMX UI: observe (audit-log, parsed_tickets), curate (knowledge tables), validate (user_suggested stores), correct (admin override), replay, stats. Gated by `ADMIN_API_KEY` + `X-Admin-Operator`.
> @tags: admin endpoints cross-service admin-api-key x-admin-operator audit-log parsed_tickets knowledge-tables override htmx alpha-beta draft
> @status: EN-COURS
> @subs: auto

**Status**: draft 2026-04-30
**Owner**: Belladynn (sole admin D+30, re-evaluate OAuth/JWT if ≥2 ops)
**Scope**: `/admin/*` endpoints cross-service (PA, AU, RW) + mini FastAPI+HTMX UI

---

## Why this ARCH

The pipeline_v3 (cf. `webservices/ratis_product_analyser/ARCH_receipt_pipeline.md`) introduces new tables (`parsed_tickets`, `pipeline_audit_log`) and a rich post-processing state (status `matched|unresolved|rejected` + `match_method` + `rejected_reason` + `top_candidates` + `decision_inputs`). To exploit this pipeline in alpha → beta, admin endpoints are needed to:

- **Observe** what is happening (audit-log, parsed_tickets browse, drops by reason)
- **Curate** the knowledge tables (ocr_knowledge / product_knowledge — manual queue)
- **Validate** user_suggested stores (cf. `ARCH_store_validation.md`)
- **Correct** bad matches (admin override per scan)
- **Replay** a parsed_ticket with fresh knowledge
- **Stats / dashboards** to track OCR quality / matched rate

This document lists the endpoints + the auth pattern + the implementation plan. It is complemented by a RW audit in parallel (section "RW endpoints" below, to be filled in).

---

## Authentication — fixed pattern

**All** `/admin/*` routes (cross-service) are gated by:

```
Header: Authorization: Bearer <ADMIN_API_KEY>
Header: X-Admin-Operator: <handle>     # ex: 'guillaume', 'bob'
```

- `ADMIN_API_KEY`: single-shared env var. Validated via `ratis_core.deps.verify_admin_key` (existing RW + PA pattern). Mounted only if `ADMIN_API_KEY` is set at lifespan (otherwise `/admin/*` returns 404 — defense in depth).
- `X-Admin-Operator`: honor-system self-identifier (the admin writes their handle). Logged in `pipeline_audit_log` (event `admin_operation`) or equivalent table on the RW side. No crypto validation — intended solely for human traceability.

**Why no JWT role=admin**: account compromise = admin compromise. With `ADMIN_API_KEY`, rotation = simple env var rotation, with no front login flow to maintain. Remains valid as long as we have ≤2 ops.

**Future migration** (post-beta, ≥3 ops): Google OAuth admin with email whitelist, or per-person keys. Documented in `DECISIONS_PENDING.md`.

---

## Central index — ALL `/admin/*` endpoints

> **Source of truth**: for details (body, query, audit, errors, implementation status), follow the `ARCH source` column:
> - `ARCH_admin_endpoints` → this file (below, sections per service)
> - `ARCH_admin_settings` → `ARCH_admin_settings.md`
> - `ARCH_anti_fraud` → `ARCH_anti_fraud.md`
>
> This index is **cross-ARCH** — a dev looking for "all admin endpoints" reads this table alone. Every endpoint prefixed `/api/v1/admin/*` must appear here (the prefix is omitted below for readability). Auth is implicit: `Authorization: Bearer ADMIN_API_KEY` everywhere, plus `X-Admin-Operator: <handle>` on mutating routes and `verify_totp_dep` on financial-sensitive actions (indicated in the Auth column).

| Service | Method + Path | ARCH source | Auth | Description |
|---|---|---|---|---|
| AU | GET /admin/users | ARCH_admin_endpoints | ADMIN_API_KEY | Paginated user list (filters email_contains, created_since, is_deleted) — no password_hash. |
| AU | GET /admin/users/{user_id} | ARCH_admin_endpoints | ADMIN_API_KEY | User detail + aggregates (refresh_tokens_active, subscription_status, cashback_withdrawal_count). |
| AU | POST /admin/users/{user_id}/anonymize | ARCH_admin_endpoints | ADMIN_API_KEY + Operator | Force GDPR DELETE /account from support side. |
| PA | GET /admin/parsed-tickets | ARCH_admin_endpoints | ADMIN_API_KEY | Paginated parsed_tickets list (query `status=unresolved&limit=N`). |
| PA | GET /admin/parsed-tickets/{id} | ARCH_admin_endpoints | ADMIN_API_KEY | View parsed_jsonb + raw_ticket_image_hash + linked scans. |
| PA | POST /admin/parsed-tickets/{id}/replay | ARCH_admin_endpoints | ADMIN_API_KEY + Operator | Re-run async Phase 3+4 on all items (Celery, returns task_id). |
| PA | GET /admin/pipeline/audit-log | ARCH_admin_endpoints | ADMIN_API_KEY | Lineage debug (filters receipt_id, parsed_ticket_id, scan_id, since, phase, level, limit). |
| PA | GET /admin/pipeline/stats | ARCH_admin_endpoints | ADMIN_API_KEY | Counts by status + latency p50/p99 + top rejected_reason (`group_by=store|day|reason`). |
| PA | POST /admin/batch/consensus/run | ARCH_admin_endpoints | ADMIN_API_KEY + Operator | Trigger ad-hoc batch consensus (Phase 3 store validation). |
| PA | GET /admin/receipts/{receipt_id} | ARCH_admin_endpoints | ADMIN_API_KEY | 360° view: receipt + parsed_ticket + scans + items_match + audit log. |
| PA | PATCH /admin/scans/{scan_id} | ARCH_admin_endpoints | ADMIN_API_KEY + Operator | Admin override on 1 scan (product_ean, status, match_method, store_id, rejected_reason). |
| PA | POST /admin/scans/{scan_id}/replay-match | ARCH_admin_endpoints | ADMIN_API_KEY + Operator | Re-run Phase 3 ONLY on this scan (sync). |
| PA | GET /admin/stores | ARCH_admin_endpoints | ADMIN_API_KEY | Browse stores (query `validation_status=pending|confirmed|disabled`, limit, offset). |
| PA | PATCH /admin/stores/{store_id}/disable | ARCH_admin_endpoints | ADMIN_API_KEY + Operator | Soft-delete (`is_disabled=true`). |
| PA | PATCH /admin/stores/{store_id}/geocode | ARCH_admin_endpoints | ADMIN_API_KEY + Operator | Set lat/lng manually (user_suggested arrives with 0/0). |
| PA | PATCH /admin/stores/{store_id}/validate | ARCH_admin_endpoints | ADMIN_API_KEY + Operator | Force-confirm a user_suggested store (audit `store_validation_history`). |
| PA | POST /admin/stores/validate-bulk | ARCH_admin_endpoints | ADMIN_API_KEY + Operator | Batch-validate stores (atomic, idempotent). |
| PA | GET /admin/knowledge/ocr-queue | ARCH_admin_endpoints | ADMIN_API_KEY | Queue raw_ocr WHERE corrected IS NULL (limit, order seen_count DESC). |
| PA | PATCH /admin/knowledge/{id} | ARCH_admin_endpoints | ADMIN_API_KEY + Operator | Apply manual correction (`corrected: str | null`, null=dismiss). |
| PA | GET /admin/knowledge/product-queue | ARCH_admin_endpoints | ADMIN_API_KEY | Same queue on product_knowledge side (mapping raw_ocr → ean). |
| PA | PATCH /admin/product-knowledge/{id} | ARCH_admin_endpoints | ADMIN_API_KEY + Operator | Apply ean mapping (`ean: str | null`). |
| PA | GET /admin/name-resolutions/queue | ARCH_admin_endpoints | ADMIN_API_KEY | NRC arbitration queue (filters state=unverified\|controverse\|all, store_id). |
| PA | GET /admin/name-resolutions/unmatched | ARCH_admin_endpoints | ADMIN_API_KEY | Scans without consensus but with candidate_eans (fuzzy fallback). |
| PA | GET /admin/name-resolutions/{store_id}/{normalized_label} | ARCH_admin_endpoints | ADMIN_API_KEY | Detail (current_state, resolutions, timeline). |
| PA | POST /admin/name-resolutions/resolve | ARCH_admin_endpoints | ADMIN_API_KEY + Operator | Record resolution method=manual_admin (weight 5×, audit). |
| PA | POST /admin/name-resolutions/reject-challenges | ARCH_admin_endpoints | ADMIN_API_KEY + Operator | Re-promote previously_verified_ean if state=unverified. |
| PA | POST /admin/name-resolutions/{store_id}/{normalized_label}/escalate | ARCH_admin_endpoints | ADMIN_API_KEY + Operator | Flag-only audit event `admin_name_resolution_escalate`. |
| PA | GET /admin/tasks/{task_id}/status | ARCH_admin_endpoints | ADMIN_API_KEY | Polling status of async tasks (Celery). |
| PA | GET /admin/users/{user_id}/scans | ARCH_admin_endpoints | ADMIN_API_KEY | Scan history for a user (relation-style endpoint, hosted in PA). |
| RW | GET /admin/cab/anomalies | ARCH_admin_endpoints | ADMIN_API_KEY | Users with balance ≠ SUM(txns) or suspicious scan volume/h (read-only V1). |
| RW | POST /admin/cab/adjustment | ARCH_admin_endpoints | ADMIN_API_KEY + Operator + TOTP | Ad-hoc manual mutation (datafix/compensation, `reference_type='admin'`, audit). |
| RW | GET /admin/cab/users/{user_id}/balance | ARCH_admin_endpoints | ADMIN_API_KEY | Balance + last activity + inconsistency flag (balance vs SUM(txns)). |
| RW | POST /admin/cab/users/{user_id}/recompute-balance | ARCH_admin_endpoints | ADMIN_API_KEY + Operator | Idempotent sync recompute (lock SELECT FOR UPDATE). |
| RW | GET /admin/cab/users/{user_id}/transactions | ARCH_admin_endpoints | ADMIN_API_KEY | Audit txns for a user (filters direction, reference_type, since, limit, offset). |
| RW | GET /admin/cashback/users/{id}/history | ARCH_admin_endpoints | ADMIN_API_KEY | Full cashback history (txns + withdrawals). |
| RW | POST /admin/cashback/users/{id}/recompute-balance | ARCH_admin_endpoints | ADMIN_API_KEY + Operator | Same as CAB (sync, atomic). |
| RW | GET /admin/cashback/withdrawals | ARCH_admin_endpoints | ADMIN_API_KEY | Runa/Stripe queue (filters status=pending\|processed\|failed, limit, offset). |
| RW | PATCH /admin/cashback/withdrawals/{id}/refuse | ARCH_admin_endpoints | ADMIN_API_KEY + Operator + TOTP | Refusal + optional balance refund + audit. |
| RW | PATCH /admin/cashback/withdrawals/{id}/validate | ARCH_admin_endpoints | ADMIN_API_KEY + Operator + TOTP | Confirm withdrawal (call Stripe payout, atomic UPDATE status=processed). |
| RW | GET /admin/stats/cab | ARCH_admin_endpoints | ADMIN_API_KEY | Total CAB issued vs debited, top earners/spenders, rate by reason. |
| RW | GET /admin/stats/cashback | ARCH_admin_endpoints | ADMIN_API_KEY | Global issued/validated/refused, top stores. |
| RW | GET /admin/stats/missions | ARCH_admin_endpoints | ADMIN_API_KEY | Completion rate by template, freeze/boost rate. |
| RW | POST /admin/battlepass/seasons | ARCH_admin_endpoints | ADMIN_API_KEY + Operator | BP season creation (beta CRUD). |
| RW | PATCH /admin/battlepass/seasons/{id}/activate | ARCH_admin_endpoints | ADMIN_API_KEY + Operator | BP season activation. |
| RW | POST /admin/battlepass/seasons/{id}/tiers | ARCH_admin_endpoints | ADMIN_API_KEY + Operator | Add BP tiers. |
| RW | POST /admin/battlepass/users/{id}/reset | ARCH_admin_endpoints | ADMIN_API_KEY + Operator | User-level BP reset datafix. |
| RW | POST /admin/missions/templates | ARCH_admin_endpoints | ADMIN_API_KEY + Operator | Mission templates CRUD (create). |
| RW | PATCH /admin/missions/templates/{id} | ARCH_admin_endpoints | ADMIN_API_KEY + Operator | Mission templates CRUD (update). |
| RW | POST /admin/missions/users/{user_mission_id}/force-complete | ARCH_admin_endpoints | ADMIN_API_KEY + Operator | User-level force-complete datafix. |
| RW | POST /admin/streak/users/{id}/repair | ARCH_admin_endpoints | ADMIN_API_KEY + Operator | Free override (vs `/streak/repair` user-paid CAB). |
| RW | POST /admin/xp/tiers | ARCH_admin_endpoints | ADMIN_API_KEY + Operator | XP tier definition. |
| RW | GET /admin/referral/payouts | ARCH_admin_endpoints | ADMIN_API_KEY | Debug batch_referral_payout (filter `status=pending`). |
| RW | POST /admin/referral/payouts/{id}/force-issue | ARCH_admin_endpoints | ADMIN_API_KEY + Operator | Bypass 30-day anti-churn (support datafix). |
| RW | GET /admin/referral/users/{id}/history | ARCH_admin_endpoints | ADMIN_API_KEY | Referrer → referrals + payouts. |
| RW | GET /admin/gift-cards/brands | ARCH_admin_endpoints | ADMIN_API_KEY | List active brands (post-Runa KYB). |
| RW | POST /admin/gift-cards/brands | ARCH_admin_endpoints | ADMIN_API_KEY + Operator | Brand creation (post-Runa KYB). |
| RW | GET /admin/gift-cards/orders | ARCH_admin_endpoints | ADMIN_API_KEY | Stuck orders (KP-17, filter `status=pending\|failed`). |
| RW | POST /admin/gift-cards/orders/{id}/retry | ARCH_admin_endpoints | ADMIN_API_KEY + Operator | Retry Runa call. |
| RW | GET /admin/settings | ARCH_admin_settings | ADMIN_API_KEY | List {section: data} (all sections). |
| RW | GET /admin/settings/{section} | ARCH_admin_settings | ADMIN_API_KEY | Section data. |
| RW | PUT /admin/settings/{section} | ARCH_admin_settings | ADMIN_API_KEY + Operator | Replace section data (allowlist editable_sections, 403 if frozen). |
| RW | POST /admin/settings/seed | ARCH_admin_settings | ADMIN_API_KEY + Operator | Re-seed from JSON file-system (idempotent). |
| RW | GET /admin/settings/audit | ARCH_admin_settings | ADMIN_API_KEY | List mutations (filters section, status, limit, offset). |
| RW | GET /admin/settings/audit/{id} | ARCH_admin_settings | ADMIN_API_KEY | Audit row detail + diff (on-the-fly fallback). |
| RW | GET /admin/settings/{section}/editable | ARCH_admin_settings | ADMIN_API_KEY | Allowlist introspection `{editable: bool}`. |
| RW | POST /admin/settings/{section}/confirm-2fa | ARCH_admin_settings | ADMIN_API_KEY + Operator + TOTP | Confirm a PUT put in pending_2fa (variation > 50% on a numeric key). |
| RW | POST /admin/settings/{section}/cancel-pending | ARCH_admin_settings | ADMIN_API_KEY + Operator | Cancel a pending_2fa PUT during the grace period. |
| RW | GET /admin/trust-scores | ARCH_anti_fraud | ADMIN_API_KEY | Trust-score queue (filters status=warning\|shadow_banned\|all, limit, offset, sort trust_score ASC). |
| RW | PATCH /admin/users/{user_id}/shadow-ban | ARCH_anti_fraud | ADMIN_API_KEY + Operator | Toggle `is_shadow_banned` + INSERT pipeline_audit_log (event `user_shadow_ban_changed`). |

---

## Endpoint list (PA — pipeline_v3)

### P0 (alpha → beta D+30)

#### Observability

```
GET /api/v1/admin/receipts/{receipt_id}
    → 360° view: receipt + parsed_ticket + linked scans + items_match + audit log
    → useful: "what happened on this ticket?"

GET /api/v1/admin/parsed-tickets/{id}
    → returns parsed_jsonb + raw_ticket_image_hash + linked scans
    → useful: reproduce/inspect a parse

GET /api/v1/admin/pipeline/audit-log
    Query : receipt_id | parsed_ticket_id | scan_id | since | phase | level | limit
    → lineage debug, filterable by entity or phase
    → SQL query spec'd in ARCH_receipt_pipeline.md § Traçabilité, exposed via HTTP
```

#### Manual fix (scan-level correction)

```
PATCH /api/v1/admin/scans/{scan_id}
    Body : { product_ean?, status?, match_method?='manual_admin', store_id?, rejected_reason? }
    → admin override on 1 scan
    → audit : INSERT pipeline_audit_log with phase='manual', event='admin_scan_override',
      payload={diff before/after, X-Admin-Operator}
    → valid transition validation via CHECK constraints already in place on DB

POST /api/v1/admin/scans/{scan_id}/replay-match
    → re-run Phase 3 ONLY on this scan with fresh knowledge
    → SYNC (1 scan ≈ fast < 200ms)
    → useful after knowledge curation: "the system has learned, retry this scan"
```

#### Full replay

```
POST /api/v1/admin/parsed-tickets/{id}/replay?log_level=verbose
    → re-run complete Phase 3+4 on all items in the parsed_ticket
    → ASYNC via Celery task, returns {task_id}
    → log_level=verbose by default (cf. user workflow "3 replays + 1 fetch logs")

GET /api/v1/admin/tasks/{task_id}/status
    → polling status of async tasks
```

#### Store validation (cf. ARCH_store_validation)

```
GET /api/v1/admin/stores
    Query : validation_status=pending|confirmed|disabled, limit, offset
    → browse stores awaiting validation

PATCH /api/v1/admin/stores/{store_id}/validate
    → force-confirm a user_suggested store
    → audit : INSERT store_validation_history with triggered_by='admin:<X-Admin-Operator>'

POST /api/v1/admin/stores/validate-bulk
    Body : { ids: [uuid1, uuid2, ...] }
    → atomic in transaction, idempotent
    → useful for batch-validate via UI checkbox multi-select OR curl script

PATCH /api/v1/admin/stores/{store_id}/disable
    → soft-delete (is_disabled=true)

PATCH /api/v1/admin/stores/{store_id}/geocode
    Body : { lat: float, lng: float }
    → set lat/lng manually (user_suggested arrives with lat/lng=0)
```

### P1

#### Knowledge curation

```
GET /api/v1/admin/knowledge/ocr-queue?limit=50
    → SELECT raw_ocr WHERE corrected IS NULL ORDER BY seen_count DESC
    → manual queue (cf. TRAINING.md)

PATCH /api/v1/admin/knowledge/{id}
    Body : { corrected: str | null }   # null = dismissal (skip this raw_ocr)
    → apply manual correction

GET /api/v1/admin/knowledge/product-queue
    → same for product_knowledge (mapping raw_ocr → ean)

PATCH /api/v1/admin/product-knowledge/{id}
    Body : { ean: str | null }   # null = no mapping (stay unresolved)
```

#### Browse & list

```
GET /api/v1/admin/parsed-tickets?status=unresolved&limit=N
    → paginated list to identify where drops are most frequent
```

#### Name Resolution Consensus — arbitration queue (cf. ARCH_name_resolution_consensus.md § Bloc D)

```
GET /api/v1/admin/name-resolutions/queue
    Query : state=unverified|controverse|all (default all),
            store_id (uuid optional), limit (≤200), offset
    Auth  : ADMIN_API_KEY only (read-only)
    → labels requiring arbitration: weighted top_eans, distinct_validators,
      previously_verified_ean (if state=unverified), challenger_count,
      sample_scans (3), sorted unverified-first then last_resolution_at DESC

GET /api/v1/admin/name-resolutions/unmatched
    Query : store_id?, limit, offset
    Auth  : ADMIN_API_KEY only
    → scans without consensus but with stored candidate_eans (fuzzy fallback)
      → grouped (store, label) + top_candidates aggregation from scans.candidate_eans

GET /api/v1/admin/name-resolutions/{store_id}/{normalized_label:path}
    Auth  : ADMIN_API_KEY only
    → full detail: current_state, resolutions (with is_challenger flag),
      timeline events consensus_state_changed

POST /api/v1/admin/name-resolutions/resolve
    Body  : { store_id, normalized_label, target_ean, operator_note? (≤300) }
    Auth  : ADMIN_API_KEY + X-Admin-Operator
    → record_resolution(method='manual_admin', user_id=RTS-ADMIN0, weight 5×)
    → fallback synthetic anchor scan if all existing scans already have
      a ledger row for (store, label)
    → audit : 'admin_name_resolution_resolve' (operator + note + target_ean)
    → state will likely → verified if target_ean wins the calculation

POST /api/v1/admin/name-resolutions/reject-challenges
    Body  : { store_id, normalized_label, operator_note? }
    Auth  : ADMIN_API_KEY + X-Admin-Operator
    → 422 state_mismatch if state ≠ unverified
    → re-promote previously_verified_ean (read from last verified event)
    → emit consensus_state_changed with extra_payload
      {action: "challenges_rejected", rejected_user_ids: [...], operator, operator_note}

POST /api/v1/admin/name-resolutions/{store_id}/{normalized_label:path}/escalate
    Body  : { operator_note? }
    Auth  : ADMIN_API_KEY + X-Admin-Operator
    → flag-only audit event 'admin_name_resolution_escalate' (no business side-effect)
```

Convention `user_id` for admin actions: seed user `RTS-ADMIN0` (`admin@ratis.internal`, `provider='internal'`) added via migration `20260501_2000_nrcD`. The `provider_check` constraint is extended to accept `'internal'`. Lookup via `support_id='RTS-ADMIN0'` (the hardcoded UUID is a migration fallback).

### P2

#### Stats & maintenance

```
GET /api/v1/admin/pipeline/stats?from=&to=&group_by=store|day|reason
    → counts by status (matched/unresolved/rejected) + latency p50/p99
    → top rejected_reason (fix prioritization)

POST /api/v1/admin/batch/consensus/run
    → trigger ad-hoc batch consensus (Phase 3 store validation)
    → consistent with ARCH_store_validation
```

---

## Endpoints (AU — user management)

### P0

```
GET /api/v1/admin/users
    Query : email | id | limit
    → user lookup for support (no current endpoint exists)

GET /api/v1/admin/users/{user_id}
    → user details (without exposing sensitive PII — mask password hash etc.)
```

### P1

```
GET /api/v1/admin/users/{user_id}/scans          [PA, not AU]
    → user scan history for support
```

### P2

```
POST /api/v1/admin/users/{user_id}/anonymize
    → force GDPR DELETE /account from support side (for cases where user loses token access)
```

> Full DELETE /account lifecycle (user-initiated + admin override + DB side-effects + GDPR): see [[ARCH_AUTH]] § DELETE /account — lifecycle complet.

---

## Endpoints (RW — rewards & cashback)

Current state: **18 existing admin endpoints** across 5 files (`webservices/ratis_rewards/routes/admin/{cashback,challenges,mystery,referral,settings}.py`). `Depends(verify_admin_key)` pattern confirmed.

**Covered sub-domains**: affiliate offers, cashback CREDIT (validate/refuse only), challenges, mystery, referral (1 single datafix endpoint), settings.

**Sub-domains WITHOUT admin endpoint (gaps)**: CAB economy mutations, cashback withdrawals, BattlePass, missions, streak, XP, gift cards, global stats.

### P0 (alpha → beta D+30) — RW

#### Cashback withdrawals (real €, legal NEVER PURGE)

```
GET /api/v1/admin/cashback/withdrawals?status=pending
    → Runa queue, browse by status

PATCH /api/v1/admin/cashback/withdrawals/{id}/validate
    → confirm a withdrawal: call Runa then update status

PATCH /api/v1/admin/cashback/withdrawals/{id}/refuse
    Body : { reason: str }
    → refusal + balance refund + audit
```

**Blocker D+30**: currently no way to process a withdrawal request except direct SQL on prod.

#### CAB economy

```
POST /api/v1/admin/cab/adjustment
    Body : { user_id, direction: 'credit'|'debit', amount_cents: int, reason: str }
    → ad-hoc manual mutation (error datafix, user compensation)
    → ⚠️ KP-08: requires simultaneous update of VALID_REASONS + _CAB_REASONS + migration CHECK
    → audit : INSERT cabecoin_transactions with reference_type='admin' (NEW value — see migration below)

GET /api/v1/admin/cab/users/{user_id}/transactions
    Query : direction, reference_type, since, limit, offset
    → audit txns for a user ("where did my CABs go?")
    → critical read-only support post-alpha
```

#### Stats — CAB economy visibility

```
GET /api/v1/admin/stats/cab?from=&to=&group_by=reason|day|user
    → total CAB issued vs debited, top earners/spenders, rate by reason
    → without this: impossible to detect a leak/abuse in beta
```

### P1 — RW

#### CAB

```
GET /api/v1/admin/cab/users/{user_id}/balance
    → balance + last activity + inconsistency flag (balance vs SUM(txns))

POST /api/v1/admin/cab/users/{user_id}/recompute-balance
    → sync recompute, idempotent, lock SELECT FOR UPDATE
```

#### Cashback (complements to the existing P0 cashback CREDIT)

```
GET /api/v1/admin/cashback/users/{id}/history
    → full cashback history (txns + withdrawals)

POST /api/v1/admin/cashback/users/{id}/recompute-balance
    → same as CAB, sync
```

#### Gamification — BattlePass

```
POST /api/v1/admin/battlepass/seasons
PATCH /api/v1/admin/battlepass/seasons/{id}/activate
POST /api/v1/admin/battlepass/seasons/{id}/tiers
    → beta = S1 BP launch. Direct SQL on prod = huge risk.
    → cf PROD_CHECKLIST.md:196-197
```

#### Gamification — Missions

```
POST /api/v1/admin/missions/templates
PATCH /api/v1/admin/missions/templates/{id}
    → mission templates CRUD (PROD_CHECKLIST.md:198)
```

#### Stats

```
GET /api/v1/admin/stats/cashback
    → global issued/validated/refused, top stores

GET /api/v1/admin/stats/missions
    → completion rate by template, freeze/boost rate
```

### P2 — RW

#### Anti-fraud (read-only V1)

```
GET /api/v1/admin/cab/anomalies
    → users with balance != SUM(txns) OR suspicious scan volume/h
```

(Mutation `freeze user CAB earning` to be arbitrated post-beta.)

#### Gamification datafix

```
POST /api/v1/admin/battlepass/users/{id}/reset
POST /api/v1/admin/missions/users/{user_mission_id}/force-complete
POST /api/v1/admin/streak/users/{id}/repair       # free admin override (vs /streak/repair user-paid CAB)
POST /api/v1/admin/xp/tiers                       # PROD_CHECKLIST.md:200
```

#### Referral

```
GET /api/v1/admin/referral/users/{id}/history     # referrer → referrals + payouts
GET /api/v1/admin/referral/payouts?status=pending # debug batch_referral_payout
POST /api/v1/admin/referral/payouts/{id}/force-issue # bypass 30-day anti-churn (support datafix)
```

#### Gift cards (with Runa KYB activation — not before)

```
GET /api/v1/admin/gift-cards/orders?status=pending|failed   # KP-17 stuck orders
POST /api/v1/admin/gift-cards/orders/{id}/retry             # retry Runa call
GET/POST /api/v1/admin/gift-cards/brands                    # PROD_CHECKLIST.md:315
```

---

## Required DB migrations

### Add `'manual_admin'` to CHECK enum `scans.match_method` (PA)

```sql
ALTER TABLE scans DROP CONSTRAINT IF EXISTS ck_scans_match_method_v3;
ALTER TABLE scans ADD CONSTRAINT ck_scans_match_method_v3
  CHECK (match_method IS NULL OR match_method IN (
    'barcode', 'knowledge', 'fuzzy_strict', 'manual_admin',
    -- legacy v2 (Bloc 8 will drop) :
    'observed_name', 'fuzzy', 'fuzzy_confirmed', 'manual', 'barcode_ean'
  ));
```

→ explicit trace that the match came from a human intervention, not the automated pipeline.
→ Integrated in the P0 PA admin endpoints PR (since that is the usage for this value).

### Add `'admin'` to CHECK enum `cabecoin_transactions.reference_type` (RW)

For `POST /admin/cab/adjustment`, we need to trace admin mutations in a way that is isolable from the normal flow (scan/mission/etc.). The RW SA recommends **a new `reference_type='admin'`** rather than a simple `reason='admin_adjustment'` — better segregation for audit/stats.

⚠️ **Synchronize 3 sources simultaneously** (KP-08):
1. CHECK constraint Alembic (`reference_type IN (..., 'admin')`)
2. `webservices/ratis_rewards/repositories/cab_repository.py` — `VALID_REASONS` set
3. `webservices/ratis_rewards/services/gamification.py` — `_CAB_REASONS` or equivalent

All in the same commit, otherwise CI passes but runtime fails.

### Optional table `admin_audit_log` (RW — for financial-sensitive mutations)

The RW SA recommends **dedicating a table** for admin cashback/CAB mutations (separate legal trail from the `pipeline_audit_log` on the PA side). To be discussed — see "Decisions to arbitrate" below.

---

## Mini admin UI — FastAPI + HTMX

**Stack**: `fastapi` (already a dependency) + `htmx` (cdn) + `tailwindcss` (cdn). Served by PA under `/admin/ui/*`.

**Login**: simple form → POST `/admin/ui/login { admin_api_key, operator }` → set HTTP-only + same-site cookie with a hash of the key. Cookie used for gating UI routes. `X-Admin-Operator` header injected server-side into all backend calls.

**Priority pages (implementation order)**:

1. **parsed_tickets dashboard**: filterable table by status (matched/unresolved/rejected), date, store. Click → 360° view.
2. **360° receipt view**: left panel image (R2 if <48h), right panel parsed_ticket + scans with inline actions (manual PATCH / replay-match).
3. **Pending stores**: list with multi-select checkboxes + "Validate selection" button → POST validate-bulk. ✅ **DONE (PR UI-1)**
4. **Knowledge OCR queue**: list of raw_ocr with input for corrected + apply button. ✅ **DONE (PR UI-1)**
5. **Audit log viewer**: query box (receipt_id / parsed_ticket_id / scan_id) → audit events table filterable by phase / level. ✅ **DONE (PR UI-1)** + query-param deep-link `?scan_id=` / `?receipt_id=` / `?parsed_ticket_id=` (✅ **PR UI-1.5**).
6. **User search**: single input auto-detect (UUID / RTS-XXXXXX / partial email) → AU lookup → results table or redirect to detail. ✅ **DONE (PR UI-1.5)**
7. **User detail**: identity block (AU `/admin/users/{id}`) + paginated/filterable scans block (PA local DB) with `[audit log]` deep-link per scan. ✅ **DONE (PR UI-1.5)**

**Not priority for V1**: stats dashboards, anonymize user.

**PR UI-1 — shell + pages 3/4/5 (✅ DONE)**:
- Shell: login form + HTTP-only / SameSite=Strict cookie (token = `sha256(ADMIN_API_KEY + operator)`) + Tailwind/HTMX CDN base layout + nav. `verify_admin_key` reuse is impossible on the UI side (cookie vs Bearer) → new dep `get_admin_session` that validates constant-time via `hmac.compare_digest`, 302 redirect → `/admin/ui/login` on missing/invalid cookie. Conditional mount on `ADMIN_API_KEY` being set (defense in depth, same pattern as `/api/v1/admin/*`).
- Backend calls: in-process direct (no loopback HTTP) → reuses `services.store_admin_service.validate_stores_bulk` + `services.knowledge_admin_service.list_ocr_queue / apply_ocr_correction` + inline SQL audit-log (mirror of `routes.admin.observability`).
- Pages 1/2 (parsed_tickets dashboard / 360° receipt view) deferred: ARCH_receipt_pipeline does not block them, but (1) PR10 endpoint `GET /admin/parsed-tickets?status=` is already merged so the dashboard is PR UI-2 without a blocker; (2) 360° view already uses `GET /admin/receipts/{id}` (PR3 merged) so UI-3 without a blocker. Split out of scope of UI-1 to limit review surface.
- Tests: 27 passing (valid/invalid login, auth gate × 4 pages, logout, stores list/bulk-validate, knowledge list/correction/dismissal/audit, audit-log query × 5).
- Decisions:
  - **Cookie path scoping**: `path="/admin/ui"` (not `/`) → the session does not leak to `/api/v1/admin/*` which remains Bearer-only.
  - **Login error UX**: 200 + form re-render with inline error (not 401 JSON) — browser-friendly flow + future HTMX swap-in-place.
  - **Audit log query**: single input `entity_id` matched against `scan_id OR parsed_ticket_id OR (parsed_ticket_id resolved via receipts)`. Static SQL with CAST on all binds (psycopg requires an explicit type when doing `IS NULL OR =` on a nullable bind).
  - **Global HTTPException handler**: intercepts only `path.startswith("/admin/ui") AND status==401 AND detail=="login_required"` → 302 redirect, otherwise delegates to `fastapi.exception_handlers.http_exception_handler` (preserves existing JSON shape on other routes).
  - **Knowledge dismissal via empty form input**: POST with `corrected=""` → mapped to `None` on the route side (mirror of the JSON PATCH semantics).

**SP6 — DB write approval pages (✅ NEW)**:
- Machine endpoint `POST /api/v1/admin/db-approvals` (PA, Bearer
  `ADMIN_API_KEY`) — the n8n `db-write-pipeline` workflow registers a
  proposal that has reached the human gate; INSERTs a row in
  `db_write_approvals` with status `pending`.
- UI pages (session cookie, same patterns as existing pages):
  - `GET /admin/ui/db-approvals` — list of `pending` proposals,
    badges 🔴 silver-tables / ⚠️ enhanced review / ⚡ break-glass.
  - `GET /admin/ui/db-approvals/{submission_id}` — detail view: client
    message + investigation highlighted, args, dry-run, LLM verdict.
  - `POST /admin/ui/db-approvals/{submission_id}/approve` — approves;
    re-entry of the procedure name required if `touches_money_tables`;
    POSTs the n8n `resume_url` `{decision: "approve", operator}`.
  - `POST /admin/ui/db-approvals/{submission_id}/reject` — rejects;
    reason mandatory; POSTs `{decision: "reject", operator, reason}`.
  - `POST /api/v1/admin/db-approvals/{submission_id}/expire` — `Wait`
    node timeout branch; moves a `pending` row to `expired`.
- UI routes in a dedicated module `admin_ui/db_approvals.py` (not in the
  monolithic `routes.py`). Model + migration: `db_write_approvals`
  in `ratis_core/models/`.

**PR UI-1.5 — user search + detail + audit-log deep-link (✅ DONE)**:
- Search page `/admin/ui/users/search`: single input with server-side format auto-detect (UUID regex / `RTS-[A-HJ-NP-Z2-9]{6}` regex / fallback partial email). UUID hit → 303 redirect to detail; support_id → AU `/admin/users?support_id=` exact match (1 hit = redirect, 0 = error, multi = defensive table); email → AU `/admin/users?email_contains=` table 50 lines max + "truncated" banner if total > 50.
- Detail page `/admin/ui/users/{user_id}`: 2 blocks. Identity (id, email, support_id, provider, created_at, is_deleted, refresh_tokens_active, subscription_status, cashback_withdrawal_count) served by AU `/admin/users/{id}`. Paginated scans (limit 50 default, bookmarkable offset) served by PA via direct SQL (mirror of `routes/admin/users.py` admin_list_user_scans — no loopback HTTP). scan_type / status / since / limit filters propagated via GET params, invalid filters silently ignored (stale-URL-friendly). Each scan row has an `[audit log]` link → `/admin/ui/audit-log?scan_id=<uuid>`.
- Audit-log enrichment: route accepts 3 typed aliases (`scan_id`, `receipt_id`, `parsed_ticket_id`) which are folded into `entity_id` server-side → reuses the existing SQL OR cascade. Form pre-fill + auto-run on page-load. Explicit `entity_id` (form input) takes priority when provided.
- AU client: `admin_ui/au_client.py` async wrapper `httpx.AsyncClient` with Bearer ADMIN_API_KEY + `X-Admin-Operator: <session.operator>`. Timeout 10s. New env var **`AU_BASE_URL`** require_env-guarded in the PA lifespan if ADMIN_API_KEY is set (fail-fast). Default docker-compose: `http://ratis_auth:8001`.
- Tests: 16 passing (format detection ×8, detail rendering ×5 + scans filter/pagination, audit-log query-param ×2). Mock of `au_get` via monkeypatch on the `admin_ui.routes` module — no extra dep (httpx already transitive), no real network in tests.
- Decisions:
  - **Cross-service AU client**: new `au_client.py` rather than widening `ratis_core.notifier_client` style — the read-blocking + Bearer admin semantics differ from the fire-and-forget INTERNAL_API_KEY of the notifier. No code sharing, distinct signatures.
  - **Format detection without UI flag**: single input + server-side regex > 3 tabs or a dropdown — reduces support friction, the operator types whatever they have at hand and the system routes.
  - **Defensive multi-hit support_id**: the column has a UNIQUE constraint but we still display the list if multiple rows come back. UI cost = a few template lines, gain = explicit inconsistency signal in the DB rather than a silent crash.
  - **Email-search limit hardcoded at 50**: sufficient for V1 (alpha < 1k users). "Truncated" banner surfaces the event rather than chunking with `?offset=`.
  - **Invalid scan filters silently ignored**: a copy-pasted URL with `status=foo` renders the page without the filter + form showing the value — does not 422 the operator.
  - **Offset URL-encoded pagination**: preserves scan_type/status/since/limit in prev/next links so that a Next does not lose the active filter.
  - **Audit-log query-param folding**: 3 typed aliases (scan_id/receipt_id/parsed_ticket_id) folded into entity_id server-side — no JS, no double SQL path. The existing OR cascade absorbs the semantics.
  - **`AU_BASE_URL` env var conditional require_env**: only if `ADMIN_API_KEY` is set (the UI is only mounted in that case) — a public-only PA deployment remains valid without this config.

**PR UI-NRC — Name Resolution Consensus arbitration (Bloc D — ✅ DONE)**:
- Queue page `/admin/ui/name-resolutions/queue`: table with `state` filters (unverified/controverse/all) + `store_id`, color-coded badges (red=unverified, amber=controverse), sorted unverified-first then last_resolution_at desc. Inline buttons "Detail" + "Validate top1" (POST resolve) + "Reject chal." (POST reject-challenges, visible only if state=unverified).
- Detail page `/admin/ui/name-resolutions/{store_id}/{normalized_label:path}`: current_state + previously_verified_ean + form action target_ean + form reject-challenges (if unverified) + resolutions table with challenger rows highlighted bg-red-50 + timeline events.
- Dashboard tile + nav link "NRC Arbitration" — dashboard counter calls `list_arbitration_queue(state=all, limit=1)` to retrieve the total.
- Tests: 16 passing (auth gate ×3, queue ×4, detail ×2, POST ×4, dashboard counter ×2, nav link ×1).
- Decisions:
  - **Synthetic anchor scan**: `record_resolution` uses `ON CONFLICT (scan_id, normalized_label) DO NOTHING` — if all existing scans for `(store, label)` already have a ledger row, the admin override must create a synthetic scan (status=`pending`, scan_type=`manual`, owned by RTS-ADMIN0) to avoid being silently skipped. Trade-off: 1 additional scan row per extreme arbitration cycle; gain: the clean solution is append-only (never UPDATE on ledger).
  - **`extra_payload` in `emit_consensus_state_changed_event`**: new optional parameter to allow injection of `{action, rejected_user_ids, operator, operator_note}` on reject-challenges. A single composite audit row rather than 2 separate events (state-change + admin-action) — easier to parse on the UI timeline side.
  - **Separate audit event `admin_name_resolution_resolve`** (`phase=manual`): captures the operator intent even when the state does not change (re-affirmation). No overlap with the automatic `consensus_state_changed` — each event has a distinct role.
  - **Seed user RTS-ADMIN0**: single sentinel row `provider='internal'` (new enum member via migration `20260501_2000_nrcD`). Canonical lookup by `support_id` (the hardcoded UUID `00000000-0000-0000-0000-000000ad0001` is a migration fallback). In tests the `_get_or_create_admin_user` service lazy-INSERTs because conftest uses `create_all`, not Alembic.
  - **Reject-challenges 422 state_mismatch**: the service fails fast if `state ≠ unverified`. Stricter than "tolerate and log a warning" — prevents an operator from accidentally forcing a re-promotion on a cold-start controverse case (semantically different).

**PR UI-skills — Skills review (Hermes claude-code-postmortem)**:
- 4 endpoints under `/admin/ui/skills/...`: `GET /` (filterable list by bucket + search), `POST /{name}/promote` (candidate→active), `POST /{name}/archive` (candidate|active→archived), `POST /{name}/drop` (candidate only, destructive with browser-side confirm).
- Used to review skill candidates generated by the Hermes `claude-code-postmortem` (POC 8). The postmortem cron deposits SKILL.md files into `.claude/skill-candidates/<name>/` (gitignored), the operator uses this UI to promote them to `.claude/skills/<name>/` (versioned) or discard them to `.claude/skill-archive/<name>/` (versioned with reason).
- Service layer `admin_ui/skills_admin_service.py`: enumerates the 3 buckets, defensively parses YAML frontmatter (no `pyyaml` dep — homegrown parser tolerating `>` / `|` block scalars + quote wrappers + fallback `unknown` on malformed frontmatter). Actions = `shutil.move` between the 3 folders, refuses overwrite (409 `skill_destination_exists`).
- Append-only audit in `.claude/skill-review-audit.jsonl` (gitignored). One JSON line per mutation: `{ts, operator, action, skill, details}`. Best-effort — an audit failure logs but does not block the mutation (the filesystem mv has already happened).
- Decisions:
  - **No pyyaml dep**: Hermes frontmatter is flat (scalars + block scalars), a ~80-line homegrown parser is sufficient. Avoids adding a transitive dependency just for this page.
  - **Injectable `repo_root`**: optional argument + `RATIS_REPO_ROOT` env var (R33 — no hardcoding). Tests inject `tmp_path`, prod resolves via walk-up from `__file__`.
  - **Drop limited to candidates**: 400 `drop_only_candidates` if applied to active/archived. Forbid-check BEFORE the 404 on the candidate so the error is explicit.
  - **No 2FA**: follows the existing ADMIN_API_KEY + cookie session pattern. Skill mv touches neither money nor PII — no additional TOTP gate.
  - **No restore from archive**: V0; the operator can do `mv .claude/skill-archive/X .claude/skill-candidates/X` manually if needed. Restore via UI = V1 if frequent.

---

## Implementation plan

Proposed order (each line = 1 PR). Priority = **revenue/legal first, observability next, datafix after**.

### PR 1 — RW: cashback withdrawals (legal revenue) ✅ DONE
- [x] `GET /api/v1/admin/cashback/withdrawals?status=&limit=&offset=` — read-only, no TOTP (pagination + status pending|processed|failed filter).
- [x] `PATCH /api/v1/admin/cashback/withdrawals/{id}/validate` — gated by `verify_admin_key + verify_totp_dep`; row-level lock (`SELECT ... FOR UPDATE`) then `initiate_payout` (Stripe in prod, deterministic `sandbox-<id>` when `PAYMENT_PROVIDER_KEY` not set); atomic UPDATE status='processed' + processed_at + payment_provider_ref + provider_initiated_at (satisfies `processed_check` + `provider_coherence` in one UPDATE). Errors: 401 (TOTP), 404 (withdrawal_not_found), 409 (already_resolved), 503 (payment_provider_unavailable, PayoutError → R21).
- [x] `PATCH /api/v1/admin/cashback/withdrawals/{id}/refuse` — gated by `verify_admin_key + verify_totp_dep`; body `{reason: 3..200, refund_balance: bool default true}`; row-lock + UPDATE status='failed' + failure_reason; optional refund via `credit_cashback_balance` (UPSERT). Idempotent: 409 if already resolved.
- [x] Tests: 23 passing (list pagination/filter/status, validate TOTP gate + effects + idempotency + 404, refuse TOTP gate + effects + reason validation + refund toggle + idempotency).
- *Why first*: this is the only flow that touches real money. No other way to process a request except direct SQL on prod.

**Actioned decisions (PR1)**:
- **Provider = Stripe (existing), not Runa**. Prod code already uses `ratis_core.payout_client.initiate_payout` (sandbox-stub if `PAYMENT_PROVIDER_KEY` not set) — reused as-is. The brief mentioned "Runa" but Runa = gift cards, not payouts; no double implementation.
- **DB status mapping ↔ admin action**: `pending` (initial) → `processed` (validate) or `failed` (refuse). No new "validated"/"refused" status in the schema — respecting existing CHECKs (`status_check`, `processed_check`, `failure_check`, `provider_coherence`).
- **Minimal audit**: no separate `admin_audit_log` table for alpha. Native columns `failure_reason` (refuse) + `payment_provider_ref` (validate) are sufficient to reconstruct history. If `X-Admin-Operator` is added later for traceability, it will go in a JSONB `admin_metadata` (post-alpha).
- **`refund_balance` default = true**: a refusal with confiscation is the exception (fraud), not the rule. The default protects the user.
- **Row lock `SELECT ... FOR UPDATE`** on both validate and refuse — idempotency protection + race anti-double-action if 2 ops click in parallel.
- **PayoutError → 503** (R21 UpstreamServiceError) with detail `payment_provider_unavailable`; user can retry, the reconciliation batch will pick it up anyway.

### PR 2 — RW: CAB adjustment + audit ✅ DONE
- [x] Migration: add `'admin'` to CHECK `cabecoin_transactions.reference_type` + add `'admin_adjustment'` to CHECK `reason` + add `context` JSONB column + sync VALID_REASONS + _CAB_REASONS (KP-08, 3 sources in the same commit)
- [x] TOTP 2FA infrastructure: `services/totp_service.py` (verify_totp_dep) + `tools/setup_totp.py` (one-shot enrolment) + `ADMIN_TOTP_SECRET` env var (require_env in lifespan, .env.example, conftest)
- [x] `POST /admin/cab/adjustment` — gated by `verify_admin_key + verify_totp_dep`; inserts `cabecoin_transactions(reason='admin_adjustment', reference_type='admin', context={operator, reason})`; atomic balance UPDATE via `admin_adjust_cab` repo helper (R09); no streak/battlepass side-effects.
- [x] `GET /admin/cab/users/{user_id}/transactions` — read-only, no TOTP required; direction/reference_type/since filters + limit/offset pagination.
- [x] Tests: 24 passing (TOTP gate, mutation effects, pagination, filters)

**Actioned decisions (PR2)**:
- `reference_type='admin'` retained (SA recommendation — better segregation than `reason`-only). Also adding `reason='admin_adjustment'` for consistency with the existing check (the `reason` enum is strict, not null-OR-IN like `reference_type`).
- `context` JSONB column added to `cabecoin_transactions` (vs new `admin_audit_log` table) — minimizes schema complexity for alpha. Migration to a dedicated table if non-financial admin logs are needed (later).
- Single-admin TOTP secret via env var (vs WebAuthn / per-key) — stateless, no DB, compatible with Google Authenticator, sufficient in alpha. Migration documented in ARCH (post-beta, ≥3 ops).
- Repo helper `admin_adjust_cab(direction, amount, operator, operator_reason)` distinct from `award_cab/debit_cab` — avoids polluting the streak multiplier / battlepass progression with admin datafixes.

### PR 3 — PA: scan-level manual admin (resolves "bad match" use case)
- Migration: add `'manual_admin'` to CHECK `scans.match_method`
- `GET /admin/receipts/{receipt_id}` (360° view)
- `PATCH /admin/scans/{scan_id}` (manual override)
- `POST /admin/scans/{scan_id}/replay-match` (sync)

### PR 4 — PA: pipeline_v3 observability
- `GET /admin/pipeline/audit-log` (filterable receipt_id/parsed_ticket_id/scan_id/since/phase)
- `GET /admin/parsed-tickets/{id}`
- `POST /admin/parsed-tickets/{id}/replay` (async + verbose default)
- `GET /admin/tasks/{task_id}/status`

### PR 5 — PA: store validation
- `GET /admin/stores?validation_status=pending`
- `PATCH /admin/stores/{store_id}/validate`
- `POST /admin/stores/validate-bulk`
- `PATCH /admin/stores/{store_id}/disable`
- `PATCH /admin/stores/{store_id}/geocode`

### PR 6 — AU + PA: user lookup support
- [x] AU: `GET /admin/users` — paginated list, filters (email_contains, created_since, is_deleted), summary fields (id, email, provider, is_deleted, created_at). NEVER exposes password_hash.
- [x] AU: `GET /admin/users/{id}` — full profile + aggregates (refresh_tokens_active count, subscription_status from most-recent row, cashback_withdrawal_count). Returns soft-deleted users (support escape hatch).
- [x] PA: `GET /admin/users/{id}/scans` — paginated scan list, filters (scan_type, status, since), per-scan fields including JOINed store_name. Empty list (200) for unknown user_id (relation-style endpoint).
- Decisions:
  - AU returns 403 (not 401) on missing/wrong ADMIN_API_KEY — matches `verify_admin_key` pattern (see `routes/admin/subscription.py`).
  - No `last_login_at` / `auth_login_log` table exists in schema — column omitted, no new table created.
  - Users model field is `provider` (not `oauth_provider`) — endpoint exposes that name as-is.
  - Scan timestamp surfaced as `created_at` in payload (mapped from `scans.scanned_at`) for consistency with the standard list-endpoint shape.
  - No TOTP — these are read-only and don't touch financial state.

### PR 7 — RW: BattlePass + missions admin (support datafix beta)
- BattlePass: seasons CRUD + activate + tiers
- Missions: templates CRUD

### PR 8 — RW + PA: critical stats
- RW: `GET /admin/stats/cab` (P0 — economy visibility)
- PA: `GET /admin/pipeline/stats?from=&to=&group_by=...`

### PR 9 — PA: knowledge curation (P1)
- `GET /admin/knowledge/ocr-queue` + `PATCH /admin/knowledge/{id}`
- `GET /admin/knowledge/product-queue` + twin `PATCH`

### PR 10 — PA: browse list (P1)
- `GET /admin/parsed-tickets?status=unresolved&limit=N`

### PR 11+ — RW: P1 complements (cashback history, CAB recompute)

### PR 12+ — Mini FastAPI+HTMX UI (parallel, starts in PR 4 with static audit-log viewer)

### PR 13+ — RW P2 (anti-fraud read, gamification datafix, referral, gift-cards post-Runa)

### PR 14+ — PA P2 (anonymize, advanced stats)

**Auth pattern**: `verify_admin_key` + `X-Admin-Operator` extraction factored into `ratis_core.deps`. A single dependency injected into all admin endpoints.

**Audit pattern**: every mutating endpoint (PATCH/POST/DELETE) **MUST** emit a `pipeline_audit_log` event (PA) or equivalent `admin_audit_log` (RW) with:
- `phase='manual'` (PA) or as appropriate per service
- `event='admin_<operation>'`
- `payload={diff_before, diff_after, operator: X-Admin-Operator}`

---

## Anti-pattern note — picker UI

The ARCH `receipt_pipeline.md` § Forbidden anti-patterns point 4 says: "Picker UI: no user window shows a list of candidates to choose from. Resolution = physical barcode only."

→ This anti-pattern applies **on the user side (`ratis_client/`)**. **On the admin side**, the picker is allowed (the admin is a curated source, not unstable like a user). The mini-UI can display `top_candidates` from an ItemMatch and let the admin pick — that is precisely the intended use.

---

## Decisions to arbitrate (post-RW audit)

Questions raised by the RW audit to validate with user:

1. **`reference_type='admin'` or `reason='admin_adjustment'`** in `cabecoin_transactions`?
   - SA recommendation: new `reference_type='admin'` (better segregation for stats/audit)
   - Alternative: free `reason` (more flexible, less migration)

2. **Dedicated `admin_audit_log` table for cashback/CAB mutations**?
   - For: separate legal trail from `pipeline_audit_log`, simpler query "who did what to whose €"
   - Against: concept duplication, schema complexity
   - SA recommendation: dedicated for cashback/CAB (financial), `pipeline_audit_log` for the rest

3. **Admin 2FA for ultra-sensitive endpoints** (cashback withdraw validate, gift-cards retry, CAB adjustment)?
   - SA recommendation: not P0, document in `DECISIONS_PENDING.md` for post-beta

4. **Gamification datafix**: generic endpoint `POST /admin/datafix/{table}` or specific endpoints?
   - SA recommendation: specific (R33 clean solution, audit-friendly, per-endpoint validation)

5. **Gift cards admin**: in-scope alpha→beta D+30 or wait for Runa KYB activation?
   - SA recommendation: with Runa activation (coupled)

6. **Anti-fraud `freeze user CAB`** mutation: V1 or post-beta?
   - SA recommendation: V1 read-only (`GET /admin/cab/anomalies`), mutation post-beta

---

## Actioned decisions (reference)

- 2026-04-30: ADMIN_API_KEY + X-Admin-Operator validated (no JWT role=admin for now)
- 2026-04-30: `manual_admin` added to CHECK enum `scans.match_method`
- 2026-04-30: scan-level replay-match = SYNC, full parsed_ticket replay = ASYNC
- 2026-04-30: `parsed_tickets` remains immutable (no direct admin edit of parsed_ticket — admin edits ONLY scans)
- 2026-04-30: Mini UI = FastAPI+HTMX served by PA (not a separate service)

---

## Tracking (R31 — post-dev maintenance)

After each admin endpoints PR, update:
- This ARCH (check off the implementation plan item)
- `ENDPOINTS.md` (auto-regenerated via `python scripts/generate-endpoints-inventory.py`)
- `KNOWN_PROBLEMS.md` if a new pitfall is discovered
- `SESSION_LOG.md` closing entry
