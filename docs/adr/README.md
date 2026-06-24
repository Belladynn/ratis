# Architecture Decision Log

This directory is the **Architecture Decision Log** for Ratis — a cashback + realtime-price + gamification platform (5 Python/FastAPI microservices, an Expo/React-Native client, and an agentic ops layer). It captures the consequential architecture decisions: not just *what* was chosen, but the forces in play, the options rejected, and the quality-attribute trade-off accepted.

## Format

Each file is one decision in **[MADR 4.0.0](https://adr.github.io/madr/)** form:

- Title `# ADR-NNNN: <title>` and a **Status** line (`Accepted`, with supersession notes where relevant).
- `## Context and Problem Statement`
- `## Decision Drivers`
- `## Considered Options`
- `## Decision Outcome` — the chosen option and why others were rejected, an explicit **quality-attribute trade-off** sentence (which attribute we bought, which we paid), and `### Consequences` split Good / Bad.

Files are named `NNNN-kebab-title.md` with zero-padded, contiguous numbering. New decisions append sequentially; a superseded decision keeps its file and cross-references its replacement in the Status line.

## Canonical decision register

This log is a **curated synthesis**. The **canonical decision register remains [`../decisions/DECISIONS_ACTED.md`](../decisions/DECISIONS_ACTED.md)**, alongside the per-service and cross-service `ARCH_*.md` design docs. Every ADR here links back to it; when the two differ, `DECISIONS_ACTED.md` and the source `ARCH_*.md` are authoritative.

## Index

| ID | Decision | Status | Tags | File |
|----|----------|--------|------|------|
| ADR-0001 | Asymmetric RS256 JWT — single issuer, many verifiers | Accepted | `auth` `security` `microservices` | [0001](0001-rs256-jwt-single-issuer.md) |
| ADR-0002 | OAuth-only delegated authentication (Apple + Google) | Accepted | `auth` `security` `identity` `rgpd` | [0002](0002-oauth-only-delegated-auth.md) |
| ADR-0003 | Monetary amounts as integer cents end-to-end | Accepted | `money` `data-integrity` `domain` | [0003](0003-integer-cents-money.md) |
| ADR-0004 | psycopg v3 + SQLAlchemy 2.0 via a shared engine factory | Accepted | `persistence` `stack` `ci-guard` | [0004](0004-psycopg3-sqlalchemy2-engine-factory.md) |
| ADR-0005 | Rewards + gamification in one service, not split | Accepted | `service-boundaries` `yagni` `transactions` | [0005](0005-rewards-gamification-one-service.md) |
| ADR-0006 | Transactional outbox for fire-and-forget notifications | Accepted | `reliability` `async` `outbox` | [0006](0006-transactional-outbox-notifications.md) |
| ADR-0007 | Multi-user price consensus with time-weighted trust score | Accepted | `consensus` `data-quality` `tunable` | [0007](0007-time-weighted-price-consensus.md) |
| ADR-0008 | GDPR deletion by in-place anonymization (4-tier tombstones) | Accepted | `rgpd` `data-lifecycle` `legal` | [0008](0008-gdpr-in-place-anonymization.md) |
| ADR-0009 | Portable Docker-Compose stack with a staged hosting path | Accepted | `infra` `deployment` `portability` | [0009](0009-portable-compose-staged-hosting.md) |
| ADR-0010 | AWS target as Terraform IaC ahead of need | Accepted (POC) | `infra` `aws` `iac` `terraform` | [0010](0010-aws-terraform-ahead-of-need.md) |
| ADR-0011 | agent-mcp — Keychain-backed control plane, no raw secrets in the model | Accepted | `agentic` `security` `mcp` `secrets` | [0011](0011-agent-mcp-keychain-control-plane.md) |
| ADR-0012 | db-write-pipeline — 7-layer confinement for agent-initiated DB writes | Accepted | `agentic` `security` `defense-in-depth` `database` | [0012](0012-db-write-pipeline-7-layer-confinement.md) |
| ADR-0013 | Agentic doc RAG — hybrid bge-m3 + sqlite-vec behind a typed MCP surface | Accepted | `agentic` `rag` `mcp` `local-first` | [0013](0013-agentic-doc-rag-bge-m3-sqlite-vec.md) |
| ADR-0014 | LLM production observability via self-hosted Langfuse | Accepted | `agentic` `observability` `llm` `rgpd` | [0014](0014-langfuse-llm-observability.md) |
