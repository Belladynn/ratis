# ADR-0001: Asymmetric RS256 JWT — single issuer, many verifiers

**Status:** Accepted (supersedes the original HS256 design, `ARCH_RATIS` DA-02, revoked)

## Context and Problem Statement

All five Ratis services sit behind a shared JWT (`aud=ratis`). The original design used HS256 with one shared symmetric `JWT_SECRET` that both signed *and* verified tokens. Security audit H1 (2026-05-17) flagged this: any service could forge a token impersonating any user, and a leak of any one service's `.env` compromised authentication for the entire platform. How should tokens be minted and verified so that a single service compromise is not platform-fatal?

## Decision Drivers

- A leaked credential on any one of five services must not let an attacker forge platform-wide identities.
- Verification logic must not be able to drift between services (e.g. one service forgetting the audience check).
- No auth hot-path / single point of failure on the request path.
- Alpha stage tolerates a hard cutover given short token lifetimes.

## Considered Options

- **RS256 (RSA-2048), single issuer, many verifiers** — only `ratis_auth` holds the private key and signs; consumers hold only the public key and verify.
- **Keep HS256 + shared secret** — one symmetric key signs and verifies everywhere.
- **JWKS endpoint with key rotation** — issuer publishes rotating public keys consumers fetch.

## Decision Outcome

Chosen: **asymmetric RS256 (RSA-2048)**. Only `ratis_auth` holds the private key (`JWT_PRIVATE_KEY_PATH`) and signs access + refresh tokens; consumer services (PA, LO, RW) hold only the public key (`JWT_PUBLIC_KEY_PATH`) and verify. Verification (algorithm, `audience=ratis`, expiry, signature) is centralized in `ratis_core/jwt.py::decode_access_token` so it is byte-identical everywhere. One RSA keypair per environment (dev/test/prod), never committed, never placed in `ratis_core` (which ships to all 5 services). Hard cutover with no dual-algo window — HS256 access tokens (15 min) expire on their own; HS256 refresh tokens fail signature verification → clean 401 → re-login.

**Rejected:** HS256 + shared secret (forgeable and single-leak-fatal, the audit finding itself). JWKS with rotation rejected as YAGNI — its only edge, rotating keys without redeploying consumers, matters only with independent deployments or external API consumers, but Ratis deploys all 5 services together via docker-compose; flagged as a future evolution if the API opens to third parties.

**Quality-attribute trade-off:** we bought **security and integrity** (a leaked public key is inert; only `ratis_auth` can mint) at the cost of **operability** of key management (rotation needs a documented runbook and logs out every connected user; no JWKS means consumers cannot rotate independently).

### Consequences

- **Good:** leaked public key is inert; uniform verification logic in one shared module; local verification with no auth SPOF; clean security boundary — only `ratis_auth` can mint tokens.
- **Bad:** key rotation needs a runbook (`gen-jwt-keys.sh`, swap PEMs, redeploy all 5 services) and logs out every connected user once; no JWKS means consumers cannot rotate independently; per-environment keypairs must be managed out-of-band.

**Source.** `webservices/ratis_auth/ARCH_AUTH.md` DA-44; `ratis_core/ARCH_CORE.md` DA-04; supersedes `docs/arch/ARCH_RATIS.md` DA-02 (revoked). Canonical register: [`../decisions/DECISIONS_ACTED.md`](../decisions/DECISIONS_ACTED.md).
