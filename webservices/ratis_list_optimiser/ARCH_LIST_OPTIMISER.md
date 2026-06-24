---
# Identity
type: service-global
service: ratis_list_optimiser
status: production

# Navigation (Obsidian + RAG)
parent: ARCH_RATIS
sub_archs: []
related: [ARCH_CORE, ARCH_PRODUCT_ANALYSER]

# Technique
port: 8002
tech: [FastAPI, PostgreSQL, Redis, Celery, OSRM]
tables: [shopping_lists, shopping_list_items, optimized_routes, user_store_preferences]
env_vars: [DATABASE_URL, JWT_PUBLIC_KEY_PATH, JWT_AUDIENCE, OSRM_BASE_URL, REDIS_URL]

# Business
tags: [route, osrm, list, optim, gis, rgpd, template]
business_domain: pricing
rgpd_concern: true

# Freshness
updated: 2026-05-18
---

# ratis_list_optimiser — shopping lists + route optimisation

> FastAPI service (port 8002) that manages user shopping lists and computes optimised multi-store itineraries via OSRM. Routes cached for 24 h (TTL), `steps` JSONB without a "home" point (PII).
> @tags: list optimiser route osrm shopping-lists shopping_list_items optimized_routes celery redis ttl rgpd
> @status: LIVRÉ V0
> @subs: auto

> [[ARCH_RATIS]] · sub-ARCHs: none · relations: [[ARCH_CORE]], [[ARCH_PRODUCT_ANALYSER]]

## Index

- [One-sentence summary](#one-sentence-summary) · L.48
- [Responsibility](#responsibility) · L.52
- [Exposed endpoints](#exposed-endpoints) · L.61
- [Owned tables](#owned-tables) · L.83
- [Internal dependencies (other ratis services)](#internal-dependencies-other-ratis-services) · L.90
- [External dependencies (third parties)](#external-dependencies-third-parties) · L.95
- [Key architecture decisions](#key-architecture-decisions) · L.100
- [Main flow](#main-flow) · L.132
- [GDPR constraints specific to this service](#gdpr-constraints-specific-to-this-service) · L.158
- [Things to know (vectorised FAQ)](#things-to-know-vectorised-faq) · L.165
- [Sub-ARCHs](#sub-archs) · L.194
- [Glossary](#glossary) · L.198

---

## One-sentence summary

ratis_list_optimiser is the FastAPI service (port 8002) that manages Ratis users' shopping lists, computes the best multi-store purchasing plan from the price consensus, and traces an OSRM route between the selected stores while protecting the user's geographic PII.

## Responsibility

- ratis_list_optimiser exposes `/api/v1/lists/*` for CRUD operations on shopping lists and their items (add, remove, edit, clear, save-as-template, from-template, scan-check).
- ratis_list_optimiser exposes `/api/v1/lists/{id}/optimize` and `/api/v1/routes/*` to trigger optimisation, retrieve a route, move an item from one store to another, or remove a store and redistribute its items.
- ratis_list_optimiser exposes `/api/v1/suggestions/*` for progressive list suggestions based on purchase history.
- ratis_list_optimiser exposes `/api/v1/price` for unit-price lookup in the consensus.
- ratis_list_optimiser calls OSRM (MLD routing, France car profile) to compute the steps of a multi-store itinerary.
- ratis_list_optimiser respects PII: the user's live position is never persisted; only `users.ref_lat`/`ref_lng` rounded to ~200 m are used as fallback, and `optimized_routes.steps` never contains the departure/arrival point (home point).

## Exposed endpoints

Full auto-generated inventory in `ENDPOINTS.md` (section `ratis_list_optimiser`). Functional summary:

**Shopping lists — `/api/v1/lists/*`**
- `GET /lists` · `POST /lists` · `GET/PATCH/DELETE /lists/{id}`
- `POST /lists/{id}/items` · `PATCH/DELETE /lists/{id}/items/{item_id}`
- `POST /lists/{id}/clear` — empties all items
- `POST /lists/{id}/save-as-template` + `POST /lists/from-template/{template_id}` — max 3 templates per user
- `POST /lists/{id}/scan-check` — auto-checks an item via EAN scan

**Optimisation — `/api/v1/lists/{id}/optimize` + `/api/v1/routes/*`**
- `POST /lists/{id}/optimize` — triggers optimisation (sync V1, Celery in V2)
- `GET /lists/{id}/route` — retrieves the latest non-expired route (TTL 24 h)
- `GET /routes/{route_id}` — retrieves a specific route
- `POST /routes/{route_id}/move-item` — moves an item from one store to another in the route
- `POST /routes/{route_id}/remove-store` — removes a store and redistributes its items

**Suggestions + pricing**
- `GET /suggestions/eligibility` · `POST /suggestions/generate`
- `GET /price` — unit-price lookup from `price_consensus`

## Owned tables

- **`shopping_lists`** — user lists. Columns: `id`, `user_id`, `name`, `has_default_name` (true + `name=''` = default list, never send `name=''` from the client), `is_template` (max 3 per user). FK SET NULL on user_id.
- **`shopping_list_items`** — items in a list. Columns: `list_id`, `product_ean`, `quantity`, `checked` (auto-checked via scan-check).
- **`optimized_routes`** — optimised routes. Columns: `id`, `list_id`, `steps` (JSONB, without home-point), `total_distance_m`, `total_cost_cents`, `expires_at` (TTL 24 h). Stored steps never contain the departure/arrival position (PII).
- **`user_store_preferences`** — store preferences (favourite/excluded). Pre-dates the service; ratis_list_optimiser is the primary consumer.

## Internal dependencies (other ratis services)

- [[ARCH_CORE]] — uses `ratis_core.auth.get_current_user`, `ratis_core.database.make_engine/get_db`, `ratis_core.startup.require_env`, `ratis_core.settings.load_settings`, `ratis_core.middleware.RequestIDMiddleware`, `ratis_core.observability.init_sentry`.
- [[ARCH_PRODUCT_ANALYSER]] — consumes the `price_consensus` table (populated by ratis_product_analyser via ticket/label scans). No direct HTTP call; shared DB read.

## External dependencies (third parties)

- **OSRM** (Open Source Routing Machine 5.27, MLD algorithm, car profile, France-PBF prebuilt data) — accessible via `OSRM_BASE_URL` (default `http://osrm:5000`). Timeout configurable via `settings.list_optimiser.osrm_timeout_seconds`.
- **Redis** — Celery backend for V2 async (not yet used in V1, synchronous optimisation).

## Key architecture decisions

### DA-01 — Synchronous optimisation in V1, Celery in V2

**Choice**: in V1, `POST /lists/{id}/optimize` runs the optimisation inside the HTTP handler and returns the route directly.
**Rejected alternative**: async from day one via Celery with push notification.
**Rationale**: in ratis_list_optimiser, the V1 computation (a few dozen stores × a few dozen items + one OSRM call) completes in <2 s. The infra cost of Celery + async notification is disproportionate as long as parallelism is not needed. The V2 migration will only change the wrapping — the optimisation engine stays the same.

### DA-02 — `optimized_routes.steps` without home-point (GDPR)

**Choice**: the route stored in DB contains only inter-store steps (store_a → store_b → store_c). The departure and return point (user home) is computed on the fly for display, never persisted.
**Rejected alternative**: store the home-point in `steps` to avoid recomputation.
**Rationale**: in ratis_list_optimiser, a user's precise position is sensitive GDPR data. Persisting the address in `optimized_routes.steps` would create illegitimate retention (the route lasts 24 h but the position would persist in history). Recomputing costs one extra OSRM call, which is acceptable.

### DA-03 — Minimum item threshold per store to justify the detour

**Choice**: a store is only included in the route if it contains at least `settings.list_optimiser.min_items_per_store` items.
**Rejected alternative**: pure "lowest price per item" optimisation with no density constraint.
**Rationale**: in ratis_list_optimiser, going to a store for a single item saves 10 cents but adds 15 min of detour → user anti-value. The threshold enforces a minimum grouping and improves the saving/time ratio.

### DA-04 — Live position sent by the client, never persisted

**Choice**: the client sends its live GPS position in the `/optimize` request (if the user has granted permission). Otherwise falls back to `users.ref_lat`/`ref_lng` (rounded to ~200 m).
**Rejected alternative**: persist the live position in `users.current_lat`/`current_lng`.
**Rationale**: in ratis_list_optimiser, the live position changes every second and persisting it would be a major GDPR risk. Transiting through the request (not logged) + coarse DB fallback = defence in depth.

### DA-05 — 24 h TTL on optimised routes

**Choice**: `optimized_routes.expires_at = now() + 24h`. Beyond that, the route is considered stale (consensus prices may have changed).
**Rejected alternative**: unlimited cache + manual invalidation.
**Rationale**: in ratis_list_optimiser, consensus prices can change several times a day (ticket scans). A 48 h route may point to a store that is no longer the cheapest. The 24 h TTL is a compromise between freshness and recomputation cost.

## Main flow

### Flow 1 — Shopping list optimisation

1. The client calls `POST /api/v1/lists/{list_id}/optimize` with (optionally) its live position `{user_lat, user_lng}`.
2. ratis_list_optimiser resolves the reference position: live if provided, otherwise `users.ref_lat`/`ref_lng`.
3. ratis_list_optimiser fetches the list items and queries `price_consensus` for each EAN within the `user_preferences.search_radius_km` radius.
4. ratis_list_optimiser runs the assignment engine (`optimization_engine.py`): for each item, find the cheapest store respecting `min_items_per_store` and `user_store_preferences` (excluded stores ignored, favourite stores boosted).
5. ratis_list_optimiser calls OSRM with the coordinates of the selected stores to compute the route (not the home-point).
6. ratis_list_optimiser inserts a row in `optimized_routes` with `steps` JSONB (without home-point) and `expires_at = now() + route_expiry_hours`.
7. The client receives the full route (steps + distance + total cost).

### Flow 2 — Manual route adjustment (move-item / remove-store)

1. The client calls `POST /routes/{route_id}/move-item` with `{item_id, target_store_id}` or `POST /routes/{route_id}/remove-store` with `{store_id}`.
2. ratis_list_optimiser verifies that the route belongs to the authenticated user and has not expired.
3. For `move-item`: moves the item within the `steps` structure, recomputes the cost (consensus lookup for the new store), does NOT recompute the OSRM route (V2).
4. For `remove-store`: removes the store, redistributes its items across the remaining stores (via the assignment engine), does NOT recompute OSRM.
5. ratis_list_optimiser UPDATEs `optimized_routes.steps` and returns the new structure.

### Flow 3 — Scan-check auto-checking

1. The user scans a barcode during shopping. The client calls `POST /api/v1/lists/{list_id}/scan-check` with the EAN.
2. ratis_list_optimiser looks for an unchecked `shopping_list_items` entry with that EAN in the list.
3. If found → UPDATE `checked = true` and return the item. Otherwise → 404.

## GDPR constraints specific to this service

- `optimized_routes.steps` NEVER contains the home-point (user position). Recomputed on the fly for client display.
- The live position transmitted in `POST /optimize` is never logged (middleware scrub) and is not persisted anywhere.
- `users.ref_lat`/`ref_lng` are rounded to 3 decimal places (DECIMAL(9,3) ≈ 111 m precision), sufficient for a 5–50 km search radius.
- Expired routes (>24 h) are purged by `ratis_batch_purge` to limit retention.

## Things to know (vectorised FAQ)

### Why does ratis_list_optimiser use OSRM and not Google Maps or Mapbox?

In ratis_list_optimiser, OSRM is self-hosted (Docker instance with France-PBF preloaded), free, quota-less, and privacy-respecting (no call to a commercial third party with the user's geolocation). Latency <200 ms for a 10-point route on the MLD profile. Google Maps / Mapbox would incur a per-1000-request cost + leakage of geolocation to a third party.

### Why is the optimisation synchronous in V1?

In ratis_list_optimiser, an average user has 20–30 items on their list, and a 5–10 km radius returns 5–20 stores with consensus data. The computation is <2 s, which fits within a synchronous HTTP handler. Moving to Celery + push notification adds infra complexity (worker, retry, monitoring) with no user benefit as long as we stay under 3 s. V2 async is planned when we move to several hundred items (B2B scenario, out of V1 scope).

### How to test ratis_list_optimiser locally?

1. Start the infra: `docker compose up -d` (includes an OSRM container with `osrm-backend:v5.27` and France-PBF).
2. Verify OSRM: `curl http://localhost:5000/route/v1/driving/2.35,48.85;2.36,48.86` → JSON response.
3. Run tests: `uv run --package ratis_list_optimiser pytest webservices/ratis_list_optimiser/tests/ -v` (87+ tests, V1 target).
4. Start the service: `uv run --package ratis_list_optimiser uvicorn main:app --port 8002 --reload` from `webservices/ratis_list_optimiser/`.

### What is the difference between ratis_list_optimiser and ratis_product_analyser regarding prices?

ratis_product_analyser populates `price_consensus` via ticket and label scans (OCR + matching). ratis_list_optimiser only reads that table to select the cheapest store per item. The two services share the same DB but do not call each other via HTTP — `price_consensus` is the contractual boundary.

### How does ratis_list_optimiser handle stores excluded by the user?

During optimisation, the engine reads `user_store_preferences` and filters out stores with `excluded=true`. Stores with `favourite=true` receive a bonus in scoring (if two stores have the same price, the favourite wins). Thresholds are in `ratis_settings.json`.

### Why `has_default_name=true + name=''` rather than `name='My list'`?

In ratis_list_optimiser, the "default list" displays a localised name on the client side (i18n `t('list.default_name')`). Storing `name=''` signals to the client that it must display the i18n label. Storing a hardcoded name would embed a specific language in the DB. Strict client-side rule: never send `name=''` in a PATCH (leave the `has_default_name` flag intact).

## Sub-ARCHs

ratis_list_optimiser has no sub-ARCHs — everything is in this document.

## Glossary

- **DA-XX**: numbered architecture decision (see dedicated section).
- **OSRM**: Open Source Routing Machine, self-hosted routing engine, France-PBF car profile, MLD (Multi-Level Dijkstra) algorithm.
- **MLD**: Multi-Level Dijkstra, the OSRM routing algorithm that preprocesses the graph for <200 ms queries.
- **Home-point**: the user's departure/arrival position. Never stored in `optimized_routes.steps` for GDPR reasons.
- **Price consensus**: aggregated value in `price_consensus` (populated by ratis_product_analyser). Source of truth for "what price for this EAN at this store".
- **List template**: a list marked `is_template=true`, reusable via `POST /lists/from-template/{id}`. Max 3 per user.
