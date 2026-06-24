---
name: doc-reconcile
description: >-
  Use at the end of a dev block, just before creating the PR, to
  reconcile the branch diff against the project's tracking docs —
  PROD_CHECKLIST.md, ARCH_*.md implementation checklists,
  DECISIONS_PENDING.md, KNOWN_PROBLEMS. Catches the delta where work
  that is actually done still sits in a "to-do" list and resurfaces on
  the next "what's next ?".
---

# doc-reconcile

The orchestrator's end-of-block gateway to « do the tracking docs still
match reality ? ».

A dev block ships code. The tracking docs (`PROD_CHECKLIST.md`, ARCH
checklists, `DECISIONS_PENDING.md`, `docs/known/KNOWN_PROBLEMS.md`) are supposed to
record what is done vs. what remains. They drift : an item gets built
but its checkbox stays `[ ]`, so it resurfaces — wrongly — the next
time someone asks « what's next ? ». This skill closes that delta
**before the PR is created**, so the PR diff carries the doc updates.

## Why this skill exists

The recurring failure : the orchestrator says « it seems like that was
done — verify », a subagent confirms it manually, and only then the
checkbox gets flipped. The verification is real but ad-hoc, so it gets
skipped under budget pressure and the delta accumulates. This skill
makes the verification **systematic and evidence-gated**.

## Core principle — the evidence rule

**A checkbox is flipped to done ONLY if a concrete artifact in the
branch diff proves it.** « It seems done » / « it was probably done »
/ « the title matches » is NOT evidence. Evidence = a precise citation
(`file:line` or a diff hunk) that a verification subagent produced.

No citation → the item stays untouched. This is the whole point : a
truly-done item gets checked (and stops resurfacing), and a not-done
item can never be falsely checked.

## When to use

Invoke at the end of EVERY dev block, **before** creating the PR and
before the `finishing-a-development-branch` skill. The doc edits land
inside the PR diff.

**When NOT to use :** not for branches that touch zero documented
behaviour (pure tooling/CI) — run it anyway, it will simply report
« 0 items applicable » in seconds. Not a substitute for the ARCH-first
rule (R24) — that happens at design time, this happens at close time.

## The procedure

The orchestrator runs this; the verification step is delegated to a
subagent (R30).

### 1. Determine branch scope

```bash
git diff main...HEAD --stat
git diff main...HEAD
```

Identify the services / areas / tables / config keys the branch
touched. This bounds which doc items are even candidates.

### 2. Collect candidate items

From the branch-touched areas only, gather:

| Doc | Candidate items |
|-----|-----------------|
| `PROD_CHECKLIST.md` | unchecked `- [ ]` lines in touched areas |
| `ARCH_*.md` touched (`docs/arch/` + per-service `webservices/**/ARCH_*.md` · `batch/**/ARCH_BATCH_*.md` · `ratis_client/ARCH_*.md` · `ratis_core/ARCH_CORE.md`) | unchecked implementation-checklist items |
| `DECISIONS_PENDING.md` (if present — local-only, may be absent — at repo root) | every pending decision |
| `docs/known/KNOWN_PROBLEMS.md` + `docs/known/KNOWN_PROBLEMS_INDEX.md` | open KP in touched areas |

Also collect, for the reverse check : `- [x]` items already marked
done in touched areas.

### 3. Dispatch the verification subagent

Brief = `SA_EXPLORE.md`. Hand it the full branch diff + the candidate
list. It must classify EACH candidate and return a report:

```
DONE      <item>  — evidence: <file:line or hunk>   (mandatory citation)
PARTIAL   <item>  — done: <…> / missing: <…>
NOT DONE  <item>  — <one line why>
REVERSE   <[x] item>  — diff contradicts it: <file:line>   (flag only)
```

Rules the subagent MUST follow (state them in the brief):
- A `DONE` line WITHOUT a `file:line`/hunk citation is invalid — downgrade to `NOT DONE`.
- Evidence must come from the **branch diff**, not from pre-existing code.
- Do not edit any file. Report only.

### 4. Orchestrator applies the edits

For `DONE` items with valid evidence, the **orchestrator** (not the
subagent) edits the docs in the main context:

| Doc | Edit |
|-----|------|
| `PROD_CHECKLIST.md` | flip `- [ ]` → `- [x]`, match the neighbouring style |
| `ARCH_*.md` | flip checklist `- [ ]` → `- [x]`; bump frontmatter `updated: YYYY-MM-DD`; update statut field if present |
| `DECISIONS_PENDING.md` → `docs/decisions/DECISIONS_ACTED.md` | move the entry, assign the next `DA-NN`, add `(YYYY-MM-DD)` + a context paragraph |
| `KNOWN_PROBLEMS*` | **do NOT auto-edit** — gitignored, R31. List the resolved KP in the output and ask the user before writing. |

`PARTIAL` / `NOT DONE` → leave untouched.

`REVERSE` flags → **do NOT uncheck anything.** List them in the output;
the user decides.

### 5. Output the reconciliation summary

```
doc-reconcile — <branch>
  ✓ checked  : <N> items   (each with evidence)
  ○ still open: <M> items   ← the real "what's next"
  ⚠ reverse  : <K> flags   (already-[x], diff contradicts — user decides)
  KP resolved (pending your OK): <list or "none">
```

The `M` open items are the truthful backlog. The `N` checked items can
no longer resurface on a future « what's next ? ».

## Rationalizations — STOP if you catch yourself here

| Excuse | Reality |
|--------|---------|
| "The checklist title matches the branch name, check it" | Title match ≠ evidence. Require a diff citation. |
| "It seems done, I'll just tick it" | "Seems" is the exact failure this skill exists to kill. |
| "The subagent said done, no citation but I trust it" | A `DONE` with no `file:line` is invalid. Downgrade it. |
| "Evidence is in old code, close enough" | Evidence must be in the branch diff. Old code = not this block's work. |
| "Skip the subagent, I'll eyeball the diff myself" | Eyeballing under budget pressure is how the delta started. Delegate (R30). |
| "It's mostly done, check it and note the rest" | Mostly done = `PARTIAL` = stays `[ ]`. Half-checked boxes lie. |
| "Pure-tooling branch, skip the skill" | Run it — it returns "0 applicable" in seconds. Skipping breeds the habit of skipping. |

## Red flags — re-run the evidence rule

- A box flipped to `[x]` with no `file:line` behind it
- "Probably" / "should be" / "I think" anywhere near a checkbox edit
- Editing `KNOWN_PROBLEMS*` without the user's OK
- Unchecking a `[x]` item yourself instead of flagging it `REVERSE`
- The subagent edited docs instead of only reporting

## Common mistakes

**Checking the whole PROD_CHECLIST section** — only items the *branch
diff* proves. A section about prod infra is not done because you
touched one line of it.

**Letting the subagent apply edits** — it reports; the orchestrator
writes. Keeps doc edits in the main context, under the evidence rule.

**Forgetting the `updated:` bump** — every ARCH you tick must get its
frontmatter `updated: YYYY-MM-DD` refreshed (doc-freshness rule).

**Running it after the PR exists** — run it before, so the doc edits
are part of the PR diff, not a follow-up commit.
