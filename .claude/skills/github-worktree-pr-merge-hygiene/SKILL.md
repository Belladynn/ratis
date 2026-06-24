---
name: github-worktree-pr-merge-hygiene
description: "Pre-flight and recovery checklist for merging PRs from a dirty, multi-worktree repo without corrupting local state — clean coordination branch, rebase in the owning worktree, force-with-lease."
---

# github-worktree-pr-merge-hygiene

Agent-driven PR work often runs across several git worktrees with a dirty
local `main`. In that state `gh pr merge`, `git pull`, and `git push`
fail in confusing ways : `main` has uncommitted changes, a branch is
attached to another worktree, or `origin/main` advanced mid-flight. This
skill is the pre-flight + recovery checklist that keeps the merge clean
and local state intact.

## When to Use

- `gh pr merge`, `git pull`, or `git push` fails because local `main` is
  dirty, a branch is attached to a worktree, or `origin/main` advanced
  during agent PR work.
- You are coordinating merges across multiple active worktrees.

## When NOT to Use

- A single clean clone with one branch and no worktrees — standard
  `gh pr merge` is enough; this overhead is unnecessary.
- The PR is red on CI — fix CI first (R15: never merge red); merge
  hygiene is orthogonal to a failing pipeline.
- You intend to commit feature work directly on `main` — don't; this
  skill exists partly to prevent that.

## Procedure

1. **Inspect state before touching anything.** Run `git status`, note the
   current branch, and run `git worktree list` to see which branches are
   attached where.
2. **Merge from a clean base.** Merge via `gh` from a clean coordination
   branch or an `origin/main`-backed temporary worktree — never commit
   directly on `main`, and never merge from a dirty tree.
3. **If mergeability fails, rebase in the owning worktree.** Rebase the
   PR branch *in the worktree that owns it*, `git push --force-with-lease`,
   re-run checks to green, then merge.
4. **Verify post-merge.** Confirm `origin/main` advanced as expected and
   no worktree is left on a deleted/detached branch.

## Post-merge cleanup — branch deletion blocked by an attached worktree

After a merge, `git branch -d <branch>` (or a cleanup script) often fails
because the branch is **still checked out in a worktree** — git refuses to
delete a branch that's attached. The fix is to detach/remove the worktree
first, not to force-delete the branch out from under it.

1. **Refresh local merge state first.** `git fetch --prune` before any
   cleanup — a "branch not fully merged" complaint is frequently just a
   stale local ref, not real unmerged work.
2. **Find the attached worktree.** `git worktree list` and identify which
   worktree holds the branch you're trying to delete.
3. **Remove or detach it safely.** `git worktree remove <path>` (or move
   that worktree off the branch). Then `git worktree prune` to clear stale
   worktree metadata for already-deleted directories.
4. **Retry the deletion and verify.** Delete the branch, then confirm
   with `git worktree list` and `git branch` that both the worktree and
   the local branch are gone and no worktree is left detached.
