# ratis-agent-mcp

MCP server exposing typed tools to Claude Code agents (GlitchTip, EAS, GitHub, Stripe, R2) without leaking provider tokens to the model. The Notion module was removed on 2026-05-31 (DA-47) — replaced by GlitchTip self-hosted (see [`docs/arch/ARCH_incident_management.md`](../../docs/arch/ARCH_incident_management.md)); the `sentry_tools` module was renamed `glitchtip_tools` as a follow-up to DA-47 (Sentry SaaS → GlitchTip local).

Full architecture and design rationale : [`ARCH_agent_mcp.md`](../../docs/arch/ARCH_agent_mcp.md).

## Table of contents

- [What this is](#what-this-is)
- [Install / first-time setup](#install--first-time-setup)
- [Daily usage](#daily-usage)
- [Per-provider reference](#per-provider-reference)
  - [GlitchTip](#glitchtip-tools)
  - [EAS](#eas-usage)
  - [GitHub](#github-usage)
  - [Notion](#notion-usage)
  - [Stripe](#stripe-usage)
  - [R2](#r2-usage)
  - [db (Postgres read-only)](#module-db--postgres-read-only-access)
- [Audit log](#audit-log)
- [Diagnostics](#diagnostics)
- [Smoke test](#smoke-test)
- [Tests](#tests)
- [Token rotation](#token-rotation)
- [Troubleshooting](#troubleshooting)
- [FAQ](#faq)

## What this is

`ratis-agent-mcp` is a **Model Context Protocol** server (Anthropic spec) that runs locally as a stdio companion of Claude Code. It exposes typed Python functions (e.g. `glitchtip_list_issues`, `eas_update_preview`, `github_comment_pr`) as MCP tools so the agent can interact with third-party providers **without ever seeing the API tokens**.

**For:** Guillaume (the human operator running Ratis) and the Claude Code agents (orchestrator + dispatched subagents) running on the Mac mini host.

**What it does:**

* Stores provider tokens in the **macOS Keychain** under service `ratis-agent-mcp`. Tokens are read fresh per call, injected into the outbound HTTP / subprocess **only**, and never appear in tool arguments, return values, audit logs, or stderr (DA-43).
* Splits caller authority into two MCP-level tokens: `admin` (full access) and `ops` (read + whitelisted writes). Each tool declares its scope; calls with the wrong token are rejected with `forbidden_tool` **before any provider traffic** (DA-44).
* Appends one redacted JSONL line per call to `~/.local/state/ratis-agent-mcp/audit.log` for forensic traceability (DA-48).
* Lives as a uv workspace member (`tools/agent-mcp/`, DA-52) — no separate clone, no extra Python install.

**What it does NOT do:**

* It does NOT touch Ratis Redis. Historically "providers only": since the `db` module (2026-05-17, see `DECISIONS_ACTED.md`), agent-mcp also covers internal Ratis infrastructure in **read-only** mode (Postgres) — see the `db` module below. Internal writes remain outside agent-mcp until the V1 approval pipeline.
* It does NOT auto-rotate tokens (DA-51 — rotation is reactive, on-demand).
* It does NOT run as a daemon — Claude Code launches a fresh MCP process per session via stdio, killed when the session ends.
* It does NOT run on Linux/WSL today — the Keychain backend is macOS-only (V0 scope, DA-43).

## Install / first-time setup

The 4-step onboarding (DA-43 + DA-44).

### 1. Sync the workspace

From the repo root :

```bash
uv sync --package ratis-agent-mcp
```

Confirm the console script is on `PATH` :

```bash
uv run --package ratis-agent-mcp agent-mcp --version
```

### 2. Generate the two MCP caller tokens

```bash
uv run --package ratis-agent-mcp agent-mcp init
```

This generates a fresh `MCP_AUTH_ADMIN_TOKEN` and `MCP_AUTH_OPS_TOKEN`, writes them to `~/.config/ratis-agent-mcp/tokens.env` (chmod 600), and **prints them to stdout exactly once** for copy-paste into Claude Code's config. They are never re-displayed afterwards (rotate via `agent-mcp tokens rotate --role <admin|ops>` if lost).

`init` refuses to overwrite an existing `tokens.env` — use `tokens rotate` for rotations.

### 3. Wire Claude Code to talk to the MCP

Register the server with `claude mcp add` — this is the **only** supported
mechanism. (Do NOT hand-edit `~/.claude/mcp.json` : Claude Code does not read
that path. User-scope MCP config lives in `~/.claude.json`, which `claude mcp add`
writes for you.)

```bash
claude mcp add ratis --scope user \
  --env MCP_AUTH_TOKEN=<paste-admin-or-ops-token-here> \
  -- uv run --directory /absolute/path/to/Ratis --package ratis-agent-mcp agent-mcp serve
```

`uv run --directory <repo>` pins the command to the Ratis workspace, so the
server starts correctly regardless of Claude Code's current working directory
(no `cwd` field needed).

Choose the **admin** token for interactive sessions (where you validate sensitive actions) and the **ops** token for dispatched subagents / automation.

Restart Claude Code, then confirm with `claude mcp list` (the `ratis` server
should report `✓ Connected`). The tools become visible at the next session.

### 4. Seed the Keychain with provider tokens

For each provider you intend to use :

```bash
uv run --package ratis-agent-mcp agent-mcp keychain set <provider>
# (you'll be prompted for the token, no echo)
```

Provider account names (one per provider) :

| Provider | Keychain account(s) |
|---|---|
| GlitchTip | `admin-glitchtip` |
| EAS     | `eas` |
| GitHub  | `github` |
| Stripe  | `stripe` |
| R2      | `r2-access-key-id` · `r2-secret-access-key` · `r2-endpoint-url` |

Provider-specific scopes/setup are documented in the per-provider sections below.

## Daily usage

You don't call the tools directly — you ask Claude Code in natural language and Claude dispatches via MCP :

```
> List the 10 most recent issues for the ratis-product-analyser project
> Fetch the details of PR #297 and summarise the failing checks
> Re-run the failing checks on PR #297            (admin only)
> Push an OTA to preview with message "fix scan crash"
> Create a GlitchTip incident — title "Scan worker crashloop"
> List the 20 most recent R2 objects under the receipts/ prefix
```

Audit-log tail in another shell while you work :

```bash
tail -f ~/.local/state/ratis-agent-mcp/audit.log | jq .
```

## Per-provider reference

### GlitchTip tools

DA-47 — Sentry SaaS sunset, migration to GlitchTip self-hosted (Sentry-compatible protocol, same paths `/api/0/...`). Admin token created via the local GlitchTip UI (`http://localhost:8000/profile/auth-tokens`) with scopes `project:read`, `event:read`, `event:admin`, `org:read`.

```bash
uv run --package ratis-agent-mcp agent-mcp keychain set admin-glitchtip
```

The token lands in the Keychain under service `ratis-agent-mcp`, account `admin-glitchtip` (the CLI wrapper `~/glitchtip/bin/glt` shares the same entry). Optional overrides :

```bash
export GLITCHTIP_API_URL=http://localhost:8000/api/0   # default — GlitchTip self-hosted local
export GLITCHTIP_ORG=my-other-org                      # default: ratis
```

Available tools :

| Function                                                | Scope | HTTP                                       |
|---------------------------------------------------------|-------|--------------------------------------------|
| `glitchtip_list_issues(project, query, limit)`          | ops   | `GET  /api/0/projects/<org>/<project>/issues/` |
| `glitchtip_get_issue(issue_id)`                         | ops   | `GET  /api/0/issues/<id>/`                 |
| `glitchtip_list_events(issue_id, limit)`                | ops   | `GET  /api/0/issues/<id>/events/`          |
| `glitchtip_resolve_issue(issue_id, comment)`            | admin | `PUT  /api/0/issues/<id>/` (+ optional comment POST) |

`comment=""` (default) skips the comment POST. `glitchtip_resolve_issue` requires the **admin** MCP token.

Pre-flight (one-time) : check the GlitchTip container is up — `docker compose -f ~/glitchtip/docker-compose.yml ps`.

### EAS usage

Expo personal access token from <https://expo.dev/accounts/[account]/settings/access-tokens>, scope `publish` :

```bash
uv run --package ratis-agent-mcp agent-mcp keychain set eas
```

The wrappers shell out to `eas-cli`, which must be available on `PATH` (typically `npm i -g eas-cli`). All commands run with `cwd=<repo-root>/ratis_client/` and `--non-interactive`. The token is injected ONLY via `env={..., "EXPO_TOKEN": <token>}` of `subprocess.run` — never in argv, audit logs, or returned dicts.

Available tools :

| Function                                        | Scope | eas-cli command                                                            |
|-------------------------------------------------|-------|----------------------------------------------------------------------------|
| `eas_list_updates(channel, limit)`              | ops   | `eas update:list --branch=<channel> --limit=<n> --json --non-interactive`  |
| `eas_list_builds(platform, limit)`              | ops   | `eas build:list --platform=<p> --limit=<n> --json --non-interactive`       |
| `eas_update_preview(message, environment)`      | admin | `eas update --channel preview --environment <env> --message "..." --json` |
| `eas_update_production(message)`                | admin | `eas update --channel production --environment production --message "..."` (gated) |
| `eas_rollback_to_embedded(channel)`             | admin | `eas update:roll-back-to-embedded --channel <c> --non-interactive`         |

#### Hardcoded lessons learned

* **KP-57** — `--environment` is ALWAYS passed matching `--channel`. `eas_update_preview` defaults the environment to `preview` (override allowed for niche cases) ; `eas_update_production` hardcodes both to `production` with no override possible. This guarantees EAS Update inlines the dashboard `EXPO_PUBLIC_*` vars in the bundle (without it, the OTA ships without the API URLs and crashes on launch).
* **KP-32** — Channel mismatch (publishing to channel X while the installed APK listens to Y) silently no-ops on the device. The MCP can't prevent it, but `eas_list_builds(platform="android", limit=1)` exposes the `channel` field of the most recent build so a caller can verify the target channel BEFORE publishing.
* **KP-34** — Native deps require an `eas build`, not an `eas update`. The MCP cannot detect a missing native module from JS source — it remains a human-discipline rule (R34).

#### Pre-publish gate (production only)

`eas_update_production` runs a local R34 guard BEFORE invoking eas-cli :

1. `git fetch origin main` (refresh remote pointer).
2. Compare `git rev-parse HEAD` vs `git rev-parse origin/main`.
3. If they differ → `RuntimeError` (no eas call attempted).

This refuses to publish from a feature branch / unmerged commit. `eas_update_preview` does NOT run the gate — preview channel is intentionally permissive for testing branches.

#### Project root resolution

`RATIS_PROJECT_ROOT` env var (override) → fallback to `git rev-parse --show-toplevel`.

### GitHub usage

GitHub Personal Access Token (classic or fine-grained) :

```bash
uv run --package ratis-agent-mcp agent-mcp keychain set github
```

Recommended PAT scopes :

* **classic PAT** : `repo` (full repo access — required to comment + rerun checks), `workflow` (rerun failed check runs).
* **fine-grained PAT** : repository access scoped to `Belladynn/ratis`, with `Pull requests: read & write`, `Actions: read & write`, `Contents: read`, `Metadata: read`.

The wrappers hit `api.github.com` directly via `httpx` (no `gh` CLI dependency — testability). Repo defaults to `Belladynn/ratis` ; override via `RATIS_GITHUB_REPO=my-fork/ratis`.

Available tools :

| Function                                       | Scope | HTTP                                                                          |
|------------------------------------------------|-------|-------------------------------------------------------------------------------|
| `github_list_prs(state, limit)`                | ops   | `GET  /repos/<repo>/pulls?state=<s>&per_page=<n>`                             |
| `github_get_pr(pr_number)`                     | ops   | `GET  /repos/<repo>/pulls/<n>`                                                |
| `github_list_check_runs(pr_number)`            | ops   | `GET  /repos/<repo>/pulls/<n>` + `GET /repos/<repo>/commits/<head_sha>/check-runs` |
| `github_rerun_failed_checks(pr_number)`        | admin | resolves head sha + filters `conclusion="failure"` + `POST /repos/<repo>/check-runs/<id>/rerequest` per failure |
| `github_comment_pr(pr_number, body)`           | admin | `POST /repos/<repo>/issues/<n>/comments` body `{"body": "..."}`               |

`github_rerun_failed_checks` only re-runs check runs whose `conclusion == "failure"`. Pending / success / neutral / cancelled are skipped intentionally. Returns `{"rerequested": [<id>...], "total_failed": n}`.

### Notion usage · 🛑 DEPRECATED — removed 2026-05-31 (DA-47)

> The `notion_tools` module has been **removed** in the PR `chore/notion-sunset` (2026-05-31). Notion is replaced by **GlitchTip self-hosted** as the central incident management system. See [`docs/arch/ARCH_incident_management.md`](../../docs/arch/ARCH_incident_management.md) and [DA-47](../../docs/decisions/DECISIONS_ACTED.md). The CLI wrapper `~/glitchtip/bin/glt` replaces the historical CRUD usage of `notion_*` tools. The section below is kept as project archaeology only — do not attempt to reintroduce these tools without rethinking the "painful manual tokens" doctrine that motivated the sunset.

#### Legacy V0 (before sunset 2026-05-31)

Notion internal-integration token (from <https://www.notion.so/my-integrations>) :

```bash
uv run --package ratis-agent-mcp agent-mcp keychain set notion
```

Required integration capabilities : "Read content", "Update content", "Insert content". The integration must also be **shared with each target database** (Notion's per-database access model) — open the DB → `•••` menu → `Add connections` → select the integration. Without that share, every call returns 404 even with a valid token.

#### Whitelisted writes (DA-44)

`notion_create_ticket` and `notion_update_ticket_status` are mutating but classified `ops` — the safety guarantee is a **per-database whitelist** :

```bash
export RATIS_NOTION_INCIDENT_DBS=11111111-1111-1111-1111-111111111111,22222222-2222-2222-2222-222222222222
```

(or pre-populate alongside `MCP_AUTH_*_TOKEN` in `~/.config/ratis-agent-mcp/tokens.env`).

* **Empty / unset whitelist** → all write tools fail closed with `PermissionError("database_id not in whitelist")`.
* **`notion_create_ticket`** → checks `database_id` arg before any HTTP traffic.
* **`notion_update_ticket_status`** → GETs the page first, resolves `page.parent.database_id`, then checks the whitelist. PATCH is NOT issued on rejection.
* IDs are compared **normalised** (dashes stripped, lowercased).

Available tools :

| Function                                                          | Scope             | HTTP                                                                              |
|-------------------------------------------------------------------|-------------------|-----------------------------------------------------------------------------------|
| `notion_search(query, limit=10)`                                  | ops               | `POST /v1/search` body `{"query": "...", "page_size": <n>}`                       |
| `notion_get_page(page_id)`                                        | ops               | `GET /v1/pages/<id>` + `GET /v1/blocks/<id>/children?page_size=100`               |
| `notion_create_ticket(database_id, title, body, properties=None)` | ops (whitelisted) | `POST /v1/pages` body `{"parent": {"database_id": "..."}, "properties": ..., "children": ...}` |
| `notion_update_ticket_status(page_id, status)`                    | ops (whitelisted) | `GET /v1/pages/<id>` (parent resolution) + `PATCH /v1/pages/<id>` body `{"properties": {"Status": {"status": {"name": "..."}}}}` |

`notion_create_ticket` builds the title property under the key `Name` (Notion's default for new DBs). If your DB uses `Title` instead, override via `properties={"Title": {"title": [{"text": {"content": "..."}}]}}` — user-provided properties are merged after the default and win on key collision.

### Stripe usage

Stripe secret key (from <https://dashboard.stripe.com/apikeys>) — the value is prompted, not on argv :

```bash
uv run --package ratis-agent-mcp agent-mcp keychain set stripe
# → Enter the value when prompted (no echo). Paste your sk_test_... (V0) or sk_live_... (V1) key.
```

> **V0 = test mode only.** Use a `sk_test_...` key in V0 (pre-Runa-KYB). V1 (post-KYB) will switch to `sk_live_...`. The wrapper detects `sk_live_` keys at runtime and emits a one-shot `live_mode_used` warning into the audit log on first call within the process — non-blocking, but visible to operators tailing the log so accidental V0 → V1 leakage is loud.

The wrappers hit `api.stripe.com/v1/` directly via `httpx`. POST bodies are `application/x-www-form-urlencoded` (NOT JSON, Stripe's contract).

Available tools :

| Function                                                  | Scope | HTTP                                                                   |
|-----------------------------------------------------------|-------|------------------------------------------------------------------------|
| `stripe_list_customers(limit=10, email=None)`             | ops   | `GET  /v1/customers?limit=<n>[&email=<e>]`                             |
| `stripe_get_subscription(subscription_id)`                | ops   | `GET  /v1/subscriptions/<id>`                                          |
| `stripe_list_recent_charges(limit=20)`                    | ops   | `GET  /v1/charges?limit=<n>`                                           |
| `stripe_refund_charge(charge_id, amount_cents=None, reason="requested_by_customer")` | admin | `POST /v1/refunds` form body `charge=<id>[&amount=<cents>][&reason=<r>]` |

`stripe_refund_charge` requires the **admin** MCP token. Refunds are irreversible money movement — calling with the ops token is rejected at the auth gate before any HTTP traffic.

### R2 usage

R2 is Cloudflare's S3-compatible object storage — receipt images live there for 48h before RGPD purge. Three Keychain entries to set :

```bash
uv run --package ratis-agent-mcp agent-mcp keychain set r2-access-key-id
uv run --package ratis-agent-mcp agent-mcp keychain set r2-secret-access-key
uv run --package ratis-agent-mcp agent-mcp keychain set r2-endpoint-url
# value is the FULL URL : https://<account_id>.r2.cloudflarestorage.com
```

The endpoint URL is stored in the Keychain (rather than as an env var) because it embeds the Cloudflare account id — moderately sensitive when combined with stolen credentials.

Bucket defaults to `ratis-receipts-prod` (the production R2 bucket referenced by `R2_BUCKET_NAME` in `.env.example`). Override per-process : `export RATIS_R2_BUCKET=ratis-receipts-staging`.

Available tools :

| Function                                       | Scope | boto3 call                                                      |
|------------------------------------------------|-------|-----------------------------------------------------------------|
| `r2_list_objects(prefix="", limit=50)`         | ops   | `s3:ListObjectsV2(Bucket, Prefix, MaxKeys)`                      |
| `r2_get_object_url(key, ttl_seconds=600)`      | admin | `s3:generate_presigned_url("get_object", Params={Bucket,Key}, ExpiresIn)` |
| `r2_delete_object(key)`                        | admin | `s3:DeleteObject(Bucket, Key)`                                   |

`limit` clamped to `[1, 1000]` ; `ttl_seconds` clamped to `[1, 7*24*3600]` (S3 SigV4 max).

`r2_get_object_url` is admin-scoped because the presigned URL exposes RGPD-class data (receipt images). The URL itself is the WHOLE POINT of the tool, but it is **never logged to the audit JSONL** — the dispatcher logs args + status only, NOT the return value.

`r2_delete_object` follows S3 semantics : deleting a non-existent key is NOT an error.

### Module `db` — Postgres read-only access

`db_query(sql, env="dev"|"prod")` — executes a SQL query **in read-only mode**
against the dev database (local) or prod (Hetzner via SSH). All writes are
rejected by Postgres (session `default_transaction_read_only=on`). Scope `ops`.

No Keychain entry required: psql runs as a trusted local user inside the
`ratis-postgres-1` container; the prod hop uses the `ratis-prod` SSH key.

| Function                       | Scope | Transport                                              |
|--------------------------------|-------|--------------------------------------------------------|
| `db_query(sql, env="dev")`     | ops   | `docker exec -i ratis-postgres-1 psql` (SQL via stdin) |
| `db_query(sql, env="prod")`    | ops   | `ssh ratis-prod docker exec -i … psql` (SQL via stdin) |

Result: `{columns, rows, rowcount, truncated}`, capped at 200 rows.

## Audit log

Every tool call appends one JSONL line to `~/.local/state/ratis-agent-mcp/audit.log` (chmod 600, DA-48). Schema :

```json
{"ts": "...", "caller": "admin|ops", "tool": "...", "args_redacted": {...},
 "status": "ok|forbidden_tool|keychain_miss|provider_error|audit_error|tool_not_registered|token_rotated|live_mode_used",
 "latency_ms": 145, "error": null}
```

Args matching `token|key|secret|password|auth|credential` (case-insensitive) are replaced with `"<redacted>"` before logging. Provider tokens are never passed as tool arguments — they live in the Keychain and are fetched per-call inside the tool implementation.

Useful queries :

```bash
# Tail in real time, JSON-pretty
tail -f ~/.local/state/ratis-agent-mcp/audit.log | jq .

# Only failed calls
jq 'select(.status != "ok")' ~/.local/state/ratis-agent-mcp/audit.log

# Calls per tool, today
jq -r '.tool' ~/.local/state/ratis-agent-mcp/audit.log | sort | uniq -c

# Who (admin vs ops) did what
jq -r '[.ts,.caller,.tool,.status] | @tsv' ~/.local/state/ratis-agent-mcp/audit.log | column -t
```

## Diagnostics

```bash
uv run --package ratis-agent-mcp agent-mcp paths     # show resolved config / state paths
uv run --package ratis-agent-mcp agent-mcp --version
```

### `keychain check` — audit every required provider account

```bash
uv run --package ratis-agent-mcp agent-mcp keychain check
# account              status
# -------------------  ------
# admin-glitchtip      present
# eas                  missing
# github               present
# stripe               missing
# r2-access-key-id     present
# r2-secret-access-key present
# r2-endpoint-url      present
```

Exits 0 if every required account is present, 1 if any is missing. Use after onboarding to confirm the seeding step is complete, or after a Mac restore to verify the Keychain survived.

### `keychain get <provider>` — print one secret on stdout (raw, no decoration)

```bash
uv run --package ratis-agent-mcp agent-mcp keychain get admin-glitchtip
# (secret printed on stdout, no trailing newline)
# agent-mcp: secret printed to stdout — use `--no-warn` to silence.   <-- on stderr

# Pipe-friendly form (silences the warning) :
uv run --package ratis-agent-mcp agent-mcp keychain get admin-glitchtip --no-warn | pbcopy
```

Same security boundary as `security find-generic-password -s ratis-agent-mcp -a <provider> -w` but stays inside the canonical service name. Exits 1 with a clean stderr message if the account is absent.

### `call <tool> [json_args]` — one-shot in-process Dispatcher invocation

```bash
# Provide MCP_AUTH_TOKEN exactly like the server does.
export MCP_AUTH_TOKEN=<paste-admin-or-ops-token>

uv run --package ratis-agent-mcp agent-mcp call glitchtip_list_issues '{"project":"ratis-backend","limit":3}'
# JSON result printed on stdout (indent=2)

# Args default to {} if omitted :
uv run --package ratis-agent-mcp agent-mcp call github_list_prs

# Errors land on stderr with the canonical status :
uv run --package ratis-agent-mcp agent-mcp call glitchtip_resolve_issue '{"issue_id":"X"}'
# agent-mcp: forbidden_tool: caller 'ops' lacks scope 'admin'
```

Reuses the same `Dispatcher.dispatch()` pipeline as the MCP server — auth, audit log, scope enforcement and provider error wrapping all behave identically. Exit 0 on success, 1 on any error (`forbidden_tool`, `keychain_miss`, `provider_error`, `tool_not_registered`).

## Smoke test

`tools/agent-mcp/scripts/smoke.sh` exercises three **read-only** tools (GlitchTip, EAS, R2) against the real providers using your seeded Keychain. Single command answers the question : **are my Keychain credentials still valid against the live providers?**

Useful right after :

* token rotation at any provider ;
* a Mac restore (Keychain survived?) ;
* CI cache flake (did anything escape into prod state?) ;
* a fresh onboarding to confirm the seeding step is complete.

### Prerequisites

* macOS Keychain seeded for **at minimum** : `admin-glitchtip`, `eas`, `r2-access-key-id`, `r2-secret-access-key`, `r2-endpoint-url`. The `github` and `stripe` accounts are NOT in the V0 smoke set — the script warns but doesn't fail when they're missing.
* `jq` on `PATH` (`brew install jq`).
* `eas-cli` on `PATH` (the EAS test shells out to it).
* `MCP_AUTH_TOKEN` env var set to either the ops or admin token from `~/.config/ratis-agent-mcp/tokens.env`.

### Run

```bash
MCP_AUTH_TOKEN=<paste-ops-or-admin-token> tools/agent-mcp/scripts/smoke.sh
```

Example output (truncated) :

```
agent-mcp smoke test
====================
glitchtip_list_issues  [PASS]     124ms  3 issues returned
eas_list_builds     [PASS]    3412ms  3 builds returned
r2_list_objects     [PASS]     451ms  5 objects returned
====================
3/3 passed.
```

On failure the relevant `agent-mcp` error envelope is shown under the failing test :

```
r2_list_objects     [FAIL]     480ms  agent-mcp: provider_error: NoSuchBucket
                    └─ stderr: agent-mcp: provider_error: bucket 'ratis-receipts-prod' not found
```

### Override env vars

| Variable | Default | Effect |
|---|---|---|
| `RATIS_SMOKE_GLITCHTIP_PROJECT` | `ratis-backend` | GlitchTip project slug used by `glitchtip_list_issues` |
| `RATIS_SMOKE_EAS_PLATFORM`   | `android`    | `platform` arg passed to `eas_list_builds` |
| `RATIS_SMOKE_R2_PREFIX`      | (empty)      | `prefix` arg passed to `r2_list_objects` (empty = list bucket root) |

### Exit codes

* `0` — all 3 tests passed.
* `1` — at least one test failed (the others still ran ; the summary lists every result).
* `2` — pre-flight aborted before any test ran (missing `MCP_AUTH_TOKEN`, `agent-mcp` unresolvable, `jq` missing, or required Keychain account missing).

### Out of scope (V0)

* No admin-scope writes (no `eas_update_*`, no `r2_delete_*`, no `r2_get_object_url`) — those have side effects or cost.
* No Stripe / Notion / GitHub tests — these aren't seeded yet ; adding them would emit false-FAIL noise. Will land once those accounts are routinely seeded.
* Not wired into CI — it requires the live macOS Keychain. Manual ops tool, not a unit test.

### Troubleshooting first-run failures

If you see `Project does not exist` (GlitchTip) or `AccessDenied` / `NoSuchBucket` (R2) on first run, your real org/bucket may differ from the script defaults — set `GLITCHTIP_ORG`, `RATIS_SMOKE_GLITCHTIP_PROJECT`, `RATIS_R2_BUCKET` accordingly. The defaults target the canonical Ratis local resources (`ratis` org, `ratis-backend` project, `ratis-receipts-prod` bucket) ; staging or fork deployments will need overrides.

## Tests

```bash
uv run --package ratis-agent-mcp pytest tools/agent-mcp/tests/ -q
```

The test suite is hermetic — no real Keychain, no real provider traffic, no `~/.config/...` mutation. 226 tests, ~2s.

## Token rotation

### MCP caller tokens (admin / ops)

Reactive only (DA-51) — no calendar rotation. Triggers : suspected compromise, after a PR that changes the admin tool surface (defensive audit).

```bash
uv run --package ratis-agent-mcp agent-mcp tokens rotate --role admin
# → generates a new MCP_AUTH_ADMIN_TOKEN, rewrites ~/.config/ratis-agent-mcp/tokens.env
# → writes a "token_rotated:admin" line to the audit log
# → invalidates the previous token immediately

uv run --package ratis-agent-mcp agent-mcp tokens rotate --role ops
```

After rotation, update the `MCP_AUTH_TOKEN` of the `ratis` server (`claude mcp remove ratis`, then re-run the `claude mcp add` from step 3 with the new token) and restart Claude Code.

### Provider tokens (Sentry, EAS, GitHub, Notion, Stripe, R2)

Rotate at the provider's UI → re-set in the Keychain :

```bash
uv run --package ratis-agent-mcp agent-mcp keychain set <provider>
# (paste new token, no echo)
```

Old token is overwritten in place ; the next tool call picks up the new value (60s in-memory cache may delay it briefly).

## Troubleshooting

### `agent-mcp: command not found`

The console script is a workspace member — invoke it through uv :

```bash
uv run --package ratis-agent-mcp agent-mcp <subcommand>
```

If that fails, run `uv sync --package ratis-agent-mcp` from the repo root.

### "I don't see the tools in Claude Code"

1. Verify the `ratis` server is registered : `claude mcp list` should show it (`✓ Connected`).
2. Verify the `--directory` argument is the absolute path to the Ratis repo root.
3. Verify `uv` is on the `PATH` of the Claude Code launcher (not just your shell — Claude Code may launch from a different env).
4. Restart Claude Code (the MCP is loaded at session start, not hot-reloaded).
5. Check the Claude Code MCP logs : Settings → MCP → ratis → Logs.

### `forbidden_tool` on a tool call

Scope mismatch — the `MCP_AUTH_TOKEN` configured for the `ratis` server is `ops` but the tool requires `admin`. Switch to the ADMIN token (`claude mcp remove ratis` + re-add) and restart Claude Code. This is by design (DA-44).

### `keychain_miss` on a tool call

The provider token was never set (or was deleted). Run :

```bash
uv run --package ratis-agent-mcp agent-mcp keychain set <provider>
```

### `PermissionError: database_id not in whitelist` on Notion writes

`notion_create_ticket` / `notion_update_ticket_status` need an explicit DB whitelist. Set :

```bash
export RATIS_NOTION_INCIDENT_DBS=<dash-or-no-dash-uuid>[,<another-uuid>...]
```

(or persist in `~/.config/ratis-agent-mcp/tokens.env`).

### `RuntimeError: refusing production publish — HEAD != origin/main` on EAS

The R34 pre-publish gate is doing its job. You're trying to push an OTA from a feature branch / unmerged commit. Merge the PR first (or `git pull --ff-only` if you simply forgot to sync), then re-run.

### `provider_error` with 401/403 from a provider

The Keychain token has expired or been revoked at the provider. Re-set with `agent-mcp keychain set <provider>`.

### Audit log not being written

Permissions on `~/.local/state/ratis-agent-mcp/`. The dir is created at boot — if creation fails, the MCP falls back to writing the audit failure to stderr, which Claude Code surfaces in the MCP logs panel.

### EAS update silent no-op (channel mismatch, KP-32)

The MCP cannot detect this automatically in V0. Verify the installed APK's channel BEFORE publishing :

```
> What is the channel of the latest Android APK via the MCP?
```

(Calls `eas_list_builds(platform="android", limit=1)` — the response includes the `channel` field.)

## FAQ

### Why agent-mcp instead of just 1Password CLI?

1Password CLI solves only the storage-encryption part. It does NOT solve :

* the audit trail (who called what, when) ;
* the admin/ops scope split per caller ;
* the "business function" abstraction that prevents the model from manipulating the token directly. With 1Password CLI, Claude still has to `op read 'op://...sentry-token'` then `curl -H "Authorization: Bearer $token" ...` — the token transits through the model's context.

`agent-mcp` is an **abstraction layer** that internalises all three. 1Password CLI **could** be a future Keychain-backend alternative (see DA-43), but doesn't suffice alone.

### Why is the MCP not allowed to touch Ratis Redis?

Redis stays out of scope. Postgres, however, is accessible **in read-only mode** via the `db` module (2026-05-17, see `DECISIONS_ACTED.md`): the "providers only" boundary was lifted for internal infrastructure. Internal writes (DB mutations, `/admin/*`) remain outside agent-mcp until the V1 approval pipeline (spec `docs/superpowers/specs/2026-05-17-db-mcp-access-design.md`).

### What happens at Mac mini reboot?

Nothing manual. The MCP is launched per Claude Code session via stdio — not a daemon. After reboot :

* Keychain intact (macOS persistent store) ;
* `~/.config/ratis-agent-mcp/tokens.env` intact (filesystem) ;
* `~/.local/state/ratis-agent-mcp/audit.log` intact (filesystem) ;
* Next Claude Code session re-spawns the MCP transparently.

### What protects against a compromised Claude (prompt injection) firing admin actions?

Three layers (DA-44 + DA-48) :

1. **Token separation** : Claude is launched by default with the OPS token. All admin tools (`glitchtip_resolve_issue`, `eas_update_production`, `stripe_refund_charge`, …) reject before any provider traffic.
2. **Whitelisted ops writes** : on the ops scope, only a few writes are permitted (Notion ticket creation against whitelisted DBs, GitHub PR comment) — everything else is read-only.
3. **Audit log** : each call is traced for post-mortem analysis.

The admin token is reserved for interactive sessions where the human validates each sensitive action.

### Why one MCP for all providers and not one per provider?

Shared: 1 process to monitor, 1 audit log (cross-provider correlation trivial — "what GlitchTip issue triggered the EAS update right after?"), 1 wire-up via `claude mcp add`. Trade-off : a runtime bug impacts all providers at once — mitigated by tests + scope additivity.

### How do I add an 8th module?

Standard procedure, ~1 chunk :

1. Create `tools/agent-mcp/src/agent_mcp/tools/<provider>_tools.py` with typed functions (Pydantic-compatible).
2. Register them in `server.py` (existing module loader auto-discovers).
3. Add Keychain entry : `uv run --package ratis-agent-mcp agent-mcp keychain set <provider>`.
4. TDD with mocked provider HTTP / subprocess.
5. Bump minor version in `pyproject.toml` (1.N → 1.N+1).
6. Update this README's per-provider section + reference table.
