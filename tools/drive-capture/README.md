# drive-capture — capture de prix « drive » (Phase 1)

Outil de seeding de données pour Ratis. L'opérateur navigue **lui-même,
manuellement**, sur un site drive par enseigne ; mitmproxy enregistre les
réponses porteuses de données (JSON, mais aussi HTML/XML pour les sites
server-rendered) que les sites renvoient à son navigateur, pour éviter la
saisie des prix à la main.

Capture **passive** de la propre session de navigation de l'opérateur :
pas d'automatisation, pas de crawl, pas de contournement d'anti-bot.

> ⚠️ Les CGU de certains drives interdisent l'extraction de leurs
> données. La capture manuelle reste à faible risque, mais c'est une
> décision produit assumée.

## Phase 1 (cet outil) vs Phase 2

- **Phase 1** — capture brute : on enregistre *tout* (JSON + HTML + XML),
  regroupé par hôte, sans interprétation. Objectif : récolter des
  échantillons réels — quelle que soit la techno du site (API JSON
  moderne ou page `.aspx` server-rendered type Leclerc Drive).
- **Phase 2** (à venir) — parsers par enseigne : à partir des JSON
  récoltés, on écrit l'extraction `(EAN, prix, magasin, …)`. Une fois les
  parsers au point, ils se brancheront dans le hook `response` de
  `capture_addon.py` pour capturer + normaliser en un seul passage.

## Setup (une fois)

1. **Installer mitmproxy** :
   ```bash
   uv tool install mitmproxy
   ```
2. **Générer le certificat CA** — lancer mitmproxy une première fois pour
   qu'il crée `~/.mitmproxy/` :
   ```bash
   mitmdump --version >/dev/null   # crée ~/.mitmproxy/ au premier run
   ```
3. **Faire confiance au certificat** (nécessaire pour intercepter le
   HTTPS). macOS :
   ```bash
   sudo security add-trusted-cert -d -r trustRoot \
     -k /Library/Keychains/System.keychain \
     ~/.mitmproxy/mitmproxy-ca-cert.pem
   ```
   Ou : naviguer sur `http://mitm.it` (navigateur proxifié) et suivre les
   instructions par OS.

## Capturer une session

1. Lancer le capteur (écoute sur `localhost:8080`) :
   ```bash
   cd tools/drive-capture
   mitmdump -s capture_addon.py
   ```
   Au démarrage, l'addon affiche l'**allowlist `domains.txt` active** (ou
   « capture de TOUS les hôtes » si absente) — vérifie qu'elle pointe bien
   l'enseigne que tu vas capturer.
2. **Proxifier le navigateur** vers `localhost:8080`. Recommandé : un
   navigateur / profil dédié, pour que seule la navigation drive passe
   par le proxy. Options :
   - Firefox : Préférences → Réseau → Configuration manuelle, HTTP/HTTPS
     `localhost:8080`.
   - Chrome : lancer avec `--proxy-server=localhost:8080`.
3. Naviguer sur les drives (un par enseigne), parcourir les rayons.
4. `Ctrl-C` sur `mitmdump` pour clore la session — l'addon affiche alors
   un **résumé** : nombre de réponses capturées par hôte (ou un
   avertissement si rien n'a été capté).

> **Changer de `domains.txt` = redémarrer `mitmdump`.** L'allowlist est
> lue une seule fois, au démarrage. Éditer `domains.txt` pendant que le
> capteur tourne n'a aucun effet.

## Sortie

```
tools/drive-capture/captures/<session-AAAAMMJJ_HHMMSS>/<hôte>.ndjson
```

Un fichier NDJSON par hôte, une ligne par réponse capturée. `content_type`
indique la nature ; `response_json` est rempli pour le JSON, `response_text`
pour le HTML/XML (et pour le JSON non parsable) :

```json
{"captured_at":"…","host":"drive.exemple.fr","method":"GET","url":"…","status":200,"content_type":"application/json","request_json":null,"response_json":{…},"response_text":null}
```

Le dossier `captures/` est **gitignored** — la donnée captée ne va pas
dans le dépôt.

## Filtrage (optionnel)

Par défaut le capteur prend **toutes** les réponses JSON, HTML et XML (le
bruit — analytics… — est isolé dans ses propres fichiers par hôte ; les
assets binaires, images/css/js, sont ignorés). Pour
restreindre, créer un `domains.txt` (un fragment d'hôte par ligne,
`#` = commentaire) — voir `domains.txt.example`.
