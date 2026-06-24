---
# Identity
type: cross-cutting
status: in-progress

# Navigation (Obsidian + RAG)
parent: ARCH_RATIS
related: [ARCH_deployment, ARCH_llm_observability, ARCH_agent_mcp, ARCH_incident_management, ARCH_anti_fraud]

# Technical
tech: [AWS, Terraform, ECS Fargate, RDS, ElastiCache, Secrets Manager, FastAPI, Celery, PostgreSQL, Redis]
tables: []
env_vars: []

# Business
tags: [well-architected, aws, security, reliability, cost, operational-excellence, sustainability, performance]
business_domain: infra
rgpd_concern: true

# Freshness (MANDATORY — R41)
updated: 2026-06-24
---

# Ratis — AWS Well-Architected self-assessment

> A six-pillar self-assessment of Ratis against the AWS Well-Architected Framework (Operational Excellence, Security, Reliability, Performance Efficiency, Cost Optimization, Sustainability). Each pillar cites concrete Ratis choices and is honest about gaps. This is a design-review artifact, not a compliance certificate: the system is a V0/V1 platform with a documented path to the AWS topology in [[ARCH_deployment]].
> @tags: well-architected aws six-pillars security reliability cost operational-excellence sustainability performance review
> @status: EN-COURS
> @subs: auto

> Parent: [[ARCH_RATIS]] · related: [[ARCH_deployment]] (topology & migration) · [[ARCH_llm_observability]] (LLM tracing) · [[ARCH_agent_mcp]] (agentic control plane) · [[ARCH_incident_management]] (incident pipeline).

## Index

- [WA-1 Operational Excellence](#wa-1)
- [WA-2 Security](#wa-2)
- [WA-3 Reliability](#wa-3)
- [WA-4 Performance Efficiency](#wa-4)
- [WA-5 Cost Optimization](#wa-5)
- [WA-6 Sustainability](#wa-6)
- [WA-7 Pillar trade-offs · what we deliberately did NOT optimise](#wa-7)

---

## WA-1 — Operational Excellence · [[ARCH_incident_management]] · EN-COURS

> TL;DR Operations are designed in, not bolted on: request-ID correlation across services, error ingestion into an incident pipeline, LLM tracing on the OCR call, and a bespoke agentic ops layer (Hermes + n8n) that turns monitoring into action. This is the pillar AWS says you must never trade away — Ratis treats it as first-class.
> @tags: operational-excellence observability requestid sentry glitchtip langfuse n8n hermes ci
> @subs: auto

- **Request correlation** — `RequestIDMiddleware` in `ratis_core` stamps every request across all five services, so a single trace is followable end-to-end.
- **Error tracking → incident workflow** — Sentry/GlitchTip DSN per service (no-op if empty), ingested into a Notion/INCIDENTS pipeline via n8n (`sentry-ingest`), with a `batch-sentinel` workflow monitoring the 10 GitHub-Actions batch crons. See [[ARCH_incident_management]] and [[ARCH_n8n_pipelines]].
- **LLM observability** — Langfuse traces the PA OCR→LLM extraction call (`claude-haiku-4-5`), with a documented tracing → offline-eval → A/B roadmap. See [[ARCH_llm_observability]].
- **Agentic ops** — the Hermes ops agent (Telegram bot, native crons, postmortem→reviewer→skill pipeline) and the n8n orchestrator make routine incident handling and digests automatable. The agent-mcp control plane exposes typed ops tools (deploy, restart, secret rotation) behind an HMAC-chained audit log.
- **Reproducibility & CI** — `docker compose up` brings the whole stack up; ~28 CI workflows run pytest + lint + doc-inventory freshness, so operational drift is caught at PR time.
- **Gap (honest)** — no formal SLOs/SLIs or alert-routing tiers yet; alerting is event-driven (GlitchTip → Telegram) rather than threshold/burn-rate based. Runbooks exist for migrations and deploy but not for every failure mode.

## WA-2 — Security · [[ARCH_agent_mcp]] · EN-COURS

> TL;DR Defence in depth around identity, secrets, and the agent itself: RS256 with a single issuer to isolate blast radius, secrets resolved at runtime via Secrets Manager `valueFrom` (never plaintext in task definitions), a Keychain-backed just-in-time vault so the LLM never holds a raw secret, a CI secret-scan gate, and an inflexible GDPR data-minimisation posture.
> @tags: security rs256 single-issuer secrets-manager valuefrom vault jit detect-secrets gdpr least-privilege
> @subs: auto

- **Identity / blast-radius isolation** — RS256 JWT where `ratis_auth` is the **sole issuer** (holds the private key); PA/LO/RW only verify with the public key, `aud=ratis`. A compromised verifier service cannot mint tokens — a deliberate improvement over the earlier shared-secret HS256 design.
- **Secrets at rest & in transit** — on AWS, secrets are stored in Secrets Manager and injected via ECS `secrets → valueFrom` (resolved at container start, never written into the task definition); the execution-role grant is least-privilege and scoped at the cluster root (`infra/aws/cluster.tf`), not inside the reusable module.
- **Agentic secret handling** — a just-in-time secrets vault (Module 10 of agent-mcp) leases and auto-revokes tokens; the LLM operates through a typed MCP control plane and never receives a raw credential. Privileged actions are recorded in an HMAC-chained, append-only audit log.
- **Supply-chain / leak prevention** — a `security.yml` CI workflow runs detect-secrets / gitleaks-class scanning; the portfolio gate additionally requires the final working tree to contain zero secrets. `.env.*` files are never committed (R17).
- **Data minimisation (GDPR as security)** — no names/first-names extracted from receipts, geolocation never logged, receipt images deleted ≤ 48h, `DELETE /account` anonymises in place. Less stored PII = smaller breach surface.
- **Gap (honest)** — no WAF / rate-based ALB rules in the AWS target yet (rate-limiting is app-level slowapi on AU); no automated dependency-vulnerability gate (e.g. `pip-audit`) wired into CI; secret rotation is JIT for agent tokens but manual for provider live keys (Cat C in the vault).

## WA-3 — Reliability · [[ARCH_deployment]] · EN-COURS

> TL;DR Reliability rests on per-service isolation, idempotency at the money boundary, an outbox-style async delivery model with batch reconciliation, and managed-service durability in the AWS target. The honest gap is single-host today and a shared database that is a single point of failure.
> @tags: reliability idempotency outbox reconciliation health-checks isolation rds backups
> @subs: auto

- **Fault isolation** — five independently deployable services; a crash in PA (OCR) does not take down AU (login) or RW (rewards). NT is internal-only, shrinking the externally-reachable surface.
- **Idempotency at the money boundary** — `gift_card_orders` is `UNIQUE(source_type, source_ref_id)`, so a retried claim cannot double-issue; balance debits are atomic conditional `UPDATE`s.
- **Async + reconciliation** — fire-and-forget delivery via Celery + a transactional-outbox posture means a transient downstream failure is retried/reconciled (the `reconciliation` batch closes the loop on pending cashback) rather than lost.
- **Health & recovery** — services expose `/health`; the AWS target uses ALB target-group health checks and ECS task replacement. RDS provides automated backups/PITR; the repo ships a `db-snapshot` n8n workflow for the current host.
- **Migrations as a controlled step** — Alembic migrations run via a dedicated run-once `migrations` service (`docker compose --profile migrate run --rm migrations`), with a documented prod runbook.
- **Gap (honest)** — current production is **single-host** (Mac-mini): no multi-AZ, `desired_count = 1` per Fargate task in the target (no horizontal redundancy yet), and the **single shared Postgres is a SPOF**. No chaos/failover testing; recovery objectives (RTO/RPO) are implicit, not stated.

## WA-4 — Performance Efficiency · [[ARCH_PRODUCT_ANALYSER]] · EN-COURS

> TL;DR Performance comes from keeping the hot path off heavy work (async OCR/routing), caching and materialising reads, and pre-building expensive assets (OSRM France PBF, OCR models lazy-loaded). The data-driver choices (psycopg v3, int-cents) favour throughput.
> @tags: performance celery redis-cache materialized-balances osrm-prebuilt psycopg-v3 lazy-import
> @subs: auto

- **Heavy work off the request path** — OCR (PA) and route optimisation (LO) run in Celery workers, so p95 API latency is decoupled from multi-second jobs.
- **Caching & materialised reads** — Redis caches OFF/product enrichment; `user_cab_balance` / `user_cashback_balance` are materialised tables so balance reads are O(1) instead of aggregating the transaction ledger.
- **Pre-built expensive assets** — OSRM runs MLD with a pre-built France PBF (≈10× faster than CH for multi-waypoint queries; rebuilt ~once/year); PaddleOCR models are lazy-imported so the web process starts fast and only the worker pays the ~200–300 MB cold start.
- **Efficient data layer** — psycopg v3 (native async, modern PG types) and int-cents arithmetic (integer aggregation, no float work) keep the data path lean.
- **Local store resolution** — store matching uses a local `pg_trgm` matcher on `stores` rather than a live Overpass call per scan, removing an external round-trip from the OCR pipeline.
- **Gap (honest)** — no load/throughput benchmarks published; no autoscaling policy defined on the Fargate target (fixed `desired_count`); no DB read replica yet, so read-heavy growth funnels onto the primary.

## WA-5 — Cost Optimization · [[ARCH_deployment]] · EN-COURS

> TL;DR Cost is managed by staging the hosting (cheap Hetzner → owned Mac-mini → AWS only when justified), running an ephemeral/right-sized AWS POC, self-hosting routing and observability instead of paying SaaS per call, and choosing a cheap-but-capable OCR LLM (`claude-haiku-4-5`).
> @tags: cost staged-hosting fargate-right-sizing self-hosted-osrm self-hosted-langfuse ocr-off-in-cloud ephemeral
> @subs: auto

- **Staged hosting** — Hetzner → Mac-mini → AWS, each step triggered by a documented saturation signal rather than provisioned up front; the Mac-mini doubles as host and CI runner fleet, avoiding duplicate spend.
- **Right-sized / ephemeral cloud** — `infra/aws` provisions `desired_count = 1` Fargate tasks via a single reusable `modules/service` (×5), and the POC is designed to be torn down (`terraform destroy`) so it isn't billed when idle.
- **Self-host over per-call SaaS** — OSRM (routing) and Langfuse (LLM tracing) are self-hosted, replacing per-request Directions-API and managed-tracing costs; OCR is run in-house rather than via a paid document-AI API.
- **OCR off in the cloud POC** — PaddleOCR/paddlepaddle is excluded from the Fargate profile (no aarch64-friendly wheel under the cost cap), keeping task images small and avoiding GPU/large-memory tiers.
- **Cheap-but-sufficient LLM** — the OCR→LLM extraction uses Anthropic's Haiku tier, the cheapest model that meets the extraction quality bar, swappable via `LLM_MODEL` env without code change.
- **Gap (honest)** — no cost monitoring/budgets/alarms wired (no AWS Budgets, no Cost Explorer tagging strategy beyond `default_tags`); no Savings Plans / Fargate Spot evaluation; cost-per-scan is not yet measured.

## WA-6 — Sustainability · EN-COURS

> TL;DR Sustainability is mostly a by-product of the cost choices: efficient arm64 hardware, no idle cloud capacity, lazy-loaded heavy models, and pre-computed assets that avoid repeated work. Honestly, it is the least formally-addressed pillar.
> @tags: sustainability arm64 efficiency lazy-load no-idle precompute carbon
> @subs: auto

- **Efficient hardware** — the dev/V0 host is an arm64 Mac-mini M4 Pro (high performance-per-watt); the AWS target runs Fargate on Graviton-class economics where applicable.
- **No idle capacity** — `desired_count = 1` and an ephemeral, tear-down-able POC mean compute isn't burning while unused; the staged-hosting model avoids over-provisioning.
- **Avoid repeated work** — OSRM France PBF is pre-built (not recomputed per request), OCR models are lazy-loaded once per worker, and materialised balances avoid re-aggregating ledgers on every read — all reduce wasted cycles.
- **Data minimisation** — GDPR-driven 48h image deletion and never-storing PII also shrink the storage/energy footprint.
- **Gap (honest)** — no carbon measurement (no Customer Carbon Footprint Tool review), no region selection on carbon-intensity grounds (eu-west-3 chosen for latency/data-residency), and no managed-service-migration goal framed explicitly as a sustainability lever.

## WA-7 — Pillar trade-offs · what we deliberately did NOT optimise · EN-COURS

> TL;DR Where pillars compete, Ratis privileges Security and Operational Excellence (never traded away), accepts a Reliability gap (single-host, single DB) in exchange for Cost, and accepts eventual consistency in exchange for perceived-latency/availability.
> @tags: trade-offs atam security-first cost-vs-reliability consistency-vs-availability
> @subs: auto

- **Cost ⟶ over Reliability (today)** — the platform deliberately runs single-host with a shared Postgres at V0/V1; multi-AZ/replica redundancy is planned but not paid for until the documented capacity signals fire ([[ARCH_deployment]]). This is the largest accepted risk.
- **Availability/perceived-latency ⟶ over strict Consistency** — fire-and-forget means a reward or notification is eventually consistent (reconciled by batch), not transactionally immediate. Accepted because the user-visible action must never block.
- **Security & Operational Excellence — not traded** — per AWS guidance these are held firm: RS256 single-issuer, runtime secret injection, JIT vault, audit logging, request correlation, and incident pipelines are kept even where a cheaper/looser option exists.
- **Simplicity ⟶ over premature scale** — no message broker, no service mesh, no autoscaling yet (YAGNI); the design records the exact thresholds at which each is revisited rather than building them speculatively.
