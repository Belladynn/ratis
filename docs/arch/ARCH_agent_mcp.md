---
# Identity
type: cross-cutting
service: agent-mcp
status: shipped

# Navigation (Obsidian + RAG)
parent: ARCH_RATIS
sub_archs: []
related: [ARCH_deployment, ARCH_itops]

# Technical
tech: [Python, MCP SDK, uv, macOS Keychain, stdio JSON-RPC]
tables: []
env_vars:
  - MCP_AUTH_ADMIN_TOKEN
  - MCP_AUTH_OPS_TOKEN
  - MCP_KEYCHAIN_SERVICE
  - MCP_AUDIT_LOG_PATH
depends_on: [macOS Keychain (security CLI), Anthropic MCP SDK (Python)]

# Business
tags: [mcp, agent, secrets, automation, ops, security, claude-code]
business_domain: infra
rgpd_concern: false

# Freshness (MANDATORY — R34 — update à chaque édition)
updated: 2026-06-01
last_chunk_completed: Module 9 (docs_tools) — phase D + session-context bridge (corpus multi-sources, docs_context_for_session, SessionStart hook)
---

# agent-mcp — MCP server `ratis-agent-mcp` (agent control plane)

> Keychain-backed MCP server `ratis-agent-mcp` that exposes typed tools (Sentry/EAS/GitHub/Notion/Stripe/R2/DB/Docs) to Claude Code without ever putting tokens in the model context. stdio JSON-RPC + macOS Keychain architecture. Modules 1-9 delivered (M8 = db_tools read-only Postgres; M9 = docs_tools hybrid vector+keyword search over the docs, phase D).
> @tags: mcp agent secrets automation ops security claude-code keychain stdio json-rpc shipped agent-mcp db_tools docs_tools sentry eas github notion stripe r2 vector embeddings bge-m3 sqlite-vec hybrid-search semantic-search agentic-docs
> @status: LIVRÉ V0
> @subs: auto

> [[ARCH_RATIS]] · relations : [[ARCH_deployment]], [[ARCH_itops]]

> This ARCH describes `ratis-agent-mcp`, an **MCP server** (Model Context
> Protocol, Anthropic spec) that exposes **typed tools** to Claude Code
> and other agents to interact with external services (Sentry, EAS,
> GitHub, Notion, Stripe, R2, Runa) **without ever exposing API tokens
> to the model**. Tokens live in the **macOS Keychain**; the MCP reads
> them at tool call time and uses them for outbound HTTP. Append-only
> audit trail for every call. V0 = Mac mini, stdio MCP, comfortable scope
> of 7 tool modules. Complementary to [[ARCH_deployment]] (which only covers
> prod secrets `.env.prod` on the Hetzner server side — silent on
> local dev secrets and agent automation).

## Index

- [Vision](#vision)
- [Components](#components)
- [Architecture & topology](#architecture--topology)
- [Key architecture decisions](#key-architecture-decisions)
- [Implementation checklist](#implementation-checklist)
- [Runbook](#runbook)
- [Out of scope](#out-of-scope)
- [Things to know (vectorized FAQ)](#things-to-know-vectorized-faq)
- [Glossary](#glossary)

---

## Vision

Three token leaks were observed over two Claude Code sessions in May 2026, all caused by **direct** agent access paths to env secrets:

1. **EXPO_TOKEN leaked via `${VAR:-default}`** (bad bash expansion — the default was substituted by the real token value in the log).
2. **EXPO_TOKEN leaked via `cat ~/.zprofile`** (the SA was checking whether shell sourcing was OK and printed the entire shell profile, which contained `export EXPO_TOKEN=...`).
3. **SENTRY_AUTH_TOKEN insufficient scopes** → friction of regenerating each session, with re-export visible in context.

The pattern that structurally fixes these leaks is **agent-mcp**: Claude never has to manipulate a secret in plain text again. It calls a typed tool (`glitchtip_list_issues(query)`, `eas_update(channel, msg)`), and the MCP handles:
- reading the token from the **macOS Keychain** at call time;
- making the HTTP/CLI call to the third-party provider;
- writing a line to the **append-only audit log** (who, when, which tool, which args without secrets, response status).

The long-term vision is for `agent-mcp` to become the **single control plane for all agents that touch secrets**: Claude Code dev (interactive sessions), dispatched SAs (in parallel on worktrees), and in Phase C the n8n ops automation workflows. One surface, one audit, centralized rotation. Secrets never leave the trusted machine; the model sees only functional results.

V0 stays intentionally minimal: local Mac mini (DA-45), stdio MCP (least infrastructure), 7 tool modules delivered in iterative SA chunks. No Docker, no exposed network, no multi-host. Sophistication will come with needs (HTTP MCP in multi-machine mode, Bitwarden/1Password if we grow beyond macOS).

## Components

9 tool modules, each = one typed Python file in `tools/agent-mcp/src/agent_mcp/tools/<module>.py` (modules 1-7 = third-party providers, module 8 = `db_tools` = Ratis internal infra, module 9 = `docs_tools` = structured search over the docs). Each tool is a **Python function with a typed signature** (Pydantic-compatible type hints + docstring) consumed by the MCP SDK which auto-generates JSON schemas (DA-49). The MCP server exposes these tools via `tools/list` at handshake then `tools/call` at each invocation.

Auth convention: each tool declares its **scope** in its docstring (`Scope: ops` / `Scope: admin` / `Scope: both`). The MCP router rejects the call if the caller token (admin or ops) does not match the scope (DA-44).

### Module 1 — `glitchtip_tools`

> **DA-47 follow-up** — formerly `sentry_tools`. Sentry SaaS sunset (2026-05-31) → self-hosted GlitchTip (Sentry-v0 compatible protocol, same `/api/0/...` paths). The Python `sentry-sdk` SDK is still used on the backend side (protocol compat); only the admin MCP tools have migrated to the local instance.

- **Backend**: GlitchTip HTTP API (`http://localhost:8000/api/0/` by default, override via `GLITCHTIP_API_URL`). ~90% Sentry-v0 compatible for the 4 paths used.
- **Keychain token**: `security -s ratis-agent-mcp -a admin-glitchtip` → GlitchTip admin auth token (created via local UI, `/profile/auth-tokens`). Scopes: `project:read`, `event:read`, `event:admin`, `org:read`. Same Keychain entry is shared with the CLI wrapper `~/glitchtip/bin/glt`.
- **Org slug**: `GLITCHTIP_ORG` env var (default: `ratis`).
- **Exposed tools**:

```python
def glitchtip_list_issues(
    project: str,
    query: str = "is:unresolved",
    limit: int = 10,
) -> list[dict]:
    """List issues for a GlitchTip project. Read-only.
    Scope: ops.
    """

def glitchtip_get_issue(issue_id: str) -> dict:
    """Get full issue details (stacktrace, breadcrumbs, user context).
    Read-only. Scope: ops.
    """

def glitchtip_list_events(
    issue_id: str,
    limit: int = 5,
) -> list[dict]:
    """List recent events for a given issue. Read-only. Scope: ops."""

def glitchtip_resolve_issue(
    issue_id: str,
    comment: str = "",
) -> dict:
    """Mark an issue as resolved (PUT /issues/<id>/ status=resolved).
    Mutating. Scope: admin.
    """
```

### Module 2 — `eas_tools`

- **Backend**: `eas-cli` (Expo Application Services), wrapped via `subprocess.run([...])` with `EXPO_TOKEN` injected into the child environment only (never in argv).
- **Keychain token**: `security -s ratis-agent-mcp -a eas` → `EXPO_TOKEN` (Expo personal access token, publish scope).
- **Hard-wired lessons learned**: KP-57 (always `--environment` matching `--channel`), KP-32 (channel mismatch APK ↔ OTA), KP-34 (rebuild required when native module added).
- **Exposed tools**:

```python
def eas_update_preview(
    message: str,
    environment: str = "preview",
) -> dict:
    """Push EAS Update on preview channel. Always uses --environment matching
    channel (cf KP-57). Mutating, visible action. Scope: admin.
    """

def eas_update_production(
    message: str,
) -> dict:
    """Push EAS Update on production channel. Pre-publish gate runs
    git fetch + checks HEAD == origin/main inside the MCP, before invoking
    eas-cli. Mutating, visible action. Scope: admin.
    """

def eas_list_updates(
    channel: str = "preview",
    limit: int = 5,
) -> list[dict]:
    """List recent EAS updates on a channel. Read-only. Scope: ops."""

def eas_list_builds(
    platform: str = "android",
    limit: int = 5,
) -> list[dict]:
    """List recent EAS builds for a platform. Read-only. Scope: ops."""

def eas_rollback_to_embedded(
    channel: str,
) -> dict:
    """Roll a channel back to embedded bundle (recovery). Mutating. Scope: admin."""
```

### Module 3 — `github_tools`

- **Backend**: `gh` CLI (already installed on Mac mini) wrapped via `subprocess`; alternative: direct call to `api.github.com/repos/<org>/<repo>`. Trade-off decided in chunk 4.
- **Keychain token**: `security -s ratis-agent-mcp -a github` → `GITHUB_TOKEN` (classic or fine-grained PAT). Minimum scopes: `repo`, `actions:read`, `workflow` (write if re-triggering runs).
- **Exposed tools** (ops/admin differentiated by mutation):

```python
def github_list_prs(
    state: str = "open",
    limit: int = 20,
) -> list[dict]:
    """List PRs on the ratis monorepo. Read-only. Scope: ops."""

def github_get_pr(pr_number: int) -> dict:
    """Get full PR details (title, body, status, checks). Read-only. Scope: ops."""

def github_list_check_runs(pr_number: int) -> list[dict]:
    """List CI check runs for a given PR. Read-only. Scope: ops."""

def github_rerun_failed_checks(pr_number: int) -> dict:
    """Re-run only failed CI check runs on a PR. Mutating. Scope: admin."""

def github_comment_pr(pr_number: int, body: str) -> dict:
    """Post a comment on a PR. Mutating. Scope: admin."""
```

### Module 4 — `notion_tools` · **DEPRECATED 2026-05-31** · removed from code (sunset)

> 🛑 **DEPRECATED — removed 2026-05-31**. See [[ARCH_incident_management]] (LIVRÉ V0) which documents the replacement: **self-hosted GlitchTip** as the central incident system. The CLI wrapper `~/glitchtip/bin/glt` replaces usages of `notion_search` / `notion_get_page` / `notion_create_ticket` / `notion_update_ticket_status`. The Python module `notion_tools.py` + `test_notion_tools.py` have been deleted (PR `chore/notion-sunset`). Keychain token `notion` also deleted. This section is kept as **project archaeology** only — do not attempt to reintroduce these tools without rethinking the "painful manual tokens" doctrine that motivated the sunset (cf `DECISIONS_PENDING.md` § "Doctrine fondamentale : sécurité par isolation").

- **Backend** (legacy V0): Notion REST API v1 (`api.notion.com/v1/`).
- **Keychain token** (legacy V0): `security -s ratis-agent-mcp -a notion` → `NOTION_TOKEN` (integration internal token).
- **Phase C dependency** (legacy V0): these tools were mainly consumed by n8n workflows + support agents when the INCIDENTS Notion DB was in place. V0 useful for manual automation (creating a ticket from Claude).

> **V0 update 2026-05-05 — Claude does NOT write to Notion**: PO decision to adopt a **filesystem queue + n8n consumer** rather than Claude → Notion direct. All sources (Claude, Sentry webhook, CI fail, mobile crash, manual) drop a `.md` into `~/.local/share/ratis/tickets-inbox/`; n8n is the sole Notion writer, which (a) centralizes anti prompt-injection hardening, (b) allows content analysis before write (e.g., a bot-created €500 auto-refund ticket gets quarantined), (c) makes the pipeline resilient. The `notion_tools` wrapper remains code-ready but Claude does not seed the Notion token for V0. The DA-44 whitelist remains relevant for future non-Claude MCP callers (n8n if it prefers MCP over direct HTTP). Full details: `chunk-mcp-followups.md` § 7.
- **Exposed tools**:

```python
def notion_search(query: str, limit: int = 10) -> list[dict]:
    """Search across the Notion workspace. Read-only. Scope: ops."""

def notion_get_page(page_id: str) -> dict:
    """Get a Notion page (properties + blocks). Read-only. Scope: ops."""

def notion_create_ticket(
    database_id: str,
    title: str,
    body: str,
    properties: dict | None = None,
) -> dict:
    """Create a ticket in a Notion database (typically the INCIDENTS DB).
    Mutating. Scope: ops (whitelisted writes, see DA-44).
    """

def notion_update_ticket_status(page_id: str, status: str) -> dict:
    """Update a ticket status property. Mutating. Scope: ops (whitelisted)."""
```

### Module 5 — `stripe_tools`

- **Backend**: Stripe API v2024 (Python SDK `stripe` or direct `httpx`).
- **Keychain token**: `security -s ratis-agent-mcp -a stripe` → `STRIPE_API_KEY` (secret key, **live mode** only in V1; in V0 pre-launch, test mode sk_test_...).
- **Default admin scope**: Stripe touches payments. No write ops without user validation. Read restricted to ops.
- **Exposed tools**:

```python
def stripe_list_customers(
    limit: int = 10,
    email: str | None = None,
) -> list[dict]:
    """List Stripe customers (filtered by email if provided). Read-only.
    Scope: ops.
    """

def stripe_get_subscription(subscription_id: str) -> dict:
    """Get subscription details. Read-only. Scope: ops."""

def stripe_list_recent_charges(limit: int = 20) -> list[dict]:
    """List most recent charges (debugging cashback flow). Read-only.
    Scope: ops.
    """

def stripe_refund_charge(
    charge_id: str,
    amount_cents: int | None = None,
    reason: str = "requested_by_customer",
) -> dict:
    """Issue a refund. Mutating, irreversible side-effect. Scope: admin."""
```

### Module 6 — `r2_tools`

- **Backend**: Cloudflare R2 via S3-compatible API (`boto3` or `botocore`), endpoint `<accountid>.r2.cloudflarestorage.com`.
- **Keychain tokens**: `security -s ratis-agent-mcp -a r2-access-key-id` + `security -s ratis-agent-mcp -a r2-secret-access-key`.
- **Relevant for receipt debugging**: R2 stores receipt images for 48h (cf [[ARCH_RATIS]] § RGPD); being able to list + download an object for OCR diagnosis without exposing credentials is useful.
- **Exposed tools**:

```python
def r2_list_objects(prefix: str = "", limit: int = 50) -> list[dict]:
    """List objects in the R2 bucket (key, size, last_modified). Read-only.
    Scope: ops.
    """

def r2_get_object_url(key: str, ttl_seconds: int = 600) -> dict:
    """Generate a presigned URL valid for ttl_seconds. Read-only on bucket,
    but generates a URL that exposes the object contents — handle with care.
    Scope: admin.
    """

def r2_delete_object(key: str) -> dict:
    """Delete a specific object. Mutating. Scope: admin."""
```

### Module 7 — `runa_tools`

- **Backend**: Runa API (gift-card provider, V1 post-KYB).
- **Keychain token**: `security -s ratis-agent-mcp -a runa` → `RUNA_API_KEY` (to provision once KYB is validated).
- **Status**: V1 module, **scope to be finalized** when the Runa integration starts on the `ratis_rewards` side.
- **Exposed tools (provisional)**:

```python
def runa_list_brands(country: str = "FR") -> list[dict]:
    """List available gift-card brands for a country. Read-only. Scope: ops."""

def runa_get_order(order_id: str) -> dict:
    """Get gift-card order status. Read-only. Scope: ops."""

def runa_resend_email(order_id: str) -> dict:
    """Re-send the gift-card delivery email. Mutating. Scope: admin."""
```

### Module 8 — `db_tools`

- **Backend**: Ratis internal Postgres — database `ratis_dev` (local, Mac mini) or `ratis_prod` (Hetzner). **Not a third-party provider**: this module extends agent-mcp's scope to Ratis internal infra (cf. `DECISIONS_ACTED.md`, 2026-05-17).
- **Transport**: `psql` launched via `docker exec -i ratis-postgres-1` (dev) or `ssh ratis-prod docker exec -i …` (prod). SQL passed via **stdin** — never in an argv or a shell, so no injection surface. No exposed Postgres port, no `psycopg`.
- **No Keychain entry**: intentional. psql connects as a trusted local user inside the container (no password); the prod hop uses the SSH key `ratis-prod` already configured in `~/.ssh/config`.
- **Read-only guarantee**: libpq options force `default_transaction_read_only=on` + `statement_timeout` at connection. Any write (INSERT/UPDATE/DELETE/DDL) is rejected **by Postgres** — the guarantee comes from the database, not from Python code.
- **Exposed tools**:

```python
def db_query(sql: str, env: str = "dev") -> dict:
    """Run a read-only SQL query against the dev or prod database.
    Read-only. Scope: ops. Result capped at MAX_ROWS (200) rows.
    """


def db_propose_write(mode, procedure, args, checks, rationale,
                     new_procedure_sql="", break_glass=False,
                     client_message="", investigation="") -> dict:
    """Submit a DB write proposal to the approval pipeline. Never executes.
    Scope: ops. La proposition est signée HMAC-SHA256 et POSTée vers le
    webhook n8n `db-write-pipeline`. Bloquant sur les étapes machine
    (dry-run + invariants + revue LLM 2 passes ≈ 2-5 min) : retourne à
    l'agent vivant un verdict `pending_human_approval`, ou `rejected` +
    feedback structuré (stage `invariants` ou `llm_review`) pour qu'il
    corrige et re-soumette. L'agent propose — il n'écrit jamais.
    """
```

**SP6 — support context**: `db_propose_write` carries two additional
optional parameters — `client_message` (the raw client message that
triggered the case) and `investigation` (the agent's investigation note:
what it checked, why this write fixes the problem). Distinct from
`rationale` (the short title/why line). Both are packaged into the
payload and displayed prominently on the detail view of the approval UI
`/admin/ui/db-approvals`.

- **V1 (in progress)**: internal write approval pipeline. `db_propose_write` (SP4) is its entry point on the agent-mcp side — it submits a proposal to the n8n workflow `db-write-pipeline` (dry-run sandbox → invariants → LLM/approval gates → execution). Until V1 (SP4+SP5+SP6) is complete, prod pipeline execution remains feature-flagged OFF.

> **Extended scope** — historically agent-mcp was "providers only". Since the `db` module, agent-mcp also covers Ratis internal infra (DB read-only), beyond external providers. Vision: the MCP eventually becomes the single channel for sensitive actions, including internal ones.

### Module 9 — `docs_tools` (phases C + D agentic-docs)

- **Backend**: pipe-separated parser of `ARCH_INVENTORY.md` at the repo root (the R41 convention). Phase C = pure in-memory grep with 60s TTL cache. **Phase D (LIVRÉ)** = hybrid **vector (bge-m3 + sqlite-vec) + keyword** backend with fusion rerank `0.7·vector + 0.3·keyword` — the public surface is identical, the swap is transparent to agents.
- **Vector backend — details**:
  - Default embedder: `BAAI/bge-m3` (sentence-transformers, ~600 MB, multilingual, top FR/EN). Automatic fallback to `paraphrase-multilingual-MiniLM-L12-v2` (~120 MB) if the heavy model fails to load. If `sentence-transformers` is not installed → silent **keyword-only fallback** (R33 graceful degradation).
  - Storage: SQLite + `sqlite-vec` extension in `<repo>/.docs-vector-index.db` (gitignored). Table `doc_embeddings(id, status, file_path, line, tags, tldr, embedding BLOB, dim)`. Meta: `indexed_at`, `inventory_mtime`, `model_name`.
  - Cosine similarity in Python (numpy) over ~80 vectors = trivial (<200 ms). No `vec0` virtual table at this scale — easy switch if corpus exceeds a few thousand entries.
  - Freshness: `is_fresh()` compares `inventory_mtime` (SQLite meta) vs `ARCH_INVENTORY.md.stat().st_mtime`. Stale → keyword fallback + log warning.
  - Build: `docs_reindex(force=False)` MCP tool OR `scripts/build-docs-vector.py` (standalone CLI, used by the `.claude/settings.json` session-start hook with `--skip-if-fresh --silent`).
- **Why typed**: R29 forbids agents from full-reading large docs. Rather than asking Claude to grep+offset+limit manually, the MCP encapsulates that discipline in 4 typed calls — Claude asks for `HSP-3` and gets the bounded section, without knowing the path or line ranges.
- **No Keychain entry**: everything is local repo reading, no external provider. Like `db_tools`, the module extends agent-mcp to internal infra (documentation is infrastructure).
- **Path resolution**: `ARCH_INVENTORY.md` is resolved from the repo root (5 parents above `docs_index.py`). Override via env var `RATIS_DOCS_INVENTORY_PATH` (used by tests).
- **Exposed tools** (all `ops`-scoped, read-only):

```python
def docs_search(query: str, top_k: int = 5) -> list[dict]:
    """Hybrid (vector + keyword) search over ARCH_INVENTORY. Read-only. Scope: ops.
    Natural language OK ; fallback keyword-only si l'index vector est
    absent/stale ou aucun embedder n'est installé."""


def docs_get(id: str) -> dict:
    """Get full body of one section by ID (e.g. DA-11, HSP-3, ARCH_AUTH).
    Read-only. Scope: ops. Slice de `## ID` au prochain `## ` (ou EOF
    pour les entrées file-level H1 legacy)."""


def docs_find(status: str | None = None,
              tags: list[str] | None = None,
              file_glob: str | None = None) -> list[dict]:
    """Filtre structuré AND-combiné. Read-only. Scope: ops.
    `status` = substring, `tags` = intersection, `file_glob` = fnmatch."""


def docs_list_files() -> list[dict]:
    """Liste les fichiers indexés avec catégorie + nb d'entries.
    Read-only. Scope: ops. Catégories : arch, known, decisions,
    service-arch, batch-arch, client-arch, audit, product, other."""


def docs_reindex(force: bool = False) -> dict:
    """(Re)build the vector index. Read-only (ops scope) — l'index est
    un artefact local, pas un side-effect sur la doc. Skip auto si l'index
    est frais (`inventory_mtime` ≥ stored). Retourne entries_indexed,
    skipped, indexed_at, model_name."""
```

- **Typical use cases**:
  - `docs_search("db-pipeline")` → HSP-1..5, DA-11/14/15/16 + scores (exact-tag keyword).
  - `docs_search("problème commit-per-row R2 DB worker crash")` → semantic matches on entries whose TL;DR/tags cover crash-safety / R2 / worker, without needing to know exact keywords (phase D).
  - `docs_get("HSP-3")` → full body of the section without needing to know `ARCH_n8n_pipelines.md:392`.
  - `docs_find(status="EN-COURS")` → all active workstreams.
  - `docs_find(tags=["db-pipeline"])` → everything touching the db-pipeline.
  - `docs_list_files()` → initial reconnaissance of the doc structure.
  - `docs_reindex()` → after a large doc refactor, forces a rebuild; otherwise the session-start hook does it automatically.

#### Session-context bridge (phase E agentic-docs — LIVRÉ 2026-06-01)

M9 extension that transforms the "ARCH only" index into a **multi-source corpus** and exposes it to the Claude Code `SessionStart` hook. Three deltas:

1. **Multi-source corpus** (cf `docs_index.IndexSource` + `load_corpus()`). Default sources:
   - `arch_inventory` → `ARCH_INVENTORY.md` (existing, unchanged)
   - `decisions_acted` → `docs/decisions/DECISIONS_ACTED.md`
   - `decisions_pending` → `docs/decisions/DECISIONS_PENDING.md` (gitignored, local-only)
   - `known_problems` → `docs/known/KNOWN_PROBLEMS.md` (sections `## KP-N`)
   - `postmortems` → `~/.claude/postmortems/*.md` (date extracted from filename)
   - `skills_active` → `.claude/skills/*/SKILL.md` (parsed YAML frontmatter)
   - `skill_candidates_reviewed` → `.claude/skill-candidates/*/SKILL.md` (filter `status: reviewed`)
   - `user_memory` → `~/.claude/projects/-Users-guillaume-Cursor-Ratis/memory/MEMORY.md` (1 entry/bullet + 1 index entry)

   Each parser emits typed `Entry` objects enriched with `source: str` and `indexed_at: str | None`. The 60s TTL cache also applies to `load_corpus()`.

2. **Filter metadata on `docs_search`** — new optional parameters:
   - `sources: list[str] | None` (subset of names)
   - `file_pattern: str | None` (fnmatch glob on `file_path`)
   - `freshness_days: int | None` (drop if `indexed_at` < cutoff; timeless entries `indexed_at=None` always kept)
   - `status_filter: str | None` (case-insensitive substring)

   When a filter is set, `docs_search` routes to `load_corpus()` + keyword backend (vector index covers `ARCH_INVENTORY` only in V1; extending to multi-source = V2). Otherwise, unchanged hybrid behavior (R33 strict backward compat).

3. **MCP tool `docs_context_for_session(cwd, branch, user_message, limit)`** — wrapper that:
   - tokenizes cwd + branch components (+ user_message if provided) into a query,
   - calls `docs_search` with `freshness_days=30` by default,
   - returns `{query_inferred, nuggets, indexed_at, sources_searched}`.

   Exposed as MCP (scope `ops`, read-only) AND directly importable by the bash hook via `scripts/hooks/inject-session-context.py`.

4. **`SessionStart` hook** — `.claude/hooks/inject-session-context.sh` + Python wrapper `scripts/hooks/inject-session-context.py`. The bash invokes the wrapper via `uv run --package ratis-agent-mcp python` (fallback to system `python3` if `uv` is absent). Markdown output is prepended to the session context by Claude Code. Errors are silent (HTML comment) — R33 graceful: a broken hook must NEVER block the session.

   Smoke test: from the repo, `echo '{"cwd":"<path>"}' | .claude/hooks/inject-session-context.sh` → 5-nugget Markdown block in ~0.2s (warm).

**Out of scope V1** — Honcho dialectical memory: deferred to V2, note in `docs/decisions/DECISIONS_PENDING.md` when starting. Re-indexation cron: V0 = on-demand only (corpus is small, `load_corpus` 60s cache suffices).

#### Skill consumer — `notion-export` (phase G agentic-docs)

On-demand skill `.claude/skills/notion-export/` that consumes `docs_*` (canonical read) and the global `notion-mcp` MCP (`notion-search` / `notion-fetch` / `notion-create-pages` / `notion-update-page`) to mirror the docs as a **decision-maker version** in Notion. Root page "État du projet Ratis" + 5 category sub-pages (Architecture / Decisions / Sub-projects / Audits / Known) + one card per entity (HSP / DA / M / ARCH) reformulated in 5 sections (what / who / when / why / how it changes). Idempotent via sentinel `<!-- ratis-export:<id> -->` on the first body line (Layout A, no backing DB). No cron — operator-triggered only. `dry-run` mode to validate without touching Notion. Full documentation: `SKILL.md` + 3 templates + tech→business dictionary in `helpers/prompt-fragments.md`.

## Architecture & topology

```
┌────────────────── Mac mini M4 Pro (host) ──────────────────────────────┐
│                                                                        │
│   Claude Code (orchestrator + SAs dispatched)                          │
│        │ stdio MCP protocol (newline-delimited JSON-RPC)               │
│        │ wired via ~/.claude/mcp.json                                  │
│        ▼                                                               │
│   ┌─ ratis-agent-mcp (Python, stdio mode) ───────────────────────────┐ │
│   │                                                                  │ │
│   │  ┌─ server.py — MCP SDK runtime ──────────────────────────────┐  │ │
│   │  │  on tools/list  → enumerate tools via decorators           │  │ │
│   │  │  on tools/call  → auth-gate → router → audit-log → dispatch│  │ │
│   │  └────────────────────────────────────────────────────────────┘  │ │
│   │                                                                  │ │
│   │  ┌─ auth.py ──────────────┐  ┌─ keychain.py ────────────────┐    │ │
│   │  │  caller token →        │  │  read-only wrap of           │    │ │
│   │  │    {admin|ops|reject}  │  │  `security find-generic-     │    │ │
│   │  │  per-tool scope check  │  │   password -s ratis-agent-   │    │ │
│   │  │                        │  │   mcp -a <provider> -w`      │    │ │
│   │  └────────────────────────┘  └──────────────────────────────┘    │ │
│   │                                                                  │ │
│   │  ┌─ audit.py ─────────────┐  ┌─ tools/<provider>.py × 7 ─────┐    │ │
│   │  │  append-only writer to │  │  glitchtip_tools.py          │    │ │
│   │  │  ~/.local/state/ratis- │  │  eas_tools.py                │    │ │
│   │  │  agent-mcp/audit.log   │  │  github_tools.py             │    │ │
│   │  │  format JSONL          │  │  notion_tools.py             │    │ │
│   │  │  fields: ts caller     │  │  stripe_tools.py             │    │ │
│   │  │  tool args(redacted)   │  │  r2_tools.py                 │    │ │
│   │  │  status latency        │  │  runa_tools.py               │    │ │
│   │  └────────────────────────┘  └──────────────────────────────┘    │ │
│   └──────────────┬───────────────────────────────────────────────────┘ │
│                  │ outbound HTTPS / subprocess                         │
│                  ▼                                                     │
│            external providers                                          │
│            Sentry · Expo · GitHub · Notion · Stripe · R2 · Runa        │
│                                                                        │
│   ┌─ macOS Keychain (login keychain, encrypted at rest) ──┐            │
│   │  service: ratis-agent-mcp                              │            │
│   │  accounts: sentry · eas · github · notion · stripe ·   │            │
│   │            r2-access-key-id · r2-secret-access-key ·   │            │
│   │            runa                                        │            │
│   └────────────────────────────────────────────────────────┘            │
│                                                                        │
└────────────────────────────────────────────────────────────────────────┘
```

- **Process model**: 1 Python process per Claude Code session (MCP stdio is per-session). Launched by Claude at startup, killed when Claude closes. Not a long-running daemon. No open network port.
- **Communication**: stdin/stdout only (newline-delimited JSON-RPC per MCP spec). `stderr` reserved for local logging.
- **Auth**: a simple `MCP_AUTH_TOKEN` token in `env` of the `~/.claude/mcp.json` config. The MCP compares it internally against the `(MCP_AUTH_ADMIN_TOKEN, MCP_AUTH_OPS_TOKEN)` pair read from its own init env (itself loaded from `~/.config/ratis-agent-mcp/tokens.env`, permissions 600).
- **Audit log**: `~/.local/state/ratis-agent-mcp/audit.log` (XDG state directory). JSONL format. Permissions 600. Phase B ingestion via Loki via Promtail.
- **No persistence** on the MCP side other than the audit log. No DB. Any in-memory caches within the process die with it.

## Key architecture decisions

### DA-43 — Storage backend = macOS Keychain in V0

**Choice**: third-party API tokens stored in the **macOS Keychain** (login keychain) under service name `ratis-agent-mcp`, one account per provider. Access via the built-in `security` CLI.
**Rejected alternatives**: Bitwarden CLI / 1Password CLI (1 external dependency + subscription), HashiCorp Vault / self-hosted Infisical (overkill single-host), encrypted `.env` (too easily left in plaintext in RAM).
**Reason**: (a) **zero infra** — `security` is macOS built-in, already encrypted at-rest by the OS; (b) **0 new dependency** — no service to provision; (c) **trivial future migration**: we ensure `keychain.py` exposes a `get_secret(account: str) -> str` interface that other backends can implement (Bitwarden, 1Password, AWS Secrets Manager). V0 ships in a few hours, pivotable later.

### DA-44 — Auth = 2 distinct MCP tokens (admin + ops)

**Choice**: two tokens in parallel.
- `MCP_AUTH_ADMIN_TOKEN` → full access (human user in interactive sessions).
- `MCP_AUTH_OPS_TOKEN` → **read** scope + whitelisted writes (dispatched Claude agents, n8n automation).

Each tool declares its scope (`Scope: admin` or `Scope: ops` or `Scope: both`). The MCP router rejects the call with `403 forbidden_tool` if the caller token does not match.
**Rejected alternative**: single token shared between human and agents.
**Reason**: (a) **independent revocation**; (b) **principle of least privilege** — an agent doesn't need `stripe_refund_charge`, so not the admin scope; (c) **inverted safety net** — against a hypothetical prompt-injection pushing Claude to `eas_update_production`, the 403 auth blocks before even touching the Keychain.

### DA-45 — Hosting V0 = local Mac mini, stdio MCP

**Choice**: `agent-mcp` runs in **stdio** launched by Claude Code via `~/.claude/mcp.json`. Companion process, scope = the Claude session that launches it. No Docker, no open port, no systemd service.
**Rejected alternative**: Docker HTTP MCP container (e.g. port 8050) network-exposable. Reserved for Phase B+.
**Reason**: (a) **maximum simplicity**; (b) no network auth to protect (stdio = no TCP socket); (c) **per-session scope** matches the Claude Code model (1 agent-mcp = 1 session), audit naturally scoped. Docker/HTTP migration documented in Runbook § Migration scenarios.

### DA-46 — Stack = Python 3.12 + official Anthropic MCP SDK + uv

**Choice**: Python 3.12 (consistency with all Ratis services), official Anthropic MCP SDK (`mcp` PyPI package), managed via uv.
**Rejected alternative**: Node.js / TypeScript MCP SDK, Go.
**Reason**: (a) **Ratis stack consistency** — all our backends, batches, tools are in Python 3.12; (b) **uv workspace** allows sharing `ratis_core` (Pydantic type schemas, client utils) if needed; (c) the Python SDK is as stable as the TS one.

### DA-47 — Scope V0 = comfortable (7 modules, 30+ tools)

**Choice**: ship all 7 modules in the same minor version 1.0, but **implemented in iterative SA chunks** (cf checklist). Sentry + EAS are priority 1 (urgent given the 3 leaks experienced).
**Rejected alternative**: minimalist V0 (Sentry only).
**Reason**: (a) the marginal cost of adding a module is low; (b) we want to avoid the "too lean V0" trap where Claude keeps manipulating tokens in plaintext because the MCP tool doesn't exist yet; (c) a comfortable scope makes the MCP attractive to use from the first session.

### DA-48 — Audit trail = local append-only JSONL file

**Choice**: 1 JSON line per call in `~/.local/state/ratis-agent-mcp/audit.log`, format `{ts, caller, tool, args_redacted, status, latency_ms, error}`. Permissions 600. No rotation V0 (small size: ~500 bytes/line × ~100 calls/day = 50 KB/day).
**Rejected alternatives**: local SQLite (overkill V0), direct Loki push (tight coupling, audit loss if Loki down).
**Reason**: (a) **zero dependency** — append-only file works everywhere, even without network; (b) **JSONL** format trivial to parse by Promtail (Phase B+ auto-ingest into Loki cf [[ARCH_itops]] DA-41); (c) **forensic-friendly**: if someone compromises the MCP and tries to erase lines, the inode changes and is detectable.

### DA-49 — Tool format = typed Python functions (auto-schemagen MCP SDK)

**Choice**: each tool is a Python function with **type hints** + docstring. The MCP SDK auto-generates JSON schemas for `tools/list`. Default type model: Pydantic (Ratis consistency).
**Rejected alternative**: hand-written JSON schemas on the server side.
**Reason**: (a) **DRY** — the Python signature is the source of truth; (b) **build-time errors** — mypy type-checker can validate constraints; (c) **Ratis consistency** — all our FastAPI services use Pydantic v2.

### DA-50 — MCP versioning = semantic, bumped per module added

**Choice**: SemVer versioning.
- `1.0.x` = comfortable V0 scope (7 modules).
- `1.1.0` = adding an 8th module or a cross-module feature.
- `2.0.0` reserved for breaking changes (tool rename, scope rework, major MCP protocol change).

Each release tagged + special `mcp_version_bump` audit-log line at each startup.
**Reason**: SemVer is expected by agents (Claude Code can display the version in the connected MCPs list, n8n can condition its workflows on it). Defuses the problem of a consumer silently breaking when a module is added or a tool renamed.

### DA-51 — MCP token rotation = manual on-demand

**Choice**: no automatic rotation of admin/ops MCP tokens. No fixed duration. **Re-roll only on trigger**:
- suspected compromise;
- after each PR that modifies the list of admin tools (defensive audit).

The **third-party API tokens** (Sentry, EAS, …) remain managed by their respective providers. The MCP only **reads** them from Keychain — we rotate them on the provider side + `agent-mcp keychain set <provider> <new>` to update locally.
**Rejected alternative**: automatic monthly rotation.
**Reason**: (a) **friction** — automatic rotation of admin/ops tokens breaks existing Claude sessions without warning; (b) **scale** — V0 = 1 user (human) + N transient agents = auto-rotation solves a scale problem we don't have; (c) **reactive rotation >> calendar rotation** for our risk profile.

### DA-52 — Distribution = uv workspace member in ratis-monorepo

**Choice**: `tools/agent-mcp/` is a **workspace member** declared in the root `pyproject.toml`, like `webservices/ratis_auth` or `batch/ratis_batch_consensus`. Source code in `src/agent_mcp/`.
**Rejected alternative**: separate `ratis-agent-mcp` repo in the organization.
**Reason**: (a) **single source of truth** Ratis = monorepo; (b) **code sharing** possible (`ratis_core` Pydantic schemas, settings loader if needed); (c) **onboarding**: a new SA session sees the code immediately without an extra clone.

## Implementation checklist

### Chunk 1 — Foundation (Phase 1, prio 1)

- [x] `tools/agent-mcp/pyproject.toml` (workspace member, deps : `mcp`, `pydantic`, `httpx`)
- [x] `tools/agent-mcp/src/agent_mcp/server.py` : MCP SDK stdio runtime, `tools/list` returns empty list initially, `tools/call` route → auth → audit → dispatch
- [x] `tools/agent-mcp/src/agent_mcp/auth.py` : load tokens from `~/.config/ratis-agent-mcp/tokens.env` at boot, function `check_scope(caller_token, required_scope)`
- [x] `tools/agent-mcp/src/agent_mcp/audit.py` : append-only JSONL writer with lock file (concurrent calls in same session)
- [x] `tools/agent-mcp/src/agent_mcp/keychain.py` : wrapper `subprocess.run(["security","find-generic-password",...])` + 60s in-memory cache
- [x] CLI : `agent-mcp serve` (stdio) · `agent-mcp init` (generates admin/ops tokens + writes `~/.config/ratis-agent-mcp/tokens.env`) · `agent-mcp keychain set|rm` · `agent-mcp tokens rotate --role admin|ops` · `agent-mcp paths` (diagnostics)
- [x] TDD tests: auth gate (admin/ops/wrong + scope hierarchy), audit log format JSONL + concurrent flock + redaction, keychain mock (stdin-piped, no argv leak), Dispatcher pipeline (auth→registry→scope→audit), CLI round-trips. 64 tests, 100% pass local.
- [x] Workspace member added in root `pyproject.toml`
- [x] PR `feat/agent-mcp-foundation` merged (PR #298, commit `9e4db2f`)

**Notes Chunk 1 (V0 design choices, validated during implementation)**:
- Runtime split into two layers: `Dispatcher` (pure-Python, MCP-SDK-agnostic, testable standalone) and `build_mcp_server()` (MCP SDK glue, lazy-imported). Allows unit-testing without the SDK installed and facilitates future HTTP-MCP migration (DA-45 § Migration scenarios).
- DA-49 validated: `_build_input_schema()` uses `pydantic.create_model` + `model_json_schema()` to auto-generate the JSON Schema for tools from their type hints. Permissive fallback `{type: object, additionalProperties: true}` if pydantic is unavailable (defensive).
- `redact_args()` recursively redacts (depth-1) on keys containing `token|key|secret|password|auth|credential` (case-insensitive regex). Modules can extend with their own per-arg rules without disabling this base sweep.
- `audit.AuditLog.write()` combines `threading.Lock` (intra-process) + `fcntl.flock(LOCK_EX)` (inter-process) to guarantee 0 corruption on parallel SA dispatch.
- `keychain.set()` passes the secret via `subprocess.run(input=value)` (stdin) — NEVER in argv. Test `test_set_does_not_pass_secret_in_argv` enforces this in CI.
- `cli.cmd_init()` refuses to overwrite an existing `tokens.env` — forces the use of `agent-mcp tokens rotate --role X` for atomic single-role rotation.

### Chunk 2 — Sentry tools (Phase 1, prio 1, urgent)

- [x] `tools/agent-mcp/src/agent_mcp/tools/sentry_tools.py` : 4 typed tools (`list_issues`, `get_issue`, `list_events`, `resolve_issue`)
- [x] CLI : `agent-mcp keychain set sentry <token>` + verify (round-trip get) + command `agent-mcp keychain rm <provider>`
- [x] TDD tests: mock Sentry HTTP API via `httpx.MockTransport`, assertions on audit log + scope rejection
- [x] README addition: Sentry usage section + examples
- [x] PR `feat/agent-mcp-sentry-tools` merged
- [x] CI workflow `.github/workflows/agent_mcp.yml` added (lint + pytest on paths `tools/agent-mcp/**`)

### Chunk 3 — EAS tools (Phase 1, prio 2)

- [x] `tools/agent-mcp/src/agent_mcp/tools/eas_tools.py` : 5 tools
- [x] Wrap `eas-cli` via subprocess, `EXPO_TOKEN` injected **in env** (never argv) → DA-43 enforced
- [x] Pre-publish gate in `eas_update_production` : `git fetch && git log -1 origin/main` + check HEAD ratis_client = origin/main HEAD (cf KP-32, KP-26)
- [x] TDD tests: mock subprocess, verify args (no token in argv), verify pre-publish gate refuses if HEAD ≠ origin/main
- [x] PR `feat/agent-mcp-eas-tools` merged

### Chunk 4 — GitHub tools

- [x] `tools/agent-mcp/src/agent_mcp/tools/github_tools.py` : 5 tools
- [x] Trade-off `gh` CLI vs direct HTTP API → choose HTTP API (`api.github.com`) for mock-friendly testability
- [x] Ops vs admin scope differentiated (read = ops, comment/rerun = admin)
- [x] TDD tests
- [x] PR `feat/agent-mcp-github-tools` merged

### Chunk 5 — Notion tools (depends on Phase C)

- [x] `tools/agent-mcp/src/agent_mcp/tools/notion_tools.py` : 4 tools
- [x] Note in the module: these tools consumed in V1 by n8n (Phase C, [[ARCH_itops]] § Phase C)
- [x] Whitelist DBs `RATIS_NOTION_INCIDENT_DBS` (DA-44) — fail-closed if empty; normalized ID comparison (dashes stripped, lowercased)
- [x] TDD tests
- [x] PR `feat/agent-mcp-notion-tools` merged

### Chunk 6 — Stripe tools

- [x] `tools/agent-mcp/src/agent_mcp/tools/stripe_tools.py` : 4 tools, admin by default (except list_customers / get_subscription / list_recent_charges = ops read-only)
- [x] TDD tests with test_mode (sk_test) in V0
- [x] One-shot `live_mode_used` warning in audit log if `sk_live_*` detected at runtime (DA-43, non-blocking)
- [x] PR `feat/agent-mcp-stripe-tools` merged

### Chunk 7 — R2 tools

- [x] `tools/agent-mcp/src/agent_mcp/tools/r2_tools.py` : 3 tools
- [x] Wrap `boto3` (S3-compatible, SigV4 mandatory for R2)
- [x] Endpoint URL stored in Keychain (`r2-endpoint-url`) — embeds Cloudflare account id, sensitive
- [x] TDD tests with `moto[s3]>=5.0`
- [x] PR `feat/agent-mcp-r2-tools` merged

### Chunk 8 — Runa tools (V1, depends on KYB) — DEFERRED

- [x] **Deferred per plan** — gift-card provider integration will start when Runa KYB is validated. Module to be scoped then. This chunk is explicitly skipped in V0.

### Chunk 9 — User documentation + ratis integration

- [x] `tools/agent-mcp/README.md` complete: ToC, intro, install 4-step, daily usage, per-provider reference, audit log, token rotation, troubleshooting, FAQ
- [x] Update `ARCH_agent_mcp.md` § Runbook + § Lessons learned (this section)
- [x] Section added in `CLAUDE.md` § pointers: `agent-mcp` → ARCH_agent_mcp.md + tools/agent-mcp/README.md
- [x] `chunk-mcp-followups.md` consolidated at repo root (V1.5 items)
- [ ] Phase B : Promtail tail on audit log → ingest Loki (cf [[ARCH_itops]] § Phase B/C). **Deferred V1.5 followup**: requires mounting the host path `~/.local/state/ratis-agent-mcp/audit.log` in the Promtail container (`infra/itops/docker-compose.yml`) — cross-stack, to be pair-implemented when adding the audit-log scrape job on the Promtail side. Tracked in `chunk-mcp-followups.md`.
- [x] PR `docs/agent-mcp-readme-and-pointers` opened

### Chunk 10 — `docs_tools` phase D (hybrid vector)

- [x] Deps added to `tools/agent-mcp/pyproject.toml` : `numpy>=1.26`, `sqlite-vec>=0.1.6` (core) ; `sentence-transformers>=2.7` as optional `[vector]` (heavy model, optional for agents that only consume)
- [x] `tools/agent-mcp/src/agent_mcp/tools/docs_vector.py` : `Embedder` protocol, `HashEmbedder` (deterministic 16-D test stub), `SentenceTransformerEmbedder` (bge-m3 + miniLM fallback, lazy-load), `build_or_refresh()`, `search()`, `is_fresh()`, `ReindexResult` Pydantic
- [x] SQLite schema: `doc_embeddings(id PK, status, file_path, line, tags, tldr, embedding BLOB, dim)` + `doc_meta(key, value)` (indexed_at, inventory_mtime, model_name, dim). Path `<repo>/.docs-vector-index.db` (gitignored)
- [x] `docs_tools.docs_search` : new hybrid path (vector top-20 + normalized keyword top-20, fusion `0.7·v + 0.3·k`), keyword-only fallback if index absent/stale/embedder unavailable (R33 graceful)
- [x] New tool `docs_reindex(force: bool = False)` (ops-scoped) — MCP wrapper around `build_or_refresh`
- [x] `scripts/build-docs-vector.py` CLI — `--force`, `--skip-if-fresh` (default), `--silent`. Respects `RATIS_DOCS_VECTOR_SKIP=1` (CI)
- [x] Session-start hook in `.claude/settings.json` after `generate-arch-inventory.py` : `uv run --package ratis-agent-mcp python scripts/build-docs-vector.py --skip-if-fresh --silent` (timeout 60 s)
- [x] `.gitignore` : `.docs-vector-index.db` + WAL/SHM journals
- [x] TDD tests: 13 tests `test_docs_vector.py` (build idempotence, freshness, fallback) + 8 tests hybrid path in `test_docs_tools.py`. CI mock embedder via `HashEmbedder` → 0 download
- [x] Update `ARCH_agent_mcp.md` § Module 9 (this section)
- [ ] **Optional V1.5** : also index the `## KP-N` sub-sections of `KNOWN_PROBLEMS.md` (and other files with regular H2 structure) so `docs_search` returns specific KPs directly instead of the file-level entry. Requires extension of the inventory parser (out of scope phase D — current inventory = "1 entry per conventioned H2 section OR 1 file-level entry")

### Chunk 10.5 — Session-context bridge (phase E agentic-docs — LIVRÉ 2026-06-01)

- [x] `docs_index.py` : added `IndexSource` dataclass, `parse_decisions`, `parse_known_problems`, `parse_postmortem`, `parse_skill`, `parse_skill_filter_reviewed`, `parse_user_memory`, `load_corpus()` with TTL 60s cache. `Entry` extended with `source: str = "arch_inventory"` + `indexed_at: str | None = None` (strict backward compat)
- [x] `docs_tools.docs_search` : 4 new optional params (`sources`, `file_pattern`, `freshness_days`, `status_filter`). Routes to multi-source corpus + keyword backend when at least one filter is set; hybrid behavior unchanged otherwise (R33)
- [x] New tool `docs_context_for_session(cwd, branch, user_message, limit)` (ops-scoped) — query inference (tokenize cwd + branch + user_message, stopwords list, dedup), returns `{query_inferred, nuggets, indexed_at, sources_searched}`. Filter `freshness_days=30` by default
- [x] `.claude/hooks/inject-session-context.sh` + `scripts/hooks/inject-session-context.py` : bash hook + in-process Python wrapper (no `agent-mcp call` → no `MCP_AUTH_TOKEN` round-trip for this local read). Graceful fallback (HTML comment) on any error. Conditional timeout wrapper (`timeout` / `gtimeout` / none), Claude Code also applies its own 15s timeout via `settings.json`
- [x] `.claude/settings.json` SessionStart: new hook between `build-docs-vector.py` and `cat ORCHESTRATOR.md`
- [x] TDD tests: 23 new tests `test_docs_session_bridge.py` (parsers, load_corpus, filters, context_for_session) + 4 tests `test_session_context_hook.py` (smoke subprocess on the Python wrapper). No existing M9 test broken (62 baseline → 62 + 27 = 89 in scope)
- [x] Update `ARCH_agent_mcp.md` § Module 9 sub-section Session-context bridge (this section)
- [ ] **V2** : Honcho dialectical memory (bidirectional session → corpus learning) — deferred to `docs/decisions/DECISIONS_PENDING.md` when starting
- [ ] **V2** : vector indexing of the multi-source corpus (V1 = vector ARCH_INVENTORY only, keyword for other sources). Requires SQLite schema extension + re-embedding on each rebuild

### Chunk 11 — `notion-export` skill (phase G agentic-docs)

- [x] `.claude/skills/notion-export/SKILL.md` : on-demand skill (no cron). Pre-flight, root + 5 Notion categories, scan `docs_find`/`docs_get`, 5-section reformulation, write via notion-mcp, recap. `dry-run` mode. Layout A (sentinel `<!-- ratis-export:<id> -->` on 1st body line) — Layout B (backing DB) planned V1.5.
- [x] `templates/overview-page.md` + `templates/category-page.md` + `templates/entity-page.md` : 3 markdown templates with typed placeholders
- [x] `helpers/prompt-fragments.md` : 5-section prompt-skeleton + tech→business dictionary (JWT→jeton, HMAC→empreinte, …) + 3 before/after examples (DA-43, HSP-3, ARCH_AUTH)
- [x] Pointer in Module 9 § « Skill consumer — `notion-export` » (above)
- [ ] **V1.5 optional** : Layout B with `notion-create-database` (backing DB + `External ID` property), for cleaner Notion queries long-term
- [ ] **V1.5 optional** : one page per KP (already tracked § Chunk 10 last item — depends on inventory parser extension)

## Runbook

### Setup initial (1-time)

```bash
# 1. Sync workspace (depuis racine repo)
uv sync --package ratis-agent-mcp

# 2. Init tokens MCP admin + ops + dossier config
uv run agent-mcp init
# → écrit ~/.config/ratis-agent-mcp/tokens.env (chmod 600)
#   contenu : MCP_AUTH_ADMIN_TOKEN=... / MCP_AUTH_OPS_TOKEN=...
# → affiche les 2 tokens UNE SEULE FOIS pour copy-paste dans Claude config

# 3. Seed Keychain avec les API tokens (1 commande par provider)
uv run agent-mcp keychain set sentry      # prompt interactif (no-echo)
uv run agent-mcp keychain set eas
uv run agent-mcp keychain set github
uv run agent-mcp keychain set notion
uv run agent-mcp keychain set stripe
uv run agent-mcp keychain set r2-access-key-id
uv run agent-mcp keychain set r2-secret-access-key
# uv run agent-mcp keychain set runa  # quand KYB validé

# 4. Wire Claude Code config — éditer ~/.claude/mcp.json
```

```jsonc
{
  "mcpServers": {
    "ratis-agent-mcp": {
      "command": "uv",
      "args": ["run", "--package", "ratis-agent-mcp",
               "agent-mcp", "serve"],
      "env": {
        "MCP_AUTH_TOKEN": "<paste OPS token here for SA dispatching, or ADMIN for interactive sessions>"
      },
      "cwd": "<absolute path to ratis-monorepo>"
    }
  }
}
```

### Daily usage

- **In Claude Code**: tools appear in the MCP palette at the next Claude startup. Claude calls `glitchtip_list_issues(...)` like any other tool. No token manipulation on the Claude side.
- **Live audit log reading**:
  ```bash
  tail -f ~/.local/state/ratis-agent-mcp/audit.log | jq .
  ```
- **If a call fails**: the first log line indicates the `status` (`forbidden_tool` = scope mismatch; `keychain_miss` = token absent; `provider_error` = provider call failed + message; `audit_error` = audit write failed, fallback to stderr).

### Updating an API token (provider rotation)

When regenerating a GlitchTip (or Expo, or other) token on the provider side, update Keychain:
```bash
uv run agent-mcp keychain set admin-glitchtip
# prompt: (paste new token, no echo, validation round-trip via glitchtip_list_issues on test project)
```

### MCP admin/ops token rotation

```bash
uv run agent-mcp tokens rotate --role admin
# → génère un nouveau MCP_AUTH_ADMIN_TOKEN, réécrit ~/.config/ratis-agent-mcp/tokens.env
# → écrit aussi une ligne audit-log spéciale "token_rotated:admin"
# → invalide l'ancien immédiatement (à update dans ~/.claude/mcp.json)

uv run agent-mcp tokens rotate --role ops
# idem pour ops
```

### Keychain inspection (admin only)

```bash
# List accounts for the Keychain service
security find-generic-password -s ratis-agent-mcp 2>&1 | grep '"acct"'

# View a token (rare, debug only, macOS sudo prompt)
security find-generic-password -s ratis-agent-mcp -a sentry -w

# Delete a token
uv run agent-mcp keychain rm sentry
```

### Troubleshooting

- **MCP server doesn't start in Claude Code**: check `~/.claude/mcp.json` for valid JSON syntax, correct `cwd`, `uv` in the PATH of the Claude Code launcher. Claude Code logs: Settings → MCP → view stderr.
- **Tool returns 401/403 from provider**: Keychain token invalid or expired. `uv run agent-mcp keychain set <provider>` to update.
- **Tool returns `forbidden_tool`**: scope mismatch. The MCP token in use is `ops` but the tool requires `admin`. Switch to the ADMIN token in `~/.claude/mcp.json` (restart Claude after).
- **Audit log not writing**: check permissions on `~/.local/state/ratis-agent-mcp/` (auto-mkdir by MCP at boot; if failing, look at the process `stderr`).
- **EAS update silent no-op**: channel mismatch (cf KP-32). The MCP cannot detect this automatically in V0 — check via `eas_list_builds(platform="android", limit=1)` → `channel` field must match the targeted channel.

### Migration scenarios

- **Mac mini → Hetzner / AWS (host change)**: export `~/.config/ratis-agent-mcp/tokens.env` + Keychain dump via `security export -k login.keychain-db -t certs -f pemseq -o backup.p12` (incl. generic passwords). Re-import on new host.
  **But**: if the OS changes (Linux), Keychain is no longer available → switch to Bitwarden CLI backend. This is precisely the reason for the `keychain.py` interface (cf DA-43) — a second implementation suffices.
- **Multi-host mode (n8n + multiple devs)**: switch to HTTP MCP Docker mode. `agent-mcp serve --http --port 8050` + Bearer auth. Containerize the image, deploy on Mac mini side with Tailscale-only exposure. The code remains mostly the same (MCP SDK supports both transports). Audit log then mounted on a volume + Promtail tail.

### Lessons learned during chunks 2-7 implementation

Notes consolidated during module implementation (post-Foundation, pre-prod-usage). Updated with real usage feedback as it comes in.

- **Chunk 2 (Sentry)** — The Sentry Python SDK is bulky (incl. BackgroundWorker transport, threading); we skip it entirely and hit `api.sentry.io/api/0/` directly via `httpx.AsyncClient` mocked by `httpx.MockTransport` in tests. Decision validated: zero overhead, hermetic tests, simpler provider swap if needed.
- **Chunk 3 (EAS)** — The pre-publish gate (HEAD vs origin/main) is implemented **in the Python wrapper**, not in `eas-cli`. Reason: we want to refuse BEFORE spawning the subprocess (zero flakiness on `eas update --dry-run` which doesn't reproduce the human contract "publish only from merged main"). KP-57 (`--environment` matching `--channel`) is hard-wired via signature values: `eas_update_production` does not allow overriding `environment`, compile-time guarantee.
- **Chunk 4 (GitHub)** — Tradeoff `gh` CLI vs HTTP API decided in favor of direct HTTP (`api.github.com` via `httpx`). Reason: zero external dependency (no need for `gh` installed on the MCP host), trivially mockable tests via `MockTransport`, stable REST contract. Minor downside: 2 round-trips needed for `github_list_check_runs` / `github_rerun_failed_checks` (PR → head sha → check-runs) — acceptable since called on agent demand, not in a loop.
- **Chunk 5 (Notion)** — The `RATIS_NOTION_INCIDENT_DBS` whitelist must compare UUIDs **normalized** (dashes stripped + lowercased): Notion accepts both `abc-123-...` and `abc123...` formats interchangeably in its payloads. A raw string comparison would pass one format and block the other. `notion_update_ticket_status` must GET the page first (to resolve `parent.database_id`) BEFORE checking the whitelist — extra round-trip but more secure.
- **Chunk 6 (Stripe)** — Stripe REST expects `application/x-www-form-urlencoded` bodies (NOT JSON), a classic mistake for those going from the SDK to raw `httpx`. `httpx`'s `data=` kwarg sets the correct Content-Type automatically. The one-shot `live_mode_used` audit-log warning (on `sk_live_*` detected at runtime) is **non-blocking** but visible — the least intrusive safety net for V0 where we're supposed to be on `sk_test_*` exclusively.
- **Chunk 7 (R2)** — The **endpoint URL** is stored in the Keychain (`r2-endpoint-url`) and not in an env var, because it embeds the Cloudflare account-id (`https://<account_id>.r2.cloudflarestorage.com`) — combined with stolen credentials, it facilitates enumeration. R2 forces SigV4 (rejects SigV2) and limits `MaxKeys` to 1000 per page → we clamp on the code side, V1.5 pagination if real need arises. `r2_get_object_url` is admin-scoped because the returned presigned URL exposes the object contents (receipts = RGPD) — the returned value is **never** logged in the audit JSONL (the dispatcher logs args + status, not the return value).
- **CI infrastructure** — The `.github/workflows/agent_mcp.yml` workflow (added in chunk 2) runs on `self-hosted` runners (Mac mini M4 Pro, post-migration PR #287 from 2026-05-04 — details [[ARCH_itops]] § Topology), filters on paths `tools/agent-mcp/**`, runs lint (ruff) + tests (pytest). 216 tests at the end of chunk 7, ~2s execution on a warm runner. Coverage rate not measured (Codecov not wired for `tools/`) — listed as V1.5 followup.
- **Chunk 8 (Runa) — DEFERRED** — V1 gift-card module post-KYB. No code written in V0. When the Runa integration starts on the `ratis_rewards` side, we'll dispatch a new dedicated SA chunk. ARCH § Module 7 describes the provisional scope (`runa_list_brands`, `runa_get_order`, `runa_resend_email`) — to confirm/refine at that time.

## Out of scope

- **n8n + Notion DB INCIDENTS** (Phase C, [[ARCH_itops]] § Phase C): will consume the MCP tools but has its own dedicated ARCH.
- **Audit forensic UI / dashboard** (Phase D, optional): in V1 we read the file or Loki. Dedicated UI if the need emerges.
- **MCP multi-tenancy**: 1 MCP per project, not multi-project. If another project starts (post-Ratis), it's a separate `agent-mcp` deployment with its own Keychain service name (`other-agent-mcp`).
- **Auto-rotation tokens** (DA-51 explicit: not implemented in V0).
- **Non-macOS secret backends**: Linux/WSL requires Bitwarden CLI or equivalent. Out of scope V0.
- **Sensitive Sentry/Stripe write tools**: no `sentry_delete_project`, no `stripe_create_subscription`, no `stripe_cancel_subscription` in V0. These operations remain UI-driven by the human.

## Things to know (vectorized FAQ)

### Why agent-mcp and not just 1Password CLI?

1Password CLI solves **only one** of the three problems (encrypted storage). It does **not** solve:
- the centralized audit trail (who called what when);
- the admin/ops scope separation per caller;
- the "business function" abstraction that prevents the model from directly manipulating the token (with 1Password CLI, Claude still has to do `op read 'op://...sentry-token'` then `curl -H "Authorization: Bearer $token" ...` — the token transits through context).

`agent-mcp` is an **abstraction layer** that internalizes these 3 concerns. 1Password CLI **could** be an alternative backend to the Keychain (cf `keychain.py` interface DA-43), but is not sufficient on its own.

### How to debug a failing MCP call?

3 info sources, in this order:
1. **Tool call response** — Claude sees the `error` field in the returned JSON. Format `{status: "...", error: "..."}` standardized by the MCP.
2. **Audit log**: `tail -f ~/.local/state/ratis-agent-mcp/audit.log | jq 'select(.status != "ok")'` to see only failures.
3. **MCP process stderr** — Claude Code exposes stderr in Settings → MCP → ratis-agent-mcp → Logs.

### What happens if the Mac mini reboots?

The MCP is NOT a long-running daemon — it is launched by Claude Code each session via stdio. On reboot:
- Keychain remains intact (it's the macOS store, persistent).
- MCP admin/ops tokens in `~/.config/ratis-agent-mcp/tokens.env` intact.
- Audit log intact (file on disk).
- On next Claude Code startup, the MCP restarts transparently via `~/.claude/mcp.json`.

No manual action required on reboot.

### What prevents a compromised Claude (prompt injection, malicious tool result) from taking admin actions?

3 defense layers:
1. **Separate token** (DA-44): Claude is launched by default with the OPS token, **not admin**. All admin tools (`glitchtip_resolve_issue`, `eas_update_production`, `stripe_refund_charge`, …) return `forbidden_tool` even if Claude tries to call them.
2. **Ops write whitelist** (DA-44): within the ops scope, only a few writes are permitted (creating a Notion ticket, commenting a GitHub PR…), everything else is read-only.
3. **Audit log** (DA-48): every call is traced, we can audit post-mortem any suspicious behavior.

Note: the **admin token** is only used for interactive sessions where the human validates each sensitive action. If the human leaves their machine with Claude open, the residual risk is limited to the admin scope at that moment — hence DA-51 (reactive rotation on suspicion).

### How to add an 8th tool module later?

Standard procedure, 1 SA chunk:
1. Create `tools/agent-mcp/src/agent_mcp/tools/<provider>_tools.py` with typed functions (Pydantic compatible).
2. Decorate each function with `@register_tool(scope="ops"|"admin")` exposed by `server.py`.
3. Add Keychain account: `uv run agent-mcp keychain set <provider>`.
4. TDD tests with a mock of the provider HTTP.
5. Bump minor version (`1.N` → `1.N+1`) in `pyproject.toml`.
6. PR.

No modification of the runtime (`server.py`) in theory — the auto-discovery registration mechanism finds new modules. If a runtime refactor is necessary (rare case), that's `2.0.0` (DA-50).

### Why not one MCP per provider (1 MCP Sentry, 1 MCP EAS, …)?

Choice to **consolidate**: 1 process, 1 audit log, 1 admin/ops token pair. Advantages:
- smaller surface (1 process to monitor instead of 7);
- unified audit log (trivial cross-provider correlation, e.g.: "which Sentry issue triggered the EAS update?");
- 1 setup, 1 wire in `~/.claude/mcp.json`.

Downside: a bug in the runtime impacts all providers at once. Mitigated by tests + additive scope (a new module doesn't touch the others).

### Does the MCP have access to the Ratis Postgres / Redis DB?

**No, intentionally.** The MCP is restricted to **external third-party services**. To interact with Ratis Postgres/Redis, we go through the `/admin/*` endpoints of Ratis services which have their own `ADMIN_API_KEY` auth. If we want a `ratis_admin_tools` tool later, it will be a new module with its own Keychain token `ADMIN_API_KEY`, strictly admin-scoped.

### How to audit what Claude did in a session?

```bash
# All calls today
jq 'select(.ts >= "2026-05-05")' ~/.local/state/ratis-agent-mcp/audit.log

# Count calls by tool
jq -r '.tool' ~/.local/state/ratis-agent-mcp/audit.log | sort | uniq -c

# Identify who (admin vs ops) did what
jq -r '[.ts,.caller,.tool,.status] | @tsv' ~/.local/state/ratis-agent-mcp/audit.log | column -t
```

Phase B: these queries become Grafana dashboards on Loki (cf [[ARCH_itops]]).

## Glossary

- **MCP (Model Context Protocol)**: Anthropic specification for language models to communicate with tool servers via JSON-RPC. Two transports: **stdio** (the MCP is a sub-process of the client) and **HTTP/SSE** (network). agent-mcp uses stdio in V0 (DA-45).
- **stdio MCP**: MCP transport with newline-delimited JSON-RPC on the MCP process's stdin/stdout. No open network port. Process launched by the client (Claude Code) at session startup.
- **HTTP MCP**: MCP transport via HTTP+SSE for multi-host usage. Out of V0, planned as migration scenario.
- **Keychain (macOS)**: native macOS secret store, encrypted at-rest by the OS. Accessible via the `security` CLI. Service name = label for grouping, account = key under that label. agent-mcp uses service `ratis-agent-mcp` with one account per provider (DA-43).
- **Append-only audit log**: JSONL file where each line is immutable once written. No DELETE/UPDATE. If a call must be functionally undone, a "compensation" line is written rather than modifying the previous one. Forensic-friendly (DA-48).
- **DA-XX**: numbered architecture decision (cf dedicated section).
- **Scope ops**: read permission + whitelisted writes (creating a Notion ticket, commenting a PR…). Token used by dispatched Claude agents and n8n workflows (DA-44).
- **Scope admin**: full permission, including sensitive writes (resolve Sentry, EAS prod update, Stripe refund). Token used by the human in interactive sessions (DA-44).
- **Whitelist writes ops**: subset of ops scope writes, defined tool by tool in each module's code. No wildcard.
