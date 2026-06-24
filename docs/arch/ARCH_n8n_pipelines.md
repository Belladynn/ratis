# ARCH n8n pipelines — multi-source incident orchestration

> 🛑 **PARTIAL SUNSET 2026-05-31** : the "Notion tickets" destination is **abandoned**. Notion is replaced by **GlitchTip self-hosted** (cf [`ARCH_incident_management.md`](ARCH_incident_management.md), LIVRÉ V0). The n8n rail remains valid for **non-SDK sources** (batch GH Actions outcomes, PR-merged closure), but with adapted destinations:
>
> - `sentry-ingest.json` → **deleted** (Sentry SDK clients point directly to the GlitchTip DSN — no n8n relay needed for this source).
> - `batch-sentinel.json` → POST to GlitchTip ingest endpoint in Sentry event format (preserves HMAC verify, schema validation, Discord alerting, quarantine fallback).
> - `github-pr-merged-closer.json` → PATCH `/api/0/issues/{id}/` GlitchTip (instead of PATCH Notion page). PR body pattern matching: `glitchtip-issue:(\d+)` replaces `notion-uuid:...`.
>
> All content below (Notion DB schema INCIDENTS, DA-2 through DA-10 referencing Notion, ASCII diagrams with Notion, etc.) is **V0 archaeology** kept for traceability. Read [`ARCH_incident_management.md`](ARCH_incident_management.md) first to understand the current architecture.

---

> **Status** : 🟡 **Partial sunset** — Notion sunset 2026-05-31 (DA-N), GlitchTip replacement. V0 sections below kept as history of the original design (validated brainstorming 2026-05-06).

> **Owner** : Guillaume (PO + orchestrator)
> **Host stack** : ARCH depends on [`ARCH_itops.md`](ARCH_itops.md) — n8n container lives in `infra/itops/docker-compose.yml` (Phase C ITOps).

---

## Index

- [Vision](#vision)
- [Components](#components)
- [Architecture & topology](#architecture--topology)
- [Key architecture decisions](#key-architecture-decisions)
- [Data flow](#data-flow)
- [Notion DB schema (INCIDENTS)](#notion-db-schema-incidents)
- [Failure handling](#failure-handling)
- [Implementation checklist](#implementation-checklist)
- [Things to know (vectorised FAQ)](#things-to-know-vectorised-faq)
- [Glossary](#glossary)

---

## Vision

n8n self-hosted on the Mac mini acts as the **central orchestrator** for Ratis incidents, transforming raw events (Sentry errors, support requests, batch fails) into **ready-to-consume Notion tickets for an autonomous agent** (Claude SA dispatched by Guillaume) without multi-dashboard navigation.

5 irreducible properties of the design:

1. **Convergent multi-source** : Sentry (V0), WhatsApp / Discord / Reddit / X / email (V1.5+), filesystem queue (V1.5+), Healthchecks / Uptime Kuma (V1.5+) — ALL write to the same Notion `INCIDENTS` DB with unified provenance tags.
2. **Single central sanitization gate** : HMAC verification + schema validation + (V1.5+) prompt-injection regex + amount sanity on the n8n side. No source can create a ticket without passing through this gate. Single point of hardening.
3. **Automatic enrichment towards agent-friendly format** : each ticket contains exhaustive context (logs, related PRs, similar past, breadcrumbs) so an LLM can fix without manual searching.
4. **Full issue-tracker lifecycle** : 5 Kanban states + reopen-on-regression + semi-auto closure via GitHub webhook + idempotency by fingerprint. Preserves history for institutional memory.
5. **Graceful degradation** : no event disappears silently. Dependencies down → ticket still created with explicit mention; Notion down → quarantine fallback to `~/.local/share/ratis/tickets-quarantine/`.

V0 prototypes the complete pattern on **Sentry only** for end-to-end validation before duplication per source.

---

## Components

| Component | Type | Location | Role |
|---|---|---|---|
| **n8n** | Docker Service | `infra/itops/docker-compose.yml` (n8nio/n8n latest), bind `127.0.0.1:5678`, persistent SQLite volume | Workflow engine + webhook receiver + Notion writer |
| **Tailscale Funnel** | Host-level config | `tailscale funnel 5678` on Mac mini | Exposes `https://<host>.<tailnet>.ts.net` → `localhost:5678`, TLS terminated at Tailscale, encrypted tunnel |
| **Loki** | Docker Service | `infra/itops/docker-compose.yml` (deployed PR #310) | Log source for enrichment window ±60s around the event |
| **n8n credentials store** | n8n internal | AES-256 encrypted with `N8N_ENCRYPTION_KEY` (env var Mac mini Keychain) | Stores GitHub / Notion / Sentry tokens consumed directly by n8n (not via agent-mcp for V0) |
| **Notion workspace** | SaaS | Guillaume's workspace, `INCIDENTS` DB (UUID in n8n env config) | Canonical ticket storage |
| **Sentry SaaS** | SaaS | sentry.io org `ratis`, projects `ratis-webservices` + `ratis-client` | V0 event source; alert rules configured on the Sentry UI side |
| **GitHub** | SaaS | repo `Belladynn/ratis`; webhook PR-merged | (a) source of last-PR-touching-file for enrichment; (b) ticket closure trigger via PR-merged webhook |
| **`report-batch-outcome` composite action** | GH Actions composite | `.github/actions/report-batch-outcome/action.yml` | Bash composite invoked as final step `if: always()` in the 10 `batch_*` workflows; signs HMAC-SHA256 + anti-replay timestamp; best-effort POST to n8n |
| **`batch-sentinel` workflow** | n8n | `infra/itops/n8n-workflows/batch-sentinel.json` | Webhook receiver for batch outcomes + 09h05 Europe/Paris digest cron; Phase 1 passive monitoring |
| **Quarantine filesystem** | Host directory | `~/.local/share/ratis/tickets-quarantine/` chmod 700 | Fallback when Notion is down; `.md` payloads for manual review |

---

## Architecture & topology

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                                Internet                                     │
│                                                                             │
│  ┌──────────────────┐                  ┌──────────────────┐                 │
│  │   Sentry SaaS    │                  │   GitHub SaaS    │                 │
│  │  (alert rules    │                  │   (PR-merged     │                 │
│  │   filtre E)      │                  │    webhook)      │                 │
│  └────────┬─────────┘                  └────────┬─────────┘                 │
│           │ POST                                │ POST                      │
│           │ HMAC-SHA256                         │ HMAC-SHA256               │
│           │ X-Sentry-Signature                  │ X-Hub-Signature-256       │
│           │                                     │                           │
└───────────┼─────────────────────────────────────┼───────────────────────────┘
            │                                     │
            ▼                                     ▼
   ┌────────────────────────────────────────────────────┐
   │           Tailscale Funnel edge                    │
   │  *.ts.net subdomain TLS-terminated by Tailscale    │
   │  Encrypted WireGuard tunnel to tailnet device      │
   └────────────────────────┬───────────────────────────┘
                            │
                            ▼ (intra-tailnet, encrypted)
┌───────────────────────────────────────────────────────────────────────────┐
│                          Mac mini (host)                                  │
│                                                                           │
│  Tailscale daemon → 127.0.0.1:5678 ┐                                      │
│                                    ▼                                      │
│  ┌──────────────────────────────────────────────────────────────────────┐ │
│  │              docker-compose-itops (network ratis_itops_net)          │ │
│  │                                                                      │ │
│  │  ┌──────────────────┐                                                │ │
│  │  │  n8n container   │                                                │ │
│  │  │   :5678          │                                                │ │
│  │  │                  │                                                │ │
│  │  │  Workflows :     │                                                │ │
│  │  │  - sentry-ingest │      ┌─────────────────┐                       │ │
│  │  │  - github-pr-    │ ◀────┤  Loki  :3100   │  (logs window)         │ │
│  │  │    merged-closer │      └─────────────────┘                       │ │
│  │  │  - daily-digest  │                                                │ │
│  │  │    (V0.5)        │      ┌──────────────────┐                      │ │
│  │  └────────┬─────────┘ ─────┤ Promtail tail    │ (n8n stdout → Loki)  │ │
│  │           │                │ Docker socket    │                      │ │
│  │           │                └──────────────────┘                      │ │
│  └───────────┼──────────────────────────────────────────────────────────┘ │
│              │                                                            │
│              │ outbound HTTPS                                             │
│              ▼                                                            │
└──────────────┼────────────────────────────────────────────────────────────┘
               │
               ▼
┌──────────────────────────────────────────────────────────────────────┐
│                         External APIs                                │
│                                                                      │
│   GitHub API       Sentry API       Notion API                       │
│   (last PR)        (similar past)   (create / update / lookup)       │
│                                                                      │
└──────────────────────────────────────────────────────────────────────┘
```

**Notes** :
- n8n is in the **same Docker network** as Loki → `http://loki:3100/loki/api/v1/query_range` is directly accessible (not localhost from the n8n container).
- Promtail tails the Docker socket → stdout/stderr of **all** containers (including n8n) goes to Loki with `service_name=<container>` automatically (config PR #310). **No n8n-specific config needed to have the logs.**
- Tailscale Funnel ≠ container; Tailscale daemon runs on the host Mac mini, routes port 443 of the `*.ts.net` subdomain to `localhost:5678`. Setup in 1 command: `tailscale funnel 5678`.

---

## Key architecture decisions

### DA-1 — Sentry SaaS (not GlitchTip self-host) for V0

**Context** : choice between Sentry SaaS, official Sentry self-host, and GlitchTip self-host.

**Decision** : Sentry SaaS. GlitchTip = V1.5 backup option if the SaaS bill becomes significant or if data sovereignty becomes critical.

**Why** :
- Ship fast (1 DSN parameter to leave as-is)
- Sentry SaaS free tier = 5k events/month, sufficient for current volume
- Official Sentry self-host = ~14 GB RAM minimum + 30 containers + 1 day/month admin → Mac mini saturation (already 24 GB shared between ratis dev stack + 16 runners + ITOps)
- GlitchTip = ~2 GB RAM, lightweight, Sentry SDK-compatible ingest API, **viable option** but: skip V0 for focus, reconsider at V1.5 if signal

**Consequence** : n8n V0 pipeline depends on Sentry SaaS availability. GlitchTip migration = change DSN in Ratis services + redeploy n8n alert rules logic; pipeline architecture strictly identical.

### DA-2 — Tailscale Funnel for webhook ingress

**Context** : Sentry SaaS must POST to a public FQDN that routes to the local Mac mini behind NAT.

**Decision** : Tailscale Funnel (`*.ts.net` → encrypted WireGuard → Mac mini → n8n :5678).

**Rejected alternatives** :
- **Cloudflare Tunnel** : acceptable but longer setup (CF account + domain config + tunnel daemon); not justified vs sufficient Tailscale free tier
- **Caddy + DDNS on ISP** : exposes residential IP directly, fragile router port forwarding, certs to manage → 2010-era architecture, skip
- **Polling Sentry API from n8n** : no ingress needed but 5-15 min latency, Sentry rate-limits, less clear regression detection → real-time webhook is superior

**Why Tailscale Funnel** :
- Generous free tier (~1-2 TB/month Funnel, vs ~5-50 KB per Sentry webhook)
- Auto TLS via Let's Encrypt managed by Tailscale
- Setup in 1 command (`tailscale funnel 5678`)
- Single-vendor risk mitigated: WireGuard config exportable, fallback to vanilla WG possible (never protocol lock-in)
- Mac mini already under Tailscale (probably)

**HMAC mandatory** : Tailscale terminates TLS on their side, so in theory they can decrypt the payload between edge and Mac mini. **HMAC SHA256 verification on the n8n side** = E2E integrity independent of Tailscale. Single effective mitigation.

### DA-3 — Webhook + Sentry alert rules (gating logic on Sentry side, not n8n side)

**Context** : where do we materialise the ticket creation policy (hybrid policy E)?

**Decision** : alert rules configured in Sentry UI; n8n receives already filtered.

**3 alert rules to create** :
1. **Rule fatal-prod** : `level:fatal AND environment:production` → webhook (1 event sufficient, 0 throttle)
2. **Rule error-frequency-prod** : `level:error AND environment:production`, frequency `≥3 events in 30 min` per fingerprint → webhook
3. **Rule regression** : Sentry built-in, `regression` action sent automatically on the webhook at the project level

**Explicitly excluded** : `warning`, `info`, `environment:staging|dev`. Zero tickets for these flows.

**Why Sentry-side gating** :
- Sentry is designed for this flow (alert rules are native, A/B-able in UI)
- Minimal traffic towards n8n (only real hits, not all events)
- You adjust "3 events / 30 min" from Sentry UI without touching n8n config
- Sentry naturally handles dedup-window (option "ignore if same fingerprint already alerted in last 1h")

**Consequence** : the gating logic is in 2 places (Sentry alert rules + n8n action routing). Acceptable because separation is clear: Sentry = "should this fire?", n8n = "what to do with the firing".

### DA-4 — n8n calls providers directly (not via agent-mcp for V0)

**Context** : n8n consumes GitHub / Notion / Sentry. Two options: (α) via agent-mcp HTTP server, (β) via n8n credentials store + direct HTTP nodes.

**Decision** : Option β (direct providers).

**Why** :
- Option α requires migrating agent-mcp to HTTP transport (anticipated DA-45 but not implemented V0)
- Option β = native n8n pattern, credentials AES-256 encrypted with `N8N_ENCRYPTION_KEY` stored outside config
- Tokens duplicated between agent-mcp Keychain + n8n credentials store = marginal attack surface (same Mac mini, same accessible Keychain)
- agent-mcp control plane remains coherent for Claude agents (human dispatching SAs); n8n has its own internal control plane, no conflict

**V1.5+ consequence** : option to migrate to α (agent-mcp HTTP) when a unified control plane is wanted for Claude agents + n8n + other consumers (cf DA-45 in `ARCH_agent_mcp.md`).

### DA-5 — Idempotency via External ID = sha256(fingerprint)

**Context** : natural Sentry retry if webhook doesn't receive 200 → risk of double-creating the ticket.

**Decision** : Notion `External ID` property = `sha256(fingerprint)` 16-char prefix. Lookup-before-create in all branches (new / reopen / increment).

**Central mechanism** :
```
Sentry webhook arrives → compute External ID
  → Notion query DB by External ID
    → No match    : create new (branch "new-ticket")
    → Match Done  : reopen (branch "reopen-ticket")
    → Match Open  : increment (branch "increment-occurrence")
```

**Why External ID = sha256(fingerprint)** rather than `sha256(fingerprint + timestamp)` :
- Sentry fingerprint = logical invariant of the bug (Sentry computes it from stack trace + error message)
- 1 fingerprint = 1 Notion page for life, which reopens or increments depending on the action
- This is exactly what we want for the regression mechanism: the same External ID matches the previously closed ticket

**Consequence** : routing by Sentry `action` (`created` / `regression` / `triggered`) becomes an **optimization hint** (skip lookup for `regression`) rather than strict branching. External ID lookup = single source of truth for deciding the branch.

### DA-6 — Reopen on regression preserves full history

**Context** : a closed Notion ticket (`Status=Done`) whose bug regresses — create a new ticket (history loss) or reopen (memory preserved)?

**Decision** : reopen the existing ticket. Status `Done` → `In Progress`, `Reopened Count`++, append comment "REGRESSION <ts>" + new context (fresh Loki window + last PR since the previous fix + new breadcrumbs).

**Why** :
- The agent picking up the regressed ticket sees the full history: previous fix, linked PR, resolution comment → **first reflex** = "diff between previous fix and HEAD = likely culprit"
- Without reopen mechanism, agent must independently rediscover via similar-past-issues query (enrichment ingredient #6) — works but not 100% guaranteed
- Classic issue tracker pattern (GitHub issues, Jira, Linear)

**Consequence** : the Notion body can grow indefinitely across regressions. If readability becomes an issue in V1, add a "## REGRESSION HISTORY (collapsed)" Notion toggle section to maintain readability while preserving data.

### DA-7 — 5-state Kanban + semi-auto closure via GitHub webhook

**Context** : what set of Kanban states for the INCIDENTS DB, and who transitions to `Done`?

**Decision** :
- 5 states: `Open` / `In Progress` / `In Review` / `Done` / `Won't Fix`
- Semi-auto closure: n8n workflow `github-pr-merged-closer` listens to GitHub PR-merged webhook, parses body for `closes #<notion-uuid>` (or `fixes` / `resolves`), PATCH Notion Status=`Done`

**Transitions** :
- `Open` ← n8n create
- `In Progress` ← Guillaume manually when picking (or Claude SA in the process of fixing)
- `In Review` ← Guillaume manually when PR opened (or auto in V1 if PR-opened webhook is wired)
- `Done` ← n8n auto via PR-merged webhook
- `Won't Fix` ← Guillaume manually (rejected bug)

**Why semi-auto not full-auto** :
- Auto-close on Sentry "issue resolved" = trap (Sentry auto-resolves if no event in 7d, may be premature)
- PR-merged webhook + body parsing is deterministic (the PR body explicitly contains the Notion reference)
- Guillaume remains in control for `In Review` and `Won't Fix` — these two states express human intent, not a system state

### DA-8 — Sanitization gate single point; quarantine fallback

**Context** : a convergent multi-source pipeline is a rich target for prompt-injection / fraudulent tickets. Where do we harden?

**Decision** : single point of hardening on the n8n side, before any Notion call. V0 pipeline = HMAC + schema validation. V1.5+ adds prompt-injection regex + amount sanity (€500 refund = quarantine).

**Quarantine fallback** : if Notion API is down (>3 exponential backoff retries fail), enriched Markdown payload is pushed to `~/.local/share/ratis/tickets-quarantine/` with YAML frontmatter metadata, for manual review by Guillaume.

**Why** :
- Prompt-injection is a real risk when extending to uncontrolled sources (WhatsApp, Discord). Architecting the gate from V0 = no design regret later.
- Controlled V0 sources (Sentry SaaS) have near-zero injection risk, but the presence of the gate is a future cross-source guarantee.
- No event disappears silently = non-negotiable principle. Fail-loud, never fail-silent.

### DA-9 — V0 without Grafana, V0.5 auto-recursive n8n daily digest

**Context** : observability on the pipeline itself — alert if n8n exec fails silently?

**V0 decision** : n8n emits 3 structured metrics on stdout (`webhook_received`, `ticket_created|reopened|incremented`, `latency_ms`). Promtail tails Docker socket → metrics in Loki. **No dashboard, no alerting**. Query Loki on demand via curl.

**V0.5 (delivered 2026-05-17)** : n8n `daily-digest` workflow (cron 9h every morning), queries the **n8n executions API** (`GET /api/v1/executions` + `/workflows`) — not Loki: the Loki source didn't cover actual execution failures. POST to Discord webhook. Scope = pipeline health (executions, failures, latency); ticket business activity is explicitly out of scope.

**V1+** : Grafana dashboard + alerting (when Grafana arrives in `infra/itops/`, ITOps Phase B-bis).

**Why no Grafana V0** : ~3-4h setup for useful Tier 2 + Tier 3 alerting, scope creep V0 when raw data is already in Loki for free. Grafana arrives with need (first dev who joins, B2B analytics, etc.).

### DA-10 — No full-autonomous Option B in V0 (consumer = human-dispatched Claude SA)

**Context** : "ready-to-consume ticket for autonomous agent processing" can mean 3 very different architectures.

**V0 decision** : semi-autonomous Option A — Guillaume reads Notion, dispatches a Claude SA reading the ticket as a brief, SA codes + opens PR, Guillaume merges.

**Deferred V1.5+** : fully-autonomous Option B — n8n picks `Status=ready` ticket, launches Claude Code headless with the ticket as initial prompt, monitors result, watches CI, auto-merges if green + severity < critical, otherwise assigns Guillaume with a report.

**Why A in V0** :
- Delivers the real delta value ("agent directly codes the fix without navigating 5 tools") **right now**
- Keeps merge control — regression risk under control
- Building V0 confidence over 2-3 months watching the quality of fixes done via enriched tickets = data-driven decision to move to B
- B adds a complete workstream (n8n process spawning, worktree management, gh CLI orchestration, auto-merge decision logic) that is not justifiable without prior A validation

---

## DA-11 — db-write-pipeline : DB write approval hub · #509/#517/#522/#524/#532 · LIVRÉ V1.1
> n8n `db-write-pipeline` workflow receives HMAC `db_propose_write` proposals (agent-mcp) → sandbox → dry-run → LLM review → human gate → prod execution. SP1+SP3+SP4+SP5+SP6 delivered V1 (backbone + 2-pass LLM + mirror UI gate `db_write_approvals`). V1.1 hardens via HSP-1 to HSP-5 (atom catalogue + DB floor + unforgeable gate + agent confinement + swap prep). Post-delivery cleanup: DA-14/15/16.
> @tags: db-write-pipeline n8n agent-mcp db_propose_write hub overview V1.1 hardening HSP audit
> @subs: auto

### Lineage V1 → V1.1

- **V1 (SP1+SP3+SP4+SP5+SP6)** : n8n backbone + disposable sandbox + 2-pass Anthropic LLM review + mirror UI gate `db_write_approvals`. JSON `infra/itops/n8n-workflows/db-write-pipeline.json`. `EXECUTE_ENABLED=false` (prod execution frozen).
- **V1.1 (HSP-1 to HSP-5)** : hardening post-audit `AUDIT_2026-05-19_db_write_pipeline.md` (21 findings of which 4 Critical). 7-layer confinement model: agent proposes ② atom catalogue ③ each procedure = atom ④ curated catalogue ⑤ pipeline measures ⑥ human gate ⑦ DB floor. Worst case remains ≤ 1 atom/call + ≤ 5k CAB/user + ≤ 50k/day, bounded to declared tables.
- **Post-V1.1 cleanup** : DA-14 (M1/M5/M7/L1/L3/L4), DA-15 (L2 Anthropic API bounds), DA-16 (M6 sandbox sec posture).

### Sub-project hub

| HSP | Scope | Findings addressed | Status |
|---|---|---|---|
| **HSP-1** | Atom catalogue (TOML manifest + `pglast` verifier + 3 atoms) | C1, C3, H6, M5 | LIVRÉ |
| **HSP-2** | DB floor (change-log + dormant caps) | F (economic caps) | LIVRÉ |
| **HSP-3** | Hardened human gate (M1-M5) | C4, H3, H2, M1 | LIVRÉ |
| **HSP-4** | Agent confinement (`agent_read` role + identity + schema + no-SQL + checksum + anti-exfil) | C1 runtime, C2, H1, H4, H5, H6, M3 | LIVRÉ |
| **HSP-5** | Swap prep bootstrap → locked (inventory + runbook) | M3 (capacity), posture | LIVRÉ prep |

`EXECUTE_ENABLED=true` flipped at HSP-4 delivery — the pipeline executes in prod, protected by checksum (HSP-4 M5) + caps (HSP-2). The **effective swap** (`db_query` → `agent_read` role via `AGENT_DATABASE_URL`, SSH key removal from agent) remains to be executed (HSP-5 prep documents only).

### Pointers

- V1.1 parent spec: `git show 73f8e5cb:docs/superpowers/specs/2026-05-19-db-write-pipeline-v1.1-hardening-design.md` (distilled — Batch B).
- Audit source: `AUDIT_2026-05-19_db_write_pipeline.md`.
- Canonical workflow JSON: `infra/itops/n8n-workflows/db-write-pipeline.json`.

---

## HSP-1 — atom catalogue (db-write-pipeline V1.1) · #534 · LIVRÉ V1.1
> TOML sidecar manifest per stored procedure (purpose, facing, direction, money_tier, args, affects). `pglast` verifier with 8 checks at merge time + defense in depth in `apply_procedure`. 3 initial atoms: `support_credit_cab`, `support_debit_cab`, `support_link_scan_to_user`. Guards: single-row, fixed direction, explicit money_tier, OUT integer rows_affected. Kills findings C1 (free-form runtime SQL) and C3 (auto-written invariants).
> @tags: db-pipeline catalogue manifeste TOML pglast verifier atome procedure-stockee support_credit_cab support_debit_cab support_link_scan_to_user single-row money_tier direction facing audit-V1 hardening C1 C3 H6 M5
> @subs: auto

### Key decisions
- **TOML sidecar manifest** per procedure (`db/procedures/<name>.manifest.toml`) — TOML read via `tomllib` Python 3.12 stdlib, human-reviewable format.
- **`pglast` verifier** (Python binding of `libpg_query` — the real Postgres parser). 8 checks at merge time: procedure name conforms, signature ≡ `[[args]]` manifest, tables touched ⊆ `[[affects]]`, no dynamic `EXECUTE`, `OUT rows_affected integer` present, `GET DIAGNOSTICS rows_affected = ROW_COUNT` present, `COMMENT ON PROCEDURE` present, manifest itself Pydantic-valid.
- **Procedure creation always human-gated** — git PR → CI verifier → operator review (manifest + verdict, never the PL/pgSQL line-by-line) → manual merge → migration. No auto-merge.
- **3 initial frozen atoms** (`cab` regime only — direct money requires HSP-3): `support_credit_cab` (1 ≤ amount ≤ 10000), `support_debit_cab`, `support_link_scan_to_user`.
- Structural guards: single-row per call, fixed direction (`credit` ≠ `debit` = two distinct procedures), facing vs internal, bulk = atom orchestration.

### Architecture
- Models: `ratis_core/ratis_core/db_procedure_manifest.py` (Pydantic v2 — `ProcedureManifest`, controlled vocabularies `direction`/`money_tier`/`op`).
- Verifier: `ratis_core/ratis_core/db_procedure_verifier.py` (parse `pglast` + manifest confrontation, 8 checks).
- Extended application: `ratis_core/ratis_core/db_procedures.py:apply_procedure` loads manifest + verifier before `op.execute(sql)`.
- Catalogue: `db/procedures/support_credit_cab.{sql,manifest.toml}` + `support_debit_cab.*` + `support_link_scan_to_user.*` + `_TEMPLATE.manifest.toml`.
- Migration: `alembic/versions/20260519_*_apply_initial_atoms.py` (3 atoms).
- CI: verifier job in `.github/workflows/doc-inventories.yml`.
- Index: `scripts/generate-procedures-catalogue.py` reads manifests → enriched `docs/arch/PROCEDURES.md` (purpose / facing / money_tier / affects).

### Tests
- ~19 verifier tests (`ratis_core/tests/test_db_procedure_verifier.py`).
- 8 manifest model tests (`test_db_procedure_manifest.py`).
- Extensions `test_db_procedures.py` (apply_procedure rejects missing manifest, rejects undeclared table).

### Tracking
- Spec: `git show 324cb7de:docs/superpowers/specs/2026-05-19-db-pipeline-hsp1-catalogue-design.md` (Batch B distilled).
- Plan: `git show 324cb7de:docs/superpowers/plans/2026-05-19-db-pipeline-hsp1-catalogue.md`.

---

## HSP-2 — DB floor (db-write-pipeline V1.1) · #535 · LIVRÉ V1.1
> Append-only `db_change_log` table populated by generic trigger on 6 sensitive tables (`to_jsonb(OLD)`/`to_jsonb(NEW)` tagged `submission_id`). Dormant temporal caps on `cabecoin_transactions` (double kill-switch session+settings, 20k/50k global + 5k per-user CAB/24h thresholds). Layer ⑦ of the V1.1 model — the DB structurally bounds the worst case, independent of pipeline/LLM/human/agent.
> @tags: db-pipeline plancher BDD change-log triggers caps dormants kill-switch app_settings cabecoin_transactions 20k 50k 5k submission_id append-only HSP2 audit V1.1 hardening
> @subs: auto

### Key decisions
- **`db_change_log` `FOR EACH ROW` granularity** — captures full `to_jsonb(OLD)`+`to_jsonb(NEW)`, append-only (2 guard triggers no-update/no-delete pattern `fn_pipeline_audit_log_no_update`).
- **PK extractable from `new_data->>'id'`** — no dedicated `row_pk` column (JSONB = source of truth).
- **Double kill-switch** — `current_setting('app.caps_enforced', true)` transaction-local + `app_settings.db_pipeline_caps.caps_enforced` global. Caps dormant until both are `true` — bootstrap intact.
- **Thresholds in `app_settings.db_pipeline_caps`** (JSONB) — trigger reads at each evaluation, modifiable without migration. Out of reach of the agent role (prepared by HSP-2, finalised HSP-4).
- **Tables protected by caps** : `cabecoin_transactions` only (proxy tier `cab`). Tier `direct` (`cashback_*`) → systematic human gate HSP-3.
- **Per-call checksum shifted to HSP-4** : HSP-2 delivers the capability (change-log keyed by `submission_id`), HSP-4 delivers the consumer (post-`CALL` read + manifest confrontation).

### Architecture
- Single migration `alembic/versions/20260520_*_apply_hsp2_floor.py`:
  - Table `db_change_log` (id uuid, submission_id uuid NULL, table_name, op, old_data/new_data jsonb, created_at) + 2 indexes + 2 no-update/no-delete guard triggers.
  - Function `fn_db_change_log_record()` attached AFTER INSERT/UPDATE/DELETE FOR EACH ROW on 6 tables: `user_cab_balance`, `cabecoin_transactions`, `cashback_transactions`, `cashback_withdrawals`, `subscriptions`, `scans`.
  - Function `fn_db_pipeline_caps_enforce()` attached BEFORE INSERT on `cabecoin_transactions` (direction='credit', 24h window, RAISE EXCEPTION 50k global / 5k user, RAISE WARNING 20k).
  - Seed `app_settings.db_pipeline_caps` (`caps_enforced=false`, initial values).
- Model: `ratis_core/ratis_core/models/db_change_log.py` (read-only, trigger inserts alone).

### Tests
- Generic change-log on 6 tables + `submission_id` propagation (`SET LOCAL`).
- Append-only enforcement (UPDATE/DELETE → exception).
- Caps dormant by default; active on double switch → block 50k/5k, warn 20k.
- Direction-aware (debit does not contribute).
- Clean migration UP+DOWN via `scripts/test_migrations.sh`.

### Tracking
- Spec: `git show 2fa1ff2f:docs/superpowers/specs/2026-05-20-db-pipeline-hsp2-floor-design.md` (Batch B distilled).
- Plan: `git show 2fa1ff2f:docs/superpowers/plans/2026-05-20-db-pipeline-hsp2-floor.md`.
- Effective cap activation (`caps_enforced=true`) = separate operator act (cf `PROD_CHECKLIST.md`).

---

## HSP-3 — hardened human gate (db-write-pipeline V1.1) · #539 · LIVRÉ V1.1
> 5 structural mechanisms against rubber-stamping and agent bypass. M1 challenge to type systematically (case+space insensitive, 3 attempts → 60s lockout, anti-paste `user-select:none`). M2 distinct HMAC secret `HUMAN_APPROVAL_SECRET` (argon2id, never in plaintext in DB/env/log). M3 deterministic French summary (n8n Code node, not LLM). M4 5 structural anomaly flags frozen at Register approval. M5 graduation `trust_level ∈ {manual, caps_only, frozen}`. Kills findings C4 + H3 structurally, mitigates H4/H2/M1.
> @tags: db-pipeline gate-humain challenge HMAC argon2id summary-fr anomaly-flags graduation trust-level HSP3 M1 M2 M3 M4 M5 C4 H3 audit V1.1 hardening admin-ui
> @subs: auto

### Key decisions
- **M1 challenge** = `<last 6 chars of procedure> <primary>` (primary depends on `direction`: raw `amount_cents` for credit/debit, `<entity>_id` for link/unlink, PK for fix/set). Normalised `strip().lower().replace(" ","")`. 3 attempts → 60s lockout, counter `payload.failed_confirms`.
- **M2 secret** = `HUMAN_APPROVAL_SECRET` 32 bytes urandom, argon2id hash stored in `app_settings.human_approval_secret_hash`. Lives in browser sessionStorage (cleared on close) + process RAM (loaded at login). **Never** in `.env`, MCP Keychain, Sentry logs, n8n. HMAC body+ts ±60s on each decision.
- **M2 reversed recovery** : n8n no longer *receives* "approved" via spoofable webhook — it *reads* `db_write_approvals.status` in the database and requires a `mac` computed with `N8N_RESUME_SECRET` (distinct, Mac mini env var). Knowing `resume_url` is no longer sufficient → kills H3 structurally.
- **M3 summary** computed by stateless endpoint `POST /api/v1/admin/db-pipeline/build-summary`, frozen `payload.summary_fr` at Register approval, rendered H1 pale yellow. French hardcoded templates by `direction × money_tier`. No LLM.
- **M4 5 V1 flags** : `first_use_of_procedure`, `amount_above_p95`, `user_repeat_in_24h`, `approaching_daily_cap`, `proposed_outside_business_hours`. Via endpoint `POST /api/v1/admin/db-pipeline/compute-flags`, frozen `payload.anomaly_flags`. No agent input.
- **M5 graduation** = `mode='graduation'` proposal that goes through the full pipeline (operator self-approves). Overrides `app_settings.db_pipeline_trust_levels` JSONB (key = procedure name). Tier `direct` refuses `caps_only` (hardcoded in `support_set_trust_level.sql`). Suggested criterion: N=20 consecutive runs without red flag + zero rejections in 90d.
- **No passkey** assumed out of scope (single operator). Deferred to `PROD_CHECKLIST.md § Security post-V1`.

### Architecture
- Backend modules: `webservices/ratis_product_analyser/admin_ui/human_secret.py` (new, M2), `db_approvals.py` (modified M1+M2), `auth.py` remains for SP6 cookie but now insufficient alone.
- Jinja2 templates: `db_approval_unlock.html` (new), `db_approval_detail.html` (rewrite M1+M3+M4).
- Scripts: `scripts/init-human-approval-secret.py`, `scripts/reset-human-approval-secret.py`.
- n8n workflow: `Compute summary FR` + `Compute anomaly flags` nodes before `Register approval`; `Build decision` extended (verify `mac` + re-read DB status); `trust_level` routing at start (caps_only short-circuits gate, frozen direct reject).
- Single migration `alembic/versions/20260521_1000_apply_hsp3_human_gate.py` (`mode` column + 3 `app_settings` seeds).
- Env var: `N8N_RESUME_SECRET` (R20 — `.env.example` + `conftest.py` + `require_env` PA).
- HSP-1 patch: `trust_level_initial` field added to TOML manifest.

### Tests
- M1: `approve_requires_challenge`, `case_insensitive`, `space_insensitive`, `3_attempts_then_lockout`, `value_per_direction`, `user-select:none` template.
- M2: `unlock_correct_secret_sets_cookie`, `approve_requires_valid_hmac`, `rejects_stale_ts`, `replay_blocked`, `secret_hash_argon2id_only_in_settings`, `n8n_resume_mac_required`.
- M3: `summary_fr_credit_cab`, `_debit_cab`, `_link`, `_no_llm_called`, `_pluralisation_1_cab`.
- M4: one test/flag with seeded data (`first_use`, `amount_above_p95`, `user_repeat_4_in_24h`, `approaching_cap_18k_today`, `outside_business_hours_3am`) + `flags_independent_of_payload_contents`, `flags_frozen_at_register_time`.
- M5: `trust_level_default_manual`, `caps_only_skips_gate`, `caps_only_refused_for_direct`, `frozen_rejects_immediately`, `graduation_proposal_full_pipeline`.
- Independent activation via feature flags `hsp3.m{1..5}_enabled` in `ratis_settings.json`.

### Tracking
- Spec: `git show 42f20e68:docs/superpowers/specs/2026-05-20-db-pipeline-hsp3-human-gate-design.md` (Batch B distilled).
- Plan: `git show 42f20e68:docs/superpowers/plans/2026-05-21-db-pipeline-hsp3-human-gate.md`.
- HSP-3.1 finishing touches (PR #547): doc + init-secret + caps flag + trust override.

---

## HSP-4 — agent confinement (db-write-pipeline V1.1) · #540 · LIVRÉ V1.1
> 7 mechanisms: REVOKE-restricted PG `agent_read` role (M1), `agent_id`+`proposed_at` HMAC identity re-verification (M2), runtime dynamic Pydantic schema validation (M3), no-SQL runtime `mode:new` removed (M4), per-call checksum via `db_change_log` post-`CALL` + `trust_level` freeze (M5), anti-exfil cap 1000 rows + 60/min throttle + payload ≤32 kB (M6), idempotent UP+DOWN migration (M7). `EXECUTE_ENABLED=true` flipped. Effective swap remains HSP-5.
> @tags: db-pipeline confinement-agent agent_read rôle-PG REVOKE identity-check schema-validation no-SQL-runtime checksum freeze trust-level anti-exfil HSP4 M1 M2 M3 M4 M5 M6 M7 C1 C2 H1 H4 H5 H6 M3 audit V1.1 hardening EXECUTE_ENABLED
> @subs: auto

### Key decisions
- **M1 PG `agent_read` role** : `NOINHERIT LOGIN`, `statement_timeout=5s`. Total REVOKE on 7 forbidden tables (`db_change_log`, `db_write_approvals`, `app_settings`, `admin_settings_audit`, `refresh_tokens`, `user_push_tokens`, `user_identities`). Column REVOKE on `users` (email, password_hash, support_id, ref_lat, ref_lng, password_changed_at), `subscriptions` (payment_ref, stripe_session_id, discount_*), `scans` (user_lat, user_lng). Column REVOKE ≫ SECURITY DEFINER view (alt rejected — duplication, breaks joins by name).
- **M2 identity** : `agent_id` + `proposed_at` UTC ISO-8601 auto-injected into the signed payload. Pipeline rejects 400 if regex KO, ±5 min window exceeded, or `agent_id ∉ manifest.allowed_callers`. Kills H5 (anti-replay by construction).
- **M3 schema** : dynamic Pydantic on the `ratis_core/ratis_core/db_procedure_args.py:validate_args` side + endpoint `POST /api/v1/admin/db-pipeline/validate-args`. Called by n8n after identity verify. Defense in depth also on the `apply_procedure` side.
- **M4 no-SQL runtime** : `mode:new`, `new_procedure_sql`, `checks`, `break_glass` removed from the MCP binary and from the n8n workflow. Procedure creation = git PR only (HSP-1 §4 flow unchanged).
- **M5 per-call checksum** : `SET LOCAL app.submission_id` before `CALL`, `SELECT FROM db_change_log GROUP BY table_name` after. Mismatch → freeze procedure (`apply-graduation` trust_level=frozen) + Discord alert. **V1.1 trade-off** : COMMIT precedes the checksum (constrained psql multi-stmt); freeze bounds the downstream risk. V1.2 → real atomic ROLLBACK via node-postgres.
- **M6 anti-exfil** : `MAX_ROWS=1000` + 60 req/min in-memory throttle (deque sliding window) on `db_query`, `db_propose_write` payload ≤ 32 kB. No ticket-zones (parked V2).
- **M7 migration** `20260521_1100_apply_hsp4_agent_confinement` idempotent UP+DOWN. Role password via env `AGENT_READ_PASSWORD` (Hetzner secrets in prod, Mac mini Keychain in dev).
- **`EXECUTE_ENABLED=true`** flipped at delivery — the pipeline now executes in prod, protected by checksum (M5) + HSP-2 caps.

### Architecture
- Single migration `alembic/versions/20260521_1100_apply_hsp4_agent_confinement.py` (CREATE ROLE + GRANT/REVOKE batch + GRANT EXECUTE on the 3 facing atoms).
- `ratis_core/ratis_core/db_procedure_args.py` (dynamic Pydantic validate_args from manifest).
- Field `allowed_callers: list[str]` (default `["claude-code-main"]`) added to HSP-1 manifest.
- `tools/agent-mcp/src/agent_mcp/tools/db_tools.py`: `db_propose_write` adds `agent_id` + `proposed_at` to HMAC payload; `db_query` capped 1000 rows + throttle.
- n8n workflow: `Validate identity` + `Validate args vs manifest` nodes inserted after `HMAC Verify`. `Execute` wraps `CALL` with `SET LOCAL app.submission_id` + `SET LOCAL app.caps_enforced='true'`; post-`CALL` checksum; freeze on mismatch.
- Env vars: `AGENT_DATABASE_URL` (documented HSP-4, swapped HSP-5), `AGENT_READ_PASSWORD` (Alembic env), `RATIS_AGENT_ID` (MCP binary, default `"claude-code-main"`).

### Tests
- M1: `tests/test_agent_role_permissions.py` — conn `agent_read`: `SELECT email FROM users` denied, `SELECT id FROM cabecoin_transactions` OK, `CALL support_credit_cab(...)` OK, `SELECT * FROM db_change_log` denied, `SELECT * FROM db_write_approvals` denied.
- M2: payload without `agent_id` / not allowlisted / `proposed_at` > 5 min → reject.
- M3: `amount=-1` reject, `amount=50000` reject (>max), type mismatch reject, required absent reject.
- M4: `db_propose_write(mode="new")` raises TypeError; POST n8n `mode:"new"` → 400.
- M5: e2e pipeline — `support_credit_cab` → COMMIT; buggy procedure UPDATE 2 rows → freeze observed in `app_settings.db_pipeline_trust_levels`.
- M6: 60 req OK, 61st reject; payload >32 kB reject on binary side + n8n side.
- M7: `scripts/test_migrations.sh` UP, DOWN, UP idempotent; verify `\dp+` post-migration.

### Tracking
- Spec: `git show c7365401:docs/superpowers/specs/2026-05-20-db-pipeline-hsp4-agent-confinement-design.md` (Batch B distilled).
- Plan: `git show c7365401:docs/superpowers/plans/2026-05-21-db-pipeline-hsp4-agent-confinement.md`.
- Effective swap (`db_query` → `AGENT_DATABASE_URL` via `agent_read` role) remains for actual HSP-5 (HSP-5 prep documents it).

---

## HSP-5 — swap prep bootstrap → locked · #536 · LIVRÉ prep
> Preparatory phase **with no irreversible act**. Inventory of the 11 surfaces where the agent borrows operator identity (SSH `ratis_hetzner_v3`, `gh` CLI, MCP Keychain, n8n admin UI, Tailscale Funnel, EAS, shell env `EXPO_TOKEN`, Hetzner `.env.prod`, `infra/itops/.env`, Anthropic API keys, GH Actions runners). Design of the scoped MCP role post-swap (`db_query` recommended transport: Tailscale + psycopg). 10-step swap runbook (backup `authorized_keys` → pre-flight checks → SSH agent key removal → verify n8n workflows → migrate `EXPO_TOKEN` Keychain → change n8n admin password → final e2e run → announcement). 8 open questions to settle before actual HSP-5.
> @tags: db-pipeline swap bootstrap-vs-verrouille HSP5 prep inventaire-acces SSH runbook MCP rôle-scopé Tailscale clé-prod open-questions runbook transition-graduelle audit V1.1
> @subs: auto

### Key decisions
- **Prep ≠ execution** : no key removed, no token rotated, no DB role created beyond HSP-4. HSP-5 prep produces the document only.
- **Inventory covers 11 surfaces** with one line per surface: current channel, who can do what today, post-swap target, swap act, verification status (✅ local command executed / 📋 to validate with Guillaume / 🚫 impossible without an act).
- **No scoped SSH `command=`** as an emergency door — KISS, MCP + n8n are sufficient for 100% of legitimate operations. Guillaume keeps **his** `ratis-prod` key (distinct) for human-only use.
- **`db_query` post-swap recommended transport** : Tailscale + psycopg direct (option 1). Alternatives: n8n proxy (option 2), restricted SSH `command=` (option 3). To be settled as an open question.
- **HSP-5 actual preconditions** : HSP-1+HSP-2+HSP-3+HSP-4 merged AND deployed, `EXECUTE_ENABLED=true` stable ≥1 week ≥5 proposals without incident, `caps_enforced=true` active, `agent_read` role delivered, 2nd operator key `guillaume_personal_v2` in Hetzner `authorized_keys`.

### Architecture (post-swap target)
- MCP surface **identical** (DA-44 ARCH_agent_mcp respected) — what changes: `db_query` loses half its visibility (scoped `agent_read` role HSP-4), out-of-band SSH no longer exists as a bypass.
- `ops` tools removed in passing: `notion_update_ticket_status` recommended moved to `admin` (audit whitelist DA-44).
- `MCP_AUTH_OPS_TOKEN` + `MCP_AUTH_ADMIN_TOKEN` rotation on the occasion of the swap (psychological moment).

### 10-step runbook (sequenced)
1. `[AUTO]` Backup `authorized_keys` on Hetzner side.
2. `[HUMAN]` Open parallel SSH session (safety net).
3. `[AUTO]` Pre-flight checks (db_query new transport, db_propose_write 1 CAB test, Sentry/GH/EAS via MCP, docker ps Mac mini).
4. `[HUMAN]` Remove `ratis_hetzner_v3` key from `~/.ssh/authorized_keys` on Hetzner (central act).
5. `[AUTO]` Verify n8n workflows (db-write-pipeline test, daily-digest, sentry-to-notion).
6. `[AUTO]` Verify Mac mini cron batches.
7. `[HUMAN]` Migrate `EXPO_TOKEN` out of `~/.zprofile` (already in Keychain).
8. `[HUMAN]` Change n8n admin password (Keychain `ratis-mac-mini::n8n-admin`, outside `ratis-agent-mcp`).
9. `[AUTO]` Final e2e run (1 CAB test proposal user_id=99999) — checksum must refuse reading `db_change_log`.
10. `[HUMAN]` Announce (update `SESSION_LOG.md`, `ARCH_agent_mcp.md`, `PROD_CHECKLIST.md`).

### 8 open questions to settle
1. `db_query` post-swap transport (Tailscale psycopg / n8n proxy / SSH `command=`).
2. Fine PII to REVOKE on `users` (beyond HSP-4 minimum).
3. `eas_update_preview` removed from `ops` scope?
4. Read-only n8n API token via MCP `n8n_get_execution(id)`?
5. `notion_update_ticket_status` removed from ops scope?
6. Remove Tailscale CLI from agent PATH (sudo only).
7. Rotation of the 2 MCP tokens at the time of the swap?
8. Healthchecks `.env` itops → Keychain?

### Tracking
- Spec: `git show 828b8f02:docs/superpowers/specs/2026-05-20-db-pipeline-hsp5-prep-design.md` (Batch B distilled).
- No implementation plan — HSP-5 prep = document only; actual HSP-5 to be specified as a separate execution plan.

---

## DA-12 — batch-sentinel : push monitoring composite action → n8n webhook · #551 · LIVRÉ V1.0
> Phase 1 passive monitoring. The 10 GH Actions batch workflows (off_sync, achievements, consensus, purge, savings, referral_payout, annual_reset, osm_bulk_sync, vrac_seed, origins_backfill) **push** their final outcome to n8n via shared composite action `report-batch-outcome` (HMAC-SHA256 + anti-replay timestamp ±300s). n8n applies the `sentry-ingest` pattern (Notion lookup by External ID `batch:<workflow_name>` → new/reopen/increment branch → red Discord alert + quarantine fallback). Cron 09h05 Europe/Paris posts a 24h Discord digest aggregated from the workflow's own static data.
> @tags: batch-sentinel n8n push-monitoring composite-action HMAC anti-replay Notion External-ID dedupe Discord digest GH-Actions continue-on-error Phase-1 sentry-ingest-pattern
> @subs: auto

### Key decisions
- **DA-1 push GH Actions → n8n webhook** (composite action `report-batch-outcome` invoked as final step `if: always()`). Push is idiomatic (the workflow knows its exact result), zero latency, robust to `cancelled` (ambiguous on the GH API side). Poll discarded (3-4 extra nodes, latency, `cancelled` lost).
- **DA-2 Notion dedup by External ID `batch:<workflow_name>`** — a batch that fails 3 nights = 1 ticket with Occurrence=3, not 3 tickets. Direct reuse of the `sentry-ingest` pattern. `batch:` prefix namespaces vs `sentry:<hex16>`. Signature-based deferred to Phase 2+.
- **DA-3 all `batch_*.yml` report**, including one-shots — when a one-shot is launched manually, a failure deserves a ticket. Filtering by default introduces a list to maintain.
- **DA-4 `continue-on-error: true`** — the sentinel is an observer, not a dependency. If n8n goes down, workflows don't cascade to red.
- **DA-5 HMAC-SHA256 hex** (header `X-Signature-256`) + **anti-replay timestamp ±5 min** (header `X-Timestamp`). Consistency with `sentry-ingest`, `github-pr-merged-closer`, `db-write-pipeline`.
- **DA-6 digest = new cron node 09h05** in `batch-sentinel.json` (not an extension of `daily-digest.json`). Daily-digest reads n8n API (n8n health) ≠ batch-sentinel reads static data (GH Actions batch health). SRP, post Discord 09h00 (n8n) then 09h05 (batches).

### Architecture
- Composite action: `.github/actions/report-batch-outcome/action.yml` (bash, HMAC sign, best-effort POST).
- n8n workflow: `infra/itops/n8n-workflows/batch-sentinel.json` (webhook `/webhook/batch-outcome` + cron 09h05 Europe/Paris).
- Secrets: `BATCH_SENTINEL_WEBHOOK_SECRET` (GH Actions), `N8N_BATCH_SENTINEL_WEBHOOK_SECRET` (n8n) — same value. URL: `BATCH_SENTINEL_WEBHOOK_URL` GH Actions var points to `https://<host>.<tailnet>.ts.net/webhook/batch-outcome`.
- 10 workflows instrumented Phase 1. 3 not instrumented: `batch_reconciliation.yml`, `batch_data_reconciliation.yml` (exec jobs commented "until prod DB is wired"), `batch_push_receipts.yml` (no active exec job).

### Phase 2 deferred
When the prod DB is in place: (a) status quo + sentinel, or (b) n8n becomes scheduler via `workflow_dispatch` GitHub API.

### Tracking
- Spec: `git show 0edd0896:docs/superpowers/specs/2026-05-22-n8n-batch-sentinel-design.md` (Batch B distilled).
- Plan: `git show 0edd0896:docs/superpowers/plans/2026-05-22-n8n-batch-sentinel.md`.

---

## DA-14 — audit V1 cleanup (M1/M5/M7/L1/L3/L4) · #541 · LIVRÉ V1.1
> Cleanup post HSP-1→HSP-4 of 6 findings from audit `AUDIT_2026-05-19_db_write_pipeline.md`. 3 immediate fixes (M1 `Money-table scan` was querying non-existent `ratis_sandbox` → `ratis_prod`; M5 non-deterministic `args` order → `argOrder = manifest.args.map(a => a.name)`; L3 `_llm_unavailable` silent fail-open → explicit catch). 3 new corrections (M7 regex validation of `_sandbox_id`/container name before shell interpolation; L1 hardcoded `EXECUTE_ENABLED` → `$env.DB_PIPELINE_EXECUTE_ENABLED === 'true'` fail-safe; L4 `redact_args` truncates `client_message`+`investigation` to 100 chars). 3 M/L DROPPED (M2/M3/M4 auto-resolved by HSP1-4).
> @tags: audit cleanup M1 M5 M7 L1 L3 L4 ratis_sandbox argOrder sqlQuote llm_unavailable EXECUTE_ENABLED redact_args sandbox-id-validation defense-depth
> @subs: auto

### Findings addressed
- **M1** Money-table scan `-d ratis_sandbox` (non-existent → fail-safe catch forced `_touches_money_tables=true`, permanent red badge) → `-d ratis_prod`.
- **M5** `Dry-run + invariants` was using `Object.values(p.args).map(...)` (non-deterministic JS order → args potentially inverted). Aligned with `Execute (HSP4 checksum)` pattern: `argOrder = manifest.args.map(a => a.name)` + `sqlQuote`.
- **M7** Regex validation of `_sandbox_id` (`^[0-9]+-[0-9]+$`) + container name (`^ratis-sandbox-[0-9]+-[0-9]+$`) **before** shell interpolation in `Dry-run + invariants` and `Money-table scan`. Invalid → `_outcome: "rejected_sandbox_id_invalid"`, short-circuit.
- **L1** Hardcoded `EXECUTE_ENABLED` `true` → `$env.DB_PIPELINE_EXECUTE_ENABLED === 'true'` (strict string match, default `false` fail-safe). Documented in `.env.example` + `infra/itops/docker-compose.yml`.
- **L3** `Build decision` was reading `_llm_unavailable` behind an empty `try{}catch(e){}` (silent fail-open on security signal). Replaced by explicit catch forcing `_llm_unavailable=true` + `_llm_read_error="<message>"`.
- **L4** `redact_args` (`tools/agent-mcp/src/agent_mcp/audit.py`) truncates `client_message`+`investigation` to 100 chars + `...[truncated N more chars]`. Full payload preserved in `db_write_approvals.payload` for human review. 5 new TDD tests.

### Findings DROPPED (auto-resolved)
- **M2** break-glass branch — HSP-4 M4 completely removed `IF break-glass`.
- **M3** `db_query` full read — HSP-4 M1 delivered the capability (`agent_read` role + REVOKE). Effective swap = HSP-5.
- **M4** payload size limit — HSP-4 M6 cap 32 kB + `checks`/`new_procedure_sql` removed.

### Tests
263 tests passed (agent-mcp suite), 15/15 audit tests (10 old + 5 new L4 TDD).

---

## DA-15 — audit L2 cleanup : Anthropic API bounds (defense in depth) · #543 · LIVRÉ V1.1
> Closes finding L2 (unbounded Anthropic API cost) via 3 defense-in-depth layers against unbounded API consumption (2 LLM calls per proposal on the n8n side). Layer 1 agent-mcp in-memory throttle `db_propose_write` 10 req/min sliding window (deque separate from `db_query` 60/min). Layer 2 n8n workflow node `Rate limit webhook` (sliding window 20/min via `$workflow.staticData`) + IF + Respond 429, inserted between `IF HMAC OK [true]` and `Validate identity`. Layer 3 PROD_CHECKLIST entry monthly spend cap ~50€ on n8n Anthropic API key.
> @tags: audit L2 anthropic-api cost-unbounded rate-limit throttle defense-in-depth deque sliding-window 10-req-min 20-req-min PROD_CHECKLIST monthly-cap
> @subs: auto

### Layers
- **Layer 1** (agent-mcp) : in-memory throttle `db_propose_write` 10 req/min, sliding window, deque separate from `db_query` (60 req/min HSP-4 M6). Pattern copied from HSP-4 M6, tighter bound since proposals are rare by nature.
- **Layer 2** (n8n workflow) : `Rate limit webhook` Code node (sliding window 20/min via `$workflow.staticData`) + IF + Respond 429. Inserted between `IF HMAC OK [true]` and `Validate identity`. Cuts before any Anthropic LLM call if > 20 proposals/min globally.
- **Layer 3** (PROD_CHECKLIST) : entry to set a monthly spend cap (~50 €) on the Anthropic API key used by n8n, on the console side. Last economic safeguard.

### Tests
266 passed (31 HSP-4 M6 preserved + 3 new L2). Ruff clean. Workflow JSON parses OK.

---

## DA-16 — audit M6 quick wins : sandbox sec posture · #545 · LIVRÉ V1.1
> Quick wins M6 sandbox (encryption at-rest + anonymisation on restore remain V2 pre-third-party-user opening). QW1 `chmod 700` on `~/.local/share/ratis/db-sandbox/` + `snapshots/` + `chmod 600` on `.sql.gz`. QW2 dedicated isolated Docker network per sandbox (`ratis_sandbox_isolated_<id>`) created by `sandbox-up.sh`, deleted by `sandbox-down.sh`, no `-p` port mapping (accessible only via `docker exec`). QW3 snapshot retention 7d → 24h via `find -mmin +1440`, tunable `SNAPSHOT_MAX_AGE_MINUTES`. QW4 dedicated section in `PRIVACY.md` (Mac mini as processing location) + M6 V2 entry in `PROD_CHECKLIST.md`.
> @tags: audit M6 sandbox sec-posture chmod-700 docker-isolated-network port-mapping retention 24h SNAPSHOT_MAX_AGE_MINUTES PRIVACY-update PROD_CHECKLIST quick-wins
> @subs: auto

### Quick wins
- **QW1** `chmod 700` on `~/.local/share/ratis/db-sandbox/` + `snapshots/` (idempotent). `chmod 600` on each `.sql.gz`.
- **QW2** Isolated Docker network `ratis_sandbox_isolated_<sandbox_id>` created by `sandbox-up.sh`, deleted by `sandbox-down.sh`. No `-p` port mapping → accessible only via `docker exec`. `sandbox-reap.sh` purges orphaned networks.
- **QW3** Snapshot retention 7d → 24h via `find -mmin +1440`. Tunable `SNAPSHOT_MAX_AGE_MINUTES`. `SNAPSHOT_KEEP` removed.
- **QW4** Dedicated section in `PRIVACY.md` establishing Mac mini as processing location + M6 V2 entry in `PROD_CHECKLIST.md` § Security + n8n-workflows README updated.

### Files touched
`scripts/db-sandbox/_common.sh`, `snapshot.sh`, `sandbox-up.sh`, `sandbox-down.sh`, `sandbox-reap.sh`, `PRIVACY.md`, `PROD_CHECKLIST.md`, `infra/itops/n8n-workflows/README.md`.

### V2 deferred (before opening to third-party users)
- Encryption at-rest for snapshots (Keychain passphrase).
- Anonymisation of `email`/`password_hash`/`phone` on restore (`pg_anonymizer` extension or pre-restore SQL script).

---

## DA-13 — Inventory rework : pipe-separated convention for long-lived docs · #555 · LIVRÉ V1.0
> End-to-end demo of the new `## <ID> — <title> · <refs> · <STATUS>` convention indexed in `ARCH_INVENTORY.md` (Batch A inventory-rework). All long-lived ARCHs will migrate progressively (Batch B).
> @tags: inventory convention doc-rework arch-inventory grep-friendly batch-A
> @subs: auto

### Context

The existing index (`ARCH_INVENTORY.md` old version) rendered each ARCH as a multi-line block (title + sections in `·`-separated form). Human-readable but unusable for grep: no way to filter `LIVRÉ`, `EN-COURS`, or a specific tag without loading the entire file. For an agent that needs to jump across 60+ docs without saturating its context, this forces a full-read — anti-pattern R29.

### Decision

1. **Convention in source** : each major H2 section in a long-lived doc (`ARCH_*.md`, `KNOWN_PROBLEMS.md`, `DECISIONS_ACTED.md`) follows `## <ID> — <title> · <refs> · <STATUS>` with a quote-block `> TL;DR` + `> @tags: …` + `> @subs: auto`.
2. **Derived index** : `scripts/generate-arch-inventory.py` regenerates `ARCH_INVENTORY.md` in pipe-separated format `ID | STATUS | file:line | tags | TL;DR` — 1 line per entity, grep-friendly, self-contained.
3. **Progressive migration** : for 2 sprints, non-migrated files appear as `LEGACY` entries (1 per file). `scripts/check-arch-convention.sh` emits warnings without blocking (pedagogical phase).
4. **Agent discipline** : rule R41 in CLAUDE.md — NEVER full-read the inventory, always use targeted `Grep`.

### Consequences

- `ARCH_INVENTORY.md` goes from verbose markdown format to compact pipe-separated text (~6 KB for 60 entries vs ~20+ KB old format).
- CI `doc-inventories.yml` gains a warn-only `check-arch-convention` job (Batch A → systematic exit 0).
- This `## DA-13` section is itself the end-to-end demo: grep `DA-13` in `ARCH_INVENTORY.md` must return exactly 1 line pointing to `ARCH_n8n_pipelines.md:<line>`.

### Pointers

- Spec to produce if Batch B requires refinement.
- Migration backlog: see Batch B (next issue).

---

## Data flow

Summary: **8 steps** for `sentry-ingest` (webhook → HMAC → schema → routing by External ID lookup → parallel enrichment → Notion create/update → structured log → respond), **4 steps** for `github-pr-merged-closer` (webhook → HMAC → filter merged=true → parse body + PATCH Notion).

---

## Notion DB schema (INCIDENTS)

→ Detailed in the spec doc § Notion DB schema. 13 properties (Title, Status, Source, Severity, Sentry Fingerprint, External ID, First Seen, Last Seen, Occurrence Count, Reopened Count, Sentry Issue URL, Related PR, Tags) + structured Markdown body with 6 enriched sections (TL;DR, Stack trace, Breadcrumbs, User context, Loki window, Last PR, Similar past Sentry).

---

## Failure handling

| Step | Failure | Behaviour |
|---|---|---|
| HMAC verification | invalid sig | Drop 401, log `hmac_invalid`, no Notion write |
| Schema validation | malformed payload | Drop 400, log `schema_invalid`, no Notion write |
| Loki query | timeout / 5xx | Continue pipeline, body: `[Loki unavailable at <ts>]` |
| GitHub last-PR query | timeout / 5xx | Continue, body: `[GitHub last PR lookup failed]` |
| Sentry similar query | timeout / 5xx | Continue, body: `[Sentry similar query failed]` |
| Notion create / update | 5xx | Retry 3× exponential (1s/4s/16s); final failure → push Markdown payload to `~/.local/share/ratis/tickets-quarantine/` + log `notion_unavailable` |

**Principle** : no Sentry event disappears. Enrichments degrade gracefully; ticket creation guaranteed via quarantine fallback.

---

## Implementation checklist

> Detailed plan will be produced by skill `writing-plans`. Estimate: 3-4 SA chunks, ~2-3 days total wall-time.

### Chunk 1 — n8n container + Tailscale Funnel + bootstrap
- [ ] Add service `n8n` to `infra/itops/docker-compose.yml` (image `n8nio/n8n:1.x`, persistent SQLite volume, bind `127.0.0.1:5678`, in `ratis_itops_net`)
- [ ] `.env.example`: `N8N_ENCRYPTION_KEY`, `N8N_BASIC_AUTH_USER`, `N8N_BASIC_AUTH_PASSWORD`, `N8N_HOST=mac-mini.<tailnet>.ts.net`, `N8N_PROTOCOL=https`
- [ ] `tailscale funnel 5678` on host (command to document in runbook, not in Docker config)
- [ ] Smoke: `curl -fsS https://<host>.<tailnet>.ts.net/healthz` → 200 OK
- [ ] Update `infra/itops/n8n-workflows/README.md` (ops cookbook) with setup steps + runbook; brief mention in `infra/itops/README.md` § n8n

### Chunk 2 — `sentry-ingest` workflow
- [ ] Webhook node on `/webhook/sentry-incoming`
- [ ] HMAC verification Code node (`N8N_SENTRY_WEBHOOK_SECRET` env)
- [ ] Schema validation Code node (required fields)
- [ ] External ID computation Code node (`sha256(fingerprint).slice(0,16)`)
- [ ] Notion lookup node (filter by External ID)
- [ ] Switch node: new / reopen / increment branches
- [ ] Parallel HTTP nodes: Loki query, GitHub last-PR, Sentry similar
- [ ] Markdown body format Code node
- [ ] Notion create node (new branch) or update node (reopen / increment)
- [ ] Structured log emit Code node (3 metrics)
- [ ] Respond 200 node
- [ ] Quarantine fallback: Filesystem write node if Notion final 5xx
- [ ] Workflow JSON exported, committed to `infra/itops/n8n-workflows/sentry-ingest.json`

### Chunk 3 — `github-pr-merged-closer` workflow
- [ ] Webhook node on `/webhook/github-pr-merged`
- [ ] HMAC verification Code node (`N8N_GITHUB_WEBHOOK_SECRET` env)
- [ ] Filter node: action="closed" + merged=true
- [ ] Code node parse PR body for `closes #<uuid>` patterns
- [ ] Foreach matched UUID: Notion update Status=`Done` + comment
- [ ] Workflow JSON exported, committed to `infra/itops/n8n-workflows/github-pr-merged-closer.json`

### Chunk 4 — Sentry alert rules + GitHub webhook config + Notion DB + smoke tests
- [ ] Notion: create `INCIDENTS` DB with the 13 specified properties
- [ ] Notion: extract DB UUID, set in n8n env
- [ ] Sentry UI: create 3 alert rules (fatal-prod, error-frequency-prod, regression built-in) pointing to `https://<host>.<tailnet>.ts.net/webhook/sentry-incoming`
- [ ] Sentry UI: configure shared webhook secret (set on Sentry project settings side, and `N8N_SENTRY_WEBHOOK_SECRET` on n8n side)
- [ ] GitHub: org-level webhook PR events → `https://<host>.<tailnet>.ts.net/webhook/github-pr-merged`, shared `N8N_GITHUB_WEBHOOK_SECRET`
- [ ] Smoke 1: trigger a Sentry test event → Notion ticket created in <5s
- [ ] Smoke 2: trigger Sentry "issue regression" → existing ticket reopens + comment added
- [ ] Smoke 3: merge a PR with `closes #<uuid>` in body → Notion ticket `Status=Done`
- [ ] Smoke 4: forge a POST without HMAC → drop 401 + log
- [ ] Smoke 5: Notion API down (override with invalid URL) → quarantine `.md` created

### Chunk 5 (V0.5, deferred 1-2 weeks)
- [x] `daily-digest` workflow (cron 9h, query n8n executions API 24h, POST Discord webhook) — done 2026-05-17
- [x] `batch-sentinel` workflow (Phase 1 monitoring: composite action + Notion ticket per workflow_name + 09h05 digest) — done 2026-05-27; 10/13 batch workflows instrumented (3 without exec job pending)

> **Operational runbook** (first deployment, Tailscale Funnel verify/kill, live logs, quarantine review, webhook secret rotation) → see [`infra/itops/n8n-workflows/README.md`](../../infra/itops/n8n-workflows/README.md).

---

## Things to know (vectorised FAQ)

### Why n8n and not a custom Python script?

n8n provides (a) a visual workflow UI easy to modify without touching code, (b) a native encrypted credentials system, (c) a retry framework + queryable execution history, (d) hundreds of pre-built nodes (HTTP, Notion, GitHub, Sentry) that work out-of-the-box. A custom Python script costs ~3× more time for equivalent results and loses the clickable UI for in-prod adjustments.

### Why Sentry SaaS and not GlitchTip self-host from V0?

Mac mini RAM cost (~2 GB for GlitchTip vs ~14 GB for official Sentry). GlitchTip is viable but adds another ITOps service, not justified in V0. GlitchTip migration = just change the DSN, pipeline architecture strictly identical. Cf DA-1.

### Why Tailscale and not Cloudflare Tunnel?

Faster setup (3 commands), sufficient free tier, Mac mini probably already under Tailscale. Cloudflare Tunnel = valid V1.5 option if a custom domain name is wanted (`n8n.ratis.app` instead of `*.ts.net`). Cf DA-2.

### What happens if Sentry sends a webhook with a corrupted HMAC?

Immediate 401 drop on the n8n side, log `hmac_invalid`, no Notion ticket created. This is the E2E defence against Tailscale potentially MITMing the payload (theoretical but possible). HMAC verification on the n8n side = mandatory non-negotiable. Cf DA-2 + DA-8.

### What happens if Notion is down when a Sentry event arrives?

Retry 3× exponential backoff (1s, 4s, 16s). If still failing, enriched Markdown payload is pushed to `~/.local/share/ratis/tickets-quarantine/` with YAML frontmatter metadata. Guillaume reviews manually when the directory grows. No Sentry event disappears silently. Cf DA-8.

### How does the autonomous agent consume a ticket?

V0 = Guillaume reads Notion, copies the ticket URL or content, dispatches a Claude SA passing the ticket as a brief. The ticket is sufficiently enriched (logs, related PR, similar past) so the SA doesn't need to navigate Sentry/GitHub/Loki manually. The SA fixes + opens a PR + Guillaume merges. Cf DA-10.

### Why no Grafana dashboard from V0?

YAGNI calibrated. The 3 structured metrics emitted by n8n go automatically to Loki via Promtail (already deployed PR #310). You can query them on demand via `curl loki:3100`. Auto-recursive n8n daily digest (V0.5, ~30 min of work) covers 80% of the "alert if pipeline breaks" need. Grafana arrives with need (first dev who joins, B2B analytics starts, etc.). Cf DA-9.

### How to scale to other sources (WhatsApp, Discord, etc.)?

Duplication pattern: create one n8n workflow per source (`whatsapp-ingest`, `discord-ingest`, etc.) that converges towards the same Notion INCIDENTS DB via the same External ID mechanism + Kanban lifecycle. The delta = on the ingestion side (source-specific payload parsing) + stricter sanitization gate (prompt-injection regex mandatory for uncontrolled sources). The **pipeline pattern is invariant**, which is what justifies the V0 solid architecture investment.

### Does the GitHub PR-merged webhook risk missing a PR?

Yes if GitHub has a brief outage + retries exhausted. V1 mitigation: a nightly batch workflow that scans PRs merged in the last 24h and matches against open `Status=In Review` tickets to catch up. V0 accepts this marginal gap since PR/merge volume is low (~5-10/day peak) and Guillaume regularly views Notion.

### Can we exceed the Tailscale Funnel free tier?

Free tier ~1-2 TB/month bandwidth. Typical Sentry webhook = 5-50 KB. Current volume = a few events/day, 2-3 year projection at 100× scaling. **At 0.001% of the limit**. If we reach it, we have 10 million events/month → we have other problems, and Tailscale Pro tier = $6/user/month. Non-issue at foreseeable horizon.

---

## Glossary

| Term | Definition |
|---|---|
| **n8n** | Self-hosted workflow automation platform (Node.js based). Open-source, fair-code license. Clickable UI + JSON-serializable workflows + encrypted credentials. Site: n8n.io |
| **Tailscale Funnel** | Tailscale feature that exposes a public `*.ts.net` subdomain TLS-terminated, routed encrypted to a tailnet device. Sufficient free tier. |
| **HMAC-SHA256** | Hash-based Message Authentication Code. Verifies the integrity + authenticity of a payload via shared secret. Not encryption — it is a crypto signature. |
| **Sentry alert rule** | Configuration on the Sentry UI side that describes "when to fire a webhook" based on conditions (level, environment, frequency, etc.). Native, adjustable without code. |
| **External ID** | Notion property = `sha256(fingerprint)` 16-char prefix. Idempotency key that matches 1 Sentry fingerprint to 1 Notion page for life. |
| **Sentry Fingerprint** | Hash computed by Sentry from stack trace + error message that identifies an "issue" (logical invariant bug). 100 events of the same bug = 1 fingerprint. |
| **Sentry Action** | Webhook field: `created` (1st event) / `triggered` (subsequent event) / `regression` (resolved → unresolved). Hint for n8n routing. |
| **Quarantine** | Directory `~/.local/share/ratis/tickets-quarantine/` where n8n pushes payloads when Notion API is down. Manual review by Guillaume. |
| **Graceful degradation** | Pattern where a downstream failure (Loki, GitHub, Sentry similar) does not block the pipeline — the ticket is created with an explicit mention of the miss. No event disappears. |
| **Reopen on regression** | Mechanism: closed `Done` ticket whose fingerprint regresses → status goes back to `In Progress` + comment added with new context. Preserves history. |
| **DA-N** | Numbered Architecture Decision. Each DA documents a trade-off + its rationale for long-term traceability. Ratis convention (cf `ARCH_example.md`). |
