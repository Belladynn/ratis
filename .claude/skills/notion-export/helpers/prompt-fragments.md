# helpers — prompt fragments de reformulation tech → décideur

> Ces prompts sont des **modèles internes** que la skill applique
> mentalement pour produire les 5 sections de `entity-page.md`. Ils ne
> sont pas envoyés à un LLM externe — c'est Claude qui reformule
> directement, en s'appuyant sur ces gabarits comme guide-rails.
>
> Garder ces fragments **courts et concrets**. C'est un référentiel de
> style, pas un cours de communication.

---

## Prompt-skeleton pour chaque entité

Quand la skill arrive à l'étape 4 (« reformuler + écrire »), Claude
applique mentalement ce prompt sur le `Section.body` retourné par
`docs_get(id)` :

```
Tu reçois la fiche canonique d'une entité projet ratis (HSP / DA / M /
ARCH / Audit). Ta sortie : 5 sections en markdown français, dans cet
ordre exact : Quoi, Qui est concerné, Quand, Pourquoi, Comment ça
change pour eux. Ton lecteur est un décideur non-tech (investisseur,
DG, manager produit, juriste).

Règles dures :
- Aucun terme technique brut. Remplacer par l'intention business.
- Phrases ≤ 25 mots.
- Aucun chemin de fichier, aucun code, aucun nom de variable d'env.
- Aucun jargon agent / IA (subagent, MCP, LLM).
- Aucune mention de #PR, commit, branche.
- Vocabulaire métier autorisé : solde cashback, scan, paiement, carte
  cadeau, notification, connexion utilisateur, sécurité, conformité
  RGPD, performance, disponibilité.
- Si une section ne peut pas être renseignée depuis le canonique →
  écrire « _Information non disponible dans la source canonique._ ».

Source canonique :
{CANONICAL_BODY}

Sortie attendue : strict markdown des 5 sections, sans préambule ni
postface.
```

---

## Mapping tech → business (dictionnaire interne)

Quand un de ces termes apparaît dans le canonique, le remplacer par
l'équivalent décideur listé :

| Terme canonique                     | Équivalent décideur                            |
|-------------------------------------|------------------------------------------------|
| JWT / token / Bearer                | jeton de connexion                            |
| HMAC / argon2id / hash              | empreinte cryptographique inviolable          |
| rôle PG / GRANT / REVOKE / NOINHERIT | droit d'accès en base de données             |
| migration Alembic / DDL              | mise à jour de la structure de la base       |
| webhook                             | notification automatique entre services       |
| Celery task / worker                | tâche de fond                                 |
| OCR / PaddleOCR                     | lecture automatique des tickets               |
| Postgres / Redis                    | base de données                               |
| Sentry                              | suivi des erreurs en production               |
| Stripe                              | gestion des abonnements payants               |
| R2 / S3                             | stockage des fichiers                         |
| OSRM                                | calcul d'itinéraire                           |
| OAuth / Google / Apple              | connexion via Google ou Apple                 |
| Expo / EAS / OTA                    | mise à jour de l'application mobile           |
| API REST / endpoint                 | service accessible par l'app                  |
| feature flag                        | bascule de fonctionnalité                     |
| race condition                      | conflit d'accès simultané                     |
| advisory lock                       | verrou logiciel                               |
| trust_level / graduation            | niveau de confiance accordé à l'opérateur     |
| anomaly flag                        | signal d'alerte automatique                   |
| n8n / workflow                       | enchaînement automatisé d'étapes              |
| MCP / agent-mcp                     | passerelle outillée pour l'assistant agentic   |
| subagent                            | tâche déléguée à un assistant secondaire      |
| CAB / cabecoin                      | jeton de récompense Ratis                     |
| price_consensus                     | prix de référence calculé                     |
| product_knowledge                   | base de connaissances produit (lecture OCR)   |
| RG / RGPD                           | conformité aux règles européennes de données |

Ce dictionnaire vit ici, pas dans `SKILL.md`, pour rester à jour sans
toucher la skill principale. Le compléter quand un nouveau terme tech
apparaît dans le canonique.

---

## Exemples d'avant / après

### Exemple 1 — DA-43 (Keychain pour tokens MCP)

Canonique :
> Tous les tokens (Sentry, EAS, GitHub, Notion, Stripe, R2) sont stockés
> dans macOS Keychain via `security` CLI. Cache positif 60 secondes.
> Jamais en .env, jamais en log, jamais en arg de tool. Account name =
> nom du provider.

Décideur :
> **Quoi.** Tous les codes d'accès aux services externes (Sentry, GitHub,
> Notion, Stripe, etc.) sont rangés dans le coffre-fort sécurisé du Mac
> de production, pas dans des fichiers de configuration.
>
> **Qui est concerné.** L'opérateur Ratis (sécurité de ses accès) et
> indirectement tous les utilisateurs (un code volé donnerait accès à
> des données sensibles).
>
> **Quand.** Décision actée le 2026-04-22.
>
> **Pourquoi.** Un code d'accès qui traîne dans un fichier de config ou
> dans un log est la 1ʳᵉ cible d'une attaque ou d'une fuite. Le
> coffre-fort macOS est conçu pour garder ces secrets isolés du reste
> du système.
>
> **Comment ça change pour eux.** Aucune friction visible côté
> utilisateur. Pour l'opérateur, une étape de mise en place par code
> d'accès, ensuite tout est automatique.

### Exemple 2 — HSP-3 (extrait — cf SKILL.md § Example I/O)

Voir `SKILL.md` § « Example I/O — HSP-3 » pour le exemple complet.

### Exemple 3 — ARCH_AUTH (fiche fichier-level)

Canonique :
> Service d'authentification. JWT HS256 audience=ratis. OAuth Google et
> Apple Sign-In. Refresh tokens 30j, access tokens 60min. Rate-limit
> slowapi sur /login, /register, /change-password, /refresh.

Décideur :
> **Quoi.** Le service qui gère la connexion des utilisateurs à
> l'application Ratis (par Google ou Apple) et la gestion de leur
> compte.
>
> **Qui est concerné.** Tous les utilisateurs finaux à chaque
> connexion. L'équipe sécurité en arrière-plan.
>
> **Quand.** En production depuis V0.
>
> **Pourquoi.** Ratis ne stocke pas de mot de passe — l'authentification
> est déléguée à Google et Apple, qui sont mieux armés contre les
> tentatives de piratage. Les sessions sont limitées en durée et le
> nombre d'essais est plafonné pour éviter les attaques brute-force.
>
> **Comment ça change pour eux.** L'utilisateur clique « se connecter
> avec Google » ou « Apple » et c'est tout. Pas de mot de passe à
> retenir. Sa session reste active environ 30 jours sans avoir à se
> reconnecter.

---

## Anti-patterns à éviter

- **Re-traduire en gardant la structure tech.** ❌ « Le service expose
  un endpoint /login qui retourne un JWT. » → ❌ même style en
  français. ✅ « L'utilisateur clique sur connexion, l'application le
  reconnaît. »
- **Lister les 5 mécanismes M1-M5 mot pour mot.** Le décideur n'a pas
  besoin de la décomposition technique — il a besoin de comprendre
  l'intention et le résultat business. Si M1-M5 doivent être nommés,
  utiliser leurs rôles (« le challenge à taper »), pas leurs labels
  internes.
- **Justifier en parlant de la dette technique.** ❌ « On remplace
  l'ancien système car il ne scalait pas. » Le décideur n'a pas le
  contexte. ✅ « Cette mise à jour permet à l'application de continuer
  à fonctionner correctement avec 10× plus d'utilisateurs. »
- **Mentionner des numéros (PRs, commits, lignes)**. Sauf dans la
  ligne « Source canonique » du header — le body ne doit jamais en
  contenir.
- **Phrases interminables.** Si une phrase fait plus de 25 mots, la
  couper en deux.
