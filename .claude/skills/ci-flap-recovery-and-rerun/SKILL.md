---
name: ci-flap-recovery-and-rerun
description: "Diagnose and recover PR/main CI when failures are caused by flaky GitHub API or codeload connectivity rather than code, then rerun the affected jobs."
---

# ci-flap-recovery-and-rerun

CI on PRs, main, and self-hosted runners sometimes goes red for reasons
that have nothing to do with the diff : a transient GitHub API hiccup, a
`codeload`/checkout timeout, or runner-side network flakiness during
setup/download. This skill keeps you from "fixing" code that was never
broken, and gives a clean recover-and-rerun loop.

## When to Use

- GitHub checks fail during setup, checkout, or artifact download.
- `gh` commands time out or return 5xx intermittently.
- Self-hosted runners show network-related false reds that disappear on
  a rerun.

## When NOT to Use

- The failure is a real test/lint/build error reproducible locally — fix
  the code, never rerun to mask a genuine regression.
- The job log shows an assertion, type error, or constraint violation
  tied to the diff — that is a code failure, not a flap.
- A persistent infra outage (runner offline, DB down) — escalate and
  triage the host, do not loop reruns against a dead dependency.

## Procedure

1. **Localise the failure.** Read the job log and find where it stopped.
   A setup/checkout/download stage with a network error = infra suspect;
   a test/lint/build assertion = code failure (stop here, fix the code).
2. **Confirm it's infra, not code.** Probe GitHub API / codeload
   connectivity and runner health. Compare against jobs that passed in
   the same window — if unrelated jobs also flapped, it's the network.
3. **Rerun the failed jobs** (`gh run rerun --failed <id>`) and monitor
   to green. If a flap recurs on the same stage, retry with backoff
   rather than editing code.
4. **Document any policy override.** If you had to merge over a known
   infra flap, record the rationale (run id + evidence it was network,
   not code) so the override is auditable.
