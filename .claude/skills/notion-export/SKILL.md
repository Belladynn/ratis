---
name: notion-export
description: >-
  Use when the operator or a stakeholder asks for a "non-tech / décideur"
  view of the Ratis project state in Notion, or invokes /notion-export.
  Generates / refreshes the canonical Notion mirror of the repo
  documentation (ARCH, HSP, DA, M, audits) — one page per entity,
  rewritten for a decision-maker who has never touched code. Idempotent :
  re-running updates the existing pages instead of duplicating them.
---

# notion-export

The orchestrator's on-demand bridge from the **canonical doc inside the
repo** (`docs/arch/`, `docs/decisions/`, `docs/audits/`, ARCHs in
services / batch / client) to a **decision-maker-friendly Notion mirror**.

Single entry-point : `Skill notion-export` (or `/notion-export`). The
skill walks through the procedure below — it is not a script and not an
MCP server. It guides Claude through the docs-mcp scan, the
tech→décideur reformulation, and the Notion writes.

## Why this skill exists

The canonical doc in the repo is the source of truth for Claude (R28,
R29, the `docs-mcp` tools) — but it's written for engineers and
agents. The terminology is technical (« rôle PG `agent_read` NOINHERIT »,
« HMAC argon2id »), the structure is `HSP-N` / `DA-NN` / `M-NN`, and the
inventory `ARCH_INVENTORY.md` is a pipe-separated index. Useful for
agents, opaque for stakeholders, investors, future ops humans.

A « décideur » needs to read **what changed for the user / for the
business / for the money**, not « bind-mounts en `:ro` ». This skill
generates that mirror, on-demand, without forking the source of truth.

## When to invoke

**On demand only.** Triggers :

- Operator asks « génère la doc décideur Notion » / « refresh Notion ».
- A stakeholder ping needs the high-level project state in Notion.
- After a significant batch of `DECISIONS_ACTED` / `LIVRÉ V1.x` lands
  and the operator wants the human-readable mirror in sync.

**Do NOT invoke automatically.** There's no cron, no SessionStart hook —
the Notion mirror is a deliverable, refreshed when the operator asks.

## Core principle — canonical-first, write-only

1. **Canonical = repo.** The skill READS via `docs-mcp` (`docs_search` /
   `docs_get` / `docs_find` / `docs_list_files`) — never writes back to
   the repo, never invents content that isn't in the canonical source.
2. **Notion = mirror.** The skill writes / updates Notion pages. If a
   Notion page contradicts the canonical doc, the canonical wins ; the
   skill overwrites the Notion body on the next run.
3. **Idempotent via External ID.** Every Notion page carries a
   `External ID` property (or a sentinel string in the title block — see
   § Notion store) of the form `ratis-export:<entity-id>`
   (e.g. `ratis-export:HSP-3`). Re-running the skill matches by that ID
   and updates, never duplicates.
4. **Reformulation, not translation.** Tech sentences are
   *rewritten* for a non-tech reader, not *translated* word-for-word.
   See § Reformulation rules below.

## Procedure

The skill is run by Claude (orchestrator or a one-off subagent — both
work, the procedure is the same). Operator gets a recap at the end.

### Step 0 — Pre-flight

Verify the tools needed are available :

```
docs_search / docs_get / docs_find / docs_list_files   (agent-mcp, ops scope, read-only)
notion-search / notion-fetch                            (notion-mcp, read)
notion-create-pages / notion-update-page                (notion-mcp, write)
notion-create-database                                  (notion-mcp, only for first run)
```

If `docs_*` tools are missing → docs-mcp not loaded ; abort and ask the
operator to enable agent-mcp.

If `notion-*` tools are missing → notion-mcp connector not connected ;
abort and surface the install instruction.

### Step 1 — Find or create the Notion root

Strategy — the skill maintains a single root page « État du projet
Ratis » under which everything lives :

1. `notion-search` for « État du projet Ratis » (exact title).
2. If exactly one page matches → use its `page_id` as the root.
3. If zero or multiple → ask the operator (single AskUserQuestion) :
   « Page racine introuvable / ambiguë. (a) Créer une nouvelle page
   racine sous l'espace `Ratis`, (b) pointer une page existante par
   URL, (c) annuler. » Wait for the answer before continuing.
4. If creating : `notion-create-pages` with title « État du projet
   Ratis » + the body from `templates/overview-page.md`.

Persist the root `page_id` in the run summary so subsequent steps reuse
it without re-searching.

### Step 2 — Find or create the 5 category pages

Under the root, the skill maintains one sub-page per category :

| Category    | Title                          | Scope                                            |
|-------------|--------------------------------|--------------------------------------------------|
| Architecture | `Architecture & Modules`      | `docs_list_files()` entries with category `arch` / `service-arch` / `batch-arch` / `client-arch` |
| Décisions   | `Décisions actées (DA)`        | `docs_find(file_glob="docs/decisions/*")`        |
| Sous-projets | `Sous-projets en cours (HSP / M)` | `docs_find(status="EN-COURS")` + `docs_find(status="LIVRÉ V1.1")` filtered to HSP / M entries |
| Audits      | `Audits & contexte`             | category `audit`                                 |
| Connus      | `Problèmes connus (mémoire)`    | category `known` — V1 scope : ONLY the index summary, not per-KP pages |

For each category, look up by title under the root. Create if absent
using `templates/category-page.md`.

### Step 3 — Enumerate the entities to export

Run, in this order, and concatenate :

```
docs_find(status="EN-COURS")
docs_find(status="LIVRÉ V1.1")
docs_find(status="LIVRÉ V1.0")
docs_find(status="LIVRÉ V0")
docs_find(status="PLANIFIÉ")
```

Filter out `LEGACY` entries (V1 scope — see Scope decision below).

For each entry, classify into a category :

| Entry ID prefix / shape | Category page    |
|-------------------------|------------------|
| `HSP-N`, `M-N`          | Sous-projets     |
| `DA-NN`                 | Décisions        |
| `ARCH_*` (whole-file)   | Architecture     |
| Audit-shaped entries (category `audit`) | Audits |

### Step 4 — For each entity : reformulate + write

Per entity, the cycle is :

1. `docs_get(id)` — pull the canonical body (`Section.body`).
2. Build the décideur version using `templates/entity-page.md` and the
   prompts in `helpers/prompt-fragments.md`. See § Reformulation rules.
3. Look up the existing Notion page by External ID
   `ratis-export:<id>` (see § Notion store).
4. If found → `notion-update-page` with the new body. Bump the page's
   « Dernière mise à jour » property (today's date).
5. If not found → `notion-create-pages` as a child of the right
   category page, with the External ID set.

Process entities one-by-one — the Notion API accepts batch writes but
keeping one call per entity makes the per-entity recap easier and avoids
partial-batch failures masking individual errors.

### Step 5 — Refresh the root summary

`notion-update-page` on the root with a fresh table-of-contents that
lists the 5 category pages + the count of entities under each + the
ISO-date of this run. Body template : `templates/overview-page.md`.

### Step 6 — Recap

Output (to the operator, in the assistant reply — not written
anywhere) :

```
notion-export — <YYYY-MM-DD>
  root      : <Notion URL>
  created   : <N> pages
  updated   : <M> pages
  unchanged : <K> pages (body already matched)
  errors    : <E> (list with entity_id + reason)
  scope     : <N+M+K+E> entities total
```

If `errors > 0`, list each failed entity with its `<id>` and the error
message (1 line each). The operator decides whether to retry.

## Notion store — how External ID works

The notion-mcp page model doesn't have a generic « External ID »
property unless the parent DB defines one. Two valid layouts depending
on whether the operator wants a flat DB or a tree of pages :

### Layout A — Tree of nested pages (V1 default)

The 5 category pages contain child sub-pages. Each entity page has, as
the **first block of its body**, a hidden sentinel paragraph :

```
<!-- ratis-export:HSP-3 -->
```

The skill `notion-fetch`-es a category page, lists its children, and
for each child reads the first block to extract the sentinel. Match by
that string. This works without a custom DB schema — bootstrap-friendly.

### Layout B — Backing DB with `External ID` property (future)

If the operator later asks the skill to create a backing DB (via
`notion-create-database`) with an `External ID` rich-text property, the
skill switches to querying the DB. Cleaner long-term ; not V1.

The procedure above describes Layout A. Layout B is a backwards-compatible
extension : the skill detects which layout is in use by checking whether
the root page has a child DB titled `Ratis Decideur Export`.

## Reformulation rules — tech to décideur

These are the rules Claude follows when rewriting `docs_get(id).body`
into the body of a Notion page. Full prompt fragments :
`helpers/prompt-fragments.md`.

**Style :**

- French, accessible, sentences ≤ 25 words.
- No code blocks, no SQL, no Python, no Postgres roles, no env-var
  names, no `service.py:42`. If a sentence requires one of those to be
  understood, rewrite the sentence to use the underlying business
  intent.
- Tech terms → business equivalents :
  - « JWT » → « jeton de connexion »
  - « rôle PG `agent_read` NOINHERIT » → « accès lecture seule pour les agents automatisés »
  - « HMAC argon2id » → « empreinte cryptographique inviolable »
  - « webhook » → « notification automatique »
  - « migration Alembic » → « mise à jour de la base de données »

**Structure (every entity page has these 5 sections, in order) :**

1. **Quoi** — what was decided / built / planned. 1-3 phrases.
2. **Qui est concerné** — utilisateur final ? équipe support ? business ?
   Légal ? Investisseur ?
3. **Quand** — `status` + ISO date if visible.
4. **Pourquoi** — la motivation business / produit / sécurité /
   conformité. 2-4 phrases.
5. **Comment ça change pour eux** — concrètement, qu'est-ce qui bouge
   pour la personne concernée. 1-3 phrases.

**Forbidden phrasings :** « le service écoute sur le port », « la
fonction X retourne », « il faut configurer Y », « le repo
PostgreSQL », « le job Celery », « la branche feature/xxx ».

**Allowed business words :** « solde cashback », « scan », « paiement »,
« carte cadeau », « notification », « connexion utilisateur »,
« sécurité », « conformité RGPD », « performance », « disponibilité ».

## Dry-run mode

To validate the skill locally without writing to Notion, run with the
operator instruction « dry-run » :

```
Skill notion-export args="dry-run"
```

In dry-run mode :

- Steps 0-3 run unchanged (READ from docs-mcp + Notion).
- Step 4 builds the reformulated body for each entity but does NOT
  call `notion-create-pages` / `notion-update-page`.
- Step 5 is skipped.
- The recap reports « would create / would update » counts and prints
  the first 200 chars of 3 representative reformulated bodies for spot
  check.

Use it after editing the templates or the prompt fragments to verify
the change before pushing to Notion.

## Example I/O — HSP-3

The canonical entry (from `docs_get("HSP-3")`) starts with :

> ## HSP-3 — gate humain durci (db-write-pipeline V1.1) · #539 · LIVRÉ V1.1
> > 5 mécanismes structurels contre rubber-stamping et contournement
> > agent. M1 challenge à taper systématique (insensible casse+espaces,
> > 3 essais → lockout 60s, anti-paste `user-select:none`). M2 secret
> > HMAC distinct `HUMAN_APPROVAL_SECRET` (argon2id, jamais en clair en
> > DB/env/log). M3 résumé français déterministe. M4 5 anomaly flags
> > structurels figés. M5 graduation `trust_level ∈ {manual, caps_only,
> > frozen}`.

The Notion décideur page generated by the skill :

> **HSP-3 — Validation humaine renforcée pour les mouvements en base
> de données**
>
> **Quoi.** Un dispositif à 5 verrous structurels qui empêche les
> agents automatisés ou un opérateur distrait d'écrire en base de
> données sans validation explicite et tracée.
>
> **Qui est concerné.** L'opérateur Ratis qui valide les écritures
> sensibles (correction de solde cashback, lien d'un scan à un
> utilisateur). Indirectement, tous les utilisateurs finaux : leurs
> soldes sont à l'abri d'une modification non-désirée.
>
> **Quand.** Livré en V1.1 (production), PR #539.
>
> **Pourquoi.** Avant ce verrou, un opérateur pouvait valider une
> proposition d'écriture sans en lire le contenu. C'est exactement le
> type de geste qu'un agent malveillant essaierait d'exploiter. Ce
> dispositif rend impossible une validation accidentelle ou abusive
> et trace chaque décision avec une empreinte cryptographique.
>
> **Comment ça change pour eux.** L'opérateur doit retaper un mini-mot
> de passe spécifique à chaque écriture sensible (3 essais sinon
> blocage 60 secondes). Un résumé en français clair de la modification
> est affiché avant validation. Des alertes automatiques signalent les
> cas suspects (montant inhabituel, utilisateur récidiviste, hors
> heures de bureau).

## Common mistakes

- **Reading `ARCH_INVENTORY.md` directly** — don't. Use
  `docs_list_files()` and `docs_find()` (R29). Cheaper, structured,
  same source.
- **Calling `notion_create_ticket` from agent-mcp** — that tool is
  whitelisted to the INCIDENTS DB (DA-44). Use the notion-mcp
  `notion-create-pages` instead, which can write anywhere the
  integration has access to.
- **Translating instead of reformulating** — copying the canonical
  paragraph as-is into Notion defeats the skill's purpose. If the body
  contains a code identifier, REWRITE the sentence around the business
  intent.
- **Duplicate pages on re-run** — happens if the External ID sentinel
  is missing or the lookup ignores Layout A. Always set the sentinel
  as the FIRST block of the body on creation.
- **Reading large canonical files in full** (R29) — only the entries
  returned by `docs_find` are fetched, one `docs_get(id)` at a time.
  Never `Read` an `ARCH_*.md` in full from this skill.
- **Skipping the recap** — always end with the recap block. It's the
  operator's only audit trail of the run.

## Red flags — STOP and re-check

- A Notion page is being created without an External ID sentinel.
- The reformulated body contains `psycopg`, `JWT`, `Postgres`,
  `migration`, or a file path.
- The skill is about to call `notion-create-pages` more than once for
  the same entity in the same run.
- The recap says « 0 updated » after a 2nd run with no canonical
  changes in between — the lookup logic is broken (Layout A sentinel
  not being found).
- `docs_get(id)` is being called in a tight loop with the same id —
  cache the result for the duration of the run.

## Relationship with other skills

- `codebase-recon` answers « what exists in code ». `notion-export`
  answers « what exists in **decided + documented** form, and how do
  I tell a non-tech human about it ».
- `doc-reconcile` keeps the canonical doc honest at end-of-block.
  `notion-export` mirrors the (already-honest) canonical to Notion.
- Neither replaces `ENDPOINTS.md` / `ARCH_INVENTORY.md` for agent
  consumption — those stay the source of truth for engineering work.
