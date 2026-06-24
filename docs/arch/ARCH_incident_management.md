---
# Identité
type: cross-cutting
service: incident-management
status: LIVRÉ V0

# Navigation (Obsidian + RAG)
parent: ARCH_RATIS
sub_archs: []
related: [ARCH_n8n_pipelines, ARCH_agent_mcp, ARCH_itops, ARCH_deployment]

# Technique
tech: [GlitchTip, Sentry SDK protocol, Docker Compose, PostgreSQL 16, Valkey, bash wrapper, macOS Keychain]
tables: []
env_vars:
  - GLITCHTIP_DOMAIN
  - SECRET_KEY  # Django, randomly generated at setup
  - DATABASE_URL  # Dedicated Postgres for GlitchTip, isolated from Ratis PG
  - VALKEY_URL
  - REDIS_URL
  - CELERY_BROKER_URL
  - CELERY_RESULT_BACKEND
  - GLITCHTIP_API_TOKEN  # consumed by n8n + scripts, read from Keychain
  - GLITCHTIP_DSN_RATIS_MOBILE
  - GLITCHTIP_DSN_RATIS_BACKEND
  - GLITCHTIP_DSN_N8N_WORKFLOWS
depends_on:
  - macOS Keychain (security CLI) — admin token + DSN storage
  - Docker Compose — orchestration of the 5 GlitchTip services (web, worker, migrate, postgres, valkey)
  - Wrapper bash `~/glitchtip/bin/glt` — simplified GlitchTip API

# Business
tags: [incidents, sentry-replacement, glitchtip, self-hosted, sunset-notion, rgpd, local-first, kanban, dedup, observability]
business_domain: infra-observability
rgpd_concern: true  # incidents may contain PII (stacktraces, user_id) — privacy local-first

# Freshness (R34)
updated: 2026-05-31
last_chunk_completed: V0 delivered — 3 initial projects + glt wrapper + n8n workflows migrated
---

# incident-management — Self-hosted GlitchTip as the central incident system

> TL;DR: replace Notion (Ratis INCIDENTS DB) with **self-hosted GlitchTip** as the unified incident management rail. Lightweight stack (~600 MB RAM, 5 Docker containers) hosted on Mac mini, natively Sentry-compatible (SDK clients point directly → 0 n8n relay needed), full REST API for scripting/automation, native RGPD compliance (zero data outside LAN). Replaces 1 SaaS (Notion) + 1 monthly token subscription + several n8n relay workflows. Doctrine: "1 durable admin token + N write-only DSNs per project, everything in Keychain `ratis-agent-mcp`".
> @tags: glitchtip self-hosted sentry-compat sunset-notion incidents kanban dedup rgpd local-first docker-compose wrapper-cli livré-v0
> @status: LIVRÉ V0
> @subs: auto

> [[ARCH_RATIS]] · relations : [[ARCH_n8n_pipelines]], [[ARCH_agent_mcp]], [[ARCH_itops]], [[ARCH_deployment]]

> This ARCH documents the **Ratis incident management** rail post-Notion sunset. It replaces the former system (Notion Ratis INCIDENTS DB + n8n relay workflows Sentry/batch → Notion). The pattern is now: SDK clients (Expo, Python services, batch jobs) send their errors **directly** to GlitchTip via Sentry-compatible DSN. n8n is no longer needed for standard Sentry ingestion (workflow `sentry-ingest.json` deleted). There remains a `batch-sentinel.json` workflow for GH Actions batch outcomes (non-SDK) that POST to the GlitchTip ingestion endpoint in Sentry event format. And `github-pr-merged-closer.json` which PATCHes GlitchTip issues via admin API to `status=resolved` on PR merge.

## Index

- [Vision & motivation](#vision--motivation)
- [Target architecture](#target-architecture)
- [IM-1 — Self-hosted GlitchTip stack (~/glitchtip)](#im-1)
- [IM-2 — 3 initial projects (mobile / backend / n8n-workflows)](#im-2)
- [IM-3 — Ingestion: direct SDK + batch via n8n](#im-3)
- [IM-4 — Wrapper CLI `glt`](#im-4)
- [IM-5 — Migration from Notion (full sunset)](#im-5)
- [IM-6 — Future doctrine (V1+: source maps, perf monitoring, scaling)](#im-6)
- [Out of scope](#out-of-scope)
- [Cross-references](#cross-references)

---

## Vision & motivation

Notion as an incident system had 4 structural problems accumulated in V0:

1. **Token friction**: Notion integration + monthly subscription + periodic rotation + labyrinthine UI. See `DECISIONS_PENDING.md` § "Core doctrine: security through isolation, not segmentation".
2. **3 n8n relay workflows** (sentry-ingest, batch-sentinel, github-pr-merged-closer) that existed *solely* to transform Sentry events → Notion API calls. Double maintenance burden (ARCH Notion + ARCH n8n + ARCH Sentry).
3. **Data outside LAN**: stack traces + PII (user_id, paths, metadata) were sent to Notion (US). Conflict with PRIVACY.md and the Ratis RGPD doctrine (never PII outside trusted infrastructure).
4. **Slow UI, rigid schema**: Notion DB views for 2 historical tickets = over-engineering. Not suited to real V1 volume (~10-100 events/day).

**Retained GlitchTip pattern**:

- **Self-hosted Docker** on Mac mini (5 containers, ~600 MB RAM, isolated from Ratis PG and Ratis Redis).
- **Sentry-compatible**: identical ingestion protocol to the official Sentry SDK. SDK clients point directly to the GlitchTip DSN (1 config line). Zero code to write.
- **Full REST API**: projects, issues, events, tags, releases. `glt` bash wrapper for day-to-day commands.
- **Native Kanban**: status `open/resolved/unresolved/ignored`. No need to reproduce Kanban logic by hand.
- **Automatic dedup** by stack trace fingerprint. No need for the custom External ID hash we had in Notion.
- **0 subscription**: open-source AGPL, free, no plan, no artificial limits.
- **Native RGPD**: data stays on Mac mini (Hetzner for production later). No US routing.

---

## Target architecture

```
┌──────────────────────────────────────────────────────────────────────────┐
│ Mac mini (M4 Pro, 48 GB RAM)                                             │
│                                                                          │
│  ┌────────────────────────────────────────────┐                         │
│  │ ~/glitchtip/                               │                         │
│  │  ├─ docker-compose.yml (5 services)        │                         │
│  │  ├─ .env (SECRET_KEY, DATABASE_URL, ...)   │  ← gitignored           │
│  │  └─ bin/glt  (wrapper CLI bash, 250 l)     │                         │
│  │                                            │                         │
│  │  Services :                                │                         │
│  │  - ratis-glitchtip-web (Django + granian)  │  127.0.0.1:8000          │
│  │  - ratis-glitchtip-worker (Celery + beat)  │                         │
│  │  - ratis-glitchtip-postgres (PG 16 alpine) │  internal ports only    │
│  │  - ratis-glitchtip-valkey (Redis-compat)   │                         │
│  │  - ratis-glitchtip-migrate (one-shot)      │                         │
│  └────────────────────────────────────────────┘                         │
│                ▲                          ▲                              │
│                │ POST events              │ HTTP API (Bearer admin)      │
│                │ via DSN                  │                              │
│  ┌─────────────┴──────────┐   ┌──────────┴────────────────────┐         │
│  │ SDK clients            │   │ Scripts / n8n / wrapper glt    │         │
│  │ - Expo (ratis-mobile)  │   │ - n8n batch-sentinel           │         │
│  │ - Python services      │   │ - n8n github-pr-merged-closer  │         │
│  │ - n8n workflows        │   │ - glt CLI (admin commands)     │         │
│  └────────────────────────┘   └────────────────────────────────┘         │
│                                                                          │
│  Keychain `ratis-agent-mcp` :                                            │
│  - admin-glitchtip               (64 chars, API admin token, immutable)  │
│  - ops-glitchtip-dsn-ratis-mobile   (56 chars, DSN public Expo)         │
│  - ops-glitchtip-dsn-ratis-backend  (56 chars, DSN public Python svc)   │
│  - ops-glitchtip-dsn-n8n-workflows  (56 chars, DSN public n8n + batch)  │
└──────────────────────────────────────────────────────────────────────────┘
```

---

## IM-1 — Self-hosted GlitchTip stack (~/glitchtip) · ARCH_incident_management.md · LIVRÉ V0

> TL;DR: `~/glitchtip/docker-compose.yml` orchestrates 5 services (web + worker + postgres + valkey + one-shot migrate). Port 8000 on loopback only (Tailscale Funnel possible later for external ingestion). Dedicated PostgreSQL and Valkey (not shared with ratis-postgres or ratis-redis for isolation). Total RAM ~600 MB. Resource limits: web 1 GB / worker 512 MB.
> @tags: docker-compose 5-services postgres-16-alpine valkey-7 granian celery loopback-only isolation-resources
> @status: LIVRÉ V0
> @subs: auto

Setup install (outside repo, eventually to be migrated to `infra/itops/glitchtip/`):

```sh
mkdir -p ~/glitchtip && cd ~/glitchtip
# .env generates SECRET_KEY + PG_PASSWORD random at initial bootstrap
docker compose up -d
# First access via http://localhost:8000 → create admin account via UI (once)
# Disable ENABLE_USER_REGISTRATION=False after bootstrap
```

Critical env vars:
- `DATABASE_URL=postgres://glitchtip:...@glitchtip-postgres:5432/glitchtip`
- `VALKEY_URL=valkey://glitchtip-valkey:6379/0` + `REDIS_URL=redis://glitchtip-valkey:6379/0`
- `CELERY_BROKER_URL` + `CELERY_RESULT_BACKEND` (same valkey)
- `SECRET_KEY` (Django, 50+ chars urlsafe, generated at bootstrap)
- `GLITCHTIP_DOMAIN=http://localhost:8000`
- `ENABLE_USER_REGISTRATION=False` (re-enable temporarily only for admin bootstrap)

**Future migration**: move `~/glitchtip/` to `infra/itops/glitchtip/` in the Ratis repo once V0 stability is confirmed (probably 2-4 weeks of real usage).

---

## IM-2 — 3 initial projects (mobile / backend / n8n-workflows) · ARCH_incident_management.md · LIVRÉ V0

> TL;DR: 3 GlitchTip projects created via API at setup, 1 public DSN per project, stored in Keychain. Convention: `ratis-<scope>` for the project name, `ops-glitchtip-dsn-<scope>` for the Keychain DSN account. SDK → project routing is handled by the DSN injected into each service's config.
> @tags: 3-projects ratis-mobile ratis-backend n8n-workflows dsn keychain-ops convention
> @status: LIVRÉ V0
> @subs: auto

| GlitchTip project | Event sources | DSN Keychain | Platform |
|---|---|---|---|
| `ratis-mobile` | Expo client (errors + boundary catches) | `ops-glitchtip-dsn-ratis-mobile` | `javascript-react-native` |
| `ratis-backend` | 5 FastAPI Python services (auth, product_analyser, list_optimiser, rewards, notifier) | `ops-glitchtip-dsn-ratis-backend` | `python` |
| `n8n-workflows` | n8n workflows + GH Actions batch jobs (via batch-sentinel) | `ops-glitchtip-dsn-n8n-workflows` | `javascript` |

**How a Ratis service retrieves its DSN** (at runtime, never in plaintext in the code):

```sh
# In the entrypoint / .env of a service
export SENTRY_DSN=$(security find-generic-password -s ratis-agent-mcp -a ops-glitchtip-dsn-ratis-backend -w)
```

The Sentry SDK sees this env var, initializes, and sends events to the GlitchTip DSN. No application code modification required.

---

## IM-3 — Ingestion: direct SDK + batch via n8n · ARCH_incident_management.md · LIVRÉ V0

> TL;DR: 2 ingestion paths to GlitchTip. (1) **Direct SDK via DSN** for Expo + Python services (Sentry-compatible, 0 custom code). (2) **n8n `batch-sentinel.json`** for GH Actions batch outcomes (which do not run a Sentry SDK). Historical workflow `sentry-ingest.json` **deleted** (SDKs point directly, no more relay). Workflow `github-pr-merged-closer.json` adapted to PATCH GlitchTip issue instead of Notion page.
> @tags: ingest sdk-direct sentry-compat n8n batch-sentinel sunset-sentry-ingest pr-merged-closer
> @status: LIVRÉ V0
> @subs: auto

### Path 1 — Direct SDK (Sentry-compatible, the simplest)

Official Sentry SDKs (Python, JavaScript, React Native) speak the **Sentry SDK protocol**. GlitchTip implements that same protocol. Therefore:

```python
# ratis_auth/main.py (and the same for all Ratis Python services)
import sentry_sdk
sentry_sdk.init(dsn=os.environ["SENTRY_DSN"])  # = GlitchTip DSN injected via Keychain
```

```typescript
// ratis_client/services/sentry.ts (Expo)
import * as Sentry from "@sentry/react-native";
Sentry.init({ dsn: process.env.EXPO_PUBLIC_SENTRY_DSN });  // = GlitchTip DSN
```

No custom logic is required. GlitchTip ingests, deduplicates, classifies, and alerts.

### Path 2 — n8n `batch-sentinel.json` (for non-SDK sources)

GH Actions batch jobs do not run a Sentry SDK init (fast run, ephemeral container, no persistent session). Their outcome (success/fail) is posted to n8n via the composite-action `report-batch-outcome` (HMAC signed, defined in `.github/actions/report-batch-outcome/`).

`batch-sentinel.json` receives this webhook, validates the HMAC, and **POSTs** a Sentry-formatted event to the GlitchTip ingest endpoint:

```
POST {DSN_URL}/store/?sentry_key={public_key}
Body: {
  "message": "[batch fail] osm_sync",
  "level": "error",
  "tags": { "source": "batch", "workflow": "osm_sync", "severity": "fatal" },
  "fingerprint": ["batch-osm_sync"],
  "extra": { "run_id": "...", "loki_query": "..." }
}
```

The `fingerprint` guarantees deduplication on the GlitchTip side (a batch that fails repeatedly = 1 single issue that increments, same as before with Notion External ID).

### Path 3 — Automatic closer on PR merge

`github-pr-merged-closer.json` receives GitHub webhooks `pull_request.closed && merged=true`. Parses the PR body looking for the pattern `glitchtip-issue:(\d+)` (replaces the old `notion-uuid:(...)`). If matched, PATCHes the GlitchTip issue:

```
PATCH http://localhost:8000/api/0/issues/{id}/
Authorization: Bearer {GLITCHTIP_API_TOKEN}
Body: { "status": "resolved" }
```

PR convention: the developer (human or SA agent) references the issue in the body with `glitchtip-issue:123` to trigger automatic closure on merge.

---

## IM-4 — Wrapper CLI `glt` · ARCH_incident_management.md · LIVRÉ V0

> TL;DR: `~/glitchtip/bin/glt` is a lightweight bash wrapper (~250 lines) that encapsulates GlitchTip API calls. The admin token is read from the Keychain on each call, never exposed in plaintext in the code or in outputs (except via an explicit flag). 9 subcommands covering common operations (list, add, remove, resolve, status, show-dsn, orgs, teams, help).
> @tags: wrapper-bash glt cli token-keychain api-glitchtip 9-commands no-secret-exposure
> @status: LIVRÉ V0
> @subs: auto

Available commands:

```sh
glt help                                  # Help
glt status                                # Org + projects + counts summary
glt list-projects                         # List org projects
glt list-issues <project> [--limit N]     # Issues for a project
glt list-events <project> [--limit N]     # Latest events
glt add-project <name> [--platform X]     # Creates project + stores DSN in Keychain
glt remove-project <slug>                 # Deletes project + removes DSN
glt show-dsn <project> [--public]         # Confirms DSN existence (--public for value)
glt resolve <issue_id>                    # Marks issue as resolved
glt orgs / glt teams                      # Debug
```

Security convention:
- GlitchTip admin token stored only in Keychain (`account=admin-glitchtip`)
- The wrapper reads it via `security find-generic-password -w` at the time of each call
- No output prints the token, unless the user passes `--show-token` explicitly (not implemented in V0)
- DSNs are less sensitive (write-only) but follow the same Keychain convention

**Add to PATH**:
```sh
echo 'export PATH="$HOME/glitchtip/bin:$PATH"' >> ~/.zshrc
```

---

## IM-5 — Migration from Notion (full sunset) · ARCH_incident_management.md · LIVRÉ V0

> TL;DR: Notion sunset in a single pass (~1 PR), no data migration (the 2 historical incidents visible in Ratis INCIDENTS Notion were throwaway smoke tests — Guillaume's decision 2026-05-31). Removal of the `notion_tools.py` module (agent-mcp), removal of the `sentry-ingest.json` workflow (n8n), adaptation of `batch-sentinel.json` + `github-pr-merged-closer.json` to point to GlitchTip. Notion token deleted from Keychain. Notion integration revoked from the Notion UI by Guillaume.
> @tags: sunset migration zero-data-loss 2026-05-31 1-pr propre clean-break
> @status: LIVRÉ V0
> @subs: auto

### Code changes made (PR `chore/notion-sunset`)

| File | Action |
|---|---|
| `tools/agent-mcp/src/agent_mcp/tools/notion_tools.py` | **Deleted** (429 lines, 4 tools) |
| `tools/agent-mcp/tests/tools/test_notion_tools.py` | **Deleted** (30K, 30+ tests) |
| `tools/agent-mcp/src/agent_mcp/server.py:132-135` | Removed import + register_all() |
| `tools/agent-mcp/src/agent_mcp/cli.py:57` | Removed `"notion"` from `REQUIRED_PROVIDER_ACCOUNTS` |
| `infra/itops/n8n-workflows/sentry-ingest.json` | **Deleted** (1006 lines, 25 Notion refs) — relay no longer needed since Sentry SDK points directly to GlitchTip |
| `infra/itops/n8n-workflows/batch-sentinel.json` | Adapted: POST Notion API → POST GlitchTip ingest endpoint in Sentry event format (preserves HMAC verify, schema validation, Discord alerting, quarantine fallback fs) |
| `infra/itops/n8n-workflows/github-pr-merged-closer.json` | Adapted: PATCH Notion page → PATCH `/api/0/issues/{id}/` GlitchTip with `Bearer GLITCHTIP_API_TOKEN`. PR body matching pattern: `glitchtip-issue:(\d+)` replaces `notion-uuid:...` |
| `infra/itops/n8n-workflows/README.md` | Notion section removed, GlitchTip section added |
| `infra/itops/.env.example` | Added `GLITCHTIP_API_TOKEN`, `GLITCHTIP_DSN_N8N_WORKFLOWS`. Removed `RATIS_NOTION_INCIDENT_DBS` |
| `docs/arch/ARCH_agent_mcp.md` | Module 4 (Notion) → DEPRECATED + redirect to this ARCH |
| `docs/arch/ARCH_n8n_pipelines.md` | Notion refs replaced by GlitchTip refs + this ARCH |
| `docs/ops/SETUP_CHECKLIST.md` | Section 5.1 (Notion DB creation) removed |
| `docs/decisions/DECISIONS_ACTED.md` | New entry DA-N "Notion sunset → GlitchTip self-hosted (2026-05-31)" |

### Keychain cleanup

```sh
security delete-generic-password -s ratis-agent-mcp -a notion
```

### Guillaume's actions (Notion UI, outside code)

1. `notion.so/profile/integrations` → revoke the "ratis-agent-mcp" integration (and "ratis-hermes-poc-codex" created during POC)
2. Downgrade or cancel personal Notion workspace (if nothing is used anymore)

---

## IM-6 — Future doctrine (V1+) · ARCH_incident_management.md · PLANIFIÉ

> TL;DR: planned extensions post-V0 based on real volume and needs. **Not urgent**, to be scoped when the pain actually manifests. Expo source maps + perf monitoring + Hetzner scaling remain the 3 main axes.
> @tags: futur source-maps performance-monitoring scaling-hetzner v1-plus
> @status: PLANIFIÉ
> @subs: auto

| Extension | When to tackle |
|---|---|
| **Expo source maps** (debug minified stack traces in prod) | When we have a serious mobile crash that GlitchTip cannot demangle. Sentry CLI already works with GlitchTip for uploads. |
| **Performance monitoring (transactions)** | V1.5+, when volume justifies it. GlitchTip supports it but disabled by default. |
| **Mac mini → Hetzner / cloud migration** | When event volume explodes (>1000/day) OR production V1 is stable. Backup PG glitchtip + restore on new host. |
| **Sentry-self-hosted (full stack)** | If GlitchTip hits a ceiling on features we actually use (rare). At that point: 10 containers vs 5, ~3 GB RAM vs 600 MB. |
| **Intelligent alerts (PagerDuty-like)** | Probably not before V2 volume. Custom Telegram bot + cron is sufficient for V1. |
| **User feedback widget** | Optional — useful for mobile crash users if direct support is needed. |

---

## Out of scope

- **Notion → GlitchTip data migration**: the 2 historical incidents were throwaway (smoke tests). Guillaume's decision 2026-05-31. If Notion data is needed in the future, export via the `notion-export` skill (kept in `.claude/skills/`).
- **notion-export skill**: left as-is as a historical reference. To be removed later once we are certain nothing more needs to be exported from Notion.
- **Sentry SaaS (sentry.io)**: not considered. If V2+ volume is a problem, we switch instead to `sentry-self-hosted` Docker (10 containers, ~3 GB RAM) without changing the client SDK layer (DSN-compatible).
- **GlitchTip SSO / OAuth auth**: not enabled in V0 (1 admin Guillaume is sufficient, ENABLE_USER_REGISTRATION=False). To consider if there is a team.
- **PagerDuty / Opsgenie integration**: direct Telegram bot via cron + threshold is sufficient for V0/V1.

## Cross-references

- [[ARCH_n8n_pipelines]] § "batch-sentinel" and § "github-pr-merged-closer" — n8n workflows that POST to GlitchTip ingest (path 2)
- [[ARCH_agent_mcp]] Module 4 — DEPRECATED, replaced by this ARCH + `glt` wrapper
- [[ARCH_itops]] — Mac mini ops stack (n8n + healthchecks + GlitchTip coexist)
- [[ARCH_deployment]] — When Hetzner V1 migration happens, GlitchTip follows the same rail
- `DECISIONS_ACTED.md` § DA-N "Notion sunset → GlitchTip self-hosted (2026-05-31)"
- `DECISIONS_PENDING.md` § "Core doctrine: security through isolation" — justifies the pattern of 1 durable admin token + N write-only DSNs
- `~/glitchtip/docker-compose.yml` — live stack (to migrate to `infra/itops/glitchtip/` after stabilization)
- `~/glitchtip/bin/glt` — CLI wrapper (to migrate to `infra/itops/glitchtip/bin/` after stabilization)
