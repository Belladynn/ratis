---
name: prod-alembic-migration-runbook
description: "Run and verify production Alembic migrations safely via Docker Compose — handling env-file/image-rebuild drift, prod-behind-by-several-revisions, and stale-PR double-head detection and repair."
---

# prod-alembic-migration-runbook

Applying an Alembic migration in production via `docker compose --profile
migrate` (on Hetzner / `ratis-prod`) has three recurring failure classes
that this runbook covers end-to-end:

- **Env / image drift** — the migrations image is stale after `alembic/`
  changed, or a required secret isn't passed into the migrations
  container.
- **Prod behind** — production is several revisions back and the upgrade
  chain must be applied and verified in order.
- **Double head** — parallel PRs each green in isolation merged to a repo
  with two Alembic heads, so `upgrade head` (or a head check) fails on
  main.

## When to Use

- Asked to apply a production migration via `docker compose --profile
  migrate` / Alembic on the prod host.
- Prod is several revisions behind, compose env validation fails, or
  Alembic reports multiple heads after merging parallel PRs.
- A PR's CI was green in isolation but main fails on Alembic
  upgrade/head checks.

## When NOT to Use

- A local/dev migration — run it through the normal autogen→verify→
  `upgrade head` flow; the prod-host safeguards are overhead.
- Schema design / writing the migration itself — that's the
  authoring/TDD step; this runbook is about *applying* one safely.
- Non-migration prod compose failures (missing service env unrelated to
  migrations) — see `prod-compose-required-env-audit`.

## Procedure

1. **Verify prod + chain state first.** Check the prod repo git state and
   the pending Alembic revision chain. Confirm whether prod is behind and
   by how many revisions before running anything.
2. **Detect double heads up front.** Confirm the number of heads on
   `origin/main` (`alembic heads`). If there are two, identify the branch
   parents — this is the stale-parallel-PR merge signature.
3. **Repair a double head before upgrading.** Generate/add an Alembic
   **merge migration** to restore a single head; verify the CI head-check
   guard / auto-heal workflow is in place so future stale parallel merges
   are caught, and document residual semantic-conflict limits.
4. **Rebuild the image when `alembic/` changed.** If migration files
   changed, rebuild the migrations image — a stale image runs old
   revisions.
5. **Validate compose with the real env.** Use `--env-file .env.prod`;
   reconcile `${VAR:?}` references and ensure required vars are actually
   passed into the **migrations container** (not just available for
   interpolation). Never dummy a real secret — use validation-only
   placeholders solely for vars the migration doesn't actually use.
6. **Run the migration** via `docker compose --profile migrate run --rm
   migrations` (apply the full pending chain in order).
7. **Verify after.** Confirm `alembic_version` matches the expected head
   and the expected DB artifacts (tables/columns/constraints) exist.
8. **Clean up + record.** Remove any temporary files, and record
   follow-ups (e.g. CI guard gaps, residual conflicts) for the operator.

## PR rebase gate — prevent the double head at the source

The double-head repair in step 3 is a *recovery*. The cheaper move is to
catch the stale chain on the PR **before** it merges. A migration PR
authored before newer migrations landed on `origin/main` carries a
`down_revision` that no longer points at the current head — merge it and
main gets two heads.

Run this gate whenever a migration PR is more than a few commits behind
main, or before merging any PR that adds an Alembic revision :

1. **Fetch and locate the real head.** `git fetch origin`, then determine
   the current single Alembic head on `origin/main` (`alembic heads` on
   the updated main).
2. **Compare the PR's chain links.** Inspect the new migration's
   `revision` and `down_revision` against that head. If `down_revision`
   doesn't point at the current head, the chain is stale — the PR will
   fork the history.
3. **Rebase and re-chain.** Rebase the PR branch onto `origin/main` and
   re-point the migration's `down_revision` to the current head (re-chain
   it after the newly-landed revisions).
4. **Re-verify single head.** Confirm exactly one head remains
   (`alembic heads`) and rerun `alembic check` / the migration tests
   before merging. A clean single-head PR never triggers the step-3
   recovery on main.
