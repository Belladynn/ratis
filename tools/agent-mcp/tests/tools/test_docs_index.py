"""TDD coverage for `agent_mcp.tools.docs_index`.

Strategy
--------
* Build synthetic inventory files in `tmp_path` and point
  `RATIS_DOCS_INVENTORY_PATH` at them — no dependency on the real
  repo-root ``ARCH_INVENTORY.md`` (which evolves frequently).
* Test the parser shape : 5-cell pipe rows, header rows skipped,
  malformed lines silently dropped.
* Test the TTL cache : second call within TTL is a no-op (file mtime
  doesn't matter while cached) ; `force=True` re-reads.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from agent_mcp.tools import docs_index

# -- fixtures -------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_cache() -> None:
    """Drop the module-level cache between tests."""
    docs_index._reset_cache_for_tests()


@pytest.fixture
def synth_inventory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Write a small valid ARCH_INVENTORY.md to tmpdir and point env at it."""
    inv = tmp_path / "ARCH_INVENTORY.md"
    inv.write_text(
        "\n".join(
            [
                "# Ratis Doc Inventory",
                "",
                "> preamble blah",
                "",
                "**3 entries** (1 EN-COURS · 1 LIVRÉ · 1 LEGACY)",
                "",
                "Format : `ID | STATUT | file:line | tags espacés | TL;DR`",
                "",
                "ID | STATUT | FICHIER:LIGNE | TAGS | TL;DR",
                "---+--------+---------------+------+------",
                "DA-11 | LIVRÉ V1.1 | docs/arch/ARCH_n8n_pipelines.md:294 | db-write-pipeline n8n | Workflow.",
                "ARCH_AUTH | LIVRÉ V0 | webservices/ratis_auth/ARCH_AUTH.md:27 | auth oauth jwt | Service auth.",
                "ARCH_geo | EN-COURS | docs/arch/ARCH_geo.md:25 | geo postgis proximity | Couche géo.",
                "KNOWN_PROBLEMS | LEGACY | docs/known/KNOWN_PROBLEMS.md:1 |  | Pièges récurrents.",
                "",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("RATIS_DOCS_INVENTORY_PATH", str(inv))
    return inv


# -- parsing --------------------------------------------------------------


def test_parse_line_valid_entry() -> None:
    raw = "DA-11 | LIVRÉ V1.1 | docs/arch/ARCH_n8n_pipelines.md:294 | tag-one tag-two | TL;DR text."
    entry = docs_index._parse_line(raw)
    assert entry is not None
    assert entry.id == "DA-11"
    assert entry.status == "LIVRÉ V1.1"
    assert entry.file_path == "docs/arch/ARCH_n8n_pipelines.md"
    assert entry.line == 294
    assert entry.tags == ["tag-one", "tag-two"]
    assert entry.tldr == "TL;DR text."


def test_parse_line_empty_tags_cell() -> None:
    """Legacy entries (KNOWN_PROBLEMS) have an empty tags column — `[]`."""
    raw = "KNOWN_PROBLEMS | LEGACY | docs/known/KNOWN_PROBLEMS.md:1 |  | Catalogue."
    entry = docs_index._parse_line(raw)
    assert entry is not None
    assert entry.id == "KNOWN_PROBLEMS"
    assert entry.tags == []
    assert entry.tldr == "Catalogue."


def test_parse_line_header_row_skipped() -> None:
    """Header rows look like data but the `line` cell is non-numeric → skipped."""
    assert docs_index._parse_line("ID | STATUT | FICHIER:LIGNE | TAGS | TL;DR") is None


def test_parse_line_rule_row_skipped() -> None:
    assert docs_index._parse_line("---+--------+---------------+------+------") is None


def test_parse_line_too_few_cells() -> None:
    assert docs_index._parse_line("DA-1 | LIVRÉ | only-three") is None


def test_parse_line_missing_line_number() -> None:
    raw = "DA-1 | LIVRÉ | docs/arch/X.md | tags | tldr"
    assert docs_index._parse_line(raw) is None


def test_parse_line_negative_line_number() -> None:
    raw = "DA-1 | LIVRÉ | docs/arch/X.md:0 | tags | tldr"
    assert docs_index._parse_line(raw) is None


def test_parse_line_blank_line_returns_none() -> None:
    assert docs_index._parse_line("") is None
    assert docs_index._parse_line("\n") is None


# -- load_inventory + cache -----------------------------------------------


def test_load_inventory_parses_all_data_rows(synth_inventory: Path) -> None:
    entries = docs_index.load_inventory()
    ids = [e.id for e in entries]
    assert ids == ["DA-11", "ARCH_AUTH", "ARCH_geo", "KNOWN_PROBLEMS"]


def test_load_inventory_cache_hits_within_ttl(synth_inventory: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Second call within TTL must not re-read the file."""
    entries1 = docs_index.load_inventory()
    # Wipe the file ; cache must hold the previously-loaded entries.
    synth_inventory.write_text("ID | STATUT | ...\n", encoding="utf-8")
    entries2 = docs_index.load_inventory()
    assert entries1 == entries2
    assert len(entries2) == 4  # still 4 from the cached parse


def test_load_inventory_force_bypasses_cache(synth_inventory: Path) -> None:
    docs_index.load_inventory()
    synth_inventory.write_text(
        "DA-99 | LIVRÉ | docs/arch/NEW.md:1 | tag-x | new entry\n",
        encoding="utf-8",
    )
    entries = docs_index.load_inventory(force=True)
    assert len(entries) == 1
    assert entries[0].id == "DA-99"


def test_load_inventory_missing_file_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    missing = tmp_path / "nope.md"
    monkeypatch.setenv("RATIS_DOCS_INVENTORY_PATH", str(missing))
    with pytest.raises(FileNotFoundError, match="ARCH_INVENTORY"):
        docs_index.load_inventory()


# -- path resolution ------------------------------------------------------


def test_inventory_path_uses_env_var(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    target = tmp_path / "custom.md"
    monkeypatch.setenv("RATIS_DOCS_INVENTORY_PATH", str(target))
    assert docs_index.inventory_path() == target


def test_inventory_path_default_is_docs_reference_inventory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("RATIS_DOCS_INVENTORY_PATH", raising=False)
    p = docs_index.inventory_path()
    assert p.name == "ARCH_INVENTORY.md"
    # The inventory moved out of the repo root into docs/reference/ — its
    # parent is now <repo>/docs/reference, and the grandparent is <repo>/docs.
    assert p.parent.name == "reference"
    assert p.parent.parent.name == "docs"
    # The default inventory must hang off the computed repo root.
    assert p.parent.parent.parent == docs_index._default_repo_root()


def test_repo_root_decoupled_from_inventory_when_no_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """In production the repo root is computed from __file__, NOT from the
    inventory's parent (which would now be docs/reference)."""
    monkeypatch.delenv("RATIS_DOCS_INVENTORY_PATH", raising=False)
    root = docs_index._repo_root()
    assert root == docs_index._default_repo_root()
    # Critically : the root is the real repo root, not docs/reference.
    assert root != docs_index.inventory_path().parent
    assert root.name != "reference"


def test_repo_root_follows_inventory_parent_under_override(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """With the test override set, the repo root falls back to the inventory's
    parent so synthetic tmp-root repos keep resolving sibling sources."""
    inv = tmp_path / "ARCH_INVENTORY.md"
    inv.write_text("DA-1 | LIVRÉ | docs/arch/X.md:1 | x | y\n", encoding="utf-8")
    monkeypatch.setenv("RATIS_DOCS_INVENTORY_PATH", str(inv))
    assert docs_index._repo_root() == tmp_path
