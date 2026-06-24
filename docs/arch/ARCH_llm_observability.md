---
# Identity
type: cross-cutting
status: in-progress

# Navigation (Obsidian + RAG)
parent: ARCH_RATIS
related: [ARCH_CORE, ARCH_receipt_pipeline]

# Technical
tech: [Langfuse, OpenTelemetry, Anthropic]
env_vars: [LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY, LANGFUSE_HOST]

# Business
tags: [observability, llm, langfuse, tracing, eval, ocr, cost]
business_domain: infra
rgpd_concern: true

# Freshness (MANDATORY — update à chaque édition)
updated: 2026-06-19
---

# llm_observability — tracing & eval of Ratis LLM calls (Langfuse self-hosted)

> TL;DR: Ratis calls an LLM in production (Claude Haiku 4.5 in the OCR pipeline, `AnthropicLLMClient.extract`) with no observability. We instrument this call with **Langfuse self-hosted** (OTEL + `AnthropicInstrumentor`): tracing tokens/cost/latency/fallback, modelled on the no-op-if-empty pattern from `init_sentry`. Self-hosted is mandatory (receipt text is purchase data → GDPR). Roadmap: tracing → offline eval (ground-truth = `product_knowledge`) → A/B Haiku vs Sonnet.
> @tags: observability llm langfuse opentelemetry anthropic tracing eval ocr cost haiku self-hosted clickhouse rgpd
> @status: EN-COURS
> @subs: auto

> [[ARCH_RATIS]] · relations : [[ARCH_CORE]], [[ARCH_receipt_pipeline]]

## Index

- [One-sentence summary](#one-sentence-summary) · L.NN
- [Responsibility / scope](#responsibility--scope) · L.NN
- [Key architecture decisions](#key-architecture-decisions) · L.NN
- [Main flow](#main-flow) · L.NN
- [Deployment stack (self-hosted)](#deployment-stack-self-hosted) · L.NN
- [GDPR constraints](#gdpr-constraints) · L.NN
- [Env vars](#env-vars) · L.NN
- [Implementation checklist](#implementation-checklist) · L.NN
- [Roadmap (beyond the quick-win)](#roadmap-beyond-the-quick-win) · L.NN
- [Things to know (vectorised FAQ)](#things-to-know-vectorised-faq) · L.NN

---

## One-sentence summary

`llm_observability` is the cross-cutting layer that instruments Ratis LLM calls with Langfuse self-hosted (tracing + eval), starting with the Claude call in the OCR pipeline of `ratis_product_analyser`.

## Responsibility / scope

- Trace every production LLM call (model, prompt, response, tokens in/out, latency, cost, fallback) without coupling business logic (cross-cutting, wired at the edges like Sentry).
- Provide the eval foundation: measure OCR extraction quality against a reference dataset.
- **Out of scope for quick-win V1**: instrumentation of n8n `db-write-pipeline` (2 Sonnet passes) and `scripts/git_agent/llm_reviewer.py` — see Roadmap.

## Key architecture decisions

### DA-LO1 — Self-host mandatory (GDPR)
`receipt_text` (products, prices, store) is included in the trace input = purchase data. Sending this to Langfuse Cloud is forbidden. Self-hosted deployment (`~/langfuse`, docker-compose, port 3000, `telemetry_enabled=false`), consistent with the Ratis posture (Caddy, GlitchTip, n8n self-hosted).

### DA-LO2 — Init at worker boot, modelled on `init_sentry`
`init_langfuse(service_name)` added to `ratis_core/observability.py`, same contract as `init_sentry` (`observability.py:88`): reads env, **silent no-op if keys are absent**, `try/except` → `log.warning`, never crashes. Since the LLM call lives in the Celery worker, init happens in the **`worker_process_init` signal (post-fork)** — OTEL export threads created before `fork` are dead in the child.

### DA-LO3 — `@observe` on `process_receipt` = 1 scan = 1 trace
`@observe()` decorator (inner, under `@celery_app.task`) on `process_receipt` (`receipt_task.py:272`). The `AnthropicLLMClient.extract` call (`llm_clients.py:83`) is automatically nested as a *generation* via `AnthropicInstrumentor().instrument()`.

### DA-LO4 — Cloud fallback refused (hard GDPR guard)
The Langfuse SDK defaults to `cloud.langfuse.com` when `LANGFUSE_HOST` is absent. `init_langfuse` enables tracing **only if all 3 vars are present** (`PUBLIC_KEY` + `SECRET_KEY` + `HOST`); if `HOST` is missing → no-op + warning, never a cloud default.

### DA-LO5 — `LANGFUSE_HOST` (verified, not `BASE_URL`)
SDK v4.9.0 verified by smoke (`auth_check: True`): the variable read is `LANGFUSE_HOST`. Do not use `LANGFUSE_BASE_URL`.

## Main flow

```
scan (process_receipt @observe)  ─────────────► TRACE "ocr_scan" {receipt_id, scan_type}
        └─ orchestrator.run_pipeline
              └─ comprehend.comprehend_ticket
                    └─ AnthropicLLMClient.extract  ──► GENERATION (auto via AnthropicInstrumentor)
                          model=claude-haiku-4-5 · tokens in/out · latence · coût ($1/$5 /1M) · fallback?
```

## Deployment stack (self-hosted)

`~/langfuse/docker-compose.yml` (cloned, outside Ratis repo) — 6 containers: web + worker + postgres + clickhouse + redis + minio. Adaptations vs upstream: internal DB ports (no host publishing → avoids collision with `ratis-postgres-1`/`ratis-redis-1`), minio bind `127.0.0.1`, `clickhouse mem_limit: 4g`. Secrets via `~/langfuse/.env` (openssl, chmod 600). Headless bootstrap `LANGFUSE_INIT_*` → org `ratis` / project `ratis-product-analyser`. UI: http://localhost:3000.

## GDPR constraints

- Self-hosted only (DA-LO1); `telemetry_enabled=false`.
- **No PII in traces**: never `user_lat`/`user_lng`/home address; trace metadata = internal IDs (`receipt_id`) only.
- `receipt_text` stays internal (never leaves the Mac mini).

## Env vars

| Var | Role | Default |
|---|---|---|
| `LANGFUSE_PUBLIC_KEY` | project public key | absent → tracing off |
| `LANGFUSE_SECRET_KEY` | project secret key | absent → tracing off |
| `LANGFUSE_HOST` | self-hosted instance URL | absent → tracing off (never cloud) |

Optional (no-op if absent), like `SENTRY_DSN`. Worker in container → `LANGFUSE_HOST=http://host.docker.internal:3000`.

## Implementation checklist

- [x] PA deps: `uv add langfuse opentelemetry-instrumentation-anthropic` (runtime) — `langfuse>=4.9.0`, `opentelemetry-instrumentation-anthropic>=0.61.0`
- [x] `init_langfuse(service_name)` in `ratis_core/observability.py` (no-op-if-empty + cloud refusal DA-LO4 + try/except + idempotence + kill-switch DA-LO6)
- [x] Call in `worker_process_init` (post-fork) — `celery_app.py` (`_init_llm_observability`)
- [x] `@observe(name="ocr_scan", capture_output=False)` on `process_receipt` (`receipt_task.py:273`) — input=receipt_id UUID, output not captured (return None + GDPR, DA-LO3)
- [x] PA `.env.example` (block modelled on Sentry, GDPR self-hosted note) + `tests/conftest.py` (vars="" + `LANGFUSE_TRACING_ENABLED=false` → no-op CI)
- [x] No-op tests (empty keys, cloud refusal, idempotence, auth-fail, SDK absent) ratis_core + tracing-off PA tests + PA suite green
- [ ] End-to-end verification: 1 real scan → trace visible :3000 (orchestrator, post-merge)

### DA-LO6 — SDK kill-switch when tracing is disabled (`LANGFUSE_TRACING_ENABLED=false`)
Trap discovered during implementation: `@observe` **auto-initialises** the global Langfuse client on the first call *even with empty keys* → registers an OTEL span-processor whose exporter logs `Failed to export span batch` (invalid URL, no host) at process shutdown. Clean fix: `init_langfuse` sets `LANGFUSE_TRACING_ENABLED=false` (native SDK kill-switch, via `os.environ.setdefault` — an operator override wins) on **every** no-op/refusal path → the decorator becomes a true pass-through (0 threads, 0 network). `conftest.py` PA also sets it for hermetic silent tests.

## Roadmap (beyond the quick-win)

- **Phase 2 — offline eval**: Langfuse dataset from `product_knowledge` (raw_ocr → corrected = human ground-truth); score OCR extraction.
- **Phase 3 — model A/B**: Haiku vs Sonnet on a frozen dataset (cost/quality).
- **Phase 4 — expand**: n8n `db-write-pipeline` (2 Sonnet passes), `git_agent/llm_reviewer.py`.

## Things to know (vectorised FAQ)

- **Why not Cloud?** GDPR: receipt text is purchase data (DA-LO1).
- **Why `worker_process_init` and not the top-level module?** Celery prefork: OTEL threads created before `fork` are dead in the child (DA-LO2).
- **Impact on CI/tests?** None: no-op if keys are empty, like Sentry; `conftest.py` forces vars to "".
- **Cost visible?** Yes: Langfuse computes cost from `usage` + model pricing (Haiku 4.5 = $1/$5 /1M).
