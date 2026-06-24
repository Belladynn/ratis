# Pending skill candidates: inspection checklist

Use this when asked to summarize or review “pending skills”, “pending candidates”, or postmortem-generated skill proposals.

## Meaning

Postmortem skill candidates are not active Hermes skills. They are generated proposals under a worktree-local path:

- `<worktree>/.claude/skill-candidates/<skill_name>/SKILL.md`

Promoting a candidate usually means moving that directory into the worktree's `.claude/skills/`; archiving means moving it to `.claude/skill-archive/` with a short rationale.

## Fast inspection sequence

1. Search accessible worktrees for directories named `skill-candidates`.
2. If found, read the first lines of each `SKILL.md` and summarize:
   - candidate name
   - frontmatter status/version if present
   - first paragraph / ROI verdict
   - whether it looks class-level or too session-specific
3. If no candidate directory is found, check the postmortem prompt cache for `PENDING REVIEW` blocks. These list previously known candidates injected into later LLM prompts.
4. Check `/opt/data/state/claude-postmortem-audit.jsonl` for `candidates_count > 0`. Treat those as evidence that candidates may have been generated, but do not claim they are readable unless the referenced paths exist.
5. If report paths point to a host path unavailable inside the container, say so directly and ask for the mounted path or host-side access.

## Reporting rule

Do not invent candidate summaries from audit counts. Counts prove generation attempts, not readable pending skills. If `PENDING REVIEW` says `- (none)` and no `skill-candidates` directory is accessible, report “none accessible here” with file/line evidence.
