---
type: feature
service: ratis_rewards
status: production
parent: ARCH_RATIS
sub_archs: []
related: [ARCH_REWARDS, ARCH_PRODUCT_ANALYSER, ARCH_BATCH_CONSENSUS, ARCH_admin_endpoints]
tech: [Python, SQLAlchemy, Postgres]
tables: [users, product_name_resolutions, pipeline_audit_log, batch_sync_log]
env_vars: [DATABASE_URL, NOTIFIER_URL, INTERNAL_API_KEY, ADMIN_API_KEY]
tags: [anti-fraud, nrc, trust-score, shadow-ban]
business_domain: anti-fraud
rgpd_concern: false
updated: 2026-05-02
---

# anti-fraud V1 — user trust score

> Anti-fraud system V1: `users.trust_score` computed nightly (batch), grace period, thresholds → shadow ban (NRC hook `weight_override` + silent CAB skip). No user-facing screen, invisible action.
> @tags: anti-fraud nrc trust-score shadow-ban v1 batch-nightly weight-override grace-period silent-skip users-trust_score
> @status: LIVRÉ V0
> @subs: auto

> [[ARCH_RATIS]] · relations : [[ARCH_REWARDS]], [[ARCH_PRODUCT_ANALYSER]], [[ARCH_BATCH_CONSENSUS]]

## Index

- [Summary in one sentence](#summary-in-one-sentence)
- [V1 scope](#v1-scope)
- [trust_score definition](#trust_score-definition)
- [Grace period](#grace-period)
- [Thresholds](#thresholds)
- [Shadow ban effects](#shadow-ban-effects)
- [Computation (batch nightly)](#computation-batch-nightly)
- [Ledger hook: weight_override](#ledger-hook-weight_override)
- [CAB reward hook](#cab-reward-hook)
- [Admin endpoints](#admin-endpoints)
- [DB schema](#db-schema)
- [Implementation checklist](#implementation-checklist)
- [Backlog (V2+)](#backlog-v2)

---

## Summary in one sentence

Each user carries a `trust_score` 0–100 recomputed every night based on
the agreement between their contributions to the `product_name_resolutions`
ledger and the current consensus of the relevant labels; below 65
(with ≥ 100 consensual contributions), they are silently switched to
shadow ban: their votes carry weight 0 in the consensus, their scans no
longer open any CAB reward.

## V1 scope

- No automatic hard ban. Hard ban remains a manual admin action
  (toggle via PATCH).
- No Terms of Service mention in this release (backlog).
- No mobile / front-end (separate SA block E).
- Batch nightly computation, not real-time.

## trust_score definition

`trust_score` = `round(agreed / total × 100)` where:

- `total`: number of contributions from the user (`match_method` ∈
  {`barcode`, `manual_admin`, `fuzzy_pending`, `observed_name`}) on
  `(store_id, normalized_label)` pairs whose current consensus state
  is `VERIFIED` or `UNVERIFIED` (read from the last
  `consensus_state_changed` of `pipeline_audit_log` for each pair).
- `agreed`: subset of `total` where the `product_ean` voted by the
  user equals the `top1_ean` of the last persisted state.

When `total = 0`, the score stays at `50` (neutral — neither clean nor
suspect). This is also the default value in the schema.

Rounding is implemented with integer arithmetic
(`(agreed * 100 + total // 2) // total`) to remain deterministic across
all platforms — the float `round()` drifts at the 0.5 boundary.

## Grace period

`total < 100` ⇒ no penalty is applied, even if the score is
mathematically very low. The rationale: a new user who makes mistakes on
2 scans out of 5 would drop to 60% and be wrongly shadow-banned. The
100 scans provide a fair statistical window before any effect.

## Thresholds

From `total >= 100`:

| trust_score | penalty | user notif | admin flag |
|---|---|---|---|
| `>= 75` | none | no | no |
| `[65, 75)` | warning | push (visible) | yes (admin queue) |
| `< 65` | auto shadow ban | **silent** | yes |

Bounds: inclusive at the bottom, exclusive at the top.

## Shadow ban effects

- All future contributions from the user are written with
  `weight_override = 0` (audit trail preserved, vote carries zero weight).
- `distinct_validators` ignores rows with weight 0 — a banned user
  cannot satisfy the quorum on their own.
- The user's scans no longer open any CAB / XP / mission /
  challenge / battlepass / mystery reward (silent skip in
  `cab_service.handle_scan_accepted`).
- No mobile notification, no UI feedback — this is **the whole point**
  of the penalty (an informed fraudster migrates to another account).
- The batch **never auto-unbans**, even if the score rises back above 75.
  Lifting the ban requires an explicit admin PATCH —
  prevents an attacker from "washing" themselves by spamming a few
  correct scans.

## Computation (batch nightly)

`batch/ratis_batch_trust_score/trust_score.py`

Pipeline:

1. Selects the latest `consensus_state_changed` per
   `(store_id, normalized_label)` via `DISTINCT ON` on
   `pipeline_audit_log` (PG-idiomatic newest-row-per-group).
2. Joins with `product_name_resolutions` filtered on contributing methods
   + `users.is_deleted = false`.
3. Aggregates by `user_id`: `total`, `agreed`, plus the previous
   `trust_score` / `is_shadow_banned` for transition detection.
4. For each user: computes `trust_score`, decides
   `is_shadow_banned`, decides `should_warn` (only on the
   *transition* `>=75 → [65,75)`).
5. UPDATE `users` (atomic per row, `db.commit()` per user).
6. Fire-and-forget notification via `ratis_core.notifier_client`
   (`notif_type='trust_score_warning'`, payload `{trust_score: int}`)
   for warnings only. Shadow bans = silence.
7. INSERT `batch_sync_log` (status=success/failed, rows_affected).

Idempotent: successive runs without new data → no-op (warning transitions
are guarded by comparison against `previous_score`).

## Ledger hook: weight_override

Migration: `ALTER TABLE product_name_resolutions ADD COLUMN
weight_override INT NULL`.

In `repositories/name_resolution_writes.py::record_resolution`:

```python
weight_override = _shadow_ban_weight_override(db, user_id=user_id)
# → 0 if is_shadow_banned, otherwise NULL
INSERT ... weight_override = :weight_override
```

In `repositories/name_resolution_repository.py::get_consensus_for_label`:

```python
weight_expr = func.coalesce(
    ProductNameResolution.weight_override, method_weight_expr
)
# distinct_validators and top1/top2 use this weight_expr
```

Consequences:

- The INSERT always takes place (append-only respected). No row is
  ever skipped — the audit trail remains complete.
- The consensus computation ignores rows with weight 0 (sum + distinct
  validators).
- `evaluate_state_transition` is always called after the INSERT,
  but its result will be identical to the previous state since the new
  row carries weight 0 → no spurious transition.

## CAB reward hook

In `webservices/ratis_rewards/services/cab_service.py::handle_scan_accepted`:

```python
if _is_shadow_banned(db, user_id):
    logger.info("scan_accepted: skipped rewards ...")
    return
```

The skip is silent on the API side (`200 ok` returned by the internal
webhook — it is `fire-and-forget`, the PA does not check the return value).
CAB / XP / missions / challenges / battlepass / mystery are all bypassed
at a single point.

## Admin endpoints

Host service: `ratis_rewards` (RW) · prefix `/api/v1` · auth
`ADMIN_API_KEY` (gate `verify_admin_key`).

### `GET /admin/trust-scores`

Query:
- `status`: `warning | shadow_banned | all` (default `all`)
- `limit`: 1..200 (default 50)
- `offset`: 0+ (default 0)

Response:
```json
{
  "total": 42,
  "users": [
    {
      "id": "<uuid>",
      "support_id": "RTS-XXXXXX",
      "trust_score": 60,
      "total_resolved_scans": 130,
      "is_shadow_banned": false,
      "trust_score_updated_at": "2026-05-02T03:00:00+00:00"
    }, ...
  ]
}
```

Sort: `trust_score ASC, total_resolved_scans DESC, id ASC` — the worst +
most visible first. Soft-deleted users (`is_deleted`) excluded.

### `PATCH /admin/users/{user_id}/shadow-ban`

Headers: `X-Admin-Operator: <handle>` (audit).

Body:
```json
{ "enabled": true|false, "reason": "<3..500 chars>" }
```

Response `200`:
```json
{ "user_id": "...", "is_shadow_banned": true, "previous": false }
```

Effect:
- UPDATE `users.is_shadow_banned`.
- INSERT `pipeline_audit_log` event `user_shadow_ban_changed` with
  `{operator, reason, previous, new}`.

Errors:
- 404 `user_not_found` — unknown user.
- 403 `forbidden` — bad ADMIN_API_KEY.

## DB schema

`users`:

| col | type | default | note |
|---|---|---|---|
| `trust_score` | `INT` | `50` | `CHECK 0..100` |
| `total_resolved_scans` | `INT` | `0` | denorm count of consensual contributions |
| `is_shadow_banned` | `BOOL` | `false` | silent flag |
| `trust_score_updated_at` | `TIMESTAMPTZ` | `NULL` | last batch touch |

Partial index `idx_users_trust_score`:
```sql
CREATE INDEX idx_users_trust_score ON users (trust_score)
WHERE trust_score < 75 AND total_resolved_scans >= 100
```
(Used by the admin queue query; rows outside the warning/banned zone are
never displayed.)

`product_name_resolutions`:

| col | type | default | note |
|---|---|---|---|
| `weight_override` | `INT` | `NULL` | if non-NULL, replaces the weight derived from `match_method` |

## Implementation checklist

- [x] Migration `20260502_1000_anti_fraud_v1.py` — columns + index +
  CHECK + `weight_override`.
- [x] `User` model updated (4 new fields).
- [x] `ProductNameResolution` model updated (`weight_override`).
- [x] `record_resolution` reads `is_shadow_banned` and sets
  `weight_override = 0` for banned users.
- [x] `get_consensus_for_label` aggregates via `coalesce(weight_override,
  method_weight)` and excludes rows with weight 0 from the quorum.
- [x] `handle_scan_accepted` silent skip for shadow ban.
- [x] Batch `ratis_batch_trust_score` (entrypoint + tests).
- [x] Endpoint `GET /admin/trust-scores`.
- [x] Endpoint `PATCH /admin/users/{user_id}/shadow-ban`.
- [x] ARCH (this file).
- [ ] Terms of Service mention (backlog) — separate PR.
- [ ] Mobile frontend (separate PR — block E).
- [ ] GitHub Actions cron workflow for the batch (to be wired once prod-execution is ready).
- [ ] Service `docker-compose.prod.yml` profile `batch_trust_score`
  (to be wired at the same time as the other batches during
  Hetzner deployment).

## Backlog (V2+)

- Progressive auto-recovery (e.g. score ≥ 90 + 50 new clean scans
  → auto-unban). Today a human must confirm.
- Temporal trust score (decay: a user whose contributions all date
  from > 6 months ago deserves a progressive re-test).
- Aggregate fraud signals: `past_challenges_count`, `barcode_vs_manual_ratio`,
  device fingerprint consistency — see `ARCH_name_resolution_consensus.md`
  § "Out of scope".
- Explicit Terms of Service mention: "manifestly false scans may result
  in a silent limitation of your account".
