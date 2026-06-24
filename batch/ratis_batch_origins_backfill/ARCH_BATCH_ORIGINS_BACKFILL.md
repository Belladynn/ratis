---
type: batch-global
service: ratis_batch_origins_backfill
status: one-shot
parent: ARCH_RATIS
sub_archs: []
related: [ARCH_BATCH_OFF_SYNC, ARCH_missions, ARCH_PRODUCT_ANALYSER]
tech: [Python, httpx, SQLAlchemy, Postgres]
tables: [products, batch_sync_log]
env_vars: [DATABASE_URL, OFF_API_BASE_URL, OFF_USER_AGENT, SENTRY_DSN]
tags: [batch, backfill, off, origins_tags, missions, phase-c2]
business_domain: missions
rgpd_concern: false
updated: 2026-05-11
---

# ratis_batch_origins_backfill — one-shot ETL `products.origins_tags`

> Batch CLI **one-shot** Phase C-2 sprint missions : parcourt `products WHERE origins_tags IS NULL`, fetch via OFF API `GET /api/v2/product/{ean}?fields=origins_tags`, UPDATE la row. Déverrouille les missions `product_identification + attribute:french` à ≥80 % couverture.
> @tags: batch backfill off origins_tags missions french phase-c2 one-shot products idempotent resumable
> @status: LIVRÉ V0
> @subs: auto

> [[ARCH_RATIS]] · relations : [[ARCH_BATCH_OFF_SYNC]], [[ARCH_missions]], [[ARCH_PRODUCT_ANALYSER]]

## Index

- [Résumé en une phrase](#résumé-en-une-phrase)
- [Responsabilité](#responsabilité)
- [Fréquence d'exécution](#fréquence-dexécution)
- [Tables lues / écrites](#tables-lues--écrites)
- [Dépendances internes (autres services/libs ratis)](#dépendances-internes-autres-serviceslibs-ratis)
- [Dépendances externes (tiers)](#dépendances-externes-tiers)
- [Décisions d'architecture clés](#décisions-darchitecture-clés)
- [Flow principal](#flow-principal)
- [Paramètres](#paramètres)
- [Monitoring / logs](#monitoring--logs)
- [Runbook](#runbook)
- [Glossaire](#glossaire)

---

## Résumé en une phrase

ratis_batch_origins_backfill est un batch CLI **one-shot** qui parcourt les products en base avec `origins_tags IS NULL`, fetch le tableau OFF correspondant via `GET /api/v2/product/{ean}?fields=origins_tags`, et UPDATE la row. Phase C-2 du sprint missions — déverrouille les 3 missions `product_identification + attribute:french` après que la couverture en prod atteint ≥80%.

## Responsabilité

- Page (LIMIT 500 par défaut) sur `products WHERE origins_tags IS NULL [AND source='off']`
- Pour chaque EAN, appel HTTP OFF avec User-Agent ratis identifiant
- UPDATE la row avec le tableau (ou `[]` pour les EANs introuvables côté OFF / sans metadata d'origine)
- Rate-limit configurable entre appels (default 1 req/s, conforme guideline OFF)
- Idempotent : re-runs skipent les rows déjà fillées (`origins_tags IS NOT NULL`)
- Resumable : crash en cours de page → les rows non-écrites restent NULL → éligibles au prochain run
- Audit dans `batch_sync_log (batch_name='ratis_batch_origins_backfill')`

## Fréquence d'exécution

**One-shot**, déclenché manuellement via `workflow_dispatch` sur GitHub Actions (cf workflow `batch_origins_backfill.yml`).

Pas de cron : après la migration `20260511_2400_phase_c2_origins_tags` qui ajoute la colonne, l'op lance le batch en prod **une fois** (potentiellement multi-jours selon le volume + rate-limit OFF). Le forward path est déjà couvert par `ratis_batch_off_sync` (chaque run delta nightly écrit `origins_tags` sur toute row touchée via la clause EXCLUDED de `_SYNC_COLS`).

## Tables lues / écrites

| Table | R/W | Description |
|---|---|---|
| `products` | R | SELECT EANs avec `origins_tags IS NULL` (filtre optionnel `source='off'`) |
| `products` | W | UPDATE `origins_tags = :tags` par EAN |
| `batch_sync_log` | W | Audit succès/échec + `rows_affected` (compte updated + empty + not_found) |

## Dépendances internes (autres services/libs ratis)

- `ratis_core.database.make_engine` — connection pooling
- `ratis_core.observability.init_sentry` — Sentry (no-op silent si DSN vide)

## Dépendances externes (tiers)

- **OFF API** (`https://world.openfoodfacts.org/api/v2/product/{ean}?fields=origins_tags`) — single-product lookup, GET, JSON response. Honor `Retry-After` sur 429 (via tenacity).
- **httpx** ≥0.27 — client HTTP synchrone (single-threaded, simple).
- **tenacity** ≥8.0 — retry 3× sur 429/5xx/transport-error.

## Décisions d'architecture clés

### DA-01 : Single-product API plutôt que multi-code search

L'API OFF Search supporte `cgi/search.pl?code=A|B|C` pour batcher des lookups. On utilise quand même `/api/v2/product/{ean}` un-par-un parce que :
- **404 explicite** : status=0 vs status=1 sans ambiguité ; le multi-code ne dit pas quel EAN est manquant.
- **URL-length** : > ~100 EANs combinés ferait sauter le query-string limit côté Cloudflare devant OFF.
- **Rate-limit** : la latence est dominée par le sleep entre requêtes (1s default), pas par le throughput → batcher ne gagne rien.

### DA-02 : `origins_tags = []` sentinel pour les EANs introuvables

Si OFF renvoie status=0 (EAN inconnu côté OFF) ou status=1 mais `origins_tags=[]`, on écrit quand même `[]` (PG empty array, NOT NULL). Raison : sans ça, les EANs introuvables resteraient NULL et seraient re-tentés à chaque run — infini loop coûteux.

Conséquence : `is_french_product(origins_tags)` traite `[]` comme "pas de signal" → False → pas d'emit `attribute:french`. C'est le comportement souhaité.

### DA-03 : Per-run in-memory exclusion list pour les erreurs réseau

Si une erreur réseau persiste sur un EAN (5xx après 3 retries, timeout), on l'ajoute à un set in-memory et on saute les SELECT suivants. Le run continue sur les autres EANs. Au run suivant, le set est vide → l'EAN sera re-tenté.

Sans cette mécanique, un EAN bloqué côté OFF tournerait en boucle (`IS NULL` re-le-sélectionne à chaque page).

### DA-04 : Commit per-page (pas per-row)

Une seule transaction par page (500 rows) — minimise overhead PG + reste compatible avec le pattern de test SAVEPOINT-isolé (cf `tests/conftest.py`). Sur crash mid-page, les rows non-flushées restent NULL → ré-éligibles. Pas de perte de progression au-delà de la page courante.

### DA-05 : Default `only_off_source=True`

Les rows `source IN ('internal', 'obp', 'opf', 'opff')` n'ont pas d'entrée OFF correspondante. Les inclure générerait 100% de 404 inutiles. Operator escape hatch : `--all-sources` pour bypass (utilisé seulement si la décision produit a évolué).

### DA-06 : Pas de mission flip dans cette PR

La PR Phase C-2 livre la column + l'extractor + ce batch + l'helper PA + le dual-emit. Les 3 missions `product_identification + attribute:french` restent `is_active=false`. Le flip est manuel (one-row migration ou SQL admin), exécuté **après** que ce batch a tourné en prod et atteint ≥80% de couverture. Cf [PROD_CHECKLIST.md § Missions Phase C-2](../../docs/ops/PROD_CHECKLIST.md).

Rationale : enable les missions avant que la donnée soit là provoquerait des "tu n'as scanné aucun produit français depuis 7 jours" alors que la column est vide partout — UX bug.

## Flow principal

```
init Sentry
require DATABASE_URL
open httpx.Client + SQLAlchemy engine
while True :
    open session
    SELECT eans (IS NULL, optional source filter, optional exclude list, LIMIT page_size)
    if empty -> break
    for ean in eans :
        try fetch_origins_tags(client, base_url, ean)
        except -> errors += 1, add ean to excluded set, continue
        if not_found  -> UPDATE origins_tags = []  (stats.not_found += 1)
        elif tags == []-> UPDATE origins_tags = [] (stats.empty_origins += 1)
        else          -> UPDATE origins_tags = tags (stats.updated += 1)
        sleep(request_delay_sec)
    db.commit()  (whole page)
write batch_sync_log (success | failed)
close engine
exit code 0|1|2
```

## Paramètres

CLI args (cf `origins_backfill/main.py`) :

| Flag | Default | Description |
|---|---|---|
| `--page-size` | 500 | EANs lus par SELECT |
| `--request-delay-sec` | 1.0 | Sleep entre appels OFF (rate-limit) |
| `--max-eans` | (none) | Cap pour smoke runs |
| `--all-sources` | False | Bypass le filtre `source='off'` |
| `--dry-run` | False | Fetch sans persister (DB SELECTs restent réels) |

Env vars :

| Var | Required | Description |
|---|---|---|
| `DATABASE_URL` | yes | postgresql+psycopg:// |
| `OFF_API_BASE_URL` | no | Default `https://world.openfoodfacts.org` |
| `OFF_USER_AGENT` | no | Default `Ratis-origins-backfill/1.0 (contact: ...)` |
| `SENTRY_DSN` | no | Silent no-op si vide |

## Monitoring / logs

- INFO `origins_backfill: page %d — %d EANs (scanned so far: %d)` à chaque nouvelle page
- INFO `origins_backfill: progress — scanned=… updated=… not_found=… empty=… errors=…` toutes les 1000 rows
- WARNING `origins_backfill: fetch failed for ean=… — leaving NULL (%s)` sur chaque erreur réseau
- INFO `origins_backfill END — stats=…` à la sortie (dict avec compteurs + elapsed_seconds)
- Sentry capture les exceptions non-trappées (fatale → exit code 2)
- `batch_sync_log` row finale : `status='success'|'failed'`, `rows_affected = updated + empty + not_found`

## Runbook

Exécution prod via GitHub Actions `workflow_dispatch` :

1. Aller sur **Actions** → **batch-origins-backfill** → **Run workflow**
2. **Étape 1** — dry-run + max_eans pour valider la network path :
   - `dry_run: true`, `max_eans: 100`, `request_delay_sec: 1.0` → vérifier logs OK
3. **Étape 2** — first real run (capped pour mesurer le throughput) :
   - `dry_run: false`, `max_eans: 5000`, `request_delay_sec: 1.0` → ~1h30 → relever ratio updated/empty/not_found
4. **Étape 3** — full run :
   - `dry_run: false`, `max_eans: (vide)`, `request_delay_sec: 1.0` → laisser tourner ~1-3 jours selon le volume
5. **Pendant le run** — surveiller via Sentry + `SELECT COUNT(*) FROM products WHERE origins_tags IS NULL` régulier
6. **Quand `COUNT(*) WHERE origins_tags IS NOT NULL` ≥ 80% des active products** :
   - Lancer la one-row migration de mission flip (cf PROD_CHECKLIST.md § Missions Phase C-2)

## Glossaire

- **origins_tags** — array OFF d'origines déclarées par le contributeur (e.g. `["en:france", "en:european-union"]`). Niveau d'origine variable : pays seul, hiérarchie complète, ou commune française précise (`fr:saint-martin-de-gurson`).
- **attribute:french** — qualifier mission émis par le PA quand `is_french_product(origins_tags)`. Cf `services/product_attributes.py`.
- **Phase C-2** — wave du sprint missions qui câble l'enrichment french (this batch + extractor + dual-emit + helper).
- **Forward path** — propagation continue via `ratis_batch_off_sync` nightly (chaque delta touche `origins_tags`). Ce batch traite l'arriéré historique.
