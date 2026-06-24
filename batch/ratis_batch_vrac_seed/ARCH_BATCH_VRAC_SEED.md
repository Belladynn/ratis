---
type: batch-global
service: ratis_batch_vrac_seed
status: production
parent: ARCH_RATIS
sub_archs: []
related: [ARCH_PRODUCT_ANALYSER]
tech: [Python, SQLAlchemy, Postgres]
tables: [products, batch_sync_log]
env_vars: [DATABASE_URL]
tags: [batch, seed, vrac, ocr-matching]
business_domain: ocr-matching
rgpd_concern: false
updated: 2026-04-29
---

# ratis_batch_vrac_seed — one-shot seed of generic bulk-produce products

> Script CLI **one-shot** qui ensemence `products` avec ~65 entrées canoniques de vrac FR (fruits, légumes, vracs secs) — EAN interne `2999000000NNN`, `source='internal'`, `unit='kg'`. Cible le matching ticket vrac sans code-barres.
> @tags: batch seed vrac bulk-produce ocr-matching one-shot products internal ean kg fruits-legumes
> @status: LIVRÉ V0
> @subs: auto

> [[ARCH_RATIS]] · related : [[ARCH_PRODUCT_ANALYSER]]

## Résumé

Script CLI **one-shot** (pas de cron) qui ensemence la table `products` avec
~65 entrées canoniques de vracs FR (fruits, légumes, vracs secs). Chaque entrée
a un EAN interne `2999000000NNN`, `source='internal'`, `unit='kg'`.

## Pourquoi

Quand un user scanne un ticket avec un vrac (ex. `POMMES VRAC 1.234kg 3.45€`),
il n'y a pas d'EAN sur le ticket. Le pipeline de matching tente :

1. `barcode_ean` → fail (pas d'EAN imprimé)
2. `fuzzy_match` → match contre `products.name`

Sans seed, le step 2 fail aussi : la DB ne contient quasi-aucune entrée
générique vrac (OFF index les produits packagés EAN-13). Tout vrac termine
🟠 fuzzy ou 🔴 unmatched, dégradant l'expérience alpha.

Avec seed, ~65 noms canoniques sont disponibles → la majorité des tickets
vracs FR matchent.

## Quand l'exécuter

- **1 seul run** sur prod après merge.
- Trigger manuel via GitHub Actions `workflow_dispatch` (`.github/workflows/batch_vrac_seed.yml`).
- **Pas de cron** — re-runs sont safes (idempotent) mais inutiles.

## Décisions

- **DA-1 — Format EAN `2999000000NNN`** : la contrainte `ean ~ '^\d{8,14}$'`
  + `internal_ean_prefix` (CHECK `ean LIKE '2%'`) interdit `INT-VRAC-XXXX`.
  Choix : 13 digits, préfixe `2` (norme GS1 in-store), `999000000` namespace
  Ratis-vrac, `NNN` séquentiel. Capacité 999 entrées V1, extensible via
  ajout d'un 2e namespace si besoin.
- **DA-2 — Idempotence via `ON CONFLICT (ean) DO NOTHING`** : re-run sans
  danger, ne réécrit pas les noms d'entrées déjà présentes (préserve les
  enrichissements manuels post-seed).
- **DA-3 — `unit='kg'` partout** : les vracs OCR'd sont quasi tous en kg
  (`1.234kg`). AVOCATS / ANANAS qui sont parfois vendus à la pièce restent
  `kg` — le matching est name-driven (fuzzy), `unit` ne sert qu'à la
  normalisation consensus.
- **DA-4 — `category_id=NULL`** : pas de cross-référence vers `categories`
  car les UUIDs prod ne sont pas connus au seed time. Une enrichment V2
  pourra mapper via le tag informatif présent dans `seed_data.py`
  (`FRUITS` / `LEGUMES` / `EPICERIE`).
- **DA-5 — Names en MAJUSCULES sans accents** : reflète la sortie typique
  d'OCR de tickets de caisse FR (UPPER + ASCII). Maximise la similarité
  fuzzy avec ce que l'OCR produit.
- **DA-6 — Ordre stable** : `_VRAC_NAMES` dans `seed_data.py` est la
  source de vérité pour l'attribution d'EAN. **Ne jamais réordonner**
  une fois shippé en prod ; uniquement append.

## Limites V1

- Couvre les vracs **courants FR** uniquement. Vracs régionaux ou exotiques
  (`RUTABAGA`, `TOPINAMBOUR`, `CHOU ROMANESCO`) → miss → fuzzy faible.
- Pas de variantes BIO systématiques (sauf `BANANES BIO`) — la convention
  d'écriture varie trop entre enseignes.
- Catégorie persistée NULL → enrichment manuel ou batch ultérieur requis
  pour navigation taxonomique.

## V2 (hors scope)

- Batch d'enrichissement automatique via OFF + crowdsourcing
  `user_suggested` pour étendre la couverture vracs.
- Mapping `category_id` via résolution du tag informatif
  (`FRUITS` → `categories.id` correspondant).
- Internationalisation (`source='internal'` est FR-only V1).

## Tables touchées

| Table | Lecture | Écriture |
|---|---|---|
| `products` | check existence (CONFLICT) | INSERT (idempotent) |
| `batch_sync_log` | — | 1 ligne par run (status, rows_affected) |

## Usage

```bash
# Dry-run (recommandé first) :
uv run python batch/ratis_batch_vrac_seed/vrac_seed.py --dry-run

# Commit :
uv run python batch/ratis_batch_vrac_seed/vrac_seed.py
```

## Tests

- `tests/test_seed_data.py` — pure (pas de DB) : forme, unicité, contraintes.
- `tests/test_vrac_seed.py` — intégration : insert, idempotence, skip,
  préservation des rows existantes.
