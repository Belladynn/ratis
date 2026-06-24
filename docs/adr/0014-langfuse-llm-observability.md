# ADR-0014: LLM production observability via self-hosted Langfuse

**Status:** Accepted (no-op-if-empty; RGPD-hard)

## Context and Problem Statement

Ratis calls Claude Haiku 4.5 in production inside the OCR receipt pipeline (`AnthropicLLMClient.extract`) with zero observability — no visibility into tokens, cost, latency, or fallback. The trace input (`receipt_text`: products, prices, store) is purchase data and is PII-sensitive under RGPD. How do we observe the production LLM call without leaking purchase data and without breaking CI/dev where keys are absent?

## Decision Drivers

- Need token / cost / latency / fallback visibility on the production LLM call.
- Receipt text is PII-sensitive purchase data and must never leave the Mac mini (consistent with the Caddy/GlitchTip/n8n self-hosted posture).
- CI and dev must stay clean with empty keys (same contract as Sentry).
- Must survive the Celery fork model (post-fork OTEL export threads).
- The SDK must never silently default to cloud.

## Considered Options

- **Self-hosted Langfuse** (OpenTelemetry + `AnthropicInstrumentor`), no-op-if-empty, hard cloud-refusal guard.
- **Langfuse Cloud.**
- **Let the SDK default to `cloud.langfuse.com` when `HOST` is missing.**
- **Also instrument the n8n db-write-pipeline and the git LLM reviewer now.**

## Decision Outcome

Chosen: instrument the LLM call with **self-hosted Langfuse** (OpenTelemetry + `AnthropicInstrumentor`). `init_langfuse(service_name)` is added to `ratis_core/observability.py` with the same contract as `init_sentry`: reads env, no-op silently if keys absent, `try/except → warning`, never crashes. It is initialized in the Celery `worker_process_init` (post-fork) signal; `process_receipt` is decorated `@observe` so each scan = one trace with the LLM call as a nested generation. Tracing activates **only if all three** of `LANGFUSE_PUBLIC_KEY`/`SECRET_KEY`/`HOST` are present — never defaulting to cloud. Output capture is disabled for RGPD; traces carry only internal IDs like `receipt_id`.

**Rejected:** Langfuse Cloud (purchase data + RGPD); letting the SDK default to `cloud.langfuse.com` when `HOST` is missing (explicitly blocked by guard DA-LO4); instrumenting the n8n db-write-pipeline (2-pass Sonnet) and the git LLM reviewer now (deferred to a roadmap, not in this quick-win).

**Quality-attribute trade-off:** we bought **observability** (token/cost/latency/fallback tracing, a foundation for offline eval and A/B Haiku-vs-Sonnet) at the cost of **operational weight and trace richness** — a 6-container self-hosted stack on the Mac mini (clickhouse alone needs 4g) and RGPD-disabled output capture that limits how much each trace can show.

### Consequences

- **Good:** token / cost / latency / fallback tracing with zero PII in traces; safe defaults matching Sentry so CI/dev stay clean; foundation for offline eval (ground truth = `product_knowledge`) and A/B Haiku vs Sonnet; post-fork init avoids dead OTEL export threads.
- **Bad:** adds a 6-container self-hosted stack (web/worker/postgres/clickhouse/redis/minio, clickhouse needs 4g) on the Mac mini; output capture is disabled for RGPD so trace richness is limited; end-to-end verification against a real scan was still pending at time of writing.

**Source.** `docs/arch/ARCH_llm_observability.md` (DA-LO1..DA-LO6; flow; stack; env vars). Canonical register: [`../decisions/DECISIONS_ACTED.md`](../decisions/DECISIONS_ACTED.md).
