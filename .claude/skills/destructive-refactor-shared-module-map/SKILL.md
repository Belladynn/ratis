---
name: destructive-refactor-shared-module-map
description: "Before deleting or renaming a large subsystem, classify each module as exclusive vs shared using external and internal import maps, so you don't take down a shared library that lives inside a legacy-named directory."
---

# destructive-refactor-shared-module-map

Deleting or renaming a subsystem is dangerous when its directory name
implies "all legacy, safe to drop" but it actually hosts modules other
features still import. The clean move is to build an import map *before*
touching anything : prove which modules are exclusive to the doomed
subsystem (safe to delete) and which are shared (must be kept or moved
first). This is the destructive-refactor companion to `codebase-recon` —
recon tells you what exists ; this skill tells you what's safe to remove.

## When to Use

- Removing or renaming a legacy subsystem / directory whose name may hide
  shared libraries used elsewhere.
- Any deletion where you are not certain every file in the target tree is
  used *only* by that subsystem.

## When NOT to Use

- Deleting a genuinely leaf module with zero importers you've already
  confirmed in one grep — the full map is overkill.
- A pure rename handled by tooling that updates all references atomically
  (IDE refactor, codemod) with a green build to confirm.
- You haven't yet decided *whether* to refactor — that's a
  `codebase-recon` / brainstorm step; this skill is for executing a
  decided destructive change safely.

## Procedure

1. **Map external importers.** For every module in the target directory,
   find who imports it from *outside* the directory (grep the codebase for
   each module path / symbol). Any module with an external importer is
   **shared**, not exclusive.
2. **Map internal-to-kept imports.** Check imports *from* the modules you
   intend to keep back into the target tree — a kept module depending on a
   doomed one is the same hazard in reverse.
3. **Classify each module.** Exclusive (only used within the doomed
   subsystem) → safe to delete. Shared → must be kept, or moved to a
   neutral location and its importers repointed *first*.
4. **Delete / move only the proven-exclusive set.** Repoint shared
   modules before removing anything that fed them.
5. **Verify.** Run the full test suite and build. A green build after the
   import-map-driven deletion is the confirmation; a failure points
   straight at a module you misclassified.
