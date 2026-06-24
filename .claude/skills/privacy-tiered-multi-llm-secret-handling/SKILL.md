---
name: privacy-tiered-multi-llm-secret-handling
description: "Route secret-reading automation to a local model while keeping cloud models for non-sensitive orchestration — credentials never enter cloud context, only non-secret handles do."
---

# privacy-tiered-multi-llm-secret-handling

When automation must view, extract, rotate, or store credentials, those
secrets must never flow into a cloud model's context. The pattern is a
privacy tier split : a local provider (or browser/computer-use) handles
the secret-visible step and writes only to a secure store, while the cloud
orchestrator receives non-secret handles and status. This skill encodes
that routing so credential-heavy work can be automated safely.

## When to Use

- An automation needs to view, extract, rotate, or store
  credentials/tokens from dashboards or web UIs.
- A cloud-model orchestrator is driving work that includes a
  secret-handling step.

## When NOT to Use

- No secrets are involved — plain orchestration; the tiering adds
  pointless complexity.
- A purpose-built secret tool already handles the credential without an
  LLM ever seeing it (e.g. `secret_*` MCP, CLI) — use it directly; don't
  route a value through any model that doesn't need to.
- The secret would unavoidably enter cloud context to complete the task —
  stop and rethink the design rather than leaking it.

## Procedure

1. **Classify by privacy tier before routing.** Decide which steps touch
   secret material and which don't, and assign each to local vs cloud
   accordingly.
2. **Keep secret visibility local.** Use a local provider / browser /
   computer-use for any step that *sees* the secret, and write the value
   only to Keychain / a password manager / a secure store — never echo
   it.
3. **Return only handles upward.** Pass non-secret handles/status back to
   the cloud orchestrator; wipe transient memory/session state that held
   the secret.
