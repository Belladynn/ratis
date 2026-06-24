---
name: dockerized-self-hosted-runner-network-triage
description: "Diagnose CI failures where Dockerized self-hosted GitHub runners can no longer reach host services via localhost, and route to the correct service DNS / host-gateway / GH Actions service."
---

# dockerized-self-hosted-runner-network-triage

A self-hosted GitHub Actions job running inside a Docker container loses
the ability to reach a host service over `localhost` / `127.0.0.1` /
`::1` — typically after a runner, container, or networking change. The
fix is rarely the code : it's choosing the right network route between
container and target. This generalizes to any Dockerized self-hosted
runner hitting a host-vs-container localhost mismatch.

## When to Use

- A self-hosted GitHub Actions job inside Docker fails to connect to
  `localhost` / `127.0.0.1` / `::1` for a service that worked before a
  runner/container/networking change.
- The same connectivity error hits one cron and several sibling
  workflows that share the runner config.

## When NOT to Use

- The job runs directly on the host (not in a container) — localhost
  resolves normally; look elsewhere.
- The target service is genuinely down or misconfigured — fix the
  service first; this skill is about the network route, not the target's
  health.
- A pure application bug (wrong credentials, wrong path) that would fail
  identically outside CI — that is a code/config issue, not a route
  issue.

## Procedure

1. **Compare failing vs working jobs.** Diff env/secrets and confirm
   whether `localhost` is being used from *inside* a runner container vs
   from the host.
2. **Inspect the runner topology.** Read the runner `docker-compose`
   networks, service aliases, `extra_hosts`, and host port mappings.
   Confirm the intended target from docs or the operator.
3. **Pick the correct route and implement it:**
   - Compose sidecar → use the **service DNS name** on the shared
     network.
   - Host service → use `host.docker.internal` with `host-gateway` in
     `extra_hosts`.
   - Otherwise → migrate the workflow to an explicit **GitHub Actions
     `services:`** block so the dependency is provisioned per-job.
4. **Verify** by rerunning the affected job(s) and confirm the
   connection now succeeds end-to-end.
