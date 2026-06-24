---
# Identity
type: service-global
service: ratis_product_analyser
status: production

# Navigation (Obsidian + RAG)
parent: ARCH_RATIS
sub_archs: [ARCH_barcode, ARCH_consensus, ARCH_store_resolution, ARCH_OCR_LLM_BRIDGE, ARCH_receipt_pipeline, ARCH_store_validation]
related: [ARCH_CORE, ARCH_REWARDS, ARCH_BATCH_OSM_SYNC, ARCH_BATCH_OFF_SYNC, ARCH_BATCH_CONSENSUS, ARCH_BATCH_MYSTERY_ANNOUNCE, ARCH_BATCH_VRAC_SEED, ARCH_ocr_store_detection, ARCH_admin_endpoints]

# Technical
port: 8003
tech: [FastAPI, PostgreSQL, Redis, Celery, Cloudflare R2, PaddleOCR, pyzbar, OSM Overpass, LLM (Mistral / Anthropic / Ollama swap-able)]
tables: [scans, receipts, products, product_knowledge, ocr_knowledge, price_consensus, price_consensus_history, price_consensus_scans, user_product_favorites, stores, scan_debug, parsed_tickets, pipeline_audit_log, store_validation_history, store_candidates]
env_vars: [DATABASE_URL, JWT_PUBLIC_KEY_PATH, JWT_AUDIENCE, REDIS_URL, R2_ENDPOINT_URL, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY, R2_BUCKET_NAME, OSM_OVERPASS_URL, NOTIFIER_URL, INTERNAL_API_KEY, LLM_PROVIDER, LLM_BASE_URL, LLM_API_KEY, LLM_MODEL, STORE_DEBUG, ADMIN_API_KEY]

# Business
tags: [ocr, scan, receipt, barcode, price, consensus, rgpd, r2, celery, osm, llm, anthropic, mistral]
business_domain: pricing
rgpd_concern: true

# Freshness
updated: 2026-05-18
---

# ratis_product_analyser — receipt/label scanning, OCR, price consensus

> FastAPI service (port 8003) that ingests receipt/label/barcode scans from Ratis users, runs an async OCR pipeline (Celery + PaddleOCR), identifies products and stores, maintains multi-user `price_consensus`, and exposes product pages + local prices via EAN.
> @tags: product-analyser scan receipt label barcode ocr paddleocr celery price consensus r2 ean rgpd llm anthropic mistral
> @status: LIVRÉ V0
> @subs: auto

> [[ARCH_RATIS]] · sub-ARCHs : [[ARCH_barcode]], [[ARCH_consensus]], [[ARCH_store_resolution]] · relations : [[ARCH_CORE]], [[ARCH_REWARDS]], [[ARCH_BATCH_OSM_SYNC]], [[ARCH_BATCH_OFF_SYNC]], [[ARCH_BATCH_CONSENSUS]], [[ARCH_BATCH_MYSTERY_ANNOUNCE]], [[ARCH_ocr_store_detection]]

## Index

- [One-sentence summary](#one-sentence-summary) · L.48
- [Responsibilities](#responsibilities) · L.52
- [Exposed endpoints](#exposed-endpoints) · L.61
- [Owned tables](#owned-tables) · L.81
- [Internal dependencies (other ratis services)](#internal-dependencies-other-ratis-services) · L.93
- [External dependencies (third parties)](#external-dependencies-third-parties) · L.103
- [Key architecture decisions](#key-architecture-decisions) · L.111
- [Main flow](#main-flow) · L.149
- [GDPR constraints specific to this service](#gdpr-constraints-specific-to-this-service) · L.176
- [Key points (vectorised FAQ)](#key-points-vectorised-faq) · L.184
- [Sub-ARCHs](#sub-archs) · L.217
- [Glossary](#glossary) · L.223

---

## One-sentence summary

ratis_product_analyser is the FastAPI service (port 8003) that ingests receipt and electronic-label scans from Ratis users, runs an asynchronous OCR pipeline (Celery + PaddleOCR), identifies products and stores, maintains the multi-user `price_consensus`, and exposes product pages + local prices via EAN.

## Responsibilities

- ratis_product_analyser exposes `/api/v1/scan/*` for uploading receipts (`POST /receipt`), electronic labels (`POST /label`, `POST /label/batch`), barcodes (`POST /barcode`), status tracking (`GET /receipt/{id}`, `GET /label/session/{id}`), client-side deduplication (`GET /check-hash`), and user history (`GET /history`).
- ratis_product_analyser exposes `/api/v1/product/*` for the product page by EAN, local prices, and favorites management.
- ratis_product_analyser runs an asynchronous OCR pipeline via Celery (separate worker): preprocessing → image classification → 3-pass OCR → arbitration → matching → DB write.
- ratis_product_analyser stores uploaded images in Cloudflare R2 with a 48h lifecycle rule (GDPR). Label images are nullified (`image_url=NULL`) upon scan acceptance.
- ratis_product_analyser maintains `price_consensus(store_id, product_ean)` via weighted aggregation of scans, with an immutable history (`price_consensus_history`) and trust_score (frozen at ≥95%).
- ratis_product_analyser resolves stores via OSM Overpass API (search by coordinates + OCR name) — see [[ARCH_store_resolution]].

## Exposed endpoints

Full auto-generated inventory in `ENDPOINTS.md` (section `ratis_product_analyser`). Functional summary:

**Scan — `/api/v1/scan/*`**
- `POST /receipt` → 202 + `receipt_id` (async)
- `POST /label` → 202 + `scan_id` (async)
- `POST /label/batch` → 202 + `session_id` + `[scan_ids]` (mass scan)
- `POST /barcode` → direct product page (sync, see [[ARCH_barcode]])
- `GET /receipt/{id}` → overall status (pending/processing/done/failed) + summary
- `GET /label/session/{id}` → batch status
- `GET /check-hash?sha256=...` → `{duplicate: bool}` without upload
- `GET /history` → user history with cursor pagination

**Product — `/api/v1/product/*`**
- `GET /{ean}` → product page + local prices
- `GET /favorites` → user favorites list
- `POST /{ean}/favorite` · `DELETE /{ean}/favorite`

**Admin debug — `/api/v1/admin/*`** (alpha — gated by presence of `ADMIN_API_KEY`)
- `GET /scans/{scan_id}/debug` → post-mortem tool: returns `rich_blocks` PaddleOCR + `llm_output` (3-bucket schema) + `legacy_receipt_data` (legacy parser running in parallel) + pre-signed R2 URLs (raw image + processed image) + `ocr_passes_summary`. Auth Bearer `ADMIN_API_KEY`. Mounted only if the var is set at service startup. Data available only when `STORE_DEBUG=true` at scan time + within the 48h pre-purge window.

## Owned tables

- **`scans`** — source of truth. One row per individual scan. Columns: `type` (`receipt`/`electronic_label`/`manual`), `status` (cycle `pending` → (`unmatched`/`accepted`/`rejected`)), `user_id` (SET NULL), `product_ean` (SET NULL), `store_id`, `price_cents`, `raw_ocr`, `image_url` (NULL after acceptance for labels). `tva_amount` only on receipts.
- **`receipts`** — one receipt = 1 row, several `scans` linked to it. Columns: `id`, `user_id`, `store_id`, `total_amount` (denorm OCR-guard), `date` (SENTINEL `1970-01-01` if unresolved), `image_r2_key`, `image_uploaded_at`, `image_deleted_at`.
- **`products`** — product catalogue. `source` (`off`/`internal`), EAN PK, `name`, `brand`, `category`, classification via `classification_rules.json`.
- **`product_knowledge`** — OCR auto-learn: `raw_ocr` → `corrected_ean`. `corrected_ean=NULL` = manual queue (see TRAINING.md).
- **`ocr_knowledge`** — extended in PR #122 to also cover **dismissals** (boilerplate to filter: payment methods, footer slogans, total labels). Schema: `(text PRIMARY KEY, type ENUM('product'|'dismissal'), dismissal_category ENUM(...), source ENUM('llm'|'manual'), seen_count INT, last_seen TIMESTAMPTZ)`. Auto-populated by `filter_and_learn` on every LLM call via `bulk_upsert_dismissals`. Enables pre-filtering of already-seen blocks before the next LLM prompt (token savings + accelerated learning loop).
- **`scan_debug`** — alpha instrumentation (PR #126), gated by `STORE_DEBUG=true`. Schema: `(scan_id PK FK CASCADE → scans, rich_blocks JSONB, llm_output JSONB, legacy_receipt_data JSONB, ocr_passes_summary JSONB, processed_image_r2_key TEXT, created_at TIMESTAMPTZ, purge_after TIMESTAMPTZ NOT NULL INDEX)`. 48h TTL purged by `ratis_batch_purge` step `purge_scan_debug` (DB row + R2 image). Anchored on the first scan of a receipt (fan-out logic in the admin endpoint to retrieve the row from a sibling scan).
- **`price_consensus`** — consensus value per `(store_id, product_ean)` UNIQUE. `trust_score` ≥95% → `frozen_until`. Parameters in `ratis_settings.json`.
- **`price_consensus_history`** — immutable history of every price change (INSERT + UPDATE current). See [[ARCH_consensus]].
- **`price_consensus_scans`** — FK CASCADE to consensus, scan→consensus link for auditing.
- **`user_product_favorites`** — user/EAN favorites. Simple join table.
- **`stores`** — stores. Source `osm` (batch sync) or `user_suggested` (lat/lng=0 pending admin). Soft-delete `is_disabled`.

## Internal dependencies (other ratis services)

- [[ARCH_CORE]] — uses `ratis_core.auth.get_current_user`, `ratis_core.database`, `ratis_core.deps.verify_internal_key`, `ratis_core.knowledge.load_knowledge/classify`, `ratis_core.uploads.validate_image_upload`, `ratis_core.settings.load_settings`, `ratis_core.startup.require_env`.
- [[ARCH_REWARDS]] — fire-and-forget call via `ratis_core.rewards_client.trigger_scan_accepted` when a scan transitions to `accepted` → ratis_rewards credits CAB and updates missions.
- [[ARCH_NOTIFIER]] — calls `notify_user(type='scan_done', data=...)` when the pipeline finishes a receipt.
- [[ARCH_BATCH_OSM_SYNC]] — populates `stores` from OSM (batch cron), ratis_product_analyser is a reader.
- [[ARCH_BATCH_OFF_SYNC]] — populates `products` from OpenFoodFacts (batch cron), ratis_product_analyser is a reader.
- [[ARCH_BATCH_CONSENSUS]] — nightly batch for reconciliation + recalculation of `trust_score` on `price_consensus`.
- [[ARCH_BATCH_MYSTERY_ANNOUNCE]] — batch linked to the mystery product on the rewards side; can trigger announced scans.

## External dependencies (third parties)

- **Cloudflare R2** (S3-compatible) — bucket `ratis-ocr-images`. 48h deletion lifecycle rule (GDPR). Access via standard `boto3`. Env vars `R2_ENDPOINT_URL`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, `R2_BUCKET_NAME`.
- **PaddleOCR** (fr) + **paddlepaddle ≥3.0** — OCR engine, lazy-imported in `worker/pipeline/ocr_engine.py` to avoid a 200-300 MB cold-start on every FastAPI startup. Models pre-loaded in Dockerfile via `RUN python -c "from paddleocr import PaddleOCR; PaddleOCR(...)"`.
- **pyzbar** — barcode reading in scans (EAN-13).
- **OSM Overpass API** — store lookup by proximity + name. Access via `OSM_OVERPASS_URL`.
- **Redis** — Celery backend (queue `scan_tasks`), separate from the FastAPI worker.
- **LLM provider (alpha)** — external API call to structure PaddleOCR blocks into a 3-bucket JSON (retailer / products / dismissals). Provider config-time via `LLM_PROVIDER`, factory `make_default_llm_filter()` returns the right class (`LlmFilter` Mistral, `AnthropicLlmFilter`, or Ollama via `LlmFilter` with custom base_url):
  - `mistral` (FR/EU, GDPR-by-design) — `LlmFilter` class, OpenAI-compatible chat completions, default model `mistral-small-latest`
  - `anthropic` (US, DPA GDPR signable, opt-out training by default on the enterprise API) — `AnthropicLlmFilter` class via SDK `anthropic`, default model `claude-haiku-4-5`, prompt caching enabled (cf KP-31: 4K-token minimum threshold, no-op while the system prompt does not reach that threshold)
  - `ollama` (post-Mac-Mini, self-host FR) — reuses `LlmFilter` because Ollama speaks OpenAI-compat, `LLM_BASE_URL=http://mac-mini:11434/v1`, local model
  
  Image is never sent — only the OCR text. Phase 2h: the `LLM_FILTER_ENABLED` gate has been removed; provisioning `LLM_API_KEY` is the only switch (empty key = inert v2 path, fallback to legacy `parse_receipt`). Automatic fallback to legacy parser on any HTTPError / JSONDecodeError / ValueError. See `PRIVACY.md` § "LLM-assisted parsing".

## Key architecture decisions

### DA-01 — Async OCR pipeline via separate Celery worker

**Choice**: `POST /scan/receipt` returns 202 immediately with a generated `receipt_id`, and enqueues a Celery task (`worker/receipt_task.py`). The worker (separate process) runs the pipeline and writes the result to DB.
**Rejected alternative**: synchronous processing inside the FastAPI handler.
**Reason**: in ratis_product_analyser, a single PaddleOCR pass takes 3-15s per image (CPU-bound). Keeping it in the HTTP handler would saturate the uvicorn event loop and block other requests. Celery isolates the CPU-heavy part and enables horizontal scaling (N workers) independently of API scaling.

### DA-02 — `receipt` status derived on the fly, no `status` column

**Choice**: `GET /scan/receipt/{id}` computes the status (`pending`/`processing`/`done`/`failed`) at read time, from the states of the child `scans`: no scan = `pending`, at least 1 `pending` = `processing`, all terminal with at least 1 `accepted` = `done`, timeout + 0 scans created = `failed`.
**Rejected alternative**: a `status` column on `receipts` updated by the worker.
**Reason**: in ratis_product_analyser, the status column would duplicate the source of truth (the `scans`) and introduce divergence risks (worker crash between the INSERT scan and the UPDATE receipt.status). On-the-fly derivation costs 1 extra query but is always consistent.

### DA-03 — Label images `image_url=NULL` upon acceptance

**Choice**: as soon as a label scan transitions to `accepted`, `scans.image_url` is nullified. The R2 image is deleted (or left for the 48h lifecycle rule to purge it).
**Rejected alternative**: keep the image indefinitely for auditing.
**Reason**: in ratis_product_analyser, an electronic label contains enough indirect PII (geolocated store, timestamp) to justify minimal retention. Once the price is extracted and consolidated into the consensus, the image has no remaining value. Receipts have `image_r2_key` kept for 48h to debug the OCR pipeline if needed, but are likewise deleted afterwards.

### DA-04 — `price_consensus` with trust_score and freeze

**Choice**: each price scan for `(store_id, product_ean)` feeds `price_consensus` via weighted aggregation (by `raw_ocr_confidence`, recency). When `trust_score ≥ 95%`, the consensus is "frozen" (`frozen_until`): subsequent scans no longer modify the price until the freeze period expires.
**Rejected alternative**: simple unweighted average.
**Reason**: in ratis_product_analyser, an OCR scan with low confidence may be erroneous (4.99 vs 499€). Weighting by confidence + recency protects the consensus. Freezing at 95% prevents flip-flopping and pollution attacks (a user scanning a fake price 1000 times).

### DA-05 — Multi-stage store resolution

**Choice**: upon receipt ingestion, ratis_product_analyser attempts (1) match by user_lat/user_lng + OCR name in the local `stores` table, (2) fallback to OSM Overpass if no match, (3) fallback to "user_suggested" (`lat/lng=0`, pending admin) if still nothing.
**Rejected alternative**: systematic OSM lookup on every scan.
**Reason**: in ratis_product_analyser, OSM Overpass has a rate limit and high latency. The local `stores` cache (populated by `ratis_batch_osm_sync`) covers >90% of cases. The user_suggested fallback avoids blocking a scan when OSM finds nothing (admin review follows). See [[ARCH_store_resolution]] for details.

### DA-06 — Celery worker in a separate process, not a thread

**Choice**: the ratis_product_analyser Dockerfile defines two services: `api` (uvicorn) and `worker` (celery). Communication via Redis queue.
**Rejected alternative**: worker in a thread of the same process.
**Reason**: in ratis_product_analyser, PaddleOCR is GIL-limited + very memory-hungry (~1 GB per instance due to loaded models). Sharing the API process would block requests during OCR. The separation also allows scaling both independently (e.g. 2 API replicas + 4 workers at peak hours).

### DA-07 — LLM filter as a second layer behind PaddleOCR (alpha) — PRs #121/#122/#125/#127

**Choice**: stack PaddleOCR (text extraction + bbox) → **LLM filter** (structuring into 3 buckets: retailer / products / dismissals) → downstream matching. The LLM does NOT replace PaddleOCR; it structures what PaddleOCR already extracted. Provider config-time via env var `LLM_PROVIDER`, factory `make_default_llm_filter()` returns the right class (`LlmFilter` Mistral, `AnthropicLlmFilter`, or Ollama via `LlmFilter` with custom base_url).

**Rejected alternatives**:
- Vision LLM (sending the image to the LLM) → the image stays on our infrastructure (privacy + cost), only the OCR text leaves
- Replace PaddleOCR with LLM-only → we lose bounding boxes (useful for layout-aware filtering) and multiply token cost on text that PaddleOCR already extracts for free
- Hard-code a single provider → portability from Mistral cloud → Mac Mini self-host is non-negotiable post-alpha

**Reason**: PaddleOCR is accurate on text but cannot separate header / items / footer. The legacy regex parser attempted this separation but failed on noisy receipts. The LLM is layout-aware by nature (spatial positioning + learned heuristics) and derives a robust 3-bucket structure.

**Guardrails**:
- Phase 2h: the `LLM_FILTER_ENABLED` gate has been removed. Provisioning `LLM_API_KEY` is the only switch (empty key = inert v2 path, fallback to legacy parser).
- **Automatic fallback** to the legacy parser on HTTPError / TimeoutException / JSONDecodeError / KeyError / ValueError. A scan never crashes on an LLM failure.
- **Knowledge feedback loop**: the `dismissals` returned by the LLM are upserted into `ocr_knowledge` (extended existing table). On the next scan, OCR blocks whose text is already known as a dismissal are **pre-filtered** → token savings + the LLM focuses on new cases.
- **Arithmetic cross-validation** (PR #121): `validate_receipt(items, total_cents, tva_cents)` verifies `sum(items) + tva ≈ total` and flags incoherent receipts post-LLM. Detects LLM hallucinations.

**Alpha cost** (200 receipts × ~700 tok input + ~200 tok output): ~$0.30 on Mistral, ~$0.50 on Anthropic Haiku 4.5. Negligible.

**Known limitation (KP-31)**: on Anthropic, `cache_control={"type":"ephemeral"}` on the system prompt does not activate caching while the block is < 4096 tokens (on Haiku 4.5). Our current prompt ~1000 tokens → no-op today, forward-compat only. See KP-31 for mitigation.

### DA-08 — Debug instrumentation `scan_debug` behind flag (alpha) — PR #126

**Choice**: table `scan_debug` (1 row per scan, gated by `STORE_DEBUG=true`) persisted in the same Celery worker transaction: `rich_blocks` (PaddleOCR raw output), `llm_output` (LLM filter result if flag ON), `legacy_receipt_data` (legacy parser result running in parallel for comparison), `ocr_passes_summary` (per-pass metrics), `processed_image_r2_key` (post-preprocessing image uploaded to `debug/<scan_id>.processed.jpg` in R2).

Admin endpoint `GET /api/v1/admin/scans/{scan_id}/debug` exposed under Bearer `ADMIN_API_KEY` (router conditionally mounted only if the var is set). Returns full JSON + pre-signed R2 URLs with 15-min TTL on images.

**Rejected alternatives**:
- Store permanent debug data → R2 + DB cost explodes, not scalable and not GDPR-friendly
- Sentry breadcrumbs only → text-only, no images, no structured rich_blocks queryable after the fact

**Reason**: during alpha we need to **see what the pipeline sees** on real receipts to iterate the LLM prompt and identify OCR regressions. Without this tool, debugging = re-running the pipeline on the raw image (slow, may diverge from the original scan conditions).

**Privacy guarantee**: everything is gated by `STORE_DEBUG=true`, purged within 48h via `ratis_batch_purge::purge_scan_debug` (DB row + R2 image). In V1 the flag stays OFF, no permanent trace.

**Atomicity**: `_persist_scan_debug` never raises — if R2 or DB fails, the row is simply absent and the endpoint returns an explicit 404. The main scan is never blocked by the instrumentation.

## Main flow

### Flow 1 — Receipt scan (POST /scan/receipt)

1. The mobile client uploads the receipt image via `POST /api/v1/scan/receipt` with Bearer user + optional `user_lat`/`user_lng`.
2. ratis_product_analyser validates the image (`ratis_core.uploads.validate_image_upload`), computes a `sha256` for deduplication, creates a `receipts` row with `image_r2_key`, and uploads the image to R2.
3. ratis_product_analyser enqueues `receipt_task.process_receipt(receipt_id, user_id, user_lat, user_lng)` in Celery and returns 202 `{receipt_id}`.
4. The Celery worker pulls the task and runs the pipeline: `preprocessor` → `type_detector` (classifier) → `ocr_engine` (3-pass PaddleOCR) → `store_detector` (store resolution) → `parser` (line + total extraction) → `matcher` (raw_ocr → product_ean via `product_knowledge` + pg_trgm) → INSERT `scans` (one row per item).
5. For each `accepted` scan, the worker calls fire-and-forget `ratis_core.rewards_client.trigger_scan_accepted(user_id, scan_type='receipt')`.
6. The worker updates `price_consensus` via weighted aggregation (INSERT `price_consensus_history` + UPDATE `price_consensus`).
7. The worker calls `ratis_core.notifier_client.notify_user(user_id, type='scan_done', data={products_identified, total_amount})`.
8. The client polls `GET /scan/receipt/{id}` until `status='done'` to display the summary.

### Flow 2 — Barcode scan (POST /scan/barcode — sync)

1. The client scans an EAN-13 and sends it to `POST /api/v1/scan/barcode` with `{ean, user_lat, user_lng}`.
2. ratis_product_analyser looks up `products` (populated by the OFF batch) and returns the product page + local prices via `price_consensus` within the user's radius.
3. ratis_product_analyser creates a `scans` row of type `manual` (no image, no OCR) to count the scan (mission `barcode_scan`).
4. Synchronous response with product page + local prices. See [[ARCH_barcode]] for details.

### Flow 3 — Mass label scan (POST /scan/label/batch)

1. The client uploads N images in a single POST (electronic-label aisle scan). ratis_product_analyser creates a `session_id` and N `scan_id`s.
2. ratis_product_analyser uploads the N images to R2 and enqueues N separate Celery tasks.
3. The client polls `GET /scan/label/session/{session_id}` to track overall progress (pending/processing/done per scan).
4. Accepted images have their `image_url` nullified immediately after consensus consolidation.

## GDPR constraints specific to this service

- Receipt images are stored for 48h in R2 (lifecycle rule) + `image_deleted_at` confirms it on the DB side. Never longer.
- Electronic label images have `image_url=NULL` as soon as `status='accepted'` (immediate DB-side deletion, R2 lifecycle as backup).
- Names/first names detected on receipts (customer header) are rejected by the parser (blocklist) and never persisted in `scans.raw_ocr`. Only relevant item lines are retained.
- `user_lat`/`user_lng` (scan emission position): used for store resolution then discarded, never persisted.
- `scans.user_id` FK SET NULL: upon a user's GDPR deletion, scans remain anonymised to preserve the consensus (useful data, not PII).

## Key points (vectorised FAQ)

### Why does ratis_product_analyser use PaddleOCR and not Tesseract?

In ratis_product_analyser, PaddleOCR (`PP-OCRv4` French) significantly outperformed Tesseract on our test sets (crumpled receipts, low contrast, typos). The trade-off is the size (200-300 MB of models) and the paddlepaddle dependency that forces Python 3.12 (no 3.13 wheel). Accepted: receipts have a critical error rate on prices, and PaddleOCR cuts the failure rate by a factor of 3.

### Why is the pipeline in Celery and not in FastAPI BackgroundTasks?

In ratis_product_analyser, FastAPI's `BackgroundTasks` runs inside the web server's event loop → any latency blocks other requests. Worse: a pipeline crash crashes the FastAPI worker. Celery provides a true separate process, automatic retries, monitoring (Flower), and horizontal scaling. Trade-off: one more Redis dependency, but we already need it for slowapi rate limiting.

### How to test ratis_product_analyser locally?

1. Start the infra: `docker compose up -d` (includes Postgres, Redis, R2 mock via `minio`).
2. Pre-warm PaddleOCR: the Dockerfile does it, but locally `uv run --package ratis_product_analyser python -c "from paddleocr import PaddleOCR; PaddleOCR(use_angle_cls=True, lang='fr')"` the first time.
3. Run the tests: `uv run --package ratis_product_analyser pytest webservices/ratis_product_analyser/tests/ -v`. OCR tests use pre-annotated image fixtures in `tests/resources/`.
4. Start API + worker: `uv run --package ratis_product_analyser uvicorn main:app --port 8003 --reload` + (separate terminal) `uv run --package ratis_product_analyser celery -A celery_app worker --loglevel=info`.

### What is the difference between `scans` and `receipts`?

A `receipt` is a physical till receipt (one photo, one total, one store, one date). Several `scans` can be linked to it — one per detected item line. `scans` of type `electronic_label` or `manual` have no `receipt_id`. `scans` is the source of truth for prices; `receipts` is the metadata that groups them and carries `total_amount` as an OCR guard.

### How does ratis_product_analyser match noisy OCR to an EAN?

In ratis_product_analyser, `matcher.py` looks up `product_knowledge`: this table learns over time that `"YAOURT NATURE 4x125G"` corresponds to EAN `3033491234567`. If there is no direct match, it tries pg_trgm (similarity) + classification `ratis_core.knowledge.classify` which uses `classification_rules.json`. Unmatched items go to `unmatched` and can be manually corrected (admin queue) → feeds the knowledge for the next time. See TRAINING.md.

### Why is `price_consensus` frozen at trust_score ≥95%?

In ratis_product_analyser, a consensus with 95%+ trust_score means that N concordant scans have confirmed the price. Freezing it prevents a dubious scan (erroneous OCR, vandalism) from modifying this price during `frozen_until`. The freeze is periodically lifted to allow legitimate updates (price change in store). Parameters in `ratis_settings.json.consensus`.

### What is the difference between ratis_product_analyser and ratis_list_optimiser on prices?

ratis_product_analyser writes `price_consensus` (DB tables) via scans. ratis_list_optimiser only reads this table to choose the cheapest store. Both services share the DB but do not call each other via HTTP — `price_consensus` is the explicit contractual boundary.

## Sub-ARCHs

- [[ARCH_barcode]] — EAN scanning (sync flow, endpoint `POST /scan/barcode`) + details of product/local-price lookup.
- [[ARCH_consensus]] — `price_consensus` aggregation algorithm, trust_score, history, freeze.
- [[ARCH_store_resolution]] — multi-stage store resolution (local `stores` cache → OSM Overpass → user_suggested).

## Glossary

- **DA-XX**: numbered architecture decision (see dedicated section).
- **EAN**: European Article Number, 13-digit product barcode. PK of `products`.
- **OCR 3 passes**: PaddleOCR is run three times with different preprocessings (contrast level, rotation), then an arbitrator picks the best extraction.
- **`price_consensus`**: table that consolidates the consensus value of a price for a (store_id, product_ean) pair, weighted by confidence + recency.
- **trust_score**: score ∈ [0,1] indicating the reliability of the consensus. Frozen at ≥0.95.
- **SENTINEL_DATE**: `1970-01-01` used in `receipts.date` when the receipt date could not be resolved by OCR.
- **R2**: Cloudflare R2, S3-compatible object storage with no egress fees, used for receipt images with a 48h lifecycle rule.
- **Overpass**: OSM API for geographic queries (looking up a store by name + coordinates).
