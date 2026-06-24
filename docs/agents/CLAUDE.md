# CLAUDE.md — Ratis shared agent reference

> **Role routing (read this first)** :
> - **Main Claude Code session (orchestrator)** → `docs/agents/ORCHESTRATOR.md` is auto-injected at session start via `.claude/settings.json` hook. Read it to know your role rules.
> - **Subagent dispatched for a dev task** → when briefed, read `docs/agents/SA_DEV.md` for coding rules/patterns/pitfalls.
> - **Subagent dispatched for exploration/research** → when briefed, read `docs/agents/SA_EXPLORE.md` for reading discipline (grep→index→seg→full).
> - **Subagent dispatched for code review** → follow your agent-type system prompt, no extra file needed.
>
> **Important : the orchestrator MUST explicitly tell each subagent which SA_*.md to read in the brief.** Subagents inherit CLAUDE.md auto but NOT the SA_*.md — those are read only when briefed.
>
> This file (`docs/agents/CLAUDE.md`) contains shared knowledge applicable to **everyone**. Never modify without user ask (R26).

## abbr
AU=ratis_auth · PA=ratis_product_analyser · LO=ratis_list_optimiser · RW=ratis_rewards · NT=ratis_notifier · RC=ratis_core · CL=ratis_client
PG=Postgres · R=Redis · BP=Battlepass · CAB=cabecoin · SA=subagent

## identity
ratis = cashback + realtime prices + gamif + gift-cards (V1 post Runa KYB)
red-lines: !sell-CAB · !paid-ranking · fire-and-forget (every user action async)
hors-V1: "Trésor découvert" (1000pts+OFF-contrib) · B2B-analytics

## services
AU :8001 `/api/v1/auth/* /account/* /webhooks/*` deps=PG+R+Google+Apple+Stripe · rate-lim=`/login /register /change-password /refresh`
PA :8003 `/api/v1/scan/* /product/*` deps=PG+R(Celery)+R2+PaddleOCR+pyzbar+OSM-Overpass · Celery-worker=separate-proc · OCR=lazy-import
LO :8002 `/api/v1/lists/*` deps=PG+R(Celery)+OSRM · Celery-worker · route.steps no-home-point (PII)
RW :8004 `/api/v1/gamification/* /rewards/* /admin/*` deps=PG+Runa(V1) · `/admin/*` uses ADMIN_API_KEY
NT :8005 `/api/v1/notify` (internal) deps=PG+Expo · called via INTERNAL_API_KEY

## stack
pkg=uv(never pip; `uv add` `uv run` `uv sync --package X --frozen`)
py=3.12 (pinned .python-version; never 3.13/14 → paddleocr no-wheel)
web=FastAPI+Pydantic-v2+async-lifespan
db=PG-16 · driver=`psycopg[binary]` v3 · URL=`postgresql+psycopg://` (never psycopg2)
orm=SQLAlchemy-2.0 via `RC.database.make_engine(url)`
mig=Alembic (`/alembic`) · flow=autogen→verify→`upgrade head`→commit(mig+model together) · prod runner=service `migrations` (`webservices/ratis_migrations/Dockerfile`, profile `migrate`, run-once : `docker compose --profile migrate run --rm migrations`)
cache=R-7 (slowapi + Celery + cache)
ocr=PaddleOCR+paddlepaddle≥3.0 · lang=fr · lazy-import `PA/worker/pipeline/ocr_engine.py`
routing=OSRM-5.27 MLD · France-PBF prebuilt
proxy=Caddy-2 (ACME auto; self-host only)
front=Expo-SDK54+RN+expo-router+i18next(fr-V0)+React-Query+expo-auth-session
mobile-build=EAS (`CL/eas.json` profiles: development/preview/production)
container=Docker+compose-v2 multi-arch(amd64+arm64)
dev-host=Mac mini M4 Pro arm64 (macOS, since 2026-05-04 / PR #287) — same machine hosts the 16 GH Actions self-hosted runners. CI runs in Linux Docker containers via runners → CI = ground-truth Linux despite the arm64 host. Shell commands = standard POSIX bash (no PowerShell, no MINGW).
agent-mcp=`tools/agent-mcp/` Keychain-backed binary exposing typed tools to Claude · 10 modules (Sentry · EAS · GitHub · Notion · Stripe · R2 · DB-write-pipeline · Hermes · **Module 9 docs** : `docs_search`/`get`/`find`/`list_files`/`reindex` (#560/#561) · **Module 10 secrets-vault** : `secret_*` 12 tools + CLIs `ratis-secret`/`ratis-admin` (#563-#570)) · telegram pattern in `docs/arch/ARCH_agent_mcp.md`
n8n=central incident/pipeline orchestrator on Mac mini (Tailscale Funnel public · admin UI `localhost:5678`) · deployed workflows : `db-write-pipeline` (HSP1-5 V1.1) · `batch-sentinel` (monitoring 10 GH Actions batches) · `sentry-ingest` → Notion INCIDENTS · `github-pr-merged-closer` · `daily-digest` · `db-snapshot` · cf `docs/arch/ARCH_n8n_pipelines.md`
auth=OAuth Google+Apple(iOS) → JWT HS256 aud=ratis
money=int-cents always (rates=NUMERIC OK) · OCR conv=`int(round(Decimal(str(v))*100))` never `int(float*100)`
i18n=`t('key')` → `CL/locales/fr.json` · backend err `detail="snake_code"`
obs=Sentry(DSN env, no-op-if-empty) + `RequestIDMiddleware` (RC)
dev-start=`docker compose up -d` · DATABASE_URL=`postgresql+psycopg://ratis:ratis@localhost:5432/ratis_dev` <!-- pragma: allowlist secret -->

## shared rules (apply to both orchestrator and subagents)

R15 pre-**merge** **CI** green (never merge red). Push red OK, MERGE red forbidden. SA can run pytest locally for quick feedback (timeout-safe `thread` mode cross-OS, cf `docs/agents/SA_DEV.md` § tests). CI Linux Docker = ground truth for merge.
R16 commits ≤3 lines · Conventional-Commits (`feat/fix/chore/refactor/test/docs/infra/ci`) · no `Co-authored-by` · no credentials in messages
R17 never commit `.env.local` · `.env.prod` · keys · tokens · `credentials.json`
R24 ARCH-first every feature (template `docs/arch/ARCH_EXAMPLE.md`) · checklist tracked one-item-at-a-time · update ARCH on scope-changing decision · check `docs/reference/ARCH_INVENTORY.md` before creating new ARCH
R25 prod decisions → `docs/ops/PROD_CHECKLIST.md` immediate
R26 `docs/agents/CLAUDE.md` / `docs/agents/ORCHESTRATOR.md` / `docs/agents/SA_DEV.md` / `docs/agents/SA_EXPLORE.md` : the agent may **propose** modifications (after audit, emerging new convention, stale paths spotted in session) — **ask operator confirmation before committing**. No silent modification, but no paralyzing blockage either. Propose → wait for OK → apply.
R27 **BEFORE any session/task that may design or modify a backend endpoint** → run `python scripts/generate-endpoints-inventory.py` then consult `docs/reference/ENDPOINTS.md`. Orchestrator checks at design time; subagent checks before coding. Reuse existing — never reinvent.
R28 **BEFORE any brainstorm/design session** → recon via `docs-mcp` (agent-mcp Module 9, LIVRÉ #560/#561) : `docs_search(query, top_k)` hybrid vector+keyword first, `docs_get(id)` for body section, `docs_find(status=, tags=, file_glob=)` for typed filters. Fallback `Grep docs/reference/ARCH_INVENTORY.md` only if docs-mcp unavailable. `python scripts/generate-arch-inventory.py` remains the index authority (regen at session-start hook). Extend existing ARCH rather than duplicate.
R29 **Large docs — NEVER read in full.** Applies to: `docs/arch/ARCH_*.md` (+ per-service `ARCH_*.md` under `webservices/`, `batch/`, `ratis_client/`, `ratis_core/`) · `docs/product/PRODUCT.md` · `docs/ops/PROD_CHECKLIST.md` · `docs/known/KNOWN_PROBLEMS.md` · `docs/product/PRIVACY.md` · `docs/product/TRAINING.md`.
    Procedure : `Read offset=0 limit=50` (index/TOC) → identify relevant H2 sections → `Read offset=X limit=Y` OR scoped `Grep` on those sections. Full-file allowed only if <100 lines OR explicit cross-file refactor requires it.
R30 **Subagent delegation (orchestrator-only — for reference in CLAUDE.md so subagents understand the discipline)** — heavy search · large-file synthesis · feature dev · TDD · audit MUST go to subagents via `Agent` tool. Main context = orchestration + small-edits + commands. Full details in `docs/agents/ORCHESTRATOR.md`.
R31 **Post-dev maintenance (orchestrator duty)** — after each dev block is done : (a) ARCH touched → checklist marked + decisions documented · (b) `docs/known/KNOWN_PROBLEMS.md` + `docs/known/KNOWN_PROBLEMS_INDEX.md` updated if new pitfall discovered · (c) `SESSION_LOG.md` closing entry. Full details in `docs/agents/ORCHESTRATOR.md` § post-dev maintenance.
R32 **Subagent file assignment (orchestrator duty)** — the orchestrator MUST tell each subagent explicitly which `SA_*.md` to read in the brief (first line). Subagents don't know which to pick — I assign it. Types→files mapping in `docs/agents/ORCHESTRATOR.md` § file assignment per subagent type.
R33 **Clean solution always** — never workaround for convenience, never shortcut. If the clean path takes 3× longer, take it anyway. Dev cost doesn't apply to Claude Code — speed is in tokens, not hours. Shortcuts breed tech debt that costs 10× more later.
    · Never delete a test. Blocking ? `@pytest.mark.skip(reason="...")` + entry `DECISIONS_PENDING.md` with context + reasoned recommendation.
    · Never hardcode a value to make a test pass. Fix the logic.
    · Never disable a lint rule inline (`# noqa`, `// eslint-disable`) without explicit justification + reviewer approval.
    · Never bypass CI with `--no-verify` unless user explicitly asks.
    · Prefer the documented pattern (see `docs/agents/SA_DEV.md` § recurring patterns) over inventing a local convention.
    · If you can't find the clean path → ask. Don't ship a hack "to unblock".
R34 **EAS publish discipline (CL only)** — pre-publish gate (clean tree + HEAD==origin/main) · channel must match installed APK (cf KP-32) · always `--environment` matching `--channel` (cf KP-57) · OTA suffices for pure JS/TS edit, otherwise `eas build`. Full details : `## EAS / mobile deploy`.
R35 **SaaS UI step-by-step guidance — current-version discipline** — when SaaS UI is forced (= R36 failed) :
    · **Direct URL** rather than click-path (URLs more stable than overhauled menus)
    · Click-paths marked `*(may have moved)*` + fallback search-bar keyword
    · **At the first "this button doesn't exist"** : DO NOT guess — (a) WebFetch current doc OR (b) ask for screenshot OR (c) analyze screenshot pixel-by-pixel
    · **Preventive WebFetch** if tool not used >3 months
R36 **SaaS configuration — API-first systematic** — for ALL SaaS config (alert rules, webhooks, integrations, tokens, projects, teams, etc.) :
    · **CHECK REST API / CLI FIRST** and use it → 0 human friction, instant, scriptable, idempotent
    · If admin API token missing : guide its creation once, then automate everything else
    · Direct URL (R35) is the BACKUP when API doesn't support it (rare in 2026)
    · Click-path step-by-step is the BACKUP-of-last-resort
    · Test : "can I do this in 1 Bash command?" — if yes, it's my job, not the operator's
R41 **doc inventory — pipe-separated convention** : each major section in `ARCH_*.md` (docs/arch/ + sub-dirs webservices/batch/client) · `docs/known/KNOWN_PROBLEMS.md` · `docs/decisions/DECISIONS_ACTED.md` follows `## <ID> — <title> · <refs> · <STATUS>` (IDs : `DA-N` · `KP-N` · `HSP-N` · `M-N` in the sense `[A-Z]+-N` ; statuses : `LIVRÉ` · `EN-COURS` · `PLANIFIÉ` · `DEPRECATED` with free suffix like `LIVRÉ V1.1`). Quote-block immediately after the title : `> TL;DR` (1-2 sentences) · `> @tags: space-separated words` (author-free, no closed vocabulary) · `> @subs: auto` (script computes `### Sub-sections(Lxx)` up to next `##`). Single pipe-separated index in `docs/reference/ARCH_INVENTORY.md` (auto-regenerated by `python scripts/generate-arch-inventory.py`, CI freshness check `doc-inventories.yml`). **NEVER full-read** — default agent workflow : `docs_search(query)` (hybrid vector+keyword, LIVRÉ #561) → `docs_get(id)` for targeted body · `docs_find(status=, tags=, file_glob=)` for typed filters · fallback `Grep docs/reference/ARCH_INVENTORY.md` + `Read offset=<line>` if MCP unavailable. Progressive migration : non-migrated files appear as `LEGACY` (1 entry per file), `scripts/check-arch-convention.sh` warn-only for 2 sprints. Out of scope : `docs/product/` (PRODUCT/PRIVACY/TRAINING) · `SESSION_LOG.md` · `docs/ops/PROD_CHECKLIST.md` · agent refs (`docs/agents/CLAUDE.md` · `docs/agents/ORCHESTRATOR.md` · `docs/agents/SA_*.md`). **Before writing a new section or a new doc** : consult `docs/arch/ARCH_doc_system.md` (DS-1..DS-7) — target tree, exact R41 format, lifecycle spec→ARCH, obsolete marking.
R42 **secrets-vault — JIT discipline** : for any Cat A token (auto-minted) or Cat B (CLI-mintable provider — github-app · cloudflare-r2 · sentry · eas · vercel · stripe-restricted), the agent systematically uses `ratis-secret use <name> --cmd "..."` (env subprocess injection, secret never displayed) OR on the Python side the `secret_with` context manager from the `agent_mcp` module (lease + auto-revoke). To access an admin UI : `ratis-admin open <path> [--service pa|rw|au]` (OTT JWT 60s single-use → cookie session ; ADMIN_API_KEY read from Keychain `ratis-agent-mcp/admin-api-key`, never printed). **NEVER ask the operator to set a secret manually** when the vault can mint it — the V0 pattern "you set your X" is obsolete. Cat C (UI-only — Stripe live, OpenAI/Anthropic console, Notion integrations, Apple/Google OAuth) : `ratis-secret import <name> --category C --expires-at YYYY-MM-DD` after manual browser mint (no-echo prompt ; rotation reminder via cron). HMAC-chained append-only audit log : `~/.local/state/ratis-agent-mcp/audit/secrets-YYYY-MM.jsonl`. Tail : `ratis-secret audit`. Vault LIVRÉ #563-#570 (Module 10 agent-mcp).

## repo
```
ratis/
  docs/agents/                                         CLAUDE.md ORCHESTRATOR.md SA_DEV.md SA_EXPLORE.md   agent refs
  docs/reference/                                      ENDPOINTS.md ARCH_INVENTORY.md                       auto-gen indexes
  docs/ops/                                            PROD_CHECKLIST.md (SESSION_LOG.md = root, local-only)   ops + journal
  docs/arch/                                           ARCH_*.md (cross-service) + PROCEDURES.md (auto-gen)
  docs/product/                                        PRODUCT · PRIVACY · TRAINING
  docs/known/                                          KNOWN_PROBLEMS{,_INDEX}.md
  docs/decisions/                                      DECISIONS_ACTED.md (+ PENDING local-only)
  docs/ops/                                            RUNBOOK_MIGRATION · SETUP_CHECKLIST · OPS_SCRIPTS.md (auto-gen)
  docker-compose.yml docker-compose.prod.yml Caddyfile scripts/ops/start_all.sh scripts/ops/stop_all.sh
  pyproject.toml uv.lock .python-version               uv workspace + py 3.12
  alembic/versions/                                    migrations
  scripts/                                             generate-{endpoints,arch,procedures}-* · cleanup-ghost-runners.sh ; scripts/ops/update-scripts-help.sh → docs/ops/OPS_SCRIPTS.md
  ratis_client/                                        Expo (eas.json · app/ · hooks/ · services/ · locales/ · ARCH_*.md per-screen)
  ratis_core/                                          shared py-lib + config/{ratis_settings,classification_rules}.json + ARCH_CORE.md
  webservices/{ratis_auth,ratis_product_analyser,ratis_list_optimiser,ratis_rewards,ratis_notifier}/  (each ships ARCH_*.md next to code)
  batch/ratis_batch_{osm_sync,off_sync,consensus,purge,reconciliation,mystery_announce,savings,referral_payout}/  (each ships ARCH_BATCH_*.md)
  db/{schema.sql,schema_lite.sql,datafixes/}
  runner/docker-compose.yml                            16 GH Actions self-hosted runners on Mac mini
  infra/cloud-init-hetzner.yaml                        VM auto-bootstrap
  .github/workflows/                                   CI pytest + batch crons + doc-inventories freshness
  .claude/settings.json                                shared SessionStart hook config
```

## frontend (CL/)
```
app/(auth)/login.tsx        OAuth Google+Apple + dev-bypass (__DEV__)
app/(tabs)/                 index=Dashboard · liste=List+route · scan=Camera+bg-queue · produit=EAN lookup · profil=Stats+menu
app/{my-info,referral,shop,scan-history}.tsx   dedicated features (cf ARCH_*)
hooks/                      30+ React Query hooks `use-*` (canonical source = `ratis_client/hooks/`)
services/                   {api,rewards,product,list}-client.ts → EXPO_PUBLIC_<X>_URL
locales/fr.json             i18next (V0 fr only)
__tests__/                  jest 800+
```

## tables (domain semantics)
scans                       source-of-truth · type={receipt|electronic_label|manual} · cycle=pending→(unmatched|accepted|rejected) · tva_amount=receipt-only · receipt-img 48h-R2 · label image_url→NULL@accepted
receipts                    total_amount denorm (OCR-guard) · SENTINEL_DATE=1970-01-01 if unresolved
price_consensus             UNIQUE(store_id,product_ean) · price-change→INSERT price_consensus_history+UPDATE · trust_score≥95%→frozen_until · params=ratis_settings.json
products                    source={off|internal} · classification via classification_rules.json
product_knowledge           OCR auto-learn raw_ocr→corrected · corrected=NULL=manual-queue · see docs/product/TRAINING.md
shopping_lists              has_default_name=true+name=''=default · never send name=''
optimized_routes            TTL=24h · JSONB steps no-home-point (PII)
cabecoin_transactions       direction=credit|debit · reference_type CHECK={scan|referral|mission|battlepass|...} · promo codes UPPERCASE
gift_card_orders            UNIQUE(source_type,source_ref_id)=idempotent · eligible_at=anti-churn-30d (referral)
user_cab_balance            materialized · UPDATE-atomic mandatory
user_cashback_balance       materialized · UPDATE-atomic mandatory
subscriptions               Stripe-backed · CHECK payment_ref_coherence (payment_ref required) · NEVER PURGE
cashback_withdrawals        NEVER PURGE (legal)
cashback_transactions       NEVER PURGE (legal)
stores                      soft-delete is_disabled · source='user_suggested'→lat/lng=0 pending-admin
users                       soft-delete is_deleted · `DELETE /account`→in-place anonymize

## env vars
all          DATABASE_URL(postgresql+psycopg://) · TEST_DATABASE_URL · INTERNAL_API_KEY(=all5) · SENTRY_DSN(opt)
jwt          JWT signed RS256 (audit H1) : AU holds JWT_PRIVATE_KEY_PATH (issuer) ; AU+PA+LO+RW have JWT_PUBLIC_KEY_PATH (verify) ; JWT_AUDIENCE=ratis on all 4 · NT does not verify JWT
AU           JWT_PRIVATE_KEY_PATH · JWT_PUBLIC_KEY_PATH · JWT_AUDIENCE=ratis · GOOGLE_CLIENT_ID · APPLE_CLIENT_ID(empty=Android-only) · STRIPE_SECRET_KEY · STRIPE_WEBHOOK_SECRET · ACCESS_TOKEN_EXPIRE_MINUTES=60 · REFRESH_TOKEN_EXPIRE_DAYS=30 · REDIS_URL · REWARDS_BASE_URL · NOTIFIER_URL
PA           JWT_PUBLIC_KEY_PATH · JWT_AUDIENCE=ratis · R2_ENDPOINT_URL · R2_ACCESS_KEY_ID · R2_SECRET_ACCESS_KEY · R2_BUCKET_NAME · REDIS_URL · OSM_OVERPASS_URL · NOTIFIER_URL
LO           JWT_PUBLIC_KEY_PATH · JWT_AUDIENCE=ratis · OSRM_BASE_URL(=http://osrm:5000) · REDIS_URL
RW           JWT_PUBLIC_KEY_PATH · JWT_AUDIENCE=ratis · ADMIN_API_KEY · CASHBACK_WEBHOOK_SECRET_{AFFILAE,AWIN,CJ}(+_PREV, audit MED · secret per provider) · GIFT_CARD_PROVIDER_KEY(opt) · AFFILAE_API_KEY · AWIN_API_KEY · CJ_API_KEY · PAYMENT_PROVIDER_KEY(opt)
NT           EXPO_PUSH_URL=https://exp.host/--/api/v2/push/send
batch_osm    OSM_OVERPASS_URL
batch_off    OFF_USER_AGENT · OFF_API_BASE_URL

## tools-access
agent EXECUTES mechanical actions (secret/deploy/OTA/GlitchTip-issue/restart/n8n-import) without asking · operator ALONE for irreversibles (cut SSH agent, flip `DB_PIPELINE_EXECUTE_ENABLED`/`caps_enforced`) + business decisions (graduation procedure, threshold calibration post-data)
keychain     svc `ratis-agent-mcp` (MCP provider tokens, list : `agent-mcp keychain check`) · CLI `agent-mcp keychain {set,rm,check} <provider>` · svc `ratis-runner-pat`/acct `Cursor` (PAT 16 GH self-hosted runners)
ssh-prod     alias `ratis-prod` (~/.ssh/config → root@46.225.63.79, ident `~/.ssh/ratis_hetzner_v3`) · operator bootstrap key V1.1 (HSP5 swap) · agent → deploy/`docker compose pull && up -d`/set secret/restart/restore-snapshot
eas          token Keychain `ratis-agent-mcp`/`eas` · agent-mcp tools `eas_{list_builds,list_updates,update_preview,update_production,rollback_to_embedded}` · discipline R34 (channel/env matching, pre-publish gate clean tree + HEAD==origin/main)
gh           authenticated `Belladynn` · agent : PRs, merges, `gh secret set`, runs/checks, PR comments · agent-mcp double `github_*` read
n8n          admin UI `localhost:5678` Mac mini + Tailscale Funnel public · import/edit workflow + secret env → agent (scripted UI or n8n API)
docs-mcp     Module 9 agent-mcp (#560/#561) · MCP tools `docs_search(query,top_k)` (hybrid vector bge-m3 + keyword via sqlite-vec) · `docs_get(id)` (body section) · `docs_find(status=,tags=,file_glob=)` (typed filters) · `docs_list_files()` (categorization) · `docs_reindex(force=)` (maintenance) · cf R28+R41
secrets-vault Module 10 agent-mcp (#563-#570) · CLIs `ratis-secret {list,new,use,revoke,audit,rotate,import}` + `ratis-admin open <path> [--service pa|rw|au]` · 12 MCP tools `secret_{generate,get,list,delete,inject,provision,revoke,renew,audit_expiry,import,rotate,rollback}` · cf R42
skill notion-export  `.claude/skills/notion-export/SKILL.md` (#562) → scans via docs-mcp + generates Notion decision-maker version · idempotent External ID `ratis-export:<id>` · dry-run mode
hermes-ops   personal ops agent (container `ratis-hermes`) : Telegram bot @RatisAppBot (pairing · /status · /pending_ticket · GlitchTip alerts · digests) · 4 native crons · kanban tracker · postmortem→reviewer→skills pipeline · exposed MCP server in Claude Code (`hermes mcp serve`) · versioned stack `infra/hermes/` + deploy.sh · cf `docs/arch/ARCH_hermes_ops.md`

## git / PR workflow
main=protected · PR-required · CI-green-required · GitHub-Flow
branch per block: feat/x · fix/y · chore/z · docs/w · infra/v · ci/u
worktree: `git worktree add .worktrees/<n> -b <branch> origin/main` (.worktrees/ gitignored)
squash pre-merge → 1 commit/PR · iterate via `git commit --amend --no-edit && git push --force-with-lease`
end-of-block: squash→push→`gh pr create --title "..." --body "$(cat <<'EOF' ... EOF)"`→`gh run view --log-failed <id>` if red→fix→flag in SESSION_LOG.md
CI-secrets: DATABASE_URL · JWT_PRIVATE_KEY_PATH · JWT_PUBLIC_KEY_PATH · INTERNAL_API_KEY · Expo-tokens (batch/deploy workflows)

## EAS / mobile deploy (CL only)
pre-publish gate    `git fetch && git status` clean · `HEAD SHA == origin/main SHA` (not just "no unstaged") · publish only AFTER PR merge
OTA suffices        pure JS/TS (`app/` `services/` `hooks/` `components/` `locales/` `contexts/`) → `eas update --channel X --environment X` (matching flags mandatory, cf KP-57)
rebuild required    new native lib · new plugin (app.json plugins[]) · `runtimeVersion` change · `android/`/`ios/` edits → `eas build`
post-OTA verify     force-stop ×2 (download + apply bundles) → smoke test. If OTA does not appear : channel mismatch likely, cf KP-32
broken OTA recovery `eas update:roll-back-to-embedded --channel X` (APK reverts to embedded bundle, no rebuild)
runtime policy      `runtimeVersion.policy="appVersion"` → version bump = rebuild mandatory before re-OTA
debug channel       `eas build:list --limit 1 --platform=android` then read `Channel:` BEFORE any `eas update`. Sentry `app.boot` event + badge "OTA build #N" in profile = runtime traceability

## RGPD (inflexible — everyone)
- no names/firstnames stored (OCR)
- receipt-imgs 48h R2 → image_deleted_at
- label-scan image_url → NULL @accepted
- user_lat / user_lng = PII · never-logged
- optimized_routes.steps: no-home-point
- NEVER PURGE: cashback_withdrawals · cashback_transactions · subscriptions
- DELETE /account → in-place anonymize + behavioral-PII delete

## gotchas (top KPs that cost 30 min — full index `docs/known/KNOWN_PROBLEMS_INDEX.md`)
KP-13  · `httpx.AsyncClient` without `timeout=` → bandit B113 + indefinite TCP block · ALWAYS `timeout=httpx.Timeout(30.0)` at minimum
KP-24  · External service + DB mutation in loop : R2 upload OK then DB insert crash = orphaned R2 object (PA OCR scan) · commit-per-row pattern + typed exception scope, not global `except Exception`
KP-25  · `.env.prod` saved Windows CRLF silently breaks all `require_env` (read returns with `\r` appended) · check `file .env.prod` post-edit
KP-32  · OTA channel mismatch silent no-op (4h debug paid) · ALWAYS `eas build:list --limit 1` then read `Channel:` BEFORE `eas update --channel X` · cf R34
KP-44  · Model/migration drift : `Mapped[datetime]` SQLAlchemy → `TIMESTAMP WITHOUT TIME ZONE` by default, while migration enforces `TIMESTAMPTZ` · ALWAYS `Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), ...)` explicit
KP-57  · `eas update --channel X` without `--environment X` does not pull EAS env vars (post-Mac mini migration) · both flags mandatory and matching
KP-77  · Quiet hours boundary off-by-one (22:00 Paris treated as outside the 22h-8h window) — `notifier/services/notify_service.py` · check `>=` vs `>` at boundaries

## decisions (quick reference)
new-endpoint    → ARCH → TDD → route+service+repo (no SQL outside repo) → db.commit() → i18n-err → tests → commit → PR
new-config      → ratis_settings.json | app_settings table (never hardcode)
amount          → int-cents
new-env-var     → .env.example + conftest.py + require_env() simultaneous
py-dep          → `uv add X` in concerned service (not root)
ext-API 5xx/TO  → UpstreamServiceError → 503
mig-drop        → `op.execute("ALTER...DROP CONSTRAINT IF EXISTS...")` + test_migration.sh pre-push
blocking-test   → @pytest.mark.skip + DECISIONS_PENDING.md (local-only, repo root) · never delete test
new-decision    → DECISIONS_PENDING.md pending · validated → docs/decisions/DECISIONS_ACTED.md + ctx
kanban-vs-decn  → follow-up/todo = Hermès kanban (tracking, ephemeral) · decision = DECISIONS_{PENDING,ACTED} (record, git) · cf docs/arch/ARCH_hermes_ops.md HO-3
end-of-block    → audit docs/known/KNOWN_PROBLEMS_INDEX.md · surface every finding canonical-table

## task→files
auth-bug        ratis_core/auth.py · ratis_core/jwt.py · webservices/ratis_auth/services/auth_service.py · webservices/ratis_auth/routes/auth.py
scan-bug        webservices/ratis_product_analyser/tasks.py · worker/pipeline/* · routes/scan.py
CAB/gamif       webservices/ratis_rewards/services/* · webservices/ratis_rewards/routes/* · ratis_core/config/ratis_settings.json
mobile          ratis_client/app/<screen>.tsx · ratis_client/hooks/use-*.ts · ratis_client/services/*-client.ts
migration       alembic/versions/<latest>.py · ratis_core/models/
infra/deploy    docker-compose.prod.yml · webservices/*/Dockerfile · Caddyfile · infra/cloud-init-hetzner.yaml · docs/arch/ARCH_deployment.md
test-setup      tests/conftest.py · fixture assert_no_pending_changes · DB fixture

## pointers (don't duplicate content here)
backend endpoints inventory (auto-gen)        → `docs/reference/ENDPOINTS.md` (regen via `python scripts/generate-endpoints-inventory.py`) · **MUST read before endpoint design — R27**
ARCH inventory (auto-gen)                     → `docs/reference/ARCH_INVENTORY.md` (regen via `python scripts/generate-arch-inventory.py`) · **MUST read before feature brainstorm/design — R28**
orchestrator rules (session main)             → `docs/agents/ORCHESTRATOR.md` (auto-injected at session start)
dev-subagent rules                            → `docs/agents/SA_DEV.md` (read when dispatched for dev)
explore-subagent rules                        → `docs/agents/SA_EXPLORE.md` (read when dispatched for research/synthesis)
deploy arch/topology/commands/runbook        → `docs/arch/ARCH_deployment.md`
hosting timeline Hetzner→Mac-mini→AWS + saturation signals → `docs/ops/PROD_CHECKLIST.md` § Hosting strategy
product vision/business-model/target         → `docs/product/PRODUCT.md`
pre-prod tasks (gift-cards/legal/obs/perf)   → `docs/ops/PROD_CHECKLIST.md`
known-problems KP-NN                         → `docs/known/KNOWN_PROBLEMS.md` + `docs/known/KNOWN_PROBLEMS_INDEX.md`
OCR training data-flow                       → `docs/product/TRAINING.md`
agent-mcp client/admin tooling                → `docs/arch/ARCH_agent_mcp.md` + `tools/agent-mcp/README.md` (Keychain-backed MCP exposing typed Sentry/EAS/GitHub/Stripe/R2 tools to Claude — no token in model context. Notion removed 2026-05-31, DA-47, replaced by `docs/arch/ARCH_incident_management.md` + wrapper `~/glitchtip/bin/glt`)
doc system / writing methodology             → `docs/arch/ARCH_doc_system.md` (DS-1..DS-7) · **MUST read before writing/structuring a new doc** : target tree, exact R41 format, lifecycle spec→ARCH, agent consumption/writing, obsolete marking
per-feature ARCHs (docs/arch/ARCH_cab_economy.md, docs/arch/ARCH_referral.md, etc.) → implementation checklist + decisions (consulted via docs/reference/ARCH_INVENTORY.md index)

## mantra
uv≠pip · psycopg-v3 · int-cents · db.commit() · TDD · YAGNI · DRY · fire-and-forget · never-sell-CAB
