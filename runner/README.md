# Ratis self-hosted GitHub Actions runners

## Quick context

Cette stack fait tourner **16 runners GitHub Actions self-hosted** dans Docker sur le Mac mini M4 Pro 48 GB qui héberge la prod Ratis (depuis PR #287, 2026-05-04). Les runners exécutent toute la CI Ratis sur des **conteneurs Linux** (image `myoung34/github-runner:ubuntu-jammy`) — donc malgré l'host macOS arm64, la CI = ground truth Linux pour les merges.

Labels exposés à GitHub Actions : `self-hosted,linux,docker` (tous les runners partagent les mêmes labels — pas de séparation arm64/x86_64 aujourd'hui ; les workflows ciblent simplement `runs-on: self-hosted`). Les jobs CI tournent en parallèle dans des containers Docker (Docker-in-Docker via le service `dind` mutualisé).

## Architecture

```
┌──────── Mac mini M4 Pro (host) ────────────────────────────┐
│                                                            │
│  runner/docker-compose.yml                                 │
│  ┌──────────────────────────────────────────────────────┐  │
│  │  service `dind`     (docker:dind, privileged)        │  │
│  │  service `postgres` (postgres:16, ratis_test* DBs)   │  │
│  │  service `runner-1` … `runner-16`                    │  │
│  │     hostname/container_name: ratis-runner-N          │  │
│  │     RUNNER_NAME=ratis-runner-N                       │  │
│  │     stop_grace_period=120s (deregistration propre)   │  │
│  │     DOCKER_HOST=tcp://dind:2375                      │  │
│  └──────────────────────────────────────────────────────┘  │
│                                                            │
│  Volumes : dind_data (Docker layer cache),                 │
│            pg_data   (Postgres data)                       │
│                                                            │
│  Pas de label Watchtower → ignoré par l'auto-update        │
│  itops (cf DA-39 dans ARCH_itops.md).                      │
└────────────────────────────────────────────────────────────┘
```

Notes structure :
- **`dind` mutualisé** : un seul daemon Docker partagé par les 16 runners. Évite le coût de 16 daemons concurrents. Side-effect : si `dind` tombe, les 16 runners deviennent inutilisables → c'est le SPOF assumé Phase A.
- **`postgres` partagé** : initialise plusieurs bases isolées (`ratis_test_rewards`, `ratis_test_auth`, etc., voir `initdb/01-create-databases.sql`) pour éviter les conflits entre jobs CI parallèles d'un même service.
- **Noms fixes** : `RUNNER_NAME` + `container_name` + `hostname` figés à `ratis-runner-N` pour que les runners se réenregistrent avec la même identité après un restart. Évite l'accumulation de ghosts côté GitHub.

## Setup first-time

Pré-requis : Docker Desktop ou Colima installé sur le Mac mini, `gh` CLI authentifié.

1. **Créer le PAT GitHub** : Settings → Developer settings → Personal access tokens → Tokens (classic) → scope `repo`.
2. **Stocker le PAT dans le Keychain macOS** — pas de `.env`, le token ne touche jamais un fichier du repo (même pattern que `tools/agent-mcp`, cf [`ARCH_agent_mcp.md`](../ARCH_agent_mcp.md)) :
   ```bash
   security add-generic-password -a "$USER" -s ratis-runner-pat -U \
     -D "GitHub PAT for Ratis CI runners" -w "<paste-PAT>"
   ```
3. **Démarrer la stack** :
   ```bash
   cd runner
   ./start.sh
   ```
   `start.sh` lit le PAT depuis le Keychain (`security find-generic-password`) et le passe à `docker compose` comme `ACCESS_TOKEN`. Premier boot : ~30-60 s (pull image + enregistrement des 16 runners auprès de GitHub).
4. **Valider l'enregistrement** :
   ```bash
   gh api repos/Belladynn/ratis/actions/runners --jq '.runners[] | {name, status}'
   ```
   Attendu : 16 lignes `ratis-runner-1` … `ratis-runner-16` toutes en `status: online`.

## Day-to-day operations

| Action | Commande |
|---|---|
| Démarrer / relancer la stack | `./start.sh` (lit le PAT du Keychain) |
| Voir l'état des 16 runners | `docker compose ps` |
| Logs d'un runner spécifique | `docker logs -f ratis-runner-N` |
| Logs de toute la stack | `docker compose logs -f` |
| Restart un runner stuck | `docker restart ratis-runner-N` |
| Restart toute la stack (sans deregister) | `docker compose restart` |
| Stop propre + deregister | `docker compose down` (respecte `stop_grace_period=120s`) |
| Cleanup ghost runners (offline) | `bash ../scripts/cleanup-ghost-runners.sh --confirm` |
| Scale up/down | éditer `docker-compose.yml` (ajouter/retirer un bloc `runner-N`) puis `docker compose up -d` |

Pour ajouter un 17e runner : copier-coller le bloc `runner-16` en bas du `docker-compose.yml`, incrémenter chaque `-16` en `-17`, puis `docker compose up -d`. Le nouveau runner s'enregistre automatiquement auprès de GitHub.

## Décision DA-39 — itops ne touche pas cette stack

Volontairement, **les 16 runners n'ont PAS le label** `com.centurylinklabs.watchtower.enable=true`. Watchtower (déployé dans la stack `infra/itops/`) ignore donc complètement les containers runners. Raison : un restart inopiné mid-job casserait un build CI en cours. Les bumps d'image runner (`myoung34/github-runner:ubuntu-jammy`) se font **manuellement** par PR avec validation. Détails dans [`ARCH_itops.md` § Pourquoi itops ne touche pas la stack runners](../ARCH_itops.md#pourquoi-itops-ne-touche-pas-la-stack-runners-runnerdocker-composeyml-).

## Why 16 runners

Le sizing 16 runners (vs 4-8 historique) répond à plusieurs contraintes constatées sur le repo Ratis :
- **Concurrence des PRs** : sur une journée active (3-4 SAs en parallèle + reviews), on observe régulièrement 8-12 jobs CI en queue. À 4 runners, le P95 wait time dépassait 5 min ; à 16, il tombe sous 30 s.
- **Batch CI parallèle** : chaque service web (auth, PA, LO, RW, NT) lance pytest + ruff + bandit indépendamment. Une PR cross-service peut déclencher 5-8 jobs en simultané.
- **Workflows batch crons** : `.github/workflows/batch_*.yml` (8 batches) ajoutent un baseline de jobs récurrents qui occupent quelques runners en continu.
- **RAM disponible** : Mac mini M4 Pro 48 GB → 16 runners + dev stack + itops tient confortablement (chaque runner idle ≈ 200 MB, peak ≈ 1-2 GB sous pytest). Migration 2026-05-04 a relevé le plafond.

Si downgrade nécessaire (Mac mini saturé, futur host moins puissant), supprimer les blocs `runner-9` à `runner-16` dans `docker-compose.yml` puis `docker compose up -d` (les runners 9-16 se desinscrivent via `stop_grace_period`).

## Troubleshooting

**Runner offline dans GitHub UI** alors que `docker ps` montre le container running :
- Vérifier les logs : `docker logs ratis-runner-N | tail -50`. Souvent un PAT expiré — symptôme : `curl: (22) ... error: 401` au boot. Mettre à jour le Keychain (`security add-generic-password -a "$USER" -s ratis-runner-pat -U -D "GitHub PAT for Ratis CI runners" -w "<new-PAT>"`) puis `./start.sh`.
- Tester la connectivité GitHub depuis le container : `docker exec ratis-runner-N curl -sI https://api.github.com`.

**Runner stuck** (job qui ne démarre pas, queue bloquée) :
- `docker restart ratis-runner-N` suffit dans 90 % des cas.
- Si récurrent : `docker compose down && docker compose up -d` (reset complet, respecte la deregistration grâce à `stop_grace_period=120s`).

**Ghosts accumulés** (entrées `offline` qui s'empilent dans `gh api repos/Belladynn/ratis/actions/runners`) :
- Causes typiques : SIGKILL avant deregistration (`docker kill`, OOM), perte du `RUNNER_NAME` à un boot précédent, coupure brutale du host.
- Cleanup : `bash scripts/cleanup-ghost-runners.sh` (dry-run par défaut, `--confirm` pour delete réel).

**`dind` qui pète** (Docker-in-Docker daemon down) :
- Symptôme : tous les jobs CI échouent avec `cannot connect to Docker daemon at tcp://dind:2375`.
- `docker compose restart dind`. Si persiste : check `docker compose logs dind` pour OOM ou crash du daemon.

**Postgres saturé** (jobs CI qui timeout sur les fixtures DB) :
- `docker exec -it $(docker compose ps -q postgres) psql -U ratis -c "SELECT count(*) FROM pg_stat_activity;"`.
- Si trop de connexions ouvertes (>100) : `docker compose restart postgres` (les bases `ratis_test_*` sont recréées au boot via `initdb/`, pas de perte applicative).

---

Pour le design + intégration ops globale : voir [`ARCH_itops.md`](../ARCH_itops.md). Pour le déploiement projet global : voir [`ARCH_deployment.md`](../ARCH_deployment.md).
