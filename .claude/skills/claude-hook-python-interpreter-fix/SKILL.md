---
name: claude-hook-python-interpreter-fix
description: "Diagnose and fix Claude Code hook failures caused by hooks assuming a bare `python` exists when only `python3` is available, and triage recurring non-blocking hook errors that pollute sessions without stopping the task."
---

# claude-hook-python-interpreter-fix

A Claude Code hook (or a plugin-installed hook) runs under `/bin/sh` and
invokes a bare `python`. On a host where only `python3` exists, every
trigger emits `/bin/sh: python: command not found`. Two distinct problems
follow : (1) the hook itself is broken and must be repointed to a real
interpreter, and (2) if the hook is non-blocking, the error becomes
recurring session noise that doesn't stop work but can mask a *real* hook
failure. This skill covers both — the durable interpreter fix and the
non-blocking-noise triage.

## When to Use

- A hook error repeatedly shows `/bin/sh: python: command not found` (or
  any bare-`python` invocation failing because only `python3` is on PATH).
- A Claude Code session keeps emitting hook errors that don't block the
  main task but recur every turn and clutter the transcript.

## When NOT to Use

- The hook fails for a real reason (the script itself errors, wrong args,
  missing file) rather than a missing interpreter — debug the script.
- A genuinely one-off PATH glitch on a single machine that won't recur —
  fixing the environment once is enough; no durable hook change needed.
- The "hook" is actually your project's test runner — for `uv run` /
  package-resolution issues see `uv-python-command-discipline`.

## Procedure

1. **Identify the failing hook.** Locate which configured hook or plugin
   script invokes `python` — check `.claude/settings.json` (and
   `settings.local.json`) hook commands and any plugin-installed hooks.
2. **Repoint to a real interpreter.** Replace the bare `python` with
   `python3`, an absolute interpreter path, or the project-managed
   `uv run python` — whichever is reliably present in the hook's execution
   environment. Bare `python` is the bug; never reintroduce it.
3. **Verify in the same shell context.** Re-trigger / reload the hook and
   confirm `hook_success` with no command-not-found error. The hook runs
   under `/bin/sh`, so confirm the chosen interpreter resolves *there*,
   not just in your interactive shell.
4. **Triage non-blocking noise.** Distinguish hooks that merely pollute
   the session from hooks that affect task correctness. Fix the
   interpreter for all of them, but flag the blocking ones first — a wall
   of non-blocking errors can hide a hook that's actually breaking work.
