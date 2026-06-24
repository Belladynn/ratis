---
name: long-form-doc-subagent-language-consistency-gate
description: "Create or rewrite a large exploratory doc via a subagent while enforcing language, structure, and word-budget gates — and verifying them before reporting done."
---

# long-form-doc-subagent-language-consistency-gate

Delegating a long markdown doc (create / extend / translate / rewrite) to
a subagent is efficient but drifts : the output flips language partway,
loses headings, or blows the word budget. This skill puts explicit
constraints in the brief and a verification gate after, so language and
structure drift is caught before the doc is reported complete.

## When to Use

- A long markdown document is generated, extended, translated, or
  rewritten by a subagent for the project.
- The doc has a required language, structure, or length you need to hold.

## When NOT to Use

- A short doc or a small inline edit you do yourself — no subagent, no
  drift surface; the gate is overhead.
- A doc with no language/structure contract to enforce (freeform notes).
- Code or config files — this is about prose-document consistency, not
  source.

## Procedure

1. **Constrain the brief.** State the target language, required structure
   (sections/headings), word budget, and explicit preservation rules
   (what must NOT change) before dispatching the subagent.
2. **Verify the output against the contract.** After completion, check
   line/word count, heading set, and sample content for language drift —
   don't trust "done."
3. **Dispatch a focused fix pass if it drifted.** If language or structure
   drift appears, send a targeted rewrite/fix pass before reporting the
   doc complete.
