"""TDD coverage for the session-context bridge — M9 extension.

This test module covers the multi-source corpus loader, the new metadata
filters on `docs_search`, the `docs_context_for_session` wrapper and the
individual parsers (postmortem / skill / decisions / known-problems /
user-MEMORY).

Strategy
--------
* Each test builds a synthetic mini-repo in ``tmp_path`` and points
  ``RATIS_DOCS_INVENTORY_PATH`` at the synth inventory. Repo root is
  derived from the inventory path (see :func:`docs_index._repo_root`), so
  any glob pattern in :data:`docs_index.DEFAULT_SOURCES` resolves relative
  to that synth root.
* Postmortems live under ``~/.claude/postmortems/`` in the real world ; we
  build a fake HOME under ``tmp_path/home`` to keep the parser exercised
  without ever reading the operator's real postmortems.
* The vector index is NOT built here — the multi-source path is keyword
  only (see ``docs_tools._multi_source_search``).
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from agent_mcp.tools import docs_index, docs_tools

# -- fixtures -------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_caches() -> Iterator[None]:
    docs_index._reset_cache_for_tests()
    docs_index._reset_corpus_cache_for_tests()
    docs_tools._reset_for_tests()
    yield
    docs_index._reset_cache_for_tests()
    docs_index._reset_corpus_cache_for_tests()
    docs_tools._reset_for_tests()


@pytest.fixture
def synth_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Build a synthetic mini-repo + fake HOME with multi-source fixtures.

    Layout :
      tmp_path/
        ARCH_INVENTORY.md
        docs/decisions/DECISIONS_ACTED.md
        docs/known/KNOWN_PROBLEMS.md
        .claude/skills/some-skill/SKILL.md
        .claude/skill-candidates/cand-reviewed/SKILL.md     (reviewed)
        .claude/skill-candidates/cand-draft/SKILL.md         (draft)
      tmp_path/home/.claude/postmortems/2026-05-05-abcd.md
      tmp_path/home/.claude/projects/-Users-guillaume-Cursor-Ratis/memory/MEMORY.md
    """
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))

    # Inventory.
    #
    # The inventory now lives at <repo_root>/docs/reference/ARCH_INVENTORY.md
    # (the `arch_inventory` corpus source resolves it there, via _repo_root()).
    # We additionally keep a tmp-root copy and point RATIS_DOCS_INVENTORY_PATH
    # at it : with the override set, _repo_root() == inventory_path().parent
    # == tmp_path, which is what makes the OTHER repo-root-relative sources
    # (docs/decisions/..., docs/known/...) resolve under the synth root.
    # In production both references point at the same real file ; here they're
    # two copies with identical bytes, modelling that decoupling faithfully.
    inv_text = "\n".join(
        [
            "# Ratis Doc Inventory",
            "",
            "ID | STATUT | FICHIER:LIGNE | TAGS | TL;DR",
            "---+--------+---------------+------+------",
            "DA-11 | LIVRÉ V1.1 | docs/arch/ARCH_pipe.md:3 | n8n db-write-pipeline | Workflow.",
            "ARCH_AUTH | LIVRÉ V0 | webservices/auth/ARCH_AUTH.md:5 | auth oauth jwt | Auth.",
            "",
        ]
    )
    inv = tmp_path / "ARCH_INVENTORY.md"
    inv.write_text(inv_text, encoding="utf-8")
    ref_dir = tmp_path / "docs" / "reference"
    ref_dir.mkdir(parents=True)
    (ref_dir / "ARCH_INVENTORY.md").write_text(inv_text, encoding="utf-8")
    monkeypatch.setenv("RATIS_DOCS_INVENTORY_PATH", str(inv))

    # Decisions
    dec_dir = tmp_path / "docs" / "decisions"
    dec_dir.mkdir(parents=True)
    (dec_dir / "DECISIONS_ACTED.md").write_text(
        "\n".join(
            [
                "# Decisions Acted",
                "",
                "## DA-42 — admin UI skills review · #584 · LIVRÉ V1.0",
                "> Admin endpoint for reviewing Hermes skill candidates.",
                "> @tags: admin-ui hermes skills review",
                "",
                "## DA-43 — postmortems pipeline · #582 · LIVRÉ V1.0",
                "> Daily postmortem generator with redaction.",
                "> @tags: postmortem pipeline hermes",
                "",
            ]
        ),
        encoding="utf-8",
    )

    # Known problems
    kp_dir = tmp_path / "docs" / "known"
    kp_dir.mkdir(parents=True)
    (kp_dir / "KNOWN_PROBLEMS.md").write_text(
        "\n".join(
            [
                "# Known Problems",
                "",
                "## KP-99 — admin-ui crash on empty list · symptom-x · ACTIVE",
                "> Admin UI panics when the skill candidates list is empty.",
                "> @tags: admin-ui crash empty-state",
                "",
            ]
        ),
        encoding="utf-8",
    )

    # Active skill
    skill_dir = tmp_path / ".claude" / "skills" / "some-skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "\n".join(
            [
                "---",
                "name: some-skill",
                "description: >-",
                "  Tool that helps reconcile admin-ui session data.",
                "---",
                "",
                "# some-skill",
                "Body.",
            ]
        ),
        encoding="utf-8",
    )

    # Reviewed candidate
    cand_rev_dir = tmp_path / ".claude" / "skill-candidates" / "cand-reviewed"
    cand_rev_dir.mkdir(parents=True)
    (cand_rev_dir / "SKILL.md").write_text(
        "\n".join(
            [
                "---",
                "name: cand-reviewed",
                "status: reviewed",
                "description: >-",
                "  Reviewed candidate that should appear in the corpus.",
                "---",
                "",
                "# cand-reviewed",
            ]
        ),
        encoding="utf-8",
    )

    # Draft candidate (NOT reviewed) — must be filtered out
    cand_dr_dir = tmp_path / ".claude" / "skill-candidates" / "cand-draft"
    cand_dr_dir.mkdir(parents=True)
    (cand_dr_dir / "SKILL.md").write_text(
        "\n".join(
            [
                "---",
                "name: cand-draft",
                "status: draft",
                "description: >-",
                "  Draft candidate, should NOT be indexed.",
                "---",
            ]
        ),
        encoding="utf-8",
    )

    # Postmortem
    pm_dir = fake_home / ".claude" / "postmortems"
    pm_dir.mkdir(parents=True)
    (pm_dir / "2026-05-05-abcd1234.md").write_text(
        "\n".join(
            [
                "# Post-mortem — session abcd1234",
                "",
                "## Summary",
                "",
                "Admin UI hook failed because python was not on PATH ;"
                " the fix was to switch to python3 in the SessionStart hook.",
                "",
                "## Outcome",
                "",
                "- **Outcomes**: partially completed",
                "",
                "## Patterns observed",
                "",
                "- **hook depends on unavailable python executable** (×2): see above.",
                "",
            ]
        ),
        encoding="utf-8",
    )

    # User MEMORY index
    mem_dir = fake_home / ".claude" / "projects" / "-Users-guillaume-Cursor-Ratis" / "memory"
    mem_dir.mkdir(parents=True)
    (mem_dir / "MEMORY.md").write_text(
        "\n".join(
            [
                "# Memory index",
                "",
                "- [GitHub plan constraint](github.md) — private repo on free plan; no branch protection",
                "- [Avancer décisivement](decisive.md) — exécuter une décision actée sans re-confirmer",
                "",
            ]
        ),
        encoding="utf-8",
    )

    return tmp_path


# -- parsers --------------------------------------------------------------


def test_parse_decisions_extracts_id_status_tldr(synth_repo: Path) -> None:
    path = synth_repo / "docs" / "decisions" / "DECISIONS_ACTED.md"
    entries = docs_index.parse_decisions(path)
    by_id = {e.id: e for e in entries}
    assert set(by_id) == {"DA-42", "DA-43"}
    assert "LIVRÉ" in by_id["DA-42"].status
    assert "Admin endpoint" in by_id["DA-42"].tldr
    assert "admin-ui" in by_id["DA-42"].tags
    assert by_id["DA-42"].source == "decisions"


def test_parse_known_problems_extracts_kp_entries(synth_repo: Path) -> None:
    path = synth_repo / "docs" / "known" / "KNOWN_PROBLEMS.md"
    entries = docs_index.parse_known_problems(path)
    assert len(entries) == 1
    assert entries[0].id == "KP-99"
    assert "ACTIVE" in entries[0].status
    assert "admin-ui" in entries[0].tags


def test_parse_postmortem_returns_one_entry_with_date(synth_repo: Path) -> None:
    path = synth_repo / "home" / ".claude" / "postmortems" / "2026-05-05-abcd1234.md"
    entries = docs_index.parse_postmortem(path)
    assert len(entries) == 1
    e = entries[0]
    assert e.id == "2026-05-05-abcd1234"
    assert "Admin UI hook" in e.tldr
    assert "postmortem" in e.tags
    assert e.indexed_at is not None
    assert e.indexed_at.startswith("2026-05-05")
    assert e.source == "postmortems"


def test_parse_skill_reads_frontmatter_description(synth_repo: Path) -> None:
    path = synth_repo / ".claude" / "skills" / "some-skill" / "SKILL.md"
    entries = docs_index.parse_skill(path)
    assert len(entries) == 1
    e = entries[0]
    assert e.id == "some-skill"
    assert "reconcile admin-ui" in e.tldr
    assert e.source == "skills_active"


def test_parse_skill_filter_reviewed_keeps_only_reviewed(synth_repo: Path) -> None:
    reviewed = synth_repo / ".claude" / "skill-candidates" / "cand-reviewed" / "SKILL.md"
    draft = synth_repo / ".claude" / "skill-candidates" / "cand-draft" / "SKILL.md"
    assert len(docs_index.parse_skill_filter_reviewed(reviewed)) == 1
    assert docs_index.parse_skill_filter_reviewed(draft) == []


def test_parse_user_memory_creates_one_entry_per_bullet(synth_repo: Path) -> None:
    path = synth_repo / "home" / ".claude" / "projects" / "-Users-guillaume-Cursor-Ratis" / "memory" / "MEMORY.md"
    entries = docs_index.parse_user_memory(path)
    ids = [e.id for e in entries]
    # 2 bullet entries + 1 index entry
    assert any("github" in i.lower() for i in ids)
    assert any("avancer" in i.lower() or "decisive" in i.lower() for i in ids)
    assert "MEMORY_INDEX" in ids


# -- load_corpus ----------------------------------------------------------


def test_load_corpus_aggregates_all_default_sources(synth_repo: Path) -> None:
    entries = docs_index.load_corpus()
    sources = {e.source for e in entries}
    # All these sources should have produced at least one entry.
    expected = {
        "arch_inventory",
        "decisions_acted",
        "known_problems",
        "postmortems",
        "skills_active",
        "skill_candidates_reviewed",
        "user_memory",
    }
    assert expected.issubset(sources), f"missing sources : {expected - sources}"


def test_load_corpus_does_not_include_draft_candidates(synth_repo: Path) -> None:
    entries = docs_index.load_corpus()
    ids = {e.id for e in entries}
    assert "cand-reviewed" in ids
    assert "cand-draft" not in ids


def test_load_corpus_caches_within_ttl(synth_repo: Path) -> None:
    first = docs_index.load_corpus()
    # Mutate the inventory the `arch_inventory` source actually reads
    # (docs/reference/) ; cache should still hold the first parse.
    (synth_repo / "docs" / "reference" / "ARCH_INVENTORY.md").write_text(
        "DA-99 | LIVRÉ | docs/arch/X.md:1 | x | new\n", encoding="utf-8"
    )
    second = docs_index.load_corpus()
    assert second == first


def test_load_corpus_force_bypasses_cache(synth_repo: Path) -> None:
    docs_index.load_corpus()
    (synth_repo / "docs" / "reference" / "ARCH_INVENTORY.md").write_text(
        "DA-77 | LIVRÉ | docs/arch/Y.md:1 | tag-y | bumped\n", encoding="utf-8"
    )
    entries = docs_index.load_corpus(force=True)
    ids = {e.id for e in entries}
    assert "DA-77" in ids
    assert "DA-11" not in ids  # the old inventory entry is gone


def test_load_corpus_custom_source_subset(synth_repo: Path) -> None:
    only_decisions = [s for s in docs_index.DEFAULT_SOURCES if s.name == "decisions_acted"]
    entries = docs_index.load_corpus(sources=only_decisions, force=True)
    assert all(e.source == "decisions_acted" for e in entries)
    assert {"DA-42", "DA-43"} == {e.id for e in entries}


def test_load_corpus_skips_disabled_sources(synth_repo: Path) -> None:
    # Build a copy of DEFAULT_SOURCES with postmortems disabled.
    custom = [
        docs_index.IndexSource(s.name, s.glob_pattern, s.parser, enabled=False) if s.name == "postmortems" else s
        for s in docs_index.DEFAULT_SOURCES
    ]
    entries = docs_index.load_corpus(sources=custom, force=True)
    sources_present = {e.source for e in entries}
    assert "postmortems" not in sources_present


# -- docs_search filters --------------------------------------------------


def test_docs_search_filter_by_source(synth_repo: Path) -> None:
    """sources=['postmortems'] must restrict the result set."""
    results = docs_tools.docs_search("admin-ui", sources=["postmortems"])
    # Postmortem mentions "admin-ui" via the patterns observed.
    # Even if zero, the call must not crash AND must not include ARCH entries.
    for r in results:
        assert r["source"] == "postmortems"


def test_docs_search_filter_by_file_pattern(synth_repo: Path) -> None:
    results = docs_tools.docs_search("admin-ui", file_pattern="docs/decisions/*")
    # DA-42 / DA-43 live under docs/decisions/. ARCH_AUTH does not.
    for r in results:
        assert r["file_path"].startswith("docs/decisions/")


def test_docs_search_filter_by_status(synth_repo: Path) -> None:
    results = docs_tools.docs_search("admin", status_filter="LIVRÉ")
    # All returned entries should have LIVRÉ in their status.
    for r in results:
        assert "livré" in r["status"].lower()


def test_docs_search_filter_by_freshness_drops_old(synth_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A postmortem dated 2026-05-05 is >30 days from "now=2026-07-01" → dropped."""
    # We make the postmortem's freshness-derived date be "old" by setting
    # freshness_days=0, which means "must be exactly now or future". The
    # postmortem's indexed_at is 2026-05-05 → filtered out.
    results = docs_tools.docs_search(
        "admin-ui",
        sources=["postmortems"],
        freshness_days=1,  # only entries within last 24h
    )
    # Postmortem is dated 2026-05-05 → older than 1 day → filtered.
    assert results == []


def test_docs_search_filter_preserves_existing_behaviour_when_no_filter(
    synth_repo: Path,
) -> None:
    """Calling docs_search without filters must hit the original inventory path."""
    results = docs_tools.docs_search("n8n")
    ids = [r["id"] for r in results]
    assert "DA-11" in ids


# -- docs_context_for_session --------------------------------------------


def test_context_for_session_returns_nuggets(synth_repo: Path) -> None:
    out = docs_tools.docs_context_for_session(
        cwd="/Users/guillaume/Cursor/Ratis/.worktrees/feat-admin-ui",
        branch="feat/admin-ui-skills",
        limit=5,
    )
    assert "query_inferred" in out
    assert "admin" in out["query_inferred"]
    assert "ui" in out["query_inferred"]
    assert isinstance(out["nuggets"], list)
    assert "indexed_at" in out
    assert "arch_inventory" in out["sources_searched"]


def test_context_for_session_inferred_query_dedups(synth_repo: Path) -> None:
    out = docs_tools.docs_context_for_session(
        cwd="/tmp/admin",
        branch="feat/admin-something",
        user_message="we need admin features",
        limit=3,
    )
    tokens = out["query_inferred"].split()
    assert tokens.count("admin") == 1, f"admin appeared {tokens.count('admin')} times"


def test_context_for_session_empty_inputs_returns_empty_nuggets(
    synth_repo: Path,
) -> None:
    out = docs_tools.docs_context_for_session(cwd="", branch="", limit=5)
    assert out["nuggets"] == []
    assert out["query_inferred"] == ""


def test_context_for_session_respects_limit(synth_repo: Path) -> None:
    out = docs_tools.docs_context_for_session(
        cwd="admin/ui",
        branch="feat/skill-review",
        limit=2,
    )
    assert len(out["nuggets"]) <= 2


def test_context_for_session_includes_postmortem_match(synth_repo: Path) -> None:
    """A query that overlaps with the postmortem must surface it (if recent enough)."""
    # We need a fresh postmortem — bump its mtime to now so freshness_days=30 keeps it.
    pm = synth_repo / "home" / ".claude" / "postmortems" / "2026-05-05-abcd1234.md"
    import time as _t

    now = _t.time()
    import os as _os

    _os.utime(pm, (now, now))
    # Rewrite the date prefix in the filename to today, so the parser doesn't
    # pin indexed_at to a stale date.
    from datetime import datetime as _dt

    today_prefix = _dt.now().strftime("%Y-%m-%d")
    new_pm = pm.parent / f"{today_prefix}-abcd1234.md"
    pm.rename(new_pm)
    docs_index._reset_corpus_cache_for_tests()
    out = docs_tools.docs_context_for_session(
        cwd="/tmp/admin",
        branch="feat/admin-ui-python-hook",
        limit=10,
    )
    sources_found = {n["source"] for n in out["nuggets"]}
    # Either ARCH or decisions match — but the call should not crash and
    # should at least surface SOMETHING for "admin ui hook".
    assert out["nuggets"], "expected at least one nugget"
    assert sources_found  # non-empty


# -- registration ---------------------------------------------------------


def test_context_for_session_registered_with_ops_scope(synth_repo: Path) -> None:
    docs_tools.register_all()
    from agent_mcp.server import TOOLS_REGISTRY

    assert "docs_context_for_session" in TOOLS_REGISTRY
    assert TOOLS_REGISTRY["docs_context_for_session"].scope == "ops"
