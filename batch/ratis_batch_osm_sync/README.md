# ratis_batch_osm_sync

Peuplement de la table `stores` (+ `cities`, `retailers`, `retailer_aliases`)
depuis OpenStreetMap.

Deux chemins coexistent :

| Chemin | Script | Usage |
|---|---|---|
| **PBF streaming** (DA-36, V1 principal) | `osm_bulk_import.py` | Bulk France entière + refresh hebdo via diffs Geofabrik |
| Overpass JSON (legacy) | `osm_sync.py` | Requêtes ciblées, zones limitées, debug |

Les deux partagent `normalize.py` (résolution retailer, normalisation tags).

## 1. Bulk import PBF — setup

### a. Télécharger le PBF initial (une fois)

Depuis [Geofabrik France](https://download.geofabrik.de/europe/france.html) :

```bash
cd batch/ratis_batch_osm_sync/data/
curl -O https://download.geofabrik.de/europe/france-latest.osm.pbf
# ~4-5 GB, ~15-30 min selon la connexion
```

Le fichier est **gitignoré** — ne jamais commiter de PBF.

> **Windows** : si `curl` n'est pas dispo, utiliser
> `Invoke-WebRequest -Uri https://download.geofabrik.de/europe/france-latest.osm.pbf -OutFile france-latest.osm.pbf`.

### b. Dépendances

`osmium` (PyPI) est ajouté comme dépendance — `uv sync --all-groups --all-packages`
l'installe automatiquement. Le wheel embarque `libosmium` donc aucune
installation système requise (Linux / macOS / Windows).

Pour le refresh hebdo, l'outil CLI `pyosmium-up-to-date` est livré par le même
package. Vérifier sa présence :

```bash
which pyosmium-up-to-date   # ou: where pyosmium-up-to-date.exe
```

## 2. Bulk import — exécution manuelle

```bash
# Dry-run (aucune écriture DB, counts only)
uv run python batch/ratis_batch_osm_sync/osm_bulk_import.py \
    --pbf batch/ratis_batch_osm_sync/data/france-latest.osm.pbf \
    --dry-run

# Import réel, sans diff refresh, sans détection de fermetures
uv run python batch/ratis_batch_osm_sync/osm_bulk_import.py \
    --pbf batch/ratis_batch_osm_sync/data/france-latest.osm.pbf

# Import complet : refresh Geofabrik + détection de fermetures
uv run python batch/ratis_batch_osm_sync/osm_bulk_import.py \
    --pbf batch/ratis_batch_osm_sync/data/france-latest.osm.pbf \
    --update \
    --disable-missing
```

Durée attendue : **~1-2 h** pour le PBF France complet (mesure smoke test :
~25-30 shops/s sustained sur un laptop après fast-path C-level).
Empreinte mémoire : ~200 MB (streaming, jamais tout charger en RAM).

### Progression en temps réel

Le handler logue une ligne INFO toutes les 500 shop elements rencontrés :

```
progress: 500 shop elements seen, 465 kept, 122/s elapsed 4.1s
```

- `seen` : shops rencontrés (post-filtre `shop=*` C-level)
- `kept` : shops insérés/upsertés (après normalisation — `name` requis, pas de
  null island, etc.)
- rate `/s` : shops vus par seconde (wall-clock). Décroît naturellement au fil
  du PBF — dense en zones urbaines, sparse en zones rurales.

### Kill-safety

Chaque chunk (`batch_chunk_size=1000`) est committé indépendamment, et l'upsert
est idempotent via `ON CONFLICT (osm_id) DO UPDATE`. Kill → rerun sans état
corrompu. En `--dry-run`, rien n'est écrit (aucun rollback nécessaire).

### Optimisation V2 (optionnelle) — `osmium tags-filter`

Pour les runs de production (CI hebdo), on peut pré-filtrer le PBF au niveau
C++ avec `osmium tags-filter` (du package `osmium-tool` — non bundlé dans le
wheel Python). Cela réduit un PBF 5 GB à ~50 MB en ~30 s, et l'import tombe à
quelques minutes au lieu d'heures. Trade-off : dépendance système
supplémentaire. Non activé par défaut — le pipeline Python pur suffit pour la
V1 (tourne la nuit, pas sur le chemin critique user).

## 3. Refresh hebdomadaire (`--update`)

Lorsqu'on passe `--update`, le script invoque `pyosmium-up-to-date` **avant**
l'import :

1. Lit le header du PBF pour extraire sa `sequence_number` Geofabrik.
2. Télécharge les fichiers `.osc.gz` (diffs minutely/hourly/daily) émis
   depuis.
3. Les applique in-place sur le PBF local → le PBF est à jour jusqu'au dernier
   diff Geofabrik disponible.
4. L'import continue sur le PBF fraîchement refreshé.

Si l'outil est absent (`shutil.which` retourne `None`), le script loggue un
warning et continue sur le PBF existant (stale-but-usable — voir SESSION_LOG).

Le workflow `.github/workflows/batch_osm_bulk_sync.yml` peut être déclenché
manuellement (`workflow_dispatch`) avec les inputs `update_pbf`, `dry_run`,
`disable_missing`. Le cron hebdomadaire est commenté (DB locale).

## 4. `--disable-missing`

Lorsqu'activé, le script accumule pendant l'import l'ensemble des `osm_id`
rencontrés puis, une fois terminé, exécute :

```sql
UPDATE stores
SET is_disabled = true, disabled_at = NOW()
WHERE osm_id IS NOT NULL
  AND NOT is_disabled
  AND osm_id <> ALL(:seen_ids);
```

Cela détecte les fermetures de magasins (supprimés d'OSM) → flagués en soft
delete (jamais `DELETE` en prod — cf. `CLAUDE.md`).

## 5. Overpass legacy (`osm_sync.py`)

Toujours fonctionnel pour des zones ciblées :

```bash
OSM_OVERPASS_URL=https://overpass-api.de/api/interpreter \
uv run python batch/ratis_batch_osm_sync/osm_sync.py --dry-run
```

## 6. Décisions

Voir **DA-34** (normalisation retailers), **DA-35** (detection locale de
stores), **DA-36** (PBF bulk + weekly diff) dans `DECISIONS_ACTED.md`.
