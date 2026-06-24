---
type: batch-global
service: ratis_batch_push_receipts
status: production
parent: ARCH_RATIS
sub_archs: []
related: [ARCH_NOTIFIER, ARCH_CORE]
tech: [Python, SQLAlchemy, Postgres, httpx, Expo Push]
tables: [push_receipt_tickets, user_push_tokens, batch_sync_log]
env_vars: [DATABASE_URL, EXPO_RECEIPTS_URL]
tags: [batch, notifications, push, expo, cleanup, infra]
business_domain: infra
rgpd_concern: false
updated: 2026-05-18
---

# ratis_batch_push_receipts — polling des accusés de réception Expo

> Batch CLI qui polle les receipts Expo (`push_receipt_tickets`) après envoi push : confirme `DeliveredOK`, classifie `DeliveredError`, invalide les `user_push_tokens` retournés `DeviceNotRegistered`. Cleanup auto des tokens expirés.
> @tags: batch notifications push expo receipts polling DeviceNotRegistered cleanup infra user_push_tokens
> @status: LIVRÉ V0
> @subs: auto

> [[ARCH_RATIS]] · relations : [[ARCH_NOTIFIER]], [[ARCH_CORE]]

## Index

- [Résumé en une phrase](#résumé-en-une-phrase) · L.30
- [Responsabilité](#responsabilité) · L.34
- [Fréquence d'exécution](#fréquence-dexécution) · L.42
- [Tables lues / écrites](#tables-lues-écrites) · L.48
- [Dépendances](#dépendances) · L.56
- [Décisions d'architecture clés](#décisions-darchitecture-clés) · L.62
- [Flow principal](#flow-principal) · L.92
- [Paramètres](#paramètres) · L.106
- [Monitoring / logs](#monitoring-logs) · L.112
- [FAQ vectorisée](#faq-vectorisée) · L.118
- [Glossaire](#glossaire) · L.134

---

## Résumé en une phrase

ratis_batch_push_receipts est un batch CLI périodique (cron horaire cible) qui interroge l'endpoint *getReceipts* d'Expo pour les tickets de push persistés dans `push_receipt_tickets`, et supprime de `user_push_tokens` tout token rapporté `DeviceNotRegistered` — fermant la fuite de tokens morts qui s'accumulaient sans nettoyage.

## Responsabilité

- ratis_batch_push_receipts lit les lignes `push_receipt_tickets` non encore vérifiées (`checked_at IS NULL`)
- ratis_batch_push_receipts POST les `expo_ticket_id` vers Expo *getReceipts* par chunks de 1000 (limite Expo)
- ratis_batch_push_receipts supprime la ligne `user_push_tokens` correspondante pour tout reçu `status='error'` + `details.error='DeviceNotRegistered'`
- ratis_batch_push_receipts marque `checked_at = now()` sur chaque ticket polled — y compris ceux sans reçu (Expo ne retient les reçus que ~24h) — pour ne jamais re-poller
- ratis_batch_push_receipts écrit un run `success`/`failed` dans `batch_sync_log` (`batch_name='push_receipts'`, `rows_affected` = nb de tokens morts supprimés)

## Fréquence d'exécution

- **Workflow GitHub Actions** : `.github/workflows/batch_push_receipts.yml` (lint + sast + tests ; cron commenté tant que la prod-execution n'est pas câblée)
- **Cron cible** : `0 * * * *` (horaire) — un reçu Expo n'est retenu que ~24h, un poll horaire laisse une large marge
- **Run manuel** : `uv run python batch/ratis_batch_push_receipts/push_receipts.py [--dry-run]` · prod : `./run-prod-batch.sh push_receipts [--dry-run]`

## Tables lues / écrites

| Table | Opération |
|---|---|
| `push_receipt_tickets` | SELECT lignes `checked_at IS NULL` · UPDATE `checked_at = now()` |
| `user_push_tokens` | DELETE de la ligne dont `token` correspond à un reçu `DeviceNotRegistered` |
| `batch_sync_log` | INSERT (`batch_name='push_receipts'`, status, rows_affected) |

## Dépendances

- **Internes** : [[ARCH_CORE]] — `make_engine`, `sessionmaker`, `load_settings`, `init_sentry`. [[ARCH_NOTIFIER]] — `ratis_notifier` alimente `push_receipt_tickets` (une ligne par envoi Expo réussi).
- **Externes** : Expo Push API — endpoint *getReceipts* `https://exp.host/--/api/v2/push/getReceipts` (configurable via `EXPO_RECEIPTS_URL` ou `ratis_settings.json` `notifier.expo_receipts_url`). Pas d'authentification requise côté Expo.

## Décisions d'architecture clés

### DA-01 — Table `push_receipt_tickets` dédiée plutôt que `notification_logs.expo_ticket_id`

**Choix** : une table `push_receipt_tickets` avec une ligne par `(envoi, token)`.
**Alternative rejetée** : réutiliser `notification_logs.expo_ticket_id`.
**Raison** : `notification_logs` ne garde qu'**un** ticket par appel (`first_ticket_id`), même quand un user a plusieurs devices. Un reçu Expo est *par-ticket* donc *par-token* — pour supprimer le bon token il faut le mapping `ticket → token` complet. La table dédiée stocke aussi `push_token` en clair (pas un FK) : le nettoyage est un lookup direct et la ligne ticket survit à la suppression du token (trace d'audit).

### DA-02 — `checked_at` même sans reçu

**Choix** : tout ticket polled est marqué `checked_at`, qu'Expo ait renvoyé un reçu ou non.
**Alternative rejetée** : ne marquer que les tickets avec reçu, re-poller les autres.
**Raison** : Expo ne retient un reçu que ~24h. Un ticket sans reçu après ce délai ne réapparaîtra jamais — le re-poller indéfiniment ferait grossir la requête sans fin. Marquer `checked_at` borne le travail.

### DA-03 — Idempotence par `checked_at IS NULL`

**Choix** : le SELECT filtre `checked_at IS NULL` ; un re-run saute les tickets déjà traités.
**Raison** : pattern identique aux guards `IS NULL` des autres batches (mystery_announce). Un re-run après crash est sûr — aucun double-DELETE de token.

### DA-04 — DELETE immédiat du token mort (cohérent avec DA-03 du notifier)

**Choix** : `DeviceNotRegistered` → `DELETE FROM user_push_tokens`. Pas de flag `is_valid=false`.
**Raison** : aligné sur [[ARCH_NOTIFIER]] DA-03 — un token `DeviceNotRegistered` est définitivement mort. Le notifier supprime déjà les tokens morts détectés *au moment de l'envoi* (erreur Expo synchrone) ; ce batch couvre le cas *asynchrone* où le ticket d'envoi était OK mais le reçu révèle après coup que le device n'est plus enregistré.

### DA-05 — Purge des lignes `push_receipt_tickets`

**Choix** : les lignes `push_receipt_tickets` sont purgées après 7 jours par `ratis_batch_purge`.
**Raison** : une fois `checked_at` posé, la ligne n'a plus d'utilité opérationnelle. 7 jours laissent une fenêtre de debug confortable. (Câblage de la règle de purge : à ajouter dans `ratis_batch_purge` — cf. backlog.)

## Flow principal

1. Charge `expo_receipts_url` + `push_receipt_batch_size` depuis `ratis_settings.json` (`EXPO_RECEIPTS_URL` env override possible).
2. SELECT les `push_receipt_tickets` avec `checked_at IS NULL` (LIMIT `push_receipt_batch_size`, ORDER BY `created_at`).
3. Si aucune ligne → no-op gracieux, exit 0.
4. POST les `expo_ticket_id` vers Expo *getReceipts* en chunks de 1000.
5. Pour chaque ticket : si le reçu est `status='error'` avec `details.error='DeviceNotRegistered'` → `DELETE FROM user_push_tokens WHERE token = push_token`.
6. `UPDATE push_receipt_tickets SET checked_at = now()` pour tous les tickets polled.
7. `commit`, puis INSERT `batch_sync_log`.

Mode `--dry-run` : exécute le poll, rapporte les tokens qui *seraient* supprimés, ne supprime rien et ne marque rien.

## Paramètres

- `ratis_settings.json` `notifier.expo_receipts_url` — URL de l'endpoint Expo getReceipts.
- `ratis_settings.json` `notifier.push_receipt_batch_size` — nb max de tickets traités par run (défaut 1000, = limite Expo getReceipts).

## Monitoring / logs

- Format stdout : `%(asctime)s %(levelname)s %(name)s %(message)s`
- Log par run : `polled=N receipts=M dead_tokens_removed=K`
- Exit code 1 si le batch lève (Sentry capture l'exception)
- `batch_sync_log` écrit status `success`/`failed`

## FAQ vectorisée

### Pourquoi un batch séparé alors que le notifier supprime déjà les tokens `DeviceNotRegistered` ?

Le notifier supprime les tokens morts détectés **à l'envoi** — quand Expo renvoie l'erreur dans la réponse synchrone du POST *send*. Mais Expo accepte parfois un push (ticket `ok`) et ne révèle `DeviceNotRegistered` que plus tard, dans le **reçu**. Ce cas asynchrone n'est visible que via *getReceipts*, qu'aucun chemin du notifier n'appelle. Sans ce batch, ces tokens morts s'accumulent indéfiniment.

### Que se passe-t-il si Expo ne renvoie pas de reçu pour un ticket ?

Le ticket est quand même marqué `checked_at` (DA-02). Expo ne retient les reçus que ~24h ; un ticket sans reçu après coup ne réapparaîtra jamais. Le re-poller serait du gaspillage.

### Le batch peut-il supprimer un token encore valide ?

Non. Seuls les reçus `status='error'` avec `details.error='DeviceNotRegistered'` déclenchent un DELETE. Un reçu `status='ok'` ou un reçu absent laisse le token intact.

### Comment tester localement ?

`uv run --package ratis-batch-push-receipts pytest batch/ratis_batch_push_receipts/tests/` — les tests mockent entièrement l'HTTP Expo (`fetch_receipts`). Dry-run : `uv run python batch/ratis_batch_push_receipts/push_receipts.py --dry-run`.

## Glossaire

- **DA-XX** : décision d'architecture numérotée
- **Ticket Expo** : identifiant renvoyé par l'API *push/send* d'Expo pour chaque push accepté. Persisté dans `push_receipt_tickets.expo_ticket_id`.
- **Reçu Expo (receipt)** : statut final de livraison, obtenu en interrogeant *push/getReceipts* avec les ticket IDs. Retenu ~24h côté Expo.
- **DeviceNotRegistered** : code d'erreur Expo signifiant que le push token est mort (app désinstallée, notifs désactivées). Déclenche la suppression du token.
- **checked_at** : timestamp posé une fois le reçu d'un ticket polled — empêche tout re-poll.
