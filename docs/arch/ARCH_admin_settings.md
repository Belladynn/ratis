---
type: sub-arch
parent: ARCH_admin_endpoints
related: [ARCH_admin_endpoints, ARCH_REWARDS, ARCH_PRODUCT_ANALYSER]
status: shipped
tags: [admin, settings, app_settings, runtime-config, audit-log]
business_domain: admin
rgpd_concern: false
updated: 2026-05-09
---

# ARCH — Admin Settings UI + Runtime Config Management

> Admin Settings UI + runtime config management: editing `app_settings` (section/data JSONB) without curl/Postman, audit log who/when/old/new, segmentation of editables vs frozen (economic parameters DB-editable vs algo parameters PR-only). Shipped Blocs A→E + security hardening.
> @tags: admin settings app_settings runtime-config audit-log shipped rewards-cab-params editables frozen htmx blocs-a-e hardening
> @status: LIVRÉ V0
> @subs: auto

> Sub-ARCH of [[ARCH_admin_endpoints]]. Also read: [[ARCH_REWARDS]] (90% of editable settings = CAB/gamif parameters), `ratis_core/ratis_core/settings.py` (load_settings DB-first + JSON fallback already in place).

> Status: ✅ **Shipped (2026-05-02 → 2026-05-03)** — Blocs A→E merged (PRs #257, #258, #264, #267, #269), followed by security hardening (#275). Code in prod: `webservices/ratis_rewards/routes/admin/settings.py`, `services/admin/settings_service.py`, model `ratis_core/models/admin_audit.py`, tests `ratis_core/tests/test_models_admin_audit.py` + `webservices/ratis_rewards/tests/admin/test_settings_endpoints.py`. Product decisions made 2026-05-02 (orchestrator + product owner). See § "Remaining work V1.x" for the post-V1 backlog.

---

## Genesis

The settings infrastructure is **already in place on the backend**:

- Table `app_settings(section TEXT PK, data JSONB, updated_at TIMESTAMPTZ)` created 2026-04-15 (migration `20260415_1400_k5l6m7n8o9p0_app_settings.py`).
- `ratis_core.settings.load_settings()` reads DB-first then silently falls back to JSON file-system.
- 3 REST endpoints `/admin/settings/*` (GET list + GET section + PUT replace section + POST seed) on the RW side, gated by `verify_admin_key`.
- `seed_settings(db)` idempotent ON CONFLICT DO UPDATE from `ratis_settings.json`.

**What is missing**:

1. **Browser-based admin UI** for editing settings without curl/Postman (daily friction).
2. **Audit log** `who/when/old/new` — today only `app_settings.updated_at` exists (no operator, no diff).
3. **Segmentation editables vs frozen**: some settings are **economic parameters** (CAB ratio, subscription multiplier) — editable in prod without algo risk. Others are **algo parameters** (matcher thresholds, fuzzy thresholds, OCR) — modifiable only via git PR for traceability.

**Product decision (validated 2026-05-02)**:

> **The admin UI lives in PA** (status quo, moving to RW = V2 debt). We add a settings page that calls RW via a new cross-service client `rw_client.py` (modelled on `au_client.py`). Audit log = dedicated table. V1 validation = JSON parseable only. Service reload = manual restart after save (TTL/pubsub = V2).

---

## Implementation plan by blocs

| Bloc | Description | Status |
|---|---|---|
| **A — backend write service + audit table** | Migration `admin_settings_audit(id, timestamp, operator, section, old_data, new_data)` · helper `update_settings_section(db, section, data, operator)` in `ratis_core/settings.py` · INSERT audit row on each write · seed_settings unchanged. | ✅ V1 |
| **B — REST settings endpoints (RW) reinforced** | Keep the 3 existing endpoints `/admin/settings/*`. Add `GET /admin/settings/audit?section=&limit=&offset=` (paginated). PUT verifies editable_sections allowlist (returns 403 if frozen). Header `X-Admin-Operator` propagated to audit. | ✅ V1 |
| **C — cross-service client `rw_client.py` (PA)** | `webservices/ratis_product_analyser/admin_ui/rw_client.py` modelled on `au_client.py`: `rw_get` + `rw_put` with Bearer ADMIN_API_KEY + X-Admin-Operator. Timeout 10s. No pool (admin = human-paced). | ✅ V1 |
| **D — admin UI pages** | `admin_ui/templates/settings_list.html` (section list + 🔓/🔒) · `settings_detail.html` (raw JSON textarea + diff preview + save). Dashboard tile. Routes `GET /admin/ui/settings`, `GET /admin/ui/settings/{section}`, `POST /admin/ui/settings/{section}`. Auth = existing session cookie. | ✅ V1 |
| **E — UI audit log** | Page `GET /admin/ui/settings/audit` listing recent mutations (filter by section). | ✅ V1 |
| **F — Pydantic validation per section** | Add Pydantic v2 schemas per section (rewards, xp, missions...) for fail-fast typing. | 🔁 V2 |
| **G — Hot-reload** | TTL cache 60s on `load_settings()` OR Redis pubsub `settings_invalidate`. Today: manual restart after save. | 🔁 V2 |
| **H — UI migration to RW** | Move `admin_ui/` PA → RW (the transverse admin has no reason to live in PA — a scan business-logic service). | 🔁 V2 |

---

## Editable vs frozen sections (V1)

`ratis_settings.json` contains 25 sections. V1 segmentation (validated 2026-05-02):

### 🔓 Editable via UI (8)

Economic / behavioural parameters — modifiable in prod without algo risk:

- `rewards` — CAB ratios, earns per action
- `xp` — XP curves, bonuses
- `missions` — quotas, multipliers, durations
- `battle_pass` — tiers, rewards, season duration
- `mystery_product` — tiers, probabilities, rewards
- `gift_cards` — conversion rates, caps
- `referral` — referrer/referee bonuses, anti-churn
- `gamification` — `freeze_cost_cab`, `burst_contest` **(sub-key `feed_jack` frozen — cf. allowlist sub-keys)**
- `buffer` — `n_max_daily`, `weekly_allowed`, `notif_lead_time_hours` (cf [[ARCH_gamification]] § Buffer)
- `burst` — `cap_n_max`, `leaderboard_top_size`

### 🔓 New sections to create (1)

- `subscription_promotions` — variable Stripe promo codes, start/end dates, multipliers. **Empty section to seed in bloc A** (`{ "active_codes": [], "default_multiplier": 1.0 }`).

### 🔒 Frozen (UI read-only, modifiable via git PR only)

Algo / infra / templates / pricing parameters — any modification = behaviour change = **must go through PR for audit + tests**:

- `subscription` — Stripe pricing + callback URLs (modification = change to user contract)
- `notifier` — push templates (live modification = risk of breaking all users)
- `consensus`, `fuzzy`, `name_resolution_consensus` — price/name algos
- `ocr`, `type_detector`, `knowledge`, `llm` — OCR pipeline
- `pipeline_v3` — worker orchestrator
- `label` — ESL parser params
- `store_validation`, `store_matching`, `osm_sync` — store pipeline
- `off_sync` — OFF sync
- `list_optimiser`, `savings` — LO/RW algos

> The UI displays frozen sections as read-only (JSON preview + 🔒 tag + tooltip "modifiable via git PR only"). The REST PUT returns 403 `{detail: "section_frozen"}` if attempted programmatically.

### Frozen sub-keys (granularity within an editable section)

Some editable sections contain sensitive sub-keys that need protection. The backend config declares a `frozen_keys` allowlist per section:

```python
# webservices/ratis_rewards/services/admin/settings_service.py
EDITABLE_SECTIONS: dict[str, frozenset[str]] = {
    "rewards":        frozenset(),
    "xp":             frozenset(),
    "missions":       frozenset(),
    "battle_pass":    frozenset(),
    "mystery_product": frozenset(),
    "gift_cards":     frozenset(),
    "referral":       frozenset(),
    "gamification":   frozenset({"feed_jack"}),  # ← sub-key frozen
    "subscription_promotions": frozenset(),
}
```

On PUT, the service compares `new_data` vs `old_data` for keys in `frozen_keys` — if a frozen_key has been modified → 403 `{detail: "frozen_key_modified", key: "feed_jack"}`. The UI displays the sub-object as read-only.

**Why `feed_jack` is frozen**: streak Jack algo (multiplier_per_day, max_multiplier, food_reserve_cost) — dangerous typo (5.0 vs 0.05 = massive CAB inflation). 5%/day is final post-V0. If variation is truly needed: hard-edit DB (SQL statement with context).

---

## DB schema — Audit log (= permanent history)

The audit log **IS** the settings history. No separate table. The JSON file will disappear in V2 — the DB becomes the source-of-truth + its audit the history.

```sql
CREATE TYPE admin_settings_audit_status AS ENUM ('applied', 'pending_2fa', 'expired', 'cancelled');

CREATE TABLE admin_settings_audit (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  timestamp TIMESTAMPTZ NOT NULL DEFAULT now(),
  operator TEXT NOT NULL,
  section TEXT NOT NULL,
  reason TEXT NOT NULL,                              -- mandatory business motivation
  old_data JSONB,                                    -- NULL on first write (initial seed)
  new_data JSONB NOT NULL,                           -- target value (may NOT be applied if pending_2fa)
  diff JSONB,                                        -- pre-computed: added/removed/modified keys (V1+)
  status admin_settings_audit_status NOT NULL DEFAULT 'applied',
  expires_at TIMESTAMPTZ,                            -- NULL if applied directly; otherwise timestamp + 10 min for pending_2fa
  applied_at TIMESTAMPTZ,                            -- timestamp of effective application (NULL if pending_2fa/expired/cancelled)
  CONSTRAINT chk_reason_min_len CHECK (length(reason) >= 8),
  CONSTRAINT chk_status_2fa_coherence CHECK (
    (status = 'applied' AND applied_at IS NOT NULL)
    OR (status = 'pending_2fa' AND expires_at IS NOT NULL AND applied_at IS NULL)
    OR (status IN ('expired', 'cancelled') AND applied_at IS NULL)
  )
);

CREATE INDEX idx_admin_settings_audit_section_ts
  ON admin_settings_audit (section, timestamp DESC);

CREATE INDEX idx_admin_settings_audit_ts
  ON admin_settings_audit (timestamp DESC);

-- Index for pending 2FA cleanup batch (V1+)
CREATE INDEX idx_admin_settings_audit_pending
  ON admin_settings_audit (expires_at)
  WHERE status = 'pending_2fa';
```

**Decisions**:
- **`reason TEXT NOT NULL` + CHECK length ≥ 8**: mandatory business motivation on each save.
- **`status` ENUM**: `applied` (value applied) · `pending_2fa` (awaiting TOTP confirmation) · `expired` (10 min elapsed without 2FA) · `cancelled` (admin cancelled during grace period).
- **`expires_at`**: NULL if direct save, otherwise `now() + 10 min` for pending_2fa.
- **`applied_at`**: NULL until the value is written to `app_settings`. Consistency guaranteed by CHECK constraint.
- Dedicated table (vs JSONB column on `app_settings`): queryable, paginable, non-correlated growth.
- Append-only: no mutation or purge. Update allowed only on `status` and `applied_at` (transition pending_2fa → applied/expired/cancelled).
- No FK on `section`: allows auditing a section that may have been dropped in the meantime.

---

## V1 Guardrails

Editable UI modifications = economic parameters (CAB, missions, BP, gift cards) = typo risk (`5000` instead of `500` = silent ×10 inflation). Guardrails:

### 1. Mandatory `reason` (≥ 8 chars)

Business motivation on each save. No "test" / "fix" possible. Accepted examples: `"Bump CAB receipt 500→600 — alpha test data ramp"`, `"Reduce mission daily quota — feedback alpha too heavy"`. The UI rejects submit if empty or < 8 chars.

### 2. Magnitude check + grace period + TOTP 2FA

**Detection**: on **all numeric keys** (int / float — not string/bool/array) modified in the PUT. If `|new_value - old_value| / |old_value| > 0.5` (variation > 50%) on **at least one numeric key** → the entire PUT goes to `pending_2fa` (atomic).

**Flow**:
1. Admin saves via UI → backend detects variation > 50% → audit row `status='pending_2fa'`, `expires_at = now() + 10 min`, `applied_at = NULL`. **Value NOT applied** to `app_settings`.
2. UI redirects to "2FA Validation" page with 10 min countdown, expected TOTP code, Cancel button.
3. Admin enters TOTP code → POST `/admin/settings/{section}/confirm-2fa {audit_id, totp}` → backend verifies via `X-Admin-TOTP` header (existing TOTP infra on RW side, already in place for withdrawals + CAB adjust).
4. If TOTP valid → audit row `status='applied'`, `applied_at=now()`, value written to `app_settings`. UI confirms "Settings applied."
5. If TOTP invalid → 401, admin can retry as long as `expires_at > now()`.
6. If 10 min timeout → nightly batch (or lazy trigger on next GET) marks row `status='expired'`, save silently abandoned.
7. Cancel button during grace period → POST `/admin/settings/{section}/cancel-pending {audit_id}` → row `status='cancelled'`.

**No fallback**: a PUT without `X-Admin-TOTP` that exceeds the threshold is **always** put to pending. The admin MUST confirm via 2FA. That is the guardrail.

### 3. Diff preview before save

The UI displays a visible diff (green added, red removed, yellow modified) before the "Confirm save" button. If magnitude > 50% detected on the UI side, modal: "This modification will trigger 2FA validation (10 min grace period). Continue?".

### 4. No "Restore default JSON" button

The JSON disappears in V2 — restoring a past version = re-read the audit log + manual replay (V2 = "Restore version N" button from the audit log).

---

## Remaining work V1.x

V1 fully shipped (5 blocs A→E + security hardening) as of 2026-05-03. No item in the checklist below is outstanding as of 2026-05-09. Post-V1 improvements are intentionally deferred to the V2 backlog (see next section) — no work is blocked in V1.x.

---

## V2 guardrails backlog

- Pydantic schemas per section (`Field(ge=, le=)` to bound values)
- Daily edit cap per section (max N edits/day to prevent runaway iteration)
- Hard limits on the backend (e.g.: `cab_per_receipt_complete max=5000` even with 2FA)
- 2FA also on frozen → editable section transitions (moving a section from frozen to editable)
- Automatic email notification to all ops on `status='pending_2fa'` (alerting)
- Sentry webhook on expired/cancelled (incident tracking)

---

## Threat model V1 — trust assumptions explicit

The V1 model rests on the postulate **"trusted non-colluding ops"**: each
admin operator is a trusted team member, but we do not assume they
actively collaborate against the system. The V1 guardrails protect
against honest mistakes (magnitude typo, double-PUT during the grace
period) and cross-attribution (one op validating another's 2FA).
They do **not** protect against:

- A malicious op who has both the `ADMIN_API_KEY` and the `TOTP_SECRET`
  (both secrets live server-side — an op rooted on the VM has them).
- Collusion between 2 ops (op A initiates, op B confirms — prevented by H1
  only because the operator filter is on the same row, not active
  coordination).
- A leak of the JSON `app_settings` (the payload is not encrypted at rest —
  V2 if we ever store secrets in a section).

### Guardrail mitigations (security audit 2026-05-03)

| ID | Risk | V1 Mitigation | Out of scope V1 |
|----|--------|---------------|---------------|
| H1 | Op B confirms the 2FA initiated by op A | Filter `operator == X-Admin-Operator` on the confirm-2fa SELECT → 404 (no leak) | Multi-op co-signature (V2 if 4-eyes compliance needed) |
| H2 | 3 successive PUTs > 50% leave 3 pending rows | Auto-cancel of old pending on PUT + partial UNIQUE INDEX `(section) WHERE status='pending_2fa'` | — |
| M1 | Cookie token = raw `sha256(api_key:operator)` → vulnerable to rainbow tables on short key | (a) `compute_token` rewritten as HMAC-SHA256(key=api_key, msg=operator) — canonical KDF, rainbow defense; (b) `require_env_min_length("ADMIN_API_KEY", 32)` at PA + RW lifespan. Migration: all existing cookies invalidated on first deploy → forced re-login (acceptable alpha 1-2 ops). | — |
| M2 | Session cookie without `Secure` flag → theft via HTTP MITM | `secure=request.url.scheme == "https"` (production HTTPS via Caddy → always `Secure`) | Force `secure=True` always (V2 — requires mkcert dev) |
| M3 | `subscription_promotions.active_codes` (active promo codes) leaked via GET /admin/settings/audit | `redact_for_audit(section, data)` — masks `***REDACTED***` at API/UI serialization; DB row intact (legal audit trail NEVER PURGE). `REDACTED_KEYS_PER_SECTION` mapping = future-proof pattern for future secrets/PII. | At-rest encryption of JSON (V2 if we store secrets in a section) |
| M5 | Multi-MB PUT body → service DoS / audit_row saturation | Explicit 64 KB cap on `data` JSON serialized (`validate_body_size`) → 413 `payload_too_large`. Bypass-proof: same limit as UI but enforced backend. | — |
| L1 | Nested JSON 10000 levels → `RecursionError` 500 | Cap `_MAX_DEPTH = 32` in `_walk` → return `(False, None)` beyond that | — |
| L2 | `reason` free-text unbounded → bloated audit row, logs drowned | `Field(min_length=8, max_length=2000)` on `PutSectionBody.reason` (Pydantic). | — |

**Cancel-pending cross-op**: not covered by H1 (the audit brief limits the
fix to confirm-2fa). Under the "non-colluding ops" model, an op cancelling
another op's pending is annoying but reversible (the original op can re-PUT).
To be revisited in V2 if we add ops email notifications.

**Non-fixed V1 backlog (orchestrator decisions 2026-05-03)**:
- **L3 — rotate-key endpoint**: not created. Increased attack surface
  (an endpoint that regenerates ADMIN_API_KEY exposed). Prod rotation
  procedure = manual SSH Hetzner + Sentry boot event traced (cf.
  `ARCH_deployment.md` § Disaster recovery).
- **IP whitelist admin endpoints**: YAGNI'd alpha (1-2 ops, dynamic
  home IP). To reconsider in V2 if ≥3 active ops or a fixed
  ratis office.
- **M3 future-proof**: if the ops team grows (≥3) or we store
  secrets in a section, extend `REDACTED_KEYS_PER_SECTION`
  rather than inventing ad-hoc redaction.

---

## Endpoints

### Existing (RW, kept)

```
GET    /admin/settings                       — list {section: data}
GET    /admin/settings/{section}             — section data
PUT    /admin/settings/{section}             — replace section data
POST   /admin/settings/seed                  — re-seed from JSON
```

### New (RW)

```
GET    /admin/settings/audit                 — list mutations (filter section, limit, offset)
GET    /admin/settings/audit/{id}            — detail + diff
GET    /admin/settings/{section}/editable    — bool { editable: true/false }
```

### UI (PA → calls RW via rw_client)

```
GET    /admin/ui/settings                    — list page (10 editable + 15 frozen)
GET    /admin/ui/settings/{section}          — detail page (form if editable, read-only otherwise)
POST   /admin/ui/settings/{section}          — submit form (calls RW REST PUT)
GET    /admin/ui/settings/audit              — audit log page
```

---

## Auth

Reuses the 2 existing patterns:

- **Browser UI**: session cookie `admin_session` + `admin_operator` (existing login form `/admin/ui/login`). No change.
- **Cross-service REST**: `Authorization: Bearer ADMIN_API_KEY` + `X-Admin-Operator: <handle>` propagated from the session cookie. Fixed pattern `ARCH_admin_endpoints.md` § Auth.

No TOTP required on settings (vs withdrawals/CAB adjust). To strengthen in V2, we can add `X-Admin-TOTP` on sensitive sections (`rewards`, `gift_cards`, `subscription`).

---

## V1 Validation

JSON parseable only (`json.loads(body)`). If a type error occurs, the consuming service will explode at runtime — an accepted V1 trade-off.

V2: Pydantic v2 schemas per section. Each section has a `schemas/<section>.py` module defining the expected types, and PUT validates via `<Schema>.model_validate(data)`.

---

## Service reload after save

**V1**: manual restart via `docker compose restart <service>`. UI displays after save: "Settings saved. Restart the relevant services to apply."

**V2 options (post-V1, to brainstorm)**:
- TTL 60s on `load_settings()` (acceptable since settings = not a hot path)
- Redis pubsub channel `settings_invalidate` (instant, but adds Redis dependency to services that don't use it)
- Endpoint `/internal/reload-settings` (admin pushes, services pull) — cleaner, requires coordination

---

## Data migration

- `app_settings` table already in place (migration 2026-04-15).
- Prod seed: `seed_settings(db)` to run once post-merge bloc A (idempotent).
- Audit table: new migration A.

---

## Out of scope V1 — V2 Backlog

| V2 | Description |
|---|---|
| **Pydantic validation** | Schemas per section (Bloc F) |
| **Hot-reload** | TTL/pubsub (Bloc G) |
| **UI migration to RW** | admin_ui PA → RW (Bloc H) |
| **TOTP on sensitive sections** | rewards/gift_cards |
| **Diff pre-calc background** | Compute `diff` JSONB via PG trigger |
| **Rollback** | "Restore version N" button from audit log |
| **Multi-env override** | Settings per environment (dev/staging/prod) |
| **Schema migration tool** | Auto-detect sections missing from DB → seed delta |
| **Drop JSON file** | Once prod is stable, drop `ratis_settings.json` — DB becomes sole source-of-truth |
| **Daily edit cap + cool-down** | Anti-runaway-iteration limits |
| **Admin replay notification endpoint** | `POST /admin/notifications/replay {user_id, event_id}` — replay a failed notification (outside ARCH settings scope — to note in `ARCH_admin_endpoints.md`) |

---

## Details per bloc

### Bloc A — backend write service + audit table + 2FA grace

- **Files touched**:
  - `alembic/versions/<new>_admin_settings_audit.py` (NEW) — table + ENUM + 3 indexes + CHECK constraints
  - `ratis_core/ratis_core/settings.py` (MODIFIED — adds `update_settings_section`)
  - `ratis_core/ratis_core/models/admin_audit.py` (NEW — `AdminSettingsAudit` model + `AdminSettingsAuditStatus` enum)
  - `ratis_core/ratis_core/services/settings_2fa.py` (NEW — magnitude detection + grace period helpers)
  - `ratis_core/tests/test_settings_write.py` (NEW)
  - `ratis_core/tests/test_settings_2fa_detection.py` (NEW — detect_magnitude_breach for numeric keys)
- **TDD tests**:
  - write happy path (variation < 50%): audit row `status='applied'`, `applied_at=now()`, value on `app_settings`.
  - write 2FA-required (variation > 50%): audit row `status='pending_2fa'`, `expires_at=now()+10min`, `applied_at=NULL`, `app_settings` unchanged.
  - detect_magnitude_breach:
    - `{"x": 500} → {"x": 600}` (×1.2): breach=False
    - `{"x": 500} → {"x": 5000}` (×10): breach=True, key='x'
    - `{"x": 100} → {"x": 30}` (×0.3): breach=True (50% drop also counts)
    - `{"x": "old"} → {"x": "new"}` (string): skip, breach=False
    - `{"x": True} → {"x": False}` (bool): skip, breach=False
    - `{"x": [1, 2]} → {"x": [3, 4]}` (array): skip, breach=False
    - multi-key PUT with 1 numeric > 50% → breach=True (atomic).
  - reason validation: reject if len < 8, accept ≥ 8.
  - operator propagation into audit row.
  - CHECK constraint status/applied_at coherence (status='applied' implies applied_at NOT NULL).
- **Risks**: none — greenfield table, no data migration.
- **Orchestrator notes**: no 2FA logic itself in Bloc A (just the `pending_2fa` marking). TOTP validation + transition to `applied` = Bloc B (RW endpoints).

### Bloc B — reinforced REST endpoints (RW)

- **Files touched**:
  - `webservices/ratis_rewards/routes/admin/settings.py` (MODIFIED — adds `/audit` + editable_sections allowlist + propagate operator)
  - `webservices/ratis_rewards/services/admin/settings_service.py` (NEW — encapsulates update + audit)
  - `webservices/ratis_rewards/tests/admin/test_settings.py` (NEW/MODIFIED)
- **Tests**: allowlist 403 if frozen, audit listing pagination, operator required.
- **Allowlist**: module-level constant `EDITABLE_SECTIONS = frozenset({"rewards", "xp", ...})` (10 sections). Test: PUT attempt on frozen section → 403.

### Bloc C — cross-service client `rw_client.py`

- **Files touched**:
  - `webservices/ratis_product_analyser/admin_ui/rw_client.py` (NEW)
  - `webservices/ratis_product_analyser/main.py` (MODIFIED — `require_env` RW_BASE_URL at lifespan)
  - `webservices/ratis_product_analyser/tests/test_rw_client.py` (NEW)
- **Tests**: monkeypatch RW_BASE_URL, mock httpx, assert headers.
- **Env var**: `RW_BASE_URL` (e.g.: `http://ratis_rewards:8004` dev, `https://rewards.ratis.app` prod).

### Bloc D — admin UI pages

- **Files touched**:
  - `webservices/ratis_product_analyser/admin_ui/templates/settings_list.html` (NEW)
  - `webservices/ratis_product_analyser/admin_ui/templates/settings_detail.html` (NEW)
  - `webservices/ratis_product_analyser/admin_ui/routes.py` (MODIFIED — 3 routes + dashboard tile)
  - `webservices/ratis_product_analyser/admin_ui/templates/index.html` (MODIFIED — tile)
  - `webservices/ratis_product_analyser/tests/test_admin_ui_settings.py` (NEW)
- **UX**: raw JSON textarea + Tailwind + HTMX (consistent with the rest). Diff preview before save. `reason` field (textarea, min 8 chars, mandatory). Magnitude > 50% confirmation modal.
- **No "Restore default JSON" button** (JSON disappears in V2 — restore = via audit log replay in V2).
- **Tests**: auth required, 403 on PUT frozen, 403 on modified frozen sub-key (`gamification.feed_jack`), audit row visible post-save with reason, reject submit if reason empty.

### Bloc E — UI audit log

- **Files touched**:
  - `webservices/ratis_product_analyser/admin_ui/templates/settings_audit.html` (NEW)
  - `webservices/ratis_product_analyser/admin_ui/routes.py` (MODIFIED — route `GET /admin/ui/settings/audit`)
- **Tests**: pagination, filter by section.

---

## Implementation checklist

### Bloc A — backend + audit ✅ (PR #257 merged 2026-05-02)
- [x] Migration `admin_settings_audit` created + ENUM + 3 indexes + 2 CHECK + upgrade/downgrade tests (8 tests)
- [x] SQLAlchemy model `AdminSettingsAudit` + `AdminSettingsAuditStatus` enum
- [x] Helper `update_settings_section(db, section, new_data, operator, reason, *, bypass_2fa)` ratis_core
- [x] Service `detect_magnitude_breach(old, new)` + 30 tests (numeric only, skip bool/string/array, nested, atomic multi-keys)
- [x] TDD tests write happy path + 2FA-required + bypass_2fa + first-write + reason validation + CHECK constraint (9 DB tests)
- [x] CI green (48 SUCCESS / 7 SKIPPED / 0 fail)

### Bloc B — REST ✅ (PR feat/admin-settings-bloc-b-rest)
- [x] `EDITABLE_SECTIONS` allowlist (9 sections — 8 historical + new `subscription_promotions`) in `services/admin/settings_service.py`
- [x] PUT 403 `section_frozen` if section absent from allowlist
- [x] PUT 403 `frozen_key_modified` if frozen sub-key changed (allowlist `gamification.feed_jack`)
- [x] PUT contract `{data, reason}` — body 200 `{audit_id, status}`, status='applied' OR 'pending_2fa'
- [x] Endpoint `GET /admin/settings/audit` paginated (limit/offset, section + status filters)
- [x] Endpoint `GET /admin/settings/audit/{audit_id}` detail + diff (on-fly fallback)
- [x] Endpoint `POST /admin/settings/{section}/confirm-2fa` (verify_totp_dep reused, transitions pending_2fa→applied with UPSERT app_settings)
- [x] Endpoint `POST /admin/settings/{section}/cancel-pending` (transitions pending_2fa→cancelled, without TOTP)
- [x] Endpoint `GET /admin/settings/{section}/editable` (allowlist introspection)
- [x] X-Admin-Operator propagated to audit (header read via `_operator(request)`)
- [x] New section `subscription_promotions` added to `ratis_settings.json` (`{active_codes: [], default_multiplier: 1.0}`)
- [x] TDD tests allowlist + audit listing + 2FA flow + editable (24 tests in `tests/admin/test_settings_endpoints.py`) + existing tests `test_admin_settings.py` adapted to new contract `{data, reason}`
- [x] CI green

#### Bloc B — notable decisions
- **Confirm-2fa transitions the pending row, does not create a new audit row**: the original trace (operator + reason + timestamps) remains the sole record of the mutation. `applied_at` is set to `now()` at confirm. Avoids double-event in the audit log.
- **Diff fallback (`_shallow_diff`) inline in `routes/admin/settings.py`** rather than importing the private `_compute_diff` from `ratis_core.settings` — clean decoupling, no cross-package underscore-import.
- **`row.expires_at` aware/naive reconciliation**: the migration creates `TIMESTAMPTZ` but the SQLAlchemy model leaves the type implicit, so `Base.metadata.create_all()` produces `TIMESTAMP WITHOUT TIME ZONE` in tests. The route normalizes both sides to naive UTC for comparison — to fix at the model level one day (Mapped[datetime] with `TIMESTAMP(timezone=True)`) but out of scope for Bloc B.
- **HTTPException dict detail for `frozen_key_modified`**: FastAPI surfaces the dict as-is under `detail`. Response body: `{"detail": {"detail": "frozen_key_modified", "key": "feed_jack"}}` — slightly nested but conforms to the contract required by the ARCH.
- **Reason validation Pydantic**: `Field(..., min_length=8)` on the body schema → immediate 422 before reaching the helper. `update_settings_section_with_2fa_check` re-maps `ValueError` → 422 `reason_too_short` as fallback (belt-and-suspenders if Pydantic is ever bypassed).

### Bloc C — rw_client
- [x] `rw_client.py` (rw_get + rw_put + rw_post with optional TOTP propagation)
- [x] `require_env('RW_BASE_URL')` at lifespan (kept conditional on ADMIN_API_KEY being set, same as `AU_BASE_URL`)
- [x] TDD client tests (12 tests: Bearer + operator headers, TOTP propagation, JSON body, query params, call-time base_url resolution, 10s timeout)
- [x] `RW_BASE_URL` added in `.env.example`, `docker-compose.prod.yml`, `tests/conftest.py` (R20 — 3+ places simultaneously)
- [x] CI green (PR #264)

### Bloc D — UI pages
- [x] Templates settings_list.html + settings_detail.html + settings_2fa_pending.html
- [x] 5 routes: GET list, GET detail, POST save, POST confirm-2fa, POST cancel-pending
- [x] Dashboard tile + nav link base.html
- [x] Mirror constant `EDITABLE_SECTIONS_MIRROR` + `FROZEN_SECTIONS` on the PA admin_ui side + AST-parsing contract test of the RW source to catch any drift
- [x] Local fail-fast `reason ≥ 8 chars` + JSON parse + 64 KB body cap before PUT
- [x] Unwrap `detail.detail.key` for `frozen_key_modified` (FastAPI nested dict detail)
- [x] TDD page + auth tests (18 tests, modelled on `test_admin_ui_users.py` mock-rw_client pattern)
- [x] CI green (PR #267 merged 2026-05-02)

#### Bloc D — notable decisions
- **Mirror constant + AST contract test** rather than HTTP discovery: 26 sections × `GET /admin/settings/{s}/editable` on each page-load = wasteful. The local mirror `EDITABLE_SECTIONS_MIRROR` duplicates the list, the AST contract test parses `webservices/ratis_rewards/services/admin/settings_service.py` at every CI run to catch drift without cross-package import (PA does not have RW as a dep).
- **JS diff preview**: not implemented in V1 — the spec brief mentioned "Diff preview on textarea blur". Decision: ship without it for V1, the real guardrail = magnitude>50% backend-side which forces the 2FA flow. Visual diff is cosmetic and can be added in a follow-up without changing the contract.
- **Body size cap 64 KB**: low-cost protection against accidental paste of an entire dump. Normal sections = a few KB max.
- **Reused pattern from users page**: stub_rw script-table monkeypatching symbols `rw_get/rw_put/rw_post` in `admin_ui.routes` — exact mirror of the existing `stub_au` pattern.

### Bloc E — UI audit
- [x] Template settings_audit.html (list) + settings_audit_detail.html (detail with diff viewer)
- [x] Routes `/admin/ui/settings/audit` (list, paginated + section/status filters) + `/admin/ui/settings/audit/{audit_id}` (detail, drill-down from list)
- [x] TDD tests: auth gate, rendering, section/status filters (combined), offset pagination + last-page no-next, RW 5xx graceful, detail diff rendering, detail 404 → 404 template (15 tests)
- [x] CI green (PR #269 merged 2026-05-02)

#### Bloc E — notable decisions
- **Routes ordered before `/settings/{section}` catch-all**: FastAPI evaluates routes in declaration order. Without this placement, `/settings/audit` would have been matched by `settings_detail_page(section="audit")` then 404 on RW. Pattern: literal routes before homonymous path-params.
- **Filter dropdown sections = union editable + frozen**: reuse of `EDITABLE_SECTIONS_MIRROR` ∪ `FROZEN_SECTIONS` (Bloc D mirror) — no new constant. The operator can filter on any section that could have produced an audit.
- **Local status enum `_AUDIT_STATUSES`**: tuple of 4 values (applied / pending_2fa / expired / cancelled) hard-coded on the UI side — mirror of `AdminSettingsAuditStatus` on the RW side. Drift caught by the AST contract test if a state is added (to extend in Bloc F if needed).
- **404 RW → 404 UI**: the detail route propagates the upstream 404 (instead of 200 + flash) so that a stale bookmark gives the correct HTTP signal. The template render remains user-friendly (explanatory "not found" message).
- **Pagination link suppressed at last page**: no "Next" link rendered when `offset + limit >= total`; displays a greyed-out disabled span to preserve the grid layout.
- **Old/new data prettified via `json.dumps(indent=2, sort_keys=True)`**: same formatting as `_render_settings_detail` on the Bloc D side — the operator sees consistent JSON regardless of which screen they are on.

---

## Glossary

- **section**: top-level key in `ratis_settings.json` (e.g.: `rewards`, `missions`).
- **editable**: section modifiable via admin UI (10 economic-parameter sections).
- **frozen**: section read-only via UI, modifiable only via git PR (15 algo sections).
- **operator**: self-declared handle at login (e.g.: `guillaume`). Logged in audit, not crypto-validated.
- **seed**: initial INSERT of sections from `ratis_settings.json` (idempotent ON CONFLICT DO UPDATE).
