---
type: cross-cutting
parent: ARCH_RATIS
related: [ARCH_agent_mcp, ARCH_admin_endpoints]
status: shipped
tags: [doc-system, methodology, r41, docs-mcp, lifecycle, inventory, superpowers, notion-export]
business_domain: infra
rgpd_concern: false
updated: 2026-05-31
---

# ARCH — Doc system (writing & consumption methodology)

> Reference guide for the Ratis doc process: where each doc type lives · strict R41 format · spec → ARCH cycle · agent consumption workflow (`docs_search`/`docs_get`) + writing (extend > new) · marking obsolete vs deleting · human layer `notion-export`. Consulted before any dilemma of "where does this belong / how to format it / who reads it".
> @tags: doc-system methodology r41 docs-mcp lifecycle inventory superpowers notion-export pipe-separated arch-inventory
> @status: LIVRÉ V0
> @subs: auto

> [[ARCH_RATIS]] · relations: [[ARCH_agent_mcp]] (Module 9 docs-mcp = default read channel), [[ARCH_admin_endpoints]] (admin UI also consumes ARCHs via docs-mcp on the tooling side).

> Scope: **agent-consumed** documentation (ARCH, KP, DA, decisions, settings) and **operator-consumed on-demand** documentation (Notion via skill `notion-export`). Out of scope: code comments, service READMEs (covered by their owning ARCHs), product Notion specs (managed by operator). The goal is that an agent wondering "where should I write this?" or "how should I read it?" finds the answer here in one targeted read.

## Index

- [DS-1 Doc tree — what lives where](#ds-1)
- [DS-2 R41 convention — strict format](#ds-2)
- [DS-3 Lifecycle spec→ARCH — distillation then purge](#ds-3)
- [DS-4 Agent consumption — 3-step workflow](#ds-4)
- [DS-5 Agent writing — extend > new](#ds-5)
- [DS-6 Obsolescence — DEPRECATED vs deletion](#ds-6)
- [DS-7 Human layer — notion-export on demand](#ds-7)

---

## DS-1 — Doc tree · `CLAUDE.md ## repo` · LIVRÉ V0

> TL;DR Agent doc = `docs/<cat>/` (arch · product · known · decisions · ops · seed). Stay-at-root exceptions are legitimate: agent contracts (CLAUDE/ORCHESTRATOR/SA_*), auto-gen indexes (ENDPOINTS/ARCH_INVENTORY), PROD_CHECKLIST, and the local-only journal (SESSION_LOG/DECISIONS_PENDING).
> @tags: arborescence repo root docs-tree categorization ops audits
> @subs: auto

- **Root** (stay-at-root):
  - `CLAUDE.md` · `ORCHESTRATOR.md` · `SA_DEV.md` · `SA_EXPLORE.md` — agent contracts (R26 v2 propose+confirm)
  - `ENDPOINTS.md` · `ARCH_INVENTORY.md` — auto-gen via `scripts/generate-{endpoints,arch}-inventory.py`, SessionStart hook + CI freshness check
  - `PROD_CHECKLIST.md` — pre-prod tasks (R25), root for immediate visibility at each session
  - `SESSION_LOG.md` · `DECISIONS_PENDING.md` — journal + pending decisions, **local-only** (gitignored)
- **`docs/arch/`** — `ARCH_*.md` cross-service (cab_economy, referral, deployment, agent_mcp, agent_mcp_isolation, doc_system, etc.) + templates (`ARCH_EXAMPLE.md` services, `ARCH_BATCH_TEMPLATE.md` batches) + `PROCEDURES.md` (stored-procedure support catalogue, **auto-gen** via `scripts/generate-procedures-catalogue.py`)
- **Per-service ARCHs** — live next to the code: `webservices/<svc>/ARCH_<SVC>.md` · `batch/ratis_batch_<n>/ARCH_BATCH_<N>.md` · `ratis_client/ARCH_*.md` · `ratis_core/ARCH_CORE.md`. The `generate-arch-inventory.py` script indexes them all.
- **`docs/product/`** — `PRODUCT.md` · `PRIVACY.md` · `TRAINING.md` (vision/legal/OCR data-flow — operator-consumed)
- **`docs/known/`** — `KNOWN_PROBLEMS.md` (KP-N catalog) + `KNOWN_PROBLEMS_INDEX.md` (keywords → KP-N), **gitignored** (local incident catalog)
- **`docs/decisions/`** — `DECISIONS_ACTED.md` (DA-N acted decisions, R41-indexable). PENDING stays root local-only
- **`docs/ops/`** — operator runbooks + checklists: `RUNBOOK_MIGRATION.md` (dev-host migration), `SETUP_CHECKLIST.md` (new machine bootstrap), `OPS_SCRIPTS.md` (**auto-gen** via `./update-scripts-help.sh` — root shell script catalogue). Operator-consumed on demand, no R41 index
- **`docs/seed/`** — documented fixtures/seed data

---

## DS-2 — R41 convention — strict format · `CLAUDE.md R41` · LIVRÉ V0

> TL;DR Each major section: `## <ID> — <title> · <refs> · <STATUS>` + quote-block `> TL;DR` (1-2 sentences) + `> @tags: space-separated words` + `> @subs: auto`. IDs `[A-Z]+-N` (DA, KP, HSP, M, DS…). Statuses: `LIVRÉ` | `EN-COURS` | `PLANIFIÉ` | `DEPRECATED` (+ free suffix like `LIVRÉ V1.1`).
> @tags: r41 format heading id status tldr tags subs convention pipe-separated
> @subs: auto

- **Heading**: `## <ID> — <short title> · <optional refs> · <STATUS>`. Refs = other IDs, PRs (`PR #560`), dates, etc. Non-breaking space around `·`.
- **TL;DR**: 1-2 dense sentences answering "if I read ONLY this, what do I know?". Avoid narration.
- **@tags**: space-separated keywords, **open vocabulary** (author chooses), no closed validation. They feed `docs_search` + `docs_find(tags=)`.
- **@subs: auto**: literal marker. `generate-arch-inventory.py` automatically computes `### Sub-sections(Lxx)` up to the next `##`.
- **Validation**: `bash scripts/check-arch-convention.sh` (warn-only, exit 0, pedagogical phase 2 sprints). Reads the same file-set as `generate-arch-inventory.py`. Emits `WARN: <file>:<line> — <id> non-compliant (<pattern>)` per drift.
- **Scanned scope**: `ARCH_*.md` everywhere (root + subdirectories) · `docs/known/KNOWN_PROBLEMS.md` · `docs/decisions/DECISIONS_ACTED.md`. **Excluded**: `docs/product/**` · `SESSION_LOG.md` · `PROD_CHECKLIST.md` · agent contracts (`CLAUDE.md`/`ORCHESTRATOR.md`/`SA_*.md`).
- **Minimal example** (see `docs/arch/ARCH_EXAMPLE.md` for full service template):
  ```
  ## DA-12 — Choix matcher consensus-only · PR #492 · LIVRÉ V1
  > TL;DR Le matcher fuzzy products a été retiré 2026-05-02 ; consensus crowdsourcé prend le relais.
  > @tags: consensus matcher products fuzzy retired decision
  > @subs: auto
  ```

---

## DS-3 — Lifecycle spec → ARCH · `superpowers:brainstorming` · `superpowers:writing-plans` · LIVRÉ V0

> TL;DR Spec/plan = dense draft produced by a brainstorm or writing-plans skill. **Transitory**: distilled into the target ARCH (10-30 dense lines), then removed from the tree post-merge. Git history preserves the draft.
> @tags: lifecycle spec plan brainstorm writing-plans distillation transitory git-history
> @subs: auto

- **1. Brainstorm** → skill `superpowers:brainstorming` (pre-flight `codebase-recon` MANDATORY) → produces an exploratory note (transitory spec)
- **2. Plan** → skill `superpowers:writing-plans` (pre-flight `codebase-recon` MANDATORY) → produces a transitory plan (steps + checkpoints, superpowers format)
- **3. Implementation** → SA dev via `executing-plans` / `subagent-driven-development`; throughout development, **acted decisions migrate** to the target ARCH (sections `## DA-N` + checked implementation checklist)
- **4. Post-merge** → orchestrator (R31 post-dev maintenance):
  - Verify that the spec/plan has been properly distilled into the ARCH (dense sections, not a copy-paste)
  - Remove the transitory draft from the tree → leaves git history intact
  - If someone wants to re-read the raw draft: recoverable via git history (the file remains accessible on prior commits)
- **Why**: spec/plan = verbose by construction (10-50 KB), ARCH = dense by construction (1-3 KB per section). Keeping both = stale duplicate that pollutes `docs_search` and confuses future readers. The spec did its job at dev time; after merge it lives in git history.
- **Exception**: if the spec contains long-form reasoning that is still useful (deep-dive audit, strategic RFC) → promote it as a `docs/decisions/DECISIONS_ACTED.md` section `DA-N` distilled, then `git rm` the spec.

---

## DS-4 — Agent consumption — 3-step workflow · `CLAUDE.md R28+R41` · LIVRÉ V0

> TL;DR Default: `docs_search(query, top_k=5)` → `docs_get(id)` for body → optional `docs_find(status=,tags=,file_glob=)` for typed lists. Fallback `Grep ARCH_INVENTORY.md` + `Read offset=<line>` if docs-mcp is down. Never full-read R29.
> @tags: consommation docs-mcp docs-search docs-get docs-find workflow agent-reading r29 fallback
> @subs: auto

- **Step 1** — `docs_search(query, top_k=5)` (Module 9 agent-mcp, delivered #560/#561): hybrid vector bge-m3 + keyword via sqlite-vec, scope = entire corpus indexed by `generate-arch-inventory.py` (ARCH + KP + DA). Returns a list of hits already bounded at section-level (thanks to R41's `## ID — … STATUS` headings).
- **Step 2** — For each relevant hit: `docs_get(id)` → returns the section body (up to the next `##`). This is generally sufficient.
- **Step 3 (optional)** — `docs_find(status=, tags=, file_glob=)` when you want a typed list rather than a semantic search: "all ARCH `EN-COURS` tagged `cashback`", "all KP-N tagged `migration`".
- **Auxiliary** — `docs_list_files()` (categorization of indexed files) · `docs_reindex(force=)` (maintenance after large doc refactor).
- **Fallback** (docs-mcp unavailable, e.g. offline or Module 9 debug):
  1. `Grep ARCH_INVENTORY.md` by tag/ID/status (pipe-separated, grep-friendly)
  2. Read the hit → coordinates `file:line`
  3. `Read <file> offset=<line> limit=<Y>` targeted at the section
- **SA Explore side**: SA_EXPLORE.md § discipline (Step 1 docs-mcp first, Step 2 grep/glob, Step 3 index, Step 4 segmented, Step 5 full-file last resort).
- **SA Dev side**: SA_DEV.md § start-of-task → ARCH lookup via `docs_search` then `docs_get(id)` (R29 never full-read).
- **Orchestrator side**: `codebase-recon` skill (pre-flight brainstorm + plan + dispatch) delegates to an Explore SA that follows the workflow above.

---

## DS-5 — Agent writing — extend > new · `CLAUDE.md R24+R28` · LIVRÉ V0

> TL;DR Before writing a new ARCH: `codebase-recon` checks that there is no adjacent ARCH to extend. If new: strict R41 format (YAML frontmatter + heading + TL;DR + @tags + @subs). After writing: `generate-arch-inventory.py` + `check-arch-convention.sh` must stay green.
> @tags: ecriture extend new-arch r41-strict template frontmatter regen inventory check-convention
> @subs: auto

- **Before writing**:
  - Skill `codebase-recon` → verdict `EXTEND`? then extend the existing ARCH (add a section `## ID-N — …`), do not create a new file
  - Verdict `FROM SCRATCH`? copy `docs/arch/ARCH_EXAMPLE.md` (services) or `docs/arch/ARCH_BATCH_TEMPLATE.md` (batches) as a base
- **YAML frontmatter** (mandatory, see templates): `type` · `parent` · `related` · `status` · `tags` · `business_domain` · `rgpd_concern` · `updated: YYYY-MM-DD` (R34 — update on every edit)
- **Main heading**: `# <slug> — <title>` then TL;DR quote-block + @tags + @subs (the **same** as the frontmatter, so `docs_search` finds both levels)
- **Sections**: each in R41 format (`## ID — title · refs · STATUS` + quote-block)
- **After writing**:
  1. `python scripts/generate-arch-inventory.py` → regenerates `ARCH_INVENTORY.md`
  2. `bash scripts/check-arch-convention.sh` → warning count must not increase
  3. `docs_reindex(force=true)` if docs-mcp is running locally (otherwise CI will do it at merge)
- **Commit**: `docs(<area>): <intent>` ≤3 lines (R16). Mention the touched ARCH in the message (e.g. `docs(cab): DA-25 — multiplier subscription 1.5× — updates ARCH_cab_economy`).
- **Anti-patterns**:
  - ❌ Creating `ARCH_X_phase2.md` alongside `ARCH_X.md` → extend `ARCH_X.md` instead
  - ❌ Heading without STATUS → check-convention warns
  - ❌ Forgetting `@subs: auto` → the index does not compute sub-sections
  - ❌ Hardcoding a summary in `ARCH_INVENTORY.md` → it is auto-gen, never edit by hand

---

## DS-6 — Obsolescence — DEPRECATED vs deletion · `CLAUDE.md R41` · LIVRÉ V0

> TL;DR Keep the index entry as `STATUS: DEPRECATED` + reason + link to successor. Delete the file = `git rm` + breadcrumb in the successor (`> Successor of [[ARCH_X]] — deleted <date>, see git show <SHA>:<path>`).
> @tags: deprecation removal breadcrumb successor git-history index-preservation
> @subs: auto

- **Mark as obsolete (preferred for partially valid ARCH)**:
  - Change the heading STATUS: `## ID — title · refs · DEPRECATED — replaced by ARCH_Y · 2026-05-15`
  - Add a quote-block at the top of the file: `> ⚠️ DEPRECATED — replaced by [[ARCH_Y]] (PR #NNN). Retained for short-term history (Q+1).`
  - The entry stays in `ARCH_INVENTORY.md` (status LEGACY or DEPRECATED), grep-able for understanding why something disappeared
- **Delete (preferred for 100% replaced ARCH)**:
  - `git rm docs/arch/ARCH_X.md` (git history preserves it)
  - In `ARCH_Y.md` (successor), add a breadcrumb: `> Successor of ARCH_X — deleted 2026-05-15 (PR #NNN). See `git show <SHA>:docs/arch/ARCH_X.md` for historical content.`
  - `generate-arch-inventory.py` regenerates without the deleted entry
- **Decision criterion**:
  - Did the doc provide long-form reasoning still useful today? → DEPRECATED (keep it grep-able)
  - Is the doc 100% replaced with no residual value? → deletion + breadcrumb (context lives in the successor)
- **NEVER**: silently modify an active ARCH by pasting DEPRECATED into it without a breadcrumb (the future reader loses the "why"). Every transition to DEPRECATED = SESSION_LOG.md entry + explicit commit message.

---

## DS-7 — Human layer — `notion-export` on demand · `.claude/skills/notion-export/` · `PR #562` · LIVRÉ V0

> TL;DR Skill `notion-export` (#562) generates a decision-maker-readable Notion version from a subset of the documentation (selected via docs-mcp). Idempotent via External ID `ratis-export:<id>`. Dry-run mode available. **Not for daily use — invoked on demand** (board review, operator sharing, non-tech alignment).
> @tags: notion-export skill human-layer on-demand decision-maker dry-run idempotent external-id
> @subs: auto

- **Invocation**: skill `notion-export` (slash-command or Skill tool) → asks which ARCHs / KPs / DAs to export
- **Internal pipeline**:
  1. Scans via docs-mcp (`docs_search`/`docs_get`) the requested sections
  2. Reformats into Notion blocks (H1/H2/H3, paragraphs, code blocks)
  3. Pushes via `mcp__ratis__notion_*` or Notion API (Keychain-backed, R42)
  4. External ID `ratis-export:<id>` guarantees idempotence (re-export = update, not duplication)
- **Dry-run mode**: `--dry-run` → displays the Notion payload without pushing it (debug / preview)
- **Use cases**:
  - Operator onboarding on a domain ("read the CAB economy doc in Notion format")
  - Board review (sharing 3-4 LIVRÉ V1 ARCHs)
  - Aligning a non-tech partner (Runa, Affilae) on a specific sub-arch
- **Out of scope**: not for daily agent use — the source-of-truth doc stays in `docs/` on the repo side. Notion = presentation layer on demand. If the Notion doc diverges from the repo doc, **the repo wins** (re-export to resync).
- **Security**: Notion secrets are minted via `ratis-secret use notion-token --cmd "..."` (R42), never hardcoded in the skill.

---

## Out of scope

- **Code comments**: Python docstrings + TS JSDoc, handled case-by-case in code, not indexed by docs-mcp
- **Service READMEs**: if present, they are a lightweight duplicate of the service ARCH — deprecate in favour of the ARCH (R24)
- **Product Notion specs**: live on the operator side, can be imported to `docs/product/` manually if they become a reference

---

## Glossary

- **R41**: pipe-separated convention defined in `CLAUDE.md` — heading `## ID — title · refs · STATUS` + TL;DR/@tags/@subs quote-block
- **docs-mcp**: Module 9 agent-mcp (#560/#561) — tools `docs_search`/`docs_get`/`docs_find`/`docs_list_files`/`docs_reindex`
- **ARCH_INVENTORY.md**: auto-gen pipe-separated index (all R41-compliant sections), regenerated by `scripts/generate-arch-inventory.py` at session-start hook + CI freshness
- **Distillation**: passage from a verbose spec 10-50 KB to a dense ARCH 1-3 KB/section, post-implementation
- **Breadcrumb**: note in a file pointing to another (deleted/deprecated) to preserve traceability even after `git rm`
