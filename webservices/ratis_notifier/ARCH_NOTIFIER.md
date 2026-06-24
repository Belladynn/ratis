---
# Identity
type: service-global
service: ratis_notifier
status: production

# Navigation (Obsidian + RAG)
parent: ARCH_RATIS
sub_archs: []
related: [ARCH_CORE]

# Technical
port: 8005
tech: [FastAPI, PostgreSQL, Expo Push]
tables: [user_push_tokens, notification_logs, push_receipt_tickets]
env_vars: [DATABASE_URL, EXPO_PUSH_URL, INTERNAL_API_KEY]

# Business
tags: [notifications, push, expo, internal, infra, fire-and-forget]
business_domain: infra
rgpd_concern: false

# Freshness
updated: 2026-05-18
---

# ratis_notifier — push notification delivery

> Internal FastAPI service (port 8005) that sends push notifications via Expo, stores device tokens and receipts for audit. Called fire-and-forget by other services using `INTERNAL_API_KEY`.
> @tags: notifier notifications push expo internal fire-and-forget user_push_tokens notification_logs receipts infra
> @status: LIVRÉ V0
> @subs: auto

> [[ARCH_RATIS]] · sub-ARCHs: none · relations: [[ARCH_CORE]]

## Index

- [One-sentence summary](#one-sentence-summary) · L.48
- [Responsibility](#responsibility) · L.52
- [Exposed endpoints](#exposed-endpoints) · L.61
- [Owned tables](#owned-tables) · L.69
- [Internal dependencies (other ratis services)](#internal-dependencies-other-ratis-services) · L.74
- [External dependencies (third parties)](#external-dependencies-third-parties) · L.79
- [Key architecture decisions](#key-architecture-decisions) · L.83
- [Main flow](#main-flow) · L.115
- [GDPR constraints specific to this service](#gdpr-constraints-specific-to-this-service) · L.133
- [Things to know (vectorised FAQ)](#things-to-know-vectorised-faq) · L.140
- [Sub-ARCHs](#sub-archs) · L.168
- [Glossary](#glossary) · L.172

---

## One-sentence summary

ratis_notifier is the internal FastAPI service (port 8005) of Ratis that relays push notifications from all services to the Expo Push API, with invalid-token handling, retry backoff, anti-spam (quiet hours + max/day + deduplication), and full logging.

## Responsibility

- ratis_notifier exposes a single internal endpoint `POST /api/v1/notify` protected by `INTERNAL_API_KEY` (no direct user access).
- ratis_notifier fetches all active `user_push_tokens` for a user and sends them to Expo Push API (`https://exp.host/--/api/v2/push/send`).
- ratis_notifier handles invalid tokens (`DeviceNotRegistered` returned by Expo) by deleting them immediately.
- ratis_notifier enforces anti-spam rules: `quiet_hours` (22h–8h), `max_notifications_per_day`, 10-minute deduplication on `(user_id, type)`.
- ratis_notifier retries failed sends with exponential backoff (configurable via `ratis_settings.json`).
- ratis_notifier contains no business logic: it is a technical relay, not a campaign orchestrator.

## Exposed endpoints

Full auto-generated inventory in `ENDPOINTS.md` (section `ratis_notifier`). Functional summary:

- `POST /api/v1/notify` — enqueues a push notification for a user. Payload: `{user_id: UUID, type: str, data: dict}`. Authentication: `INTERNAL_API_KEY`. Never called directly by the mobile client.

Supported types (validated via `settings.notifier.notification_types`): `scan_done`, `cashback_available`, `badge_unlocked`, `price_alert`, etc.

## Owned tables

- **`user_push_tokens`** — Expo tokens for a user (iOS + Android, multiple devices possible). Columns: `id`, `user_id`, `token` (Expo `ExponentPushToken[...]`), `platform`, `created_at`, `last_used_at`. Deleted immediately if Expo returns `DeviceNotRegistered`.
- **`notification_logs`** — log of every send attempt (success + failure + skipped). Columns: `user_id`, `type`, `status` (`sent`/`failed`/`skipped`), `expo_ticket_id`, `sent_at`, `error_reason`. Purged after 90 days by `ratis_batch_purge`.
- **`push_receipt_tickets`** — one Expo ticket per successful `(send, token)`. Columns: `id`, `expo_ticket_id` (UNIQUE), `user_id`, `push_token`, `created_at`, `checked_at`. Populated by the notifier on each successful Expo send; consumed by the `ratis_batch_push_receipts` batch that polls Expo *getReceipts* and deletes dead tokens (`DeviceNotRegistered` async). See `batch/ratis_batch_push_receipts/ARCH_BATCH_PUSH_RECEIPTS.md`.

## Internal dependencies (other ratis services)

- [[ARCH_CORE]] — uses `ratis_core.deps.verify_internal_key` (auth), `ratis_core.database.make_engine/get_db`, `ratis_core.startup.require_env`, `ratis_core.settings.load_settings`, `ratis_core.middleware.RequestIDMiddleware`, `ratis_core.observability.init_sentry`.
- All other services (ratis_auth, ratis_rewards, ratis_product_analyser, ratis_list_optimiser, batches) call ratis_notifier via `ratis_core.notifier_client.notify_user` (wrapping `POST /api/v1/notify`) in fire-and-forget mode.

## External dependencies (third parties)

- **Expo Push API** — `https://exp.host/--/api/v2/push/send` (configurable via `EXPO_PUSH_URL`). No authentication required on the Expo side (the tokens themselves act as secrets). Normalised response with `ticket_id` and per-token status.

## Key architecture decisions

### DA-01 — No Celery, direct calls

**Choice**: ratis_notifier processes each `POST /notify` synchronously in the HTTP handler and returns 202. The caller is fire-and-forget (short timeout on the client side).
**Rejected alternative**: Celery queue + async worker.
**Reason**: in ratis_notifier, the send to Expo is already async I/O (httpx). Adding Celery would multiply the infrastructure (worker, Redis) with no perceived latency gain. Retries are handled in-thread via `asyncio.sleep`. If volume explodes (>1000 notifs/min), we will migrate, but not before.

### DA-02 — Fire-and-forget on the caller side

**Choice**: all services call `notify_user()` inside an `asyncio.create_task` or equivalent, without awaiting the response or propagating errors.
**Rejected alternative**: synchronous call with error propagation.
**Reason**: in ratis_notifier, a missed notification is never blocking for the business action (accepted scan, validated cashback). Propagating an Expo error would fail a business route because of a push problem → unacceptable. Fire-and-forget guarantees isolation.

### DA-03 — Auto-cleanup of invalid tokens

**Choice**: when Expo returns `DeviceNotRegistered`, ratis_notifier immediately deletes the corresponding `user_push_tokens` row. No retry, no "invalid" flag.
**Rejected alternative**: `is_valid=false` flag to retain history.
**Reason**: in ratis_notifier, a `DeviceNotRegistered` token is permanently dead (user uninstalled the app, disabled notifications, or Expo invalidated the token). Keeping it serves no purpose and pollutes subsequent queries. Immediate DELETE is clean.

**Addendum (2026-05-18)**: Expo may accept a push (ticket `ok`) and only reveal `DeviceNotRegistered` later, in the *receipt*. This async case is not visible at send time. The notifier therefore persists each ticket in `push_receipt_tickets`, and the `ratis_batch_push_receipts` batch periodically polls Expo *getReceipts* to delete tokens detected as dead after the fact. The synchronous DELETE (above) covers the error-at-send case; the batch covers the error-at-receipt case.

### DA-04 — Configurable anti-spam via `ratis_settings.json`

**Choice**: `quiet_hours_start`, `quiet_hours_end`, `max_notifications_per_day`, `retry_attempts`, `retry_delay_seconds` are all in `settings.notifier` (loaded via `app_settings` DB or JSON fallback).
**Rejected alternative**: hardcoded in Python.
**Reason**: in ratis_notifier, anti-spam thresholds will be adjusted without redeployment (R19 CLAUDE.md). An admin can tighten them via `PUT /api/v1/admin/settings/notifier` (on the ratis_rewards side). The lifespan validates the presence of required keys (`_REQUIRED_NOTIFIER_KEYS`) and crashes at boot if any are missing.

### DA-05 — 10-minute deduplication on (user_id, type)

**Choice**: before sending, ratis_notifier checks `notification_logs` for an existing notification of the same type for the same user within the last 10 minutes.
**Rejected alternative**: no deduplication (leave it to the caller).
**Reason**: in ratis_notifier, a bug on the caller side (double-call of `notify_user` during a retry) must not spam the user. Centralised deduplication guarantees the bound "max 1 notif/type/10min", defence-in-depth.

### DA-06 — Daily-cap serialised by per-user advisory lock (2026-05-18)

**Choice**: before counting the day's notifications (`max_notifications_per_day`), the pipeline acquires a `pg_advisory_xact_lock(hashtext('notif_cap:'||user_id))`.
**Rejected alternative**: non-atomic check-then-insert.
**Reason**: two concurrent `notify` requests for the same user both read the `count` below the cap and both proceed → cap is exceeded by +1. The transaction-scoped advisory lock serialises the count+insert: the second request only reads the count after the first has committed. Same pattern as `gift_card_cap_service.reserve_gift_card_cap`. The lock is released automatically on commit/rollback of the pipeline transaction.

## Main flow

### Flow 1 — Sending a notification

1. A service (e.g. ratis_product_analyser at the end of a scan) calls `POST /api/v1/notify` via `ratis_core.notifier_client.notify_user` with `INTERNAL_API_KEY` and payload `{user_id, type, data}`.
2. ratis_notifier validates `INTERNAL_API_KEY` (via `ratis_core.deps.verify_internal_key`) and the payload schema.
3. ratis_notifier applies anti-spam filters: current time vs quiet_hours, today's count vs max_per_day, 10-min dedup. If filtered → INSERT `notification_logs` with `status='skipped'` and return 202.
4. ratis_notifier reads `user_push_tokens` for the user. If no tokens → INSERT log `status='skipped'` and return 202.
5. For each token, ratis_notifier sends a POST to `EXPO_PUSH_URL` with the standard Expo body (`to`, `title`, `body`, `data`).
6. If Expo returns `DeviceNotRegistered` → ratis_notifier DELETEs the token from `user_push_tokens`.
7. For transient errors (network, Expo 5xx) → retry with exponential backoff (`retry_attempts`, `retry_delay_seconds`).
8. INSERT `notification_logs` with `status='sent'` + `expo_ticket_id` or `status='failed'` + `error_reason`.

### Flow 2 — Log purge

1. `ratis_batch_purge` (daily cron) deletes `notification_logs` rows where `sent_at < now() - 90 days`.
2. `user_push_tokens` are not purged by age (only cleaned up on `DeviceNotRegistered`).

## GDPR constraints specific to this service

- ratis_notifier stores no direct personal data: `notification_logs` contains `user_id` (FK) + `type` + `status`, not message content.
- The `data` in the `POST /notify` payload is not persisted (it transits to Expo, and is logged in Sentry only on error with scrubbing applied).
- Expo tokens are secrets (capable of spamming the user if leaked) → never logged, never returned in an API response.
- `notification_logs.user_id` is FK SET NULL: upon GDPR deletion of a user, the logs remain with `user_id=NULL` (no residual personal information).

## Things to know (vectorised FAQ)

### Why does ratis_notifier have no Celery when ratis_product_analyser does?

In ratis_notifier, each notification is a single HTTP request to Expo (~100ms). No dedicated worker is needed: the FastAPI async handler chains the POSTs without blocking the event loop. ratis_product_analyser needs Celery because an OCR pass takes 3–15s (CPU-bound, cannot stay in the HTTP handler). As long as ratis_notifier stays I/O-bound and <500ms, we keep it synchronous.

### Why does ratis_notifier have a single `POST /api/v1/notify` endpoint instead of one per type?

In ratis_notifier, `type` is a field in the payload (validated against `settings.notifier.notification_types`). A single endpoint simplifies the client (`notifier_client.notify_user` is one function) and centralises anti-spam + routing. Adding a new type means adding an entry in `ratis_settings.json`, not a new endpoint.

### How to test ratis_notifier locally without sending real notifications?

1. In tests: `EXPO_PUSH_URL` points to a mock server (fixture in `conftest.py` with `httpx_mock`).
2. Run: `uv run --package ratis_notifier pytest webservices/ratis_notifier/tests/ -v`.
3. For manual testing: start the service with a real dev Expo token, then `curl -H "X-Internal-Key: ..." -d '...' localhost:8005/api/v1/notify`.

### What is the difference between ratis_notifier and a direct Expo webhook from each service?

In ratis_notifier, three things are centralised that should not be duplicated across 5 services: (1) anti-spam (quiet_hours, dedup, max/day), (2) invalid-token handling (cleanup), (3) retry with backoff. A direct Expo call from each service would duplicate this logic and make monitoring diffuse. The single service is a "single source of truth" choice for everything related to push.

### What happens if Expo is down?

ratis_notifier retries `retry_attempts` times (default 3) with `retry_delay_seconds` (default 30). If still failing → INSERT log `status='failed'` and return 202 to the caller (which is fire-and-forget and therefore sees nothing). Callers are never blocked. The notification is lost (no store-and-forward in V1), which is acceptable for push (not critical like an email).

### Why is `EXPO_PUSH_URL` an env var?

In ratis_notifier, we may want to point to another relay (e.g. direct FCM in V2, or a mock in dev/CI). Keeping the URL in the environment rather than hardcoded allows switching without touching the code.

## Sub-ARCHs

ratis_notifier has no sub-ARCHs — everything is in this document.

## Glossary

- **DA-XX**: numbered architecture decision (see dedicated section).
- **Expo Push Token**: opaque string `ExponentPushToken[...]` provided by the Expo SDK to the mobile device. Stored in `user_push_tokens`.
- **DeviceNotRegistered**: Expo error code indicating the token is permanently dead (app uninstalled, notifications disabled, or Expo invalidated the token). Triggers an immediate DELETE of the token.
- **Fire-and-forget**: call pattern where the caller launches the request in an async task and neither awaits nor inspects the response — the business logic is never blocked by a notification error.
- **Quiet hours**: time window (default 22h–8h) during which no notifications are sent.
- **Outbox pattern**: not used on the ratis_notifier side (callers do direct fire-and-forget). ratis_rewards has an internal outbox for its own notifications via ratis_notifier.
