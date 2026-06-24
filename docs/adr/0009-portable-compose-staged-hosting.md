# ADR-0009: Portable Docker-Compose stack with a staged hosting path

**Status:** Accepted (stage 2 — Mac mini — done 2026-05-04, PR #287)

## Context and Problem Statement

A solo founder needs production infra for an alpha (5–50 users) that can survive aggressive marketing spikes and eventual internationalization, while keeping cash burn near zero and learning ops. A hard early constraint surfaced: PaddleOCR/paddlepaddle has no wheel for `linux_aarch64`, which broke the first ARM Hetzner build (CAX21) and forced a move to x86 (CX31). What hosting strategy keeps cost near zero and avoids lock-in while leaving a fast path to managed cloud?

## Decision Drivers

- Near-zero cash burn during alpha; pay for managed cloud only when justified.
- Zero lock-in and trivial host migration.
- Survive marketing spikes and a future cutover in under a week.
- Hard constraint: no PaddlePaddle `aarch64` wheel.
- Operator wants ops-learning value from running the stack.

## Considered Options

- **Portable Docker + docker-compose + Caddy**, staged Hetzner → Mac mini → AWS, multi-arch images.
- **Go straight to managed cloud (AWS/Railway) from day one.**
- **ARM Hetzner (CAX) instances.**
- **Lock-in managed AWS services (DynamoDB, SQS, Lambda).**

## Decision Outcome

Chosen: standardize on a portable **Docker + docker-compose + Caddy** stack so the same artifacts run on any host, and progress through three deliberate stages: (1) **Hetzner Cloud VPS** for V0 alpha, (2) **Mac mini M4 Pro** self-hosted at home (done), (3) **AWS** (ECS Fargate + RDS + ElastiCache) when the Mac mini saturates. Images are built multi-arch (amd64 + arm64).

**Rejected:** managed cloud from day one (premature and expensive); ARM Hetzner CAX (no PaddlePaddle `aarch64` wheel); lock-in managed AWS services (DynamoDB/SQS/Lambda banned until an explicit scaling decision — stay on Postgres + Redis + FastAPI).

**Quality-attribute trade-off:** we bought **portability and cost-efficiency** (zero lock-in, ~€0/mo at the Mac mini stage, fast host swaps) at the cost of **availability/operability** — the self-hosted home stage couples production to a residential IP/DNS and the operator's machine, inherits the ARM PaddlePaddle limitation, and requires proactive saturation detection (CPU >60% over 24h) to avoid a panic cutover.

### Consequences

- **Good:** cheap, portable, no lock-in, fast host swaps (AWS cutover targeted in <1 week if Terraform is ready); moving Hetzner → Mac mini required no config change since both are ARM64.
- **Bad:** the Mac mini is ARM64, inheriting the PaddlePaddle/OCR `aarch64` limitation (OCR must be externalized or run x86); OSRM France PBF needs 16+ GB RAM, so small VMs must point at the public OSRM router; the home stage couples production to a residential IP/DNS and the operator's machine; saturation must be detected proactively.

**Source.** `docs/arch/ARCH_deployment.md` (multi-stage strategy, migration path, CPU/arch table); `PROD_CHECKLIST.md` (hosting strategy). Canonical register: [`../decisions/DECISIONS_ACTED.md`](../decisions/DECISIONS_ACTED.md).
