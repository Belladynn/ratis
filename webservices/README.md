# webservices/

Un service par domaine métier. Chaque service est un package FastAPI indépendant avec son `pyproject.toml`, ses routes, services et repositories. Tous dépendent de `ratis_core` via uv workspace.

Structure interne : `routes/` → `services/` → `repositories/` — jamais de SQL brut hors repositories.

Pour les détails d'architecture d'un service : lire son `ARCH.md`.

---

## Services

### `ratis_auth/` ✅
OAuth (Google/Apple) + email/password, JWT, refresh tokens stateful, profil utilisateur, préférences, suppression de compte (tombstone RGPD).
→ Lire `ratis_auth/ARCH.md`

### `ratis_product_analyser/` ✅
OCR tickets de caisse (PDF + image), OCR étiquettes électroniques (photo unique + batch), scans EAN manuels (scan libre + résolution unmatched), fiche produit enrichie (prix local + prix proches géolocalisés), calcul `price_consensus`.
→ Lire `ratis_product_analyser/ARCH.md`, `ARCH_barcode.md`

### `ratis_list_optimiser/`
Comparaison de prix entre magasins environnants (depuis `price_consensus`), calcul de trajet optimisé (OSRM), gestion des listes de courses. TTL routes 24h — ne pas stocker le point de départ (domicile = PII).

### `ratis_rewards/`
Cabecoins (monnaie virtuelle), cashback affiliation (Affilae/Awin/CJ), gamification (streaks, badges, niveaux), abonnement premium, codes promo, retraits cashback.

### `ratis_notifier/`
Envoi notifications push (Expo), gestion `user_push_tokens`, alertes baisse de prix (`price_alerts`), historique `notification_logs`.
