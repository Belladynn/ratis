# Prod Checklist

Tout ce qui doit être traité avant la mise en production. Alimenté au fil du développement.

---

## Sécurité

- [ ] **pg_hba.conf** : `trust` → `scram-sha-256`
- [ ] **Frontend — rendu produits** : vérifier qu'aucun champ produit OFF (`name`, `brands`, `quantity_raw`, etc.) n'est rendu comme HTML (dangerouslySetInnerHTML / WebView) — risque XSS si l'API OFF est compromise
- [ ] **Apple JWKS cache** : migrer vers Redis (in-memory par worker — duplication sous Gunicorn)
- [ ] **JWT RS256 — paire de clés prod** : générer la paire prod une fois
  via `./scripts/gen-jwt-keys.sh` sur l'hôte, placer `jwt_private.pem` +
  `jwt_public.pem` dans `secrets/` (gitignored, jamais commité), distincte
  des paires dev/test. `docker-compose.prod.yml` les monte en read-only.
  Runbook de rotation : voir `ARCH_AUTH.md` DA-44.
- [ ] **Cashback webhook — secrets par provider** : le secret unique
  `CASHBACK_WEBHOOK_SECRET` est remplacé par un secret par provider
  (`CASHBACK_WEBHOOK_SECRET_{AFFILAE,AWIN,CJ}`, + `_PREV` pour la rotation).
  Renseigner les 3 vars dans le `.env.prod` de `ratis_rewards` AVANT de
  redéployer le service — sinon le guard `require_env` du lifespan le fait
  crasher au boot (fail-fast voulu).
- [x] **Rate limiting** : `slowapi` câblé — `/auth/login` 5/min, `/auth/register` 5/min, `/account/change-password` 5/min, `/auth/refresh` 1/hour. ✅
- [ ] **Révocation access token post change-password** : N/A en V1 (OAuth-only, pas de change-password). À traiter si auth email/password ajoutée plus tard.
- [ ] **PostgreSQL local Windows** : vérifier qu'il ne tourne pas en prod

### Durcissement db-write-pipeline (V1 module `db`) — avant lancement

Audit 2026-05-19 : l'architecture V1 (agent propose → dry-run sandbox → invariants → revue LLM → gate humain → exécution transactionnelle) défend bien contre un agent *buggé*, moins contre un agent *manipulé* (prompt injection). La sécurité réelle des écritures prod repose sur le gate humain. À durcir avant qu'il y ait des données / de l'argent réels :

- [x] **Invariants indépendants** : les `checks[]` sont rédigés par l'agent dans la proposition → un agent manipulé écrit des checks qui passent. Définir des invariants au niveau du **catalogue de procédures** (ex : `support_credit_cab` impose `delta ≤ N`), non rédigeables par l'agent. ✅ HSP1 (#534) — manifeste TOML `[[affects]].rows` + verifier pglast.
- [x] **break-glass** : saute dry-run + LLM. Imposer la friction de re-saisie (façon tables-argent SP6) à *tout* break-glass, pas seulement aux tables-argent. ✅ HSP4 M4 (#540) — break-glass supprimé entièrement (plus de branche à durcir).
- [x] **Reprise du `Wait` n8n** : l'URL de reprise est une capability URL stockée en clair dans `db_write_approvals.resume_url` — qui lit la colonne court-circuite le gate humain. Ajouter un HMAC (ou 2e facteur) sur le POST de reprise. ✅ HSP3 M2 (#539) — N8N_RESUME_SECRET HMAC + relecture status BDD à la reprise.
- [ ] **Snapshots sandbox — M6 V2** : quick wins V1 livrés (`chmod 700` + rétention 24h + réseau Docker isolé sans port + note `PRIVACY.md`). Reste à livrer avant ouverture aux premiers utilisateurs tiers : (a) chiffrement at-rest des snapshots (clé hors Mac mini, ex : `age` + clé sur YubiKey), (b) anonymisation/masquage des colonnes PII au moment de la restauration sandbox (le dry-run teste des invariants structurels, pas des valeurs réelles — pas besoin de la vraie PII).
- [ ] **n8n = concentration de privilège** : détient secret HMAC + clé Anthropic + `ADMIN_API_KEY` + SSH Mac mini + (flag ON) capacité d'écriture prod. Garder patché, basic-auth fort ; réfléchir à isoler la capacité « écrire en prod » mieux qu'un Code node n8n.

Source : `AUDIT_2026-05-19_db_write_pipeline.md`. Brainstorm anti-prompt-injection à mener en session dédiée.

### Activation V1.1 — actes humains requis

V1.1 (durcissement, specs HSP1-HSP5 — voir `docs/superpowers/specs/2026-05-19-db-write-pipeline-v1.1-hardening-design.md`) est implémentée en autonomie par l'agent en mode bootstrap (PRs séquentielles). Quelques actes finaux restent **explicitement humains** — ils ne seront *jamais* exécutés par l'agent seul :

- [ ] **Flipper `EXECUTE_ENABLED`** via l'env var `DB_PIPELINE_EXECUTE_ENABLED=true` côté n8n (Settings → Variables, ou env du container n8n) — le nœud `Execute (HSP4 checksum)` lit `$env.DB_PIPELINE_EXECUTE_ENABLED === 'true'`, default `false`. Seulement après HSP1-HSP4 livrés *et* la pipeline éprouvée end-to-end avec `DB_PIPELINE_EXECUTE_ENABLED = false`. Décision business — c'est le go-live.
- [ ] **Couper l'accès SSH large de l'agent** (révoquer la clé `ratis-prod` de l'agent / Guillaume côté `~/.ssh/authorized_keys` de la VM). HSP5 doit avoir posé la clé MCP scopée *avant* la coupure, sinon le MCP perd l'accès prod aussi. Acte irréversible-ish — toi seul.
- [ ] ~~Passkey / clé matérielle~~ — **abandonné** : HSP3 a été livré *sans passkey* (le gate humain repose sur M1 challenge-à-taper + M2 `HUMAN_APPROVAL_SECRET` argon2id, suffisant pour V1.1). À reconsidérer en V2 si besoin d'un 2e facteur matériel.
- [ ] **Décider de la graduation d'une procédure** vers l'auto-exécution (cycle de confiance HSP3). Au cas par cas, après que la procédure a tourné N fois avec succès dans des bornes connues. Décision opérateur, jamais auto.
- [ ] **Armer les caps temporels** (HSP2 livré, dormants par défaut). Deux gestes nécessaires *en même temps* :
    1. `UPDATE app_settings SET data = jsonb_set(data, '{caps_enforced}', 'true'::jsonb) WHERE section = 'db_pipeline_caps';` (le kill-switch settings).
    2. La pipeline n8n doit poser `SET LOCAL app.caps_enforced = 'true'` + `SET LOCAL app.submission_id = '<uuid>'` avant chaque `CALL` (le kill-switch session — câblage code HSP4). Sans les deux, le trigger no-op.
  Conséquence : à partir de ce moment, toute insertion `direction='credit'` sur `cabecoin_transactions` via la pipeline est plafonnée à 20k warn / 50k block global / 5k per-user (24h glissantes). Vérifier avant que les valeurs sont calibrées (cf checkbox suivant).
- [ ] **Valider/ajuster les seuils** une fois qu'il y a des données réelles : caps temporels (`20k/50k CAB par jour`, `5k/user` — stockés en JSONB dans `app_settings.db_pipeline_caps`), seuil d'auto-exécution CAB, valeurs des bornes `min/max` par procédure dans le catalogue. Les valeurs initiales du brainstorm sont posées en config ; tu raffines à mesure que la médiane des opérations se révèle.
- [ ] **Anthropic console — monthly spend cap** : poser un budget cap mensuel sur la clé API utilisée par n8n (`https://console.anthropic.com/settings/billing` → Usage limits). Recommandation : 50 €/mois pour V1.1 (cap dur, alerte à 80 % usage). Si la pipeline boom à cause d'un agent manipulé non encore détecté par HSP4 M2/M6, c'est le dernier rempart économique. Distinct de la clé API utilisée par Claude Code locale.
- [ ] **Activer batch-sentinel (Phase 1 monitoring — DA-12)** — 3 actes côté ops, dans l'ordre :
    1. `gh secret set BATCH_SENTINEL_WEBHOOK_URL --body "https://<host>.<tailnet>.ts.net/webhook/batch-outcome"` puis `gh secret set BATCH_SENTINEL_WEBHOOK_SECRET --body "$(openssl rand -hex 32)"` (note la valeur — c'est la même que celle qui ira côté n8n).
    2. Copier la même valeur du secret dans `infra/itops/.env` sous `N8N_BATCH_SENTINEL_WEBHOOK_SECRET=...` puis `docker compose -f infra/itops/docker-compose.yml up -d n8n` (recharge le container avec la nouvelle env var).
    3. Notion UI → DB INCIDENTS → propriété `Source` (select) → New option `batch` (sans quoi l'API Notion 400 sur création de ticket). Puis n8n UI → import `infra/itops/n8n-workflows/batch-sentinel.json` → Activate. Smoke local : `N8N_BATCH_SENTINEL_WEBHOOK_SECRET=<valeur> ./scripts/test_batch_sentinel_smoke.sh`.

**Parké** (ne pas adresser tant que pas débloqué) :
- **Anti-exfil ticket-zones (HSP4)** — bloqué sur l'absence d'un système de tickets support défini. Reprendra quand ce système sera conçu (les tickets actuels du projet sont les tickets de caisse, sans rapport). Voir spec HSP1 § Hors-scope.

## Infrastructure

- [ ] **PostgreSQL** : Docker local → RDS ou équivalent (Railway/Supabase en bootstrap)
- [ ] **Variables d'environnement** : `.env.local` → secrets manager (Railway/AWS)
- [ ] **Reverse proxy** : Nginx ou Traefik devant les services
- [ ] **Alembic** : vérifier `alembic upgrade head` sur base prod avant premier déploiement
- [ ] **app_settings — seed** : après `alembic upgrade head`, exécuter `DATABASE_URL=... python -m ratis_core.seed_settings` pour peupler la table depuis `ratis_settings.json`. Sans ça, les services démarrent avec les valeurs JSON embarquées (ok en dev, risque en prod si les clés prod diffèrent).
- [ ] **app_settings — clés à valeur vide ou placeholder** : les clés suivantes ont des valeurs vides/dev dans `ratis_settings.json` et doivent être mises à jour en DB avant toute mise en prod (via `UPDATE app_settings SET data = data || '{"key": "value"}' WHERE section = '...'`) :
  - `gift_cards.annual_subscription_brand_id` → ID de la marque Runa pour la carte cadeau annuelle (vide = désactivé silencieusement)
  - `gift_cards.battlepass_brand_id` → ID de la marque Runa pour les récompenses battlepass (vide = désactivé silencieusement)
  - `subscription.price_annual_eur` → actuellement `11.99` (identique au mensuel — placeholder). Fixer le tarif annuel réel (ex. `9.99/mois × 12 = 119.88`) avant ouverture des abonnements.
  - `subscription.success_url` / `cancel_url` → vérifier que le scheme `ratis://` correspond au deeplink de l'app en prod
- [ ] **app_settings — fail-fast partiel** : si `app_settings` est vide en DB, le service démarre sans erreur (fallback JSON). Si une **section entière** est manquante → `KeyError` au démarrage du module (comportement voulu). Mais une **clé manquante dans une section existante** ne crash pas — les callers utilisent `.get()`. Vérifier manuellement les valeurs critiques après le seed.
- [x] **Batch purge** : `batch/ratis_batch_purge/` ✅ — à scheduler en cron quotidien
- [x] **Batch purge — défi communautaire expiré** : `expire_community_challenges` implémenté dans `purge.py` step 8. ✅
- [ ] **User-Agent batch OFF** : mettre à jour `Ratis/1.0 (contact: hike.muskox5137@eagereverest.com)` → URL du domaine définitif dans `batch/ratis_batch_off_sync/off_sync/api.py`
- [ ] **GitHub Secrets** : configurer `DATABASE_URL` (+ secrets services) dans `Settings > Secrets and variables > Actions` — workflows batchs OFF désactivés en attendant DB cloud
- [ ] **Dépendances** : vérifier les dernières versions (pyproject.toml, GitHub Actions) — Claude Code peut être en retard sur sa date de coupure

## Stratégie hébergement — du bootstrap au scale

**Timeline 2026 prévue** :
1. **V0 alpha (dimanche 26/04/2026)** : Hetzner Cloud CAX11 (ARM64, 4 GB RAM, 3.79€/mo). Stack docker-compose + Caddy — cf `docker-compose.prod.yml` et `Caddyfile`.
2. **~1 mois après alpha** : migration Mac mini self-hosted à la maison. Même stack docker-compose (ARM64 identique), zéro changement de config. Motivation : 0€/mo d'infra, contrôle total, apprentissage ops.
3. **Dès que le Mac mini sature** : migration AWS. Tout doit être prêt **avant** le début de la saturation pour pouvoir bascule en < 1 semaine.

### Signaux de saturation Mac mini — quand switch AWS

Surveiller en continu via Sentry / Netdata / Prometheus :
- [ ] **PaddleOCR queue** > 10 jobs en backlog pendant > 5 min consécutives (OCR ne suit plus)
- [ ] **Postgres CPU** > 70% soutenu sur 1h (besoin read replicas)
- [ ] **Mac mini CPU moyen** > 60% sur 24h glissantes (plus de marge pour absorber pics)
- [ ] **Latence p99 API** > 800ms sur routes non-OCR (goulot généraliste)
- [ ] **RAM utilisée** > 80% soutenue (risque swap → slowdown systémique)
- [ ] **Erreurs 5xx** > 0.5% requêtes sur 1h (signal de stress)

Alerting à configurer dès V0 pour ne pas découvrir la saturation en mode panique.

### Ce qui doit être prêt **avant** le switch AWS (prépa progressive post-alpha)

- [ ] **Dockerfiles prod AWS-ready** : déjà multi-arch ARM64/amd64 → compatibles Fargate ECS ARM + x86. ✅
- [ ] **Tests de charge** : script `k6` ou `locust` dans `tools/load-test/` — scénarios scan ticket + scan label + liste CRUD + login. À lancer contre Hetzner d'abord, puis Mac mini, pour établir les baselines chiffrées.
- [ ] **CI/CD AWS pipeline** : GitHub Actions qui push les images vers ECR, update les task definitions ECS. À ajouter en `.github/workflows/deploy-aws.yml` dès stabilisation Mac mini, déclenchable manuellement.
- [ ] **Terraform / CDK** : infrastructure as code pour AWS. Ressources cibles minimales : VPC + 1 RDS Postgres Multi-AZ + 1 ElastiCache Redis + ECS Fargate pour les 5 services + S3 (ou garder R2 Cloudflare) + ALB + Route 53 + ACM. **À initier dans un sous-dossier `infra/aws/` avec commentaires "pas appliqué en prod"** dès qu'on a 500+ users actifs.
- [ ] **Migration DB plan** : export `pg_dump` Mac mini → import RDS. Tester end-to-end sur un RDS staging AVANT saturation. Estimer le downtime (probablement 15-30 min avec DB < 50 GB).
- [ ] **OCR worker cluster** : plan pour faire tourner PaddleOCR sur des tasks Fargate spot dédiées (scale horizontal). Schéma : `product_analyser` API reste léger, pousse jobs Redis, workers Fargate consomment. Architecture à documenter dans ARCH dédié post-alpha.
- [ ] **R2 Cloudflare → S3 (facultatif)** : garder R2 Cloudflare (compatible S3 API, bande passante gratuite sortante — avantage cost vs S3). Seulement si R2 devient limite, migrer vers S3 + CloudFront.
- [ ] **Budget AWS estimé** : à ~10k DAU, RDS db.t4g.medium (~80€/mo) + 5 Fargate tasks (~150€/mo) + ALB (~20€/mo) + data transfer (~30€/mo) = **~280€/mo**. Vs Hetzner scaling vertical à la même charge : ~30-50€/mo (CX42). AWS se justifie quand : géo-réplication, SLA client demandés, pic de charge imprévisible, levée de fonds.

### Règles de code pour éviter le lock-in hébergeur

Déjà respectées, à garder :
- [x] Pas de dépendance Railway-specific (`$PORT` standard, pas de plugins proprio) — Dockerfiles portables ✅
- [x] Stockage images via R2 Cloudflare (pas S3) — fonctionne partout (protocole S3-compatible) ✅
- [ ] Ne jamais utiliser de services managés lock-in AWS (DynamoDB, SQS, Lambda) avant d'avoir décidé explicitement de scaler AWS. Rester sur Postgres + Redis + FastAPI tant que possible.

### Pattern d'exécution batch en prod (2026-04-30)

`docker-compose.prod.yml` expose chaque batch dans `batch/ratis_batch_*` comme un service one-shot derrière un profile `batch_<name>`. Image partagée `batch/Dockerfile`. Wrapper `./run-prod-batch.sh <name>` pour exécution distante via SSH.

- [x] Pattern documenté : `ARCH_deployment.md` § "Exécution de batches en prod"
- [x] 9 batches câblés : `consensus`, `vrac_seed`, `off_sync`, `osm_sync`, `purge`, `savings`, `referral_payout`, `mystery_announce`, `reconciliation`
- [ ] **Env vars à ajouter à `.env.prod`** (manquantes dans `.env.railway.example`) : `OFF_USER_AGENT`, `OFF_API_BASE_URL`, `OSM_OVERPASS_URL`. Défauts raisonnables fournis dans le compose, surchargeables.
- [ ] **Crons GitHub Actions toujours sur DB locale runner** : actuellement les workflows `.github/workflows/batch_*.yml` tournent contre une DB jetable, pas la prod. Migration des crons (systemd-timer Hetzner OU GH Actions qui SSH déclenche le wrapper) = PR séparée, post-alpha.
- [ ] **SSH key mandatory** : le wrapper dépend de `~/.ssh/ratis_hetzner_v3` chargé dans `ssh-agent` (cf `start_all.sh`). Sans agent loaded → die fast.
- [ ] **Premier run live** : tester `./run-prod-batch.sh vrac_seed` post-merge pour valider l'image build + exec end-to-end avant d'utiliser pour vrais ops.

## Auth

- [x] `POST /api/v1/account/logout` ✅
- [x] `POST /api/v1/account/change-password` ✅
- [x] `is_deleted` tombstone ✅ — `get_current_user` bloque immédiatement, access token expire naturellement (15 min max)
- [ ] **GET /account/preferences** : désactiver cache HTTP côté proxy (get-or-create écrit en DB)
- [ ] **Migration `payment_ref`** sur `subscriptions` + `pg_dump`
- [x] **Endpoints abonnement & historique** à implémenter dans `ratis_auth/account` :
  - `GET  /account/subscription` — statut abonnement courant
  - `POST /account/subscription` — souscrire
  - `DELETE /account/subscription` — résilier
  - `POST /account/subscription/promo` — appliquer un code promo
  - `GET  /account/cab/history` — historique transactions CAB
  - `GET  /account/cashback/history` — historique transactions Cashback

## ratis_list_optimiser

- [ ] **Celery worker — déploiement** : `ratis_list_optimiser` nécessite un process Celery worker séparé. Sans worker démarré, les `/optimize` 202 seront acceptés mais jamais exécutés — les routes resteront en `status="computing"` indéfiniment. Commande : `celery -A worker.celery_app worker --loglevel=info`. À ajouter dans `docker-compose.yml` comme service distinct du web FastAPI.
- [ ] **Redis — broker Celery** : `REDIS_URL` requis pour `ratis_list_optimiser` (broker + backend Celery). Déjà requis par `ratis_product_analyser` et `ratis_auth` — vérifier que la même instance Redis est accessible depuis le worker `ratis_list_optimiser`. Ajouter `REDIS_URL` dans les secrets Railway/AWS pour ce service.
- [ ] **Routes expirées — purge** : les `optimized_routes` ont un TTL (`expires_at`). `ratis_batch_purge` doit supprimer (ou marquer `is_expired=true`) les routes dont `expires_at < now()`. Vérifier que le batch purge inclut cette table avant mise en prod.
- [ ] **Point de départ des trajets** : ne jamais persister dans `optimized_routes.steps` (domicile = PII) — recalculer depuis position GPS temps réel. Vérifier support OSRM "départ dynamique".
- [ ] **Intégration missions savings** : `ratis_list_optimiser` doit notifier `ratis_rewards` via `rewards_client.notify_savings(user_id, savings_cents)` quand une route est complétée (X tickets, même date, stores différents). Interface inter-service à définir avec `ratis_rewards` ARCH missions. Voir `DECISIONS_PENDING.md` section "Missions savings — design".
- [ ] **"Bonne surprise" — magasin hors route** : si l'utilisateur scanne un ticket d'un magasin absent de la route fournie, comparer le prix réel au `price_consensus` max dans le périmètre. Si économie supérieure → incrément bonus sur mission savings + notification encourageante. Si le magasin n'est pas dans ses préférences → suggérer de l'ajouter. Design cross-périmètre `ratis_product_analyser` × `ratis_list_optimiser` × `ratis_rewards` — à traiter après implémentation `ratis_list_optimiser`.
- [ ] **`optimized_routes.total_price` → centimes** : actuellement `NUMERIC(10,2)` (voir convention montants en bas de ce fichier). À migrer en `INTEGER` centimes lors de la prochaine fenêtre de migration pour ratis_list_optimiser. Voir PROD_CHECKLIST "Colonnes restantes à traiter".

## RGPD

- [ ] **Politique de confidentialité publique** : page légale dans l'app
- [ ] **Audit DPO** avant lancement public
- [ ] **Consentement usage commercial** : consentement explicite distinct (V2)
- [ ] **Politique de rétention** : documenter et implémenter selon délais légaux
- [x] **R2 suppression tickets 48h** : `purge_receipt_images` implémenté dans `purge.py` step 6 — supprime R2 + met à jour `image_deleted_at`. ✅

## RGPD — Cashback handling at account deletion

> **Décision directionnelle 2026-05-08** : pending cashback withdrawals au moment du DELETE → status `abandoned`, montant absorbé côté Ratis, audit trail conservé. Justification : compte anonymisé = impossible de retourner l'argent au bon destinataire. Cf [`ARCH_seed_test_data.md`](ARCH_seed_test_data.md) Step 5 diane state.

- [x] (2026-05-11) **Schema migration** : `'abandoned'` ajouté à `cashback_withdrawals.status` CHECK via migration `20260511_2200_cashback_abandoned` (PR Wave 4 seed). ORM mirror sur `CashbackWithdrawal.status_check` ; `db/schema.sql` mis à jour ; `test_schema_sync` re-passes ; e2e seed asserts post-DELETE diane row insérable avec `status='abandoned'`. Pattern A respecté.
- [ ] **DELETE flow — extension de `account_service.delete_account`** : ajouter logique d'absorption au flow existant (qui aujourd'hui préserve cashback_withdrawals tel quel). Flow : (a) INSERT new `cashback_transactions` de type `account_deletion_absorption` qui debit le montant résiduel — préserve NEVER PURGE invariant + trace l'absorption. **Prereq** : widening de `cashback_transactions.type` CHECK pour admettre le nouveau type (CHECK actuel limité à `('CREDIT', 'BOOST', 'WITHDRAWAL')`), (b) UPDATE pending cashback_withdrawals.status = `abandoned`, (c) recompute user_cashback_balance → 0 (cohérent avec la transaction absorption), (d) INSERT `admin_audit_logs` avec anonymized user_id + montant absorbé.
- [ ] **DELETE flow UX — modal de confirmation explicite** : avant le `DELETE /account`, si user a cashback balance > 0 OU pending withdrawals, afficher modal "Vous avez X€ de cashback en attente. Si vous supprimez votre compte, ce montant ne pourra plus être réclamé. Êtes-vous sûr ?" avec opt-in explicite. Sinon = risque litige RGPD (data abandonment ≠ pecuniary abandonment).
- [ ] **T&Cs section "Suppression de compte"** : mention claire du forfeit pending cashback / withdrawals lors d'un DELETE. À ajouter à la prochaine update T&Cs avant launch public.

## RGPD — Deletion

- [x] (2026-04-20) `product_favorites` hard-delete on `DELETE /account` — PR #55. Favoris = PII-adjacent (révèlent habitudes), doivent être supprimés, pas anonymisés avec le user.
- [ ] Auditer les autres tables user-liées pour identifier d'autres PII-adjacent à hard-delete à l'anonymisation (scan_history, user_preferences, etc.)

## Réconciliation batch (ratis_batch_reconciliation)

- [x] (2026-04-30) **Idempotence — runs concurrents (DP-03)** — résolu :
  - Migration `20260415_1800_n8o9p0q1r2s3` : partial UNIQUE INDEX `uq_cabtx_scan_credit ON cabecoin_transactions(reference_id) WHERE direction='credit' AND reference_type='scan'` + `uq_cashbacktx_scan_ean_credit ON cashback_transactions(scan_id, product_ean) WHERE type='CREDIT'`.
  - Batch INSERTs en `ON CONFLICT DO NOTHING` ciblé sur ces indexes (cab.py + cashback.py).
  - Fix complémentaire (PR DP-03) : `cab._credit_scan` utilise `RETURNING id` pour ne PAS bumper `user_cab_balance` si l'INSERT est skipped par un run concurrent (sinon double crédit du solde matérialisé alors que la ligne TX reste unique).
  - Tests d'invariant : `test_reconciliation_cab.py` (`test_credit_scan_skips_balance_when_concurrent_run_won`, `test_unique_index_blocks_raw_double_insert_for_scan_credit`, `test_unique_index_allows_credit_and_debit_for_same_reference`) et `test_reconciliation_cashback.py` (`test_unique_index_blocks_duplicate_cashback_credit`, `test_unique_index_allows_credit_and_withdrawal_for_same_user`, `test_reconcile_missing_cashback_scans_idempotent_under_concurrent_runs`).
- [ ] **`check_missions_progress` idempotence en réconciliation** : le upsert SQL actuel incrémente à nouveau en contexte réconciliation — peut surcounter les missions. À vérifier et corriger avant activation du batch en production.

## OCR Store Detection (feature/ocr-store-detection)

- [ ] **Internationalisation téléphone** : `normalize_phone()` est paramétré `country_code="FR"` dès V1. Avant extension à un autre pays : implémenter le pattern de validation et la normalisation du préfixe international correspondant (`+44` UK, `+49` DE, etc.). Ne pas hardcoder FR sans le paramètre.
- [ ] **Internationalisation adresses / codes postaux** : les patterns d'extraction header (code postal `\d{5}`, mots-clés `RUE/BD/AV`) sont hardcodés France en V1. Avant extension : paramétrer `extract_store_header(lines, country_code)` et externaliser les patterns dans `ratis_settings.json` ou un fichier de config par pays.
- [ ] **ratis_batch_osm_sync** : à déployer et exécuter **avant** la mise en production de la détection store — la table `stores` doit être peuplée pour que le matching fonctionne. Vérifier couverture OSM sur les enseignes cibles avant go-live.
- [ ] **Index stores** : vérifier que `uq_stores_phone`, `uq_stores_siret`, `ix_stores_brand`, `ix_stores_postal` existent sur la DB prod après migration.
- [ ] **pg_trgm — adresse fuzzy** : vérifier que `pg_trgm` est activé et qu'un index GIN existe sur `stores.address` avant activation du matching adresse.
- [ ] **Seuils store_matching** : valeurs initiales `threshold_auto=80`, `threshold_confirm=40` à calibrer sur données réelles après les premiers scans en production. Ajustables via `app_settings` sans redéploiement.

## ratis_product_analyser

- [ ] **OSM_OVERPASS_URL** : env var requise par le resolver temps réel (Option C — store resolution cold-start) et l'endpoint `POST /scan/receipt/{id}/identify-store` (Option B). Ajouter dans les secrets Railway/AWS pour `ratis_product_analyser`. Dev local sans connexion Overpass : laisser unset → skip silencieux, aucune erreur.

- [ ] **Seeder `brand_receipt_formats` en prod** : la table `brand_receipt_formats` est vide au déploiement initial — aucun store_code ne sera extrait des barcodes. Créer un seed SQL dans `db/datafixes/` avec les formats réels (Lidl, Intermarché, Monoprix…) et l'appliquer manuellement après la migration. Tant que la table est vide, le barcode parsing renvoie silencieusement `None` — pas de crash, mais le slow-path `store_code` dans `_candidate_intersection` ne matche rien.

- [ ] **OSM sync prérequis pour store_code slow-path** : `_candidate_intersection` recherche `stores.store_code` depuis DA-23 (DP-05). La colonne est peuplée passivement via `record_fingerprints` (auto-learning). Avant le premier déploiement significatif, lancer `ratis_batch_osm_sync` pour peupler les stores connus. Sans stores en DB, le slow-path store_code ne matche rien même avec un format barcode correct.

- [ ] **Stores `user_suggested` — revue admin** : les stores créés via `POST /scan/receipt/{id}/identify-store` avec `source='user_suggested'` ont `lat=0/lng=0`. Créer une vue ou alerte admin pour identifier ces stores et les compléter avec de vraies coordonnées. Ces stores bloquent le cashback store-specific (`store_status='pending'`) jusqu'à résolution admin. Voir ARCH_store_resolution.md § Hors scope V1.

- [ ] **Store resolution — purchased_at sentinel** : les receipts résolus via `identify-store` (Option B) conservent `purchased_at = 1970-01-01` (SENTINEL_DATE) car la date OCR n'est pas re-extraite au moment de la résolution. V2 : passer `purchased_at` dans `receipt.pending_items` pour restauration lors de la résolution.

- [ ] **Endpoint produit incomplet au hasard** : `GET /api/v1/products/random-incomplete` — retourne un produit avec au moins un champ manquant (name, brands, category_id, unit, product_quantity). Utilisé par l'onglet Produits comme "produit à enrichir du jour". Pondération suggérée : prioriser les produits fréquemment scannés mais peu renseignés.

- [ ] **Rétention images étiquettes** : les images `label/` sont conservées indéfiniment pour dataset V2. Décision à prendre avant mise à l'échelle : (a) suppression après N jours, (b) archivage dans un bucket cold storage séparé, (c) opt-in utilisateur pour contribution au dataset. Implémenter via `label_image_expires_at TIMESTAMPTZ` sur `scans` + job `ratis_batch_purge`. Actuellement acceptable — données publiques, pas de PII, aucun impact RGPD direct — mais la politique doit être documentée dans `PRIVACY.md` avant ouverture publique.

- [ ] **pg_trgm en production** : vérifier que l'extension `pg_trgm` est activée (`CREATE EXTENSION IF NOT EXISTS pg_trgm`) et que l'index `gin_products_name` existe sur `products.name` avant le premier déploiement
- [ ] **RGPD — image ticket uploadée via route label** : si un user envoie un ticket via `POST /label`, l'image est stockée sous `label/` avec rétention indéfinie alors qu'elle contient des PII (date, montant, magasin). Correctif avant prod : ajouter `label_image_expires_at TIMESTAMPTZ` sur `scans` — le worker positionne ce champ à `now() + 48h` quand il détecte un ticket (`hint_mismatch:likely_receipt`), `ratis_batch_purge` supprime l'objet R2 et NULL le `label_r2_key`. Tant que ce correctif n'est pas déployé, `POST /label` ne doit pas être exposé en production.
- [ ] **batch_purge — scans label pending orphelins** : si la queue Celery est down au moment de `POST /label`, les scans sont commitées en `pending` sans task associée. `ratis_batch_purge` doit purger les scans `electronic_label` en `pending` depuis plus de N heures (ex : 2h). Message UI : "Nous avons identifié X produits" — l'user ne voit pas les pending.
- [ ] **batch_purge — R2 orphelins label batch** : si `POST /label/batch` échoue en cours de boucle d'upload (panne R2 partielle), les images déjà envoyées en R2 n'ont pas de DB record (rollback). Ces objets R2 `label/` sont orphelins sans lifecycle rule. `ratis_batch_purge` doit lister les clés `label/` du bucket et supprimer celles sans correspondance dans `scans.label_r2_key`. Acceptable en V1 (training data, pas de RGPD) — à planifier avant mise à l'échelle.
- [ ] **Mass scan — chunking côté frontend** : `POST /label/batch` est limité à 10 images par appel (`label.batch_max_images`). L'app doit découper automatiquement en batches de 10 et envoyer en arrière-plan. L'user voit "N photos en cours de traitement" — pas les batches. Si un batch échoue → retry sur ce batch uniquement. Le session_id de chaque batch est indépendant — agréger les résultats côté client.
- [ ] **EXPLAIN ANALYZE fuzzy** : vérifier que `word_similarity(name, :query) > :threshold` utilise bien le GIN index (`gin_products_name`) et non un seqscan — `EXPLAIN (ANALYZE, BUFFERS) SELECT ean FROM products WHERE word_similarity(name, 'Nutella 400g') > 0.75 ...`
- [ ] **fuzzy_confirmed → CAB** : les scans résolus par `match_method='fuzzy_confirmed'` devront générer des CabeCoins (gamification non encore implémentée — `ratis_rewards`)
- [ ] **Part B — réconciliation ticket pour scans `store_status='unknown'`** : Part A (DA-29) persiste les label scans hors rayon avec `store_id=NULL`. Part B doit consommer un ticket OCR avec `store_status='confirmed'` et géo-matcher (via `user_lat/user_lng` stockés sur les scans unknown) pour : (1) renseigner `store_id`, (2) passer `store_status='confirmed'`, (3) enqueue OCR label sur ces scans, (4) awarder CAB rétroactif. Créer ARCH dédié. Sans Part B, les scans unknown restent en DB à jamais sans récompenser l'utilisateur — fenêtre temporelle acceptable en V1 bootstrap, mais bloquant avant ouverture publique.

## RGPD — Logs

- [ ] **GPS dans les logs d'accès Uvicorn** : `GET /api/v1/product/{ean}?user_lat=...&user_lng=...` apparaît en clair dans les access logs Uvicorn/Nginx. Options : (a) désactiver les access logs Uvicorn (`--no-access-log`) et ne garder que les logs applicatifs, (b) configurer Nginx pour masquer les query params dans les logs d'accès, (c) accepter si les logs sont stockés dans un environnement sécurisé sans accès externe. Décision d'infra avant ouverture publique.

## Base de données — Cohérence tz-aware/tz-naive

- [x] **DateTime tz-naive → tz-aware sur tout le projet** ✅ — migration `c2d3e4f5a6b7` + modèles `DateTime(timezone=True)` sur toutes les colonnes. Gardes défensives `replace(tzinfo=None)` supprimées.

## Notifier

- [ ] **Audit notifications — fonctionnalités manquantes** : faire le tour complet avant le lancement. Points identifiés comme bénéficiant de notifications push :
  - [x] `challenge_milestone_unlocked` — défi communautaire : palier claimable (implémenté, envoi au user qui déclenche l'incrément)
  - [x] `battlepass_milestone_unlocked` — battlepass : palier claimable après gain de CABs (implémenté)
  - [ ] **Broadcast défi communautaire** : aujourd'hui seul l'user qui déclenche l'incrément reçoit la notif. Les autres users ne sont pas notifiés quand un palier communautaire se débloque. Nécessite un mécanisme de fan-out (tous les users actifs) — infrastructure à concevoir.
  - [ ] **Mission complétée** : `POST /gamification/missions/{id}/claim` — notifier quand une mission est claimable ou complétée
  - [ ] **Streak en danger** (`needs_repair = true`) : notif push le soir si `last_fed_at == yesterday` et streak > N jours — nécessite un batch quotidien
  - [ ] **Cashback disponible** : quand un cashback passe `pending → confirmed` — déjà prévu dans la liste des types notifier mais à vérifier si câblé
  - [ ] **Nouveau défi communautaire activé** : notif à tous les users quand un admin active un nouveau défi — fan-out, même infrastructure que broadcast ci-dessus
  - [ ] **Battlepass saison terminée** : rappel avant la fin de saison si des milestones sont unlocked mais pas claimed
- [ ] **`notify_user` synchrone** : l'implémentation actuelle fait un `httpx.post` synchrone avec timeout 5s. Sur le chemin critique (scan_accepted), les notifications sont appelées **après** le commit DB, donc hors transaction, mais elles bloquent quand même le retour HTTP si le notifier est lent. À migrer vers BackgroundTasks FastAPI ou une queue async avant prod à forte charge.
- [ ] **`NotifyRequest.type` — pas de validation** : n'importe quelle chaîne est acceptée, le titre Expo fallback sur la valeur brute. Ajouter un `Literal["scan_done", "cashback_available", "badge_unlocked", "price_alert"]` ou un enum avant prod.

## Légal / Conformité

- [ ] **Cashback — avance abonnés** : créditer le cashback immédiatement aux abonnés avant validation par la marque = on porte le risque de crédit. Vérifier avec un légiste si les montants et durées prévus (0,50 €–2 € / 5 j max) tombent sous un seuil d'exemption ACPR ou si un agrément établissement de paiement est requis.
- [ ] **"Doublez votre cashback" — publicité** : vérifier avec un légiste que le message marketing est conforme (publicité non trompeuse, DGCCRF) — la promesse doit être tenue dans 100 % des cas où l'utilisateur déclenche le boost.
- [ ] **Cabet / jeux à mise** : voir section ratis_rewards ci-dessous — ne pas implémenter sans avis juridique.

## ratis_rewards — Outils d'administration manquants

Les outils ci-dessous n'ont pas d'endpoint admin. Chaque item = opération qui requiert un accès admin en prod.

- [ ] **BattlePass — saisons** : pas d'endpoint pour créer/activer une saison (`POST /admin/battlepass/seasons`, `PATCH /admin/battlepass/seasons/{id}/activate`). Actuellement géré via SQL direct.
- [ ] **BattlePass — paliers** : pas d'endpoint pour ajouter un palier à une saison (`POST /admin/battlepass/seasons/{id}/tiers`). Géré via SQL direct.
- [ ] **Missions — config** : pas d'endpoint pour créer/modifier les templates de mission (`POST /admin/missions`, `PATCH /admin/missions/{id}`). Géré via SQL direct.
- [ ] **Définition des templates de gamification (mission templates + battlepass milestones + community_challenges templates)** : la base prod n'a aujourd'hui aucune mission active, aucune saison battlepass, aucun community challenge. Sans ces templates : (a) la prod ne peut récompenser personne, (b) le seed `ratis_seed` (cf [`ARCH_seed_test_data.md`](ARCH_seed_test_data.md) Step 4) ne peut pas être implémenté. Approche recommandée : définir un fichier source-of-truth `ratis_core/gamif_templates.py` (ou JSON config) consommé à la fois par le seed et par les admin SQL inserts prod. Volume cible : ~10 mission templates (mix daily/weekly/seasonal), 5 battlepass seasons × 30 tiers chacune (= 150 milestones), ~53 community_challenges weekly × 3 milestones chacun (= 159 milestones). Templates ≠ données générées : ce sont les blueprints qui se traduiront en `user_*` records par persona/user.
- [ ] **Achievements / succès — feature à concevoir** : pas de table `achievements` au schema aujourd'hui. Concept à designer : récompenses durables cross-saison, distinct des battlepass milestones (qui sont per-saison). Exemples : "First scan", "100 scans", "First cashback withdrawal", "First referral conversion", "1 year anniversary". À spec'er après que les missions/battlepass/community challenges soient en place. Le seed n'en seede pas pour l'instant.
- [ ] **Récompenses — RewardConfig** : pas d'endpoint admin pour modifier les récompenses par type d'action. Géré via `ratis_settings.json` uniquement.
- [ ] **XP — paliers de niveau** : pas d'endpoint pour créer/modifier les `xp_level_tiers`. Géré via SQL direct.
- [ ] **Streak — paliers de multiplicateur** : pas d'endpoint pour modifier les `streak_multiplier_tiers`. Géré via SQL direct.
- [ ] **Badges** : pas de système de badges encore implémenté — à concevoir avec le système cosmétiques.

---

## ratis_rewards

- [ ] **CAB — use cases originaux** : voir [💡 Idées rewards](https://www.notion.so/33f1a844299c81469fb9ea9807918501) — gel de mission, gel de série, stonks (boost mission), don solidaire, accès premium temporaire. Arbitrer avant V2.
- [ ] **Feed Jack — tarification réserves de nourriture** : `food_reserve_cost_cab` (actuellement 50 CABs/réserve, arbitraire) et `max_food_reserves` (actuellement 7, arbitraire) à calibrer avant launch en fonction de l'économie CABs. Questions : est-ce que 50 CABs = trop facile (dévalorise la streak) ou trop cher (frein à l'engagement) ? Valider avec les métriques d'engagement post-beta.
- [ ] **Feed Jack — rattrapage (auto vs manuel)** : décision dans `DECISIONS_PENDING.md`. Auto = meilleure rétention (l'utilisateur est protégé sans friction), Manuel = meilleure lisibilité (l'utilisateur comprend la mécanique). Trancher avant implémentation.
- [ ] **Feed Jack — trigger** : action dans l'app (tap sur Jack) définie côté frontend dans `ratis_client/ARCH_feed_jack.md`. Valider le parcours UX avant de wirer l'endpoint.
- [ ] **Feed Jack — anti-abus timezone** : le timezone étant stocké server-side, un user ne peut pas manipuler le calcul de `gap_days` à chaque request. Surveiller en beta si des patterns d'abus émergent (changements fréquents de timezone via appels API) et limiter les updates timezone à N fois/jour si nécessaire.
- [ ] **Feed Jack — lancement international** : calculs de streak validés uniquement pour `Europe/Paris` en V1. Avant ouverture à d'autres marchés, couvrir les fuseaux extrêmes (UTC+12, UTC−12) dans les tests et vérifier le comportement au changement d'heure (DST).
- [ ] **Boost de mission (Stonks)** : `POST /rewards/missions/{id}/boost` — dépenser `cab_reward_courant` CABs pour doubler target ET reward, **stackable sans limite** (intentionnellement absurde). Ajouter `boost_count INT DEFAULT 0` sur `user_missions`. Voir [💡 Idées rewards](https://www.notion.so/33f1a844299c81469fb9ea9807918501).
- [ ] **Gel de mission** : `POST /rewards/missions/{id}/freeze` — dépenser CABs pour reporter une mission à la période suivante. Coût à définir dans `ratis_settings.json`.
- [ ] **Cabet** : paris de CABs — à voir avec un avocat. Risque ANJ si hasard + valeur réelle. Ne pas implémenter sans avis juridique.
- [ ] **Système cosmétiques** : type générique `skin` utilisé dans les Défis communautaires pour couvrir badge, skin de profil, skin de mascotte, bannières, etc. Système à concevoir avant mise en prod des défis avec `reward_type='skin'`. Questions à trancher : (1) catalogue en DB (`cosmetic_items`) ou assets statiques côté frontend ? (2) inventaire user (`user_cosmetics`) — quelle table, quelle contrainte d'unicité ? (3) livraison : le claim défi INSERT dans `user_cosmetics`, le frontend lit l'inventaire. En attendant, les défis V1 peuvent n'utiliser que `reward_type='cab'`/`'xp'`/`'multiplier'`.
- [ ] **Leaderboard `leaderboard_current`** : définir le DDL de la vue matérialisée dans `ratis_batch_leaderboard/ARCH.md` avant d'implémenter le batch.
- [ ] **Réconciliation solde CAB** : batch nocturne vérifiant `balance == SUM(cabecoin_transactions)` par user — à ajouter dans `ratis_batch_reconciliation`.
- [ ] **Cartes cadeaux** : à concevoir et implémenter (ARCH + endpoints + batch reconciliation si applicable). Scope V1 ou V2 à arbitrer.

## Communication / Risques bad buzz

- [ ] **"Ratis incite à l'achat"** : risque de mauvaise interprétation au lancement. Ligne de défense : la récompense principale (label_scan, barcode_scan, OCR) ne requiert aucun achat — l'utilisateur scanne les étiquettes électroniques en rayon et repart sans dépenser. `receipt_scan` n'est pas boostable, pas sur-récompensé. Vérifier que l'onboarding est explicite sur ce point avant toute communication publique.

## Convention montants — centimes entiers

- [x] **Colonnes monétaires principales → `INTEGER` centimes** ✅ — migration `a8b9c0d1e2f3` : `scans.price`, `price_consensus.price` + `price_consensus_history.price`, `receipts.total_amount`, `cashback_transactions.amount`, `cashback_withdrawals.amount`, `user_cashback_balance.balance`, `scans.tva_amount`, `receipts.tva_total`. OCR conversion : `int(round(Decimal(str(v)) * 100))`. Voir DA-02.

- [ ] **Colonnes restantes à traiter :**
  - `optimized_routes.total_price` → `NUMERIC(10,2)` — à migrer quand `ratis_list_optimiser` sera implémenté
  - `subscriptions.price` → `NUMERIC(10,2)` — à arbitrer : passer en centimes (style Stripe) ou garder NUMERIC pour compatibilité facturation
  - `shopping_list_items.target_price` / `validated_price` → colonnes inexistantes pour l'instant — à créer en `INTEGER` centimes directement

## Observabilité

- [x] **Sentry + X-Request-ID middleware** ✅ — `sentry-sdk[fastapi]` dans les 4 services, `init_sentry(service_name)` dans chaque `main.py` (no-op si `SENTRY_DSN` absent), `RequestIDMiddleware` dans `ratis_core`. Variables : `SENTRY_DSN`, `SENTRY_ENVIRONMENT`, `SENTRY_SEND_PII`. Voir DA-17.
- [ ] **Sentry — configurer projet en prod** : créer le projet "Ratis" sur sentry.io, renseigner `SENTRY_DSN` dans les secrets Railway/AWS, `SENTRY_ENVIRONMENT=production`.

## tools/sentry_webhook — Sentry → Notion

- [ ] **Déploiement** : actuellement pensé pour tourner en local derrière ngrok. En production, déplacer sur un VPS ou container (aucune dépendance DB — simple `uv run uvicorn tools.sentry_webhook:app --port 8099`). Mettre à jour l'URL du webhook dans Sentry *Alerts → Webhook* après déploiement.
- [ ] **Variables d'environnement** : `SENTRY_WEBHOOK_SECRET`, `NOTION_TOKEN`, `NOTION_DATABASE_ID` — à ajouter dans les secrets Railway/AWS. Ne jamais committer `tools/.env.local`.
- [ ] **IP allowlist** : ajouter un allowlist des IPs Sentry (plage publiée sur leur doc) devant le endpoint `/webhook` en production (Nginx `allow`/`deny` ou règle pare-feu) pour rejeter toute requête hors Sentry avant même la vérification HMAC.
- [ ] **Rotation du secret HMAC** : si `SENTRY_WEBHOOK_SECRET` est compromis, le regénérer dans Sentry *Alerts → Webhook → Secret* et mettre à jour la variable d'environnement en même temps. Le serveur lit le secret au démarrage — redémarrage requis après rotation.
- [ ] **Sentry Alert Rule** : configurer dans Sentry *Alerts → Create Alert → Issue Alert* :
  - Condition : `A new issue is created`
  - Condition : `The issue has been seen more than 3 times` (filtre bruit)
  - Action : `Send a notification via Webhook` → URL prod + secret HMAC

## Qualité données produits (OFF)

- [ ] **batch data-clean — conception** : arbitrer les 6 questions ouvertes dans `batch/ratis_batch_off_sync/ARCH_off_sync.md` (architecture batch vs extractor vs colonnes `_clean`, `category_id`, `brand_id`, normalisation unités, déduplication tags langue, images placeholder) avant implémentation. Impact direct sur `draw_random_product(category_filter=…)` et tout filtrage par catégorie/marque.

- [ ] **Source data non-food (hygiène, ménager, beauté, papeterie, etc.)** : OFF (OpenFoodFacts) couvre uniquement l'alimentaire. Aujourd'hui la base produits Ratis n'a **aucune source officielle** pour les products non-food → quand un user scanne un shampoing, dentifrice, lessive, batterie, livre, etc. → "produit non trouvé" garanti. C'est un gap **bloquant pour le promesse "comparateur multi-magasin pour TOUTES tes courses"**. Le seed `ratis_seed` (cf [`ARCH_seed_test_data.md`](ARCH_seed_test_data.md) DA-3-bis) **NE seede PAS** ces categories pour ne pas masquer la limitation.
  - **Options à étudier avant launch** :
    - **OBP (Open Beauty Facts)** — pendant OFF pour cosmetic/beauty (free, openfoodfacts org sister-project)
    - **OPFF (Open Pet Food Facts)** — pet food (free, idem)
    - **OPF (Open Products Facts)** — generic products non-food (alpha state, license open). Coverage limitée mais plante les bases.
    - **GS1 / GTIN registry** — base mondiale officielle EAN-13. Payante (~150€/mois starter), mais APIs typed. Source-of-truth ultime pour barcode → product.
    - **Lookup en cascade** : OFF (food) → OBP (beauty) → OPFF (pet) → OPF (autres) → fallback `source='internal'` (user-suggested) avec validation admin
    - **Crowdsourcing user** : laisser le scan créer un product `source='internal'` avec `pending_review=true`, admin valide ou auto-promote après N scans concordants (cf product_knowledge `corrected=NULL=manual queue` pattern existant)
  - **Décision à prendre** : pick option(s) + plan d'intégration batch. Fait gracieusement les 3 (OFF + OBP + OPFF + OPF) en cascade ne demande pas de cash mais double la complexité du sync. GS1 simplifie mais coûte.
  - **Impact stratégique** : sans solution, Ratis reste "comparateur d'épicerie" et non "comparateur de courses". À traiter avant le marketing public-facing "tous tes achats du quotidien".

## Qualité données stores — multi-sources directory (post-alpha)

> **Contexte** (2026-04-30) — bulk import OSM unique du 27/04/2026 a manqué un Intermarché Express existant sur OSM depuis 17/06/2024 (way `1293099937`, tagué `shop=convenience` + `name` + `brand` + `addr:housenumber/street` mais SANS `addr:city/postcode`). Cause probable : notre `osm_bulk_import` exige `addr:city`+`addr:postcode` pour insérer → skip silencieux des stores avec adresse partielle. PR-B `confirm-store` flow gère ce cas user-side mais on devrait ne pas dépendre uniquement d'OSM.

- [x] **Bug osm_bulk_import** : ne plus exiger `addr:city`+`addr:postcode` à l'insertion. Insérer best-effort avec NULL, puis enrichir via reverse-geocoding batch (Nominatim sur lat/lng → city/postcode/country). Quantifier l'ampleur avant : combien de POI OSM `shop in whitelist` avec `name` mais sans `addr:city` ? (probablement 5-15% = milliers de stores manquants en FR).
  - **RÉSOLU 2026-04-30** (PR pending) — diagnostic affiné : la stricte addr:city/postcode n'était pas le bug réel ; `normalize_pbf_tags` acceptait déjà des adresses partielles. Cause racine = (a) `_ShopHandler.way()` skippait silencieusement TOUTES les ways (pas de résolution geometry), (b) tous les skip events incrémentaient un compteur sans logger l'osm_id. Fix : `apply_file(locations=True)` + centroïde des nodes pour les ways + log structured INFO `osm_skip kind=node|way osm_id=… reason=…` sur chaque skip. Régression couverte : Intermarché Express Courbevoie (way 1293099937, shop=convenience polygon). Reverse-geocoding Nominatim reste à faire séparément (peu prioritaire post-alpha — la majorité des stores ont déjà city/postcode dans OSM).
- [x] **Source FR — SIRENE INSEE** : DÉPLOYÉ (PR6 mergée 2026-05-31). Batch mensuel cron actif (`0 4 1 * *`). Pipeline complet : download ZIP → unzip Parquet → filtre APE → geocode Géoplateforme → upsert `store_consolidation`. Safety net F-14 active. Géocodage via `data.geopf.fr/geocodage` (BAN Géoplateforme, successeur api-adresse.data.gouv.fr décommissionné 2026-01).
  **RÉSOLU 2026-05-31** — `batch/ratis_batch_sirene_sync` PR6 : pipeline end-to-end câblé, cron mensuel activé, tests e2e verts.
  ~~intégrer en complément d'OSM.~~ Base officielle 38M+ établissements FR avec SIRET, enseigne, adresse vérifiée, code APE. Mise à jour quotidienne. License Etalab (gratuit, open). Approche : `batch/ratis_batch_sirene_sync` qui pull les codes APE commerce alimentaire (`47.11A` épicerie, `47.11C` supermarché, `47.11D` superette, `47.11F` hyper, `47.21Z` fruits/légumes, `47.22Z` viande, `47.23Z` poisson, `47.24Z` pain/pâtisserie, `47.25Z` boissons, `47.81Z` marchés alimentaires). Géocodage via `adresse.data.gouv.fr` (gratuit, BAN officielle FR). Coverage attendue post-merge : >95% des commerces alimentaires FR avec adresse vérifiée + SIRET.
- [ ] **Pattern multi-sources** : généraliser à un directory pluggable. Par pays cible (FR / BE / DE / UK / ES / IT…) lister les registres officiels équivalents :
  - **BE** : KBO/BCE (Banque-Carrefour des Entreprises), gratuit
  - **DE** : Handelsregister (payant, scraping bancable mais pas free dump) + UmweltBundesamt geocoding
  - **UK** : Companies House (gratuit, API+dump). Pour POIs commerciaux : OSM + manual
  - **ES** : Registro Mercantil (limité), pas d'équivalent SIRENE. OSM principal
  - **IT** : Registro Imprese (payant). OSM + Yelp/TripAdvisor scraping
- [ ] **Source globale en complément** : **Overture Maps Places** (Meta+MS+Amazon+TomTom OSS foundation, GA depuis 2024). License CDLA Permissive 2.0. Format Parquet via S3 publique. Couverture meilleure qu'OSM brut (Meta a backfill avec Facebook Places). Useful pour `opening_hours`, photos, ratings, multi-pays. Coverage FR comparable à OSM, mais densité world > OSM seul.
- [ ] **Source US/global commerciale** : **Foursquare OS Places** (lancé fin 2024, Apache 2.0, ~100M POI, taxonomie propre). À évaluer pour multi-pays.
- [ ] **NON-options** :
  - Google Maps Places API : ToS interdisent stockage persistent + tarif prohibitif (~$17/1000 calls Place Details). Réservé aux vues UI temps réel, pas aux directories.
  - Apple Maps Connect : limité, pas de bulk export public.
  - HERE Maps : commercial, dumps possibles mais cher.
- [ ] **Architecture cible (V2-3)** : `ARCH_data_sources_stores.md` (à créer) — un batch `directory_sync` par source qui écrit dans `stores` avec `source IN ('osm','sirene','overture','user_suggested','admin')`. Dédup par fuzzy match (lat/lng + name + brand). Trust score / priorité par source (admin > sirene > overture > osm > user_suggested). Endpoint admin pour merge manuel des doublons.
- [ ] **Trigger d'urgence** : tant que ce chantier n'est pas fait, dépendance forte sur le `confirm-store` user flow + run manuel `./run-prod-batch.sh osm_sync` pour delta. Pas bloquant alpha (volumes faibles).

## Missions Phase C-2 — attribute:french rollout (2026-05-11)

> **Contexte** — la PR Phase C-2 livre la column `products.origins_tags`, l'extractor off_sync étendu, le batch `ratis_batch_origins_backfill`, et le dual-emit PA `attribute:french`. Les 3 missions `product_identification + attribute:french` (daily/easy + weekly/easy + weekly/medium) **restent désactivées**. Le flip vers `is_active=true` est manuel, séquencé après le backfill prod pour éviter le bug UX "aucun produit français scanné" tant que la column est NULL partout.

Procédure opérateur :

1. **Ship la PR Phase C-2** (déjà mergée à ce stade). Vérifier que la migration `20260511_2400_phase_c2_origins_tags` est passée en prod : `SELECT column_name FROM information_schema.columns WHERE table_name='products' AND column_name='origins_tags'` retourne 1 row.

2. **Smoke test du batch en dry-run** :
   - Aller sur GitHub Actions → `batch-origins-backfill` → Run workflow
   - Cocher `dry_run: true`, mettre `max_eans: 100`, `request_delay_sec: 1.0`
   - Vérifier les logs : pas d'erreur réseau, ratio updated/empty/not_found cohérent

3. **First capped real run** (mesurer le throughput) :
   - `dry_run: false`, `max_eans: 5000`, `request_delay_sec: 1.0`
   - Attendre la fin (~1h30 avec 1 req/s)
   - Vérifier `SELECT COUNT(*) FROM products WHERE origins_tags IS NOT NULL` → ~5000

4. **Full run** :
   - `dry_run: false`, `max_eans: (vide)`, `request_delay_sec: 1.0`
   - Durée estimée : 1-3 jours selon volume de products + rate-limit OFF
   - Surveiller via Sentry (errors > 0 ?) + log progress every 1000 rows
   - Possibilité de couper et relancer librement (idempotent par `IS NULL`)

5. **Vérifier la couverture avant flip** :
   ```sql
   SELECT
     COUNT(*) FILTER (WHERE origins_tags IS NOT NULL) AS filled,
     COUNT(*) AS total,
     ROUND(100.0 * COUNT(*) FILTER (WHERE origins_tags IS NOT NULL) / COUNT(*), 1) AS pct
   FROM products
   WHERE source = 'off';
   ```
   Cible : **`pct ≥ 80%`** avant le flip. Si `not_found` est élevé (>20%), c'est OK — ces EANs sont écrits avec `[]`, ils comptent comme "filled" (juste pas français).

6. **Flip les 3 missions** — appliquer la one-row migration ou exécuter directement en SQL admin :
   ```sql
   UPDATE missions
      SET is_active = TRUE
    WHERE qualifier = 'attribute:french'
      AND action_type = 'product_identification'
      AND is_active = FALSE;
   -- Expect: UPDATE 3
   ```
   À partir de cet instant, les events `attribute:french` qui arrivent commencent à faire avancer les nouvelles `user_missions` (le runtime instancie lazily la mission au prochain action_event reçu pour un user).

7. **Vérifier en prod** : scanner un produit français (e.g. ean=`3175680011480` Évian) avec un compte test → les 3 user_missions french doivent se créer + progresser.

8. **Communication** : si la mission "Scanne 5 produits français cette semaine" doit être visible côté CL, vérifier que le screen `app/(tabs)/index.tsx` (Dashboard) re-fetch les missions au focus (déjà le cas via React Query stale time). Aucune action FE requise — la mission apparaît automatiquement.

## Performance

- [ ] **Gunicorn** : configurer nombre de workers
- [ ] **Indexes** : `EXPLAIN ANALYZE` sur requêtes fréquentes

## CI / Tests — parallélisation

Le repo a passé le cap des **~2000 tests** (mesuré 2026-04-26 alpha) :
- Backend : 1524 (auth=141, product_analyser=550, list_optimiser=96, rewards=350, notifier=37, core=60, batch=290)
- Frontend (jest) : 510

Tant qu'on tourne sur la machine de dev de Guillaume (i9 + 64GB) ça passe à <30s. En CI sur runner GitHub-hosted standard (2 CPU, 7GB), ça monte vite avec la croissance attendue (+gamif, +scan-history, +liste, +produit).

À traiter avant que ça morde :
- [ ] **pytest-xdist** : `pytest -n auto` sur les services lourds (product_analyser surtout, 550 tests). Vérifier que les fixtures DB par-test ne se cannibalisent pas (tests existants partagent une DB temp via session-scoped fixture, à vérifier).
- [ ] **jest workers** : `jest --maxWorkers=50%` est déjà le défaut ; valider que ça scale bien à 1k+ tests jest.
- [ ] **CI matrix de services** : aujourd'hui chaque service a son workflow GitHub. Les tester en parallèle (matrix par service) plutôt qu'en séquentiel global. `jobs.<svc>.strategy.matrix.service: [auth, product_analyser, ...]`.
- [ ] **Cache uv** : actuellement chaque workflow re-télécharge ses deps. Ajouter `actions/cache` sur `~/.cache/uv` keyé par `uv.lock`. Gain potentiel ~30s par workflow.
- [ ] **Tests intégration séparés** : marquer les tests qui sortent du process (DB réelle, msw, container) avec `@pytest.mark.integration` et les exécuter dans un job CI séparé pour pas les rejouer sur chaque commit feature.
- [ ] **Triage flaky** : créer une CI weekly qui rerun les tests skipped (cf DECISIONS_PENDING) pour vérifier s'ils passent maintenant.

→ Pas urgent en alpha tant que le runner-self-hosted Hetzner tient. Devient bloquant quand on passe à GitHub-hosted (= post-alpha) ou que l'équipe grandit (run-time perçu en feedback PR).

## Part B — réconciliation ticket-based (DA-30)

- [ ] **Nominatim user-agent** : déployer `NOMINATIM_USER_AGENT` avec une adresse mail réelle et monitorée (`contact@ratis.app` ou équivalent) dans les secrets prod de `ratis_product_analyser`. Le défaut du code est un placeholder — OSM ToS exigent un contact joignable.
- [ ] **Nominatim self-hosted (option V2)** : au-delà de ~1 req/s soutenu, la public API devient incompatible avec les ToS. Préparer un mirroir self-hosted (docker `mediagis/nominatim`) + `NOMINATIM_BASE_URL` pointé dessus.
- [ ] **Rate limit monitoring** : alerter si Nominatim renvoie HTTP 429 (`GeocodingUnavailable` → log WARNING) — indicateur qu'un déploiement self-hosted devient nécessaire.
- [ ] **Cron batch_purge** : la nouvelle étape `unknown_scans` tourne à la même cadence journalière. Vérifier dans `.github/workflows/` que le workflow schedulé inclut bien ce step (pas de config séparée — `STEPS` est run séquentiellement).
- [ ] **Notification template `store_validated`** : s'assurer côté `ratis_notifier` qu'une clé i18n `push.store_validated` existe avec la chaîne `"Ton magasin {store_name} a été validé. +{reconciled_count} scan(s) débloqué(s)."` ou équivalent. Templates gérés par le notifier, pas ici.
- [ ] **Anti-fraude** : monitorer les signaux `reconciled_count` mensuels. Si > 100 par user / mois, investigate (peut indiquer un fake receipt cycle). V1 = monitor only, pas de blocage.
- [ ] **RGPD audit** : confirmer via `SELECT COUNT(*) FROM scans WHERE user_lat IS NOT NULL AND scanned_at < now() - interval '7 days'` = 0 après chaque passage du batch purge.

## Gift cards (Runa) — activation post-KYB

Les 2 flux gift-card côté prod (**yearly subscription bonus Y 20€** et **referral reward X 5€**) sont **code-complets** mais désactivés par config tant que le KYB Runa n'est pas finalisé. Le code degrade gracieusement (log WARNING, flow skippé) quand les brand IDs sont vides — pas de crash, pas d'émission erronée.

- [ ] **KYB Runa finalisé** — process externe ~3-5 jours ouvrés, obtenir les credentials API + `provider_brand_id` pour Amazon.fr (ou équivalent catalogue FR).
- [ ] **Env var `GIFT_CARD_PROVIDER_KEY`** définie dans les secrets prod de `ratis_rewards` (Runa API key). Le service lève `RuntimeError` au lifespan si absente en prod — fail fast.
- [ ] **Seed `gift_card_brands`** avec au moins 1 row `{ name: 'Amazon.fr', provider_brand_id: '<runa_brand_id>', is_active: true }`. Script `alembic/datafixes/` ou INSERT manuel via admin — **jamais en prod sans validation**.
- [ ] **`ratis_settings.json#gift_cards.annual_subscription_brand_id`** — set à l'UUID du brand seed. Au startup, vérifier absence du WARNING `"gift_cards.annual_subscription_brand_id not set — annual subscription gift cards will not be issued"`.
- [ ] **`ratis_settings.json#referral.gift_card_brand_id`** — set au même UUID (référrale utilise le même brand par défaut, changer uniquement si brand dédié voulu plus tard).
- [ ] **Test manuel end-to-end yearly** : créer une souscription annuelle test via Stripe → webhook → vérifier row `gift_card_orders` avec `source_type='annual_subscription'`, `status='issued'`, `code` non-null, mail Runa reçu côté user.
- [ ] **Test manuel end-to-end referral** : user A partage code, user B s'inscrit avec code + souscrit monthly → vérifier `gift_card_orders` pending + `eligible_at = NOW() + 30 days`. Puis avancer le clock (test env) ou attendre 30j → vérifier batch `ratis_batch_referral_payout` le marque `issued` + Runa call OK.
- [ ] **Activer le cron batch_referral_payout** — décommenter le `schedule` dans `.github/workflows/batch_referral_payout.yml` (aujourd'hui désactivé, seulement `workflow_dispatch` manuel). Cron daily à 04:00 UTC recommandé.
- [ ] **Observabilité gift cards** : ajouter alerte Sentry / Notion si :
  - `gift_card_orders.status = 'failed'` rate > 5% sur 24h (problème provider)
  - `referral_payout` batch : stats `errors > 0` (échec notify → gift card stuck)
  - Aucun `status = 'issued'` sur 7j alors que `candidates > 0` (provider down ?)
- [ ] **Comptabilité marketing** : les gift cards sont une dépense marketing. Tracker mensuellement via requête :
  ```sql
  SELECT source_type, COUNT(*), SUM(denomination)/100.0 AS total_eur
  FROM gift_card_orders WHERE status = 'issued' AND issued_at > now() - interval '30 days'
  GROUP BY source_type;
  ```
- [ ] **RGPD audit** : `gift_card_orders.code` contient potentiellement un voucher utilisable. Vérifier que le champ n'est pas exposé hors API `GET /rewards/gift-cards` (auth JWT + ownership check) et qu'il est purgé après redemption (V2+ — pour V1 on le garde pour support).

## Hardware constraints — règles d'achat de VM

**Source** : galère 2026-04-25 — déploiement Hetzner CAX21 (ARM64, 4 GB) a échoué au build product_analyser car `paddlepaddle==3.3.1` n'a pas de wheel pour `linux_aarch64`. Migration vers Hetzner CX31 (x86_64, 8 GB) en cours d'alpha.

### Règles dures (ne jamais déroger)

- ❌ **Pas d'ARM64** tant que PaddlePaddle est dans la stack. PaddlePaddle officiel ne distribue pas de wheels `linux_aarch64`. Build from source = 30-90 min sans garantie.
- ❌ **Pas de RAM <8 GB** pour la stack complète (Postgres + Redis + 5 services + OCR + Caddy). Idle ~1.6 GB, charge typique ~2.8 GB, pic 3 scans concurrents ~5.2 GB. 4 GB = OOM risk garanti.
- ❌ **Pas d'OSRM local** sur une VM <16 GB. France PBF MLD = 3-5 GB en RAM. Sans VM 16+ GB ou Mac mini, pointer `OSRM_BASE_URL` vers `https://router.project-osrm.org` (OK pour <100 req/jour).

### Cibles validées

| Provider | Type | RAM | CPU | Arch | Prix /mois | Verdict |
|---|---|---|---|---|---|---|
| **Hetzner CX31** | VPS | 8 GB | 4 vCPU | x86_64 | ~10€ | ✅ V0 alpha |
| Hetzner CX41 | VPS | 16 GB | 8 vCPU | x86_64 | ~16€ | ✅ V1 (OSRM local OK) |
| Hetzner CAX (tous) | VPS | * | * | **ARM64** | * | ❌ tant que PaddlePaddle |
| Mac mini M-series | Bare metal | 48 GB | 8-12 cores | ARM64 | élec | ⚠️ OCR à externaliser (même limitation ARM) |
| AWS EC2 c6i.large+ | Cloud | 4-8 GB | 2-4 vCPU | x86_64 | ~$60+ | ✅ V2 scale |

### Process à suivre avant tout `hcloud server create` (ou équivalent)

1. **Confirmer x86_64** dans le nom (Hetzner : `cx*` = x86, `cax*` = ARM)
2. **Confirmer RAM ≥8 GB**
3. **Tester `docker pull --platform linux/amd64`** des images critiques AVANT de provisionner si doute
4. **Documenter le choix** dans `SESSION_LOG.md` avec spec + raison

### Pour le Mac mini quand il arrive (M+1)

Le Mac mini M-series est ARM64 → **même problème PaddlePaddle qu'Hetzner CAX**. Solutions à arbitrer :
- OCR sur VM x86 dédiée (Hetzner CX) appelée par le Mac
- PaddleOCR via Docker x86 émulé (qemu, perf dégradée)
- Switch OCR → Tesseract (qualité française inférieure mais ARM natif)

À trancher en `DECISIONS_PENDING.md` au moment de l'arrivée du Mac mini.

## Auto-ops infrastructure — MCP servers

Stratégie d'autonomie : pour que l'agent (Claude Code) puisse intervenir sur la prod **sans avoir accès aux credentials raw** (token Hetzner, clé R2, secrets Stripe), on construit progressivement une couche **MCP (Model Context Protocol) servers** qui isolent les credentials et exposent uniquement des actions whitelistées.

**Pattern unique** :
```
[Agent Claude]                              [MCP server local]
      │                                              │
      │  tool call: hetzner_reboot_server(name)     │
      ├─────────────────────────────────────────────►│
      │                                              ├──► Hetzner API (token côté serveur uniquement)
      │  result: {"status": "ok", "took": "12s"}    │
      │◄─────────────────────────────────────────────┤
```

L'agent voit `mcp__ratis_hetzner__reboot_server` mais jamais le token. Token stocké en env var côté process MCP (root-only, pas accessible à l'agent).

### Roadmap MCP (post-alpha, par ordre d'utilité)

- [ ] **`ratis-hetzner-mcp`** — VM ops
  - Tools : `reboot_server`, `view_console_log`, `snapshot`, `resize`, `server_info`, `list_servers`
  - Token Hetzner stocké en env var, jamais exposé
  - Couvre les 1% de cas où SSH ne suffit pas (kernel panic, OOM total, resize)
  - Estimé : 2-3h de dev (Node.js + `@hetzner-cloud/sdk` + `@modelcontextprotocol/sdk`)

- [ ] **`ratis-cloudflare-mcp`** — DNS + R2
  - Tools : `dns_list`, `dns_update`, `r2_list_objects`, `r2_purge_old_receipts`, `bot_fight_toggle`
  - Token Cloudflare scoped en lecture+R2 uniquement, pas write DNS sauf via tool whitelisté
  - Cas d'usage : update DNS quand on switch IP, audit objets R2, purge RGPD on-demand

- [ ] **`ratis-sentry-mcp`** — observabilité
  - Tools : `list_recent_errors`, `error_details`, `error_count_by_release`, `mark_resolved`
  - Auth token lecture-seule sur le projet ratis
  - Cas d'usage : "il y a un crash, montre-moi les 10 dernières erreurs prod" sans browse Sentry web

- [ ] **`ratis-stripe-mcp`** — paiements (post-V1)
  - Tools : `customer_lookup`, `subscription_status`, `refund` (avec confirmation user obligatoire), `dispute_list`
  - Restricted key Stripe en read-only par défaut, write uniquement pour `refund` avec rate-limit

- [ ] **`ratis-github-mcp`** — déjà partiellement couvert par `gh` CLI mais à formaliser
  - Tools : `pr_status`, `merge_pr` (avec confirmation), `trigger_workflow`, `view_logs`
  - Permet déclencher rebuilds / redeploys sans accès direct au token GH

### Principes communs aux MCP servers ratis

- **Hosted sur le Mac mini** (quand il arrive — 48 GB RAM, toujours allumé)
- **Auth Bearer entre Claude Code et chaque MCP** : un token par server, rotable
- **Audit log obligatoire** : chaque tool call logué dans `/var/log/ratis-mcp/<server>.log` avec timestamp + caller + args + result
- **Rate limiting** : 1 reboot/heure, 10 reads/min — protège contre boucles agent
- **Confirmations humaines explicites** sur les actions destructives (reboot, refund, delete) — l'agent doit déclencher une push notif user qui valide. Pas de validation = pas d'action.

### Pourquoi ne PAS faire MCP server aujourd'hui

- Alpha = 30 users famille, ces tools = 0 usage probable cette semaine
- Crash kernel improbable, debug applicatif via SSH suffit
- 2-3h × 5 MCP servers = 1-2 semaines de dev distrayants des features V0
- Mieux : on construit le 1er MCP (`ratis-hetzner-mcp`) le jour où on en a vraiment besoin (premier vrai incident ops)

## Sentry sourcemaps — réactiver après alpha

**Aujourd'hui** : `eas.json` contient `SENTRY_DISABLE_AUTO_UPLOAD=true` pour skip l'upload des sourcemaps (sentry-cli a besoin d'un `SENTRY_AUTH_TOKEN`).

Conséquence : les crashes en prod arrivent dans Sentry mais avec des stack traces minifiées (illisibles).

- [ ] **Créer `SENTRY_AUTH_TOKEN`** sur https://sentry.io/settings/account/api/auth-tokens/ avec scopes `Release: Read & Write` + `Organization: Read`
- [ ] **`eas env:create`** sur les profiles `preview` et `production` avec ce token (visibility `sensitive`)
- [ ] **Retirer `SENTRY_DISABLE_AUTO_UPLOAD`** de `eas.json` une fois le token en place
- [ ] **Tester** : push un crash volontaire, vérifier que la stack trace dans Sentry est lisible (`scan.tsx:42` au lieu de `main.bundle.js:1:23456`)

Acceptable de laisser tel quel pour l'alpha 30 users (debug par discussion directe avec la famille). Prioritaire dès que le user count dépasse ~100 ou qu'on ne connaît plus tous les utilisateurs nominalement.

## Cartographie — clé MapTiler (carte d'itinéraire)

**Contexte** : depuis le revert Google Maps → MapLibre + MapTiler (DA-46, 2026-05-25, branche `chore/map-revert-maplibre-maptiler`), la carte d'itinéraire (`ratis_client/components/liste/route-map.tsx`) consomme `EXPO_PUBLIC_MAPTILER_KEY` au runtime pour le style de tuiles. Sans clé en env, la carte affiche un fallback lisible ("Carte indisponible") au lieu de planter — mais la carte ne s'affiche pas. Pas de billing requis (MapTiler free tier, pas de CB).

- [ ] **Créer une clé API MapTiler** sur https://cloud.maptiler.com/account/keys/ (free tier, pas de carte bancaire requise)
- [ ] **`eas env:create`** sur les profiles `preview` et `production` : `EXPO_PUBLIC_MAPTILER_KEY` (visibility `sensitive`). Ne jamais committer la clé (R17) — ni dans `eas.json` ni ailleurs.
- [ ] **Restreindre la clé** côté MapTiler (allowed origins / referers) si le free tier le permet, pour limiter le détournement de quota.
- [ ] **Rebuild `eas build`** (changement natif lib MapLibre → pas d'OTA possible) puis vérifier le rendu carte + polyline OSRM sur l'onglet Liste → itinéraire.

## Hetzner — alternatives si UX continue d'être merdique

Hetzner Cloud a posé plusieurs problèmes au déploiement alpha :
- Catalogue ARM anémique (CAX uniquement, pas de CX entre 4 et 16 GB)
- Console web qwerty hardcoded, pas de copy-paste
- `hcloud --ssh-key` qui ne déploie pas la clé sur la VM (bug ou misuse, non-clarifié)
- OpenSSH 9.6p1 d'Ubuntu 24.04 + signature publickey-hostbound qui rejette des clés ED25519 fraîches sans raison apparente (workaround : régénérer une autre clé)
- Catalogue qui change sans préavis (CAX31 indispo certains jours)

Si la frustration s'accumule, alternatives à évaluer post-alpha :

| Provider | DC français | Console moderne | Prix CX33-équivalent | Verdict |
|---|---|---|---|---|
| **Scaleway** | Paris (PAR1, PAR2) | ✅ moderne | ~13€/mois (GP1-S, AMD EPYC) | **Plan A** post-alpha si Hetzner reste pénible |
| **OVH** | Multiple France | UI à l'ancienne mais fonctionnelle | ~15€/mois (VPS Comfort) | Plan B |
| **DigitalOcean** | Frankfurt (proche FR) | ✅ moderne, doc excellente | ~24€/mois (Premium AMD 8GB) | Plus cher mais reliable |
| **AWS Lightsail** | Paris | ✅ AWS console | ~24€/mois (4GB Standard) | Lock-in AWS — éviter pour V0 |

- [ ] **Si décision migrer hors Hetzner** : suivre la procédure de migration document dans `ARCH_deployment.md` (snapshot VM → provision new → DNS update → validation → delete old)
- [ ] **Pré-conditions pour migration sereine** : doc up-to-date du déploiement, snapshot DB exporté, EAS APK qui pointe sur les sous-domaines (DNS pointables ailleurs sans rebuild app)
