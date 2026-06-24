# infra/hermes

Versioned, recoverable bits of the Hermes Agent stack (the personal-ops agent
running on the Mac mini). It holds both the **kanban snapshot**
(tickets-as-documentation) and the **full stack as code** (config templates,
cron scripts, the postmortem skill, routines, compose files, `glt`, and an
idempotent `deploy.sh` that rebuilds everything on a fresh machine).

## Stack complète versionnée

The Hermes stack used to live only in unsaved local dirs (`~/.hermes`,
`~/hermes`, `~/glitchtip`, `/tmp`). Lose the Mac mini and it was gone. This dir
is the secret-stripped mirror + a deploy script to restore it.

### Arborescence

```
infra/hermes/
  deploy.sh                              # idempotent restore (see runbook below)
  .env.example                           # env var KEYS only (values → Keychain)
  hermes-home/                           # mirror of ~/.hermes
    SOUL.md                              # agent persona (verbatim)
    config.template.yaml                 # ~/.hermes/config.yaml (secrets → ${VAR})
    webhook_subscriptions.template.json  # webhook sub (secret → ${WEBHOOK_SECRET})
    scripts/                             # cron scripts (digest, github-watch, postmortem, …)
    skills/ratis/claude-code-postmortem/ # the custom postmortem skill (code + tests + refs)
  hermes-compose/                        # mirror of ~/hermes
    docker-compose.yml                   # hermes + glitchtip-proxy services
    glitchtip-proxy/proxy.py             # HMAC-signing GlitchTip→Hermes sidecar
    setup-hermes-runtime.sh              # one-shot runtime bootstrap
  glitchtip/                             # mirror of ~/glitchtip
    docker-compose.yml                   # self-hosted GlitchTip (Sentry-alike)
    bin/glt                              # GlitchTip CLI wrapper (Keychain-backed)
  routines/                              # Claude.ai scheduled-task prompts (fragile, were in /tmp)
    postmortem-deep.md
    skill-reviewer.md
```

**Secrets**: no real secret is committed. `*.template.*` files carry `${VAR}`
placeholders; real values live in macOS Keychain (`ratis-agent-mcp`) and only
`.env.example` (keys, no values) is versioned. The `detect-secrets` CI gate
guards this.

### Runbook — restore on a fresh machine

```bash
git clone <ratis-repo> && cd ratis
bash infra/hermes/deploy.sh        # copies sources, renders config from Keychain
```

`deploy.sh` is idempotent (re-runnable). It creates `~/.hermes`, `~/hermes`,
`~/glitchtip`, copies the sources, marks scripts executable, renders
`config.yaml` + `webhook_subscriptions.json` by substituting `${VAR}` from
Keychain (missing secret → warn + continue, never abort), and best-effort
installs `faster-whisper` into the running Hermes container venv.

Then the **4 manual steps** `deploy.sh` prints at the end (cannot be automated):

1. **Re-auth Codex** — `docker exec -it ratis-hermes hermes auth add openai-codex` (OAuth).
2. **Re-pair Telegram** — `/start` to the bot, then `hermes pairing approve <id>`.
3. **Paste routines** — copy `routines/postmortem-deep.md` and
   `routines/skill-reviewer.md` into Claude.ai scheduled tasks.
4. **Bring stacks up** — `docker compose up -d` in `~/glitchtip` then `~/hermes`
   (re-run `deploy.sh` once after, so `faster-whisper` lands in the live venv).

## Kanban snapshot — why

The Hermes kanban (`~/.hermes/kanban.db`) is the project's living follow-up
board: pending arbitrations, decisions, deferred work. The tickets double as
**documentation**, and rebuilding them by hand would be painful. But the DB
lives only in the container (gitignored, local) → a machine loss = tickets gone.

So we version the ticket **data**, not the automation around it (the cron is
disposable — trivial to recreate; the data is not).

| File | What |
|---|---|
| `kanban-snapshot.json` | Full ticket dump (all statuses incl. archived), pretty-printed for stable diffs. **Source of truth for restore.** |
| `kanban-snapshot.md` | Human-readable view (grouped by status) — readable in PRs / on GitHub. |
| `kanban-snapshot.sh` | Regenerate both + commit (and push) if changed. Deterministic, **zero LLM**. |
| `kanban-restore.sh` | Rebuild the kanban from the JSON after a crash (best-effort). |

## Refresh the snapshot

```bash
infra/hermes/kanban-snapshot.sh            # export + commit + push if changed
KANBAN_SNAPSHOT_PUSH=0 infra/hermes/kanban-snapshot.sh   # commit only, no push
```

Runs on the **host** (needs git + repo), not in the container. Schedule it
however you like — a daily host cron line is enough, and losing that cron is
not a problem (re-add in 10s); only the committed data matters:

```cron
0 20 * * *  cd /Users/guillaume/Cursor/Ratis && infra/hermes/kanban-snapshot.sh >> /tmp/kanban-snapshot.log 2>&1
```

## Restore after a crash

```bash
infra/hermes/kanban-restore.sh                 # rebuild from kanban-snapshot.json
DRY=1 infra/hermes/kanban-restore.sh           # preview without applying
```

No native `hermes kanban import` exists, so restore loops `hermes kanban create`
with `--idempotency-key = original-id` (safe to re-run, no duplicates) and
re-applies status (blocked/done/archived). **Comments & event history are not
restored** — title, body, priority, assignee, status are.
