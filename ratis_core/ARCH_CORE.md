---
# Identity
type: shared-lib-global
service: ratis_core
status: production

# Navigation (Obsidian + RAG)
parent: ARCH_RATIS
sub_archs: []
related: [ARCH_AUTH, ARCH_REWARDS, ARCH_PRODUCT_ANALYSER, ARCH_LIST_OPTIMISER, ARCH_NOTIFIER, ARCH_CLIENT]

# Technical
tech: [SQLAlchemy, psycopg, Pydantic-v2, JWT, FastAPI]
tables: []
env_vars: [DATABASE_URL, JWT_PUBLIC_KEY_PATH, JWT_AUDIENCE, INTERNAL_API_KEY]

# Business
tags: [shared, lib, auth, database, config, models, rewards_client, notifier_client]
business_domain: infra
rgpd_concern: false

# Freshness (MANDATORY — R34)
updated: 2026-05-18
---

# ratis_core — ARCH shared Python lib

> Shared Python lib imported by all Ratis services: `auth` (get_current_user), `database` (make_engine, get_db), `deps` (verify_internal_key), `jwt`, `knowledge`, `notifier_client`, `rewards_client`, `schemas`, `settings`, `startup` (require_env), `uploads`, `utils`, `geo`. Never duplicate (R18).
> @tags: shared lib auth database config models rewards_client notifier_client core sqlalchemy psycopg pydantic jwt fastapi r18-never-duplicate
> @status: LIVRÉ V0
> @subs: auto

> Parent : [[ARCH_RATIS]] · Relations : [[ARCH_AUTH]] · [[ARCH_PRODUCT_ANALYSER]] · [[ARCH_LIST_OPTIMISER]] · [[ARCH_REWARDS]] · [[ARCH_NOTIFIER]] · [[ARCH_CLIENT]]

## Index

- [One-sentence summary](#one-sentence-summary) · L.45
- [Responsibility](#responsibility) · L.49
- [Structure](#structure) · L.60
- [External dependencies](#external-dependencies) · L.114
- [Who uses ratis_core?](#who-uses-ratis_core) · L.124
- [Key architecture decisions](#key-architecture-decisions) · L.135
- [Flow: how a service uses ratis_core](#flow-how-a-service-uses-ratis_core) · L.186
- [GDPR constraints specific to ratis_core](#gdpr-constraints-specific-to-ratis_core) · L.212
- [Vectorised FAQ](#vectorised-faq) · L.221
- [Glossary](#glossary) · L.251

---

## One-sentence summary

ratis_core is the shared Python 3.12 lib, imported by all 5 FastAPI webservices and the 9 Ratis batches, which exposes SQLAlchemy 2.0 models, JWT/auth/DB helpers, inter-service HTTP clients (rewards_client, notifier_client, payout_client), and the configuration loader (`ratis_settings.json` + `app_settings` table).

## Responsibility

- ratis_core is the single source of truth for **SQLAlchemy 2.0 models** used by all Ratis services (tables users, scans, receipts, products, stores, shopping_lists, cabecoins_transactions, cashback_*, etc.) — services never redefine a model locally.
- ratis_core provides the **authentication helpers** (`get_current_user`, `get_http_current_user`, `decode_access_token`, `verify_internal_key`, `verify_admin_key`) that each service injects via FastAPI `Depends`.
- ratis_core exposes the **inter-service HTTP clients** (`rewards_client`, `notifier_client`, `payout_client`) so that, for example, `ratis_product_analyser` can call `trigger_scan_accepted()` in `ratis_rewards` without duplicating the call logic.
- ratis_core centralises **versioned configuration** via `load_settings()` (reads `ratis_core/config/ratis_settings.json`) and loads the `retailers_fr.json` catalogue.
- ratis_core provides **classification utilities** (`knowledge.load_knowledge`, `knowledge.classify`) based on `classification_rules.json` for the `ratis_product_analyser` OCR pipeline.
- ratis_core provides **GDPR-aware validation helpers** (`uploads.validate_image_upload`, `schemas.check_timezone`, `utils.assert_owner`).
- ratis_core provides the **observability infrastructure**: `RequestIDMiddleware`, Sentry init (`sentry`), the `require_env()` helper for fail-fast at lifespan, and the observability middleware (`observability.py`).
- ratis_core provides **shared business helpers** (consensus, savings, normalize) used by both the FastAPI backend and the batches.

## Structure

Directory: `ratis_core/ratis_core/`

```
ratis_core/ratis_core/
├── __init__.py               # re-exports Base, get_db, main models
├── auth.py                   # get_current_user, get_http_current_user
├── config/
│   ├── ratis_settings.json   # versioned business parameters (consensus, CAB, BP)
│   └── retailers_fr.json     # FR retailers catalogue for OSM matching
├── consensus.py              # price consensus calculation helpers (shared PA + batch)
├── data/                     # embedded data (fixtures, references)
├── database.py               # Base SQLAlchemy, make_engine(url), SessionLocal, get_db
├── deps.py                   # FastAPI Depends — get_bearer_token, verify_internal_key, verify_admin_key
├── exceptions.py             # UpstreamServiceError + other cross-cutting exceptions
├── jwt.py                    # decode_access_token + decode_refresh_token (RS256 public-key verify, aud=ratis)
├── knowledge.py              # load_knowledge, classify — OCR product classification
├── middleware.py             # RequestIDMiddleware for inter-service traceability
├── models/                   # SQLAlchemy 2.0 models (source of truth)
│   ├── analytics.py
│   ├── batch_sync_log.py
│   ├── city.py
│   ├── gamification.py       # CabecoinsTransaction, UserCabBalance, Badge, LevelTier, StreakTier, LeaderboardSnapshot, Subscription, RewardConfig, UserBadge, UserStreak, UserSessionStat, DiscountCampaign
│   ├── mystery.py            # mystery product
│   ├── notifications.py      # NotificationLog, UserPushToken
│   ├── price.py              # PriceConsensus, PriceConsensusHistory, PriceConsensusScans, PriceAlert, PriceChallenge, PriceChallengeResponse
│   ├── product.py            # Product, Category, ProductTracking, AffiliateOffer
│   ├── referral.py           # referral tables (code, history, 30-day anti-churn)
│   ├── retailer.py
│   ├── retailer_receipt_format.py
│   ├── rewards.py            # CashbackTransaction, CashbackWithdrawal, UserCashbackBalance
│   ├── scan.py               # Scan, Receipt
│   ├── settings.py           # app_settings table (runtime config)
│   ├── shopping.py           # ShoppingList, ShoppingListItem, OptimizedRoute
│   ├── store.py              # Store (soft-delete is_disabled)
│   ├── store_candidate.py    # store candidates in cold-start
│   ├── store_fingerprint.py  # store fingerprint for OCR resolver
│   └── user.py               # User (soft-delete is_deleted), UserPreferences, UserStorePreference, UserSession
├── normalize.py              # string normalisation (store names, products)
├── notifier_client.py        # notify_user(user_id, ...) → HTTP call to ratis_notifier
├── observability.py          # Sentry helpers + request_id propagation
├── payout_client.py          # HTTP client for referral payout (batch → rewards)
├── rewards_client.py         # trigger_scan_accepted, trigger_referral_reward, trigger_mission_progress...
├── savings.py                # user savings calculation (used by savings batch)
├── schemas.py                # Cross-service Pydantic schemas only (User*, Token*, Login*, ProductDetailResponse, UserPreferences*, SubscriptionResponse, check_timezone) — see DA-08
├── seed/                     # DB seed fixtures (dev + test)
├── seed_settings.py          # initial seed of app_settings from ratis_settings.json
├── settings.py               # load_settings() → reads ratis_settings.json
├── startup.py                # require_env("VAR1", "VAR2") — fail-fast lifespan
├── uploads.py                # validate_image_upload — check type/size/magic-bytes
└── utils.py                  # assert_owner, strip_str, match_str — miscellaneous utilities
```

## External dependencies

- **SQLAlchemy 2.0** — ORM, `Base` declaration and models
- **psycopg[binary] v3** — PostgreSQL driver (URL `postgresql+psycopg://`, see DA-03 of [[ARCH_RATIS]] / pitfall P01)
- **Pydantic v2** — schemas, validation
- **PyJWT** — encode/decode JWT RS256 (private-key signing in ratis_auth, public-key verification everywhere)
- **httpx** — inter-service HTTP calls (rewards_client, notifier_client, payout_client)
- **Sentry SDK** — Sentry init (no-op if DSN is empty)
- **FastAPI** (only for `Depends` / `HTTPException` types in `deps.py` and `auth.py`)

## Who uses ratis_core?

**All Ratis services and batches import `ratis_core`**:

- [[ARCH_AUTH]] — models `User`, `UserSession`, `Subscription` + `jwt.py` + `auth.py` + `notifier_client` + `rewards_client`
- [[ARCH_PRODUCT_ANALYSER]] — models `Scan`, `Receipt`, `Product`, `Store`, `PriceConsensus*` + `consensus.py` + `knowledge.py` + `normalize.py` + `rewards_client` + `notifier_client` + `uploads.py`
- [[ARCH_LIST_OPTIMISER]] — models `ShoppingList`, `ShoppingListItem`, `OptimizedRoute`, `Store`, `Product` + `auth.py` + `settings.py`
- [[ARCH_REWARDS]] — models `CabecoinsTransaction`, `UserCabBalance`, `CashbackTransaction`, `CashbackWithdrawal`, `UserCashbackBalance`, `RewardConfig`, `Badge`, `LevelTier`, `StreakTier`, `UserBadge`, `UserStreak`, `UserSessionStat`, `LeaderboardSnapshot` + `settings.py` + `notifier_client` + `deps.verify_admin_key`
- [[ARCH_NOTIFIER]] — models `NotificationLog`, `UserPushToken`, `User` + `deps.verify_internal_key`
- Batches (`batch/ratis_batch_*`) — all use `make_engine()`, the relevant models, and settings

## Key architecture decisions

### DA-01 — Monorepo workspace instead of a published lib

**Choice**: `ratis_core` lives in the Ratis monorepo as a `uv` workspace package (`ratis_core/pyproject.toml`), imported by services via `uv sync --package <svc>`.
**Rejected alternative**: publish `ratis_core` to a private index (Nexus, CodeArtifact, private PyPI) with semver versions.
**Reason**: Ratis is a single-team, single-repo project. Publishing `ratis_core` would impose a "bump version → publish → bump in each service → CI reinstall" cycle on every SQLAlchemy model change, significantly slowing down Alembic migrations (which often touch `ratis_core/models/` AND the services simultaneously). The uv workspace guarantees that all services use **exactly** the same version of `ratis_core` at all times (a single `uv.lock`). Accepted trade-off: `ratis_core` cannot be consumed from a service outside the monorepo (not a V1 requirement).

### DA-02 — Centralised SQLAlchemy models (not per service)

**Choice**: all Ratis table models live in `ratis_core/models/`, never duplicated in the services.
**Rejected alternative**: each service declares its own models ("database per service" pattern from true microservices).
**Reason**: Ratis shares a single physical PostgreSQL database across the 5 services (see DA of [[ARCH_RATIS]]). Declaring a `User` model in `ratis_auth/models.py` and another in `ratis_rewards/models.py` would create schema divergence risks and break FK traceability. Centralised models in `ratis_core` guarantee that Alembic autogenerate always sees the full set of tables and detects any drift.

### DA-03 — Pydantic v2 (never v1)

**Choice**: `ratis_core.schemas` and all services use Pydantic v2 (`model_validate`, `model_dump`, `model_fields_set`).
**Rejected alternative**: stay on Pydantic v1 (`.dict()`, `.parse_obj()` API).
**Reason**: FastAPI 0.100+ aligns with Pydantic v2. v2 is ~5× faster at validation (Rust core), natively supports discriminated unions (useful for polymorphic scan payloads receipt/label/manual), and enables the PATCH pattern via `model_fields_set` which distinguishes "field absent" vs "field explicitly null" (see R13 in `SA_DEV.md`).

### DA-04 — Centralised JWT RS256 in ratis_core.jwt

**Choice**: a single `ratis_core/jwt.py` module exposes `decode_access_token()` + `decode_refresh_token()` used by all services. Signing (private key) happens only in `ratis_auth`; verification (public key) happens in all services via `ratis_core`.
**Rejected alternative**: each service implements its own JWT verification.
**Reason**: centralising in `ratis_core.jwt` guarantees that verification (RS256 algorithm, `ratis` audience, expiry, public key `JWT_PUBLIC_KEY_PATH`) is identical everywhere. A verification bug in a single service (e.g., ignoring the audience) would create a global security vulnerability. The `auth.get_current_user` helper relies on `jwt.decode_access_token` and injects it via FastAPI `Depends`. RS256 vs HS256 choice: see DA-44 of [[ARCH_AUTH]].

### DA-05 — Fail-fast on missing env vars (`require_env`)

**Choice**: each service calls `require_env("VAR1", "VAR2", ...)` in its `lifespan` at startup. If an env var is missing, the service crashes immediately.
**Rejected alternative**: `os.environ.get("VAR", "default")` with a silent default value.
**Reason**: secrets and critical URLs (`DATABASE_URL`, `JWT_PUBLIC_KEY_PATH`, `INTERNAL_API_KEY`) must never have a fallback. A service that starts without `JWT_PUBLIC_KEY_PATH` cannot verify any JWT and would accept — or reject — requests unpredictably. The fail-fast at lifespan catches the problem at deploy time, not after the first production request.

### DA-06 — Shared settings as versioned JSON

**Choice**: static business parameters (consensus thresholds, CAB grid, BP multipliers) live in `ratis_core/config/ratis_settings.json`, committed to the repo. Parameters that need to change in production without redeployment live in the `app_settings` table (values in cents).
**Rejected alternative**: everything in `app_settings`, or everything hardcoded in Python.
**Reason**: a versioned JSON allows every business rule change (e.g., consensus rate, battle pass tier) to be reviewed in a PR — perfect git trace. But some parameters must be adjustable in production without redeployment (marketing campaign, temporary multiplier) → `app_settings`. Hardcoding in Python is forbidden by rule R19.

### DA-07 — Inter-service HTTP clients in ratis_core

**Choice**: inter-service calls (e.g., `ratis_product_analyser` → `ratis_rewards`) go through a centralised module `ratis_core/rewards_client.py` (and `notifier_client.py`, `payout_client.py`) instead of ad-hoc `httpx` calls in each service.
**Rejected alternative**: each service implements its httpx calls inline.
**Reason**: centralising in `ratis_core` guarantees that the signature, the `INTERNAL_API_KEY` header, the timeout, and the error mapping (`UpstreamServiceError` → 503) are uniform. A retry bug or a poorly-handled timeout fixed in one place benefits everyone. Signatures are typed (Pydantic) to detect mismatches at lint time.

### DA-08 — `ratis_core.schemas` reserved for cross-service schemas

**Choice**: `ratis_core/schemas.py` contains **only** the Pydantic schemas that are effectively imported by multiple services (or cross-cutting concerns like `check_timezone`). Service-local request/response schemas live next to their consuming route in `webservices/<service>/` (typically in `routes/<feature>.py` or a service-local `schemas.py`).
**Rejected alternative**: an exhaustive "shared schemas" file covering every table (a pattern inherited from before the idiomatic FastAPI migration).
**Reason**: a code health audit on 2026-05-09 (F-1) found that ~80% of the classes in `schemas.py` were dead — each service had migrated its schemas inline to benefit from route/validation/service co-location, without cleaning up `ratis_core/schemas.py`. Keeping unused schemas (a) inflates the diff of every PR touching `ratis_core`, (b) creates the illusion of a stable API and encourages using it rather than iterating service-locally, (c) doubles the maintenance surface on model changes. Schemas retained (May 2026): `UserCreate`, `UserUpdate`, `UserResponse`, `TokenResponse`, `LoginRequest`, `ProductDetailResponse`, `UserPreferencesUpdate`, `UserPreferencesResponse`, `SubscriptionResponse`, plus the `check_timezone` function and the `ORMModel` / `_DisplayNameMixin` bases.
**Usage rule**: before adding a schema to `ratis_core/schemas.py`, verify it will be imported by ≥ 2 services. Otherwise, place it in the consuming service.

## Flow: how a service uses ratis_core

### Flow 1 — FastAPI service starts up

1. The service (e.g., `ratis_rewards/main.py`) imports `ratis_core.startup.require_env` and `ratis_core.sentry.init_sentry`.
2. In the `@asynccontextmanager async def lifespan(app)`, the service calls `require_env("DATABASE_URL", "JWT_PUBLIC_KEY_PATH", "INTERNAL_API_KEY", ...)` — fail-fast if any var is missing.
3. The service calls `init_sentry("ratis_rewards")` — no-op if `SENTRY_DSN` is empty.
4. The service creates its engine via `ratis_core.database.make_engine(DATABASE_URL)`.
5. Routes receive `db: Session = Depends(ratis_core.database.get_db)` and `user = Depends(ratis_core.auth.get_current_user)`.

### Flow 2 — Service triggers a reward in ratis_rewards

1. `ratis_product_analyser` validates a scan (`scan.status = accepted`).
2. It calls `ratis_core.rewards_client.trigger_scan_accepted(user_id=..., scan_id=..., amount_cents=...)`.
3. `rewards_client` sends a `POST /rewards/events/scan_accepted` to `ratis_rewards` with the header `Authorization: Bearer <INTERNAL_API_KEY>` (not a user JWT).
4. `ratis_rewards` verifies the key via `ratis_core.deps.verify_internal_key`, credits CAB, advances missions, updates battle pass.
5. If `ratis_rewards` is down, `rewards_client` raises `UpstreamServiceError` → `ratis_product_analyser` returns 503 to the user (rule R21).

### Flow 3 — Adding a new shared model

1. The developer adds `ratis_core/models/new_thing.py` with the SQLAlchemy class.
2. They register it in `ratis_core/models/__init__.py` and re-export from `ratis_core/__init__.py` if needed.
3. They create an Alembic migration (`alembic/versions/<id>_add_new_thing.py`) — autogenerate sees the new model via `ratis_core.database.Base.metadata`.
4. They commit migration + model **together** (rule R24 workflow).
5. Services consuming the model import it via `from ratis_core.models import NewThing` or `from ratis_core import NewThing`.

## GDPR constraints specific to ratis_core

ratis_core is not a running service — it logs nothing itself. But it defines the GDPR constraints that services must respect:

- The `User`, `Scan`, `Receipt`, `OptimizedRoute` models carry the soft-delete and retention flags/columns (`is_deleted`, `is_disabled`, `image_deleted_at`).
- `ratis_core.uploads.validate_image_upload` rejects any non-compliant image (type, size, magic bytes) before services write to R2.
- `ratis_core.utils.assert_owner` guarantees that a user can only access their own resources (used by all routes that read a `user_id`).
- The `normalize.py` module centralises OCR string normalisation to avoid storing PII variants in the `product_knowledge` table.

## Vectorised FAQ

### How do I add a shared SQLAlchemy model in ratis_core?

In Ratis, any new model shared between services is created in `ratis_core/ratis_core/models/<topic>.py`, then registered in `ratis_core/ratis_core/models/__init__.py`. An Alembic migration must be generated immediately in `alembic/versions/` and committed **together** with the model (rule R24). Consuming services import it via `from ratis_core.models import MyModel` — never redefine a model locally (see DA-02). Remember to regenerate `db/schema.sql` after migration.

### How do I import ratis_core in a new batch?

A new batch (e.g., `batch/ratis_batch_xyz/`) must declare the dependency `ratis_core = { workspace = true }` in its `pyproject.toml`. Then `uv sync --package ratis_batch_xyz` installs `ratis_core` from the workspace. The batch typically uses `from ratis_core.database import make_engine; engine = make_engine(os.environ["DATABASE_URL"])` then `with Session(engine) as db:` for its operations. The batch has no FastAPI lifespan — it calls `load_settings()` and `require_env()` manually if needed.

### Why not a proper lib published on a private PyPI?

ratis_core is too tightly coupled to the Ratis DB to be useful outside the project: its SQLAlchemy models exactly reflect the PostgreSQL schema shared by the 5 services, and an Alembic migration systematically touches `ratis_core/models/` + the consuming service. Publishing would force a "bump version → publish → bump in each service → CI reinstall" cycle that would slow down every migration. The uv monorepo workspace solves the problem without added complexity (see DA-01 and DA-09 of [[ARCH_RATIS]]). Publishing will be considered if Ratis opens its API to external consumers.

### Which ratis_core modules must I NEVER duplicate?

Rule R18 in `SA_DEV.md` explicitly lists the shared modules that must never be re-implemented in a service: `auth` (get_current_user, get_http_current_user), `database` (make_engine, get_db), `deps` (get_bearer_token, verify_internal_key, verify_admin_key), `jwt` (decode_access_token), `knowledge` (load_knowledge, classify), `notifier_client` (notify_user), `rewards_client` (trigger_scan_accepted, trigger_referral_reward), `schemas` (check_timezone), `settings` (load_settings), `startup` (require_env), `uploads` (validate_image_upload), `utils` (assert_owner, strip_str, match_str). Duplicating any of these modules immediately creates a divergence risk and a security regression.

### How does ratis_core handle JWT verification?

ratis_core centralises JWT verification in `ratis_core/jwt.py` (`decode_access_token`), which checks the RS256 algorithm, the `ratis` audience, expiry, and the signature via the public key `JWT_PUBLIC_KEY_PATH`. The `ratis_core.auth.get_current_user` helper uses this module and is injected into a FastAPI route via `user = Depends(get_current_user)`. JWT signing (issuance) happens only in `ratis_auth` using the private key (see [[ARCH_AUTH]]). All other services only hold the public key: they verify only, never issue (see DA-02 / DA-44 of [[ARCH_AUTH]]).

### How do I test ratis_core locally?

ratis_core has no tests of its own in V1 — service tests (`webservices/*/tests/`) exercise `ratis_core` indirectly via SQLAlchemy fixtures and auth helpers. Rule R01 (TDD) requires writing the test in the consuming service before implementing a change in `ratis_core`. To test locally: `uv run pytest webservices/ratis_auth/tests/ -v` (or any other service), having first run `docker compose up -d` for PG + Redis. SQLAlchemy models are exercised via the `assert_no_pending_changes` fixture that detects forgotten `db.commit()` calls (pitfall P13).

### What happens if a required env var is missing at startup?

Each Ratis service calls `ratis_core.startup.require_env("VAR1", "VAR2", ...)` in its `lifespan`. If a var is missing or empty, `require_env` raises an `EnvironmentError` that crashes the service before it accepts the first request. This is intentional: a service that starts without `JWT_PUBLIC_KEY_PATH` could not verify any JWT, so a visible crash at deploy time is preferred over a half-functioning service (see DA-05). Rule R20 requires adding any new env var simultaneously in three places: `.env.example`, `conftest.py` (test value), and `require_env()` in the lifespan.

## Glossary

- **DA-XX**: numbered architecture decision (see dedicated section)
- **KP-XX**: known problem / pitfall numbered in `KNOWN_PROBLEMS.md`
- **R-XX**: project rule numbered in `CLAUDE.md` or `SA_DEV.md`
- **AU / PA / LO / RW / NT**: abbreviations for the 5 webservices (see glossary of [[ARCH_RATIS]])
- **Base** (SQLAlchemy): root `DeclarativeBase` class from `ratis_core.database`, used by Alembic for autogenerate
- **JWT RS256**: JSON Web Token signed with RSA-SHA256 — private key for signing (in ratis_auth only), public key for verification (shared to consuming services)
- **workspace** (uv): uv monorepo mode where a package can reference another via `ratis_core = { workspace = true }` in `pyproject.toml`
