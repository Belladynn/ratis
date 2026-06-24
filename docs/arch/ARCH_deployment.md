---
type: cross-cutting
parent: ARCH_RATIS
related: [ARCH_RATIS]
status: in-progress
tags: [deployment, infra, docker, hetzner, mac-mini, caddy, eas]
updated: 2026-05-09
---

# ARCH — Ratis Production Deployment

> Multi-stage Ratis deployment strategy (Hetzner V0 → Mac mini → AWS), Docker Compose + Caddy topology, self-hosted GH Actions runner, cloud-init bootstrap, emergency runbook, secrets, backups, EAS Android APK.
> @tags: deployment infra docker hetzner mac-mini caddy eas cloud-init docker-compose runbook self-hosted gh-actions aws migration-path backups
> @status: EN-COURS
> @subs: auto

> Parent : [[ARCH_RATIS]]

## Index
- [Multi-stage strategy](#multi-stage-strategy)
- [Physical architecture V0 (Hetzner)](#physical-architecture-v0-hetzner)
- [Network & DNS topology](#network-dns-topology)
- [Software stack](#software-stack)
- [Automatic bootstrap (cloud-init)](#automatic-bootstrap-cloud-init)
- [Code deployment (docker-compose.prod.yml)](#code-deployment)
- [Migration smoke test (post-deploy)](#migration-smoke-test-post-deploy)
- [Running batches in production](#running-batches-in-production)
- [Mobile (EAS APK Android)](#mobile-eas-apk-android)
- [Secrets & env vars](#secrets-env-vars)
- [Cross-service URL conventions](#cross-service-url-conventions)
- [Observability](#observability)
- [Backups & disaster recovery](#backups-disaster-recovery)
- [Migration path Mac mini → AWS](#migration-path)
- [Recurring ops commands](#recurring-ops-commands)
- [Emergency runbook](#emergency-runbook)

---

## Multi-stage strategy

**Philosophy**: portable code via Docker + docker-compose → zero lock-in, trivial migration between hosting providers. Progression:

1. **V0 alpha (Sunday 26/04/2026)** — Hetzner Cloud CX33 (x86, 8 GB RAM, Nuremberg 🇩🇪). Goal: 5-50 alpha users (family + close circle) + start of organic marketing. Migration from CAX21 (ARM) on 2026-04-27 — detail in SESSION_LOG.md.
2. **Mac mini M4 Pro — ALREADY DONE (2026-05-04, PR #287)** — production ratis switched from the historical Windows host to the Mac mini M4 Pro (macOS, arm64). Detailed post-migration runbook: [[ARCH_itops]] § Vision + § Topology. The Mac mini also hosts the 16 self-hosted GH Actions runners. Hetzner Cloud CX33 remains available as a V0 alpha backup during the transition.
3. **When Mac mini is saturated** — switch to AWS (ECS Fargate + RDS + ElastiCache). Saturation signals documented in `PROD_CHECKLIST.md` § Stratégie hébergement.

**Business constraints**:
- Aggressive marketing planned → possible traffic spikes, infrastructure must handle them
- Internationalisation 3-6 months after launch (UK, DE, ES)
- EU launch eventually (political + fiscal reasons) → prepare Iceland/Switzerland hosting migration post-alpha

---

## Physical architecture V0 (Hetzner)

**Single virtual bare-metal server**. All services in Docker on the same VM. Simplicity > redundancy in V0.

```
┌─────────────────────────────────────────────────────────────┐
│  Hetzner Cloud CX33 (x86) — Nuremberg                       │
│  IPv4: 46.225.63.79    |  8 GB RAM  |  4 vCPU  |  80 GB SSD │
│                                                              │
│  ┌──────────────────┐    ┌────────────────────────────────┐ │
│  │  Caddy (TLS ACME)│───▶│  Docker network ratis_net      │ │
│  │  :80 :443        │    │                                │ │
│  └──────────────────┘    │  ┌──────┐ ┌──────┐ ┌─────────┐ │ │
│         ▲                │  │ auth │ │ list │ │ rewards │ │ │
│         │                │  │ :8001│ │ :8002│ │ :8004   │ │ │
│  ┌──────┴───────────┐    │  └──────┘ └──────┘ └─────────┘ │ │
│  │  Internet        │    │  ┌─────────────┐ ┌──────────┐  │ │
│  │  (UFW 22/80/443) │    │  │product_anlsr│ │ notifier │  │ │
│  └──────────────────┘    │  │:8003 (OCR)  │ │ :8005    │  │ │
│                          │  └─────────────┘ └──────────┘  │ │
│                          │  ┌──────────┐ ┌──────┐ ┌────┐  │ │
│                          │  │ postgres │ │redis │ │OSRM│  │ │
│                          │  │ :5432    │ │:6379 │ │:5000│  │ │
│                          │  └──────────┘ └──────┘ └────┘  │ │
│                          └────────────────────────────────┘ │
│                                                              │
│  Docker volumes: postgres_data, redis_data, caddy_data      │
│  Files: /root/ratis/ (git clone), /data/osrm (France PBF)   │
└─────────────────────────────────────────────────────────────┘
```

> **Path note** (2026-04-27): the live VM has its git clone under `/root/ratis`,
> not `/opt/ratis` as suggested by Linux convention + cloud-init. A future
> migration is planned (`mv /root/ratis /opt/ratis` + systemd/scripts adjustment).
> In the meantime, all ops scripts (`scripts/ops_lib.sh`)
> and this doc reference `/root/ratis` by default, overridable via
> the `PROD_DIR` env variable.

**CX33 x86 justification**:
- Migration from CAX21 ARM on 2026-04-27 (reason: to document in SESSION_LOG.md — likely paddleocr / other deps better tested on x86)
- 8 GB RAM: OSRM baseline 3 GB + PaddleOCR 2 GB + Postgres 1 GB + remaining 2 GB ≈ OK up to a few hundred active users
- 4 shared x86 vCPU — sufficient for async OCR pipeline + alpha API traffic
- Future Mac mini ARM migration impact: remains portable since everything is in Docker. A multi-arch image rebuild (already supported by the Dockerfiles) is enough to target ARM.

---

## Network & DNS topology

**Domain**: `ratis.app` (DNS at Cloudflare). Wildcard proxy can be enabled later if needed.

**Production subdomains (CNAME pointing to `46.225.63.79`)**:

| Subdomain | Target | Service |
|---|---|---|
| `api.ratis.app` | Caddy → `auth:8001` | ratis_auth |
| `products.ratis.app` | Caddy → `product_analyser:8003` | OCR + products |
| `lists.ratis.app` | Caddy → `list_optimiser:8002` | Lists + routes |
| `rewards.ratis.app` | Caddy → `rewards:8004` | CAB + battlepass + missions |
| `notifier.ratis.app` | Caddy → `notifier:8005` | Expo push (internal) |
| `osrm.ratis.app` | *(optional, not exposed V0)* | Routing |

**Cloudflare**:
- **Proxy disabled** (DNS only, grey cloud) during the 1st Let's Encrypt challenge via Caddy (otherwise Cloudflare intercepts)
- **Proxy can be enabled** after TLS is established, for DDoS protection + cache + WAF rules
- **SSL/TLS mode**: "Full (strict)" — Cloudflare verifies the origin cert

**UFW firewall on the VM**:
```
22/tcp  ALLOW IN   # SSH
80/tcp  ALLOW IN   # HTTP (Let's Encrypt ACME challenge)
443/tcp ALLOW IN   # HTTPS (application traffic)
                   # Everything else: DENY by default
```

---

## Software stack

| Layer | Technology | Version | Why |
|---|---|---|---|
| OS | Ubuntu LTS | 24.04 | Support until 2029, official Docker |
| Python runtime | CPython | 3.12 | Pinned via `.python-version` (not 3.13/14 — paddleocr) |
| Package mgr | uv | ≥0.5 | 10x faster than pip, native workspace |
| Web framework | FastAPI | ≥0.135 | async, Pydantic V2, lifespan |
| DB | PostgreSQL | 16 | JSONB, generated cols, native partitioning |
| Cache/queue | Redis | 7-alpine | slowapi, Celery broker, product cache |
| OCR | PaddleOCR | ≥2.9.1 | better FR than Tesseract, multi-pass confidence |
| Routing | OSRM | 5.27.1 | MLD algorithm, pre-compiled France PBF |
| Reverse proxy | Caddy | 2 | auto TLS ACME, zero TLS config |
| Containerisation | Docker + compose | v2 | multi-arch, profiles, healthchecks |
| Firewall | UFW | Ubuntu system | simple rules, iptables underneath |
| Fail2ban | 1.x | system | SSH brute-force protection |
| DB migrations | Alembic | ≥1.18 | autogenerate, branching |
| Monitoring | Sentry + Sentry→Notion webhook | — | application errors, alerting |
| Mobile | Expo SDK 54 + EAS | — | cloud build, OTA updates |
| Frontend auth | expo-auth-session | — | Google OAuth 2.0 + PKCE |

---

## Automatic bootstrap (cloud-init)

**File**: `infra/cloud-init-hetzner.yaml`.

Passed to `hcloud server create --user-data-from-file`, runs on first boot. Installs:
- 4 GB swap + swappiness=10
- Docker + docker-compose-v2
- UFW (22/80/443 in) + enabled
- fail2ban (bantime 1h, maxretry 5 on sshd)
- unattended-upgrades (daily Ubuntu security)
- timezone Europe/Paris
- Docker pull of postgres:16, redis:7, caddy:2 (warmup)
- Marker: `/var/log/ratis-bootstrap-done` contains UTC end timestamp

**Idempotency**: re-run via `cloud-init clean --logs && cloud-init init` if re-provisioning is needed (rare).

**Duration**: 3-5 min after `server created`. Verify:
```bash
ssh root@<IP> "cat /var/log/ratis-bootstrap-done || cloud-init status --wait"
```

---

## Code deployment

**Main file**: `docker-compose.prod.yml` (repo root).

**Deployment command** (to run on the VM after clone):
```bash
cd /root/ratis
git pull origin main
docker compose -f docker-compose.prod.yml --env-file .env.prod --profile self-hosted up -d --build
```

**Docker profiles**:
- *(default)* — starts DB + Redis + OSRM + 5 services. No Caddy (used when Railway managed TLS at the edge, abandoned case).
- `self-hosted` — adds Caddy with TLS ACME volumes. **Profile used on Mac mini (current production, post-PR #287) and kept for Hetzner CX33 V0 alpha backup**.

**Healthchecks** — all critical services have a Docker healthcheck that delays dependent services:
- Postgres: `pg_isready`
- Redis: `redis-cli ping`
- OSRM: `wget` on a test route

**Alembic migrations** — dedicated Compose service `migrations`, run-once, opt-in via profile `migrate`. **Must be run before each deploy that contains a new migration in `alembic/versions/`** (not auto via `depends_on` — alpha choice to stay explicit). Builds a dedicated image `webservices/ratis_migrations/Dockerfile` (~360 MB: python:3.12-slim + uv + alembic + psycopg + ratis_core; **no** PaddleOCR/OpenCV unlike `product_analyser`):

```bash
# 1. (first time or if Dockerfile/alembic changes) build the image
docker compose -f docker-compose.prod.yml --env-file .env.prod \
  --profile migrate build migrations

# 2. Run-once: applies alembic upgrade head then exits 0
docker compose -f docker-compose.prod.yml --env-file .env.prod \
  --profile migrate run --rm migrations

# 3. Check alembic_version state post-run
docker compose -f docker-compose.prod.yml exec postgres \
  psql -U ratis -d ratis_prod -c "SELECT version_num FROM alembic_version"
```

The `migrate` profile prevents any automatic startup of the service on a standard `docker compose up`. Idempotent: `alembic upgrade head` at the current revision = no-op (default alembic behavior).

**History (2026-04-27)**: before this PR, alembic was not in Docker images → migrations applied in prod via raw psql with manual `UPDATE alembic_version` (cf. KP "DP-alembic-in-image-broke-ci"). That was fragile — no tracking, no downgrade, risk of drift between the migration source and the DB state. The `migrations` service fixes this definitively.

**Inspection / alternative commands** (override the default CMD at run):
```bash
docker compose -f docker-compose.prod.yml --profile migrate run --rm migrations alembic current
docker compose -f docker-compose.prod.yml --profile migrate run --rm migrations alembic history --verbose
docker compose -f docker-compose.prod.yml --profile migrate run --rm migrations alembic downgrade -1
```

**Initial seed** (after migrations):
```bash
# Seed app_settings from ratis_settings.json
docker compose exec auth uv run python -m ratis_core.seed_settings

# Seed France stores (France PBF pre-downloaded in /data/osm/)
docker compose exec auth uv run python -m ratis_batch_osm_sync.osm_sync --pbf /data/osm/france-latest.osm.pbf

# Seed OFF products (pre-downloaded CSV dump)
docker compose exec auth uv run python -m ratis_batch_off_sync.off_sync --dump /data/off/products.csv
```

**Future CI/CD**: GitHub Actions that SSH into the VM on `main` push → `git pull && docker compose up -d --build`. Not implemented in V0, to be added post-alpha.

---

## Migration smoke test (post-deploy)

**Goal**: validate that `alembic upgrade head` correctly applied the expected migrations + that DB structures (tables, columns, indexes, triggers, constraints, ENUMs, extensions) are effective. This runbook accompanies **every prod migration** but explicitly targets the last 3 in quick succession:

> **Note 2026-05-12** — `./scripts/ops/deploy-prod.sh` now runs `alembic upgrade head` automatically between git-pull and build (step 4.5). The pre-deploy check (§1 below) remains useful for noting the pre-migration revision; the `apply migrations` commands (§2) are already covered by `deploy-prod.sh`. Post-migration smoke tests (§3+) still need to be run manually. The standalone `./scripts/ops/migrate-prod.sh` keeps its place for ad-hoc migrations without service redeploy.

| Revision | File | Content |
|---|---|---|
| `20260502_1900_xretail` | `alembic/versions/20260502_1900_cross_retailer_consensus.py` | NRC cross-retailer: `product_name_resolutions.{source_type, retailer_id}` + trigger `trg_pnr_sync_retailer_id` + 3 indexes (including GIN trgm) + extension `pg_trgm` + backfill |
| `20260502_1900_admauad` | `alembic/versions/20260502_1900_admin_settings_audit.py` | Table `admin_settings_audit` + ENUM `admin_settings_audit_status` (4 values) + 3 indexes + 2 CHECK |
| `9082f271f4d5` | `alembic/versions/20260502_1415_9082f271f4d5_merge_admin_audit_cross_retailer_heads.py` | Merge revision (no-op) that reconverges the 2 branches above |

Helpers used below (defined in [`scripts/ops_lib.sh`](../../scripts/ops_lib.sh)):
- `ssh_prod '<cmd>'` — runs `<cmd>` on the prod VM
- `ssh_psql '<sql>'` — runs `<sql>` via `docker compose exec postgres psql -At` (tab-separated output, no banner)
- `ssh_psql_table '<sql>'` — same but aligned output with headers (human-readable)

### 1. Pre-deploy checks

To be done **before** `docker compose --profile migrate run --rm migrations`.

```bash
# (a) Local: HEAD == origin/main + clean tree (R34 EAS pattern)
git fetch origin main
git status                          # → "nothing to commit, working tree clean"
[ "$(git rev-parse HEAD)" = "$(git rev-parse origin/main)" ] && echo OK || echo DRIFT

# (b) Prod: note the currently applied revision (before upgrade)
ssh_prod 'cd /root/ratis && docker compose -f docker-compose.prod.yml --env-file .env.prod \
  --profile migrate run --rm migrations alembic current'
# → e.g.: "20260502_1700_consmatch (head)" — note the value in the deploy log.

# (c) Prod: must return ONE SINGLE head — if >1, STOP and investigate (multi-branch schema drift)
ssh_prod 'cd /root/ratis && docker compose -f docker-compose.prod.yml --env-file .env.prod \
  --profile migrate run --rm migrations alembic heads'

# (d) Local: must return ONE SINGLE head after merge revision 9082f271f4d5
uv run --package ratis-migrations alembic heads
# → "9082f271f4d5 (head)" expected post-merge revision
```

**Rule**: the prod HEAD (b) can be N migrations behind local — that's normal, it's what we're about to apply. But `alembic heads` must return **1 single head** on both sides (otherwise divergent alembic graph → fix before deploy).

### 2. Apply migrations

Exact command (captures full output in a dated file for audit):

```bash
TS=$(date +%Y%m%d_%H%M%S)
ssh_prod "cd /root/ratis && docker compose -f docker-compose.prod.yml --env-file .env.prod \
  --profile migrate run --rm migrations" 2>&1 | tee "deploy_migrate_${TS}.log"
```

**Idempotency**: `alembic upgrade head` does NOT re-run already applied migrations (alembic compares with the `alembic_version` table). Re-running after success = safe no-op.

**Transactionality**: each migration runs in **its own PG transaction**. If a migration fails mid-way:
- The failure is isolated to that migration (PG auto-rollback)
- `alembic_version` reflects the last **successful** migration (not the failed one)
- Investigate the log → fix the code → push → re-run `alembic upgrade head` (resumes where it stopped)

### 3. Post-migration smoke tests

#### 3a. Migration `20260502_1900_xretail` (NRC cross-retailer)

| Check | Command | Expected result |
|---|---|---|
| Columns added on `product_name_resolutions` | `ssh_psql_table '\d product_name_resolutions'` | See rows `source_type` (text NOT NULL DEFAULT 'receipt') and `retailer_id` (uuid, FK → retailers) |
| 3 new indexes present | `ssh_psql "SELECT count(*) FROM pg_indexes WHERE tablename='product_name_resolutions' AND indexname IN ('idx_pnr_scan_source_label', 'idx_pnr_retailer_source_label', 'idx_pnr_norm_label_trgm')"` | `3` |
| CHECK `pnr_match_method_check` extended | `ssh_psql "SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname='pnr_match_method_check'"` | The string contains `'esl'` AND `'cross_source_esl_exact'` |
| CHECK `pnr_source_type_check` created | `ssh_psql "SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname='pnr_source_type_check'"` | `CHECK (source_type IN ('receipt', 'esl'))` |
| Trigger present | `ssh_psql "SELECT count(*) FROM pg_trigger WHERE tgname='trg_pnr_sync_retailer_id'"` | `1` |
| Trigger function present | `ssh_psql "SELECT count(*) FROM pg_proc WHERE proname='fn_sync_pnr_retailer_id'"` | `1` |
| Extension `pg_trgm` loaded (otherwise GIN trgm fails) | `ssh_psql "SELECT count(*) FROM pg_extension WHERE extname='pg_trgm'"` | `1` |

**Backfill validation** (R-KP-42 — prod backfill always needs auditing):

```sql
-- How many rows were backfilled? (info, not a pass/fail)
SELECT count(*) AS total,
       count(*) FILTER (WHERE retailer_id IS NOT NULL) AS with_retailer,
       count(*) FILTER (WHERE retailer_id IS NULL AND store_id IS NOT NULL) AS null_with_store,
       count(*) FILTER (WHERE store_id IS NULL) AS null_without_store
FROM product_name_resolutions;
```

```bash
ssh_psql_table 'SELECT count(*) AS total, count(*) FILTER (WHERE retailer_id IS NOT NULL) AS with_retailer, count(*) FILTER (WHERE retailer_id IS NULL AND store_id IS NOT NULL) AS null_with_store, count(*) FILTER (WHERE store_id IS NULL) AS null_without_store FROM product_name_resolutions'
```

Interpretation:
- `null_with_store > 0` is **expected** for rows whose `stores.retailer_id` is itself NULL (typically `stores.source='user_suggested'` not yet admin-validated). Not a bug.
- `null_with_store == 0` = complete backfill (ideal alpha situation).
- If suspicious: cross-check with `SELECT count(*) FROM stores WHERE retailer_id IS NULL`. The backfill doesn't invent retailer_id values, it joins on stores.

**Trigger smoke test** (transaction rolled back — no side effects):

```sql
BEGIN;

-- Pick a store with non-null retailer_id
WITH src_store AS (
  SELECT id, retailer_id FROM stores WHERE retailer_id IS NOT NULL LIMIT 1
)
INSERT INTO product_name_resolutions (
  scan_id, store_id, normalized_label, source_type, match_method
)
SELECT
  (SELECT id FROM scans LIMIT 1),
  (SELECT id FROM src_store),
  '__SMOKE_TEST__' || gen_random_uuid()::text,
  'receipt',
  'manual'
RETURNING id, store_id, retailer_id, source_type;
-- Expected: retailer_id non-NULL, equal to src_store.retailer_id (trigger did its job)

ROLLBACK;
```

```bash
# Put the command above in a file then:
ssh_prod "cd /root/ratis && docker compose -f docker-compose.prod.yml exec -T postgres \
  psql -U ratis -d ratis_prod" < /tmp/trigger_smoke.sql
```

If the INSERT row comes back with `retailer_id` NULL while `src_store.retailer_id` was non-NULL → **trigger is not firing**, escalate immediately.

#### 3b. Migration `20260502_1900_admauad` (admin_settings_audit)

| Check | Command | Expected result |
|---|---|---|
| Table created + 11 columns | `ssh_psql_table '\d admin_settings_audit'` | `id, timestamp, operator, section, reason, old_data, new_data, diff, status, expires_at, applied_at` |
| ENUM created with 4 ordered values | `ssh_psql "SELECT enumlabel FROM pg_enum WHERE enumtypid='admin_settings_audit_status'::regtype ORDER BY enumsortorder"` | `applied`, `pending_2fa`, `expired`, `cancelled` (in that order) |
| 4 indexes (PK + 3 explicit) | `ssh_psql "SELECT count(*) FROM pg_indexes WHERE tablename='admin_settings_audit'"` | `4` |
| 2 CHECK constraints | `ssh_psql "SELECT count(*) FROM pg_constraint WHERE conrelid='admin_settings_audit'::regclass AND contype='c'"` | `2` |
| Exact CHECK names | `ssh_psql "SELECT conname FROM pg_constraint WHERE conrelid='admin_settings_audit'::regclass AND contype='c' ORDER BY conname"` | `chk_reason_min_len`, `chk_status_2fa_coherence` |
| Partial index `idx_admin_settings_audit_pending` present | `ssh_psql "SELECT indexdef FROM pg_indexes WHERE indexname='idx_admin_settings_audit_pending'"` | Must contain `WHERE (status = 'pending_2fa'::admin_settings_audit_status)` |

#### 3c. Merge revision `9082f271f4d5`

```bash
# After the upgrade, alembic must point to the merge revision and have 1 single head
ssh_prod 'cd /root/ratis && docker compose -f docker-compose.prod.yml --env-file .env.prod \
  --profile migrate run --rm migrations alembic current'
# → "9082f271f4d5 (head)" until the merge; after including the `retroscan` migration (§ 3d)
#   the head becomes `20260502_2100_retroscan`.

ssh_prod 'cd /root/ratis && docker compose -f docker-compose.prod.yml --env-file .env.prod \
  --profile migrate run --rm migrations alembic heads'
# → ONE single line. If 2+ → divergent graph, STOP.
```

#### 3d. Migration `20260502_2100_retroscan` (cabecoin_transactions extensions for `retro_cab`)

This migration (revises `9082f271f4d5`) introduces constraints needed by Job 4 (`retro_cab`) of the new `ratis_batch_data_reconciliation` batch (Phase 1). It writes `reference_type='retro_scan'` + `reason='retro_scan'` to stay isolated from the financial batch `ratis_batch_reconciliation` writes (which uses `reference_type='scan'`).

**Changes**:

- CHECK constraint `cabecoin_transactions_reference_type_check` extended with `'retro_scan'`
- CHECK constraint `cabecoin_transactions_reason_check` extended with `'retro_scan'`
- Partial UNIQUE INDEX `uq_cabtx_retro_scan_credit ON cabecoin_transactions(reference_id) WHERE direction='credit' AND reference_type='retro_scan'` — guarantees application-level idempotency of the batch (safe rerun after crash)

| Check | Command | Expected result |
|---|---|---|
| `alembic current` points to retroscan | `ssh_prod 'cd /root/ratis && docker compose -f docker-compose.prod.yml --env-file .env.prod --profile migrate run --rm migrations alembic current'` | `20260502_2100_retroscan (head)` |
| CHECK reference_type contains `'retro_scan'` | `ssh_psql "SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conrelid='cabecoin_transactions'::regclass AND conname='cabecoin_transactions_reference_type_check'"` | The string contains `'retro_scan'` |
| CHECK reason contains `'retro_scan'` | `ssh_psql "SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conrelid='cabecoin_transactions'::regclass AND conname='cabecoin_transactions_reason_check'"` | The string contains `'retro_scan'` |
| Partial UNIQUE INDEX present | `ssh_psql "SELECT indexdef FROM pg_indexes WHERE tablename='cabecoin_transactions' AND indexname='uq_cabtx_retro_scan_credit'"` | 1 row with `WHERE ... direction = 'credit' AND reference_type = 'retro_scan'` |

**Pre-migration audit** (R-KP-42 — prod backfill always needs auditing; no backfill here but `CREATE UNIQUE INDEX` can fail if pre-existing duplicates):

```sql
-- Possible duplicates that would block CREATE UNIQUE INDEX (should return 0 rows in alpha)
SELECT reference_id, count(*)
FROM cabecoin_transactions
WHERE reference_type = 'retro_scan' AND direction = 'credit'
GROUP BY reference_id
HAVING count(*) > 1;
```

```bash
ssh_psql_table "SELECT reference_id, count(*) FROM cabecoin_transactions WHERE reference_type='retro_scan' AND direction='credit' GROUP BY reference_id HAVING count(*) > 1"
```

Interpretation:
- 0 rows expected in alpha (the `data_reconciliation` batch has never run in prod, no `retro_scan` rows exist → trivial migration).
- If > 0 rows → STOP the migration: manual dedup required (`DELETE FROM cabecoin_transactions WHERE id NOT IN (SELECT min(id) FROM cabecoin_transactions WHERE reference_type='retro_scan' AND direction='credit' GROUP BY reference_id) AND reference_type='retro_scan' AND direction='credit'`) before re-running `alembic upgrade head`. Document the decision in `DECISIONS_PENDING.md`.

**Batch idempotency smoke test** (post-wiring `ratis_batch_data_reconciliation` in `docker-compose.prod.yml` + adding the name to the closed list in `run-prod-batch.sh`; at this stage the batch is not yet wired on prod side, this smoke test is documented for when it will be — cf. `batch/ratis_batch_data_reconciliation/ARCH_BATCH_DATA_RECONCILIATION.md` for progress):

```bash
# Run 1 — first dry-run, identifies candidate rows without mutating
ssh_prod 'cd /root/ratis && docker compose -f docker-compose.prod.yml --env-file .env.prod \
  --profile batch_data_reconciliation run --rm batch_data_reconciliation python run.py --dry-run'

# Run 2 — rerun dry-run, must not re-INSERT (idempotent thanks to partial UNIQUE INDEX)
ssh_prod 'cd /root/ratis && docker compose -f docker-compose.prod.yml --env-file .env.prod \
  --profile batch_data_reconciliation run --rm batch_data_reconciliation python run.py --dry-run'
```

No `UniqueViolation` errors expected: batch INSERTs go through `ON CONFLICT DO NOTHING` targeting `uq_cabtx_retro_scan_credit`. If a `UniqueViolation` surfaces → the batch is not using `ON CONFLICT` correctly, fix on the code side before real prod-run.

**Failure scenarios**:

- **Migration fails at CREATE UNIQUE INDEX** → pre-migration audit above returns > 0 duplicates. Manual dedup + re-run. Unlikely in alpha (zero pre-existing rows).
- **Batch fails post-migration with `check constraint violation`** → migration applied but batch code writes a value other than `'retro_scan'` in `reason`/`reference_type`. Inspect `batch/ratis_batch_data_reconciliation/data_reconciliation/jobs/retro_cab.py`, verify alignment with the new allowed values.
- **Rollback**: `alembic downgrade 9082f271f4d5` restores the pre-migration CHECKs and drops the INDEX. If `retro_scan` rows have already been written in prod, the downgrade will fail on re-CREATE of the CHECKs (constraint violation on existing rows) — you must purge the `retro_scan` rows first. Rare case: the migration lives alongside the batch, downgrade should only happen as an emergency rollback **before** any prod run of the batch.

### 4. Failure scenarios + rollback

**Case 1 — `alembic upgrade head` fails mid-way**:
- The faulty migration auto-rolls back (isolated PG transaction).
- `alembic current` shows the **last successful migration** (not the failed one).
- Procedure: log → fix code → re-push → re-run `alembic upgrade head` (resumes automatically).

**Case 2 — migration applied but runtime service is broken** (e.g.: DROP COLUMN of a field that the v(N-1) service still reads):
- Roll back to the revision **before** the faulty migration:
  ```bash
  ssh_prod 'cd /root/ratis && docker compose -f docker-compose.prod.yml --env-file .env.prod \
    --profile migrate run --rm migrations alembic downgrade <revision_id_target>'
  ```
- **Important**: do NOT use `alembic downgrade -1` on a **merge revision** (`9082f271f4d5`). Alembic refuses the relative argument on a merge node ("Ambiguous walk"). Always specify the explicit target revision, e.g. `alembic downgrade 20260502_1700_consmatch`.
- Verify that `downgrade()` of the faulty migration is properly implemented (presence of `op.execute("DROP CONSTRAINT IF EXISTS ...")` + recreation of previous structures — cf. R07).

**Case 3 — prod backfill corrupted rows** (post-commit regret):
- No automatic rollback: a `UPDATE` backfill is not auto-reversible.
- If the `UPDATE` condition was too broad → identify the affected rows via `updated_at` ≥ deploy timestamp (if the table has an `updated_at` managed by PG trigger, R06).
- Restore from pre-deploy pg_dump backup if necessary (cf. § Backups & disaster recovery).
- Concrete case monitored here: the `xretail` backfill (`UPDATE product_name_resolutions SET retailer_id = s.retailer_id ... WHERE pnr.retailer_id IS NULL`). Strictly additive, only touches NULL rows. Residual risk = near-zero, but monitor if re-applied on a more mature dataset.

**Case 4 — schema drift detected via local pg_dump**:
- If `pg_dump --schema-only` locally diverges from prod (e.g.: ENUM values listed in a different order), **don't panic**: `alembic current` is the source of truth, not pg_dump.
- Reference: KP around `users_provider_check` post-`20260501_2000_nrc_d_admin_user`. pg_dump may display CHECK constraints in a normalized form different from what the migration wrote, without semantic change.
- Check the actual state: `ssh_psql "SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname='<constraint>'"` (runtime ground truth).

### 5. Cross-references

- [`webservices/ratis_product_analyser/ARCH_cross_retailer_consensus.md`](../../webservices/ratis_product_analyser/ARCH_cross_retailer_consensus.md) § DB schema — NRC tables / triggers / indexes detail
- [`ARCH_admin_settings.md`](ARCH_admin_settings.md) § DB schema — `admin_settings_audit` detail + ENUM + 2FA flow
- [`batch/ratis_batch_data_reconciliation/ARCH_BATCH_DATA_RECONCILIATION.md`](../../batch/ratis_batch_data_reconciliation/ARCH_BATCH_DATA_RECONCILIATION.md) — Job 4 `retro_cab` detail that consumes the CHECKs + partial UNIQUE INDEX introduced by `20260502_2100_retroscan`
- [`docs/known/KNOWN_PROBLEMS.md`](../known/KNOWN_PROBLEMS.md) — KP-08 (alembic revision id ≤32 chars), KP-42 (prod backfill audit pre-migration). Alembic multi-heads and `users_provider_check` specific KPs have not (yet) been formally entered in `docs/known/KNOWN_PROBLEMS_INDEX.md` — to add post-deploy if encountered.
- [`scripts/ops_lib.sh`](../../scripts/ops_lib.sh) — `ssh_prod`, `ssh_psql`, `ssh_psql_table` helpers used in this runbook
- [`scripts/test_migrations.sh`](../../scripts/test_migrations.sh) — pre-push local up→down→up test on ephemeral DB (R07 mig-drop)

---

## Running batches in production

**Pattern**: each batch in `batch/ratis_batch_*` is declared in `docker-compose.prod.yml` as a one-shot service, kept behind a profile `batch_<name>`. All share a single image `batch/Dockerfile` (python 3.12 + uv + ratis_core + workspace `--all-packages`). Mirror of the `migrations` pattern (cf. § Code deployment).

**When to use**:
- One-shot ops (initial seed, e.g. `vrac_seed`)
- Replay a batch in debug (day's consensus, targeted purge)
- Manual admin operations (`mystery_announce` test)

**When NOT to use**:
- Regular crons: for now they run via GitHub Actions on the runner's local DB (cf. `.github/workflows/batch_*.yml`). Migrating crons to prod = separate PR (probably systemd-timer Hetzner OR GH Actions SSH-triggering the wrapper).
- No in-process usage from a webservice — a batch always remains **an isolated binary**.

**Wrapper script**: `./scripts/ops/run-prod-batch.sh <name> [extra args]`

```bash
# One-shot initial seed
./scripts/ops/run-prod-batch.sh vrac_seed

# Dry-run consensus
./scripts/ops/run-prod-batch.sh consensus --dry-run

# Explicit bash delegate (direct equivalent on the VM)
docker compose -f docker-compose.prod.yml --env-file .env.prod \
  --profile batch_consensus run --rm batch_consensus --dry-run
```

The wrapper:
1. Validates the batch name against the closed list (typo-proof)
2. SSHes into the VM (`/root/ratis`)
3. `git fetch && git pull --ff-only origin main` (always runs on the latest main code)
4. `docker compose --profile batch_<name> run --rm batch_<name> [args]`
5. Propagates the return code; on failure, shows the command to retrieve logs

**Available batches** (each has its profile `batch_<name>` + its entry point script):

| Name | Entry point | Specific env vars |
|---|---|---|
| `consensus` | `batch/ratis_batch_consensus/consensus.py` | (none beyond `DATABASE_URL`) |
| `vrac_seed` | `batch/ratis_batch_vrac_seed/vrac_seed.py` | (none) |
| `off_sync` | `batch/ratis_batch_off_sync/off_sync/main.py` | `OFF_USER_AGENT`, `OFF_API_BASE_URL` |
| `obp_sync` | `batch/ratis_batch_off_sync/off_sync/main.py --source obp` | (defaults via `off_sync.sources` registry) |
| `osm_sync` | `batch/ratis_batch_osm_sync/osm_sync.py` | `OSM_OVERPASS_URL` |
| `purge` | `batch/ratis_batch_purge/purge.py` | `R2_*` (4 vars) |
| `savings` | `batch/ratis_batch_savings/savings_batch.py` | (none) |
| `referral_payout` | `batch/ratis_batch_referral_payout/payout.py` | `REWARDS_BASE_URL`, `INTERNAL_API_KEY` |
| `mystery_announce` | `batch/ratis_batch_mystery_announce/mystery_announce.py` | (none V0) |
| `reconciliation` | `batch/ratis_batch_reconciliation/run.py` | (none) |
| `sirene_sync` | `batch/ratis_batch_sirene_sync/sirene_sync.py` | `SIRENE_BULK_URL`, `GEOPLATEFORME_GEOCODE_URL`, `SIRENE_BULK_CACHE_DIR` |

**Why a shared Dockerfile**: all batches share the same base (python 3.12 + uv workspace + ratis_core + psycopg). Duplicating 9 Dockerfiles would bloat the registry and slow builds for zero benefit. `uv sync --all-packages` resolves the entire workspace into a single `.venv`. Adding a new batch = a new service block here, no new Dockerfile.

**New env vars to add to `.env.prod`**: `OFF_USER_AGENT`, `OFF_API_BASE_URL`, `OSM_OVERPASS_URL`. Reasonable defaults are provided in `docker-compose.prod.yml` but the operator can override them.

---

## Mobile (EAS APK Android)

**Config file**: `ratis_client/eas.json`.

**V0 profile**: `preview` — APK side-load, distribution=internal, channel=preview, API URLs point to `*.ratis.app` prod.

**Build**:
```bash
cd ratis_client
npx eas build --platform android --profile preview
```

→ Builds in the Expo cloud (~10-15 min). Produces an automatically signed APK (EAS-managed keystore). Download URL provided.

**Alpha distribution**:
- Upload APK to a public Cloudflare R2 bucket → shareable link
- Or GitHub Releases (private repo → token in the URL)
- Users: enable "Unknown sources" on Android → install APK → Google login → usage

**Google OAuth config** (crucial):
1. After the 1st EAS build, retrieve the keystore SHA-1:
   ```bash
   npx eas credentials -p android
   # → shows SHA-1 in Keystore info
   ```
2. Google Cloud Console → Ratis project → Credentials → OAuth 2.0 Client (Android) → **Package name** `app.ratis.client` + **SHA-1 fingerprint** (from EAS)
3. Rebuild APK if SHA-1 was missing on the first build (rare)

**Declared permissions** (`app.json → android.permissions`):
- CAMERA, ACCESS_FINE_LOCATION, ACCESS_COARSE_LOCATION, INTERNET, READ_EXTERNAL_STORAGE
- Plugins `expo-camera` + `expo-location` with FR permission strings

---

## Secrets & env vars

**Template file**: `.env.example` (repo root) — vars documented with `<REQUIRED>` / `<OPTIONAL>`.

**Actual file on server**: `/root/ratis/.env.prod` — **NEVER committed**, `chmod 600`, owned by `root` only.

**Generated once** (locally, then copied into `.env.prod`):
```bash
python -c "import secrets; print('JWT_SECRET=' + secrets.token_urlsafe(64))"
python -c "import secrets; print('INTERNAL_API_KEY=' + secrets.token_urlsafe(32))"
python -c "import secrets; print('ADMIN_API_KEY=' + secrets.token_urlsafe(32))"
python -c "import secrets; print('CASHBACK_WEBHOOK_SECRET_AFFILAE=' + secrets.token_urlsafe(32))"
python -c "import secrets; print('CASHBACK_WEBHOOK_SECRET_AWIN=' + secrets.token_urlsafe(32))"
python -c "import secrets; print('CASHBACK_WEBHOOK_SECRET_CJ=' + secrets.token_urlsafe(32))"
python -c "import secrets; print('POSTGRES_PASSWORD=' + secrets.token_urlsafe(24))"
```

**Non-negotiable rules**:
- `JWT_SECRET` identical across all 5 services (shared signature)
- `INTERNAL_API_KEY` identical across all 5 services (internal communication)
- `ADMIN_API_KEY` distinct (used only by you for `/admin/*` endpoints)
- `CASHBACK_WEBHOOK_SECRET_{AFFILAE,AWIN,CJ}` — one distinct secret PER provider (webhook validation): a leaked secret only compromises a single provider

**External secrets** (obtained from providers, do not generate):
- `GOOGLE_CLIENT_ID` — Google Cloud Console (OAuth project)
- `APPLE_CLIENT_ID` — empty in V0 (Android-only), to fill for iOS later
- `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET` — empty in V0 (no payment)
- `R2_*` — Cloudflare R2 bucket credentials
- `SENTRY_DSN` — to create on sentry.io
- `GIFT_CARD_PROVIDER_KEY` — empty until Runa KYB is validated

**Rotation**: any `_KEY` or `_SECRET` rotatable via new env + service restart. `JWT_SECRET` → invalidates ALL active tokens (forced logout).

**`require_env(...)` ↔ compose prod drift (KP-79)**: adding a `require_env("VAR")` in `webservices/ratis_<svc>/main.py` IMMEDIATELY requires adding `VAR: ${VAR:?VAR is required}` (fail-fast) or `VAR: ${VAR:-<default>}` (default) in the `environment:` map of the service in `docker-compose.prod.yml`. Without the mapping, compose does not pass through the env from `.env.prod` to the container and the service crashes at boot on the next deploy. The CI guard `scripts/tests/test_compose_env_passthrough.py` (wired in `.github/workflows/security.yml` → job `compose_env_passthrough`) parses each service main.py via AST and fails if a `require_env(...)` doesn't have a corresponding mapping.

---

## Cross-service URL conventions

Internal services communicate via HTTP (rewards_client, notifier_client, au_client, rw_client). **Base URLs MUST include the `/api/v1` prefix** because:

- All FastAPI routers are mounted with `app.include_router(router, prefix="/api/v1")` (cf `webservices/ratis_*/main.py`)
- Clients build paths from the base URL **without re-adding** the prefix (e.g.: `rewards_client.py` → `f"{base_url}/rewards/referral/trigger"`)
- Without `/api/v1` in the base, the final path is `/rewards/...` which returns 404 — and since most calls are fire-and-forget, the 404 is silently swallowed (warning log only, no user visibility)

**Expected format per variable**:

| Var | Format | docker-compose example | Railway example |
|---|---|---|---|
| `AU_BASE_URL` | `<host>/api/v1` | `http://auth:8001/api/v1` | `https://auth.ratis.app/api/v1` |
| `RW_BASE_URL` | `<host>/api/v1` | `http://rewards:8004/api/v1` | `https://rewards.ratis.app/api/v1` |
| `REWARDS_BASE_URL` | `<host>/api/v1` | `http://rewards:8004/api/v1` | `https://rewards.ratis.app/api/v1` |
| `NOTIFIER_URL` | `<host>/api/v1/notify` | `http://notifier:8005/api/v1/notify` | `https://notifier.ratis.app/api/v1/notify` |

`NOTIFIER_URL` includes the full path up to `/notify` because `notifier_client.notify_user()` POSTs directly to the URL without appending.

**Who consumes what**:

| Var | Consumers |
|---|---|
| `AU_BASE_URL` | PA (admin mini UI → `/admin/users`) |
| `RW_BASE_URL` | PA (admin mini UI → `/admin/settings/*`) |
| `REWARDS_BASE_URL` | AU + PA worker + batch_referral_payout (rewards_client) |
| `NOTIFIER_URL` | PA + AU + RW (notifier_client.notify_user) |

**Bug 2026-05-03 V1**: the defaults in `docker-compose.prod.yml` did not include `/api/v1` → admin UI 404, silent push notifications, silent cashback events. Fixed via PR `fix/internal-cross-service-urls-include-api-v1`. If you deploy to a new host (Mac mini, Iceland) **verify** that `.env.prod` correctly follows the `/api/v1` pattern.

**Defensive pattern (V2 candidate)**: add an internal URL health-check at service boot (`require_env` + an `httpx.get(base_url)` that validates a 401/403 response but not 404). Allows detecting an env regression at startup rather than on the first user call.

---

## Observability

**V0** (minimum viable):
- **Sentry** on all 5 webservices + mobile — variable `SENTRY_DSN`, no-op if empty
- **RequestIDMiddleware** in `ratis_core` — X-Request-ID in every log
- **Docker logs** — `docker compose logs -f <service>` for live debugging
- **Sentry→Notion webhook** (`tools/sentry_webhook/`) — not deployed V0, to do post-alpha

**Post-alpha to install on the VM**:
- **Netdata** (free) — web dashboard CPU/RAM/disk/net
- **Uptime Kuma** (free, Docker) — external monitoring of the 5 subdomains
- **Grafana + Prometheus** (optional) — if custom metrics are needed (OCR queue, conversion rate)

---

## Backups & disaster recovery

**V0 minimum**:
- **Automatic Hetzner snapshot** — option enabled at 20% of VM price (~€1.30/mo). Daily snapshot, 7-day retention. Restoration = 1 click, 5 min.
- **Daily Postgres dump** (to add) — cron `pg_dump` → upload to Cloudflare R2 → 30-day retention. Script `infra/backup-postgres.sh` to write in a follow-up PR.
- **Pre-deploy automatic snapshot** — `deploy-prod.sh` runs `pg_dump | gzip -9 > /var/backups/ratis/pre_deploy_<TS>.sql.gz` by default before any mutation (build/up). Covers the deployment window when the daily Hetzner snapshot may be >12h old. Opt-out via `--skip-backup` for emergency redeployments where a snapshot already exists.

**V0 target RPO/RTO**:
- RPO (max data loss): 24h (daily snapshot)
- RTO (restoration time): 30 min

**Post-alpha / scale**:
- Postgres streaming replication to a 2nd read-only server (Hetzner Pro)
- Continuous WAL backups via barman or pgBackRest
- RPO < 15 min, RTO < 15 min

**`ADMIN_API_KEY` rotation (manual, no endpoint)** — orchestrator decision
2026-05-03 (L3 security audit): no `/admin/rotate-key` endpoint exposed
(increased attack surface). Rotation = manual SSH Hetzner:
1. `python -c "import secrets; print(secrets.token_urlsafe(32))"` → new key ≥ 43 chars
2. Update `.env.prod` (`ADMIN_API_KEY=<new>`) on the VM
3. `docker compose restart ratis_product_analyser ratis_rewards`
4. Sentry captures the boot event (services re-init via `init_sentry` + lifespan
   `require_env_min_length("ADMIN_API_KEY", 32)` — a boot crash = key too short).
5. Re-login admin UI (HMAC cookies keyed by the old key are
   automatically invalidated — no purge needed).

---

## Migration path

**Mac mini — ALREADY done (2026-05-04, PR #287)**. Production ratis now runs on the Mac mini M4 Pro (macOS, arm64). Detailed runbook: [[ARCH_itops]] § Vision + § Topology. For the record, the applied steps were:
1. Boot Mac mini with stock macOS
2. Install Docker Desktop
3. `git clone https://github.com/Belladynn/ratis /root/ratis`
4. Copy `.env.prod` from Hetzner (via `scp`)
5. `docker compose -f docker-compose.prod.yml --profile self-hosted up -d`
6. Optional: restore Postgres dump on Mac mini (if prod was already running)
7. Swap Cloudflare DNS → residential IP (via Cloudflare Tunnel or DDNS)

**AWS (when Mac mini saturation signal fires)** — workflow defined in PROD_CHECKLIST.md § Stratégie hébergement. Viable switchover in ~1 week if Terraform is ready.

---

## Recurring ops commands

### Ops scripts (CLI shortcuts)

Rather than retyping long SSH+docker compose+psql incantations each time,
8 shell scripts live at the repo root. See [`OPS_SCRIPTS.md`](../ops/OPS_SCRIPTS.md)
(auto-generated) for details. Regenerate with `./scripts/ops/update-scripts-help.sh`.

| Task | Script |
|---|---|
| Pull main + (pg_dump backup) + **alembic upgrade head** + rebuild + restart services on prod | `./scripts/ops/deploy-prod.sh [--services CSV] [--skip-backup] [--skip-migrations] [--dry-run]` |
| Run alembic upgrade head on prod (ad-hoc, without service redeploy) | `./scripts/ops/migrate-prod.sh` |
| OTA push (with channel-match guard) | `./scripts/ops/ota-push.sh [preview\|production]` |
| Start an EAS Android build | `./scripts/ops/eas-build.sh [preview\|production]` |
| Debug a receipt (worker logs + admin debug HTML) | `./scripts/ops/scan-debug.sh [<receipt_id>]` |
| Last 5 prod receipts | `./scripts/ops/last-receipts.sh` |
| Prod health snapshot (services + alembic + counts) | `./scripts/ops/prod-status.sh` |

All scripts have `--help` and require SSH key `~/.ssh/ratis_hetzner_v3` loaded
in the agent (cf. `start_all.sh`).

### PuTTY + WinSCP setup (Windows ops, no-CLI)

**Local prerequisites**:
- OpenSSH private key `ratis-hetzner-v3` in `C:\Users\FlowUP\.ssh\`
- PuTTY + WinSCP installed (https://www.putty.org/ + https://winscp.net/)

**1. Convert OpenSSH key → PuTTY format (.ppk) — once only**:
1. Launch **PuTTYgen**
2. Conversions → Import key → select `C:\Users\FlowUP\.ssh\ratis-hetzner-v3` (the private key, without `.pub`)
3. Save private key → save as `C:\Users\FlowUP\.ssh\ratis-hetzner-v3.ppk`
4. Keep PuTTYgen open or copy the `.ppk` version somewhere safe — this is what PuTTY/WinSCP will load

**2. PuTTY profile (saved for reuse)**:
1. Launch **PuTTY**
2. Session → Host Name: `46.225.63.79` · Port: `22` · Connection type: SSH
3. Connection → Data → Auto-login username: `root`
4. Connection → SSH → Auth → Credentials → Private key file: `C:\Users\FlowUP\.ssh\ratis-hetzner-v3.ppk`
5. Session → Saved Sessions: type `ratis-prod` → Save
6. To connect: double-click on `ratis-prod` in the list

**3. WinSCP profile (file transfer)**:
1. Launch **WinSCP**
2. New Site → File protocol: SFTP · Host name: `46.225.63.79` · Port: `22` · User name: `root`
3. Advanced → SSH → Authentication → Private key file: `C:\Users\FlowUP\.ssh\ratis-hetzner-v3.ppk` (or native `.ppk` if WinSCP supports it)
4. Save → site name: `ratis-prod`
5. To connect: double-click on `ratis-prod`. You get a split explorer (left = your machine, right = `/root/` on the server).

**4. Once connected via PuTTY** — typical commands you will need to type (or that you can put in shell scripts):

```bash
# Go to the prod repo
cd /root/ratis

# See what's running
docker compose -f docker-compose.prod.yml ps

# See a service's logs live (Ctrl+C to quit)
docker compose -f docker-compose.prod.yml logs -f --tail=100 ratis_product_analyser_worker

# Edit .env.prod (nano = simple console editor — Ctrl+O to save, Ctrl+X to quit)
nano .env.prod

# Reload services after .env change (force-recreate otherwise Compose doesn't re-read the env)
docker compose -f docker-compose.prod.yml --env-file .env.prod --profile self-hosted \
  up -d --force-recreate ratis_product_analyser ratis_product_analyser_worker

# Pull the new code version (after PR merged on main)
git pull origin main
docker compose -f docker-compose.prod.yml --env-file .env.prod --profile self-hosted up -d --build

# Alembic DB migration (if a new commit adds a file in alembic/versions/)
# → use the dedicated `migrations` service (profile `migrate`, run-once)
docker compose -f docker-compose.prod.yml --env-file .env.prod \
  --profile migrate run --rm migrations
```

**5. If you want to edit `.env.prod` from Windows via WinSCP**:
- ⚠️ **DO NOT** double-click to open in Notepad → Windows CRLF will break everything (cf KP-25). Using nano on the server via PuTTY is the safe way.
- If you really want to edit locally: configure WinSCP → Editor → use an editor that forces LF (Notepad++ or VS Code, with view → end-of-line forced to LF).

### Create / rescale Hetzner VM

(via `hcloud` CLI + ephemeral token):
```bash
# Token in current session env var only
export HCLOUD_TOKEN="<token_fresh>"

# Create VM
hcloud server create --name ratis-prod-01 --type cx33 --image ubuntu-24.04 \
  --location nbg1 --ssh-key ratis-hetzner-v3 \
  --user-data-from-file infra/cloud-init-hetzner.yaml \
  --label env=prod --label project=ratis

# Rescale to a larger type (requires power off)
hcloud server poweroff ratis-prod-01
hcloud server change-type ratis-prod-01 <new-type>   # e.g.: cx43, ccx13, etc.
hcloud server poweron ratis-prod-01

# Delete
hcloud server delete ratis-prod-01
```

**Deploy new code version**:
```bash
ssh root@46.225.63.79
cd /root/ratis
git pull origin main
docker compose -f docker-compose.prod.yml --env-file .env.prod --profile self-hosted up -d --build
# If DB migration:
docker compose -f docker-compose.prod.yml --env-file .env.prod \
  --profile migrate run --rm migrations
```

**View a service's logs**:
```bash
ssh root@46.225.63.79 "cd /root/ratis && docker compose -f docker-compose.prod.yml logs -f --tail=100 <service>"
```

**Rebuild PaddleOCR models** (if corrupted):
```bash
ssh root@46.225.63.79
cd /root/ratis
docker compose -f docker-compose.prod.yml exec product_analyser \
  python -c "from paddleocr import PaddleOCR; PaddleOCR(use_angle_cls=True, lang='fr', show_log=False)"
```

---

## Emergency runbook

| Symptom | Diagnostic | Fix |
|---|---|---|
| Services down, site unreachable | `ssh root@<IP> docker ps` | If no container: `docker compose up -d`. If Caddy crashed: `docker logs caddy` + restart |
| TLS Let's Encrypt fail | Cloudflare proxy enabled | Disable proxy (grey cloud), wait 1 min, `docker restart caddy` |
| Postgres disk full | `df -h /var/lib/docker/volumes/` | `VACUUM FULL` + check WAL logs + consider disk rescale |
| OCR queue blocked | `docker logs product_analyser` | Redis down? Celery worker crashed? Restart: `docker compose restart product_analyser redis` |
| Intermittent 502 Caddy error | Upstream service down | `docker compose restart <service>` and check logs |
| RAM saturated (OOM killer active) | `dmesg \| grep oom` | Upgrade CAX21 → CAX31, or reduce concurrent PaddleOCR workers |
| Cloud-init bootstrap not finished | `cloud-init status` | See `/var/log/cloud-init-output.log` for the exact error |
| SSH refused | Fail2ban blacklisted? | Hetzner console → rescue mode → `fail2ban-client unban <your_ip>` |
| Hetzner API token compromised | Measured panic | Hetzner UI → Security → API Tokens → delete token. Create new one if needed. |

**Emergency contacts**:
- Hetzner support: support@hetzner.com (24/7, response <2h)
- Cloudflare support: dashboard → chat (Pro plan required for priority chat)
- Anthropic status: status.anthropic.com (for Sentry → Notion webhook down)
