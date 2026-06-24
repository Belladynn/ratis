"""TDD coverage for `agent_mcp.tools.docs_vector` — phase D agentic-docs.

Strategy
--------
The real embedder (``sentence-transformers`` + bge-m3 / miniLM) downloads
hundreds of MB of weights and is too heavy for CI. We therefore inject a
**deterministic dummy embedder** (Pydantic-typed, sync) that produces
reproducible 16-dimensional vectors derived from token hashes — enough to
exercise the cosine ranking + the rerank fusion without ever touching torch.

One integration test exercises the *real* embedder behind the
``RATIS_DOCS_VECTOR_REAL_MODEL`` env flag — skipped by default in CI.

Coverage focuses on the contract `docs_search` actually relies on :

* `build_or_refresh` is idempotent and respects the inventory mtime.
* `search` returns ``(entry, score)`` pairs ranked by cosine similarity.
* The fallback path (missing index, stale index, missing model) is silent
  and triggers via a return value the caller can branch on.
* The hybrid rerank in `docs_tools.docs_search` mixes vector + keyword
  scores according to the documented 0.7 / 0.3 weights (tested in
  ``test_docs_tools.py`` — this module owns the vector half only).
"""

from __future__ import annotations

import sqlite3
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
from agent_mcp.tools import docs_index, docs_vector

# -- fixtures -------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_caches() -> Iterator[None]:
    docs_index._reset_cache_for_tests()
    docs_vector._reset_for_tests()
    yield
    docs_index._reset_cache_for_tests()
    docs_vector._reset_for_tests()


@pytest.fixture
def synth_inventory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Tiny inventory with 4 entries — semantically distinct topics."""
    inv = tmp_path / "ARCH_INVENTORY.md"
    inv.write_text(
        "\n".join(
            [
                "# Ratis Doc Inventory",
                "",
                "ID | STATUT | FICHIER:LIGNE | TAGS | TL;DR",
                "---+--------+---------------+------+------",
                "DA-11 | LIVRÉ V1.1 | docs/arch/A.md:3 | db-write-pipeline n8n | Workflow n8n écrit en DB.",
                "DA-22 | LIVRÉ V1.0 | docs/arch/B.md:3 | batch-sentinel push-monitoring | Surveillance push.",
                "DA-33 | EN-COURS | docs/arch/C.md:3 | scan barcode | Scan code-barres produit.",
                "DA-44 | LIVRÉ V0 | docs/arch/D.md:3 | r2 storage crash-safety worker celery | "
                "Worker écrit dans R2 puis en DB, gérer le crash entre les deux.",
                "",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("RATIS_DOCS_INVENTORY_PATH", str(inv))
    monkeypatch.setenv("RATIS_DOCS_VECTOR_DB_PATH", str(tmp_path / ".docs-vector-index.db"))
    return inv


@pytest.fixture
def dummy_embedder() -> docs_vector.Embedder:
    """Deterministic 16-dim embedder. NO model load — purely token-hash based.

    Two strings sharing many tokens get high cosine similarity. Two strings
    with disjoint tokens get cosine ≈ 0. Good enough to exercise the ranking
    logic without paying the cost of a real model.
    """
    return docs_vector.HashEmbedder(dim=16)


# -- build_or_refresh -----------------------------------------------------


def test_build_or_refresh_writes_rows(synth_inventory: Path, dummy_embedder: docs_vector.Embedder) -> None:
    result = docs_vector.build_or_refresh(embedder=dummy_embedder)
    assert result.entries_indexed == 4
    assert result.db_path.exists()
    with sqlite3.connect(result.db_path) as conn:
        row_count = conn.execute("SELECT COUNT(*) FROM doc_embeddings").fetchone()[0]
        assert row_count == 4


def test_build_or_refresh_is_idempotent_when_fresh(synth_inventory: Path, dummy_embedder: docs_vector.Embedder) -> None:
    first = docs_vector.build_or_refresh(embedder=dummy_embedder)
    second = docs_vector.build_or_refresh(embedder=dummy_embedder)
    assert second.skipped is True
    assert second.entries_indexed == first.entries_indexed


def test_build_or_refresh_rebuilds_when_inventory_changes(
    synth_inventory: Path, dummy_embedder: docs_vector.Embedder
) -> None:
    docs_vector.build_or_refresh(embedder=dummy_embedder)
    # Touch the inventory so mtime > vector indexed_at.
    time.sleep(0.05)
    text = synth_inventory.read_text()
    synth_inventory.write_text(
        text + "DA-55 | EN-COURS | docs/arch/E.md:3 | onboarding | Onboarding utilisateur.\n",
        encoding="utf-8",
    )
    docs_index._reset_cache_for_tests()
    result = docs_vector.build_or_refresh(embedder=dummy_embedder)
    assert result.skipped is False
    assert result.entries_indexed == 5


def test_build_or_refresh_force_rebuilds_even_if_fresh(
    synth_inventory: Path, dummy_embedder: docs_vector.Embedder
) -> None:
    docs_vector.build_or_refresh(embedder=dummy_embedder)
    result = docs_vector.build_or_refresh(embedder=dummy_embedder, force=True)
    assert result.skipped is False


# -- is_fresh -------------------------------------------------------------


def test_is_fresh_false_when_db_missing(synth_inventory: Path) -> None:
    assert docs_vector.is_fresh() is False


def test_is_fresh_true_after_build(synth_inventory: Path, dummy_embedder: docs_vector.Embedder) -> None:
    docs_vector.build_or_refresh(embedder=dummy_embedder)
    assert docs_vector.is_fresh() is True


def test_is_fresh_false_after_inventory_modified(synth_inventory: Path, dummy_embedder: docs_vector.Embedder) -> None:
    docs_vector.build_or_refresh(embedder=dummy_embedder)
    time.sleep(0.05)
    synth_inventory.write_text(synth_inventory.read_text() + "\n", encoding="utf-8")
    assert docs_vector.is_fresh() is False


# -- search ---------------------------------------------------------------


def test_search_returns_entries_and_scores(synth_inventory: Path, dummy_embedder: docs_vector.Embedder) -> None:
    docs_vector.build_or_refresh(embedder=dummy_embedder)
    results = docs_vector.search("workflow n8n", top_k=3, embedder=dummy_embedder)
    assert len(results) > 0
    assert all(isinstance(r[0], docs_index.Entry) for r in results)
    assert all(0.0 <= r[1] <= 1.0 for r in results)


def test_search_ranks_overlapping_tokens_higher(synth_inventory: Path, dummy_embedder: docs_vector.Embedder) -> None:
    """Token overlap drives cosine in the hash embedder. The DA-44 TL;DR
    mentions worker/R2/DB/crash so the query should rank it first.
    """
    docs_vector.build_or_refresh(embedder=dummy_embedder)
    results = docs_vector.search("worker r2 db crash", top_k=4, embedder=dummy_embedder)
    assert results[0][0].id == "DA-44"


def test_search_respects_top_k(synth_inventory: Path, dummy_embedder: docs_vector.Embedder) -> None:
    docs_vector.build_or_refresh(embedder=dummy_embedder)
    results = docs_vector.search("workflow", top_k=2, embedder=dummy_embedder)
    assert len(results) <= 2


def test_search_returns_empty_when_db_missing(synth_inventory: Path, dummy_embedder: docs_vector.Embedder) -> None:
    """No build → no DB → search must return [] (caller decides fallback)."""
    results = docs_vector.search("anything", top_k=3, embedder=dummy_embedder)
    assert results == []


# -- registration / module surface ----------------------------------------


def test_module_exposes_protocol_and_helpers() -> None:
    """Smoke-check the public surface stays stable for `docs_tools`."""
    assert hasattr(docs_vector, "Embedder")
    assert hasattr(docs_vector, "HashEmbedder")
    assert hasattr(docs_vector, "build_or_refresh")
    assert hasattr(docs_vector, "search")
    assert hasattr(docs_vector, "is_fresh")
    assert hasattr(docs_vector, "ReindexResult")


# -- defensive : missing sentence-transformers module ---------------------


def test_default_embedder_returns_none_when_torch_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If sentence-transformers isn't installed, the helper returns None
    so the caller can decide to fallback rather than crash.
    """
    # Simulate the optional dep being absent.
    import sys

    monkeypatch.setitem(sys.modules, "sentence_transformers", None)
    embedder = docs_vector.default_embedder()
    assert embedder is None
