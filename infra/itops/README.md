# infra/itops — self-hosted ops stack

Self-hosted ops stack running on the Mac mini host alongside the dev stack
and the GH Actions runners.

- **Phase A** — **Healthchecks** (batch monitoring) + **Watchtower**
  (label-scoped auto-update).
- **Phase B.1** — **Uptime Kuma** (HTTP/TCP probes + dashboard + alerting).
- **Phase B.2** — **Loki** + **Promtail** (log aggregation queryable via REST).

## Quick start

```bash
cp .env.example .env
$EDITOR .env                  # set HEALTHCHECKS_SUPERUSER_*, SECRET_KEY
docker compose up -d
docker compose ps             # all services should be Up / healthy

open http://localhost:8000    # Healthchecks UI (login page)
open http://localhost:3001    # Uptime Kuma UI (first run = create admin)
curl -fsS http://localhost:3100/ready  # Loki readiness probe
```

To bring up only the Phase B subset :

```bash
docker compose up -d uptime-kuma loki promtail
```

## Ports

- `8000` — Healthchecks UI + ping endpoint (override via `HEALTHCHECKS_PORT`).
- `3001` — Uptime Kuma UI (override via `UPTIME_KUMA_PORT`).
- `3100` — Loki HTTP API, **bound on `127.0.0.1` only** (override via
  `LOKI_PORT`). Promtail joint Loki via le réseau Docker `itops_net` ;
  consommateurs externes (n8n Phase C) doivent tourner sur le même host.
- Promtail n'expose aucun port — agent push-only.

## Premier setup Uptime Kuma

1. Ouvre http://localhost:3001 → wizard de création admin (1 user max).
2. Add new monitor pour chaque service Ratis (recommandé) :
   - `ratis_auth` — HTTP GET `http://host.docker.internal:8001/health` — interval 60s
   - `ratis_product_analyser` — HTTP GET `http://host.docker.internal:8003/health`
   - `ratis_list_optimiser` — HTTP GET `http://host.docker.internal:8002/health`
   - `ratis_rewards` — HTTP GET `http://host.docker.internal:8004/health`
   - `ratis_notifier` — HTTP GET `http://host.docker.internal:8005/health`
   - `osrm` — TCP `host.docker.internal:5000`
   - `Healthchecks self` — HTTP GET `http://healthchecks:8000/accounts/login/` (in-cluster)
3. Settings → Notifications → ajouter un canal (Discord / Slack / email SMTP).
4. Attach le canal aux monitors.

## Loki — query API

Loki expose une API REST inspirée de Prometheus, queryable via LogQL :

```bash
# Liste les labels indexés actuellement
curl -sS 'http://localhost:3100/loki/api/v1/labels'

# Liste les valeurs pour un label donné
curl -sS 'http://localhost:3100/loki/api/v1/label/service_name/values'

# Query : tous les logs du service ratis_auth, dernières 5min
curl -sSG 'http://localhost:3100/loki/api/v1/query_range' \
  --data-urlencode 'query={service_name="ratis_auth"}' \
  --data-urlencode 'limit=50'

# Filtrage texte : seulement les logs contenant "ERROR"
curl -sSG 'http://localhost:3100/loki/api/v1/query_range' \
  --data-urlencode 'query={service_name="ratis_auth"} |= "ERROR"'
```

Doc LogQL complète : <https://grafana.com/docs/loki/latest/query/>.

## MCP audit log (DA-48 — `agent-mcp` tool dispatches)

`agent-mcp` writes one JSONL line per tool dispatch to
`~/.local/state/ratis-agent-mcp/audit.log` on the host (XDG state dir,
perms 600). Promtail tails this file via a bind-mount and ships every
line to Loki under `job=mcp-audit`, with a small bounded label set.

### Labels (cardinality-safe)

Promoted to Loki labels (bounded enums, safe to index) :

- `job=mcp-audit` — fixed identifier for this stream.
- `service_name=ratis-agent-mcp` — same convention as Docker logs.
- `host=mac-mini` — origin host (override label in `promtail-config.yml`
  if you fan out to multiple agent-mcp hosts later).
- `caller` — `admin` or `ops` (from the audit JSON).
- `tool` — registered tool name (~25 today, capped by registry).
- `status` — closed enum (`ok`, `forbidden_tool`, `keychain_miss`,
  `provider_error`, `audit_error`, `tool_not_registered`,
  `token_rotated`, `live_mode_used`).

NOT promoted (would explode label cardinality, kept in the raw log
body — query via `| json` LogQL filters when needed) :

- `args_redacted` (per-call dict).
- `error` (free-form provider error text).
- `latency_ms`, `ts` (continuous values).

### LogQL queries (operator cheat-sheet)

```bash
# All MCP audit lines (last 1h) :
curl -sSG 'http://localhost:3100/loki/api/v1/query_range' \
  --data-urlencode 'query={job="mcp-audit"}' \
  --data-urlencode 'limit=200'

# Admin-token calls only (high-stakes scope) :
curl -sSG 'http://localhost:3100/loki/api/v1/query_range' \
  --data-urlencode 'query={job="mcp-audit",caller="admin"}'

# Failures only (any status that is not "ok") :
curl -sSG 'http://localhost:3100/loki/api/v1/query_range' \
  --data-urlencode 'query={job="mcp-audit",status!="ok"}'

# Single high-stakes operation (EAS prod publish) :
curl -sSG 'http://localhost:3100/loki/api/v1/query_range' \
  --data-urlencode 'query={job="mcp-audit",tool="eas_update_production"}'

# Drill into raw fields for failures (extracts JSON for filtering) :
curl -sSG 'http://localhost:3100/loki/api/v1/query_range' \
  --data-urlencode 'query={job="mcp-audit",status!="ok"} | json'
```

### Volume mount on non-Mac-mini hosts

Promtail expects the host audit directory at the path declared in
`MCP_AUDIT_LOG_DIR` (defaults to the Mac mini layout for user
`guillaume`). On a different host, set it explicitly in your `.env` :

```bash
# Linux dev box / alt user :
MCP_AUDIT_LOG_DIR=$HOME/.local/state/ratis-agent-mcp
```

The bind-mount is read-only (`:ro`) — Promtail cannot mutate the file.
If the directory or `audit.log` file does not exist yet (MCP has never
been invoked), Promtail's `static_configs` tails gracefully and starts
shipping lines as soon as the file appears — no crash, no need to
pre-create.

macOS Docker Desktop auto-shares `/Users/*`, so the default mount path
works out of the box. If you have moved your XDG state dir elsewhere
(rare), make sure the new path is declared in Docker Desktop's
"Resources → File sharing" allow-list.

## n8n — incident orchestration pipelines (Phase C)

n8n self-hosted (container dans ce stack) héberge les workflows qui ingèrent des incidents (V0 = Sentry + GitHub PR-merged) vers Notion DB INCIDENTS.

- **Design + topology + sécurité** : voir [`ARCH_n8n_pipelines.md`](../../ARCH_n8n_pipelines.md)
- **Cookbook opérationnel** (imports CLI/UI, smoke testing, credentials, versioning) : voir [`n8n-workflows/README.md`](./n8n-workflows/README.md)
- **Setup secrets first-time** : `openssl rand -hex 32` pour `N8N_ENCRYPTION_KEY`, `N8N_BASIC_AUTH_PASSWORD`, `N8N_SENTRY_WEBHOOK_SECRET`, `N8N_GITHUB_WEBHOOK_SECRET` ; `tailscale funnel --bg 5678` pour exposer Funnel

## What this stack does NOT include (still Phase B+ / future)

- Tailscale (installed on host via `brew install --cask tailscale`).
- SMTP for Healthchecks alert emails.
- Slack/Discord notifications for Watchtower.
- Grafana UI sur Loki (Phase B.3 si besoin de dashboards visuels).
- Structured logging JSON dans les services Ratis (pré-requis pour query
  Loki par `user_id` natif — follow-up séparé).
- TLS / public hostname (Caddy reverse-proxy).

Full design, decisions, and runbook → [`ARCH_itops.md`](../../ARCH_itops.md).
