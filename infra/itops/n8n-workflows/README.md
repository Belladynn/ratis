# n8n workflows — Ratis ITOps cookbook

Workflow JSONs for the n8n self-hosted instance running in `infra/itops/docker-compose.yml` (Phase C). Source of truth lives in this directory — re-export from the n8n UI after any UI edit, then commit.

> **Design / topology / décisions d'architecture** : voir [`ARCH_n8n_pipelines.md`](../../../ARCH_n8n_pipelines.md). Ce fichier est le **cookbook ops** — imports, smoke, runbook live.

## Files

| File | Purpose |
|---|---|
| `batch-sentinel.json` | Receives batch-outcome webhook from GH Actions composite action `report-batch-outcome`, verifies HMAC + timestamp, validates schema, posts a Sentry-formatted event to GlitchTip ingest on failure (fingerprint `batch:<workflow_name>` → idempotent create / auto-reopen / occurrence increment handled GlitchTip-side). Falls back to `/quarantine` filesystem if GlitchTip is unreachable, alerts Discord on failure, aggregates a 24h digest daily at 09:05. |
| `github-pr-merged-closer.json` | Receives GitHub PR-merged webhook, verifies HMAC, parses PR body for `glitchtip-issue:<id>` references and resolves the matching GlitchTip issues via `PATCH /api/0/issues/<id>/`. |

> **GlitchTip self-hosted as incident sink** — depuis le sunset Notion, les
> tickets d'incident ne vivent plus dans une base Notion mais dans GlitchTip
> (`http://localhost:8000/` côté Mac mini, Sentry-API compatible). Trois
> projets sont provisionnés : `ratis-mobile`, `ratis-backend`, `n8n-workflows`.
> Les SDK Sentry des services Ratis pointent directement vers le DSN du projet
> correspondant — pas de relais n8n nécessaire (raison pour laquelle l'ancien
> `sentry-ingest.json` a disparu). Les workflows n8n ci-dessus n'utilisent
> GlitchTip que pour leurs propres flux (batchs et clôture PR).

## First-time bootstrap

```bash
# 1. Génère les secrets et colle-les dans infra/itops/.env
echo "N8N_ENCRYPTION_KEY=$(openssl rand -hex 32)"
echo "N8N_BASIC_AUTH_PASSWORD=$(openssl rand -hex 16)"
echo "N8N_GITHUB_WEBHOOK_SECRET=$(openssl rand -hex 32)"
echo "N8N_BATCH_SENTINEL_WEBHOOK_SECRET=$(openssl rand -hex 32)"
# Récupère les secrets GlitchTip depuis le Keychain
echo "GLITCHTIP_API_TOKEN=$(security find-generic-password -s ratis-agent-mcp -a admin-glitchtip -w)"
echo "GLITCHTIP_DSN_N8N_WORKFLOWS=$(security find-generic-password -s ratis-agent-mcp -a ops-glitchtip-dsn-n8n-workflows -w)"

# 2. Tailscale FQDN (= valeur de N8N_HOST à coller dans .env)
tailscale status | head -1   # colonne 2

# 3. Boot le container
cd /Users/guillaume/Cursor/Ratis/infra/itops
docker compose up -d n8n

# 4. Tailscale Funnel persistent (host-level, pas dans le container)
tailscale funnel --bg 5678

# 5. Smoke ingress
curl -fsS https://<host>.<tailnet>.ts.net/healthz   # → 200 OK
```

## Import procedure — UI path (clickable)

1. Open n8n UI: `https://<host>.<tailnet>.ts.net/`.
2. Go to **Workflows** → top-right `⋮` menu → **Import from File** → select the JSON.
3. Credential references — les workflows actuels (`batch-sentinel`,
   `github-pr-merged-closer`) n'utilisent **plus de credentials n8n stockés**.
   GlitchTip est consommé via env vars (`GLITCHTIP_API_TOKEN` +
   `GLITCHTIP_DSN_N8N_WORKFLOWS`) injectées dans les nœuds HTTP générique.
   Aucune action requise au moment de l'import.
4. Save the workflow (Ctrl+S).
5. Activate via the toggle top-right of the workflow editor.

## Import procedure — CLI path (recommended for batch)

The docker-compose stack bind-mounts this directory read-only at `/home/node/.n8n/workflows-imports/` inside the n8n container, so `n8n` CLI can import directly from the host filesystem after `git pull`. **Operator runs from the Mac mini host** :

```bash
# 1. Pull the repo so the JSON files are on disk
cd /Users/guillaume/Cursor/Ratis && git pull --ff-only

# 2. Bulk-import all workflows in this directory (one file = one workflow)
docker exec ratis-itops-n8n n8n import:workflow --separate \
  --input=/home/node/.n8n/workflows-imports/

# 3. List workflows to retrieve the IDs n8n auto-assigned
docker exec ratis-itops-n8n n8n list:workflow
# Output looks like :
# ID|Name
# 1|batch-sentinel
# 2|github-pr-merged-closer
# 3|daily-digest
# (db-snapshot, db-write-pipeline également selon l'état du worktree)

# 4. Activate each (substitute IDs from step 3)
docker exec ratis-itops-n8n n8n update:workflow --id=1 --active=true
docker exec ratis-itops-n8n n8n update:workflow --id=2 --active=true
```

**Credential auto-linking** — sans objet pour les workflows post-Notion. Les
secrets (token API GlitchTip + DSN) sont injectés via `infra/itops/.env` et
visibles au runtime via `$env.GLITCHTIP_API_TOKEN` / `$env.GLITCHTIP_DSN_N8N_WORKFLOWS`.

**Re-import after edit** — `n8n import:workflow` is idempotent on the workflow ID. If you re-import a JSON whose internal `id` matches an existing workflow, n8n updates in place ; otherwise it creates a new one. Safer pattern : delete the local workflow first via `n8n delete:workflow --id=N`, then re-import.

## Smoke testing

Les workflows actifs exposent leurs webhooks via Tailscale Funnel.

```bash
export N8N_HOST="<your-mac-mini>.<tailnet>.ts.net"

# batch-sentinel (POST /webhook/batch-outcome, signé HMAC + X-Timestamp)
export N8N_BATCH_SENTINEL_WEBHOOK_SECRET=$(grep N8N_BATCH_SENTINEL_WEBHOOK_SECRET infra/itops/.env | cut -d= -f2)
# (script à fournir ou rejouer payload depuis tools/n8n/sample-payloads/)

# github-pr-merged-closer
export N8N_GITHUB_WEBHOOK_SECRET=$(grep N8N_GITHUB_WEBHOOK_SECRET infra/itops/.env | cut -d= -f2)
bash tools/n8n/scripts/post-github-test.sh tools/n8n/sample-payloads/github-pr-merged.json
```

Pour valider l'ingest GlitchTip côté `batch-sentinel`, ouvrir
`http://localhost:8000/ratis/issues/?project=<n8n-workflows-project-id>` après
un payload de test : l'issue avec fingerprint `batch:<workflow_name>` doit
apparaître (ou voir son occurrence count incrémentée si déjà existante).

## daily-digest — runbook

Workflow de surveillance : poste chaque matin à 09:00 (Europe/Paris) un résumé de
santé de la pipeline n8n sur Discord (exécutions, échecs, latence). Cf. spec
`docs/superpowers/specs/2026-05-17-n8n-daily-digest-design.md`.

### Prérequis (une fois)

1. **Clé API n8n** — UI n8n → Settings → n8n API → Create an API key.
   Coller dans `infra/itops/.env` → `N8N_API_KEY`.
2. **Webhook Discord** — salon cible → Paramètres → Intégrations → Webhooks →
   Nouveau webhook. Coller l'URL dans `infra/itops/.env` →
   `N8N_DISCORD_DIGEST_WEBHOOK_URL`.
3. Redémarrer n8n pour charger les env vars : `docker compose up -d n8n`.

### Import + activation

```bash
docker cp daily-digest.json ratis-itops-n8n:/tmp/daily-digest.json
docker exec ratis-itops-n8n n8n import:workflow --input=/tmp/daily-digest.json
docker exec ratis-itops-n8n n8n list:workflow | grep daily-digest   # récupère l'ID
docker exec ratis-itops-n8n n8n update:workflow --id=<ID> --active=true
```

### Smoke test

UI n8n → workflow `daily-digest` → bouton **Test workflow**. Vérifier que le
message arrive sur Discord et que les chiffres recoupent l'onglet Executions.

### Panne

- **Pas de message un matin** → n8n est probablement down. Vérifier
  `docker ps --filter name=n8n`.
- **Message `⚠️ API n8n injoignable`** → la clé `N8N_API_KEY` est invalide ou
  expirée, ou l'API n8n ne répond pas. Régénérer la clé dans l'UI et mettre
  `infra/itops/.env` à jour.

## db-sandbox — runbook

Scripts : `scripts/db-sandbox/`. La sandbox éphémère de dry-run (SP3 du pipeline
d'approbation des écritures, cf. `docs/superpowers/specs/2026-05-18-db-sandbox-sp3-design.md`).

- `snapshot.sh` — dump prod → Mac mini (`~/.local/share/ratis/db-sandbox/snapshots/`,
  `chmod 700`), rétention 24 h (M6 quick win — RGPD-friendly, audit 2026-05-19).
  Tunable via `SNAPSHOT_MAX_AGE_MINUTES`.
- `sandbox-up.sh` — monte un container `postgres:16` neuf restauré depuis le dernier snapshot,
  sur un réseau Docker isolé dédié (`ratis_sandbox_isolated_<id>`, M6 quick win — pas de port
  mapping, joignable uniquement via `docker exec` depuis le host) ; imprime
  `{sandbox_id, container}`. Cap : 3 sandboxes concurrentes.
- `sandbox-down.sh <id>` — détruit container + réseau associé (idempotent).
- `sandbox-reap.sh` — purge les sandboxes de plus de 2 h + réseaux isolés orphelins (anti-fuite).

Workflow `db-snapshot.json` : cron 03:00 → snapshot, cron horaire → reap.
Les nœuds Execute Command invoquent les scripts via `ssh mac-mini-host` — vérifier
que l'alias SSH `mac-mini-host` est configuré côté container n8n avant activation.

## db-write-pipeline — runbook

Pipeline d'approbation des écritures DB (SP4 de la V1 du module `db`). L'agent
soumet via l'outil agent-mcp `db_propose_write` → webhook signé HMAC → dry-run
sur la sandbox (SP3) → invariants → routage tables-argent → hooks LLM/approbation
→ exécution prod.

**État** : backbone + revue LLM (SP5). La revue LLM est une vraie revue en
2 passes (intention + cas-magique) appelant l'API Anthropic Messages via le
nœud HTTP Request ; un verdict `not_ok` rejette la proposition et renvoie un
feedback structuré à l'agent. Le gate approbation (SP6) reste un stub
passthrough. L'exécution prod est **feature-flaggée OFF** (`EXECUTE_ENABLED`
dans le Code node Execute). Le workflow reste `active: false` jusqu'à V1 complet.

Prérequis : `N8N_DB_PIPELINE_WEBHOOK_SECRET` + `ANTHROPIC_API_KEY` dans
`infra/itops/.env` ; les scripts SP3 `scripts/db-sandbox/` ; l'alias SSH
`mac-mini-host` côté container n8n.

## Re-export reminder

After editing a workflow in the n8n UI, **re-download** the JSON via **Workflows** → workflow → `⋮` menu → **Download**, then overwrite the matching file in this directory and commit. The git copy is the source of truth — UI-only changes will be lost on container restart if the encryption key rotates or the volume is rebuilt.

## Hardcoded constants

- GlitchTip endpoint : `http://glitchtip-web:8000` (réseau Docker interne,
  utilisé par `github-pr-merged-closer` → `PATCH /api/0/issues/<id>/`). À
  exposer côté n8n container via le service alias `glitchtip-web` du
  `docker-compose.yml` de la stack itops.
- GitHub repo : `Belladynn/ratis` — référencé dans des scripts de smoke
  (`tools/n8n/scripts/post-github-test.sh`), pas dans les workflows actifs.

## Live operations

- **n8n UI** : `https://<host>.<tailnet>.ts.net/` → onglet `Executions` pour l'historique des runs (status, duration, payload, errors).
- **Logs container** via Docker :
  ```bash
  docker compose -f infra/itops/docker-compose.yml logs -f n8n
  ```
- **Logs container** via Loki (déjà ingéré via Promtail) :
  ```bash
  curl -sSG 'http://localhost:3100/loki/api/v1/query_range' \
    --data-urlencode 'query={service_name="n8n"}' --data-urlencode 'limit=200'
  ```
- **Quarantine review** (tickets en attente quand l'ingest GlitchTip a échoué) :
  ```bash
  ls -la "$RATIS_QUARANTINE_DIR"
  # Default Mac mini : ~/.local/share/ratis/tickets-quarantine/
  cat ~/.local/share/ratis/tickets-quarantine/*.md   # review chaque
  # Quand traité : mv vers tickets-processed/ ou rm si junk
  ```
- **Replay un batch outcome manuellement (debug)** : reposter un payload depuis
  `tools/n8n/sample-payloads/` vers `https://<host>.<tailnet>.ts.net/webhook/batch-outcome`
  avec headers `X-Signature-256` (HMAC-SHA256 du body avec `N8N_BATCH_SENTINEL_WEBHOOK_SECRET`)
  et `X-Timestamp` (epoch seconds courant).
- **Inspecter les issues GlitchTip** : UI `http://localhost:8000/ratis/issues/`
  (filtre par projet `n8n-workflows`). API : `curl -H "Authorization: Bearer $GLITCHTIP_API_TOKEN" http://localhost:8000/api/0/projects/ratis/n8n-workflows/issues/`.

## Runbook — `db-write-pipeline` (gate d'approbation, SP6)

Le workflow `db-write-pipeline` reçoit les propositions d'écriture DB
signées HMAC émises par l'outil agent-mcp `db_propose_write`. Depuis SP6,
l'étape d'approbation est un **vrai gate humain** (le stub `Approval` a
disparu).

**Flux** : webhook → HMAC → routage break-glass → sandbox up → dry-run +
invariants → scan tables-argent → sandbox down → revue LLM 2 passes →
`Register approval` (POST `PA_ADMIN_BASE_URL/api/v1/admin/db-approvals`)
→ `Notify Discord` → `Wait` (reprise webhook, timeout **24 h**) →
décision.

**Décider une proposition** : ouvrir `/admin/ui/db-approvals` (UI admin
PA), examiner le contexte support + dry-run + verdict LLM, approuver ou
rejeter. Une écriture tables-argent (badge 🔴) exige de retaper le nom
de la procédure. Sans décision sous 24 h, la proposition passe `expired`.

**Prérequis env** (service n8n, cf `.env.example` / `docker-compose.yml`) :
- `PA_ADMIN_BASE_URL` — base URL du service PA appelé par n8n.
- `ADMIN_API_KEY` — Bearer pour l'endpoint d'enregistrement ; doit être
  identique à l'`ADMIN_API_KEY` du service PA.

**Exécution prod** : reste feature-flaggée **OFF** (`EXECUTE_ENABLED`
dans le nœud `Execute`). SP6 ne flippe pas le switch.

## Tailscale Funnel — kill switch

```bash
# Coupe IMMÉDIATEMENT l'ingress public ; n8n reste accessible sur localhost:5678
tailscale funnel reset
```

## Rotation des webhook secrets

```bash
# 1. Générer nouveau secret
openssl rand -hex 32

# 2. Update infra/itops/.env (N8N_GITHUB_WEBHOOK_SECRET ou N8N_BATCH_SENTINEL_WEBHOOK_SECRET)
$EDITOR /Users/guillaume/Cursor/Ratis/infra/itops/.env

# 3. Restart pour picker la nouvelle env
cd /Users/guillaume/Cursor/Ratis/infra/itops
docker compose up -d n8n

# 4. Update côté provider :
#    - GitHub : repo/org Settings → Webhooks → set new secret
#    - Batch sentinel : composite action `report-batch-outcome` (côté GH Actions
#      org secret RATIS_BATCH_SENTINEL_WEBHOOK_SECRET → idem env n8n)
# 5. Smoke test : trigger un test event provider, verify HMAC passe
```
