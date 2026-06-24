# Setup Checklist — Comptes & services externes

Liste ordonnée de tout ce qu'il faut créer pour que Ratis passe du dev à la prod.
À cocher au fur et à mesure. L'ordre est important — certaines étapes nécessitent les précédentes.

---

## 🎯 Étape 0 — Domaine + Email pro (à faire EN PREMIER)

**Pourquoi d'abord :** `contact@ratis.app` sera l'adresse qu'on utilise pour inscrire tous les comptes qui suivent. C'est la fondation.

### 0.1 — Acheter le domaine `ratis.app`

- [x] Créer un compte Cloudflare
- [x] Activer la 2FA
- [x] Acheter `ratis.app` via Cloudflare Registrar
- [x] Auto-renew activé

**Coût :** ~18 €/an ✅

### 0.2 — Proton Mail Plus + configuration DNS

**Décision retenue :** Proton Mail Plus (~4 €/mois) au lieu de Cloudflare Email Routing — vraie boîte avec envoi depuis `@ratis.app`, chiffrement E2E, RGPD strict.

- [x] S'inscrire à Proton Mail Plus (~48 €/an en annuel)
- [x] Ajouter le domaine `ratis.app` dans Proton Settings → Domains
- [x] Ajouter le TXT de vérification dans Cloudflare DNS
- [x] Ajouter les 2 MX records (`mail.protonmail.ch` priorité 10, `mailsec.protonmail.ch` priorité 20)
- [x] Ajouter le TXT SPF (`v=spf1 include:_spf.protonmail.ch ~all`)
- [x] Ajouter les 3 CNAME DKIM (`protonmail._domainkey`, `protonmail2._domainkey`, `protonmail3._domainkey`)
- [x] Ajouter le TXT DMARC (`v=DMARC1; p=quarantine; rua=mailto:dmarc@ratis.app; pct=100; adkim=s; aspf=s`)
- [x] Tous les records en **DNS only** (nuage gris Cloudflare, pas de proxy)
- [x] Domaine vérifié ✅ par Proton
- [x] Catch-all `*@ratis.app` activé dans Proton
- [ ] Dans 2-4 semaines : upgrade DMARC de `p=quarantine` → `p=reject` après confirmation des rapports

**Coût :** ~48 €/an ✅

### 0.3 — Créer les 10 adresses/alias Proton

À faire dans Proton → Settings → Identity and addresses :

- [ ] `contact@ratis.app` — adresse principale (display: **Ratis**)
- [ ] `support@ratis.app` — support utilisateur (display: **Support Ratis**)
- [ ] `noreply@ratis.app` — transactionnel (display: **Ratis**)
- [ ] `hello@ratis.app` — alias friendly (display: **Ratis**)
- [ ] `postmaster@ratis.app` — technique RFC 2142 (display: **Ratis Postmaster**)
- [ ] `abuse@ratis.app` — technique RFC 2142 (display: **Ratis Abuse**)
- [ ] `privacy@ratis.app` — RGPD (display: **Ratis Confidentialité**)
- [ ] `legal@ratis.app` — juridique (display: **Ratis Juridique**)
- [ ] `security@ratis.app` — security.txt (display: **Ratis Security**)
- [ ] `dmarc@ratis.app` — rapports DMARC (display: **Ratis DMARC**)

**Coût :** inclus dans Proton Mail Plus ✅

---

## 🔥 Étape 1 — Phase 1 : bloque le dev actuel

### 1.1 — Google Cloud Console (OAuth Google)

**Pourquoi :** remplacer `REPLACE_WITH_GOOGLE_OAUTH_CLIENT_ID` dans `app.json` pour que "Continuer avec Google" marche.

- [ ] Aller sur https://console.cloud.google.com
- [ ] Se connecter avec un compte Google (ton perso ou un nouveau dédié Ratis)
- [ ] Accepter les CGU, fournir une CB pour vérification (pas de frais pour OAuth)
- [ ] Créer un nouveau projet : nom = `Ratis`
- [ ] Attendre la création (30s)
- [ ] `APIs & Services` → `OAuth consent screen`
  - [ ] User Type : **External**
  - [ ] App name : `Ratis`
  - [ ] User support email : `contact@ratis.app`
  - [ ] Developer contact : `contact@ratis.app`
  - [ ] App domain : `ratis.app`
  - [ ] Privacy policy : `https://ratis.app/legal/confidentialite` (la page n'existe pas encore — OK, juste déclarer l'URL)
  - [ ] Terms of service : `https://ratis.app/legal/cgu`
  - [ ] Scopes : ajouter `openid`, `email`, `profile`
  - [ ] Test users : ajouter ton email perso pour tester avant publication
- [ ] `Credentials` → `+ Create Credentials` → `OAuth client ID`
  - [ ] Application type : **iOS** → Bundle ID : `app.ratis.client` → Create
  - [ ] Noter le **Client ID iOS** : `_______________________________`
- [ ] `+ Create Credentials` → `OAuth client ID`
  - [ ] Application type : **Android** → Package name : `app.ratis.client`
  - [ ] SHA-1 fingerprint : **temporaire** (on le récupérera depuis EAS plus tard — pour l'instant, mettre une SHA-1 de debug)
  - [ ] Noter le **Client ID Android** : `_______________________________`
- [ ] **Me donner les 2 Client IDs** → je câble dans `app.json`

**Coût :** 0 €
**Durée :** 30 min

### 1.2 — Sentry (observabilité)

**Pourquoi :** le code backend + frontend a déjà les hooks prêts, il suffit de fournir les DSNs.

- [ ] Aller sur https://sentry.io/signup
- [ ] Sign up avec `contact@ratis.app` (alias Gmail via Cloudflare Email Routing)
- [ ] Vérifier l'email
- [ ] Créer l'organisation : `Ratis`
- [ ] **Créer 2 projets distincts :**

  **Projet 1 — ratis-backend** :
  - [x] Platform : **Python** → **FastAPI**
  - [x] Project name : `ratis-backend`
  - [x] Team : `#ratis`
  - [x] Récupérer le DSN depuis Settings → Projects → ratis-backend → Client Keys
  - [x] DSN backend : `https://1c2691301cb71de58b588a70580a9c67@o4511250717540352.ingest.de.sentry.io/4511250732154960`

  **Projet 2 — ratis-mobile** :
  - [x] Platform : **React Native**
  - [x] Project name : `ratis-mobile`
  - [x] Team : `#ratis`
  - [x] Récupérer le DSN
  - [x] DSN mobile : `https://5374c0382a462442f41528a71705167c@o4511250717540352.ingest.de.sentry.io/4511250729140304`

- [x] **DSNs câblés** — backend : `SENTRY_DSN` dans chaque `.env.local` · mobile : `app.json extra.sentryDsn`

**Coût :** 0 € (free tier 5 000 erreurs/mois partagé entre les 2 projets)
**Durée :** 15 min

### 1.3 — Apple Developer Program

**Pourquoi :** obligatoire pour Sign in with Apple + TestFlight + App Store.

- [ ] Aller sur https://developer.apple.com/programs
- [ ] Se connecter avec un Apple ID (ou en créer un dédié : `contact@ratis.app`)
- [ ] S'inscrire au **Apple Developer Program** (99 $/an)
  - Compte **Individual** si tu es solo, **Organization** si tu as créé une entité légale (SASU, EURL, etc.)
  - Organization nécessite un DUNS Number (gratuit mais demande 3-5 jours, via Dun & Bradstreet)
- [ ] Payer (99 $ ~= 92 €)
- [ ] Attendre la validation Apple (24-72h, parfois 1 semaine)
- [ ] Une fois validé :
  - [ ] Aller sur https://developer.apple.com/account
  - [ ] Certificates, IDs & Profiles → Identifiers → `+`
  - [ ] App IDs → App
  - [ ] Bundle ID : **Explicit** → `app.ratis.client`
  - [ ] Description : `Ratis`
  - [ ] Cocher la capability **Sign in with Apple**
  - [ ] Register
- [ ] (Optionnel) Services ID pour web Sign in with Apple → inutile V1
- [ ] Le provisioning profile sera généré automatiquement par EAS Build plus tard

**Coût :** 99 $/an (~92 €)
**Durée :** 30 min d'action + 24-72h d'attente validation Apple

---

## 🚧 Étape 2 — Phase 2 : avant TestFlight beta

### 2.1 — Expo / EAS

- [ ] Aller sur https://expo.dev/signup
- [ ] Sign up avec `contact@ratis.app` (ou GitHub OAuth)
- [ ] Créer l'organisation : `ratis`
- [ ] Accepter le plan **Free** (30 builds/mois)
- [ ] Depuis ton terminal local : `cd ratis_client && npx eas login`
- [ ] `npx eas init` → lie le projet Expo au workspace EAS
- [ ] Noter le **projectId** qu'EAS génère dans `app.json` → `extra.eas.projectId`
- [ ] **Quand tu ships régulièrement en TestFlight** → upgrade Production plan (29 $/mois)

**Coût :** 0 € → 29 $/mois (quand nécessaire)
**Durée :** 15 min

### 2.2 — Google Play Console

**Pourquoi :** obligatoire pour publier l'app Android.

- [ ] Aller sur https://play.google.com/console/signup
- [ ] Se connecter avec un compte Google (idem celui utilisé pour Google Cloud à l'étape 1.1)
- [ ] Payer **25 $** (one-time à vie)
- [ ] Remplir les informations développeur :
  - [ ] Compte : **Personal** ou **Organization**
  - [ ] Nom développeur public : `Ratis`
  - [ ] Email de contact : `contact@ratis.app`
  - [ ] Téléphone (vérifié par SMS)
  - [ ] Site web : `https://ratis.app`
- [ ] Vérification d'identité : uploader une pièce d'identité (photo recto-verso)
- [ ] Attendre la validation (jusqu'à 5 jours ouvrés)
- [ ] Créer une app : `Ratis` (draft, on y reviendra avant publication)

**Coût :** 25 $ one-time (~23 €)
**Durée :** 30 min d'action + 2-5 jours de validation

---

## 🏗️ Étape 3 — Phase 3 : mise en prod backend

### 3.1 — Choix d'hébergement backend (choisir UN)

**Option A — Railway (recommandé bootstrap)** : https://railway.com
- [ ] Sign up avec `contact@ratis.app`
- [ ] Link GitHub → autoriser l'accès au repo `Belladynn/ratis`
- [ ] Créer un projet `Ratis`
- [ ] Ajouter les 5 services : ratis_auth, ratis_product_analyser, ratis_rewards, ratis_notifier, ratis_list_optimiser
- [ ] Ajouter PostgreSQL managed + Redis managed
- [ ] Noter l'`DATABASE_URL` et `REDIS_URL` fournis

**Option B — Fly.io** : https://fly.io (plus technique, similaire prix)

**Option C — Self-host Mac mini** : 0 € mensuel mais maintenance personnelle + Cloudflare Tunnel à configurer

**Coût :** ~20-40 €/mois Railway | 0 € self-host
**Durée :** 1-2h setup Railway, plus long self-host

### 3.2 — Cloudflare R2 (stockage tickets)

**Même compte que le domaine.**

- [ ] Dashboard Cloudflare → R2 → Overview
- [ ] **Purchase R2** (free tier = 10 GB + 1M requêtes/mois, pas besoin de CB au début)
- [ ] `Create bucket` → name : `ratis-receipts-prod` (ou staging séparé)
- [ ] Region : `Automatic` (Europe si possible)
- [ ] `Manage R2 API Tokens` → `Create API Token`
  - [ ] Token name : `ratis-backend-prod`
  - [ ] Permissions : **Object Read & Write**
  - [ ] Specify bucket : `ratis-receipts-prod`
  - [ ] TTL : **Forever** (ou rotation 1 an)
- [ ] Noter les 4 variables à me filer :
  - `R2_ENDPOINT_URL` : `_______________________________`
  - `R2_ACCESS_KEY_ID` : `_______________________________`
  - `R2_SECRET_ACCESS_KEY` : `_______________________________`
  - `R2_BUCKET_NAME` : `ratis-receipts-prod`

**Coût :** 0 € V1 (free tier largement suffisant)
**Durée :** 20 min

### 3.3 — Codecov (coverage CI, optionnel mais utile)

- [ ] Aller sur https://about.codecov.io/sign-up/
- [ ] Sign up via **GitHub OAuth** (pas besoin de créer un compte séparé)
- [ ] Autoriser Codecov sur l'organisation GitHub `Belladynn`
- [ ] Activer le repo `ratis`
- [ ] Récupérer le **upload token** depuis Settings → Repository
- [ ] GitHub → repo ratis → Settings → Secrets and variables → Actions → `New repository secret`
  - Name : `CODECOV_TOKEN`
  - Value : token récupéré

**Coût :** 0 € (gratuit repos privés jusqu'à 5 users)
**Durée :** 15 min

---

## 💰 Étape 4 — Phase 4 : monétisation (quand prêt)

### 4.1 — Stripe (abonnements)

**Quand :** dès que les écrans d'abonnement sont prêts côté app.

- [ ] Aller sur https://dashboard.stripe.com/register
- [ ] Sign up avec `contact@ratis.app`
- [ ] Compléter KYC entreprise :
  - [ ] Nom légal entreprise + SIRET (ou statut solo : auto-entrepreneur)
  - [ ] IBAN pour les versements
  - [ ] Pièce d'identité dirigeant
  - [ ] Preuve de domicile
- [ ] Activer le compte (validation 2-5 jours)
- [ ] Créer les produits :
  - [ ] `Ratis Premium Mensuel` — prix : 11.99 €/mois
  - [ ] `Ratis Premium Annuel` — prix : 119.88 €/an
- [ ] Récupérer les **Price IDs** pour chaque produit
- [ ] `Developers` → `API keys` → noter :
  - `STRIPE_SECRET_KEY` : `sk_live_______`
  - `STRIPE_PUBLISHABLE_KEY` : `pk_live_______`
- [ ] `Developers` → `Webhooks` → `Add endpoint`
  - URL : `https://api.ratis.app/webhooks/stripe`
  - Events : `checkout.session.completed`, `customer.subscription.deleted`, `invoice.payment_failed`
  - Noter `STRIPE_WEBHOOK_SECRET` : `whsec_______`

**Coût :** 0 € fixe, 1,5 % + 0,25 € par transaction UE
**Durée :** 45 min + 2-5 jours validation

---

## 📊 Étape 5 — Phase 5 : optionnel / plus tard

### 5.1 — GlitchTip self-hosted (système d'incidents central, remplace Notion+Sentry-SaaS)

Sunset Notion 2026-05-31 (DA-N) — voir [`docs/arch/ARCH_incident_management.md`](../arch/ARCH_incident_management.md) pour le design complet.

Installation locale du stack :

- [ ] `mkdir -p ~/glitchtip && cd ~/glitchtip`
- [ ] Créer `~/glitchtip/docker-compose.yml` (template : 5 services — web + worker + postgres-16-alpine + valkey-7 + migrate one-shot)
- [ ] Créer `~/glitchtip/.env` avec : `SECRET_KEY` (50+ chars random, urlsafe), `POSTGRES_PASSWORD`, `DATABASE_URL`, `VALKEY_URL`, `REDIS_URL`, `CELERY_BROKER_URL`, `CELERY_RESULT_BACKEND`, `GLITCHTIP_DOMAIN=http://localhost:8000`
- [ ] `docker compose up -d` (~30s pull + boot)
- [ ] Ouvrir `http://localhost:8000` → créer compte admin via UI (1 fois)
- [ ] Créer organisation "Ratis", team "ratis"
- [ ] Settings → Profile → Auth Tokens → "Create Token" avec tous scopes admin
- [ ] Stocker dans Keychain : `pbpaste | security add-generic-password -s ratis-agent-mcp -a admin-glitchtip -U -w`
- [ ] Désactiver `ENABLE_USER_REGISTRATION=False` dans `.env` + `docker compose up -d --force-recreate glitchtip-web glitchtip-worker`
- [ ] Récupérer/installer le wrapper CLI : `~/glitchtip/bin/glt` (cf `ARCH_incident_management.md` § IM-4)
- [ ] Créer les projets via wrapper : `glt add-project ratis-mobile --platform react-native` + `ratis-backend` + `n8n-workflows` (DSN stockés auto dans Keychain `ops-glitchtip-dsn-<projet>`)

**Coût :** 0 € (self-hosted)
**Durée :** ~45 min première fois
**RAM impact Mac mini :** ~600 MB

### 5.2 — Analytics produit (Mixpanel / Amplitude)

Reporté quand tu auras du produit à analyser (post-V1).

### 5.3 — Email transactionnel (Resend / Postmark)

Reporté V2 quand tu activeras les emails (confirmation cashback, alertes prix, etc.).

### 5.4 — Runa (cartes cadeaux)

Reporté V2.

---

## ✅ Récap — ordre d'exécution optimal

### Semaine 1 (max 2-3h de ta part)
1. [ ] Cloudflare compte + domaine `ratis.app` (~18 €, 15 min)
2. [ ] Cloudflare Email Routing → `contact@ratis.app` forwardé Gmail (10 min)
3. [ ] Gmail → ajouter adresse d'envoi `contact@ratis.app` (5 min)
4. [ ] Sentry → 2 projets + 2 DSNs (15 min)
5. [ ] Google Cloud → OAuth client iOS + Android (30 min)
6. [ ] Apple Developer → inscription (30 min + 24-72h attente)

### Semaine 2 (quand Apple Dev est validé)
7. [ ] Apple Developer → App ID `app.ratis.client` + Sign in with Apple capability (15 min)
8. [ ] Expo / EAS account + `eas init` (15 min)
9. [ ] Google Play Console (~23 €, 30 min + 2-5 jours validation)

### Mois suivant (avant TestFlight beta)
10. [ ] Hébergement backend — Railway ou Mac mini (1-3h selon choix)
11. [ ] Cloudflare R2 bucket (20 min)
12. [ ] Codecov (15 min)

### Quand monétisation prête
13. [ ] Stripe — KYC + produits + webhooks (45 min + 2-5 jours validation)

---

## 💸 Récap coûts

| Item | Coût | Fréquence |
|---|---|---|
| Domaine ratis.app | ~18 € | /an |
| Cloudflare Email Routing | 0 € | — |
| Sentry | 0 € | — (free tier) |
| Google Cloud OAuth | 0 € | — |
| Apple Developer Program | ~92 € | /an |
| Expo Free → Production | 0 € → ~27 € | /mois (quand upgrade) |
| Google Play Console | ~23 € | one-time à vie |
| Hébergement Railway | ~30 € | /mois |
| Cloudflare R2 | 0 € | — (free tier) |
| Codecov | 0 € | — |
| Stripe | 0 € fixe | + 1,5 % + 0,25 €/transaction |
| **Total bootstrap** | **~133 €** | **avant 1er user** |
| **Total mensuel V1 prod** | **~55 €/mois** | (sans scale) |

---

## 📝 Ce que tu me donnes au fur et à mesure

Dès que tu as les valeurs suivantes, tu me les colles dans le chat — je câble direct :

- [ ] **Sentry DSN backend** : `_______________________________`
- [ ] **Sentry DSN mobile** : `_______________________________`
- [ ] **Google OAuth Client ID iOS** : `_______________________________`
- [ ] **Google OAuth Client ID Android** : `_______________________________`
- [ ] **Apple Team ID** (optionnel, pour EAS) : `_______________________________`
- [ ] **EAS Project ID** (après `eas init`) : `_______________________________`
- [ ] **R2 credentials** (4 variables) : cf. section 3.2
- [ ] **Stripe keys** (3 variables + price IDs) : cf. section 4.1

Cette checklist est vivante — coche au fur et à mesure, ajoute des notes si besoin.
