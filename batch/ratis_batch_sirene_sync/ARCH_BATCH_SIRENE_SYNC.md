---
type: batch-global
service: ratis_batch_sirene_sync
status: production
parent: ARCH_RATIS
sub_archs: []
related: [ARCH_BATCH_OSM_SYNC]
tech: [Python, pyarrow, httpx, SQLAlchemy, Postgres]
tables: [stores, sirene_geocode_cache, retailers, retailer_aliases]
env_vars: [DATABASE_URL, SIRENE_BULK_URL, GEOPLATEFORME_GEOCODE_URL, SIRENE_BULK_CACHE_DIR]
tags: [batch, sirene, stores, fr, insee, geocoding]
business_domain: pricing
rgpd_concern: false
updated: 2026-05-31
---

# ratis_batch_sirene_sync — sync magasins SIRENE INSEE (source primaire FR)

> Batch mensuel qui ingère la base SIRENE INSEE (Parquet bulk via data.gouv.fr) dans `stores` pour les établissements alimentaires de France, via geocoding bulk Géoplateforme. Source primaire FR, `ratis_batch_osm_sync` reste source internationale + fallback FR.
> @tags: batch sirene stores fr insee geocoding geoplateforme parquet ape whitelist trust-hierarchy
> @status: LIVRÉ V1.0
> @subs: auto

> [[ARCH_RATIS]] · relations : [[ARCH_BATCH_OSM_SYNC]]

## Index

- [Résumé en une phrase](#résumé-en-une-phrase) · L.27
- [Responsabilité](#responsabilité) · L.31
- [Fréquence d'exécution](#fréquence-dexécution) · L.44
- [Tables lues / écrites](#tables-lues-écrites) · L.52
- [Dépendances internes (autres services/libs ratis)](#dépendances-internes-autres-serviceslibs-ratis) · L.64
- [Dépendances externes (tiers)](#dépendances-externes-tiers) · L.70
- [Décisions d'architecture clés](#décisions-darchitecture-clés) · L.79
- [Flow principal](#flow-principal) · L.118
- [Paramètres (ratis_settings.json section `sirene_sync`)](#paramètres-ratis_settingsjson-section-sirene_sync) · L.134
- [Implementation checklist](#implementation-checklist) · L.152
- [Note multi-country V3](#note-multi-country-v3) · L.170

---

## Résumé en une phrase

ratis_batch_sirene_sync est un batch CLI mensuel qui ingère les établissements alimentaires de la base SIRENE INSEE (Parquet bulk, ~12 M lignes) dans `stores` via filtre code APE, geocoding bulk Géoplateforme BAN (`data.geopf.fr/geocodage`), et upsert via le helper partagé `batch/_shared/store_consolidation.py`.

## Responsabilité

- ratis_batch_sirene_sync télécharge le ZIP bulk SIRENE (`StockEtablissement_utf8.zip`) depuis data.gouv.fr (cache local Parquet, TTL 30 jours par défaut)
- ratis_batch_sirene_sync filtre les établissements actifs (`etatAdministratifEtablissement = 'A'`) avec code APE dans la whitelist alimentaire (cf `ratis_settings.json § sirene_sync.ape_whitelist`)
- ratis_batch_sirene_sync géocode en bulk via `data.geopf.fr/geocodage/search/csv` (Géoplateforme IGN, successeur BAN décommissionné 2026-01), avec cache `sirene_geocode_cache`
- ratis_batch_sirene_sync upsert les stores via `batch_shared.store_consolidation.upsert_store()` (helper PR2, importé depuis `batch/_shared/`)
- ratis_batch_sirene_sync marque `is_disabled=true` les établissements SIRENE passés à `etatAdministratifEtablissement = 'F'` (fermés)
- ratis_batch_sirene_sync respecte la hiérarchie de confiance : `admin > sirene > overture > osm > user_suggested` — ne surécrit jamais un store `source='admin'`

## Fréquence d'exécution

- **Cron actif (PR6)** : `0 4 1 * *` (1er du mois, 04h UTC)
- **Workflow GitHub Actions** : `.github/workflows/batch_sirene_sync.yml` (lint + tests, exécution via `workflow_dispatch` pour debug)
- **Run prod one-shot** : `./run-prod-batch.sh sirene_sync [--dry-run] [--full]`

## Tables lues / écrites

- **`stores`** (écrit) — upsert par `siret` (UNIQUE INDEX `ix_stores_siret_lookup`, PR1) ; fallback sur `(retailer, address, postal_code)` si siret absent
- **`sirene_geocode_cache`** (lue/écrite, PR1) — cache geocoding par adresse normalisée pour éviter les appels Géoplateforme répétés (TTL 90 jours)
- **`retailers`** (lue/écrite) — résolution enseigne par `siret → enseigne → retailer_aliases`
- **`retailer_aliases`** (lue) — lookup insensible à la casse pour résoudre `enseigne → retailers.id`

## Dépendances internes (autres services/libs ratis)

- **ratis-core** — `make_engine`, `load_settings`, `require_env`, `startup`
- **ratis-batch-shared** (`batch/_shared/`) — `store_consolidation.upsert_store()` + `trust_priority()` (helper PR2, partagé avec `ratis_batch_osm_sync` V2)

## Dépendances externes (tiers)

- **INSEE / data.gouv.fr** — `StockEtablissement_utf8.zip` (bulk SIRENE, mise à jour mensuelle par l'INSEE). URL : `https://files.data.gouv.fr/insee-sirene/StockEtablissement_utf8.zip`
- **Géoplateforme IGN (BAN)** — `https://data.geopf.fr/geocodage/search/csv` — batch geocoding FR (successeur de `api-adresse.data.gouv.fr`, décommissionné 2026-01). Sans authentification, respecter les quotas (throttle + retry exponentiel via `tenacity`).

## Décisions d'architecture clés

### DA-01 — Batch séparé d'OSM, non intégré dans `ratis_batch_osm_sync`

**Choix** : nouveau package `ratis_batch_sirene_sync` indépendant.
**Alternative rejetée** : fusionner la logique SIRENE dans `ratis_batch_osm_sync`.
**Raison** : Les cycles d'exécution sont différents (mensuel SIRENE vs hebdomadaire OSM). Les dépendances divergent (pyarrow pour Parquet, osmium pour PBF). La séparation permet de déployer, monitorer et rollback chaque batch indépendamment. Partager du code via `batch/_shared/store_consolidation.py` (DA-02) suffit pour éviter la duplication sans coupler les batches.

### DA-02 — Helper partagé `batch/_shared/store_consolidation.py` (PR2)

**Choix** : fonctions pures importées depuis `ratis-batch-shared` (workspace member).
**Alternative rejetée** : copier-coller la logique dans chaque batch.
**Raison** : La hiérarchie de confiance (`trust_priority()`), le dedup spatial (`dedup_radius_m`) et le mécanisme de résolution retailer sont communs à SIRENE et OSM. Un seul point de vérité évite la divergence silencieuse. Les fonctions pures sont plus faciles à tester unitairement sans DB.

### DA-03 — Parquet bulk Géofabrik vs API SIRENE en ligne

**Choix** : télécharger le ZIP bulk SIRENE mensuel (Parquet), le décompresser, streamer avec `pyarrow`.
**Alternative rejetée** : API SIRENE en ligne (rate-limited, ~12 M établissements = impraticable en delta).
**Raison** : Le ZIP bulk est le seul moyen raisonnable pour traiter 12 M lignes avec un Mac mini. Cache local (TTL 30 jours) évite de re-télécharger un fichier de ~1 Go à chaque run mensuel. `pyarrow` permet de filtrer par colonne APE sans charger tout le fichier en mémoire.

### DA-04 — Geocoding bulk Géoplateforme, pas Google/Nominatim

**Choix** : `data.geopf.fr/geocodage/search/csv` (Géoplateforme IGN, gratuit, sans clé API).
**Alternative rejetée** : Google Maps Geocoding API (coût), Nominatim/OSM (ToS forbid bulk).
**Raison** : La Géoplateforme est le successeur officiel de la BAN (api-adresse.data.gouv.fr, décommissionné 2026-01). Elle est gratuite, sans authentification pour les usages publics, et couvre l'intégralité du territoire FR. Cache `sirene_geocode_cache` (TTL 90 jours) réduit les appels répétés entre runs mensuels.

## Flow principal

### Flow SIRENE → stores (chemin normal PR6)

1. `sirene_sync.main()` appelle `require_env("DATABASE_URL")` + charge `ratis_settings["sirene_sync"]`
2. Vérifie le cache local Parquet (`SIRENE_BULK_CACHE_DIR`) — si absent ou expiré (`bulk_cache_ttl_days`), télécharge le ZIP via `httpx` avec retry `tenacity`
3. Décompresse le ZIP → Parquet (ou lit directement si déjà Parquet en cache)
4. Streame le Parquet en chunks (`batch_chunk_size=5000`) avec `pyarrow`, filtre `etatAdministratif='A'` + `activitePrincipaleEtablissement` dans `ape_whitelist`
5. Pour chaque chunk : vérifie `sirene_geocode_cache` — appelle `data.geopf.fr/geocodage/search/csv` pour les adresses non-cachées, stocke le résultat + score
6. Rejette les géocodages sous `geocode_min_score=0.7`
7. Upsert via `batch_shared.store_consolidation.upsert_store()` (source='sirene', trust_priority résolu)
8. Marque `is_disabled=true` les SIRETs qui n'apparaissent plus dans le nouveau bulk (`etatAdministratif='F'` ou absents)
9. Log stats (inserted / updated / disabled / skipped_geocode / skipped_admin_protected)

### Flow --geocode-only (PR5)

1. Lit `sirene_geocode_cache` où `lat IS NULL AND failed_at IS NULL`
2. Envoie batch vers Géoplateforme, met à jour le cache
3. Réessaie les adresses échouées (`failed_at`) si TTL dépassé

## Paramètres (ratis_settings.json section `sirene_sync`)

```json
{
  "sirene_sync": {
    "ape_whitelist": ["47.11A", "47.11B", "47.11C", "47.11D", "47.11E", "47.11F",
                      "47.21Z", "47.22Z", "47.23Z", "47.24Z", "47.25Z", "47.29Z", "47.81Z"],
    "geocode_min_score": 0.7,
    "fuzzy_threshold": 0.85,
    "dedup_radius_m": 50,
    "bulk_cache_ttl_days": 30,
    "geocode_cache_ttl_days": 90,
    "batch_chunk_size": 5000
  }
}
```

- `ape_whitelist` : codes APE NAF retenus (supérettes, supermarchés, épiceries, boulangeries, boucheries, poissonneries, fromageries, cavistes, marchés). Voir annexe spec SIRENE pour la liste complète.
- `geocode_min_score` : score de confiance minimum retourné par la Géoplateforme (0–1) pour accepter un géocodage.
- `fuzzy_threshold` : seuil de similarité pour le matching fuzzy nom enseigne → `retailer_aliases` (Jaro-Winkler ou Levenshtein, cf `batch_shared`).
- `dedup_radius_m` : rayon de déduplication spatial (en mètres) — si un store SIRENE est à <50 m d'un store `source='admin'`, on préserve l'admin.
- `bulk_cache_ttl_days` : durée de validité du cache ZIP/Parquet SIRENE local (30 jours = 1 cycle mensuel).
- `geocode_cache_ttl_days` : durée de validité d'une entrée `sirene_geocode_cache` (90 jours).
- `batch_chunk_size` : nombre d'établissements traités par chunk Parquet (mémoire vs vitesse).

## Implementation checklist

- [x] **PR1** — Schema migration : `stores.source` CHECK extension + `ix_stores_siret_lookup` + `sirene_geocode_cache` table + model sync
- [x] **PR2** — Shared helper `batch/_shared/store_consolidation.py` (pure functions + tests, non câblé)
- [x] **PR3** — Squelette `ratis_batch_sirene_sync` : pyproject + Dockerfile + GH Actions (cron commenté) + entrypoint stub + ARCH + settings
- [x] **PR4** — SIRENE parser + filter : download ZIP → unzip Parquet + filtre APE + filtre `etatAdministratif='A'`
- [x] **PR5** — Geocoding : appel bulk Géoplateforme `search/csv` + `sirene_geocode_cache` lookup + retry `tenacity`
- [x] **PR6** — Pipeline complet : download → filter → geocode → upsert via `store_consolidation`, activation cron `0 4 1 * *`
- [ ] **PR7** — Adapter `ratis_batch_osm_sync` pour utiliser `batch/_shared/store_consolidation.py` (no-régression tests existants)


## Décisions prises en PR6

### DA-05 — Safety net F-14 : seuil >10% candidats fermés = abort

**Choix** : si le ratio `is_disabled` dans le batch courant dépasse 10%, lever `ValueError` avant tout write, log Sentry, exit 1.
**Raison** : l'audit 2026-05-10 (F-14) a identifié le risque qu'un changement de format INSEE sur `etatAdministratifEtablissement` marque tous les stores comme fermés. Sans filet de sécurité, le batch effacerait silencieusement tout le catalogue FR. Un seuil conservateur de 10% absorbe les fermetures légitimes d'un mois (<5% historiquement) tout en bloquant une dérive catastrophique.

### DA-06 — `upsert_candidates()` matérialise l'itérable

**Choix** : `list(candidates)` dans `upsert_candidates` pour compter `closed_count` avant tout write.
**Alternative rejetée** : passer en deux passes (comptage + upsert) sur l'itérable original.
**Raison** : les candidats SIRENE sont générés par un pipeline (parser → normalize → geocode). Matérialiser une fois est la seule option sans modifier l'API du pipeline ou introduire un buffer intermédiaire. Le coût mémoire est acceptable (chunk_size=5000 = ~5000 × ~200 bytes = ~1 MB par chunk).

### DA-07 — Un seul `db.commit()` dans `main()`

**Choix** : la session est ouverte une seule fois dans `main()`, et `db.commit()` est appelé une seule fois après `upsert_candidates()` réussi.
**Raison** : cohérence R-DB-02. Toutes les fonctions sous-jacentes (`apply_upsert`, `geocode_candidates`, `_upsert_cache`) font `db.flush()` mais pas `db.commit()`. Si une exception se produit, le rollback implicite à la sortie du `with Session() as db:` protège l'état de la DB.

## Note multi-country V3

SIRENE est exclusivement FR (SIRET = identifiant legal FR). Pour une extension internationale V3 (Belgique KBO, Allemagne Handelsregister, etc.), le pattern à suivre est :
- Paramétrer `source` et `legal_id_type` dans `store_consolidation.upsert_store()` (déjà prévu dans le design PR2)
- Créer un batch `ratis_batch_<country>_sync` séparé par pays — ne pas brancher sur `ratis_batch_sirene_sync`
- Le champ `stores.siret` est FR-only ; un futur `stores.legal_id + stores.country` remplacerait le SIRET pour l'international (décision V3, hors scope V1)
