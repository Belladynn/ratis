---
name: subagent-pr-verification-gate
description: "Independently verify a subagent's PR claims (complete / green / merged / blocked) with your own gh and git checks before reporting status to the user."
---

# subagent-pr-verification-gate

A background coding subagent reports "PR is done, CI green, merged" — or
"blocked by a pre-existing failure." Those claims must be confirmed with
independent checks before they reach the user, because a confident report
is not evidence. This skill is the verification gate that sits between a
subagent's status and a user-facing claim. The principle is forge-agnostic
even though the commands here are `gh`/`git`.

## When to Use

- A background coding subagent claims a PR is complete, green, merged, or
  blocked by a pre-existing CI failure.
- Before relaying any subagent-reported completion status to the user.

## When NOT to Use

- You performed and observed the work yourself in this session — you
  already have first-hand evidence; re-verifying is redundant.
- Work that produced no PR/branch artifact to check against (pure
  discussion, planning).
- Mid-flight progress updates that make no completion claim — gate on
  claims of done/green/merged/blocked, not on routine status.

## Procedure

1. **Inspect the PR independently.** Fetch PR metadata, the file list,
   the diff, and the check runs yourself (`gh pr view`, `gh pr checks`,
   `gh pr diff`) — do not take the subagent's word for green or merged.
2. **Verify claimed blockers.** If the subagent says "blocked by a
   pre-existing failure," confirm it against `origin/main` or against
   diff paths the PR doesn't touch — a real pre-existing failure is
   reproducible off the branch.
3. **Reconcile claim vs evidence.** Any completion claim without a
   matching artifact (green check run, merge commit, diff hunk) is
   downgraded to not-verified.
4. **Only then report** status and next actions to the user, citing the
   evidence you checked.

## Sharpened checks — freshness, true scope, CI ownership

The four checks above catch the obvious "it's not actually merged/green"
gap. These four catch the subtler ones, where the SA's report is
*technically* true but misleading. Apply them with merge-base-aware git
commands, not just `gh`.

1. **PR freshness.** Confirm the branch is rebased on the *current*
   `origin/main`, not the main from when the SA started. `git fetch` then
   compare the merge-base against `origin/main` — a "green" check run on a
   stale base can flip red after rebase, and a stale branch may miss a
   fix that already landed.
2. **True diff scope.** Diff against the merge-base (`git diff
   $(git merge-base origin/main HEAD)..HEAD`), not `HEAD~n`. A squash, a
   bad rebase, or an accidental revert can make the *real* change set
   differ from what the SA describes — verify the diff matches the
   claimed work.
3. **CI ownership of the failure.** Before accepting "blocked by unrelated
   CI," check whether the failing job actually exercises paths the PR
   touches. A failure in a job whose inputs the diff doesn't change points
   to a base-branch problem, not this PR.
4. **Base-branch failures.** Reproduce a claimed pre-existing failure on
   `origin/main` itself. If main is red independently, the blocker is real
   and shared (escalate it as a main-is-broken issue); if main is green,
   the failure belongs to the PR despite the SA's claim.
