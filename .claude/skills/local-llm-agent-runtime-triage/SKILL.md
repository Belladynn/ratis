---
name: local-llm-agent-runtime-triage
description: "Runbook for configuring and validating a local LLM as an agent provider (Ollama/OpenAI-compatible), including warmup, context, networking, and fallback when a large thinking model is too slow or unstable."
---

# local-llm-agent-runtime-triage

Wiring a local LLM as an agent provider for privacy-sensitive work hits a
predictable cluster of blockers : the model isn't installed/warm, the
OpenAI-compatible endpoint isn't reachable from the container, context
length is exceeded, latency is unusable, or a big "thinking" model is too
slow to be practical. This skill is the setup-and-validate runbook plus
the fallback path. Keep it general across local providers, not tied to one
model.

## When to Use

- An agent runtime is wired to Ollama / a local provider for
  privacy-sensitive work and model behavior, context, or latency becomes
  a blocker.
- You need to validate a local model end-to-end from the agent runtime
  before trusting it.

## When NOT to Use

- The task has no privacy constraint and a cloud model is faster/cheaper
  — use the cloud model; local-LLM overhead isn't justified.
- The hardware clearly can't host a usable model size — escalate the
  capacity gap rather than fighting warmup/latency forever.
- The failure is in the agent application logic, not the model runtime —
  debug the app path instead.

## Procedure

1. **Verify the runtime plumbing.** Confirm the local service (e.g.
   Ollama) is up, the model is installed, the OpenAI-compatible endpoint
   responds, container/Docker networking reaches it, and the
   agent/provider config points at the right URL.
2. **Smoke-test with bounded prompts.** Send small prompts, watch logs,
   exercise thinking controls (`/no_think` or equivalent), and try a few
   context-length variants to find the working envelope.
3. **Fall back deliberately if blocked.** Downgrade model size or switch
   model family, **document the trade-off** (quality vs latency/privacy),
   and retest end-to-end from the agent runtime — not just the raw
   endpoint.
