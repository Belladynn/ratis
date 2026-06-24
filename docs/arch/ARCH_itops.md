---
# Identité
type: cross-cutting
service: itops
status: in-progress

# Navigation (Obsidian + RAG)
parent: ARCH_RATIS
sub_archs: []
related: [ARCH_deployment, ARCH_observability]

# Technique
tech: [Docker, Docker Compose, Healthchecks, Watchtower, Uptime Kuma, Loki, Promtail, SQLite]
tables: []
env_vars:
  - HEALTHCHECKS_PORT
  - HEALTHCHECKS_SITE_ROOT
  - HEALTHCHECKS_SITE_NAME
  - HEALTHCHECKS_SUPERUSER_EMAIL
  - HEALTHCHECKS_SUPERUSER_PASSWORD
  - HEALTHCHECKS_SECRET_KEY
  - HEALTHCHECKS_ALLOWED_HOSTS
  - HEALTHCHECKS_PING_URL
  - HEALTHCHECKS_DEFAULT_FROM_EMAIL
  - HEALTHCHECKS_EMAIL_HOST
  - HEALTHCHECKS_EMAIL_PORT
  - HEALTHCHECKS_EMAIL_HOST_USER
  - HEALTHCHECKS_EMAIL_HOST_PASSWORD
  - HEALTHCHECKS_EMAIL_USE_TLS
  - WATCHTOWER_SCHEDULE
  - UPTIME_KUMA_PORT
  - LOKI_PORT
  - LOKI_RETENTION_PERIOD
  - TZ
depends_on: [docker]

# Business
tags: [itops, monitoring, auto-update, observability, logs, self-hosted, mac-mini]
business_domain: infra
rgpd_concern: false

# Freshness (MANDATORY — R34 — update à chaque édition)
updated: 2026-05-05
---

# itops — self-hosted ops stack (Healthchecks · Watchtower · Uptime Kuma · Loki/Promtail)

> Self-hosted ops stack on Mac mini: Healthchecks (cron monitoring + email alerts), Watchtower (Docker image auto-update), Uptime Kuma (uptime status page), Loki+Promtail (centralised logs). Everything in `infra/itops/` Docker Compose.
> @tags: itops monitoring auto-update observability logs self-hosted mac-mini healthchecks watchtower uptime-kuma loki promtail docker
> @status: EN-COURS
> @subs: auto

> [[ARCH_RATIS]] · relations : [[ARCH_deployment]]

> This ARCH describes the **ITOps stack** deployed on the Mac mini after the Windows→Mac migration (PR #287). Phase A = batch monitoring (Healthchecks) + opt-in container auto-update (Watchtower). Phase B (this PR) = adds Uptime Kuma (HTTP/TCP probes) + Loki/Promtail (queryable log aggregation via REST API). Phase C = n8n consumes Sentry + Healthchecks + Uptime Kuma + Loki to orchestrate Notion tickets.

## Index

- [Vision](#vision) · L.65
- [Components](#components) · L.93
- [Architecture & topology](#architecture--topology) · L.190
- [Key architecture decisions](#key-architecture-decisions) · L.250
- [Implementation checklist](#implementation-checklist) · L.345
- [Runbook](#runbook) · L.414
- [Key facts (vectorised FAQ)](#things-to-know-vectorised-faq) · L.568
- [Glossary](#glossary) · L.669

---

## Vision

Since the Windows → Mac mini M4 Pro migration (PR #287, 2026-05-04), the
Ratis production stack is entirely served from this host: 16 self-hosted GH
Actions runners, dev Docker stack, future V0 services. The ITOps Phase A
stack addresses **two concrete ops needs**:

1. **Batch job visibility** — Ratis has 8+ cron batches (`ratis_batch_osm_sync`,
   `ratis_batch_off_sync`, `ratis_batch_consensus`, `ratis_batch_purge`,
   `ratis_batch_reconciliation`, `ratis_batch_mystery_announce`,
   `ratis_batch_savings`, `ratis_batch_referral_payout`). Without end-of-run
   pings to an external service, a batch that stops running goes unnoticed
   for days. **Healthchecks** solves this: each batch pings a URL
   at the end (success or fail), and alerts if a ping is missed (dead cron,
   host down, OOM).

2. **Containers up-to-date without manual intervention** — on a self-hosted
   host there is no `apt unattended-upgrades` or managed service that patches
   third-party images (Postgres, Redis, OSRM, etc.). **Watchtower** pulls
   new images and restarts the affected containers on a cron schedule, with
   an **opt-in label** scope to avoid wildly updating stacks (runners, dev)
   that we want to manage manually.

Phase A is intentionally minimal: local SQLite for Healthchecks,
no SMTP, no Watchtower notifications. We ship now to build the ops
reflex from day one, and enrich in Phase B (alert notifications,
clean Tailscale exposure, eventual TLS).

## Components

### Healthchecks (`healthchecks/healthchecks:latest`)

- **Role**: HTTP ping-based cron job monitoring. Each Ratis batch (and
  potentially each future web service) pings `<SITE_ROOT>/ping/<uuid>`
  at the start and end (success/fail) of a run. Healthchecks alerts
  via email/Slack/etc if a ping is late or repeatedly fails.
- **Phase A scope**: monitoring the 8 `ratis_batch_*` batches. UI accessible
  from the Mac mini (port 8000). No public exposure yet.
- **Storage**: local SQLite in the `healthchecks_data` volume. Zero
  critical data (UI config + ping history); rebuild from scratch is
  acceptable. See DA-38.
- **Email**: not configured in Phase A (`HEALTHCHECKS_EMAIL_*` env vars
  empty). Alerts visible in the UI only. SMTP in Phase B.
- **Future integration with `ratis_batch_*`**: each batch adds at the start
  of `main()` a `requests.get(os.environ["HC_PING_URL_<NAME>"])` (start)
  and at the end of the run a `.../<uuid>` or `.../<uuid>/fail`. Pattern to
  be documented in `SA_DEV.md` § new batch boilerplate when Phase A is active.

### Watchtower (`containrrr/watchtower:latest`)

- **Role**: daemon that watches Docker images of RUNNING containers, pulls
  new versions on a cron schedule, and recreates the affected containers.
- **Phase A scope**: **opt-in via label** `com.centurylinklabs.watchtower.enable=true`
  only. In practice: only Healthchecks + Watchtower itself are candidates
  for auto-update. Other compose stacks (runners, ratis dev, future backend
  services) must add the label explicitly to participate. See DA-39.
- **Cleanup**: `WATCHTOWER_CLEANUP=true` → old image removed after a
  successful pull (disk saving on Mac mini SSD).
- **Cron**: `0 0 4 * * *` = every day at 04:00 Europe/Paris. Override via
  `WATCHTOWER_SCHEDULE` in `.env`.
- **Notifications**: not configured in Phase A. Phase B = Slack/Discord
  channel to see daily which containers were updated.

### Uptime Kuma (`louislam/uptime-kuma:1`) — Phase B.1

- **Role**: HTTP/TCP/keyword/JSON probes on Ratis services to
  detect application unavailability (beyond a simple "container running"
  check). Viewable uptime dashboard, optional public status pages,
  multi-channel notifications (Discord, Slack, email SMTP, webhook…).
- **Phase B scope**: one monitor per Ratis service (auth/PA/LO/RW/NT) +
  OSRM + Healthchecks itself. Suggested interval 60s. Initial configuration
  via UI (first access = admin wizard) — no provisioning in Phase B
  for V1 (low monitor count, manual addition is acceptable).
- **Storage**: local SQLite in the `uptime_kuma_data` volume. Like
  Healthchecks, losing the volume means recreating monitors in the UI (~10 min).
- **Port**: 3001 (UI + API). Bound on all host interfaces —
  same Tailscale/TLS constraints as Healthchecks for public exposure.
- **No Watchtower opt-in label** (DA-42): manual updates.

### Loki (`grafana/loki:3.3.0`) — Phase B.2

- **Role**: log aggregator queryable via Prometheus-inspired REST API
  (LogQL). Stores logs in compressed chunks on filesystem + TSDB
  index. Indexes by **labels** only (not by content — full-text via
  `|=` `|~` filters at query-time).
- **Phase B scope**: ingestion of stdout/stderr logs from all Docker
  containers on the host via Promtail. Indexed labels:
  `container_name` · `compose_project` · `compose_service` ·
  `service_name` (canonical Loki 3.x label — populated with priority
  `com.ratis.service` > `com.docker.compose.service` > container_name) ·
  `image` · `job=docker`. Note: Loki 3.x automatically renames the
  `service` label to `service_name` (OpenTelemetry-compat convention).
- **Storage**: local filesystem in the `loki_data` volume. Single-binary
  mode (no cluster). Schema v13 + TSDB shipper.
- **Retention**: 14 days by default (DA-41). Compactor purges automatically
  every 10 min.
- **Auth**: disabled — private Docker network `itops_net` + localhost
  bind on the host side. Tailscale will add a network layer for cross-host exposure.
- **API consumed by n8n** (Phase C): `/loki/api/v1/query_range` to
  scan error windows, `/loki/api/v1/labels` for dynamic discovery
  of services to monitor.

### Promtail (`grafana/promtail:3.3.0`) — Phase B.2

- **Role**: push-only agent that collects Docker container logs
  (via Docker socket + `docker_sd_configs`) and pushes them to Loki.
- **Phase B scope**: collects all running containers on the host
  (dev stack, runners, itops, future prod services). Relabel rules
  extract `container_name`, `compose_project`, `compose_service`,
  `service`, `image` from Docker / docker-compose labels.
- **Pipeline parsing**: no active `json` pipeline today — Ratis
  services log in plain text via stdlib `logging` (no JSONFormatter /
  structlog in place). Consequence: `level` and `user_id`
  are NOT extracted as Loki labels; they remain in the raw
  text, queryable only via `|= "ERROR"` or
  `|~ "user_id=(\\d+)"` filters. A "structured JSON logging" upgrade is a
  separate follow-up pre-Phase-C to unlock native queries by user_id.
- **Mounted volumes**: Docker socket (read-only) + `/var/lib/docker/containers`
  (read-only). On macOS Docker Desktop, the second mount may not be
  functional (Docker runs inside a Linux VM) — Promtail then falls back
  to streaming via the Docker API, which works cross-platform. If a
  log gap is observed on Mac, the documented fallback is the `loki-docker-driver`
  plugin on the daemon side (push driver-side instead of sidecar agent).
- **No exposed port**: push-only agent, communicates with Loki
  intra-Docker network `itops_net`.

## Architecture & topology

```
┌────────────────────────── Mac mini M4 Pro (host) ────────────────────────────────┐
│                                                                                  │
│  Docker Desktop (or Colima — `docker info` ok)                                   │
│                                                                                  │
│  ┌─ stack: runner/docker-compose.yml ────────────┐                               │
│  │  16× actions-runner-N (GH Actions self-host)  │  no watchtower label          │
│  └───────────────────────────────────────────────┘                               │
│                                                                                  │
│  ┌─ stack: docker-compose.yml (dev) ─────────────┐                               │
│  │  postgres · redis · osrm · ratis_*            │  logs collected ▶ Promtail    │
│  └───────────────────────────────────────────────┘                               │
│                                                                                  │
│  ┌─ stack: infra/itops/docker-compose.yml ─────────────────────────────────┐    │
│  │  network: ratis_itops_net                                                │    │
│  │                                                                          │    │
│  │  Phase A                                                                 │    │
│  │  ┌─ healthchecks ─┐   ┌─ watchtower ─────┐                              │    │
│  │  │  port 8000     │   │ /var/run/..sock  │   self-update label only      │    │
│  │  │  vol: hc_data  │   │ LABEL_ENABLE     │   (no Phase B services)       │    │
│  │  └────────────────┘   └──────────────────┘                              │    │
│  │                                                                          │    │
│  │  Phase B (this PR)                                                       │    │
│  │  ┌─ uptime-kuma ──┐   ┌─ loki ───────┐    ┌─ promtail ──────────────┐  │    │
│  │  │  port 3001     │   │ port 3100    │◄───┤ docker.sock (ro)        │  │    │
│  │  │  vol: kuma_data│   │ vol: loki_   │    │ /var/lib/docker/        │  │    │
│  │  │  HTTP/TCP probe│   │   data       │    │   containers (ro)       │  │    │
│  │  │  → Ratis svcs  │   │ retention 14j│    │ docker_sd_configs       │  │    │
│  │  │                │   │ /ready       │    │ → push to loki:3100     │  │    │
│  │  └────────────────┘   └──────────────┘    └─────────────────────────┘  │    │
│  │       (no auto-update labels — DA-42, manual control for ITOps stack)   │    │
│  │                                                                          │    │
│  │  Loki HTTP 3100 bound on 127.0.0.1 → consumed locally                   │    │
│  │     by Promtail (intra-net) AND by n8n (Phase C, host-side)             │    │
│  └──────────────────────────────────────────────────────────────────────────┘    │
│                                                                                  │
│  Tailscale daemon (installed via `brew --cask`, auth interactive — out of scope) │
│                                                                                  │
└──────────────────────────────────────────────────────────────────────────────────┘
```

- **Network**: `ratis_itops_net` (bridge, dedicated to the itops stack). The
  itops containers communicate with each other via this network. No cross-network
  with the dev stack or runners. Promtail accesses host containers via the
  Docker socket — network isolation does not affect it.
- **Volumes**: `healthchecks_data` (SQLite + assets), `uptime_kuma_data`
  (SQLite + monitors config), `loki_data` (chunks + TSDB index).
- **Ports exposed on the host**: `8000` (Healthchecks UI), `3001` (Uptime
  Kuma UI), `3100` **bound 127.0.0.1** (Loki API). Watchtower and Promtail
  are agents/workers with no host port.
- **Log flow**: Docker containers → stdout/stderr → Docker daemon →
  Promtail (via socket+docker_sd) → Loki API push → filesystem chunks.
- **Phase C flow (n8n, future)**: local n8n queries `localhost:3100/loki/api/v1/query_range`,
  `localhost:8000/api/v1/checks/` (Healthchecks), `localhost:3001/api/status-page/heartbeat/<slug>`
  (Uptime Kuma) + Sentry HTTP API → correlates → creates Notion API tickets.
- **Secrets**: all via `.env` (gitignored). No secret hardcoded in the
  compose or in this ARCH.

## Key architecture decisions

### DA-38 — Healthchecks: local SQLite in Phase A (not Postgres)

**Choice**: local SQLite in a dedicated Docker volume (`healthchecks_data`).
**Rejected alternative**: point Healthchecks at the existing dev/prod
Postgres or a dedicated Postgres instance.
**Reason**: the ITOps stack must be **self-sufficient and independent**
from the Ratis Postgres. If the dev/prod PG is down or being migrated, we
still want to see batch pings arriving. Healthchecks data is UI
config + ping history (not business-critical, reproducible from
scratch in 5 min: recreate checks in the UI). SQLite comfortably covers
the throughput (8 batches × ~1 ping/day = 8 pings/day). Moving to PG
will become useful if we also monitor web services (ping on every
health request), which we will not cross before Phase B/C.

### DA-39 — Watchtower: opt-in scope via label, never global

**Choice**: `WATCHTOWER_LABEL_ENABLE=true` → only containers that
explicitly carry the label `com.centurylinklabs.watchtower.enable=true`
are auto-updated.
**Rejected alternative**: global scope by default (Watchtower's default
behaviour: all running containers on the host).
**Reason**: several stacks coexist on the Mac mini that we do **not**
want wildly auto-updated: (a) the 16 GH Actions runners have versions
pinned by GitHub — an unexpected restart mid-job breaks a build;
(b) the dev stack (Postgres, Redis, OSRM) is intentionally frozen for
reproducibility — we want to bump versions manually with a checklist;
(c) future Ratis backend services will be deployed from private registries
patched via PR/CI, not by host auto-update.
Consequence: adopting Watchtower on a new service is a
deliberate action (adding the label in its compose). No surprises.

### DA-40 — Log stack: Grafana Loki + Promtail (not ELK / Vector / OpenSearch)

**Choice**: `grafana/loki` (single-binary) + `grafana/promtail` (docker_sd
sidecar agent).
**Rejected alternatives**:
- **Elasticsearch + Logstash + Kibana (ELK)**: too RAM-heavy (ES alone
  consumes 1-2 GB minimum), JVM overhead unnecessary on Mac mini, operational
  complexity (sharding, mapping, ILM) out of proportion for a V0/V1
  single-host setup.
- **Vector + ClickHouse**: Vector is mature but ClickHouse as a log
  store is not native (requires a custom schema + third-party UI). Less
  battle-tested for the log-aggregation case; riskier to debug in production.
- **OpenSearch**: ES fork under Apache 2.0 — solves the licence but not
  the RAM/complexity overhead.
**Reason for Loki**: (a) **label-based indexing** = ultra-light disk/RAM
footprint (compressed chunks, minimal TSDB index); (b) **REST API
LogQL** Prometheus-like = trivially consumable by n8n in Phase C;
(c) **Grafana ecosystem** = Promtail/Alloy/Grafana integrate
out-of-the-box, a Grafana UI can be bolted on in Phase B.3 if needed
without touching the backend; (d) **OSS Apache 2.0** stable since 2019,
massively adopted (CNCF incubating). For our target volume (~5-50 MB
logs/day with 5-7 services in V1), Loki single-binary is comfortably
over-provisioned in performance and under-provisioned in operational
complexity — exactly what we want.

### DA-41 — Loki retention: 14 days by default

**Choice**: `retention_period: 336h` (14 days), configurable via
`LOKI_RETENTION_PERIOD` in `.env`.
**Rejected alternative**: 7d (too short for incidents that surface at the
end of the week + weekend debug) or 30d (Mac mini storage, and most
Ratis incidents are caught within the first week).
**Reason**: trade-off between (a) a debug window sufficient for the majority
of incidents (typical user issues resurface in 1-2 weeks) and
(b) disk consumption on Mac mini SSD (estimated < 5 GB for 14d of
multi-service logs, negligible). The Loki compactor purges automatically
every 10 min — no manual operation required. Override in plain text
via env var for cases where we debug an old bug (temporarily bump
to 720h = 30d).

### DA-42 — ITOps stack: no Watchtower opt-in label on Phase B services

**Choice**: Healthchecks and Watchtower carry the label
`com.centurylinklabs.watchtower.enable=true` (Phase A). Uptime Kuma, Loki
and Promtail do **NOT** have this label.
**Rejected alternative**: tag the entire itops stack to benefit from
auto-update.
**Reason**: (a) Uptime Kuma stores monitor configuration in
SQLite — a breaking change in the image (rare but possible: Node SDK
update, DB schema migration) may require manual intervention
(re-add monitors, re-auth) that we do not want to discover on waking up. (b) Loki
and Promtail are **version-coupled** (push API stable within a minor)
— a Watchtower that bumped Loki alone (Promtail not updated if the
promtail image tag is latest but not recently pulled) silently breaks ingestion.
**We control Loki/Promtail bumps manually** via
`docker compose pull && up -d` after reading the changelog. Healthchecks
remains auto-updated (stable Django image, breaking changes announced via
release notes readable post-fact). Watchtower remains activatable on a
case-by-case basis for Ratis application services in V1 — that is the
very purpose of the opt-in DA-39.

## Implementation checklist

### Phase A (merged — PR #294)

- [x] `infra/itops/docker-compose.yml`: 2 services + network + volume
- [x] `infra/itops/.env.example`: variables documented with placeholders
- [x] `infra/itops/README.md`: quick-start
- [x] `ARCH_itops.md`: this document
- [x] Local validation: `docker compose config` + `up -d` + `ps`
- [x] PR merged on `infra/itops-phase-a` → main (commit 7257c63)

### Phase B.1 — Uptime Kuma (this PR)

- [x] `infra/itops/docker-compose.yml`: `uptime-kuma` service added
      (image pinned `:1`, volume `uptime_kuma_data`, port 3001, healthcheck
      Node http GET, no Watchtower label DA-42)
- [x] `infra/itops/.env.example`: variable `UPTIME_KUMA_PORT` documented
- [x] `infra/itops/README.md`: quick-start + first setup wizard
- [x] `ARCH_itops.md`: component + DA-42 + runbook section

### Phase B.2 — Loki + Promtail (this PR)

- [x] `infra/itops/docker-compose.yml`: `loki` + `promtail` services added
      (Loki port 3100 bind 127.0.0.1, Promtail no-port with depends_on
      service_healthy, Docker socket + containers ro volumes)
- [x] `infra/itops/loki/loki-config.yml`: single-binary config, schema v13
      TSDB shipper, 14d retention via compactor (DA-41)
- [x] `infra/itops/promtail/promtail-config.yml`: docker_sd_configs with
      relabel rules for `service` / `container_name` / `compose_project`
      / `compose_service` / `image`. No JSON pipeline (Ratis services
      = plain text, separate structured logging follow-up).
- [x] `infra/itops/.env.example`: `LOKI_PORT` + `LOKI_RETENTION_PERIOD`
- [x] `infra/itops/README.md`: Loki query API section + LogQL examples
- [x] `ARCH_itops.md`: Loki/Promtail components + DA-40/41 + runbook +
      Phase C flow documented
- [x] Local validation: `docker compose config` + `up -d` + `/ready` +
      `/loki/api/v1/labels`

### Phase B.3 (optional, post-merge if needed)

- [ ] Grafana UI on Loki (visual dashboards, ad-hoc exploration) — to
      install only if n8n + CLI queries are insufficient for debug
- [ ] Tailscale exposure (Healthchecks + Uptime Kuma accessible from tailnet)
- [ ] Configure Healthchecks SMTP (transactional provider to be chosen)
- [ ] Configure Watchtower notifications (Slack/Discord webhook)
- [ ] Configure Uptime Kuma notification channels (Discord/Slack/email)
- [ ] Add Uptime Kuma monitors for the 5 Ratis services (auth/PA/LO/RW/NT)
      + OSRM + Healthchecks itself (interval 60s)
- [ ] Wire the 8 `ratis_batch_*` batches to Healthchecks (UUID env vars
      + ping start/success/fail)
- [ ] Document the pattern in `SA_DEV.md` § new batch boilerplate
- [ ] Upgrade structured JSON logging in Ratis services (JSONFormatter
      `{level, ts, service, user_id, request_id, msg}`) → enables Loki
      to extract `level` / `user_id` as labels via Promtail `json` pipeline.
      Pre-requisite for Phase C for trivial query-by-user_id.
- [ ] Decide on SQLite → PG migration if we exceed ~100 Healthchecks checks
      or ~50 Uptime Kuma monitors
- [ ] Caddy reverse-proxy + TLS if public exposure is required

### Phase C (n8n pipelines — design 2026-05-06, pending implementation)

- [ ] **n8n self-hosted**: workflows orchestrating Sentry + Healthchecks
      + Uptime Kuma + Loki → Notion ticket creation. Loki API consumed
      via `/loki/api/v1/query_range` to scan error windows.
      → **Canonical architecture: [`ARCH_n8n_pipelines.md`](ARCH_n8n_pipelines.md)** (design validated in brainstorming 2026-05-06, V0 = Sentry → Notion INCIDENTS pipeline, V1.5+ = duplication by source — WhatsApp / Discord / Reddit / X / email / filesystem queue / Healthchecks / Uptime Kuma).
      → The n8n container lives here (`infra/itops/docker-compose.yml`), exposed via Tailscale Funnel (host-level), tokens in n8n credentials store.
- [ ] Healthchecks also as watchdog for web services (periodic ping to
      health endpoints)
- [ ] Watchtower metrics exported to Prometheus (if we install
      Prometheus for backend services)
- [ ] Grafana dashboard + alerting (Phase B-bis, to activate when the first dev
      joins the team or when B2B analytics starts — V0/V0.5 n8n
      observability is sufficient with auto-recursive daily digest)

## Runbook

### Start the stack

```bash
cd infra/itops
cp .env.example .env
$EDITOR .env                  # fill in HEALTHCHECKS_SUPERUSER_* + SECRET_KEY
docker compose up -d
docker compose ps             # verify Up + healthy
docker compose logs -f healthchecks   # live logs (Ctrl+C to quit)
open http://localhost:8000    # access the UI, login with SUPERUSER_*
```

### Stop / restart

```bash
docker compose stop           # clean stop
docker compose start          # restart
docker compose down           # stop + remove containers (volume preserved)
docker compose down -v        # ⚠ DESTRUCTIVE — also removes the Healthchecks volume
```

### Manual upgrade (without waiting for the Watchtower cron)

```bash
docker compose pull           # pull latest tags
docker compose up -d          # recreate containers with the new images
docker image prune -f         # post-upgrade cleanup
```

### Check received Healthchecks pings

UI → "Checks" tab → click the relevant check → "Events" tab displays
the last received pings with timestamp and payload (stdout head).

### Logs / debug

```bash
docker compose logs healthchecks --tail=200
docker compose logs watchtower --tail=200
docker exec -it ratis-itops-healthchecks /bin/sh   # shell inside the container
```

### Full reset (Phase A: acceptable, see DA-38)

```bash
docker compose down -v        # ⚠ removes volume + data
rm .env                       # start from a fresh .env if needed
cp .env.example .env && $EDITOR .env
docker compose up -d
```

### Uptime Kuma — common operations (Phase B.1)

```bash
# Isolated service start
docker compose up -d uptime-kuma
docker compose logs -f uptime-kuma --tail=100

# First access (admin wizard) then create monitors
open http://localhost:3001

# Config backup (before an upgrade or rebuild) — copies the SQLite
docker run --rm -v ratis_itops_uptime_kuma_data:/src -v "$PWD":/dst alpine \
  tar czf /dst/uptime_kuma_backup.tar.gz -C /src .

# Manual upgrade (DA-42 — no auto-update)
docker compose pull uptime-kuma
docker compose up -d uptime-kuma
```

### Loki — common operations (Phase B.2)

```bash
# Health probe
curl -fsS http://localhost:3100/ready                  # "ready"
curl -fsS http://localhost:3100/metrics | head -20      # Prometheus metrics

# List currently indexed labels (useful post-up to confirm
# that Promtail is pushing)
curl -sS http://localhost:3100/loki/api/v1/labels

# List values for a given label
curl -sS http://localhost:3100/loki/api/v1/label/service_name/values
curl -sS http://localhost:3100/loki/api/v1/label/compose_project/values

# LogQL query — last 100 logs from ratis_auth
curl -sSG http://localhost:3100/loki/api/v1/query_range \
  --data-urlencode 'query={service_name="ratis_auth"}' \
  --data-urlencode 'limit=100'

# Text-filtered query (e.g. errors only)
curl -sSG http://localhost:3100/loki/api/v1/query_range \
  --data-urlencode 'query={service_name="ratis_auth"} |= "ERROR"' \
  --data-urlencode 'limit=50'

# Reset chunks + index (⚠ destructive)
docker compose down loki
docker volume rm ratis_itops_loki_data
docker compose up -d loki

# Manual upgrade (DA-42)
docker compose pull loki promtail
docker compose up -d loki promtail
```

### Promtail — debug ingestion

```bash
docker compose logs -f promtail --tail=100

# Verify that Promtail sees the containers (Docker SD)
docker exec ratis-itops-promtail \
  wget -qO- http://localhost:9080/targets

# If Promtail sends nothing to Loki:
# 1. Check the network: Promtail must be able to resolve `loki` → 3100
docker exec ratis-itops-promtail \
  wget -qO- http://loki:3100/ready
# 2. Check positions (where Promtail is in each file)
docker exec ratis-itops-promtail cat /tmp/positions.yaml
# 3. Check Promtail logs for relabel or parsing errors
```

### macOS Docker socket — fallback if Promtail is incomplete

On macOS Docker Desktop, the `/var/lib/docker/containers` mount may be
absent or phantom read-only (Docker runs inside a Linux VM).
If Promtail detects containers via `docker_sd_configs` but cannot
read their logs, fallback to the **Loki Docker driver plugin**:

```bash
# Install the Docker plugin
docker plugin install grafana/loki-docker-driver:3.3.0 --alias loki

# Configure a service to push its logs directly to Loki
# (in an external compose, e.g.: docker-compose.yml dev stack)
services:
  ratis_auth:
    logging:
      driver: loki
      options:
        loki-url: "http://localhost:3100/loki/api/v1/push"
        loki-pipeline-stages: |
          - regex:
              expression: '(?P<level>ERROR|WARN|INFO|DEBUG)'
          - labels:
              level:
```

This alternative completely bypasses Promtail for the affected services
— useful if sidecar collection fails. See KP candidate in PR NOTES.

## Things to know (vectorised FAQ)

### Why does itops use Healthchecks and not a managed service (PagerDuty, Better Uptime)?

V0 Ratis is self-hosted; we minimise paid SaaS subscriptions while
budgets remain tight. Self-hosted open-source Healthchecks covers 100%
of our Phase A need (batch job alerts). If we grow (10+ devs,
need for on-call rotation), a managed service will become justified — until
then Healthchecks remains sufficient.

### Why does itops not touch the runners stack (`runner/docker-compose.yml`)?

Watchtower is intentionally opt-in scoped (DA-39). The 16 runners do
NOT have the label, so Watchtower ignores them. No modification to the
runners stack is necessary — that is precisely the benefit of label-based
opt-in.

### How do I add a new service to Watchtower auto-update?

In the compose file of the relevant service, add to the container:

```yaml
labels:
  - "com.centurylinklabs.watchtower.enable=true"
```

On the next cron run (04:00 by default), Watchtower will see the new label
and include the container in its scope.

### Does itops affect Mac mini performance?

Healthchecks idle = ~80 MB RAM, <1% CPU (Django + SQLite). Watchtower idle
= ~20 MB RAM, ~0% CPU (sleeping between cron runs). Image pull on cron run =
a few seconds I/O. Negligible next to the 16 runners + dev stack.

### What happens if I lose the `healthchecks_data` volume?

Phase A: we lose the UI config + ping history. The concrete consequence is
that each check must be recreated in the UI (5 min for 8 batches) and
the batches redeployed with the new ping URLs. No business data lost. This
is precisely why SQLite is sufficient (DA-38).

### Uptime Kuma vs Healthchecks — why both?

Different monitoring needs:
- **Healthchecks** = **passive** ping-based monitoring. Ratis batches ping
  Healthchecks at the end of their run (success/fail). If no ping received
  within the expected window → alert. Ideal for cron jobs.
- **Uptime Kuma** = **active** probe-based monitoring. Uptime Kuma sends an
  HTTP/TCP request every 60s to Ratis services. If no 200 response → alert.
  Ideal for web services that must respond 24/7.
- Both are complementary: a service that crashes without a log error
  (deadlock, OOM segfault) is invisible to Healthchecks
  (no batch run) but detected by Uptime Kuma (probe failed).

### Why does Loki index by labels and not by content (full-text)?

Grafana's architectural choice: **invert the indexing cost**. ELK indexes
every word of the log → huge index, CPU-intensive ingestion, but ultra-fast
queries. Loki indexes only labels (10-20 values per log),
stores compressed chunks without an index → minimal footprint, lightweight
ingestion. At query-time, LogQL filters chunks by labels (strong selectivity)
then applies `|=` `|~` filters in streaming over the content.
Acceptable performance for ad-hoc debug, and **for our case (5-50 MB
logs/day)** latency is sub-second on any query.

### Loki API queryable from n8n — what format?

The Loki REST API is Prometheus-inspired:
- `GET /loki/api/v1/query_range?query=<LogQL>&start=<ns>&end=<ns>&limit=<n>` for a time window
- `GET /loki/api/v1/query?query=<LogQL>` for instant
- `GET /loki/api/v1/labels` for discovery
- `GET /loki/api/v1/label/<name>/values` for label values

Prometheus-typed JSON response (status / data / result). In Phase C, n8n
will: (1) `query_range {service=~".+"} |= "ERROR" | last 5m`, (2) parse
the returned streams, (3) group by `service`, (4) create a Notion
ticket if N occurrences > threshold. Workflow ~50 JSON lines in n8n.

### What happens if I lose the `loki_data` volume?

We lose the last 14 days of logs (per DA-41). Services
continue logging to Docker stdout/stderr (independent retention
managed by Docker); Promtail resumes pushing as soon as Loki is
back up. No business impact, just a debug gap if investigating an
incident prior to the crash. For long-term recovery, we will activate
an S3-compatible export in Phase C if needed (out of scope V1).

### Why not structured JSON logging in services right now?

Ratis services log in plain text via stdlib `logging` (`logger.warning(...)`
formatted with `%s` placeholders). Migrating to `JSONFormatter` or
`structlog` is a cross-service change that impacts (a) the format
expected by tests that assert `caplog`, (b) dev-local log readability,
(c) batch outputs that are sometimes captured as-is. Doing it correctly
= dedicated ARCH + separate PR. Not a Phase B.2 blocker — Loki still
indexes raw logs, we query via `|= "user_id=123"` in the meantime. Phase C
n8n can tolerate that too via LogQL regexes.

## Glossary

- **DA-XX**: numbered architecture decision (see dedicated section).
- **Healthchecks**: `https://healthchecks.io` — open-source service for
  HTTP ping-based cron job monitoring. Official Docker image
  `healthchecks/healthchecks`.
- **Watchtower**: `https://containrrr.dev/watchtower` — Docker daemon for
  opt-in/opt-out container auto-update. Official image
  `containrrr/watchtower`.
- **Phase A / Phase B / Phase C**: temporal breakdown of ITOps features.
  Phase A = what ships in this PR. Phase B = notification/SMTP/Tailscale
  enhancements. Phase C = web service monitoring.
- **Mac mini**: current dev-host (M4 Pro, macOS), since PR #287
  (2026-05-04). Replaces the pre-migration Windows machine. Also hosts
  the 16 self-hosted GH Actions runners.
- **Opt-in label**: for Watchtower,
  `com.centurylinklabs.watchtower.enable=true` placed on a container =
  explicit signal "you can auto-update me". Without the label, Watchtower
  ignores the container.
- **Uptime Kuma**: `https://uptime.kuma.pet` — open-source uptime monitoring
  service via HTTP/TCP/keyword/JSON probes, dashboard and multi-channel
  notifications. Docker image `louislam/uptime-kuma`.
- **Loki**: `https://grafana.com/oss/loki/` — open-source log aggregator
  by Grafana Labs, label-based indexing, REST LogQL API. Image
  `grafana/loki`. Apache 2.0.
- **Promtail**: `https://grafana.com/docs/loki/latest/clients/promtail/` —
  push-only agent by Grafana Labs that collects logs (file tailing,
  Docker socket, journald, syslog, …) and pushes them to Loki. Image
  `grafana/promtail`. Apache 2.0.
- **LogQL**: `https://grafana.com/docs/loki/latest/query/` — Loki query
  language, Prometheus-like syntax with `|=`, `|~`, `|!`,
  `|json` filters, `rate()`, `count_over_time()` aggregations.
