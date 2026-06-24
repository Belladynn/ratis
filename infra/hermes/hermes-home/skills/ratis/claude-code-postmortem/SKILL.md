---
name: claude-code-postmortem
description: "Analyze Claude Code session JSONL transcripts to produce post-mortems and propose candidate skills."
version: 0.1.0
author: Ratis
license: MIT
platforms: [linux, macos]
metadata:
  hermes:
    tags: [ratis, claude-code, postmortem, skills-discovery, internal]
prerequisites:
  commands:
    - python3                     # runs inside ratis-hermes container (Python 3.13)
  env:
    optional:
      - HERMES_POSTMORTEM_MODEL          # default: gpt-5.5 (passed to Hermes CLI, OAuth ChatGPT Plus)
      - HERMES_POSTMORTEM_TIMEOUT        # default: 300 (seconds per Hermes chat call)
      - HERMES_POSTMORTEM_HERMES_BIN     # default: hermes (in-container PATH)
      - HERMES_CLAUDE_PROJECTS_DIR       # default: /opt/claude/projects (RO mount of ~/.claude/projects)
      - HERMES_POSTMORTEM_OUTPUT_DIR     # default: /opt/claude/postmortems (RW mount)
      - HERMES_POSTMORTEM_STATE_PATH     # default: /opt/data/state/claude-postmortem-state.json
      - HERMES_POSTMORTEM_AUDIT_LOG      # default: /opt/data/state/claude-postmortem-audit.jsonl
      - HERMES_POSTMORTEM_QUIET_SECONDS  # default: 900 (15 min — session-idle threshold)
      - HERMES_POSTMORTEM_STRATEGY        # default: heuristic; options: heuristic|tiered|llm
      - HERMES_POSTMORTEM_PREPROCESS_MAX_CHARS # default: 20000
      - HERMES_POSTMORTEM_LLM_MAX_CHARS   # default: 120000 guardrail in tiered mode
---

# claude-code-postmortem

Ingests Claude Code session transcripts (`~/.claude/projects/*/*.jsonl`),
parses them, redacts Tier-S secrets, and defaults to a deterministic local
heuristic classifier (`happy path` / `incident`) so cron cannot time out on huge
transcripts. Codex/Hermes LLM analysis remains available on demand via
`--strategy llm`, or conditionally via `--strategy tiered`, to produce deeper
post-mortems plus optional skill candidates for the worktree's
`.claude/skill-candidates/`.

Designed to be run by a Hermes cron every **2 hours** (was 30min until
2026-06-01, lowered to ×4 reduce Codex quota burn — see `hermes cron list`
job `d2646d0ee94f` and DECISIONS_ACTED.md DA-49). Rate-limited intelligently:
does **nothing** when the queue is empty or the hourly cap is reached. No
tokens wasted on a quiet hour.

## When to Use

- A Hermes cron schedules this skill every 2 hours (`0 */2 * * *`).
- The operator wants to manually analyze a specific session
  (e.g. `python scripts/postmortem.py --session <path>.jsonl`).
- The operator wants to validate the parser/redactor pipeline
  (e.g. `... --dry-run --no-llm`).

## When NOT to Use

- During development on a session that is still active — wait until the session
  has been idle for 15 minutes (the script enforces this automatically).
- When you intend to send raw transcripts to a cloud LLM yourself — this skill
  already redacts; don't double-pipe.

## Procedure

When asked to inspect or summarize “pending skills” / postmortem-generated candidates, use the checklist in `references/pending-skill-candidates.md`. Key pitfall: `candidates_count > 0` in the audit log is not enough to summarize candidates; only readable `<worktree>/.claude/skill-candidates/*/SKILL.md` files or explicit `PENDING REVIEW` blocks are evidence of accessible pending skills.

The cron-driven path (default):

- Hermes invokes `~/.hermes/scripts/postmortem.sh` (a thin wrapper that
  `exec`s the canonical `/opt/data/skills/ratis/claude-code-postmortem/scripts/postmortem.py`
  inside the same container).
- The script reads JSONL transcripts from `/opt/claude/projects/` (read-only
  mount of `~/.claude/projects/`) and writes postmortems to
  `/opt/claude/postmortems/` (RW mount of `~/.claude/postmortems/`).
- Hermes delivers stdout (one terse summary line) to Telegram. No agent loop,
  no LLM cost on top of the Codex calls the script itself makes.

Manual usage from the host (operator runs):

```bash
docker exec ratis-hermes python3 /opt/data/skills/ratis/claude-code-postmortem/scripts/postmortem.py [flags]
```

Or, from inside the container (`docker exec -it ratis-hermes bash`):

```bash
python3 /opt/data/skills/ratis/claude-code-postmortem/scripts/postmortem.py [flags]
```

Useful flag combinations:

| Goal | Flags |
|---|---|
| Dry-run on one specific session (default local heuristic, no Codex spend) | `--session <path> --dry-run --verbose --force` |
| Real run on one specific session (default local heuristic) | `--session <path> --force` |
| Deep LLM analysis on demand | `--session <path> --strategy llm --force` |
| Conditional routing: skip LLM for happy/huge sessions | `--session <path> --strategy tiered --force` |
| Cron mode (process queue, respect rate limit, local heuristic by default) | *(no flags)* |
| Re-analyze everything | `--force` |

**Outputs**:
- **Post-mortem report**: `/opt/claude/postmortems/YYYY-MM-DD-<session_id_short>.md`
  (= `~/.claude/postmortems/...` on the host — same dir, two views).
- **Candidate skills** (one dir each):
  `<worktree>/.claude/skill-candidates/<skill_name>/SKILL.md`
- **Audit trail** (one JSONL line per run):
  `/opt/data/state/claude-postmortem-audit.jsonl`.
- **State** (mtime cache + hourly counters):
  `/opt/data/state/claude-postmortem-state.json`.

3. Privacy: every transcript is passed through the Tier-S regex redactor
   (`scripts/redactor.py`) **before** being sent to Codex. Redactions are
   counted and logged.

## Rate-limit behaviour

- **Off-peak (02h-06h local)**: up to **10 sessions / hour**.
- **Normal hours**: up to **3 sessions / hour**.
- A session is queued only if (a) its JSONL mtime moved since last analysis
  AND (b) the file has been idle ≥ 15 minutes (assumed finished).
- Empty queue → immediate exit, zero Codex calls.
- Combined with the 2h cron cadence, the hard upper bound is ≈ 12 Codex calls
  per day (typical: 2-4) — well below the ChatGPT Plus quota.

## Testing

```bash
cd /Users/guillaume/.hermes/skills/ratis/claude-code-postmortem
python -m pytest tests/ -v
```

## Source of truth & deploy

This skill currently lives only at `~/.hermes/skills/ratis/claude-code-postmortem/`
(mounted into the container at `/opt/data/skills/ratis/claude-code-postmortem/`).
There is no separate canonical copy in the Ratis repo today — when a versioned
canonical is reintroduced, update this section and add a deploy target.

## Invocation flow (final, 2026-06-01)

```
Hermes cron job d2646d0ee94f (schedule 0 */2 * * *, --no-agent --deliver telegram)
  → /opt/data/scripts/postmortem.sh
    → exec python3 /opt/data/skills/ratis/claude-code-postmortem/scripts/postmortem.py
      → reads /opt/claude/projects/*.jsonl (RO mount of ~/.claude/projects)
      → redacts (Tier-S) + local heuristic classifier by default
        (Codex/Hermes only with --strategy llm or selected tiered cases)
      → writes /opt/claude/postmortems/YYYY-MM-DD-<id>.md
      → writes /opt/data/state/*.{json,jsonl}
      → stdout = one terse summary line → Telegram via hermes cron --deliver
```
