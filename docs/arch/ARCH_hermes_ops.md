---
# Identité
type: cross-cutting
service: hermes-ops
status: LIVRÉ V0

# Navigation (Obsidian + RAG)
parent: ARCH_RATIS
sub_archs: []
related: [ARCH_incident_management, ARCH_agent_mcp, ARCH_itops, ARCH_n8n_pipelines]

# Technique
tech: [Hermes Agent (NousResearch), Docker Compose, Telegram Bot API, OpenAI Codex (gpt-5.5), faster-whisper, holographic memory, macOS Keychain, GlitchTip, Claude.ai routines]
tables: []
env_vars:
  - TELEGRAM_BOT_TOKEN          # Keychain ops-telegram-bot-token
  - WEBHOOK_SECRET              # Keychain ops-hermes-webhook-secret
  - DIGEST_GITHUB_TOKEN         # Keychain ratis-agent-mcp/github
  - DIGEST_GLITCHTIP_TOKEN      # Keychain admin-glitchtip
  - HERMES_POSTMORTEM_TIMEOUT
  - HERMES_CLAUDE_PROJECTS_DIR
rgpd_concern: false
updated: 2026-06-11
last_chunk_completed: HO-7 (adapters & assumed debt)
---

# ARCH_hermes_ops — Hermès as personal ops agent (Mac mini)

> MCP/messaging personal agent (NousResearch Hermes) running in a container on the Mac mini: bidirectional Telegram, native crons (digest/watch/backup), GlitchTip alerts, tracking kanban, auto postmortem→reviewer pipeline feeding the skills library, and Hermès exposed as an MCP server inside Claude Code. Stack versioned under `infra/hermes/` + deploy.sh.
> @tags: hermes agent telegram cron kanban postmortem skills mcp glitchtip voice backup infra-as-code ops mac-mini codex
> @status: LIVRÉ V0
> @subs: auto

## Index

- [Vision & scope](#vision--scope)
- [HO-1 — Telegram Gateway + persona + voice](#ho-1)
- [HO-2 — Hermès native crons](#ho-2)
- [HO-3 — Tracking kanban + boundary with DECISIONS](#ho-3)
- [HO-4 — Auto postmortem→reviewer→skills pipeline](#ho-4)
- [HO-5 — Hermès-as-MCP inside Claude Code](#ho-5)
- [HO-6 — Versioned stack infra/hermes + restoration](#ho-6)
- [HO-7 — Adapters & assumed debt](#ho-7)
- [Out of scope](#out-of-scope)

## Vision & scope

Hermès is Guillaume's **personal ops agent**, distinct from Claude Code (dev orchestrator). It lives in Telegram, watches prod (GlitchTip), tracks todos (kanban), learns from sessions (postmortem), and delegates/is delegated to via MCP. Goal: move from "I'll go check Sentry/GitHub" to "Hermès pushes me what matters".

Provider: **OpenAI Codex (gpt-5.5)** via OAuth ChatGPT Plus. Memory: local `holographic` provider. Persona: `SOUL.md` (FR, concise, technical, direct). Container `ratis-hermes` (compose `~/hermes`).

**Deliberately excluded** (YAGNI solo / out of context): multi-profile, multi-user pairing, Modal/Daytona sandboxes, real-time GitHub webhooks (polling is enough), browser automation + computer-use (Docker ≠ macOS accessibility), web-search (Perplexity instead), fallback provider (deferred), Spotify/X-search/image-gen/ACP/API-server/batch-training.

## HO-1 — Telegram Gateway + persona + voice · ARCH_hermes_ops.md · LIVRÉ V0

> TL;DR: bidirectional Telegram bot `@RatisAppBot`, concise FR persona `SOUL.md`, 6h memory + holographic, voice-in STT (faster-whisper local), optional voice-out TTS (edge FR, `auto_tts` toggleable).
> @tags: telegram gateway pairing soul persona memory holographic voice stt tts whisper
> @subs: auto

- **Gateway**: `hermes gateway run` (container). Bot via BotFather, token Keychain `ops-telegram-bot-token`. Access via **pairing** (`hermes pairing approve`), 1 user (Guillaume).
- **Persona**: `~/.hermes/SOUL.md` (FR, ≤150 words per chat, evidence-first, metro-readable). `display.personality: concise` aligned.
- **Memory**: `memory.provider: holographic` (local SQLite FTS5) + `agent.gateway_auto_continue_freshness: 21600` (6h continuity).
- **Quick commands** (exec, **0 LLM**): `/status` (on-demand digest), `/pending_ticket` (kanban summary). See `~/.hermes/config.yaml` `quick_commands`.
- **Voice**: STT `provider: local` (faster-whisper `base`) → Telegram voice transcribed. TTS `edge` voice `fr-FR-DeniseNeural`; `voice.auto_tts` voice replies (off by default — operator prefers text).

---

## HO-2 — Hermès native crons · ARCH_hermes_ops.md · LIVRÉ V0

> TL;DR: 4 `hermes cron --no-agent` jobs (deterministic, 0 LLM except postmortem): daily-digest 09h, github-watch /15min, auto-codex-reset /h, hermes-state-backup 05h. Scripts in `~/.hermes/scripts/` (versioned under `infra/hermes/hermes-home/scripts/`).
> @tags: cron daily-digest github-watch backup codex-reset hermes-cron scripts deterministic
> @subs: auto

| Job | Schedule | Role |
|---|---|---|
| `daily-digest` | `0 9 * * *` | Telegram digest: GlitchTip issues + PRs + kanban + latest postmortem |
| `github-watch` | `*/15 * * * *` | polls open/merged PRs + red CI (code only, batch-crons filtered) → Telegram; state-dedup |
| `auto-codex-reset` | `0 * * * *` | clears the Codex `exhausted` flag when `reset_at` has passed (see HO-7) |
| `hermes-state-backup` | `0 5 * * *` | targeted snapshot ~277KB (kanban+memory+auth+config+cron) → `~/.claude/postmortems/hermes-backups/`, rotation 6 |

The Hermès postmortem cron (initial POC) has been **removed**: replaced by a Claude.ai routine (HO-4), because Codex was timing out on large sessions (KP-102).

---

## HO-3 — Tracking kanban + boundary with DECISIONS · ARCH_hermes_ops.md · LIVRÉ V0

> TL;DR: Hermès kanban = **tracker for ephemeral todos/ops** (lifecycle todo→done), `dispatch_in_gateway: false` (no autonomous execution). JSON+MD snapshot versioned under `infra/hermes/kanban-snapshot.*` (re-importable, see kanban-restore.sh). Does NOT replace DECISIONS_PENDING/ACTED.
> @tags: kanban tracker snapshot restore decisions-pending frontière source-of-truth
> @subs: auto

**Agreed boundary (DP validated 2026-06-11)** — two systems, two roles:

| | `DECISIONS_{PENDING,ACTED}.md` (git, R41) | Hermès Kanban |
|---|---|---|
| Content | **decisions of record** (arch/process, the WHY) | **tasks/follow-ups** (the WHAT-TO-DO, lifecycle) |
| Authority | audited source of truth | operational tracking |
| Persistence | git versioned | container DB + versioned snapshot |

Rule: a **decision** → DECISIONS. A **task/follow-up** → kanban. A PENDING decision can *spawn* a kanban ticket to track its execution, but the decision text stays in DECISIONS. The kanban was seeded by importing follow-ups (not decisions) from DECISIONS_PENDING.

- `dispatch_in_gateway: false`: the kanban is a **passive board** (the operator decides), not an agent engine (an active dispatcher was auto-executing decision-tickets — discarded).
- Backup: `kanban-snapshot.sh` (pretty JSON + MD export); `kanban-restore.sh` (best-effort, loops `hermes kanban create --idempotency-key`, no native `kanban import` → comments/history not restored).

---

## HO-4 — Auto postmortem→reviewer→skills pipeline · ARCH_hermes_ops.md · LIVRÉ V0

> TL;DR: 2 Claude.ai routines (Pro/Max sub, 0€ marginal) — postmortem-deep (Opus + 5 parallel Explores, anti-timeout) mines sessions → skill-candidates; reviewer (triple-validation anti-injection) validates → operator promotes. Self-learning loop (DA-48).
> @tags: postmortem reviewer skills candidates explores opus routine claude-ai anti-injection self-learning DA-48 sentinel
> @subs: auto

- **postmortem-deep** (Claude.ai routine 03h): dispatches 5 Explores (errors / friction / decisions / skill-patterns / outcomes), each doing grep→excerpt (never full-read = anti-timeout). Synthesis → `~/.claude/postmortems/*.md` + skill-candidates in `.claude/skill-candidates/`. Prompt versioned under `infra/hermes/routines/postmortem-deep.md`.
- **claude-skill-reviewer** (Claude.ai routine 04h): Layer 1 anti-injection regex (8 languages) → Layer 2 anonymized semantic review → Layer 3 deterministic floor. Verdict promote/review/archive. `.claude/skills/claude-skill-reviewer/SKILL.md`.
- **Anti-loop (sentinel)**: routine sessions carry `ROUTINE-SENTINEL: ratis-automated-routine-do-not-postmortem` → postmortem skips them (otherwise self-analysis). Content-signature fallback for old runs.
- **Result**: 56 candidates processed → **skills library 4 → 31** (2 batches promoted, clusters merged, duplicates/subsumed archived). Promotion is **human-gated** (operator decides). Dependencies: Claude.ai app open on Mac mini + routines quota (15/week).

---

## HO-5 — Hermès-as-MCP inside Claude Code · ARCH_hermes_ops.md · LIVRÉ V0

> TL;DR: `hermes mcp serve` registered as an MCP stdio server in Claude Code (`claude mcp add hermes`) → 10 tools (messages_send, conversations, events, …). Claude Code can drive Hermès (push Telegram, read convos). Distinct from our custom agent-mcp (which remains the Keychain-backed typed tools layer).
> @tags: mcp serve claude-code stdio bidirectional messages-send agent-mcp
> @subs: auto

- Registered user scope: `docker exec -i ratis-hermes hermes mcp serve`. 10 tools `mcp__hermes__*`.
- Visible at the **next Claude Code session start** (MCP loaded at boot).
- ≠ agent-mcp (`tools/agent-mcp/`, see ARCH_agent_mcp): agent-mcp = Ratis Keychain-backed typed tools; Hermès-MCP = conversational bridge. Complementary.

---

## HO-6 — Versioned stack infra/hermes + restoration · ARCH_hermes_ops.md · LIVRÉ V0

> TL;DR: the entire stack (secrets-stripped config templates, scripts, postmortem skill, routines, compose, glitchtip, glt) versioned under `infra/hermes/` + idempotent `deploy.sh`. Before: everything lived locally unsaved. Secrets → `${VAR}` read from Keychain, CI detect-secrets gate.
> @tags: infra-as-code deploy backup versioning secrets-template keychain detect-secrets recovery runbook
> @subs: auto

- `infra/hermes/`: `deploy.sh` (rebuilds `~/.hermes`/`~/hermes`/`~/glitchtip` + renders config from Keychain + installs faster-whisper) · `.env.example` (keys only) · `hermes-home/` · `hermes-compose/` · `glitchtip/` · `routines/` · `kanban-snapshot.*`.
- **0 committed secrets**: `*.template.*` → `${VAR}`, values in Keychain `ratis-agent-mcp`. `detect-secrets` gate.
- **Fresh machine restoration**: `git clone` → `deploy.sh` → **4 irreducible manual steps**: re-auth Codex (OAuth), re-pair Telegram, paste routines into Claude.ai, `docker compose up -d`.
- Runtime state (memory/kanban/auth): covered by `hermes-state-backup` (HO-2) + kanban-snapshot (HO-3).

---

## HO-7 — Adapters & assumed debt · ARCH_hermes_ops.md · LIVRÉ V0

> TL;DR: 2 pragmatic adapters honestly tracked vs R33 — auto-codex-reset (band-aid on the Codex exhausted flag; real fix = fallback provider, deferred for lack of a 2nd key) and the GlitchTip→Hermès HMAC proxy (sidecar because GlitchTip does not sign its webhooks).
> @tags: tech-debt R33 adapter auto-codex-reset hmac-proxy fallback-provider workaround honest
> @subs: auto

- **auto-codex-reset**: Codex Plus marks the credential `exhausted` on 429 but does not clear it when `reset_at` passes → hourly cron that clears it. **Band-aid**: the root cause (Codex Plus OAuth not designed for 24/7 agentic use, see KP-102) would be properly fixed with `fallback_providers` (Anthropic/OpenRouter key) — deferred because no 2nd key has been provided.
- **HMAC proxy** (`hermes-compose/glitchtip-proxy/proxy.py`): GlitchTip does not expose a secret field on its webhooks (schema `WebhookAlertRecipientIn`) → a Python sidecar re-signs (`X-Webhook-Signature` HMAC-SHA256) before forwarding to Hermès. Security supplemented by Docker network isolation.
- **kanban-snapshot auto-commit**: if scheduled, `kanban-snapshot.sh` pushes the data file directly to main (assumed deviation vs PR-required — data, not code; commit-if-changed). Currently run manually / via PR.

---

## Out of scope

- **Incident management** (GlitchTip stack, projects, glt, ingestion) → `ARCH_incident_management.md` (HO-1/HO-2 only *consume* alerts).
- **agent-mcp** (Ratis Keychain-backed typed tools) → `ARCH_agent_mcp.md`.
- **n8n pipelines** → `ARCH_n8n_pipelines.md`.
- The exhaustive detail of all 31 skills → `.claude/skills/*/SKILL.md` (versioned, not ARCH-indexed).
