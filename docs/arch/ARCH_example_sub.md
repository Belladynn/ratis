---
# Identity
type: sub-arch                           # sub-arch | cross-cutting
service: ratis_example                   # service parent
parent: ARCH_EXAMPLE                     # ARCH global du service
status: planned                          # production | in-progress | planned | deprecated

# Navigation
related: [ARCH_OTHER_SUB]

# Business
tags: [example, feature-x]

# Freshness (MANDATORY — R34)
updated: 2026-04-24
---

# ratis_example — ARCH [feature name]

> Template for a sub-ARCH (feature within an existing service or cross-cutting ARCH) to be copied as `ARCH_<feature_snake>.md`. Includes Index/Checklist/Context/Decisions/Tests sections. Counterpart to `ARCH_EXAMPLE.md` on the service-global side.
> @tags: template arch boilerplate sub-arch convention scaffold meta example feature
> @status: LIVRÉ V0
> @subs: auto

> Parent: [[ARCH_EXAMPLE]] · Relations: [[ARCH_OTHER_SUB]]

> ⚠️ **THIS FILE IS A TEMPLATE** for a sub-ARCH (a feature within an existing service, or a cross-cutting ARCH). Copy it and rename to `ARCH_<feature_snake>.md`.

## Index

> ⚠️ The Index is **mandatory** in every ARCH. Update line numbers at each major edit (R34). It enables segmented reading (R29) to read just `Read offset=X limit=Y` instead of the entire file.

- [One-sentence summary](#one-sentence-summary) · L.NN
- [Implementation checklist](#implementation-checklist) · L.NN
- [Context](#context) · L.NN
- [Tables](#tables) · L.NN
- [Endpoints](#endpoints) · L.NN
- [Internal logic](#internal-logic) · L.NN
- [Inter-services](#inter-services) · L.NN
- [Architecture decisions](#architecture-decisions) · L.NN
- [Rules](#rules) · L.NN
- [Out of scope](#out-of-scope) · L.NN

---

## One-sentence summary

This feature adds [what] to ratis_example, for [purpose]. Self-contained, mentions the parent service.

## Implementation checklist

**Base checklist — to be kept in every sub-feature ARCH:**
- [ ] Alembic migration created and verified
- [ ] SQLAlchemy models updated
- [ ] Repository — CRUD functions
- [ ] Service — business logic + edge cases
- [ ] Route — endpoint + error codes
- [ ] Tests written (TDD — before the code)
- [ ] `conftest.py` updated if new `require_env()`
- [ ] `ratis_settings.json` updated if new parameters
- [ ] `pg_dump > db/schema.sql` after migration
- [ ] `ruff check --fix` clean
- [ ] CI pipeline green
- [ ] Parent ARCH [[ARCH_EXAMPLE]] updated (frontmatter `updated:` + relevant section — R34)

**Custom checklist:**
- [ ] [item specific to this feature]
- [ ] [item specific to this feature]

> ⚠️ One item at a time. Do not move to the next one without completing the current one.

## Context

Why this feature exists. 3-5 self-contained sentences, each mentioning ratis_example or the business domain.

## Tables

### `[table_name]` — created / modified

```sql
id          UUID PRIMARY KEY DEFAULT gen_random_uuid()
user_id     UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT
[col]       TEXT NOT NULL CHECK ([col] IN ('v1', 'v2'))
created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
```

## Endpoints

### `METHOD /api/v1/[route]`

- **Auth**: JWT / internal key / admin key
- **Request**:
  ```json
  { "champ": "valeur" }
  ```
- **Response**:
  ```json
  { "champ": "valeur" }
  ```
- **Error codes**: `404 not_found`, `422 invalid_input`

## Internal logic

### `[function_name](db, param)`

1. Verify [condition]
2. [atomic action]
3. [return]

Edge cases:
- If [condition] → [behavior]

## Inter-services

| Direction | Service | Function | Trigger |
|---|---|---|---|
| → outgoing | [[ARCH_NOTIFIER]] | `notify_user()` | After [action] |
| ← incoming | [[ARCH_PRODUCT_ANALYSER]] | `trigger_scan_accepted()` | After scan accepted |

## Architecture decisions

### DA-XX — [name]

**Choice**: [selected option]
**Alternative**: [rejected]
**Reason**: self-contained, mentions ratis_example.

## Rules

- [Absolute rule 1] — see KP-XX if applicable
- Never [forbidden action]
- Always [mandatory action]

## Out of scope

- [What the feature does NOT cover]
- [What will be done in another block]
