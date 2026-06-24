# claude-code-postmortem (operator README)

> Auto-analyzes Claude Code session transcripts via Codex (ChatGPT Plus OAuth,
> through the `ratis-hermes` container) to surface patterns and propose new
> skills. Designed for an unattended cron loop with sane rate-limits.

For the **agent-facing** procedure see [`SKILL.md`](./SKILL.md). This file is
for the human operator who runs / deploys / debugs the skill.

## What it does

1. Walks `~/.claude/projects/*/*.jsonl` for any session whose `mtime` moved
   since last analysis AND that has been idle ≥ 15 min (assumed finished).
2. Parses the JSONL into a compact markdown transcript (drops noise types,
   thinking blocks, truncates tool outputs).
3. Runs Tier-S regex redaction (`scripts/redactor.py`) — strips Stripe / GitHub
   / JWT / Notion / IBAN / credit-card patterns BEFORE leaving the host.
4. Shells out to `docker exec ratis-hermes hermes chat -Q -v -q '<prompt>'`
   with the redacted transcript + the JSON schema we want back.
5. Writes a post-mortem markdown to `~/.claude/postmortems/<date>-<short>.md`.
6. Drops skill candidates under
   `<worktree>/.claude/skill-candidates/<name>/SKILL.md` for review.
7. Appends a one-line JSON audit entry to
   `~/.hermes/state/claude-postmortem-audit.jsonl`.

## Why Hermes instead of `OPENAI_API_KEY`

The previous client POST'd directly to the OpenAI Chat Completions API and
needed an `OPENAI_API_KEY` env var with metered billing on top of the
operator's existing ChatGPT Plus seat. Switching to `docker exec ratis-hermes
hermes chat` reuses the OAuth credentials already configured in the container
(`OpenAI Codex` provider, default model `gpt-5.5`) — one fewer secret, one
fewer line item on the bill.

The wireformat parser handles both Hermes outputs:

- `-Q/--quiet` mode (preferred) — plain text reply on stdout.
- Box-drawing fallback (`╭─ ⚕ Hermes ─╮ ... ╰─╯`) — defensive, in case the
  `-Q` contract is ever dropped.

## Quick start

```bash
# 1. Deploy the skill to the runtime location.
make -C tools/hermes-skills deploy SKILL=claude-code-postmortem

# 2. Dry-run on one session (no Hermes call, no writes).
python ~/.hermes/skills/ratis/claude-code-postmortem/scripts/postmortem.py \
  --session ~/.claude/projects/<encoded-worktree>/<session-uuid>.jsonl \
  --dry-run --no-llm --force --verbose

# 3. Real run on one session (calls Hermes/Codex, writes report).
python ~/.hermes/skills/ratis/claude-code-postmortem/scripts/postmortem.py \
  --session ~/.claude/projects/<encoded-worktree>/<session-uuid>.jsonl \
  --force --verbose

# 4. Cron mode — process the queue, respect rate-limit.
python ~/.hermes/skills/ratis/claude-code-postmortem/scripts/postmortem.py
```

## CLI flags

| Flag | Effect |
|------|--------|
| `--session PATH` | Analyze a single JSONL (bypasses discovery + rate-limit). |
| `--dry-run` | Do not write the post-mortem or candidate skills. |
| `--no-llm` | Skip the Hermes call — use a stub response. Useful for pipeline tests. |
| `--force` | Ignore the mtime cache (re-analyze even if unchanged). |
| `--max-sessions N` | Cap per-run (default 10). |
| `--verbose` / `-v` | Extra logging on stderr. |

## Rate-limit

- **Normal hours**: 3 sessions / hour.
- **Off-peak 02h-06h local**: 10 sessions / hour.
- Counters live in `~/.hermes/state/claude-postmortem-state.json`. Wipe the
  file to reset (does not delete past reports).

## Env vars

All optional, sane defaults:

| Var | Default | Purpose |
|-----|---------|---------|
| `HERMES_POSTMORTEM_MODEL` | `gpt-5.5` | Reported in the audit only; Hermes picks its own default. |
| `HERMES_POSTMORTEM_CONTAINER` | `ratis-hermes` | `docker exec` target. |
| `HERMES_POSTMORTEM_TIMEOUT` | `300` | Seconds per Hermes call. |
| `HERMES_POSTMORTEM_DOCKER_BIN` | `docker` | Override for podman / nerdctl. |
| `HERMES_POSTMORTEM_HERMES_BIN` | `hermes` | Path inside the container. |
| `HERMES_CLAUDE_PROJECTS_DIR` | `~/.claude/projects` | Source of JSONL transcripts. |
| `HERMES_POSTMORTEM_OUTPUT_DIR` | `~/.claude/postmortems` | Where reports land. |
| `HERMES_POSTMORTEM_STATE_PATH` | `~/.hermes/state/claude-postmortem-state.json` | mtime + counter cache. |
| `HERMES_POSTMORTEM_AUDIT_LOG` | `~/.hermes/state/claude-postmortem-audit.jsonl` | Per-run audit JSONL. |
| `HERMES_POSTMORTEM_QUIET_SECONDS` | `900` | Idle window before a session is "finished". |

## Tests

```bash
# From the repo.
cd tools/hermes-skills/claude-code-postmortem
uv run --with pytest pytest tests/ -v
```

31 tests covering the JSONL parser, the redactor, the chunker, the state
machine, the Hermes wireformat parser, and a smoke E2E in `--dry-run --no-llm`
mode.

## Debugging a bad run

1. Look at the audit log:
   `tail -1 ~/.hermes/state/claude-postmortem-audit.jsonl | jq`.
2. Re-run the same session with `--verbose --force` — stderr will tell you
   which step failed (parse / redact / Hermes call / JSON decode).
3. If Hermes itself is unhealthy:
   `docker exec ratis-hermes hermes status` — confirm
   `OpenAI Codex   ✓ logged in`. If logged out, re-auth and retry.
4. If a candidate skill looks wrong, edit the `SKILL.md` directly under
   `.claude/skill-candidates/<name>/` and promote it manually.

## ROI scoring — promote ou archive en un coup d'œil

Chaque skill candidate généré inclut un **ROI score** calculé par Codex pendant
l'analyse. Le verdict (`promote` / `review` / `archive`) tient en haut du
`SKILL.md` candidate et dans le rapport postmortem, pour que tu décides
instantanément sans relire toute la procédure.

| Verdict | Critères | Action recommandée |
|---|---|---|
| 🟢 **promote** | `frequency_in_session >= 2` ET `reusability != "low"` | `mv` vers `.claude/skills/` |
| 🟡 **review** | Borderline (frequency limite ou procedure ambiguë) | Éditer le SKILL.md candidate, puis re-décider |
| 🔴 **archive** | `frequency == 1` OU `reusability == "low"` (bug one-shot trop spécifique) | `mv` vers `.claude/skill-archive/` avec raison |

Le score inclut aussi :
- `frequency_in_session` — combien d'occurrences réelles dans la session
- `reusability_outside_context` — high/medium/low (applicabilité hors bug spécifique)
- `operator_cost_saved` — high/medium/low (gain estimé si skill avait existé)
- `specificity_warning` — ce qui rend le skill trop étroit (nul si pas applicable)
- `verdict_reason` — 1 phrase de justification

Dans le rapport MD, les candidates sont **triés par verdict** (promote en haut,
archive en bas) pour que tu voies les plus pertinents en premier.

## Skill lifecycle — 3 buckets

The postmortem keeps an inventory of skills in **three locations** so the LLM
can avoid both duplicates AND re-proposing rejected ideas across sessions:

| Bucket | Path | Versioned? | Role |
|---|---|---|---|
| **Active** | `.claude/skills/<name>/SKILL.md` | yes (git-tracked) | Validated, ready to use. The postmortem treats these as "already done, do not duplicate". |
| **Pending review** | `.claude/skill-candidates/<name>/SKILL.md` | no (gitignored) | LLM-proposed by a previous postmortem run, awaiting human decision. The postmortem treats these as "do not re-propose duplicates; if the same pattern recurs, suggest an enhancement of the existing candidate". |
| **Archived (rejected)** | `.claude/skill-archive/<name>/SKILL.md` | yes (git-tracked) | Explicitly rejected by the operator, kept with a `## Why archived` section explaining the reason. The postmortem treats these as "DO NOT propose again — the rejection is final". |

Operator workflow:

| Decision | Command |
|---|---|
| **Promote** a candidate (looks good, becomes active) | `mv .claude/skill-candidates/X .claude/skills/X` |
| **Archive** a candidate with rationale (rejected, kept for memory) | `mv .claude/skill-candidates/X .claude/skill-archive/X` then add a `## Why archived` section to its `SKILL.md` |
| **Drop** a candidate (genuinely useless, no memory needed) | `rm -rf .claude/skill-candidates/X` |

The LLM prompt receives all three buckets as a 3-section block (ACTIVE /
PENDING REVIEW / ARCHIVED). This is the anti-doublon and anti-re-proposition
mechanism: rejected skills stay rejected across sessions because the archive
is git-tracked and seeded into every future postmortem call.
