# ADR-0004: psycopg v3 + SQLAlchemy 2.0 via a shared engine factory

**Status:** Accepted

## Context and Problem Statement

Developers (and LLM codegen) default to `psycopg2` by reflex — it is the historical Python Postgres driver. Ratis standardizes on `psycopg[binary]` v3 with the `postgresql+psycopg://` URL scheme and SQLAlchemy 2.0. The wrong driver is incompatible with the chosen URL scheme and causes connection failures. How is the driver-and-engine convention enforced across 5 services plus batch jobs?

## Decision Drivers

- One Postgres driver and URL scheme across all services and batches — mixing drivers breaks connections.
- Consistent pool/connection config from a single shared library (R18 — never duplicate).
- The rule must survive both human and LLM defaults, which pull toward `psycopg2`.

## Considered Options

- **`psycopg[binary]` v3 only, all engines via `ratis_core.database.make_engine(url)`**, enforced by a CI grep guard.
- **`psycopg2` driver** (the historical/legacy default).
- **Direct `create_engine` per service** instead of a shared factory.

## Decision Outcome

Chosen: always **`psycopg[binary]` v3**; never `psycopg2`; never call SQLAlchemy `create_engine` directly — always go through `ratis_core.database.make_engine(url)`. A CI grep guard fails the build if the string `psycopg2` appears anywhere under `webservices/`, `ratis_core/`, or `batch/`. The convention is codified as known-problem guard KP-06.

**Rejected:** `psycopg2` (incompatible with `make_engine` and the `postgresql+psycopg://` URL; legacy); direct `create_engine` per service (config drift, duplication).

**Quality-attribute trade-off:** we bought **consistency and maintainability** (uniform connectivity, centralized config, automated drift prevention) at the cost of a **standing CI guard** that must stay in place — because human/LLM defaults pull toward `psycopg2` (KP-06), the rule is not self-evident from the code alone.

### Consequences

- **Good:** uniform DB connectivity; centralized engine config in one shared library; automated drift prevention via the CI guard.
- **Bad:** a persistent footgun because LLM/dev defaults pull toward `psycopg2` (KP-06) — the CI guard must remain in place to stay effective.

**Source.** `docs/known/KNOWN_PROBLEMS.md` KP-06; `CLAUDE.md` stack (db driver); `ratis_core/ARCH_CORE.md`. Canonical register: [`../decisions/DECISIONS_ACTED.md`](../decisions/DECISIONS_ACTED.md).
