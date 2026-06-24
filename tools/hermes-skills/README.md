# tools/hermes-skills — versioned Hermes skills for Ratis

This directory is the **source of truth** for every Hermes skill that lives
inside the Ratis repo (i.e. skills that we want code-reviewed, tested in CI,
and rolled back via `git revert` like any other piece of infrastructure).

The runtime copy lives at `~/.hermes/skills/ratis/` on the dev host (Mac mini)
and is consumed by Hermes (`docker compose up ratis-hermes`) and by the cron
that triggers each skill on its schedule. The runtime copy is **derived** —
never edit it by hand; the next deploy rsync will overwrite the change.

## Layout

```
tools/hermes-skills/
├── Makefile                              deploy + diff + test entry points
├── README.md                             this file
└── <skill-name>/                         one dir per skill
    ├── SKILL.md                          Hermes manifest (front-matter + procedure)
    ├── README.md                         optional — operator-facing usage doc
    ├── scripts/                          Python entry points + helpers
    └── tests/                            pytest suite (run via the Makefile)
```

## Deploy

```bash
# Deploy everything (rsync --delete; idempotent).
make -C tools/hermes-skills deploy

# Deploy a single skill.
make -C tools/hermes-skills deploy SKILL=claude-code-postmortem

# Dry-run — show what would change without writing.
make -C tools/hermes-skills diff

# Run every skill's pytest suite.
make -C tools/hermes-skills test
```

After each merge to `main` that touches `tools/hermes-skills/`, an operator
runs `make deploy` on the dev host. Automating this on the post-merge runner
is a V1+ improvement (`KP` / future ARCH).

## Why this lives in the repo

- **Versioning** — `git log` is the change history; PRs are the review channel.
- **Tests** — CI can run `pytest` on every skill before merge (R15 protects merges).
- **Rollback** — `git revert` + `make deploy` restores a known-good state.
- **Discoverability** — new operators see the skills the same way they see the
  rest of the platform code.

## Conventions for new skills

1. Pick a kebab-case name and create `tools/hermes-skills/<name>/`.
2. Always include a `SKILL.md` with the Hermes front-matter (`name`,
   `description`, `version`, `prerequisites.env`, `metadata.hermes.tags`).
3. Code in `scripts/` (Python 3.12, no third-party deps if avoidable — Hermes
   runs inside a minimal container).
4. Tests in `tests/` (pytest). The Makefile wires `uv run --with pytest pytest`
   for you.
5. Document operator-facing usage in `README.md` next to the skill (not in
   `SKILL.md`, which is for the agent).
6. Never hard-code paths under `/Users/guillaume/...` — read them from env
   vars with sensible defaults (`Path.home() / ".hermes" / ...`).
