# ADR-0011: agent-mcp — a Keychain-backed control plane so the model never sees secrets

**Status:** Accepted

## Context and Problem Statement

Three token leaks occurred across two Claude Code sessions in May 2026, all caused by agents touching env secrets directly (bash `${VAR:-default}` substitution leaking `EXPO_TOKEN`, `cat ~/.zprofile` dumping it, Sentry-token re-export friction). Agents needed to perform ops (deploy, OTA, query Sentry/Stripe/R2) without ever handling cleartext credentials. How can an agent run privileged ops without a secret string ever entering the model context?

## Decision Drivers

- Structurally eliminate the leak class — the model must never manipulate a secret string.
- One surface for unified audit and centralized rotation.
- Zero new infra / zero new external dependency / encrypted at rest.
- Least-privilege scoping, not one all-powerful token.
- A swappable secret backend for the future.

## Considered Options

- **`ratis-agent-mcp`: an MCP server exposing typed tools, tokens in the macOS Keychain.**
- **1Password / Bitwarden CLI.**
- **HashiCorp Vault / Infisical self-hosted.**
- **Encrypted `.env`.**
- **One MCP per provider**, or **a single shared admin/agent token.**

## Decision Outcome

Chosen: build `ratis-agent-mcp`, an MCP server exposing **typed tools** (GlitchTip/EAS/GitHub/Stripe/R2/DB/Docs, ~30+ tools across 9 modules). Tokens live in the **macOS Keychain** (service `ratis-agent-mcp`, one account per provider); the server reads a token at call time, performs the HTTP/CLI call, and writes an append-only audit line. The model only ever sees functional results, never secrets. `keychain.py` exposes a `get_secret(account)` interface so backends (Bitwarden/1Password/AWS Secrets Manager) are swappable later. Least-privilege via two scoped tokens (admin vs ops) with per-tool scope checks that 403 before even touching the Keychain.

**Rejected:** 1Password/Bitwarden CLI (external dependency + subscription); Vault/Infisical (overkill for a single host); encrypted `.env` (stays cleartext in RAM too easily); one MCP per provider (N processes, fragmented audit); a single shared token (no least-privilege).

**Quality-attribute trade-off:** we bought **security** (leak-proof by construction, single audit trail, least-privilege) at the cost of **portability** — V0 is macOS/Keychain-bound and single-host (stdio, no network, no daemon), and security still leans on the OS Keychain and the auth-gate rather than hardware isolation.

### Consequences

- **Good:** leak-proof by construction; single audit trail (JSONL, append-only, perms 600, inode-change tampering detection); least-privilege via two tokens with per-tool scope checks that 403 before touching the Keychain.
- **Bad:** V0 is macOS/Keychain-bound and single-host (stdio, no network, no daemon) — multi-machine/HTTP transport is deferred; security leans on the OS Keychain and auth-gate, not hardware isolation (a planned `ARCH_agent_mcp_isolation` adds a dedicated OS user + Unix-socket peer-cred).

**Source.** `docs/arch/ARCH_agent_mcp.md` (Vision; DA-43 Keychain; DA-44 two tokens; DA-45 stdio local; DA-48 audit). Canonical register: [`../decisions/DECISIONS_ACTED.md`](../decisions/DECISIONS_ACTED.md).
