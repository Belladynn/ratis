# Contributing to Ratis

Ratis is primarily a solo-developed portfolio project, but it is structured so
that anyone can clone it, run it, and propose a change with confidence. This
guide covers the development environment, the commit convention, the quality
gates a pull request must clear, and a note on how the repository itself is
built.

> By contributing, you agree that your contributions are licensed under the
> project's [Apache License 2.0](LICENSE). Please also read the
> [security policy](SECURITY.md) before reporting anything sensitive.

## Development environment

Prerequisites:

- **Python 3.12** — pinned via [`.python-version`](.python-version). Newer
  Python (3.13+) is intentionally unsupported because PaddleOCR ships no wheel
  for it; `pyproject.toml` encodes this as `requires-python = ">=3.12,<3.13"`.
- **[uv](https://docs.astral.sh/uv/)** — the package and workspace manager.
  Do **not** use `pip` directly; this is a uv workspace with a single committed
  lockfile.
- **Docker** (with Compose v2) — for Postgres 16, Redis 7, and OSRM.

Set up the workspace:

```bash
# 1. Bring up the backing services (Postgres, Redis, OSRM)
docker compose up -d

# 2. Install the full workspace from the committed lockfile (reproducible)
uv sync --frozen

# 3. Install the git hooks (ruff, ruff-format, uv-lock, gitleaks, mypy)
uv run pre-commit install

# 4. Point the app at the dev database and run migrations
export DATABASE_URL=postgresql+psycopg://ratis:ratis@localhost:5432/ratis_dev
docker compose --profile migrate run --rm migrations   # alembic upgrade head
```

A few workspace conventions worth knowing:

- Add dependencies with `uv add <pkg>` **inside the package that needs it**
  (e.g. a service directory), never at the repo root.
- Monetary amounts are integer cents end-to-end — never floats.
- SQL lives only in repository classes; services call repositories, routes call
  services. Business errors are raised in services; routes translate them to
  `HTTPException`.

## Commit convention

Commits follow **[Conventional Commits](https://www.conventionalcommits.org/)**.
Use one of these types and keep the subject imperative and concise:

```
feat:     a new user-facing capability
fix:      a bug fix
chore:    tooling / housekeeping with no runtime effect
refactor: a behavior-preserving code change
test:     adding or correcting tests
docs:     documentation only
infra:    infrastructure / IaC / deployment
ci:       CI/CD configuration
```

Examples:

```
feat(rewards): add referral payout idempotency guard
fix(notifier): correct quiet-hours boundary off-by-one
docs(adr): record RS256 single-issuer JWT decision
```

Do not put credentials, tokens, or secrets in commit messages or in the tree.

## Branch and pull-request flow

This repository follows **GitHub Flow**: `main` is protected and always
releasable.

1. Branch from `main` with a type-prefixed name: `feat/...`, `fix/...`,
   `chore/...`, `docs/...`, `infra/...`, `ci/...`.
2. Make your change with tests. Keep commits atomic.
3. Push and open a pull request against `main`.
4. Ensure CI is green (see the gates below). **Never merge a red PR.**

## Quality gates

Every pull request must pass the following. Run them locally before pushing —
the pre-commit hooks cover most of them automatically, and CI mirrors them so a
hook cannot be bypassed with `--no-verify`.

| Gate | Command | What it checks |
| ---- | ------- | -------------- |
| **Lint** | `uv run ruff check .` | Lint rules (`I`, `B`, `UP`, `SIM`, `C4`, `S`, `RUF`, …). |
| **Format** | `uv run ruff format --check .` | Ruff is the declared formatter (double quotes). |
| **Types** | `uv run --group typecheck ./scripts/run-mypy.sh` | Static type-checking of the 5 services + `ratis_core` + `tools/agent-mcp`. |
| **Tests** | `uv run --package <pkg> pytest` | Python tests (per package; SAVEPOINT-isolated DB fixtures). |
| **Secrets** | `gitleaks detect` / `detect-secrets` | No credentials enter the tree (pre-commit + CI). |

> **`scripts/run-mypy.sh`** exists because this is a uv workspace where several
> packages ship top-level modules with the same name (each service has its own
> `main.py`, `routes/`, `services/`). It type-checks each package as its own
> root so mypy doesn't collide on fully-qualified module names.

On GitHub, the single **required** status check is the **`ci-passed`**
aggregator job in [`.github/workflows/ci.yml`](.github/workflows/ci.yml). It
`needs:` every per-service job and inspects their results, so path-filtered
skips can't report green by omission — the branch ruleset only has to gate on
`ci-passed`.

Frontend changes under `ratis_client/` additionally run the Jest suite
(`npm test`).

## How this repo is built

Ratis is developed with a deliberate **agentic-development methodology**: a
single human operator works alongside a fleet of Claude Code agents — a
planning **orchestrator** that dispatches **typed subagents** (dev, exploration,
review) under explicit, versioned operating rules. Those rules are not hidden
scaffolding; they are committed and documented as a feature of the repository.

If you want to understand the conventions a change is expected to follow — the
recon-before-design discipline, the test-first workflow, the documentation and
decision-logging rules — read [`docs/agents/`](docs/agents/), starting with its
[README](docs/agents/README.md). The shared agent reference
([`docs/agents/CLAUDE.md`](docs/agents/CLAUDE.md)) is, in effect, the canonical
contributor handbook for this codebase.
