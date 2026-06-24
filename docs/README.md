# Ratis documentation

Welcome to the Ratis documentation set. **Ratis** is a production-grade cashback + real-time-price + gamification platform — five FastAPI microservices, an Expo/React-Native app, and Terraform/AWS IaC — operated by a bespoke **agentic-AI control plane**.

If you are here to **understand the system**, start with the C4 diagrams in [`arch/ARCH_RATIS.md`](arch/ARCH_RATIS.md). If you are here to **run it**, jump to the [Quickstart](#quickstart). If you are here to **judge the engineering**, read the [architecture decision records](adr/) and the [Well-Architected self-assessment](arch/WELL_ARCHITECTED.md).

## How this documentation is organised (Diátaxis)

This corpus follows the [**Diátaxis**](https://diataxis.fr/) taxonomy — every document is deliberately *one* of tutorial, how-to, reference, or explanation, because mixing those modes is the main cause of unusable docs. The mapping:

| Diátaxis mode | Purpose | Where it lives |
|---------------|---------|----------------|
| **Tutorial / How-to** | Get started, accomplish a task | the root `README.md` (onboarding + quickstart) · [`ops/`](ops/) runbooks (migration, setup, scaling) |
| **Reference** | Look up precise facts | [`reference/`](reference/) — `ENDPOINTS.md` (API inventory), `ARCH_INVENTORY.md` (doc index) · `db/schema.sql` |
| **Explanation** | Understand *why* it is built this way | [`arch/`](arch/) (`ARCH_*.md` C4 + design) · [`adr/`](adr/) (decision records) · [`arch/WELL_ARCHITECTED.md`](arch/WELL_ARCHITECTED.md) |
| **How-to (operations)** | Operate production safely | [`ops/`](ops/) (`RUNBOOK_MIGRATION.md`, `SETUP_CHECKLIST.md`, `SCALING.md`) · [`known/`](known/) (known problems) |

The split is intentional: the "why" stays in ADRs and `ARCH_*.md`; the "what exactly" stays in `reference/`; the "how do I" stays in `ops/`. The architecture indexes (`reference/ARCH_INVENTORY.md`, `reference/ENDPOINTS.md`) are **auto-generated** and CI-freshness-checked, so the reference layer cannot silently drift.

## Documentation map

### Start here

- [`arch/ARCH_RATIS.md`](arch/ARCH_RATIS.md) — **top-level system architecture** with the inline C4 set (System Context, Container, Deployment) and the agentic-layer diagram.
- [`arch/WELL_ARCHITECTED.md`](arch/WELL_ARCHITECTED.md) — AWS Well-Architected six-pillar self-assessment (with honestly-noted gaps).
- [`adr/`](adr/) — append-only architecture decision records (MADR): RS256 single-issuer JWT, int-cents money model, psycopg v3 + SQLAlchemy 2.0, rewards-as-one-service, transactional outbox, time-weighted price consensus, GDPR in-place anonymisation, staged hosting, AWS-ahead-of-need.

### Architecture & design (explanation)

- [`arch/`](arch/) — cross-cutting `ARCH_*.md`: [deployment](arch/ARCH_deployment.md), [agent-mcp control plane](arch/ARCH_agent_mcp.md), [agent isolation](arch/ARCH_agent_mcp_isolation.md), [LLM observability](arch/ARCH_llm_observability.md), [n8n pipelines](arch/ARCH_n8n_pipelines.md), [Hermes ops](arch/ARCH_hermes_ops.md), [incident management](arch/ARCH_incident_management.md), [CAB economy](arch/ARCH_cab_economy.md), [referral](arch/ARCH_referral.md), [anti-fraud](arch/ARCH_anti_fraud.md), [OCR store detection](arch/ARCH_ocr_store_detection.md), [doc system](arch/ARCH_doc_system.md).
- Per-service ARCHs live **next to the code** (`webservices/<svc>/ARCH_<SVC>.md`, `batch/.../ARCH_BATCH_*.md`, `ratis_core/ARCH_CORE.md`) and are indexed in [`reference/ARCH_INVENTORY.md`](reference/ARCH_INVENTORY.md).

### Reference

- [`reference/ENDPOINTS.md`](reference/ENDPOINTS.md) — auto-generated API endpoint inventory across all services.
- [`reference/ARCH_INVENTORY.md`](reference/ARCH_INVENTORY.md) — auto-generated index of every `ARCH_*.md` (the discovery layer for the docs).

### Operations (how-to)

- [`ops/RUNBOOK_MIGRATION.md`](ops/RUNBOOK_MIGRATION.md) — production Alembic migration runbook.
- [`ops/SETUP_CHECKLIST.md`](ops/SETUP_CHECKLIST.md) — environment/setup checklist.
- [`ops/SCALING.md`](ops/SCALING.md) — the Hetzner → Mac-mini → AWS scaling story with saturation signals.
- [`ops/PROD_CHECKLIST.md`](ops/PROD_CHECKLIST.md) — pre-production task list.
- [`known/KNOWN_PROBLEMS.md`](known/KNOWN_PROBLEMS.md) + [`known/KNOWN_PROBLEMS_INDEX.md`](known/KNOWN_PROBLEMS_INDEX.md) — catalogued pitfalls (the "things that cost 30 minutes" log).

### Product & privacy

- [`product/PRODUCT.md`](product/PRODUCT.md) — product vision, business model, target users.
- [`product/PRIVACY.md`](product/PRIVACY.md) — the GDPR/RGPD policy (data minimisation, retention, anonymisation).

### How this repo is built (methodology)

- [`agents/`](agents/) — the agentic-development reference files (`CLAUDE.md`, `ORCHESTRATOR.md`, `SA_DEV.md`, `SA_EXPLORE.md`) that define how the LLM control plane operates this codebase. These are part of the showcase, not incidental config.

## Quickstart

```bash
# prerequisites: Python 3.12 (pinned), uv, Docker
docker compose up -d                                    # Postgres 16, Redis, OSRM
uv sync --frozen                                        # reproducible workspace
export DATABASE_URL=postgresql+psycopg://ratis:ratis@localhost:5432/ratis_dev
docker compose --profile migrate run --rm migrations    # alembic upgrade head
curl -sf http://localhost:8001/health                   # verify auth service is up
```
