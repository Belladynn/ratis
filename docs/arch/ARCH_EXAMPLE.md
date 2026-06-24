---
# Identity
type: service-global                    # service-global | batch-global | shared-lib | client-global | cross-cutting | project-root
service: ratis_example                   # full canonical name (omitted if cross-cutting or project-root)
status: in-progress                      # production | in-progress | planned | deprecated

# Navigation (Obsidian + RAG)
parent: ARCH_RATIS
sub_archs: [ARCH_feature_x, ARCH_feature_y]
related: [ARCH_CORE, ARCH_OTHER_SERVICE]

# Technical
port: 8000                               # omit if not applicable
tech: [FastAPI, PostgreSQL, Redis]
tables: [example_table]                  # owned tables
env_vars: [EXAMPLE_API_KEY]

# Business
tags: [example, template]
business_domain: infra                   # auth | cashback | gamification | pricing | social | infra | rgpd
rgpd_concern: false

# Freshness (MANDATORY — R34 — update on every edit)
updated: 2026-04-24
---

# ratis_example — global service ARCH template

> Global service ARCH template to copy into `webservices/ratis_<svc>/ARCH_<SVC>.md` when creating a new FastAPI service. Includes normalized YAML frontmatter + standardized sections (Index, Responsibility, Endpoints, Tables, Decisions, Flow, Sub-ARCHs, Glossary).
> @tags: template arch boilerplate skeleton convention scaffold meta service-global example
> @status: LIVRÉ V0
> @subs: auto

> [[ARCH_RATIS]] · sub-ARCHs: [[ARCH_feature_x]], [[ARCH_feature_y]] · relations: [[ARCH_CORE]]

> ⚠️ **THIS FILE IS A TEMPLATE.** Copy it and rename to `ARCH_<SERVICE_MAJ>.md` (e.g. `ARCH_AUTH.md`, `ARCH_REWARDS.md`) when creating a new global service/batch/lib ARCH. Sections are written to be self-contained (each section makes sense in isolation for RAG — R34).

## Index

> ⚠️ The Index is **mandatory** in every ARCH. Update line numbers on each major edit (R34). It allows segmented reading (R29) to read only `Read offset=X limit=Y` instead of the entire file.

- [One-sentence summary](#one-sentence-summary) · L.NN
- [Responsibility](#responsibility) · L.NN
- [Exposed endpoints](#exposed-endpoints) · L.NN
- [Owned tables](#owned-tables) · L.NN
- [Internal dependencies (other ratis services)](#internal-dependencies-other-ratis-services) · L.NN
- [External dependencies (third parties)](#external-dependencies-third-parties) · L.NN
- [Key architecture decisions](#key-architecture-decisions) · L.NN
- [Main flow](#main-flow) · L.NN
- [GDPR constraints specific to the service](#gdpr-constraints-specific-to-the-service) · L.NN
- [Things to know (vectorized FAQ)](#things-to-know-vectorized-faq) · L.NN
- [Sub-ARCHs](#sub-archs) · L.NN
- [Glossary](#glossary) · L.NN

---

## One-sentence summary

ratis_example is a FastAPI service that [does X, for Y, using Z]. One self-contained sentence, mentions the full service name.

## Responsibility

Factual bullets, each bullet mentions the service to be self-contained:
- ratis_example exposes [X endpoints] for [domain]
- ratis_example manages state [Y] via table [Z]
- ratis_example integrates with [third-party API] for [purpose]

## Exposed endpoints

Full auto-generated inventory in `ENDPOINTS.md` (section `ratis_example`). Functional summary:
- `POST /api/v1/example` — [purpose in 1 sentence]
- `GET /api/v1/example/{id}` — [purpose in 1 sentence]

## Owned tables

- **`example_table`** — stores [what] for [use]. Keys: `id` (UUID PK), `user_id` (FK users), `status` (enum). Retention: [rule].

## Internal dependencies (other ratis services)

- [[ARCH_CORE]] — uses `make_engine`, `deps.verify_admin_key`, shared settings loader
- [[ARCH_NOTIFIER]] — calls `POST /api/v1/notify` via `INTERNAL_API_KEY` for [case]

## External dependencies (third parties)

- [Third party X] — [usage] (doc: [link])
- [Third party Y] — [usage]

## Key architecture decisions

### DA-01 — [Decision name]

**Choice**: [selected option]
**Rejected alternative**: [discarded option]
**Reason**: 2-3 self-contained sentences. E.g.: "In ratis_example, we chose X over Y because Z." → this section alone answers the question "why X in ratis_example?".

### DA-02 — [Decision name]

Same.

## Main flow

### Flow 1 — [Explicit name, e.g.: "Resource creation"]

Numbered steps, each step understandable out of context:
1. Client calls `POST /api/v1/example` with [payload]
2. ratis_example validates [what] via [how]
3. ratis_example inserts [what] into [table]
4. ratis_example notifies [who] via [channel]
5. Client receives [response]

### Flow 2 — [Explicit name]

Same.

## GDPR constraints specific to the service

- [Constraint 1, e.g.: ratis_example never stores first/last names in plaintext]
- [Constraint 2]
- `DELETE /account` procedure (if applicable): how ratis_example anonymizes its data.

## Things to know (vectorized FAQ)

Each question is phrased the way a user would ask a RAG. The answer is self-contained (2-4 sentences) and mentions `ratis_example`.

### Why does ratis_example use [X] and not [Y]?

Short, reasoned answer. Mentions the service and the technology in question.

### How to test ratis_example locally?

Concrete steps (required env vars, fixtures, `pytest` command). E.g.: `uv run pytest webservices/ratis_example/tests/ -v`.

### What is the difference between ratis_example and [nearby service]?

Clear comparison if the domains are easily confused.

## Sub-ARCHs

- [[ARCH_feature_x]] — [1 sentence: what it covers]
- [[ARCH_feature_y]] — [1 sentence]

_(If none: "ratis_example has no sub-ARCHs — everything is in this document.")_

## Glossary

- **DA-XX**: numbered architecture decision (see dedicated section)
- **[Abbreviation]**: [full expansion — useful for RAG which does not resolve abbreviations]
