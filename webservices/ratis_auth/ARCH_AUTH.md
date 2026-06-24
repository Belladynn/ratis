---
# Identity
type: service-global
service: ratis_auth
status: production

# Navigation (Obsidian + RAG)
parent: ARCH_RATIS
sub_archs: []
related: [ARCH_CORE, ARCH_REWARDS, ARCH_NOTIFIER, ARCH_referral]

# Technique
port: 8001
tech: [FastAPI, PostgreSQL, Redis, OAuth, JWT, Stripe]
tables: [users, user_identities, user_preferences, refresh_tokens, subscriptions, referrals]
env_vars: [DATABASE_URL, JWT_PRIVATE_KEY_PATH, JWT_PUBLIC_KEY_PATH, JWT_AUDIENCE, GOOGLE_CLIENT_ID, APPLE_CLIENT_ID, STRIPE_SECRET_KEY, STRIPE_WEBHOOK_SECRET, REDIS_URL, REWARDS_BASE_URL, NOTIFIER_URL, INTERNAL_API_KEY]

# Business
tags: [auth, oauth, jwt, security, session, stripe, rgpd, subscription]
business_domain: auth
rgpd_concern: true

# Freshness
updated: 2026-06-12
---

# ratis_auth — identity, sessions, subscriptions

> FastAPI service (port 8001) responsible for Ratis user identity: OAuth Google+Apple → JWT HS256, stateful Redis refresh tokens, Stripe subscription accounts, and the GDPR `DELETE /account` lifecycle (in-place anonymisation). No password in V1.
> @tags: auth oauth google apple jwt redis refresh-token stripe subscription account-deletion login register rgpd
> @status: LIVRÉ V0
> @subs: auto

> [[ARCH_RATIS]] · sub-ARCHs: none (everything is in this document) · relations: [[ARCH_CORE]], [[ARCH_REWARDS]], [[ARCH_NOTIFIER]], [[ARCH_referral]]

## Index

- [Summary in one sentence](#summary-in-one-sentence) · L.48
- [Responsibility](#responsibility) · L.52
- [Exposed endpoints](#exposed-endpoints) · L.61
- [Owned tables](#owned-tables) · L.86
- [User identifiers](#user-identifiers) · L.94
- [Internal dependencies (other ratis services)](#internal-dependencies-other-ratis-services) · L.110
- [External dependencies (third-parties)](#external-dependencies-third-parties) · L.100
- [Key architecture decisions](#key-architecture-decisions) · L.106
- [Main flow](#main-flow) · L.138
- [DELETE /account — full lifecycle](#delete-account--full-lifecycle) · L.182
- [GDPR constraints specific to this service](#gdpr-constraints-specific-to-this-service) · L.164
- [Things to know (vectorised FAQ)](#things-to-know-vectorised-faq) · L.172
- [Sub-ARCHs](#sub-archs) · L.201
- [Glossary](#glossary) · L.205

---

## Summary in one sentence

ratis_auth is the FastAPI service (port 8001) that manages Ratis user identity: delegated OAuth authentication only (Google + Apple), stateful refresh token rotation, account preferences, Stripe subscriptions, and the GDPR `DELETE /account` procedure via in-place anonymisation. Email/password auth is **decommissioned** (Phase 1 — DA-39).

## Responsibility

- ratis_auth exposes public endpoints `/api/v1/auth/*` (oauth, refresh, me) and `/api/v1/account/*` (profile, preferences, logout, logout-all, stats, rings, subscription, DELETE). Email/password endpoints (`register`, `login`, `change-password`) are **removed** — see DA-39.
- ratis_auth issues RS256 JWTs (`aud=ratis`, signed with the private key) consumed by all other services via `ratis_core.auth.get_current_user` (public-key verification) — no HTTP call to ratis_auth from other services.
- ratis_auth manages the lifecycle of stateful refresh tokens (table `refresh_tokens`) with mandatory rotation on every `/refresh`.
- ratis_auth integrates Stripe for premium subscriptions (checkout + webhook `/webhooks/stripe`).
- ratis_auth creates the user, their `user_preferences`, their `user_cab_balance` (via a call to ratis_rewards) and their referral code at registration, all in a single transaction.
- ratis_auth applies the GDPR `DELETE /account` procedure via in-place anonymisation (soft-delete `is_deleted=true`, behavioural PII purged, legal transactions retained).

## Exposed endpoints

Full auto-generated inventory in `ENDPOINTS.md` (section `ratis_auth`). Functional summary:

**`/api/v1/auth/*` — authentication**
- ~~`POST /register`~~ — **removed** (DA-39 — Phase 1 OAuth-only)
- ~~`POST /login`~~ — **removed** (DA-39 — Phase 1 OAuth-only)
- `POST /oauth` — `{provider: "google"|"apple", token}` → `TokenResponse`. Verifies `email_verified` before any account creation (see DA-06). Resolution **strictly by `(provider, provider_id)`** in `user_identities` (see DA-45): known identity → login for the owning account; unknown identity → creation of a new account. **No more auto-link by email** — same email + different provider = separate accounts.
- `GET /me` — Bearer → user profile
- `POST /refresh` — refresh_token → new pair (stateful JTI rotation), rate-limited

**`/api/v1/account/*` — account management**
- `GET /profile` · `PATCH /profile` (display_name, avatar_url nullable via `model_fields_set`)
- `GET /preferences` · `PATCH /preferences`
- ~~`POST /change-password`~~ — **removed** (DA-39 — Phase 1 OAuth-only)
- `GET /identities` — Bearer → list of OAuth identities linked to the account (see DA-45)
- `POST /link-provider` — `{provider, token}` → links an additional OAuth identity to the current account. `409 identity_already_linked` if the identity already belongs to another account.
- `DELETE /identities/{provider}` — detaches an OAuth identity from the account. `409 cannot_unlink_last_identity` if it is the last one (would lock the user out).
- `POST /logout` (revokes current token) · `POST /logout-all` (revokes all)
- `GET /stats` — aggregated stats for the Profile screen
- `POST /rings/claim` — claim a pending ROI ring
- `DELETE /account` — GDPR tombstone

**`/api/v1/account/subscription` — Stripe subscriptions**
- `POST` (checkout) · `GET` (status) · `DELETE` (cancellation)

**`/webhooks/stripe`** — Stripe webhook signed with `STRIPE_WEBHOOK_SECRET`.

## Owned tables

- **`users`** — user profile. PK `id` UUID, `email` (informational contact field — **no longer UNIQUE** since Phase 2, see DA-45), `support_id` (`RTS-XXXXXX` UNIQUE, public non-PII — see § User identifiers), `account_type` (`oauth|internal|deleted|dev` — account state, **replaces `provider`/`provider_id`** since Phase 2, see DA-45), `display_name`, `avatar_url`, `is_deleted` (GDPR soft-delete), `ref_lat`/`ref_lng` (reference position ~200m precision). No first or last name ever stored.
- **`user_identities`** — one row per OAuth identity linked to an account. Columns `id`, `user_id` (FK `users`), `provider` (`google|apple`), `provider_id` (stable OAuth subject), `email` (provider snapshot), `created_at`. Constraint `UNIQUE(provider, provider_id)` — a given OAuth identity belongs to exactly one account. Source of truth for OAuth login resolution (see DA-45).
- **`user_preferences`** — user preferences (language, search radius, transport mode, etc.). Always created at registration via `get_or_create()`.
- **`refresh_tokens`** — stateful JWT refresh. Columns `jti` (PK), `user_id`, `expires_at`, `revoked_at`. Rotation on every `/refresh`.
- **`subscriptions`** — Stripe subscriptions. CHECK `payment_ref_coherence`: if `status='active'` then `payment_provider_ref` is required. Never purged (legal constraint).
- **`referrals`** — referral table (linked to [[ARCH_referral]], write shared with ratis_rewards).

## User identifiers

Three distinct identifiers coexist on `users`, each with a precise usage:

| Identifier | Type | Format | Usage |
|---|---|---|---|
| `id` | UUID v4 | 36 chars | Stable internal identity. FK from all tables. Never shown to the user (too long to dictate). |
| `email` | string | RFC-5322 | Informational contact channel (snapshot from the OAuth provider). **Not an account key** since Phase 2 — non-UNIQUE, two accounts can legitimately share an email (see DA-45). **PII** — must not be shared publicly. |
| `support_id` | string | `RTS-XXXXXX` (10 chars) | Public, non-PII, dictation-friendly. Displayed in the mobile profile (PR-CL-PROFIL) and admin panel (PR UI-1.5). Used for support tickets on Twitter / by phone. |

Generation of `support_id`: `ratis_core.identifiers.generate_support_id` draws 6 chars from the alphabet `[A-HJ-NP-Z2-9]` (32 chars, without I/O/0/1 to avoid visual confusion) via `secrets.choice`. The keyspace is 32⁶ ≈ 1.07 billion — collisions negligible even at 1M users. Centralisation is done in `repositories.user_repository.create_user` which handles a retry on `IntegrityError` (max 5 attempts) — every creation path (OAuth Google, OAuth Apple) goes through this function.

API exposure:
- `GET /auth/me` returns `support_id` (the user can copy it from their profile).
- `GET /admin/users` (list + detail) returns `support_id` and accepts `?support_id=RTS-XXXXXX` as an exact filter (mutually exclusive with `?email_contains=`).
- No unauthenticated endpoint exposes `support_id` (the code remains safe to share but it is the user who decides when to do so).

## Internal dependencies (other ratis services)

- [[ARCH_CORE]] — uses `ratis_core.auth.get_current_user`, `ratis_core.database.make_engine`, `ratis_core.startup.require_env`, `ratis_core.jwt.decode_access_token`, `ratis_core.middleware.RequestIDMiddleware`, `ratis_core.observability.init_sentry`.
- [[ARCH_REWARDS]] — internal HTTP call (via `INTERNAL_API_KEY` + `REWARDS_BASE_URL`) to create `user_cab_balance` at registration and trigger `POST /rewards/referral/signup-bonus` + `POST /rewards/referral/trigger` on subscription.
- [[ARCH_NOTIFIER]] — call `POST /api/v1/notify` via `INTERNAL_API_KEY` to notify the user (welcome, subscription activated).

## External dependencies (third-parties)

- **Google OAuth** — verification of Google tokens (client ID `GOOGLE_CLIENT_ID`).
- **Apple Sign-In** — Apple JWKS verification (client ID `APPLE_CLIENT_ID`, JWKS cache TTL 1h protected by `threading.Lock`). If `APPLE_CLIENT_ID` is empty → Android-only mode.
- **Stripe** — checkout + webhook. Keys `STRIPE_SECRET_KEY` + `STRIPE_WEBHOOK_SECRET`.

## Key architecture decisions

### DA-01 — Stateful refresh tokens with mandatory rotation

**Choice**: table `refresh_tokens(jti, user_id, expires_at, revoked_at)` consulted on every `/refresh`. The old JTI is revoked, a new one is issued.
**Rejected alternative**: stateless JWT refresh tokens (signature only).
**Reason**: in ratis_auth, stateful enables immediate revocation (logout, logout-all → `revoke_all_for_user`) and blocks stolen tokens after rotation: an attacker replaying an already-used refresh token gets 401. The cost (one DB query per refresh) is acceptable since it only happens roughly every ~15 min on the client side.

### DA-02 — JWTs validated locally by each service (no call to ratis_auth)

**Choice**: all services validate RS256 JWTs with the public key (`JWT_PUBLIC_KEY_PATH`) + `JWT_AUDIENCE=ratis` via `ratis_core.auth.get_current_user`.
**Rejected alternative**: centralised `/auth/verify` endpoint.
**Reason**: in ratis_auth, a verification endpoint would make this service a SPOF on every request across the entire infra. Public-key verification = local O(1) validation, ratis_auth becomes optional at runtime (can be downed without impacting auth in other services). User revocation is handled via the `is_deleted` flag checked in `get_current_user` (→ 401 `account_deleted`).

> **DA-02 — REVOKED (2026-05-18, audit H1).** The reasoning "HS256
> is enough, RS256 = complexity without gain" is wrong: with HS256 the
> shared `JWT_SECRET` allows any service (or its leaked `.env`) to forge
> a token. See DA-44 below. Local O(1) validation per service is
> retained — only the algorithm changes.

### DA-03 — Single polymorphic OAuth endpoint

**Choice**: `POST /oauth` with `provider: Literal["google","apple"]` and `token`.
**Rejected alternative**: separate `/oauth/google` + `/oauth/apple`.
**Reason**: in ratis_auth, a single endpoint simplifies the client (one path, one response schema) and centralises identity resolution.

> **Note (Phase 2 — DA-45)**: the silent link-by-email logic originally described here is **removed**. Resolution is done strictly by `(provider, provider_id)`; linking of multiple identities is now explicit via `POST /account/link-provider`.

### DA-04 — Stripe webhook = source of truth for subscriptions

**Choice**: `POST /webhooks/stripe` (verified via `STRIPE_WEBHOOK_SECRET`) updates `subscriptions.status`. Client endpoints always return the DB state, never the live Stripe state.
**Rejected alternative**: polling Stripe API on every GET.
**Reason**: in ratis_auth, the webhook guarantees event-driven consistency (successful payment, cancelled subscription, card failed) at a single point, independently of user traffic. GETs remain fast (DB read). Reconciliation (lost webhook) is handled by a dedicated batch via `payment_provider_ref`.

### DA-05 — `avatar_url` nullable and clearable via sentinel `model_fields_set`

**Choice**: PATCH profile distinguishes absent (field not provided) vs explicit null (clear the avatar) via `payload.model_fields_set`.
**Rejected alternative**: sentinel value `""` or a dedicated DELETE endpoint.
**Reason**: in ratis_auth, `model_fields_set` is the documented Pydantic v2 pattern (cf. R13 CLAUDE.md), clean and without value hacks.

### DA-06 — `email_verified` check in `oauth_google` / `oauth_apple` (Phase 1 — 2026-05-17)

**Context**: audit 2026-05-17 (finding C1) — auto-link by email without `email_verified` check allowed account takeover: an attacker controlling an OAuth provider without email verification could link their identity to an existing account.

**Choice**: the `email_verified` check is placed in the `oauth_google` and `oauth_apple` handlers (service layer), **not** in the raw verifiers (`verify_google_token`, `verify_apple_token`).

**Reason for placement**: Apple omits the `email` and `email_verified` fields on re-login of an existing account (Apple only returns them on first consent). Placing the check in the raw verifier would block all Apple re-logins for existing accounts. The handler can distinguish the two cases: new accounts → `email_verified` mandatory; existing accounts found via already-registered `provider_id` → `email_verified` not required (the Apple `provider_id` is stable and cannot be impersonated).

**Enacted 2026-05-17.** See spec `docs/superpowers/specs/2026-05-17-oauth-only-auth-design.md`.

### Note — `password_changed_at`: dormant column (cleanup candidate Phase 2)

The column `users.password_changed_at` (to revoke access tokens issued before a password change) has had no writer since the removal of `change_password` (DA-39 Phase 1). It is **dormant**: no code writes to it, and the revocation logic based on `password_changed_at` in `get_current_user` has no effect. Candidate for cleanup in Phase 2 (`user_identities` model) once the schema is reviewed.

### DA-44 — JWT signed in RS256, private key held by ratis_auth alone (audit H1)

**Choice**: ratis_auth signs access + refresh tokens with an RSA-2048
private key (`JWT_PRIVATE_KEY_PATH`). Consumer services
(`ratis_product_analyser`, `ratis_list_optimiser`, `ratis_rewards`) only
hold the public key (`JWT_PUBLIC_KEY_PATH`) — verification only. ratis_auth
is both issuer and consumer.

**Reason**: under HS256 (DA-02, revoked), the shared symmetric `JWT_SECRET`
both signed and verified — any service (or its leaked `.env`) could forge a
token impersonating any user; leaking the `.env` of a single service
compromised the auth of the entire platform. In RS256, a leaked public key
is harmless. One RSA pair per environment (dev/test/prod),
never committed, never in `ratis_core` (which is delivered to all 5 services).

**Rejected**: JWKS endpoint — its benefit (rotation without redeploying
consumers) only matters with independent deployments or external
consumers; Ratis deploys all 5 services together via
docker-compose. YAGNI. Possible future evolution.

**Cutover**: hard cutover, no dual-algo window. HS256 access tokens
(15 min) expire on their own; HS256 refresh tokens fail the
signature check → clean 401 → re-login. Acceptable in alpha.

#### Runbook — rotation of the JWT key pair

1. On the prod host: `./scripts/gen-jwt-keys.sh secrets-new` (generates
   a new pair in `secrets-new/`).
2. Replace `secrets/jwt_private.pem` + `secrets/jwt_public.pem` with the
   new files.
3. `docker compose -f docker-compose.prod.yml up -d` (redeploys all 5
   services — they re-read the mounted PEMs at boot).
4. Consequence: all currently connected users are logged out once and
   re-authenticate (same effect as the initial cutover).

### DA-45 — `user_identities` model + explicit linking (Phase 2 — 2026-05-18)

**Context**: Phase 1 (DA-39) decommissioned email/password auth but left on `users` the columns `provider`/`provider_id` and the `auth_coherence` constraint, inherited from the V0 model. OAuth identity was attached to `users` in a 1:1 relationship — a user could only own one identity, and OAuth resolution fell back on a fragile email auto-link (origin of CRITICAL C1).

**Choice**:
- A new table **`user_identities`** — one row per OAuth identity (`user_id`, `provider`, `provider_id`, `email`, `created_at`), `UNIQUE(provider, provider_id)`. An account can own multiple identities (Apple AND Google).
- OAuth login resolution is done **strictly by `(provider, provider_id)`** in `user_identities`: known identity → login for the owning account, unknown identity → new account. **Auto-link by email is removed** — same email + different provider = two separate accounts. This is intentional: email does not prove account ownership, and auto-link was the C1 attack vector.
- 3 new `/api/v1/account/*` endpoints: `GET /identities` (list), `POST /link-provider` (explicit linking of an additional identity), `DELETE /identities/{provider}` (detachment, refused if last identity — otherwise lockout).
- Columns `users.provider`/`provider_id` are **collapsed** into a single column `users.account_type` (`oauth|internal|deleted|dev`) — an account *state*, no longer an identity *identifier*. The actual identity is externalised into `user_identities`. The CHECK `auth_coherence` and the `UNIQUE(provider, provider_id)` constraint on `users` are dropped.
- The **`users_email_key` UNIQUE constraint is dropped**: `email` becomes an informational contact field (provider snapshot), no longer an account key. Two accounts can legitimately share an email (a user registering via Google then Apple with the same address — spec §4.2).

**Rejected alternative**: keep `users.email` UNIQUE and create sentinel emails (`deleted+<uuid>@…`) for collision cases. Rejected — that is a workaround (R33): email should never have been an account key once delegated auth was adopted. A clean removal of the constraint is preferable to a sentinel artefact that drags on forever.

**Reason for collapse rather than pure drop**: `account_type` keeps a trace of account origin (useful for admin, future `internal`/`dev` accounts) without replaying the role of identifier. Blast radius bounded: migration `20260518_1300_users_account_type` renames the column, drops 4 constraints (`auth_coherence`, `users_provider_provider_id_key`, `provider_check`, `users_email_key`) with `DROP CONSTRAINT IF EXISTS` (R07).

**Enacted 2026-05-18.** See spec `docs/superpowers/specs/2026-05-17-oauth-only-auth-design.md` (Phase 2) + `DECISIONS_ACTED.md`. Known concurrency pitfall on linking: **KP-100**.

### DA-46 — Registration kill-switch (`auth.registrations_open`)

**Context**: the public alpha opens registration via a public link. It must be possible to **close new registrations** instantly (load cap, incident, end of alpha wave) without touching code or cutting auth for already-registered accounts.

**Choice**:
- Boolean flag **`auth.registrations_open`** in settings (`ratis_settings.json` section `auth`, overridden by `app_settings.auth` via the admin UI), **default `true`** → behaviour unchanged as long as it is not flipped.
- The gate is placed **only on the creation branch** of `services/auth_service.py::_resolve_or_create_oauth_user`, just before `user_repo.create_user()`. The *resolve* branch (already-known `(provider, provider_id)` identity) **is not gated**: an existing user continues to log in even when registrations are closed. Covers both Google and Apple (shared creation path).
- Closed → raises `RegistrationsClosedError` (custom service exception), mapped by the `/oauth` route to **HTTP 503 `registrations_closed`** (distinct from the generic `upstream_service_error`). i18n key `errors.registrations_closed` on the client side.
- **Fail-open** read: `load_settings().get("auth", {}).get("registrations_open", True)` — a partially seeded `app_settings` (missing `auth` section) never accidentally locks registrations.

**Rejected alternative**: env var `REGISTRATIONS_OPEN`. Rejected — an operational flag that needs to be flipped hot without redeployment belongs to settings (R19), not the environment; and the admin settings UI (`app_settings`) already provides the zero-friction lever.

**Enacted 2026-06-12.** Implemented in TDD (`tests/test_registration_killswitch.py`). No migration (JSON settings + existing `app_settings`).

## Main flow

### Flow 1 — Registration / Login (OAuth only — DA-39)

> `POST /register` and `POST /login` email/password are **removed**. The only entry path is `POST /api/v1/auth/oauth`.

1. The client calls `POST /api/v1/auth/oauth` with `{provider: "google"|"apple", token}`.
2. ratis_auth verifies the token against the provider (Google tokeninfo or Apple JWKS), then resolves the identity **strictly by `(provider, provider_id)`** in `user_identities` (DA-45). Known identity → login for the owning account. Unknown identity → new account (there is **no** email auto-link — same email + different provider gives two separate accounts). For a **new account**, `email_verified` must be `true` (DA-06) — otherwise 400 `email_not_verified`. For an existing account found via `provider_id`, `email_verified` is not required (Apple does not re-emit it on re-login).
3. ratis_auth creates the `users` row (`account_type='oauth'`) + the `user_identities` row + `user_preferences` (via `get_or_create()`) in a single transaction (if new account).
4. ratis_auth calls ratis_rewards (`INTERNAL_API_KEY`) to create `user_cab_balance` + generate the referral code (if new account).
5. ~~If `referral_code` provided~~ — referral at signup no longer has an input vector since the removal of `register`. See **KP-95**.
6. ratis_auth issues an RS256 JWT access token (15 min) + a stateful refresh token (30 days) → inserts the JTI into `refresh_tokens`.
7. The client receives `{access_token, refresh_token, expires_in}`.

### Flow 2 — Refresh with rotation

1. The client (near expiry) calls `POST /api/v1/auth/refresh` with the current refresh token.
2. ratis_auth decodes the refresh token, retrieves the JTI, checks in DB: not revoked, not expired.
3. ratis_auth revokes the old JTI (`revoked_at = now()`), inserts a new JTI, issues a new access+refresh pair.
4. The client receives the new pair. If an attacker replays the old refresh token → 401 (JTI revoked).

### Flow 3 — DELETE /account (GDPR) — see also § DELETE /account — full lifecycle

1. The client calls `DELETE /api/v1/account` with their Bearer token.
2. ratis_auth sets `users.is_deleted = true`, clears `email`/`display_name`/`avatar_url`, **deletes `user_identities` rows** for this user (the OAuth identity must no longer resolve to a tombstone account), purges tokens `refresh_tokens.revoked_at = now()` for this user.
3. ratis_auth does NOT delete the legal rows (`cashback_withdrawals`, `cashback_transactions`, `subscriptions`) → `user_id` SET NULL via FK.
4. On every subsequent request attempting to use this JWT, `get_current_user` returns 401 `account_deleted` (check `is_deleted`).

## DELETE /account — full lifecycle

> Consolidated (canonical) section. Cross-reference for all account deletion paths (user-initiated and admin-override). Table-level details in [[PRIVACY]] § Account deletion; admin endpoint detail in [[ARCH_admin_endpoints]] § Endpoints (AU — user management).

### User-initiated (mobile front)

**Endpoint**: `DELETE /api/v1/account` (Bearer JWT auth required).

**Flow**:
1. The mobile client (Profile screen → "Delete my account") calls `DELETE /api/v1/account` with their Bearer JWT.
2. ratis_auth resolves the `user_id` via `get_current_user` then runs the in-place anonymisation routine in a single transaction (see § DB side-effects).
3. All JTI refresh tokens for the user are marked `revoked_at = now()` — the user can no longer refresh, and the current token becomes unusable on the next request (`get_current_user` returns 401 `account_deleted` via the `users.is_deleted` check).
4. Response `204 No Content`. The client deletes its local tokens and redirects to the unauthenticated home screen.
5. No notification is sent (the user explicitly initiated the deletion).

**Reversibility**: none. Deletion is immediate and permanent (no 30-day grace period). If the user wants to come back, they must create a new account (new email or new OAuth provider).

### Admin override (support)

**Endpoint**: `POST /api/v1/admin/users/{user_id}/anonymize` (auth `ADMIN_API_KEY`, hosted in ratis_auth — cf [[ARCH_admin_endpoints]] § P2 user management).

**Use case**: the user has lost access to their account (forgotten password + lost email + inaccessible OAuth provider) but wishes to exercise their GDPR right to deletion. Support triggers the deletion without a user token.

**Flow**:
1. Support receives the GDPR request (email, ticket, letter).
2. Support identifies the target `user_id` via the admin panel (`GET /admin/users?email=...` or `?support_id=RTS-XXXXXX`).
3. Support calls `POST /admin/users/{user_id}/anonymize` with `ADMIN_API_KEY`.
4. The anonymisation routine is **identical** to the user-initiated one (same internal service, same DB side-effects, same JTI revocation).
5. Audit trail: the action is traced (admin caller logged).

**Status**: P2 in the admin roadmap (cf [[ARCH_admin_endpoints]]) — not blocking at J+30 (rare in alpha).

### DB side-effects (anonymisation + retention)

Anonymisation follows the **tombstone pattern**: the `users` row is retained to preserve legal FKs, but all identifying PII is erased. Since audit F-AU-3 (2026-05-11), the routine applies a **4-tier policy** that breaks cross-table correlation while preserving the analytical value of aggregated histories.

**Tombstone `users`**: `email → deleted_{uuid}@deleted.invalid`, `display_name`/`avatar_url`/`password_hash`/`provider_id → NULL`, `provider → "deleted"`, `is_deleted → true`.

**Tier 1 — Hard DELETE** (PII or per-user materialised state, no analytical value):
`refresh_tokens`, `user_push_tokens`, `shopping_lists` (CASCADE → items + routes), `product_tracking`, `price_alerts`, `user_sessions`, `user_session_stats`, `notification_logs`, `user_store_preferences`, `user_streaks`, `user_badges`, `leaderboard_snapshots`, `user_cab_balance`, `user_xp_balance`, `user_savings_snapshot`, `notification_outbox`, `product_favorites`.

**Tier 2 — SET NULL** (data preserved for consensus / audit, FK SET NULL in schema):
`scans`, `receipts`, `price_challenge_responses`, `referral_codes`, `stores.suggested_by_user_id`.

**Tier 3 — Per-user anon UUID** (preserves per-user grouping for analytics, breaks re-identification — see `ratis_core.anonymize.anonymize_user_id`):
`user_achievements`, `reward_events`, `user_missions`, `user_battlepass_progress`, `user_battlepass_claims`, `community_challenge_claims`, `community_multipliers`, `mystery_challenge_finds`, `label_sessions`, `mission_xp_records`, `xp_transactions`, `referral_uses.referred_user_id`, `product_name_resolutions`.
- Mechanism: `user_id → anonymize_user_id(real_id, RGPD_ANONYMIZE_SALT)`. Deterministic output = a deleted user always hashes to the same anon UUID → per-user analytics remain valid ("X% unlock achievement Y").
- Security: SHA-256(real_id || salt) truncated to 16 bytes + version 4. Without the salt, a DB-only attacker cannot trace back to the real user (SHA-256 preimage over 2^128 = infeasible).
- Schema: the FK `users.id` is **dropped** on these 13 tables (cf migration `20260511_1000_rgpd_anon_completeness`) — the anon UUID intentionally has no corresponding row in `users`.

**Tier 4 — Static anon sentinel** (NEVER-PURGE financial / legal audit — row retained 5-10 years intact but FK rewritten to the sentinel):
`cabecoin_transactions`, `cashback_transactions`, `cashback_withdrawals`, `gift_card_orders`.
- Mechanism: `user_id → ANON_SENTINEL_USER_ID` (= `00000000-0000-0000-0000-000000000001`, `users` row seeded by migration `20260511_1000_rgpd_anon_completeness`). **All** deleted users point to the same sentinel → per-user correlation completely broken, but the row remains auditable.
- Justification for sentinel vs anon UUID: these tables have **legal** value (accounting obligation under the Code de commerce), not analytical value. The row is kept but attribution is neutralised.

**Not touched**:
- `subscriptions`: carries the Stripe `payment_provider_ref` (customer_id), which is a distinct business identifier with active Stripe coupling. **Out-of-scope F-AU-3** — tombstone correlation accepted, addressed in a separate follow-up if needed.
- `user_cashback_balance`: materialised, retained as-is (the user has waived the residual cashback by deleting — Ratis keeps the balance).
- `user_preferences`: no PII.
- `store_validation_history.triggered_by`: text field `user:<uuid>` or `batch:<name>` — the UUID points to the non-identifiable tombstone, pattern accepted.

**Idempotence**: the routine is re-entrant. A second call = DELETE on already-empty sets + UPDATE to the same anon UUID (deterministic) + re-affirmation of the tombstone. No state flips between two calls.

**Table-by-table detail** (cross-reference + reason per table): see [[PRIVACY]] § Account deletion — tombstone pattern.

### GDPR constraints

- Anonymisation is **in-place and immediate** (no 30-day retention). The user no longer appears in exports / public endpoints as of the processed request.
- **No hard-delete** on `users`: FK RESTRICT constraints from `cashback_withdrawals` (legal accounting obligation 5-10 years) prevent deletion. The tombstone is the only path compatible with GDPR + Code de commerce.
- **Residual cashback**: if the user had a balance at the time of deletion, it is recoverable by Ratis (the user waived it via the deletion). No automatic refund.
- **JWT revocation**: all refresh tokens are revoked (`revoked_at`). The current access token is not technically revocable (stateless JWT), but becomes unusable because `get_current_user` checks `users.is_deleted` on every request → 401 `account_deleted`.
- **Sentry logs**: no PII data is logged during anonymisation (only the `user_id` UUID, which points to the tombstone). Scrubbing is ensured by `RequestIDMiddleware` in ratis_core.
- **No post-deletion notification** (neither email nor push) — the email is already invalidated and the absence of notification is itself a GDPR choice (no residual contact channel on a deleted account).

## GDPR constraints specific to this service

- ratis_auth never stores first or last names in plaintext (only a `display_name` freely chosen by the user).
- ratis_auth stores `ref_lat`/`ref_lng` rounded to ~200m (3 decimal places DECIMAL(9,3)). The postal address is never stored.
- `users.email` is a login identifier only: not logged, not sent to downstream services in JWT claims (only `sub=user_id` is transmitted).
- `DELETE /account` → immediate in-place anonymisation (no 30-day retention). `users` rows are retained with PII cleared to preserve legal FKs.
- The JWT private key (`JWT_PRIVATE_KEY_PATH`) and `password_hash` must never leave the service. Sentry logs (via `init_sentry`) are scrubbed by `RequestIDMiddleware`.

## Things to know (vectorised FAQ)

### Why does ratis_auth use RS256 for JWTs?

Since audit H1 (2026-05-18), ratis_auth signs JWTs in RS256 with an RSA-2048 private key (`JWT_PRIVATE_KEY_PATH`); consumer services only hold the public key (`JWT_PUBLIC_KEY_PATH`) and can only verify. Under the old HS256 model (DA-02, revoked), the shared symmetric `JWT_SECRET` both signed and verified: leaking the `.env` of a single service compromised the auth of the entire platform. In RS256, a leaked public key is harmless. Choice details, rejection of the JWKS endpoint, and rotation runbook: see **DA-44**.

### Why are ratis_auth refresh tokens stateful and not stateless?

In ratis_auth, stateful enables immediate revocation in 2 critical cases: `logout` (current token), `logout-all` (all user tokens). (`change-password` was removed — DA-39.) A stateless refresh token (signature only) would require waiting for the natural expiry of the stolen token. The DB overhead is negligible (one query every ~15 min per active user).

### How to test ratis_auth locally?

1. Start the dev infra: `docker compose up -d` (Postgres + Redis).
2. Create the test DB: `uv run --package ratis_auth pytest` creates `ratis_test` automatically.
3. Run the tests: `uv run --package ratis_auth pytest webservices/ratis_auth/tests/ -v`.
4. Start the service: `uv run --package ratis_auth uvicorn main:app --port 8001 --reload` (from `webservices/ratis_auth/`).

### What is the difference between ratis_auth and ratis_rewards for referrals?

ratis_auth creates the `referrals` row (pending) at registration of the referred user Y if they provide a referral code. ratis_rewards hosts the reward logic: `POST /rewards/referral/signup-bonus` (Y receives 150 CAB) and `POST /rewards/referral/trigger` (X receives CAB + XP + gift card when Y subscribes). See [[ARCH_referral]] for the full flow.

### Why can `APPLE_CLIENT_ID` be empty?

In ratis_auth, if `APPLE_CLIENT_ID` is not defined, the service starts in Android-only mode (Google OAuth only). Useful for dev/preview deployments that do not test iOS. `require_env` accepts an empty string for this variable specifically.

### How does ratis_auth synchronise the creation of `user_cab_balance`?

At registration, ratis_auth makes a synchronous call to `POST /rewards/users/{user_id}/init-balance` (internal) with `INTERNAL_API_KEY`. If the call fails → complete rollback of user creation (Python transaction). This guarantees that an existing user always has a balance (never NULL). See the absolute rule in [[ARCH_REWARDS]] "cab_balance never NULL for existing user".

## Sub-ARCHs

ratis_auth has no sub-ARCH — everything is in this document. Related cross-cutting features are documented in [[ARCH_referral]] (referral, shared with ratis_rewards).

**Phase 2 (upcoming)**: `user_identities` model — dedicated table allowing a user to link multiple OAuth providers (Apple + Google) from their profile, with explicit linking and no fragile email auto-link. See spec `docs/superpowers/specs/2026-05-17-oauth-only-auth-design.md` § Phase 2.

## Glossary

- **DA-XX**: numbered architecture decision (see dedicated section).
- **JTI**: JWT ID, unique identifier of each refresh token, primary key of the `refresh_tokens` table.
- **JWKS**: JSON Web Key Set, endpoint published by Apple to verify Apple OAuth tokens.
- **Stateful rotation**: each `/refresh` revokes the old JTI in DB and issues a new one, making any replay impossible.
- **Link-by-email**: during an OAuth flow (Google/Apple), if the email returned by the provider matches an existing account (found via `provider_id` or email), the provider is linked to the account without creating a duplicate. Since DA-39 (Phase 1), email/password auth is removed — the link is no longer a transition path email→OAuth but the sole entry mode. Phase 2 (`user_identities`) will allow a user to explicitly link multiple OAuth providers from their profile.
- **GDPR tombstone**: `users` row retained with PII cleared + `is_deleted=true`, to preserve legal FKs (cashback, subscriptions).
