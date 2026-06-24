---
name: prod-docker-env-mount-drift-triage
description: "Diagnose production containers that pass code/config review but crash at runtime because bind mounts or env-file values drift from repository assumptions."
---

# prod-docker-env-mount-drift-triage

A service can look correct in code and compose review yet crash in
production because the **runtime host** disagrees with the repo : a bind
mount points at a missing file, a directory was created where a file was
expected, or an env var that exists in `.env` is never forwarded into the
service. This skill is the SSH-side triage that finds the drift between
repository assumptions and the live host.

## When to Use

- A service works in code/config review but crashes in production due to
  missing files, a directory created in place of a file, or env vars
  present in `.env` but absent from compose forwarding.
- A prod container restarts/fails right after a deploy with no code
  change that would explain it.

## When NOT to Use

- The failure reproduces locally from the repo alone — it's a code/config
  bug, not host drift; fix it in the branch.
- A genuine application error visible in logs (stack trace in business
  logic) unrelated to mounts/env — debug the code path instead.
- You lack authorization for the prod host — escalate; do not improvise
  irreversible host changes.

## Procedure

1. **Look at the live container.** Check status and logs, then inspect
   the actual host file paths and `.env`-file values over SSH (e.g.
   `ratis-prod`). Confirm what the runtime really sees.
2. **Compare compose vs runtime needs.** Diff the service's
   `environment:` and `volumes:` against what the code requires at
   startup — is the var only available for interpolation, or actually
   passed into the service? Is the mount source a file or a directory?
3. **Fix the drift.** Correct the host file/path or add the missing
   compose forwarding, then recreate the affected containers with the
   right `--env-file`.
4. **Verify.** Re-check logs and uptime to confirm the container stays
   healthy after recreation.
