# ADR-0005: Rewards + gamification in one service, not split

**Status:** Accepted

## Context and Problem Statement

The CAB economy (cabecoin balances, cashback) and gamification (missions, battle pass, streaks, challenges) could each be its own microservice. But a single user scan simultaneously produces a reward *and* advances missions/battlepass. Where should the service boundary fall between these two domains?

## Decision Drivers

- A scan event mutates both reward and gamification state, and that mutation must be atomic.
- Avoid distributed-systems cost (distributed transactions, sagas) that the workload doesn't yet require (YAGNI).
- Preserve a future extraction path if a split ever becomes warranted.

## Considered Options

- **Keep CAB economy and gamification together in `ratis_rewards`**, with logical separation in code (distinct routes / services / repositories).
- **Two separate services (rewards vs gamification)**, communicating across a service boundary.

## Decision Outcome

Chosen: **keep CAB economy and gamification together in `ratis_rewards`**. Splitting them into two services is judged over-engineering. A scan = reward **and** mission in the same DB transaction. Logical separation in code (distinct routes / services / repositories) is considered sufficient to split later if ever needed.

**Rejected:** two separate services — it would break the transactional atomicity of the scan → reward + mission flow, forcing a distributed transaction or an eventual-consistency saga for what is naturally one local transaction.

**Quality-attribute trade-off:** we bought **consistency and operational simplicity** (atomic scan → reward + mission in one local transaction, no cross-service consistency machinery) at the cost of **modularity** — `ratis_rewards` is a large multi-domain service whose internal boundaries are enforced only by convention.

### Consequences

- **Good:** atomic scan → reward + mission in one transaction; simpler ops; no cross-service consistency machinery.
- **Bad:** `ratis_rewards` is a large multi-domain service (CAB, cashback, gift-cards, battlepass, missions, streaks, mystery, referral, leaderboard, admin) whose internal boundaries are convention-only; a future split would require disentangling shared transactions.

**Source.** `docs/decisions/DECISIONS_ACTED.md` DA-05; `ARCH_INVENTORY.md` ARCH_REWARDS. Canonical register: [`../decisions/DECISIONS_ACTED.md`](../decisions/DECISIONS_ACTED.md).
