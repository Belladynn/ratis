---
name: detect-secrets-false-positive-pragmas
description: "Triage detect-secrets CI failures by separating true secrets from stable identifiers / CLI names, then place the allowlist pragma on the exact flagged line."
---

# detect-secrets-false-positive-pragmas

The `Detect secrets` CI step fails on a string that *looks* like a
credential but is a stable identifier, generated CLI name, provider ID,
or documentation token. The fix is a targeted allowlist pragma — but only
after confirming the value is genuinely non-secret, and placed on the
**exact** flagged line (a common pitfall: the pragma must be on the line
detect-secrets reports, not the line above). This skill is the triage +
correct-placement workflow.

## When to Use

- GitHub CI `Detect secrets` fails on code strings, generated CLI names,
  provider IDs, or documentation text that are not actual secrets.

## When NOT to Use

- The flagged value is, or might be, a real credential — never allowlist
  it; rotate/remove the secret and scrub history instead.
- You cannot establish that the value is non-secret and stable — leave it
  flagged and escalate rather than silencing an unknown.
- Using the pragma to mask a true secret "to unblock CI" — that is
  exactly what R33 / the secret-pragma rule forbids.

## Procedure

1. **Find the exact location.** Read `gh run view --log-failed` to get
   the precise file and line detect-secrets flagged.
2. **Confirm it's non-secret.** Verify the value is a stable, public,
   non-credential string (CLI name, provider ID, doc text). If you can't
   prove it's safe, stop — never allowlist an unknown.
3. **Pragma the exact line.** Add `# pragma: allowlist secret` on the
   **same line** that was flagged (off-by-one placement won't suppress
   the finding).
4. **Re-verify.** Run detect-secrets / lint locally if available, commit,
   and confirm the CI check goes green.
