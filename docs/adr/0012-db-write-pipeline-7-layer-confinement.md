# ADR-0012: db-write-pipeline — 7-layer confinement for agent-initiated DB writes

**Status:** Accepted (HSP-1..HSP-4 in force; HSP-5 identity swap is prep-only)

## Context and Problem Statement

Letting an agent (or n8n automation) write to the production database is high-risk. A security audit (`AUDIT_2026-05-19`) of the initial pipeline found 21 findings including 4 Critical (free-form SQL at runtime, self-authored invariants, rubber-stamping risk, agent-identity bypass). The worst case had to be structurally bounded regardless of LLM/human/agent error. How can an agent be allowed to initiate production writes while the blast radius is provably bounded?

## Decision Drivers

- The worst case must be bounded structurally, not by trusting any single actor (LLM, human reviewer, or agent).
- No free-form SQL executed at runtime (Critical C1).
- No self-authored / auto-generated invariants (Critical C3).
- A human gate that is hard to bypass or rubber-stamp.
- Kill-switches and a full append-only audit trail.

## Considered Options

- **An n8n pipeline where the agent only proposes, bounded by 7 independent confinement layers.**
- **Free-form SQL executed at runtime** (the original design).
- **Auto-generated / self-authored invariants.**
- **Letting the agent reuse the operator identity/SSH for writes.**

## Decision Outcome

Chosen: build an n8n `db-write-pipeline` where the agent only **proposes** (HMAC-signed via `db_propose_write`) and a **7-layer confinement** bounds the blast radius: agent proposes → catalogue of atoms → each write is a frozen stored procedure with a TOML manifest → curated catalogue → pipeline measurement → human gate → DB floor (change-log triggers + dormant caps). Atoms are verified with `pglast` (8 checks) at merge time; the human gate uses a typed challenge + a distinct argon2id HMAC secret + a deterministic French summary; the agent runs under a `REVOKE`-restricted Postgres role with identity re-verification and a per-call checksum. The worst case is provably bounded: ≤1 atom per call, ≤5k CAB per user and ≤50k/day, restricted to declared tables.

**Rejected:** free-form runtime SQL (Critical C1 — replaced by stored procedures + manifests); auto-generated/self-authored invariants (Critical C3 — replaced by human-curated TOML manifests verified by the real Postgres parser); reusing operator identity/SSH (being phased out via HSP-5 swap to a scoped `agent_read` role).

**Quality-attribute trade-off:** we bought **safety/integrity** (a structurally bounded worst case, defense-in-depth across independent layers) at the cost of **simplicity and a concentration of privilege** — heavy multi-project machinery, a 2-pass Anthropic LLM review adding cost and throttling, and n8n becoming a single point holding the HMAC secret + Anthropic key + `ADMIN_API_KEY` + SSH.

### Consequences

- **Good:** structurally bounded worst case; full append-only change log; kill-switches (session + settings); human approval that is hard to bypass or rubber-stamp; stored procedures + manifests kill Critical C1 and C3.
- **Bad:** heavy machinery (multiple HSP sub-projects, n8n + 2-pass Anthropic LLM review adding cost, throttling — DA-15); n8n becomes a concentration of privilege; the final identity swap (`agent_read` replacing operator SSH) is documented but not yet executed (HSP-5 prep only).

**Source.** `docs/arch/ARCH_n8n_pipelines.md` DA-11 (hub), HSP-1..HSP-5; `DECISIONS_ACTED`; `PROD_CHECKLIST.md`. Canonical register: [`../decisions/DECISIONS_ACTED.md`](../decisions/DECISIONS_ACTED.md).
