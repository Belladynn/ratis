---
type: batch-global
service: ratis_batch_data_reconciliation
status: planned
parent: ARCH_RATIS
sub_archs: []
related: [ARCH_cross_retailer_consensus, ARCH_receipt_pipeline, ARCH_REWARDS, ARCH_NOTIFIER, ARCH_BATCH_RECONCILIATION]
tech: [Python, SQLAlchemy, Postgres, httpx]
tables: [scans, receipts, parsed_ticket_items, product_name_resolutions, ocr_knowledge, price_consensus, cabecoin_transactions, stores]
env_vars: [DATABASE_URL, NOTIFIER_URL, INTERNAL_API_KEY]
tags: [batch, reconciliation, data, nrc, esl, retro-cab, mdd-vote, price-disambiguate]
business_domain: data
rgpd_concern: false
updated: 2026-05-02
---

# ratis_batch_data_reconciliation — réconciliation métier (data, pas argent)

> Batch nightly planifié qui réconcilie les données métier (NRC EAN partiel, items en disambiguation, receipts ambigus multi-store, scans nouvellement résolus) post-sync nuit (consensus, OFF, SIREN) pour déclencher CAB rétroactif + notifs gratitude. Séparé volontairement du batch financier `ratis_batch_reconciliation`.
> @tags: batch reconciliation data nrc esl retro-cab mdd-vote price-disambiguate planned
> @status: PLANIFIÉ
> @subs: auto

> [[ARCH_RATIS]] · relations : [[ARCH_cross_retailer_consensus]] (Bloc I), [[ARCH_receipt_pipeline]], [[ARCH_REWARDS]] (CAB rétroactif), [[ARCH_NOTIFIER]] (notif gratitude), [[ARCH_BATCH_RECONCILIATION]] (séparation argent vs métier)

> Statut : 📋 **Planifié** — design validé 2026-05-02 (orchestrator + product owner). Implémentation en 2 phases (cf § Plan d'implémentation).

---

## Résumé en une phrase

Batch nightly qui réconcilie les données métier accumulées (scans unresolved, items en disambiguation, receipts ambigus multi-store, scans nouvellement résolus) pour exploiter les data fraîches synchronisées la nuit (consensus, OFF, SIREN) et déclencher CAB rétroactif + notifs gratitude.

---

## Genèse

`ratis_batch_reconciliation` existe et reste **100% financier** (CAB integrity, cashback expiry, withdrawals stale). Mélanger logique financière (= légalement contraint, audit strict, never purge) et logique métier (= correctionnelle, idempotente, soumise au consensus changeant) violerait la séparation des concerns.

**Décision produit (validée 2026-05-02)** :

> **Nouveau batch dédié `ratis_batch_data_reconciliation` pour la réconciliation métier.** L'existant `ratis_batch_reconciliation` reste pure financier. Aucun mélange.

Ce batch absorbe :
1. **Bloc I NRC** (recovery EAN partiel + résolution rétroactive consensus) — déjà documenté dans [[ARCH_cross_retailer_consensus]].
2. **Store MDD vote** — résout les receipts ambigus multi-store via vote sur marques distributeur des items.
3. **Price disambiguate via consensus** — tranche les items en `disambiguation` (multi-OCR conflict) via `price_consensus` retailer-keyed.
4. **Retro CAB + notif gratitude** — crédite les CAB rétroactifs sur scans nouvellement matched + notif push gratitude-driven.

---

## Responsabilité

- **Read** : tables `scans`, `receipts`, `parsed_ticket_items`, `product_name_resolutions`, `price_consensus`, `stores`, `products`.
- **Write** : `scans` (status updates), `receipts` (status updates), `parsed_ticket_items` (status updates), `product_name_resolutions` (INSERT new resolutions), `cabecoin_transactions` (INSERT retro_scan), `ocr_knowledge` (INSERT auto-feed).
- **Trigger** : `POST /api/v1/notify` (NT) pour notif gratitude (fire-and-forget, NT gère retry).
- **Pas de touche** aux tables financières du batch_reconciliation existant (cabecoin_transactions financier OK car table partagée mais reference_type='retro_scan' isolé).

---

## Fréquence d'exécution

**06:00 UTC quotidien** (= 07:00 / 08:00 Paris selon DST).

Justification ordre :
- 02:00 consensus (price_consensus mis à jour)
- 03:00 purge / 03:30 savings / 04:00 referral_payout (financier)
- 04:00 off_sync (peut prendre 1-2h, découvre nouveaux EAN)
- 05:30 siren_sync (V2, découvre nouveaux stores)
- **06:00 data_reconciliation** ← profite de toutes les data sources fraîches

L'utilisateur reçoit ses notifs gratitude à 06:00 UTC ≈ 07:00-08:00 Paris = heure de réveil. Idéal pour la notif "✨ tes scans ont été validés cette nuit".

---

## Tables lues / écrites

| Table | R | W | Notes |
|---|---|---|---|
| `scans` | ✓ | ✓ (status) | unresolved → matched, idempotent via UNIQUE PNR |
| `receipts` | ✓ | ✓ (status, store_id) | pending_user_reconciliation → matched (Job 2) |
| `parsed_ticket_items` | ✓ | ✓ (status, price) | disambiguation → matched (Job 3) |
| `product_name_resolutions` | ✓ | ✓ (INSERT) | UNIQUE (scan_id, source_type, normalized_label) + ON CONFLICT |
| `ocr_knowledge` | ✗ | ✓ (INSERT auto) | confidence > 0.85 → auto-feed (Bloc I NRC) |
| `price_consensus` | ✓ | ✗ | read only — tranche disambiguate (Job 3) |
| `cabecoin_transactions` | ✓ | ✓ (INSERT) | reference_type='retro_scan', idempotent |
| `stores` | ✓ | ✗ | read only — MDD vote candidates (Job 2) |
| `products` | ✓ | ✗ | read only — MDD lookup (Job 2) |

---

## Dépendances internes (autres services/libs ratis)

- **`ratis_core`** — DB engine, settings (`ratis_settings.json` clé `data_reconciliation`)
- **NRC repos** (Bloc B) — `repositories/name_resolution_repository.py`, `repositories/retailer_resolution.py`
- **NT (notifier)** — `POST /api/v1/notify` cross-service via `INTERNAL_API_KEY`

---

## Dépendances externes (tiers)

Aucune — tous les writes restent en BDD locale. NT consomme Expo Push API mais le batch ne l'appelle pas directement.

---

## Décisions d'architecture clés

### Séparation argent vs métier

Pas de mélange avec `ratis_batch_reconciliation` (financier). 2 batches indépendants. Le seul recouvrement = `cabecoin_transactions` (table partagée), mais isolé via `reference_type` :
- `ratis_batch_reconciliation` écrit `reference_type IN ('scan', 'cashback_credit', 'cashback_refund', ...)`
- `ratis_batch_data_reconciliation` écrit `reference_type='retro_scan'`

Pas de risque de conflit / double crédit.

### 4 jobs séquentiels avec try/except per-job

```
run.py
├─ try: ean_recovery()         # Job 1 — Bloc I NRC
├─ try: store_mdd_vote()       # Job 2 — phase 2
├─ try: price_disambiguate()   # Job 3 — phase 2
└─ try: retro_cab()            # Job 4 — collect résolus + notif
```

Si un job plante, les autres tournent. Ordre matters : `retro_cab` lit les résultats des 3 précédents.

### Idempotence par construction

- `product_name_resolutions` UNIQUE (scan_id, source_type, normalized_label) + ON CONFLICT DO NOTHING
- `cabecoin_transactions` UNIQUE (reference_type, reference_id) + ON CONFLICT DO NOTHING
- Statuses (`scan.status`, `receipt.status`, `item.status`) idempotent (UPDATE WHERE status='X' AND new_data IS NOT NULL)

Le batch peut être rerun sans danger après un crash.

### Notif gratitude : trigger NT, pas planifier

NT a déjà la robustesse (tenacity retry, dedup, quiet hours, daily cap, push token management, log persistant). Le batch fait juste un `POST /api/v1/notify` sync HTTP fire-and-forget. NT renvoie 202 immédiatement et traite en background.

Le batch ne :
- Maintient pas de queue interne
- Ne gère pas le retry
- Ne gère pas la dedup

Le batch s'engage juste à dire "envoie cette notif". NT s'engage à la livrer. **DRY respecté.**

### Aggrégation par user (anti-spam)

`retro_cab` aggrège par `user_id` : 1 notif max par user par run, avec contenu agrégé "X scans validés, Y CAB crédités". Si l'user a 50 scans résolus en 1 run, il reçoit 1 notif (pas 50).

### Daily cap NT applicable

Le `retro_cab_gratitude` est une notif normale (compte dans le daily cap NT). Le batch tournant à 06:00 UTC, l'user n'a normalement pas atteint son cap → la notif passe. Si exception (l'user a déjà spam de notifs) → NT skip silencieusement, c'est OK.

V2 backlog : inventaire des notifs in-cap / out-of-cap.

### Audit via structured logs (pas de table dédiée V1)

```python
log.info({
    "job": "ean_recovery",
    "count_processed": 42,
    "count_resolved": 8,
    "count_skipped": 34,
    "count_errors": 0,
    "duration_ms": 1234,
})
```

Capture via Sentry (en prod) ou grafana V2. Si V1+ on veut un dashboard "santé batch reconciliation", on ajoutera une table `data_reconciliation_runs` (YAGNI maintenant).

---

## Flow principal — détail par job

### Job 1 — `ean_recovery` (Bloc I NRC)

```python
INPUT:  scans WHERE status='unresolved'
              AND scan_type='receipt'
              AND created_at > now() - INTERVAL '60 days'
ACTION: pour chaque scan, retry match via :
        - exact lookup retailer-keyed (consensus mis à jour depuis last run)
        - fuzzy retailer-wide (GIN trgm index, Bloc B NRC)
        - recovery EAN partiel (Levenshtein ≤ 2 + sim nom > 0.75 + filter retailer_id)
OUTPUT: si match trouvé →
        - UPDATE scan SET status='matched', match_method=...
        - INSERT product_name_resolutions (idempotent)
        - INSERT ocr_knowledge si confidence > 0.85 (auto-feed)
```

### Job 2 — `store_mdd_vote`

```python
INPUT:  receipts WHERE status='pending_user_reconciliation'
                AND store_candidates JSONB IS NOT NULL
ACTION: pour chaque receipt :
        - lis les items du ticket
        - pour chaque store candidate :
            count_items_matching_mdd(retailer_id, items)
        - calcule confidence_per_store
        - si max > 70% → tranche
OUTPUT: si vote tranche → UPDATE receipt SET store_id=... + status='matched'
                       + INSERT pipeline_audit_log entry
```

**MDD reference data** : table `retailer_mdd_brands(retailer_id, brand_name)` à seed
- Carrefour : "Reflets de France", "Carrefour Bio", "Carrefour Discount", ...
- Auchan : "MMM", "Auchan Bio", "Pouce", ...
- Leclerc : "Eco+", "Marque Repère", ...
- Intermarché : "Pâturages", "Monique Ranou", "Chabrior", ...
- Lidl : "Milbona", "Combino", "Freeway", ...
- Casino : "Casino Bio", "Casino Délices", ...

(Liste à compléter par le SA — input from PRODUCT.md ou recherche propre.)

### Job 3 — `price_disambiguate`

```python
INPUT:  parsed_ticket_items WHERE status='disambiguation'
                          AND disambiguation_candidates IS NOT NULL
ACTION: pour chaque item :
        - resolve retailer_id depuis le receipt store
        - query price_consensus(retailer_id, ean) → consensus_price_cents
        - si une candidate ±10% du consensus → tranche
OUTPUT: si tranche → UPDATE item SET price_cents=... + status='matched'
                  + INSERT pipeline_audit_log
        sinon → reste disambiguation (humain requis)
```

### Job 4 — `retro_cab`

```python
INPUT:  scans WHERE accepted_at > <last_run_started_at>
              AND id NOT IN (SELECT reference_id FROM cabecoin_transactions
                             WHERE reference_type='retro_scan')
ACTION: aggrégation par user :
        for user_id, scans in group_by_user(new_matches):
            for scan in scans:
                amount = compute_retro_cab(scan)  # cf settings cab_economy
                INSERT cabecoin_transactions (
                    reference_type='retro_scan',
                    reference_id=scan_id,
                    amount=amount,
                ) ON CONFLICT DO NOTHING
                total += amount
            httpx.post(NOTIFIER_URL, json={
                "user_id": str(user_id),
                "template": "retro_cab_gratitude",
                "data": {"scans_count": len(scans), "cab_total": total},
            })
            # NT renvoie 202, retro_cab passe à l'user suivant
```

---

## Paramètres `ratis_settings.json`

Section nouvelle `data_reconciliation` :

```json
"data_reconciliation": {
  "ean_recovery": {
    "lookback_days": 60,
    "fuzzy_levenshtein_max": 2,
    "fuzzy_similarity_min": 0.75,
    "ocr_knowledge_auto_feed_confidence_min": 0.85
  },
  "store_mdd_vote": {
    "min_majority_pct": 70,
    "min_items_to_vote": 3
  },
  "price_disambiguate": {
    "consensus_tolerance_pct": 10
  },
  "retro_cab": {
    "max_lookback_hours": 48,
    "notif_template": "retro_cab_gratitude"
  }
}
```

À ajouter dans `ratis_core/ratis_core/config/ratis_settings.json` au moment de l'implémentation Job 1.

---

## Monitoring / logs

- Structured logs JSON via `logging.info` (capturé par Sentry en prod).
- Alerts : `count_errors > 0` → Sentry warning (configurable per-job).
- V1+ : table `data_reconciliation_runs(id, started_at, completed_at, dry_run, job_stats JSONB)` pour dashboard admin (YAGNI maintenant).

---

## Limitations connues (V1)

- **Pas de retry intra-batch** : si NT 5xx, l'user rate sa notif gratitude (le CAB est crédité). Pas de re-trigger au prochain run (anti-doublon notif).
- **Pas de daily cap exempt** : `retro_cab_gratitude` peut être skippée par NT si l'user a déjà N notifs ce jour. Edge case rare en pratique (batch tourne à 06:00 UTC, daily cap reset).
- **Job 2 store_mdd_vote** : dépend d'une table `retailer_mdd_brands` à seed (pas existante en main). Bloqué tant que pas créée.
- **Job 3 price_disambiguate** : dépend de `parsed_ticket_items.disambiguation_candidates JSONB` à introduire par `ARCH_receipt_reconciliation.md` (à brainstormer ensuite).
- **MDD coverage** : la liste MDD par retailer doit être complète. Si manquante / partielle, vote moins fiable. À étendre progressivement.

---

## Plan d'implémentation par phases

### Phase 1 — Jobs autonomes (post merge ARCH)

| Bloc | Description | Dépendances | Status |
|---|---|---|---|
| **Phase 1.A** — scaffold batch | `pyproject.toml`, `run.py`, dossier `data_reconciliation/`, conftest.py, GitHub Action workflow | aucune | ✅ done (PR `feat/batch-data-reconciliation-phase-1`) |
| **Phase 1.B** — Job 1 ean_recovery | implémentation + tests TDD | NRC Bloc B+C (ok) | ✅ done |
| **Phase 1.C** — Job 4 retro_cab | implémentation + tests TDD | NRC Bloc B+C+D (ok) | ✅ done |
| **Phase 1.D** — Settings data_reconciliation | ajout section JSON | Phase 1.B+C | ✅ done |

#### Décisions prises pendant l'implémentation Phase 1 (2026-05-02)

- **Migration `20260502_2100_retroscan`** : extension additive des CHECK
  ``cabecoin_transactions_reference_type_check`` (ajout `retro_scan`) et
  ``cabecoin_transactions_reason_check`` (ajout `retro_scan`) +
  partial unique index ``uq_cabtx_retro_scan_credit`` pour la
  guarantie d'idempotence à l'application layer (rerun du batch ne
  peut pas double-créditer le même scan).
- **Job 1 — `match_method` du PNR row** : utilisé `'observed_name'`
  côté ledger (le CHECK ``pnr_match_method_check`` n'accepte pas
  `consensus_match`). `consensus_match` reste sur ``scans.match_method``
  comme prévu par la cascade matcher (cohérent avec la migration
  `20260502_1700_consmatch`).
- **`scans.accepted_at`** mentionné dans le brief n'existe pas dans le
  modèle V1 — utilisé `status_updated_at` à la place pour le filtre
  lookback du Job 4. Sémantiquement équivalent : c'est le timestamp de
  la dernière transition d'état (à `matched`).
- **Stage E.2 partial EAN recovery** : commenté (TODO Phase 1+) — sera
  ajouté si l'alpha montre que la confusion de digits OCR est un vrai
  failure mode. Pointer inline dans `ean_recovery.py`.
- **NotifType `retro_cab_gratitude`** : ajouté au Literal de
  ``webservices/ratis_notifier/routes/notify.py`` + au mapping
  ``ratis_settings.json::notifier.notification_types``. Fait partie de
  ce PR pour une release atomique (R33 — pas de hack provisoire avec
  `cashback_available`).
- **Notif via `ratis_core.notifier_client.notify_user`** plutôt que
  `httpx.post` direct — DRY (R18) + bénéficie du retry / log /
  fire-and-forget contract du shared module.

### Phase 2 — Jobs dépendants (post `ARCH_receipt_reconciliation.md`)

| Bloc | Description | Dépendances |
|---|---|---|
| **Phase 2.A** — Job 2 store_mdd_vote | impl + table `retailer_mdd_brands` + seed MDD | `ARCH_receipt_reconciliation` mergée (introduit `pending_user_reconciliation` status) |
| **Phase 2.B** — Job 3 price_disambiguate | impl + tests | `ARCH_receipt_reconciliation` (introduit `disambiguation` status + `disambiguation_candidates JSONB`) |

### Phase 1+ (deferred — TODOs in code)

- **Stage E.2 partial EAN recovery via Levenshtein on EAN string** —
  `batch/ratis_batch_data_reconciliation/data_reconciliation/ean_recovery.py:372`.
  Postponed pending alpha telemetry sur la fréquence réelle de la
  digit-confusion OCR. Cf `ARCH_cross_retailer_consensus.md § Stage E.2`
  pour le design détaillé. Hook prévu dans la boucle existante du Job 1.

### Phase V2 (backlog)

- Trust score dégradation (job 5)
- Auto-disambiguate via consensus prix sur tickets historiques (étendu Job 3)
- Inventaire notifs in-cap / out-of-cap
- Table `data_reconciliation_runs` + dashboard admin

---

## FAQ vectorisée

**Q : Pourquoi pas étendre `ratis_batch_reconciliation` existant ?**
R : Ce batch est 100% financier (CAB integrity, cashback expiry, withdrawals). Mélanger logique financière (audit strict, never purge) et métier (correctionnelle, consensus changeant) violerait la séparation des concerns. Décision produit 2026-05-02.

**Q : Pourquoi pas une queue côté batch pour les notifs ?**
R : NT a déjà toute la robustesse (retry tenacity, dedup, quiet hours, daily cap, log). Une queue côté batch dupliquerait la logique. DRY respecté → batch trigger only, NT gère la livraison.

**Q : Comment on évite les doubles crédits CAB ?**
R : `cabecoin_transactions` UNIQUE (reference_type, reference_id). Si rerun, ON CONFLICT DO NOTHING. Le SELECT initial filtre les scans déjà créditer.

**Q : Que se passe-t-il si un scan passe matched grâce au batch puis l'user le supprime via DELETE /account ?**
R : Cascade RGPD : `scan.id ON DELETE CASCADE` propage à `cabecoin_transactions.reference_id` ? Non — la table cabecoin_transactions ne CASCADE pas (NEVER PURGE - audit légal). Le crédit reste en place. C'est intentionnel (anonymize, not delete).

**Q : Comment l'user voit qu'un scan a été résolu rétroactivement ?**
R : Notif push "🎉 X scans validés cette nuit" + le scan apparaît matched dans son historique au prochain refresh.

**Q : Pourquoi 06:00 UTC ?**
R : Après off_sync (04:00, peut prendre 1-2h) et future siren_sync (05:30). Heure de réveil Paris (07:00-08:00) → user voit la notif au réveil.

**Q : Que faire si un seed MDD est faux (ex: "Carrefour Bio" assigné à Auchan par erreur) ?**
R : Job 2 dépend de la qualité du seed. Si erreur identifiée, fix dans la table `retailer_mdd_brands` + rerun batch (idempotent). V2 : interface admin pour curer la table MDD.

---

## Glossaire

- **MDD** : Marque De Distributeur (private label). Ex : Carrefour Bio, Reflets de France, Eco+, MMM.
- **Bloc I NRC** : Bloc batch nightly du `ARCH_cross_retailer_consensus.md` qui re-balaie les scans unresolved.
- **`reference_type='retro_scan'`** : enum value sur `cabecoin_transactions` pour isoler les crédits rétroactifs des crédits temps-réel.
- **Disambiguation** : status d'item parsé quand 2+ OCR runs ont produit des valeurs différentes (price, name, qty) — l'item est en attente de tranchage.
- **`pending_user_reconciliation`** : status de receipt après rescan multi-OCR — receipt en attente de réconciliation (utilisé Job 2).
- **Retro CAB** : crédit CAB sur un scan qui passe d'unresolved à matched grâce au batch (vs CAB temps-réel sur scan accepted at upload time).
