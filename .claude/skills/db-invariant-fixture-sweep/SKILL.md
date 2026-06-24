---
name: db-invariant-fixture-sweep
description: "Pre-migration sweep for test fixtures that would violate a new database invariant — covering typed ORM fixtures, helper defaults, and raw SQL literals separately."
---

# db-invariant-fixture-sweep

When a migration tightens a database invariant (a new/changed CHECK
constraint, an enum or provider restriction, or removal of a legacy
value), tests built on the old assumption break — often through fixtures
that are easy to miss because they live in raw SQL strings or helper
defaults, not just typed ORM objects. This skill sweeps for every fixture
that would violate the new invariant **before** the migration lands, so
the constraint ships green.

## When to Use

- Changing a model CHECK constraint, an enum/provider invariant, or
  removing a legacy value that tests currently rely on.
- Tightening any DB-level rule where old fixtures may now be illegal.

## When NOT to Use

- A purely additive change (new nullable column, new optional value) that
  cannot invalidate any existing fixture.
- Schema changes with no test fixtures touching the affected columns —
  the sweep returns nothing; don't manufacture work.
- Application-logic changes that don't alter a DB invariant — there's no
  constraint for fixtures to violate.

## Procedure

1. **Sweep typed fixtures and helper defaults.** Search ORM fixtures,
   factory helpers, and default-value helpers for the legacy value or the
   soon-to-be-illegal shape.
2. **Sweep raw SQL separately.** Grep raw SQL literals, `INSERT`
   statements, and column-combination patterns (e.g.
   provider/password pairs) — these are the ones typed searches miss.
3. **Run affected tests before migrating.** Execute the impacted service
   test groups to confirm the current (still-passing) baseline and to
   surface which fixtures will break.
4. **Migrate, then re-run constraint tests.** Apply the migration and
   re-run the constraint/affected tests to prove the invariant holds and
   no fixture violates it.
