---
type: batch-global
service: ratis_batch_consensus
status: production
parent: ARCH_RATIS
sub_archs: []
related: [ARCH_PRODUCT_ANALYSER, ARCH_consensus]
tech: [Python, SQLAlchemy, Postgres, ThreadPoolExecutor]
tables: [price_consensus, price_consensus_scans, price_consensus_history, scans, batch_sync_log]
env_vars: [DATABASE_URL]
tags: [batch, pricing, consensus]
business_domain: pricing
rgpd_concern: false
updated: 2026-04-24
---

# ratis_batch_consensus — recalcul trust_score

> Batch CLI quotidien qui recalcule le `trust_score` des lignes `price_consensus`, détecte Pattern A (OCR aberrant) + Pattern B (nouveaux prix), applique decay temporel et dégèle les consensus `frozen_until` échus.
> @tags: batch consensus pricing trust-score ocr-pattern decay frozen price_consensus
> @status: LIVRÉ V0
> @subs: auto

> [[ARCH_RATIS]] · relations : [[ARCH_PRODUCT_ANALYSER]], [[ARCH_consensus]]

## Index

- [Résumé en une phrase](#résumé-en-une-phrase) · L.38
- [Responsabilité](#responsabilité) · L.42
- [Fréquence d'exécution](#fréquence-dexécution) · L.51
- [Tables lues / écrites](#tables-lues-écrites) · L.57
- [Dépendances internes (autres services/libs ratis)](#dépendances-internes-autres-serviceslibs-ratis) · L.67
- [Dépendances externes (tiers)](#dépendances-externes-tiers) · L.72
- [Décisions d'architecture clés](#décisions-darchitecture-clés) · L.76
- [Flow principal](#flow-principal) · L.96
- [Paramètres (ratis_settings.json section `consensus`)](#paramètres-ratis_settingsjson-section-consensus) · L.122
- [Monitoring / logs](#monitoring-logs) · L.143
- [FAQ vectorisée](#faq-vectorisée) · L.149
- [Glossaire](#glossaire) · L.167

---

## Résumé en une phrase

ratis_batch_consensus est un batch CLI quotidien qui recalcule le `trust_score` de chaque ligne `price_consensus`, détecte les patterns aberrants d'OCR (Pattern A) et les émergences de nouveaux prix (Pattern B), applique un decay temporel sur les consensus inactifs, et dégèle les consensus dont le `frozen_until` est échu.

## Responsabilité

- ratis_batch_consensus itère sur toutes les lignes de `price_consensus` et met à jour `trust_score` + `computed_at`
- ratis_batch_consensus neutralise (poids → 0) les scans isolés encadrés de scans concordants (Pattern A : erreur OCR probable)
- ratis_batch_consensus détecte les N derniers scans consécutifs à un nouveau prix divergent et réduit le poids des anciens concordants (Pattern B : prix émergent)
- ratis_batch_consensus détecte les basculements de prix (dominant_price ≠ consensus.price avec score supérieur) et crée une ligne dans `price_consensus_history` + met à jour `price_consensus.price`
- ratis_batch_consensus applique un decay temporel (`trust_score` réduit) quand un consensus n'a pas reçu de nouveau scan depuis plus de `decay_grace_days`
- ratis_batch_consensus dégèle les `frozen_until` échus (la mise en gel, elle, est déclenchée en live par les routes de scan, pas par ce batch)

## Fréquence d'exécution

- **Workflow GitHub Actions** : `.github/workflows/batch_consensus.yml`
- **Cron** : `0 2 * * *` (quotidien 02h00 UTC) — actuellement désactivé (commenté) en mode DB locale
- **Déclenchement manuel** : `workflow_dispatch` toujours disponible

## Tables lues / écrites

| Table | Lecture | Écriture |
|---|---|---|
| `price_consensus` | tous les ids | `trust_score`, `computed_at`, `frozen_until`, `price` (sur basculement), `first_seen_at` (sur basculement) |
| `price_consensus_scans` | jointure pour récupérer la fenêtre de scans | — |
| `scans` | `price`, `scanned_at` dans la fenêtre | — |
| `price_consensus_history` | — | INSERT sur basculement (snapshot ancien prix) |
| `stores` | `id`, `validation_status`, `created_at` (Phase 3) | `validation_status` (`pending`→`confirmed` ou `pending`→`suspicious`) en Phase 3 |
| `store_validation_history` | — | INSERT audit row par flip Phase 3 (avec `meta` JSONB) |
| `batch_sync_log` | — | INSERT run success/failed avec `rows_affected` |

## Dépendances internes (autres services/libs ratis)

- [[ARCH_CORE]] — `make_engine`, `load_settings`, modèles SQLAlchemy `PriceConsensus`, `PriceConsensusScans`, `PriceConsensusHistory`, `Scan`
- [[ARCH_PRODUCT_ANALYSER]] — source des `scans` consommés par le batch (écrit par le pipeline OCR)

## Dépendances externes (tiers)

- Aucune — ratis_batch_consensus tourne 100% en local contre la DB Postgres.

## Décisions d'architecture clés

### DA-01 — Parallélisation via ThreadPoolExecutor, pas multiprocessing

**Choix** : ThreadPoolExecutor + session dédiée par chunk
**Alternative rejetée** : ProcessPoolExecutor
**Raison** : le travail est largement I/O-bound (requêtes DB), GIL n'est pas un problème. ThreadPool partage le pool de connexions SQLAlchemy sans sérialisation inter-process. Chunks configurables via `batch_chunk_size` et `batch_max_workers`.

### DA-02 — Une transaction par consensus

**Choix** : chaque `process_consensus()` ouvre sa propre session + commit individuel
**Alternative rejetée** : transaction globale sur tout le batch
**Raison** : dans ratis_batch_consensus, une erreur sur une ligne (DB race, donnée corrompue) ne doit pas rollback tout le batch. On log l'erreur, on continue, on compte les échecs à la fin pour écrire le status final dans `batch_sync_log`.

### DA-03 — Tous les paramètres dans ratis_settings.json

**Choix** : pas une seule constante numérique dans le code
**Alternative rejetée** : defaults Python + overrides optionnels
**Raison** : R19 — le batch doit être tunable sans redéploiement. Les clés requises sont validées au démarrage (`_REQUIRED_KEYS`), fail-fast si manquantes.

## Flow principal

### Flow 1 — Recalcul d'un consensus

1. Si `frozen_until > now()` → skip (retour `"frozen"`)
2. Si `frozen_until ≤ now()` → unfreeze (`frozen_until = NULL`)
3. Récupère la fenêtre : `window_size` scans les plus récents via JOIN `price_consensus_scans` → `scans`
4. Calcule le poids de base de chaque scan : `max(scan_weight_floor, 1.0 - age_days × scan_weight_decay_per_day)`
5. Applique Pattern A (neutralise les scans isolés entre 2 concordants de chaque côté)
6. Applique Pattern B (réduit le poids des anciens concordants si les N derniers sont à un nouveau prix consécutif)
7. Calcule trust_score (somme pondérée concordante / total) et dominant_price (prix ayant la plus grosse part pondérée)
8. Si dominant_price ≠ consensus.price ET dominant_score > trust_score → basculement : INSERT history + UPDATE price_consensus.price
9. Applique decay si `days_inactive > decay_grace_days` : `max(decay_floor, trust_score - (excess × decay_rate_pct))`
10. Persist : `trust_score`, `computed_at`, `frozen_until` (sur update), puis commit

### Flow 2 — Orchestration globale

1. `main()` parse `--dry-run`
2. Charge `settings.consensus` + `settings.store_validation`, valide toutes les clés requises (fail-fast)
3. Crée engine + sessionmaker
4. **Phase 1+2** (existant — `recalc_phase`) : `run_batch()` récupère tous les ids `price_consensus`, les split en chunks. ThreadPoolExecutor submit 1 future par chunk — chaque chunk process ses consensus en séquence (Flow 1).
5. **Phase 3** (NEW depuis PR-B — `store_validation_phase`) : flip pending→confirmed/suspicious + retroactive cashback (Flow 3 ci-dessous). Transaction séparée — un fail Phase 3 ne rollback PAS Phase 1+2.
6. Agrège les stats (`updated`, `basculement`, `frozen` pour Phase 1+2 ; `flipped_confirmed`, `flipped_suspicious`, `retroactive_cashback_calls` pour Phase 3) et la liste d'erreurs
7. Écrit une ligne dans `batch_sync_log` avec status `success`/`failed` et `rows_processed`
8. Exit code 1 si erreurs, 0 sinon

### Flow 3 — Store validation (Phase 3, depuis PR-B)

> Détail complet dans [[ARCH_store_validation]] § "Phase 3 de `ratis_batch_consensus`".

1. **Sub-phase 3.1 (auto-validation)** : pour chaque store `validation_status='pending'`, count `DISTINCT product_ean` dans `price_consensus` avec `trust_score >= consensus_min_trust_score (=80)`. Si `≥ min_distinct_eans_for_validation (=20)` → flip `confirmed` + audit row dans `store_validation_history` + appel HTTP fire-and-forget `POST /rewards/cashback/process-retroactive`.
2. **Sub-phase 3.2 (auto-suspicious)** : pour chaque store `pending` créé `≥ suspicious_after_months (=6 mois)`, recompute count distinct EAN. Si `< suspicious_threshold_eans (=30)` → flip `suspicious` + audit row.
3. Phase 3 commit par store flip (transactions isolées). Exception cashback retroactive loggée mais batch continue.

## Paramètres (ratis_settings.json section `consensus`)

```json
"consensus": {
  "window_size": 20,
  "scan_weight_decay_per_day": 0.10,
  "scan_weight_floor": 0.30,
  "freeze_threshold_scans": 3,
  "freeze_duration_hours": 24,
  "decay_grace_days": 5,
  "decay_rate_pct": 10,
  "decay_floor": 30,
  "emerging_consecutive_threshold": 4,
  "emerging_old_weight": 0.15,
  "batch_chunk_size": 100,
  "batch_max_workers": 4
}
```

`freeze_threshold_scans` et `freeze_duration_hours` sont consommés par les routes de scan (mise en gel live), pas par le batch — il s'agit de partage de config section.

## Monitoring / logs

- Stdout JSON-friendly : `"%(asctime)s %(levelname)s %(message)s"`
- Compteurs finaux loggés : `N updated, M basculements, K frozen skipped, E errors`
- `batch_sync_log(batch_name='consensus', status, rows_affected)` persistant pour suivi des runs

## FAQ vectorisée

### Pourquoi ratis_batch_consensus ne met-il pas à jour `frozen_until` pour geler un consensus ?

Dans ratis_batch_consensus, le gel est une décision **live** prise par les routes de scan : 3 scans concordants dans une même journée déclenchent `frozen_until = now() + freeze_duration_hours`. Le batch se contente de détecter l'expiration et de remettre `frozen_until = NULL`. Ça évite des incohérences entre le comportement temps-réel et la convergence quotidienne.

### Quelle différence entre Pattern A et Pattern B dans ratis_batch_consensus ?

Pattern A neutralise un **scan isolé** (probable erreur OCR) dont le prix diffère mais qui est encadré de 2 scans concordants de chaque côté — son poids passe à 0. Pattern B détecte un **vrai changement de prix** : si les N derniers scans sont tous au même nouveau prix divergent, on réduit le poids des anciens concordants pour accélérer le basculement. A = bruit, B = signal.

### Comment tester ratis_batch_consensus localement ?

Depuis la racine du repo : `uv run pytest batch/ratis_batch_consensus/tests/ -v`. Les tests utilisent la DB `ratis_test` (conftest.py dédié). Pour un dry-run manuel : `uv run python batch/ratis_batch_consensus/consensus.py --dry-run` contre une DB peuplée (logs les basculements/updates sans commit).

### Que se passe-t-il si un consensus a une fenêtre vide ?

Dans ratis_batch_consensus, si `price_consensus_scans` ne renvoie rien pour un consensus (cas anormal : consensus créé sans scan rattaché, ou scans tous purgés), on logge un warning, on met `computed_at = now` pour tracer le passage du batch, et on continue. Pas d'erreur.

## Glossaire

- **DA-XX** : décision d'architecture numérotée
- **Pattern A** : neutralisation d'un scan isolé (bruit OCR)
- **Pattern B** : accélération du basculement vers un prix émergent
- **Basculement** : changement du prix consensus d'un produit/magasin vers un nouveau prix dominant
- **Decay** : décroissance linéaire du `trust_score` pour les consensus inactifs > `decay_grace_days`
- **Fenêtre** : les N derniers scans (`window_size`) pris en compte pour le recalcul
