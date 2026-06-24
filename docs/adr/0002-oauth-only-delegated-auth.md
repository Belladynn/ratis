# ADR-0002: OAuth-only delegated authentication (Apple + Google)

**Status:** Accepted (email/password decommissioned; email demoted from account key)

## Context and Problem Statement

The 2026-05-17 audit raised a **CRITICAL (C1)**: account takeover via OAuth linking on an unverified email matching an existing email/password account. The email/password path was never actually used (all users arrived via Apple/Google), had no password-reset flow and no email infrastructure. The V0 `users` table still carried `provider`/`provider_id` as a 1:1 identity plus a `UNIQUE(email)` constraint, and OAuth resolution still fell back to fragile auto-link-by-email. How should authentication be modelled to close C1 without building infrastructure for an unused path?

## Decision Drivers

- Close the C1 account-takeover vector at its root, not with a patch.
- Avoid building password-reset + email infrastructure for a path no real user takes.
- *Sign in with Apple* is mandatory on iOS once any third-party login is offered.
- Apple + Google cover ~100% of the French smartphone market.
- Keep the account's origin queryable for admin and future internal/dev accounts.

## Considered Options

- **Delegated-auth-only (Apple + Google)** with an explicit `user_identities` table keyed on `(provider, provider_id)`.
- **Build proper email/password + reset + email infra** to make the existing path safe.
- **Add Facebook/Meta** as a third provider.
- **Keep `UNIQUE(email)` and mint sentinel emails on collision.**
- **Keep auto-link-by-email.**

## Decision Outcome

Chosen: **delegated-auth-only (Apple + Google)**. Decommission `register`/`login`/`change-password` (DA-39, Phase 1). Phase 2 (DA-45): externalize OAuth identity into a new `user_identities` table (one row per identity, `UNIQUE(provider, provider_id)`); resolve login strictly by `(provider, provider_id)` — no auto-link by email; collapse `users.provider/provider_id` into a single `users.account_type` (`oauth|internal|deleted|dev`) state column; drop the `auth_coherence` CHECK and `users_email_key` UNIQUE, demoting email to an informational contact snapshot. Multiple identities (Apple *and* Google on one account) link explicitly via 3 new `/api/v1/account/*` endpoints.

**Rejected:** building email/password + reset + email infra (a dead V0 remnant with no value — build only on real demand); Facebook/Meta (no added coverage, in decline, reintroduces linking surface); sentinel emails on collision (a workaround per R33 that drags an artifact forever); auto-link-by-email (the C1 vector itself).

**Quality-attribute trade-off:** we bought **security** (zero stored passwords removes the entire password attack surface) and a **simpler identity model**, at the cost of **availability/independence** — the platform is fully dependent on Apple/Google uptime, and a known TOCTOU race on link/unlink-provider (KP-100) is accepted rather than fixed.

### Consequences

- **Good:** no password leak surface; no email infra to build; clean explicit identity model supporting multi-provider linking; bounded migration blast radius (column rename + 4 `DROP CONSTRAINT IF EXISTS`).
- **Bad:** two accounts can legitimately share an email (same person via Google then Apple); the link/unlink TOCTOU race is documented and accepted, not fixed (KP-100); full dependency on Apple/Google availability.

**Source.** `docs/decisions/DECISIONS_ACTED.md` DA-39 and DA-45; `webservices/ratis_auth/ARCH_AUTH.md` DA-45. Canonical register: [`../decisions/DECISIONS_ACTED.md`](../decisions/DECISIONS_ACTED.md).
