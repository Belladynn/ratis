---
# Identity
type: cross-cutting
status: production

# Navigation (Obsidian + RAG)
parent: ARCH_RATIS
sub_archs: []
related: [ARCH_store_resolution, ARCH_store_validation, ARCH_ocr_store_detection, ARCH_BATCH_OSM_SYNC, ARCH_deployment]

# Technical
tech: [PostgreSQL, PostGIS, SQLAlchemy, GeoAlchemy2]
tables: [stores]
env_vars: []

# Business
tags: [geo, postgis, proximity, infra]
business_domain: infra
rgpd_concern: false

# Freshness (MANDATORY — R34 — update on every edit)
updated: 2026-05-15
---

# ratis_core.geo — Ratis PostGIS geospatial layer

> Shared Ratis geospatial layer: PostGIS spatial index on `stores` + stateless Python module `ratis_core.geo` that replaces proximity searches by full-table-scan + manual haversine. Migrated consumers see dedicated section.
> @tags: geo postgis proximity infra ratis_core stores spatial-index geoalchemy2 haversine-replace osm-deprecated
> @status: LIVRÉ V0
> @subs: auto

> [[ARCH_RATIS]] · relations : [[ARCH_store_resolution]], [[ARCH_store_validation]], [[ARCH_ocr_store_detection]], [[ARCH_BATCH_OSM_SYNC]], [[ARCH_deployment]]

## Index

- [Summary in one sentence](#summary-in-one-sentence) · L.30
- [Objective](#objective) · L.34
- [Responsibility](#responsibility) · L.46
- [Accepted decisions](#accepted-decisions) · L.54
- [Architecture](#architecture) · L.78
- [The `ratis_core.geo` module](#the-ratis_coregeo-module) · L.104
- [Migrated consumers](#migrated-consumers) · L.122
- [Implementation checklist](#implementation-checklist) · L.134
- [Out of scope](#out-of-scope) · L.146
- [Key facts (vectorised FAQ)](#key-facts-vectorised-faq) · L.154
- [Pointers](#pointers) · L.176
- [Glossary](#glossary) · L.182

---

## Summary in one sentence

`ratis_core.geo` is the shared geospatial layer of Ratis: a PostGIS spatial index on the `stores` table and a stateless Python module that replaces proximity searches by full-table-scan + manual haversine.

## Objective

The "stores around a point" proximity search is a central, cross-cutting operation in Ratis (LO route, store detection at label scan, savings computation). Before this project, haversine was re-implemented service by service and every query performed a *full table scan* of `stores` — a real performance risk at the intended scale (~200-500 k stores ingested from OSM).

This project lays a **reusable geo foundation**:

- a **GIST spatial index** on `stores`, via a generated `geography` column;
- a **shared module `ratis_core.geo`** where all current and future consumers plug in;
- the **replacement of proximity searches** by `ST_DWithin` / KNN PostGIS.

This is a migration/refactor (proximity already existed in prod), not a from-scratch design. We are building the foundation, **not** future geo features (YAGNI).

## Responsibility

- `ratis_core.geo` exposes shared spatial proximity helpers (`stores_within_radius`, `nearest_stores`, `distance_km`).
- The `stores` table carries the generated column `geog` (`geography(Point,4326)`) and its GIST index `ix_stores_geog`.
- The PostGIS extension is enabled by an Alembic migration; the custom Postgres image `db/Dockerfile` makes it natively available in dev, CI, and prod.
- The module is stateless: radius and filters are passed as arguments by each caller.

## Accepted decisions

### DA-01 — Ambition = reusable geo foundation

**Choice**: build a clean geo module where future geo features will plug in, without building those features.
**Reason**: the symptom motivating this project is the absence of a common home for proximity (haversine reinvented everywhere). We fix that gap; we do not guess future needs (YAGNI).

### DA-02 — Delivery = single project

**Choice**: one spec, one ARCH, the 5 consumers migrated together in the same project.
**Reason**: the geo layer is cohesive — delivering it in pieces would leave residual haversine code and two proximity conventions running in parallel.

### DA-03 — Spatial storage = generated `geography` column

**Choice**: column `stores.geog` derived from `lat`/`lng` by Postgres (`GENERATED ALWAYS AS … STORED`).
**Rejected alternative**: application-maintained column (dual write lat/lng + geog).
**Reason**: single source of truth, consistency guaranteed by Postgres, zero application maintenance — avoids the family of bugs caused by drift between two sources.

### DA-04 — Type `geography` (not `geometry`), SRID 4326

**Choice**: `geography(Point,4326)`.
**Reason**: distances in metres, correct earth curvature, no projection concerns.

### DA-05 — Module `ratis_core.geo` stateless and config-agnostic

**Choice**: radius and filters passed as arguments; the module reads no config.
**Reason**: each consumer reads its own config and passes it in; no reorganisation of settings keys (YAGNI — geo keys stay where they are).

### DA-06 — Postgres image: custom Dockerfile `db/Dockerfile`

**Choice**: `db/Dockerfile` = `FROM postgres:16` + `apt-get install postgresql-16-postgis-3`; the 3 compose files switch from `image:` to `build:`.
**Rejected alternative**: official `postgis/postgis` image.
**Reason**: `postgis/postgis` is published **for amd64 only** → emulated on the arm64 Mac mini (dev *and* CI runners). `postgres:16` is the official multi-arch image → the custom image runs natively everywhere (arm64 dev/CI, x86 prod), with no third-party dependency.

### DA-07 — The DA-32 note is superseded

The note "Manual SQL haversine (not PostGIS), OK for V1" (documented and bounded V1 shortcut) is **superseded** by this project: PostGIS becomes the official geo layer of Ratis. See the corresponding entry in `DECISIONS_ACTED.md`.

## Architecture

### Infrastructure

- **Postgres image**: custom Dockerfile `db/Dockerfile` (`FROM postgres:16` + `postgresql-16-postgis-3`). Common tag `ratis-postgis:16-3` shared between `docker-compose.yml`, `docker-compose.prod.yml` (`context: ./db`) and `runner/docker-compose.yml` (`context: ../db`).
- **Extension activation**: Alembic migration `20260515_1200_postgis_geo_layer` — `CREATE EXTENSION IF NOT EXISTS postgis` as the first operation. The `ratis` user is a superuser of its Docker instance → no extra privilege required (dev, CI, self-hosted prod are identical).

### Schema — `stores` table

The migration adds to `stores`:

```sql
ALTER TABLE stores ADD COLUMN geog geography(Point, 4326)
  GENERATED ALWAYS AS (
    CASE WHEN lat = 0 AND lng = 0 THEN NULL
         ELSE ST_SetSRID(ST_MakePoint(lng, lat), 4326)::geography END
  ) STORED;

CREATE INDEX ix_stores_geog ON stores USING GIST (geog);
```

- **Generated column**: automatic backfill of existing rows at creation time. All operands of the expression are `IMMUTABLE`.
- Ghost stores `(0,0)` (`source='user_suggested'` pending admin review) → `geog = NULL` → natively excluded from any `ST_DWithin` query (NULL never matches).
- `downgrade()` removes the index and the column but **intentionally leaves the PostGIS extension in place**.

### Alembic watchpoint

Alembic autogenerate + PostGIS produces noise (system table `spatial_ref_sys`). `alembic/env.py` filters PostGIS system objects to prevent them from appearing in autogenerated migrations.

## The `ratis_core.geo` module

**Location**: `ratis_core/ratis_core/geo.py` — single responsibility: spatial proximity.

**Public API**:

- `stores_within_radius(db, lat, lng, radius_km, *, include_disabled=False, exclude_store_ids=None, retailer_id=None) -> list[StoreProximity]` — stores within the radius, sorted nearest to farthest, with distance. Built on `ST_DWithin` + KNN sort `geog <-> point`.
- `nearest_stores(db, lat, lng, k=1, *, max_radius_km=None, retailer_id=None) -> list[StoreProximity]` — the `k` nearest active stores (indexed KNN `ORDER BY geog <-> point LIMIT k`).
- `distance_km(lat1, lng1, lat2, lng2) -> float` — haversine distance between two points, pure in-memory computation (no DB), for tests and display.

**Return type** `StoreProximity`: dataclass `(store: Store, distance_km: float)` — `store` is the full ORM `Store` object. Distance always in km.

**Implementation**: spatial queries in parameterised SQL (`text()`) inside the module — `ratis_core.geo` acts as a shared geo repository, legitimate for a common library (R03). `ST_DWithin` takes metres → internal conversion `radius_km * 1000`. Stateless module.

## Migrated consumers

5 consumers are migrated. Functional behaviour is **identical** (same stores, same distances) — just indexed. No change to any public service API.

The 3 "pure" store proximity searches go through the `ratis_core.geo` module functions. The 2 queries that join `stores` to other tables (prices, dedup) keep their SQL and simply replace haversine with `ST_DWithin` (same approach as the savings CTE).

| Consumer | Before | After |
|---|---|---|
| **LO route** — `optimization_service._compute_route_data` | `SELECT` all stores + Python haversine | `geo.stores_within_radius(...)` ; removal of the local `haversine_km` |
| **`get_nearest_store`** — `scan_repository` (`_NEAREST_STORE_SQL`) | full scan `stores` + SQL haversine | `geo.nearest_stores(...)` ; the `unambiguous` logic (2× ratio) stays in `get_nearest_store` |
| **Batch savings** — `compute_savings_for_user` (`ratis_core/savings.py`, `_SAVINGS_SQL`) | SQL haversine in the `nearby_stores` CTE | the `nearby_stores` CTE uses a `ST_DWithin` condition; the single CTE query is preserved |
| **PA proximity prices** — `barcode_repository.get_nearby_prices` (`_NEARBY_SQL`, `_NEARBY_EXCLUDE_SQL`) | full scan + SQL haversine, JOIN `price_consensus` | `ST_DWithin` + `ST_Distance` on `stores.geog`; price JOIN and `price ASC` sort preserved |
| **PA store dedup** — `store_creation_service._find_existing_nearby` (`_DEDUP_SQL`) | full scan + SQL haversine ×2, 50 m radius | `ST_DWithin` + KNN sort `geog <-> point` |

## Implementation checklist

- [x] Task 1 — custom Postgres+PostGIS image (`db/Dockerfile`, 3 compose files using `build:`)
- [x] Task 2 — PostGIS extension in the 20 test conftests + Alembic `spatial_ref_sys` filter
- [x] Task 3 — generated column `stores.geog` + GIST index + migration `20260515_1200_postgis_geo_layer`
- [x] Task 4 — module `ratis_core.geo` (`stores_within_radius`, `nearest_stores`, `distance_km`)
- [x] Task 5 — LO `_compute_route_data` migration → `geo.stores_within_radius`
- [x] Task 6 — `get_nearest_store` migration → `geo.nearest_stores`
- [x] Task 7 — savings `_SAVINGS_SQL` CTE `nearby_stores` migration → `ST_DWithin`
- [x] Task 8 — documentation (this ARCH, `DECISIONS_ACTED.md`, cross-references in store ARCHs)
- [x] Task 9 — PA `barcode_repository` (proximity prices) + `store_creation_service` (dedup) migration → `ST_DWithin`

## Out of scope

- **PA `reconciliation_service._MATCH_SQL`** is **not** migrated: this query looks for *scans* near a point (not stores), and its candidate set is already bounded (`scans` filtered by `user_id` + 7-day window) — no full-table scan, hence no performance issue. Migrating it would require spatially indexing PII coordinates (`scans.user_lat/user_lng`) for zero gain.
- **No new geo features** ("prices around you" dedicated view, catchment-area polygons, heatmaps, geolocated missions) — the foundation will make them easy; we are not building them here.
- No reorganisation of geo settings keys — the module is config-agnostic.

## Key facts (vectorised FAQ)

### Why PostGIS and not a manual SQL haversine?

The manual haversine forced a full-table scan of `stores` on every proximity search. At the intended scale (~200-500 k OSM stores), this is a performance risk on user-facing paths (LO route, label scan). PostGIS + GIST index enables indexed `ST_DWithin` and KNN searches. This is the graduation of the V1 shortcut noted in DA-32.

### Why a generated `geog` column rather than one written by the application?

A `GENERATED ALWAYS AS … STORED` column derives `geog` from `lat`/`lng` directly in Postgres: single source of truth, guaranteed consistency, automatic backfill at migration time. An application-maintained column would open the door to drift between `lat`/`lng` and `geog`.

### Why a custom Postgres image `db/Dockerfile`?

The official `postgis/postgis` image is published for amd64 only. On the arm64 Mac mini (dev and CI runners), it would run under emulation. `db/Dockerfile` starts from `postgres:16` (official multi-arch) and installs `postgresql-16-postgis-3` → the image runs natively everywhere.

### How to test the geo layer locally?

Test conftests bootstrap their database via `Base.metadata.create_all` (not Alembic), so each `conftest.py` that creates the schema adds `CREATE EXTENSION IF NOT EXISTS postgis`. Tests: `uv run pytest ratis_core/tests/test_geo.py -v`.

## Pointers

- **Accepted decision**: PostGIS entry in `DECISIONS_ACTED.md` (supersedes the DA-32 note)
- **Migration**: `alembic/versions/20260515_1200_postgis_geo_layer.py`
- **Module**: `ratis_core/ratis_core/geo.py`

## Glossary

- **PostGIS**: geospatial extension for PostgreSQL — `geometry`/`geography` types, spatial indexes, distance and proximity computation functions.
- **`geography(Point,4326)`**: PostGIS type for a point in WGS84 coordinates (SRID 4326) — distances are computed in metres on the terrestrial ellipsoid.
- **Generated column (`GENERATED ALWAYS AS … STORED`)**: a column whose value is computed by Postgres from other columns; read-only from the application's perspective.
- **GIST index**: Generalized Search Tree — PostgreSQL index type used by PostGIS for spatial searches (`ST_DWithin`, KNN).
- **`ST_DWithin`**: PostGIS function testing whether two geometries are within a given distance — uses the spatial index.
- **KNN (`<->`)**: PostGIS distance operator for "k nearest neighbours" sorting, accelerated by the GIST index.
- **DA-XX**: numbered architecture decision.
