---
name: uv-python-command-discipline
description: "Avoid system python / pytest mismatches by discovering the project's real test runner and invoking it through uv run from the package directory that owns the suite."
---

# uv-python-command-discipline

A bare `python` or `pytest` invocation in a uv workspace fails in several
recurring ways : `python: command not found` (only `python3` exists), the
wrong interpreter (system Python instead of the pinned 3.12), or
"no tests ran / module not found" because the command ran from the repo
root instead of the package that owns the suite. The clean discipline is :
never assume an interpreter or a CWD — discover the runner and run it
through `uv run` from the correct package.

On Ratis specifically the canonical runner is `scripts/run-tests.sh`
(auto-detects `--package` from the path) ; the raw form is
`uv run --package <pkg> pytest <target>`.

## When to Use

- A Python command fails with `python: command not found`, a missing
  `pytest`, or a wrong-interpreter error.
- Tests collect nothing or raise import / package-resolution errors when
  run from the repo root.
- You are about to run a test suite in a uv workspace and want the
  command right the first time.

## When NOT to Use

- A non-uv project (plain venv / Poetry / system Python) — use that
  project's documented runner instead.
- A one-off script with no package context where `python3 foo.py` is
  unambiguous and correct.
- The failure is a real test assertion or logic error, not an
  interpreter / invocation problem — debug the test, not the runner.

## Procedure

1. **Discover before invoking.** Identify the package layout and the
   project's documented test runner before typing a command. On Ratis :
   `scripts/run-tests.sh` is the wrapper ; the `pkg → svc dir` mapping
   lives in `SA_DEV.md`.
2. **Never call bare `python` / `pytest`.** Route everything through
   `uv run` so you get the pinned interpreter (3.12) and the workspace's
   resolved environment — never the system one.
3. **Run from the package that owns the suite.** Use
   `uv run --package <pkg> pytest <target>` (or the wrapper, which
   auto-detects `--package` from the path). Running from the wrong CWD is
   the usual cause of "no tests ran".
4. **On failure, shrink before reporting.** Re-run the smallest verified
   command (a single test file or node id) to confirm whether the problem
   is the invocation or the code, then report the precise failing target.
