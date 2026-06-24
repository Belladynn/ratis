---
type: batch-global
service: ratis_batch_osm_sync
status: production
parent: ARCH_RATIS
sub_archs: []
related: [ARCH_PRODUCT_ANALYSER, ARCH_ocr_store_detection]
tech: [Python, osmium (PBF streaming), httpx (Overpass), SQLAlchemy, Postgres, pyosmium-up-to-date]
tables: [stores, cities, retailers, retailer_aliases]
env_vars: [DATABASE_URL, OSM_OVERPASS_URL]
tags: [batch, osm, stores, geo]
business_domain: pricing
rgpd_concern: false
updated: 2026-04-30
---

# ratis_batch_osm_sync — sync magasins OpenStreetMap

> Batch CLI quotidien qui sync les magasins OpenStreetMap (PBF streaming via osmium + Overpass incrémental) dans `stores`, `cities`, `retailers`, `retailer_aliases`. **Note 2026-05-15** : la proximité magasins ne lit plus ici mais passe par `ARCH_geo.md` (PostGIS).
> @tags: batch osm stores geo openstreetmap pbf overpass osmium retailers cities proximity ratis_core-geo
> @status: LIVRÉ V0
> @subs: auto

> **Note (2026-05-15)** : la recherche de proximité magasins passe désormais par PostGIS + `ratis_core.geo`. Voir `ARCH_geo.md`.

> [[ARCH_RATIS]] · relations : [[ARCH_PRODUCT_ANALYSER]], [[ARCH_ocr_store_detection]]

## Index

- [Résumé en une phrase](#résumé-en-une-phrase) · L.38
- [Responsabilité](#responsabilité) · L.42
- [Fréquence d'exécution](#fréquence-dexécution) · L.51
- [Tables lues / écrites](#tables-lues-écrites) · L.58
- [Dépendances internes (autres services/libs ratis)](#dépendances-internes-autres-serviceslibs-ratis) · L.68
- [Dépendances externes (tiers)](#dépendances-externes-tiers) · L.74
- [Décisions d'architecture clés](#décisions-darchitecture-clés) · L.80
- [Flow principal](#flow-principal) · L.118
- [Paramètres (ratis_settings.json section `osm_sync`)](#paramètres-ratis_settingsjson-section-osm_sync) · L.149
- [Monitoring / logs](#monitoring-logs) · L.162
- [FAQ vectorisée](#faq-vectorisée) · L.168
- [Glossaire](#glossaire) · L.190

---

## Résumé en une phrase

ratis_batch_osm_sync est un batch CLI qui peuple et met à jour la table `stores` à partir d'OpenStreetMap France, soit en streamant un dump PBF Geofabrik (`osm_bulk_import.py`, chemin principal V1), soit en interrogeant l'API Overpass pour des zones ciblées (`osm_sync.py`, chemin legacy), avec résolution automatique des retailers (enseignes) via `retailer_aliases`.

## Responsabilité

- ratis_batch_osm_sync upsert les commerces alimentaires OSM France (shop types : `supermarket`, `convenience`, `bakery`, `butcher`, `greengrocer`) dans `stores` (identifiés par `osm_id`)
- ratis_batch_osm_sync résout le tag OSM `brand` → `retailers.id` via la table `retailer_aliases` (lookup case-insensitive), en créant une ligne `retailers` non-vérifiée (`is_verified=false`) si l'alias est inconnu
- ratis_batch_osm_sync upsert `cities` (postal_code + city_name) pour peupler la base géographique consommée par le matching store OCR
- ratis_batch_osm_sync met à jour in-place le dump PBF via `pyosmium-up-to-date` (option `--update`) pour appliquer les diffs Geofabrik depuis le dernier run
- ratis_batch_osm_sync peut marquer `is_disabled=true` + `disabled_at=NOW()` les stores OSM absentes du PBF courant (option `--disable-missing`), détection des fermetures
- ratis_batch_osm_sync skip les stores "null island" (`lat=0.0 AND lon=0.0`) et celles sans `name` ou coordonnées valides

## Fréquence d'exécution

- **Workflow GitHub Actions** : `.github/workflows/batch_osm_bulk_sync.yml`
- **Cron prévu** : `0 3 * * 0` (dimanche 03h00 UTC) — actuellement désactivé (DB locale)
- **Déclenchement manuel** : `workflow_dispatch` avec options `update_pbf`, `dry_run`, `disable_missing`
- Le workflow télécharge le PBF `france-latest.osm.pbf` (~4-5 GB) ou applique les diffs via `pyosmium-up-to-date` avant d'appeler `osm_bulk_import.py`

## Tables lues / écrites

| Table | Opération |
|---|---|
| `stores` | UPSERT `ON CONFLICT (osm_id) WHERE osm_id IS NOT NULL DO UPDATE` : name, retailer_id, address, city, postal_code, lat/lng, phone, siret, opening_hours, reset is_disabled=false |
| `stores` (disable-missing) | UPDATE `is_disabled=true, disabled_at=NOW() WHERE osm_id <> ALL(:seen_ids)` |
| `retailers` | INSERT (canonical_name, slug, is_verified=false) `ON CONFLICT (slug) DO UPDATE` pour enseignes inconnues |
| `retailer_aliases` | INSERT (retailer_id, alias=lowercased brand, source='osm') `ON CONFLICT DO NOTHING` |
| `cities` | INSERT (postal_code, city_name UPPER, department, country_code) `ON CONFLICT (postal_code, city_name) DO NOTHING` |

## Dépendances internes (autres services/libs ratis)

- [[ARCH_CORE]] — `make_engine`, `load_settings` (`osm_sync.shop_types`, `country_code`, `overpass_timeout`, `batch_chunk_size`), `require_env`, `normalize_phone`
- [[ARCH_PRODUCT_ANALYSER]] — consommateur principal de `stores` (OCR header → matching magasin)
- [[ARCH_ocr_store_detection]] — dépend de la fraîcheur de `stores` + `retailer_aliases` pour le matching en-tête ticket

## Dépendances externes (tiers)

- **Geofabrik** — dump PBF `france-latest.osm.pbf` (chemin principal V1, DA-36)
- **OpenStreetMap Overpass API** — `OSM_OVERPASS_URL` (env var), chemin legacy `osm_sync.py` pour requêtes ciblées/tests
- **pyosmium-up-to-date** (binary optional) — diff application sur PBF local. Si absent du PATH, warning + run sur PBF stale

## Décisions d'architecture clés

### DA-36 — PBF streaming comme chemin principal, Overpass en fallback

**Choix** : `osm_bulk_import.py` (osmium.SimpleHandler streaming) = chemin V1
**Alternative rejetée** : Overpass seul pour tout peupler la France
**Raison** : Overpass timeout sur des requêtes couvrant la France entière (>600M nodes). Le PBF streaming lit le fichier localement, peak RAM ~200 MB, traite le pays complet en quelques minutes. Overpass reste utile pour des zones ciblées (tests, corrections ponctuelles).

### DA-02 — Fast path tag check au C level

**Choix** : dans `_ShopHandler.node()`, check `shop = n.tags.get("shop")` avant toute matérialisation Python
**Alternative rejetée** : convertir tous les tags en dict Python puis filtrer
**Raison** : 99.9% des nodes OSM n'ont pas de tag `shop`. Matérialiser un dict Python par node = catastrophique (600M allocations). Le `.get("shop")` reste au niveau C d'osmium → return immédiat si None.

### DA-03 — Ways résolus via node-location index (révisé 2026-04-30)

**Choix actuel** : `apply_file(locations=True)` — osmium maintient un index `flex_mem` des positions de nodes pendant le streaming, le callback `way()` calcule un centroïde simple en moyennant les `.location` des nodes constituants. Centroïde valide même partiel (nodes hors-PBF ignorés tant qu'au moins un est résolvable).
**Choix initial (DA-36, abandonné)** : `way()` tagged shop → `skipped_invalid` sans résolution.
**Raison du revirement** : la skip-policy initiale silenced ~5-15% des stores (e.g. Intermarché Express Courbevoie way `1293099937` — bâtiment OSM polygon, pas node). Coût RAM mesuré du `flex_mem` index : ~1-2 GB pour la France entière, acceptable sur un runner avec ≥4 GB RAM. Le gain (récupération de tous les magasins polygon-tagged + bâtiments d'enseigne) justifie le coût.
**Skip discipline** : tous les drop events (way no-resolvable-nodes, node null-island, missing-name, normalize None) émettent désormais un log INFO `osm_skip kind=… osm_id=… reason=…` pour permettre l'audit prod.

### DA-04 — Resolve-or-create retailer inline

**Choix** : `resolve_or_create_retailer(db, brand_tag)` lookup alias puis INSERT if miss (slug-based)
**Alternative rejetée** : passe séparée de clean-up retailers
**Raison** : éviter un batch séparé pour alimenter `retailers`. Le flux OSM apporte déjà la liste des enseignes ; `is_verified=false` signale qu'une review humaine est souhaitée avant d'exposer l'enseigne à l'app, mais les stores peuvent déjà être rattachés.

### DA-05 — `upsert_city` UPPERCASE + dedup par `(postal_code, city_name)`

**Choix** : city_name stocké en UPPER côté SQL
**Alternative rejetée** : stocker tel quel
**Raison** : OSM contient `"Paris"`, `"PARIS"`, `"paris"` pour la même ville. Clé UNIQUE `(postal_code, UPPER(city_name))` dedup au niveau DB. Display : ré-upper/lower côté front au besoin.

### DA-06 — Skip "null island" (0.0, 0.0)

**Choix** : skip_null_island=True par défaut dans `_ShopHandler`
**Alternative rejetée** : insérer avec warning
**Raison** : coordonnées (0,0) sont soit un bug OSM, soit un point au milieu de l'océan Atlantique. Aucun cas légitime pour un shop français.

### DA-07 — `upsert_store` gère 4 invariants d'unicité (pré-check Python)

**Choix** : pré-check en Python (`SELECT 1 ... LIMIT 1`) avant l'`INSERT`, NULL-out des champs en collision (phone, siret) ou merge sur la row existante (composite). L'`INSERT` final garde son `ON CONFLICT (osm_id) DO UPDATE` canonique.
**Alternative rejetée (A)** : sanitize en bulk (un seul SELECT par insert) — refait le même travail mais sans gain.
**Alternative rejetée (B)** : SAVEPOINT + retry sur `IntegrityError` en parsant `constraint_name` — plus rapide en happy-path mais plus complexe (parsing de message d'erreur, gestion de l'aborted-transaction state).
**Raison** : Postgres n'accepte qu'un seul `conflict_target` par `INSERT`. La table `stores` carry **4 invariants** : `uq_stores_osm_id` (partial), `uq_stores_phone` (partial), `uq_stores_siret` (partial), `unique_store` (composite NULL-safe sur `(retailer, address, postal_code)`). Le pré-check Python reste lisible, prédictible, testable et coût négligeable (2 SELECT/insert pour 60 k rows = +30 s sur un import de 75 s — tolérable).

**Stratégie par contrainte** :

| Conflit | Action |
|---|---|
| `osm_id` même | `ON CONFLICT (osm_id) DO UPDATE` (comportement original) |
| `phone` autre osm_id | NULL out phone côté nouveau row + WARNING |
| `siret` autre osm_id | NULL out siret côté nouveau row + WARNING |
| `(retailer, address, postal_code)` collide avec row `osm_id IS NULL` (admin-seeded) | UPDATE in-place de la row existante : adopte l'`osm_id` OSM, fusionne les champs OSM-sourced |
| `(retailer, address, postal_code)` collide avec autre `osm_id` | Skip + WARNING (deux nodes OSM réclament la même adresse — donnée à investiguer) |

**Contexte historique (2026-04-27)** : un bulk import Geofabrik (58 921 stores en 75 s) a forcé le DROP des 3 indexes (`unique_store`, `uq_stores_phone`, `uq_stores_siret`) faute de gestion multi-conflit dans `upsert_store`. Migration `20260427_1700_recreate_stores_uq` recrée les indexes après déploiement du fix.


### DA-08 — Upsert délègue à batch_shared (PR7 — 2026-05-31)

**Choix** : depuis PR7, `osm_sync.run_batch` et `osm_bulk_import.run_bulk_import` utilisent `batch_shared.store_consolidation.find_match + apply_upsert` au lieu de `normalize.upsert_store` local. `normalize.resolve_or_create_retailer` est remplacé par l'import de `batch_shared.retailer_resolution.resolve_or_create_retailer` (avec `alias_source='osm'`).
**Raison** : partager la logique de consolidation multi-source (SIRENE, OSM, Overture) dans un helper unique — évite la dérive entre les batches, permet la gestion trust-priority (admin > sirene > overture > osm > user_suggested).
**Ce qui reste OSM-spécifique** :
- `normalize.slugify()`, `normalize_pbf_tags()`, `osm_dict_to_candidate()` — mappers PBF/Overpass.
- `osm_composite_key_collision()` — pré-check du `unique_store` composite index avant INSERT, conservé car `apply_upsert` ne gère pas les stores sans attributs identifiants (no SIRET, different osm_id, names trop dissimilaires pour le fuzzy).
- `normalize.upsert_store()` — DEPRECATED, conservé pour les tests existants uniquement.
- `_disable_missing_stores()` — bulk UPDATE `is_disabled=true WHERE osm_id NOT IN (...)`, OSM-spécifique.
**Voir** : `ARCH_BATCH_SIRENE_SYNC.md` § stratégie consolidation multi-source.

## Flow principal

### Flow 1 — Bulk PBF streaming (chemin V1)

1. `main()` parse args : `--pbf PATH`, `--update`, `--dry-run`, `--disable-missing`
2. `require_env("DATABASE_URL")`, charge `settings.osm_sync`
3. Si `--update` (hors dry-run) : `update_pbf(pbf_path)` lance `pyosmium-up-to-date` (ou warn si absent)
4. `run_bulk_import(factory, cfg, pbf_path, ...)` instancie `_ShopHandler` avec shop_types whitelisté
5. `handler.apply_file(pbf_path)` : streaming node/way → fast-path tag check → `_emit()` batch
6. Chaque `chunk_size` (default 1000) : flush_cb → upsert en batch + commit
7. Après EOF : `flush_remainder`
8. Si `--disable-missing` (hors dry-run) : UPDATE `stores SET is_disabled=true WHERE osm_id NOT IN (seen_osm_ids)`
9. Retourne stats : inserted, skipped_non_shop, skipped_invalid, cities_upserted, chunks_committed, disabled_missing

### Flow 2 — Overpass ciblé (legacy)

1. `osm_sync.main()` require `DATABASE_URL`, `OSM_OVERPASS_URL`
2. `_build_overpass_query(cfg)` : query QL sur shop_types + country ISO2 avec `out center;`
3. `fetch_osm_elements(OSM_OVERPASS_URL, cfg)` : POST httpx, timeout = overpass_timeout + 30
4. `_normalize_osm_element(element, country_code)` → délègue à `normalize_pbf_tags` (même mapping que PBF)
5. Loop par chunks de `batch_chunk_size` (default 500) : upsert_store + upsert_city + commit

### Flow 3 — Résolution retailer_id depuis brand OSM

1. `brand_tag` présent dans les OSM tags
2. `resolve_or_create_retailer(db, brand_tag)` : strip + lowercase = `alias_key`
3. Lookup `retailer_aliases WHERE alias = :alias_key LIMIT 1` → si hit, retour `retailer_id`
4. Si miss : `slug = slugify(cleaned)` (translit accents + alphanum), INSERT `retailers (canonical_name, slug, is_verified=false) ON CONFLICT (slug) DO UPDATE SET canonical_name = retailers.canonical_name RETURNING id`
5. INSERT `retailer_aliases (retailer_id, alias, source='osm') ON CONFLICT DO NOTHING`
6. Retour `retailer_id` → consommé par `upsert_store` (le trigger `trg_stores_sync_retailer_text` peuple `stores.retailer` TEXT depuis retailer_id)

## Paramètres (ratis_settings.json section `osm_sync`)

```json
"osm_sync": {
  "shop_types": ["supermarket", "convenience", "bakery", "butcher", "greengrocer"],
  "country_code": "FR",
  "overpass_timeout": 120,
  "batch_chunk_size": 500,
  "dedup_radius_m": 50,
  "fuzzy_threshold": 0.85
}
```

Mode PBF utilise `batch_chunk_size=1000` par défaut (surchargeable en config).
`dedup_radius_m` et `fuzzy_threshold` sont passés à `batch_shared.store_consolidation.find_match` (ajoutés en PR7).

## Monitoring / logs

- Progression tous les 500 shop elements vus : `progress: N seen, K kept, rate/s, elapsed`
- Stats finales : `Bulk import complete: {inserted, skipped_non_shop, skipped_invalid, cities_upserted, chunks_committed, disabled_missing, seen_osm_ids}`
- Warnings : PBF non trouvé, pyosmium-up-to-date absent, R2 errors (si pertinent)

## FAQ vectorisée

### Pourquoi ratis_batch_osm_sync utilise-t-il le streaming PBF plutôt qu'Overpass pour la France ?

Overpass a un timeout server-side (~180s) et une limite de bande passante. Une query `shop=supermarket|convenience|bakery|butcher|greengrocer` sur toute la France dépasse largement ces limites. Le PBF Geofabrik (dump OSM brut) permet de streamer localement via osmium — le fichier pèse ~4-5 GB mais se parcourt en quelques minutes avec un peak RAM ~200 MB. Overpass reste en fallback pour des zones ciblées.

### Comment ratis_batch_osm_sync résout-il les enseignes (retailers) ?

Le tag OSM `brand` (ex : `"Carrefour"`) est normalisé en lowercase + trimmed pour servir de clé dans `retailer_aliases`. Si un alias existe, le `retailer_id` est retourné. Sinon, ratis_batch_osm_sync crée une entrée `retailers` non-vérifiée (slug-based, `is_verified=false`) et enregistre l'alias. Un admin peut ensuite valider/merger les retailers downstream. Les stores sans tag `brand` restent avec `retailer_id = NULL` jusqu'à correction manuelle ou OCR.

### Que se passe-t-il si un magasin disparaît d'OSM (fermeture) ?

Avec l'option `--disable-missing`, ratis_batch_osm_sync marque `is_disabled=true` + `disabled_at=NOW()` toutes les stores dont `osm_id` n'est pas dans le set vu pendant le run courant. C'est un soft-delete — la store reste en DB (FK préservées) mais disparaît de l'app. Pour éviter les faux positifs, cette option est optionnelle (pas activée par défaut dans le cron).

### Comment tester ratis_batch_osm_sync localement ?

`uv run pytest batch/ratis_batch_osm_sync/tests/` (tests unitaires sur normalize + osm_sync + osm_bulk_import). Pour un dry-run contre un petit PBF extrait : `uv run python batch/ratis_batch_osm_sync/osm_bulk_import.py --pbf data/test.pbf --dry-run`. Le chemin Overpass nécessite un serveur (OSM_OVERPASS_URL) accessible ; en local, utiliser un mock httpx.

### Quelle différence entre ratis_batch_osm_sync et le matching store côté OCR ?

ratis_batch_osm_sync peuple la base géographique (table `stores`). Le matching store OCR (dans [[ARCH_ocr_store_detection]]) lit cette base pour retrouver le magasin d'un ticket scanné depuis son en-tête texte + localisation GPS. Les deux sont couplés : sans ratis_batch_osm_sync à jour, le matching OCR a des lacunes ; sans matching OCR, les stores restent inutilisées.

## Glossaire

- **DA-XX** : décision d'architecture numérotée
- **PBF** : Protocol Buffer Format — encodage binaire compact des données OSM (dump Geofabrik)
- **Overpass** : API de query read-only sur OSM (QL syntax), alternative au dump PBF pour des zones ciblées
- **osmium** : bibliothèque C++ + binding Python pour parser les PBF en streaming
- **osm_id** : identifiant unique OSM d'un node ou d'un way
- **retailer_aliases** : table de mapping `alias_text → retailer_id` pour résoudre les variations de casse/orthographe des enseignes
- **Null island** : point (0,0) au milieu de l'océan Atlantique — valeur par défaut erronée dans certaines données géographiques
- **pyosmium-up-to-date** : binary qui applique les diffs Geofabrik à un PBF local pour éviter le re-download complet
