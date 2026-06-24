---
name: codebase-recon
description: >-
  Use BEFORE writing an implementation plan, BEFORE dispatching a dev
  subagent, during brainstorm pre-flight, or whenever you need to know
  what already exists in the codebase — symbols (hooks, components,
  config keys, theme tokens, table columns, functions, endpoints),
  ARCH decisions, reusable modules. Returns a reconciliation report so
  you never reference an invented name or rebuild an existing brick.
---

# codebase-recon

The orchestrator's on-demand gateway to « what already exists in the
codebase ». Two functions, one entry point :

1. **Symbol reconciliation** — do the names you're about to use exist,
   and under what real name ?
2. **Reuse discovery** — what bricks already exist for what you want to
   build ?

## Why this skill exists

**Lesson 2026-05-14.** ~70 % of that day's pitfalls traced to one root
cause : the orchestrator wrote implementation plans referencing
*invented* symbol names — `Colors.text` (real : `Colors.textPrimary`),
`useAddItemToList` (real : `useAddItem`), a top-level
`cab_per_fill_product_field` config key (real : nested under
`rewards.*`). Inline verification costs context budget, so under budget
pressure it gets skipped and names get invented. Subagents caught the
errors, but at the cost of plan deviations and wasted CI cycles. One
error would have shipped a `KeyError` to production if executed
literally.

**Lesson 2026-05-03.** A brainstorm almost designed a subject « from
scratch » on the assumption « it doesn't exist » — when
`ARCH_receipt_pipeline.md` (44 KB, 4 phases, 2 marked DONE),
`worker/pipeline_v3/` and two acted decisions (DA-18, DA-25) already
covered 80 % of it. Invisible if you only read the `ARCH_INVENTORY.md`
index (which does not descend into `webservices/*/ARCH_*.md`).

The fix is this skill : delegate codebase verification to an explorer
subagent so the orchestrator's context stays clean (it receives a
distilled report, not raw files), and make verification a *structural
step* of the workflow rather than a discipline to remember.

## When to invoke

**Mandatory checkpoints — invoke on your own initiative, no user
prompt needed :**

- Before announcing a brainstorm (the pre-flight).
- Before finalizing a plan in `writing-plans` (reconcile every symbol
  the draft references).
- Before dispatching a dev subagent (reconcile the brief's symbols).

**On demand :** anytime you — or the user — have a question about what
already exists.

**Before replacing/migrating a subsystem to a framework :** run the
**Framework / replacement mode** (dedicated section below) — a *dual*
inventory (local + upstream) so you never rebuild a native primitive.

## Procedure

### Step 1 — Classify the question

| Category | Examples | Resolution |
|---|---|---|
| **Trivial lookup** | « does `useFoo` exist ? » · « what's the add-item hook called ? » · « is there a `GET /product/incomplete` endpoint ? » | Answered in 1-3 `Grep` calls. Do it directly (Step 2a). |
| **Real exploration** | « what bricks exist for completing a product ? » · brainstorm pre-flight · « reconcile these 12 symbols from my draft » · « what touches OCR matching ? » | Dispatch an Explore subagent (Step 2b). |

**Decision rule :** trivial = the answer fits in the result of 1-3
greps AND needs no cross-file synthesis. Exploration = requires reading
whole files, cross-referencing multiple sources, or synthesizing « does
this already exist ». **When in doubt → exploration (SA).** The cost of
one extra SA is bounded ; the cost of polluting context with whole
files read inline is not.

### Step 2a — Trivial path

For a code-symbol question : `Grep` directly (the right file or
directory), read the result, answer. For a doc-section question :
`docs_search(query, top_k=3)` via docs-mcp (Module 9 agent-mcp,
#560/#561), eventually one `docs_get(id)` to pull the body. Produce
only Part A of the report (below), inline, 1-3 lines. Done.

### Step 2b — Exploration path

Dispatch an Explore subagent. Brief template :

```
Tu es un subagent Explore sur ratis. Avant toute chose : lis SA_EXPLORE.md
(discipline docs-mcp→grep→index→seg→full).

Question : [the exact question / the list of symbols to reconcile]

Périmètre — sweep the relevant subset of these 8 sources :
  1. **docs-mcp first** : `docs_search(query, top_k=5)` (hybride vector
     bge-m3 + keyword via sqlite-vec) → couvre tous les ARCH, KP, DA
     indexés. Suivi de `docs_get(id)` sur les hits pertinents pour le
     body section.
  2. `docs_find(status=, tags=, file_glob=)` pour listes typées
     (ex : tous les ARCH `EN-COURS` taggés `cashback`).
  3. Si docs-mcp KO ou question hors-doc : `ARCH_INVENTORY.md` (pipe-séparé
     R41 — index de tous les ARCHs sous docs/arch/ + webservices/**/ARCH_*.md
     + batch/**/ARCH_*.md + ratis_client/ARCH_*.md + ratis_core/ARCH_CORE.md
     + entrées KP/DA), grep par tag/ID/statut.
  4. Sections ciblées des ARCHs matchants (`Read offset=X limit=Y`, R29).
  5. `docs/decisions/DECISIONS_ACTED.md` (grep keywords ou `docs_search`).
  6. `docs/known/KNOWN_PROBLEMS_INDEX.md` + `docs/known/KNOWN_PROBLEMS.md`
     (grep keywords ou `docs_search`).
  7. `ratis_core/config/ratis_settings.json` (grep — thresholds, configs,
     NESTED KEY SHAPES — ne pas supposer top-level).
  8. `ENDPOINTS.md` (grep — endpoint paths).
  9. Targeted code grep dans le(s) service(s) concerné(s) — coded vs planned.

Livrable : the reconciliation report defined below. < 400 words.
Every claim with file:line OR docs-mcp id. NEVER paste code paragraphs —
verdicts + pointers only.
```

Run the dispatch in background (R35). If a recon SA was already
dispatched this session, re-question it via `SendMessage` rather than
spawning a fresh one — it keeps its exploration context.

### Step 3 — Consume the report

Read the report, correct the plan / brief / reasoning before
continuing.

## Reconciliation report format

Three parts, scaled to the question.

### Part A — Symbol reconciliation

Verdict-per-symbol table, **sorted by actionability** : `❌` / `⚠️`
verdicts first (what needs fixing), `✅` grouped at the end.

```
symbol                   verdict      evidence
─────────────────────────────────────────────────────────────────────
Colors.text              ❌ RENAME    → Colors.textPrimary (constants/theme.ts:42)
Button variant=tertiary  ❌ ABSENT    real variants : primary|secondary|gold|danger
cab_per_fill_product...  ⚠️  NESTED   exists under rewards.* (ratis_settings.json:83)
validate_image_upload    ✅ EXISTS    ratis_core/uploads.py:18 sig=(image,*,allow_pdf,max_size_bytes)
```

Four verdicts : `✅ EXISTS` · `❌ RENAME` (exists under another name —
the real one is given) · `❌ ABSENT` (does not exist) · `⚠️ NESTED/PARTIAL`
(exists but not in the expected form — nested key, different signature).

### Part B — Reuse discovery

List of exploitable bricks (only when the question was « what exists
for X ») :

```
- ARCH_X.md § Y — covers ~60 %, status=planned, 2 phases DONE → extend
- services/foo_service.py already has do_thing() — extensible
- DA-18 already decided the strategy for this domain
- KP-42 documents a pitfall here
```

### Part C — Synthetic verdict (1 line, always present)

One of :
- 🟢 `FROM SCRATCH` — nothing exists, a clean design is legitimate
- 🟡 `EXTEND` — bricks exist, the work is extension/reuse not creation
- 🔴 `MOSTLY EXISTS` — already 70 %+ covered ; the brainstorm should be
  a *review*, not a *design*

## Framework / replacement mode

A third recon trigger, beyond symbol reconciliation and local reuse
discovery : **before replacing, migrating, or building a project
subsystem that a framework (or a platform you already run) might already
provide natively.** Examples : « should we build a ticketing queue ? »
when Hermes already ships a kanban ; « let's write a cron runner » when
the framework has `cron` built in ; « our own memory store » when a
`memory` provider exists.

**Lesson 2026-06-04.** A multi-day Hermes integration partially rebuilt
capabilities the framework already shipped — a crontab+script postmortem
runner (Hermes has native `cron`), follow-up tracking in Markdown
(Hermes has a `kanban`), and the start of a memory layer (Hermes has a
`memory` provider). The friction surfaced *four times* in one session
before the realization landed. Pure local recon (R27/R28) cannot catch
this : the thing you'd reinvent does not exist in **your** codebase — it
exists **upstream**, in the framework's `--help`, docs, and the
already-installed-but-unused surface. So this mode runs a **dual**
inventory, not one.

### Procedure — dual inventory

Run two recons in parallel, then reconcile them :

1. **Internal** — the normal `codebase-recon` (Step 2b) : what we've
   already built / decided for this subsystem.
2. **Upstream** — what the framework provides natively **and** what's
   already installed locally but unused. For a CLI framework : sweep
   `<tool> --help` and each relevant `<tool> <subcmd> --help`, the docs
   site (WebFetch the official page — don't infer from memory), the
   installed plugin/skill/extension list, and the config schema. Cite
   evidence exactly like the internal side (the `--help` line, doc URL,
   or installed-state check) — **never assert a capability from memory.**

### Output — Part D : keep / replace / defer matrix

Replaces Part C for this mode. One row per subsystem in scope :

```
subsystem   we built                  framework provides            verdict
──────────────────────────────────────────────────────────────────────────
cron        crontab + python script   hermes cron (native, deliver) 🟢 REPLACE
ticketing   DECISIONS_PENDING.md (git) hermes kanban (sqlite+dispatch) 🟡 KEEP-git + kanban for ephemerals
memory      MEMORY.md (static)        hermes memory (honcho, FTS5)  🔵 DEFER (V1 static suffices)
incidents   GlitchTip self-hosted     (framework has none)          🔴 SKIP (no overlap)
```

Four verdicts :
- 🟢 `REPLACE` — framework does it better ; migrate to the native primitive.
- 🟡 `KEEP` — our version fits our context better ; **state why** (e.g.
  git-versioned audit trail beats a local SQLite board). Optionally use
  the native one for a narrower slice.
- 🔵 `DEFER` — real overlap but not worth migrating now ; revisit on a
  named trigger (scale, new need).
- 🔴 `SKIP` — no real overlap ; the framework doesn't cover this.

**Discipline :** never `REPLACE` on assumption. The upstream column is
evidence-based or the row is invalid — the cost of rebuilding a native
primitive (the 2026-06-04 lesson) is exactly what an unverified « it
probably doesn't have it » produces.

## Relationship with R27 / R28 / R41

R27/R28 (`CLAUDE.md`) state the goal — consult the inventory, reuse,
never reinvent. R41 defines the doc indexing convention (pipe-séparé
`## ID — titre · refs · STATUT` + tags) that lets docs-mcp slice on
section granularity. `codebase-recon` is the orchestrator's *means* of
satisfying R27/R28. Subagents consult `ENDPOINTS.md` directly (R27)
and `docs_search`/`docs_get` via docs-mcp (R28+R41, fallback
`Grep ARCH_INVENTORY.md`) — they do not invoke this skill.
