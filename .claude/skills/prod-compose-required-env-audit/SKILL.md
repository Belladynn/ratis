---
name: prod-compose-required-env-audit
description: "Audit Docker Compose required variables (${VAR:?}) against .env.prod and against actual service environment propagation before running prod commands."
---

# prod-compose-required-env-audit

A prod compose command can fail two distinct ways on env vars : a
`${VAR:?}` interpolation reference with no value in `.env.prod`, or a var
that *interpolates fine* but is never actually passed into the target
service's `environment:` — so the container starts without it. This skill
audits both before you run a prod command or a migration that needs a
secret inside the container.

## When to Use

- A prod compose command fails with required-variable interpolation
  errors.
- A migration or service needs a secret available *inside* the container,
  not just for compose interpolation.

## When NOT to Use

- A local/dev compose with no required-var gating and no secret
  propagation concern.
- The failure is unrelated to env (image build error, port conflict,
  volume issue) — triage that instead.
- A one-off where the var is genuinely interpolation-only and never
  needed at runtime — confirm that, then skip the service-env wiring.

## Procedure

1. **Match `${VAR:?}` against `.env.prod`.** List every required
   reference and confirm each has a value in the prod env file.
2. **Check propagation, not just presence.** For each var, determine
   whether it's only available for *interpolation* or actually listed in
   the target service's `environment:` — a var can satisfy `${VAR:?}` yet
   never reach the container.
3. **Fix the wiring properly.** Add the missing service `environment:`
   entry via a PR (the durable fix). Use a temporary per-run env only
   when it's safe and the value isn't a long-lived secret.
4. **Re-run** the prod command and confirm both interpolation and runtime
   availability succeed.

## Stale-prod forward-deploy drift check

Deploying current `main` to a production environment that has been running
behind for a while fails in more ways than a missing `${VAR:?}`. Newly
*required* configuration that landed on main since the last deploy —
env vars, compose mounts, `app_settings` rows, per-service secret
mappings, n8n workflow versions, n8n environment variables, migration
prerequisites — is simply absent in the stale prod, so containers crash
or workflows misbehave even though code review passed. Run this drift
check around any forward deploy to a stale environment. (Ratis service
names below are examples — the reusable part is the drift sweep.)

**Before deploy — diff main against deployed prod for:**
1. **Required env vars** — beyond the `${VAR:?}` audit above, any var
   newly *added* to a service's `environment:` on main but missing from
   `.env.prod`.
2. **Compose mounts** — new bind mounts / volumes a service now expects.
3. **`app_settings` rows** — config moved into the DB that the stale prod
   row set doesn't have.
4. **Per-service secrets** — provider keys / webhook secrets newly mapped
   to a service.
5. **n8n** — workflow versions to re-import and n8n environment variables
   to set.
6. **Migration prerequisites** — schema the new code assumes (coordinate
   with `prod-alembic-migration-runbook`).

**After deploy — verify activation, don't assume it:**
- migrations applied to the expected head,
- every container healthy (not just started),
- logs clean of missing-config / crash-loop signatures,
- n8n workflow versions are the imported ones,

before declaring the deploy complete.
