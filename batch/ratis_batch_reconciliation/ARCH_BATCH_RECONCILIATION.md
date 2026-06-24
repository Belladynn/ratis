---
type: batch-global
service: ratis_batch_reconciliation
status: production
parent: ARCH_RATIS
sub_archs: []
related: [ARCH_REWARDS, ARCH_cashback]
tech: [Python, SQLAlchemy, Postgres]
tables: [scans, cabecoin_transactions, user_cab_balance, cashback_transactions, user_cashback_balance, cashback_withdrawals, receipts]
env_vars: [DATABASE_URL]
tags: [batch, reconciliation, cab, cashback, integrity]
business_domain: cashback
rgpd_concern: false
updated: 2026-04-24
---

# ratis_batch_reconciliation — réconciliation CAB + cashback

> Batch CLI **financier strict** : vérifie l'intégrité des balances CAB + cashback (sum transactions == balance), détecte les divergences, alerte. Reste 100 % financier (jamais de métier) — séparation argent/data avec `ratis_batch_data_reconciliation`.
> @tags: batch reconciliation cab cashback integrity financial balance audit user_cab_balance user_cashback_balance
> @status: LIVRÉ V0
> @subs: auto

> [[ARCH_RATIS]] · relations : [[ARCH_REWARDS]], [[ARCH_cashback]]

## Index

- [Résumé en une phrase](#résumé-en-une-phrase) · L.39
- [Responsabilité](#responsabilité) · L.43
- [Fréquence d'exécution](#fréquence-dexécution) · L.52
- [Tables lues / écrites](#tables-lues-écrites) · L.59
- [Dépendances internes (autres services/libs ratis)](#dépendances-internes-autres-serviceslibs-ratis) · L.71
- [Dépendances externes (tiers)](#dépendances-externes-tiers) · L.76
- [Décisions d'architecture clés](#décisions-darchitecture-clés) · L.81
- [Flow principal](#flow-principal) · L.113
- [Paramètres](#paramètres) · L.139
- [Monitoring / logs](#monitoring-logs) · L.147
- [Limitations connues (V1)](#limitations-connues-v1) · L.154
- [FAQ vectorisée](#faq-vectorisée) · L.161
- [Glossaire](#glossaire) · L.183

---

## Résumé en une phrase

ratis_batch_reconciliation est un batch CLI nocturne qui détecte et corrige les incohérences entre les événements sources (scans, receipts) et les enregistrements comptables (cabecoin_transactions, cashback_transactions) quand ratis_rewards était down au moment de l'appel fire-and-forget, puis vérifie l'intégrité des soldes matérialisés (`user_cab_balance`, `user_cashback_balance`).

## Responsabilité

- ratis_batch_reconciliation détecte les scans `status='accepted'` > 10 min sans ligne correspondante dans `cabecoin_transactions` (direction=credit) et crédite le CAB manquant + met à jour `user_cab_balance` atomiquement
- ratis_batch_reconciliation vérifie l'intégrité du solde CAB : `user_cab_balance.balance` doit égaler `SUM(credits) - SUM(debits)` de `cabecoin_transactions` — les dérives sont **loggées en alerte**, jamais corrigées automatiquement
- ratis_batch_reconciliation expire les cashbacks `type='CREDIT' status='pending'` > 90 jours en `refused` (param `cashback_pending_expiry_days`) et rembourse les BOOST enfants le cas échéant
- ratis_batch_reconciliation détecte les receipts dont aucun scan n'a généré de `cashback_transactions` et reconstitue les lignes via `detect_cashback()` sur les scans accepted du receipt (idempotent par `(scan_id, product_ean)`)
- ratis_batch_reconciliation log en ERROR les retraits (`cashback_withdrawals`) bloqués en `pending` > 24h — **stub V1**, pas de retry Stripe automatique, intervention manuelle requise
- ratis_batch_reconciliation vérifie l'intégrité du solde cashback avec les règles de calcul incluant les compensatoires de remboursement (CREDIT/BOOST `distributed_at IS NOT NULL` en +, WITHDRAWAL/SUBSCRIPTION_PAYMENT en −, CREDIT/BOOST refused non déduits)

## Fréquence d'exécution

- **Workflow GitHub Actions** : **aucun workflow dédié à ce jour** (pas de `.github/workflows/batch_reconciliation.yml` dans le repo)
- **Cron prévu** : quotidien en heure creuse (à créer)
- **Run manuel** : `uv run python batch/ratis_batch_reconciliation/run.py [--dry-run]`
- Charge `.env.local` du dossier batch via `python-dotenv` pour un run local simplifié

## Tables lues / écrites

| Table | Opération |
|---|---|
| `scans` | lecture (status='accepted' sans crédit, receipts sans cashback) |
| `receipts` | lecture indirecte (via join scans) |
| `cabecoin_transactions` | INSERT CREDIT manquant (reference_id=scan_id, direction='credit') |
| `user_cab_balance` | UPDATE atomique (+= amount) |
| `cashback_transactions` | UPDATE `status='refused'` (expirés 90j) + INSERT CREDIT pending (missing cashback) |
| `user_cashback_balance` | lecture seule (vérif intégrité) |
| `cashback_withdrawals` | lecture seule (V1 stub : log ERROR) |

## Dépendances internes (autres services/libs ratis)

- [[ARCH_CORE]] — `make_engine`, `load_settings` (pour `rewards.cab_per_*` et `cashback_pending_expiry_days`)
- [[ARCH_REWARDS]] — **ratis_batch_reconciliation duplique en SQL direct la logique de `award_cab` et `detect_cashback`** (DA-01). Toute évolution de ces fonctions côté ratis_rewards doit être répercutée ici. Les fonctions sources portent un commentaire `⚠️ RECONCILIATION SYNC`.

## Dépendances externes (tiers)

- Aucune en V1 — ratis_batch_reconciliation tourne 100% local DB.
- V2 prévu : client Stripe pour le retry automatique des `cashback_withdrawals` pending (actuellement stub).

## Décisions d'architecture clés

### DA-01 — SQL direct, pas d'appel HTTP à ratis_rewards

**Choix** : réimplémenter `award_cab` / `detect_cashback` en SQL direct dans `reconciliation/cab.py` et `reconciliation/cashback.py`
**Alternative rejetée** : appel HTTP interne à ratis_rewards
**Raison** : le batch doit tourner même si ratis_rewards est down (c'est la raison d'être de la réconciliation). Appeler le service qu'on essaie de rattraper créerait un deadlock logique. Le prix : sync manuelle obligatoire entre les deux côtés (enforced par commentaire `⚠️ RECONCILIATION SYNC` + documentation).

### DA-02 — Grace period 10 minutes avant réconciliation

**Choix** : `scanned.status_updated_at < NOW() - INTERVAL '10 minutes'`
**Alternative rejetée** : réconcilier en temps réel tous les scans
**Raison** : le chemin live (scan → Celery → notify_scan_accepted fire-and-forget → ratis_rewards) a une latence possible. 10 min = largement plus que le p99 de la chaîne Celery + HTTP. Évite de créer des transactions en double juste parce que le chemin live prend 30s.

### DA-03 — Intégrité solde = alerte, pas correction auto

**Choix** : `check_cab_balance_integrity` et `check_cashback_balance_integrity` **retournent la liste des dérives** sans rien corriger
**Alternative rejetée** : rebalance automatique du solde matérialisé
**Raison** : une dérive de solde est soit (a) un bug critique côté code, soit (b) une corruption DB. Les deux requièrent une intervention humaine pour comprendre la cause avant correction. Correction auto masquerait le bug.

### DA-04 — Une Session par opération

**Choix** : chaque `reconcile_*` / `check_*` ouvre sa propre session et commit indépendamment
**Alternative rejetée** : transaction globale
**Raison** : si `reconcile_missing_scan_rewards` crash, les autres opérations doivent quand même tourner. Les résultats sont agrégés dans `results` dict pour le log final + exit code.

### DA-05 — `reconcile_pending_withdrawals` = stub V1

**Choix** : log ERROR + comptage, pas de retry
**Alternative rejetée** : intégration Stripe complète
**Raison** : V1 = partenariats directs, pas d'API de polling fournisseur. Implémentation complète = dépendance Stripe client + backoff + réconciliation webhook. Acceptable en V1 parce que le volume est faible — documented in `DECISIONS_PENDING.md` R4-01.

## Flow principal

### Flow 1 — Réconciliation CAB manquant

1. Query `scans WHERE status='accepted' AND NOT EXISTS (cabecoin_transactions WHERE reference_id = scan.id AND direction='credit')` avec grace period 10 min
2. Pour chaque scan : charge le montant CAB via `settings.rewards.cab_per_<scan_type>`
3. INSERT `cabecoin_transactions` (reference_id=scan.id, direction='credit', reference_type='scan') — guard par index partiel unique `uq_cabtx_scan_credit` (idempotent write-side sous runs concurrents)
4. UPDATE atomique `user_cab_balance.balance += amount`
5. Commit, comptage dans stats

Limitations V1 (acceptées) : multiplicateur de streak non appliqué, battlepass non mis à jour, missions non incrémentées, notifications outbox non enqueuée. Intentionnellement conservateur.

### Flow 2 — Réconciliation cashback manquant

1. Query receipts `type='receipt' status='accepted'` sans aucune ligne `cashback_transactions` liée (join via scan_id → receipt_id)
2. Pour chaque receipt : `SELECT product_ean, price FROM scans WHERE receipt_id = :rid AND status='accepted' AND product_ean IS NOT NULL` = reconstitution des lignes du ticket
3. Appel direct `detect_cashback(db, user_id, first_scan_id, receipt_lines, rewards_cfg)` — idempotent via check `(scan_id, product_ean)`
4. Les CREDITs sont INSERT en `status='pending'` — le webhook AFFILAE/AWIN/CJ les marquera `distributed_at` quand validés

### Flow 3 — Vérification intégrité solde

1. Comparaison `user_cab_balance.balance` vs `SUM(credit) - SUM(debit)` de `cabecoin_transactions`
2. Lignes où `stored != computed` = dérives → retournées dans la liste
3. `run.py` compte `len(drifts)` dans `results["cab_integrity_drifts"]` pour le log final
4. Idem `user_cashback_balance` avec règles plus complexes (types CREDIT/BOOST/WITHDRAWAL/SUBSCRIPTION_PAYMENT + `distributed_at` non NULL + `status != refused`)

## Paramètres

- `settings.rewards.cab_per_receipt_scan` — défaut 20 (V1.x recal 2026-05-08)
- `settings.rewards.cab_per_label_scan` — défaut 3 (V1.x recal 2026-05-08)
- `settings.rewards.cab_per_barcode_scan` — défaut 1 (V1.x recal 2026-05-08)
- `settings.rewards.cashback_pending_expiry_days` — défaut 90
- Grace periods hardcodés dans SQL (10 min pour CAB, 90j pour cashback expiry, 24h pour withdrawals) — volontairement pas dans settings (contraintes métier stables)

## Monitoring / logs

- Log format : `%(asctime)s %(levelname)s %(name)s %(message)s`
- Dict `results` en fin de run : `{"missing_scan_rewards": N, "cab_integrity_drifts": D, "expired_cashbacks": E, "missing_cashback_scans": M, "pending_withdrawals": P, "cashback_integrity_drifts": D2}`
- Exit code 1 si au moins une opération a retourné `"ERROR"` → intervention requise
- Pas de `batch_sync_log` écrit à ce jour (TODO — à aligner avec les autres batches)

## Limitations connues (V1)

- ~~**Idempotence sous runs concurrents (DP-03)**~~ : **résolu** (2026-04-30). La garde `NOT EXISTS` était read-side et insuffisante en runs concurrents. Correctif déployé :
  - Partial UNIQUE indexes `uq_cabtx_scan_credit` et `uq_cashbacktx_scan_ean_credit` (migration `20260415_1800_n8o9p0q1r2s3`) — write-side guard.
  - INSERTs batch en `ON CONFLICT DO NOTHING` ciblé.
  - `_credit_scan` (cab.py) utilise `INSERT ... DO NOTHING RETURNING id` pour skipper le bump de `user_cab_balance` quand un run concurrent a déjà commit la TX — sinon le solde matérialisé serait double-crédité alors que la ligne TX resterait unique.
- **Multiplicateur streak non appliqué** : les transactions réconciliées créditent le montant brut (pas de bonus streak).
- **Missions non incrémentées** : `check_missions_progress` non appelé sur scans réconciliés.
- **`reconcile_pending_withdrawals` stub** : pas de retry Stripe en V1, log ERROR uniquement.

## FAQ vectorisée

### Pourquoi ratis_batch_reconciliation ne rappelle-t-il pas ratis_rewards au lieu de dupliquer le code SQL ?

Parce que ratis_batch_reconciliation existe précisément pour les cas où ratis_rewards était down au moment de l'événement live. Le rappeler créerait un deadlock logique : si ratis_rewards refonctionne au moment du batch, le chemin live aura déjà rattrapé en grande partie ; s'il est toujours down, l'appel HTTP du batch échouera pareil. Solution : SQL direct, avec sync manuelle maintenue via commentaire `⚠️ RECONCILIATION SYNC` dans le code source des deux côtés.

### Pourquoi ratis_batch_reconciliation ne corrige-t-il pas automatiquement les dérives de solde ?

Une dérive entre `user_cab_balance.balance` et `SUM(credit) - SUM(debit)` indique **soit** un bug dans le code de mise à jour (race condition, commit manquant), **soit** une corruption DB, **soit** une intervention admin non tracée. Corriger automatiquement masquerait le bug. ratis_batch_reconciliation alerte uniquement → un humain investigue la cause avant de rebalance manuellement.

### Comment ratis_batch_reconciliation évite-t-il les doublons CAB sur un même scan ?

L'index partiel unique `uq_cabtx_scan_credit` sur `cabecoin_transactions (reference_id) WHERE direction='credit' AND reference_type='scan'` garantit qu'un même scan ne peut avoir qu'un seul CREDIT. Si le chemin live a déjà inséré (delai réseau vs batch), l'INSERT du batch échoue avec une violation d'unicité, attrapée et loggée. **Limitation V1** : la garde côté SELECT est `NOT EXISTS` read-side — deux runs batch concurrents peuvent passer la garde en même temps et insérer (un seul réussira grâce à l'index, mais l'autre génère une exception DB). Un `ON CONFLICT DO NOTHING` est prévu pour nettoyer (DP-03).

### Comment tester ratis_batch_reconciliation localement ?

`uv run pytest batch/ratis_batch_reconciliation/tests/` (tests unitaires par module cab/cashback). Pour un dry-run contre une DB peuplée avec des incohérences volontaires : `uv run python batch/ratis_batch_reconciliation/run.py --dry-run` — log les comptes sans écrire.

### Pourquoi les images tickets ne sont-elles pas gérées par ratis_batch_reconciliation ?

Parce que c'est le rôle de [[ratis_batch_purge]]. Le découpage est : `ratis_batch_reconciliation` répare les incohérences comptables entre événements et agrégats, `ratis_batch_purge` gère le cycle de vie des données volatiles (RGPD, stockage, caches). Les deux tournent sur des crons séparés.

## Glossaire

- **DA-XX** : décision d'architecture numérotée
- **Grace period** : délai après un événement avant que le batch le considère "en retard" (10 min pour scans, 24h pour withdrawals)
- **Dérive** : différence entre un solde matérialisé (`user_cab_balance.balance`) et le calcul exhaustif sur la table source (`cabecoin_transactions`)
- **⚠️ RECONCILIATION SYNC** : commentaire convention dans `ratis_rewards` signalant qu'une fonction doit être répercutée côté batch
- **Fire-and-forget** : le service appelant (worker Celery) n'attend pas la réponse du service aval (ratis_rewards) — améliore la latence perçue mais autorise les incohérences en cas de panne aval, d'où ce batch
- **DP-03 / R4-01** : références vers `DECISIONS_PENDING.md` pour les décisions en attente de validation user
