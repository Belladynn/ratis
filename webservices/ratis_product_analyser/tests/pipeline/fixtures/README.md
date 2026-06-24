# Pipeline v3 — Oracle fixtures

Images réelles de tickets de caisse + DB state attendu après traversal complet.

Servent d'**oracle absolu** pour le contract test du pipeline (cf.
`webservices/ratis_product_analyser/ARCH_receipt_pipeline.md` § Contract test).

## Discipline

- Le test charge l'image, exécute le pipeline complet, compare le DB state contre
  `expected_*.json`. **Toute divergence = test rouge**.
- Si le pipeline régresse, le test rougit avant qu'on merge.
- Si l'expected change parce que les règles d'extraction évoluent légitimement,
  on **met à jour l'expected dans la même PR que le changement de pipeline** —
  jamais un changement d'expected sans changement code, jamais un changement code
  sans réviser l'expected.
- Anti-pattern : un test qui asserte le bug en cours est un faux contrat.
  Cf. ARCH § "Anti-patterns explicitement interdits" point 3.

## Fixtures actuelles

| Fichier                          | Source                                   | Caractéristiques                                    |
|----------------------------------|------------------------------------------|-----------------------------------------------------|
| `intermarche_courbevoie.jpg`     | Ticket alpha 2026-04-30, Intermarché Express Courbevoie 18 ter rue de Bezons | Cas réel qui foirait pré-v3 : adresse vide, store unknown, items partiellement extraits. Sert de smoke-test minimal de la pipeline complète. |

## RGPD

Les tickets de caisse ne contiennent **pas** de données personnelles user
(pas de nom, prénom, adresse résidence, e-mail, téléphone). Ce qu'ils contiennent :
- Date / heure / numéro de ticket
- Nom + adresse du magasin (donnée publique)
- Items + prix
- Numéro de carte fidélité (parfois — à anonymiser AVANT commit si présent)
- Mode de paiement (CB / espèces — pas de numéro de carte complet)

Avant d'ajouter une nouvelle fixture : **vérifier qu'aucun numéro de fidélité,
SIRET utilisateur, ou code-barres SCT (carte privative magasin) n'est lisible**.
Si présent, masquer la zone (rectangle noir) avant commit.

## Format du fichier `expected_<name>.json`

Décrit la DB state attendue après que l'image traverse les 4 phases du pipeline.
Sections obligatoires :

- `parsed_ticket` — state Phase 2 (image hash, OCR engine version)
- `scans[]` — chaque ItemMatch persisté (status, match_method, product_ean, rejected_reason)
- `store_match` — status (matched | suggested | unresolved)
- `pipeline_audit_log[]` — events clés émis par chaque phase (level=normal/production)

Voir ARCH § Contract test pour les détails.

Le scaffold initial (bloc 3) contient des **placeholders `FILL_ME_...`** documentés
par des clés voisines `_comment_<field>`. Ces commentaires guident le remplissage
manuel — ils sont supprimés du JSON final via `_strip_comments` côté test, donc
peuvent rester en place pendant tout le cycle de remplissage. Une fois la valeur
réelle fixée, supprime à la fois le `FILL_ME_...` et son `_comment_<field>` voisin
pour garder le JSON concis.

## Workflow de remplissage de l'expected_<name>.json

1. **Calculer l'image hash** :
   ```bash
   python -c "import hashlib; print(hashlib.sha256(open('intermarche_courbevoie.jpg','rb').read()).hexdigest())"
   ```
   Coller dans `parsed_ticket.raw_ticket_image_hash`.

2. **Vérifier la version PaddleOCR** installée dans le service (cf.
   `webservices/ratis_product_analyser/pyproject.toml` ou
   `worker/pipeline/ocr_engine.py`). Format conventionnel : `paddleocr-2.7.3-fr`.

3. **Inspecter visuellement l'image**. Lister tous les items attendus + le store
   visible. Pour chaque item, décider du `status` attendu selon les règles
   ARCH § Phase 4 contrat enum :
   - `matched` : on est sûr du match (barcode lu + product en DB OU label
     fuzzy_strict ≥ seuil)
   - `unresolved` : label OCR'd mais aucun candidat ≥ seuil → user doit
     barcode-scan (`rejected_reason='requires_user_barcode'`)
   - `rejected` : label illisible ou item parasite (header, footer, prix
     négatif, …) (`rejected_reason ∈ {no_qty, no_price, ocr_garbage, ...}`)

4. **Remplir tous les `FILL_ME_*`**. Supprimer les clés `_comment_*` voisines une
   fois la valeur fixée — elles servent de guide, pas de doc finale.

5. **Re-runner le contract test** :
   ```bash
   ./scripts/run-tests.sh webservices/ratis_product_analyser/tests/pipeline/test_contract.py -v
   ```
   Tant que les phases extract/comprehend/match/persist (blocs 4-7) ne sont pas
   livrées, le test SKIP via `pytest.importorskip` sur
   `worker.pipeline.orchestrator`. Le skip disparaîtra automatiquement le
   jour où le module landera ; aucune modif du test à prévoir.
