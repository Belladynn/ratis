---
type: batch-global
service: ratis_batch_savings
status: production
parent: ARCH_RATIS
sub_archs: []
related: [ARCH_PRODUCT_ANALYSER, ARCH_REWARDS]
tech: [Python, SQLAlchemy, Postgres]
tables: [scans, user_savings_snapshot, batch_sync_log]
env_vars: [DATABASE_URL]
tags: [batch, savings, stats, snapshot]
business_domain: cashback
rgpd_concern: false
updated: 2026-04-24
---

# ratis_batch_savings — snapshot cumul d'économies user

> Batch CLI quotidien qui calcule le cumul d'économies par user (delta prix scan vs consensus) et stocke un snapshot dans `user_savings_snapshot`. Alimente les stats profil + dashboard mobile.
> @tags: batch savings stats snapshot user_savings_snapshot dashboard cab profil daily
> @status: LIVRÉ V0
> @subs: auto

> [[ARCH_RATIS]] · relations : [[ARCH_PRODUCT_ANALYSER]], [[ARCH_REWARDS]]

## Index

- [Résumé en une phrase](#résumé-en-une-phrase) · L.38
- [Responsabilité](#responsabilité) · L.42
- [Fréquence d'exécution](#fréquence-dexécution) · L.50
- [Tables lues / écrites](#tables-lues-écrites) · L.56
- [Dépendances internes (autres services/libs ratis)](#dépendances-internes-autres-serviceslibs-ratis) · L.65
- [Dépendances externes (tiers)](#dépendances-externes-tiers) · L.71
- [Décisions d'architecture clés](#décisions-darchitecture-clés) · L.75
- [Flow principal](#flow-principal) · L.101
- [Paramètres](#paramètres) · L.128
- [Monitoring / logs](#monitoring-logs) · L.133
- [FAQ vectorisée](#faq-vectorisée) · L.141
- [Glossaire](#glossaire) · L.163

---

## Résumé en une phrase

ratis_batch_savings est un batch CLI nocturne qui recalcule le `lifetime_savings_cents` de chaque user ayant au moins un scan receipt accepted, et upsert le résultat dans `user_savings_snapshot` — la hot-path `/account/stats` lit ensuite `snapshot.lifetime + live_delta` (scans depuis `last_computed_at`) pour un affichage sub-seconde.

## Responsabilité

- ratis_batch_savings énumère tous les users ayant au moins un scan `status='accepted' scan_type='receipt'` non-NULL user_id
- ratis_batch_savings appelle `ratis_core.savings.compute_savings_for_user(db, uid, since=None)` pour recalculer le lifetime total en cents (source unique de la formule, partagée entre online et offline)
- ratis_batch_savings UPSERT `user_savings_snapshot (user_id, lifetime_savings_cents, last_computed_at, updated_at)` avec `ON CONFLICT (user_id) DO UPDATE`
- ratis_batch_savings écrit le run dans `batch_sync_log` (`batch_name='savings_snapshot'`)
- ratis_batch_savings est **idempotent** : relancer le même run produit le même snapshot (pas de delta, recalcul complet)

## Fréquence d'exécution

- **Workflow GitHub Actions** : `.github/workflows/batch_savings.yml`
- **Cron** : `30 3 * * *` (quotidien 03h30 UTC) — actuellement désactivé (DB locale)
- **Déclenchement manuel** : `workflow_dispatch` (avec option `--dry-run`)

## Tables lues / écrites

| Table | Opération |
|---|---|
| `scans` | lecture DISTINCT user_id (WHERE status='accepted' AND scan_type='receipt' AND user_id IS NOT NULL) |
| (tables lues indirectement) | `compute_savings_for_user` joint scans + `price_consensus` / `products` pour comparer prix payé vs prix de référence |
| `user_savings_snapshot` | UPSERT (user_id PK, lifetime_savings_cents, last_computed_at, updated_at) |
| `batch_sync_log` | INSERT (`batch_name='savings_snapshot'`, status='success'/'failed') |

## Dépendances internes (autres services/libs ratis)

- [[ARCH_CORE]] — `make_engine`, `compute_savings_for_user` depuis `ratis_core.savings` (source unique de la formule d'économies)
- [[ARCH_PRODUCT_ANALYSER]] — produit les `scans` consommés par le calcul d'économies (OCR tickets acceptés)
- [[ARCH_REWARDS]] — la hot-path `/account/stats` (service ratis_auth ou ratis_rewards selon implem) lit `user_savings_snapshot` + applique un `live_delta` pour les scans récents

## Dépendances externes (tiers)

- Aucune — ratis_batch_savings est 100% DB local.

## Décisions d'architecture clés

### DA-01 — Formule partagée via `ratis_core.savings.compute_savings_for_user`

**Choix** : appel à la même fonction que le chemin live
**Alternative rejetée** : SQL ad-hoc dans le batch
**Raison** : **une seule source de vérité** pour la formule d'économies. Sinon drift garanti entre le batch (calcul historique) et le live-path (delta récent). La signature `compute_savings_for_user(db, uid, since=None)` permet au live de passer `since=snapshot.last_computed_at` pour ne calculer que le delta.

### DA-02 — Snapshot + live-delta pour la hot-path

**Choix** : `/account/stats` lit `snapshot.lifetime + compute_savings_for_user(db, uid, since=snapshot.last_computed_at)`
**Alternative rejetée** : calcul live complet à chaque GET
**Raison** : `compute_savings_for_user` scanne tous les receipts de l'user. Pour un user actif avec des milliers de scans, calculer à chaque ouverture de l'app = latence unacceptable (secondes). Snapshot + delta = O(1) pour l'historique + O(scans récents) pour le delta. Cette architecture a justifié l'existence du batch.

### DA-03 — Recalcul complet, pas de delta-patch

**Choix** : chaque run recalcule le lifetime depuis zéro pour chaque user éligible
**Alternative rejetée** : delta-patch (ajouter à `snapshot.lifetime` les savings des scans accepted depuis last_computed_at)
**Raison** : robustesse. Si des prix consensus ont évolué (basculements), si des scans ont été acceptés rétroactivement, si un bug live a sous-compté, le recalcul complet corrige tout. Coût : tous les users en une nuit = acceptable même à grande échelle (O(N users × scans/user)).

### DA-04 — UPSERT par user, pas bulk

**Choix** : INSERT ... ON CONFLICT (user_id) DO UPDATE un par un
**Alternative rejetée** : bulk UPSERT avec unnest
**Raison** : le coût est dominé par `compute_savings_for_user(db, uid)` (requête par user), pas par l'INSERT. Garder une boucle Python simple évite de complexifier pour un gain marginal.

## Flow principal

### Flow 1 — Recompute & snapshot

1. `main()` parse `--dry-run`, require `DATABASE_URL`, crée engine + Session
2. `recompute_all_user_snapshots(db, dry_run)` :
   - SELECT DISTINCT user_id FROM scans WHERE status='accepted' AND scan_type='receipt' AND user_id IS NOT NULL
   - Log : `N user(s) to process`
   - `now = datetime.now(timezone.utc)` capturé une fois
   - Pour chaque uid :
     - `lifetime = compute_savings_for_user(db, uid, since=None)` (full recompute)
     - Si dry-run : log `user {uid} → lifetime_savings_cents={lifetime} (dry-run)`, continue
     - Sinon : INSERT ... ON CONFLICT DO UPDATE (user_id, lifetime_savings_cents, last_computed_at=now, updated_at=now)
     - count++
   - Commit final (hors dry-run)
3. Log `savings_batch: processed N user(s)`
4. `_write_sync_log(Session, "success", dry_run)`
5. Exception globale → log + sync_log status='failed' → exit 1

### Flow 2 — Consommation par la hot-path `/account/stats`

Hors scope ce batch mais mentionné pour contexte :
1. Client appelle `GET /account/stats`
2. Service lit `user_savings_snapshot WHERE user_id = :uid` → `(lifetime, last_computed_at)`
3. Service appelle `compute_savings_for_user(db, uid, since=last_computed_at)` → `live_delta`
4. Renvoie `total_savings = lifetime + live_delta`

## Paramètres

- Pas de paramètres métier dans `ratis_settings.json` section dédiée
- Note : `settings.savings.subscription_price_cents=799` existe dans les settings mais est consommé ailleurs (logique de calcul du seuil de rentabilité dans `compute_savings_for_user`)

## Monitoring / logs

- Format stdout : `%(asctime)s %(levelname)s %(message)s`
- Log start : `savings_batch: N user(s) to process`
- En dry-run : 1 log par user avec le lifetime calculé
- Log end : `savings_batch: processed N user(s)` ou `savings_batch FAILED: {exception}`
- `batch_sync_log(batch_name='savings_snapshot', status)` en fin (best-effort, log séparé si write échoue)

## FAQ vectorisée

### Pourquoi ratis_batch_savings recalcule-t-il tout le lifetime à chaque run plutôt qu'un delta ?

Parce que les données source peuvent changer rétroactivement : `price_consensus` peut basculer (nouveaux prix dominants), des scans peuvent être acceptés manuellement après review, les formules de savings peuvent être tunées. Un delta-patch produirait des snapshots drift progressivement. Le recalcul complet nocturne garantit que chaque snapshot reflète l'état cohérent actuel de toute l'histoire du user. Coût : O(N users) par nuit, acceptable.

### Pourquoi ratis_batch_savings n'utilise-t-il pas de SQL direct ?

Pour **centraliser la formule**. `ratis_core.savings.compute_savings_for_user` est la seule implémentation de "combien cet user a-t-il économisé" — elle est appelée par la hot-path live (avec `since=last_snapshot`) et par ratis_batch_savings (avec `since=None`). Dupliquer en SQL garantit un drift. Si la formule évolue (ex : nouveau type d'économie), on modifie la lib, les deux chemins sont cohérents.

### Comment ratis_batch_savings est-il consommé par `/account/stats` ?

Lecture O(1) de `user_savings_snapshot` → `lifetime + last_computed_at`. Ensuite un calcul `compute_savings_for_user(db, uid, since=last_computed_at)` qui ne scanne que les scans acceptés depuis. Total = `lifetime + live_delta`. Pour un user qui n'a pas scanné depuis hier, le delta est 0 et la lecture est quasi-instantanée. Sans le snapshot, chaque GET de stats coûterait un scan complet des receipts de l'user.

### Que se passe-t-il si un user est créé entre deux runs du batch ?

Son `user_savings_snapshot` n'existe pas encore. La hot-path détecte `snapshot IS NULL` → fallback sur `compute_savings_for_user(db, uid, since=None)` (calcul complet). Au prochain run nocturne, le batch insère le snapshot. Coût : les premiers GET stats d'un nouvel user sont légèrement plus lents (1 scan complet, mais sur peu de rows), puis O(1) après la première nuit.

### Comment tester ratis_batch_savings localement ?

`uv run pytest batch/ratis_batch_savings/tests/` pour la suite complète (test_savings_batch.py). Pour un dry-run contre une DB peuplée : `uv run python batch/ratis_batch_savings/savings_batch.py --dry-run` — log les lifetimes calculés user par user sans commit DB. Pour forcer un user spécifique : appeler directement `compute_savings_for_user` en REPL.

## Glossaire

- **DA-XX** : décision d'architecture numérotée
- **lifetime_savings_cents** : cumul total des économies réalisées par un user depuis son inscription, en centimes (toujours int, R-money)
- **snapshot** : ligne dans `user_savings_snapshot` contenant le lifetime calculé à un `last_computed_at` donné
- **live_delta** : économies accumulées par les scans accepted depuis `last_computed_at`, calculées à la volée dans la hot-path
- **Hot-path** : chemin code optimisé pour la latence (ex : `/account/stats` doit répondre en < 200ms)
- **Economie** : différence entre le prix payé par l'user (scans) et un prix de référence (price_consensus local du magasin) — formule détaillée dans `ratis_core.savings`
