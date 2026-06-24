# KNOWN_PROBLEMS

Catalogue des problèmes récurrents et leurs solutions propres.
Mis à jour après chaque audit. Ne jamais supprimer une entrée — marquer `[RÉSOLU]` si corrigé définitivement.

---

## KP-01 — `db.commit()` manquant dans une route (L10)

**Symptôme :** La route retourne `{"claimed": true}` ou similaire mais rien n'est persisté en prod. Le test passe parce qu'il partage la même session SQLAlchemy (flush visible dans la transaction). En prod chaque requête a sa propre session — sans `commit()` tout est rollbacké silencieusement.

**Règle :** `db.commit()` obligatoire dans toute route qui mute la DB.

**Solution propre :**
```python
# ✅ Correct
def claim_mission(db, user_id, mission_id):
    mark_mission_claimed(db, mission_id)
    award_cab(db, user_id, amount, reason)
    db.commit()  # ← obligatoire
    return {"claimed": True}
```

**Détection :** Fixture `assert_no_pending_changes` dans chaque `conftest.py` — voir KP-02.

**Décision :** → `DECISIONS_ACTED.md` DA-01

---

## KP-02 — Fixture `assert_no_pending_changes` — savepoint pour IntegrityError (L30)

**Symptôme :** Un test qui provoque une `IntegrityError` (ex: doublon `affiliate_offer`) corrompt la connexion de test — tous les tests suivants échouent en cascade. Tentation de supprimer le test.

**Règle :** Ne jamais supprimer un test. Utiliser un savepoint interne pour isoler l'IntegrityError.

**Solution propre :**
```python
def test_duplicate_offer_raises(db):
    make_affiliate_offer(db, provider="affilae", external_id="X")
    db.flush()
    with pytest.raises(IntegrityError):
        sp = db.begin_nested()  # savepoint interne
        make_affiliate_offer(db, provider="affilae", external_id="X")
        db.flush()
        sp.commit()
    db.rollback()  # rollback to outer savepoint — connexion intacte ✅
```

**Pourquoi :** PostgreSQL marque toute la transaction `aborted` après une `IntegrityError` non catchée. Le savepoint limite le rollback à la sous-transaction uniquement.

**Fixture complète :**
```python
# ⚠️ NE PAS SUPPRIMER — détecte les db.commit() manquants
# Un rollback intentionnel (except block) doit appeler db.rollback() explicitement
@pytest.fixture(autouse=True)
def assert_no_pending_changes(db):
    _writes = []
    _orig_execute, _orig_commit, _orig_rollback = db.execute, db.commit, db.rollback

    def _track_execute(stmt, *args, **kwargs):
        sql = str(stmt).strip().upper()
        if sql.startswith(("INSERT", "UPDATE", "DELETE")):
            _writes.append(str(stmt)[:60])
        return _orig_execute(stmt, *args, **kwargs)

    def _track_commit():
        _writes.clear()
        return _orig_commit()

    def _track_rollback():
        _writes.clear()  # rollback intentionnel = ok
        return _orig_rollback()

    db.execute, db.commit, db.rollback = _track_execute, _track_commit, _track_rollback
    yield
    if _writes:
        pytest.fail(f"Uncommitted writes detected — missing db.commit()?\n" + "\n".join(f"  {w}" for w in _writes))
```

---

## KP-03 — Montants monétaires : centimes entiers, jamais float (L55)

**Symptôme :** `NUMERIC(10,2)` en DB, `float` dans le code. Problèmes de précision IEEE 754 — `int(float("2.50") * 100) = 249` au lieu de 250. Calculs de cashback incorrects.

**Règle :** Tous les montants monétaires en centimes entiers (`INT`) côté backend et DB. Jamais `NUMERIC`, `FLOAT`, `DECIMAL` pour un montant. Exceptions : les taux (`cashback_rate`, multiplicateurs) restent en `NUMERIC`.

**Solution propre — conversion OCR :**
```python
# ⚠️ Toujours via Decimal — jamais via float directement
from decimal import Decimal

def euros_to_cents(value: str | float) -> int:
    """Convert OCR float/string price to integer cents. Never use float directly."""
    return int(round(Decimal(str(value)) * 100))

# ✅ euros_to_cents("2.50") → 250
# ❌ int(float("2.50") * 100) → 249
```

**Dans ratis_settings.json :**
```json
"cashback_min_withdrawal": 1000  // ✅ centimes
"cashback_min_withdrawal": 10.00 // ❌ euros
```

**Décision :** → `DECISIONS_ACTED.md` DA-02

---

## KP-04 — `parent_transaction_id` double sémantique (L80)

**Symptôme :** La même colonne `parent_transaction_id` dans `cashback_transactions` pointe vers deux types de parents différents : (1) BOOST → CREDIT parent affilié, (2) CREDIT compensatoire → WITHDRAWAL échoué. Difficile à debugger en prod.

**Solution propre (V2) :**
Ajouter `parent_type TEXT CHECK IN ('boost_credit', 'withdrawal_refund')` pour distinguer les deux cas sans ambiguïté.

**Statut :** Accepté en V1 — migration pas encore en prod. La requête de traçage dans `ratis_rewards/ARCH_cashback.md` compense. À corriger avant V2.

**Décision :** → `DECISIONS_PENDING.md`

---

## KP-05 — `HTTPException` dans les services (L100)

**Symptôme :** Un service lève `HTTPException` au lieu d'une exception métier Python. En prod avec Celery (`ratis_product_analyser`), le worker attrape une exception non HTTP → tâche FAILED avec logs cryptiques, retry x3 inutiles.

**Règle :** `HTTPException` dans les routes uniquement. Les services lèvent des exceptions métier Python définies dans `exceptions.py`.

**Solution propre :**
```python
# services/exceptions.py
class MilestoneNotFound(Exception): pass
class InsufficientBalance(Exception): pass
class MilestoneAlreadyClaimed(Exception): pass

# routes/battlepass.py
@router.post("/battlepass/claim/{milestone_id}")
async def claim_milestone(milestone_id: UUID, ...):
    try:
        return battlepass_service.claim(db, user_id, milestone_id)
    except MilestoneNotFound:
        raise HTTPException(404, "milestone_not_found")
    except InsufficientBalance:
        raise HTTPException(422, "insufficient_balance")
```

**Décision :** → `DECISIONS_ACTED.md` DA-03

---

## KP-06 — `psycopg2` au lieu de `psycopg[binary]` v3 (L120)

**Symptôme :** CC utilise `psycopg2` par réflexe (driver Python par défaut dans son training). Incompatible avec `make_engine` de `ratis_core.database`.

**Règle :** Toujours `psycopg[binary]` v3. Jamais `psycopg2`. Jamais `create_engine` directement — toujours `make_engine` depuis `ratis_core.database`.

**Détection CI :**
```yaml
- name: Check no psycopg2
  run: |
    if grep -r "psycopg2" webservices/ ratis_core/ batch/ --include="*.py"; then
      echo "psycopg2 found — use psycopg[binary] v3"; exit 1
    fi
```

---

## KP-07 — `rewarded_at` jamais mis à jour dans webhooks Stripe (L135)

**Symptôme :** `referral_uses.rewarded_at` n'est jamais mis à jour dans `ratis_auth/routes/webhooks.py`. Si Stripe retry un webhook → double-reward parrainage en prod.

**Solution propre :**
```python
# Dans le handler webhook Stripe, après avoir déclenché la récompense :
db.execute(
    "UPDATE referral_uses SET rewarded_at = now() WHERE id = :id AND rewarded_at IS NULL",
    {"id": referral_use_id}
)
db.commit()
```

**Statut :** 🔴 À corriger en priorité avant toute mise en prod.

---

## KP-08 — CHECK constraints `cabecoin_transactions` : trois sources de vérité désynchronisées

**Symptôme :** Les reasons et reference_types valides pour `cabecoin_transactions` sont définis à **trois endroits** indépendants. Un oubli dans l'un d'eux ne se voit pas immédiatement — les tests passent si on touche le modèle SQLAlchemy, mais la migration manque → prod plante.

| Source | Fichier | Utilisé par |
|---|---|---|
| `VALID_REASONS` frozenset | `cab_repository.py` | Validation Python avant INSERT |
| `_CAB_REASONS` tuple | `ratis_core/models/gamification.py` | `create_all` test DB (CheckConstraint) |
| Migration Alembic | `alembic/versions/…_cabecoin_reason_*.py` | Prod/dev DB |

Idem pour `reference_type` : modèle SQLAlchemy + migration.

**Règle :** Toute nouvelle reason ou reference_type → **trois fichiers en même temps, dans le même commit** :
1. `VALID_REASONS` dans `cab_repository.py`
2. `_CAB_REASONS` dans `ratis_core/models/gamification.py`
3. Une migration Alembic `ALTER TABLE … DROP/ADD CONSTRAINT`

```python
# ⚠️ keep in sync with _CAB_REASONS in gamification.py AND cabecoin_transactions_reason_check migration
VALID_REASONS = frozenset({..., "nouvelle_reason"})
```

**Drift réel survenu (2026-05-15) :** `retro_scan` était présent dans `_CAB_REASONS` (modèle + migration `20260502_2100_retroscan`) mais **absent** de `VALID_REASONS` dans `cab_repository.py`. Toute tentative d'`award_cab(reason="retro_scan")` aurait été rejetée en Python (`ValueError`) alors que la DB l'accepte — dead-path silencieux côté batch reconciliation Job 4. Corrigé : `retro_scan` ajouté à `VALID_REASONS`.

**Test de régression — inadéquat puis corrigé :** l'ancien test (`test_achievement_unlock_in_cab_reasons`) ne vérifiait qu'**une seule** reason (`"achievement_unlock" in both`) — un spot-check incapable de détecter une dérive sur toute autre reason ; il n'a pas attrapé le drift `retro_scan`. Remplacé par une assertion d'**égalité d'ensemble complète** : `VALID_REASONS == frozenset(_CAB_REASONS)` (+ équivalent XP `VALID_XP_REASONS == frozenset(_XP_REASONS)`), plus deux tests qui lisent le CHECK live via `pg_get_constraintdef` et le comparent au frozenset. Voir `webservices/ratis_rewards/tests/test_cab.py` (`test_cab_reasons_match_model_enum`, `test_xp_reasons_match_model_enum`, `test_cab_reason_guard_matches_db_check_constraint`, `test_cab_reference_types_match_db_check_constraint`).

**`reference_type` non validé (corrigé 2026-05-15) :** `award_cab` / `debit_cab` validaient `reason` mais acceptaient n'importe quel `reference_type` — une valeur inconnue traversait Python et plantait en `IntegrityError` 500 opaque au COMMIT. Ajout d'un frozenset `VALID_REFERENCE_TYPES` dans `cab_repository.py` (miroir du CHECK `cabecoin_transactions_reference_type_check`) + helper `_validate_reference_type` appelé avant tout write.

**`xp_transactions` aligné sur `cabecoin_transactions` (RW-04, 2026-05-15) :** `xp_transactions` n'avait NI CHECK `reference_type` NI CHECK de cohérence `(reference_id IS NULL) = (reference_type IS NULL)`, contrairement à `cabecoin_transactions` — schema drift entre deux tables-ledger parallèles. Ajout des deux `CheckConstraint` à `XpTransaction.__table_args__` (`xp_transactions_reference_type_check` + `xp_transactions_reference_consistency_check`) + migration `20260515_1400_xp_tx_ref_checks`. **L'allowlist `reference_type` XP = exactement la même liste que `cabecoin_transactions`** (décision PO) — donc 4 sources désormais à garder en sync pour `reference_type` : CHECK CAB (modèle + migration) + CHECK XP (modèle + migration), plus le frozenset `VALID_REFERENCE_TYPES`. Tout littéral ajouté à l'un des deux CHECK doit être ajouté à l'autre. Test de garde `test_xp_reference_type_allowlist_matches_cab` dans `test_xp.py` vérifie l'égalité d'ensemble des deux CHECK live via `pg_get_constraintdef`.

**Décision :** → `DECISIONS_ACTED.md` DA-04

---

## KP-09 — Migration Alembic bloquée par une VIEW dépendante (L182)

**Symptôme :** `ALTER TABLE scans ALTER COLUMN price TYPE INTEGER` échoue avec :
`cannot alter type of a column used by a view or rule. DETAIL: rule _RETURN on view price_history depends on column "price"`.

**Cause :** PostgreSQL refuse de modifier le type d'une colonne référencée par une VIEW. Le `ALTER` doit se faire à nu.

**Solution propre :** Avant le `ALTER`, DROP la VIEW. Après, la recréer à l'identique.
```python
# Dans upgrade()
op.execute("DROP VIEW IF EXISTS price_history")
op.execute("ALTER TABLE scans ALTER COLUMN price TYPE INTEGER USING ROUND(price * 100)::INTEGER")
op.execute(
    "CREATE VIEW price_history AS "
    "SELECT id AS observation_id, store_id, product_ean, price, quantity, "
    "       scan_type, scanned_name, scanned_at AS recorded_at "
    "FROM scans WHERE status = 'accepted'"
)
# Idem dans downgrade() — DROP puis CREATE avec le type d'origine
```

**Règle :** Avant tout `ALTER COLUMN TYPE` en migration, vérifier les VIEWs dépendantes avec :
```sql
SELECT viewname FROM pg_views WHERE definition ILIKE '%<table_name>%';
```

---

## KP-10 — ORM model non mis à jour après migration Alembic → tests faux positifs (L210)

**Symptôme :** La migration Alembic change `amount NUMERIC(10,2)` → `INTEGER`. Les tests passent en CI mais `test_boost_success` échoue avec :
`TypeError: unsupported operand type(s) for *: 'decimal.Decimal' and 'float'`

**Cause :** Le test DB est créé via `Base.metadata.create_all()` (ORM models), pas via Alembic. Si le modèle SQLAlchemy n'est pas mis à jour, le test DB crée encore la colonne `NUMERIC` → psycopg retourne `Decimal` au lieu de `int` → le code qui fait `int_amount * float_rate` explose.

**Règle :** Toute migration qui change un type de colonne doit être accompagnée de la mise à jour du modèle SQLAlchemy correspondant dans `ratis_core/models/`. Les deux fichiers sont atomiques.

**Solution propre :**
```python
# ratis_core/models/rewards.py — avant
amount: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)

# après
amount: Mapped[int] = mapped_column(Integer, nullable=False)
```

Si le modèle est oublié : les tests passent (la session partagée masque le bug de type) mais échouent à l'exécution dès qu'on fait une opération arithmétique sur la valeur retournée.

---

## KP-11 — `db.flush()` dans un fixture conftest → faux positif `assert_no_pending_changes` (L252)

**Symptôme :** Un test qui retourne une erreur (422, 403, etc.) sans committer est signalé par `assert_no_pending_changes` comme ayant des writes non committés — alors qu'ils viennent du fixture `make_user` ou `make_xxx`, pas de la route. L'erreur est trompeuse.

**Cause :** Le fixture utilise `db.flush()` au lieu de `db.commit()` pour insérer ses données. `flush()` envoie le SQL sans commit → `_writes` du tracker n'est pas vidé. Si la route testée ne committe pas (ex : route qui retourne 422), le tracker voit encore les writes du fixture et crie au loup.

**Règle :** Dans les fixtures conftest qui insèrent des données de setup, toujours terminer par `db.commit()` — jamais `db.flush()`.

**Solution propre :**
```python
def make_user(db: Session, *, email: str | None = None) -> uuid.UUID:
    user_id = uuid.uuid4()
    db.execute(text("INSERT INTO users ..."), {...})
    db.execute(text("INSERT INTO user_cab_balance ..."), {...})
    db.execute(text("INSERT INTO user_cashback_balance ..."), {...})
    db.commit()  # ✅ vide _writes — les tests d'erreur ne déclenchent pas de faux positif
    return user_id
    # ❌ db.flush() → _writes reste peuplé → assert_no_pending_changes échoue sur les routes qui retournent une erreur
```

**Pourquoi flush() ne suffit pas :** `assert_no_pending_changes` vide `_writes` uniquement sur `db.commit()` (et `db.rollback()`). Avec `flush()`, les writes du fixture sont encore dans `_writes` quand le yield du fixture passe la main au test.

---

## KP-12 — Constantes de test en euros (`Decimal`) utilisées dans des appels DB → collision après migration centimes (L282)

**Symptôme :** Après la migration des montants en centimes entiers (`INTEGER`), des tests d'intégration échouent avec `assert 4 == Decimal('3.60')`. Deux constantes `Decimal("3.50")` et `Decimal("3.60")` roundent toutes les deux à `4` en base → deviennent identiques → la logique métier ne voit aucune différence de prix → le basculement ne se déclenche pas.

**Cause :** Les constantes de test étaient définies en euros pour les tests purement in-memory, mais réutilisées pour les appels DB (ex: `make_consensus(price=PRICE)`, `add_scan(price=PRICE)`). PostgreSQL cast le `Decimal("3.50")` en `4` via `ROUND(3.50) = 4`. Comme `ROUND(3.60) = 4` aussi, les deux prix sont identiques.

**Règle :** Séparer les constantes in-memory (euros, `Decimal`) des constantes pour appels DB (centimes, `int`). Nommer explicitement avec le suffixe `_DB`.

**Solution propre :**
```python
# ✅ Deux jeux de constantes distincts
PRICE = Decimal("3.50")   # tests purement in-memory — valeur réelle en euros
OTHER = Decimal("3.60")   # tests purement in-memory

PRICE_DB = 350             # centimes pour les appels DB (make_consensus, add_scan)
OTHER_DB = 360             # centimes pour les appels DB — différence garantie de 10 cts

# Dans les tests d'intégration DB :
make_consensus(db, store_id=..., ean=..., price=PRICE_DB)
add_scan(db, consensus_id=..., price=OTHER_DB)
assert history_row.old_price == PRICE_DB  # 350, pas Decimal('3.50')
```

**Diagnostic rapide :** Si deux prix de test distincts produisent le même résultat après round-trip DB, chercher une conversion implicite float→int qui écrase la précision.

---

## KP-13 — `httpx.AsyncClient` sans timeout → B113 bandit + risque blocage TCP (L310)

**Symptôme :** Bandit signale B113 (`request_without_timeout`, Medium/Low) sur un `httpx.AsyncClient(headers=...)` sans `timeout=`. Le batch peut bloquer indéfiniment si l'API externe ne répond pas au niveau TCP.

**Cause :** Mettre un `timeout=` sur les appels `client.get(...)` individuels ne suffit pas si `AsyncClient` lui-même n'a pas de timeout — un blocage à l'établissement de la connexion contourne les timeouts de requête.

**Règle :** Toujours passer `timeout=` au constructeur `httpx.AsyncClient`. Les timeouts sur les requêtes individuelles sont optionnellement additifs.

**Solution propre :**
```python
# ✅ Timeout sur le client (connection + read)
async with httpx.AsyncClient(
    headers={"User-Agent": "..."},
    timeout=30.0,
) as client:
    r = await client.get(url, params=params)

# ❌ Timeout seulement sur la requête — n'empêche pas un blocage TCP à la connexion
async with httpx.AsyncClient(headers={"User-Agent": "..."}) as client:
    r = await client.get(url, params=params, timeout=30)
```

**Note :** `# nosec B113` est une solution de contournement acceptable uniquement si le timeout est géré par un wrapper externe (ex: `asyncio.wait_for`). Sinon, corriger le code.

---

## KP-14 — `db.commit()` dans un GET (L325)

**Symptôme :** Un `db.commit()` traîne dans une route GET qui ne mute rien. En prod ça cause un commit inutile (coût réseau) et masque des bugs : si des writes ont été flushés par erreur en amont, ils sont validés silencieusement.

**Règle :** `db.commit()` uniquement dans les routes qui mutent la DB (POST, PATCH, PUT, DELETE).

**Solution propre :**
```python
# ❌ Mauvais — GET avec commit
@router.get("/rewards/cashback/balance")
def get_balance(db: Session = Depends(get_db), ...):
    balance = get_cashback_balance(db, user_id)
    ...
    db.commit()  # ← à supprimer
    return {...}

# ✅ Correct
@router.get("/rewards/cashback/balance")
def get_balance(db: Session = Depends(get_db), ...):
    balance = get_cashback_balance(db, user_id)
    ...
    return {...}
```

**Détection :** Grep `db.commit()` dans les fonctions de route `@router.get`.

**[RÉSOLU]** Supprimé dans `routes/rewards/cashback.py` — audit gift_cards 2026-04.

---

## KP-15 — Validation `resolution` absente au niveau service (L350)

**Symptôme :** Le service `resolve_cashback()` acceptait `resolution = "anything"` — la validation n'était faite qu'au niveau de la route Pydantic. Si le service est appelé directement (batch, test, autre service), une valeur invalide passait silencieusement dans le `elif` ou tombait dans le cas implicite.

**Règle :** Tout paramètre métier critique doit être validé dès l'entrée du service, indépendamment de la validation route.

**Solution propre :**
```python
def resolve_cashback(db, tx_id, resolution, rewards_cfg):
    if resolution not in ("confirmed", "refused"):
        raise ValueError(f"invalid resolution: {resolution!r}")
    # ...
```

**[RÉSOLU]** Guard ajouté dans `services/cashback_service.py` — audit gift_cards 2026-04.

---

## KP-16 — Validation `ge=0` absente sur un montant entier en centimes (L374)

**Symptôme :** Un champ `price: int` (centimes) dans un Pydantic model d'entrée n'avait pas de contrainte `ge=0`. Un appel malveillant avec `price: -500` aurait crédité du cashback négatif (débit implicite de la balance).

**Règle :** Tout champ centimes dans un schema de route doit avoir `Field(..., ge=0)`.

**Solution propre :**
```python
from pydantic import BaseModel, Field

class ReceiptLine(BaseModel):
    ean: str
    price: int = Field(..., ge=0)  # centimes
    scan_id: uuid.UUID
```

**[RÉSOLU]** Ajouté dans `routes/rewards/cashback.py:ReceiptLine` — audit gift_cards 2026-04.

---

## KP-17 — `_mark_failed` logue en WARNING mais l'échec est critique (L398)

**Symptôme :** Si `update_order_failed()` échoue (ex: connexion DB perdue), l'exception était loguée en `WARNING`. En prod la commande de carte cadeau reste bloquée en `pending` indéfiniment — aucune alerte ne se déclencherait sur un WARNING.

**Règle :** Un échec à persister un état d'erreur est un événement critique → `log.error`.

**Solution propre :**
```python
def _mark_failed(db, order_id):
    try:
        update_order_failed(db, order_id)
    except Exception:
        log.error(
            "issue_gift_card: CRITICAL — could not mark order %s as failed "
            "(order may be stuck in pending)",
            order_id,
            exc_info=True,
        )
```

---

## KP-18 — `slowapi` actif dans les tests : cross-test pollution + rate limits non testables (L431)

**Symptôme :** Après ajout de `@limiter.limit("N/minute")` sur un endpoint, les tests qui frappent cet endpoint plus de N fois dans une même session pytest reçoivent des 429 inattendus. `TestClient` partage la même IP (`testclient`) et le storage slowapi persiste entre les tests.

**Deux mauvaises réponses :**
- `RATELIMIT_ENABLED=False` dans `conftest.py` → corrige la pollution mais rend le rate limiting entièrement non testable (aucun test ne peut vérifier le 429).
- Ne rien faire → tests qui passent seuls échouent en CI selon l'ordre d'exécution.

**Solution propre :** Fixture `autouse=True` qui reset le storage entre chaque test, combinée à des tests dédiés qui épuisent la limite explicitement.

```python
# conftest.py
@pytest.fixture(autouse=True)
def reset_rate_limiter():
    """Reset in-memory rate limit counters between tests to prevent cross-test pollution."""
    from limiter import limiter
    limiter._storage.reset()
```

```python
# test_scan_receipt.py — test dédié rate limiting
def test_fourth_request_returns_429(self, client, store, user):
    # Épuise la limite (3/minute) avec 3 requêtes distinctes
    for img in [_JPEG_BYTES, _PNG_BYTES, _WEBP_BYTES]:
        client.post("/api/v1/scan/receipt", data={"store_id": str(store.id)},
                    files={"image": ("t.jpg", io.BytesIO(img), "image/jpeg")},
                    headers=_auth(user))
    resp = client.post("/api/v1/scan/receipt", data={"store_id": str(store.id)},
                       files={"image": ("t.jpg", io.BytesIO(_PDF_BYTES), "image/jpeg")},
                       headers=_auth(user))
    assert resp.status_code == 429
    assert resp.json()["detail"] == "rate_limit_exceeded"
```

**Règle :** Tout service qui ajoute `slowapi` doit avoir dans son `conftest.py` la fixture `reset_rate_limiter` ET des tests qui vérifient le 429. Pattern référence : `ratis_auth/tests/conftest.py` + `test_auth.py::test_register_rate_limited`.

---

## KP-19 — Données de test identiques → violation de contrainte UNIQUE silencieuse dans les batchs (L468)

**Symptôme :** Un test batch qui envoie N items avec la même valeur constante (ex: `_JPEG_BYTES` pour 3 images) passe avant l'ajout d'une contrainte UNIQUE sur cette valeur, puis retourne 409/IntegrityError après. Le test semble valide — il envoie des données légitimes — mais la contrainte rejette les doublons dès le 2e item.

**Exemple concret :**
```python
# AVANT — passe car pas de contrainte UNIQUE sur photo_hash
def _multi_payload(self, n=3):
    return [("images", (f"label_{i}.jpg", io.BytesIO(_JPEG_BYTES), "image/jpeg"))
            for i in range(n)]

# APRÈS ajout de la déduplication photo — le 2e scan est rejeté 409
# car tous les items ont le même SHA-256
```

**Solution propre :** Distinguer les valeurs par un suffixe unique indexé sur `i`. Le suffixe en fin de bytes ne perturbe pas la détection MIME (magic bytes en début).

```python
def _multi_payload(self, n=3):
    # Suffixe bytes([i]) garantit des SHA-256 distincts par item
    return [("images", (f"label_{i}.jpg", io.BytesIO(_JPEG_BYTES + bytes([i])), "image/jpeg"))
            for i in range(n)]
```

**Règle générale :** Tout helper de test qui crée plusieurs entités en boucle doit utiliser des valeurs distinctes sur les colonnes UNIQUE de la table cible. Avant d'ajouter une contrainte UNIQUE, auditer les helpers de test existants qui insèrent des séries de données identiques (`for i in range(n)` avec constante).

---

## KP-20 — `PendingRollbackError` dans un `except IntegrityError` : accès attribut ORM avant `db.rollback()` (L496)

**Symptôme :** Un `except IntegrityError` attrape correctement l'exception (ex: violation de contrainte UNIQUE sur un flush), appelle ensuite `logger.warning(..., obj.some_attr)` ou toute autre expression qui accède à un attribut ORM — et une `PendingRollbackError` non capturée remonte jusqu'à Celery / l'appelant.

**Cause :** Quand un `db.flush()` échoue, SQLAlchemy fait deux choses simultanément :
1. Met la session en état `DEACTIVE` (needs rollback)
2. **Expire tous les objets modifiés** qui participaient au flush raté

Accéder à un attribut d'un objet expiré déclenche un lazy-load → SQLAlchemy tente un SELECT → session `DEACTIVE` → `PendingRollbackError`. Cette exception est levée **à l'intérieur** du bloc `except`, donc non capturée par les autres `except` du même `try`.

**Exemple concret :**
```python
# ❌ Mauvais — receipt est expiré après le flush raté
except IntegrityError as exc:
    if "receipts_semantic_dedup_key" in str(exc.orig):
        logger.warning("duplicate store_id=%s", receipt.store_id)  # lazy-load DEACTIVE → PendingRollbackError
        db.rollback()
        create_scan(db, ...)

# ✅ Correct — rollback en premier, puis accès aux attributs
except IntegrityError as exc:
    if "receipts_semantic_dedup_key" in str(exc.orig):
        db.rollback()   # session ACTIVE, lazy-load autorisé
        logger.warning("duplicate store_id=%s", receipt.store_id)
        create_scan(db, ...)
```

**Règle :** Dans tout `except` qui suit un `flush()` ou `commit()` raté, appeler `db.rollback()` en **première instruction** avant tout accès à un objet ORM.

**Variante test — `flush()` sans `commit()` avant `_run()` dans un fixture `create_savepoint`** :

Avec `join_transaction_mode="create_savepoint"`, `db.flush()` sans `db.commit()` laisse la ligne **à l'intérieur du savepoint courant**. Si le handler appelle `db.rollback()` (ROLLBACK TO SAVEPOINT), cette ligne est effacée → FK violation sur les opérations suivantes.

```python
# ❌ Mauvais — r2 dans le savepoint, sera rollbacké
db.add(r2); db.flush()
self._run(db, r2, ...)  # rollback interne efface r2 → FK violation

# ✅ Correct — r2 "released" dans la transaction outer avant le savepoint
db.add(r2); db.flush(); db.commit()
self._run(db, r2, ...)
```

**Règle test :** Dans les fixtures utilisant `join_transaction_mode="create_savepoint"`, tout objet qui doit survivre à un `db.rollback()` interne doit être committé (`db.commit()`) avant d'appeler la fonction testée.

---

## KP-21 — Exception pré-écriture dans `db_transaction` : rollback annule le setup de test (L543)

**Symptôme :** Un test qui configure la DB via un helper (`_set_cashback_balance`, etc.) puis appelle une route qui lève une exception avant tout write (ex: `BelowMinimum`, `ValidationError`) voit son setup annulé. `assert bal == 1500` renvoie `0` après le call.

**Cause :** `db_transaction` appelle `db.rollback()` sur toute exception, y compris les exceptions levées avant le premier write. Ce rollback remonte jusqu'au SAVEPOINT courant du conftest et annule toutes les insertions du helper.

**Exemple concret :**
```python
# Helper utilise db.flush() — setup reste dans le SAVEPOINT courant
def _set_cashback_balance(db, user_id, amount):
    db.execute(text("UPDATE user_cashback_balance SET balance = :bal ..."), ...)
    db.flush()  # ❌ dans le SAVEPOINT — effacé par le rollback de la route

# Route : BelowMinimum levé avant tout write → db_transaction roule tout en arrière
# Test : balance retournée = 0, pas 1500
```

**Solution propre — deux règles complémentaires :**

1. **Helpers de test :** utiliser `db.commit()`, pas `db.flush()`, pour que le setup soit released avant le SAVEPOINT de la route.

```python
def _set_cashback_balance(db, user_id, amount):
    db.execute(text("UPDATE user_cashback_balance SET balance = :bal ..."), ...)
    db.commit()  # ✅ released — survit au rollback de la route
```

2. **Routes :** pré-valider les exceptions pré-écriture avant d'entrer dans `db_transaction`. Évite le rollback inutile et rend la validation explicite.

```python
# ✅ Pré-check avant la transaction — BelowMinimum ne peut pas sortir du with
if body.amount < rewards_cfg["cashback_min_withdrawal"]:
    raise HTTPException(status_code=422, detail="below_minimum")
try:
    with db_transaction(db):
        result = withdraw_cashback(db, current_user.id, body.amount, rewards_cfg)
except InsufficientCashbackBalance:
    raise HTTPException(status_code=422, detail="insufficient_balance")
```

**Pourquoi `begin_nested()` ne suffit pas :** utiliser `db.begin_nested()` dans `db_transaction` et appeler `nested.rollback()` sur exception évite d'annuler le SAVEPOINT du test, mais `assert_no_pending_changes` patche uniquement `db.commit()` et `db.rollback()` (méthodes session) — `nested.rollback()` (méthode `NestedTransaction`) n'est pas intercepté, les writes restent dans `_writes` et le fixture fail en faux positif.

**Règle :** Toute exception métier levée avant le premier write DB doit être pré-vérifiée dans la route, avant d'entrer dans `db_transaction`. Les helpers de test qui configurent un état persistent doivent toujours terminer par `db.commit()`.

---

## KP-22 — Import circulaire entre repositories : lazy import comme workaround (L590)

**Symptôme :** Un repository A doit appeler un repository B, mais B importe déjà A au niveau module. Tentation de faire `from B import func` à l'intérieur de la fonction pour éviter le cycle (`ImportError` au chargement sinon).

**Exemple concret :**
```
challenge_repository → (import lazy) → cab_repository
cab_repository       → (module level) → challenge_repository  ← cycle
```

`_apply_reward` dans `challenge_repository` importait `award_cab` / `award_xp` en lazy pour éviter le cycle. Ça fonctionne mais c'est un code smell : l'import conditionnel masque une violation de la hiérarchie des dépendances.

**Cause racine :** La logique d'application de récompense (appel à `award_cab`/`award_xp`) n'a pas sa place dans le repository. Un repository ne doit contenir que des opérations DB — pas orchestrer d'autres repositories.

**Solution propre :**

Le repository retourne une spec de récompense sans l'appliquer. L'orchestration est dans la route (ou un service dédié) :

```python
# challenge_repository.py — retourne la spec, n'applique rien
def claim_milestone(db, user_id, milestone_id) -> dict:
    # ... validation + INSERT claim ...
    return {"reward_type": ..., "reward_value": ..., "challenge_id": ...}

# routes/gamification/challenge.py — orchestre
result = claim_milestone(db, user_id, milestone_id)
if result["reward_type"] == "cab":
    award_cab(db, user_id, result["reward_value"]["amount"], "challenge_milestone", ...)
elif result["reward_type"] == "xp":
    award_xp(...)
elif result["reward_type"] == "multiplier":
    create_community_multiplier(...)
```

**Règle :** Un repository ne doit jamais importer un autre repository, même en lazy. Si c'est nécessaire, l'orchestration appartient à la couche au-dessus (route ou service). Les imports lazy sont un signal d'alerte, pas une solution.

---

## KP-23 — Modèle SQLAlchemy diverge de la migration : index partiels et contraintes custom absents

**Symptôme :** Un test qui dépend d'une contrainte DB (unique, partial index, check) passe sur la DB de dev (mise à jour par Alembic) mais échoue silencieusement en test — la contrainte n'est pas levée car elle n'existe pas dans le schéma `create_all`.

**Cause :** `create_all` crée les tables depuis `Base.metadata` (modèles SQLAlchemy). Les index partiels, contraintes CHECK custom, et tout ce qui est créé dans la migration via `op.create_index(postgresql_where=...)` ou `op.execute("ALTER TABLE...")` n'existent dans le test DB **que s'ils sont aussi déclarés dans `__table_args__`** du modèle.

**Exemple concret (ce bloc) :**

`community_challenges_one_active` est un index partiel unique `WHERE is_active = TRUE`. Créé dans la migration :
```python
op.create_index("community_challenges_one_active", "community_challenges",
                ["is_active"], unique=True, postgresql_where="is_active = TRUE")
```
Mais absent du modèle `CommunityChallenge`. Résultat : en test, deux lignes `is_active = TRUE` coexistent sans erreur → `activate_challenge` retourne 200 au lieu de 409.

**Solution propre :**

Déclarer l'index dans `__table_args__` du modèle SQLAlchemy :
```python
from sqlalchemy import Index, text as sa_text

class CommunityChallenge(Base):
    ...
    __table_args__ = (
        Index(
            "community_challenges_one_active",
            "is_active",
            unique=True,
            postgresql_where=sa_text("is_active = TRUE"),
        ),
    )
```

Avec ça, `create_all` crée l'index en test DB → l'`IntegrityError` est levée nativement → pas besoin de vérification Python TOCTOU.

**Règle :** Toute contrainte créée dans une migration Alembic (`op.create_index`, `op.execute("ALTER TABLE ADD CONSTRAINT")`) doit aussi être déclarée dans `__table_args__` du modèle correspondant. Migration + modèle = source de vérité duale — les deux doivent rester en sync.

**Corollaire :** Ne jamais compenser l'absence d'une contrainte DB par une vérification Python avant le write (TOCTOU). Corriger la déclaration du modèle à la place.

---

## KP-24 — Appel service externe + mutation DB en boucle : crash safety et portée des exceptions

**Symptôme A (commit global) :** Un batch supprime N objets R2 puis fait `db.commit()` une seule fois après la boucle. Si le process crashe à mi-boucle, les objets R2 sont déjà supprimés mais la colonne de traçage (`image_deleted_at`, `label_r2_key`, etc.) n'est jamais mise à jour. Au prochain run, le batch ré-interroge les mêmes lignes, tente de supprimer des clés inexistantes (silencieusement ignoré par R2), et ne progresse jamais → lignes bloquées en permanence.

**Symptôme B (portée exception trop étroite) :** `except (BotoCoreError, ClientError)` ne couvre pas les erreurs réseau de bas niveau que boto3 peut remonter non wrappées (`socket.gaierror`, `urllib3.exceptions.ReadTimeoutError`…). Ces exceptions propagent jusqu'au caller et font échouer l'étape entière au lieu de logger un warning et continuer.

**Symptôme C (dry-run par early return) :** Utiliser `if dry_run or not rows: return` au lieu du pattern `if not dry_run: db.commit()` est fonctionnellement équivalent mais incohérent avec le reste du fichier. Si on refactorise la fonction plus tard en retirant le early return, le dry-run sera cassé silencieusement.

**Solution propre :**

```python
def purge_xxx(Session, dry_run: bool) -> None:
    with Session() as db:
        rows = db.execute(text("SELECT id, r2_key FROM ... WHERE ...")).fetchall()
        log.info("xxx: %d eligible", len(rows))

        if not dry_run:
            client = _get_r2_client()
            for row in rows:
                if _r2_delete(row.r2_key, client):
                    db.execute(text("UPDATE ... SET col = ... WHERE id = :id"), {"id": str(row.id)})
                    db.commit()  # commit par ligne : crash safe


def _r2_delete(key: str, client) -> bool:
    try:
        client.delete_object(Bucket=os.environ["R2_BUCKET_NAME"], Key=key)
        return True
    except Exception as exc:          # pas seulement BotoCoreError/ClientError
        log.warning("R2 delete failed for %s: %s", key, exc)
        return False
```

**Règles :**
1. `db.commit()` à l'intérieur de la boucle, pas après — un commit par objet traité avec succès.
2. `except Exception` dans le helper R2, pas `except (BotoCoreError, ClientError)`.
3. Dry-run via `if not dry_run:` englobant le bloc actif, jamais via early return — cohérence avec le reste du fichier.
4. Ces mêmes règles s'appliquent à tout service externe (Stripe, prestataire cadeau, webhook) combiné à une mutation DB.

## KP-25 — `.env.prod` saved with Windows line endings (CRLF) silently breaks all credentials

**Symptôme** : tous les services backend démarrent OK mais leurs appels externes (Stripe, R2, Google ID token validation, JWT roundtrip) échouent silencieusement avec des erreurs cryptiques type `InvalidArgument: length 53 should be 32` (R2 boto3) ou des 401 mystérieux.

**Cause** : le fichier `.env.prod` avait été édité ou copié depuis Windows, ce qui a inséré `\r\n` (CRLF) à chaque fin de ligne. Quand Docker Compose lit `.env.prod`, il prend la ligne `R2_ACCESS_KEY_ID=cbdbad63...0fc0\r` — le `\r` final fait partie de la valeur. Boto3 voit donc 33 caractères au lieu de 32, JWT_SECRET `secret\r` au lieu de `secret`, etc. Tout fail mais avec des erreurs qui pointent ailleurs.

**Détection** :
```bash
grep -c $'\r' .env.prod    # count of CRs — should be 0
awk -F= '/^R2_/ {print $1, length($2)}' .env.prod   # lengths off by 1
```

**Fix curatif** :
```bash
sed -i 's/\r$//' .env.prod
docker compose -p ratis -f docker-compose.prod.yml --env-file .env.prod \
  up -d --force-recreate <services...>
```
`restart` ne suffit pas — Compose ne recharge pas l'env_file sur restart, il faut `up -d --force-recreate` (ou `down` puis `up`).

**Fix préventif** : éditer `.env.prod` SUR le serveur Linux (via `ssh + nano`), pas via SCP depuis Windows. Si édition Windows nécessaire, configurer Git/l'éditeur pour LF (`.editorconfig` + `git config core.autocrlf input`).

**Découvert** : alpha 2026-04-26, après le bug d'auth Google qui pointait sur "InvalidArgument" R2 — la vraie cause était le CRLF dans toute la file, pas juste les vars R2. ALL credentials étaient corrompus.

---

## KP-26 — `expo-auth-session/providers/google` redirige sur `<scheme>://oauthredirect` qui crash expo-router en "unmatched route"

**Symptôme** : OAuth Google complète côté Google (UI, choix du compte, validation), puis l'app reçoit le redirect URL `app.<package>.client://oauthredirect?code=...&id_token=...` comme deep-link, mais expo-router ne trouve aucune route correspondante → écran "unmatched route" + l'AuthSession listener est mort (parce que cold-started par le deep-link). Sentry vide. Aucun log d'erreur.

**Cause** : `Google.useAuthRequest({ androidClientId, ... })` (du module `expo-auth-session/providers/google`) fixe `redirectUri = ${Application.applicationId}:/oauthredirect`. Le custom-tab Chrome doit consommer cette URL via le callback intra-app. Mais sur certains devices, le custom-tab échoue à intercepter et le redirect devient un deep-link OS qui réveille l'app cold-start. À ce moment-là, le callback `WebBrowser.openAuthSessionAsync` qui aurait résolu `promptAsync()` n'existe plus.

**Fix** : ne pas utiliser `expo-auth-session/providers/google`. Migrer vers le SDK natif Google Play Services :
```bash
npx expo install @react-native-google-signin/google-signin
```
Puis dans AuthContext :
```ts
import { GoogleSignin } from "@react-native-google-signin/google-signin";

GoogleSignin.configure({ webClientId, iosClientId, scopes: [...] });

// Pas de browser, pas de deep-link, pas de redirect URI à configurer.
const response = await GoogleSignin.signIn();
const idToken = response.data?.idToken;
```
Le SDK natif passe par Google Play Services directement, jamais par un custom-tab → pas d'opportunity pour le deep-link de leak vers expo-router.

**À noter aussi** : appeler `GoogleSignin.configure()` **avant chaque `signIn()`** (pas seulement au boot du module), car la config native peut être perdue après freeze du process Android (post-veille → "Erreur inattendue").

**Découvert** : alpha 2026-04-26 (PR #102 = migration définitive).

---

## KP-27 — `eas update` sans `--environment` charge le `.env.local` du repo, pas les EAS env vars

**Symptôme** : OTA publié, l'app fetch le bundle, mais une fois appliqué les fetch partent sur `http://192.168.1.x:8001/api/v1/...` (= IPs LAN du dev) au lieu des URLs prod → "Network request failed" à chaque appel API. Tags Sentry confirment `environment=production`, mais les xhr breadcrumbs montrent les LAN URLs.

**Cause** : `eas update --channel preview` (sans `--environment <name>`) ne charge pas les variables d'environnement enregistrées sur EAS via `eas env:create`. Il prend par défaut le `.env.local` du répertoire courant — qui en dev contient les LAN URLs. Le bundle bake ces valeurs au lieu des prod.

**Détection** :
```bash
eas env:list --environment preview     # vérifier que les vars sont bien sur EAS
# Comparer avec ce qui est dans le bundle via Sentry breadcrumbs xhr
```

**Fix** : **TOUJOURS** passer `--environment <name>` en plus de `--channel <name>` :
```bash
eas update --channel preview --environment preview --message "..."
eas update --channel production --environment production --message "..."
```

**Fix préventif côté code** (R33 : solution propre, pas fallback silencieux) : utiliser `requireEnv(name, value)` au lieu de `??`/`||` pour les `EXPO_PUBLIC_*` URLs. Si une var manque, throw bruyamment avec le nom exact de la var → l'erreur est immédiate, pas masquée par un fallback hardcodé qui pointerait sur prod sans qu'on s'en rende compte. Cf `services/env.ts` + `services/api-client.ts` pour le pattern.

**Découvert** : alpha 2026-04-26 (PR #107).

---

## KP-28 — Photos prises en portrait ont l'orientation EXIF dans le tag, pas dans les pixels — OCR side-server lit à l'envers

**Symptôme** : ticket photographié droit dans l'app, prévisualisation droite côté front (galerie photos = OK), upload OK côté backend (R2 stocke bien le fichier), mais PaddleOCR ne lit que des fragments incohérents (typiquement le pied du ticket : TVA, mode paiement) et rate complètement la liste des items achetés.

**Cause** : Android (notamment expo-camera avec `skipProcessing: true`) produit un JPEG dont les **pixels sont en orientation capteur** (paysage) avec un **tag EXIF orientation** disant "à l'affichage, rotation 90°". iOS Photos / Chrome / l'app Galerie honorent ce tag → image droite. Mais beaucoup de libs server-side ignorent EXIF par défaut : boto3 streaming, PIL sans `ImageOps.exif_transpose`, OpenCV `imread`. PaddleOCR utilise une de ces libs → il lit l'image en orientation capteur → le texte est à 90° (ou 180° sur certains tels), illisible.

Si la photo a été prise tête-à-l'envers, l'OCR lira mirrored et capturera le pied du ticket en premier (qui se trouve "en haut" une fois inversé).

**Détection** :
- Symptôme typique : OCR sort uniquement des labels de TVA / lignes de paiement (CB, ESPECES, TVA 5,5%) et 0 item.
- Vérifier : télécharger l'image depuis R2, l'ouvrir avec un tool qui ignore EXIF (genre `cv2.imread`) — si elle est à l'envers, le bug est confirmé.

**Fix** : flatten l'EXIF dans les pixels AVANT l'upload, côté front. Avec `expo-image-manipulator` :
```ts
import * as ImageManipulator from 'expo-image-manipulator';

const flattened = await ImageManipulator.manipulateAsync(
  capturedUri,
  [{ resize: { width: 1600 } }],   // bonus : downscale, PaddleOCR over-segmente au-delà de ~2000 px
  { compress: 0.75, format: ImageManipulator.SaveFormat.JPEG },
);
// flattened.uri → upload via FormData. Pixels en bonne orientation, EXIF nettoyé.
```

**Pourquoi pas `skipProcessing: false` côté camera** : ce flag d'expo-camera est censé baker EXIF dans les pixels, mais sa sémantique varie selon Android/iOS et selon les versions du module. `expo-image-manipulator` est explicite, indépendant des évolutions camera, et permet aussi de resize au passage (bonus pour la qualité OCR).

**Découvert** : alpha 2026-04-26 (PR #118).

---

## KP-29 — `.env.local` placeholder leak in pytest conftest : 401 storm masqué en "DB pollution"

**Symptôme :** `pytest` lancé localement sur une machine dev → la quasi-totalité des tests authentifiés échouent en `401 Unauthorized` (ex : 119/575 sur ratis_product_analyser). CI vert (DB éphémère par run, pas de `.env.local` injecté). Au premier coup d'œil ça ressemble à de la pollution DB ou un problème d'auth random — le **vrai** root cause est plus subtil.

**Root cause :** `webservices/<svc>/tests/conftest.py` charge `.env.local` AVANT d'appliquer ses defaults via `os.environ.setdefault(...)`. Le `.env.local` est un fichier **développeur** local (gitignored) qui peut contenir des **placeholders littéraux** copiés depuis `.env.example` :

```env
# webservices/ratis_product_analyser/.env.local (oublié rempli)
JWT_SECRET=<random-256-bit-secret>
DATABASE_URL=postgresql+psycopg://<user>:<password>@<host>:<port>/<dbname>
```

Le flow de pollution :
1. `load_dotenv(.env.local)` → `os.environ["JWT_SECRET"] = "<random-256-bit-secret>"`
2. `os.environ.setdefault("JWT_SECRET", "test-secret-key-for-tests-only")` → no-op (clé déjà set par dotenv).
3. `make_token(...)` (helper de tests) signe avec la constante littérale `"test-secret-key-for-tests-only"`.
4. Le serveur sous test décode avec `os.environ["JWT_SECRET"]` = `"<random-256-bit-secret>"`.
5. Mismatch HS256 → 401 sur **tous** les tests authentifiés.

**Faux symptômes possibles** (à éviter de partir dans cette direction) :
- "La DB ratis_test est polluée par un alembic résiduel" — non, le conftest fait `DROP SCHEMA public CASCADE; create_all` qui absorbe ça.
- "`pg_trgm` n'est pas installé" — non, `CREATE EXTENSION IF NOT EXISTS` est idempotent.
- "C'est un problème de TestClient / dependency override" — non, c'est strictement la signature JWT.

**Remédiation (appliqué pour ratis_product_analyser dans `chore/fix-local-pytest-db-pollution`) :**
1. **Ne PAS load_dotenv dans conftest.** Les tests doivent être hermétiques. Si un dev a besoin d'override `TEST_DATABASE_URL`, il l'exporte dans son shell.
2. **Force-set, pas setdefault.** Remplacer `os.environ.setdefault(KEY, VAL)` par `os.environ[KEY] = VAL` dans conftest pour les valeurs critiques (JWT_SECRET, R2 fakes, etc.) — défense en profondeur si le shell exporte une valeur polluée.
3. **Sentinelle post-`create_all`** : juste après `Base.metadata.create_all()`, exécuter `SELECT 1 FROM products LIMIT 0` (et autres tables critiques). Si ça plante, lever un `RuntimeError` explicite ("conftest setup failed: products table not created — likely a model import issue") au lieu de laisser pytest courir sur un schéma cassé.

**Étendue aux 4 autres services (2026-04-27)** : `ratis_auth`, `ratis_rewards`, `ratis_notifier`, `ratis_list_optimiser` ont reçu le même fix dans la PR `chore/hermetic-conftest-other-services`. Validation : auth 141/0/0 (19s) + rewards 350/0/0 (11s) + notifier 36/0/1 (2s, skip déjà documenté) + list_optimiser 96/0/0 (11s) = **623/0/1 across the 5 services**. La dette latente est fermée.

**Refactor candidat (hors scope KP-29)** : un `ratis_core/test_env.py` partagé qui centraliserait le force-set des env de test, éliminerait la duplication entre les 5 conftests. Aussi : harmoniser le pattern `db` fixture (auth/rewards/notifier en manuel `nonlocal nested`, PA et list_optimiser en moderne `join_transaction_mode="create_savepoint"`). À tagger en futur cleanup.

**Scenario de repro pour valider une remédiation** :
```bash
# 1. Avoir un .env.local avec placeholder JWT_SECRET=<random-256-bit-secret>
cd webservices/ratis_product_analyser
TEST_DATABASE_URL=postgresql+psycopg://ratis:ratis@localhost:5432/ratis_test \  # pragma: allowlist secret
  uv run pytest tests/test_scan_receipt.py::TestPostScanReceipt::test_returns_202_and_receipt_id
# Avant fix : 401 == 202 AssertionError
# Après fix : passes.
```

---

## KP-30 — Worktree gotcha : Edit tool sur paths absolus écrit dans le main checkout, pas le worktree isolé du SA

**Symptôme :** un SA dispatché avec `isolation: "worktree"` (qui crée `.claude/worktrees/agent-<id>/`) est censé travailler dans son worktree isolé. En pratique, quand le SA utilise le `Edit` tool avec un **path absolu** (ex : `C:/Users/FlowUP/Cursor/Ratis/webservices/.../file.py`), le fichier modifié atterrit dans **le main checkout** au lieu du worktree isolé. Le SA reporte des modifs effectuées, mais après son retour les changements sont sur le worktree principal (pas commités, pas sur sa branche). Source d'orphelins indétectables.

**Découverte :** 2026-04-26 lors de PR #124 — le SA avait des modifs sur le worktree principal alors que le worktree isolé du SA était vide. Reproduction confirmée par 2 SAs différents (SA #125 et #127) dans la session 2026-04-27. Le SA #126 ne l'a PAS rencontré (raison : il a check-out la branche cible directement dans son worktree isolé d'abord, puis fait toutes ses modifs via paths relatifs au worktree).

**Cause hypothétique :** le `Edit` tool utilise le path absolu fourni tel quel ; le harness Claude Code n'intercepte pas pour rerouter vers le cwd du SA. La séparation `git worktree add` crée bien un dossier isolé, mais le Edit tool ignore cette structure si on lui donne `C:/...`. Si le SA pense qu'il est dans son worktree mais que le Edit pointe ailleurs, il croit ses modifs visibles via `git status` du SA — alors qu'elles sont en fait sur le main worktree.

**Workaround découvert (SA #125, repris en SA #127) :**
```bash
# Avant le commit — copier les modifs orphelines depuis le main worktree vers le worktree isolé
WORKTREE="$(git rev-parse --show-toplevel)"
for f in $LIST_OF_MODIFIED_FILES; do
  cp "C:/Users/FlowUP/Cursor/Ratis/$f" "$WORKTREE/$f"
done
# puis git add depuis le worktree isolé
```

**Solution propre (à investiguer) :**
1. Toujours `git -C <worktree-path>` pour les commandes git, et utiliser des paths **relatifs au worktree** dans les Edit calls
2. OU : checkout la branche cible dans le worktree isolé en premier, et faire toutes les modifs depuis ce cwd
3. OU : modifier le brief pour interdire les paths absolus dans Edit (mais le harness force parfois leur usage en cas de Read d'un fichier d'un autre worktree)

**Impact orchestrator :** un SA qui ne connaît pas le gotcha pousse des modifs invisibles. L'orchestrateur doit, au retour du SA, vérifier `git -C <main-worktree> status` pour repérer d'éventuels orphelins. Si trouvés, deux options : (a) si le SA avait commit fait dans son worktree, les orphelins sont des duplicates → reset main ; (b) si le SA n'a pas commité, les orphelins sont **les vraies modifs** → les commit sur la bonne branche.

**Action :** à mentionner dans le brief de tout SA dispatch en attendant fix harness, et à surveiller à chaque retour SA. R39 candidat dans ORCHESTRATOR.md si le pattern persiste.

---

## KP-31 — Anthropic prompt caching : minimum prefix size silently no-ops

**Symptôme :** `cache_control={"type": "ephemeral"}` ajouté sur un system block dans un appel `messages.create`, mais `usage.cache_read_input_tokens` reste à 0 sur les requêtes successives. Coût input ne baisse pas malgré des appels répétés avec exactement le même prompt.

**Cause :** Anthropic impose un **minimum cacheable prefix par modèle** :
- **Claude Haiku 4.5** : 4096 tokens
- (autres modèles : à vérifier au cas par cas dans la doc Anthropic)

Si le bloc marqué `cache_control` est sous ce seuil, le marker est **silently ignored** — la lib `anthropic` ne raise pas, ne warn pas. La requête part non-cachée comme s'il n'y avait pas de marker.

**Détection :**
```python
# Logger ce champ sur chaque appel pour observability :
logger.info(
    "llm.receipt.parsed cache_read_input_tokens=%d input_tokens=%d",
    response.usage.cache_read_input_tokens,
    response.usage.input_tokens,
)
# Si cache_read_input_tokens reste à 0 sur N requêtes identiques d'affilée → cache pas activé.
```

**Découverte :** 2026-04-27 lors de PR #127 (AnthropicLlmFilter + skill `claude-api`). La skill claude-api a flaggé le seuil 4K. Notre `_SYSTEM_PROMPT` actuel = ~1000 tokens → **le marker est forward-compat seulement, pas un gain immédiat**.

**Mitigation (3 options) :**

1. **Grossir le system prompt au-delà du seuil** (recommandé pour ratis_product_analyser) :
   - Few-shot examples (3-5 tickets fully worked) → ~1500 tokens, gain qualité massif en plus du cache
   - Patterns OCR error courants (substitutions typiques PaddleOCR) → ~400 tokens
   - Patterns retailers FR connus (supermarché vs takeaway vs pharmacie) → ~500 tokens
   - Edge case rules étendues (multi-payment, gift cards, refunds) → ~300 tokens
   - **Total cumulé : ~3700-4000 tokens, suffisant pour activer le cache**
   - Bonus : le passage de 1K à 4K est aussi celui qui **améliore le plus la précision** sur les cas borderline. Cache = side-effect.

2. **Accepter le no-op tant que le prompt est court** (statu quo court terme) :
   - Garder le `cache_control` dans le code (forward-compat, coût nul)
   - Documenter clairement dans le commentaire que ça active dès que le prompt grossit
   - Coût input reste 5-10× plus haut tant que le cache n'est pas actif, mais sur 200 tickets/alpha à $1/MTok input ça fait $0.30 négligeable

3. **Override `LLM_MODEL` vers Sonnet/Opus** :
   - Seuils potentiellement différents (à vérifier dans la doc Anthropic au cas par cas)
   - Trade-off : le cost per token de Sonnet/Opus est >> Haiku ; activer le cache compense sur les répétitions mais ne suffit pas forcément à rester moins cher au total
   - Approche réservée aux cas où la qualité Haiku ne suffit pas

**Action :** PR #128 (post-fixtures réelles capturées via `/admin/scans/<id>/debug`) — option 1 + faire les few-shot à partir de tickets alpha réels. Pas avant : itérer sur fixtures réelles donne 10× plus de ROI que spéculer.

---

## KP-32 — OTA channel mismatch silent no-op (4h debug wasted)

**Symptôme :** `eas update --channel <X>` réussit, retourne un Update Group ID, EAS dashboard montre l'update publié. **Mais** côté tel utilisateur force-stop ×2 ne change rien : aucune nouvelle version active, aucun event Sentry, aucun comportement nouveau. Hours wasted à chercher un bug introuvable.

**Cause :** L'APK installé sur le tel écoute un channel SPÉCIFIQUE (défini au build EAS). Les builds `eas build --profile preview` produisent des APKs sur channel `preview`. Les builds `--profile production` produisent des AAB sur channel `production`. Pousser un OTA sur **un channel que l'APK n'écoute pas** = silent no-op (pas d'erreur, pas de warning).

**Conditions de répro :**
- Alpha test : utilisateur side-load un APK preview (`eas build --profile preview`)
- Dev push OTA via `eas update --channel production` (par habitude / autopilot)
- → APK reçoit jamais l'OTA. Tout fonctionne "comme avant" pour l'utilisateur.

**Détection :**
```bash
eas build:list --platform=android --limit=1
# Lire `Channel: <X>` dans la sortie. C'est CE channel qu'il faut cibler dans `eas update`.
```

OU dans l'app (post-PR #138/#139) : visible dans le badge `OTA build #N` du tab Profil. Si le N ne change pas après force-stop ×2, l'OTA n'a pas été appliqué.

**Découverte :** 2026-04-27 alpha — 4 OTAs poussés sur `production` channel pendant que l'APK alpha utilisateur était sur `preview`. Aucun fix d'aujourd'hui n'a touché le tel jusqu'à ce qu'on switch.

**Mitigation :**
1. **R34 (CLAUDE.md)** mis à jour : "Channel must match installed APK — verify via `eas build:list --limit 1` and read `Channel:` field BEFORE `eas update --channel <X>`".
2. **`./ota-push.sh`** (PR #143) inclut maintenant un guard automatique : check le channel du dernier APK et abort si mismatch.
3. **Badge `OTA build #N`** visible dans Profil (PR #139) : check visuel post-OTA + post-force-stop ×2.

**Action :** rule R34 active. Plus jamais cette erreur si `./ota-push.sh` est utilisé.

---

## KP-33 — alembic_version divergence prod vs migration files (manual SQL drift)

**Symptôme :** Une migration alembic existe dans `/alembic/versions/`, dit-elle de faire X. Mais en prod, l'opérateur a appliqué Y manuellement via psql (par exemple parce qu'alembic n'était pas encore installé dans le container). `alembic_version` table dit qu'on est à `<revision>`, mais l'état schéma réel ne match pas le contenu de cette migration.

**Conséquence :** Quand alembic est ENFIN déployé en prod (cas vécu 2026-04-27), `alembic upgrade head` from-current-version applique les migrations suivantes en partant d'une **fausse hypothèse** sur l'état du schema. Si la migration suivante fait `op.alter_column(...)` ou `op.create_index(...)` en supposant la shape produite par la migration précédente, elle peut échouer (constraint already exists, column has different type, etc.).

**Conditions de répro :**
- Bulk import OSM nécessite drop d'indexes en urgence
- Indexes droppés via `psql DROP INDEX` (rapide, pas de PR)
- Migration officielle écrite plus tard pour les recréer
- En prod, indexes sont droppés. La migration "recreate" est marquée appliquée dans alembic_version SANS avoir tourné.
- Plus tard, on déploie alembic en prod. Alembic démarre depuis une revision qui ne match pas la réalité.

**Détection :**
```sql
-- Compare ce que alembic dit vs ce qui existe vraiment :
SELECT version_num FROM alembic_version;
SELECT indexname, indexdef FROM pg_indexes WHERE tablename = 'stores';
```

**Mitigation :**
- **Pattern Option B (utilisé 2026-04-27 PR #141)** : écrire une migration de réconciliation idempotente (`<timestamp>_partial_indexes_policy.py`) qui DROP les indexes potentiellement dans la mauvaise shape et RECRÉE dans la bonne shape. Idempotente partout :
  - Sur prod (déjà manuel) : DROP+CREATE sur shape déjà correcte = no-op effectif
  - Sur dev/CI from-scratch : applique correctement la shape voulue
- Cette migration s'ajoute APRÈS la migration originale dans la chain alembic.
- L'avantage : pas de history rewriting (la migration originale reste merged), mais la chain converge vers le bon état.

**Trigger d'application :** uniquement après que tous les services soient déployés avec le code qui consomme la nouvelle shape (ex. `upsert_store` qui sait gérer multi-conflict avant qu'on recrée les unique indexes).

**Découverte :** 2026-04-27 — 3 unique indexes droppés à la mano pour bulk import OSM, puis service migrations + migration de réconciliation déployés en fin de journée.

**Action :** quand tu fais une intervention DB manuelle en prod, ouvre immédiatement une PR avec une migration de réconciliation idempotente. Ne pas attendre.

---

## KP-34 — OTA bundle ships JS that calls a native module not present in installed APK

**Symptôme :** Crash app sur écran scan/camera après application d'un OTA :
```
Error: Cannot find native module 'ExpoImageManipulator'
TypeError: Cannot read property 'ErrorBoundary' of undefined
```

L'utilisateur ne peut plus utiliser la caméra. AppCrashScreen apparaît.

**Cause :** Un OTA peut shippr du nouveau JS, MAIS PAS du code natif. Si le bundle JS appelle un module natif qui n'a pas été compilé dans l'APK installé (parce que la dep `expo-*` a été ajoutée au `package.json` après le dernier `eas build`), l'OTA importe un module qui n'existe pas → crash.

**Conditions de répro typiques :**
- APK alpha buildé semaine dernière au commit X
- PR mergée cette semaine ajoute `expo-image-manipulator` au `package.json`
- OTA poussé : nouveau JS importe `manipulateAsync`
- APK installé n'a jamais compilé le module natif → crash au runtime de la fonction qui l'appelle

**Détection :**
- Sentry voit le crash :
  - `Error: Cannot find native module 'ExpoImageManipulator'` (ou autre)
  - Souvent accompagné de `TypeError: Cannot read property 'ErrorBoundary' of undefined` (cascade depuis le crash initial)
- Side-effects observables : tab scan crashe systématiquement après force-stop ×2

**Mitigation immédiate (rollback OTA) :**
```bash
eas update:roll-back-to-embedded --channel <X> --runtime-version 1.0.0
```
- Cette commande publie un "rollback marker" sur le channel.
- Force-stop ×2 utilisateur → l'app revient au bundle EMBEDDED (= celui compilé dans l'APK), qui a forcément les bons modules natifs.
- Caméra refonctionne immédiatement (mais sans les fixes JS de l'OTA).

**Fix propre (rebuild APK) :**
- Lancer un nouveau `eas build --profile <X>` qui inclut TOUS les modules natifs courants.
- Distribuer le nouvel APK aux alpha testeurs.
- Re-poussé l'OTA quand l'APK est installé.

**Prévention :** dans toute PR qui ajoute une dep `expo-*` au `package.json`, flag explicitement OTA-incompatible dans le body de la PR. Le script `./ota-push.sh` (PR #143) ne détecte PAS ça automatiquement actuellement — à durcir dans une PR follow-up (parser le diff `package.json` depuis le dernier EAS build).

**Découverte :** 2026-04-27 alpha — `expo-image-manipulator` ajouté en PR #118 (AF-12 EXIF flatten), puis OTAs poussés assumant que le module natif existait. APK alpha utilisateur du 26/04 antérieur à cette PR → crash.

**Action :** rebuild EAS preview lancé 2026-04-27 17:50 UTC pour matcher tous les `package.json` deps actuels.

---

## KP-35 — SA dispatch parallèle dans même worktree → file clash

**Symptôme :** Deux SAs dispatch en parallèle, chacun touche les mêmes fichiers, l'un overwrite ou discard le travail de l'autre. Le SA qui finit en second peut signaler "j'ai discarded des uncommitted edits qui n'étaient pas à moi". Travail perdu silencieusement.

**Cause :** Les SAs Claude Code partagent le même working directory par défaut quand dispatched en background. Ils opèrent tous sur la même copie du repo. Si plusieurs SAs travaillent en parallèle :
- SA-A crée la branche `feat/X`, fait des edits
- SA-B crée la branche `feat/Y` (différente), fait `git checkout` → écrase potentiellement les fichiers de SA-A si SA-A n'a pas committé
- Le `git checkout` ou `git reset` qu'un SA peut faire pour aligner son worktree écrase le travail d'autres SAs en cours

**Détection :** dans le rapport final du SA, ligne du genre :
> "I had to stash my files, switch branches, and discard those uncommitted edits via git checkout (they were never committed and not in any stash, so they're gone from the working tree)"

**Mitigation :** Toujours utiliser `isolation: "worktree"` quand on dispatch ≥2 SAs dev en parallèle :

```python
Agent({
  ...
  "isolation": "worktree",  # SA bosse dans un worktree git séparé
})
```

Avec `isolation: "worktree"`, le harness Claude Code crée un git worktree dédié (path : `.claude/worktrees/agent-<id>/`) avant de dispatcher le SA. Les opérations git se font dans cet espace isolé. Pas de collision possible avec d'autres SAs ou avec l'orchestrator.

Le worktree est nettoyé automatiquement si le SA ne fait aucune modification. Sinon il survit (pour qu'on puisse récupérer les artifacts du SA).

**Découverte :** 2026-04-27 — SA `a2d12368` (alembic en prod) et SA `ad410eef` (phone refactor) dispatched en parallèle dans même worktree. Le second a discardé les edits non-commités du premier. Heureusement le second a poussé son PR séparément avec ses changes, mais la fenêtre de race était dangereuse.

**Action :** R30 (ORCHESTRATOR.md) à compléter avec règle "≥2 SAs dev en parallèle = isolation: worktree obligatoire".

---

## KP-36 — Self-hosted runners DinD don't share docker networks with host daemon

**Symptôme :** Un workflow GitHub Actions sur runner self-hosted essaie de `docker run --network <X>` où `<X>` est un network créé par un autre `docker compose` stack sur le host. Erreur :
```
docker: Error response from daemon: failed to set up container networking: network <X> not found
```

**Cause :** Les runners self-hosted Ratis utilisent Docker-in-Docker (DinD) pour isoler les conteneurs des jobs CI du Docker daemon principal. Le runner container parle à un daemon DinD séparé (`DOCKER_HOST=tcp://dind:2375`), pas au daemon du host.

→ `docker network ls` exécuté DANS un step liste les networks du daemon DinD, pas du host.
→ Les networks créés par `runner/docker-compose.yml` (project-default `runner_default`) ou par le stack prod (`ratis_ratis_net`) sont sur le host daemon, invisibles depuis DinD.

**Détection :**
```yaml
- run: docker run --rm --network runner_default ...
# fail "network not found" sur runner self-hosted DinD
```

**Mitigation (utilisée 2026-04-27 PR #145) :** créer un network éphémère par job dans DinD :

```yaml
- name: Create ephemeral network
  run: docker network create ratis_ci_${{ github.run_id }}_${{ github.run_attempt }}

- name: Start ephemeral postgres
  run: |
    docker run -d --name pg_ci \
      --network ratis_ci_${{ github.run_id }}_${{ github.run_attempt }} \
      --network-alias postgres \
      -e POSTGRES_USER=ratis -e POSTGRES_PASSWORD=ratis \
      postgres:16

- name: Run migrations
  run: |
    docker run --rm \
      --network ratis_ci_${{ github.run_id }}_${{ github.run_attempt }} \
      -e DATABASE_URL=postgresql+psycopg://ratis:ratis@postgres:5432/db \  # pragma: allowlist secret
      ratis-migrations:ci

- name: Teardown
  if: always()
  run: |
    docker rm -f pg_ci || true
    docker network rm ratis_ci_${{ github.run_id }}_${{ github.run_attempt }} || true
```

Naming `<run_id>_<run_attempt>` pour éviter les collisions entre jobs concurrents.

Le `services:` block GitHub Actions ne marche pas sur DinD car GA expose les ports sur le host de la GA-runner-machine, pas sur DinD. Le container DinD-internal n'a pas accès à ces ports via `localhost`.

**Découverte :** 2026-04-27 PR #145 — fix `ratis_migrations` workflow qui crashait sur `runner_default` introuvable.

**Note pour V1+** : Si on bouge à des runners `runs-on: ubuntu-latest` (GitHub-hosted), le `services:` block redevient la solution préférée. Le pattern éphémère reste compatible mais devient overkill.

**Action :** appliquer ce pattern à tout futur workflow qui démarre des containers liés (ex. tests d'intégration). Dans le doute → pattern éphémère.

---

## KP-37 — `re.IGNORECASE` ne fold PAS les accents en char class

**Symptôme :** Un regex Python avec `re.IGNORECASE` matche bien `m`/`M` mais PAS `é`/`É` quand le pattern utilise un character class type `[éE]`. Exemple concret SA #177 : pattern voulait matcher `TEL`/`TÉL`/`Tél` mais seul le caractère explicite dans la class était matché côté accent — IGNORECASE ne sauvait rien.

**Cause :** `re.IGNORECASE` ne fait que la case-folding ASCII (a↔A, b↔B, ...). Pour les caractères accentués (é/É, è/È, à/À, etc.), il faut soit :
- Énumérer explicitement les 2 casses dans la char class : `[eEéÉèÈ]`
- Pré-normaliser le texte avec `unicodedata.normalize('NFD', text)` puis stripper les accents avant le regex

**Solution adoptée (PR #177) :** énumération explicite + tolérance OCR.
```python
# ✅ Correct — char class étendue + caractères OCR-confondus
_PHONE_PREFIX_OCR_RE = re.compile(r"T[EÉ3][LIl]")
# Couvre : E déformé en 3, L déformé en I/l, casse + accent FR explicite.
```

**Prévention :** pour tout regex sur du texte FR (OCR ou input user), 2 réflexes :
1. Char class contient un caractère accentué → énumérer explicitement les 2 casses (`é` ET `É`).
2. Texte vient d'OCR → préférer une normalisation NFD + strip accents en amont du match.

**Mots-clés :** re.IGNORECASE, char class, accents, é vs É, OCR FR, store_detector, phone, regex Python, NFD normalize, fuzzy match

**Découverte :** 2026-04-28 PR #177 (store_detector phone OCR-tolerant prefix detection).

---

## KP-38 — Worktree FE Expo nécessite `npm install` (~2 min) au premier run

**Symptôme :** SA dispatché sur un worktree pour bosser dans `ratis_client/` (frontend Expo) — quand il lance `npx jest` ou `npm run lint`, échec immédiat `Cannot find module 'X'`. Cause : `node_modules/` n'existe pas dans le worktree (gitignored).

**Cause :** git worktree partage le `.git` du main checkout mais **pas** les fichiers gitignored. `node_modules/` (~800 MB, ~30 000 fichiers) n'est pas symlinké automatiquement → chaque worktree FE doit faire son propre `npm install`.

**Solution :** à chaque création de worktree FE, prévoir 2 min d'install au premier passage. Brief explicite dans la mission SA pour qu'il anticipe ce step et ne le confonde pas avec un bug.

```bash
# Dans le worktree FE, AVANT toute commande jest/tsc/lint :
cd ratis_client && npm install   # ~2 min sur Windows
```

**Prévention :**
- Brief SA FE : ajouter dans le workflow attendu "0. Si worktree fresh, run `cd ratis_client && npm install` (~2 min)".
- Alternative future : symlink `.worktrees/<n>/ratis_client/node_modules` → `<main>/ratis_client/node_modules` au moment de la création du worktree (script helper). Risque : drift entre branches qui ajoutent/retirent des deps.
- Pas applicable au backend Python car `uv` gère un cache global (`~/.cache/uv`) et `uv run` résout depuis le workspace `pyproject.toml` — chaque worktree Python est utilisable immédiatement.

**Mots-clés :** worktree, ratis_client, Expo, npm install, node_modules, gitignored, jest, frontend, SA dispatch, performance setup, FE-only

**Découverte :** 2026-04-29 — SA FE PR #178 dispatch dans worktree fresh, premier run `jest` a échoué sur module manquant ; install explicite ajouté au workflow.

---

## KP-39 — Celery worker `sys.path` quirk : module-load storage import

**Symptôme :** Worker Celery (PA scan tasks) crash au boot avec `ModuleNotFoundError: No module named 'storage'` (ou autre module local du package). Le service FastAPI tourne sans souci avec exactement le même code et le même venv.

**Cause :** Celery worker, lancé via `celery -A app worker`, n'ajoute PAS le répertoire courant au `sys.path` de la même façon que le serveur FastAPI ASGI (qui passe par uvicorn → l'import root est résolu différemment via le `module:app` qu'on lui donne). Quand le code importe un module relatif (ex: `from storage import upload`), Celery échoue parce que son root n'est pas le service dir mais le cwd du process supervisor.

**Détection :**
```
Traceback (most recent call last):
  File ".../celery/apps/worker.py", line ...
  ...
  File ".../webservices/ratis_product_analyser/worker/receipt_task.py", line N
    from storage import upload_to_r2
ModuleNotFoundError: No module named 'storage'
```

**Solution (PR #152) :** import absolu OU configuration `sys.path` explicite au top du module Celery.
```python
# ✅ Correct — import absolu, fonctionne sous FastAPI ET Celery
from webservices.ratis_product_analyser.storage import upload_to_r2
```

**Prévention :**
- Tous les imports dans le code partagé entre route + Celery task doivent être **absolus** (`from webservices.ratis_product_analyser.storage import upload`).
- Si un import absolu n'est pas possible (ex. dans un package interne), patch `sys.path` au top du module Celery avec un commentaire clair.
- Tester systématiquement le worker en local via `docker compose up -d ratis_product_analyser_worker` (pas juste le service FastAPI) avant push — c'est la seule façon de catcher ce piège pré-CI.

**Mots-clés :** Celery worker, sys.path, ModuleNotFoundError, storage, import relatif, PR #152, FastAPI vs Celery, worker boot, lazy import, ratis_product_analyser

**Découverte :** 2026-04 — PR #152 fix `module-load storage import in workers`.

---

## KP-40 — Pytest Windows + bash MINGW = stdout buffering perdu si kill avant flush

**Symptôme :** Sur Windows, `uv run --package X pytest ...` lancé via Bash MINGW (Claude Code shell) affiche **0 byte d'output** pendant plusieurs minutes, puis termine en exit code 0 (ou autre). Si on `taskkill` le process avant qu'il flush son buffer, le fichier d'output reste vide → impossible de savoir si tests passed/failed.

**Cause :** double buffering :
1. **Pytest stdout** est line-buffered en TTY mais block-buffered en pipe (le redirect `>` ou capture par la harness Claude Code). Sans flush explicite, l'output reste en RAM jusqu'à la fin OU jusqu'à ce qu'un buffer de 4-64 KB soit plein.
2. **Bash MINGW Windows** utilise des handles Windows pour les redirections → le flush ne se fait pas en temps réel comme sur Linux/macOS. Les bytes écrits par le process enfant ne deviennent visibles qu'après un flush côté process OU à la fermeture du handle.

**Conséquence pratique :** SA voit "rien depuis 3 min", pense que pytest hang, kill avec `taskkill //F`, le buffer est perdu → l'erreur de test n'est jamais affichée.

**Solution adoptée (2026-04-29) :**
1. Ajouter `-s` (no-capture) à pytest pour bypass son propre buffering.
2. Si quand même hang >2 min sans output : trust CI Linux Docker (R15) — `taskkill //F //PID <X>` puis push, CI vérifie en clean.
3. Pour debugging local : `uv run --package X pytest ... --capture=no -p no:cacheprovider` + `set PYTHONUNBUFFERED=1`.
4. Préférer l'outil **PowerShell** au Bash sur Windows pour les pytest runs (voir SA_DEV.md § tests).

**Prévention :**
- Brief SAs : si pytest local hang sur Windows, kill et trust CI (root cause = buffering, pas bug réel — sauf si reproduit en CI).
- Roadmap Mac mini (J+6) : ce piège disparaît côté Mac (Bash natif Unix, line-buffered standard).

**Mots-clés :** pytest, Windows, bash MINGW, stdout buffer, hang, output empty, taskkill, exit code 0, R15, CI Linux Docker ground truth, PYTHONUNBUFFERED, --capture=no, -s, PowerShell

**Découverte :** 2026-04-28/29 — investigation Explore SA + restoration pytest local PR #174.

---

## KP-41 — `handle_barcode_rescan` race condition (concurrent uploads same ticket)

**Symptôme :** 2 uploads quasi-simultanés du même ticket physique (queue client qui drain trop vite, double-tap user, retry async) → l'un des deux échoue avec :
```
IntegrityError: duplicate key value violates unique constraint "receipts_receipt_barcode_key"
```
Le user voit un 500 cryptique côté client, son upload est perdu dans la queue (status orphelin).

**Cause :** `webservices/ratis_product_analyser/repositories/scan_repository.py::handle_barcode_rescan` utilise `select(Receipt).with_for_update()` — il lock l'ancien receipt **s'il existe**. Mais si **aucun receipt précédent n'a ce barcode** (cas 2 fresh uploads concurrents), il n'y a rien à lock → les 2 transactions partent en parallèle :

```
T1 : SELECT old WHERE barcode='ABC' → None → INSERT new (barcode='ABC') → COMMIT OK
T2 : SELECT old WHERE barcode='ABC' → None (T1 pas encore commit visible)
     → INSERT new (barcode='ABC') → COMMIT ❌ UniqueViolation
```

**Conditions de répro :** 2 uploads avec même `receipt_barcode` arrivant à <1s d'intervalle dans Celery (pas même worker process). Probabilité × en alpha (5-10 users), monte vite avec scale.

**Solution proposée (mini-PR post-deadline) :** `pg_advisory_xact_lock(hashtext(barcode))` au début du `receipt_task` → sérialise les uploads avec le même barcode au niveau process. Quasi-zéro overhead (advisory locks PG sont memory-only, libérés en fin de transaction).

```python
# Au début du receipt_task, avant toute SELECT/INSERT touchant le barcode :
db.execute(text("SELECT pg_advisory_xact_lock(hashtext(:b))"), {"b": barcode})
# Suite normale du flow (handle_barcode_rescan etc.)
```

**Alternatives :**
- B : `INSERT ... ON CONFLICT (receipt_barcode) DO NOTHING` + retry idempotent — plus de complexité applicative, mais évite le lock.
- C : déduplication Celery par barcode hash dans la queue (single-flight task per barcode) — change l'archi, scope plus large.

**Prévention :** tester systématiquement les endpoints qui mutent une UNIQUE-constraint avec **2 requêtes concurrentes** (`asyncio.gather`, `pytest-asyncio`) avant prod. Pattern reproducible à créer dans `tests/test_concurrent_scan.py`.

**Status :** bug latent connu, pas critique en alpha (peu de probabilité de trigger), à fix avant beta ouverte. Tracking dans le todo list orchestrator.

**Mots-clés :** handle_barcode_rescan, race condition, IntegrityError, receipts.receipt_barcode unique, concurrent scans, advisory lock, pg_advisory_xact_lock, scan_repository, alpha, mini-PR

**Découverte :** 2026-04-29 — analyse code post-PR #173 pendant audit reconnaissance Phase 2h.

---

## KP-42 — Backfill migration `UPDATE stores SET validation_status='pending'` peut casser stores manuellement validés admin

**Symptôme :** la migration de PR-B (ARCH_store_validation) inclut un backfill `UPDATE stores SET validation_status='pending' WHERE source='user_suggested'`. **En prod**, si certains stores `source='user_suggested'` ont été **manuellement validés par admin** (ex: store ajouté en alpha, validé à la main, cashback déjà payé), les flipper en pending **casse leur cashback en cours** — receipt.store_status='confirmed' mais store.validation_status='pending' → le double-check gating les bloque rétroactivement.

**Cause :** la migration ne distingue pas un store user_suggested **non validé** d'un store user_suggested **validé manuellement**. Avant PR-B il n'y avait pas de moyen de tracker la validation manuelle (pas de champ dédié, pas de log d'admin).

**Conditions de répro :**
- Alpha : 1-2 stores user_suggested ajoutés à la mano + validés admin pour débloquer un user.
- Migration PR-B appliquée sans audit pré-migration.
- Cashback déjà crédité sur ces receipts → flip pending → re-gating au prochain re-process.

**Solution V1 alpha :** pas de prod réelle au moment de la migration, le backfill est inoffensif. **Acceptable en alpha** parce qu'aucun cashback réel n'a été distribué.

**Solution V2 (avant beta) :**
- Avant la migration, audit des stores `user_suggested` existants : flag manuel ceux validés admin (via `ratis_settings` ou champ `manually_validated_by_admin`).
- Migration conditionnelle :
  ```sql
  UPDATE stores
  SET validation_status='pending'
  WHERE source='user_suggested'
    AND (manually_validated_by_admin IS NULL OR manually_validated_by_admin = false);
  ```
- Alternative : process post-migration manuel pour reflipper en confirmed les stores connus comme valides (script idempotent).

**Prévention :** avant TOUT backfill migration en prod, audit pre-migration sur l'état réel + dry-run + plan de rollback documenté. Lié à KP-33 (alembic_version drift) — toute manip prod manuelle doit être documentée.

**Mots-clés :** migration Alembic, backfill, stores.validation_status, user_suggested, admin manual validate, cashback gating, ARCH_store_validation Pitfall P-2, PR-B, prod safety, idempotent

**Découverte :** 2026-04-29 — review brainstorming ARCH_store_validation (PR-B), pitfall surface avant le merge.

---

## KP-43 — `pytest-timeout` config silencieusement ignorée si plugin pas installé

**Symptôme :** tests CI hangent indéfiniment malgré `[tool.pytest.ini_options] timeout = 60` dans le pyproject.toml du service. Pytest accepte les options inconnues sans warning bloquant, donc rien ne signale que le timeout est inactif. PR #233 a hung 85 min sur self-hosted runner avant cancel manuel ; PR #234 a hung 8 min puis cancel. Cycle observé plusieurs fois avant diagnostic.

**Cause :** dans une `uv` workspace, `uv sync --group dev` exécuté depuis un sous-package n'installe **que** le dev-group de ce sous-package. Le `pytest-timeout` déclaré dans le `dev` group de la **racine** `pyproject.toml` n'est PAS propagé. Si le sous-package configure `timeout = 60` mais ne déclare pas `pytest-timeout` dans son propre `[dependency-groups].dev`, le plugin n'est pas dans le venv → pytest ignore silencieusement l'option (les tests tournent sans garde-fou).

**Conditions de répro :**
- `webservices/<svc>/pyproject.toml` contient `[tool.pytest.ini_options] timeout = 60` mais pas `pytest-timeout` dans `[dependency-groups].dev`.
- CI fait `uv sync --group dev` depuis `working-directory: webservices/<svc>` (cas standard, voir `.github/workflows/ratis_<svc>.yml`).
- Un test introduit un deadlock (lock SQL, await infini, requête réseau bloquante sans timeout) → pytest tourne jusqu'au cancel manuel.

**Détection :** vérifier que chaque service avec `timeout = N` dans `[tool.pytest.ini_options]` déclare aussi `pytest-timeout` dans `[dependency-groups].dev` du **même** pyproject.toml. `uv run --package <pkg> pytest --help | grep -- --timeout=` doit afficher l'option (sinon plugin absent du venv).

**Solution propre :** `cd <package_dir> && uv add --dev pytest-timeout` par package (uv édite le bon pyproject + le lock root). Ne **pas** s'appuyer sur le dev-group de la racine — chaque service uv est autonome dans son install CI.

**Sanity probe (recommandée par service avec timeout config) :** ajouter un test trivial `def test_pytest_timeout_plugin_is_loaded(): import pytest_timeout` qui détectera toute future régression de la dépendance.

**Mots-clés :** pytest-timeout, pyproject, dependency-groups, uv workspace, uv sync --group dev, timeout silently ignored, hang CI, self-hosted runner, PR #233, PR #234, sanity probe

**Découverte :** 2026-05-01 — diagnostic récurrent suite à PR #233 (85 min hang) + PR #234 (8 min hang).

---

## KP-44 — Drift modèle/migration : `Mapped[datetime]` → `TIMESTAMP WITHOUT TIME ZONE` vs migration `TIMESTAMPTZ`

**Symptôme :** une route compare `datetime.now(timezone.utc)` (aware) à une colonne datetime lue via SQLAlchemy → `TypeError: can't compare offset-naive and offset-aware datetimes` en test, ou divergence silencieuse de comportement entre prod (TZ-aware) et tests (`create_all` produit des colonnes naïves). Symptôme corollaire dans Bloc B admin : on a dû normaliser aware/naïve à la main dans les routes `confirm-2fa` et `cancel-pending` pour que les tests passent.

**Cause :** la shorthand SQLAlchemy 2.0 `Mapped[datetime] = mapped_column(...)` (sans type explicite) résout par défaut sur `TIMESTAMP` **sans** timezone, indépendamment du driver. Quand la migration Alembic crée `sa.TIMESTAMP(timezone=True)`, le schéma effectif en prod est `TIMESTAMPTZ` — mais `Base.metadata.create_all()` (utilisé par les tests) reproduit le **modèle**, pas la migration. Drift invisible : les tests passent en naïve, la prod tourne en aware, code de réconciliation forcé dans les routes.

**Détection :**
```sql
SELECT column_name, data_type
FROM information_schema.columns
WHERE table_name = 'admin_settings_audit'
  AND column_name IN ('timestamp', 'expires_at', 'applied_at');
-- Doit retourner 'timestamp with time zone' partout.
```
Pattern test recommandé : assertion `inspect(Model).columns[col].type.timezone is True` + lecture de `information_schema.columns` après `create_all()`.

**Solution propre :**
```python
from sqlalchemy import TIMESTAMP

timestamp: Mapped[datetime] = mapped_column(
    TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
)
expires_at: Mapped[Optional[datetime]] = mapped_column(
    TIMESTAMP(timezone=True), nullable=True
)
```
Ne **jamais** se reposer sur la shorthand `Mapped[datetime]` quand la colonne représente un instant temporel — toujours déclarer `TIMESTAMP(timezone=True)` explicit. La règle vaut pour toute future colonne datetime (création, modification, expiration, application…).

**Prévention :** ajouter au moins un test schema-shape par table avec colonnes datetime (cf. `ratis_core/tests/test_models_admin_audit.py`). Lié à KP-10 (drift NUMERIC/Integer même pattern, autre type).

**Mots-clés :** TIMESTAMP, TIMESTAMPTZ, timezone-aware, timezone-naive, Mapped[datetime], create_all, drift modèle migration, admin_audit, expires_at, applied_at, aware/naive comparison, Bloc B admin, PR #258

**Découverte :** 2026-05-02 — Bloc B admin signal lors de l'implémentation `confirm-2fa` / `cancel-pending`. Fix appliqué post V1 NRC.

---

## KP-45 — Alembic multi-heads après PRs parallèles branchées sur le même parent

**Symptôme :** plusieurs PRs développées en parallèle ajoutent chacune une migration Alembic ; chacune utilise comme `down_revision` la même tête connue avant le démarrage. Après merge des deux PRs, `alembic upgrade head` échoue avec `Multiple head revisions are present for given argument 'head'`. CI bloquée jusqu'à création d'une revision de merge.

**Cause :** `alembic revision --autogenerate` lit la tête courante au moment où le SA travaille — il n'a pas connaissance des autres branches non-mergées. Chaque PR croit être linéaire, mais après merge le DAG comporte deux feuilles. Alembic refuse `upgrade head` parce qu'il ne sait pas laquelle suivre.

**Détection :** `alembic heads` retourne plus d'une ligne après merge. CI `ratis_migrations` job échoue au step `alembic upgrade head`.

**Solution propre (post-merge) :**
```bash
alembic merge -m "merge <topic-A> + <topic-B> heads" <head_A> <head_B>
git add alembic/versions/<merge_rev>.py
git commit -m "chore(alembic): merge heads after parallel PRs"
```
La merge-revision a deux `down_revision` (tuple) et n'opère aucune mutation schéma — simple jonction du DAG.

**Prévention orchestrator :** sérialiser les PRs touchant `alembic/versions/` (un SA à la fois). Si parallélisation indispensable, prévoir un round de merge-revision **dans le brief** dès le départ. Détection préventive : `gh pr list --search "alembic in:files"` avant dispatch.

**Mots-clés :** alembic, multi heads, merge revision, parallel PRs, down_revision, DAG, upgrade head, CI fail, PR #259, PR #261, PR #263

**Découverte :** 2026-05-02 — session V1 NRC + admin + batch (17 PRs en parallèle).

---

## KP-46 — `alembic downgrade -1` sur merge revision : "FAILED: Ambiguous walk"

**Symptôme :** dans le workflow `ratis_migrations` (test_migration.sh ou step équivalent CI), `alembic downgrade -1` exécuté sur une merge revision (deux parents) échoue avec :
```
FAILED: Ambiguous walk from <merge_rev> to <parent>; multiple paths
```

**Cause :** `downgrade -1` ne sait pas quel parent emprunter quand la révision courante a plus d'un `down_revision`. Le DAG n'est pas un arbre, et Alembic refuse l'ambiguïté plutôt que de choisir arbitrairement (comportement correct mais piégeux pour les workflows automatisés qui assument linéarité).

**Détection :** sortie `FAILED: Ambiguous walk` dans les logs CI immédiatement après `alembic downgrade -1`.

**Solution propre (workflow) :** patcher `test_migration.sh` (et workflows équivalents) pour gérer les merge revisions : détecter via `alembic show <current>` si la révision courante a plusieurs parents (parser la ligne `Parent:`), et skip le step `downgrade -1` dans ce cas — ou descendre explicitement vers un parent nommé. **Ne pas** se reposer sur un parsing fragile de la sortie `FAILED: Ambiguous walk` (try/catch sur exit code + grep texte non portable aux versions Alembic futures). Détection structurée via `alembic show` reste portable.

**Alternative :** `alembic downgrade <specific_parent>` explicite si on a besoin de descendre vraiment d'un cran depuis la merge.

**Mots-clés :** alembic, ambiguous walk, downgrade -1, merge revision, multiple parents, test_migration.sh, CI workflow, ratis_migrations, PR #263

**Découverte :** 2026-05-02 — PR #263 merge revision a fait sauter le step downgrade dans `ratis_migrations` workflow.

---

## KP-47 — `assert_no_pending_changes` faux positif : `db.add(); db.commit()` sans flush explicite

**Symptôme :** un test setup ajoute une row via `db.add(obj)` puis `db.commit()`. Le commit déclenche un flush implicite, mais la fixture `assert_no_pending_changes` (autouse) lit `db.new`/`db.dirty`/`db.deleted` à un moment où le set des `_writes` semble déjà vidé — résultat : la fixture ne déclenche pas, l'INSERT passe, mais des assertions ultérieures qui dépendent de l'ordre `add` → `flush` → `commit` se comportent étrangement (ID auto-généré pas encore disponible, ou row pas visible aux autres connexions dans le même SAVEPOINT).

**Cause :** SQLAlchemy `commit()` flush en interne avant de commit. Selon l'ordre des opérations dans la fixture vs le code de test, l'observation des `_writes` peut être trompeuse — ils ont été flushés (donc clearés du set) avant qu'on les inspecte. Le pattern `add(); commit()` "compresse" deux phases en une, masquant des bugs où le flush n'aurait jamais dû avoir lieu.

**Workaround / pattern propre :**
```python
# Pattern explicite — flush et commit séparés
db.add(obj)
db.flush()    # row visible, IDs assignés, _writes vidés explicitement
db.commit()   # COMMIT SQL
```
Ou pour les seeds purs (pas besoin d'ID au Python) :
```python
db.execute(
    text("INSERT INTO admin_settings_audit (...) VALUES (...)"),
    {...},
)
# Pas de session state à inspecter, pas de race avec assert_no_pending_changes
```

**Détection :** symptômes intermittents — un test passe mais la fixture rate à signaler une oubliée `commit()` ailleurs. Refactor des seeds vers `db.execute(text("INSERT ..."))` ou `add() + flush() + commit()` explicite si le test est sensible à l'ordre.

**Mots-clés :** assert_no_pending_changes, db.add, db.flush, db.commit, fixture, faux positif, _writes, INSERT direct, seeds, Bloc B admin, PR #258

**Découverte :** 2026-05-02 — Bloc B admin lors d'un seed audit row : surprise sur l'ordre `add()/commit()` qui ne triggerait pas la fixture autouse.

---

## KP-48 — `HTTPException(detail=dict)` produit un body `{"detail": {"detail": "..."}}` (FastAPI wrap)

**Symptôme :** une route raise `HTTPException(status_code=403, detail={"detail": "frozen_key_modified", "key": "trust_min"})`. Le client (mobile, curl) reçoit un body :
```json
{"detail": {"detail": "frozen_key_modified", "key": "trust_min"}}
```
Le code i18n (`body.detail`) n'est plus à la racine — l'UI affiche `Erreur inattendue` parce qu'elle lit `body["detail"]` (string attendu) et trouve un dict.

**Cause :** FastAPI wrap **systématiquement** le contenu de `HTTPException.detail` dans une clé `"detail"` au niveau réponse. Si `detail` est déjà un dict avec une clé `"detail"`, on obtient un nesting double. Ce comportement est documenté mais pas intuitif si on a l'habitude de la convention `detail="snake_code"` (string) du repo.

**Solution propre — deux options selon l'API contract :**

1. **String classique (préféré, R12 convention)** : `raise HTTPException(403, detail="frozen_key_modified")` — l'UI reste simple, body = `{"detail": "frozen_key_modified"}`.
2. **Si vraiment besoin de joindre des données structurées** :
   ```python
   raise HTTPException(403, detail={"code": "frozen_key_modified", "key": "trust_min"})
   ```
   Body devient `{"detail": {"code": "...", "key": "..."}}` — l'UI lit `body.detail.code`. **Ne jamais** mettre une clé `"detail"` à l'intérieur du dict (collision avec le wrap FastAPI).

**Détection :** test d'intégration qui lit la response body :
```python
assert response.json()["detail"] == "frozen_key_modified"  # string attendu
# OU
assert response.json()["detail"]["code"] == "frozen_key_modified"  # dict attendu
```
Si la convention repo est string + sub-clé optionnelle dans le body en plus, contrôler que la sub-clé n'est **pas** sous `"detail"`.

**Mots-clés :** HTTPException, detail, dict, nesting, FastAPI wrap, body, i18n, frozen_key_modified, R12, PR #258, PR #267

**Découverte :** 2026-05-02 — Bloc B admin (backend) + Bloc D admin UI (front) — front a unwrappé `body.detail.detail` pour récupérer la clé i18n, fix backend recommandé en V1.5.

---

## KP-49 — Native PG ENUM type — premier usage du repo : downgrade doit `DROP TYPE IF EXISTS`

**Symptôme :** la migration `20260502_1900_admauad` crée le type natif PG `admin_settings_audit_status` via `postgresql.ENUM(...).create()`. Si le downgrade ne drop pas le type, un re-up suivant échoue avec `type "admin_settings_audit_status" already exists`. Risque : recréation locale d'environnement test, alembic downgrade puis upgrade ne tourne pas idempotent.

**Cause :** convention historique du repo = `TEXT + CHECK constraint` pour les enums (ex : `cabecoin_transactions.reference_type`). La migration admin audit est la **première** à utiliser un ENUM PG natif. Le pattern n'était pas figé pour les downgrades — `op.drop_table()` ne drop pas le type natif (le type a une vie indépendante de la table).

**Solution propre (à appliquer dans tout downgrade qui crée un ENUM natif) :**
```python
def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ...")
    op.execute("DROP TABLE IF EXISTS admin_settings_audit")
    op.execute("DROP TYPE IF EXISTS admin_settings_audit_status")  # obligatoire
```

**Convention V1.5 (à acter en équipe) :** continuer le pattern `TEXT + CHECK` pour la majorité des cas (plus simple à étendre, pas de migration pour ajouter une valeur). N'utiliser `postgresql.ENUM` que si on a vraiment besoin du type-safety SQL niveau colonne (cas rare). Ratis_core code parlant à plusieurs services : préférer TEXT pour faciliter introspection et migration future.

**Détection :** test idempotence migration — `alembic upgrade head ; alembic downgrade base ; alembic upgrade head` doit passer sans erreur. Ajouter au workflow `ratis_migrations` si pas déjà.

**Mots-clés :** PostgreSQL, ENUM type, native, postgresql.ENUM, DROP TYPE IF EXISTS, downgrade, idempotent, admin_settings_audit_status, TEXT + CHECK, convention repo, PR #257

**Découverte :** 2026-05-02 — Bloc A admin (création table audit). Pattern à figer pour future migrations.

---

## KP-50 — `users_provider_check` fail au pg_dump local après seed admin row

**Symptôme :** la migration `20260501_2000_nrc_d_admin_user` insère une row admin dans `users` ; localement, après application puis `pg_dump` du schéma de test, le dump rejoue les INSERTs et trip la CHECK constraint `users_provider_check` — l'admin row utilise une valeur de `provider` non-conforme aux providers métier (ex : `'admin'` ou `NULL`).

**Cause :** drift sémantique entre la convention business du repo (`provider` = OAuth provider : `google`, `apple`) et le besoin opérationnel de seed un compte admin sans OAuth. Le CHECK constraint a été ajouté avant l'admin seed sans anticiper le cas. Pas un bug code en prod (le seed ne passe pas par le dump cycle), mais piège dev local : `pg_dump | psql restore` chaîne CI peut ne pas reproduire exactement.

**Détection locale :** `pg_dump --schema-only ratis_test > /tmp/schema.sql ; psql -f /tmp/schema.sql ratis_fresh` → erreur CHECK constraint si le dump incluait l'admin row.

**Solution V2 (à acter) :**
- **Option A** : étendre `users_provider_check` pour accepter `'admin'` ou `'system'` :
  ```sql
  ALTER TABLE users DROP CONSTRAINT users_provider_check;
  ALTER TABLE users ADD CONSTRAINT users_provider_check
    CHECK (provider IN ('google', 'apple', 'admin'));
  ```
- **Option B** (plus propre RGPD) : sortir l'admin user de la table `users` (compte non-public, pas de PII attendu) → table dédiée `admin_users` ou `service_accounts` avec ses propres règles.

**Workaround V1 :** ne pas inclure l'admin row dans les `pg_dump` exportés vers d'autres environnements (filter via `--exclude-table-data=users` pour les dumps schema-only).

**Mots-clés :** users_provider_check, CHECK constraint, admin seed, pg_dump, drift sémantique, provider, RGPD, admin_users, service accounts, PR #257

**Découverte :** 2026-05-02 — SA Bloc A NRC, signal au moment du test local pg_dump.

---

## KP-51 — FastAPI route ordering : literal `/x/audit` doit être déclarée AVANT `/x/{section}`

**Symptôme :** une requête `GET /admin/settings/audit` est routée vers le handler `get_section(section: str)` (le path-param) avec `section="audit"`, au lieu d'atteindre le handler dédié `list_audit()`. Réponse 404 `settings_section_not_found` parce que `app_settings` n'a pas de section nommée `"audit"`.

**Cause :** FastAPI évalue les routes **dans l'ordre de déclaration** dans le router. Quand un literal segment (`/audit`) coexiste avec un path-param (`/{section}`) au même niveau, le premier déclaré gagne. Si le path-param est déclaré en premier, il match tous les segments, y compris ceux qui auraient dû atteindre des routes literal.

**Solution propre — convention à figer :**
```python
# Order matters : literal first, path-param last.
@router.get("/admin/settings")           # exact root
@router.get("/admin/settings/audit")     # literal
@router.get("/admin/settings/audit/{audit_id}")
@router.get("/admin/settings/seed")      # literal
@router.get("/admin/settings/{section}/editable")  # specific path-param child
@router.get("/admin/settings/{section}")  # generic path-param ← LAST
```

**Pattern repo recommandé :**
1. Toutes les routes literal (`/audit`, `/seed`, `/health`, etc.) groupées en haut du router.
2. Routes path-param (`/{x}`) déclarées **après**.
3. Si une route literal est ajoutée plus tard, la déplacer au-dessus du path-param générique.

**Détection :** test d'intégration qui hit explicitement la route literal :
```python
def test_get_audit_does_not_match_section_path_param(client, admin_headers):
    resp = client.get("/admin/settings/audit", headers=admin_headers)
    assert resp.status_code == 200
    assert "items" in resp.json()  # not a section payload
```

**Mots-clés :** FastAPI, route ordering, literal vs path-param, /audit, /{section}, get_section, list_audit, 404 settings_section_not_found, PR #269

**Découverte :** 2026-05-02 — SA Bloc D admin UI, debug d'un 404 inexpliqué sur `/admin/settings/audit`.

---

## KP-52 — CI pytest deadlock flake : `setup_db` `DROP SCHEMA public CASCADE` lock concurrents

**Symptôme :** un job CI (rare, ~5-10 % des runs) hang ou échoue avec un deadlock lors du fixture `setup_db` ; symptômes : `psycopg.errors.DeadlockDetected` sur `DROP SCHEMA public CASCADE` ou wait infini sur lock advisory. `gh run rerun --failed` fix le problème → cause non-déterministe.

**Cause hypothétique :** deux jobs CI concurrents (ex : `ratis_rewards` et `ratis_auth` lancés sur la même PR) partagent la même DB Postgres self-hosted (`ratis_test` ou similaire selon la config). `DROP SCHEMA public CASCADE` prend un lock ACCESS EXCLUSIVE sur tout le schema ; si l'autre job est en plein run avec des connexions ouvertes, deadlock ou wait infini selon timing.

**Workaround V1 (utilisé en cours) :** `gh run rerun --failed <run-id>` au cas par cas. Acceptable tant que le rate < 10 % et que ça ne bloque pas le merge.

**Solution V2 (à investiguer si récurrence > 15 %) :**
- **Option A — DB par job** : `CREATE DATABASE ratis_test_<svc>_<run_id>` au début de chaque job, `DROP DATABASE` à la fin. Isolation totale mais coût création/destruction (~2-5 s par run).
- **Option B — Mutex job-level** : sérialiser les jobs CI qui touchent la même DB (`concurrency: { group: db-test, cancel-in-progress: false }` au niveau workflow). Simple à mettre en place, mais ralentit la CI globale.
- **Option C — Schema-per-job** : un schema dédié par run (`CREATE SCHEMA test_<run_id>`), `search_path` configuré au début. Compromis entre isolation et coût.

**Détection :** rate de flake monitoré via `gh run list --json conclusion,event` + filtre sur `setup_db` dans les logs failed. Si > 15 % sur 50 runs consécutifs → V2 obligatoire.

**Mots-clés :** CI pytest, deadlock, DROP SCHEMA public CASCADE, setup_db fixture, flake, gh run rerun, jobs concurrents, ratis_test partagé, V2 isolation per-job, PR #269, PR #270

**Découverte :** 2026-05-02 — SAs Bloc D admin UI + Bloc E NRC scan history, observation indépendante du même symptôme. Pas de pattern reproductible — flake intermittent.

---

## KP-53 — `healthchecks/healthchecks:latest` n'embarque ni `curl` ni `wget` (Docker healthcheck Phase A)

**Symptôme :** Container `healthchecks` reste `unhealthy` selon `docker compose ps` malgré l'app fonctionnelle (UI accessible). Le healthcheck Docker écrit avec `curl -f http://localhost:8000/...` échoue avec `OCI runtime exec failed: exec: "curl": executable file not found in $PATH`.

**Cause root :** L'image officielle `healthchecks/healthchecks` est minimale (Python + Django uniquement). Aucun outil HTTP en ligne de commande n'est installé.

**Solution propre :** Utiliser Python (toujours présent dans cette image Django) avec `urllib.request` pour le healthcheck Docker.

```yaml
# docker-compose.yml
healthchecks:
  image: healthchecks/healthchecks:latest
  healthcheck:
    test: ["CMD-SHELL", "python -c \"import urllib.request, sys; urllib.request.urlopen('http://localhost:8000/accounts/login/').read()\" || exit 1"]
    interval: 30s
    timeout: 10s
    retries: 3
```

**Pattern réutilisable :** Pour toute image minimaliste basée Python (Django, FastAPI, Flask), préférer `python -c "import urllib.request..."` à `curl/wget` dans les healthchecks. Si image Node : `node -e "..."`. Si image Go statiquement linkée : pas d'autre choix que d'embarquer un binaire dédié (ex: `ghcr.io/grpc-ecosystem/grpc-health-probe`).

**Détection :** `docker compose ps` colonne `STATUS` qui reste à `(health: starting)` ou `(unhealthy)` plus de 90s post-startup. `docker inspect <container> --format '{{.State.Health.Log}}'` montre l'erreur exacte.

**Mots-clés :** healthchecks self-hosted, Docker healthcheck, curl missing, wget missing, image minimale, Python urllib, infra/itops, Phase A

**Découverte :** 2026-05-04 — SA Phase A ITOps déploiement initial. Image officielle Healthchecks ne ship qu'avec Python.

---

## KP-54 — Healthchecks API namespace = `/api/v3/` (auth required), pas `/api/v1/`

**Symptôme :** Tentative de healthcheck Docker via `curl -f http://localhost:8000/api/v1/` (ou `/api/v1/checks/`) renvoie systématiquement HTTP 500 ou 404. Confusion avec le SaaS public healthchecks.io qui exposait historiquement `/api/v1/`.

**Cause root :** Les versions récentes de Healthchecks (auto-hosté ou SaaS) ont migré l'API vers `/api/v3/`, ET cette API requiert un token. Pour un healthcheck Docker non-authentifié, il faut pointer sur une route HTML toujours-200 unauthenticated.

**Solution propre :** Utiliser `/accounts/login/` (page de login Django, toujours 200 sans auth) pour le healthcheck Docker. Pour les vraies queries API (rate stats, list checks, etc.), utiliser `/api/v3/` + bearer token.

```yaml
healthcheck:
  test: ["CMD-SHELL", "python -c \"import urllib.request; urllib.request.urlopen('http://localhost:8000/accounts/login/').read()\" || exit 1"]
```

**Détection :** logs container `healthchecks-1` montrent des `404 Not Found` ou `Internal Server Error` répétés sur `/api/v1/...` au timing du healthcheck.

**Mots-clés :** healthchecks self-hosted, API v1 vs v3, /accounts/login, auth required, Django, healthcheck Docker, Phase A, infra/itops

**Découverte :** 2026-05-04 — SA Phase A ITOps initial. Brief de l'orchestrator suggérait `/api/v1/`, le SA a corrigé vers `/accounts/login/` après test.

---

## KP-55 — Healthchecks `EMAIL_PORT` doit être un `int` valide, même si `EMAIL_HOST` est vide

**Symptôme :** Container `healthchecks` exit immédiatement au boot avec `ValueError: invalid literal for int() with base 10: ''` ou similar dans les logs. App ne démarre jamais.

**Cause root :** Healthchecks utilise `os.environ.get("EMAIL_PORT")` puis convertit via `envint()` qui crash sur empty string. Même si `EMAIL_HOST` est vide (= pas d'envoi d'email actif en V0), `EMAIL_PORT` doit être un entier valide. La validation ne se fait pas conditionnellement.

**Solution propre :** Toujours définir `EMAIL_PORT=587` (ou 25) dans `.env` avec une valeur intentionnelle, même si SMTP n'est pas câblé. C'est inert tant que `EMAIL_HOST` est vide, donc safe.

```env
# .env
EMAIL_HOST=        # vide = pas d'envoi V0
EMAIL_PORT=587     # OBLIGATOIRE int valide même si HOST vide
EMAIL_HOST_USER=
EMAIL_HOST_PASSWORD=
DEFAULT_FROM_EMAIL=healthchecks@example.com
```

**Pattern réutilisable :** Pour toute app Python qui lit des env vars typées (int, bool), ne JAMAIS laisser une var "vide" si le getter ne tolère pas — défini une valeur sentinelle inert.

**Détection :** logs `docker compose logs healthchecks` montrent un `ValueError` au tout début (avant que Django ne fasse son démarrage normal).

**Mots-clés :** healthchecks self-hosted, EMAIL_PORT, envint, ValueError, empty string, env var int, Django settings, .env.example, Phase A

**Découverte :** 2026-05-04 — SA Phase A ITOps. Détecté lors du premier `docker compose up` avec `.env` minimal.

---

## KP-56 — Watchtower client Docker API version 1.25 trop vieux pour Docker récent (Mac/Linux 1.40+)

**Symptôme :** Container `watchtower` boot mais émet immédiatement des warnings/erreurs : `client API 1.25 unsupported, server requires 1.40` (ou similar selon la version Docker hôte). Watchtower ne pull / ne restart aucun container.

**Cause root :** Watchtower (versions ~1.7.x) embarque un client Docker SDK qui négocie par défaut l'API version 1.25 (compatibilité historique). Les daemons Docker récents (Docker Desktop ≥ 4.27, Docker Engine ≥ 24.x) requirent au minimum API 1.40.

**Solution propre :** Pinner `DOCKER_API_VERSION` dans l'environnement Watchtower :

```yaml
# docker-compose.yml
watchtower:
  image: containrrr/watchtower:1.7
  environment:
    - DOCKER_API_VERSION=1.40
    - WATCHTOWER_LABEL_ENABLE=true
    - WATCHTOWER_CLEANUP=true
    - WATCHTOWER_SCHEDULE=0 0 4 * * *
  volumes:
    - /var/run/docker.sock:/var/run/docker.sock
```

**Détection :** `docker compose logs watchtower` au boot affiche les errors de négociation API. Aucune ligne `Found new <image> image` n'apparaît même quand des images sont pull.

**Mots-clés :** Watchtower, Docker API version, 1.25 vs 1.40, DOCKER_API_VERSION, client unsupported, containrrr/watchtower, infra/itops, Phase A

**Découverte :** 2026-05-04 — SA Phase A ITOps déploiement initial sur Mac mini Docker Desktop arm64.

---

## KP-57 — `eas update` sans `--environment <env>` ne pull pas les EAS env vars (post-Mac mini migration)

**Symptôme :** OTA push EAS Update réussi (`✔ Published!`), mais l'app crash au boot post-OTA avec une erreur du type :

```
Error: Missing required EAS env var: EXPO_PUBLIC_API_URL.
Configure it via `eas env:create --environment <preview|production>
--name EXPO_PUBLIC_API_URL --value <url> --visibility plaintext`
then republish.
```

Les vars sont **pourtant** déjà configurées dans `eas env:list --environment preview` côté EAS dashboard.

**Cause root :** Depuis EAS Update v17+, `eas update` n'inline les `EXPO_PUBLIC_*` du bundle JS QUE si on passe explicitement `--environment <env>` matching le `--channel`. Sans ce flag, EAS Update utilise uniquement les `.env*` locaux (gitignored, non versionnés). Sur une machine fraîche post-migration où `.env.local` n'a pas été restauré, le bundle ship sans les URLs API → crash au runtime.

**Solution propre :** TOUJOURS passer `--environment <env>` matching `--channel <env>` dans tous les push EAS Update. Re-publish suffit, pas besoin de rebuild EAS.

```bash
# ❌ Mauvais — skip les env vars EAS dashboard, ne lit que .env* local
eas update --channel preview --message "..."

# ✅ Bon — pull les env vars de l'environnement preview EAS
eas update --channel preview --environment preview --message "..."
```

**Pattern à appliquer dans toute commande de publish OTA :** matcher `--channel` ET `--environment`. Si tu utilises un script wrapper (ex: `ota-push.sh`), forcer les 2 flags ensemble.

**Détection :** Sentry remonte une issue `RATIS-CLIENT-N` (ou similaire) avec message exact "Missing required EAS env var" dans les minutes suivant un `eas update`. Si tu vois ça → re-publish avec le flag correct, l'OTA suivant fixe immédiatement.

**Lien R34 (CLAUDE.md) :** R34 EAS publish discipline doit toujours mentionner les 2 flags `--channel <X> --environment <X>` ensemble.

**Mots-clés :** eas update, --environment, --channel, EXPO_PUBLIC_API_URL, env var missing at runtime, OTA crash, post-Mac-mini migration, .env.local non restauré, Sentry RATIS-CLIENT-N, R34, EAS Update v17+

**Découverte :** 2026-05-05 — Premier push OTA preview après migration Windows → Mac mini. Bug détecté via Sentry, fix par re-publish avec `--environment preview` (Update group `603f4158`).

---

## KP-58 — `security` CLI is macOS-only — Linux CI fails on Keychain code paths if not mocked

**Symptôme :** Tests passing locally on Mac mini fail in Linux CI with :

```
FileNotFoundError: [Errno 2] No such file or directory: 'security'
```

The error fires inside `subprocess.run(["security", ...])` calls invoked by `agent_mcp.keychain.Keychain.get/set/delete` (or any code path that hits `subprocess.run(["security", ...])` without mocking).

**Cause root :** The `security` CLI is part of macOS only. CI runs on Linux Docker self-hosted runners. Any test that exercises a code path which transitively calls `Keychain.get/set/delete` (without mocking the runner) crashes on `FileNotFoundError` instead of returning the test's expected outcome.

**Specific instance discovered (chunk 2 SA dispatch, 2026-05-05) :**
- `tools/agent-mcp/tests/test_cli.py::test_keychain_set_empty_value_aborts` patched `getpass.getpass` to return empty string and asserted `cli.main(...)` returns rc=1.
- Locally on Mac mini (where `security` exists) : `kc.get(account)` at start of `cmd_keychain_set` raised `KeychainMiss` (caller not found) → empty value check fired → rc=1. ✅
- On Linux CI : same `kc.get(account)` raised `FileNotFoundError` (no `security` binary) → uncaught → test error, not rc=1. ❌

**Solution propre :** Tests touching Keychain MUST inject a fake `runner` callable via `monkeypatch.setattr(keychain_mod.Keychain, "__init__", patched_init_with_runner)`. The `Keychain` class accepts a `runner=` kwarg precisely for this. Reference pattern in `tools/agent-mcp/tests/test_cli.py::test_keychain_rm_with_yes_flag_skips_confirm` (chunk 1 already had the right pattern for one test, but missed it for another — chunk 2 portability fix).

```python
# ✅ Test pattern (Linux-portable)
def fake_runner(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[Any]:
    return subprocess.CompletedProcess(argv, 44, "", "")  # 44 = security "not found"

real_init = keychain_mod.Keychain.__init__
def patched_init(self: Any, *args: Any, **kwargs: Any) -> None:
    kwargs.setdefault("runner", fake_runner)
    real_init(self, *args, **kwargs)

monkeypatch.setattr(keychain_mod.Keychain, "__init__", patched_init)
```

**Pattern à appliquer dans tous les tests `tools/agent-mcp/tests/` qui touchent Keychain :** vérifier que `runner=` est injecté (directement ou via fixture). Audit run :
```bash
grep -L "fake_runner\|monkeypatch.setattr.*Keychain.*runner" tools/agent-mcp/tests/test_*.py | xargs -I {} grep -l "Keychain\(\)" {}
```

**Note R33 :** Pas de `pytest.mark.skipif(sys.platform != "darwin")` — c'est un workaround. Le bon fix est de mocker, pas de skipper. Skip = test devient inactif sur la moitié des plateformes = R33 violation.

**Détection :** CI Linux fail avec `FileNotFoundError: [Errno 2] No such file or directory: 'security'`. Stack trace pointe vers `subprocess.py:1955`.

**Mots-clés :** security CLI, macOS-only, Keychain.get, FileNotFoundError, subprocess.run, Linux CI, agent-mcp, fake_runner, runner kwarg injection, monkeypatch Keychain __init__

**Découverte :** 2026-05-05 — agent-mcp Chunk 2 Sentry tools PR #301 first CI run flagged the inherited test gap from Chunk 1.

---

## KP-59 — n8n Code node sandbox bloque `require()` sans `NODE_FUNCTION_ALLOW_BUILTIN`

**Symptôme :** Workflow execution échoue avec `Cannot find module 'crypto'` (ou autre Node.js builtin) ligne X dans un Code node, même si le code est syntaxiquement valide.

**Cause :** n8n 1.x sandboxe les Code nodes par défaut — `require('crypto')`, `require('fs')`, `require('http')` etc. sont **bloqués** sauf si whitelistés via env var `NODE_FUNCTION_ALLOW_BUILTIN`. Le default vide = aucun module Node.js core accessible.

**Fix :** ajouter dans le bloc `environment:` du service n8n dans `infra/itops/docker-compose.yml` :
```yaml
NODE_FUNCTION_ALLOW_BUILTIN: ${NODE_FUNCTION_ALLOW_BUILTIN:-crypto}
```
Et dans `.env` :
```bash
NODE_FUNCTION_ALLOW_BUILTIN=crypto
```
(comma-separated list, ou `*` pour tout — moins safe).

**Détection :** UI Executions tab montre node rouge avec "Cannot find module 'X' [line N]". Aussi visible dans `docker compose logs n8n` au moment du POST.

**Mots-clés :** n8n, Code node, require, crypto, sandbox, NODE_FUNCTION_ALLOW_BUILTIN, builtin module, HMAC, Cannot find module

**Découverte :** 2026-05-07 PR #320 — premier smoke après import des workflows hand-crafted.

---

## KP-60 — n8n hand-crafted workflow JSON nécessite `webhookId` UUID explicite

**Symptôme :** Après `n8n import:workflow` + activation, les logs disent `Activated workflow "X"` MAIS les requêtes POST sur le webhook path retournent **404** : `Received request for unknown webhook: The requested webhook "POST X" is not registered.`

**Cause :** n8n's UI auto-génère un champ `webhookId` (UUID v4) à la sauvegarde d'un node Webhook. Le CLI `import:workflow` **ne le génère pas** si absent du JSON. Sans `webhookId`, l'activation passe les checks mais la route HTTP n'est pas register dans la table `webhook_entity`.

**Fix :** ajouter un `webhookId` UUID au JSON de chaque node Webhook (hors `parameters`, au niveau du node) :
```json
{
  "parameters": { "path": "X", ... },
  "id": "...",
  "name": "Webhook",
  "type": "n8n-nodes-base.webhook",
  "typeVersion": 2,
  "webhookId": "<uuid-v4>"
}
```
Puis re-importer (idempotent par workflow id).

**Détection :** logs n8n containent `unknown webhook` à chaque POST. Le workflow apparaît bien dans `n8n list:workflow` avec `active=true` mais le path n'est pas joignable.

**Mots-clés :** n8n, webhook, webhookId, hand-crafted JSON, import:workflow, unknown webhook, 404 not registered, route registration

**Découverte :** 2026-05-07 PR #320 — quirk découvert pendant le smoke initial du workflow `sentry-ingest`.

---

## KP-61 — n8n HTTP nodes returning empty response → Merge `combineByPosition` stoppe le workflow

**Symptôme :** Workflow s'arrête en silence (UI montre tous les nodes en VERT, pas d'erreur) sur un node Merge configuré en `combineByPosition`. Aucun output downstream, response webhook = 200 empty body.

**Cause :** Quand un HTTP Request node retourne légitimement `[]` (empty array — par ex. Loki query no logs, GitHub commits empty, Sentry similar issues none), n8n split la response en **0 items**. Le node `Merge` en mode `combineByPosition` requiert ≥1 item sur **chaque** input pour produire un output. Avec 0 items sur 1 des inputs, le merge n'émet rien → workflow s'arrête naturellement (pas une erreur, juste fin de path).

**Fix :** ajouter `alwaysOutputData: true` au niveau du node (hors `parameters`) sur **chaque HTTP node** qui peut légitimement retourner empty :
```json
{
  "name": "Loki window",
  "type": "n8n-nodes-base.httpRequest",
  "alwaysOutputData": true,
  ...
}
```
Force l'émission d'au moins 1 item (vide `{}`) même quand la response API est vide. Le merge fonctionne alors normalement.

**Aussi pertinent pour :** Notion Database lookup (returns `[]` quand pas de match) — même fix.

**Détection :** UI Executions tab montre workflow arrêté en vert (no errors) à un Merge node. Pas d'output downstream visible.

**Mots-clés :** n8n, Merge node, combineByPosition, alwaysOutputData, empty response, 0 items, workflow stops silently, graceful degradation, enrichment

**Découverte :** 2026-05-07 PR #320 — surfaced quand on a tenté smoke avec Sentry/GitHub/Loki tous "down" (DB vide, prod pas connectée).

---

## KP-62 — n8n Webhook node + `options.rawBody: true` expose body en binaire (`item.binary.data`), pas en string

**Symptôme :** Code node tente de hash le body pour HMAC verification ; le body est PARSÉ en JS object malgré `options.rawBody: true` configuré sur le Webhook. Re-stringifier puis hasher donne un HMAC qui ne match jamais ce que l'expéditeur (Sentry / GitHub) a calculé sur les bytes raw.

**Cause :** n8n's Webhook node v2 avec `options.rawBody: true` ne preserve **PAS** le body en string. Le body est toujours parsé en JSON et exposé à `$json.body` (object). Le rawBody flag attache **également** une représentation binaire du body raw à `item.binary.data` (base64-encoded).

**Fix HMAC** (sentry-ingest + github-pr-merged-closer) :
```javascript
const item = $input.first();
const itemJson = item.json || {};
const headers = itemJson.headers || {};

// Raw bytes via binary attachment
const binaryData = item.binary && item.binary.data;
if (!binaryData || !binaryData.data) {
  return { json: { _hmac_status: "raw_body_unavailable", _statusCode: 500 } };
}
const rawBuf = Buffer.from(binaryData.data, "base64");
const expected = crypto.createHmac("sha256", secret).update(rawBuf).digest("hex");

// Parsed body for downstream nodes (already parsed by n8n)
const bodyObj = itemJson.body || {};
return { json: { ...bodyObj, _hmac_status: "ok" } };
```

**Smoke script** : utiliser `--data-binary @file` (envoie raw bytes du file) et HMAC sur les bytes du file, **PAS** `jq -c` re-stringification :
```bash
SIG=$(openssl dgst -sha256 -hmac "$SECRET" -hex < "$PAYLOAD_FILE" | awk '{print $2}')
curl ... --data-binary "@${PAYLOAD_FILE}"
```

**Bonus FYI :** Tailscale Funnel ajoute `tailscale-headers-info` + `tailscale-user-login` headers — utile pour V1 ACL.

**Détection :** HMAC fail systématique avec `_hmac_status: "invalid_signature"` même quand le sender + n8n utilisent le même secret. Debug via Code node returning `Object.keys(item)` montre `binary` field présent.

**Mots-clés :** n8n, webhook, rawBody, item.binary.data, HMAC verification, body re-stringification, JSON.stringify mismatch, raw bytes, base64, openssl dgst, --data-binary

**Découverte :** 2026-05-08 — fixé en main context après debug session iterative ; HMAC bypass V0 (PR #320) restored to proper verification.

---

## KP-63 — n8n CLI `import:workflow` re-link credentials par **name** (id literal toléré)

**Symptôme :** Après `n8n import:workflow`, les nodes qui réfèrent une credential paraissent linkés mais l'execution échoue avec auth error / la credential n'est pas trouvée.

**Cause :** Le JSON hand-crafted pose un `credentials: { typeName: { id, name } }` au niveau du node. n8n's CLI import effectue un **lookup par `name`** dans la credentials store local et **re-link automatiquement** l'`id` si le name match. Si le name ne match aucune credential locale → l'id literal reste dans le JSON et n8n ne trouve pas la credential à l'execution.

**Fix :** s'assurer que la credential dans n8n UI est nommée **exactement** comme dans le JSON. Si tu renames une credential APRÈS un import, **re-importer le workflow** pour que n8n re-link.

**Format recommandé pour JSON hand-crafted** :
```json
"credentials": {
  "notionApi": {
    "id": "ratis-notion-incidents",
    "name": "ratis-notion-incidents"
  }
}
```
Set `id == name` — n8n's import re-resolve l'id à la vraie UUID au moment de l'import.

**Détection :** Notion / GitHub / autres APIs return 401 Unauthorized lors de l'execution. Workflow logs montrent "credential not found" ou similar.

**Mots-clés :** n8n, credentials, import:workflow, credential link, by name, UUID, hand-crafted JSON, ratis-notion-incidents, ratis-github

**Découverte :** 2026-05-07 PR #320 — Notion lookup ne trouvait jamais de match jusqu'à ce que la credential soit renommée puis le workflow re-importé.

---

## KP-64 — UNIQUE nullable + PG `NULLS NOT DISTINCT`

**Symptôme :** Tu étends une UNIQUE constraint existante avec une nouvelle colonne nullable (ex. ajout `qualifier TEXT NULL` à un UNIQUE `(action_type, frequency, difficulty)`). Tu écris un test qui assert que 2 rows avec la même clé + `qualifier=NULL` doivent raise `IntegrityError`. Le test fail avec `DID NOT RAISE` — l'INSERT duplicate passe silencieusement.

**Cause root :** PG (et le standard SQL) traitent `NULL ≠ NULL`. Donc 2 rows avec `qualifier=NULL` ne sont **pas** considérées comme duplicates par défaut, même si tous les autres champs de la UNIQUE matchent. Symptôme insidieux : les tests passent en surface (insert ne raise pas) mais le duplicate s'installe en base. Détection tardive seulement quand un comportement métier touche le doublon (ex. lazy-gen pioche 2× le même template).

**Solution propre :** utiliser la clause `NULLS NOT DISTINCT` (PG 15+) sur la UNIQUE constraint.

```sql
ALTER TABLE missions
  DROP CONSTRAINT missions_action_type_frequency_difficulty_key;
ALTER TABLE missions
  ADD CONSTRAINT uq_mission UNIQUE NULLS NOT DISTINCT (action_type, qualifier, frequency, difficulty);
```

Côté SQLAlchemy ORM (pour `create_all` test DB) :

```python
__table_args__ = (
    UniqueConstraint(
        "action_type", "qualifier", "frequency", "difficulty",
        name="uq_mission",
        postgresql_nulls_not_distinct=True,
    ),
)
```

Côté Alembic, `op.create_unique_constraint()` ne supporte pas (encore) le flag `postgresql_nulls_not_distinct=True` directement (en discussion upstream — vérifier au moment de l'écriture). Workaround : `op.execute("ALTER TABLE ... ADD CONSTRAINT ... UNIQUE NULLS NOT DISTINCT (...);")`.

**Pattern à appliquer pour TOUTE table qui étend une UNIQUE avec une nullable column :**
- Toujours `NULLS NOT DISTINCT` sur la nouvelle constraint.
- Si test "INSERT duplicate raises" fail avec `DID NOT RAISE` sur extension UNIQUE → vérifier d'abord que `NULLS NOT DISTINCT` n'est pas oublié AVANT de chercher ailleurs.
- Garder le triplet en sync (pattern KP-08) : modèle SQLAlchemy + migration Alembic + (si applicable) script seed.

**Référence :** PR #324 (missions catalog v1) — voir `alembic/versions/20260508_1000_missions_catalog_v1.py` pour le pattern correct (DROP ancien `_key` + ADD `uq_mission` via `op.execute` raw SQL).

**Détection :** test pytest `test_*_unique_*` qui assert `IntegrityError` sur insert duplicate fail avec `DID NOT RAISE <class 'sqlalchemy.exc.IntegrityError'>`. En prod, doublons s'accumulent silencieusement dans la table jusqu'à ce qu'un côté métier trippe dessus.

**Mots-clés :** UNIQUE, NULLS NOT DISTINCT, nullable, qualifier, Postgres, PG 15+, IntegrityError, DID NOT RAISE, silent duplicate, extend constraint, migration UPDATE, postgresql_nulls_not_distinct, op.execute ALTER, missions_catalog_v1, PR #324

**Découverte :** 2026-05-08 — PR #324 (Phase A missions catalog), test `test_mission_unique_with_nullable_qualifier` fail à l'écriture, fix par `NULLS NOT DISTINCT` sur la nouvelle UNIQUE constraint.

---

## KP-65 — `extract_store_signals` filtre les keys préfixées `_` → `_city_raw` et `_raw_barcode` perdus en sortie

**Symptôme :** `webservices/ratis_product_analyser/worker/pipeline/store_detector.py:262` finit par `return {k: v for k, v in signals.items() if not k.startswith("_") and v is not None}`. Le code stash localement `_city_raw` (ligne 222) et `_raw_barcode` (ligne 214) pour usage interne (extraction du `store_code` via `barcode_formats`), mais ce filtre les supprime du dict retourné. Conséquences en cascade : `record_candidate` ne reçoit jamais de city (la table `store_candidates` n'a même pas la colonne — dette assumée) et le caller ne peut pas promouvoir l'OCR-derived barcode quand pyzbar a échoué (cf KP-66).

**Cause :** convention "private signals only" pour garder une API propre, mais aucun mécanisme alternatif d'exposition. Les données sont calculées puis jetées.

**Solution propre :** exposer `city` et `raw_barcode` dans le dict retourné (sans préfixe `_`) — laisser le caller décider de les utiliser. Côté store_candidates : ajouter colonne `city` via migration alembic + modèle SQLAlchemy + update `record_candidate` (cf P1-2 de l'audit OCR pipeline). Tant que la colonne city n'existe pas, au minimum exposer la raw value pour ne pas perdre la donnée déjà extraite.

**Mots-clés :** extract_store_signals, store_detector, _city_raw, _raw_barcode, signals filter, prefix underscore, store_candidates, city perdue, OCR pipeline, V2 refinement, audit silent drops

**Découverte :** lors de l'audit OCR pipeline 2026-04-30 (§ 3.17, 3.18 et § 6 P1-2). Drop confirmé sur ticket Intermarché Courbevoie `ddba8e7f-4523-490c-8947-1ce8ecf638ef` : `COURBEVOIE` extrait par OCR mais `store_candidates.city` reste vide.

---

## KP-66 — pyzbar fail + OCR-vu barcode → `receipt.receipt_barcode` reste NULL

**Symptôme :** Un ticket dont le code-barres est mal lu par pyzbar (qualité d'impression, contraste, angle) finit avec `receipts.receipt_barcode=NULL` même si l'OCR a vu la string complète (ex. `202604221101001800107879` à confidence 0.972). `receipts.receipt_barcode` n'est alimenté que via le path pyzbar (`worker/receipt_task.py:1585` env.) ; le `_raw_barcode` détecté par regex `\d{≥20}` dans `extract_store_signals` n'est utilisé que pour extraire le `store_code` et n'est jamais promu vers la colonne receipt.

**Cause :** path pyzbar et path OCR-regex sont déconnectés. Le `_raw_barcode` est aussi filtré à la sortie de `extract_store_signals` (cf KP-65), donc le caller `process_receipt` n'y a pas accès même s'il voulait l'utiliser.

**Solution propre :** quand pyzbar `read_receipt_barcode_with_fallbacks` retourne None, et que `extract_store_signals` a peuplé `_raw_barcode`, promouvoir cette valeur en `receipt.receipt_barcode`. Dépend de KP-65 (exposer `raw_barcode` dans le dict retourné, ou écrire directement dans `process_receipt` avant le filter `_*`).

**Mots-clés :** pyzbar, receipt_barcode, OCR barcode fallback, _raw_barcode, store_detector, barcode_reader, silent drop, NULL barcode, audit OCR

**Découverte :** lors de l'audit OCR pipeline 2026-04-30 (§ 3.3 et § 6 P0-4). Drop confirmé sur ticket Intermarché Courbevoie : OCR a la string barcode complète mais `receipts.receipt_barcode=NULL`.

---

## KP-67 — Address regex `^\d+\s+(RUE|...)` rejette `18 TER RUE`, `42 BIS RUE`, `5 A AVENUE`

**Symptôme :** Pour un ticket avec adresse `18 TER RUE DE BEZONS`, `extract_store_signals` ne détecte pas l'adresse → `signals["address"]` reste absent → `record_candidate` enregistre `address_guess=NULL` → fingerprint scoring perd le boost `+40` address_fuzzy → le ticket finit `store_status='unknown'` même quand le store existe en DB. Cas similaire : `42 BIS RUE`, `5 A AVENUE`, `1 QUATER PLACE`.

**Cause :** `webservices/ratis_product_analyser/worker/pipeline/store_detector.py:122` compile `re.compile(r"^\d+\s+" + keywords, re.IGNORECASE)`. Le `^\d+\s+` exige que le keyword (RUE, BD, AV, ...) suive directement le numéro, sans token intermédiaire. Les suffixes immobiliers français (BIS, TER, QUATER, A, B, C, D) cassent le pattern.

**Solution propre :** étendre le préfixe optionnel : `^\d+(\s+(?:BIS|TER|QUATER|[A-D]))?\s+` puis le keyword. Tests unitaires basés sur les vraies lignes alpha (Intermarché, Casino, Carrefour) qui exposent les variantes — capturer aussi `1ER ETAGE` et `Lieu-dit` si applicable, ou les rejeter explicitement.

**Mots-clés :** address regex, 18 TER, 42 BIS, QUATER, store_detector, _ADDRESS_KEYWORDS_BY_COUNTRY, _get_address_re, address_guess vide, store_status unknown, audit OCR

**Découverte :** lors de l'audit OCR pipeline 2026-04-30 (§ 3.15 et § 6 P0-5). Drop confirmé sur ticket Intermarché Courbevoie (`18 TER RUE DE BEZONS`).

---

## KP-68 — `_v2_output_to_receipt_data` cluster product sans prix → `continue` silent drop (no DB trace)

**Symptôme :** Un cluster classifié `product` par le LLM mais sans prix trouvé dans `y_tolerance` (cf KP-69) est silencieusement skippé : `worker/receipt_task.py:693-703` log un warning puis `continue`. L'item est complètement perdu — pas dans `pending_items`, pas dans `scans`, pas de `rejected_reason`. Le legacy `parse_receipt` n'est PAS sollicité pour rattraper (le check est `if receipt_data is None`, pas par-item). Conséquence : un ticket avec 2 lignes HIPRO finit avec `pending_items_count=1` au lieu de 2.

**Cause :** design de drop sans trace dans `_v2_output_to_receipt_data` — la logique « persisting a None-price item would create a useless scan » ne prévoit pas de chemin alternatif (pending_items entry avec `rejected_reason="no_nearby_price"`).

**Solution propre :** au lieu d'un `continue`, créer une entry `pending_items` avec `scanned_name`, `rejected_reason="no_nearby_price"`, et raw OCR text préservé. Si le store résout plus tard (`process_pending_items`), l'item peut être ré-évalué. Cf P0-1 de l'audit + P2-1 (contrat de pipeline : aucune ligne OCR'd ne disparaît sans trace).

**Mots-clés :** v2_assembly, _v2_output_to_receipt_data, find_price_for_cluster, no nearby price, continue silent, pending_items, rejected_reason, contrat pipeline, audit OCR

**Découverte :** lors de l'audit OCR pipeline 2026-04-30 (§ 3.11 et § 6 P0-1). Drop probable sur ticket Intermarché Courbevoie (un des 2 HIPRO).

---

## KP-69 — `y_tolerance=30.0` hardcodé dans `_v2_output_to_receipt_data` ne s'adapte pas à la résolution

**Symptôme :** Tickets thermiques avec hauteur de ligne variable (ex. ~40px chez Monoprix vs ~60px chez Intermarché après preprocessing) → `find_price_for_cluster` rate des prix qui sont juste hors de la fenêtre `y_tolerance=30px` constante. Cause mécanique principale du drop décrit dans KP-68.

**Cause :** `worker/receipt_task.py:636` définit `y_tolerance: float = 30.0` en argument par défaut. Le pipeline rescale les images à ~1600px, mais ne paramétrise pas la tolérance en fonction de la hauteur de bloc OCR observée.

**Solution propre :** rendre `y_tolerance` proportionnel à la hauteur médiane de cluster : `median(c.height for c in clusters) * 1.5` (cf P1-3 de l'audit). Conserver le 30px en fallback si zéro cluster. Tester sur fixtures alpha multi-retailers pour valider que la tolérance dynamique ne crée pas de faux-positifs (ex. associer un prix d'une autre ligne).

**Mots-clés :** y_tolerance, hardcoded 30, _v2_output_to_receipt_data, find_price_for_cluster, hauteur médiane, cluster height, scaling pipeline, audit OCR

**Découverte :** lors de l'audit OCR pipeline 2026-04-30 (§ 3.11 cause mécanique + § 6 P1-3).

---

## KP-70 — Branch A (`store_id is None`) — `finalize_receipt(total_amount=None)` hardcodé ignore `receipt_data.total_amount`

**Symptôme :** Quand le store n'est pas auto-confirmé (`receipt.store_id is None`), `worker/receipt_task.py:1851-1857` appelle `finalize_receipt(db, receipt, total_amount=None, ...)` même si `_v2_output_to_receipt_data` a réussi à extraire `receipt_data.total_amount`. Conséquence : un ticket dont le total est correctement extrait par OCR finit avec `receipts.total_amount=NULL` simplement parce que le store n'est pas reconnu. Le diagnostic terrain interprète ça comme "le pipeline n'a pas vu le total" alors que c'est juste une assignation oubliée.

**Cause :** code historique qui partait du principe "pas de store = pas de total persisté" — confusion entre "le total est lié au store" (faux : c'est une donnée du ticket) et "le cashback est lié au store" (vrai). Le total reste valide indépendamment du store.

**Solution propre :** passer `receipt_data.total_amount if receipt_data else None` à `finalize_receipt` même en Branch A. Fix d'une ligne (cf P0-3 de l'audit). Préserve l'info pour : (a) le diagnostic admin, (b) le `_is_parsing_suspect` check appliqué plus tard quand le store est confirmé, (c) le batch consensus sur receipts orphelins.

**Mots-clés :** finalize_receipt, total_amount=None, Branch A, store_id is None, pending receipt, receipt_data total, audit OCR, silent drop total

**Découverte :** lors de l'audit OCR pipeline 2026-04-30 (§ 3.22 et § 6 P0-3). Drop confirmé sur ticket Intermarché Courbevoie : `MONTANT DU 18,76 EUR` vu par OCR mais `receipts.total_amount=NULL`.

---

## KP-71 — Total LLM-only sans fallback `parse_receipt` quand items ≥ 1

**Symptôme :** Si le LLM ne classifie pas correctement la ligne `MONTANT DU 18,76 EUR` en `total` (la met en `dismissal` ou `other`), `total_amount=None` dans la sortie de `_v2_output_to_receipt_data`. Le worker check `if receipt_data is None` (ligne 1639) pour décider du fallback `parse_receipt` legacy — donc dès que v2 a extrait **au moins un item**, le total LLM-only écrase tout sans rattrapage. Le legacy regex `_TOTAL_RE` (`webservices/ratis_product_analyser/worker/pipeline/parser.py:49`) aurait reconnu `MONTANT DU` mais n'est jamais appelé.

**Cause :** check `is None` sur l'objet `receipt_data` complet plutôt que par-champ. Architecture v2 conçue comme "tout-ou-rien" plutôt que comme couches complémentaires.

**Solution propre :** quand `receipt_data is not None` mais `receipt_data.total_amount is None`, fallback ciblé sur `parse_receipt` **uniquement pour le total** (pas les items, pour éviter doublons). Cf P1-1 de l'audit. Logique : `total_legacy = parse_receipt(rich_blocks_winner).total_amount; if total_legacy: receipt_data.total_amount = total_legacy`.

**Mots-clés :** total_amount, MONTANT DU, LLM classify total, parse_receipt fallback, receipt_data is None, _TOTAL_RE, legacy parser, par-champ fallback, audit OCR

**Découverte :** lors de l'audit OCR pipeline 2026-04-30 (§ 3.13, 3.14 et § 6 P1-1).

---

## KP-72 — `process_pending_items` ne re-calcule pas `total_amount` quand items promus

**Symptôme :** Un receipt qui passe Branch A (store inconnu, items en `pending_items`) puis voit son store flippé à `confirmed` via user-action ou batch consensus → `process_pending_items` (cf `repositories/scan_repository.py`) crée les scans correspondants mais ne touche pas `receipt.total_amount`. Conséquence : ces receipts gardent `total_amount=NULL` à vie même si on connaît la somme des items, et même après KP-70 fixé. Régression silencieuse permanente sur la cohorte historique de receipts Branch A.

**Cause :** `process_pending_items` est conçu comme « promouvoir items pending → scans » uniquement, sans toucher aux champs aggregate du receipt parent.

**Solution propre :** quand `process_pending_items` promeut des items et que `receipt.total_amount IS NULL`, recalculer `total_amount` à partir de la somme des items promus (ou à partir d'une `pending_items[].total` stashée à l'écriture initiale, si on stocke aussi le total dans le JSONB). Cf P2-4 de l'audit. Backfill possible via batch sur receipts existants Branch A.

**Mots-clés :** process_pending_items, total_amount NULL, Branch A, store confirm, pending → scans, receipt aggregate, backfill, scan_repository, audit OCR

**Découverte :** lors de l'audit OCR pipeline 2026-04-30 (§ 3.22 note + § 6 P2-4). Aggravé par KP-70 mais distinct (KP-70 = ne stocke pas à l'écriture initiale ; KP-72 = ne rattrape pas plus tard).

---

## KP-73 — Image blurry / arbitrate fail → scan rejected sans `rejected_reason` détaillé

**Symptôme :** Quand `assess_quality` détecte une image blurry → `_run_ocr_pipeline` retourne `OcrPipelineResult(ocr_result=None)`. Idem si les 3 passes OCR + le fallback inverted ne convergent pas (`arbitrate` retourne None). Le worker finit dans la branch « no usable receipt_data » et appelle `create_scan(status='rejected', rejected_reason='no_usable_receipt_data')`. Diagnostic terrain : impossible de distinguer un fail "image cassée" (blurry) d'un fail "pipeline incohérent" (arbitrate convergence) — les 2 reçoivent le même `rejected_reason`.

**Cause :** réduction de toutes les causes amont à un seul `rejected_reason` générique. Les sous-fonctions logguent en warning mais ne propagent pas la cause.

**Solution propre :** propager une cause typée depuis `_run_ocr_pipeline` (par ex. enum `OcrFailReason.BLURRY | NO_OCR_CONVERGENCE | NO_BLOCKS`) jusqu'au `create_scan(rejected_reason=...)`. Cf P1-5 de l'audit. Permet de quantifier les drops (Sentry counter, dashboard admin) et de cibler des fix preprocessing.

**Mots-clés :** rejected_reason, no_usable_receipt_data, blurry, arbitrate, OCR pipeline fail, _run_ocr_pipeline, assess_quality, propagation cause, diagnostic terrain, audit OCR

**Découverte :** lors de l'audit OCR pipeline 2026-04-30 (§ 3.4, 3.5 et § 6 P1-5).

---

## KP-74 — `header_lines = lines[:8]` truncation perd retailer/postal/phone hors fenêtre

**Symptôme :** `worker/pipeline/store_detector.py:168` utilise `header_lines = lines[: _CFG.get("header_lines", 8)]`. Tickets avec un en-tête dépassant 8 lignes (logo + slogan + welcome + horaires + ...) → la ligne enseigne / postal / phone tombe en position 9+ et est silencieusement ignorée. Aucun log warning : la fonction continue avec `signals` partiels.

**Cause :** constante 8 historique adaptée à la majorité des tickets FR mais pas robuste aux variations retailer (ex. enseignes avec en-tête promotionnel long, tickets avec logo multi-lignes ASCII).

**Solution propre :** soit (a) augmenter la fenêtre par défaut (16-20 lignes — coût CPU négligeable, regex rapides), soit (b) introduire un fallback : si après 8 lignes aucun retailer match, scanner jusqu'à 20 lignes ; soit (c) rendre la fenêtre dépendante du retailer connu (per-retailer override dans `ratis_settings.json`). À minima, log warning quand `header_lines` truncation a coupé une ligne `\d{5}\s+` ou un nom uppercase candidat retailer.

**Mots-clés :** header_lines, lines[:8], truncation, store_detector, retailer perdu, postal_code perdu, header window, OCR header, audit OCR

**Découverte :** lors de l'audit OCR pipeline 2026-04-30 (§ 3.19).

---

## KP-75 — Achievement `exp_unknown_10` (Pionnier·e) un-unlockable en V1 — handler `_eval_unique_products_discovered_count` retourne `False` always

**RÉSOLU 2026-05-10** — V1.1 follow-up : option (a) implémentée. Migration `20260510_2100_pfd` ajoute `products.first_discovered_by_user_id UUID NULL` (FK ON DELETE SET NULL + partial index + backfill 1-shot) ; nouveau helper `ratis_core.products.claim_first_discovery` (CAS atomique, idempotent, anti-banni) câblé dans 5 chemins d'acceptation : `scan_repository.create_scan` + `barcode_repository.resolve_scan` + `label_task` (post-`update_label_scan_result`) + `pipeline_v3/persist._insert_scan` + `routes/admin/scans.patch_scan_override`. Handler dans `achievement_service.py` réécrit en `COUNT(*) FROM products WHERE first_discovered_by_user_id = user_id`. Couverture tests : 12 unitaires sur le helper (`ratis_core/tests/test_products_first_discovery.py`), 6 d'intégration sur les hooks (`webservices/ratis_product_analyser/tests/test_first_discovery_hooks.py`), 6 sur le handler (`webservices/ratis_rewards/tests/test_trigger_handlers.py::TestEvalUniqueProductsDiscoveredCount`). Garde l'historique ci-dessous pour traçabilité.

**Symptôme :** Le seed achievement `exp_unknown_10` (« Pionnier·e — Découvrir 10 produits jamais vus », Émeraude, 150 CAB) est livré dans le catalog mais **jamais déclenchable** côté event ni batch. User reachant la catégorie Exploration voit un Émeraude permanently locked sans path possible.

**Cause :** `webservices/ratis_rewards/services/achievement_service.py:240` `_eval_unique_products_discovered_count` retourne `False` unconditionally. La notion « produit jamais vu sur Ratis avant ce user » n'est pas disponible en V1 — il manque soit (a) une colonne `products.first_discovered_by_user_id UUID NULL` (= 1er user à scanner cet EAN se l'attribue), soit (b) une materialized view `MIN(scans.created_at) GROUP BY product_id`.

**Solution propre :** option (a) recommandée — la donnée est utile au-delà de cet achievement (fingerprint « primeur produit » exposable à terme). Migration data backfill 1-shot sur Mac mini. Cf `DECISIONS_PENDING.md` § DP-achievements-v1-followups item 1 pour l'arbitrage.

**Mots-clés :** achievement, unique_products_discovered_count, exp_unknown_10, Pionnier, first_discovered_by_user_id, achievements V1.1, placeholder False

**Découverte :** lors de l'implémentation PR2 Achievements V1 (2026-05-09) — schema gap identifié + documenté en TODO V1.1 inline + flagged au final code review.

---

## KP-76 — Achievement `progress` field always `null` dans serializer V1 — barre X/Y `<AchievementCard />` reste neutre — RÉSOLU 2026-05-10

**Symptôme :** `GET /api/v1/rewards/achievements` retourne `progress: null` pour TOUS les achievements (unlocked ou non). Le frontend `<AchievementCard />` a une barre X/Y qui ne reçoit aucun signal — affiche un état neutre au lieu de la progression user (ex : "47/50 scans" pour `v_50`). Les achievements se sentent statiques au lieu de motivants.

**Cause :** `webservices/ratis_rewards/services/achievement_serializer.py` est un placeholder V1 — `'progress': None` hardcoded. Le calcul live nécessite d'appeler chaque handler avec un mode "compute_current_value" (au lieu de boolean threshold check). Pattern non implémenté en V1 par souci de scope.

**Solution propre :** factoriser `achievement_service._compute_progress(ach, user_id) -> int | float | None` qui réutilise les SQL des handlers (`_eval_*` retourne bool ; `_compute_*` retourne le compteur courant). Le serializer appelle `_compute_progress` pour les non-unlocked. Coût : +1 SELECT par achievement non-unlocked dans `GET /achievements` → risque N+1 si 100+ achievements à terme. Mitigation : batcher via single CTE par `trigger_type` (`SELECT user_id, scan_count FROM (SELECT count(*) FROM scans WHERE ...) GROUP BY user_id`).

**Mots-clés :** achievement, progress, serializer, _compute_progress, AchievementCard, X/Y bar, achievements V1.1

**Découverte :** lors de l'implémentation PR5 Achievements V1 (2026-05-09) — explicit V1 scope decision (serializer ships avec `progress: null`, frontend tolère) + flagged au final code review comme V1.1 follow-up.

**RÉSOLU 2026-05-10** — V1.1 PR `feat(achievements): V1.1 progress field + refacto 5 primitives compute (KP-76)`. Refacto en 5 primitives compute partagées (`_count_for_user`, `_sum_for_user`, `_count_distinct_for_user`, `_max_streak_for_user`, `_first_event_seen`) + 9 wrappers `_compute_*` (un par trigger). Nouveau dispatcher `compute_progress(db, ach, user_id) -> int|float|None` câblé dans le serializer (param optionnel `db`/`user_id` — préserve la rétro-compat des tests unitaires sans DB). Endpoint `GET /api/v1/rewards/achievements` passe maintenant `db` + `current_user.id` au serializer. Unlocked → `progress = target_value` (barre pleine) ; non-unlocked → live value cappée au target ; secret unrevealed → `progress = null` (pas de leak). N+1 caveat documenté inline (V2 batch par trigger_type). 26 nouveaux tests, 800 tests ratis_rewards verts.

---

## KP-77 — `notifier/services/notify_service.py` quiet hours boundary off-by-one (22:00 Paris traité comme hors fenêtre 22h-8h)

**Symptôme :** Test `webservices/ratis_notifier/tests/test_notify.py:188` skipped depuis 2026-04-26 avec reason "DECISIONS_PENDING.md → DP-quiet-hours-paris-boundary". Le test expose : à 22:00 Paris exactement, le check quiet-hours retourne "outside" alors que la fenêtre est 22h-8h (devrait inclure 22:00).

**Cause :** Probable comparaison `>` au lieu de `>=` dans la logique de boundary check (`current_hour > 22 or current_hour < 8` au lieu de `current_hour >= 22 or current_hour < 8`). À investiguer dans `webservices/ratis_notifier/services/notify_service.py` (fonction quiet_hours / is_quiet_now).

**Solution propre :** (a) Confirmer la sémantique attendue (22:00 inclusive ou exclusive ?). Convention standard "quiet hours 22h-8h" inclut 22:00 et exclut 8:00. Donc `>= 22 or < 8`. (b) Fix la comparaison dans notify_service. (c) Réactiver le test (retirer le `@pytest.mark.skip`) + vérifier qu'il pass. (d) Ajouter test boundary 8:00 (devrait être hors quiet hours).

**Mots-clés :** notifier, quiet_hours, off-by-one, boundary, 22h-8h, Paris, push notification rate limit, test skip, DP-quiet-hours-paris-boundary

**Découverte :** lors de l'audit code health 2026-05-09 (§ F-5). Skip avait été mis ~2 semaines avant (2026-04-26) avec entrée locale `DECISIONS_PENDING.md` invisible aux reviewers GitHub.

---

## KP-78 — CHECK constraints non miroitées dans l'ORM masquent des bugs prod silencieux (Bug 5 + OAuth merge)

**RÉSOLU 2026-05-11** — fix(auth): bug5 — DELETE /account was silently broken (widen users CHECKs).

**Symptôme :**
Deux chemins applicatifs étaient cassés silencieusement en prod alors que les tests passaient au vert :
1. `DELETE /api/v1/account` (RGPD anonymize) → 500 systématique au `db.commit()`. La routine d'anonymisation écrit `provider='deleted', password_hash=NULL, provider_id=NULL, is_deleted=TRUE` ; ces valeurs étaient rejetées par les CHECKs `provider_check` (whitelist sans `'deleted'`) et `auth_coherence` (3 branches sans arm tombstone).
2. `POST /api/v1/auth/oauth` (Google/Apple) pour un email user existant → 500 au `db.commit()`. Le code de link-by-email mettait `provider='google'` (ou apple) + `provider_id` mais laissait `password_hash` populé → `auth_coherence` reject (l'arm OAuth exige `password_hash IS NULL`).

**Cause racine commune :**
Les CHECKs n'étaient PAS déclarées dans `__table_args__` du modèle ORM `User` — uniquement dans les migrations Alembic. La fixture `setup_db` de chaque service utilise `Base.metadata.create_all(bind=engine)` qui matérialise UNIQUEMENT les CHECKs déclarées dans l'ORM. Résultat : le schéma de tests était plus permissif que celui de production, et tout INSERT/UPDATE incohérent passait silencieusement en tests mais explosait en prod. C'est le **Pattern A drift** — la guard `ratis_core/tests/test_schema_sync.py::test_orm_check_constraints_match_pg` existe précisément pour détecter ce drift, mais ces 2 CHECKs étaient en `DEFERRED_PG_ONLY_CONSTRAINTS` (deferred set) avec un commentaire "follow-up pending" qui traînait.

**Solution propre :**
- Mirror dans `ratis_core/ratis_core/models/user.py::__table_args__` les 2 CheckConstraint `provider_check` et `auth_coherence` (mêmes noms que PG → la guard détecte la correspondance).
- Retrait des 2 entrées de `DEFERRED_PG_ONLY_CONSTRAINTS` dans `test_schema_sync.py`.
- Fix le service OAuth (`oauth_google` + `oauth_apple` dans `services/auth_service.py`) : clear `user.password_hash = None` lors de la transition email → OAuth.
- Fix la fixture admin (`tests/admin/test_admin_users.py::_make_user`) : respecter le shape per-provider (email garde hash, OAuth a `provider_id`, internal/deleted ont les 2 NULL).
- Migration `20260511_1000_rgpd_anon_completeness` avait déjà widenné les CHECKs en prod (audit F-AU-3) — donc PG accepte le tombstone, c'est l'ORM qui était en retard.
- Ajout d'un test E2E `webservices/ratis_auth/tests/test_account_delete_e2e.py` (qui aurait dû exister depuis le ship) + tests CHECK unitaires `ratis_core/tests/test_users_constraints.py` (16 tests sur les arms).

**Leçon transverse :**
Une entrée dans `DEFERRED_PG_ONLY_CONSTRAINTS` n'est pas neutre — c'est un signal qu'un test ne capture pas la même réalité que la prod. Toute entrée doit avoir un follow-up PR explicite + deadline, sinon elle masque un bug latent. Le merge d'une migration qui widen un CHECK doit être suivi IMMÉDIATEMENT du mirror ORM dans le même PR.

**Mots-clés :** Pattern A, DEFERRED_PG_ONLY_CONSTRAINTS, provider_check, auth_coherence, create_all, CheckConstraint, ORM drift, schema sync, DELETE /account, RGPD anonymize, tombstone, OAuth merge, link-by-email, password_hash NULL, silent IntegrityError, audit F-AU-3, migration 20260511_1000_rgpd_anon_completeness

**Découverte :** Bug 5 listed dans le bug-board orchestrator (2026-05-11) — investigation a révélé que la migration prod était déjà appliquée mais l'ORM mirror manquait, ce qui expliquait le silencieux. L'OAuth merge bug a été révélé en bonus une fois le mirror appliqué (4 tests prod-relevant qui passaient sur le schéma permissif sont devenus rouges → fix direct du service, pas un workaround).

---

## KP-79 — Drift `require_env(...)` ↔ `docker-compose.prod.yml` casse le boot au prochain deploy (Bug 5 collatéral)

**RÉSOLU 2026-05-12** — fix(ops): docker-compose.prod.yml env passthroughs (auth RGPD_ANONYMIZE_SALT + notifier REDIS_URL) + regression guard.

**Symptôme :**
Lors du deploy `b02eb6c` (Phase C + anti-fraud + Bug 5), deux services ont crashé au boot avec exactement le même message :
- `auth` : `RuntimeError: Missing required environment variables: RGPD_ANONYMIZE_SALT — aborting`
- `notifier` : `RuntimeError: Missing required environment variables: REDIS_URL — aborting`

Hot-patch prod : ajouté manuellement les 2 lignes dans `docker-compose.prod.yml` sur la VM + généré un salt random pour `.env.prod`. Sans sync repo, le prochain `./deploy-prod.sh` `git pull` aurait écrasé les hot-patches et recrashé prod au même endroit.

**Cause racine :**
Pattern de dérive entre :
1. Le code service (`webservices/ratis_<svc>/main.py` → `require_env("VAR", ...)` au lifespan, fail-fast obligatoire R20)
2. Le compose prod (`docker-compose.prod.yml services.<svc>.environment.VAR: ${VAR:?...}` qui passthrough l'env du `.env.prod` au container)

Quand un dev ajoute `require_env("NEW_VAR")` mais oublie d'ajouter `NEW_VAR: ${NEW_VAR:?...}` dans le compose, le service crashe en prod alors qu'il démarre en dev (où `.env.local` est chargé via `load_dotenv` au top du `main.py` directement, sans passer par compose). Tests CI passent (conftest.py injecte la var pour les tests).

Cas `RGPD_ANONYMIZE_SALT` : ajouté dans `require_env(...)` lors de Bug 5 (PR #399 — RGPD anon completeness, audit F-AU-3) mais la 3e étape de R20 (mettre à jour le compose prod) a été oubliée.
Cas `REDIS_URL` notifier : drift pré-existant (V1.1 push rate-limiter Redis SETNX) — la `require_env("INTERNAL_API_KEY", "REDIS_URL")` était là depuis le push rate-limiter mais le compose n'a jamais reçu la mapping.

**Solution propre :**
- Ajouter les 2 lignes manquantes dans `docker-compose.prod.yml` (fix immédiat — sync repo avec hot-patch prod).
- Ajouter une garde de régression `scripts/tests/test_compose_env_passthrough.py` qui :
  - Parse chaque `webservices/ratis_<svc>/main.py` avec AST, extrait chaque var passée à `require_env(...)` et `require_env_min_length(...)`
  - Charge `docker-compose.prod.yml` via `yaml.safe_load` et lit la map `services.<svc>.environment`
  - Assert que chaque var requise a une clé dans le compose env mapping (la syntaxe `:?` vs `:-` est laissée au choix par-var)
- Wirer la garde au workflow `.github/workflows/security.yml` (nouveau job `compose_env_passthrough`) — bloque la merge si nouvelle drift introduite.
- Mettre à jour `.env.example` pour documenter `RGPD_ANONYMIZE_SALT` (génération : `openssl rand -hex 32`, NEVER ROTATE).
- Ajouter `notifier` à `depends_on: redis` (le service utilise REDIS_URL au boot pour le push rate-limiter).

**Leçon transverse :**
R20 (`.env.example` + `conftest.py` + `require_env()` simultaneous) doit explicitement inclure **un 4e endroit** pour les services prod-ready : `docker-compose.prod.yml services.<svc>.environment`. Sans CI guard, le drift est invisible jusqu'au prochain deploy. La guard `test_compose_env_passthrough.py` est le filet — same spirit que `test_schema_sync.py::test_orm_check_constraints_match_pg` pour le drift ORM↔PG (KP-78).

**Mots-clés :** require_env, docker-compose.prod.yml, environment passthrough, RGPD_ANONYMIZE_SALT, REDIS_URL, auth boot, notifier boot, RuntimeError missing env, fail-fast lifespan, R20, deploy crash, Bug 5 collateral, PR #399, test_compose_env_passthrough.py, AST parsing, security.yml CI guard, drift detection

**Découverte :** Au moment du deploy `b02eb6c` (12 mai 2026) — `docker ps` montrait auth + notifier en restart-loop, `docker logs ratis_auth-1` a craché le RuntimeError immédiat. Hot-patch + sync repo immédiat (cette PR).

---

## KP-80 — Clause `OR` avec expression function-wrapped bypass un partial GIN trgm index → silent prod seq scan

**RÉSOLU 2026-05-13** — `alembic/versions/20260513_1000_brands_text_trgm_idx.py` ajoute un index fonctionnel match-verbatim sur l'expression `brands_text`.

**Symptôme :**
PO reporte 2-3s de latence entre la saisie « lait » dans l'AddBar Liste et l'apparition du dropdown autocomplete. Le 300ms debounce + 30s React Query staleTime étaient déjà en place, donc la lenteur perçue venait d'ailleurs. `time curl` côté prod confirmait 1.7-2s de réponse backend par requête sur le catalogue OFF (~2.5M rows).

**Cause racine :**
La requête SQL de `GET /api/v1/product/search` (dans `webservices/ratis_product_analyser/repositories/product_search_repository.py`) :

```sql
SELECT ... FROM products
WHERE source <> 'user_suggested'
  AND (
      name_normalized LIKE :anywhere
      OR UPPER(immutable_unaccent(coalesce(brands_text, ''))) LIKE :anywhere
  )
```

Le premier arm du `OR` utilise l'index existant `ix_products_name_normalized_trgm` (GIN trgm sur colonne GENERATED `name_normalized`). Le second arm wrappe `brands_text` dans une **fonction immutable** (`UPPER(immutable_unaccent(coalesce(...)))`) sans **index fonctionnel correspondant**. PostgreSQL ne peut **pas** satisfaire le `OR` via un BitmapOr de deux index distincts si l'un des deux n'est pas indexable → il tombe en **Seq Scan full table**.

EXPLAIN ANALYZE sur dev DB (50k rows synthétiques) avant fix : `Seq Scan on products (cost=0.00..14217.02 rows=405 width=108) (actual time=1.293..35.206 rows=50 loops=1)`. Extrapolation prod : ~1.7-2s.

**Solution propre :**
Créer un index fonctionnel GIN trgm **match-verbatim** sur l'expression du WHERE :

```sql
CREATE INDEX ix_products_brands_text_normalized_trgm
ON products USING gin (
    (UPPER(immutable_unaccent(COALESCE(brands_text, '')))) gin_trgm_ops
);
```

Détails :
- L'expression de l'index = l'expression du WHERE (à un caractère près). PG voit que les deux match, peut satisfaire le `OR` via BitmapOr.
- `COALESCE('')` au lieu de `WHERE brands_text IS NOT NULL` (partial index lossy si le SQL coalesce dans le WHERE).
- `immutable_unaccent` est le wrapper IMMUTABLE shippé dans `db/schema.sql` autour de `unaccent` (qui n'est pas IMMUTABLE par défaut).
- Pas de `CONCURRENTLY` : alembic wrappe la migration en transaction par défaut. Sur prod (2.5M rows) le `CREATE INDEX` lock la table 1-2min — acceptable en alpha low-traffic.

Re-EXPLAIN après index : `BitmapOr (cost=12.83..12.83 rows=405 width=108) (actual time=0.33ms)`. Latency passe de 1.7-2s → <50ms à l'échelle prod.

**Leçon transverse :**
Quand le WHERE filtre via `colA LIKE '%x%' OR func(colB) LIKE '%x%'`, **chaque arm a besoin d'un index distinct**. PG ne peut pas combiner « index partial sur colA » + « seq scan sur func(colB) » via un BitmapOr — il fallback en seq scan complet. Vérification simple : `EXPLAIN ANALYZE` sur un query type, repérer `Seq Scan` au lieu de `Index Scan` ou `Bitmap Heap Scan` → un des arms du `OR` n'a pas d'index match-verbatim. Le pattern « index sur l'expression IMMUTABLE complète » est la solution canonique côté PG.

**Mots-clés :** GIN trgm, pg_trgm, OR clause, function-wrapped, immutable_unaccent, BitmapOr, seq scan, product search, name_normalized, brands_text, alembic migration, functional index, performance, slow query, 2-3s latency, AddBar dropdown, R8 psycopg-v3

**Découverte :** Profilage post-OTA wave 6 — PO ticket « recherche trop lente (2-3s) ». SA dev a EXPLAIN ANALYZE sur dev DB seedé 50k rows, repéré le `Seq Scan` au lieu d'un `BitmapOr`, écrit la migration. Reproductible dès qu'on dépasse ~5k produits indexés (en dessous le seq scan est plus rapide qu'un index lookup).

---

## KP-81 — `migrate-prod.sh` ne git-pull pas → la migration container ne voit pas les nouveaux fichiers alembic

**RÉSOLU 2026-05-14** — `migrate-prod.sh` patché : git pull + rebuild image migrations en pré-requis du `docker compose run`, avec flags `--no-pull` / `--no-rebuild` pour les edge cases.

**Symptôme :**
Lors du déploiement wave 6 (13 mai 2026), j'ai lancé `./migrate-prod.sh` après merge de PR #431 qui shippait une nouvelle migration (`20260513_1000_brands_text_trgm_idx`). Le script a reporté « alembic upgrade head succeeded » + version_num `20260511_2400_c2org` (= HEAD précédent, pas le nouveau). La migration n'avait **pas tourné**.

**Cause racine :**
`migrate-prod.sh` (`./migrate-prod.sh`) lance `docker compose --profile migrate run --rm migrations`. Cette commande utilise l'**image migrations actuellement construite** sur le host prod — qui contient les fichiers `alembic/versions/` au moment du dernier `docker build`. Le script ne fait **pas** :

1. `git pull --ff-only origin main` sur prod pour récupérer le nouveau fichier alembic
2. `docker compose --profile migrate build migrations` pour reconstruire l'image avec les fichiers à jour

Conséquence : la migration container tourne avec l'image stale (du dernier deploy). `alembic upgrade head` cherche le head dans son filesystem interne, ne voit pas le nouveau fichier, conclut « déjà à head » → success silencieux mais misleading.

À l'inverse, `deploy-prod.sh` (post PR #424) orchestre tout : git pull → build migrations → upgrade head → build services → restart. Donc passer par `deploy-prod.sh` au lieu de `migrate-prod.sh` corrige le problème mais redéploie aussi les services (overhead inutile si seule une migration change).

**Solution propre :**
Patcher `migrate-prod.sh` pour qu'il fasse explicitement :

```bash
ssh_prod "set -e; cd $PROD_DIR && git fetch origin main && git merge-base --is-ancestor HEAD origin/main || die 'prod diverged'"
ssh_prod "cd $PROD_DIR && git pull --ff-only origin main"
ssh_prod "cd $PROD_DIR && $COMPOSE_PROD --profile migrate build migrations"
ssh_prod "cd $PROD_DIR && $COMPOSE_PROD --profile migrate run --rm migrations"
ssh_prod "cd $PROD_DIR && $COMPOSE_PROD exec -T postgres psql -U ratis -d ratis_prod -At -c \"SELECT version_num FROM alembic_version;\""
```

Plus un flag `--no-pull` pour les rares cas où l'opérateur veut explicitement rerun la migration avec l'image actuelle (ex: corruption metadata alembic, dev DB local).

**Workaround manuel (avant patch shippé) :**
```bash
ssh root@prod 'cd /root/ratis && git pull --ff-only origin main && docker compose -f docker-compose.prod.yml --env-file .env.prod --profile migrate build migrations && docker compose -f docker-compose.prod.yml --env-file .env.prod --profile migrate run --rm migrations'
```

**Leçon transverse :**
Les scripts ops qui appellent `docker compose run` sur une image custom **doivent** soit (a) rebuild l'image avant chaque run, soit (b) require que l'image soit déjà à jour (avec un check). `migrate-prod.sh` faisait implicitement (b) sans le checker, ce qui ne se voit qu'au moment où la migration silencieuse fait croire au succès. À catch dans la garde de regression : `alembic current` sur prod après migrate, comparé avec `alembic heads` côté repo, doit match.

**Mots-clés :** migrate-prod.sh, alembic upgrade head, docker compose run --rm, stale image, migrations profile, git pull, silent success, version_num, 20260513_1000_btxttrgm, wave 6, deploy-prod.sh, ops_lib.sh, R29, scripts/migrate-prod.sh, image rebuild

**Découverte :** Wave 6 deploy (13 mai 2026) — j'ai vu `Current alembic_version: 20260511_2400_c2org` après le « upgrade head succeeded » et tilté que le head local était `20260513_1000_btxttrgm`. Workaround manuel via SSH + rebuild + run.

---

## KP-82 — `onPress` vs `onPressIn` sur dropdown row : touch-up timing battu par le blur du parent TextInput

**RÉSOLU 2026-05-12** (wave 5) — PR #430 : `Pressable` du dropdown utilise `onPressIn` au lieu de `onPress`, + délai blur 250ms.

**Symptôme :**
PO reporte « quand je sélectionne un produit dans le dropdown autocomplete de la Liste, ça ne l'ajoute pas à ma liste ». Le test unitaire jest passait (`props.onSelectHit` mocké appelé), mais sur device réel le tap était silencieusement avalé.

**Cause racine :**
La séquence d'events React Native pour un tap court :

1. `touch-down` sur la dropdown row → React Native va éventuellement fire `onPress` à `touch-up`
2. **MAIS** simultanément, le TextInput parent au-dessus de la dropdown row reçoit `onBlur` (le focus se déplace vers la Pressable, donc le TextInput perd le focus en arrivée de touch)
3. Le `onBlur` est wired à `setFocused(false)` immédiat → ça démonte la dropdown (rendue conditionnellement sur `focused`)
4. La Pressable est démontée **avant** que `touch-up` n'arrive → `onPress` ne fire jamais

Sur un test jest avec `fireEvent.press(pressable)`, RN simule directement `onPress` sans simuler le blur cascade, donc le test passe à tort.

**Solution propre :**
Deux changes combinés (PR #430) :

1. **Switch `onPress` → `onPressIn`** sur la dropdown row. `onPressIn` fire à `touch-down`, **avant** le blur cascade. Le handler atteint le parent avant que la Pressable soit démontée.

2. **Délai 250ms sur le `onBlur`** du TextInput : `onBlur={() => setTimeout(() => setFocused(false), 250)}`. Filet de sécurité au cas où certains devices fire `onPressIn` après `onBlur` (ordre non garanti par RN sur tous Android).

```tsx
<Pressable
  onPressIn={() => pickHit(hit)}   // ⭐ pas onPress
  hitSlop={6}                       // étend la zone touchable
  ...
>
```

**Limitation : la regression test jest ne reproduit pas le bug** (RN testing-library simule `onPress` direct sans blur). Solution : tester avec `fireEvent.pressIn(...)` explicite ET asserter qu'un blur cascade entre tap et fire n'affecte pas le résultat (ce qui demande de patcher le setTimeout).

**Leçon transverse :**
Pour tout dropdown / autocomplete / menu déroulant **conditionné sur le focus du parent TextInput** :
- Utiliser `onPressIn` (touch-down) au lieu de `onPress` (touch-up) sur les rows. Le handler doit fire **avant** le démontage potentiel.
- Délai 200-300ms sur le `onBlur` du TextInput pour donner le temps à l'event chain de se résoudre.
- `hitSlop` pour pardonner les taps approximatifs aux bords.
- Test jest manuel avec `fireEvent.pressIn` (pas `fireEvent.press`) pour matcher le code path runtime.

Ce pattern apparaîtra à chaque fois qu'on a un dropdown anchored sur le focus d'un input — Pré-V1 : Liste AddBar. Probables futures : recherche produit dans Produit tab, search bar admin UI, autocomplete dans formulaires.

**Mots-clés :** onPressIn, onPress, Pressable, touch-up, touch-down, dropdown, autocomplete, TextInput, onBlur, blur cascade, focus, conditional render, React Native, racing event, hitSlop, AddBar, wave 5, PR #430, jest fireEvent.press, fireEvent.pressIn, gesture handler

**Découverte :** PO ticket wave 4 (12 mai 2026) — tap silently avalé. Diagnostic SA dev en remontant la chaîne de focus events RN. Confirmé par PO testing post-OTA wave 5.

---

## KP-83 — Celery worker `list_optimiser_worker` : `ModuleNotFoundError: services` au boot du worker en prod

**RÉSOLU 2026-05-13** (PR #436) — `PYTHONPATH: /app/webservices/ratis_list_optimiser` ajouté à l'env du service `list_optimiser_worker` dans `docker-compose.prod.yml`.

**Symptôme :**
Post-deploy wave 6, le service `list_optimiser_worker` (Celery worker pour l'optimisation OSRM des routes) crash-loopait silencieusement au boot avec `ModuleNotFoundError: No module named 'services'`. La task `optimize_route` ne tournait jamais → les routes restaient en `pending` indéfiniment, le UI Itinéraire montrait un spinner permanent. FastAPI process (`list_optimiser_api`) tournait normalement, lui : le bug était isolé au worker.

**Cause racine :**
Le script CLI `celery` (entry-point fourni par `pip install celery`) **n'ajoute pas le `cwd` à `sys.path` au boot**, contrairement à ce que fait `uvicorn` quand on lui passe `--app-dir`. Du coup, quand le `Dockerfile` du worker fait `WORKDIR /app/webservices/ratis_list_optimiser` puis `CMD ["celery", "-A", "tasks", "worker", ...]`, le worker démarre dans le bon dossier mais Python ne voit pas `services/`, `routes/` etc. dans `sys.path`. Le `import services.route_service` dans `tasks.py` explose au moment où Celery résout `-A tasks`.

Le bug n'apparaissait pas en dev parce que le `docker-compose.yml` (dev) avait déjà la bonne entry `PYTHONPATH` héritée d'un précédent fix, mais `docker-compose.prod.yml` ne l'avait pas — divergence dev/prod silencieuse.

**Cousin de KP-39** (même root cause sur `ratis_product_analyser` Celery worker). À chaque nouveau service Celery, vérifier le `PYTHONPATH` explicite dans le compose.

**Solution propre :**
```yaml
# docker-compose.prod.yml
list_optimiser_worker:
  environment:
    PYTHONPATH: /app/webservices/ratis_list_optimiser
    # ... reste
```

Alternative considérée : `python -m celery` au lieu de `celery` (qui force Python à insérer `cwd` dans `sys.path`). Rejeté pour cohérence avec les autres workers du repo qui utilisent tous le script direct + `PYTHONPATH` explicite (pattern documenté).

**Test guard :** ajouter au CI un check qui parse chaque compose file et vérifie que tout service nommé `*_worker` a un `PYTHONPATH` explicite. Type-check de l'infra, équivalent au `test_compose_env_passthrough.py` de KP-79.

**Leçon transverse :**
Les workers Celery shippés via le script CLI direct (`celery -A ...`) doivent **toujours** avoir `PYTHONPATH` explicite dans le compose, sinon `sys.path` au boot ne contient pas le `cwd` du Dockerfile. Symptôme typique : `ModuleNotFoundError` sur le premier import de module local au moment où Celery résout `-A <module>`. Diagnostic : `docker compose logs <worker_service>` montre le traceback complet.

**Mots-clés :** celery, list_optimiser_worker, sys.path, PYTHONPATH, ModuleNotFoundError, services, route_service, docker-compose.prod.yml, worker boot, FastAPI vs Celery script, dev/prod divergence, KP-39 cousin, PR #436

**Découverte :** Post-deploy wave 6 (13 mai 2026) — PO a remarqué le spinner permanent sur Itinéraire. Audit `docker compose logs list_optimiser_worker` → traceback `ModuleNotFoundError: services`. Cross-check avec `ratis_product_analyser` worker (KP-39) → même fix appliqué.

---

## KP-84 — Liste autocomplete `POST /lists/{id}/items` : FE envoyait `search_term`, backend attendait `query` → article jamais ajouté quand sélectionné depuis le dropdown

**RÉSOLU 2026-05-13** (waves 5-9) — FE renvoie le bon shape, backend Pydantic strict mode catch les mismatchs futurs.

**Symptôme :**
PO sur wave 4 : « quand je tape "lait" ça matche bien dans le dropdown, mais quand je sélectionne un produit, rien n'est ajouté à la liste ». Le tap était reçu (cf KP-82 résolu en wave 5), le `POST /lists/{id}/items` partait avec un 200 OK, mais l'item n'apparaissait pas dans le state local et un GET ultérieur sur la liste ne le montrait pas non plus. Backend logs : aucune trace d'INSERT items, pas d'erreur 4xx/5xx — juste un no-op silencieux côté service.

**Cause racine :**
Le FE postait `{ "search_term": "Lait demi-écrémé", "product_ean": "3245678901234" }` mais le Pydantic model côté `ratis_list_optimiser` était :

```python
class AddItemRequest(BaseModel):
    query: str | None = None
    product_ean: str | None = None
    quantity: int = 1
    # ...
```

Pydantic v2 en `extra="allow"` (config par défaut historique du projet) → le champ `search_term` était silencieusement ignoré, `query` restait `None`, le service `add_item_to_list` voyait `query=None` + `product_ean="3245..."` et prenait la branche « ajout par EAN direct ». MAIS l'EAN était mal-typé côté FE pour un product synthétique généré à la volée (sans ligne `products` correspondante) → le service retournait `None` au lieu de raiser, le route renvoyait 200 OK avec un body vide, pas d'INSERT. Triple no-op silencieux.

Le contrat FE↔BE était divergent depuis la création de la route, jamais détecté parce que :
- Pydantic `extra="allow"` (au lieu de `extra="forbid"`) → typo ignorée silencieusement
- Le service `add_item_to_list` ne raise pas quand son ENA ne résout rien → return early `None`
- La route route_handler ne distinguait pas `item=None` (echec) de `item=created` (success) → toujours 200 OK
- Le test integration `test_add_item_by_query` utilisait `query="..."` directement, jamais `search_term="..."` (test ne shadowing pas le bug FE)

**Solution propre (3 niveaux) :**
1. **FE** : renommé `search_term` → `query` dans `list-client.ts` POST `/items`. Champ aligné avec le contrat backend.
2. **Backend Pydantic** : passé `model_config = ConfigDict(extra="forbid")` sur `AddItemRequest`. Une typo future → 422 Unprocessable Entity bien explicite, plus de silence.
3. **Backend service** : `add_item_to_list` raise `ItemResolutionError` quand ni `query` ni `product_ean` ne résout, route map → 404. Plus de return `None` silencieux.

**Leçon transverse :**
Les contrats Pydantic doivent être `extra="forbid"` par défaut sur les routes externes (FE-facing). `extra="allow"` (le défaut historique) cache les typos FE et les évolutions de contrat non-coordonnées. À ajouter en règle CI : grep `extra="allow"` dans les Pydantic models exposés en route et flag warning. À étendre aux autres services dans une PR ciblée.

Symétriquement, les services métier ne doivent pas return `None` silencieux quand un input est inrésolvable — un `ItemResolutionError` ou `NotFoundError` typé permet à la route de mapper proprement vers une 4xx.

**Mots-clés :** Pydantic, extra=forbid, extra=allow, search_term, query, AddItemRequest, list items, POST /lists/{id}/items, FE contract drift, silent 200, no INSERT, add_item_to_list, return None, ItemResolutionError, list-client.ts, waves 5-9, R8 schema contract

**Découverte :** PO ticket wave 4 (12 mai 2026) — « Lait dans dropdown mais pas dans la liste ». Tracé via logs FastAPI + Pydantic raw body inspection. Bug latent depuis la création de la route — jamais déclenché parce que le test intégration utilisait le bon nom de champ.

---

## KP-85 — Sentry token EAS build : scope `project:releases` + `org:read` requis pour le sourcemap upload gradle, sinon 401 silencieux et symbolicate runtime KO

**RÉSOLU 2026-05-14** (work in progress) — token à régénérer côté Sentry web avec les bonnes scopes.

**Symptôme :**
Builds EAS Android (preview channel) tombent en erreur depuis le PR #443 avec un message obscur lors du sourcemap upload phase de Sentry gradle plugin : `Invalid token (http status: 401)` puis sur retry `403 permission denied`. Pas dans la liste d'erreurs Sentry connues. Le build fini quand même en succès si on désactive le plugin (via `SENTRY_DISABLE_AUTO_UPLOAD=true`), mais runtime Sentry ne peut alors plus symbolicate les stack traces JS → toutes les errors apparaissent comme `index-Hash.android.bundle:N:M` au lieu des fichiers source.

**Cause racine :**
Le Sentry auth token créé via le UI « User Auth Tokens » (page perso `/settings/account/api/auth-tokens/`) **n'inclut pas** par défaut le scope `project:releases`. Le sourcemap upload gradle plugin appelle `POST /api/0/organizations/<org>/releases/<release>/files/` qui requiert :
- `project:releases` (créer une release + upload de files associés)
- `org:read` (résolution du `<org>` slug → id)

Un token avec uniquement `event:read` (le défaut) authentifie pour les API ingestion (envoi d'events) mais **rate-limit ou rejette** les API admin releases.

Le 401/403 est silencieux dans le build EAS sauf si on regarde le log gradle complet — par défaut EAS skip les sourcemap upload errors comme « non-fatal » et le build continue.

**Solution propre :**
Régénérer le token Sentry avec les bonnes scopes :
- URL : `https://sentry.io/settings/account/api/auth-tokens/`
- Click « Create New Token »
- Scopes à cocher : `event:read`, `event:admin`, `project:read`, `project:releases`, `org:read` (les 3 derniers étant les nouveaux)
- Copier le token immédiatement (Sentry ne le re-affiche jamais)

Puis push dans EAS env (preview + production) :
```bash
# Si SENTRY_AUTH_TOKEN existait déjà, delete d'abord :
eas env:delete --variable-name SENTRY_AUTH_TOKEN --environment preview --non-interactive 2>/dev/null || true
eas env:delete --variable-name SENTRY_AUTH_TOKEN --environment production --non-interactive 2>/dev/null || true

# Puis create :
eas env:create --name SENTRY_AUTH_TOKEN --value "<TOKEN>" --environment preview --visibility secret
eas env:create --name SENTRY_AUTH_TOKEN --value "<TOKEN>" --environment production --visibility secret

# Et aussi seeder Keychain pour agent-mcp :
uv run --package ratis-agent-mcp agent-mcp keychain set sentry
```

**Workaround temporaire (déjà appliqué wave 6) :** `SENTRY_DISABLE_AUTO_UPLOAD=true` dans l'env EAS preview pour ne pas bloquer les builds. À retirer après le fix permanent.

**Leçon transverse :**
Les Sentry auth tokens ont une matrice scopes/endpoints qui n'est documentée que par essai. À chaque fois qu'on ajoute un Sentry plugin (gradle, webpack, Bun, etc.), checker les scopes du plugin avant de créer le token. Garder en tête que le scope par défaut « event:read » suffit pour l'ingestion runtime mais **pas** pour les operations admin (releases, deploys, source maps).

Token rotation policy (DA-43-ish) : chaque token Sentry doit être tagué dans `~/.config/ratis-agent-mcp/tokens.env` ou dans une note interne avec les scopes inclus, pour permettre l'audit en cas de leak.

**Mots-clés :** Sentry, auth token, project:releases, org:read, event:read, sourcemap upload, gradle plugin, EAS build, 401, 403, SENTRY_DISABLE_AUTO_UPLOAD, symbolicate, stack trace, JS bundle, index.android.bundle, agent-mcp keychain, sentry-tools, KP-85

**Découverte :** Sentry CLI gradle plugin log lors du build EAS preview wave 6 (14 mai 2026). PR #443 avait introduit un import nouveau dans `route-map.tsx` qui aurait dû générer une release fresh — mais le sourcemap upload silencieux a empêché la création. Diagnostiqué via `eas build:view <id> --logs` puis grep `releases`.

---

## KP-86 — `expo-build-properties` plugin n'expose pas `googleMapsApiKey` → injecter via `app.config.ts` overlay

**OBSOLETE 2026-05-25 (DA-46)** — `app.config.ts` a été **supprimé** lors du revert Google Maps → MapLibre + MapTiler. Google Maps abandonné (billing GCP inactivable), donc plus aucune clé Google à injecter via overlay. MapTiler passe par `EXPO_PUBLIC_MAPTILER_KEY` (runtime JS, pas d'overlay natif). KP conservé pour l'historique : le pattern overlay reste valide si on doit un jour injecter un autre champ natif dépendant de `process.env`.

**RÉSOLU 2026-05-14** (PR #446) — Pattern overlay `app.config.ts` mis en place pour injection per-platform de `GOOGLE_MAPS_API_KEY_IOS` et `GOOGLE_MAPS_API_KEY_ANDROID`.

**Symptôme :**
Lors de la migration MapLibre → `react-native-maps` + Google provider (PR #444), première intention : config dans `app.json` via le plugin `expo-build-properties` qui exposait historiquement la majorité des build settings natifs (compileSdkVersion, etc.). Pas de champ `googleMapsApiKey` ni `googleMaps` dans la signature du plugin → le SA dev a perdu 30 min à essayer plusieurs syntaxes (`ios.config.googleMapsApiKey`, `android.config.googleMaps.apiKey`, etc.) avant de réaliser que le plugin ne supporte pas ces champs.

**Cause racine :**
Le plugin `expo-build-properties` v0.x couvre les **build properties** (versions SDK, frameworks linking, NDK config, etc.) mais **PAS** les **app config** (Info.plist iOS / AndroidManifest.xml Android). Les champs natifs `GMSApiKey` (iOS Info.plist) et `com.google.android.geo.API_KEY` (Android meta-data) sont des app config, pas des build properties. Le plugin de pose pas la frontière clairement.

Confusion historique aggravée par la doc Expo qui suggère « use expo-build-properties for any native build config » sans préciser le périmètre — la frontière est :
- `app.json` ou plugin natif → fields qui finissent dans `Info.plist`, `AndroidManifest.xml`
- `expo-build-properties` → fields qui finissent dans `Podfile`, `build.gradle`

**Solution propre :**
Utiliser un overlay `app.config.ts` à la racine du projet Expo, qui hérite de `app.json` via `ConfigContext` et injecte les champs dynamiques à partir de `process.env` :

```typescript
// ratis_client/app.config.ts
import type { ConfigContext, ExpoConfig } from 'expo/config';

export default ({ config }: ConfigContext): ExpoConfig => {
  const legacyKey = process.env.GOOGLE_MAPS_API_KEY ?? '';
  const iosKey = process.env.GOOGLE_MAPS_API_KEY_IOS ?? legacyKey;
  const androidKey = process.env.GOOGLE_MAPS_API_KEY_ANDROID ?? legacyKey;

  return {
    ...config,
    name: config.name ?? 'ratis_client',
    slug: config.slug ?? 'ratis_client',
    ios: {
      ...config.ios,
      config: {
        ...(config.ios?.config ?? {}),
        googleMapsApiKey: iosKey,  // → Info.plist GMSApiKey
      },
    },
    android: {
      ...config.android,
      config: {
        ...(config.android?.config ?? {}),
        googleMaps: {
          ...(config.android?.config?.googleMaps ?? {}),
          apiKey: androidKey,  // → AndroidManifest meta-data
        },
      },
    },
  };
};
```

`app.json` reste committé pour les champs statiques (plugins, permissions, scheme, icons). `app.config.ts` overlay au-dessus pour tout ce qui dépend de `process.env`. Les deux co-existent — Expo lit `app.json` puis applique l'overlay `.ts`.

Keys per-platform : pour permettre les restrictions Google Cloud Console (Android pkg+SHA-1 OU iOS bundle id, jamais les deux sur la même clé). Fallback `GOOGLE_MAPS_API_KEY` pour early-alpha.

**Leçon transverse :**
Distinguer dès la conception **build properties** (Podfile / build.gradle / SDK versions) vs **app config** (Info.plist / AndroidManifest meta-data) :
- Build props → `expo-build-properties` plugin
- App config → `app.json` (statique) ou `app.config.ts` (dynamique env-driven)

Pour tout futur secret qui doit aller dans `Info.plist` / `AndroidManifest` (analytics keys, push tokens, deep-link schemes avec env), utiliser le pattern `app.config.ts` overlay. Ne pas tenter `expo-build-properties` — il refusera silencieusement les champs hors scope (pas d'erreur, juste pas d'injection).

**Mots-clés :** expo-build-properties, app.config.ts, ConfigContext, ExpoConfig, GOOGLE_MAPS_API_KEY, googleMapsApiKey, googleMaps.apiKey, Info.plist, GMSApiKey, AndroidManifest, com.google.android.geo.API_KEY, build properties vs app config, env injection, per-platform keys, react-native-maps, PROVIDER_GOOGLE, PR #444, PR #446, ARCH map provider switch

**Découverte :** Migration MapLibre → react-native-maps (PR #444) — SA dev a perdu 30 min à essayer plusieurs syntaxes via `expo-build-properties` avant de réaliser le périmètre du plugin. Pattern `app.config.ts` overlay validé via PR #446 avec PO review (split per-platform pour les restrictions GCP Console).

---

## KP-87 — libmagic 5.x ne détecte pas WebP de façon fiable, même sur bytes Pillow valides → 422 silencieux sur upload `image/webp`

**RÉSOLU 2026-05-14** (PR #448 — Bug 8) — fallback signature manuelle 12-byte dans `validate_image_upload`.

> Note : KP-87 + KP-88 ont été initialement catalogués le 2026-05-14 puis **perdus dans un rebase de SA parallèle** (le SA FE a `git rebase --onto` la branche BE pour nettoyer un commit cross-contaminé — cf KP-30/KP-35 — et a emporté les KP au passage). Re-catalogués le 2026-05-15.

**Symptôme :** `test_webp_accepted` (`webservices/ratis_product_analyser/tests/test_scan_receipt.py`) rouge sur main depuis l'alpha. POST `/api/v1/scan/receipt` content_type `image/webp` + body WebP valide → 422 `unsupported_file_type` alors que `image/webp` est dans `_BASE_MIME`.

**Cause racine :** `file-5.41` (libmagic shippé macOS + nombreuses distros Linux) ne reconnaît pas WebP de façon fiable — `magic.from_buffer(webp_bytes, mime=True)` retourne `application/octet-stream` au lieu de `image/webp`, **même sur des bytes WebP réels** générés par Pillow (44 bytes, chunk VP8 valide). Le support WebP existe dans libmagic depuis 5.37 mais nécessite les patterns sub-chunk VP8/VP8L/VP8X dans la magic DB compilée — souvent omis. Dans `ratis_core/ratis_core/uploads.py`, `magic.from_buffer` ligne ~70 retourne octet-stream → check `real_mime not in allowed` → 422.

Asymétrie : JPEG/PDF/EXE ont des magic bytes uniques → libmagic OK direct. WebP est un conteneur RIFF générique (partagé WAV/AVI/ANI) → libmagic n'émet `image/webp` qu'avec le bon chunk.

3 SAs précédents avaient grep "webp" sans run le test → assumé un env CI issue. Un 4e SA a blâmé la fixture → orchestrator a testé empiriquement `magic.from_buffer` sur bytes Pillow réels → confirmé que libmagic fail.

**Solution propre :** fallback signature manuelle 12-byte (`_looks_like_webp` à `uploads.py:18` — `RIFF` offset 0 + `WEBP` FourCC offset 8) déclenché UNIQUEMENT quand `declared_mime == 'image/webp'` ET libmagic retourne autre chose. Safe : un WAV/EXE smuggled échoue au check FourCC. Tests de sécurité : `test_spoofed_exe_as_webp_returns_422` + `test_spoofed_wav_as_webp_returns_422`.

**Leçon transverse :** `magic.from_buffer()` n'est pas exhaustif — la version libmagic + la magic DB compilée importent. Quand un format légitime n'est pas reconnu, tester `magic.from_buffer(real_bytes)` interactivement avant de blâmer la fixture. Nouveau format ajouté à `_BASE_MIME` → test intégration end-to-end du validator, pas juste un check de la liste allowed.

**Mots-clés :** libmagic, file-5.41, python-magic, magic.from_buffer, image/webp, RIFF, WEBP, VP8, FourCC, signature fallback, _looks_like_webp, validate_image_upload, uploads.py, 422 unsupported_file_type, application/octet-stream, Pillow, test_webp_accepted, Bug 8, PR #448, RÉSOLU 2026-05-14

**Découverte :** Test rouge depuis l'alpha, root-causé empiriquement par l'orchestrator le 2026-05-14.

---

## KP-88 — Test endpoint avec fixture `client` : `db.commit()` explicite requis sous peine de fail cryptique sur `assert_no_pending_changes`

**RÉSOLU 2026-05-14** (découvert pendant PR #449, fixé inline). Re-catalogué 2026-05-15 (cf note KP-87 sur la perte rebase).

**Symptôme :** un test endpoint qui seede des données via `db.add() + db.flush()` (sans `db.commit()`) échoue avec une erreur cryptique de `assert_no_pending_changes` après `client.get(...)` — pas un 404, pas un assert miss explicite.

**Cause racine :** l'autouse fixture `assert_no_pending_changes` (cf conftest, cousin KP-11/KP-47) détecte les writes non-committés en fin de test. **Mais elle ne fire QUE quand un HTTP client est dans `request.fixturenames`** :
- Test pure-service (no client) → flush suffit, transaction rollback au teardown, pas de check.
- Test endpoint (fixture `client`) → la fixture est active → uncommitted writes flag le test failed.

Aggravant : le `TestClient` utilise une session distincte pour les requêtes. Un seedage non-committé n'est pas visible depuis la session du request handler → l'API voit « 0 rows » alors qu'on vient d'en seeder 5. Aspect double-session qui rend le bug confusing.

**Solution propre :** pattern à documenter dans `SA_DEV.md` § Route-test seeding contract :
```python
def test_endpoint_xxx(client, user, db):
    _make_product(db, "...")
    db.flush()
    db.commit()           # OBLIGATOIRE quand `client` est in-scope
    resp = client.get(...)
```
Pour les tests service (sans `client`), `flush()` seul suffit. Pattern existant : `test_product_search.py`.

**Leçon transverse :** les autouse fixtures conditionnelles (fire seulement si une autre fixture est présente) créent des comportements de test asymétriques. Documenter explicitement la condition + le pattern, surtout quand le message d'erreur de violation n'est pas évident.

**Mots-clés :** assert_no_pending_changes, client fixture, db.commit, db.flush, TestClient, seeding, route test, endpoint test, conftest, autouse fixture, request.fixturenames, double-session, KP-11 cousin, KP-47 cousin, PR #449, SA_DEV.md to-update

**Découverte :** SA dev Phase 1 default-search PR #449 — test endpoint failait après seedage flush-only.

---

## KP-89 — `products.source` CHECK constraint : impossible de tester un filtre SQL défensif sur `user_suggested`

**OUVERT — limitation connue, pas de fix (par construction).**

**Symptôme :** en écrivant un test pour le filtre défensif `WHERE source != 'user_suggested'` (dans `incomplete_service.py` / `product_search`), le SA a voulu insérer un `Product(source="user_suggested")` en test DB → échec niveau DB, la contrainte CHECK refuse la valeur. Le test du filtre est donc impossible à écrire.

**Cause racine :** `ratis_core/ratis_core/models/product.py` (~ligne 112) — `CheckConstraint("source IN ('off', 'obp', 'opf', 'opff', 'internal')", name="source_check")`. La valeur `user_suggested` n'est PAS dans l'enum autorisé. Le `create_all` du test DB applique cette contrainte → insérer un row `user_suggested` raise `IntegrityError`, impossible même via tricks ORM.

Le filtre `source != 'user_suggested'` est donc un **garde-fou défensif future-proof** (au cas où `user_suggested` serait ajouté à l'enum un jour) mais **non-testable** en l'état.

> Side-note : drift détecté — `db/schema.sql:1308` dit `source IN ('off','internal')` (2 valeurs) alors que le modèle dit 5. Cousin Pattern A (ORM↔schema drift) — à investiguer séparément.

**Solution propre :** pas de fix — c'est une limite par construction. Discipline : quand on voit un filtre SQL défensif contre une valeur que la contrainte DB interdit, **savoir que le filtre n'est pas testable directement** et ajouter un commentaire le disant (miroir du pattern dans `product_search` SQL). Ne pas perdre du temps à essayer de le tester.

**Leçon transverse :** un filtre SQL défensif contre une valeur que le schéma interdit est un dead-code-de-sécurité légitime mais non-couvrable par les tests. Le documenter inline pour éviter qu'un futur SA s'acharne dessus.

**Mots-clés :** products.source, source_check, CHECK constraint, user_suggested, filtre défensif, incomplete_service, product_search, IntegrityError, test non-couvrable, future-proof, PR #453

**Découverte :** SA dev Phase 1 BE Compléter (PR #453) — test `test_excludes_user_suggested_source` droppé car non-insérable.

---

## KP-90 — Forward-ref string annotation (`-> "User"`) + import function-scoped = ruff F821

**RÉSOLU 2026-05-14** (PR #453 — import hoisté au niveau module).

**Symptôme :** CI ruff échoue avec `F821 undefined name 'User'` sur une fonction qui a un import function-scoped + une annotation de retour en string forward-ref :
```python
def _make_user(...) -> "User":
    from ratis_core.models.user import User
    ...
```

**Cause racine :** l'annotation forward-ref string `"User"` est résolue au runtime (par les outils qui font de l'introspection) — pas seulement par les type-checkers. Quand l'import de `User` est différé dans le corps de la fonction, le nom `"User"` n'est pas résolvable au scope module → ruff F821 (`undefined name`).

**Solution propre :** 2 options propres :
1. Hoister l'import au niveau module (`from ratis_core.models.user import User` en haut du fichier) — simple, retenu en PR #453.
2. `from __future__ import annotations` + `if TYPE_CHECKING: from ... import User` — pour les imports purement type-only (évite l'import runtime).

**Leçon transverse :** une annotation forward-ref `-> "ClassName"` ne marche proprement que si la classe est importée au scope module (ou sous `TYPE_CHECKING` avec `from __future__ import annotations`). Différer l'import dans le corps de la fonction tout en gardant l'annotation string = F821.

**Mots-clés :** ruff, F821, undefined name, forward-ref, string annotation, function-scoped import, TYPE_CHECKING, from __future__ import annotations, _make_user, PR #453

**Découverte :** SA dev Phase 1 BE Compléter (PR #453) — caught par CI ruff, fixé via hoist.

---

## KP-91 — `gh pr merge --delete-branch` clashe avec les worktrees partagés

**OUVERT — workaround documenté.**

**Symptôme :** `gh pr merge <num> --squash --delete-branch` échoue (ou laisse un état git incohérent) quand un autre worktree a déjà `main` checked-out. `gh` tente un `git checkout main` local pour mettre à jour HEAD post-merge → échoue car `main` est déjà occupé par un worktree.

**Cause racine :** `gh pr merge --delete-branch` fait, après le merge remote, un `git checkout` local de la branche de base pour synchroniser. Git interdit de checkout une branche déjà active dans un autre worktree (`main` ne peut être checked-out qu'à un seul endroit). Quand des SA tournent en `isolation: "worktree"`, le main checkout occupe `main` → le `gh` du SA clashe.

**Solution propre :** ne PAS utiliser `--delete-branch`. Séquence en 2 temps :
```bash
gh pr merge <num> --squash          # merge remote, pas de checkout local
git push origin --delete <branch>   # supprime la branche remote séparément
```

**Leçon transverse :** dès qu'on bosse en worktrees multiples (SA parallèles), les commandes `gh`/`git` qui font un checkout implicite de la branche de base peuvent clasher. Préférer les formes qui n'impliquent pas de checkout local (`gh pr merge` sans `--delete-branch`, `git push origin --delete` séparé).

**Mots-clés :** gh pr merge, --delete-branch, worktree, git checkout, branche occupée, isolation worktree, SA parallèle, KP-30 cousin, KP-35 cousin, PR #455

**Découverte :** SA FE Compléter (PR #455) en worktree isolé — `gh pr merge --delete-branch` a clashé avec le main checkout.

---

## KP-92 — Lib carto native-only (`react-native-maps`, MapLibre, …) casse l'export web → `eas update` doit passer `--platform android,ios`

**TOUJOURS VALIDE après revert DA-46 (2026-05-25)** — le piège est **générique à TOUTE lib carto native-only**, pas spécifique à `react-native-maps`. Le revert Google Maps → **MapLibre Native** (`@maplibre/maplibre-react-native`, native-only lui aussi) ne change rien : `platforms: ["ios", "android"]` dans `app.json` **reste en place** et reste nécessaire. La chaîne d'import est la même : `app/(tabs)/liste.tsx` → `components/liste/route-map.tsx` → lib carto native-only → modules React Native internes absents sur web.

**RÉSOLU 2026-05-15** — root cause : `platforms: ["ios", "android"]` ajouté à `app.json` (web n'est plus une target → `expo export`/`eas update` ne bundlent plus web du tout). Workaround initial `--platform android` conservé en ceinture+bretelles. Fix conservé tel quel au revert DA-46.

**Symptôme :** `eas update --channel preview` (sans `--platform`) échoue. Le bundle Android et iOS réussissent, mais le bundle **web** crash :
```
Web Bundling failed — Importing native-only module
"react-native/Libraries/Utilities/codegenNativeCommands" on web
from node_modules/react-native-maps/lib/MapMarkerNativeComponent.js
```

**Cause racine :** `eas update` exporte par défaut `--platform=all` (Android + iOS + **web**). `react-native-maps` (introduit PR #444, switch MapLibre→Google) est native-only — il importe des modules internes React Native (`codegenNativeCommands`) qui n'existent pas sur web. Chaîne : `app/(tabs)/liste.tsx` → `components/liste/route-map.tsx` → `react-native-maps` → module native-only. Ratis ne ship pas de target web (app mobile Expo), donc bundler web est inutile ET impossible avec cette dépendance.

**Solution propre (appliquée) :** déclarer les targets dans `ratis_client/app.json` :
```json
"platforms": ["ios", "android"]
```
Le bloc `web` (mort) a été retiré. `expo export` (donc `eas update`) ne build que les plateformes déclarées → web n'est plus tenté, le crash est structurellement impossible. Plus besoin du flag `--platform` à chaque OTA. Le flag reste utilisable en ceinture+bretelles mais n'est plus nécessaire.

**Leçon transverse :** toute dépendance native-only (`react-native-maps`, `@maplibre/maplibre-react-native`, et d'autres) casse l'export web. La parade structurelle est `platforms: ["ios", "android"]` dans `app.json` (web n'est plus une target). En ceinture+bretelles, tout `eas update` peut passer `--platform android,ios` explicitement — ne jamais s'appuyer sur le `--platform=all` par défaut tant qu'il n'y a pas de target web fonctionnel. Le piège (et son fix) est indépendant de la lib carto choisie : il survit au revert Google → MapLibre.

**Mots-clés :** react-native-maps, maplibre, @maplibre/maplibre-react-native, eas update, --platform, web bundling, codegenNativeCommands, native-only module, MapMarkerNativeComponent, route-map.tsx, --platform=all, platforms ios android, OTA wave-13, PR #444, DA-46, revert

**Découverte :** OTA wave-13 (2026-05-15) — `eas update` sans `--platform` a échoué sur le bundle web. Relancé en `--platform android`.

---

## KP-93 — Subagent en worktree isolé : le cwd du shell peut dériver vers le main checkout → commits sur la mauvaise branche

**OUVERT** — piège de discipline (pas de fix code) : mitigé en briefant explicitement chaque subagent.

**Symptôme :** un subagent dev dispatché avec `isolation: "worktree"` (ou briefé pour travailler dans un worktree) commit son travail sur `main` / une mauvaise branche au lieu de la branche feature. Découvert lors des PRs de hardening RW/LO : un commit a atterri sur `main` du checkout principal, récupéré ensuite depuis un commit-stash pendouillant.

**Cause :** `isolation: "worktree"` crée bien un worktree, mais le shell du subagent ne démarre pas forcément dedans — son cwd peut résoudre vers le checkout principal. Tout `Edit`/`git add`/`git commit` se fait alors hors du worktree. Aggravé par : le worktree auto-créé est sur une branche `worktree-agent-<id>` ; si le SA fait en plus `git checkout -b <branche-voulue>`, on obtient deux branches + un tracking remote ambigu.

**Mitigation (obligatoire dans CHAQUE brief de SA en worktree) :**
- Donner le **chemin absolu** du worktree et imposer `cd <chemin>` en première action.
- Faire vérifier `pwd` + `git branch --show-current` avant toute édition.
- Faire **re-vérifier `pwd` avant chaque `git commit`**.
Après durcissement des briefs sur ce modèle, les SAs suivants de la session (PostGIS Tasks 2-9 + décisions d'audit) n'ont plus dérapé.

**Nettoyage associé :** un worktree verrouillé refuse `git worktree remove` → `git worktree remove <path> -f -f` puis `git worktree prune`, et `git branch -D` seulement après (cf KP-91).

**Mots-clés :** subagent, worktree, isolation worktree, cwd, git branch --show-current, commit mauvaise branche, worktree-agent, dangling stash, git worktree remove -f -f, brief SA, KP-91 cousin, KP-30 cousin, KP-35 cousin

**Découverte :** session 2026-05-15/16 — SAs hardening RW/LO en worktree, commit égaré sur main récupéré ; briefs durcis ensuite.

---

## KP-94 — Fixtures de tests à dates absolues → flake au passage de minuit (`purchased_not_future`)

**OUVERT** — flake de tests : fixtures à corriger (dates relatives).

**Symptôme :** des suites de tests (`ratis_product_analyser`, `ratis_core` — `test_scan_receipt.py`, `test_receipt_task.py`, `test_persist.py`, `test_scans_constraints.py`, `test_cashback_retroactive.py`…) échouent par dizaines avec une violation de la CHECK `purchased_not_future`, mais **seulement à certaines heures** — typiquement quand la machine est en avance d'un jour sur l'horloge UTC du serveur Postgres de test (run autour de minuit UTC).

**Cause :** les fixtures insèrent des receipts avec un `purchased_at` **en date absolue** (`date.today()` côté host, ou une date codée en dur proche d'« aujourd'hui »). La CHECK `purchased_not_future` compare à `CURRENT_DATE` (horloge du serveur PG). Quand host et serveur sont à cheval sur minuit, la « date du jour » côté host est « demain » côté serveur → violation. En CI Linux Docker (conteneurs UTC cohérents) le flake est masqué la plupart du temps, mais un run lancé juste après minuit UTC le déclenche.

**Mitigation / fix recommandé :** dans les helpers de fixtures, dériver `purchased_at` d'une **date relative côté SQL** (`CURRENT_DATE - INTERVAL 'N days'`) plutôt que d'une date Python absolue, pour que la fixture et la CHECK partagent la même horloge.

**Mots-clés :** purchased_not_future, fixture, date absolue, date.today, CURRENT_DATE, flake, passage de minuit, horloge host vs serveur PG, test_scan_receipt, test_cashback_retroactive, dates relatives, audit 2026-05-17

**Découverte :** audit 2026-05-17 — plusieurs agents de correction ont vu ~300 échecs `purchased_not_future` en local, confirmés non liés à leurs changements (présents aussi sur `origin/main`).

---

## KP-95 — Parrainage au signup sans parcours filleul : `register()` supprimé, redemption de code non construite

**OUVERT** — feature manquante : un nouvel utilisateur OAuth ne peut pas être rattaché à un parrain au signup.

**Contexte :** L'endpoint `POST /register` était le seul consommateur de `referral_code` au signup (paramètre du body → création ligne `referrals` pending + appel `POST /rewards/referral/signup-bonus`). Cet endpoint a été supprimé dans le cadre du passage OAuth-only (DA-39 Phase 1). La redemption de parrainage — une UI permettant à un filleul de saisir le code qu'on lui a donné — n'a jamais été construite dans l'app livrée.

**Conséquence :** Un nouvel utilisateur qui s'inscrit via OAuth (Google ou Apple) ne dispose d'aucun moyen de saisir un code de parrainage. Les codes de parrainage existants (`referral_codes`) restent générés pour chaque user mais ne peuvent pas être utilisés par un filleul arrivant via OAuth.

**Recommandation :** Construire un parcours dédié de redemption de code parrainage, découplé du flow d'inscription. Options possibles :
- Champ code sur l'écran de login / post-OAuth (one-shot modal à la première connexion)
- Prompt one-shot post-signup (J+1 ou à la première ouverture de l'app)
- Deferred deep-linking (lien de parrainage qui survit à l'install)

Le choix du vecteur UX est à valider produit. Plan séparé requis — non bloquant Phase 1.

**Fichiers concernés :** `webservices/ratis_auth/routes/auth.py` (register supprimé), `webservices/ratis_auth/services/auth_service.py` (`register()` supprimé), `webservices/ratis_rewards/routes/referral.py` (`POST /rewards/referral/signup-bonus`), `ratis_core/models/referral.py`.

**Mots-clés :** parrainage, signup, referral_code, register supprimé, OAuth-only, filleul, redemption code, deep-link, DA-39, Phase 1, KP-95

---

## KP-96 — `n8n execute` (CLI) ne peut pas déclencher un workflow à Schedule Trigger

**OUVERT** — quirk n8n (pas de fix code) : connaître le comportement, smoke-tester autrement.

**Symptôme :** `docker exec ratis-itops-n8n n8n execute --id=<workflow>` sur un workflow dont le seul trigger est un Schedule Trigger (cron) échoue avec `CliWorkflowOperationError: Missing node to start execution — Please make sure the workflow you're calling contains an Execute Workflow Trigger node`.

**Cause :** la commande CLI `n8n execute` ne sait démarrer une exécution que depuis un nœud trigger « manuel » (Execute Workflow Trigger / Manual Trigger). Un Schedule Trigger (cron) ou un Webhook ne sont pas des points de départ valides. L'API publique n8n n'expose pas non plus d'endpoint « run workflow ».

**Mitigation :** pour smoke-tester un workflow cron/webhook, passer par l'UI n8n (bouton « Test workflow »). Ne pas compter sur `n8n execute` en CI/script pour ces workflows ; si un déclenchement scripté est indispensable, ajouter temporairement un Manual Trigger.

**Mots-clés :** n8n, n8n execute, CLI, Schedule Trigger, cron, Missing node to start execution, Execute Workflow Trigger, smoke test, Test workflow, daily-digest, OUVERT

**Découverte :** 2026-05-17 — smoke test du workflow `daily-digest` (cron 9h).

---

## KP-97 — docker-compose `environment:` est une whitelist : une var du `.env` n'atteint pas le container sans mapping explicite

**OUVERT** — piège infra (pas de fix code) : mapper chaque nouvelle env var.

**Symptôme :** une variable ajoutée à `infra/itops/.env` est absente dans le container (`docker exec <c> sh -c 'echo ${VAR:+set}'` → vide), alors que le `.env` est bien rempli.

**Cause :** quand un service docker-compose déclare un bloc `environment:` explicite (liste de clés), seules ces clés-là sont injectées dans le container. Le fichier `.env` ne sert qu'à la **substitution `${VAR}`** dans le YAML — il n'est PAS auto-déversé dans le container (ça, ce serait la directive `env_file:`). Une variable nouvellement ajoutée au `.env` reste donc invisible du process tant qu'elle n'est pas mappée dans `environment:`.

**Mitigation :** pour toute nouvelle env var consommée par un service, l'ajouter au bloc `environment:` du service dans `docker-compose.yml` (`VAR: ${VAR}`), pas seulement dans `.env` / `.env.example`. Puis recréer le container : `docker compose up -d --force-recreate <service>` (un simple `restart` ne suffit pas — il faut une recréation pour relire la config).

**Mots-clés :** docker-compose, environment, env_file, whitelist, .env, substitution ${VAR}, var absente du container, force-recreate, n8n, daily-digest, OUVERT

**Découverte :** 2026-05-17 — daily-digest : `N8N_API_KEY` / `N8N_DISCORD_DIGEST_WEBHOOK_URL` présents dans `.env` mais invisibles dans le container jusqu'au mapping `environment:`.

---

## KP-98 — Les commandes CLI `n8n` qui mutent l'état exigent un restart du container

**OUVERT** — quirk n8n (pas de fix code) : redémarrer après une commande CLI d'état.

**Symptôme :** après `docker exec ratis-itops-n8n n8n user-management:reset`, l'UI affiche toujours l'écran `/signin` au lieu de l'écran de création du compte owner. De même, `n8n update:workflow --id=<id> --active=true` affiche « Activation will not take effect if n8n is running. Please restart n8n ».

**Cause :** ces commandes CLI modifient bien la base SQLite (`user-management:reset` supprime l'owner et met `userManagement.isInstanceOwnerSetUp=false`), mais le process n8n en cours d'exécution garde son état chargé en mémoire (lu au boot). Le serveur ne « voit » pas la mutation faite par un process CLI séparé.

**Mitigation :** après toute commande CLI `n8n` qui mute l'état (`user-management:reset`, `update:workflow`, etc.), **redémarrer le container** (`docker restart ratis-itops-n8n` ou `docker compose up -d n8n`) pour que n8n relise la base.

**Mots-clés :** n8n, CLI, user-management:reset, update:workflow, restart container, isInstanceOwnerSetUp, signin, état en mémoire, activation workflow, OUVERT

**Découverte :** 2026-05-17 — récupération d'un compte owner n8n oublié + activation du workflow `daily-digest`.

---

## KP-99 — Double-head Alembic après merge de PR *stale* — détection (garde) + auto-réparation de `main`

**RÉSOLU 2026-05-18** (PR #510 garde `alembic_heads.yml` + PR #513 auto-heal `alembic_autoheal.yml`).

**Symptôme :** deux migrations Alembic branchées sur le même parent atterrissent toutes les deux sur `main` → `main` se retrouve avec **2 têtes** → `alembic upgrade head` échoue avec `Multiple head revisions are present for given argument 'head'`. Conséquence : la CI de **toutes les PR** est bloquée (le step migrations échoue partout) tant que `main` n'est pas réparé.

**Cause :** parallélisation de PR comportant chacune une migration. Le repo est **privé sur plan GitHub gratuit** → pas de branch-protection, pas de « require branches up to date before merging », pas de merge-queue. Une PR ouverte **avant** le merge d'une autre passe la garde CI au moment de son push, puis est mergée *stale* (sans re-push, donc sans réévaluation) → la garde ne se redéclenche jamais → collision de têtes sur `main`. Survenu **2× le 2026-05-18** (d'abord via #510, puis récidive via #511 mergée stale). C'est le cousin opérationnel de KP-45 : KP-45 décrit le piège DAG, KP-99 décrit comment il franchit la garde et comment `main` s'auto-répare.

**Détection (garde — PR #510) :** workflow `.github/workflows/alembic_heads.yml` — sur push d'une branche touchant `alembic/**`, superpose les migrations de la branche sur celles d'`origin/main` et échoue si le nombre de têtes ≠ 1. Doublé d'un check `alembic heads` en pré-flight de `scripts/test_migrations.sh`. **Limite connue :** la garde ne s'arme qu'au push — elle n'attrape PAS une PR mergée *stale* (mergée sans re-push après qu'une autre PR a bougé `main`).

**Réparation automatique (PR #513) :** workflow `.github/workflows/alembic_autoheal.yml` — sur `push:` vers `main` touchant `alembic/**`, si ≥ 2 têtes sont détectées → exécute `alembic merge heads` et pousse le commit de merge sur `main` sous l'identité `github-actions[bot]`. `main` s'auto-répare en ~1 min, zéro intervention humaine. **Pas de boucle infinie :** les commits créés avec le `GITHUB_TOKEN` par défaut ne re-déclenchent pas de workflow (et de toute façon le mécanisme est auto-terminant — une fois la migration de merge poussée, `main` n'a plus qu'une seule tête).

**Fix manuel** (si jamais nécessaire, hors auto-heal) :
```bash
alembic merge heads -m "merge <head-A> + <head-B>"
```
Génère une migration de merge dont le `down_revision` est le tuple des têtes, avec `upgrade()` / `downgrade()` vides — aucune mutation de schéma, simple jonction du DAG.

**Angle mort à connaître :** l'auto-heal répare le **graphe** de révisions (migration de merge vide reliant les têtes). Il ne résout **PAS** un conflit **sémantique** — si deux migrations modifient la même table de façon incompatible, le merge produit un graphe linéaire mais une logique cassée. Dans ce cas, trancher à la main : choisir/réécrire les migrations conflictuelles.

**Mots-clés :** alembic, multiple head revisions, double-head, dual head, down_revision, merge migration, alembic merge heads, parallel PR, stale PR merge, branch protection, free GitHub plan, alembic_heads.yml, alembic_autoheal.yml, auto-heal, self-hosted runner, GITHUB_TOKEN, PR #510, PR #513, RÉSOLU 2026-05-18

---

## KP-100 — TOCTOU sur `link_provider` / `unlink_provider` : check-then-act non atomique → `IntegrityError` brut au lieu de 409

**OUVERT** — risque accepté (probabilité pratique faible) : à durcir lors d'une future passe.

**Symptôme :** sous deux requêtes concurrentes du **même utilisateur**, les endpoints de gestion d'identités OAuth peuvent renvoyer une `500 Internal Server Error` (`IntegrityError` non rattrapé) au lieu de l'erreur métier propre attendue.
- `POST /api/v1/account/link-provider` : deux requêtes simultanées liant la même identité → la seconde viole la contrainte `UNIQUE(provider, provider_id)` de `user_identities` → `IntegrityError` brut → 500, au lieu d'un `409 identity_already_linked`.
- `DELETE /api/v1/account/identities/{provider}` : deux unlinks concurrents sur un compte à 2 identités → les deux peuvent franchir le garde « dernière identité » avant qu'aucun n'ait commit → le compte se retrouve à 0 identité, verrouillant le user dehors (login ne résout que par `user_identities`, DA-45).

**Cause :** `auth_service.link_provider` fait un `identity_repo.get_by_provider` (check) puis un `identity_repo.create` séparé (act) — fenêtre TOCTOU entre les deux. Idem `auth_service.unlink_provider` : `count_for_user` (check) puis `delete_for_user` (act). Aucun verrou ni gestion de course entre le check et le mutate. C'est le cousin « identités » de KP-41 (pattern défensif : tout INSERT sur une contrainte UNIQUE de user-data doit anticiper la concurrence).

**Pourquoi accepté tel quel :** la course n'est déclenchable que par des requêtes **concurrentes du même utilisateur** (le linking exige le Bearer du compte) — pas un vecteur d'attaque cross-user, et un humain ne double-tape pas « lier mon compte » assez vite en pratique. Le coût d'un durcissement immédiat (verrou + tests de concurrence) n'était pas justifié pour Phase 2.

**Fix recommandé (future passe de durcissement) :** dans `auth_service.link_provider`, entourer le `create` d'un `try/except IntegrityError` qui re-lève `LinkConflictError("identity_already_linked")` (la route mappe déjà cette exception en 409). Pour `unlink_provider`, soit un `pg_advisory_xact_lock(hashtext(str(user_id)))` en tête de transaction (sérialise les mutations d'identité d'un même user — cf. KP-41), soit un `DELETE … WHERE` conditionné sur un `count` recalculé en SQL dans le même statement, avec vérification du `rowcount`.

**Fichiers concernés :** `webservices/ratis_auth/services/auth_service.py` (`link_provider`, `unlink_provider`), `webservices/ratis_auth/repositories/` (`user_identity` repository — `get_by_provider`, `create`, `count_for_user`, `delete_for_user`), `webservices/ratis_auth/routes/account.py` (`POST /link-provider`, `DELETE /identities/{provider}`).

**Mots-clés :** TOCTOU, race condition, check-then-act, link_provider, unlink_provider, user_identities, UNIQUE provider provider_id, IntegrityError, LinkConflictError, identity_already_linked, cannot_unlink_last_identity, advisory lock, pg_advisory_xact_lock, account/identities, link-provider, DA-45, OAuth Phase 2, KP-41 cousin, OUVERT

---

## KP-101 — Flake jest sous charge : `waitFor` plafonne à 1s, indépendant de `testTimeout`

**Symptôme :** des tests jest passent en local en <100ms mais virent rouge en CI sur le Mac mini self-hosted (ex `useProductByEan › exposes error state on 404`, `card.test.tsx`). Plusieurs suites *sans rapport* échouent dans le même run — signature d'un flake sous charge, pas d'une régression.

**Cause racine :** `@testing-library` `waitFor`/`findBy*` ont leur propre `asyncUtilTimeout` (défaut **1s**), **indépendant** du `jest.config.js` `testTimeout: 15000`. Sur le Mac mini saturé (16 runners + Hermès + GlitchTip), la résolution async dépasse 1s → faux rouge.

**Règle :** `configure({ asyncUtilTimeout: 5000 })` dans `ratis_client/jest.setup.js`. `waitFor` rend la main dès que la condition est vraie → zéro ralentissement des tests verts. **Avant de conclure "régression"** sur un rouge jest : relancer / tester en local. Rouge multi-suites sans lien = flake.

**Fichiers concernés :** `ratis_client/jest.setup.js`.

**Mots-clés :** jest, flake, waitFor, asyncUtilTimeout, testTimeout, findBy, testing-library, self-hosted runner, Mac mini charge, faux rouge, useProductByEan, PR #594, RÉSOLU 2026-06-06.

**Découverte :** 2026-06-06 — jest rouge sur PR #573 (comment-only, ne peut pas casser jest) → flake sous charge, fix PR #594.

---

## KP-102 — Codex Plus (OAuth ChatGPT) pas conçu pour l'agentic 24/7

**Symptôme :** Hermès sur provider `openai-codex` (ChatGPT Plus) tombe : (a) `timeout` sur grosses sessions (postmortem 80k tokens > 5 min de stream), (b) `429 usage_limit_reached` puis credential reste flaggé `exhausted` après le reset, (c) `401 token_revoked` si un autre client (ChatGPT app, Codex CLI, VS Code) refresh les tokens du même compte.

**Cause racine :** OAuth ChatGPT Plus = usage humain interactif. Quota volume agressif, tokens device-bound invalidés au refresh cross-client, et Hermès ne re-clear pas le flag `exhausted` quand `last_error_reset_at` passe.

**Règle / mitigations :** postmortem lourd → routine Claude.ai multi-Explore (pas Codex, cf ARCH_hermes_ops HO-4) · flag collant → cron `auto-codex-reset` (band-aid HO-7) · **vrai fix propre (R33)** = `fallback_providers` (clé Anthropic/OpenRouter), différé faute de 2e clé · diagnostiquer en lisant les **erreurs récentes** (`timeout 300s` ≠ `usage_limit`).

**Fichiers concernés :** `~/.hermes/config.yaml`, `~/.hermes/scripts/auto-codex-reset.sh`, `infra/hermes/`.

**Mots-clés :** Hermès, Codex, openai-codex, ChatGPT Plus, OAuth, 429, usage_limit_reached, token_revoked, exhausted, timeout, fallback_providers, auto-codex-reset, postmortem, agentic, OUVERT (mitigé).

**Découverte :** 2026-06-01→06 — faux diagnostic "usage limit" alors que c'était un timeout client 300s.

---

## KP-103 — Worktree sur branche stale = skills `.claude/` invisibles

**Symptôme :** une session Claude Code dans un worktree ne "voit" qu'une partie des skills (ex 2 au lieu de 31) après `/reload-skills`, alors qu'ils sont bien sur `main`.

**Cause racine :** `.claude/skills/**` est **versionné dans git** (un-ignored). Une branche/worktree divergée de `main` **avant** l'ajout des skills ne les a pas — git normal. Ce n'est PAS "les worktrees n'ont pas de skills" : un worktree **frais depuis `origin/main`** les a tous.

**Règle :** créer les worktrees frais depuis `origin/main`. Branche longue-durée → `git merge origin/main` + `/reload-skills`. Le skill `repo-worktree-asset-sync-check` encode ce réflexe.

**Fichiers concernés :** `.gitignore` (`!.claude/skills/**`), workflow git.

**Mots-clés :** worktree, skills, .claude/skills, stale branch, reload-skills, behind main, versionné, repo-worktree-asset-sync-check, OUVERT.

**Découverte :** 2026-06-10 — reload-skills dans le worktree d'exploration Hermès montrait 2 skills (créé avant la promotion des 31).

**Découverte :** 2026-05-18 — implémentation OAuth-only Phase 2 (`user_identities`), revue du linking explicite.

**Découverte :** 2026-05-18 — double-head sur `main` survenu 2× le même jour (#510 puis récidive via #511 mergée stale).

---

## KP-104 — Langfuse `@observe` auto-initialise le SDK même clés vides → bruit d'export OTEL

**Symptôme :** une fonction décorée `@observe` (langfuse v4) logge `Failed to export span batch` au shutdown du process, alors que Langfuse est censé être désactivé (aucune clé configurée). Des threads d'export OTEL tournent sans raison.

**Cause racine :** `@observe` **auto-initialise** le client global langfuse au 1er appel, *même sans clés*. Ce client enregistre un span-processor OTEL dont l'exporter cible une URL invalide/cloud par défaut → échec d'export bruyant. Le no-op "naturel" (ne pas appeler `init_langfuse`) ne suffit pas : le décorateur agit tout seul.

**Règle :** sur **chaque** chemin no-op/refus de `init_langfuse`, poser le kill-switch natif du SDK `os.environ.setdefault("LANGFUSE_TRACING_ENABLED", "false")` → le décorateur redevient un vrai pass-through (0 thread, 0 réseau). `setdefault` pour que l'override opérateur gagne. Idem dans `conftest.py` pour des tests hermétiques. Cf `docs/arch/ARCH_llm_observability.md` § DA-LO6.

**Fichiers concernés :** `ratis_core/ratis_core/observability.py` (`_disable_langfuse_sdk`, `init_langfuse`), `webservices/ratis_product_analyser/tests/conftest.py`.

**Mots-clés :** langfuse, @observe, observe decorator, auto-init, OpenTelemetry, OTEL, span processor, exporter, Failed to export span batch, LANGFUSE_TRACING_ENABLED, kill-switch, no-op, clés vides, init_langfuse, AnthropicInstrumentor, DA-LO6, Celery worker prefork, shutdown, PR #607, RÉSOLU 2026-06-19.

**Découverte :** 2026-06-19 — wiring Langfuse tracing sur l'appel LLM OCR de `ratis_product_analyser` (PR #607).

---

## KP-105 — ruff `UP045`/`UP007` autofix breaks SQLAlchemy 2.0 `Mapped[...]` forward-refs

**Symptom:** after running ruff with the `Optional[X] → X | None` autofix enabled, the app crashes at mapper configuration time with `MappedAnnotationError` (SQLAlchemy can't resolve the column type). The model files look fine to a human reader, and the annotations are technically valid PEP 604 syntax — but the mapper rejects them.

**Root cause:** ruff `UP045` (and `UP007` for unions) rewrites `Optional["X"]` into `"X" | None`. Inside a SQLAlchemy 2.0 typed mapping that means `Mapped[Optional["X"]]` becomes `Mapped["X" | None]` — a string literal `"X"` `|`-ed with `None`. SQLAlchemy's annotation resolver expects either a fully-quoted forward-ref (`Mapped["X | None"]`) or a fully-resolved type; a *partially*-quoted union (`"X" | None`) is neither, so `Mapped.__class_getitem__` can't de-reference the forward-ref and raises `MappedAnnotationError` when the mapper configures. The autofix silently turns valid code into a runtime crash because the forward-ref quoting boundary moved.

**Rule:** quote the **whole** union as a single forward-ref — `Mapped["X | None"]`, never `Mapped["X" | None]` — AND add a `per-file-ignores` for `UP045`/`UP007` on `**/models/**` in the ruff config so the autofix can't re-break it on the next run. The `per-file-ignores` is the durable guard: without it the fix is one `ruff --fix` away from regressing. (Found and fixed during the quality-gates phase.)

**Files concerned:** `pyproject.toml` (ruff `[tool.ruff.lint.per-file-ignores]`), every `**/models/**` module using `Mapped[Optional[...]]` typed columns.

**Keywords:** ruff, UP045, UP007, Optional, X | None, PEP 604, autofix, SQLAlchemy 2.0, Mapped, MappedAnnotationError, forward-ref, forward reference, string annotation, partially-quoted union, mapper configuration, per-file-ignores, models, quality gates, OUVERT.

**Discovered:** 2026-06 — quality-gates phase; `ruff --fix` rewrote `Mapped[Optional["X"]]` to `Mapped["X" | None]`, breaking mapper config.
