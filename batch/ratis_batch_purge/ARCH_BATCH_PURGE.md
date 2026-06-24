---
type: batch-global
service: ratis_batch_purge
status: production
parent: ARCH_RATIS
sub_archs: []
related: [ARCH_RATIS]
tech: [Python, SQLAlchemy, Postgres, boto3 (R2)]
tables: [refresh_tokens, optimized_routes, notification_logs, user_sessions, user_session_stats, receipts, scans, community_challenges, unknown_scans_weekly_aggregate, batch_sync_log]
env_vars: [DATABASE_URL, R2_ENDPOINT_URL, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY, R2_BUCKET_NAME]
tags: [batch, rgpd, cleanup, aggregation]
business_domain: rgpd
rgpd_concern: true
updated: 2026-04-24
---

# ratis_batch_purge — purge RGPD + agrégation

> Batch CLI quotidien qui agrège les données comportementales pérennes (sessions, scans unknown) PUIS purge les tables volatiles (refresh_tokens, notifications, routes, sessions >90j, scans store_status unknown >7j) et les objets R2 expirés (tickets 48h, labels 72h). Toujours **agréger d'abord, purger ensuite**.
> @tags: batch rgpd cleanup aggregation purge volatile sessions scans unknown r2 retention refresh_tokens notifications
> @status: LIVRÉ V0
> @subs: auto

> [[ARCH_RATIS]] · concerne toutes les tables volatiles (pas de related spécifique, transverse)

## Index

- [Résumé en une phrase](#résumé-en-une-phrase) · L.39
- [Responsabilité](#responsabilité) · L.43
- [Fréquence d'exécution](#fréquence-dexécution) · L.54
- [Tables lues / écrites](#tables-lues-écrites) · L.60
- [Dépendances internes (autres services/libs ratis)](#dépendances-internes-autres-serviceslibs-ratis) · L.75
- [Dépendances externes (tiers)](#dépendances-externes-tiers) · L.80
- [Décisions d'architecture clés](#décisions-darchitecture-clés) · L.84
- [Flow principal](#flow-principal) · L.116
- [Paramètres (env + constantes)](#paramètres-env-+-constantes) · L.133
- [Monitoring / logs](#monitoring-logs) · L.138
- [Contraintes RGPD propres au batch](#contraintes-rgpd-propres-au-batch) · L.145
- [FAQ vectorisée](#faq-vectorisée) · L.154
- [Glossaire](#glossaire) · L.176

---

## Résumé en une phrase

ratis_batch_purge est un batch CLI quotidien qui agrège les données comportementales (sessions, scans) dans des tables de stats pérennes, puis purge les tables volatiles (refresh_tokens, notifications, routes optimisées, sessions > 90j, scans store_status='unknown' > 7j) et les objets R2 expirés (tickets 48h, labels 72h) — toujours **agréger d'abord, purger ensuite**.

## Responsabilité

- ratis_batch_purge agrège `user_sessions` > 90j dans `user_session_stats` (ios/android/web par mois) puis delete les sources
- ratis_batch_purge agrège les scans `store_status='unknown'` > 7j dans `unknown_scans_weekly_aggregate` (par semaine ISO) puis hard-delete les sources (PII `user_lat`/`user_lng` purgées)
- ratis_batch_purge supprime `refresh_tokens` expirés ou révoqués > 90j, `optimized_routes` expirées (TTL 24h), `notification_logs` > 90j
- ratis_batch_purge libère les locks `photo_hash` bloqués en pending > 1h (receipts + label scans) pour permettre aux users de retry
- ratis_batch_purge supprime les images R2 : tickets (`receipts.image_r2_key`) > 48h → `image_deleted_at = now()`, labels (`scans.label_r2_key`) quand `label_image_expires_at` dépassé → `label_r2_key = NULL`
- ratis_batch_purge désactive les `community_challenges` dont la période de grâce post-`ends_at` est écoulée (`is_active = FALSE`)
- ratis_batch_purge rejette les label scans stuck en `pending` > 2h (`status='rejected'`, `rejected_reason='ocr_timeout'`) pour débloquer l'user
- ratis_batch_purge **ne purge JAMAIS** : `cashback_withdrawals`, `cashback_transactions`, `subscriptions` (obligation légale RGPD / comptable)

## Fréquence d'exécution

- **Workflow GitHub Actions** : `.github/workflows/batch_purge.yml`
- **Cron** : `0 3 * * *` (quotidien 03h00 UTC) — actuellement désactivé (DB locale)
- **Déclenchement manuel** : `workflow_dispatch` avec option `--dry-run`

## Tables lues / écrites

| Table | Opération |
|---|---|
| `refresh_tokens` | DELETE expirés ou révoqués > 90j |
| `optimized_routes` | DELETE `expires_at < now()` |
| `notification_logs` | DELETE > 90j |
| `user_sessions` | agrégation → DELETE > 90j |
| `user_session_stats` | INSERT/UPDATE (aggregation cible) |
| `receipts` | UPDATE `photo_hash = NULL` (unlock) + UPDATE `image_deleted_at` après suppression R2 |
| `scans` | UPDATE `photo_hash = NULL` (label pending > 1h) + UPDATE `label_r2_key = NULL` (post-R2-delete) + UPDATE `status='rejected'` (label orphelin > 2h) + DELETE (store_status='unknown' > 7j après agrégation) |
| `community_challenges` | UPDATE `is_active = FALSE` (post grace period) |
| `unknown_scans_weekly_aggregate` | INSERT/UPDATE (agrégation ISO week) |
| `batch_sync_log` | INSERT run success/failed |

## Dépendances internes (autres services/libs ratis)

- [[ARCH_CORE]] — `make_engine`
- Toutes les tables ciblées appartiennent à différents services — ratis_batch_purge est **transverse** (le seul batch autorisé à DELETE dans les tables d'autres domaines)

## Dépendances externes (tiers)

- **Cloudflare R2** — suppression des objets tickets (48h) et labels (72h) via boto3 S3-compatible SDK

## Décisions d'architecture clés

### DA-01 — Agréger d'abord, purger ensuite

**Choix** : chaque couple agrégation/purge est dans la même transaction, dans l'ordre INSERT → DELETE
**Alternative rejetée** : agrégation séparée (risque de perdre des rows si la purge tourne entre temps)
**Raison** : R5 — jamais DELETE prod sans avoir capitalisé les stats utiles. Capture `now()` une seule fois au début de la transaction pour garantir que l'agrégation et le delete opèrent sur le même cutoff.

### DA-02 — Une transaction par étape, pas globale

**Choix** : chaque `STEP` ouvre sa propre Session et commit à la fin
**Alternative rejetée** : transaction globale pour tout le batch
**Raison** : si une étape échoue (R2 down, table verrouillée), les autres étapes doivent tourner quand même. On log l'échec, on continue, on mark le run `failed` dans `batch_sync_log` à la fin.

### DA-03 — `cashback_*`, `subscriptions` protégés par convention

**Choix** : ces tables n'apparaissent **jamais** dans les `STEPS` du batch
**Alternative rejetée** : ajouter une colonne `is_purgeable` et filtrer
**Raison** : la sécurité passe par la convention "ces tables n'ont aucun code de suppression nulle part". Ajouter un flag serait une invitation à un bug futur qui le mettrait à true par erreur.

### DA-04 — `store_status='unknown'` hard-delete après 7j + agrégation ISO week

**Choix** : PII `user_lat`/`user_lng` sont supprimées, stats conservées par semaine
**Alternative rejetée** : soft-delete ou conservation plus longue
**Raison** : RGPD — `user_lat`/`user_lng` sont des données de localisation précise (PII sensible). 7j suffisent pour qu'un upload de ticket ultérieur rattache le scan (DA-30 dans ratis_product_analyser). Au-delà, la rétention n'a plus d'utilité métier. L'agrégat ISO week (`to_char(scanned_at, 'IYYY-"W"IW')`) permet de garder des stats anonymisées sans latitude/longitude.

### DA-05 — Images tickets R2 : 48h hard-limit, confirmation DB

**Choix** : DELETE R2 → UPDATE `receipts.image_deleted_at = now()` en deux étapes sur la même row
**Alternative rejetée** : lifecycle rule R2 seule (sans confirmation DB)
**Raison** : la lifecycle R2 est un backstop, pas la source de vérité. Le batch confirme la suppression côté DB (`image_deleted_at`) pour auditer le RGPD ("l'image ticket X a bien été purgée le Y").

## Flow principal

### Ordre des 10 étapes (STEPS)

1. `refresh_tokens` — DELETE expirés + révoqués > 90j
2. `optimized_routes` — DELETE `expires_at < now()`
3. `notification_logs` — DELETE > 90j
4. `user_sessions` — agrégation `user_session_stats` + DELETE > 90j (cutoff capturé une fois)
5. `photo_hashes` — UPDATE `photo_hash = NULL` sur receipts pending > 1h + label scans pending > 1h
6. `receipt_images` — R2 DELETE tickets > 48h + UPDATE `image_deleted_at`
7. `label_images` — R2 DELETE labels post `label_image_expires_at` + UPDATE `label_r2_key = NULL`
8. `expire_community_challenges` — UPDATE `is_active = FALSE` post grace period
9. `label_pending_orphans` — UPDATE `status='rejected'` pour label scans pending > 2h
10. `unknown_scans` — agrégation `unknown_scans_weekly_aggregate` (ISO week) + DELETE `store_status='unknown'` > 7j

Chaque étape s'exécute dans sa propre transaction. Une erreur log + continue. Status final dans `batch_sync_log`.

## Paramètres (env + constantes)

- Intervalles hardcodés dans les SQL (48h, 72h, 90j, 7j, 2h, 1h) — volontairement pas dans `ratis_settings.json` car ce sont des contraintes **RGPD/légales** pas ajustables sans review
- Env vars R2 requises : `R2_ENDPOINT_URL`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, `R2_BUCKET_NAME`

## Monitoring / logs

- Chaque STEP log le nombre de rows affectées : `label: N row(s) affected`
- Agrégations loggent l'upsert count séparément
- `batch_sync_log(batch_name='purge', status)` en fin de run
- Si au moins une étape échoue → exit code 1 → workflow GH Actions marqué failed

## Contraintes RGPD propres au batch

- **Jamais supprimer** : `cashback_withdrawals`, `cashback_transactions`, `subscriptions` (obligation légale, conservation long terme)
- **Toujours supprimer dans les fenêtres RGPD** :
  - Images tickets (PII-adjacent : store + prix visibles) → 48h max en R2
  - Images labels → 72h max (`label_image_expires_at`)
  - PII `user_lat`/`user_lng` dans scans unknown → 7j max (rows hard-delete après agrégation)
- **Pré-condition** : toute table volatile doit avoir son agrégat pérenne **avant** qu'un DELETE soit ajouté au batch (pattern enforced)

## FAQ vectorisée

### Pourquoi ratis_batch_purge agrège-t-il avant de purger ?

Parce que supprimer sans agréger perd l'info utile pour toujours. Les stats comportementales (sessions par plateforme par mois, scans inconnus par semaine) alimentent les dashboards produit sans enfreindre le RGPD — ce sont des agrégats anonymisés. L'ordre strict `INSERT INTO stats … SELECT … WHERE cutoff` puis `DELETE … WHERE cutoff` dans la **même transaction** avec le même `cutoff` capturé une fois garantit l'atomicité.

### Pourquoi ratis_batch_purge ne touche-t-il jamais à `cashback_withdrawals` / `cashback_transactions` / `subscriptions` ?

Ces tables sont soumises à des obligations légales (conservation comptable multi-années, retracabilité paiements) et métier (RGPD — les retraits d'argent doivent rester auditable). ratis_batch_purge les exclut par convention : elles n'apparaissent jamais dans `STEPS`. Toute tentative d'y ajouter une purge doit passer par une review légale.

### Que fait ratis_batch_purge si R2 est down au moment du run ?

L'étape `receipt_images` / `label_images` loggue un warning par fichier en échec mais **ne met pas à jour la DB** (`image_deleted_at` reste NULL). L'étape retournera au prochain run. Le batch continue sur les étapes suivantes — pas de rollback global. `batch_sync_log` est marqué `failed` en fin, mais les purges DB pures (refresh_tokens, notif_logs, etc.) auront bien été effectuées.

### Comment tester ratis_batch_purge localement ?

`uv run pytest batch/ratis_batch_purge/tests/` pour la suite complète (conftest avec fixtures DB). Pour un dry-run manuel contre une DB peuplée : `uv run python batch/ratis_batch_purge/purge.py --dry-run` — logue les row counts affectés sans commit. **R2 n'est pas stubbé en dry-run** : le batch liste seulement les candidats sans appeler `delete_object`.

### Comment ratis_batch_purge gère-t-il la déduplication photo_hash ?

La logique est : `photo_hash IS NOT NULL` est un lock anti-doublon (un même fichier uploadé deux fois → refusé). Si le pipeline OCR crash avant de finaliser le scan, le hash reste posé et bloque l'user. Le batch libère le lock après 1h (receipts sans scan terminé, labels stuck pending > 1h). Les hashes des scans **acceptés** restent en place pour permanence de la dedup.

## Glossaire

- **DA-XX** : décision d'architecture numérotée
- **STEP** : une des 10 étapes séquentielles du batch, chacune dans sa propre transaction
- **Grace period** : délai post-`ends_at` pendant lequel un community challenge reste visible/claim-able
- **ISO week** : semaine calendaire ISO-8601 (format `IYYY-"W"IW`, ex : `2026-W17`)
- **lifecycle rule R2** : politique Cloudflare de suppression automatique côté objet — ratis_batch_purge est le "source of truth" côté DB, la lifecycle est un backstop
- **store_status='unknown'** : scan dont le magasin n'a pas pu être identifié au moment de l'OCR (ticket sans en-tête reconnu)
