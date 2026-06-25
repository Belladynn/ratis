# Ratis

**A production-grade cashback + real-time grocery-price + gamification platform — five FastAPI microservices, an Expo / React Native app, Terraform/AWS IaC, and a bespoke agentic-AI ops layer (typed MCP control plane, hybrid-RAG doc search, just-in-time secrets vault).** Built and operated solo, end to end.

<!-- Badge slug `Belladynn/ratis` is the assumed public repository slug — confirm at publish time. -->
[![CI](https://img.shields.io/github/actions/workflow/status/Belladynn/ratis/ci.yml?branch=main&label=CI)](https://github.com/Belladynn/ratis/actions)
[![License: Apache-2.0](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)
[![Python 3.12](https://img.shields.io/badge/python-3.12-blue.svg)](.python-version)
[![uv](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/uv/main/assets/badge/v0.json)](https://github.com/astral-sh/uv)
[![Last commit](https://img.shields.io/github/last-commit/Belladynn/ratis)](https://github.com/Belladynn/ratis/commits/main)
[![codecov](https://img.shields.io/codecov/c/github/Belladynn/ratis)](https://codecov.io/gh/Belladynn/ratis)

> Ratis helps budget-conscious shoppers (families, students) get each item on their list at the lowest nearby price along an optimised multi-store route. Users feed the dataset frictionlessly — snap a receipt, scan an electronic shelf label, or scan a barcode — and earn in-app rewards **only** for actions that enrich the data. The result is a virtuous loop: play → richer data → sharper prices → bigger savings → retention.

---

## Demo

<!-- TODO: <10s demo GIF of scan→price→reward, exported as docs/assets/demo.gif and embedded here. -->

Real screens from the Expo / React Native client (V0 locale is French):

| Dashboard | Product prices | Optimised route |
|:---:|:---:|:---:|
| ![Dashboard — battle pass, balances](docs/assets/app-dashboard.png) | ![Product — real-time price consensus across 7 stores](docs/assets/app-product-prices.png) | ![List — multi-store route with per-stop savings](docs/assets/app-route-optimised.png) |
| Gamification surface (battle pass, CAB + cashback balances) | Real-time per-`(store × product)` price consensus, cheapest-first | Multi-store shopping route with per-stop savings |

---

## Table of contents

- [Why this exists](#why-this-exists)
- [Architecture](#architecture)
- [The agentic AI layer](#the-agentic-ai-layer)
- [Built with](#built-with)
- [Quickstart](#quickstart)
- [Project structure](#project-structure)
- [Services](#services)
- [Deployment](#deployment)
- [Testing & CI](#testing--ci)
- [Documentation](#documentation)
- [License](#license)

---

## Why this exists

Ratis is a solo-built but production-shaped system: five independently deployable microservices behind a shared RS256 JWT, a mobile client, PostgreSQL + Redis + Celery, a container-portable infrastructure, and an agentic layer that lets an AI agent run ops without ever touching a cleartext secret. The decisions below are the ones a reviewer should read for judgment, not just code — each links to its in-repo Architecture Decision Record (MADR).

- **RS256 JWT issued by one service, verified by four — blast-radius isolation.** Only `ratis_auth` holds the RSA private key and mints tokens; `product_analyser`, `list_optimiser`, and `rewards` hold only the public key and verify locally, so the auth service is not a hot-path single point of failure. Verification is centralised in `ratis_core/jwt.py` so no service can drift on audience/expiry checks. This superseded an earlier HS256 shared-secret design (a single leaked `.env` could forge any user's token). → [ADR-0001](docs/adr/0001-rs256-jwt-single-issuer.md)
- **OAuth-only delegated auth (Apple + Google); zero stored passwords.** Email/password was a dead V0 remnant and an account-takeover vector, so it was decommissioned rather than patched: identities resolve strictly by `(provider, provider_id)`, the `UNIQUE(email)` constraint was dropped, and email was demoted to an informational contact snapshot. No password attack surface, no email infrastructure to build. → [ADR-0002](docs/adr/0002-oauth-only-delegated-auth.md)
- **Integer cents end-to-end; OCR conversion via `Decimal`.** Every monetary amount is an integer number of cents across DB, API, and settings — never float for an amount (rates stay `NUMERIC` as the documented exception). OCR-extracted prices convert with `int(round(Decimal(str(v)) * 100))`, never `int(float * 100)`, and a CI grep guard keeps the rule self-enforcing. → [ADR-0003](docs/adr/0003-integer-cents-money.md)
- **Transactional outbox for fire-and-forget side effects.** Ratis is fire-and-forget by doctrine — every user action triggers async work. Notifications are enqueued into a `notification_outbox` table in the **same transaction** as the triggering event; an in-process worker drains it with `SELECT … FOR UPDATE SKIP LOCKED`. At-least-once delivery survives a crash, with zero user-facing latency and no extra broker infrastructure. → [ADR-0006](docs/adr/0006-transactional-outbox-notifications.md)
- **`agent-mcp`: a Keychain-backed MCP control plane so the model never sees raw secrets.** Ops live behind a typed Model Context Protocol server (GlitchTip, EAS, GitHub, Stripe, R2, DB, docs-RAG, secrets-vault). Tokens live in the macOS Keychain; the server reads one at call time, performs the call, and writes an append-only HMAC-chained audit line. The agent only ever sees functional results. → [ADR-0011](docs/adr/0011-agent-mcp-keychain-control-plane.md)

Full decision log: [`docs/adr/`](docs/adr/) (14 records) · canonical register: [`docs/decisions/DECISIONS_ACTED.md`](docs/decisions/DECISIONS_ACTED.md) · architecture index: [`docs/reference/ARCH_INVENTORY.md`](docs/reference/ARCH_INVENTORY.md).

---

## Architecture

C4 **Container** view — the Expo client reaches five FastAPI services through Caddy; each carries a Bearer RS256 JWT (`aud=ratis`). To respect diagram readability (≤8 nodes / ≤6 relationships), the container picture is split into **(a) the request path** and **(b) the shared data plane**. The full C4 set (System Context, Container, Deployment) plus the agentic-layer view lives in [`docs/arch/ARCH_RATIS.md`](docs/arch/ARCH_RATIS.md).

**(a) Request-path containers**

```mermaid
flowchart TB
    app["📱 Expo Client<br/>[React Native]"]
    caddy["Caddy 2<br/>reverse proxy · TLS (ACME)"]

    subgraph SVC [FastAPI services — independently deployable]
        au["AU · ratis_auth :8001<br/>OAuth → JWT, Stripe, accounts"]
        pa["PA · ratis_product_analyser :8003<br/>scan, OCR, price consensus"]
        lo["LO · ratis_list_optimiser :8002<br/>lists, route optimisation"]
        rw["RW · ratis_rewards :8004<br/>CAB, battle pass, cashback, gift cards"]
        nt["NT · ratis_notifier :8005<br/>internal-only · Expo Push"]
    end

    app -->|"HTTPS"| caddy
    caddy -->|"path-routed, Bearer JWT"| au
    caddy -->|"path-routed, Bearer JWT"| pa
    caddy -->|"path-routed, Bearer JWT"| lo
    caddy -->|"path-routed, Bearer JWT"| rw
    pa -.->|"INTERNAL_API_KEY"| nt

    classDef client fill:#1168bd,stroke:#0b4884,color:#fff
    classDef infra fill:#438dd5,stroke:#2e6295,color:#fff
    classDef svc fill:#85bbf0,stroke:#5d82a8,color:#000
    class app client
    class caddy infra
    class au,pa,lo,rw,nt svc
```

**(b) Shared data plane + workers + AWS target**

```mermaid
flowchart TB
    all["All 5 services<br/>(AU · PA · LO · RW · NT)"]
    paw["PA worker (OCR)"]
    low["LO worker (routing)"]

    pg[("PostgreSQL 16 + PostGIS<br/>shared DB · models in ratis_core")]
    redis[("Redis 7<br/>Celery broker · cache · rate-limit")]
    osrm["OSRM 5.27<br/>routing (France PBF)"]
    r2["Cloudflare R2<br/>receipt images (48h TTL)"]
    aws["AWS target — Terraform IaC<br/>ECS Fargate · ALB · RDS · ElastiCache · Secrets Manager"]

    all -->|"read / write"| pg
    paw <-->|"broker"| redis
    low <-->|"broker"| redis
    paw -->|"upload"| r2
    low -->|"matrix / route"| osrm
    all -.->|"provisioned by (modules/service ×5)"| aws

    classDef svc fill:#85bbf0,stroke:#5d82a8,color:#000
    classDef store fill:#438dd5,stroke:#2e6295,color:#fff
    classDef cloud fill:#08427b,stroke:#052e56,color:#fff
    class all,paw,low svc
    class pg,redis,osrm,r2 store
    class aws cloud
```

**Notes that matter.** This is a *distributed monolith*, not fully decoupled microservices — models live in `ratis_core` and every service reads/writes one shared Postgres; the boundaries buy independent scaling and deployment, not data isolation. Celery workers run PA (OCR) and LO (routing) tasks off the web process so the API never blocks (the fire-and-forget red line). Every container except the Expo client is provisioned by `infra/aws` (one reusable `modules/service` instantiated 5×, RDS + ElastiCache, an ALB replacing Caddy in the cloud target).

---

## The agentic AI layer

The lead differentiator: a typed, Keychain-backed **MCP server** is the single control plane through which an LLM operates Ratis without ever holding a raw secret or writing to production directly. Tokens are leased just-in-time, production DB writes pass a 7-layer confinement pipeline, and every privileged action lands in an HMAC-chained append-only audit log.

```mermaid
flowchart TB
    llm(["🤖 Claude (agent)"])
    mcp["agent-mcp<br/>typed MCP control plane<br/>Keychain-backed · HMAC audit log"]

    subgraph CAP [Capabilities behind the control plane]
        docs["docs-mcp<br/>hybrid RAG (bge-m3 + sqlite-vec)"]
        vault["secrets vault<br/>JIT lease + revoke"]
        n8n["n8n db-write pipeline<br/>7-layer confinement"]
    end

    hermes["Hermes ops agent<br/>Telegram · crons · postmortems"]
    lf["Langfuse<br/>tracing on PA OCR→LLM"]

    llm -->|"typed tool calls"| mcp
    mcp --> docs
    mcp --> vault
    mcp -->|"propose write"| n8n
    llm -->|"MCP server"| hermes
    hermes -.->|"alerts / digests"| llm
    mcp -.->|"OCR call traced by"| lf

    classDef agent fill:#08427b,stroke:#052e56,color:#fff
    classDef plane fill:#1168bd,stroke:#0b4884,color:#fff
    classDef cap fill:#85bbf0,stroke:#5d82a8,color:#000
    class llm agent
    class mcp,hermes,lf plane
    class docs,vault,n8n cap
```

| Capability | What it does | Record |
|------------|--------------|--------|
| **MCP control plane** (`agent-mcp`) | Typed tool modules (GlitchTip, EAS, GitHub, Stripe, R2, DB) the agent calls without seeing tokens; per-call HMAC-chained audit. | [ADR-0011](docs/adr/0011-agent-mcp-keychain-control-plane.md) |
| **JIT secrets vault** | Leases a secret at call time and auto-revokes it; the model only ever handles non-secret handles. | [ADR-0011](docs/adr/0011-agent-mcp-keychain-control-plane.md) |
| **Agent → DB write pipeline** | Production DB writes pass a 7-layer confinement pipeline orchestrated in n8n (fail-safe disabled by default). | [ADR-0012](docs/adr/0012-db-write-pipeline-7-layer-confinement.md) |
| **Hybrid-RAG doc search** (`docs-mcp`) | `bge-m3` dense + keyword retrieval over the ARCH docs via `sqlite-vec`, behind a typed MCP surface. | [ADR-0013](docs/adr/0013-agentic-doc-rag-bge-m3-sqlite-vec.md) |
| **LLM observability** | Self-hosted Langfuse traces the OCR→Claude extraction call, GDPR-hard (UUID-only input, output capture off). | [ADR-0014](docs/adr/0014-langfuse-llm-observability.md) |

Deep-dive: [`tools/agent-mcp/README.md`](tools/agent-mcp/README.md) · [`docs/arch/ARCH_agent_mcp.md`](docs/arch/ARCH_agent_mcp.md) · [`docs/arch/ARCH_n8n_pipelines.md`](docs/arch/ARCH_n8n_pipelines.md) · [`docs/arch/ARCH_llm_observability.md`](docs/arch/ARCH_llm_observability.md).

---

## Built with

| Layer | Technologies |
|-------|--------------|
| **Backend** | FastAPI · Pydantic v2 · SQLAlchemy 2.0 · `psycopg[binary]` v3 (`postgresql+psycopg://`) · Celery · async lifespan |
| **Data** | PostgreSQL 16 + PostGIS · Redis 7 · Cloudflare R2 (object storage) · Alembic (migrations) |
| **AI / ML** | PaddleOCR (FR receipt OCR) · Claude / Anthropic (LLM field extraction) · Langfuse (self-hosted LLM tracing) · hybrid RAG (`bge-m3` + `sqlite-vec`) |
| **Infra / DevOps** | Docker (multi-arch amd64 + arm64) · docker-compose · Caddy 2 · Terraform → AWS (ECS Fargate · ALB · RDS · ElastiCache · Secrets Manager) · GitHub Actions |
| **Mobile** | Expo SDK 54 · React Native · expo-router · React Query · i18next · EAS build / OTA |
| **Agentic** | Custom MCP (Model Context Protocol) server · docs-RAG · JIT secrets vault · n8n confinement pipeline · prompt-injection defence |
| **Tooling** | `uv` workspace (single committed lockfile) · Ruff · Bandit · mypy (strict — no `disable_error_code`) · pytest · Jest · Codecov |

---

## Quickstart

**Prerequisites:** Python 3.12 (pinned in `.python-version` — PaddleOCR ships no 3.13+ wheel), [`uv`](https://docs.astral.sh/uv/), Docker + Compose v2.

```bash
git clone https://github.com/Belladynn/ratis.git && cd ratis
docker compose up -d                                                  # data plane: PostgreSQL 16 + PostGIS, Redis 7
uv sync --frozen                                                      # reproducible workspace (all services + shared lib)
export DATABASE_URL="postgresql+psycopg://ratis:ratis@localhost:5432/ratis_dev"
docker compose --profile migrate run --rm migrations                 # alembic upgrade head (idempotent)
( cd webservices/ratis_auth && uv run --package ratis_auth uvicorn main:app --port 8001 )  # run the auth service
curl -sf http://localhost:8001/health                                # → 200, {"status":"ok","service":"ratis_auth"}
```

Run a package's tests from its directory, e.g. `cd webservices/ratis_auth && uv run pytest`. OSRM (route optimisation) is not part of the local data-plane compose — start it separately only if you exercise `ratis_list_optimiser`.

---

## Project structure

```text
ratis/
├── webservices/             5 deployable FastAPI services (each: routes/ services/ repositories/ tests/ ARCH_*.md)
│   ├── ratis_auth/              :8001  OAuth + JWT issuer, accounts, Stripe webhooks
│   ├── ratis_product_analyser/  :8003  scan / OCR pipeline (Celery worker, R2, PaddleOCR)
│   ├── ratis_list_optimiser/    :8002  shopping lists + OSRM route optimisation
│   ├── ratis_rewards/           :8004  cabecoin, cashback, gamification, gift cards
│   ├── ratis_notifier/          :8005  Expo push (internal-only)
│   └── ratis_migrations/               dedicated Alembic runner image (compose profile)
├── ratis_core/              shared library: engine factory, JWT verify, config, SQLAlchemy models
├── ratis_client/            Expo / React Native app (app/ hooks/ services/ locales/)
├── batch/                   scheduled jobs (OSM sync, OFF sync, consensus, purge, reconciliation, …)
├── infra/aws/               Terraform: ECS Fargate, ALB, RDS, ElastiCache, Secrets Manager (+ reusable modules/service)
├── tools/agent-mcp/         Keychain-backed MCP server (the agentic-ops control plane)
├── alembic/                 migration env + versions
├── db/                      PostGIS schema + datafixes
├── docs/                    adr/ · arch/ (C4) · reference/ · ops/ · product/ · known/ · agents/ · assets/
├── docker-compose.yml       local data plane (Postgres + Redis + migrate profile)
├── docker-compose.prod.yml  production stack
└── pyproject.toml           uv workspace · uv.lock · .python-version (3.12)
```

**Why a monorepo.** This is a `uv`-workspace monorepo with a single committed `uv.lock`. The win is shared-model simplicity (one `ratis_core`, atomic schema+migration commits) and one CI surface; the cost is heavier CI (a `ratis_core` change rebuilds everything), mitigated by path-filtered, affected-only workflows. The boundary is explicit: services depend on `ratis_core`, never the reverse, and never import each other — cross-service calls go over HTTP with `INTERNAL_API_KEY`. → [ADR-0009](docs/adr/0009-portable-compose-staged-hosting.md)

---

## Services

All five services share one RSA-signed JWT (`aud=ratis`), connect to Postgres via `ratis_core.make_engine`, and raise domain exceptions in the service layer (translated to `HTTPException` only in routes) so logic is reusable from Celery workers and batch jobs.

| Service | Port | Responsibility | Key deps |
|---------|------|----------------|----------|
| `ratis_auth` | 8001 | OAuth (Google/Apple) → JWT, accounts, GDPR delete | Postgres, Redis, Stripe |
| `ratis_product_analyser` | 8003 | Receipt/label/barcode scan, OCR pipeline, price consensus | Postgres, Redis (Celery), R2, PaddleOCR |
| `ratis_list_optimiser` | 8002 | Shopping lists, multi-store route optimisation | Postgres, OSRM |
| `ratis_rewards` | 8004 | Cabecoin economy, cashback, gamification, gift cards, admin | Postgres |
| `ratis_notifier` | 8005 | Push notifications (internal-only, `INTERNAL_API_KEY`) | Postgres, Expo |

`ratis_rewards` deliberately keeps the CAB economy and gamification in one service — a scan produces a reward **and** advances missions in a single DB transaction, so splitting them would force a distributed transaction for what is naturally one local commit. → [ADR-0005](docs/adr/0005-rewards-gamification-one-service.md) · endpoint inventory: [`docs/reference/ENDPOINTS.md`](docs/reference/ENDPOINTS.md).

---

## Deployment

Container-portable by design — the same artifacts run on any host, following a deliberate three-stage path with **zero lock-in** between stages:

1. **Hetzner Cloud VPS** — V0 alpha (x86; PaddlePaddle has no `linux_aarch64` wheel).
2. **Mac mini M4 Pro (self-hosted)** — current development/V0 host, also running the GitHub Actions runners (€0/mo, full control).
3. **AWS** — Terraform-authored ahead of need (`infra/aws/`, eu-west-3): ECS Fargate + ALB + RDS PostgreSQL 16 + ElastiCache Redis 7 + Secrets Manager, with a reusable `modules/service` instantiated five times, targeting a sub-1-week cutover when the Mac mini saturates.

Provider versions are pinned and `.terraform.lock.hcl` is committed. The AWS stack is a representative POC (committed, not applied) that mirrors the compose topology one-to-one. → [ADR-0010](docs/adr/0010-aws-terraform-ahead-of-need.md) · [`docs/arch/ARCH_deployment.md`](docs/arch/ARCH_deployment.md) · [`docs/ops/SCALING.md`](docs/ops/SCALING.md).

---

## Testing & CI

- **Python tests (`pytest`) co-located per service** + Jest tests for the Expo client. Fixtures use per-test SAVEPOINT rollback isolation, an autouse `assert_no_pending_changes` guard, and an ephemeral per-session RS256 keypair.
- **GitHub Actions workflows.** Per-service pipelines chain `conventions → lint (Ruff) → SAST (Bandit) → tests (pytest + coverage → Codecov) → Alembic upgrade`, all path-filtered.
- **`security.yml`** runs `detect-secrets` plus ripgrep architecture guards (no `psycopg2`, `create_engine` confinement, no `HTTPException` in service layers) and a docker-compose ↔ `require_env` drift test.
- **`doc-inventories.yml`** enforces freshness of the auto-generated indexes ([`ENDPOINTS.md`](docs/reference/ENDPOINTS.md), [`ARCH_INVENTORY.md`](docs/reference/ARCH_INVENTORY.md)).
- **Dependabot** covers the pip and github-actions ecosystems.

CI runs in Linux Docker containers and is the ground-truth merge gate (never merge red).

---

## Documentation

The full docs set lives under [`docs/`](docs/) and follows the [Diátaxis](https://diataxis.fr/) taxonomy — start at [`docs/README.md`](docs/README.md). Highlights:

- **Understand the system** → [`docs/arch/ARCH_RATIS.md`](docs/arch/ARCH_RATIS.md) (full C4 set) · [`docs/arch/WELL_ARCHITECTED.md`](docs/arch/WELL_ARCHITECTED.md) (six-pillar self-assessment).
- **Judge the engineering** → [`docs/adr/`](docs/adr/) (14 MADR decision records).
- **How this repo is built by agents** → [`docs/agents/`](docs/agents/) (the agentic-development methodology).
- **Reference** → [`docs/reference/ENDPOINTS.md`](docs/reference/ENDPOINTS.md) · [`docs/reference/ARCH_INVENTORY.md`](docs/reference/ARCH_INVENTORY.md).

---

## License

Released under the [Apache License 2.0](LICENSE).
