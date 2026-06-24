# batch/

Jobs périodiques — un package par job, structure identique aux services : `main.py` + `pyproject.toml` + `tests/`. Chaque batch est autonome, sans endpoint HTTP exposé.

Pattern standard : `dry_run: bool` sur chaque fonction destructive — simule sans commiter. Logs structurés avec compteurs de lignes affectées.

Hébergement V1 : Railway cron jobs (gratuit dans le free tier). Redis déjà prévu pour `slowapi` — Celery Beat disponible sans infra supplémentaire.

---

## Jobs

### `ratis_batch_purge/` ✅
Nettoyage transverse + agrégation avant suppression, quotidien.
- `user_sessions` : agrège dans `user_session_stats` (ios/android/web par mois) PUIS purge lignes > 90j
- `refresh_tokens` : purge `expires_at < now() OR revoked_at IS NOT NULL`
- `optimized_routes` : purge `expires_at < now()` (TTL 24h)
- `notification_logs` : purge `sent_at < now() - 90 days`
- `image_url` scans : supprime fichier + met `NULL` sur scans `accepted`
- `image_crop_url` price_challenges : supprime sur challenges `validated/rejected`

### `ratis_batch_leaderboard/`
Mensuel — fin de mois. Calcule les CAB gagnés dans le mois, classe les users, insère dans `leaderboard_snapshots`. Basé sur `cabecoin_transactions` direction `credit`.

### `ratis_batch_reconciliation/`
Toutes les X heures. Vérifie via API prestataire (Stripe/Mangopay) les `cashback_withdrawals` en statut `pending` avec `payment_provider_ref` non NULL. Met à jour le statut si traité côté prestataire.

### `ratis_batch_off_sync/` ✅
Sync produits OFF → `products`. Voir `ARCH.md` pour détails complets.
- API : `https://world.openfoodfacts.org/api/` — config `ratis_settings.json` → `off_sync.api_base_url`
- Modes : `delta` (24h), `weekly` (7j), `monthly` (30j), `full` (dump JSONL one-shot local)
- Utilise `ratis_core/config/product_knowledge.json` pour la classification `storage_type`

### `ratis_batch_prices_sync/`
Delta sync quotidien ou hebdo depuis `https://prices.openfoodfacts.org/`. Alimente `price_consensus` en complément des scans users.
- API : `https://prices.openfoodfacts.org/api/v1/` — var `OPEN_PRICES_API_URL`

### `ratis_batch_osm_sync/`
Mise à jour hebdo ou mensuel (les magasins changent peu). Alimente `stores` (coordonnées, ouvertures/fermetures).
- API : `https://overpass-api.de/api/` — var `OSM_OVERPASS_URL`
