---
type: batch-global
service: ratis_batch_referral_payout
status: production
parent: ARCH_RATIS
sub_archs: []
related: [ARCH_REWARDS, ARCH_referral, ARCH_gift_cards]
tech: [Python, SQLAlchemy, Postgres, httpx]
tables: [gift_card_orders, referral_uses, subscriptions]
env_vars: [DATABASE_URL, REWARDS_BASE_URL, INTERNAL_API_KEY]
tags: [batch, referral, gift-cards, cashback, anti-churn]
business_domain: cashback
rgpd_concern: false
updated: 2026-04-24
---

# ratis_batch_referral_payout — payout gift-cards parrainage

> Batch CLI qui paye les gift-cards de parrainage (`referral_uses` éligibles → `gift_card_orders`) une fois le filleul passé la fenêtre anti-churn 30j (`eligible_at`). Idempotent par `UNIQUE(source_type,source_ref_id)`.
> @tags: batch referral gift-cards cashback anti-churn payout 30-days eligible_at referral_uses idempotent
> @status: LIVRÉ V0
> @subs: auto

> [[ARCH_RATIS]] · relations : [[ARCH_REWARDS]], [[ARCH_referral]], [[ARCH_gift_cards]]

## Index

- [Résumé en une phrase](#résumé-en-une-phrase) · L.38
- [Responsabilité](#responsabilité) · L.42
- [Fréquence d'exécution](#fréquence-dexécution) · L.51
- [Tables lues / écrites](#tables-lues-écrites) · L.57
- [Dépendances internes (autres services/libs ratis)](#dépendances-internes-autres-serviceslibs-ratis) · L.68
- [Dépendances externes (tiers)](#dépendances-externes-tiers) · L.75
- [Décisions d'architecture clés](#décisions-darchitecture-clés) · L.80
- [Flow principal](#flow-principal) · L.112
- [Paramètres](#paramètres) · L.140
- [Monitoring / logs](#monitoring-logs) · L.146
- [FAQ vectorisée](#faq-vectorisée) · L.154
- [Glossaire](#glossaire) · L.176

---

## Résumé en une phrase

ratis_batch_referral_payout est un batch CLI quotidien qui traite les récompenses de parrainage en `gift_card_orders` avec `source_type='referral_reward'` arrivées à leur `eligible_at` (30 jours après souscription du filleul), vérifie que le filleul est **toujours activement abonné** (anti-churn-farming), et notifie ratis_rewards pour émettre la gift-card via Runa — ou marque l'ordre `churned` si le filleul a churné.

## Responsabilité

- ratis_batch_referral_payout fetch les `gift_card_orders` `source_type='referral_reward' status='pending'` dont `eligible_at <= now()` et join avec `referral_uses` pour identifier le filleul
- ratis_batch_referral_payout vérifie pour chaque ordre si le filleul (`referred_user_id`) a encore une subscription `status='active'` avec plan `monthly` ou `annual`
- Si filleul encore abonné → ratis_batch_referral_payout POST `/rewards/gift-cards/{order_id}/issue` sur ratis_rewards (Bearer INTERNAL_API_KEY) pour déclencher l'émission Runa
- Si filleul a churné → ratis_batch_referral_payout marque l'ordre `status='churned' failed_at=now()` — la récompense n'est **jamais** émise (protection contre le churn-farming)
- ratis_batch_referral_payout est fire-and-forget sur le HTTP : si ratis_rewards répond 4xx/5xx, on compte une erreur mais on continue ; ratis_rewards a sa propre logique de retry/webhook pour l'API Runa
- ratis_batch_referral_payout retourne un dict stats `{candidates, issued, churned, errors, dry_run}`

## Fréquence d'exécution

- **Workflow GitHub Actions** : `.github/workflows/batch_referral_payout.yml`
- **Cron** : `0 4 * * *` (quotidien 04h00 UTC) — actuellement désactivé (DB locale)
- **Déclenchement manuel** : `workflow_dispatch` (avec option `--dry-run`)

## Tables lues / écrites

| Table | Opération |
|---|---|
| `gift_card_orders` | lecture (pending + source_type='referral_reward' + eligible_at ≤ now) |
| `gift_card_orders` | UPDATE `status='churned', failed_at=now()` pour les churnés |
| `referral_uses` | lecture (join pour récupérer `referred_user_id`) |
| `subscriptions` | lecture (vérif plan + status='active' du filleul) |

**Note** : ratis_batch_referral_payout ne crée jamais de gift_card_order et n'écrit pas dans les tables Runa / gift_cards — ces responsabilités appartiennent à ratis_rewards (endpoint `/rewards/gift-cards/{order_id}/issue`).

## Dépendances internes (autres services/libs ratis)

- [[ARCH_CORE]] — `make_engine`, `require_env("DATABASE_URL", "REWARDS_BASE_URL", "INTERNAL_API_KEY")` (en mode live uniquement)
- [[ARCH_REWARDS]] — appel HTTP POST `{REWARDS_BASE_URL}/rewards/gift-cards/{order_id}/issue` avec `Authorization: Bearer {INTERNAL_API_KEY}`, ratis_rewards gère derrière l'API Runa (Runa = provider de gift-cards)
- [[ARCH_referral]] — la création des `gift_card_orders` `source_type='referral_reward'` est faite par le flow referral côté ratis_rewards (source upstream)
- [[ARCH_gift_cards]] — gestion des gift-cards Runa côté ratis_rewards

## Dépendances externes (tiers)

- **ratis_rewards** (interne) — endpoint `/rewards/gift-cards/{id}/issue` qui appelle Runa en BackgroundTask. Le batch ne parle pas directement à Runa → isolation des concerns.
- **Runa** — fournisseur de gift-cards, appelé indirectement via ratis_rewards (V1 — partenariat à confirmer post-KYB).

## Décisions d'architecture clés

### DA-01 — Le batch ne parle pas à Runa directement

**Choix** : POST HTTP interne à ratis_rewards qui, lui, appelle Runa
**Alternative rejetée** : client Runa intégré dans le batch
**Raison** : isolation — ratis_rewards a déjà la logique Runa (retry, signature, webhook reconciliation, idempotence). Dupliquer cette logique dans le batch serait un risque de drift. Le batch se contente de **déclencher** l'émission, ratis_rewards est responsable du succès final.

### DA-02 — `eligible_at` = anti-churn-farming

**Choix** : délai de 30 jours entre la souscription du filleul et l'émission du gift-card
**Alternative rejetée** : récompense immédiate à la souscription
**Raison** : sans délai, un parrain pourrait inciter son filleul à souscrire puis annuler immédiatement → récompense 5€ pour 0€ payé côté Ratis. Le délai 30j + vérification subscription active au moment du payout garantissent que le filleul a au moins payé un mois complet avant que Ratis paye la récompense.

### DA-03 — Statut `churned` permanent, pas `retry`

**Choix** : un filleul churné → `status='churned'`, terminal (migration `20260517_1600_gift_card_churned_status` — H3 audit fix ; anciennement `status='failed'`)
**Alternative rejetée** : re-check plus tard si le filleul re-souscrit
**Raison** : la récompense est liée à un événement ponctuel (souscription initiale suivie de 30j d'abonnement). Si le filleul churne puis revient plus tard, c'est une nouvelle acquisition, pas une récupération de la précédente. Simplifie la logique — chaque `gift_card_orders` ligne a un cycle de vie unique et terminal. Le statut dédié `churned` (distinct de `failed` = vraie erreur Runa) permet aux audits fiscaux de distinguer les deux cas.

### DA-04 — Fire-and-forget sur le HTTP vers ratis_rewards

**Choix** : timeout 10s, erreurs loggées, le batch continue
**Alternative rejetée** : retry + backoff côté batch
**Raison** : ratis_rewards a déjà sa propre résilience (retry Runa, webhook d'update). Si le batch re-submit l'ordre au prochain run, `/issue` doit être idempotent côté ratis_rewards (contrat). Dupliquer le retry dans le batch crée des risques de double-issue.

### DA-05 — `require_env` différenciée live vs dry-run

**Choix** : en dry-run, seul `DATABASE_URL` est requis ; en live, `DATABASE_URL + REWARDS_BASE_URL + INTERNAL_API_KEY`
**Alternative rejetée** : toujours tout requis
**Raison** : permettre de faire un dry-run local depuis une DB de test sans avoir à configurer REWARDS_BASE_URL pointant vers un ratis_rewards live. Utile pour tests d'intégration.

## Flow principal

### Flow 1 — Payout journalier

1. `main()` parse `--dry-run`, `require_env` selon mode
2. `run(session_factory, dry_run)` ouvre une Session
3. `fetch_eligible_orders(db)` : SELECT `gift_card_orders` source_type='referral_reward' + status='pending' + eligible_at ≤ now, JOIN `referral_uses ON ru.id::text = gco.source_ref_id`
4. Pour chaque `EligibleOrder` (order_id, referrer_user_id, referral_use_id, referred_user_id) :
   - `is_still_subscribed(db, referred_user_id)` : SELECT plan FROM subscriptions WHERE user_id = :uid AND status = 'active' LIMIT 1 → return plan ∈ {monthly, annual}
   - Si churné (plan NULL ou non actif) :
     - En dry-run : log "churned", stats["churned"]++, continue
     - Sinon : `mark_churned(db, order_id)` UPDATE status='churned' failed_at=now(), stats["churned"]++
   - Si encore abonné :
     - En dry-run : log "would notify", stats["issued"]++, continue
     - Sinon : `notify_rewards_to_issue(order_id)` POST httpx Bearer INTERNAL_API_KEY timeout=10s
       - 2xx → stats["issued"]++
       - 4xx/5xx/network → stats["errors"]++, log
5. Commit final (hors dry-run)
6. Log stats, exit code 0

### Flow 2 — Appel ratis_rewards

1. Construct `url = {REWARDS_BASE_URL}/rewards/gift-cards/{order_id}/issue`
2. `httpx.post(url, headers={"Authorization": f"Bearer {INTERNAL_API_KEY}"}, timeout=10)`
3. `resp.raise_for_status()` → `True` si 2xx
4. `HTTPStatusError` → log "HTTP {code} for order={id}", return False
5. Toute autre exception (network, timeout) → log "network error for order={id}", return False

## Paramètres

- Pas de paramètres dans `ratis_settings.json` — tout est piloté par les rows DB (`eligible_at`, `status`, `plan`)
- La **valeur** de la gift-card est définie à la création de la ligne `gift_card_orders` (upstream ratis_rewards via `settings.referral.gift_card_amount_cents`, `gift_card_brand_id`)
- Délai 30j : appliqué à la **création** de l'ordre (pas par le batch), lecture seule ici

## Monitoring / logs

- Log format : `%(asctime)s %(levelname)s %(message)s`
- Chaque order processé : log ligne `order={id} referred_user={uid} {churned|still subscribed}`
- Stats finales : `referral_payout: {"candidates": N, "issued": I, "churned": C, "errors": E, "dry_run": bool}`
- Exit code 130 sur SIGINT (KeyboardInterrupt)
- Pas de `batch_sync_log` écrit actuellement — à aligner avec les autres batches

## FAQ vectorisée

### Pourquoi ratis_batch_referral_payout attend-il 30 jours avant de payer la récompense ?

C'est une protection anti-churn-farming. Si Ratis émettait la gift-card immédiatement à la souscription du filleul, un parrain pourrait inciter son filleul à souscrire puis annuler dans les premiers jours → Ratis paye 5€ de récompense pour ~0€ de revenu. Le délai de 30j (avec vérification subscription `active` au moment du payout) garantit que le filleul a consommé au moins un cycle complet d'abonnement avant le versement.

### Pourquoi ratis_batch_referral_payout ne parle-t-il pas directement à Runa ?

Séparation des responsabilités : ratis_rewards détient **toute** la logique Runa (clés API, retry, signature, webhook reconciliation, mapping brand_id → product_id). Dupliquer cette logique dans le batch créerait un risque de drift entre les deux intégrations. Le batch se contente de dire à ratis_rewards "émet l'ordre X" via POST HTTP. Ratis_rewards appelle Runa en BackgroundTask avec sa résilience propre.

### Que se passe-t-il si le filleul annule sa subscription juste avant le cron ?

ratis_batch_referral_payout fait un SELECT `subscriptions WHERE user_id = :uid AND status = 'active'` au moment du run. Si le filleul a annulé la veille (status='cancelled' ou absent), la requête retourne NULL → le plan n'est pas dans {monthly, annual} → l'ordre est marqué `churned`. La fenêtre de race est minime (minutes) mais possible — acceptable : anti-churn > payout parfait.

### Comment tester ratis_batch_referral_payout localement ?

`uv run pytest batch/ratis_batch_referral_payout/tests/` pour la suite complète (conftest + test_payout). Pour un dry-run : `uv run python batch/ratis_batch_referral_payout/payout.py --dry-run` — log les candidats + décisions sans commit DB ni HTTP. Pour un run live local : nécessite un ratis_rewards tournant localement et `REWARDS_BASE_URL=http://localhost:8004`.

### Comment ratis_batch_referral_payout gère-t-il l'idempotence ?

Double niveau : (a) le batch ne process que les ordres `status='pending'` — un ordre déjà `issued`/`failed`/`churned` est ignoré. (b) L'endpoint `/rewards/gift-cards/{id}/issue` côté ratis_rewards doit être idempotent (contrat interne) : si le batch re-soumet un ordre à cause d'un crash post-commit-pré-commit, ratis_rewards ne doit pas créer de double émission Runa. L'UNIQUE (source_type, source_ref_id) sur `gift_card_orders` ajoute une garde DB.

## Glossaire

- **DA-XX** : décision d'architecture numérotée
- **Referral / parrainage** : un user (parrain) invite un autre (filleul) à s'inscrire, le filleul souscrit un abonnement, les deux reçoivent une récompense après 30j
- **eligible_at** : timestamp à partir duquel un ordre de gift-card parrainage devient éligible au payout (~30j après souscription filleul)
- **Churn-farming** : tentative d'exploiter les récompenses parrainage en inscrivant puis désinscrivant rapidement
- **Runa** : fournisseur SaaS de gift-cards digitales (contrat V1 à confirmer post-KYB)
- **KYB** : Know Your Business — processus de vérification légale de Ratis SAS requis par Runa pour activation commerciale
- **BackgroundTask** : FastAPI primitive permettant d'exécuter une tâche après avoir renvoyé la réponse HTTP (utilisé par ratis_rewards pour appeler Runa sans bloquer le batch)
