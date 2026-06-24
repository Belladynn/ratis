# RUNBOOK_MIGRATION — Migrer le dev-host Ratis vers une nouvelle machine

> **Quand utiliser ce runbook :** changement de machine de dev/orchestration ratis (ex: Windows → Mac mini, Mac → autre Mac, Mac → Linux server). À ne PAS confondre avec `ARCH_deployment.md` qui couvre la prod.
>
> **Source de vérité historique :** migration Windows → Mac mini M4 Pro arm64 le 2026-05-04 (PR #287) puis post-migration tooling le 2026-05-05.
>
> **Public visé :** toi (humain) + futurs orchestrateurs Claude qui doivent reprendre la main après changement de host.

---

## Pre-flight checklist (machine source = encore accessible)

Avant de couper l'ancienne machine :

- [ ] **Lister les credentials/tokens** stockés localement uniquement (PAT GitHub, EXPO_TOKEN, SENTRY_AUTH_TOKEN, ADMIN_API_KEY, etc.). Tout ce qui n'est pas dans le repo committed = à transférer ou regénérer. Voir § Tokens & secrets ci-dessous.
- [ ] **Lister les fichiers `.env*` non versionnés** (`ratis_client/.env.local`, racine `.env`, etc.). Ils ne sont PAS dans git → à transférer ou regénérer manuellement.
- [ ] **Lister les configurations d'outils tiers** : sentry-cli (`~/.sentryclirc`), gh (`~/.config/gh/hosts.yml`), Tailscale, EAS, etc.
- [ ] **Backup les bases dev locales** si pertinent (Postgres `ratis_dev`, Redis snapshot). Optionnel — souvent reseed plus simple.
- [ ] **Note les sessions IDE/éditeur ouvertes** (Cursor, VS Code workspaces, terminaux personnalisés).
- [ ] **Push toutes les branches en cours** (`git push --all`), même les WIP (`feat/wip-*`). Une branche locale orpheline = perte sèche.

---

## Setup de la nouvelle machine — checklist standard

### 1. Tooling base (Homebrew, git, dev essentials)

```bash
# macOS only — Homebrew
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"

# Tooling de base
brew install git gh uv node@22 docker python@3.12

# Initialise brew shellenv pour les futurs shells
echo 'eval "$(/opt/homebrew/bin/brew shellenv)"' >> ~/.zprofile
echo 'eval "$(/opt/homebrew/bin/brew shellenv)"' >> ~/.zshrc  # double couverture
```

⚠️ **Piège PATH macOS** : `~/.zprofile` est lu par les login shells zsh. `~/.zshrc` par les interactive shells. Et les outils non-zsh (mon Bash sandboxé Claude Code, certains terminaux IDE) lisent `~/.profile` ou ne lisent rien. **Mettre les exports critiques dans les DEUX fichiers** pour double couverture, sinon les nouveaux shells ne voient pas brew/eas/sentry-cli, et il faut systématiquement `source ~/.zprofile` dans chaque commande.

### 2. Clone repo + worktree dev

```bash
mkdir -p ~/Cursor && cd ~/Cursor
git clone git@github.com:Belladynn/ratis.git
cd ratis
uv sync                                              # bootstrap workspace Python 3.12
docker compose up -d postgres redis osrm             # stack dev
```

### 3. Restore secrets locaux

Copie depuis l'ancienne machine ou regénère :

- `ratis_client/.env.local` (URLs API dev pour le mobile EAS)
- `.env.prod` (NE PAS committer — chmod 600)
- Tokens shell dans `~/.zprofile` (voir § Tokens ci-dessous)

### 4. Auth aux services tiers

```bash
# GitHub
gh auth login                                        # SSH si remote SSH, HTTPS sinon

# EAS (mobile)
brew install --cask eas-cli                          # ou via npm si déjà setup
# Génère un PAT sur https://expo.dev/accounts/<org>/settings/access-tokens
# scopes : default (read+write project) — suffit pour eas update
echo 'export EXPO_TOKEN="<TOKEN>"' >> ~/.zprofile
chmod 600 ~/.zprofile

# Sentry
brew install getsentry/tools/sentry-cli
# Génère un PAT sur https://sentry.io/settings/account/api/auth-tokens/
# scopes : org:read + project:read + event:read (event:admin si tu veux résoudre les issues)
echo 'export SENTRY_AUTH_TOKEN="<TOKEN>"' >> ~/.zprofile
```

⚠️ **Pattern safe pour les secrets dans `.zprofile`** :
- `chmod 600 ~/.zprofile` toujours
- Ne JAMAIS `cat ~/.zprofile` quand un autre humain ou un agent peut voir ta sortie (incl. screen sharing, pair programming, transcripts conv Claude/Cursor)
- Pour vérifier qu'une var est set sans la révéler :
  ```bash
  [ -n "$EXPO_TOKEN" ] && echo "set (length=${#EXPO_TOKEN})" || echo "unset"
  # JAMAIS : echo "${EXPO_TOKEN:-default}" → leak si var set
  ```

### 5. Remote desktop & accès distants

Sur Mac mini desktop avec besoin remote :

- **Tailscale** (VPN mesh chiffré WireGuard) :
  ```bash
  brew install --cask tailscale
  open -a Tailscale       # → Sign in via app GUI menubar
  ```
  Active **MagicDNS** sur https://login.tailscale.com/admin/dns pour les URLs conviviales (`mac-mini-ratis` au lieu d'IP).

- **Screen Sharing** macOS (intégré, gratuit, VNC standard) :
  ```
  Réglages système → Général → Partage → Screen Sharing : ON
                                       → Clipboard sharing : ON
                                       → Only these users : ton compte
  ```

- **RustDesk** (alternative remote desktop avec H.264 hardware, meilleure perf que VNC) :
  ```bash
  brew install --cask rustdesk
  # Config : Direct IP via Tailscale (pas via serveurs publics RustDesk)
  # Mot de passe permanent + Mémoriser côté client
  # Voir § "Remote desktop perf" ci-dessous pour optimisations
  ```

### 6. Clavier (si tu viens d'un layout différent)

⚠️ **Karabiner-Elements ne fonctionne PAS via remote desktop** (VNC, RustDesk, TeamViewer). Karabiner intercepte au niveau driver USB/HID, et les clients remote injectent les keys via une API macOS plus haute (`CGEventCreateKeyboardEvent`) qui bypass complètement le HID. **Conséquence** : si tu te connectes au Mac mini depuis Windows/Linux et que tu veux remap Ctrl→Cmd, Karabiner ne verra rien.

**Solutions** :
- Si tu utilises un clavier physique branché AU Mac mini : Karabiner marche normalement.
- Si tu te connectes via remote desktop : remap côté client (PowerToys Keyboard Manager sur Windows, scope `RustDesk.exe` ou `vncviewer.exe` pour ne pas pourrir le reste de ton OS hôte).

**Layout AZERTY-Windows depuis Windows host** : sur le Mac mini, ajouter `Réglages → Clavier → Sources de saisie → "Français — PC"` → mapping AZERTY-Windows native, AltGr+0 = `@` etc.

### 7. Stack ITOps locale (Phase A + B mergées)

Une fois le repo cloné :

```bash
cd ~/Cursor/Ratis/infra/itops
cp .env.example .env
# Édite .env : SUPERUSER_EMAIL, SUPERUSER_PASSWORD, ports si conflits
docker compose up -d
```

Stack disponible :
- Healthchecks (cron monitor) : `http://localhost:8000`
- Watchtower (auto-update opt-in) : pas d'UI
- Uptime Kuma (HTTP/TCP probes) : `http://localhost:3001`
- Loki (logs queryable) : `http://localhost:3100`
- Promtail (logs collector) : pas d'UI

---

## Tokens & secrets — checklist exhaustive

| Secret | Source | Stockage post-migration |
|---|---|---|
| `EXPO_TOKEN` | https://expo.dev/accounts/<org>/settings/access-tokens | `~/.zprofile` chmod 600 |
| `SENTRY_AUTH_TOKEN` | https://sentry.io/settings/account/api/auth-tokens/ | `~/.zprofile` ou `~/.sentryclirc` (préféré) |
| GitHub auth | `gh auth login` (SSH ou HTTPS) | macOS Keychain via gh CLI |
| Tailscale auth | App GUI Sign in | Stocké par l'app Tailscale |
| `ratis_client/.env.local` | À regénérer ou transférer | Fichier, chmod 600, gitignored |
| `.env.prod` (si tu sers de bastion ops) | Transfert depuis ancien serveur | `/root/ratis/.env.prod` server-side, jamais sur poste dev |
| Stripe / Runa / R2 keys | Provider dashboards | `.env.prod` server-side OU futur ratis-agent-mcp Keychain |

**Long terme (planned) : `ratis-agent-mcp`** — un MCP server qui gère tous les tokens externes via macOS Keychain, et expose des outils typés à Claude. Voir `ARCH_agent_mcp.md` (post-merge).

---

## Pièges connus post-migration

### KP-57 — `eas update` sans `--environment <X>`
Toujours passer **les 2 flags `--channel <X> --environment <X>`** ensemble. Sinon les `EXPO_PUBLIC_*` du dashboard EAS ne sont pas inlinées dans le bundle → app crash au boot. Voir KP-57 dans `KNOWN_PROBLEMS.md`.

### EOL CRLF/LF post-migration
Si tu viens d'un host Windows, les fichiers locaux peuvent avoir CRLF. Le `.gitattributes` (PR #289) auto-normalise à LF au prochain `git add`. Pour les modifs locales pré-existantes, un `git stash && git stash pop` les normalise via `.gitattributes` actif. Voir KP-25 (`.env.prod` CRLF) et `.gitattributes` à la racine.

### Working tree pollué silencieusement
Lesson 2026-04-26 : un working tree pollué a silencieusement shippé un bundle cassé (login KO en prod). **TOUJOURS** `git fetch && git log -1 && git status` AVANT `eas update`. Vérifie HEAD SHA == origin/main HEAD SHA, pas seulement « pas de modif unstaged ». Voir CLAUDE.md § R34.

### Channel mismatch APK ↔ OTA
Lesson 2026-04-27 : 4h gaspillées à pousser sur `production` alors que l'APK alpha utilisateur était sur `preview`. **TOUJOURS** `eas build:list --limit 1 --platform=android` et lire le champ `Channel:` AVANT chaque `eas update`. Voir KP-32.

### Karabiner ne marche pas via remote desktop
Détaillé en § 6 ci-dessus. C'est une limitation architecturale, pas un bug. Solution : remap côté client (PowerToys, AHK).

### macOS Modifier Keys swap par device
Si tu utilises un clavier USB tiers (clavier Windows, Logitech, etc.) sur Mac mini : `Réglages → Clavier → Touches modifiantes → Select keyboard → choisir le device USB` permet de swap Ctrl/Cmd/Option/Caps **uniquement pour ce clavier**, pas pour le clavier intégré du Mac mini ou un Apple Bluetooth.

---

## Remote desktop — perf optimisations

Si tu te connectes au Mac mini depuis Windows/autre Mac/Linux :

### VNC (Screen Sharing intégré macOS)
- Réduire qualité dans le client VNC (RealVNC Viewer onglet Expert) : `ColourLevel=Medium`, `JPEGQuality=3`, `CompressLevel=9`, `AutoSelect=False`, `PreferredEncoding=ZRLE`
- Vérifier que Tailscale est en mode **direct** (pas DERP relay) : `tailscale ping mac-mini-ratis` doit afficher "direct LAN connection". Si "via DERP X" → activer UPnP sur ta box pour permettre le hole-punching peer-to-peer.

### RustDesk
- Réglages Display : Image quality `Best` (Tailscale = LAN-like), codec H.264 hardware, FPS 60
- Direct IP via Tailscale (Settings → Network) : zéro tiers, plus rapide, plus privé
- Mot de passe permanent + Mémoriser côté client → connexion 1-clic après le 1er login

---

## Validation post-migration

Au minimum, valide ces 5 trucs :

1. **Repo OK** : `cd ~/Cursor/Ratis && git status && python scripts/generate-arch-inventory.py` doit run sans erreur
2. **Stack dev** : `docker compose up -d` (Postgres, Redis, OSRM) → `curl localhost:5432` répond
3. **Stack ITOps** : `cd infra/itops && docker compose ps` montre 5+ services Up
4. **EAS** : `cd ratis_client && source ~/.zprofile && eas whoami` répond ton compte ratis
5. **Sentry** : `source ~/.zprofile && sentry-cli info` répond `User: contact@ratis.app`

Si les 5 passent → migration réussie, tu peux lancer un `eas update preview` test.

---

## Pointers

- `ARCH_deployment.md` — prod hosting (Hetzner V0, Mac mini transit, AWS terme)
- `KNOWN_PROBLEMS_INDEX.md` + `KNOWN_PROBLEMS.md` — pièges détaillés (KP-25, KP-57 surtout pour migration)
- `CLAUDE.md` — règles agents shared (R15 pytest timeout, R34 EAS discipline)
- `ARCH_agent_mcp.md` (post-merge) — futur MCP qui remplacera ce stockage manuel des tokens dans `.zprofile`
