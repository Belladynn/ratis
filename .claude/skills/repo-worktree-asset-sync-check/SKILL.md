---
name: repo-worktree-asset-sync-check
description: "When files are generated or discovered in the main checkout but the active session runs in a separate .claude/worktrees path, verify and synchronize the asset into the active worktree before validating or committing — so generated files aren't silently left behind."
---

# repo-worktree-asset-sync-check

Agent sessions often run in an isolated `.claude/worktrees/<name>` path
while a tool, script, or earlier step writes a generated file into the
**main** checkout (or vice versa). The file then appears to exist on disk
but is invisible to the active worktree's `git status`, so it never gets
committed — or a commit references an asset the worktree doesn't have.
This skill is the sync check : confirm which checkout owns your CWD, and
make sure generated assets actually live in the worktree you're about to
commit from. It's the generated-file specialization of broader worktree
discipline (KP-30).

## When to Use

- A file is generated or discovered in the main repo but the active
  session is an isolated `.claude/worktrees/...` path (or the reverse).
- Before validating or committing work that depends on a generated /
  moved asset (icons, inventories, build artifacts, exported docs).

## When NOT to Use

- A single-checkout session with no worktree split — there is only one
  path; nothing to reconcile.
- The asset is gitignored on purpose and not meant to be committed —
  confirm it's ignored, then skip.
- The generated file is fully reproducible in-place by re-running the
  generator from the active worktree — just regenerate locally instead of
  copying.

## Procedure

1. **Identify the active checkout.** Determine whether your CWD is the
   main repo or a `.claude/worktrees/<name>` path before acting — the
   absolute path tells you which tree a write will land in (KP-30: an
   absolute-path Edit hits the path you name, not necessarily your
   worktree).
2. **Locate the asset's real path.** After generating or moving a file,
   check both the main checkout and the active worktree for it — don't
   assume it landed where the active session lives.
3. **Sync into the active worktree.** If the asset is in the wrong tree,
   regenerate it in-place from the active worktree (preferred) or copy it
   in, so the commit you're about to make actually contains it.
4. **Confirm with git status from the active worktree.** Run `git status`
   from the worktree you'll commit from and confirm the asset shows as
   tracked/modified there before committing.
