---
type: batch-global
service: ratis_batch_mystery_announce
status: production
parent: ARCH_RATIS
sub_archs: []
related: [ARCH_REWARDS, ARCH_mystery_product, ARCH_NOTIFIER]
tech: [Python, SQLAlchemy, Postgres]
tables: [mystery_challenges, mystery_challenge_clues, mystery_challenge_finds, batch_sync_log]
env_vars: [DATABASE_URL]
tags: [batch, gamification, mystery, challenge]
business_domain: gamification
rgpd_concern: false
updated: 2026-04-24
---

# ratis_batch_mystery_announce — cycle challenges mystère

> Batch CLI quotidien (0h UTC) qui orchestre le cycle des challenges « produit mystère » : révèle progressivement les indices, annonce les trouvailles, active le prochain challenge scheduled, et fait transitionner `active → frozen → revealed`.
> @tags: batch gamification mystery challenge clues reveal mystery_challenge daily-cycle notifier
> @status: LIVRÉ V0
> @subs: auto

> [[ARCH_RATIS]] · relations : [[ARCH_REWARDS]], [[ARCH_mystery_product]], [[ARCH_NOTIFIER]]

## Index

- [Résumé en une phrase](#résumé-en-une-phrase) · L.38
- [Responsabilité](#responsabilité) · L.42
- [Fréquence d'exécution](#fréquence-dexécution) · L.51
- [Tables lues / écrites](#tables-lues-écrites) · L.57
- [Dépendances internes (autres services/libs ratis)](#dépendances-internes-autres-serviceslibs-ratis) · L.66
- [Dépendances externes (tiers)](#dépendances-externes-tiers) · L.73
- [Décisions d'architecture clés](#décisions-darchitecture-clés) · L.77
- [Flow principal](#flow-principal) · L.109
- [Paramètres](#paramètres) · L.147
- [Monitoring / logs](#monitoring-logs) · L.152
- [FAQ vectorisée](#faq-vectorisée) · L.159
- [Glossaire](#glossaire) · L.181

---

## Résumé en une phrase

ratis_batch_mystery_announce est un batch CLI quotidien (0h00 UTC) qui orchestre le cycle de vie des challenges de produit mystère : révélation progressive des indices (`mystery_challenge_clues`), annonce des trouvailles passées (`mystery_challenge_finds.announced_at`), activation du prochain challenge scheduled quand aucun n'est actif, et transition `active → frozen → revealed` des challenges en fin de période.

## Responsabilité

- ratis_batch_mystery_announce révèle les indices dont le `reveal_day` est atteint pour le challenge `active` en cours (`UPDATE mystery_challenge_clues SET revealed_at = now() WHERE ... AND revealed_at IS NULL`) — idempotent
- ratis_batch_mystery_announce marque `announced_at = now()` sur les `mystery_challenge_finds` survenues avant minuit UTC du jour, pour signaler qu'elles ont été officialisées
- ratis_batch_mystery_announce active le prochain challenge `scheduled` dont `starts_at <= now()` **si aucun challenge n'est actif** — garde `NOT EXISTS` + contrainte DB (index partiel unique sur `status='active'`)
- ratis_batch_mystery_announce transitionne les challenges en fin de période : `active → frozen` quand `ends_at <= now()`, `frozen → revealed` un jour après `ends_at` (délai pour que les admins tirent le gagnant)
- ratis_batch_mystery_announce écrit un run `ok`/`failed` dans `batch_sync_log` pour audit
- ratis_batch_mystery_announce **ne push pas encore de notifications** (prévu V2 — voir PROD_CHECKLIST) — les finds sont marquées announced côté DB, le fanout notification est hors scope V1

## Fréquence d'exécution

- **Workflow GitHub Actions** : **aucun workflow dédié à ce jour** (pas de `.github/workflows/batch_mystery_announce.yml` dans le repo — à créer)
- **Cron cible** : `0 0 * * *` (quotidien 00h00 UTC) — critique pour que les transitions se fassent en début de journée UTC
- **Run manuel** : `uv run python batch/ratis_batch_mystery_announce/mystery_announce.py [--dry-run]`

## Tables lues / écrites

| Table | Opération |
|---|---|
| `mystery_challenges` | UPDATE `status` : scheduled → active (activate_next), active → frozen + frozen → revealed (freeze_and_reveal) |
| `mystery_challenge_clues` | UPDATE `revealed_at = now()` pour les indices du challenge actif dont `reveal_day` atteint |
| `mystery_challenge_finds` | UPDATE `announced_at = now()` pour les finds pré-minuit UTC non encore annoncées |
| `batch_sync_log` | INSERT (`batch_name='mystery_announce'`, status) |

## Dépendances internes (autres services/libs ratis)

- [[ARCH_CORE]] — `make_engine`, `sessionmaker`
- [[ARCH_REWARDS]] — gère les récompenses CAB distribuées au gagnant (`mystery_challenges` + `mystery_challenge_finds` sont des tables rewards)
- [[ARCH_mystery_product]] — définit la logique métier du challenge (durée, clues, règles de tirage). ratis_batch_mystery_announce est juste l'orchestrateur temporel
- [[ARCH_NOTIFIER]] — V2 : push notifications sur reveal_clue et find announcement

## Dépendances externes (tiers)

- Aucune en V1 — ratis_batch_mystery_announce est 100% DB local.

## Décisions d'architecture clés

### DA-01 — Idempotence par guards SQL (`revealed_at IS NULL`, `announced_at IS NULL`)

**Choix** : chaque UPDATE a un `WHERE ... IS NULL` supplémentaire
**Alternative rejetée** : query + tracking en Python
**Raison** : le batch peut être relancé (crash, test manuel, double cron). Les guards `IS NULL` rendent chaque UPDATE no-op au second passage. Zero risque de double-reveal ou double-announce.

### DA-02 — Contrainte DB `EXACTEMENT 1 challenge active`

**Choix** : `activate_next` utilise `WHERE ... AND NOT EXISTS (SELECT 1 FROM mystery_challenges WHERE status = 'active')` + index partiel unique côté schéma
**Alternative rejetée** : garde Python "if count(active) == 0 then activate"
**Raison** : défense en profondeur. La garde Python peut race (deux process batch qui voient 0 actif et activent en parallèle). Le NOT EXISTS SQL + index unique garantissent qu'**au plus une** ligne passe à `active` même en cas de race.

### DA-03 — `frozen` comme buffer `active → revealed`

**Choix** : transition en 2 étapes : `active → frozen` immédiat quand `ends_at <= now()`, puis `frozen → revealed` après 1 jour
**Alternative rejetée** : `active → revealed` direct
**Raison** : donner 24h aux admins pour tirer le gagnant, distribuer les récompenses, et officialiser le résultat avant que le challenge soit visible comme "révélé" côté app. `frozen` signale "en cours de clôture".

### DA-04 — Une transaction par step

**Choix** : `reveal_clues`, `announce_finds`, `activate_next`, `freeze_and_reveal` ont chacun leur Session
**Alternative rejetée** : transaction globale
**Raison** : pattern identique aux autres batches ratis (consensus, purge) — une erreur sur une étape n'affecte pas les autres. `STEPS` liste séquentielle + try/except + log.

### DA-05 — Notification push reportée V2

**Choix** : `announced_at` côté DB, pas de call `POST /api/v1/notify`
**Alternative rejetée** : push en même temps que l'update DB
**Raison** : V1 minimaliste, le produit mystère est une fonctionnalité secondaire. L'intégration notifier (résolution des device tokens Expo actifs, fan-out, retry) ajoute de la complexité — acceptable de livrer l'event DB-only en V1, le front pollera au refresh quotidien.

## Flow principal

### Ordre des 4 steps (exécution séquentielle, transaction par step)

1. **`reveal_clues`** — pour le challenge `active` en cours, révèle tous les indices dont `reveal_day` ≤ jour courant et non encore `revealed_at`
   ```sql
   UPDATE mystery_challenge_clues SET revealed_at = now()
   WHERE challenge_id = (SELECT id FROM mystery_challenges WHERE status='active' LIMIT 1)
     AND reveal_day <= EXTRACT(DAY FROM (now() - starts_at))::int + 1
     AND revealed_at IS NULL
   ```

2. **`announce_finds`** — marque `announced_at = now()` toutes les `mystery_challenge_finds` antérieures à minuit UTC du jour et non encore announced
   ```sql
   UPDATE mystery_challenge_finds SET announced_at = now()
   WHERE announced_at IS NULL
     AND found_at < date_trunc('day', now() AT TIME ZONE 'UTC')
   ```

3. **`activate_next`** — active le prochain challenge scheduled (earliest `starts_at <= now()`) si et seulement si aucun challenge n'est actif
   ```sql
   UPDATE mystery_challenges SET status = 'active'
   WHERE id = (SELECT id FROM mystery_challenges WHERE status='scheduled' AND starts_at <= now() ORDER BY starts_at LIMIT 1)
     AND NOT EXISTS (SELECT 1 FROM mystery_challenges WHERE status='active')
   ```

4. **`freeze_and_reveal`** — transitionne les challenges post-`ends_at`
   ```sql
   UPDATE mystery_challenges
   SET status = CASE
       WHEN status='active'  AND ends_at <= now()                  THEN 'frozen'
       WHEN status='frozen'  AND ends_at <= now() - interval '1 day' THEN 'revealed'
       ELSE status END
   WHERE status IN ('active','frozen') AND ends_at <= now()
   ```

Écriture finale dans `batch_sync_log(batch_name='mystery_announce', status)`.

## Paramètres

- Durée challenge, nombre de clues, règles de tirage : dans `ratis_settings.json` section `mystery_product` (lus par ratis_rewards, pas par ce batch)
- Ce batch ne consomme **aucun paramètre de settings** — son comportement est 100% piloté par l'état DB (`status`, `starts_at`, `ends_at`, `reveal_day`, `found_at`)

## Monitoring / logs

- Format stdout : `%(asctime)s %(levelname)s %(message)s`
- Chaque step log : `label: N row(s) updated`
- Exit code 1 si au moins un step a failed (exception)
- `batch_sync_log` écrit status `ok` ou `failed`

## FAQ vectorisée

### Pourquoi ratis_batch_mystery_announce tourne-t-il à minuit UTC précisément ?

Parce que les transitions `active → frozen → revealed` sont basées sur des jours calendaires (`ends_at`, `reveal_day`). Tourner à 00h00 UTC garantit que toutes les finds de la journée écoulée sont incluses dans `announce_finds` (guard `found_at < date_trunc('day', now() AT TIME ZONE 'UTC')`) et que les nouveaux indices sont révélés dès le début du jour suivant côté app. Un run plus tardif retarderait l'UX sans bénéfice.

### Comment ratis_batch_mystery_announce évite-t-il d'activer plusieurs challenges en parallèle ?

Double garde : (a) la clause SQL `AND NOT EXISTS (SELECT 1 FROM mystery_challenges WHERE status='active')` dans l'UPDATE `activate_next`, (b) un index partiel unique côté schéma sur `status='active'`. Même en cas de runs batch concurrents (race), au plus une ligne peut passer à `active` — l'autre échouera sur la contrainte DB et sera loggée.

### Pourquoi transitionner en 2 étapes `active → frozen → revealed` ?

`frozen` est une zone tampon de 24h : le challenge est terminé (plus de nouveaux finds acceptés), mais pas encore officiellement révélé côté app. Ça donne aux admins le temps de tirer le gagnant et distribuer les récompenses manuellement si nécessaire, avant que le challenge apparaisse comme "révélé" à tous les users. `revealed` est l'état terminal visible.

### Comment tester ratis_batch_mystery_announce localement ?

`uv run pytest batch/ratis_batch_mystery_announce/tests/` exécute les tests qui peuplent une DB de test avec des challenges dans divers états et vérifient les transitions. Pour un dry-run : `uv run python batch/ratis_batch_mystery_announce/mystery_announce.py --dry-run` — log les row counts sans commit.

### Que se passe-t-il si l'admin oublie de créer un nouveau challenge scheduled ?

`activate_next` ne trouve aucun candidat → 0 rows updated, pas d'erreur. L'app affichera simplement "aucun challenge en cours" jusqu'à ce qu'un admin insère une nouvelle ligne `mystery_challenges (status='scheduled', starts_at=...)`. Au prochain run, le batch l'activera. C'est un no-op gracieux, pas un crash.

## Glossaire

- **DA-XX** : décision d'architecture numérotée
- **Challenge mystère** : jeu quotidien/hebdomadaire où un produit "mystère" est à découvrir via des indices progressifs
- **reveal_day** : numéro de jour (1, 2, 3…) auquel un indice doit être révélé relativement au `starts_at` du challenge
- **find** : événement "user a trouvé le produit mystère" — tracé dans `mystery_challenge_finds`
- **announced_at** : timestamp de l'officialisation d'une find par le batch (≠ `found_at` qui est le moment où l'user a cliqué)
- **status enum** : `scheduled` (pas encore démarré) → `active` (en cours) → `frozen` (terminé, en attente admin) → `revealed` (officialisé)
