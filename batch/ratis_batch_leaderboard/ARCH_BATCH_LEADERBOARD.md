---
type: batch-global
service: ratis_batch_leaderboard
status: planned
parent: ARCH_RATIS
sub_archs: []
related: [ARCH_REWARDS, ARCH_gamification]
tech: [Python, SQLAlchemy, Postgres]
tables: [leaderboard_current, leaderboard_snapshots, cabecoin_transactions]
env_vars: [DATABASE_URL]
tags: [batch, gamification, leaderboard]
business_domain: gamification
rgpd_concern: false
updated: 2026-04-24
---

# ratis_batch_leaderboard — classement CAB

> Batch CLI **planifié** (pas encore codé, dossier batch vide) qui calculera le classement CAB des users dans `leaderboard_current` + `leaderboard_snapshots`. Vue matérialisée + rotation périodique.
> @tags: batch leaderboard gamification ranking cab planned vue-materialisee snapshot
> @status: PLANIFIÉ
> @subs: auto

> [[ARCH_RATIS]] · relations : [[ARCH_REWARDS]], [[ARCH_gamification]]

> Statut : planifié — pas encore codé. Le dossier `batch/ratis_batch_leaderboard/` ne contient que cet ARCH, aucun `main.py` n'existe à ce jour.

## Index

- [Résumé en une phrase](#résumé-en-une-phrase) · L.41
- [Responsabilité](#responsabilité) · L.45
- [Fréquence d'exécution](#fréquence-dexécution) · L.51
- [Tables lues / écrites](#tables-lues-écrites) · L.57
- [Dépendances internes (autres services/libs ratis)](#dépendances-internes-autres-serviceslibs-ratis) · L.65
- [Dépendances externes (tiers)](#dépendances-externes-tiers) · L.70
- [Décisions d'architecture clés](#décisions-darchitecture-clés) · L.74
- [Flow principal](#flow-principal) · L.94
- [Paramètres (ratis_settings.json section `leaderboard`)](#paramètres-ratis_settingsjson-section-leaderboard) · L.113
- [Schéma de la vue matérialisée](#schéma-de-la-vue-matérialisée) · L.123
- [Monitoring / logs](#monitoring-logs) · L.139
- [FAQ vectorisée](#faq-vectorisée) · L.144
- [Glossaire](#glossaire) · L.162

---

## Résumé en une phrase

ratis_batch_leaderboard est un batch long-running (worker process, pas un cron ponctuel) qui rafraîchit la vue matérialisée `leaderboard_current` toutes les X minutes et snapshot le classement mensuel dans `leaderboard_snapshots` en fin de mois.

## Responsabilité

- ratis_batch_leaderboard rafraîchit en continu la `MATERIALIZED VIEW leaderboard_current` pour que le classement affiché dans l'app mobile soit toujours récent (ordre des minutes, pas du jour)
- ratis_batch_leaderboard génère à la fin de chaque mois un snapshot figé dans `leaderboard_snapshots` pour que les users puissent consulter leurs classements passés
- ratis_batch_leaderboard tourne **en continu** (Railway worker process / systemd service), contrairement aux autres batches ratis qui sont des crons ponctuels

## Fréquence d'exécution

- **Worker long-running** — pas de cron GH Actions, pas de `.github/workflows/batch_leaderboard.yml` prévu
- Boucle interne : refresh toutes les `leaderboard.refresh_interval_minutes` (défaut 10 min)
- Snapshot fin de mois : déclenché dans la boucle quand on détecte qu'on est dernier jour du mois à ≥23h50 UTC

## Tables lues / écrites

| Table | Lecture | Écriture |
|---|---|---|
| `cabecoin_transactions` | `SUM(amount) FILTER (direction='credit')` depuis début du mois | — (la vue matérialisée lit) |
| `leaderboard_current` (MATERIALIZED VIEW) | — | `REFRESH MATERIALIZED VIEW CONCURRENTLY` |
| `leaderboard_snapshots` | — | INSERT fin de mois, `ON CONFLICT (user_id, month) DO UPDATE` |

## Dépendances internes (autres services/libs ratis)

- [[ARCH_CORE]] — `make_engine`, `load_settings`
- [[ARCH_REWARDS]] — écrit dans `cabecoin_transactions`, source de vérité du classement

## Dépendances externes (tiers)

- Aucune — ratis_batch_leaderboard est 100% local DB.

## Décisions d'architecture clés

### DA-01 — Vue matérialisée plutôt que requête directe

**Choix** : `MATERIALIZED VIEW leaderboard_current` pré-calculée
**Alternative rejetée** : calcul `SUM + RANK` à chaque appel de chaque user
**Raison** : `cabecoin_transactions` est une table non-purgeable (legal), croissante sans limite. Calculer SUM + RANK par user à chaque requête ne scale pas. La vue matérialisée amortit ce coût sur un refresh périodique — lecture O(1) côté app.

### DA-02 — `REFRESH MATERIALIZED VIEW CONCURRENTLY`

**Choix** : toujours `CONCURRENTLY` avec un index UNIQUE sur `user_id`
**Alternative rejetée** : refresh standard (bloquant)
**Raison** : sans `CONCURRENTLY`, le refresh prend un lock exclusif et bloque toute lecture (l'app ne peut plus afficher le classement pendant ~secondes à minutes). `CONCURRENTLY` maintient la vue lisible pendant le refresh, mais **requiert** un index unique sur une colonne de la vue.

### DA-03 — Worker long-running plutôt que cron

**Choix** : process qui boucle `time.sleep(interval * 60)`
**Alternative rejetée** : cron `*/10 * * * *`
**Raison** : intervalle configurable à chaud via `ratis_settings.json`, pas besoin de modifier le crontab. Simple à héberger sur Railway/VM comme worker process. Snapshot mensuel intégré dans la même boucle sans dépendance cron supplémentaire.

## Flow principal

### Flow 1 — Boucle refresh continue

1. `main()` charge `DATABASE_URL` via `require_env`
2. Charge `settings.leaderboard.refresh_interval_minutes`
3. Ouvre une connexion Postgres
4. Boucle infinie :
   - `REFRESH MATERIALIZED VIEW CONCURRENTLY leaderboard_current`
   - Appel `snapshot_if_end_of_month(db)` (no-op sauf dernier jour du mois ≥ 23h50 UTC)
   - `time.sleep(interval_minutes × 60)`

### Flow 2 — Snapshot fin de mois

1. Détection : dernier jour du mois en cours ET heure ≥ 23h50 UTC
2. Lecture de `leaderboard_current` (état final du mois)
3. `INSERT INTO leaderboard_snapshots (user_id, month, rank, cab_earned) SELECT … FROM leaderboard_current ON CONFLICT (user_id, month) DO UPDATE`
4. Idempotent — relancer après 00h00 UTC du mois suivant sur-écrit proprement (mais au ce moment-là `leaderboard_current` reflète déjà le nouveau mois → précaution : capture du snapshot avant minuit UTC)

## Paramètres (ratis_settings.json section `leaderboard`)

```json
"leaderboard": {
    "refresh_interval_minutes": 10
}
```

10 minutes est un bon défaut — assez fréquent pour être pertinent côté app, assez espacé pour ne pas stresser la DB. Ajustable à chaud sans redéploiement.

## Schéma de la vue matérialisée

```sql
CREATE MATERIALIZED VIEW leaderboard_current AS
SELECT
    user_id,
    SUM(amount) AS cab_earned,
    RANK() OVER (ORDER BY SUM(amount) DESC) AS rank
FROM cabecoin_transactions
WHERE direction = 'credit'
  AND created_at >= date_trunc('month', now() AT TIME ZONE 'UTC')
GROUP BY user_id;

CREATE UNIQUE INDEX ON leaderboard_current (user_id);  -- mandatory pour CONCURRENTLY
```

## Monitoring / logs

- Stdout : chaque refresh logue sa durée et le nombre de lignes dans `leaderboard_current`
- Supervision : si le worker meurt, l'app continue d'afficher un classement obsolète (pas de crash côté user) mais il faut redémarrer le process — alerte infra à mettre en place

## FAQ vectorisée

### Pourquoi ratis_batch_leaderboard tourne-t-il en continu plutôt qu'en cron quotidien ?

Le classement mensuel est consulté en permanence par les users (onglet leaderboard). Attendre 24h entre deux refreshs rendrait les rankings obsolètes et peu engageants. ratis_batch_leaderboard fait un refresh toutes les 10 minutes, c'est un compromis coût DB / fraîcheur. Par ailleurs, intégrer le snapshot mensuel dans la même boucle évite une orchestration cron séparée.

### Pourquoi `CONCURRENTLY` est-il obligatoire dans ratis_batch_leaderboard ?

Sans `CONCURRENTLY`, `REFRESH MATERIALIZED VIEW` prend un lock exclusif qui bloque toute lecture de `leaderboard_current` pendant le refresh. Les users verraient l'onglet classement figé ou en erreur pendant quelques secondes. `CONCURRENTLY` fait le refresh sans bloquer, en échange d'un surcoût disque et d'un index UNIQUE obligatoire sur une colonne (ici `user_id`).

### Comment ratis_batch_leaderboard garantit-il l'idempotence du snapshot mensuel ?

L'INSERT utilise `ON CONFLICT (user_id, month) DO UPDATE` : si le batch est relancé (crash + restart, fenêtre de fin de mois traversée deux fois), le snapshot est simplement sur-écrit avec les valeurs courantes. Aucun duplicata possible grâce à la contrainte UNIQUE composite.

### Comment tester ratis_batch_leaderboard localement ?

À ce jour le code n'existe pas (status: planned). Quand il sera implémenté : `uv run pytest batch/ratis_batch_leaderboard/tests/`. Pour tester la vue matérialisée elle-même, lancer manuellement `REFRESH MATERIALIZED VIEW CONCURRENTLY leaderboard_current` dans psql après avoir peuplé `cabecoin_transactions` via fixtures.

## Glossaire

- **DA-XX** : décision d'architecture numérotée
- **Vue matérialisée (MATERIALIZED VIEW)** : table Postgres peuplée par le résultat d'une requête, rafraîchie explicitement (contrairement à une VIEW classique qui ré-exécute à chaque SELECT)
- **CONCURRENTLY** : option de `REFRESH MATERIALIZED VIEW` qui ne bloque pas les lectures concurrentes (nécessite un index UNIQUE)
- **Worker process** : process long-running déployé par un orchestrateur (Railway / systemd / docker-compose), par opposition à un cron ponctuel
