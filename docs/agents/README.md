# How this repository is built by AI agents

> **TL;DR.** Ratis is a solo project, but it is not built by a single person
> typing into an editor. It is built by a **fleet of Claude Code agents** —
> a planning orchestrator that dispatches typed subagents — operating under
> explicit, versioned rules and through a custom **control plane** that lets an
> LLM run privileged operations *without ever seeing a raw secret*. This page is
> the map of that methodology. The four reference files in this folder
> ([`CLAUDE.md`](CLAUDE.md), [`ORCHESTRATOR.md`](ORCHESTRATOR.md),
> [`SA_DEV.md`](SA_DEV.md), [`SA_EXPLORE.md`](SA_EXPLORE.md)) are the actual
> operating manual the agents read at runtime.

This is a deliberate experiment in **agentic software engineering**: treating
"an agent that helps me code" as a system to be designed — with roles, contracts,
isolation boundaries, retrieval, observability, and an audit trail — rather than
an autocomplete that happens to be conversational. The honest framing: one
operator (the human) directs the work and owns every irreversible decision; the
agents do the search, the synthesis, the implementation, and the routine ops,
under rules the operator can read, version, and revoke.

---

## 1. The orchestrator + typed-subagent split

The core pattern is a **separation of concerns between contexts**, mirroring how
a tech lead delegates to specialists.

- **The orchestrator** is the main Claude Code session. It plans, designs,
  decides trade-offs, and dispatches work — but it does **not** implement
  features directly. Its rules live in [`ORCHESTRATOR.md`](ORCHESTRATOR.md),
  auto-injected at session start by a `.claude/settings.json` hook. Keeping the
  main context for orchestration (not 500-line file dumps) is what keeps it
  sharp across a long session.
- **Typed subagents** are dispatched for the heavy lifting, each with a
  single-purpose discipline:
  - a **dev subagent** ([`SA_DEV.md`](SA_DEV.md)) implements features and fixes
    test-first, in small atomic commits, and never ships a workaround;
  - an **exploration subagent** ([`SA_EXPLORE.md`](SA_EXPLORE.md)) answers a
    precise question about the codebase under a strict reading order
    (semantic doc search → grep/glob → indexes → segmented read → full read only
    as a last resort), returning a conclusion instead of raw file contents;
  - a **review subagent** runs code/security review against the diff.
- **Shared rules** that apply to everyone live in [`CLAUDE.md`](CLAUDE.md) — the
  stack contract (uv not pip, psycopg v3, integer cents, TDD), the
  domain-table semantics, the GDPR red-lines, and the numbered rules (R15–R42)
  that encode every hard-won discipline.

The reason this matters: each context stays small and on-task, the operator can
audit exactly which rules each agent is bound by, and the same brief produces
the same behavior. The orchestrator must explicitly tell each subagent which
`SA_*.md` to read — delegation is contractual, not implicit.

```
        ┌──────────────────────────────────────────────┐
        │  Operator (human) — owns irreversible calls   │
        └───────────────────────┬──────────────────────┘
                                │ directs
                    ┌───────────▼───────────┐
                    │   Orchestrator         │  ORCHESTRATOR.md
                    │  (plan · design ·      │  + CLAUDE.md (shared)
                    │   dispatch · decide)   │
                    └───┬───────────┬────────┘
            dispatch    │           │    dispatch
          ┌─────────────▼──┐   ┌────▼─────────────┐   ┌──────────────┐
          │ Dev subagent   │   │ Explore subagent │   │ Review SA    │
          │  SA_DEV.md     │   │  SA_EXPLORE.md   │   │ (diff audit) │
          │  TDD · atomic  │   │  grep→index→read │   │              │
          └────────────────┘   └──────────────────┘   └──────────────┘
```

## 2. Recon before design — never reinvent

A recurring failure mode of LLM coding is confidently referencing a symbol,
endpoint, or config key that doesn't exist (or rebuilding a brick that already
does). The methodology hard-codes a **reconnaissance discipline** to prevent it:

- Before any brainstorm or design, the orchestrator runs a `codebase-recon`
  pass that returns a verdict — `FROM SCRATCH | EXTEND | MOSTLY EXISTS` — plus
  the reusable pieces it found (rules R27/R28).
- Before designing or touching an endpoint, agents consult the auto-generated
  [`docs/reference/ENDPOINTS.md`](../reference/ENDPOINTS.md) inventory.
- Before extending architecture, they query the docs via the hybrid-RAG index
  (below) rather than full-reading large files (rules R29/R41).

This is the difference between "the agent guessed and we caught it in review"
and "the agent reconciled every symbol in its plan against what already exists
before writing a line."

## 3. `agent-mcp` — a Keychain-backed control plane (the model never sees secrets)

The centerpiece. After three token leaks in two sessions in May 2026 (a bash
`${VAR:-default}` expansion printing `EXPO_TOKEN`, a `cat ~/.zprofile` dumping
it, a Sentry token re-exported on screen), the fix was structural rather than
"be more careful": **the model must never manipulate a secret string at all.**

[`tools/agent-mcp/`](../../tools/agent-mcp/) is a local **Model Context Protocol**
server that exposes ~30+ **typed tools** to the agents (GlitchTip, EAS, GitHub,
Stripe, R2, read-only Postgres, docs, secrets) across ten modules. When the
agent wants to act, it calls a *function* — `eas_update_preview(message)`,
`github_comment_pr(pr, body)`, `glitchtip_list_issues(query)` — and the server:

1. reads the relevant token from the **macOS Keychain** at call time;
2. performs the HTTP / CLI call to the provider;
3. appends one redacted line to an append-only audit log.

The secret is injected into the outbound request only; it never appears in tool
arguments, return values, logs, or the model's context. Two scoped caller tokens
enforce least privilege — `ops` (read + a few whitelisted writes) is the default
for dispatched subagents, `admin` is reserved for interactive sessions where the
human validates each sensitive action — and an out-of-scope call is rejected
with `forbidden_tool` *before any provider traffic*. The token backend is behind
a `get_secret(account)` interface, so Bitwarden / 1Password / AWS Secrets
Manager can be swapped in later.

→ Deep dive: [`tools/agent-mcp/README.md`](../../tools/agent-mcp/README.md) ·
[`docs/arch/ARCH_agent_mcp.md`](../arch/ARCH_agent_mcp.md) ·
decision: [ADR-0011](../adr/0011-agent-mcp-keychain-control-plane.md).

## 4. The just-in-time secrets vault

`agent-mcp` is how the agent *uses* provider tokens; the **secrets vault**
(module 10) is how the agent *handles* secrets it must mint or rotate. The rule
(R42) is **just-in-time leasing**: instead of "the operator pastes a long-lived
token into the environment," the agent leases a short-lived credential for the
duration of one command and revokes it automatically —
`ratis-secret use <name> --cmd "..."` injects it into a subprocess without
displaying it, or, in Python, the `secret_with` context manager leases and
revokes around a block. Auto-forgeable and CLI-mintable secrets are minted on
demand; UI-only secrets are imported once with an explicit expiry and a rotation
reminder. Every operation lands in an **HMAC-chained, append-only audit log**,
so the trail is tamper-evident.

## 5. `docs-mcp` — hybrid-RAG over the project's own documentation

Project rule R29 forbids agents from full-reading large architecture docs (token
cost and drift). So the docs themselves are a **retrieval system**. Module 9 of
`agent-mcp` indexes the pipe-separated documentation inventory and serves
`docs_search / get / find / list_files / reindex` as typed MCP calls. Retrieval
is **hybrid**: `bge-m3` embeddings stored in `sqlite-vec`, fused with keyword
matching (`0.7 * vector + 0.3 * keyword`), so an agent can find the right
*bounded section* by meaning without knowing the file path or line range. It is
deliberately lightweight — the corpus is small (~80 vectors), cosine in numpy is
sub-200ms, everything is local (RGPD-safe), and it degrades gracefully to a
smaller model and then to keyword-only if the embedding model is unavailable. A
session-start hook injects the most relevant nuggets into context automatically.

→ Decision: [ADR-0013](../adr/0013-agentic-doc-rag-bge-m3-sqlite-vec.md).

## 6. Hermes — the always-on ops agent

Where the orchestrator and subagents are summoned per session, **Hermes** is a
**persistent ops agent** (a container on the host) that runs the routine
operational loop. It drives a Telegram bot for status, pending-ticket queries,
and incident alerts; runs native cron jobs; tracks an ephemeral kanban of
follow-ups (kept deliberately separate from the git-tracked decision log); and
runs a postmortem → reviewer pipeline that distills recurring incidents into
reusable skills. It is also exposed to Claude Code as its own MCP server, so the
interactive agents can query it. Its stack is versioned under
[`infra/hermes/`](../../infra/hermes/).

→ Architecture: [`docs/arch/ARCH_hermes_ops.md`](../arch/ARCH_hermes_ops.md).

## 7. n8n db-write confinement — bounding the worst case structurally

Letting an agent write to the **production database** is the highest-risk thing
in this whole design, so it gets the heaviest treatment. A security audit of an
early version found 21 findings (4 Critical: free-form runtime SQL,
self-authored invariants, rubber-stamping risk, agent-identity bypass). The
response is an **n8n pipeline where the agent only ever *proposes*** an
HMAC-signed write, and **seven independent confinement layers** bound the blast
radius:

1. the agent proposes (it cannot execute);
2. proposals map to a catalogue of pre-frozen **atoms** (stored procedures);
3. each atom is governed by a human-curated **TOML manifest** verified by the
   real Postgres parser (`pglast`) — no self-authored invariants;
4. the catalogue is curated, not generated;
5. the pipeline measures and bounds each call;
6. a **typed human gate** (challenge + distinct argon2id-HMAC secret +
   deterministic plain-language summary) resists rubber-stamping;
7. a database floor — change-log triggers, a `REVOKE`-restricted role, dormant
   caps (≤1 atom/call, per-user and per-day ceilings) — enforces limits even if
   everything above fails.

The point is **defense in depth**: the worst case is bounded *structurally*,
not by trusting any single actor — LLM, human reviewer, or agent.

→ Decision: [ADR-0012](../adr/0012-db-write-pipeline-7-layer-confinement.md) ·
architecture: [`docs/arch/ARCH_n8n_pipelines.md`](../arch/ARCH_n8n_pipelines.md).

## 8. Closing the loop — LLM observability in production

The agentic layer is paired with **LLM observability** on the product's own
production model call (Claude Haiku in the OCR receipt pipeline), via
self-hosted **Langfuse**: token / cost / latency / fallback tracing, with output
capture disabled and only internal IDs in traces so no purchase data leaves the
host. It is a no-op when keys are absent, so CI and dev stay clean. This is the
EvalOps foundation — tracing today, offline eval and A/B model comparison on the
roadmap.

→ Decision: [ADR-0014](../adr/0014-langfuse-llm-observability.md).

---

## The methodology in one table

| Concern | Mechanism | Reference |
| ------- | --------- | --------- |
| Roles & delegation | Orchestrator + typed subagents | [`ORCHESTRATOR.md`](ORCHESTRATOR.md), [`SA_DEV.md`](SA_DEV.md), [`SA_EXPLORE.md`](SA_EXPLORE.md) |
| Shared contract | Stack, domain, GDPR, numbered rules | [`CLAUDE.md`](CLAUDE.md) |
| Don't reinvent | `codebase-recon` + endpoint/doc inventories | rules R27–R29 |
| Secrets the agent uses | Keychain-backed MCP control plane | [ADR-0011](../adr/0011-agent-mcp-keychain-control-plane.md), [`tools/agent-mcp/`](../../tools/agent-mcp/) |
| Secrets the agent mints | Just-in-time vault + HMAC audit | rule R42 |
| Doc retrieval | Hybrid `bge-m3` + `sqlite-vec` RAG | [ADR-0013](../adr/0013-agentic-doc-rag-bge-m3-sqlite-vec.md) |
| Routine ops | Hermes persistent ops agent | [ADR / `ARCH_hermes_ops.md`](../arch/ARCH_hermes_ops.md) |
| Production DB writes | n8n 7-layer confinement (propose-only) | [ADR-0012](../adr/0012-db-write-pipeline-7-layer-confinement.md) |
| LLM observability | Self-hosted Langfuse, RGPD-hard | [ADR-0014](../adr/0014-langfuse-llm-observability.md) |

The four agentic decisions are recorded as ADRs
[0011](../adr/0011-agent-mcp-keychain-control-plane.md)–[0014](../adr/0014-langfuse-llm-observability.md);
see the [Architecture Decision Log](../adr/README.md) for the full set, and the
top-level [`README`](../../README.md) for the product and systems overview.

> **Honest scope.** This is a single-operator project run with an agent fleet,
> not a team. The agents accelerate search, synthesis, implementation, and ops;
> the operator owns the architecture, every irreversible action, and the
> business decisions. The value on display is the *engineering of the agentic
> system itself* — the roles, the contracts, the secret-isolation boundary, the
> retrieval layer, and the audit trail that make "an agent that operates a
> system" safe enough to actually use.
