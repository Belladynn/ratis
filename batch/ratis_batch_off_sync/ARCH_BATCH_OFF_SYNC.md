---
type: batch-global
service: ratis_batch_off_sync
status: production
parent: ARCH_RATIS
sub_archs: []
related: [ARCH_PRODUCT_ANALYSER]
tech: [Python, asyncio, httpx, tenacity, ProcessPoolExecutor, SQLAlchemy, Postgres]
tables: [products, batch_sync_log]
env_vars: [DATABASE_URL]
tags: [batch, pricing, open-food-facts, product-catalog]
business_domain: pricing
rgpd_concern: false
updated: 2026-04-24
---

# ratis_batch_off_sync — sync catalogue OpenFoodFacts

> Batch CLI multi-source (OFF/OBP/OPF/OPFF) qui synchronise le catalogue produits depuis les projets Open\*Facts vers `products`, soit en delta paginé (incrémental), soit en dump JSONL complet (bootstrap initial). Code partagé 100 % entre sources via `--source`.
> @tags: batch off open-food-facts open-beauty-facts product-catalog ean sync delta dump multi-source pricing
> @status: LIVRÉ V0
> @subs: auto

> [[ARCH_RATIS]] · relations : [[ARCH_PRODUCT_ANALYSER]]

## Index

- [Résumé en une phrase](#résumé-en-une-phrase) · L.41
- [Responsabilité](#responsabilité) · L.45
- [Fréquence d'exécution](#fréquence-dexécution) · L.53
- [Tables lues / écrites](#tables-lues-écrites) · L.62
- [Dépendances internes (autres services/libs ratis)](#dépendances-internes-autres-serviceslibs-ratis) · L.69
- [Dépendances externes (tiers)](#dépendances-externes-tiers) · L.74
- [Décisions d'architecture clés](#décisions-darchitecture-clés) · L.79
- [Flow principal](#flow-principal) · L.117
- [Paramètres (ratis_settings.json section `off_sync`)](#paramètres-ratis_settingsjson-section-off_sync) · L.140
- [Champs synchronisés (`_SYNC_COLS`)](#champs-synchronisés-_sync_cols) · L.150
- [Non stocké (délibérément)](#non-stocké-délibérément) · L.164
- [Comportements connus — non-bugs](#comportements-connus-—-non-bugs) · L.171
- [Monitoring / logs](#monitoring-logs) · L.181
- [FAQ vectorisée](#faq-vectorisée) · L.186
- [Glossaire](#glossaire) · L.208

---

## Résumé en une phrase

ratis_batch_off_sync est un batch CLI **multi-source** qui synchronise le catalogue produit depuis les projets Open\*Facts (OFF par défaut, OBP/OPF/OPFF en option via `--source`) vers la table `products`, soit via l'API Search paginée en mode incrémental (`delta`/`weekly`/`monthly`), soit via un dump JSONL complet pour le bootstrap initial (`full`).

## Sources supportées (à partir de PR1 multi-source)

| Source | `--source` | API base URL | batch_sync_log.batch_name |
|---|---|---|---|
| OpenFoodFacts | `off` (défaut) | `https://world.openfoodfacts.org` | `off_sync` |
| OpenBeautyFacts | `obp` (actif depuis PR2) | `https://world.openbeautyfacts.org` | `obp_sync` |
| OpenProductsFacts | `opf` (PR3) | `https://world.openproductsfacts.org` | `opf_sync` |
| OpenPetFoodFacts | `opff` (PR4) | `https://world.openpetfoodfacts.org` | `opff_sync` |

Le batch partage 100 % du code (extractor, repository, dump, api). Seuls
`Source.api_base_url`, `Source.user_agent`, `Source.photo_hosts`,
`Source.batch_name` et `Source.classify_storage` varient — voir
`off_sync/sources.py`. Pour `OBP/OPF/OPFF`, `classify_storage=False` donc
`storage_type` reste `NULL` (catégories non-food, pas applicable).

> ✅ PR2 (migration `20260511_0900_obp_opf`) a élargi le CHECK constraint
> `source_check` à `('off','obp','opf','opff','internal')` et renommé
> `off_no_unit` → `catalogue_no_unit` (couvre les 4 catalogues externes).
> OBP est donc activé via le cron matrix `[off, obp]` (`0 4 * * *`). OPF/OPFF
> seront ajoutés à la matrix dans PR3/PR4 — la migration anticipe déjà leur
> activation. Voir `docs/superpowers/specs/2026-05-10-obp-opf-design.md`
> § Décisions actées.

## Responsabilité

- ratis_batch_off_sync upsert les produits OFF (identifiés par EAN) dans `products` avec `source='off'` — les produits `source='internal'` sont protégés
- ratis_batch_off_sync extrait et valide les champs OFF utiles (name, brand, photos CDN, quantity, tags allergens/ingredients/categories/labels)
- ratis_batch_off_sync déduit `storage_type` ∈ {`frozen`, `fresh`, `ambient`, `unmatched`, NULL} depuis `categories_tags` + `labels_tags` + `conservation_conditions` via `product_knowledge.json`
- ratis_batch_off_sync trace chaque run dans `batch_sync_log` (`batch_name='off_sync'`) pour permettre au mode `delta` de repartir depuis la dernière exécution réussie
- ratis_batch_off_sync gère la déduplication EAN intra-batch (last-wins) pour éviter les `CardinalityViolation` sur `ON CONFLICT DO UPDATE`

## Fréquence d'exécution

- **Workflow GitHub Actions** : `.github/workflows/batch_off_sync.yml`
- **Crons prévus** (actuellement désactivés en DB locale) :
  - `0 4 * * *` — mode `delta` quotidien
  - `0 2 * * 0` — mode `weekly` dimanche
  - `0 1 1 * *` — mode `monthly` 1er de chaque mois
- **Mode `full`** : uniquement `workflow_dispatch` — one-shot de bootstrap, ne tourne jamais sur un cron

## Tables lues / écrites

| Table | Lecture | Écriture |
|---|---|---|
| `products` | vérif `source` avant UPDATE (protection `source='internal'`) | upsert via `unnest` sur tous les `_SYNC_COLS` pour `source='off'` |
| `batch_sync_log` | mode `delta` : `last_run_at` du dernier `success` pour calculer `since_ts` | INSERT fin de run (success/failed) avec `rows_affected = inserted + updated` |

## Dépendances internes (autres services/libs ratis)

- [[ARCH_CORE]] — `make_engine`, `load_settings` (`off_sync.api_base_url`), `classify()` via `product_knowledge.json`
- [[ARCH_PRODUCT_ANALYSER]] — consommateur principal de la table `products` (OCR → lookup EAN)

## Dépendances externes (tiers)

- **Open Food Facts Search API** — `https://world.openfoodfacts.org` (configurable via `settings.off_sync.api_base_url`), modes `delta`/`weekly`/`monthly` — User-Agent requis
- **Dump JSONL OFF** — fichier local téléchargé en amont, mode `full` uniquement

## Décisions d'architecture clés

### DA-01 — Asyncio + httpx pour l'API, ProcessPoolExecutor pour le dump

**Choix** : parallélisme adapté à la charge
**Alternative rejetée** : un seul modèle (tout async ou tout process)
**Raison** : les modes API sont I/O-bound (requêtes HTTP paginées, latence OFF) → asyncio + httpx maximise la concurrence sans coût process. Le mode full parse du JSONL gz de plusieurs Go + transformations produit → CPU-bound → ProcessPoolExecutor sur chunks.

### DA-02 — Retry via tenacity avec backoff exponentiel

**Choix** : tenacity, 3 tentatives, backoff 2-30s, retry sur 429/500/502/503
**Alternative rejetée** : pas de retry (laisser le workflow retry)
**Raison** : OFF a des coupures ponctuelles, les retries transparents évitent de rejouer tout le batch. Respect de l'en-tête `Retry-After` sur 429 pour ne pas se faire blacklister.

### DA-03 — Source `'off'` protégée, `'internal'` intouchée

**Choix** : l'upsert ne cible jamais `source='internal'`
**Alternative rejetée** : upsert générique par EAN
**Raison** : les produits internes (correction manuelle via admin) sont la vérité — OFF ne doit jamais les écraser. Filtre au niveau du SQL d'upsert.

### DA-04 — Dedup intra-batch `seen[ean] = p`

**Choix** : last-wins dans le dict avant bulk upsert
**Alternative rejetée** : accepter le `CardinalityViolation` et retry
**Raison** : `ON CONFLICT DO UPDATE` lève `CardinalityViolation` si le même EAN apparaît deux fois dans le même INSERT. OFF peut renvoyer des doublons (mise à jour multiple dans la même plage). Solution : dedup Python avant le bulk.

### DA-05 — Overlap 5 minutes en mode delta

**Choix** : `since_ts = last_success_ts - 5 min`
**Alternative rejetée** : `since_ts = last_success_ts` exact
**Raison** : les produits OFF modifiés juste avant le cutoff du run précédent pourraient être manqués (timing). 5 minutes de recouvrement × dedup intra-batch = garantit zero perte sans doublon.

### DA-06 — `storage_type` déduit, pas inventé

**Choix** : retourne `NULL` si aucun champ OFF analysable, `'unmatched'` si champs présents sans pattern, `'ambient'` si catégorie alimentaire connue sans marqueur frozen/fresh
**Alternative rejetée** : default `'ambient'` dans tous les cas
**Raison** : un produit surgelé classé `'ambient'` par défaut serait un faux positif grave (recommandation route stockage). `NULL` et `'unmatched'` sont des signaux explicites pour un futur enrichissement LLM.

### DA-08 (PR2) — OBP activé en daily delta, `classify_storage` skipped

**Choix** : OBP rejoint le cron matrix `[off, obp]` (cron `0 4 * * *`), même image
Docker, même code, seuls `--source` + le User-Agent diffèrent. Sur OBP, le flag
`Source.classify_storage=False` court-circuite `_derive_storage_type()` →
`products.storage_type=NULL` systématique.
**Alternative rejetée** : laisser la classification tourner et accepter
`storage_type='ambient'` ou `'unmatched'` pour les cosmétiques.
**Raison** : les règles `product_knowledge.json` ciblent des catégories
alimentaires (Surgelés, Produits laitiers, etc.) — appliquées sur les
catégories OBP elles renvoient des valeurs sémantiquement fausses
("Crème change Biolane" classée `'ambient'`). Cleaner = skip explicit.
**Volume nominal** : ~62k produits FR sur OBP, run typique <2 min.
**Empirie** : smoke test au 2026-05-11 → 5204 inserts sur fenêtre delta 30 min,
0 % erreur DB, tous storage_type=NULL.

## Flow principal

### Flow 1 — Mode API (delta/weekly/monthly)

1. `main()` parse args, valide (`--mode delta|weekly|monthly` ou `--since YYYY-MM-DD [--until]`)
2. Charge `settings.off_sync.api_base_url` (fail-fast si absent)
3. Si mode `delta` : lit `batch_sync_log.last_run_at` du dernier `success` → `since_ts = last - 5 min`, fallback `now - 1 day` si premier run
4. `run_api(db_url, since_ts, until_ts, workers, dry_run, api_base_url)` sous `asyncio.wait_for(timeout)`
5. Paginate OFF Search API par tranches de pages, corouroutines concurrentes (`workers`), tenacity retry sur 429/5xx
6. Pour chaque produit : `extractor.extract_product()` (validation EAN regex, extraction champs, `extract_net_weight` 3 niveaux, `classify()` storage_type) → ajoute au buffer
7. Dedup par EAN (last-wins), bulk `upsert_products` via `unnest` sur `_SYNC_COLS`
8. Retourne `Stats(inserted, updated, skipped, invalid)`
9. Écrit `batch_sync_log(status, rows_affected=inserted+updated)`

### Flow 2 — Mode full (dump JSONL)

1. `--mode full --dump /data/off.jsonl.gz` requis
2. `run_dump(path, db_url, workers, dry_run, timeout)` ouvre le JSONL gz
3. ProcessPoolExecutor sur chunks de lignes JSONL (CPU-bound : decompress + parse + transform)
4. Chaque worker : extraction + dedup local + upsert par chunk
5. Agrège les Stats chunk par chunk via `as_completed(timeout)`
6. Même écriture `batch_sync_log` en fin

## Paramètres (ratis_settings.json section `off_sync`)

```json
"off_sync": {
  "api_base_url": "https://world.openfoodfacts.org"
}
```

`workers`, `timeout`, `dry-run`, `mode` passent via CLI args — volontairement pas dans settings (paramètres d'exécution, pas métier).

## Champs synchronisés (`_SYNC_COLS`)

| Colonne `products` | Source OFF | Traitement |
|---|---|---|
| `ean` | `code` | regex `\d{8,13}` |
| `name` | `product_name_fr` > `product_name` | tronqué 500 |
| `brand` | `brands` | tronqué 200 |
| `photo_url` / `photo_url_small` | `image_front_url` / `image_front_small_url` | whitelist CDN OFF |
| `product_quantity` | `extract_net_weight` (3 niveaux de fallback) | cap 100k |
| `product_quantity_unit` | dérivé | brut OFF |
| `quantity_raw` | `quantity` | tronqué 100 |
| `storage_type` | `classify()` sur `categories_tags` + `labels_tags` + `conservation_conditions` | ∈ {frozen, fresh, ambient, unmatched, NULL} |
| `allergens_tags` / `ingredients_tags` / `categories_tags` / `labels_tags` | identiques OFF | items > 100 chars filtrés |
| `origins_tags` | `origins_tags` | items > 100 chars filtrés (PR Phase C-2 missions 2026-05-11) — drive le qualifier `attribute:french` côté PA |
| `product_name_fr` | `product_name_fr` | tronqué 500 (PR multi-fields 2026-05-01) |
| `product_name` | `product_name` | tronqué 500 (international, séparé du legacy `name`) |
| `generic_name_fr` | `generic_name_fr` > `generic_name` | tronqué 500 |
| `brands_text` | `brands` (raw multi-comma) | mirroir de `brands` — kept distinct for FE compose |
| `quantity_text` | `quantity` raw | mirroir de `quantity_raw` — kept distinct for FE compose |

Les nouveaux champs alimentent `ratis_core.products.pick_display_name` qui compose un `display_name` côté backend (response GET /scan/receipt/{id}) — voir `ARCH_PRODUCT_ANALYSER.md` pour la consommation côté PA.

## Non stocké (délibérément)

- Nutri-Score / NOVA / valeurs nutritionnelles — hors V1
- `lang`, `last_modified_t` — métadonnées OFF sans valeur métier
- `stores_tags` — on a notre propre table `stores`
- Traductions `product_name_XX` (hors `product_name_fr` / `generic_name_fr` qui sont stockés depuis 2026-05-01) — autres langues hors V1

## Comportements connus — non-bugs

### Threads `asyncio.to_thread` orphelins sur timeout (`api.py`)

Au timeout `asyncio.wait_for`, les threads déjà en cours de commit DB finissent leur INSERT (déjà sur le wire) puis tentent de rendre la connexion au pool déjà disposé. SQLAlchemy ferme proprement — quelques log errors, zero corruption. Non corrigé car la refonte complexifie `run_api` sans bénéfice (cas rare en prod).

### Workers subprocess non stoppables sur timeout (`dump.py`)

`ProcessPoolExecutor.__exit__` appelle `shutdown(wait=True)` — les workers démarrés finissent leur chunk avant exit. Durée réelle = `timeout + dernier chunk en cours`. Limitation fondamentale de Python ≤ 3.14. Non corrigé car `cancel_futures=True` n'annule que la queue, pas les process running. Le mode `full` est one-shot de bootstrap — compromis acceptable.

## Monitoring / logs

- `batch_sync_log` pour audit (last_run_at, status, rows_affected)
- Stats dataclass : `inserted` / `updated` / `skipped` / `invalid` loggées en fin

## FAQ vectorisée

### Pourquoi ratis_batch_off_sync protège-t-il `source='internal'` ?

Les produits `source='internal'` ont été créés ou corrigés manuellement via un process admin (TRAINING.md). Si ratis_batch_off_sync les écrasait avec les données OFF, on perdrait les corrections humaines à chaque run. Le filtrage au niveau SQL garantit que le batch ne touche que `source='off'`.

### Comment ratis_batch_off_sync reprend-il après une panne ?

Le mode `delta` lit `batch_sync_log.last_run_at` du dernier run marqué `success`. La prochaine exécution repart de `last_run_at - 5 min` (overlap de sécurité) jusqu'à `now()`. Si un run échoue, la ligne `batch_sync_log` est tout de même insérée avec `status='failed'` — le run suivant repart du **dernier succès**, pas du dernier failed, donc il rattrape la plage loupée.

### Pourquoi trois modes distincts delta/weekly/monthly ?

Dans ratis_batch_off_sync, les trois modes partagent la même logique de transformation/upsert — seule la plage temporelle diffère. `delta` (1 jour) pour le tempo réel quotidien, `weekly` (7 jours) pour rattraper une panne prolongée, `monthly` (30 jours) pour une reprise après incident majeur ou pour compléter les oublis. C'est du sucre syntaxique sur `--since`.

### Comment tester ratis_batch_off_sync localement ?

`uv run pytest batch/ratis_batch_off_sync/tests/` lance la suite complète (conftest dédié, mocks httpx). Pour un run réel contre OFF : `uv run python batch/ratis_batch_off_sync/off_sync/main.py --mode delta --dry-run --workers 2` — logue ce qui serait inséré/updaté sans écrire en DB.

### Quelle différence entre ratis_batch_off_sync et un batch data-clean ?

ratis_batch_off_sync se contente d'ingérer et de dériver mécaniquement (regex, classify patterns). Il ne fait **pas** : normalisation `brands`, déduplication tags multi-langue, renseignement de `category_id`/`brand_id` (FK). Ces tâches reviendront à un futur `ratis_batch_data_clean` (et éventuellement un batch LLM Mistral local) — hors V1.

## Glossaire

- **DA-XX** : décision d'architecture numérotée
- **EAN** : European Article Number — code-barre produit (8 à 13 digits)
- **delta / weekly / monthly / full** : modes CLI de ratis_batch_off_sync (fenêtres 1j / 7j / 30j / dump complet)
- **`_SYNC_COLS`** : tuple des colonnes `products` écrites par le batch — pilote l'upsert dynamique
- **storage_type** : classification du mode de conservation (frozen/fresh/ambient/unmatched/NULL)
- **`batch_sync_log`** : table d'audit partagée par tous les batches (batch_name, status, rows_affected, last_run_at)
