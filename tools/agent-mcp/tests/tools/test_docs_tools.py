"""TDD coverage for `agent_mcp.tools.docs_tools`.

Strategy
--------
* Build a synthetic mini-repo in `tmp_path` : an ARCH_INVENTORY.md and the
  ARCH files it references — so the parser, the section slicer and the
  filter logic are all exercised end-to-end without touching the real
  repo content.
* One integration test loads the *real* ARCH_INVENTORY.md and the *real*
  ARCH_n8n_pipelines.md to assert search/get work on production data.
* Dispatch-level test goes through `Dispatcher` to cover registration +
  scope (`ops`).
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from agent_mcp.audit import AuditLog
from agent_mcp.auth import AuthGate
from agent_mcp.server import Dispatcher
from agent_mcp.tools import docs_index, docs_tools, docs_vector

# -- fixtures -------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_caches_and_registration() -> Iterator[None]:
    """Each test starts with a cold cache and no pre-registered tools."""
    docs_index._reset_cache_for_tests()
    docs_tools._reset_for_tests()
    yield
    docs_index._reset_cache_for_tests()
    docs_tools._reset_for_tests()


@pytest.fixture
def synth_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Build a small repo-like tree :

    tmp_path/
      ARCH_INVENTORY.md          (4 entries)
      docs/arch/ARCH_pipe.md     (3 H2 sections : DA-11, DA-12, DA-13)
      webservices/auth/ARCH_AUTH.md   (file-level legacy)
      docs/known/KNOWN_PROBLEMS.md     (bare legacy)
    """
    inv = tmp_path / "ARCH_INVENTORY.md"
    (tmp_path / "docs" / "arch").mkdir(parents=True)
    (tmp_path / "webservices" / "auth").mkdir(parents=True)
    (tmp_path / "docs" / "known").mkdir(parents=True)

    # ARCH_pipe.md with 3 H2 entries.
    pipe = tmp_path / "docs" / "arch" / "ARCH_pipe.md"
    pipe.write_text(
        "\n".join(
            [
                "# Pipelines n8n",
                "",
                "## DA-11 — db-write-pipeline · #534 · LIVRÉ V1.1",  # line 3
                "> Workflow n8n db-write-pipeline.",
                "> @tags: db-write-pipeline n8n agent-mcp",
                "> @subs: auto",
                "",
                "### Architecture",
                "- Module 1.",
                "",
                "### Décisions",
                "- DA-11.A",
                "",
                "## DA-12 — batch-sentinel · #535 · LIVRÉ V1.0",  # line 14
                "> Phase 1 monitoring passif.",
                "> @tags: batch-sentinel n8n push-monitoring",
                "> @subs: auto",
                "",
                "### Architecture",
                "- Module 2.",
                "",
                "## DA-13 — inventory · #538 · LIVRÉ V1.0",  # line 22
                "> Démo convention.",
                "> @tags: inventory convention doc-rework",
                "> @subs: auto",
                "",
                "### Décisions",
                "- DA-13.A",
                "",
            ]
        ),
        encoding="utf-8",
    )

    # ARCH_AUTH.md = file-level legacy.
    auth = tmp_path / "webservices" / "auth" / "ARCH_AUTH.md"
    auth.write_text(
        "\n".join(
            [
                "---",
                "type: service-global",
                "---",
                "",
                "# ratis_auth",  # line 5 (the actual H1 — after frontmatter)
                "",
                "> Service FastAPI auth.",
                "> @tags: auth oauth jwt",
                "> @status: LIVRÉ V0",
                "> @subs: auto",
                "",
                "## Endpoints",
                "- /login",
                "",
                "## Tables",
                "- users",
                "",
            ]
        ),
        encoding="utf-8",
    )

    known = tmp_path / "docs" / "known" / "KNOWN_PROBLEMS.md"
    known.write_text(
        "# Known problems\n\n> Catalogue des pièges récurrents.\n",
        encoding="utf-8",
    )

    inv.write_text(
        "\n".join(
            [
                "# Ratis Doc Inventory",
                "",
                "**4 entries** (3 LIVRÉ · 1 LEGACY)",
                "",
                "Format : `ID | STATUT | file:line | tags espacés | TL;DR`",
                "",
                "ID | STATUT | FICHIER:LIGNE | TAGS | TL;DR",
                "---+--------+---------------+------+------",
                "DA-11 | LIVRÉ V1.1 | docs/arch/ARCH_pipe.md:3 | db-write-pipeline n8n agent-mcp | Workflow.",
                "DA-12 | LIVRÉ V1.0 | docs/arch/ARCH_pipe.md:14 | batch-sentinel n8n push-monitoring | Monitor.",
                "DA-13 | LIVRÉ V1.0 | docs/arch/ARCH_pipe.md:22 | inventory convention doc-rework | Démo.",
                "ARCH_AUTH | LIVRÉ V0 | webservices/auth/ARCH_AUTH.md:5 | auth oauth jwt | Auth.",
                "KNOWN_PROBLEMS | LEGACY | docs/known/KNOWN_PROBLEMS.md:1 |  | Pièges.",
                "",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("RATIS_DOCS_INVENTORY_PATH", str(inv))
    return tmp_path


# -- docs_search ----------------------------------------------------------


def test_docs_search_single_term_returns_match(synth_repo: Path) -> None:
    results = docs_tools.docs_search("db-write-pipeline")
    ids = [r["id"] for r in results]
    assert "DA-11" in ids


def test_docs_search_multi_term_adds_score(synth_repo: Path) -> None:
    """Two distinct terms matching the same entry should outscore one match."""
    results = docs_tools.docs_search("n8n monitoring")
    # DA-12 has both 'n8n' and 'push-monitoring' (substring) → should rank top.
    assert results[0]["id"] == "DA-12"


def test_docs_search_no_match_returns_empty(synth_repo: Path) -> None:
    assert docs_tools.docs_search("nonexistent-xyz-term") == []


def test_docs_search_empty_query_returns_empty(synth_repo: Path) -> None:
    assert docs_tools.docs_search("") == []
    assert docs_tools.docs_search("   ") == []


def test_docs_search_respects_top_k(synth_repo: Path) -> None:
    """top_k caps the result length even when more entries score positive."""
    # 'n8n' matches DA-11 + DA-12 (both have 'n8n' tag).
    results = docs_tools.docs_search("n8n", top_k=1)
    assert len(results) == 1


def test_docs_search_top_k_capped_at_50(synth_repo: Path) -> None:
    results = docs_tools.docs_search("DA", top_k=10_000)
    assert len(results) <= 50


# -- docs_get -------------------------------------------------------------


def test_docs_get_h2_section_stops_at_next_h2(synth_repo: Path) -> None:
    """DA-11 body should include its own Architecture/Décisions, NOT DA-12's."""
    s = docs_tools.docs_get("DA-11")
    assert s["id"] == "DA-11"
    assert s["status"] == "LIVRÉ V1.1"
    assert s["file_path"] == "docs/arch/ARCH_pipe.md"
    assert s["line"] == 3
    body = s["body"]
    assert "### Architecture" in body
    assert "### Décisions" in body
    assert "DA-11.A" in body
    # The DA-12 header must NOT be in the DA-11 body.
    assert "DA-12" not in body
    assert "## DA-12" not in body


def test_docs_get_h2_section_includes_metadata_quote_block(synth_repo: Path) -> None:
    s = docs_tools.docs_get("DA-13")
    body = s["body"]
    assert "@tags: inventory convention doc-rework" in body
    assert "## DA-13" in body  # header included
    assert "DA-13.A" in body
    assert "### Décisions" in body


def test_docs_get_file_level_entry_returns_full_file_from_h1(synth_repo: Path) -> None:
    """ARCH_AUTH is file-level (H1) — body extends to EOF."""
    s = docs_tools.docs_get("ARCH_AUTH")
    body = s["body"]
    assert body.startswith("# ratis_auth")
    assert "## Endpoints" in body
    assert "## Tables" in body
    assert "/login" in body
    assert "users" in body


def test_docs_get_unknown_id_raises_keyerror(synth_repo: Path) -> None:
    with pytest.raises(KeyError, match="DA-999"):
        docs_tools.docs_get("DA-999")


def test_docs_get_stale_inventory_raises_filenotfound(synth_repo: Path, tmp_path: Path) -> None:
    """If the inventory references a file that doesn't exist, surface clearly."""
    # Delete the underlying file but keep the inventory pointing at it.
    (tmp_path / "docs" / "arch" / "ARCH_pipe.md").unlink()
    docs_index._reset_cache_for_tests()  # force re-read of inventory
    with pytest.raises(FileNotFoundError, match="ARCH_pipe.md"):
        docs_tools.docs_get("DA-11")


# -- docs_find ------------------------------------------------------------


def test_docs_find_by_status_substring(synth_repo: Path) -> None:
    """status='LIVRÉ' matches all three V1 entries (V0/V1.0/V1.1)."""
    out = docs_tools.docs_find(status="LIVRÉ")
    ids = [e["id"] for e in out]
    assert set(ids) == {"DA-11", "DA-12", "DA-13", "ARCH_AUTH"}


def test_docs_find_by_status_specific_version(synth_repo: Path) -> None:
    out = docs_tools.docs_find(status="V1.1")
    assert [e["id"] for e in out] == ["DA-11"]


def test_docs_find_by_tags_intersection(synth_repo: Path) -> None:
    """ALL tags must appear in the entry's tag set (intersection)."""
    out = docs_tools.docs_find(tags=["n8n", "agent-mcp"])
    assert [e["id"] for e in out] == ["DA-11"]


def test_docs_find_by_single_tag(synth_repo: Path) -> None:
    out = docs_tools.docs_find(tags=["n8n"])
    ids = {e["id"] for e in out}
    assert ids == {"DA-11", "DA-12"}


def test_docs_find_by_file_glob(synth_repo: Path) -> None:
    out = docs_tools.docs_find(file_glob="docs/arch/*")
    ids = {e["id"] for e in out}
    assert ids == {"DA-11", "DA-12", "DA-13"}


def test_docs_find_combined_filters_are_and(synth_repo: Path) -> None:
    out = docs_tools.docs_find(
        status="LIVRÉ",
        tags=["n8n"],
        file_glob="docs/arch/*",
    )
    ids = {e["id"] for e in out}
    assert ids == {"DA-11", "DA-12"}


def test_docs_find_no_criteria_returns_everything(synth_repo: Path) -> None:
    out = docs_tools.docs_find()
    assert len(out) == 5


def test_docs_find_no_match_returns_empty(synth_repo: Path) -> None:
    assert docs_tools.docs_find(status="DEPRECATED") == []
    assert docs_tools.docs_find(tags=["nope"]) == []


# -- docs_list_files ------------------------------------------------------


def test_docs_list_files_groups_entries_per_file(synth_repo: Path) -> None:
    files = docs_tools.docs_list_files()
    by_path = {f["path"]: f for f in files}

    assert by_path["docs/arch/ARCH_pipe.md"]["entries_count"] == 3
    assert by_path["docs/arch/ARCH_pipe.md"]["category"] == "arch"

    assert by_path["webservices/auth/ARCH_AUTH.md"]["entries_count"] == 1
    assert by_path["webservices/auth/ARCH_AUTH.md"]["category"] == "service-arch"

    assert by_path["docs/known/KNOWN_PROBLEMS.md"]["entries_count"] == 1
    assert by_path["docs/known/KNOWN_PROBLEMS.md"]["category"] == "known"


def test_docs_list_files_sorted_by_category_then_path(synth_repo: Path) -> None:
    files = docs_tools.docs_list_files()
    keys = [(f["category"], f["path"]) for f in files]
    assert keys == sorted(keys)


# -- registration & dispatch (full pipeline) ------------------------------


def test_all_tools_registered_with_ops_scope(synth_repo: Path) -> None:
    docs_tools.register_all()
    from agent_mcp.server import TOOLS_REGISTRY

    for name in ("docs_search", "docs_get", "docs_find", "docs_list_files"):
        assert name in TOOLS_REGISTRY, f"missing {name}"
        assert TOOLS_REGISTRY[name].scope == "ops"


def test_register_all_is_idempotent(synth_repo: Path) -> None:
    docs_tools.register_all()
    docs_tools.register_all()  # would raise without idempotence guard
    from agent_mcp.server import TOOLS_REGISTRY

    assert "docs_search" in TOOLS_REGISTRY


def test_load_builtin_tools_includes_docs(synth_repo: Path) -> None:
    from agent_mcp.server import TOOLS_REGISTRY, load_builtin_tools

    load_builtin_tools()
    for name in ("docs_search", "docs_get", "docs_find", "docs_list_files"):
        assert name in TOOLS_REGISTRY


@pytest.mark.asyncio
async def test_dispatch_docs_search_audits_ok(synth_repo: Path, tmp_path: Path) -> None:
    """End-to-end : dispatcher routes ops caller to docs_search and audits ok."""
    auth = AuthGate(admin_token="ADMIN_TOK", ops_token="OPS_TOK")
    audit = AuditLog(tmp_path / "audit.log")
    docs_tools.register_all()
    disp = Dispatcher(auth=auth, audit=audit)

    outcome = await disp.dispatch(
        tool_name="docs_search",
        arguments={"query": "n8n", "top_k": 2},
        presented_token="OPS_TOK",
    )
    assert outcome.status == "ok"
    assert isinstance(outcome.result, list)
    assert len(outcome.result) <= 2
    assert all(isinstance(r, dict) for r in outcome.result)


# -- integration with real ARCH_INVENTORY --------------------------------


@pytest.fixture
def real_inventory(monkeypatch: pytest.MonkeyPatch) -> Path:
    """Exercise production resolution against the real ARCH_INVENTORY.md.

    We deliberately do NOT set ``RATIS_DOCS_INVENTORY_PATH`` here. The override
    forces ``_repo_root()`` to the inventory's parent (``docs/reference/``),
    which can't resolve the repo-root-relative source files that ``docs_get``
    reads. Letting production resolution run instead gives both the real
    inventory (``docs/reference/ARCH_INVENTORY.md``) and the real repo root
    (computed from ``__file__``), so source files resolve correctly.
    """
    # repo root = 4 parents up from this test file
    # tools/agent-mcp/tests/tools/test_docs_tools.py → 4 parents = repo root.
    # The inventory now lives under docs/reference/ (moved out of repo root).
    repo_root = Path(__file__).resolve().parents[4]
    inv = repo_root / "docs" / "reference" / "ARCH_INVENTORY.md"
    if not inv.exists():
        pytest.skip("real ARCH_INVENTORY.md missing — run scripts/generate-arch-inventory.py")
    monkeypatch.delenv("RATIS_DOCS_INVENTORY_PATH", raising=False)
    docs_index._reset_cache_for_tests()
    return inv


def test_integration_search_db_pipeline_returns_results(real_inventory: Path) -> None:
    """Sanity check on production data — 'db-pipeline' must yield ≥5 entries.

    The real inventory has the HSP series (HSP-1..5), DA-11/14/15/16 — all
    tagged `db-pipeline` / `db-write-pipeline`. If this test breaks because
    those entries were removed, update the threshold.
    """
    results = docs_tools.docs_search("db-pipeline", top_k=20)
    assert len(results) >= 5, f"expected ≥5 results, got {len(results)} : {[r['id'] for r in results]}"
    # All results should mention db-pipeline somewhere — sanity.
    for r in results:
        haystack = " ".join([r["id"], r["status"], r["tldr"], *r["tags"]]).lower()
        assert "db-pipeline" in haystack or "db-write-pipeline" in haystack


def test_integration_get_arch_n8n_section(real_inventory: Path) -> None:
    """Pulling a real H2 section must include the next-H2-stop boundary."""
    # HSP-3 is documented in ARCH_n8n_pipelines.md — known stable id.
    section = docs_tools.docs_get("HSP-3")
    assert section["id"] == "HSP-3"
    assert section["file_path"] == "docs/arch/ARCH_n8n_pipelines.md"
    # Body must include the HSP-3 header but NOT the HSP-4 header.
    body = section["body"]
    assert "## HSP-3" in body
    assert "## HSP-4" not in body


# -- hybrid backend (vector + keyword) ------------------------------------


@pytest.fixture
def synth_repo_with_vector_index(synth_repo: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """`synth_repo` + a built vector index using the deterministic HashEmbedder.

    This is the fixture every hybrid test wants : real inventory, real
    SQLite, no torch.
    """
    monkeypatch.setenv("RATIS_DOCS_VECTOR_DB_PATH", str(synth_repo / ".docs-vector-index.db"))
    embedder = docs_vector.HashEmbedder(dim=16)
    docs_vector.build_or_refresh(embedder=embedder, force=True)
    # Patch the production helper so `docs_search` reuses the same stub.
    monkeypatch.setattr(docs_vector, "default_embedder", lambda: embedder)
    return synth_repo


def test_docs_search_uses_hybrid_when_index_fresh(
    synth_repo_with_vector_index: Path,
) -> None:
    """Sanity : with a fresh index, search returns plausible Entries."""
    results = docs_tools.docs_search("n8n", top_k=5)
    ids = [r["id"] for r in results]
    # Both DA-11 and DA-12 carry the `n8n` tag — hybrid should surface them.
    assert "DA-11" in ids
    assert "DA-12" in ids


def test_docs_search_keyword_only_when_index_missing(synth_repo: Path) -> None:
    """No `.docs-vector-index.db` → pure keyword backend (unchanged behaviour)."""
    # No build_or_refresh called → no index file → keyword fallback.
    results = docs_tools.docs_search("db-write-pipeline")
    ids = [r["id"] for r in results]
    assert "DA-11" in ids


def test_docs_search_falls_back_when_index_stale(
    synth_repo_with_vector_index: Path,
) -> None:
    """Stale index (inventory mtime > indexed_at) → keyword backend."""
    inv = synth_repo_with_vector_index / "ARCH_INVENTORY.md"
    # Touch the file so its mtime moves past the recorded indexed_at.
    import time as _t

    _t.sleep(0.05)
    inv.touch()
    docs_index._reset_cache_for_tests()
    # Should NOT crash and should still return matches via the keyword path.
    results = docs_tools.docs_search("db-write-pipeline")
    assert any(r["id"] == "DA-11" for r in results)


def test_docs_search_hybrid_rewards_exact_tag_via_keyword(
    synth_repo_with_vector_index: Path,
) -> None:
    """Query that's an exact tag → keyword side pulls the entry up.

    The HashEmbedder is token-based so exact-token queries also score well
    semantically. The point of this test is just that the rerank does not
    drop the entry that the keyword backend would have surfaced.
    """
    results = docs_tools.docs_search("inventory")
    ids = [r["id"] for r in results]
    assert "DA-13" in ids  # has `inventory` tag


def test_docs_search_hybrid_finds_token_in_tldr_via_vector(
    synth_repo_with_vector_index: Path,
) -> None:
    """A query token that's in the TL;DR but not in tags still matches.

    With the HashEmbedder the TL;DR is part of the corpus, so a token
    present there scores via vector even when the keyword path also
    happens to match it (the assertion below only checks presence in
    top results, not the exact mechanism).
    """
    # 'monitor' is in the DA-12 TL;DR ("Monitor.") via the synth_repo
    # fixture. Confirm hybrid surfaces DA-12 for that query.
    results = docs_tools.docs_search("monitor")
    ids = [r["id"] for r in results]
    assert "DA-12" in ids


def test_docs_reindex_returns_typed_result(synth_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`docs_reindex` exposes ReindexResult.model_dump shape."""
    monkeypatch.setenv("RATIS_DOCS_VECTOR_DB_PATH", str(synth_repo / ".docs-vector-index.db"))
    embedder = docs_vector.HashEmbedder(dim=16)
    monkeypatch.setattr(docs_vector, "default_embedder", lambda: embedder)
    out = docs_tools.docs_reindex(force=True)
    assert out["entries_indexed"] >= 4
    assert out["skipped"] is False
    assert "indexed_at" in out
    assert out["model_name"] == "HashEmbedder"


def test_docs_reindex_skipped_on_fresh_index(synth_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RATIS_DOCS_VECTOR_DB_PATH", str(synth_repo / ".docs-vector-index.db"))
    embedder = docs_vector.HashEmbedder(dim=16)
    monkeypatch.setattr(docs_vector, "default_embedder", lambda: embedder)
    docs_tools.docs_reindex(force=True)
    second = docs_tools.docs_reindex(force=False)
    assert second["skipped"] is True


def test_docs_reindex_registered_with_ops_scope(synth_repo: Path) -> None:
    docs_tools.register_all()
    from agent_mcp.server import TOOLS_REGISTRY

    assert "docs_reindex" in TOOLS_REGISTRY
    assert TOOLS_REGISTRY["docs_reindex"].scope == "ops"
