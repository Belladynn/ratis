# PRIVACY.md — Technical Privacy Policy

Privacy-by-design decisions. Reference for CNIL/GDPR audit. Prod checklist → `PROD_CHECKLIST.md`.

## Principles
Privacy by design, data minimisation, defined retention periods, irreversible anonymisation.

## Account deletion — tombstone pattern

> Full DELETE /account lifecycle (user-initiated + admin override + flow + JTI revoke): see [[ARCH_AUTH]] § DELETE /account — lifecycle complet. This section covers only table-level details (anonymisation + retention).

`users` is never deleted (`cashback_withdrawals.user_id` RESTRICT — legal obligation).

**Tombstone anonymisation**: `email → deleted_{uuid}@deleted.invalid`, `display_name/avatar_url/password_hash/provider_id → NULL`, `provider → "deleted"`, `is_deleted → true`.

Since audit F-AU-3 (2026-05-11), the `delete_account` routine applies 4 tiers to break cross-table correlation (see [[ARCH_AUTH]] § Side-effects DB for detail + reasoning per tier):

**Tier 1 — Hard DELETE**: `refresh_tokens`, `user_push_tokens`, `shopping_lists` (CASCADE → items + routes), `product_tracking`, `price_alerts`, `user_sessions`, `user_session_stats`, `notification_logs`, `user_store_preferences`, `user_streaks`, `user_badges`, `leaderboard_snapshots`, `user_cab_balance`, `user_xp_balance`, `user_savings_snapshot`, `notification_outbox`, `product_favorites`.

**Tier 2 — SET NULL**: `scans`, `receipts`, `price_challenge_responses`, `referral_codes`, `stores.suggested_by_user_id` — price/store/consensus data preserved.

**Tier 3 — Per-user anon UUID** (per-user analytics preserved, re-identification broken): `user_achievements`, `reward_events`, `user_missions`, `user_battlepass_progress`, `user_battlepass_claims`, `community_challenge_claims`, `community_multipliers`, `mystery_challenge_finds`, `label_sessions`, `mission_xp_records`, `xp_transactions`, `referral_uses.referred_user_id`, `product_name_resolutions`. Mechanism: `user_id = sha256(real_id || RGPD_ANONYMIZE_SALT)` truncated to a v4 UUID — deterministic per user, irreversible without the salt. FK `users.id` dropped (migration `20260511_1000_rgpd_anon_completeness`).

**Tier 4 — Static anon sentinel** (financial NEVER-PURGE 5-10 years): `cabecoin_transactions`, `cashback_transactions`, `cashback_withdrawals`, `gift_card_orders`. `user_id → 00000000-0000-0000-0000-000000000001` (seeded `users` row). All deleted users point to the same sentinel → correlation broken, row retained for accounting audit. Residual cashback recoverable by Ratis.

**Not touched**: `subscriptions` (Stripe customer_id business-coupled, out-of-scope F-AU-3 — tombstone correlation accepted), `user_cashback_balance` (materialised, NEVER PURGE), `user_preferences` (no PII), `store_validation_history.triggered_by` (audit text field — UUID points to non-identifiable tombstone, pattern accepted).

## Sensitive data by domain

**OCR** — images deleted upon `accepted` (`image_url = NULL`). Never store names/first names extracted from receipts.

**Routes** — TTL 24h. Never persist the departure point in `steps` (home address = PII).

**Price challenges** — `image_crop_url` deleted upon `validated` or `rejected`.

**Sessions** — 90-day detail → aggregated into `user_session_stats` → purged. No fine-grained tracking in V1 (consent required).

**Tokens** — only refresh token JTIs are stored (not the full token). Revoked on logout/change-password/deletion.

## Prod snapshots on the Mac mini (db-sandbox dry-runs)

Daily prod database snapshots (`pg_dump`) are kept in plaintext on the Mac mini host (`~/.local/share/ratis/db-sandbox/snapshots/`) with a maximum retention of 24 hours and `chmod 700` permissions (operator user only). They are used exclusively for dry-runs of db-write-pipeline stored procedures (see `ARCH_n8n_pipelines.md` DA-11) — they never leave the Mac mini. Each restored sandbox runs on a dedicated isolated Docker network (`ratis_sandbox_isolated_<id>`) with no port mapping, reachable only via `docker exec` from the host. At-rest encryption and anonymisation at restore are still to be delivered in V2 (before onboarding the first third-party users — see `PROD_CHECKLIST.md` § Sécurité M6 V2).

## Mapping & Routing

Ratis uses **MapLibre Native** (open-source rendering engine, identical iOS + Android) with **MapTiler vector tiles** (style `streets-v2`, built from OpenStreetMap data) to display the optimised shopping route map in the "Itinéraire" tab. The map is rendered only when the user opens that tab — nowhere else in the app. The route calculation itself remains server-side via OSRM (self-hosted) — only the visual rendering relies on MapTiler.

**What leaves our infra when the map is active**:

- The coordinates of the displayed area (derived from the public lat/lng of stores, already open via OSM) are sent to MapTiler to retrieve the vector tiles for the map around that area.

**What never leaves our infra toward MapTiler**:

- No behavioural data: scans, shopping runs, favourites, CAB balance, purchase history, Ratis identifiers, etc.
- No home point: `optimized_routes.steps` excludes the user's departure point (PII, see § Sensitive data by domain § Routes).
- The user's GPS position is not transmitted to MapTiler (the system location indicator is not displayed on this map).

**Legal basis**: legitimate interest (Article 6.1.f GDPR) — providing a map visualisation of the optimised route is essential to the feature. MapTiler (tiles from OSM) covers this need without a billing account and with EU hosting.

**Sub-processor**: MapTiler AG (Switzerland / EU), tiles served from the European Union. Underlying map data: © OpenStreetMap contributors (ODbL). MapTiler DPA GDPR-compliant: https://www.maptiler.com/privacy-policy/

**Decision history** (audit traceability):

- PR #439: introduction of `react-native-maps` iOS-only.
- PR #441: switch to MapLibre Native + direct public OSM tiles (GDPR-pure, zero sub-processor).
- PR #444 (2026-05-14): back to `react-native-maps` + Google provider (iOS+Android). PO decision: Google rendering quality > independence.
- **Revert 2026-05-25 (DA-46): PR #444 decision REVOKED** — the Google Cloud billing account could not be activated (Google Maps SDK requires an active billing account). Back to MapLibre Native, with MapTiler tiles (free tier, EU host, simple client API key) instead of public OSM tile servers (OSMF tile policy forbids application usage). No more Google sub-processor on the map.

**User alternative**: from the Itinéraire screen, the "Ouvrir dans Maps" action (see `RouteStopCard`) externalises turn-by-turn navigation to native Apple Maps / Google Maps — with an explicit opt-in and a GDPR warning before each trigger (see PR #443).

## Legal retention

| Table | Duration | Reason |
|---|---|---|
| `cashback_withdrawals` / `cashback_transactions` / `subscriptions` | 5-10 years | Accounting obligation |
| `user_cashback_balance` | Tombstone duration | Orphan cashback — Ratis audit |

## Commercial use
Behavioural data = PII. Separate explicit consent required before any commercial use — not implemented in V1.

## LLM-assisted parsing (alpha)

During the alpha phase, OCR text extracted from receipts transits through an external LLM API to denoise and classify multi-pass clusters (Phase 2h v2 — denoise + regex prices). The path is inert as long as `LLM_API_KEY` is not provisioned, and automatically falls back to the local regex parser (`parse_receipt`) if the LLM API fails or is unavailable.

**What leaves our infra**: OCR text only (product names, prices, totals). No first or last names (never extracted from receipts — see § Sensitive data by domain § OCR). **The receipt image is never sent to an external LLM**, regardless of the configured provider — only the OCR'd text leaves.

**What stays on our infra**: the receipt image (R2, 48h max), the receipt↔user mapping, visual evidence (handwritten signature, photographed loyalty card, etc.).

**Possible providers (config-time, no image rebuild required)** — the choice is driven by the `LLM_PROVIDER` environment variable:

- **Mistral AI** (`LLM_PROVIDER=mistral`, default): Mistral AI SAS (FR), French jurisdiction, EU data residency, GDPR compliance.
- **Anthropic Claude** (`LLM_PROVIDER=anthropic`): Anthropic PBC, US jurisdiction. Signable GDPR-compliant DPA. Anthropic does not by default use enterprise API data to train its models (opt-out by default). Chosen for the alpha due to superior parsing quality on noisy receipts, pending the self-host switch. All requests are stateless API calls — no persistent storage on the Anthropic side beyond standard operational logs.
- **Self-hosted Ollama / vLLM** (`LLM_PROVIDER=ollama`, post-Mac-Mini): LLM running on Ratis infrastructure (FR), no external sub-processor — the target option for V1.

**Timeline**: alpha = cloud (FR or US depending on selected provider), V1 = self-host. Switching between providers requires no Docker image redeploy — only an environment variable change.

**Learning loop**: fragments classified as "dismissal" by the LLM (payment, total, footer slogan, etc.) are persisted in `ocr_knowledge` to pre-filter future receipts locally, without sending those fragments back to the LLM. No user data is stored in this table — only OCR boilerplate text ("MERCI DE VOTRE VISITE", "TOTAL TTC", etc.).
