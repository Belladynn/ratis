---
name: subagent-managed-ratis-fix-flow
description: "Orchestrate a recon subagent + scoped dev subagent for a Ratis backend fix — reconcile symbols, dispatch one scoped dev SA, verify the work, doc-reconcile, open PR, monitor CI, merge. Includes the sequential PR-train mode for multi-PR plans."
---

# subagent-managed-ratis-fix-flow

The orchestrator's end-to-end recipe for a Ratis backend fix delegated to
subagents, keeping the main context clean (it receives distilled reports,
not raw files). It wraps `superpowers:subagent-driven-development` with
Ratis-specific conventions : `codebase-recon` for symbol reconciliation,
`doc-reconcile` before the PR, `subagent-pr-verification-gate` on the SA's
claims, and the R-rules (R15 never-merge-red, R27/R28 reuse-first, R33
clean-solution). For a multi-PR plan it extends into a **PR-train** :
strictly sequential dispatch where each PR is merged before the next is
cut from the fresh `origin/main`.

## When to Use

- A bug needs cross-file backend changes and the work is being delegated
  to subagents managed by the main orchestrator.
- An approved plan has multiple sequential PRs that each need to be
  delegated, CI-verified, merged, then used as the base for the next
  (PR-train mode).

## When NOT to Use

- A trivial single-file edit the orchestrator can do inline — dispatching
  a SA is pure overhead.
- Frontend / mobile work — the file map and conventions here are
  backend-specific (`routes → services → repositories`, Alembic, etc.).
- Independent PRs with disjoint files that can run in parallel worktrees —
  use parallel dispatch, not the sequential train (and see
  `parallel-pr-conflict-rebase-resolution` if they later collide).

## Procedure — single fix

1. **Recon first.** Run `codebase-recon` to reconcile every symbol the
   fix will reference (real hook/endpoint/config-key/column names) and
   surface existing reusable bricks — never let the brief invent a name
   (R27/R28).
2. **Dispatch one scoped dev SA.** Brief it to read `SA_DEV.md` first;
   give exact file/behavior constraints, the reconciled symbols, and TDD
   discipline (R01). One SA per disjoint unit of work; each gets its own
   worktree+branch (KP-35).
3. **Gate the SA's claims.** Apply `subagent-pr-verification-gate` —
   independently confirm the commit, the diff scope, and green CI; never
   take "done / green" on the SA's word.
4. **Reconcile docs.** Run `doc-reconcile` against the branch diff before
   the PR (PROD_CHECKLIST, ARCH checklists, DECISIONS_PENDING,
   KNOWN_PROBLEMS).
5. **PR + CI + merge.** Open the PR, monitor checks, merge only on green
   (R15) — and only after you understand any failure, not by retrying
   blindly.

## Procedure — PR-train (sequential multi-PR)

1. **Confirm the plan and the next PR.** Identify the next unmerged PR
   number in the agreed sequence — work strictly in order.
2. **Dispatch one isolated dev SA per PR, cut from `origin/main`.** Each
   PR's worktree branches from the current `origin/main`, not from the
   previous unmerged branch. Require the SA to return the PR URL + test
   evidence.
3. **Verify, watch CI, merge.** Apply the verification gate, watch checks
   to green, merge.
4. **Refresh, then advance.** `git fetch` so the new `origin/main`
   includes the just-merged PR, and only *then* dispatch the next PR in
   the train. This keeps each PR rebased on its real base and avoids the
   stale-parent conflicts that `parallel-pr-conflict-rebase-resolution`
   otherwise has to clean up.
