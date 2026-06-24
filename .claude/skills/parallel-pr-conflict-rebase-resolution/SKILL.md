---
name: parallel-pr-conflict-rebase-resolution
description: "Resolve conflicts after parallel subagent PRs touch shared modules — rebase the later PR onto origin/main, preserve both feature sets deliberately at the conflict markers, re-verify, and merge."
---

# parallel-pr-conflict-rebase-resolution

When two independently developed PRs both modify shared files and the
first one merges, the second goes `DIRTY` / `CONFLICTING`. The wrong
reflex is to take one side wholesale ; the right move is to rebase the
later PR onto the new `origin/main` and resolve each conflict so **both**
logically distinct additions survive. This is the conflict-resolution
counterpart to `github-worktree-pr-merge-hygiene` (which keeps the merge
mechanics clean) and the PR-train flow (which sequences merges to avoid
this in the first place).

## When to Use

- Two PRs both modified shared files and, after the first merges, the
  second shows `DIRTY` / `CONFLICTING` (or red mergeability).
- Parallel subagent work landed overlapping edits and you must combine
  both feature sets, not pick a winner.

## When NOT to Use

- The PRs touch fully disjoint files — there is no conflict; just merge in
  order.
- One PR genuinely supersedes the other — that's a close/redo decision,
  not a merge-both resolution.
- You can still serialize the work before either merges — prefer the
  PR-train approach (rebase-before-merge sequentially) so the conflict
  never forms.

## Procedure

1. **Check mergeability explicitly.** `gh pr view <n> --json
   mergeable,mergeStateStatus` to confirm the conflict and which PR is
   behind.
2. **Rebase the later PR in its owning worktree.** `git fetch` then
   rebase the later branch onto `origin/main` — in the worktree that owns
   it (per worktree hygiene), never on a dirty `main`.
3. **Resolve to preserve both.** At each conflict marker, keep the
   logically distinct additions from *both* sides — don't blanket-accept
   ours/theirs. The goal is the union of the two feature intents, not the
   survival of one.
4. **Re-verify fully.** Run the full local tests + lint on the rebased
   branch; a rebase that drops a hunk shows up here.
5. **Force-push with lease and re-check CI.** `git push
   --force-with-lease`, wait for green checks, then merge. Never merge red
   (R15).
