---
# Identity
type: batch-global                       # always `batch-global` for a batch
service: ratis_batch_<name>              # canonical uv-workspace package name (e.g. ratis_batch_consensus)
status: production                       # production | planned | deprecated

# Navigation (Obsidian + RAG)
parent: ARCH_RATIS                       # always ARCH_RATIS for batches
sub_archs: []                            # rarely useful for a batch (leave empty)
related: [ARCH_<PARENT_SERVICE>, ARCH_<DOMAIN>]   # relevant business ARCHs (e.g. ARCH_REWARDS, ARCH_cashback)

# Technical
tech: [Python, SQLAlchemy, Postgres, ...]        # libraries used (httpx, tenacity, ThreadPoolExecutor, boto3, osmium, etc.)
tables: [<table1>, <table2>, batch_sync_log]      # tables touched (read + write)
env_vars: [DATABASE_URL, <ENV_VAR_2>]             # required env vars (DATABASE_URL always present)

# Business
tags: [batch, <domain>, ...]              # first tag always `batch`
business_domain: <domain>                 # auth | cashback | gamification | pricing | rgpd | data | ocr-matching | infra
rgpd_concern: false                       # true only if the batch handles PII (purge, anonymisation)

# Freshness (MANDATORY — bump on every edit)
updated: YYYY-MM-DD
---

# ratis_batch_<name> — <short summary 5-8 words>

> Template ARCH for any new Ratis CLI batch: standardised structure (Index, Responsibility, Frequency, Tables, Dependencies, Decisions, Flow, Parameters, Monitoring, FAQ, Glossary) to copy into `batch/ratis_batch_<name>/ARCH_BATCH_<NAME>.md`.
> @tags: template batch arch boilerplate skeleton convention scaffold meta
> @status: LIVRÉ V0
> @subs: auto

> [[ARCH_RATIS]] · relations : [[ARCH_<PARENT_SERVICE>]], [[ARCH_<DOMAIN>]]

> ⚠️ **THIS FILE IS A TEMPLATE.** Copy it into `batch/ratis_batch_<name>/ARCH_BATCH_<NAME>.md` when creating a new batch. Fill in every section: if a section does not apply (e.g. no `ratis_settings.json`), keep the heading and write `None` rather than deleting it (the template must remain recognisable).

> 📚 **When to create an ARCH_BATCH?**
> - **New batch designed** → duplicate this template, follow the checklist § "New batch boilerplate" in `SA_DEV.md`.
> - **Existing batch modified** (logic, tables, parameters, dependencies) → update the affected sections + bump `updated:` + add a DA-NN decision if the architecture changes.

## Index

> ⚠️ Index **mandatory**. Update line numbers on each major edit. Enables segmented reading (R29) without a full-read.

- [One-sentence summary](#one-sentence-summary) · L.NN
- [Responsibility](#responsibility) · L.NN
- [Execution frequency](#execution-frequency) · L.NN
- [Tables read / written](#tables-read--written) · L.NN
- [Internal dependencies (other ratis services/libs)](#internal-dependencies-other-ratis-serviceslibs) · L.NN
- [External dependencies (third parties)](#external-dependencies-third-parties) · L.NN
- [Key architecture decisions](#key-architecture-decisions) · L.NN
- [Main flow](#main-flow) · L.NN
- [Parameters (ratis_settings.json section `<name>`)](#parameters-ratis_settingsjson-section-name) · L.NN
- [Monitoring / logs](#monitoring--logs) · L.NN
- [Vectorised FAQ](#vectorised-faq) · L.NN
- [Glossary](#glossary) · L.NN

---

## One-sentence summary

ratis_batch_<name> is a <frequency> CLI batch that <main verb> <object> for <business purpose> — <short technical precision if useful (e.g. idempotent, dry-run supported, fire-and-forget)>.

## Responsibility

> **Bullets, subject = ratis_batch_<name>**. Each bullet describes an atomic responsibility (1 verb, 1 object, clear conditions). Avoid prose; aim for grep-friendly.

- ratis_batch_<name> <action 1 on table/resource A>
- ratis_batch_<name> <action 2 on table/resource B>
- ratis_batch_<name> <action N — by default writes a run entry in `batch_sync_log` for audit>

## Execution frequency

- **GitHub Actions workflow** : `.github/workflows/batch_<name>.yml`
- **Cron** : `<5-field cron>` (e.g. `0 2 * * *` daily at 02:00 UTC) — specify if `currently disabled (commented out)` in V0
- **Manual trigger** : `workflow_dispatch` always available
- **Average duration** : <X min/sec> per run
- **Idempotence** : yes / no (justify: ON CONFLICT, status guards, rerun safe)

## Tables read / written

| Table | Read | Write |
|---|---|---|
| `<table_1>` | <columns read / "all ids"> | <columns written or "—"> |
| `<table_2>` | <…> | <…> |
| `batch_sync_log` | — | INSERT run success/failed with `rows_affected` |

## Internal dependencies (other ratis services/libs)

- [[ARCH_CORE]] — `make_engine`, `load_settings`, SQLAlchemy models `<Model1>`, `<Model2>`
- [[ARCH_<PARENT_SERVICE>]] — <role in the pipeline (data source, downstream consumer, etc.)>

## External dependencies (third parties)

- <Third party 1 (OFF, OSM Overpass, Runa, Stripe, R2…)> — <role, endpoint used>
- None if the batch runs 100% locally against the Postgres DB

## Key architecture decisions

> Number DA-01, DA-02… Each decision: **Choice** / **Rejected alternative** / **Reason** (3 lines max). Serves as memory for future readers.

### DA-01 — <Short decision title>

**Choice** : <what we do>
**Rejected alternative** : <what we could have done>
**Reason** : <why the choice wins (perf, simplicity, RGPD, R-rule, etc.)>

### DA-02 — <…>

**Choice** : <…>
**Rejected alternative** : <…>
**Reason** : <…>

## Main flow

> Numbered list of steps. If there are several distinct flows (e.g. main + fallback, or multiple phases), create sub-sections `### Flow 1 — <name>`, `### Flow 2 — <name>`. Otherwise, a simple list.

1. `main()` parses `--dry-run` and other args
2. `require_env(...)` + `load_settings()` (fail-fast if required keys are missing)
3. <Business step 1>
4. <Business step 2>
5. <Business step N>
6. Aggregates stats + writes a row to `batch_sync_log` (status `success`/`failed`, `rows_processed`)
7. Exit code 1 on errors, 0 otherwise

## Parameters (ratis_settings.json section `<name>`)

> If the batch has no tunable parameters: replace with `No parameters in ratis_settings.json — behaviour driven by DB state.` and explain why (e.g. RGPD constraints hardcoded for safety, stable business values, etc.).

```json
"<name>": {
  "<param_1>": <default>,
  "<param_2>": "<default>",
  "batch_chunk_size": 100,
  "batch_max_workers": 4
}
```

Note: if the batch consumes a `ratis_settings.json` section shared with another component (e.g. live routes), mention it here to avoid surprises during tuning.

## Monitoring / logs

- **Stdout format** : `%(asctime)s %(levelname)s %(message)s` (Ratis standard)
- **Final counters logged** : `<N updated, M created, K skipped, E errors>`
- **`batch_sync_log`** : one persistent row per run (`batch_name='<name>'`, `status`, `rows_affected`)
- **Exit code** : 1 if at least one operation failed (triggers GitHub Actions workflow failure)
- **Sentry** : <yes/no — if yes, indicate the `batch_name` tag added>
- **Loki query type** : `{service="ratis_batch_<name>"} |= "FAILED"` (for debugging prod runs)

## Vectorised FAQ

> Format **Q (H3 heading) → A (short paragraph)**. Target 4-6 questions. The subject is always `ratis_batch_<name>` to maximise RAG recall. Questions should address the recurring pitfalls a dev/agent will encounter.

### Why does ratis_batch_<name> <do/not do X>?

<Answer 2-4 sentences — explanation of the trade-off, link to a DA if relevant.>

### How does ratis_batch_<name> avoid duplicates / guarantee idempotence?

<Mechanisms: ON CONFLICT, UNIQUE constraints, status guards, advisory locks…>

### What happens if <dependency> is down at run time?

<Behaviour: retry, skip, log warning, exit 1, etc.>

### How to test ratis_batch_<name> locally?

`uv run pytest batch/ratis_batch_<name>/tests/` for the full suite (dedicated conftest, DB `ratis_test`).
For a manual dry-run: `uv run python batch/ratis_batch_<name>/<entry>.py --dry-run` against a populated DB — logs counts without committing.

### <Domain-specific question for this batch (optional but encouraged)>

<Answer.>

## Glossary

> Terms specific to the batch or its domain. Do NOT redefine universal terms here (CAB, JWT, EAN…) already in the root glossary.

- **DA-XX** : numbered architecture decision
- **<Batch-specific term 1>** : <short definition>
- **<Batch-specific term 2>** : <short definition>
