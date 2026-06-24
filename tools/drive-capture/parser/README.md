# drive-capture parser — Phase 2

Transforme les captures brutes de **Phase 1** (`tools/drive-capture/`,
addon mitmproxy → NDJSON) en **observations de prix normalisées**, stockées
dans une base **SQLite indépendante** (`drive_prices.db`) — aucun couplage
avec Ratis (pas de Postgres, pas de schéma partagé).

Package **stdlib uniquement** (`json`, `sqlite3`, `dataclasses`, `re`,
`pathlib`, `argparse`) — aucune dépendance externe.

## Objectif

Phase 1 capture *tout* (JSON d'API + pages HTML server-rendered). Phase 2
extrait, par enseigne, le tuple utile : `(EAN, prix, magasin, rayon, …)`.
**7 enseignes** sont couvertes (cf tableau plus bas) ; l'infrastructure
(`model`, `pricing`, `db`, `capture`, `__main__`) est partagée par tous
les parsers, ajoutés en drop-in.

## Structure

```
parser/
  model.py            dataclasses normalisées : ParsedProduct, ParsedStore
  pricing.py          to_cents() / promo_pct() — argent = int-centimes
  capture.py          lecture NDJSON en streaming + extraction __INITIAL_STATE__
  db.py               couche SQLite : connect / init_schema / insert / upsert
  __main__.py         CLI : python -m parser <enseigne> <capture> --db ...
  enseignes/
    _catalog_api.py   base mutualisée « groupe Casino » (Franprix, Casino)
    _schemaorg.py     base mutualisée schema.org (Auchan, Système U)
    carrefour.py leclerc.py intermarche.py auchan.py
    systeme_u.py franprix.py casino.py     un parser drop-in par enseigne
  tests/              tests pytest (pricing en TDD, db, parsers vs vrais fichiers)
```

## Flux

```
capture .ndjson  ──▶  enseignes/<x>.parse_products()  ──▶  [ParsedProduct]
(Phase 1)             enseignes/<x>.parse_stores()    ──▶  [ParsedStore]
                                  │
                                  ▼
                      db.insert_observations()  (append-only)
                      db.upsert_stores()         (upsert)
                                  │
                                  ▼
                      drive_prices.db  +  <enseigne>.normalized.ndjson
```

## Schéma SQLite

| Table          | Clé                          | Sémantique |
|----------------|------------------------------|------------|
| `observations` | `id` auto-incrément          | **Append-only** — historique de prix : chaque passe de capture ajoute des lignes horodatées (`captured_at` repris de la capture, `parsed_at` = moment du parse). |
| `stores`       | `(enseigne, store_ref)`      | **Upsert** — dernier état connu du point de retrait drive. |

Colonnes `observations` : copie des champs de `ParsedProduct` (`enseigne`,
`name`, `ean`, `brand`, `quantity`, `category`, `price_cents`,
`price_per_measure_cents`, `measure_unit`, `promo_price_cents`,
`promo_pct`, `is_promo`, `product_url`, `image_url`, `available`,
`store_ref`, `enseigne_product_id`, `captured_at`) + `id`, `parsed_at`.
Tous les montants sont des **entiers en centimes**.

## Usage

```bash
cd tools/drive-capture
python -m parser carrefour captures/<session>/www.carrefour.fr.ndjson \
  --db drive_prices.db
```

Sortie : compteurs (observations / magasins), `drive_prices.db` alimentée,
et un `carrefour.normalized.ndjson` intermédiaire (1 ligne par objet
normalisé, `_kind` = `store` | `observation`) pour relecture humaine.

## Tests

```bash
# depuis la racine du repo
uv run python -m pytest tools/drive-capture/parser/tests/ -q
```

`test_pricing.py` est écrit en TDD (helper pur). `test_carrefour.py`
valide le parser contre le **vrai** fichier de capture (skippé si la
donnée — gitignored — est absente). `test_db.py` couvre la couche SQLite.

## Format par enseigne

8 enseignes ont été mappées. **7 parsers sont livrés** ; Monoprix est
différé (l'EAN est absent des données capturées — cf Notes).

| Enseigne   | Statut   | Source des produits | Source des magasins | Notes |
|------------|----------|---------------------|---------------------|-------|
| Carrefour  | ✅ pilote | SPA Vue : objets `{type:"product", attributes:{…}}` trouvés (a) dans `window.__INITIAL_STATE__` du HTML, sous `vuex.analytics.indexedEntities.product`, et (b) dans les réponses JSON d'API (`/api/recommendations`, sous `data[].attributes.products[]`). Prix dans `attributes.offers[ean][offerServiceId].attributes.price`. | Endpoint `/api/eligibility/drive` — champs `ref`, `name`, `address` (`city`, `postalCode`, `geoCoordinates`). | `store_ref` = dernier segment de `offerServiceId` (`7850-150-1323` → `1323`). `cdbase` = id interne produit → `enseigne_product_id`. Les listings de rayon chargent leurs produits en async : la page SSR a `search.data` vide → les produits ne viennent que des pages PDP et des zones de reco. Promotions du panel observé = fidélité/club uniquement (`isPromo:false`) → `is_promo=false` ; un vrai prix barré nécessiterait `isPromo`/`isPrixBarre` à `true`. |
| Leclerc    | ✅ | JSON `objElement` (rayon) / `objProduit` (fiche) embarqué dans le HTML `.aspx`, bloc `lstElements`. Prix `nrPVUnitaireTTC`. | Fichier voisin `api-recherchemagasins.leclercdrive.fr.ndjson` — endpoint `MapPoint` (~1012, ~952 `noPL` uniques). | EAN absent du rayon → joint depuis les fiches produit (`sCodeEAN`) via `iIdProduit`. `brand=None` (marque noyée dans `sLibelleLigne2`). |
| Intermarché| ✅ | Next.js App Router — payload RSC : chunks `self.__next_f.push([…])` concaténés, objets produit JSON extraits par balance-scan d'accolades. | ❌ aucune liste capturée. | EAN dans l'URL produit `/produit/[slug]/[EAN-13]`. `store_ref` = `store_id_itm`. |
| Auchan     | ✅ | HTML server-side + microdata schema.org ; tuiles de rayon. | Double source : (1) contexte `/journey` (GROCERY) — magasin courant avec coords GPS ; (2) annuaire national `/nos-magasins?types=*` — 6 formats (HYPER/SUPER/DRIVE/PICKUP_POINT/LOCKERS/PROXY), ~961 magasins. `store_ref` = `s-NNNN` pour l'annuaire ; espace de nommage distinct du contexte journey (numérique pur). Pas de GPS dans l'annuaire (adresse texte seule — géocodage possible en phase 3). | EAN seulement sur les pages détail (`/pr-C…`), joint par id `pr-C<id>`. HTML annuaire doublement échappé → unescape en boucle (2 passes) avant parsing. |
| Système U  | ✅ | Demandware — attribut `data-tc-product-tile` JSON sur les tuiles de rayon (id, name, EAN, brand, price) ; ld+json détail en fallback. | Annuaire national `/annuaire-magasin` dans le fichier voisin `www.magasins-u.com.ndjson` — ~1389 magasins (Hyper U / Super U / U Express / Utile). `store_ref` = slug d'URL (dernier segment après `/magasin/`). Pas de CP ni GPS dans l'annuaire (adresse absente — limitation). | `store_id` numérique réel (placeholder `seo-store` filtré). |
| Franprix   | ✅ | API JSON `/catalog-api/rest/api/promotion/by_department` — `items[].promotions[].product` (base mutualisée `_catalog_api.py`). | Endpoint `/api/store` (~901 magasins : `storeId`, lat, lng). | EAN présent. `discountFid` = remise fidélité intégrée à la promo. |
| Casino     | ✅ | `www.mescoursesdeproximite.com` — schema.org ld+json (`@type: Product`) dans les fiches `/produit/`. Prix toujours présent dans `offers.price`. EAN via `gtin13`. Couvre les 10 sous-enseignes du groupe (Petit Casino, Spar, Vival, Casino Shop, Casino Hypermarché, Géant Casino…). Les pages rayon `/famille/` ne contiennent qu'un bloc `LocalBusiness` (pas de tuile produit structurée) — seules les fiches détail contribuent des observations. | Bloc `@type: LocalBusiness` ld+json présent sur chaque page HTML (fiches produit, pages rayon, pages magasin). Contient nom, adresse complète et coordonnées GPS. `store_ref` = code magasin (`C1507`) extrait du champ `@id`. | Remplacement de l'ancienne source `casino.fr/catalog-api` qui n'exposait pas les prix. `store_ref` extrait depuis le champ `@id` du bloc `Product` (`.../C1507/174353#product`) — source la plus fiable et sans ambiguïté. |
| Monoprix   | ⬜ différé | API `/api/webproductpagews` — `productGroups[].decoratedProducts[]`. | — | **EAN absent** des données capturées (ni API, ni fiche) → à résoudre avant d'écrire le parser : capture d'un endpoint détail, ou matching nom/marque contre Open Food Facts. |

## Ajouter un parser d'enseigne

Les parsers sont **drop-in** : aucun registre central à éditer.

1. Créer `enseignes/<nom>.py` exposant `parse_products(ndjson_path) ->
   Iterator[ParsedProduct]` et `parse_stores(ndjson_path) ->
   Iterator[ParsedStore]`.
2. Réutiliser `capture.iter_records` / `capture.extract_initial_state` et
   `pricing.to_cents` / `pricing.promo_pct` — ne pas réinventer.
3. C'est tout : `python -m parser <nom> <capture>` charge le module par son
   nom via `importlib`. Une enseigne inconnue → message d'erreur listant
   les enseignes disponibles.
4. Ajouter un test contre un vrai fichier de capture et compléter la ligne
   du tableau « Format par enseigne » ci-dessus.

## Logging

Le pipeline log toute la route au niveau `INFO` (stderr) : démarrage,
réponses NDJSON lues, records produit / EAN / `ParsedProduct` extraits /
ignorés (avec motif), chargement SQLite, résumé final. `-v` / `--verbose`
passe en `DEBUG`.
